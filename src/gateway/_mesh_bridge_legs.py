"""Leg helpers split out of mesh_bridge.py (MF025 file-size ratchet, 2026-09-30).

Logs under the ``gateway.mesh_bridge`` logger on purpose, so journal greps and
log-capturing tests that knew these lines before the split still find them.
"""
from __future__ import annotations

import logging
import threading

from .config import MeshtasticConfig
from utils.safe_import import safe_import

from .message_queue_models import _env_seconds

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



#: BR-T #14 (2026-11-11): a row held while its leg is down ages out, witnessed
#: (queue_drop_witness -> `drop … dead_letter`), after this long. ASSERTED, not
#: measured (operator 11-11: "10 min, measure later" — best effort, not 100%):
#: covers a meshtasticd restart / USB replug; chat relayed later than this
#: reads as new on the far mesh. Applies to EVERY pending row, held or not —
#: incl. rows persisted across a bridge restart (r2 F-2: accepted, named). Re-derive from the leg-outage history
#: (deferred: mesh-bridge-leg-outage-measure-2026-11-11).
MESH_BRIDGE_MAX_AGE_S = _env_seconds("MESHFORGE_MESH_BRIDGE_MAX_AGE_S", 600)
#: How soon a row deferred on a down leg is asked again (no attempt spent).
LINK_DOWN_RETRY_S = 2.0
#: Deferrals closer together than this are ONE outage (logged ERROR once);
#: a gap longer starts a new one (r2 F-3: rows that aged out, an idle leg).
LINK_DOWN_EPISODE_GAP_S = 30.0


def dm_destination(packet: dict):
    """BR-T #18: a DM's destination from the WIRE, or ``None`` (refuse, named).

    The numeric ``to`` is the wire fact and DECIDES ALONE when present: a valid
    node number (1 .. 0xFFFFFFFE, a real int — bool is not a node) gives
    ``!{to:08x}``; any other value refuses. ``toId`` is the library's node-DB
    string, which a neighbour can set — r2 11-11: ``user.id='!ffffffff'``
    turned a DM into a broadcast, ``'!abc'`` reached ``our_exit`` — so it is a
    fallback ONLY with no ``to``, and only as ``'!'`` + 8 hex naming a node."""
    to_num = packet.get('to')
    if to_num is not None:
        if isinstance(to_num, int) and not isinstance(to_num, bool) \
                and 0 < to_num < 0xFFFFFFFF:
            return f"!{to_num:08x}"
        return None
    to_id = packet.get('toId')
    if isinstance(to_id, str) and len(to_id) == 9 and to_id[0] == '!':
        try:
            num = int(to_id[1:], 16)
        except ValueError:
            return None
        if 0 < num < 0xFFFFFFFF:
            return f"!{num:08x}"
    return None

def bump_stat(bridge, key: str) -> None:
    """One locked counter bump (the bridge's stats are shared across threads)."""
    with bridge._stats_lock:
        bridge.stats[key] = bridge.stats.get(key, 0) + 1
