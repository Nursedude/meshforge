"""BIC-2 DM slice 1 -- Fable r7 (2026-11-26) folds, red on 3405a6ab.

Operator 2026-11-26 (AskUserQuestion): "Fold important + nits, r8".
r7 #1 (important, from the r6 fold): a DM to a short-form id (``!a1b2c3d``,
which the send validator accepts) could never read delivered -- the
validator and the origin seam parsed node ids with two patterns (hfm #5).
"""
import logging
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, dm_delivery, inbox_api, own_radio  # noqa: E402
from utils.dm_ack_witness import TX_CAP, AckWitness  # noqa: E402
from utils.dm_delivery import (  # noqa: E402
    ORIGIN_REMOTE, ROW_DELIVERED, ROW_FAILED, RoutingReply, Witness, dm_origin,
)
from utils.meshtastic_se_crypto import crypto_available  # noqa: E402

ME = 0x6201CE25
P5 = 0xB03DA894
SHORT = 0x0A1B2C3D
OTHER = 0x11111111
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0

needs_pb = pytest.mark.skipif(not crypto_available(),
                              reason="meshtastic protobufs / cryptography not installed")


@pytest.fixture(autouse=True)
def _clean():
    own_radio._reset()
    inbox_api._REFUTED.clear()
    yield
    own_radio._reset()
    inbox_api._REFUTED.clear()


def _db(tmp_path, monkeypatch):
    db = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)


def _store(tmp_path, monkeypatch, to="!b03da894", own_id=None):
    _db(tmp_path, monkeypatch)
    r = messaging_mod.store_outgoing("Wx", "meshtastic", to, channel=0, delivered=True,
                                     packet_id=PID, own_id=own_id)
    return r.data["message_id"]


def _reader(mid, tx_from=None, witness=None, counts=None, channels=None, decrypted=None):
    def read(now, replies, full=False):
        class W:
            def snapshot(self, now=None):
                return {"witness": witness or Witness(T0 - 10 ** 9), "replies": replies,
                        "tx": {}, "tx_from": dict(tx_from or {}),
                        "channels": dict(channels or {}), "decrypted": dict(decrypted or {}),
                        "counts": dict(counts or {})}
        with patch.object(inbox_api.time, "time", return_value=now), \
             patch("utils.message_listener.get_listener_status", return_value={}), \
             patch("utils.inbox_legs.load_sections", return_value=None), \
             patch.object(dm_ack_witness, "get_witness", return_value=W()):
            out = inbox_api.read_inbox()
        row = {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]
        return (row, out) if full else row
    return read


# --- r7 #1 (important): ONE node-id format for the validator and the seam ----

class TestOneIdFormat:

    @pytest.mark.parametrize("typed", ["!a1b2c3d", "!A1B2C3D", "!0a1b2c3d", "!0A1B2C3D"])
    def test_send_stores_the_canonical_id(self, typed):
        params, err = inbox_api.validate_send({"network": "meshtastic", "text": "x",
                                               "destination": typed})
        assert err is None and params["to_id"] == "!0a1b2c3d"
        assert params["dest_num"] == SHORT

    def test_the_seam_and_the_validator_share_one_pattern(self):
        assert inbox_api._NODE_ID is dm_delivery.NODE_ID_RE

    def test_a_short_form_row_already_stored_still_settles(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, to="!a1b2c3d", own_id=ME)
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 5, [RoutingReply(sent + 2, SHORT, PID, "NONE")])["delivery"]
        assert d["state"] == ROW_DELIVERED

    @pytest.mark.parametrize("typed", ["!a1b2c3d", "!0a1b2c3d", SHORT])
    def test_the_seam_reads_every_form_alike(self, typed):
        assert dm_origin(typed, ME, None).state == ORIGIN_REMOTE

    @pytest.mark.parametrize("bad", ["!FFFFFFFF", "!0", "!00000000"])
    def test_broadcast_or_zero_is_never_a_dm(self, bad):
        params, err = inbox_api.validate_send({"network": "meshtastic", "text": "x",
                                               "destination": bad})
        assert params is None or params["to_id"] is None


# --- r7 #2 + #4: a refuted kept delivered leaves a durable witness ----------

def _kept_self_dm(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=ME)
    messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                       "latency_s": 0.0})])
    return mid


class TestRefuteWitness:

    def test_a_drop_is_logged_and_counted(self, tmp_path, monkeypatch, caplog):
        mid = _kept_self_dm(tmp_path, monkeypatch)
        with caplog.at_level(logging.WARNING, logger=inbox_api.logger.name):
            _, out = _reader(mid)(T0, [], full=True)
        assert any("refuted" in r.getMessage() for r in caplog.records)
        assert out["dm_witness"]["refuted_kept"] == 1
        _, out = _reader(mid)(T0 + 5, [], full=True)
        assert out["dm_witness"]["refuted_kept"] == 1          # durable for the process

    def test_dropped_is_said_only_when_it_happened(self, tmp_path, monkeypatch):
        mid = _kept_self_dm(tmp_path, monkeypatch)
        with patch.object(messaging_mod, "drop_dm_kept", side_effect=RuntimeError("locked")):
            _, out = _reader(mid)(T0, [], full=True)
        err = out.get("delivery_error") or ""
        assert "not dropped" in err and "— dropped" not in err      # r7 #4, survivor 30

    def test_a_kept_destination_failure_the_seam_refutes_is_dropped(self, tmp_path,
                                                                   monkeypatch):
        """r7 #6: "said by the destination" is false for a self/conflict origin."""
        mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=ME)
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_FAILED, "detail": "failed: NO_CHANNEL",
                                           "reason": "NO_CHANNEL",
                                           "said_by": "the destination"})])
        d = _reader(mid)(T0, [])["delivery"]
        assert d.get("said_by") != "the destination"
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_a_kept_relay_failure_stands_whatever_the_origin(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=ME)
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_FAILED, "detail": "failed: NO_ROUTE",
                                           "reason": "NO_ROUTE", "said_by": "!514eae28"})])
        assert _reader(mid)(T0, [])["delivery"]["kept"] is True


# --- r7 #5: the witness snapshot is taken after the store reads -------------

def test_the_snapshot_follows_the_store_reads(tmp_path, monkeypatch):
    mid = _store(tmp_path, monkeypatch, own_id=ME)
    order = []
    real_kept = messaging_mod.kept_dm

    class W:
        def snapshot(self, now=None):
            order.append("snapshot")
            return {"witness": Witness(T0 - 10 ** 9), "replies": [], "tx": {}, "tx_from": {},
                    "channels": {}, "decrypted": {}, "counts": {}}

    def kept(sends):
        order.append("kept")
        return real_kept(sends)
    with patch("utils.message_listener.get_listener_status", return_value={}), \
         patch("utils.inbox_legs.load_sections", return_value=None), \
         patch.object(messaging_mod, "kept_dm", side_effect=kept), \
         patch.object(dm_ack_witness, "get_witness", return_value=W()):
        inbox_api.read_inbox()
    assert order.index("kept") < order.index("snapshot")


# --- r7 #3: a failed stamp is said ----------------------------------------------

def test_a_failed_stamp_is_said_in_the_send_body(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    conn = messaging_mod._init_db()
    conn.execute("DROP TABLE dm_origin")
    conn.execute("CREATE TABLE dm_origin (message_id INTEGER, packet_id INTEGER, "
                 "own_id INTEGER, CHECK (own_id < 0))")
    conn.commit()
    conn.close()
    own_radio.note_own_node(ME, source="t")
    with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id", return_value=PID):
        code, body = inbox_api.send({"text": "x", "channel": 0, "dest_num": P5,
                                     "to_id": "!b03da894"})
    assert code == 200 and body["recorded"] is True and "stamp_error" in body


# --- r7 #8: a reply a hair before the stamp is shown as 0.0 s -----------------

def test_a_reply_just_before_the_stamp_shows_no_negative_latency():
    from utils.dm_delivery import Send, row_verdict
    v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 - 1, P5, PID, "NONE")],
                    Witness(T0 - 3600), now=T0 + 3)
    assert v.state == ROW_DELIVERED and "-" not in v.detail and v.latency_s >= 0


# --- witness line branches (survivors 32, 33) --------------------------------

class TestWitnessLine:

    def _line(self, since, counts, channels, decrypted=None):
        snap = {"witness": Witness(since), "replies": [], "tx": {}, "tx_from": {},
                "channels": channels, "decrypted": decrypted or {}, "counts": counts}
        return inbox_api._witness_line(snap, T0 + 1, 0)["line"]

    def test_a_blind_witness_names_no_unreadable_channel(self):
        assert "seen but unreadable" not in self._line(None, {}, {"Secret": T0})

    def test_decode_faults_alone_are_said(self):
        line = self._line(T0 - 60, {"decode_error": 2}, {}, {"LongFast": T0})
        assert "2 decode fault(s) seen (cause unknown)" in line


# --- r7 #7 + survivors 53/55: the _tx eviction guards its header -------------

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
class TestTxEvictionGuards:
    RELAY_TOPIC = "msh/2/e/PKI/!5f01371f"
    OWN_TOPIC = "msh/2/e/PKI/!6201ce25"

    def _evict_tx(self, w):
        for i in range(TX_CAP + 5):
            w.observe(self.OWN_TOPIC, _header(frm=ME, to=P5, pid=0x40000 + i,
                                              gateway="!6201ce25"), now=T0 + 1)

    def test_a_header_still_in_tx_order_survives_tx_eviction(self):
        """r7 #7: relay copy first (tx_order), then our own uplink (tx)."""
        w = _live()
        w.observe(self.RELAY_TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        w.observe(self.OWN_TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!6201ce25"), now=T0)
        self._evict_tx(w)
        assert w.snapshot(now=T0 + 2)["tx_from"].get(PID) == ME

    def test_tx_order_eviction_keeps_an_own_uplinked_header(self):
        """survivor 53: an id evicted from _tx_order but still in _tx keeps it."""
        w = _live()
        w.observe(self.RELAY_TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        w.observe(self.OWN_TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!6201ce25"), now=T0)
        for i in range(TX_CAP + 5):
            w.observe(self.RELAY_TOPIC, _header(frm=ME, to=P5, pid=0x50000 + i,
                                                gateway="!5f01371f"), now=T0 + 1)
        assert w.snapshot(now=T0 + 2)["tx_from"].get(PID) == ME

    def test_tx_order_eviction_keeps_a_header_guarding_a_reply(self):
        """survivor 55."""
        from tests.test_dm_ack_witness import _env
        w = _live()
        w.observe(self.RELAY_TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        w.observe("msh/2/e/LongFast/!6201ce25",
                  _env(frm=P5, to=ME, pid=0x5151, request_id=PID), now=T0 + 0.5)
        for i in range(TX_CAP + 5):
            w.observe(self.RELAY_TOPIC, _header(frm=ME, to=P5, pid=0x60000 + i,
                                                gateway="!5f01371f"), now=T0 + 1)
        assert w.snapshot(now=T0 + 2)["tx_from"].get(PID) == ME
