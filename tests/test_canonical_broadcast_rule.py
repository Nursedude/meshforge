"""Bridge matrix F15: ONE broadcast rule, owned by the byte-locked
canonical_message.py.

``CanonicalMessage.from_meshtastic`` decided broadcast on the TEXT ``toId``
(``is None or == '!ffffffff'``), the copy the F7 fix missed: the meshtastic
library reports a broadcast's ``toId`` as ``'^all'``, so a lib-shaped
broadcast became a DM addressed to ``^all``. The fix is not a second patched
copy — ``meshtastic_handler`` imports the one rule from the locked file.

Identical in MeshForge and MeshAnchor (the file it pins is byte-locked).

Run: python3 -m pytest tests/test_canonical_broadcast_rule.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.canonical_message import CanonicalMessage


def _pkt(**addr):
    return {'from': 0xAABBCCDD, 'fromId': '!aabbccdd',
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'hi'},
            'hopLimit': 3, 'hopStart': 3, **addr}


@pytest.mark.parametrize("addr,broadcast,dest", [
    # what the meshtastic library actually hands us for a broadcast
    ({'to': 0xFFFFFFFF, 'toId': '^all'}, True, None),
    ({'toId': '^all'}, True, None),
    ({'to': 0xFFFFFFFF}, True, None),
    ({'toId': '!ffffffff'}, True, None),
    # a DM
    ({'to': 0x11223344, 'toId': '!11223344'}, False, '!11223344'),
    ({'to': 0x11223344}, False, '!11223344'),
    # the numeric wire fact wins over a disagreeing text id
    ({'to': 0x11223344, 'toId': '^all'}, False, '!11223344'),
    ({'to': 0xFFFFFFFF, 'toId': '!11223344'}, True, None),
])
def test_from_meshtastic_broadcast_follows_the_wire(addr, broadcast, dest):
    msg = CanonicalMessage.from_meshtastic(_pkt(**addr))
    assert msg.is_broadcast is broadcast
    assert msg.destination_address == dest


def test_the_handler_uses_the_same_rule():
    """One rule, two consumers (honest_failure_modes #5): never a copy."""
    from gateway import canonical_message, mesh_bridge, meshtastic_handler
    assert meshtastic_handler.packet_is_broadcast is \
        canonical_message.packet_is_broadcast
    assert mesh_bridge.packet_is_broadcast is \
        canonical_message.packet_is_broadcast
