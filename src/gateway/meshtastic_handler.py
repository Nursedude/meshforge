"""
Meshtastic Handler for RNS Bridge.

Manages Meshtastic connection, message handling, and node tracking.
Extracted from rns_bridge.py for maintainability (Issue #6).

Uses dependency injection for shared state:
- config: Gateway configuration
- node_tracker: Unified node tracking
- health: Bridge health monitoring
- stats: Shared statistics dict
- callbacks: Message/status notification
"""

import logging
import subprocess
import threading
import time
from datetime import datetime
from queue import Full
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .config import GatewayConfig
from .node_tracker import UnifiedNode
from .reconnect import ReconnectStrategy
from utils.meshtastic_connection import (
    clear_stale_connections, get_connection_manager, wait_for_cooldown
)
try:
    from utils.websocket_server import broadcast_message
except ImportError:
    def broadcast_message(*args, **kwargs):
        pass
from utils.safe_import import safe_import
from utils.service_check import check_service

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)

# pubsub is an external dependency (pypubsub) - keep safe_import
pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')


class MeshtasticHandler:
    """
    Handles Meshtastic connection and message processing.

    This class manages the Meshtastic side of the bridge:
    - Connection establishment and reconnection
    - Message receiving and sending
    - Node tracking updates
    - Health monitoring

    Args:
        config: Gateway configuration object
        node_tracker: Unified node tracker instance
        health: Bridge health monitor instance
        stop_event: Threading event for graceful shutdown
        stats: Shared statistics dictionary
        stats_lock: Lock for thread-safe stats updates
        message_queue: Queue for messages to be bridged to RNS
        message_callback: Callback for received messages
        status_callback: Callback for status changes
    """

    def __init__(
        self,
        config: GatewayConfig,
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,  # Queue for mesh->rns messages
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._mesh_to_rns_queue = message_queue

        # Callbacks
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge

        # Connection state
        self._connected = False
        self._interface = None
        self._conn_manager = None
        self._pubsub_handler = None

        # Reconnection strategy
        self._reconnect = ReconnectStrategy.for_meshtastic()

        # Network topology reference (optional)
        self._network_topology = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to Meshtastic."""
        return self._connected

    @property
    def interface(self):
        """Get the Meshtastic interface."""
        return self._interface

    def set_network_topology(self, topology) -> None:
        """Set network topology reference for relay node tracking."""
        self._network_topology = topology

    def run_loop(self) -> None:
        """
        Main loop for Meshtastic connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Records events to BridgeHealthMonitor for metrics.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    if not self._reconnect.should_retry():
                        logger.warning("Meshtastic reconnection: max attempts reached, resetting")
                        self._reconnect.reset()
                        self._stop_event.wait(self._reconnect.config.max_delay)
                        continue

                    # After 3 consecutive failures, check for CLOSE-WAIT zombies.
                    # meshtasticd only allows ONE TCP client — a zombie connection
                    # blocks all reconnection. Detect and clear it early (3 attempts
                    # ≈ 7 seconds) instead of waiting for all 10 to exhaust.
                    if self._reconnect.attempts == 3:
                        cleared = clear_stale_connections(self.config.meshtastic.port)
                        if cleared:
                            self.health.record_connection_event(
                                "meshtastic", "self_healed",
                                "Cleared zombie CLOSE-WAIT connection"
                            )
                            self._reconnect.reset()

                    logger.info(f"Attempting Meshtastic connection "
                               f"(attempt {self._reconnect.attempts + 1})...")
                    self.health.record_connection_event("meshtastic", "retry")
                    self.connect()

                    if self._connected:
                        self._reconnect.record_success()
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("Meshtastic connection established")
                    else:
                        self._reconnect.record_failure()
                        self._reconnect.wait(self._stop_event)
                        continue

                if self._connected:
                    self._poll()

                self._stop_event.wait(1)

            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                category = self.health.record_error("meshtastic", e)
                logger.warning(f"Meshtastic connection error ({category}): {e}")
                self._handle_connection_lost()
                self.health.record_connection_event("meshtastic", "disconnected", str(e))
                self._reconnect.record_failure()
                self._reconnect.wait(self._stop_event)
            except Exception as e:
                category = self.health.record_error("meshtastic", e)
                logger.error(f"Meshtastic loop error ({category}): {e}")
                self._connected = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._reconnect.record_failure()
                self._reconnect.wait(self._stop_event)

    def connect(self) -> bool:
        """
        Connect to Meshtastic via TCP using singleton connection manager.

        Returns:
            True if connection successful, False otherwise.
        """
        if not _HAS_PUBSUB:
            logger.warning("pubsub not available, using CLI fallback")
            self._connected = self._test_cli()
            return self._connected

        # Advisory pre-flight: warn if meshtasticd not detected, but attempt
        # connection anyway — service may be running outside systemd (Docker, manual)
        status = check_service('meshtasticd')
        if not status.available:
            logger.warning("meshtasticd service check: %s (attempting connection anyway)",
                           status.message)
            if status.fix_hint:
                logger.info("Fix: %s", status.fix_hint)

        try:
            host = self.config.meshtastic.host
            port = self.config.meshtastic.port

            logger.info(f"Connecting to Meshtastic at {host}:{port}")

            # Use singleton connection manager to prevent connection conflicts
            # meshtasticd only allows ONE TCP client - this ensures we share
            self._conn_manager = get_connection_manager(host, port)

            # Acquire persistent connection (stays open for message receiving)
            if not self._conn_manager.acquire_persistent(owner="gateway_bridge"):
                logger.error("Could not acquire persistent Meshtastic connection")
                self._connected = False
                return False

            # Get the interface for operations
            self._interface = self._conn_manager.get_interface()

            if self._interface is None:
                logger.error("Failed to get Meshtastic interface from connection manager")
                self._connected = False
                return False

            # Subscribe to messages (store reference for proper unsubscribe)
            def on_receive(packet, interface):
                self._on_receive(packet)

            self._pubsub_handler = on_receive
            pub.subscribe(self._pubsub_handler, "meshtastic.receive")

            # Get initial node list
            self._update_nodes()

            self._connected = True
            logger.info("Connected to Meshtastic via connection manager")
            self._notify_status("meshtastic_connected")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect from Meshtastic via connection manager."""
        # Unsubscribe from pub/sub
        try:
            from pubsub import pub
            if self._pubsub_handler:
                pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                self._pubsub_handler = None
        except Exception:
            pass

        # Release persistent connection through the manager
        if self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing persistent connection: {e}")

        self._interface = None
        self._connected = False

    def send_text(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """
        Send a text message to Meshtastic network.

        Args:
            message: Text content to send
            destination: Destination node ID (None for broadcast)
            channel: Channel index to send on

        Returns:
            True if message sent successfully, False otherwise.
        """
        if not self._connected:
            logger.warning("Not connected to Meshtastic")
            return False

        try:
            if self._interface:
                # For broadcasts, use ^all instead of None
                dest = destination if destination else "^all"
                logger.info(f"Sending to Meshtastic: dest={dest}, ch={channel}, msg={message[:50]}")
                result = self._interface.sendText(
                    message,
                    destinationId=dest,
                    channelIndex=channel
                )
                if result is None or result is False:
                    logger.warning(f"sendText returned {result} — TX may have failed "
                                   f"(dest={dest}, ch={channel})")
                    return False
                logger.debug(f"sendText returned: {result}")
                return True
            else:
                # Fallback to CLI
                return self._send_via_cli(message, destination, channel)
        except Exception as e:
            logger.error(f"Failed to send to Meshtastic: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue - Meshtastic destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if sent successfully, False otherwise.
        """
        message = payload.get('message', '')
        destination = payload.get('destination')
        channel = payload.get('channel', 0)

        if not self._connected:
            return False

        try:
            if self._interface:
                dest = destination if destination else "^all"
                result = self._interface.sendText(message, destinationId=dest, channelIndex=channel)
                if result is None or result is False:
                    logger.warning(f"Queue sendText returned {result} — TX may have failed")
                    return False
                return True
            return False
        except Exception as e:
            logger.error(f"Queue send to Meshtastic failed: {e}")
            return False

    def test_connection(self) -> bool:
        """
        Test Meshtastic connection via TCP socket.

        Returns:
            True if connection test passes, False otherwise.
        """
        sock = None
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((
                self.config.meshtastic.host,
                self.config.meshtastic.port
            ))
            return result == 0
        except (OSError, Exception) as e:
            logger.debug(f"Meshtastic connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception as e:
                    logger.debug(f"Socket close during cleanup: {e}")

    def _on_receive(self, packet: dict) -> None:
        """Handle incoming Meshtastic message."""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Update node info
            from_id = packet.get('fromId')
            if from_id:
                node = UnifiedNode.from_meshtastic({
                    'num': int(from_id[1:], 16) if from_id.startswith('!') else 0,
                    'snr': packet.get('rxSnr'),
                    'hopsAway': packet.get('hopStart', 0) - packet.get('hopLimit', 0),
                })
                self.node_tracker.add_node(node)

            # Extract relay node (Meshtastic 2.6+)
            relay_node = packet.get('relayNode')
            if relay_node and relay_node > 0:
                self._discover_relay_node(relay_node, from_id, packet)

            # Handle text messages
            if portnum == 'TEXT_MESSAGE_APP':
                self._handle_text_message(packet, decoded, from_id)

        except Exception as e:
            logger.error(f"Error processing Meshtastic message: {e}")

    def _handle_text_message(self, packet: dict, decoded: dict, from_id: str) -> None:
        """Process a text message from Meshtastic."""
        # Import BridgedMessage locally to avoid circular imports
        from .rns_bridge import BridgedMessage

        payload = decoded.get('payload', b'')
        if isinstance(payload, bytes):
            text = payload.decode('utf-8', errors='ignore')
        else:
            text = str(payload)

        to_id = packet.get('toId')
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id=from_id,
            destination_id=to_id,
            content=text,
            is_broadcast=to_id == '!ffffffff',
            metadata={
                'channel': packet.get('channel', 0),
                'snr': packet.get('rxSnr'),
            }
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            # Convert broadcast marker to None
            if to_id == '!ffffffff' or to_id == '^all':
                to_id = None
            messaging.store_incoming(
                from_id=from_id,
                content=text,
                network="meshtastic",
                to_id=to_id,
                channel=packet.get('channel', 0),
                snr=packet.get('rxSnr'),
                rssi=packet.get('rxRssi'),
            )
        except Exception as e:
            logger.debug(f"Could not store incoming message: {e}")

        # Broadcast to WebSocket for real-time web UI updates
        try:
            broadcast_message({
                'from_id': from_id,
                'to_id': to_id,
                'content': text,
                'channel': packet.get('channel', 0),
                'snr': packet.get('rxSnr'),
                'rssi': packet.get('rxRssi'),
                'timestamp': datetime.now().isoformat(),
                'is_broadcast': to_id is None,
            })
        except Exception as e:
            logger.debug(f"Could not broadcast to WebSocket: {e}")

        # Queue for bridging if routing rules allow it (non-blocking to prevent deadlock)
        if self._mesh_to_rns_queue is not None:
            # Check routing rules before queueing
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"Message from {from_id} blocked by routing rules")
            else:
                try:
                    self._mesh_to_rns_queue.put_nowait(msg)
                except Full:
                    logger.warning("Mesh→RNS queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

        # Notify callback
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _discover_relay_node(self, relay_byte: int, from_id: str, packet: dict) -> None:
        """
        Discover relay node from Meshtastic 2.6+ relayNode field.

        The relayNode field only contains the last byte of the relay node's ID.
        We try to match it against known nodes or create a placeholder.
        """
        try:
            if relay_byte <= 0 or relay_byte > 255:
                return

            # Try to find existing node matching this last byte
            for node in self.node_tracker.get_meshtastic_nodes():
                if node.meshtastic_id:
                    try:
                        node_num = int(node.meshtastic_id[1:], 16)
                        if (node_num & 0xFF) == relay_byte:
                            # Found the relay node - update topology
                            if self._network_topology and from_id:
                                self._network_topology.add_edge(
                                    source_id=node.id,
                                    dest_id=from_id,
                                    hops=1,
                                    snr=packet.get('rxSnr'),
                                    rssi=packet.get('rxRssi'),
                                )
                            logger.debug(f"Relay path: {node.meshtastic_id} -> {from_id}")
                            return
                    except (ValueError, TypeError):
                        continue

            # No match - create partial relay node for tracking
            partial_id = f"!????{relay_byte:02x}"
            node = UnifiedNode(
                id=partial_id,
                name=f"Relay-{relay_byte:02x}",
                network="meshtastic",
                meshtastic_id=partial_id,
            )
            self.node_tracker.add_node(node)

            # Add topology edge from unknown relay to sender
            if self._network_topology and from_id:
                self._network_topology.add_edge(
                    source_id=partial_id,
                    dest_id=from_id,
                    hops=1,
                    snr=packet.get('rxSnr'),
                    rssi=packet.get('rxRssi'),
                )

            logger.info(f"Discovered relay node via packet routing: {partial_id}")

        except Exception as e:
            logger.debug(f"Error discovering relay node: {e}")

    def _poll(self) -> None:
        """Poll Meshtastic for health check and updates."""
        if self._interface:
            try:
                # Check if interface is still connected
                if hasattr(self._interface, 'isConnected'):
                    if not self._interface.isConnected:
                        logger.warning("Meshtastic connection lost (isConnected=False)")
                        self._handle_connection_lost()
                        return
                # Also check if we can access basic properties (catches broken pipes)
                if hasattr(self._interface, 'nodes'):
                    _ = len(self._interface.nodes)
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"Meshtastic connection lost: {e}")
                self._handle_connection_lost()
                return
            except Exception as e:
                logger.debug(f"Meshtastic health check error: {e}")

    def _handle_connection_lost(self) -> None:
        """Handle lost meshtastic connection - cleanup and prepare for reconnect."""
        logger.info("Handling lost Meshtastic connection...")
        self._connected = False

        # Release the persistent connection properly
        if self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing connection after loss: {e}")

        # Unsubscribe from pub/sub to avoid stale callbacks
        try:
            from pubsub import pub
            if self._pubsub_handler:
                pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                self._pubsub_handler = None
        except Exception as e:
            logger.debug(f"Pubsub unsubscribe error: {e}")

        self._interface = None
        self._notify_status("meshtastic_disconnected")

        # Wait for cooldown before reconnect attempt
        wait_for_cooldown()

    def _update_nodes(self) -> None:
        """Update node tracker with Meshtastic nodes."""
        if not self._interface:
            return

        try:
            my_info = self._interface.getMyNodeInfo()
            my_id = my_info.get('num', 0)

            for node_id, node_data in self._interface.nodes.items():
                is_local = node_data.get('num') == my_id
                node = UnifiedNode.from_meshtastic(node_data, is_local=is_local)
                self.node_tracker.add_node(node)

        except Exception as e:
            logger.error(f"Error updating Meshtastic nodes: {e}")

    def _test_cli(self) -> bool:
        """Test Meshtastic CLI availability."""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("Meshtastic CLI not found")
                return False

            result = subprocess.run(
                [cli_path, '--info'],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"Meshtastic CLI test failed: {e}")
            return False

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send via Meshtastic CLI as fallback."""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli() or 'meshtastic'
            cmd = [cli_path, '--host', self.config.meshtastic.host, '--sendtext', message]
            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")
