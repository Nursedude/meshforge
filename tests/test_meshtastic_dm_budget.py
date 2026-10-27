"""Bridge matrix F17: a Meshtastic DM gets a DM-sized text budget.

The egress chunked and truncated every send to 228 B. That fits a broadcast
(measured on-air max 232 B) but not a PKI-encrypted DM: the firmware adds
12 B for PKC (meshtasticd v2.7.26 ``Router.cpp``) and refuses the packet
TOO_LARGE — measured max text 220 B, 215 B with ``reply_id``. The fleet
radios are NOT in licensed mode (operator, 2026-10-26: that would disable
encryption and lose LongFast public), so PKC is live on every DM; the fix is
the budget, never the radio config.

Identical in MeshForge and MeshAnchor.

Run: python3 -m pytest tests/test_meshtastic_dm_budget.py -v
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH
import utils.defaults as _defaults

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export

# The measured PKI-DM-with-reply_id max. Read from utils.defaults when present;
# the literal lets the behavioural tests run (and fail) on code without it.
MAX_MESHTASTIC_DM_MSG_LENGTH = getattr(_defaults, "MAX_MESHTASTIC_DM_MSG_LENGTH", 215)


def mesh_text_budget(dest):
    from gateway.base_handler import mesh_text_budget as fn
    return fn(dest)


def test_dm_constant_lives_in_defaults():
    assert _defaults.MAX_MESHTASTIC_DM_MSG_LENGTH == 215


_LONG = "word " * 200   # no newlines: chunks fill to the budget


@pytest.mark.parametrize("dest", [None, "", "^all", "!ffffffff", 0xFFFFFFFF])
def test_broadcast_keeps_the_broadcast_budget(dest):
    assert mesh_text_budget(dest) == MAX_MESHTASTIC_MSG_LENGTH


@pytest.mark.parametrize("dest", ["!11223344", 0x11223344])
def test_a_dm_gets_the_dm_budget(dest):
    assert mesh_text_budget(dest) == MAX_MESHTASTIC_DM_MSG_LENGTH


def test_dm_budget_fits_on_air_as_a_pki_dm_with_reply_id():
    mesh_pb2 = pytest.importorskip("meshtastic.protobuf.mesh_pb2")
    portnums_pb2 = pytest.importorskip("meshtastic.protobuf.portnums_pb2")
    d = mesh_pb2.Data(portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                      payload=b"x" * MAX_MESHTASTIC_DM_MSG_LENGTH,
                      bitfield=3, reply_id=0xFFFFFFFF)
    # firmware: encoded + 16 B header + 12 B PKC must fit 255 B
    assert len(d.SerializeToString()) + 16 + 12 <= 255


def _rns_to_mesh(bridge, content):
    from gateway.rns_bridge import BridgedMessage
    msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                         destination_id=None, content=content)
    sent = []

    def capture(content, destination=None, channel=0, source=None):
        sent.append((content, destination))
        return True
    with patch.object(bridge, "send_to_meshtastic", side_effect=capture):
        bridge._process_rns_to_mesh(msg)
    return sent


def test_rns_to_mesh_dm_chunks_fit_a_pki_dm(bridge):
    if not hasattr(bridge, "_resolve_mesh_destination"):
        pytest.skip("no @address DM leg on RNS->Mesh in this twin (broadcast-only)")
    sent = _rns_to_mesh(bridge, "@!11223344 " + _LONG)
    assert sent and all(d == "!11223344" for _, d in sent), \
        "the control: the @address resolved to a DM"
    for chunk, _ in sent:
        assert len(chunk.encode("utf-8")) <= MAX_MESHTASTIC_DM_MSG_LENGTH


def test_rns_to_mesh_broadcast_still_uses_the_full_budget(bridge):
    sent = _rns_to_mesh(bridge, _LONG)
    assert sent and all(d is None for _, d in sent)
    sizes = [len(c.encode("utf-8")) for c, _ in sent]
    assert max(sizes) <= MAX_MESHTASTIC_MSG_LENGTH
    assert max(sizes) > MAX_MESHTASTIC_DM_MSG_LENGTH, \
        "broadcasts must not be cut to the DM budget"


@pytest.mark.parametrize("dest,budget", [
    ("!11223344", MAX_MESHTASTIC_DM_MSG_LENGTH),
    (None, MAX_MESHTASTIC_MSG_LENGTH),
])
def test_last_resort_truncation_follows_the_destination(dest, budget):
    from gateway.base_handler import BaseMessageHandler
    out = BaseMessageHandler._truncate_if_needed(None, "x" * 300,
                                                 destination=dest)
    assert len(out.encode("utf-8")) <= budget
    assert len(out.encode("utf-8")) > budget - 4


def test_queue_egress_dm_chunks_fit_a_pki_dm(bridge):
    """The persistent-queue Meshtastic sender (where a twin has one) chunks a
    DM row to the DM budget, a broadcast row to the broadcast budget."""
    fn = getattr(bridge, "_queue_send_meshtastic_egress", None)
    if fn is None:
        pytest.skip("no chunking queue egress in this twin")
    for dest, budget in (("!11223344", MAX_MESHTASTIC_DM_MSG_LENGTH),
                         (None, MAX_MESHTASTIC_MSG_LENGTH)):
        sent = []
        with patch.object(bridge, "send_to_meshtastic",
                          side_effect=lambda m, *a, **k: sent.append(m) or True):
            fn({"message": _LONG, "destination": dest})
        assert sent, "the control: the row was sent"
        assert max(len(c.encode("utf-8")) for c in sent) <= budget
        if dest is None:
            assert max(len(c.encode("utf-8")) for c in sent) > \
                MAX_MESHTASTIC_DM_MSG_LENGTH


class _Stop(BaseException):
    """Escapes the handlers' own ``except Exception`` after the spy records."""


@pytest.mark.parametrize("modname,clsname", [
    ("gateway.meshtastic_handler", "MeshtasticHandler"),
    ("gateway.mqtt_bridge_handler", "MQTTBridgeHandler"),
])
@pytest.mark.parametrize("entry", ["send_text", "queue_send"])
def test_handler_entry_points_pass_the_destination(modname, clsname, entry):
    """Review F17 #1: reverting ``destination=`` at any of the four handler
    sites left every other F17 test green. Drive the real method to its
    truncation call and assert the DM's destination reached it."""
    import importlib
    cls = getattr(importlib.import_module(modname), clsname)
    h = cls.__new__(cls)
    seen = {}

    def spy(message, max_length=None, *, destination=None):
        seen["destination"] = destination
        raise _Stop()
    h._truncate_if_needed = spy
    with pytest.raises(_Stop):
        if entry == "send_text":
            h.send_text("x" * 300, destination="!11223344")
        else:
            h.queue_send({"message": "x" * 300, "destination": "!11223344"})
    assert seen["destination"] == "!11223344"
