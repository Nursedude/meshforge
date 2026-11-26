"""BIC-2 DM slice 1, piece 4: every /inbox DM send carries ONE delivery state.

End to end over a real temp store: ``read_inbox`` → ``_attach_delivery`` →
``dm_delivery.row_verdict``, with the two witnesses stubbed at their seams
(the map's in-memory ``/e/`` witness -- the one source). On 2026-11-25
every one of these rows read ``handed_off`` -- refused, failed and
delivered alike (design §4b).
"""
from unittest.mock import patch

import pytest

from commands import messaging as messaging_mod
from utils import dm_ack_witness, inbox_api
from utils.dm_delivery import (
    ROW_DELIVERED, ROW_FAILED, ROW_NO_ACK, ROW_STATES, ROW_WAITING, WAITING_S,
    RoutingReply, Witness,
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
    def __init__(self, since=0.0, why="", replies=(), tx=None, channels=None, tx_from=None):
        # r4 #1: our gateway uplinked the DM's own header (the realistic case)
        self.snap = {"witness": Witness(since, why), "replies": list(replies),
                     "tx": dict(tx or {}), "channels": dict(channels or {}),
                     "tx_from": {PID: ME} if tx_from is None else dict(tx_from),
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


def _read(now, witness):
    with patch.object(inbox_api.time, "time", return_value=now), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch("utils.inbox_legs.load_sections", return_value=None), \
         patch.object(dm_ack_witness, "get_witness", return_value=witness):
        out = inbox_api.read_inbox()
    msgs = {m["id"]: m for t in out["threads"] for m in t["messages"]}
    return out, msgs, None


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

class TestWitnessLine:

    def test_names_the_channels_seen_from_the_broker(self, tmp_db):
        _dm()
        sw = StubWitness(since=1.0, channels={"LongFast": 9990.0, "Regional": 10.0})
        sw.snap["decrypted"] = {"LongFast": 9990.0, "Regional": 10.0}
        out, _, _ = _read(10000, sw)
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


def test_a_kept_destination_nak_is_replaced_by_its_later_ack(tmp_db):
    """Operator 11-25 (Fable r3 #8): only delivered is final -- the verdict
    never depends on whether someone polled between the NAK and the ack."""
    mid = _dm()
    t = _sent(mid)
    nak = RoutingReply(t + 24, P5, PID, "PKI_UNKNOWN_PUBKEY")
    _read(t + 30, StubWitness(since=t - 60, replies=[nak]))
    _, msgs, _ = _read(t + 40, StubWitness(since=t - 60, replies=[nak, RoutingReply(t + 35, P5, PID, "NONE")]))
    assert msgs[mid]["delivery"]["state"] == ROW_DELIVERED


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


class TestReviewR1Inbox:
    """code-review r1 findings 1 + 10 at the inbox seam -- red on f9a23a48."""

    def test_f10_destination_nak_upgrades_a_kept_relay_nak(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _read(t + 5, StubWitness(since=t - 60, replies=[RoutingReply(t + 4, 0xD8, PID, "NO_ROUTE")]))
        _read(t + 30, StubWitness(since=t - 60, replies=[RoutingReply(t + 24, P5, PID, "PKI_FAILED")]))
        k = messaging_mod.kept_dm([(mid, PID)])[0][mid]
        assert k["said_by"] == "the destination" and k["reason"] == "PKI_FAILED"

    def test_f10_sent_n_survives_being_kept(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _read(t + 5, StubWitness(since=t - 60, tx={PID: 3},
                                 replies=[RoutingReply(t + 4, P5, PID, "NONE")]))
        _, msgs, _ = _read(t + 999, StubWitness(since=None, why="restarted"))
        assert msgs[mid]["delivery"]["sent_n"] == 3

    def test_f10_store_migrates_a_dm_kept_without_sent_n(self, tmp_db):
        import sqlite3
        messaging_mod._init_db().close()
        c = sqlite3.connect(tmp_db)
        c.execute("DROP TABLE dm_kept")
        c.execute("CREATE TABLE dm_kept (message_id INTEGER NOT NULL, packet_id INTEGER NOT NULL, "
                  "state TEXT NOT NULL, detail TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
                  "said_by TEXT NOT NULL DEFAULT '', latency_s REAL, kept_at REAL NOT NULL, "
                  "PRIMARY KEY (message_id, packet_id))")
        c.commit()
        c.close()
        mid = _dm()
        messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "d",
                                           "latency_s": 1.0, "sent_n": 2})])
        assert messaging_mod.kept_dm([(mid, PID)])[0][mid]["sent_n"] == 2


class TestReviewR2:
    """Fable r2 findings 4, 8, 10 + the mutation survivors that matter --
    red on d01fafa2 unless noted."""

    def test_r2_4_line_names_only_channels_that_decrypted(self, tmp_db):
        _dm()
        w = StubWitness(since=1.0, channels={"LongFast": 9990.0, "PKI": 9990.0,
                                             "Private": 9990.0})
        w.snap["decrypted"] = {"LongFast": 9990.0}
        out, _, _ = _read(10000, w)
        line = out["dm_witness"]["line"]
        assert out["dm_witness"]["channels"] == ["LongFast"]
        assert "Private" in line and "unreadable" in line
        assert out["dm_witness"]["unreadable"] == ["Private"]   # PKI = DM bodies, not acks

    def test_r2_4_row_why_names_an_unreadable_channel(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        w = StubWitness(since=t - 60, channels={"Private": t})
        w.snap["decrypted"] = {}
        _, msgs, _ = _read(t + WAITING_S + 5, w)
        assert "Private" in msgs[mid]["delivery"]["detail"]

    def test_r2_8_unreadable_kept_store_is_said_on_the_row(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        with patch.object(messaging_mod, "kept_dm", side_effect=OSError("disk")):
            _, msgs, _ = _read(t + 999, StubWitness(since=None, why="restarted"))
        assert "kept verdicts unreadable" in msgs[mid]["delivery"]["detail"]

    def test_mut_rns_dm_threads_carry_no_delivery(self, tmp_db):
        r = messaging_mod.store_outgoing("Wx", "rns", "a" * 32, channel=0,
                                         delivered=True, packet_id=PID + 3)
        mid = r.data["message_id"]
        _, msgs, _ = _read(0, StubWitness())
        assert "delivery" not in msgs[mid]

    def test_mut_kept_late_flag_survives_a_restart(self, tmp_db):
        mid = _dm()
        t = _sent(mid)
        _read(t + WAITING_S + 30, StubWitness(since=t - 60,
              replies=[RoutingReply(t + WAITING_S + 20, P5, PID, "NONE")]))
        out, msgs, _ = _read(t + 9999, StubWitness(since=None, why="restarted"))
        assert msgs[mid]["delivery"]["late"] is True and out["dm_witness"]["late_acks"] == 1


class TestStoreRanking:
    """keep_dm's SQL rank, pinned directly (Fable r2: an unconditional
    overwrite mutant survived every test)."""

    def _keep(self, mid, state, said_by="", n=0, latency=None):
        messaging_mod.keep_dm([(mid, PID, {"state": state, "detail": f"{state} {said_by}",
                                           "said_by": said_by, "reason": "R" if state == ROW_FAILED else "",
                                           "latency_s": latency, "sent_n": n})])

    def _kept(self, mid):
        return messaging_mod.kept_dm([(mid, PID)])[0][mid]

    def test_delivered_is_never_downgraded(self, tmp_db):
        mid = _dm()
        self._keep(mid, ROW_DELIVERED, latency=1.0)
        self._keep(mid, ROW_FAILED, "the destination")
        self._keep(mid, ROW_FAILED, "!000000d8")
        assert self._kept(mid)["state"] == ROW_DELIVERED

    def test_destination_failure_outranks_and_is_never_replaced_by_a_relays(self, tmp_db):
        mid = _dm()
        self._keep(mid, ROW_FAILED, "!000000d8")
        self._keep(mid, ROW_FAILED, "the destination")
        self._keep(mid, ROW_FAILED, "!000000d9")
        assert self._kept(mid)["said_by"] == "the destination"

    def test_relay_failure_is_replaced_by_delivered(self, tmp_db):
        mid = _dm()
        self._keep(mid, ROW_FAILED, "!000000d8")
        self._keep(mid, ROW_DELIVERED, latency=2.0)
        assert self._kept(mid)["state"] == ROW_DELIVERED

    def test_sent_n_never_shrinks_on_an_upgrade(self, tmp_db):
        mid = _dm()
        self._keep(mid, ROW_FAILED, "!000000d8", n=3)
        self._keep(mid, ROW_DELIVERED, n=1, latency=2.0)
        assert self._kept(mid)["sent_n"] == 3

    def test_non_finite_latency_is_refused(self, tmp_db):
        mid = _dm()
        with pytest.raises(ValueError):
            self._keep(mid, ROW_DELIVERED, latency=float("inf"))


def test_page_renders_delivery_detail_unconditionally_when_present():
    """No JS runtime here: pin the render condition itself (an `if (false)`
    mutant survived the vocabulary tests)."""
    import pathlib
    page = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()
    assert "if (m.delivery && m.delivery.detail) {" in page
    i = page.index("if (m.delivery && m.delivery.detail) {")
    assert 'el("span", "dl-" + dk, m.delivery.detail)' in page[i:i + 400]
