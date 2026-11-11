"""Leg helpers split out of mesh_bridge.py (MF025 file-size ratchet, 2026-09-30).

Logs under the ``gateway.mesh_bridge`` logger on purpose, so journal greps and
log-capturing tests that knew these lines before the split still find them.
"""
from __future__ import annotations

import logging
import threading

from .config import MeshtasticConfig
from utils.safe_import import safe_import

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


# The library CLASSES, imported apart from mesh_bridge's module handles (tests replace
# those modules with fakes): the write-target check below asks what the
# library's own _writeBytes gates on, so it must recognise the real types.
_TCPInterfaceCls, _HAS_TCP_CLS = safe_import('meshtastic.tcp_interface', 'TCPInterface')
_StreamInterfaceCls, _HAS_STREAM_CLS = safe_import(
    'meshtastic.stream_interface', 'StreamInterface')


def _write_target_gone(interface) -> bool:
    """True when the meshtastic library would DROP a write to ``interface``
    without a word (BR-T #10, 2026-11-10).

    ``TCPInterface._writeBytes`` writes only ``if self.socket is not None`` (a
    failed ``_reconnect()`` or a ``close()`` leaves it None) and the serial
    ``StreamInterface._writeBytes`` only ``if self.stream:``; either way
    ``sendText`` still returns a packet. This reads exactly what those gates
    read. Any other interface (MQTT leg, a fake) is not judged here: False.
    """
    if _HAS_TCP_CLS and isinstance(interface, _TCPInterfaceCls):
        return interface.socket is None
    if _HAS_STREAM_CLS and isinstance(interface, _StreamInterfaceCls):
        return not interface.stream
    return False


def _library_gave_up(interface) -> bool:
    """True when a meshtastic library stream interface reads not-connected:
    its ``_disconnected()`` clears ``isConnected`` — when the reader thread
    exits (the link is gone for good), and also on a ``FromRadio.rebooted``,
    after which the library re-configs the SAME object and sets it again (r3
    finding 2: an earlier docstring claimed "nothing sets it again"). Both also
    publish ``connection.lost``, so an installed leg is torn down and
    reconnected either way; this read only decides a leg that MISSED that
    event. During the library's own 1 s ``_reconnect()`` it stays SET, so a
    self-healing link is never judged dead. Non-library interfaces: False.

    The backstop for a missed ``connection.lost`` (11-10 review r2 F-A: one
    that fires before the leg's handle is installed is dropped by the identity
    check, and the leg read connected on a dead handle forever)."""
    if not (_HAS_STREAM_CLS and isinstance(interface, _StreamInterfaceCls)):
        return False
    flag = getattr(interface, "isConnected", None)
    return isinstance(flag, threading.Event) and not flag.is_set()
