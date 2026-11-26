"""BIC-2 DM slice 1 -- Fable r6 (2026-11-26) folds, red on dcb28766.

r6 was CLEAN (0 important). Operator 2026-11-26 (AskUserQuestion): "Fold
false-delivered nits, r7" -- every path r6 found that can still show a
false "delivered" (the one outcome the feature must never show), plus the
cheap related nits and test gaps.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, inbox_api, own_radio  # noqa: E402
from utils.dm_ack_witness import TX_CAP, AckWitness  # noqa: E402
from utils.dm_delivery import (  # noqa: E402
    ORIGIN_REMOTE, ORIGIN_SELF, ORIGIN_UNKNOWN, OWN_UNREADABLE, ROW_DELIVERED, ROW_FAILED,
    RoutingReply, Witness, dm_origin,
)
from utils.meshtastic_se_crypto import crypto_available  # noqa: E402

ME = 0x6201CE25
P5 = 0xB03DA894
OTHER = 0x11111111
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0
TOPIC = "msh/2/e/PKI/!5f01371f"

needs_pb = pytest.mark.skipif(not crypto_available(),
                              reason="meshtastic protobufs / cryptography not installed")


@pytest.fixture(autouse=True)
def _no_own_id():
    own_radio._reset()
    yield
    own_radio._reset()


def _store(tmp_path, monkeypatch, to="!b03da894", own_id=None):
    db = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    r = messaging_mod.store_outgoing("Wx", "meshtastic", to, channel=0, delivered=True,
                                     packet_id=PID, own_id=own_id)
    return r.data["message_id"]


def _reader(mid, tx_from=None, witness=None, counts=None, channels=None):
    def read(now, replies, full=False):
        class W:
            def snapshot(self, now=None):
                return {"witness": witness or Witness(T0 - 10 ** 9), "replies": replies,
                        "tx": {}, "tx_from": dict(tx_from or {}),
                        "channels": dict(channels or {}), "decrypted": {},
                        "counts": dict(counts or {})}
        with patch.object(inbox_api.time, "time", return_value=now), \
             patch("utils.message_listener.get_listener_status", return_value={}), \
             patch("utils.inbox_legs.load_sections", return_value=None), \
             patch.object(dm_ack_witness, "get_witness", return_value=W()):
            out = inbox_api.read_inbox()
        row = {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]
        return (row, out) if full else row
    return read


def _plant_origin(mid, value):
    conn = messaging_mod._init_db()
    conn.execute("INSERT OR REPLACE INTO dm_origin (message_id, packet_id, own_id) "
                 "VALUES (?, ?, ?)", (mid, PID, value))
    conn.commit()
    conn.close()


# --- (a) 0 / broadcast / non-ints are never a node in the seam -------------

@pytest.mark.parametrize("bad", [0, 0xFFFFFFFF, True, 1.5, "", "!00000000", "!ffffffff",
                                 "!+201ce25", "!0x1ce25a"])
def test_a_the_seam_never_takes_a_non_node_as_a_header(bad):
    assert dm_origin(P5, None, bad).state == ORIGIN_UNKNOWN


@pytest.mark.parametrize("bad", [0, 0xFFFFFFFF, True, 1.5, ""])
def test_a_the_seam_never_takes_a_non_node_as_a_stamp(bad):
    assert dm_origin(P5, bad, ME).state == ORIGIN_UNKNOWN


def test_a_a_zero_header_never_delivers(tmp_path, monkeypatch):
    """r6 H4."""
    mid = _store(tmp_path, monkeypatch)
    rows = _reader(mid, tx_from={PID: 0})
    sent = inbox_api._epoch(rows(0, [])["ts"])
    d = rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NONE")])["delivery"]
    assert d["state"] != ROW_DELIVERED
    assert messaging_mod.kept_dm([(mid, PID)])[0] == {}


# --- (b) a corrupt stamp is unreadable, counted, never absent --------------

@pytest.mark.parametrize("value", ["junk", -5, 0, 1.5, 0xFFFFFFFF])
def test_b_a_corrupt_stamp_refuses_and_is_said(tmp_path, monkeypatch, value):
    """r6 M2."""
    mid = _store(tmp_path, monkeypatch)
    _plant_origin(mid, value)
    assert messaging_mod.dm_origins([(mid, PID)]) == {mid: OWN_UNREADABLE}
    rows = _reader(mid, tx_from={PID: ME})
    sent = inbox_api._epoch(rows(0, [])["ts"])
    d, out = rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NONE")], full=True)
    assert d["delivery"]["state"] != ROW_DELIVERED
    assert "origin row(s) unreadable" in (out.get("delivery_error") or "")


def test_b_the_origin_reader_matches_the_packet_id(tmp_path, monkeypatch):
    """r6 survivor 30: the swap guard."""
    mid = _store(tmp_path, monkeypatch, own_id=ME)
    assert messaging_mod.dm_origins([(mid, PID + 1)]) == {}


def test_b_the_writer_refuses_a_non_node(tmp_path, monkeypatch):
    """r6 survivor 27."""
    for bad in (0, 0xFFFFFFFF, -1):
        mid = _store(tmp_path, monkeypatch, own_id=bad)
        assert messaging_mod.dm_origins([(mid, PID)]) == {}


# --- (c) the stamp comes only from the radio this box sends through --------

class _Collector:
    def __init__(self, host):
        self._host = host

    def get_meshtasticd_host(self):
        return self._host

    @staticmethod
    def _canonical_meshtastic_id(_id, num):
        return f"!{num:08x}"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_c_a_local_collector_notes_the_id(host):
    from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin
    iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ME), localNode=None)
    MeshtasticDataCollectorMixin._own_node_id(_Collector(host), iface)
    assert own_radio.own_node() == ME


def test_c_a_remote_collector_never_notes_the_id():
    """r6 #1 (W1): the collector read ANOTHER box's radio; inbox sends via
    localhost -- that id is not this box's own."""
    from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin
    iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ME), localNode=None)
    got = MeshtasticDataCollectorMixin._own_node_id(_Collector("192.0.2.7"), iface)
    assert got == "!6201ce25"                      # the collector still uses it
    assert own_radio.own_node() is None            # the DM judge does not


def test_c_a_future_note_is_not_fresh():
    """r6 survivor 46."""
    own_radio.note_own_node(ME, source="a", now=2000.0)
    assert own_radio.fresh_own_node(now=1000.0) is None


# --- (d) a kept delivered passes through the seam ---------------------------

@pytest.mark.parametrize("stamp,header", [(ME, None), (ME, P5), (None, ME)])
def test_d_a_kept_delivered_the_seam_refutes_is_dropped(tmp_path, monkeypatch, stamp, header):
    """r6 #6: a pre-r6 wrong keep is not permanent. DM to our own radio:
    self (stamp or header) or conflict -> the kept delivered goes, counted."""
    mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=stamp)
    messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                       "latency_s": 0.0})])
    rows = _reader(mid, tx_from={PID: header} if header is not None else {})
    d, out = rows(T0, [], full=True)
    assert d["delivery"]["state"] != ROW_DELIVERED
    assert messaging_mod.kept_dm([(mid, PID)])[0] == {}
    assert "kept delivery row(s) refuted by the origin" in (out.get("delivery_error") or "")


def test_d_a_kept_delivered_with_unknown_origin_stands(tmp_path, monkeypatch):
    """Legacy rows with no stamp and no header in the (restarted) ring: shown."""
    mid = _store(tmp_path, monkeypatch)
    messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                       "latency_s": 2.0})])
    d = _reader(mid)(T0, [])["delivery"]
    assert d["state"] == ROW_DELIVERED and d["kept"] is True


def test_d_kept_rows_carry_an_origin_from_the_closed_enum(tmp_path, monkeypatch):
    """r6 #12."""
    from utils.dm_delivery import ORIGIN_STATES
    mid = _store(tmp_path, monkeypatch, own_id=ME)
    messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                       "latency_s": 2.0})])
    d = _reader(mid)(T0, [])["delivery"]
    assert d["origin"] in ORIGIN_STATES and d["origin"] == ORIGIN_REMOTE


# --- keep gate (r6 survivors 11, 12) ----------------------------------------

def test_keep_a_self_dm_failure_is_kept(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=ME)
    rows = _reader(mid)
    sent = inbox_api._epoch(rows(0, [])["ts"])
    d = rows(sent + 5, [RoutingReply(sent + 2, ME, PID, "NO_CHANNEL")])["delivery"]
    assert d["state"] == ROW_FAILED and d["origin"] == ORIGIN_SELF
    assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["said_by"] == "!6201ce25"


def test_keep_an_unknown_origin_failure_is_not_kept(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch)
    rows = _reader(mid)
    sent = inbox_api._epoch(rows(0, [])["ts"])
    rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NO_CHANNEL")])
    assert messaging_mod.kept_dm([(mid, PID)])[0] == {}


# --- stamp transaction (r6 #10) ---------------------------------------------

def test_a_stamp_fault_never_loses_the_send_record(tmp_path, monkeypatch):
    db = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
    conn = messaging_mod._init_db()
    conn.execute("DROP TABLE dm_origin")
    conn.execute("CREATE TABLE dm_origin (message_id INTEGER, packet_id INTEGER, "
                 "own_id INTEGER, CHECK (own_id < 0))")
    conn.commit()
    conn.close()
    r = messaging_mod.store_outgoing("Wx", "meshtastic", "!b03da894", channel=0,
                                     delivered=True, packet_id=PID, own_id=ME)
    assert r.success and r.data.get("stamped") is False


# --- wording (r6 #3) ---------------------------------------------------------

def test_wording_a_blind_witness_adds_no_key_note(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch, own_id=ME)
    d = _reader(mid, witness=Witness(None, "no crypto"), channels={"LongFast": T0})(
        T0 + 120, [])["delivery"]
    assert "key not held" not in d["detail"] and "unreadable here" not in d["detail"]


def test_wording_decode_faults_never_assert_a_cause(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch, own_id=ME)
    d, out = _reader(mid, counts={"decode_error": 1}, channels={"LongFast": T0})(
        T0 + 120, [], full=True)
    for text in (d["delivery"]["detail"], out["dm_witness"]["line"]):
        assert "cause unknown" in text
        assert "not the key" not in text and "key not held" not in text


# --- _tx_order (r6 #7, #8, survivors 34-36) ---------------------------------

def _header(*, frm, to, pid, gateway):
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    pkt = mesh_pb2.MeshPacket()
    pkt.id, pkt.to = pid, to
    setattr(pkt, "from", frm)
    pkt.encrypted = b"\x99" * 24
    pkt.pki_encrypted = True
    se = mqtt_pb2.ServiceEnvelope()
    se.packet.CopyFrom(pkt)
    se.channel_id, se.gateway_id = "PKI", gateway
    return se.SerializeToString()


def _live():
    w = AckWitness()
    w.started(now=T0 - 60)
    return w


@needs_pb
class TestTxOrder:

    def test_relay_only_headers_are_bounded(self):
        w = _live()
        for i in range(TX_CAP + 50):
            w.observe(TOPIC, _header(frm=ME, to=P5, pid=0x20000 + i, gateway="!5f01371f"),
                      now=T0)
        assert len(w.snapshot(now=T0 + 1)["tx_from"]) <= TX_CAP
        assert len(w._tx_order_ids) == len(w._tx_order) <= TX_CAP

    def test_copies_of_one_packet_take_one_slot(self):
        """r6 #7: 4000 copies of ONE packet never evict other headers."""
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        same = _header(frm=ME, to=P5, pid=0x30000, gateway="!5f01371f")
        for _ in range(TX_CAP + 5):
            w.observe(TOPIC, same, now=T0)
        assert w.snapshot(now=T0 + 1)["tx_from"].get(PID) == ME
        assert len(w._tx_order) == 2

    def test_first_writer_survives_a_reply_eviction(self):
        """r6 #8: the header stays while its id is in _tx_order."""
        from tests.test_dm_ack_witness import _env
        from utils.dm_ack_witness import RING_CAP
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        w.observe("msh/2/e/LongFast/!6201ce25",
                  _env(frm=P5, to=ME, pid=0x5151, request_id=PID), now=T0 + 1)
        filler = _env(frm=P5, to=ME, pid=0x5152, request_id=0x7777)
        for _ in range(RING_CAP):
            w.observe("msh/2/e/LongFast/!6201ce25", filler, now=T0 + 2)
        w.observe(TOPIC, _header(frm=OTHER, to=P5, pid=PID, gateway="!5f01371f"), now=T0 + 3)
        s = w.snapshot(now=T0 + 4)
        assert s["tx_from"].get(PID) == ME and s["counts"]["tx_from_conflict"] == 1
