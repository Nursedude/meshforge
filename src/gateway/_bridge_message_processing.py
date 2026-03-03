"""
Message processing and notification helpers for the gateway bridge.

Extracted from rns_bridge.py for CLAUDE.md #6 compliance (<1,500 lines).
Contains Mesh↔RNS message bridging, persistent queue retry, and notification logic.
"""

import logging
import time
from typing import Dict, Optional

from .message_lifecycle import MessageLifecycleState

logger = logging.getLogger(__name__)


def process_mesh_to_rns(bridge, msg):
    """Process message from Meshtastic to RNS.

    On send failure for non-broadcast messages, attempts to persist
    to the persistent queue for later retry.
    """
    msg_id = getattr(msg, 'id', '') or msg.source_id or 'unknown'
    bridge.flow_tracker.record_event(
        msg_id, MessageLifecycleState.SENT,
        source_network="meshtastic", destination_network="rns",
        content_preview=msg.content,
    )
    try:
        prefix = f"[Mesh:{msg.source_id[-4:]}] " if msg.source_id else "[Mesh] "
        content = prefix + msg.content

        destination_hash = None
        if msg.destination_id and not msg.is_broadcast:
            destination_hash = get_rns_destination(bridge, msg.destination_id)

        if bridge.send_to_rns(content, destination_hash):
            logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
            with bridge._stats_lock:
                bridge.stats['messages_mesh_to_rns'] += 1
            bridge.health.record_message_sent("mesh_to_rns")
            bridge.flow_tracker.record_event(
                msg_id, MessageLifecycleState.DELIVERED,
                source_network="meshtastic", destination_network="rns",
                content_preview=msg.content,
            )
        else:
            if msg.is_broadcast:
                logger.debug(f"Mesh→RNS broadcast not sent (no propagation node): {content[:30]}...")
            else:
                logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                with bridge._stats_lock:
                    bridge.stats['errors'] += 1
                requeued = requeue_failed_message(bridge, msg, "rns")
                bridge.health.record_message_failed("mesh_to_rns", requeued=requeued)
                bridge.flow_tracker.record_event(
                    msg_id, MessageLifecycleState.FAILED,
                    source_network="meshtastic", destination_network="rns",
                    content_preview=msg.content,
                    details="Send failed, requeued" if requeued else "Send failed",
                )

    except Exception as e:
        logger.error(f"Error bridging Mesh→RNS: {e}")
        with bridge._stats_lock:
            bridge.stats['errors'] += 1
        bridge.health.record_error("rns", e)
        requeue_failed_message(bridge, msg, "rns")
        bridge.health.record_message_failed("mesh_to_rns", requeued=True)
        bridge.flow_tracker.record_event(
            msg_id, MessageLifecycleState.FAILED,
            source_network="meshtastic", destination_network="rns",
            details=str(e),
        )


def get_rns_destination(bridge, meshtastic_id: str) -> Optional[bytes]:
    """Look up RNS destination hash for a Meshtastic node ID."""
    if hasattr(bridge, 'node_tracker') and bridge.node_tracker:
        node = bridge.node_tracker.get_node_by_mesh_id(meshtastic_id)
        if node and hasattr(node, 'rns_hash') and node.rns_hash:
            return node.rns_hash
    return None


def requeue_failed_message(bridge, msg, destination: str) -> bool:
    """Persist a failed message to the persistent queue for later retry.

    Args:
        bridge: The RNSMeshtasticBridge instance.
        msg: The message that failed to send (BridgedMessage or CanonicalMessage).
        destination: Target network ("meshtastic", "rns", or "meshcore").

    Returns:
        True if message was successfully persisted, False otherwise.
    """
    if not bridge._persistent_queue:
        return False

    try:
        from .message_queue import MessagePriority

        # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
        source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
        dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
        bridge._persistent_queue.enqueue(
            payload={
                'message': msg.content,
                'source_id': source_id,
                'destination_id': dest_id or "",
                'metadata': msg.metadata or {},
            },
            destination=destination,
            priority=MessagePriority.HIGH,
        )
        logger.debug(f"Failed message re-queued to persistent storage ({destination})")
        return True
    except Exception as e:
        logger.error(f"Failed to persist message for retry: {e}")
        return False


def process_rns_to_mesh(bridge, msg):
    """Process message from RNS to Meshtastic.

    On send failure, persists to persistent queue for later retry.
    """
    msg_id = getattr(msg, 'id', '') or msg.source_id or 'unknown'
    bridge.flow_tracker.record_event(
        msg_id, MessageLifecycleState.SENT,
        source_network="rns", destination_network="meshtastic",
        content_preview=msg.content,
    )
    try:
        prefix = f"[RNS:{msg.source_id[:4]}] "
        content = prefix + msg.content

        if bridge.send_to_meshtastic(content, channel=bridge.config.meshtastic.channel):
            logger.info(f"Bridge RNS→Mesh: {content[:50]}...")
            with bridge._stats_lock:
                bridge.stats['messages_rns_to_mesh'] += 1
            bridge.health.record_message_sent("rns_to_mesh")
            bridge.flow_tracker.record_event(
                msg_id, MessageLifecycleState.DELIVERED,
                source_network="rns", destination_network="meshtastic",
                content_preview=msg.content,
            )
        else:
            logger.warning("Failed to bridge RNS→Mesh")
            with bridge._stats_lock:
                bridge.stats['errors'] += 1
            requeued = requeue_failed_message(bridge, msg, "meshtastic")
            bridge.health.record_message_failed("rns_to_mesh", requeued=requeued)
            bridge.flow_tracker.record_event(
                msg_id, MessageLifecycleState.FAILED,
                source_network="rns", destination_network="meshtastic",
                details="Send failed, requeued" if requeued else "Send failed",
            )

    except Exception as e:
        logger.error(f"Error bridging RNS→Mesh: {e}")
        with bridge._stats_lock:
            bridge.stats['errors'] += 1
        bridge.health.record_error("meshtastic", e)
        requeue_failed_message(bridge, msg, "meshtastic")
        bridge.health.record_message_failed("rns_to_mesh", requeued=True)
        bridge.flow_tracker.record_event(
            msg_id, MessageLifecycleState.FAILED,
            source_network="rns", destination_network="meshtastic",
            details=str(e),
        )


def notify_message(bridge, msg):
    """Notify message callbacks and emit to event bus (thread-safe snapshot).

    Handles both BridgedMessage and CanonicalMessage objects.

    Issue #17 Phase 3: Emit messages to event bus so UI panels can subscribe
    and display RX messages without being directly coupled to the bridge.
    """
    with bridge._callbacks_lock:
        callbacks = list(bridge._message_callbacks)
    for callback in callbacks:
        try:
            callback(msg)
        except Exception as e:
            logger.error(f"Message callback error: {e}")

    # Emit to event bus for UI panels (Issue #17 Phase 3)
    try:
        from utils.event_bus import emit_message as _emit_message, emit_tactical
        has_event_bus = True
    except ImportError:
        has_event_bus = False

    if has_event_bus:
        try:
            node_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '') or ''
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', None)
            title = getattr(msg, 'title', None) or (msg.metadata.get('title') if msg.metadata else None)
            _emit_message(
                direction='rx',
                content=msg.content,
                node_id=node_id,
                node_name="",
                channel=msg.metadata.get('channel', 0) if msg.metadata else 0,
                network=msg.source_network,
                raw_data={
                    'destination_id': dest_id,
                    'is_broadcast': msg.is_broadcast,
                    'title': title,
                    'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
                    'metadata': msg.metadata
                }
            )
        except Exception as e:
            logger.warning(f"Event bus emit failed: {e}")

    # Auto-ingest tactical messages (X1 format) to timeline + event bus
    msg_type = getattr(msg, 'message_type', None)
    if msg_type is not None and hasattr(msg_type, 'value') and msg_type.value == 'tactical':
        try:
            from tactical.x1_codec import decode as x1_decode, is_x1
            if is_x1(msg.content):
                tac_msg = x1_decode(msg.content)
                from utils.event_bus import emit_tactical
                emit_tactical(
                    tactical_type=tac_msg.tactical_type.name,
                    message_id=tac_msg.id,
                    sender_id=tac_msg.sender_id,
                    content=tac_msg.content,
                    encryption_mode=tac_msg.encryption_mode.value,
                )
        except Exception as e:
            logger.warning(f"Tactical auto-ingest failed: {e}")


def start_ws_server(bridge):
    """Start WebSocket server for real-time message broadcast to web UI."""
    try:
        from utils.websocket_server import (
            start_websocket_server, is_websocket_available
        )
    except ImportError:
        return

    try:
        if is_websocket_available():
            if start_websocket_server(port=5001):
                logger.info("WebSocket server started on port 5001")
                bridge._websocket_started = True
            else:
                logger.debug("WebSocket server failed to start")
        else:
            logger.debug("WebSocket not available (websockets library not installed)")
    except Exception as e:
        logger.debug(f"Could not start WebSocket server: {e}")


def stop_ws_server(bridge):
    """Stop WebSocket server."""
    if getattr(bridge, '_websocket_started', False):
        try:
            from utils.websocket_server import stop_websocket_server
            stop_websocket_server()
            logger.info("WebSocket server stopped")
        except Exception as e:
            logger.debug(f"Error stopping WebSocket server: {e}")


def notify_status(bridge, status: str):
    """Notify status callbacks (thread-safe snapshot)."""
    with bridge._callbacks_lock:
        callbacks = list(bridge._status_callbacks)
    for callback in callbacks:
        try:
            callback(status, bridge.get_status())
        except Exception as e:
            logger.error(f"Status callback error: {e}")
