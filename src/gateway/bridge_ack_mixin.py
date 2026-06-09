"""ACK synthesis / correlation-sweep mixin for :class:`RNSMeshtasticBridge`.

Part of the 2026-06-09 ``rns_bridge.py`` split (1,500-line rule,
``CLAUDE.md``). Pure code motion — no behaviour change.
``RNSMeshtasticBridge`` in ``rns_bridge.py`` is the only consumer.

Provided methods: ``_register_lxmf_delivery_callbacks``,
``_format_ack_text``, ``_emit_ack_to_origin``, ``_maybe_emit_ack_for_msgid``,
``_sweep_overdue_acks``, ``_sweep_expired_sessions``,
``_sweep_expired_correlations``, ``_sweep_expired_acks`` (plus the
``_ACK_TEXT_*`` class attributes).
"""

import logging

from gateway import delivery_counters as _dc

logger = logging.getLogger(__name__)


class BridgeAckMixin:
    """Mixin: LXMF delivery-proof callbacks + Issue #66 ACK synthesis +
    periodic ack/session/correlation sweeps."""

    def _register_lxmf_delivery_callbacks(
        self,
        lxm,
        msg_id: str,
        destination_hash: bytes,
        msg_preview: str,
    ) -> None:
        """Wire CONFIRMED + DROPPED(rns_delivery_failed) onto an LXMessage.

        Called from BOTH send_to_rns() (direct send) and _queue_send_rns()
        (persistent-queue retry). The symmetry is the load-bearing contract:
        without it, queue-retried sends would bump SENT but never CONFIRMED,
        biasing /api/gateway/delivery.confirmation_rate downward. See
        TestDeliveryCallbackSymmetry in tests/test_regression_guards.py for
        the source-shape guard.

        Issue #66: on_delivered and on_failed also call _maybe_emit_ack_for_msgid
        so messages with ack_required=True (and a registered pending-ack
        record in the queue) get a synthetic ACK CanonicalMessage routed
        back to the origin protocol. Idempotent — mark_acked() returns None
        on the second call to prevent double-emission.
        """
        self.delivery_tracker.track_message(
            msg_id, destination_hash, msg_preview
        )

        def on_delivered(receipt):
            self.delivery_tracker.confirm_delivery(msg_id)
            _dc.record(
                _dc.DeliveryState.CONFIRMED,
                msg_id=msg_id,
                protocol="rns",
            )
            self._maybe_emit_ack_for_msgid(msg_id, kind='delivered')

        def on_failed(receipt):
            reason = "delivery_failed"
            if hasattr(receipt, 'failure_reason'):
                reason = str(receipt.failure_reason)
            self.delivery_tracker.confirm_failure(msg_id, reason)
            _dc.record(
                _dc.DeliveryState.DROPPED,
                msg_id=msg_id,
                protocol="rns",
                drop_reason=_dc.DropReason.RNS_DELIVERY_FAILED,
                note=reason[:80],
            )
            self._maybe_emit_ack_for_msgid(msg_id, kind='failed')

        try:
            lxm.register_delivery_callback(on_delivered)
            lxm.register_failed_callback(on_failed)
        except (AttributeError, TypeError):
            logger.debug(
                "LXMF callbacks not available, skipping delivery tracking"
            )

    # --- Issue #66: application-layer ack synthesis ---------------------

    # Textual ACK forms. Operators see these in their chat threads. Short
    # so they don't crowd out the actual conversation; recognizable so
    # they're easy to filter or collapse client-side.
    _ACK_TEXT_DELIVERED = "[delivered: {short_id}]"
    _ACK_TEXT_FAILED = "[failed: {short_id}]"
    _ACK_TEXT_TIMEOUT = "[timeout: {short_id}]"

    def _format_ack_text(self, msg_id: str, kind: str) -> str:
        """Compose the operator-visible ACK string. Public for tests."""
        short_id = (msg_id or "")[:8]
        if kind == 'delivered':
            return self._ACK_TEXT_DELIVERED.format(short_id=short_id)
        if kind == 'failed':
            return self._ACK_TEXT_FAILED.format(short_id=short_id)
        if kind == 'timeout':
            return self._ACK_TEXT_TIMEOUT.format(short_id=short_id)
        return f"[{kind}: {short_id}]"

    def _emit_ack_to_origin(
        self,
        msg_id: str,
        origin_network: str,
        origin_address: str,
        kind: str,
    ) -> bool:
        """
        Send a synthetic ACK message back to the origin sender.

        Args:
            msg_id: id of the message being acked (for the textual body).
            origin_network: "meshtastic" / "meshcore" / "rns".
            origin_address: protocol-native address on origin_network.
            kind: "delivered" / "failed" / "timeout".

        Returns:
            True if the synthetic ACK was dispatched to the right handler,
            False if the origin network is unsupported, the handler is
            absent, or the dispatch returned False.
        """
        text = self._format_ack_text(msg_id, kind)
        try:
            if origin_network == 'meshtastic':
                if not self._mesh_handler:
                    logger.debug(
                        "ack synthesis skipped: meshtastic handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                # Symmetric channel placeholder handling — mirrors the
                # MeshCore branch below. MeshtasticBroadcastBridge mints
                # the "channel:<idx>" origin_address when a broadcast
                # arrives without a usable source_address/source_id
                # (canonical_message synthesizes "!00000000" when fromId
                # is missing, so this is a rare edge path today, but the
                # asymmetry was a latent footgun — without parsing, the
                # placeholder string was passed as destinationId to
                # meshtastic-python, producing undefined behaviour).
                if origin_address.startswith("channel:"):
                    try:
                        ch_idx = int(origin_address.split(":", 1)[1])
                    except (ValueError, IndexError):
                        logger.warning(
                            "ack synthesis: bad channel placeholder %r "
                            "for msg_id=%s",
                            origin_address, msg_id[:8],
                        )
                        return False
                    return bool(self._mesh_handler.send_text(
                        text, destination=None, channel=ch_idx,
                    ))
                return bool(self._mesh_handler.send_text(
                    text, destination=origin_address, channel=0,
                ))
            if origin_network == 'meshcore':
                if not self._meshcore_handler:
                    logger.debug(
                        "ack synthesis skipped: meshcore handler absent "
                        f"(msg_id={msg_id[:8]} kind={kind})"
                    )
                    return False
                return bool(self._meshcore_handler.send_text(
                    text, destination=origin_address,
                ))
            if origin_network == 'rns':
                # origin_address is the hex string form of the destination
                # hash; convert back to bytes before send_to_rns().
                try:
                    dest_hash = bytes.fromhex(origin_address)
                except (TypeError, ValueError):
                    logger.warning(
                        "ack synthesis: bad RNS origin_address hex "
                        f"({origin_address!r}) for msg_id={msg_id[:8]}"
                    )
                    return False
                return bool(self.send_to_rns(text, destination_hash=dest_hash))
            logger.debug(
                f"ack synthesis: unknown origin_network={origin_network!r} "
                f"for msg_id={msg_id[:8]}"
            )
            return False
        except Exception as e:
            logger.warning(
                f"ack synthesis failed for msg_id={msg_id[:8]} "
                f"kind={kind} origin={origin_network}: {e}"
            )
            return False

    def _maybe_emit_ack_for_msgid(self, msg_id: str, kind: str) -> bool:
        """
        Convert delivery proof (or failure) into a synthetic ACK back to
        the origin sender — if and only if the message was registered
        via PersistentMessageQueue.register_pending_ack().

        Idempotent: mark_acked() returns None on the second call so we
        never emit twice for the same msg_id.

        Returns:
            True iff an ACK was emitted this call.
        """
        if not self._persistent_queue:
            return False
        try:
            origin = self._persistent_queue.mark_acked(msg_id)
        except Exception as e:
            logger.warning(
                f"ack synthesis: mark_acked failed for {msg_id[:8]}: {e}"
            )
            return False
        if not origin:
            return False
        return self._emit_ack_to_origin(
            msg_id,
            origin_network=origin['origin_network'],
            origin_address=origin['origin_address'],
            kind=kind,
        )

    def _sweep_overdue_acks(self) -> int:
        """
        Find pending-ack records past their deadline; emit a synthetic
        TIMEOUT ACK for each + finalise via mark_timeout().

        Called periodically from _bridge_loop (every ~30s) alongside
        delivery_tracker.check_timeouts(). Returns the count of TIMEOUT
        ACKs emitted this sweep so callers can plumb a stat / log line.
        """
        if not self._persistent_queue:
            return 0
        try:
            overdue = self._persistent_queue.find_overdue_acks()
        except Exception as e:
            logger.warning(f"ack sweep: find_overdue_acks failed: {e}")
            return 0
        emitted = 0
        for row in overdue:
            msg_id = row['message_id']
            try:
                # mark_timeout returns False if mark_acked() raced us;
                # in that case the delivered ACK already went out, skip.
                if not self._persistent_queue.mark_timeout(msg_id):
                    continue
            except Exception as e:
                logger.warning(
                    f"ack sweep: mark_timeout failed for {msg_id[:8]}: {e}"
                )
                continue
            self._emit_ack_to_origin(
                msg_id,
                origin_network=row['origin_network'],
                origin_address=row['origin_address'],
                kind='timeout',
            )
            emitted += 1
        if emitted:
            logger.info(
                f"ack sweep: emitted {emitted} TIMEOUT ACK(s)"
            )
        return emitted

    def _sweep_expired_sessions(self) -> int:
        """Prune idle/over-cap sessions (~every 30s from _bridge_loop).

        Theme-A step 3. Gated: a strict no-op when sessions are off —
        never opens gateway_sessions.db. Logs only when something was
        pruned. Never raises.
        """
        if not self._sessions_on():
            return 0
        try:
            removed = self._sessions.expire_idle()
        except Exception as e:
            logger.warning(f"session sweep failed: {e}")
            return 0
        if removed:
            logger.info(f"session sweep: pruned {removed} expired session(s)")
        return removed

    def _sweep_expired_correlations(self) -> int:
        """Prune expired/over-cap correlation rows (~every 30s from
        _bridge_loop).

        Thread-2 bidirectional addressability. Gated: a strict no-op when
        reply routing is off — never opens gateway_correlation.db. Logs
        only when something was pruned. Never raises.
        """
        if not self._reply_routing_on():
            return 0
        try:
            removed = self._correlation.expire_idle()
        except Exception as e:
            logger.warning(f"correlation sweep failed: {e}")
            return 0
        if removed:
            logger.info(
                f"correlation sweep: pruned {removed} expired row(s)")
        return removed

    def _sweep_expired_acks(self) -> int:
        """Prune never-acked in-flight DMs from the mesh ACK tracker
        (~every 30s from _bridge_loop).

        Thread-2 step 4. The tracker is in-memory and self-bounds on every
        register; this sweep reclaims entries on a gateway that sent DMs
        then went idle. A strict no-op when the handler is absent. Never
        raises.
        """
        try:
            if self._mesh_handler is None:
                return 0
            removed = self._mesh_handler.ack_tracker.expire_idle()
        except Exception as e:
            logger.warning(f"ack sweep failed: {e}")
            return 0
        if removed:
            logger.debug(f"ack sweep: pruned {removed} un-acked DM(s)")
        return removed
