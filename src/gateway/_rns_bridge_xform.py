"""Mesh ↔ RNS message transformation for RNSMeshtasticBridge.

Extracted from rns_bridge.py for file size compliance (CLAUDE.md #6).

Owns the bidirectional message conversion between Meshtastic and RNS/LXMF:
identity surfacing in LXMF envelopes, ``@address`` directed downlink
parsing, and persistent-queue requeuing on send failure.

Host class must provide:
- self.config (GatewayConfig)
- self.node_tracker (UnifiedNodeTracker)
- self.stats, self._stats_lock
- self.health (BridgeHealthMonitor)
- self._persistent_queue (PersistentMessageQueue or None)
- self.send_to_rns(content, destination_hash, *, title, fields)
- self.send_to_meshtastic(content, destination, channel)
- self._get_rns_destination(meshtastic_id) (defined here; override-able)
"""

import logging
import re
from typing import Optional

from .message_queue import MessagePriority

logger = logging.getLogger(__name__)


class MessageTransformMixin:
    """Mixin: bidirectional Mesh↔RNS message processing + queue requeue."""

    def _process_mesh_to_rns(self, msg):
        """Process message from Meshtastic to RNS.

        Identity is carried in the LXMF title and fields dict so that
        RNS peers (NomadNet etc.) can distinguish which Meshtastic node
        a bridged message originated from. Body stays clean.

        On send failure for non-broadcast messages, attempts to persist
        to the persistent queue for later retry.
        """
        try:
            content = msg.content

            long_name = ""
            short_name = ""
            if msg.source_id and getattr(self, 'node_tracker', None):
                try:
                    node = self.node_tracker.get_node_by_mesh_id(msg.source_id)
                    if node:
                        long_name = node.name or ""
                        short_name = getattr(node, 'short_name', '') or ""
                except Exception as e:
                    logger.debug(f"node_tracker lookup failed for {msg.source_id}: {e}")

            source_id = msg.source_id or "unknown"
            if long_name:
                title = f"{long_name} ({source_id}) via Meshtastic"
            else:
                title = f"{source_id} via Meshtastic"

            fields = {
                "meshforge_from_id": source_id,
                "meshforge_from_long": long_name,
                "meshforge_from_short": short_name,
                "meshforge_channel": (msg.metadata or {}).get("channel", ""),
                "meshforge_source_network": "meshtastic",
            }

            # Build destination list. Direct DM short-circuits to a single recipient;
            # broadcast fans out to every default_lxmf_destination configured.
            destinations: list = []
            if msg.destination_id and not msg.is_broadcast:
                direct = self._get_rns_destination(msg.destination_id)
                if direct:
                    destinations.append(direct)

            if not destinations:
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        destinations.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")

            sent_count = 0
            for dest_hash in destinations:
                if self.send_to_rns(content, dest_hash, title=title, fields=fields):
                    sent_count += 1

            if sent_count:
                if len(destinations) > 1:
                    logger.info(f"Bridge Mesh→RNS: {title} → {sent_count}/{len(destinations)} dest(s) — {content[:50]}")
                else:
                    logger.info(f"Bridge Mesh→RNS: {title} — {content[:50]}")
                with self._stats_lock:
                    self.stats['messages_mesh_to_rns'] += 1
                self.health.record_message_sent("mesh_to_rns")
            elif msg.is_broadcast:
                # Broadcast that didn't land — debug-only, not an error. Could be no
                # default_lxmf_destination configured, or all configured peers were
                # unreachable; either way, broadcast best-effort delivery is fine.
                logger.debug(f"Mesh→RNS broadcast not delivered: {content[:30]}...")
            else:
                logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "rns")
                self.health.record_message_failed("mesh_to_rns", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("rns", e)
            self._requeue_failed_message(msg, "rns")
            self.health.record_message_failed("mesh_to_rns", requeued=True)

    def _get_rns_destination(self, meshtastic_id: str) -> Optional[bytes]:
        """Look up RNS destination hash for a Meshtastic node ID."""
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        return None

    def _requeue_failed_message(self, msg, destination: str) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send (BridgedMessage or CanonicalMessage).
            destination: Target network ("meshtastic", "rns", or "meshcore").

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
            source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
            content = msg.content
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            elif not isinstance(content, str):
                content = ""
            self._persistent_queue.enqueue(
                payload={
                    'message': content,
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

    def _resolve_mesh_destination(self, addr_token: str) -> Optional[str]:
        """Resolve an ``@address`` token to a Meshtastic node id.

        Accepts ``!abcdef12`` (hex id) directly, or a short_name that
        node_tracker can uniquely resolve. Returns the canonical
        ``!xxxxxxxx`` form, or None when unresolvable or ambiguous —
        the caller falls through to broadcast on None rather than
        silently misdelivering.
        """
        if not addr_token:
            return None
        hex_match = re.match(r'^!([0-9a-fA-F]{8})$', addr_token)
        if hex_match:
            return f"!{hex_match.group(1).lower()}"
        if getattr(self, 'node_tracker', None):
            try:
                node = self.node_tracker.get_node_by_short_name(addr_token)
                if node and node.meshtastic_id:
                    return node.meshtastic_id
            except Exception as e:
                logger.debug(f"short_name resolve failed for {addr_token!r}: {e}")
        return None

    def _process_rns_to_mesh(self, msg):
        """Process message from RNS to Meshtastic.

        Supports directed downlink via a leading ``@!xxxxxxxx`` or
        ``@shortname`` token in the message body. When resolvable, the
        token is stripped and the message is sent as a Meshtastic DM
        to that node. Unresolvable or absent = broadcast (unchanged).

        In mqtt_bridge mode, routes through the persistent queue for
        reliable delivery with retry. Otherwise sends directly and
        persists to queue on failure.
        """
        # Lazy import to keep mixin file slim and avoid circular import
        # back into rns_bridge for the HAS_PERSISTENT_QUEUE flag.
        from . import rns_bridge as _rns_bridge_module
        HAS_PERSISTENT_QUEUE = _rns_bridge_module.HAS_PERSISTENT_QUEUE

        try:
            raw = msg.content
            if isinstance(raw, bytes):
                body = raw.decode("utf-8", errors="replace")
            elif isinstance(raw, str):
                body = raw
            else:
                body = ""
            destination = None
            if body.startswith('@'):
                parts = body.split(None, 1)
                if len(parts) == 2:
                    addr_token = parts[0][1:]
                    resolved = self._resolve_mesh_destination(addr_token)
                    if resolved:
                        destination = resolved
                        body = parts[1]
                    else:
                        logger.info(
                            f"RNS→Mesh: @address {addr_token!r} unresolved, "
                            f"falling through to broadcast"
                        )

            prefix = f"[RNS:{msg.source_id[:4]}] "
            content = prefix + body

            # In mqtt_bridge mode, use persistent queue for reliable delivery
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                payload = {
                    'message': content,
                    'channel': self.config.meshtastic.channel,
                    'source_id': msg.source_id,
                    'destination': destination,
                }
                msg_id = self._persistent_queue.enqueue(
                    payload=payload,
                    destination="meshtastic",
                    priority=MessagePriority.NORMAL,
                )
                if msg_id:
                    tag = f" -> {destination}" if destination else ""
                    logger.info(f"Bridge RNS→Mesh (queued{tag}): {content[:50]}...")
                    with self._stats_lock:
                        self.stats['messages_rns_to_mesh'] += 1
                    self.health.record_message_sent("rns_to_mesh")
                    return

            # Direct send (non-MQTT mode or queue unavailable)
            if self.send_to_meshtastic(
                content,
                destination=destination,
                channel=self.config.meshtastic.channel,
            ):
                tag = f" -> {destination}" if destination else ""
                logger.info(f"Bridge RNS→Mesh{tag}: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                self.health.record_message_sent("rns_to_mesh")
            else:
                logger.warning("Failed to bridge RNS→Mesh")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "meshtastic")
                self.health.record_message_failed("rns_to_mesh", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("meshtastic", e)
            self._requeue_failed_message(msg, "meshtastic")
            self.health.record_message_failed("rns_to_mesh", requeued=True)
