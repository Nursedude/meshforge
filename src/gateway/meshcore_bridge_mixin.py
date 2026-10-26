"""
MeshCore Bridge Mixin - MeshCore-specific message bridging logic.

Extracted from rns_bridge.py to reduce file size per CLAUDE.md guidelines.
Provides _process_meshcore_to_bridge and _process_bridge_to_meshcore methods
that route messages between MeshCore and other networks (Meshtastic, RNS).
"""

import logging
import time
from queue import Full
from typing import Optional

from .base_handler import is_already_bridged
from .bridge_health import SubsystemState, MessageOrigin

logger = logging.getLogger(__name__)

# One WARNING per window for a fan-out loss; DEBUG after (twin of MA's
# _meshcore_ingress.INGRESS_FULL_WARN_WINDOW_S).
FANOUT_WARN_WINDOW_S = 300.0


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
                         channel: int = 0, *,
                         source: Optional[str] = None) -> bool:
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
        return self._meshcore_handler.send_text(message, destination, channel,
                                                source=source)

    def _fan_out_to_meshcore(self, msg) -> None:
        """The ONE entry from the bridge loop onto the →MeshCore queue
        (bridge matrix F16 + F5). Decided on the MeshCore leg's own facts —
        never on what Mesh→RNS decided, so an RNS outage cannot starve it:

        - internet / MQTT origin: never a MeshCore attempt (quiet, as before);
        - a bridge-tagged Meshtastic line is already bridged — counted
          (on MF the Meshtastic/MQTT ingress already drops these before the
          Mesh→RNS queue, so this is a belt for any future producer);
        - a handler that cannot broadcast (MF on a real radio: no slot
          routing) — recorded ``dropped, unsendable`` on the delivery ledger
          and counted; never enqueued, so health cannot say *sent* and the
          persistent queue never retries it;
        - a full queue is a lost message — counted.
        """
        handler = self._meshcore_handler
        if not handler:
            return
        if getattr(msg, 'via_internet', False) or \
                getattr(msg, 'origin', None) == MessageOrigin.MQTT:
            return   # _process_bridge_to_meshcore's filter, applied early
        if getattr(msg, "source_network", None) == "meshtastic" and \
                is_already_bridged(getattr(msg, "content", None)):
            self._fanout_count('meshcore_fanout_echo_suppressed')
            logger.debug("→MeshCore: bridge-tagged Meshtastic line not fanned "
                         "out (already bridged): %r", str(msg.content)[:48])
            return
        # A handler without the attribute predates F5: enqueue as before.
        if not getattr(handler, 'can_broadcast', True):
            total = self._fanout_count('meshcore_fanout_unsendable')
            from .meshcore_handler import _record_tx
            _record_tx("dropped", None, "unsendable", "no slot routing",
                       source=self._leg_source(msg))
            self._fanout_warn(
                'unsendable',
                f"→MeshCore not sent: unsendable — MeshForge has no outbound "
                f"slot routing ({total} so far). For MeshCore-primary "
                f"bridging use MeshAnchor")
            return
        try:
            self._bridge_to_meshcore_queue.put_nowait(msg)
        except Full:
            total = self._fanout_count('meshcore_fanout_dropped_queue_full')
            self._fanout_warn('queue_full', f"→MeshCore queue full — message dropped "
                              f"({total} so far)")

    def _fanout_count(self, key: str) -> int:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + 1
            return self.stats[key]

    def _fanout_warn(self, reason: str, what: str) -> None:
        """WARNING once per window PER REASON, DEBUG after — a stalled or
        refused leg must not flood the journal, and one reason's warning
        must not hide another's."""
        now = time.monotonic()
        warned = self.__dict__.setdefault('_fanout_warned_at', {})
        last = warned.get(reason)
        if last is None or now - last >= FANOUT_WARN_WINDOW_S:
            warned[reason] = now
            logger.warning(f"{what}; further ones at DEBUG for "
                           f"{FANOUT_WARN_WINDOW_S:.0f}s")
        else:
            logger.debug(what)

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

            # Source-slot disclosure (2026-09-18): the INDEX the wire
            # delivered, carried on metadata by from_meshcore. '?' means the
            # payload named no slot — unknown, never assumed Public.
            _meta = getattr(msg, 'metadata', None) or {}
            _idx = _meta.get('channel') if isinstance(_meta, dict) else None
            ch_tag = f"[ch:{'?' if _idx is None else _idx}]"
            prefix = f"[MC:{src_label}] "
            bridged_content = prefix + content

            # Route to Meshtastic
            mesh_state = self.health.get_subsystem_state("meshtastic")
            if mesh_state not in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                if self.send_to_meshtastic(bridged_content,
                                           channel=self.config.meshtastic.channel,
                                           source="meshcore"):
                    logger.info(f"Bridge MC→Mesh {ch_tag}: {bridged_content[:50]}...")
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
                sent_count = sum(1 for dh in dests if self.send_to_rns(bridged_content, dh,
                                                                  source="meshcore"))
                if sent_count:
                    if len(dests) > 1:
                        logger.info(f"Bridge MC→RNS {ch_tag}: {sent_count}/{len(dests)} dest(s) — {bridged_content[:50]}...")
                    else:
                        logger.info(f"Bridge MC→RNS {ch_tag}: {bridged_content[:50]}...")
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

            # The leg (a TRANSPORT) via the one _leg_source derivation every
            # send path uses (review R3). The stats / health keys below stay
            # on src_net — they count ROUTING networks, a different axis.
            if self.send_to_meshcore(bridged_content,
                                     source=self._leg_source(msg)):
                direction = f"{src_net[:4]}_to_meshcore"
                logger.info(f"Bridge {net_prefix}→MC: {bridged_content[:50]}...")
                with self._stats_lock:
                    key = f'messages_{src_net}_to_meshcore'
                    self.stats.setdefault(key, 0)
                    self.stats[key] += 1
                # No health "sent" here: send_to_meshcore returns True on
                # ENQUEUE (bridge matrix F5). The TX worker records the
                # outcome as to_meshcore sent / failed.
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
