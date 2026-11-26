"""BIC-2 DM slice 1, piece 4: every /inbox DM send carries ONE delivery state.

End to end over a real temp store: ``read_inbox`` → ``_attach_delivery`` →
``dm_delivery.row_verdict``, with the two witnesses stubbed at their seams
(the map's in-memory ``/e/`` witness, this box's journal). On 2026-11-25
every one of these rows read ``handed_off`` -- refused, failed and
delivered alike (design §4b).
"""
from unittest.mock import patch

import pytest

from commands import messaging as messaging_mod
from utils import dm_ack_witness, inbox_api
from utils.dm_delivery import (
    ROW_DELIVERED, ROW_FAILED, ROW_NO_ACK, ROW_STATES, ROW_WAITING, WAITING_S,
    JournalLine, RoutingReply, Witness,
)

P5 = 0xB03DA894
DEST = "!b03da894"
ME = 0x6201CE25
PID = 0xAB3C8AC3


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    return db_path


class StubWitness:
    def __init__(self, since=0.0, why="", replies=(), tx=None, channels=None):
        self.snap = {"witness": Witness(since, why), "replies": list(replies),
                     "tx": dict(tx or {}), "channels": dict(channels or {}),
                     "counts": {"envelopes": 3}}

    def snapshot(self, now=None):
        return self.snap


def _dm(packet_id=PID, dest=DEST):
    r = messaging_mod.store_outgoing("Wx", "meshtastic", dest, channel=0,
                                     delivered=True, packet_id=packet_id)
    assert r.success, r.message
    return r.data["message_id"]


def _broadcast(packet_id=PID + 1):
    r = messaging_mod.store_outgoing("Wx", "meshtastic", None, channel=0,
                                     delivered=True, packet_id=packet_id)
    return r.data["message_id"]


def _read(now, witness, journal=([], None)):
    with patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch("utils.inbox_legs.load_sections", return_value=None), \
         patch.object(dm_ack_witness, "get_witness", return_value=witness), \
         patch("utils.box_truth_rx.read_dm_journal", return_value=journal) as jr:
        out = inbox_api.read_inbox()
    msgs = {m["id"]: m for t in out["threads"] for m in t["messages"]}
    return out, msgs, jr


def _sent(mid):
    _, msgs, _ = _read(0, StubWitness())
    return inbox_api._epoch(msgs[mid]["ts"])


class TestRowStates:

    def test_destination_ack_reads_delivered_and_is_kept(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        acks = [RoutingReply(t + 0.02, ME, PID, "NONE"), RoutingReply(t + 1.9, P5, PID, "NONE")]
        _, msgs, _ = _read(t + 3, StubWitness(since=t - 60, replies=acks))
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_DELIVERED and d["kept"] is False
        # a restart: the in-memory witness is new and blind -- the store answers
        _, msgs, _ = _read(t + 999, StubWitness(since=None, why="not started"))
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_DELIVERED and d["kept"] is True

    def test_own_radio_refusal_reads_failed_said_by_our_radio(self, tmp_db):
        mid = _dm(dest="!5eed1e56")
        t = _sent(mid)
        j = [JournalLine(t + 0.1, "alloc_err", packet_id=PID, err=39)]
        _, msgs, _ = _read(t + 5, StubWitness(since=t - 60), journal=(j, None))
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_FAILED and d["said_by"] == "our radio"
        assert "PKI_SEND_FAIL_PUBLIC_KEY" in d["detail"]

    def test_inside_the_window_reads_waiting_and_is_not_kept(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _, msgs, _ = _read(t + 10, StubWitness(since=t - 60, tx={PID: 1}))
        assert msgs[mid]["delivery"]["state"] == ROW_WAITING
        assert messaging_mod.kept_dm([(mid, PID)])[0] == {}

    def test_window_closed_reads_no_ack_with_sent_n(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _, msgs, _ = _read(t + WAITING_S + 5, StubWitness(since=t - 60, tx={PID: 3}))
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_NO_ACK and "window closed" in d["why"] and d["sent_n"] == 3

    def test_blind_witness_says_why(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _, msgs, _ = _read(t + 5, StubWitness(since=None, why="the MQTT ack witness has not started"))
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_NO_ACK and "has not started" in d["why"]

    def test_unreadable_journal_is_said_never_a_503(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        out, msgs, _ = _read(t + 5, StubWitness(since=t - 60),
                             journal=(None, "journal unreadable: timed out"))
        assert out["state"] == "ok"
        d = msgs[mid]["delivery"]
        assert d["state"] == ROW_WAITING and "journal unreadable" in d["detail"]

    def test_a_relay_nak_then_destination_ack_ends_delivered(self, tmp_db):
        """The destination's truth outranks a kept relay NAK."""
        mid = _dm()
        t = _sent(mid)
        _read(t + 5, StubWitness(since=t - 60, replies=[RoutingReply(t + 4, 0xD8, PID, "NO_ROUTE")]))
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["state"] == ROW_FAILED
        _read(t + 9, StubWitness(since=t - 60, replies=[RoutingReply(t + 8, P5, PID, "NONE")]))
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["state"] == ROW_DELIVERED

    def test_kept_delivered_is_never_downgraded(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _read(t + 5, StubWitness(since=t - 60, replies=[RoutingReply(t + 2, P5, PID, "NONE")]))
        _read(t + 9, StubWitness(since=t - 60, replies=[RoutingReply(t + 8, 0xD8, PID, "NO_ROUTE")]))
        _, msgs, _ = _read(t + 10, StubWitness(since=None, why="x"))
        assert msgs[mid]["delivery"]["state"] == ROW_DELIVERED

    def test_broadcast_and_received_rows_carry_no_delivery(self, tmp_db):
        bid = _broadcast()
        r = messaging_mod.store_incoming(DEST, "hi", "meshtastic", to_id="!6201ce25", packet_id=0x51)
        assert r is None or getattr(r, "success", True)
        _, msgs, _ = _read(0, StubWitness())
        assert "delivery" not in msgs[bid]
        assert all("delivery" not in m for m in msgs.values() if m["direction"] == "in")

    def test_every_delivery_state_is_in_the_closed_vocabulary(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        for now in (t + 1, t + WAITING_S + 1):
            _, msgs, _ = _read(now, StubWitness(since=t - 60))
            assert msgs[mid]["delivery"]["state"] in ROW_STATES

    def test_journal_read_only_when_something_is_unsettled(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _read(t + 3, StubWitness(since=t - 60, replies=[RoutingReply(t + 2, P5, PID, "NONE")]))
        _, _, jr = _read(t + 9, StubWitness(since=t - 60))
        assert not jr.called          # settled + kept: no journal walk

    def test_journal_read_starts_at_the_oldest_unsettled_send(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _, _, jr = _read(t + 5, StubWitness(since=t - 60))
        assert jr.call_args.kwargs["since"] <= t


class TestWitnessLine:

    def test_names_the_channels_seen_from_the_broker(self, tmp_db):
        _dm()
        out, _, _ = _read(10000, StubWitness(since=1.0, channels={"LongFast": 9990.0, "Regional": 10.0}))
        w = out["dm_witness"]
        assert w["acks_visible"] is True
        assert w["channels"] == ["LongFast"]          # seen in the recent window only
        assert "LongFast" in w["line"]

    def test_blind_witness_line_says_why(self, tmp_db):
        _dm()
        out, _, _ = _read(1000, StubWitness(since=None, why="MQTT disconnected (rc=7)"))
        w = out["dm_witness"]
        assert w["acks_visible"] is False and "MQTT disconnected" in w["line"]

    def test_late_acks_are_counted_from_the_store(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        late = [RoutingReply(t + WAITING_S + 20, P5, PID, "NONE")]
        out, msgs, _ = _read(t + WAITING_S + 30, StubWitness(since=t - 60, replies=late))
        assert msgs[mid]["delivery"]["late"] is True
        assert out["dm_witness"]["late_acks"] == 1


class TestStore:

    def test_store_refuses_a_non_final_state_loudly(self, tmp_db):
        mid = _dm()
        with pytest.raises(ValueError):
            messaging_mod.keep_dm([(mid, PID, {"state": ROW_WAITING, "detail": "x"})])

    def test_kept_verdict_never_lends_itself_to_another_packet_id(self, tmp_db):
        mid = _dm()
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "d", "latency_s": 1.0})])
        assert messaging_mod.kept_dm([(mid, PID + 1)])[0] == {}

    def test_unreadable_kept_store_still_judges_live_and_says_so(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        with patch.object(messaging_mod, "kept_dm", side_effect=OSError("disk")):
            out, msgs, _ = _read(t + 3, StubWitness(since=t - 60,
                                 replies=[RoutingReply(t + 2, P5, PID, "NONE")]))
        assert msgs[mid]["delivery"]["state"] == ROW_DELIVERED
        assert "disk" in out["delivery_error"]

    def test_prune_drops_kept_rows_with_their_send(self, tmp_db):
        mid = _dm()
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "d", "latency_s": 1.0})])
        conn = messaging_mod._init_db()
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        messaging_mod._drop_orphan_heard_rows(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM dm_kept").fetchone()[0] == 0
        conn.close()


def test_a_destination_nak_is_terminal_never_rejudged(tmp_db):
    mid = _dm()
    t = _sent(mid)
    _read(t + 30, StubWitness(since=t - 60, replies=[RoutingReply(t + 24, P5, PID, "PKI_UNKNOWN_PUBKEY")]))
    _, msgs, jr = _read(t + 40, StubWitness(since=t - 60, replies=[RoutingReply(t + 35, P5, PID, "NONE")]))
    d = msgs[mid]["delivery"]
    assert d["state"] == ROW_FAILED and d["kept"] is True and not jr.called


def test_a_kept_relay_nak_is_shown_while_the_destination_is_silent(tmp_db):
    mid = _dm()
    t = _sent(mid)
    _read(t + 5, StubWitness(since=t - 60, replies=[RoutingReply(t + 4, 0xD8, PID, "NO_ROUTE")]))
    _, msgs, _ = _read(t + 999, StubWitness(since=None, why="restarted"))
    d = msgs[mid]["delivery"]
    assert d["state"] == ROW_FAILED and d["said_by"] == "!000000d8" and d["kept"] is True


class TestPage:
    import pathlib as _pl
    PAGE = (_pl.Path(__file__).parent.parent / "web" / "inbox.html").read_text()

    def test_page_vocabulary_is_the_judges_closed_enum(self):
        import re
        m = re.search(r"var DELIVERY = \{([^}]*)\}", self.PAGE)
        assert m, "page lost its DELIVERY vocabulary"
        keys = set(re.findall(r"(\w+):\s*1", m.group(1)))
        assert keys == set(ROW_STATES)

    def test_page_shows_the_rows_detail_and_the_witness_line(self):
        assert "m.delivery.detail" in self.PAGE
        assert "res.j.dm_witness.line" in self.PAGE
        assert "res.j.delivery_error" in self.PAGE

    def test_every_state_has_a_style(self):
        for st in ROW_STATES:
            assert f".dl-{st}{{" in self.PAGE, st
