"""ROADMAP 6f (F2+F5, operator grilling 2026-10-17): M→R queue outage behaviour.

A sender may DEFER a row (retry_count untouched) on circuit-open / no-path;
every other error still counts. M→R rows (deferred or gated) expire after a
max age, witnessed; a wall clock that jumps never mass-expires them.
Measured motivation: one destination offline 57 min–27 h with its circuit
open, while the queue's 3-try budget dead-lettered every row inside 60 s.
"""

import json
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.test_m2r_requeue_f4 import (  # noqa: F401 — fixtures + helpers
    DEST, _broadcast, _connect, _dm, _fake_rns, _isolated_delivery_counters,
    _make_due, _rows, bridge, queue,
)


def _q(tmp_path):
    from gateway.message_queue import PersistentMessageQueue
    return PersistentMessageQueue(db_path=str(tmp_path / "q.db"))


def _row(q, msg_id):
    with q._get_connection() as conn:
        return dict(conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone())


def _age(q, msg_id, delta):
    stamp = (datetime.now() - delta).isoformat()
    with q._get_connection() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                     (stamp, msg_id))


class TestDeferPrimitive:

    def test_a_deferred_row_keeps_its_retry_budget(self, tmp_path):
        from gateway.message_queue import SendDeferred
        q = _q(tmp_path)

        def defer(p):
            raise SendDeferred(30, "circuit open")
        q.register_sender("a", defer)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        for _ in range(6):
            _make_due(q)
            q.process_once()
        row = _row(q, mid)
        assert row["status"] == "pending"
        assert row["retry_count"] == 0
        assert json.loads(row["payload"])["_deferrals"] == 6
        due = datetime.fromisoformat(row["retry_after"])
        assert timedelta(seconds=25) < due - datetime.now() <= timedelta(seconds=30)
        assert q.get_stats()["deferred_total"] == 6

    def test_a_plain_failure_still_counts(self, tmp_path):
        q = _q(tmp_path)
        q.register_sender("a", lambda p: False)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        q.process_once()
        assert _row(q, mid)["retry_count"] == 1

    def test_sender_sees_its_row_age_and_deferral_count(self, tmp_path):
        q = _q(tmp_path)
        seen = []
        q.register_sender("a", lambda p: seen.append(p) or True)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=120))
        q.process_once()
        created = datetime.fromisoformat(seen[0]["_queue_created_at"])
        assert 115 < (datetime.now() - created).total_seconds() < 130


class TestMaxAge:

    def test_an_overdue_row_expires_witnessed(self, tmp_path):
        from gateway import delivery_counters as _dc
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=200))
        assert q.expire_overdue() == 1
        assert _row(q, mid)["status"] == "dead_letter"
        assert q.get_stats()["expired_total"] == 1
        recent = _dc.get_singleton().snapshot()["recent"]
        assert any(e.get("id") == mid and e.get("drop_reason") == "expired"
                   for e in recent)

    def test_a_gated_destination_still_ages_out(self, tmp_path):
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, ready_fn=lambda: False,
                          max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=200))
        q.process_once()
        assert _row(q, mid)["status"] == "dead_letter"

    def test_a_young_row_and_an_unbounded_destination_never_expire(
            self, tmp_path):
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        q.register_sender("b", lambda p: True)
        young = q.enqueue(payload={"message": "x", "destination": "1"},
                          destination="a")
        old_b = q.enqueue(payload={"message": "y", "destination": "2"},
                          destination="b")
        _age(q, old_b, timedelta(days=3))
        assert q.expire_overdue() == 0
        assert _row(q, young)["status"] == "pending"
        assert _row(q, old_b)["status"] == "pending"

    @pytest.mark.parametrize("delta", [timedelta(hours=-1), timedelta(days=8)])
    def test_an_impossible_age_is_restamped_never_expired(self, tmp_path, delta):
        # Clock guard: a backward step or an NTP jump of days must not
        # mass-expire real messages; the row gets ONE fresh window.
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, delta)
        assert q.expire_overdue() == 0
        row = _row(q, mid)
        assert row["status"] == "pending"
        restamped = datetime.fromisoformat(row["created_at"])
        assert abs((datetime.now() - restamped).total_seconds()) < 5
        assert q.get_stats()["age_unknown"] == 1
        q.expire_overdue()
        assert q.get_stats()["age_unknown"] == 1  # once, not every pass

    def test_oldest_pending_age_is_reported(self, tmp_path):
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, ready_fn=lambda: False)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=300))
        assert 290 < q.get_stats()["oldest_pending_age_s"] < 320


class TestRnsSender:

    def test_circuit_open_defers_and_delivers_after_recovery(
            self, bridge, queue):  # noqa: F811
        # The reviewer's probe: circuit open after reconnect dead-lettered
        # 8/8 rows inside 60 s. Now they wait and go when it closes.
        _connect(bridge)
        for i in range(8):
            bridge._requeue_failed_message(
                _dm(content=f"m{i}"), "rns", rns_destinations=[DEST])
        fake_rns, fake_lxmf = _fake_rns()
        from utils.tx_guard import allow_rns_egress
        with patch.object(bridge, "can_send_to", return_value=False):
            for _ in range(6):
                _make_due(queue)
                queue.process_once()
        assert queue.get_dead_letters() == []
        with patch.object(bridge, "can_send_to", return_value=True), \
                patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
                allow_rns_egress():
            _make_due(queue)
            queue.process_once()
        assert {r["status"] for r in _rows(queue, "rns")} == {"delivered"}

    def test_no_path_requests_one_and_defers_without_busy_waiting(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        bridge._requeue_failed_message(_dm(), "rns", rns_destinations=[DEST])
        fake_rns, fake_lxmf = _fake_rns()
        fake_rns.Transport.has_path.return_value = False
        from utils.tx_guard import allow_rns_egress
        delays = []
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
                allow_rns_egress(), \
                patch.object(bridge, "can_send_to", return_value=True):
            for _ in range(3):
                _make_due(queue)
                queue.process_once()
                with queue._get_connection() as conn:
                    after = conn.execute(
                        "SELECT retry_after FROM messages").fetchone()[0]
                delays.append(round((datetime.fromisoformat(after)
                                     - datetime.now()).total_seconds()))
        assert fake_rns.Transport.request_path.call_count == 3
        assert fake_rns.Transport.has_path.call_count <= 6  # no 30-poll loop
        assert delays == [15, 30, 60]
        assert not fake_lxmf.LXMessage.called
        with queue._get_connection() as conn:
            assert conn.execute(
                "SELECT retry_count FROM messages").fetchone()[0] == 0

    def test_backoff_caps_at_five_minutes(self, bridge):  # noqa: F811
        assert bridge._m2r_defer_delay({"_deferrals": 0}) == 15
        assert bridge._m2r_defer_delay({"_deferrals": 3}) == 120
        assert bridge._m2r_defer_delay({"_deferrals": 9}) == 300

    def test_rns_senders_carry_the_max_age(self, bridge):  # noqa: F811
        from gateway.message_queue import M2R_MAX_AGE_S
        q = MagicMock()
        bridge._register_rns_queue_senders(q)
        ages = {c.args[0]: c.kwargs.get("max_age_s")
                for c in q.register_sender.call_args_list}
        assert ages == {"rns": M2R_MAX_AGE_S, "rns_xform": M2R_MAX_AGE_S}
        assert M2R_MAX_AGE_S == 86400

    def test_late_delivery_carries_queued_seconds(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        bridge._requeue_failed_message(_dm(), "rns", rns_destinations=[DEST],
                                       title="t", fields={"a": 1})
        with queue._get_connection() as conn:
            conn.execute("UPDATE messages SET created_at = ?", (
                (datetime.now() - timedelta(seconds=600)).isoformat(),))
        fake_rns, fake_lxmf = _fake_rns()
        from utils.tx_guard import allow_rns_egress
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
                allow_rns_egress(), \
                patch.object(bridge, "can_send_to", return_value=True):
            queue.process_once()
        fields = fake_lxmf.LXMessage.call_args.kwargs["fields"]
        assert 590 < fields["meshforge_queued_s"] < 620
        assert fake_lxmf.LXMessage.call_args.args[2] == "private hello"

    def test_replayed_message_carries_queued_seconds(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._connected_rns = False
        bridge._requeue_failed_message(_broadcast(), "rns")
        with queue._get_connection() as conn:
            conn.execute("UPDATE messages SET created_at = ?", (
                (datetime.now() - timedelta(seconds=600)).isoformat(),))
        _connect(bridge)
        seen = {}
        with patch.object(bridge, "send_to_rns",
                          side_effect=lambda c, d, **k: seen.update(k) or True):
            queue.process_once()
        assert 590 < seen["fields"]["meshforge_queued_s"] < 620


def test_the_real_registry_exposes_its_recovery_window(bridge):  # noqa: F811
    # The bridge reads the window, never assumes it: a fake that lacks the
    # property would silently fall back to 60 s.
    from gateway.circuit_breaker import CircuitBreakerRegistry
    bridge._circuit_breaker = CircuitBreakerRegistry(
        failure_threshold=5, recovery_timeout=42.0)
    assert bridge._circuit_recovery_s() == 42.0


@pytest.mark.parametrize("delta,expected", [
    (timedelta(seconds=90), 90),
    (timedelta(hours=-1), None),     # clock stepped back: not a wait
    (timedelta(days=8), None),       # NTP jump: not a wait
])
def test_queued_seconds_never_reports_a_clock_jump(bridge, delta, expected):  # noqa: F811
    stamp = (datetime.now() - delta).isoformat()
    got = bridge._queued_seconds({"_queue_created_at": stamp})
    assert got == expected if expected is None else abs(got - expected) <= 2


# --- review of 3c10f362 (F2/F3/F5) + operator Q15–Q18 (2026-10-17) --------

class TestReviewFixes:

    def test_expire_of_a_row_already_delivered_leaves_no_witness(self, tmp_path):
        # F2: a second process_once caller delivered the row between the
        # SELECT and the UPDATE — it must not ALSO read as expired.
        from gateway import delivery_counters as _dc
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        q.mark_delivered(mid)
        assert q._expire(mid, "a", 200, 100) is False
        assert q.get_stats()["expired_total"] == 0
        assert not any(e.get("id") == mid and e.get("drop_reason") == "expired"
                       for e in _dc.get_singleton().snapshot()["recent"])

    def test_defer_of_a_finished_row_is_refused(self, tmp_path):
        q = _q(tmp_path)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        q.mark_delivered(mid)
        assert q.mark_deferred(mid, 30, "late") is False
        assert _row(q, mid)["status"] == "delivered"
        assert q.get_stats()["deferred_total"] == 0

    def test_a_deferred_row_still_expires(self, tmp_path):
        # M2: a 24 h-old row is almost always mid-deferral (retry_after in
        # the future) — expiry must not skip it.
        from gateway.message_queue import SendDeferred
        q = _q(tmp_path)

        def defer(p):
            raise SendDeferred(300, "no path")
        q.register_sender("a", defer, max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        q.process_once()  # deferred 300 s
        _age(q, mid, timedelta(seconds=200))
        assert q.expire_overdue() == 1
        assert _row(q, mid)["status"] == "dead_letter"

    def test_expiry_re_arms_after_its_interval(self, tmp_path):
        # M3: the throttle must re-arm, not run once per process lifetime.
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, ready_fn=lambda: False,
                          max_age_s=100)
        q.process_once()
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=200))
        q.process_once()  # inside the interval: not yet
        assert _row(q, mid)["status"] == "pending"
        q._last_expire_mono -= q._EXPIRE_EVERY_S + 1
        q.process_once()
        assert _row(q, mid)["status"] == "dead_letter"

    def test_an_overdue_row_is_expired_not_dispatched(self, tmp_path):
        # M4: expiry runs BEFORE dispatch in the same pass.
        q = _q(tmp_path)
        sent = []
        q.register_sender("a", lambda p: sent.append(p) or True, max_age_s=100)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=200))
        q.process_once()
        assert sent == [] and _row(q, mid)["status"] == "dead_letter"

    def test_oldest_pending_age_ignores_finished_rows(self, tmp_path):
        # M5
        q = _q(tmp_path)
        old = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, old, timedelta(days=2))
        q.mark_delivered(old)
        new = q.enqueue(payload={"message": "y", "destination": "2"},
                        destination="a")
        _age(q, new, timedelta(seconds=60))
        assert 50 < q.get_stats()["oldest_pending_age_s"] < 90

    def test_reregistering_without_max_age_clears_it(self, tmp_path):
        # M6
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        q.register_sender("a", lambda p: True)
        mid = q.enqueue(payload={"message": "x", "destination": "1"},
                        destination="a")
        _age(q, mid, timedelta(seconds=200))
        assert q.expire_overdue() == 0

    def test_defer_delay_is_clamped(self):
        # M7
        from gateway.message_queue import SendDeferred
        assert SendDeferred(0).delay_s == 1.0
        assert SendDeferred(-5).delay_s == 1.0

    def test_one_expiry_pass_is_bounded(self, tmp_path):
        # F5: 1000 overdue rows stalled the worker ~5 s; a pass is capped.
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=100)
        n = q._EXPIRE_BATCH + 5
        ids = [q.enqueue(payload={"message": f"x{i}", "destination": "1"},
                         destination="a") for i in range(n)]
        old = (datetime.now() - timedelta(seconds=200)).isoformat()
        with q._get_connection() as conn:
            conn.execute("UPDATE messages SET created_at = ?", (old,))
        assert q.expire_overdue() == q._EXPIRE_BATCH
        assert q.expire_overdue() == 5
        assert len(ids) == n


class TestClockJump:
    # Q15: detect the jump (Δwall vs Δmonotonic per tick) and shift pending
    # rows by it, so age is time actually waited. The reviewer's probe: a
    # +25 h NTP step expired 8 two-hour-old rows.

    def test_a_forward_step_shifts_rows_instead_of_expiring_them(self, tmp_path):
        q = _q(tmp_path)
        q.register_sender("a", lambda p: True, max_age_s=86400)
        ids = [q.enqueue(payload={"message": f"x{i}", "destination": "1"},
                         destination="a") for i in range(8)]
        for mid in ids:
            _age(q, mid, timedelta(hours=2))
        t0 = datetime.now()
        q.note_clock(t0, 1000.0)
        # 2 s of real time pass, but the wall clock steps +25 h
        q.note_clock(t0 + timedelta(hours=25, seconds=2), 1002.0)
        assert q.get_stats()["clock_jumps"] == 1
        with patch("gateway.message_queue_outage_mixin.datetime") as dt:
            dt.now.return_value = t0 + timedelta(hours=25, seconds=2)
            dt.fromisoformat = datetime.fromisoformat
            assert q.expire_overdue() == 0
        assert {_row(q, m)["status"] for m in ids} == {"pending"}

    def test_steady_time_is_not_a_jump(self, tmp_path):
        q = _q(tmp_path)
        t0 = datetime.now()
        q.note_clock(t0, 1000.0)
        q.note_clock(t0 + timedelta(seconds=30), 1030.0)
        assert q.get_stats()["clock_jumps"] == 0

    def test_process_once_feeds_the_clock(self, tmp_path):
        q = _q(tmp_path)
        q.process_once()
        assert q._clock_wall is not None and q._clock_mono is not None


class TestRequeueShape:

    def test_requeued_m2r_rows_are_sheddable(self, bridge, queue):  # noqa: F811
        # Q16: HIGH rows are never shed — one dead peer could fill the queue
        # and reject new messages to every destination.
        bridge._connected_rns = False
        bridge._requeue_failed_message(_broadcast(), "rns")
        bridge._requeue_failed_message(_dm(), "rns", rns_destinations=[DEST])
        with queue._get_connection() as conn:
            prios = {r[0] for r in conn.execute("SELECT priority FROM messages")}
        assert prios == {2}  # MessagePriority.NORMAL

    def test_replay_hop_keeps_the_first_enqueue_time(
            self, bridge, queue):  # noqa: F811
        # Q17: a gated rns_xform row that replays into a failed send must not
        # restart its 24 h — the per-hash row keeps the original created_at.
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._connected_rns = False
        # a no-destination fan-out (a failed broadcast is dropped, by design)
        bridge._requeue_failed_message(_dm(dest_id=None), "rns")
        first = (datetime.now() - timedelta(hours=3)).replace(microsecond=0)
        with queue._get_connection() as conn:
            conn.execute("UPDATE messages SET created_at = ?",
                         (first.isoformat(),))
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            queue.process_once()
        with queue._get_connection() as conn:
            created = conn.execute(
                "SELECT created_at FROM messages WHERE destination = 'rns'"
            ).fetchone()[0]
        assert datetime.fromisoformat(created) == first
