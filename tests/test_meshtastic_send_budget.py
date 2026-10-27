"""Bridge matrix F18: every operator Meshtastic send respects the byte budget,
and an oversize one is REFUSED loudly — never reported as sent.

``send_text_direct`` PUTs to ``/api/v1/toradio`` and reports success on
HTTP 200. That only means meshtasticd accepted the bytes: the firmware's
TOO_LARGE refusal happens afterwards, inside the radio. So an oversize DM sent
from the web inbox answered ``success: true, "Sent via radio"`` for a packet
that never aired. The RADIO's limit (``mesh_onair_limit``: 232 B broadcast,
220 B DM, measured) must be applied BEFORE the PUT. It is the refusal line,
not the chunking target (``mesh_text_budget``: 228 / 215, F17): refusing at
the chunk target would newly drop a 229-232 B broadcast that airs today.

Identical in MeshForge and MeshAnchor.

Run: python3 -m pytest tests/test_meshtastic_send_budget.py -v
"""

import io
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DM_LIMIT, BCAST_LIMIT = 220, 232   # on-air maxima (measured)
DM_BUDGET = 215                     # the DM chunking target (F17)


def _direct(text, destination):
    import gateway.meshtastic_protobuf_client as pc
    put = MagicMock(side_effect=AssertionError("must not PUT an oversize payload"))
    with patch.object(pc, "assert_tx_allowed", lambda *a, **k: None), \
         patch.object(pc.urllib.request, "urlopen", put):
        return pc.send_text_direct_with_id(text, destination=destination), put


@pytest.mark.parametrize("dest,size", [(0x11223344, DM_LIMIT + 1),
                                       (None, BCAST_LIMIT + 1)])
def test_oversize_is_refused_before_the_put(dest, size, caplog):
    with caplog.at_level(logging.WARNING):
        pkt_id, put = _direct("x" * size, dest)
    assert pkt_id is None
    put.assert_not_called()
    assert any("refused" in r.message.lower() for r in caplog.records), \
        "a refusal must leave a witness"


def test_refusals_are_counted():
    import gateway.meshtastic_protobuf_client as pc
    before = pc.oversize_refused_count()
    _direct("x" * (DM_LIMIT + 1), 0x11223344)
    assert pc.oversize_refused_count() == before + 1


@pytest.mark.parametrize("dest,size", [(0x11223344, DM_LIMIT),
                                       (None, BCAST_LIMIT)])
def test_within_the_radio_limit_reaches_the_put(dest, size):
    pytest.importorskip("meshtastic.protobuf.mesh_pb2")
    import gateway.meshtastic_protobuf_client as pc
    resp = MagicMock(status=200)
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda s, *a: False
    with patch.object(pc, "assert_tx_allowed", lambda *a, **k: None), \
         patch.object(pc.urllib.request, "urlopen", return_value=resp) as put, \
         patch.object(pc._protobuf_circuit, "can_send", return_value=True):
        assert pc.send_text_direct_with_id("x" * size, destination=dest) is not None
    put.assert_called_once()


def _inbox(text, destination):
    from utils.map_http_handler import MapRequestHandler
    h = MapRequestHandler.__new__(MapRequestHandler)
    h._reject_if_untrusted = lambda: False
    h._reject_cross_site_write = lambda: False
    body = json.dumps({"text": text, "destination": destination}).encode()
    h.headers = {"Content-Length": str(len(body))}
    h.rfile = io.BytesIO(body)
    served = []
    h._serve_json = lambda d, status=200: served.append((status, d))
    h._get_radio_connection = MagicMock(
        side_effect=AssertionError("the TCP fallback must not send it either"))
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               side_effect=AssertionError("must not hand it to the radio")):
        h._handle_send_message()
    return served


def test_inbox_refuses_an_oversize_dm_and_says_why():
    served = _inbox("x" * (DM_LIMIT + 1), "!11223344")
    assert len(served) == 1
    status, body = served[0]
    assert status == 400
    assert "success" not in body
    assert str(DM_LIMIT) in json.dumps(body), "say the limit it exceeded"


def test_inbox_refuses_multibyte_over_the_byte_budget():
    """Bytes, not characters: 120 three-byte characters = 360 B."""
    served = _inbox("€" * 120, "^all")
    assert served[0][0] == 400


def test_messaging_chunks_meshtastic_by_bytes_for_the_destination():
    from commands import messaging
    sent = []
    fake = MagicMock()
    fake.send_to_meshtastic.side_effect = \
        lambda chunk, dest, ch: sent.append(chunk) or MagicMock(success=True)
    with patch.object(messaging, "cmd_gateway", fake), \
         patch.object(messaging, "connect_tuned", MagicMock()):
        messaging.send_message("€" * 300, destination="!11223344",
                               network="meshtastic")
    assert sent, "the control: the message was handed to the gateway"
    assert max(len(c.encode("utf-8")) for c in sent) <= DM_BUDGET


def test_cli_refuses_oversize_before_either_send_path():
    """The CLI's fallback is a ``meshtastic --sendtext`` subprocess: when the
    direct send refused, that would have aired the oversize text anyway."""
    from commands import meshtastic as cmd
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               side_effect=AssertionError("must not hand it to the radio")), \
         patch.object(cmd, "_run_command",
                      side_effect=AssertionError("must not fall back to the CLI")):
        res = cmd.send_message("x" * (DM_LIMIT + 1), dest="!11223344")
    assert not res.success
    assert str(DM_LIMIT) in res.message


def test_onair_limits_are_exactly_what_fits_the_frame():
    """232 / 220 are the encoding's real maxima: one more byte does not fit
    (firmware v2.7.26: encoded + 16 B header [+12 B PKC] <= 255 B)."""
    mesh_pb2 = pytest.importorskip("meshtastic.protobuf.mesh_pb2")
    portnums_pb2 = pytest.importorskip("meshtastic.protobuf.portnums_pb2")
    from utils.defaults import (MESHTASTIC_ONAIR_MAX_BROADCAST,
                                MESHTASTIC_ONAIR_MAX_DM)

    def fits(n, pkc):
        d = mesh_pb2.Data(portnum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                          payload=b"x" * n, bitfield=3)
        return len(d.SerializeToString()) + 16 + (12 if pkc else 0) <= 255
    assert fits(MESHTASTIC_ONAIR_MAX_BROADCAST, False)
    assert not fits(MESHTASTIC_ONAIR_MAX_BROADCAST + 1, False)
    assert fits(MESHTASTIC_ONAIR_MAX_DM, True)
    assert not fits(MESHTASTIC_ONAIR_MAX_DM + 1, True)


def test_a_broadcast_that_airs_today_is_not_newly_refused():
    """229-232 B broadcasts fit the frame: the guard must not drop them."""
    import gateway.meshtastic_protobuf_client as pc
    before = pc.oversize_refused_count()
    with patch.object(pc, "assert_tx_allowed", lambda *a, **k: None), \
         patch.object(pc, "_pb2_available", False):
        pc.send_text_direct_with_id("x" * BCAST_LIMIT, destination=None)
    assert pc.oversize_refused_count() == before


# --- F18 review: a refusal must not fall through to a path that airs it --- #

def test_session_client_refuses_oversize_too():
    """The session client is every gateway's fallback; it had no byte check."""
    import gateway.meshtastic_protobuf_client as pc
    c = pc.MeshtasticProtobufClient.__new__(pc.MeshtasticProtobufClient)
    c.send_mesh_packet = MagicMock(side_effect=AssertionError("must not send"))
    c._connected = True
    before = pc.oversize_refused_count()
    assert c.send_text("x" * (DM_LIMIT + 1), destination=0x11223344) is False
    assert pc.oversize_refused_count() == before + 1


def _spies(module):
    """RECORDING spies at the names ``module`` really calls. Raising spies
    proved nothing here: both gateway senders wrap each path in
    ``except Exception`` and return False anyway (vacuous on old code)."""
    import gateway.meshtastic_protobuf_client as pc
    direct = MagicMock(return_value=None)
    direct_bool = MagicMock(return_value=False)
    session = MagicMock()
    return direct, direct_bool, session, (
        patch.object(pc, "send_text_direct_with_id", direct),
        patch.object(pc, "send_text_direct", direct_bool),
        patch.object(module, "_get_protobuf_client", session),
        patch.object(module, "_HAS_PROTOBUF_CLIENT", True),
    )


def test_mesh_bridge_forward_refuses_before_either_path():
    """The F17b forward: an over-limit text is refused BEFORE the direct send,
    so the fromradio-reading session fallback never connects."""
    import gateway.mesh_bridge as mb
    iface = mb.MQTTMeshInterface.__new__(mb.MQTTMeshInterface)
    iface._name = "test"
    iface._config = MagicMock()
    direct, direct_bool, session, ps = _spies(mb)
    with ps[0], ps[1], ps[2], ps[3], patch.object(mb, "_HAS_PROTOBUF_CONFIG", True, create=True):
        assert iface.sendText("x" * (BCAST_LIMIT + 1)) is False
    direct.assert_not_called(); direct_bool.assert_not_called()
    session.assert_not_called()


def test_mqtt_handler_http_send_refuses_before_either_path():
    import gateway.mqtt_bridge_handler as mq
    h = mq.MQTTBridgeHandler.__new__(mq.MQTTBridgeHandler)
    h.config = MagicMock()
    h._load_balancer = None
    direct, direct_bool, session, ps = _spies(mq)
    with ps[0], ps[1], ps[2], ps[3]:
        assert h._send_via_http_protobuf("x" * (DM_LIMIT + 1),
                                         destination="!11223344") is False
    direct.assert_not_called(); direct_bool.assert_not_called()
    session.assert_not_called()


def test_inbox_labels_a_broadcast_as_a_broadcast():
    served = _inbox("x" * (BCAST_LIMIT + 1), "!ffffffff")
    assert served[0][0] == 400
    assert "broadcast" in served[0][1]["detail"]


def test_cli_bare_hex_broadcast_gets_the_broadcast_limit():
    """Bare-hex 'ffffffff' is parsed as broadcast by the send path — the guard must
    read it the same way, not as a DM."""
    from commands import meshtastic as cmd
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               return_value=True) as sd:
        res = cmd.send_message("x" * (DM_LIMIT + 5), dest="ffffffff")
    assert res.success, res.message
    sd.assert_called_once()


def test_messaging_keeps_its_chunk_markers_inside_the_budget():
    from commands import messaging
    sent = []
    fake = MagicMock()
    fake.send_to_meshtastic.side_effect = \
        lambda chunk, dest, ch: sent.append(chunk) or MagicMock(success=True)
    with patch.object(messaging, "cmd_gateway", fake), \
         patch.object(messaging, "connect_tuned", MagicMock()):
        messaging.send_message("word " * 200, destination="!11223344",
                               network="meshtastic")
    assert len(sent) > 1
    n = len(sent)
    for i, chunk in enumerate(sent, 1):
        assert chunk.startswith(f"[{i}/{n}] ")
        assert len(chunk.encode("utf-8")) <= DM_BUDGET
