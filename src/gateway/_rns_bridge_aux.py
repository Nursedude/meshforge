"""Auxiliary lifecycle mixin for :class:`RNSMeshtasticBridge`.

Holds bolt-on helpers that aren't part of the core message-bridging flow:

- Hardening A channel-deployment-gap diagnostic (Issue #43 — surface a
  one-shot WARN when no MQTT uplink is observed on the configured bridge
  channel within the threshold window; the silent symptom shape behind
  moc3's frozen-stats stall, 2026-04-27).

Extracted from ``rns_bridge.py`` to keep that file under the 1,500-line
size cap (``CLAUDE.md``). No behaviour change — the methods are imported
into ``RNSMeshtasticBridge`` via mixin inheritance, so attribute access
patterns (``RNSMeshtasticBridge._should_emit_channel_stall_warning`` for
tests) remain identical.

The gateway's own WebSocket server lived here until 2026-09-28 (Fable
review F1): it never bound on the fleet (``websockets`` absent from the
gateway's interpreter) and, once the map took ``0.0.0.0:5001``, could only
ever race the map for that port at boot. The map owns live UI push.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)



class BridgeAuxMixin:
    """Auxiliary lifecycle helpers for :class:`RNSMeshtasticBridge`.

    Provided methods (all preserve their pre-extraction signatures):

    - :meth:`_should_emit_channel_stall_warning` — pure decision helper.
    - :meth:`_channel_diagnostic_loop` — Hardening A monitoring thread.
    """

    @staticmethod
    def _should_emit_channel_stall_warning(
        last_uplink_at: Optional[float],
        already_emitted: bool,
        elapsed_sec: float,
        threshold_sec: float,
    ) -> bool:
        """Pure decision: should we emit the deployment-gap warning now?

        True iff (a) we've never seen an uplink, (b) the elapsed time
        since startup exceeds the threshold, and (c) we haven't already
        emitted (one-shot to avoid journal spam).
        """
        return (
            last_uplink_at is None
            and elapsed_sec >= threshold_sec
            and not already_emitted
        )

    def _channel_diagnostic_loop(self):
        """Hardening A: surface bridge channel deployment gaps.

        Watches the MQTT handler's _last_uplink_at and emits a one-shot
        WARNING when no uplink has been observed on the configured bridge
        channel within the threshold window. Fleet clients on a different
        channel name (the moc3 stall, 2026-04-27) leave the bridge stats
        frozen with no surface error; this turns the gap into a journal
        signal an operator can grep for.

        Threshold: 30 min default, override via MESHFORGE_BRIDGE_RX_STALE_SEC.
        """
        try:
            threshold = max(60, int(
                os.environ.get("MESHFORGE_BRIDGE_RX_STALE_SEC", "1800")
            ))
        except ValueError:
            threshold = 1800

        # Sleep in 30s slices so stop_event wakes us promptly on shutdown.
        slice_sec = 30
        elapsed = 0
        bridge_channel = getattr(
            getattr(self.config, 'mqtt_bridge', None), 'channel', None
        )
        while self._running and not self._stop_event.is_set():
            if self._stop_event.wait(slice_sec):
                return
            elapsed += slice_sec

            handler = self._mesh_handler
            if handler is None or not hasattr(handler, '_last_uplink_at'):
                continue

            if self._should_emit_channel_stall_warning(
                last_uplink_at=handler._last_uplink_at,
                already_emitted=getattr(handler, '_stale_warning_emitted', False),
                elapsed_sec=elapsed,
                threshold_sec=threshold,
            ):
                logger.warning(
                    "Bridge channel deployment gap: no MQTT uplink "
                    "observed on channel %r in %d min since startup. "
                    "Fleet clients may be transmitting on a different "
                    "channel — verify with `journalctl -u meshtasticd "
                    "| grep 'JSON publish' | head -3` to see the "
                    "actual channel name(s) meshtasticd is publishing.",
                    bridge_channel, elapsed // 60,
                )
                handler._stale_warning_emitted = True

