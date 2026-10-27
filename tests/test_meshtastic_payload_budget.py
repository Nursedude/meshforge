"""Bridge matrix F8: Meshtastic byte budgets — distinct quantities, one home each.

- ``canonical_message.MESHTASTIC_MAX_PAYLOAD`` is ``DATA_PAYLOAD_LEN``, the
  Data.payload BUFFER bound: 233 in the firmware the fleet runs (meshtasticd
  v2.7.26 ``protobufs/meshtastic/mesh.proto:1819``) and in the installed
  ``meshtastic`` protobufs. It read 237, a stale value. It is NOT the on-air
  text budget: the firmware refuses an encoded packet over 255 B with a 16 B
  header, +12 B for a PKI DM (``Router.cpp``) — measured max text 232 B
  broadcast, 220 B PKI DM (215 B with reply_id: the DM budget, F17).
- ``utils.defaults.MAX_MESHTASTIC_MSG_LENGTH`` (228) is the single-packet TEXT
  budget the egress enforces (``_truncate_if_needed``). The oracle's reply cap
  was test-pinned EQUAL to the (stale) wire constant — the wrong pair: its 237
  exceeded the egress budget, so a 229-237 byte reply would lose its tail at
  the radio with an ERROR (0 occurrences in 30 days on moc / moc3).

Identical in MeshForge and MeshAnchor.

Run: python3 -m pytest tests/test_meshtastic_payload_budget.py -v
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.canonical_message import MESHTASTIC_MAX_PAYLOAD
from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH


def test_data_payload_len_is_the_firmware_constant():
    assert MESHTASTIC_MAX_PAYLOAD == 233


def test_data_payload_len_matches_installed_protobufs():
    """When the library is present, the wire fact comes from it — a firmware
    change to DATA_PAYLOAD_LEN fails here instead of drifting silently."""
    mesh_pb2 = pytest.importorskip("meshtastic.protobuf.mesh_pb2")
    assert MESHTASTIC_MAX_PAYLOAD == \
        mesh_pb2.Constants.Value("DATA_PAYLOAD_LEN")


def test_text_budget_fits_inside_data_payload_len():
    assert 0 < MAX_MESHTASTIC_MSG_LENGTH < MESHTASTIC_MAX_PAYLOAD


# Firmware TX bound (meshtasticd v2.7.26 src/mesh/RadioInterface.h, Router.cpp):
# encoded Data + 16 B header must fit 255 B; a PKI DM adds 12 B.
_MAX_LORA_PAYLOAD_LEN, _HEADER, _PKC_OVERHEAD = 255, 16, 12


def _encoded_text_len(n_bytes: int) -> int:
    """Encoded size of an own-node TEXT packet: the firmware always sets the
    bitfield on packets it originates (Router.cpp)."""
    mesh_pb2 = pytest.importorskip("meshtastic.protobuf.mesh_pb2")
    portnums_pb2 = pytest.importorskip("meshtastic.protobuf.portnums_pb2")
    d = mesh_pb2.Data(portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                      payload=b"x" * n_bytes, bitfield=3)
    return len(d.SerializeToString())


def test_text_budget_fits_on_air_as_a_broadcast():
    assert _encoded_text_len(MAX_MESHTASTIC_MSG_LENGTH) + _HEADER \
        <= _MAX_LORA_PAYLOAD_LEN


def test_dm_budget_fits_on_air_as_a_pki_dm():
    """Bridge matrix F17: a DM is chunked to its own budget, which fits a
    PKI DM (the broadcast 228 does not — that is why the two differ)."""
    from utils.defaults import MAX_MESHTASTIC_DM_MSG_LENGTH
    assert _encoded_text_len(MAX_MESHTASTIC_DM_MSG_LENGTH) + _HEADER \
        + _PKC_OVERHEAD <= _MAX_LORA_PAYLOAD_LEN
    assert _encoded_text_len(MAX_MESHTASTIC_MSG_LENGTH) + _HEADER \
        + _PKC_OVERHEAD > _MAX_LORA_PAYLOAD_LEN
