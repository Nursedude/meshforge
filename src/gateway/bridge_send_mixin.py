"""Send/queue mixin for :class:`RNSMeshtasticBridge`.

Part of the 2026-06-09 ``rns_bridge.py`` split (1,500-line rule,
``CLAUDE.md``). Pure code motion — no behaviour change.
``RNSMeshtasticBridge`` in ``rns_bridge.py`` is the only consumer.

Provided methods: ``send_to_rns``, ``_queue_send_rns``,
``_dispatch_rns_xform_spill``, ``enqueue_message``, ``get_queue_stats``,
``_drain_persistent_queue``.
"""

import logging
import time
from typing import Dict, Optional

from gateway.bounded_rpc import bounded_call, default_on_wedge

logger = logging.getLogger(__name__)


class BridgeSendMixin:
    """Mixin: RNS/LXMF send paths + persistent-queue enqueue/drain."""

    def send_to_rns(
        self,
        message: str,
        destination_hash: bytes = None,
        title: str = None,
        fields: dict = None,
    ) -> bool:
        """Send a message to RNS network via LXMF.

        Optional ``title`` and ``fields`` let the caller carry structured
        sender identity (Mesh→RNS path uses this to surface the originating
        Meshtastic node's long_name + full id). When omitted, falls back
        to the legacy gateway-branded title with no extra fields — keeps
        the persistent-queue retry call site behavior unchanged.
        """
        if not self._connected_rns:
            logger.warning("Not connected to RNS")
            return False

        if self._lxmf_source is None:
            logger.warning("LXMF source not initialized (partial RNS init)")
            return False

        try:
            import RNS
            import LXMF

            if destination_hash:
                # Direct message
                hash_short = destination_hash.hex()[:8]
                # Issue #74: gate on the per-destination circuit BEFORE
                # any RNS RPC. A tripped/open circuit means this
                # destination recently wedged or failed repeatedly —
                # diving back into has_path/handle_outbound defeats the
                # breaker (it was write-only before this gate).
                if not self.can_send_to(hash_short):
                    logger.warning(
                        f"Send to {hash_short} blocked: circuit open "
                        f"(recent wedge/failures; retry after recovery window)"
                    )
                    return False
                # On-wedge composite: trip the circuit breaker for this
                # destination THEN run the default publish-+-counter hook.
                # The watchdog calls `os._exit(2)` after we return, so the
                # trip_open effect is process-local + brief — but it
                # still produces an observable side effect during the
                # abort window that tests with `exit_on_wedge=False`
                # rely on.
                def _on_wedge(label, target, timeout_s,
                              _hash=hash_short):
                    try:
                        if self._circuit_breaker is not None:
                            self._circuit_breaker.trip_open(
                                _hash, f"wedge:{label}"
                            )
                    except Exception:
                        # Isolated so the default publish+counter+abort
                        # still run — but VISIBLY (Issue #74): with
                        # exit_on_wedge=False a broken trip_open would
                        # otherwise be undetectable.
                        logger.exception(
                            "circuit trip_open failed during wedge "
                            "for %s", _hash,
                        )
                    default_on_wedge(label, target, timeout_s)
                if not bounded_call("rnsd.has_path",
                                    RNS.Transport.has_path, destination_hash,
                                    target=hash_short,
                                    timeout_s=3.0,
                                    on_wedge=_on_wedge):
                    bounded_call("rnsd.request_path",
                                 RNS.Transport.request_path, destination_hash,
                                 target=hash_short,
                                 timeout_s=5.0,
                                 on_wedge=_on_wedge)
                    # Wait briefly for path (interruptible on shutdown)
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        if self._stop_event.wait(0.1):
                            break

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    # Per-destination failure: feeds the threshold-based
                    # OPEN transition so repeated no-path sends stop
                    # hammering path requests (Issue #74).
                    self.record_send_failure(hash_short, "no path")
                    return False

                dest_identity = bounded_call("rnsd.identity_recall",
                                             RNS.Identity.recall,
                                             destination_hash,
                                             target=hash_short,
                                             timeout_s=3.0,
                                             on_wedge=_on_wedge)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                logger.info(
                    "Broadcast to RNS dropped: set rns.default_lxmf_destination "
                    "in gateway config to route broadcasts to one (string) or "
                    "many (list) LXMF peer(s)"
                )
                return False

            lxm = bounded_call(
                "rnsd.lxmessage_ctor",
                LXMF.LXMessage,
                destination,
                self._lxmf_source,
                message,
                title or "MeshForge Gateway",
                fields=fields,
                target=hash_short,
                timeout_s=5.0,
                on_wedge=_on_wedge,
            )

            msg_id = f"lxmf-{int(time.time() * 1000)}"
            self._register_lxmf_delivery_callbacks(
                lxm, msg_id, destination_hash, message[:50],
            )

            bounded_call("rnsd.handle_outbound",
                         self._lxmf_router.handle_outbound, lxm,
                         target=hash_short,
                         timeout_s=15.0,
                         on_wedge=_on_wedge)
            self.record_send_success(hash_short)
            return True

        except Exception as e:
            logger.error(f"Failed to send to RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            if destination_hash:
                self.record_send_failure(
                    destination_hash.hex()[:8], str(e)
                )
            return False

    def _queue_send_rns(self, payload: Dict) -> bool:
        """Send handler for persistent queue - RNS destination."""
        message = payload.get('message', '')
        destination_hash = payload.get('destination_hash')

        if not self._connected_rns:
            return False

        # Issue #74: circuit gate BEFORE the try block — the inner
        # except would swallow a raise into `return False`, which the
        # queue classifies as an unknown error (one short retry, then
        # dead-letter). Raising with a retriable-pattern message
        # ("temporarily unavailable") makes RetryPolicy back off and
        # retry after the circuit's recovery window instead.
        if destination_hash:
            _gate_key = (
                destination_hash.hex()[:8]
                if isinstance(destination_hash, bytes)
                else str(destination_hash).lower()[:8]
            )
            if not self.can_send_to(_gate_key):
                raise RuntimeError(
                    f"circuit open for {_gate_key}: "
                    f"destination temporarily unavailable"
                )

        try:
            import RNS
            import LXMF

            if not destination_hash:
                return False

            if isinstance(destination_hash, str):
                destination_hash = bytes.fromhex(destination_hash)
            hash_short = destination_hash.hex()[:8]

            def _on_wedge(label, target, timeout_s, _hash=hash_short):
                try:
                    if self._circuit_breaker is not None:
                        self._circuit_breaker.trip_open(
                            _hash, f"wedge:{label}"
                        )
                except Exception:
                    # See send_to_rns counterpart — visible failure
                    # over silent fallback (Issue #74).
                    logger.exception(
                        "circuit trip_open failed during wedge "
                        "for %s", _hash,
                    )
                default_on_wedge(label, target, timeout_s)

            if not bounded_call("rnsd.has_path",
                                RNS.Transport.has_path, destination_hash,
                                target=hash_short,
                                timeout_s=3.0,
                                on_wedge=_on_wedge):
                bounded_call("rnsd.request_path",
                             RNS.Transport.request_path, destination_hash,
                             target=hash_short,
                             timeout_s=5.0,
                             on_wedge=_on_wedge)
                for _ in range(30):
                    if RNS.Transport.has_path(destination_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            if not RNS.Transport.has_path(destination_hash):
                self.record_send_failure(hash_short, "no path")
                return False

            dest_identity = bounded_call("rnsd.identity_recall",
                                         RNS.Identity.recall, destination_hash,
                                         target=hash_short,
                                         timeout_s=3.0,
                                         on_wedge=_on_wedge)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            lxm = bounded_call(
                "rnsd.lxmessage_ctor",
                LXMF.LXMessage,
                destination, self._lxmf_source, message, "MeshForge Gateway",
                target=hash_short, timeout_s=5.0, on_wedge=_on_wedge,
            )

            # Carry the queue row's id through the syn/ack ledger so
            # history_for(msg_id) joins QUEUED (enqueue) → SENT
            # (mark_delivered) → CONFIRMED (LXMF delivery callback).
            # _queue_msg_id is injected at dispatch time by the queue's
            # _worker; fallback covers direct callers that bypass the
            # queue plumbing (mostly tests).
            msg_id = (
                payload.get('_queue_msg_id')
                or f"lxmf-{int(time.time() * 1000)}"
            )
            self._register_lxmf_delivery_callbacks(
                lxm, msg_id, destination_hash, message[:50],
            )

            bounded_call("rnsd.handle_outbound",
                         self._lxmf_router.handle_outbound, lxm,
                         target=hash_short,
                         timeout_s=15.0,
                         on_wedge=_on_wedge)
            self.record_send_success(hash_short)
            return True

        except Exception as e:
            logger.error(f"Queue send to RNS failed: {e}")
            if destination_hash:
                _fail_key = (
                    destination_hash.hex()[:8]
                    if isinstance(destination_hash, bytes)
                    else str(destination_hash).lower()[:8]
                )
                self.record_send_failure(_fail_key, str(e))
            return False

    def _dispatch_rns_xform_spill(self, payload: Dict) -> bool:
        """Persistent-queue sender for Hardening B's M→R spill.

        Reconstructs a BridgedMessage from the spilled payload and runs it
        through the standard _process_mesh_to_rns pipeline so it gets
        proper fan-out + attempted/delivered/dropped accounting. Returns
        True so the queue marks the row delivered after dispatch (the
        underlying xform will re-spill or count drops on its own failure
        paths — we don't re-loop overflow back into rns_xform).
        """
        # Lazy import — BridgedMessage is defined in rns_bridge, which
        # imports this mixin at module top (circular otherwise). Matches
        # the MessageTransformMixin idiom for the 2026-06-09 split.
        from . import rns_bridge as _rns_bridge_module
        BridgedMessage = _rns_bridge_module.BridgedMessage
        try:
            metadata = payload.get('metadata') or {}
            msg = BridgedMessage(
                source_network="meshtastic",
                source_id=payload.get('source_id', '') or '',
                destination_id=payload.get('destination_id') or None,
                content=payload.get('content', '') or '',
                title=payload.get('title'),
                is_broadcast=bool(payload.get('is_broadcast', False)),
                metadata=dict(metadata),
            )
            self._process_mesh_to_rns(msg)
            return True
        except Exception as e:
            logger.error(f"rns_xform spill dispatch failed: {e}")
            return False

    def enqueue_message(self, message: str, destination: str, dest_type: str = "meshtastic",
                        priority: str = "normal",
                        ack_required: bool = False,
                        ack_origin_network: Optional[str] = None,
                        ack_origin_address: Optional[str] = None,
                        ack_timeout_seconds: int = 300,
                        **kwargs) -> Optional[str]:
        """
        Enqueue a message for reliable delivery.

        Args:
            message: Message content
            destination: Destination ID/hash
            dest_type: "meshtastic" or "rns"
            priority: "low", "normal", "high", or "urgent"
            ack_required: Issue #66 — opt in to application-layer ack.
                When True (and both origin fields are set), a successful
                delivery callback emits a synthetic ACK CanonicalMessage
                back to (ack_origin_network, ack_origin_address). When
                the destination doesn't ack within ack_timeout_seconds
                the periodic sweep emits a TIMEOUT ACK instead.
            ack_origin_network: where to route the synthetic ACK back to
                ("meshtastic" / "meshcore" / "rns"). Required when
                ack_required=True.
            ack_origin_address: address on ack_origin_network. Required
                when ack_required=True.
            ack_timeout_seconds: how long before emitting a TIMEOUT ACK.
                Defaults to 5 minutes (PersistentMessageQueue default).
            **kwargs: Additional parameters (channel, etc.)

        Returns:
            Message ID if enqueued, None if queue unavailable
        """
        if not self._persistent_queue:
            # Fall back to direct send. ack_required is silently ignored
            # here — direct sends bypass the queue's pending-ack table,
            # so there's nothing to register. The textual fall-back is
            # already best-effort.
            if dest_type == "meshtastic":
                return "direct" if self.send_to_meshtastic(message, destination, kwargs.get('channel', 0)) else None
            else:
                dest_hash = kwargs.get('destination_hash')
                if isinstance(dest_hash, str):
                    dest_hash = bytes.fromhex(dest_hash)
                return "direct" if self.send_to_rns(message, dest_hash) else None

        # Map priority string to enum. MessagePriority is read lazily off
        # gateway.rns_bridge (the safe_import result lives there) so the
        # test idiom patch("gateway.rns_bridge.MessagePriority") keeps
        # governing this method after the 2026-06-09 split.
        from . import rns_bridge as _rns_bridge_module
        MessagePriority = _rns_bridge_module.MessagePriority
        priority_map = {
            "low": MessagePriority.LOW,
            "normal": MessagePriority.NORMAL,
            "high": MessagePriority.HIGH,
            "urgent": MessagePriority.URGENT,
        }
        msg_priority = priority_map.get(priority, MessagePriority.NORMAL)

        payload = {
            'message': message,
            'destination': destination,
            **kwargs
        }

        msg_id = self._persistent_queue.enqueue(
            payload=payload,
            destination=dest_type,
            priority=msg_priority
        )

        # Issue #66: register the pending-ack record so the LXMF
        # delivery callback / overdue sweep can synthesize back to
        # origin. Both origin fields must be present — silent skip
        # otherwise keeps the contract simple (caller forgot one, no
        # ack tracking, no surprise side effects).
        if (msg_id
                and ack_required
                and ack_origin_network
                and ack_origin_address):
            try:
                self._persistent_queue.register_pending_ack(
                    msg_id,
                    origin_network=ack_origin_network,
                    origin_address=ack_origin_address,
                    timeout_seconds=ack_timeout_seconds,
                )
            except Exception as e:
                logger.warning(
                    f"register_pending_ack failed for {msg_id[:8]}: {e}"
                )

        return msg_id

    def get_queue_stats(self) -> Dict:
        """Get persistent queue statistics."""
        if self._persistent_queue:
            return self._persistent_queue.get_stats()
        return {}

    def _drain_persistent_queue(self) -> None:
        """Process pending messages from the persistent queue.

        Called periodically from _bridge_loop when subsystems are healthy.
        Only drains messages destined for currently-connected subsystems.
        """
        if not self._persistent_queue:
            return
        try:
            self._persistent_queue.process_once(batch_size=5)
        except Exception as e:
            logger.warning(f"Persistent queue drain error: {e}")
