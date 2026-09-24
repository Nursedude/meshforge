"""Delivery protocol labels — one vocabulary for every writer and reader.

The persistent queue's ``destination`` is a ROUTING LANE, and for
mesh_bridge's dual-Meshtastic mode the lanes are ``primary`` and
``secondary`` — the two radios. delivery_counters stamped that lane as the
event's ``protocol``, so one transport was counted under three labels
(moc, 2026-09-23: ``meshtastic`` 27,906 / ``primary`` 7,280 / ``secondary``
1,672). The confirmation rate judges only protocols that have confirmed; if
Meshtastic ever records a CONFIRMED (wantAck, ``meshtastic_handler``) it
does so as ``meshtastic``, and the same radio's ``secondary`` failures would
be silently forgiven (review C F7).

Writers canonicalize and keep the lane in the event note; readers
canonicalize history written before this. Both import THIS map — two
consumers, one constant (honest_failure_modes #5). Kept dependency-free so
``monitoring/traffic_pulse`` can import it without pulling in ``gateway``.
"""
from typing import Optional

#: Routing lane -> the transport it rides. Only lanes that are NOT
#: themselves a transport belong here.
LANE_TO_PROTOCOL = {
    "primary": "meshtastic",
    "secondary": "meshtastic",
}


def canonical_protocol(label: Optional[str]) -> Optional[str]:
    """The transport a delivery label names (a lane maps to its transport)."""
    if not label:
        return label
    return LANE_TO_PROTOCOL.get(label, label)


def lane_of(label: Optional[str]) -> Optional[str]:
    """The routing lane when ``label`` is a lane rather than a transport."""
    return label if label in LANE_TO_PROTOCOL else None
