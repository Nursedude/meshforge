"""Node ids from the WIRE — the one rule for who sent a packet (and, for a
lib-packet DM, who it is for).

Dependency-free on purpose (review 2026-11-17): every mesh ingress path imports
it, and a pure wire-shape rule must not drag in a leg's meshtastic/queue
imports. Sibling of ``canonical_message.packet_is_broadcast`` (that module is
byte-identical with MeshAnchor, so this one lives beside it, not in it).

The rule (BR-T #18, operator 09-18 "decide on a wire FACT"): the numeric field
(``to`` / ``from``) is the wire fact and DECIDES ALONE when present. The string
(``toId`` / ``fromId``) is the meshtastic library's node-DB entry — ``None`` for
a node its ``nodesByNum`` lacks (lib 2.7.11 ``_nodeNumToId``: the
``[Mesh:SHORT_TURBO:????]`` tag, 2026-11-17), a neighbour-set ``user.id``
otherwise — so it is a fallback ONLY with no number, and only as ``'!'`` +
exactly 8 hex digits.

Converged here (the SENDER): the preset bridge (TCP/serial + MQTT legs), the
Mesh→RNS handler, the MQTT bridge handler (text, node-update gate, nodeinfo
self-report gate, node-tracker keying). Destination: the preset bridge's
lib-packet DMs only. NOT yet (queued 2026-11-17): MQTT-json destinations in
both MQTT readers and the Mesh→RNS handler's ``toId``; ``oracle_phoneapi_tap``
(fromId wins — ORC-1); ``utils/message_listener``; and
``canonical_message.from_meshtastic`` (MeshAnchor byte-identical twin).
"""
import re
from typing import Optional

_NODE_ID_RE = re.compile(r"![0-9a-fA-F]{8}")
_BROADCAST = 0xFFFFFFFF


def wire_node_num(value) -> Optional[str]:
    """A node NUMBER → ``!xxxxxxxx``, or ``None``. A real int (bool is not a
    node) naming a node: 1 .. 0xFFFFFFFE — not 0, not broadcast, never masked
    down from a wider value."""
    if isinstance(value, int) and not isinstance(value, bool) \
            and 0 < value < _BROADCAST:
        return f"!{value:08x}"
    return None


def node_dest(dest) -> Optional[str]:
    """A node-id STRING → the normalised ``!xxxxxxxx``, or ``None``. ``'!'`` +
    exactly 8 ASCII hex digits naming a node (not 0, not 0xFFFFFFFF).
    ``re.fullmatch`` on literal ASCII ranges: no ``0x``/``+``/space/``_``/
    non-ASCII digits, no trailing newline (BR-T #18 r3)."""
    if not isinstance(dest, str) or not _NODE_ID_RE.fullmatch(dest):
        return None
    return wire_node_num(int(dest[1:], 16))


def _wire_node(packet: dict, num_key: str, id_key: str) -> Optional[str]:
    num = packet.get(num_key)
    if num is not None:
        return wire_node_num(num)      # present and invalid → None, never the string
    return node_dest(packet.get(id_key))


def dm_destination(packet: dict) -> Optional[str]:
    """BR-T #18: a DM's destination from the wire ``to``, else ``toId``, or
    ``None`` (refuse, named). r2 11-11: ``user.id='!ffffffff'`` turned a DM
    into a broadcast and ``'!abc'`` reached the library's ``our_exit``."""
    return _wire_node(packet, 'to', 'toId')


def mqtt_json_origin(data: dict, default: str = '') -> str:
    """The SENDER of a meshtastic MQTT-json message: the originator ``from``
    by the wire rule, else the ``sender`` (the LAST-HOP radio that uplinked
    it — the attribution both MQTT readers have always fallen back to) through
    the SAME ``node_dest`` filter the bridge's ``packet_origin`` applies, else
    ``default``. One reader for both (review 2026-11-17: each hand-rolled
    ``from & 0xFFFFFFFF if from > 0``, which named broadcast as a sender and
    masked a >32-bit value into a guess; r2: a raw ``sender`` here vs a
    filtered one on the bridge leg gave one JSON two dedup keys). A sender
    that is not ``'!'`` + 8 hex (a foreign publisher's ``gw-1``) is no node
    id — it reads as no origin on every path."""
    return wire_node_num(data.get('from')) or node_dest(data.get('sender')) \
        or default


def packet_origin(packet: dict) -> Optional[str]:
    """The SENDER from the wire ``from``, else ``fromId``. Every mesh ingress
    path that feeds ``mesh_origin_content_id`` must call this: the dedup key is
    only a key if the paths agree on the origin."""
    return _wire_node(packet, 'from', 'fromId')
