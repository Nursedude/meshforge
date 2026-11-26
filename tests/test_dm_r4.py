"""BIC-2 DM slice 1 -- Fable r4 (2026-11-26) folds, red on ca2d6a81.

Operator 2026-11-26 (AskUserQuestion):
* "Both: send-time + hardened" -- r4 #1: a DM to our own radio read
  "delivered" (and was kept forever) once the witness lost the DM's own
  uplinked header. Now: this box's own node id, read from an EXISTING radio
  connection (never inferred from traffic), decides self-DM when known;
  ``tx_from`` is first-writer-wins, counted on conflict, and never evicted
  while a reply it guards is in the ring; with NEITHER known, a destination
  ack is not delivery (the row says why) until the header is seen.
* "Important first, then all 9" -- the nits below, plus killer tests for
  the behavioural mutation survivors of r4's baseline-gated run.
"""
import inspect
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, dm_delivery, inbox_api, own_radio  # noqa: E402
from utils.dm_ack_witness import COUNTERS, RING_CAP, TX_CAP, AckWitness  # noqa: E402
from utils.dm_delivery import (  # noqa: E402
    REPLY_MAX_S, ROW_DELIVERED, ROW_FAILED, ROW_LEFT_UNCONFIRMED, ROW_NO_ACK,
    ROW_WAITING, WAITING_S, RoutingReply, Send, Witness, classify, row_verdict,
)
from utils.meshtastic_se_crypto import crypto_available  # noqa: E402

ME = 0x6201CE25
P5 = 0xB03DA894
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


def _header(*, frm, to, pid, gateway, channel="PKI"):
    """A DM's uplinked header (PKI body, readable header) -- as r3's drill."""
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
# r4 #1 (important): self-DM never reads delivered, whatever the witness lost
# ---------------------------------------------------------------------------

class TestOwnRadioRegistry:

    def test_unknown_by_default(self):
        assert own_radio.own_node() is None

    def test_noted_from_an_existing_connection(self):
        own_radio.note_own_node(ME, source="map collector TCP interface")
        assert own_radio.own_node() == ME
        assert own_radio.own_node_status()["source"] == "map collector TCP interface"

    @pytest.mark.parametrize("bad", [None, 0, -1, True, 0x1_0000_0000, "!6201ce25", 0xFFFFFFFF])
    def test_refuses_what_cannot_be_a_node(self, bad):
        own_radio.note_own_node(bad, source="x")
        assert own_radio.own_node() is None

    def test_the_map_collector_notes_the_id_it_reads_from_the_connection(self):
        from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin
        iface = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=ME), localNode=None)
        MeshtasticDataCollectorMixin._own_node_id(MeshtasticDataCollectorMixin.__new__(
            MeshtasticDataCollectorMixin), iface)
        assert own_radio.own_node() == ME


class TestOriginRule:

    def test_own_id_known_decides_self_dm_without_any_header(self):
        """a2/a3/a5/a7: the header lost, never uplinked or overwritten."""
        v = row_verdict(Send(T0, PID, ME, own=ME), [RoutingReply(T0 + 0.02, ME, PID, "NONE")],
                        LIVE, now=LATE)
        assert v.state != ROW_DELIVERED
        assert "own radio" in v.detail

    def test_own_id_beats_a_poisoned_header(self):
        """a4/a5: a same-id envelope claims a different sender."""
        v = row_verdict(Send(T0, PID, ME, sender=P5, own=ME),
                        [RoutingReply(T0 + 0.02, ME, PID, "NONE")], LIVE, now=LATE)
        assert v.state != ROW_DELIVERED

    def test_own_id_known_remote_ack_is_delivered_without_a_header(self):
        v = row_verdict(Send(T0, PID, P5, own=ME), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_neither_known_a_destination_ack_is_not_delivery_after_the_window(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=LATE)
        assert v.state == ROW_LEFT_UNCONFIRMED
        assert "header" in v.detail

    def test_neither_known_inside_the_window_is_waiting(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_WAITING

    def test_neither_known_a_destination_nak_is_not_said_by_the_destination(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NO_CHANNEL")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_FAILED and v.said_by == "!b03da894"

    def test_header_seen_is_enough(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=T0 + 3)
        assert v.state == ROW_DELIVERED

    def test_the_drill_classifier_keeps_its_plain_rule(self):
        """scripts/dm_delivery_check.py reads the radio's own session."""
        (o,) = classify([Send(T0, PID, P5)], [RoutingReply(T0 + 2, P5, PID, "NONE")])
        assert o.status == "DELIVERED"


@needs_pb
class TestTxFromHardening:

    def test_first_writer_wins_and_a_conflict_is_counted(self):
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=ME, pid=PID, gateway="!6201ce25"), now=T0)
        w.observe(TOPIC, _header(frm=P5, to=ME, pid=PID, gateway="!b03da894"), now=T0 + 1)
        s = w.snapshot(now=T0 + 2)
        assert s["tx_from"][PID] == ME
        assert s["counts"]["tx_from_conflict"] == 1

    def test_a_header_guarding_a_reply_survives_tx_eviction(self):
        """a3 / r4 mutant 32."""
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=ME, pid=PID, gateway="!6201ce25"), now=T0)
        w.observe(TOPIC, _ack(frm=ME), now=T0 + 0.02)
        for i in range(TX_CAP + 5):
            w.observe(TOPIC, _header(frm=ME, to=P5, pid=0x10000 + i, gateway="!6201ce25"),
                      now=T0 + 1)
        assert w.snapshot(now=T0 + 2)["tx_from"].get(PID) == ME

    def test_an_unguarded_header_is_evicted_with_its_copy_count(self):
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=ME, pid=PID, gateway="!6201ce25"), now=T0)
        for i in range(TX_CAP + 5):
            w.observe(TOPIC, _header(frm=ME, to=P5, pid=0x10000 + i, gateway="!6201ce25"),
                      now=T0 + 1)
        s = w.snapshot(now=T0 + 2)
        assert PID not in s["tx_from"] and len(s["tx_from"]) <= TX_CAP

    def test_a_kept_header_leaves_when_its_last_reply_leaves_the_ring(self):
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=ME, pid=PID, gateway="!6201ce25"), now=T0)
        w.observe(TOPIC, _ack(frm=ME), now=T0 + 0.02)
        for i in range(TX_CAP + 5):
            w.observe(TOPIC, _header(frm=ME, to=P5, pid=0x10000 + i, gateway="!6201ce25"),
                      now=T0 + 1)
        other = _ack(frm=P5, request_id=0x7777)
        for i in range(RING_CAP):
            w.observe(TOPIC, other, now=T0 + 2)
        s = w.snapshot(now=T0 + 3)
        assert PID not in s["tx_from"]
        assert s["counts"]["reply_evicted"] >= 1

    def test_an_upper_case_gateway_id_is_the_senders_own_uplink(self):
        """r4 #8."""
        w = _live()
        w.observe(TOPIC, _header(frm=ME, to=P5, pid=PID, gateway="!6201CE25"), now=T0)
        s = w.snapshot(now=T0 + 1)
        assert s["tx_from"].get(PID) == ME and s["tx"].get(PID) == 1

    def test_new_counters_are_in_the_closed_vocabulary(self):
        for k in ("tx_from_conflict", "reply_evicted", "decode_error"):
            assert k in COUNTERS


def _store(tmp_path, monkeypatch, to="!b03da894"):
    db = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    r = messaging_mod.store_outgoing("Wx", "meshtastic", to, channel=0,
                                     delivered=True, packet_id=PID)
    return r.data["message_id"]


def _reader(mid, tx_from=None, tx=None, witness=None, counts=None):
    def read(now, replies, full=False):
        class W:
            def snapshot(self, now=None):
                return {"witness": witness or Witness(T0 - 10 ** 9), "replies": replies,
                        "tx": dict(tx or {}), "tx_from": dict(tx_from or {}),
                        "channels": {}, "decrypted": {}, "counts": dict(counts or {})}
        with patch.object(inbox_api.time, "time", return_value=now), \
             patch("utils.message_listener.get_listener_status", return_value={}), \
             patch("utils.inbox_legs.load_sections", return_value=None), \
             patch.object(dm_ack_witness, "get_witness", return_value=W()):
            out = inbox_api.read_inbox()
        row = {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]
        return (row, out) if full else row
    return read


class TestInboxOrigin:

    def test_inbox_passes_this_boxs_own_id_to_the_judge(self, tmp_path, monkeypatch):
        """a2 end to end: no header, own ack, own id known -> never delivered."""
        mid = _store(tmp_path, monkeypatch, to="!6201ce25")
        own_radio.note_own_node(ME, source="test connection")
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 90, [RoutingReply(sent + 0.02, ME, PID, "NONE")])["delivery"]
        assert d["state"] != ROW_DELIVERED
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_with_neither_known_nothing_is_kept_until_the_header_arrives(self, tmp_path,
                                                                          monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        rows = _reader(mid)
        sent = inbox_api._epoch(rows(0, [])["ts"])
        ack = RoutingReply(sent + 2, P5, PID, "NONE")
        assert rows(sent + 90, [ack])["delivery"]["state"] == ROW_LEFT_UNCONFIRMED
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}
        rows2 = _reader(mid, tx_from={PID: ME})
        assert rows2(sent + 95, [ack])["delivery"]["state"] == ROW_DELIVERED

    def test_the_witness_line_names_the_own_id_source(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        _, out = _reader(mid)(T0, [], full=True)
        assert "own radio id: unknown" in out["dm_witness"]["line"]
        own_radio.note_own_node(ME, source="test connection")
        _, out = _reader(mid)(T0, [], full=True)
        assert "own radio id: !6201ce25" in out["dm_witness"]["line"]


# ---------------------------------------------------------------------------
# The nits
# ---------------------------------------------------------------------------

class TestRefusedRowIsRemoved:
    """r4 #2: an r1-era "our radio" row was refused forever and blocked a new verdict."""

    def _plant(self, mid):
        conn = messaging_mod._init_db()
        conn.execute("INSERT INTO dm_kept (message_id, packet_id, state, detail, reason, said_by, "
                     "latency_s, sent_n, kept_at) VALUES (?, ?, 'failed', 'x', 'MAX_RETRANSMIT', "
                     "'our radio', NULL, 0, 1.0)", (mid, PID))
        conn.commit()
        conn.close()

    def test_refused_once_then_gone(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        self._plant(mid)
        assert messaging_mod.kept_dm([(mid, PID)]) == ({}, 1)
        assert messaging_mod.kept_dm([(mid, PID)]) == ({}, 0)

    def test_a_relay_nak_can_then_be_kept(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        self._plant(mid)
        messaging_mod.kept_dm([(mid, PID)])
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_FAILED, "detail": "failed: NO_ROUTE",
                                           "reason": "NO_ROUTE", "said_by": "!514eae28"})])
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["reason"] == "NO_ROUTE"

    def test_the_refused_count_is_said_in_delivery_error(self, tmp_path, monkeypatch):
        """r4 mutant 59."""
        mid = _store(tmp_path, monkeypatch)
        self._plant(mid)
        _, out = _reader(mid)(T0, [], full=True)
        assert "1 kept delivery row(s) refused" in (out.get("delivery_error") or "")


class TestRingLossIsSaid:
    """r4 #3: an answer lost from the ring is not "window closed"."""

    def test_overflow_after_the_send_is_said(self):
        w = Witness(since=T0 - 3600, lost_until=T0 + 30)
        v = row_verdict(Send(T0, PID, P5, sender=ME), [], w, now=T0 + 3600)
        assert v.state == ROW_NO_ACK and "overflowed" in v.why

    def test_older_than_the_ring_is_said(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [], LIVE,
                        now=T0 + dm_ack_witness.RING_WINDOW_S + 60)
        assert v.state == ROW_NO_ACK and "24 h" in v.why

    @needs_pb
    def test_the_witness_reports_the_lost_horizon(self):
        w = _live()
        a = _ack()
        for i in range(RING_CAP + 3):
            w.observe(TOPIC, a, now=T0 + i)
        s = w.snapshot(now=T0 + RING_CAP + 5)
        assert s["witness"].lost_until == T0 + 2
        assert s["counts"]["reply_evicted"] == 3


class TestDecodeFaultIsNotAKey:
    """r4 #4."""

    def test_no_protobuf_is_a_blind_witness(self):
        w = _live()
        with patch.object(dm_ack_witness, "_HAS_MQTT_PB", False):
            s = w.snapshot(now=T0)
        assert s["witness"].since is None and "protobuf" in s["witness"].why

    @needs_pb
    def test_a_raising_decoder_is_a_decode_error(self):
        w = _live()
        with patch("utils.meshtastic_se_crypto.decode_service_envelope",
                   side_effect=ImportError("no cryptography")):
            w.observe(TOPIC, _ack(), now=T0)
        c = w.snapshot(now=T0 + 1)["counts"]
        assert c["decode_error"] == 1 and c["decrypt_failed"] == 0

    def test_the_witness_line_says_decode_errors_not_key(self):
        snap = {"witness": LIVE, "replies": [], "tx": {}, "tx_from": {},
                "channels": {"LongFast": T0}, "decrypted": {},
                "counts": {"decode_error": 4}}
        line = inbox_api._witness_line(snap, T0 + 1, 0)["line"]
        assert "4 envelope(s) failed to decode" in line


class TestKeptDetailTail:
    """r4 #5 / mutant 27: the store keeps the bare verdict; the tail is live."""

    def test_the_kept_detail_has_no_tail_and_the_shown_one_is_live(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        sent = inbox_api._epoch(_reader(mid)(0, [])["ts"])
        nak = RoutingReply(sent + 2, RELAY, PID, "NO_CHANNEL")
        give = RoutingReply(sent + 17, ME, PID, "MAX_RETRANSMIT")
        d = _reader(mid, tx_from={PID: ME}, tx={PID: 2})(sent + 20, [nak, give])["delivery"]
        assert "gave up" in d["detail"] and "sent 2×" in d["detail"]
        k = messaging_mod.kept_dm([(mid, PID)])[0][mid]
        assert k["detail"] == "failed: NO_CHANNEL (said by !514eae28)"
        d = _reader(mid, tx_from={PID: ME}, tx={PID: 3})(sent + 30, [nak, give])["delivery"]
        assert "sent 3×" in d["detail"] and "sent 2×" not in d["detail"]

    def test_hints_are_exported(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 17, ME, PID, "TIMEOUT")],
                        LIVE, now=LATE)
        d = inbox_api._verdict_dict(v, kept=False)
        assert d["hints"] and all(isinstance(h, str) for h in d["hints"])


class TestTui:
    """r4 #6."""

    def test_a_non_object_body_is_unknown_never_a_crash(self):
        from launcher_tui.handlers.messaging import dm_delivery_lines
        lines = dm_delivery_lines(([1, 2], None))
        assert lines[0].startswith("UNKNOWN")

    def test_a_delivery_without_detail_is_said_malformed(self):
        from launcher_tui.handlers.messaging import dm_delivery_lines
        doc = {"state": "ok", "dm_witness": {"line": "x"}, "threads": [{
            "kind": "dm", "network": "meshtastic", "partner": "!b03da894", "messages": [{
                "direction": "out", "ts": "2026-11-26T00:00:00", "state": "handed_off",
                "content": "", "delivery": {"state": "delivered"}}]}]}
        assert any("malformed" in ln for ln in dm_delivery_lines((doc, None)))


class TestReplyUpperBound:
    """r4 #7 + #10: an ack long after the send is not this send's answer."""

    def test_a_reply_after_the_bound_is_not_delivery_and_is_said(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME),
                        [RoutingReply(T0 + REPLY_MAX_S + 1, P5, PID, "NONE")],
                        LIVE, now=T0 + REPLY_MAX_S + 5)
        assert v.state != ROW_DELIVERED
        assert any("long after the send" in h for h in v.hints)

    def test_a_late_but_bounded_ack_is_delivered_and_late(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME),
                        [RoutingReply(T0 + WAITING_S + 10, P5, PID, "NONE")],
                        LIVE, now=T0 + WAITING_S + 12)
        assert v.state == ROW_DELIVERED and v.late

    def test_the_bound_outlasts_the_waiting_window(self):
        assert REPLY_MAX_S > WAITING_S


class TestStaleRefs:
    """r4 #9."""

    def test_no_source_or_test_names_the_cut_cache(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for rel in ("src/utils/box_truth_rx.py", "tests/test_box_truth_dm_lines.py",
                    "src/utils/dm_delivery.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                assert "dm_journal_cache" not in f.read(), rel

    def test_the_module_docstring_names_the_row_rule(self):
        assert "row_verdict" in (dm_delivery.__doc__ or "")


class TestMutantKillersR4:
    """Behavioural survivors of r4's baseline-gated mutation run."""

    def test_m28_a_give_up_alone_is_not_left_unconfirmed(self):
        v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 + 17, ME, PID, "TIMEOUT")],
                        LIVE, now=LATE)
        assert v.state == ROW_NO_ACK

    def test_m45_a_kept_relay_failure_stands_against_another_relay(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        n1 = RoutingReply(sent + 2, RELAY, PID, "NO_ROUTE")
        rows(sent + 3, [n1])
        d = rows(sent + 9, [n1, RoutingReply(sent + 1.5, 0x11111111, PID, "NO_CHANNEL")])["delivery"]
        assert d["reason"] == "NO_ROUTE" and d["kept"] is True

    def test_m52_a_final_verdict_carries_no_coverage_tail(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        sent = inbox_api._epoch(_reader(mid)(0, [])["ts"])

        class W:
            def snapshot(self, now=None):
                return {"witness": Witness(T0 - 10 ** 9), "tx": {}, "tx_from": {PID: ME},
                        "replies": [RoutingReply(sent + 2, P5, PID, "NONE")],
                        "channels": {"Secret": sent}, "decrypted": {}, "counts": {}}
        with patch.object(inbox_api.time, "time", return_value=sent + 5), \
             patch("utils.message_listener.get_listener_status", return_value={}), \
             patch("utils.inbox_legs.load_sections", return_value=None), \
             patch.object(dm_ack_witness, "get_witness", return_value=W()):
            out = inbox_api.read_inbox()
        d = {m["id"]: m for t in out["threads"] for m in t["messages"]}[mid]["delivery"]
        assert d["state"] == ROW_DELIVERED and "unreadable" not in d["detail"]
        assert "unreadable" not in messaging_mod.kept_dm([(mid, PID)])[0][mid]["detail"]

    def test_m54_a_kept_failure_stands_when_the_send_time_is_unreadable(self, tmp_path,
                                                                        monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        rows = _reader(mid, tx_from={PID: ME})
        sent = inbox_api._epoch(rows(0, [])["ts"])
        rows(sent + 3, [RoutingReply(sent + 2, P5, PID, "NO_CHANNEL")])
        with patch.object(inbox_api, "_epoch", return_value=None):
            d = rows(sent + 9, [])["delivery"]
        assert d["state"] == ROW_FAILED and d["kept"] is True

    def test_m80_the_web_page_shows_delivery_error(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, "web", "inbox.html"), encoding="utf-8") as f:
            html = f.read()
        assert 'res.j.delivery_error' in html and '"DM delivery: "' in html


class TestMutantKillersR4Fold:
    """Survivors of the author's baseline-gated pass over this fold (37/43)."""

    def test_k4_a_destination_nak_alone_is_not_called_an_ack(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NO_CHANNEL")],
                        LIVE, now=LATE)
        assert not any("acked" in h for h in v.hints)

    def test_k7_a_self_dm_nak_from_our_radio_is_not_the_destinations(self):
        v = row_verdict(Send(T0, PID, ME, own=ME), [RoutingReply(T0 + 2, ME, PID, "NO_CHANNEL")],
                        LIVE, now=LATE)
        assert v.state == ROW_FAILED and v.said_by != "the destination"

    def test_k8_the_left_wording_names_the_unproven_origin(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=LATE)
        assert "cannot yet rule out a DM to its own radio" in v.base

    def test_k9_the_hint_names_the_missing_header(self):
        v = row_verdict(Send(T0, PID, P5), [RoutingReply(T0 + 2, P5, PID, "NONE")],
                        LIVE, now=LATE)
        assert any("DM to itself is not ruled out" in h for h in v.hints)

    def test_k17_a_self_dm_nak_is_not_counted_as_its_own_ack(self):
        v = row_verdict(Send(T0, PID, ME, own=ME), [RoutingReply(T0 + 2, ME, PID, "NO_CHANNEL")],
                        LIVE, now=LATE)
        assert not any("its own ack" in h for h in v.hints)

    def test_k29_the_inbox_own_id_alone_proves_a_remote_delivery(self, tmp_path, monkeypatch):
        mid = _store(tmp_path, monkeypatch)
        own_radio.note_own_node(ME, source="test connection")
        rows = _reader(mid)                      # no header seen
        sent = inbox_api._epoch(rows(0, [])["ts"])
        d = rows(sent + 5, [RoutingReply(sent + 2, P5, PID, "NONE")])["delivery"]
        assert d["state"] == ROW_DELIVERED
