"""
MeshCore Bridge Mixin - MeshCore-specific message bridging logic.

Extracted from rns_bridge.py to reduce file size per CLAUDE.md guidelines.
Provides _process_meshcore_to_bridge and _process_bridge_to_meshcore methods
that route messages between MeshCore and other networks (Meshtastic, RNS).
"""

import logging
from typing import Optional

from .bridge_health import SubsystemState, MessageOrigin

logger = logging.getLogger(__name__)


class MeshCoreBridgeMixin:
    """Mixin providing MeshCore-specific bridge processing methods.

    Expects the following attributes on the host class:
    - self._meshcore_handler: MeshCoreHandler instance (or None)
    - self.health: BridgeHealthMonitor
    - self.stats: dict with stats counters
    - self._stats_lock: threading.Lock
    - self.config: GatewayConfig
    - self.send_to_meshtastic(): method
    - self.send_to_rns(): method
    - self._notify_message(): method
    - self._requeue_failed_message(): method
    """

    def _meshcore_loop(self):
        """Main loop for MeshCore connection - delegates to handler."""
        if self._meshcore_handler:
            self._meshcore_handler.run_loop()

    def send_to_meshcore(self, message: str, destination: str = None,
                         channel: int = 0) -> bool:
        """Send a message to MeshCore network.

        Args:
            message: Text content to send
            destination: Destination address (None for broadcast)
            channel: Channel index

        Returns:
            True if queued successfully, False otherwise.
        """
        if not self._meshcore_handler:
            logger.warning("MeshCore handler not initialized")
            return False
        return self._meshcore_handler.send_text(message, destination, channel)

    def _process_meshcore_to_bridge(self, msg) -> None:
        """Process message from MeshCore → other networks (Meshtastic, RNS).

        MeshCore messages arrive as CanonicalMessage or BridgedMessage.
        Routes to Meshtastic and/or RNS based on routing rules.
        """
        try:
            # Extract content — handle both CanonicalMessage and BridgedMessage
            if hasattr(msg, 'source_address'):
                # CanonicalMessage
                src_label = msg.source_address[:8] if msg.source_address else 'unknown'
                content = msg.content
                is_broadcast = msg.is_broadcast
                via_internet = getattr(msg, 'via_internet', False)
            else:
                # BridgedMessage
                src_label = msg.source_id[:8] if msg.source_id else 'unknown'
                content = msg.content
                is_broadcast = msg.is_broadcast
                via_internet = getattr(msg, 'via_internet', False)

            prefix = f"[MC:{src_label}] "
            bridged_content = prefix + content

            # Route to Meshtastic
            mesh_state = self.health.get_subsystem_state("meshtastic")
            if mesh_state not in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                if self.send_to_meshtastic(bridged_content,
                                           channel=self.config.meshtastic.channel):
                    logger.info(f"Bridge MC→Mesh: {bridged_content[:50]}...")
                    with self._stats_lock:
                        self.stats.setdefault('messages_meshcore_to_mesh', 0)
                        self.stats['messages_meshcore_to_mesh'] += 1
                    self.health.record_message_sent("meshcore_to_mesh")
                else:
                    logger.warning("Failed to bridge MC→Mesh")
                    with self._stats_lock:
                        self.stats['errors'] += 1
                    self.health.record_message_failed("meshcore_to_mesh", requeued=False)

            # Route to RNS — fan the MeshCore broadcast out to EACH configured
            # default_lxmf_destination. LXMF is point-to-point (no native broadcast),
            # so a bare send_to_rns(content) with no destination always dropped; mirror
            # the Meshtastic Mesh→RNS fan-out (get_lxmf_destinations(), str|list).
            rns_state = self.health.get_subsystem_state("rns")
            if rns_state not in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                dests = []
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        dests.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")
                sent_count = sum(1 for dh in dests if self.send_to_rns(bridged_content, dh))
                if sent_count:
                    if len(dests) > 1:
                        logger.info(f"Bridge MC→RNS: {sent_count}/{len(dests)} dest(s) — {bridged_content[:50]}...")
                    else:
                        logger.info(f"Bridge MC→RNS: {bridged_content[:50]}...")
                    with self._stats_lock:
                        self.stats.setdefault('messages_meshcore_to_rns', 0)
                        self.stats['messages_meshcore_to_rns'] += 1
                    self.health.record_message_sent("meshcore_to_rns")
                else:
                    logger.debug("MC→RNS: not delivered (no default_lxmf_destination or unreachable)")

        except Exception as e:
            logger.error(f"Error bridging MeshCore→Bridge: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1

    def _process_bridge_to_meshcore(self, msg) -> None:
        """Process message from other networks → MeshCore.

        Handles text truncation to MeshCore's ~160 byte limit.
        Filters internet-originated messages (MeshCore is pure radio).
        """
        try:
            # Check internet origin filter — MeshCore is pure radio
            if hasattr(msg, 'via_internet') and msg.via_internet:
                logger.debug("Dropping internet-origin message destined for MeshCore")
                return
            if hasattr(msg, 'origin') and msg.origin == MessageOrigin.MQTT:
                logger.debug("Dropping MQTT-origin message destined for MeshCore")
                return

            # Extract content
            if hasattr(msg, 'source_address'):
                src_net = msg.source_network
                src_label = msg.source_address[:8] if msg.source_address else 'unknown'
                content = msg.content
            else:
                src_net = msg.source_network
                src_label = msg.source_id[:8] if msg.source_id else 'unknown'
                content = msg.content

            net_prefix = "Mesh" if src_net == "meshtastic" else "RNS"
            prefix = f"[{net_prefix}:{src_label}] "
            bridged_content = prefix + content

            # MeshCore has ~160 byte text limit — truncate if needed
            if len(bridged_content.encode('utf-8')) > 160:
                from .canonical_message import _truncate_utf8
                bridged_content = _truncate_utf8(bridged_content, 160)

            if self.send_to_meshcore(bridged_content):
                direction = f"{src_net[:4]}_to_meshcore"
                logger.info(f"Bridge {net_prefix}→MC: {bridged_content[:50]}...")
                with self._stats_lock:
                    key = f'messages_{src_net}_to_meshcore'
                    self.stats.setdefault(key, 0)
                    self.stats[key] += 1
                self.health.record_message_sent(f"mesh_to_meshcore"
                                                if src_net == "meshtastic"
                                                else "rns_to_meshcore")
            else:
                logger.warning(f"Failed to bridge {net_prefix}→MC")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "meshcore")
                self.health.record_message_failed(
                    f"mesh_to_meshcore" if src_net == "meshtastic" else "rns_to_meshcore",
                    requeued=requeued,
                )

        except Exception as e:
            logger.error(f"Error bridging →MeshCore: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
