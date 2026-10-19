"""Every Mesh→RNS message ends in exactly one of delivered / dropped
(operator acceptance sentence, 2026-10-18).

Before: the directed and exception paths counted ``mesh_to_rns_dropped`` the
moment a send failed — even when the message was requeued and later went out
— while the session path counted only an un-queueable failure. Nothing
counted a requeued row's real outcome (a retry that landed was never
``delivered``; one that dead-lettered was never ``dropped``), a replay of an
already-attempted message bumped ``attempted`` a second time, and an RNS-down
message whose requeue failed vanished uncounted. A successful session retry
also never refreshed the session or the reply memory.

Driven through a REAL ``PersistentMessageQueue`` with the production sender
wiring: the queue reports each row's terminal outcome (``on_terminal``) and
the bridge resolves the ORIGINAL message once.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export
from tests.test_m2r_requeue_f4 import (  # noqa: F401
    DEST, DEST_B, _isolated_delivery_counters, queue, _fake_rns, _connect,
    _make_due, _dm, _broadcast)


def _triplet(bridge):  # noqa: F811
    s = bridge.stats
    return (s.get('mesh_to_rns_attempted', 0),
            s.get('mesh_to_rns_delivered', 0),
            s.get('mesh_to_rns_dropped', 0))


def _drain(q, fake_lxmf_raises=False, rounds=6):
    """Run the queue until nothing is pending (each round makes rows due)."""
    from utils.tx_guard import allow_rns_egress
    fake_rns, fake_lxmf = _fake_rns()
    if fake_lxmf_raises:
        fake_lxmf.LXMessage.side_effect = RuntimeError("lxmf down")
    with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
            allow_rns_egress():
        for _ in range(rounds):
            _make_due(q)
            q.process_once()


def _directed(bridge, dests):  # noqa: F811
    if len(dests) == 1:
        node = MagicMock(rns_hash=dests[0])
        node.name, node.short_name = "Alice", "ALC"
        bridge.node_tracker.get_node_by_mesh_id.return_value = node
        return _dm()
    bridge.config.rns.get_lxmf_destinations.return_value = [
        d.hex() for d in dests]
    bridge.node_tracker.get_node_by_mesh_id.return_value = None
    return _dm(dest_id=None, content="to all")


class TestDirectedFailure:

    def test_requeued_then_delivered_counts_delivered_once(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST]))
        assert _triplet(bridge) == (1, 0, 0), "a requeued message is pending"
        _drain(queue)
        assert _triplet(bridge) == (1, 1, 0)

    def test_requeued_then_dead_lettered_counts_dropped_once(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST]))
        _drain(queue, fake_lxmf_raises=True)
        assert queue.get_dead_letters(), "precondition: the row dead-lettered"
        assert _triplet(bridge) == (1, 0, 1)

    def test_fanout_one_leg_lands_counts_delivered_never_dropped(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST, DEST_B]))
        from utils.tx_guard import allow_rns_egress
        fake_rns, fake_lxmf = _fake_rns()
        # Leg A lands; leg B's LXMF build raises until it dead-letters.
        fake_lxmf.LXMessage.side_effect = (
            lambda dest, src, msg, title, fields=None:
            (_ for _ in ()).throw(RuntimeError("leg B down"))
            if fake_rns.Destination.call_args.args[0] is fake_rns._b
            else MagicMock())
        fake_rns._b = MagicMock(name="identity_b")
        fake_rns.Identity.recall.side_effect = (
            lambda h: fake_rns._b if h == DEST_B else MagicMock())
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
                allow_rns_egress():
            for _ in range(6):
                _make_due(queue)
                queue.process_once()
        assert queue.get_dead_letters(), "precondition: leg B dead-lettered"
        assert _triplet(bridge) == (1, 1, 0)

    def test_fanout_every_leg_dead_letters_counts_one_drop(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST, DEST_B]))
        _drain(queue, fake_lxmf_raises=True)
        assert _triplet(bridge) == (1, 0, 1)

    def test_requeue_refused_counts_dropped_now(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False), \
                patch.object(queue, "enqueue", return_value=None):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST]))
        assert _triplet(bridge) == (1, 0, 1)


class TestSessionRetry:

    def _session(self, bridge):  # noqa: F811
        bridge.config.rns.sessions_enabled = True
        bridge.config.meshtastic.gateway_node_id = "!00000001"
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST_B.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        bridge._sessions = MagicMock()
        bridge._sessions.lookup.return_value = DEST.hex()

    def test_session_retry_that_lands_counts_and_touches_the_session(
            self, bridge, queue):  # noqa: F811
        self._session(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False), \
                patch.object(bridge, "send_to_meshtastic"):
            bridge._process_mesh_to_rns(_dm(dest_id="!00000001"))
        assert _triplet(bridge) == (1, 0, 0)
        bridge._sessions.touch.reset_mock()
        _drain(queue)
        assert _triplet(bridge) == (1, 1, 0)
        bridge._sessions.touch.assert_called_once_with("!aabb0042", DEST.hex())


class TestReplays:

    def test_exception_requeue_replay_is_not_a_second_attempt(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        with patch.object(bridge, "_own_mesh_node_id",
                          side_effect=RuntimeError("boom")):
            bridge._process_mesh_to_rns(_broadcast())
        assert _triplet(bridge) == (1, 0, 0), "requeued: pending, not dropped"
        with patch.object(bridge, "send_to_rns", return_value=True):
            _make_due(queue)
            queue.process_once()
        assert _triplet(bridge) == (1, 1, 0)

    def test_rns_down_requeue_failure_is_counted(
            self, bridge, queue):  # noqa: F811
        from gateway.bridge_health import SubsystemState
        bridge.health.get_subsystem_state.return_value = (
            SubsystemState.DISCONNECTED)
        with patch.object(bridge, "_requeue_failed_message",
                          return_value=False):
            assert bridge._m2r_rns_down(_broadcast()) is False
        assert _triplet(bridge) == (1, 0, 1)


class TestUntracked:

    def test_terminal_for_an_origin_this_process_never_saw(
            self, bridge, queue):  # noqa: F811
        bridge._m2r_on_terminal(
            {"m2r_origin": "from-before-restart"}, "delivered")
        assert _triplet(bridge) == (0, 0, 0)
        assert bridge.stats.get("m2r_terminal_untracked") == 1


class TestQueueTerminalHook:

    def _q(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        return PersistentMessageQueue(db_path=str(tmp_path / "q.db"))

    def test_delivered_and_dead_letter_fire_once_each(self, tmp_path):
        q = self._q(tmp_path)
        seen = []
        q.register_sender("x", lambda p: p["ok"],
                          on_terminal=lambda p, o: seen.append((p["n"], o)))
        # distinct "message": the dedup hash keys on content
        q.enqueue({"n": 1, "ok": True, "message": "a"}, destination="x")
        q.enqueue({"n": 2, "ok": False, "message": "b"}, destination="x")
        for _ in range(6):
            _make_due(q)
            q.process_once()
        assert sorted(seen) == [(1, "delivered"), (2, "dropped")]

    def test_expired_fires_dropped(self, tmp_path):
        q = self._q(tmp_path)
        seen = []
        q.register_sender("x", lambda p: False, max_age_s=1,
                          ready_fn=lambda: False,
                          on_terminal=lambda p, o: seen.append(o))
        q.enqueue({"n": 1, "message": "a"}, destination="x")
        # 10 s old: past max_age, inside the clock-jump sanity cap
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(seconds=10)).isoformat()
        with q._get_connection() as conn:
            conn.execute("UPDATE messages SET created_at = ?", (old,))
        assert q.expire_overdue() == 1
        assert seen == ["dropped"]

    def test_shed_fires_dropped(self, tmp_path):
        from gateway.message_queue import MessagePriority
        q = self._q(tmp_path)
        q._max_queue_size = 1
        seen = []
        q.register_sender("x", lambda p: True, ready_fn=lambda: False,
                          on_terminal=lambda p, o: seen.append((p["n"], o)))
        q.enqueue({"n": 1, "message": "a"}, destination="x",
                  priority=MessagePriority.LOW)
        q.enqueue({"n": 2, "message": "b"}, destination="x",
                  priority=MessagePriority.HIGH)
        assert seen == [(1, "dropped")]

    def test_a_raising_callback_never_breaks_the_queue(self, tmp_path):
        q = self._q(tmp_path)

        def boom(p, o):
            raise RuntimeError("callback bug")
        q.register_sender("x", lambda p: True, on_terminal=boom)
        q.enqueue({"n": 1, "message": "a"}, destination="x")
        q.process_once()
        with q._get_connection() as conn:
            assert conn.execute("SELECT status FROM messages").fetchone()[0] \
                == "delivered"
        assert q.get_stats()["terminal_callback_errors"] == 1


# --- non-author review (Fable 5.1, 2026-10-18): seven reproduced defects ---

def _pending(bridge):  # noqa: F811
    return dict(bridge._m2r_pending_map())


def _session_dm(bridge):  # noqa: F811
    bridge.config.rns.sessions_enabled = True
    bridge.config.meshtastic.gateway_node_id = "!00000001"
    bridge.config.rns.get_lxmf_destinations.return_value = [DEST_B.hex()]
    bridge.node_tracker.get_node_by_mesh_id.return_value = None
    _connect(bridge)
    bridge._sessions = MagicMock()
    bridge._sessions.lookup.return_value = DEST.hex()
    return _dm(dest_id="!00000001")


class TestReviewFindings:

    def test_f1a_shed_of_a_sibling_during_enqueue_still_resolves(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        queue._max_queue_size = 1  # leg 2's enqueue sheds leg 1
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST, DEST_B]))
        _drain(queue, fake_lxmf_raises=True)
        a, d, x = _triplet(bridge)
        assert (a, d, x) == (1, 0, 1) and not _pending(bridge)
        assert not bridge.stats.get("m2r_terminal_untracked")

    def test_f1b_terminal_before_enqueue_returns_still_resolves(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        real_enqueue = queue.enqueue

        def enqueue_then_drain(*a, **kw):  # the worker thread wins the race
            mid = real_enqueue(*a, **kw)
            _drain(queue)
            return mid
        with patch.object(bridge, "send_to_rns", return_value=False), \
                patch.object(queue, "enqueue", side_effect=enqueue_then_drain):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST]))
        assert _triplet(bridge) == (1, 1, 0) and not _pending(bridge)
        assert not bridge.stats.get("m2r_terminal_untracked")

    def test_f2a_session_success_then_health_raise_counts_once(
            self, bridge, queue):  # noqa: F811
        msg = _session_dm(bridge)
        bridge.health.record_message_sent.side_effect = RuntimeError("hiccup")
        with patch.object(bridge, "send_to_rns", return_value=True):
            bridge._process_mesh_to_rns(msg)
        assert _triplet(bridge) == (1, 1, 0)

    def test_f2b_session_failure_then_health_raise_never_requeues_twice(
            self, bridge, queue):  # noqa: F811
        msg = _session_dm(bridge)
        bridge.health.record_message_failed.side_effect = RuntimeError("hiccup")
        with patch.object(bridge, "send_to_rns", return_value=False), \
                patch.object(bridge, "send_to_meshtastic"):
            bridge._process_mesh_to_rns(msg)
        assert len(_pending(bridge)) == 1
        with queue._get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] \
                == 1, "one message, one row — never a second copy on the wire"
        bridge.health.record_message_failed.side_effect = None
        _drain(queue)
        assert _triplet(bridge) == (1, 1, 0)

    def test_f2c_refusal_then_health_raise_counts_once(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        bridge.health.record_message_failed.side_effect = RuntimeError("hiccup")
        with patch.object(bridge, "send_to_meshtastic"):
            bridge._process_mesh_to_rns(_dm())  # F14: no single recipient
        assert _triplet(bridge) == (1, 0, 1)
        with queue._get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] \
                == 0, "a refused DM must never be requeued"

    def test_f3_except_handler_raise_never_loses_a_claimed_origin(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        with patch.object(bridge, "_own_mesh_node_id",
                          side_effect=RuntimeError("boom")):
            bridge._process_mesh_to_rns(_broadcast())
        assert _triplet(bridge) == (1, 0, 0) and len(_pending(bridge)) == 1
        bridge.health.record_message_failed.side_effect = RuntimeError("hiccup")
        with patch.object(bridge, "_own_mesh_node_id",
                          side_effect=RuntimeError("boom")):
            _make_due(queue)
            queue.process_once()
        bridge.health.record_message_failed.side_effect = None
        with patch.object(bridge, "send_to_rns", return_value=True):
            for _ in range(4):
                _make_due(queue)
                queue.process_once()
        a, d, x = _triplet(bridge)
        assert a == 1 and a == d + x

    def test_f4_late_mark_failed_after_stale_dead_letter_fires_once(
            self, tmp_path):
        from datetime import datetime, timedelta
        from gateway.message_queue import PersistentMessageQueue
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        seen = []
        q.register_sender("x", lambda p: False,
                          on_terminal=lambda p, o: seen.append(o))
        mid = q.enqueue({"message": "a"}, destination="x", max_retries=1)
        assert q.mark_in_progress(mid)
        old = (datetime.now()
               - timedelta(seconds=q.STALE_TIMEOUT + 60)).isoformat()
        with q._get_connection() as conn:
            conn.execute("UPDATE messages SET updated_at = ?", (old,))
        q.cleanup_stale()
        q.mark_failed(mid, "slow dispatch finally returned False")
        assert seen == ["dropped"]

    def test_f5_sibling_legs_finishing_are_not_untracked(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST, DEST_B]))
        _drain(queue)  # both legs deliver
        assert _triplet(bridge) == (1, 1, 0)
        assert not bridge.stats.get("m2r_terminal_untracked")
        assert not _pending(bridge), "a finished origin must not linger"


def test_cli_names_the_pending_gap_and_parsers_still_read_it():
    """Review finding 7: attempted - delivered - dropped is PENDING; say so
    without breaking the two parsers that anchor on the triplet."""
    from gateway import bridge_cli
    from utils.gateway_flow_audit import _COUNTER_RE  # noqa: F401
    from utils import watchdog_gateway_delivery_report as rep
    stats = {'messages_mesh_to_rns': 3, 'mesh_to_rns_delivered': 3,
             'mesh_to_rns_attempted': 5, 'mesh_to_rns_dropped': 1,
             'rns_to_mesh_attempted': 2, 'messages_rns_to_mesh': 2,
             'rns_to_mesh_dropped': 0}
    line = [ln for ln in bridge_cli._format_counts(stats)
            if "attempted/delivered/dropped" in ln][0]
    assert line.endswith("(M->R pending: 1)")
    m = rep._GATEWAY_DELIVERY_BLOCK_RE.search(line)
    assert m and m.groups() == ("5", "3", "1", "2", "2", "0")


class TestReviewRound2:
    """R2-1: an enqueue() that RAISES (sqlite 'database is locked' on an SD
    card) skipped settle — the origin stranded, and a live leg could later
    deliver a message already counted dropped."""

    def test_enqueue_raising_mid_fanout_keeps_the_live_leg_pending(
            self, bridge, queue):  # noqa: F811
        _connect(bridge)
        real = queue.enqueue
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database is locked")
            return real(*a, **kw)
        with patch.object(bridge, "send_to_rns", return_value=False), \
                patch.object(queue, "enqueue", side_effect=flaky):
            bridge._process_mesh_to_rns(_directed(bridge, [DEST, DEST_B]))
        assert _triplet(bridge) == (1, 0, 0), "a live row exists: pending"
        assert bridge.stats.get("m2r_requeue_enqueue_errors") == 1
        _drain(queue)
        assert _triplet(bridge) == (1, 1, 0) and not _pending(bridge)

    def test_xform_enqueue_raising_counts_dropped_and_forgets(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        with patch.object(bridge, "_own_mesh_node_id",
                          side_effect=RuntimeError("boom")), \
                patch.object(queue, "enqueue",
                             side_effect=RuntimeError("database is locked")):
            bridge._process_mesh_to_rns(_broadcast())
        assert _triplet(bridge) == (1, 0, 1) and not _pending(bridge)

    def test_mark_failed_on_a_finished_row_reports_no_transition(
            self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        mid = q.enqueue({"message": "a"}, destination="x", max_retries=1)
        assert q.mark_failed(mid, "first") is True    # -> dead_letter
        assert q.mark_failed(mid, "late") is False    # ignored, and says so


def test_tcp_handler_counts_a_full_queue_drop():
    """A Mesh->RNS message lost because the bridge's in-memory queue is full
    was counted only as an error (the MQTT handler's spill-failure leg already
    counts both halves). It is a message: attempted + dropped. (MeshAnchor
    port review, 2026-10-18.)"""
    import threading
    from queue import Queue
    from gateway.config import GatewayConfig
    from gateway.meshtastic_handler import MeshtasticHandler
    q = Queue(maxsize=1)
    q.put_nowait(object())
    stats = {"messages_mesh_to_rns": 0, "errors": 0}
    h = MeshtasticHandler(
        config=GatewayConfig(), node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats=stats,
        stats_lock=threading.Lock(), message_queue=q,
        message_callback=lambda m: None)
    h._handle_text_message(
        {"fromId": "!aabbccdd", "toId": "!ffffffff", "channel": 0},
        {"payload": b"hello", "text": "hello"}, "!aabbccdd")
    assert stats.get("errors") == 1, "precondition: the full-queue branch ran"
    assert stats.get("mesh_to_rns_attempted") == 1
    assert stats.get("mesh_to_rns_dropped") == 1
