"""Leg helpers split out of mesh_bridge.py (MF025 file-size ratchet, 2026-09-30).

Logs under the ``gateway.mesh_bridge`` logger on purpose, so journal greps and
log-capturing tests that knew these lines before the split still find them.
"""
from __future__ import annotations

import logging

from .config import MeshtasticConfig

logger = logging.getLogger("gateway.mesh_bridge")


def build_downlink_injector(leg: MeshtasticConfig):
    """Construct a DownlinkInjector for a leg, or None if not enabled."""
    if (leg.injection_mode or "toradio").lower() != "downlink":
        return None
    if not leg.downlink_psk:
        logger.warning(
            "mesh_bridge %s injection_mode=downlink but no downlink_psk — "
            "falling back to toradio", leg.name)
        return None
    try:
        from .mqtt_downlink_inject import DownlinkInjector
        injector = DownlinkInjector(
            broker=leg.mqtt_broker,
            port=leg.mqtt_port,
            channel_name=leg.mqtt_channel,
            psk_b64=leg.downlink_psk,
            root_topic="msh",
        )
        if not injector.usable:
            logger.warning(
                "mesh_bridge %s downlink injector unusable: %s — "
                "falling back to toradio", leg.name, injector.fatal_reason)
            return None
        logger.info(
            "mesh_bridge %s: true-origin downlink injection ENABLED "
            "(channel=%s)", leg.name, leg.mqtt_channel)
        return injector
    except Exception as e:
        logger.warning(
            "mesh_bridge %s downlink injector init failed: %s — "
            "falling back to toradio", leg.name, e)
        return None


def bridge_event(verb: str, side: str, text: str, reason: str = "") -> None:
    """One DEBUG line per text the bridge heard, dropped or forwarded.

    ``mesh_bridge rx leg=<side>: <text>`` / ``mesh_bridge drop leg=<side>
    reason=<reason>: <text>`` / ``mesh_bridge tx dest=<side>: <text>``. The
    per-message record a run reconciles against (C21, 2026-10-19): before it
    a duplicate drop only moved a counter. Format is parsed by
    scripts/c21_bridge_run.py — change both together.
    """
    where = f"dest={side}" if verb == "tx" else f"leg={side}"
    why = f" reason={reason}" if reason else ""
    logger.debug("mesh_bridge %s %s%s: %s", verb, where, why, text[:120])


def queue_drop_witness(ingress: str):
    """on_terminal hook for a bridge queue: name a row the queue gave up on."""
    def on_terminal(payload, outcome: str) -> None:
        if outcome == "dropped":
            bridge_event("drop", ingress, str((payload or {}).get("content", "")),
                         "dead_letter")
    return on_terminal
