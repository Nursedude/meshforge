"""BIC-2 DM slice 1 -- Fable r5 (2026-11-26) folds, red on c9585bbb.

Operator 2026-11-26 (AskUserQuestion): "One origin seam, any doubt refuses"
-- see ``tests/test_dm_origin.py`` for the seam table. Plus the 9 nits and
killer tests for r5's behavioural mutation survivors.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, inbox_api, own_radio  # noqa: E402
from utils.dm_ack_witness import RING_CAP, RING_WINDOW_S, TX_CAP, AckWitness  # noqa: E402
from utils.dm_delivery import (  # noqa: E402
    ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED, ROW_NO_ACK, WAITING_S,
    RoutingReply, Send, Witness, row_verdict,
)
from utils.meshtastic_se_crypto import crypto_available  # noqa: E402

ME = 0x6201CE25
P5 = 0xB03DA894
OTHER = 0x11111111
RELAY = 0x514EAE28
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0
LIVE = Witness(since=T0 - 3600)
LATE = T0 + WAITING_S + 5
TOPIC = "msh/2/e/LongFast/!6201ce25"

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


def _reader(mid, tx_from=None, tx=None, witness=None, counts=None, channels=None):
    def read(now, replies, full=False):
        class W:
            def snapshot(self, now=None):
                return {"witness": witness or Witness(T0 - 10 ** 9), "replies": replies,
                        "tx": dict(tx or {}), "tx_from": dict(tx_from or {}),
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


def _header(*, frm, to, pid, gateway, channel="PKI"):
    from meshtastic.protobuf import mesh_pb2, mqtt_pb2
    pkt = mesh_pb2.MeshPacket()
    pkt.id, pkt.to = pid, to
    setattr(pkt, "from", frm)
    pkt.encrypted = b"\x99" * 24
    pkt.pki_encrypted = True
    se = mqtt_pb2.ServiceEnvelope()
    se.packet.CopyFrom(pkt)
    se.channel_id, se.gateway_id = channel, gateway
    return se.SerializeToString()


def _ack(frm=P5, request_id=PID, pid=0x5151, error=0):
    from tests.test_dm_ack_witness import _env as env
    return env(frm=frm, to=ME, pid=pid, error=error, request_id=request_id)


def _live():
    w = AckWitness()
    w.started(now=T0 - 60)
    return w


# ---------------------------------------------------------------------------
# r5 #1 (important): the own id is stamped AT SEND; any doubt refuses
# ---------------------------------------------------------------------------

class TestSendTimeStamp:

    def test_a1_a_later_registry_id_never_re_judges_an_old_send(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, to="!6201ce25", own_id=ME)
        own_radio.note_own_node(OTHER, source="a swapped radio")
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 90, [RoutingReply(sent + 0.02, ME, PID, "NONE")])["delivery"]
        assert d["state"] != ROW_DELIVERED
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_a2_disagreeing_sources_refuse_and_say_so(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=P5)          # stale id == dest
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 90, [RoutingReply(sent + 2, P5, PID, "NONE")])["delivery"]
        assert d["state"] == ROW_LEFT_UNCONFIRMED and "disagree" in d["detail"]
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_a_conflicted_nak_is_not_kept(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=OTHER)
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        assert rows(sent + 9, [RoutingReply(sent + 2, P5, PID, "NO_CHANNEL")]
                    )["delivery"]["state"] == ROW_FAILED
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_the_stamped_id_alone_proves_a_remote_delivery(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        assert rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NONE")]
                    )["delivery"]["state"] == ROW_DELIVERED

    def test_send_stamps_a_fresh_registry_id(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        own_radio.note_own_node(ME, source="test connection")
        with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
                   return_value=PID):
            code, _ = inbox_api.send({"text": "x", "channel": 0, "dest_num": P5,
                                      "to_id": "!b03da894"})
        assert code == 200
        conn = messaging_mod._init_db()
        mid = conn.execute("SELECT id FROM messages WHERE packet_id = ?", (PID,)).fetchone()[0]
        conn.close()
        assert messaging_mod.dm_origins([(mid, PID)]) == {mid: ME}

    def test_send_never_stamps_a_stale_registry_id(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        own_radio.note_own_node(ME, source="test connection",
                                now=inbox_api.time.time() - own_radio.OWN_FRESH_S - 5)
        with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
                   return_value=PID):
            inbox_api.send({"text": "x", "channel": 0, "dest_num": P5, "to_id": "!b03da894"})
        conn = messaging_mod._init_db()
        mid = conn.execute("SELECT id FROM messages WHERE packet_id = ?", (PID,)).fetchone()[0]
        conn.close()
        assert messaging_mod.dm_origins([(mid, PID)]) == {}

    def test_an_unreadable_origin_store_is_said_and_never_delivers(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        with patch.object(messaging_mod, "dm_origins", side_effect=RuntimeError("disk")):
            d, out = rows(sent + 90, [RoutingReply(sent + 2, P5, PID, "NONE")], full=True)
        assert d["delivery"]["state"] != ROW_DELIVERED
        assert "origin" in (out.get("delivery_error") or "")

    def test_origin_rows_go_with_their_send(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        conn = messaging_mod._init_db()
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        messaging_mod._drop_orphan_heard_rows(conn)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM dm_origin").fetchone()[0]
        conn.close()
        assert n == 0

    def test_the_own_registry_newer_id_replaces_the_old(self):
        """r5 survivor #18: pinned -- a radio swap seen by a connection wins."""
        own_radio.note_own_node(ME, source="a")
        own_radio.note_own_node(OTHER, source="b")
        assert own_radio.own_node() == OTHER

    def test_fresh_own_node_honours_its_age(self):
        own_radio.note_own_node(ME, source="a", now=1000.0)
        assert own_radio.fresh_own_node(now=1000.0 + own_radio.OWN_FRESH_S - 1) == ME
        assert own_radio.fresh_own_node(now=1000.0 + own_radio.OWN_FRESH_S + 1) is None


# ---------------------------------------------------------------------------
# Nits
# ---------------------------------------------------------------------------

class TestWording:

    def test_n2_past_the_ring_is_not_called_never_judged(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [], LIVE, now=T0 + RING_WINDOW_S + 60)
        assert "never judged" not in v.why and "no longer holds" in v.why

    def test_n3_a_witness_gap_never_hides_evidence_in_the_ring(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        Witness(None, "MQTT disconnected (rc=7)"), now=LATE)
        assert v.state == ROW_LEFT_UNCONFIRMED and "MQTT disconnected" in v.detail

    def test_n3_after_a_reconnect_too(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 2, RELAY, PID, "NONE")],
                        Witness(T0 + 30), now=LATE)
        assert v.state == ROW_LEFT_UNCONFIRMED


class TestDecodeWording:

    def test_n4_missing_cryptography_is_a_blind_witness(self):
        w = _live()
        with patch("utils.meshtastic_se_crypto.crypto_available", return_value=False):
            s = w.snapshot(now=T0)
        assert s["witness"].since is None and "cryptography" in s["witness"].why

    def test_n4_decode_errors_never_blame_the_key(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid, counts={"decode_error": 3}, channels={"LongFast": T0})
        d, out = rows(T0 + 120, [], full=True)
        assert "key not held" not in d["delivery"]["detail"]
        assert "key not held" not in out["dm_witness"]["line"]


@needs_pb
class TestRingAccounting:

    def test_n5_a_future_stamped_eviction_is_clamped_to_now(self):
        w = _live()
        w._replies.append(RoutingReply(T0 + 30 * 86400, P5, 0x9999, "NONE"))
        w._reply_ids[0x9999] += 1
        a = _ack()
        for i in range(RING_CAP):
            w.observe(TOPIC, a, now=T0 + 1)
        assert w.snapshot(now=T0 + 2)["witness"].lost_until <= T0 + 1

    def test_n6_aged_out_evictions_are_not_loss(self):
        w = _live()
        a = _ack()
        for i in range(RING_CAP):
            w.observe(TOPIC, a, now=T0)
        later = T0 + RING_WINDOW_S + 100
        w.observe(TOPIC, a, now=later)
        s = w.snapshot(now=later + 1)
        assert s["counts"]["reply_evicted"] == 0 and s["witness"].lost_until is None

    def test_r5_survivor_10_lost_until_is_the_newest_loss(self):
        w = _live()
        w._replies.append(RoutingReply(T0 + 10, P5, 0x9998, "NONE"))
        w._replies.append(RoutingReply(T0 + 5, P5, 0x9997, "NONE"))
        w._reply_ids[0x9998] += 1
        w._reply_ids[0x9997] += 1
        a = _ack()
        for i in range(RING_CAP - 2):
            w.observe(TOPIC, a, now=T0 + 20)
        w.observe(TOPIC, a, now=T0 + 20)
        w.observe(TOPIC, a, now=T0 + 20)
        assert w.snapshot(now=T0 + 21)["witness"].lost_until == T0 + 10

    def test_r5_survivor_12_a_header_stays_while_a_second_reply_remains(self):
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=ME, pid=PID, gateway="!6201ce25"), now=T0)
        w.observe(TOPIC, _ack(frm=ME, pid=0x10), now=T0 + 0.02)
        for i in range(TX_CAP + 5):
            w.observe(TOPIC, _header(frm=ME, to=P5, pid=0x10000 + i, gateway="!6201ce25"),
                      now=T0 + 1)
        filler = _ack(frm=P5, request_id=0x7777)
        for i in range(RING_CAP - 1):
            w.observe(TOPIC, filler, now=T0 + 2)
        w.observe(TOPIC, _ack(frm=ME, pid=0x11), now=T0 + 3)   # 2nd reply for PID
        w.observe(TOPIC, filler, now=T0 + 4)                   # evicts the 1st
        assert w.snapshot(now=T0 + 5)["tx_from"].get(PID) == ME


@needs_pb
class TestHeaderSources:

    @pytest.mark.parametrize("gw", ["!+201ce25", "!0x1ce25a", "!6201_e25", "!6201ce2", "!6201ce255"])
    def test_n8_only_a_strict_node_id_is_a_gateway(self, gw):
        assert dm_ack_witness._gateway_num(gw) is None

    def test_n9_another_gateways_copy_carries_the_dms_own_from(self):
        w = _live()
        w.observe("msh/2/e/PKI/!5f01371f",
                  _header(frm=ME, to=P5, pid=PID, gateway="!5f01371f"), now=T0)
        s = w.snapshot(now=T0 + 1)
        assert s["tx_from"].get(PID) == ME and s["tx"].get(PID) is None


class TestDeliveryShape:
    """r5 #7: every delivery dict has the same keys."""

    KEYS = {"state", "detail", "reason", "said_by", "why", "latency_s", "late",
            "sent_n", "kept", "hints", "origin"}

    def test_live_kept_and_unreadable_rows_share_one_shape(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        live = rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NONE")])["delivery"]
        kept = rows(sent + 9, [])["delivery"]
        with patch.object(inbox_api, "_epoch", return_value=None):
            mid2 = _store(tmp_path, monkeypatch, own_id=ME)
            unread = _reader(mid2)(sent + 9, [])["delivery"]
        assert kept["kept"] is True
        for d in (live, kept, unread):
            assert set(d) == self.KEYS, set(d) ^ self.KEYS

    def test_r5_survivor_19_a_standing_kept_failure_carries_live_hints(self, tmp_path,
                                                                       monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        nak = RoutingReply(sent + 2, RELAY, PID, "NO_ROUTE")
        rows(sent + 3, [nak])
        d = rows(sent + 30, [nak, RoutingReply(sent + 17, ME, PID, "TIMEOUT")])["delivery"]
        assert d["kept"] is True and any("gave up" in h for h in d["hints"])

    def test_r5_survivor_20_the_witness_names_the_own_id(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        own_radio.note_own_node(ME, source="t")
        _, out = _reader(mid)(T0, [], full=True)
        assert out["dm_witness"]["own_id"] == "!6201ce25"


class TestSurvivorsR5:

    def test_survivor_7_a_late_bounded_ack_is_not_long_after(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 300, P5, PID, "NONE")],
                        LIVE, now=T0 + 301)
        assert v.state == ROW_DELIVERED and v.late

    def test_survivor_5_a_loss_before_the_send_is_not_this_sends(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [], Witness(T0 - 3600, lost_until=T0 - 60),
                        now=LATE)
        assert v.why == "window closed"

    def test_survivor_24_a_string_delivery_is_not_a_crash(self):
        from launcher_tui.handlers.messaging import dm_delivery_lines
        doc = {"state": "ok", "dm_witness": {"line": "x"}, "threads": [{
            "kind": "dm", "network": "meshtastic", "partner": "!b03da894", "messages": [{
                "direction": "out", "ts": "2026-11-26T00:00:00", "state": "handed_off",
                "content": "", "delivery": "delivered"}]}]}
        assert dm_delivery_lines((doc, None))


class TestMutantKillersR5Fold:
    """Survivors of the author's baseline-gated pass over this fold (26/33;
    #1 ``own if own else hdr`` is equivalent: past the conflict check
    own == hdr whenever both are known)."""

    def test_k14_an_unreadable_origin_store_refuses_even_with_a_header(self, tmp_path,
                                                                       monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        with patch.object(messaging_mod, "dm_origins", side_effect=RuntimeError("disk")):
            d = rows(sent + 90, [RoutingReply(sent + 2, P5, PID, "NONE")])["delivery"]
        assert d["state"] != ROW_DELIVERED
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_k19_a_kept_latency_past_the_bound_is_not_late(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                           "latency_s": 700.0})])
        assert _reader(mid)(T0, [])["delivery"]["late"] is False

    def test_k20_an_old_store_without_dm_origin_is_migrated(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch, own_id=ME)
        conn = messaging_mod._init_db()
        conn.execute("DROP TABLE dm_origin")
        conn.commit()
        conn.close()
        conn = messaging_mod._init_db()
        conn.execute("SELECT own_id FROM dm_origin LIMIT 0")
        conn.close()
        assert messaging_mod.dm_origins([(mid, PID)]) == {}

    def test_k22_a_broadcast_is_never_stamped(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        r = messaging_mod.store_outgoing("Wx", "meshtastic", None, channel=0, delivered=True,
                                         packet_id=PID, own_id=ME)
        assert messaging_mod.dm_origins([(r.data["message_id"], PID)]) == {}

    def test_k24_an_id_two_hours_old_is_not_fresh(self):
        own_radio.note_own_node(ME, source="a", now=1000.0)
        assert own_radio.fresh_own_node(now=1000.0 + 2 * 3600) is None
