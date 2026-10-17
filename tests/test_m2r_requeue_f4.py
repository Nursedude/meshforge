"""Bridge matrix F4 — a failed Mesh→RNS message must be retryable.

Before the fix every M→R requeue wrote ``destination_id`` (a Meshtastic node
id) under destination "rns", while the "rns" sender (``_queue_send_rns``)
reads ``destination_hash`` — so every retry returned False and dead-lettered,
and the retry also dropped the origin title + fields. These tests drive a
REAL ``PersistentMessageQueue`` from the failure to the retry dispatch, so
they pin the reader/writer pair, not either half alone.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export

DEST = bytes.fromhex("6b1a0120941444587d7d1dc1bf6d64d7")
DEST_B = bytes.fromhex("aa" * 16)


@pytest.fixture(autouse=True)
def _isolated_delivery_counters(tmp_path, monkeypatch):
    from gateway import delivery_counters as _dc
    monkeypatch.setenv("MESHFORGE_DELIVERY_COUNTERS_DB",
                       str(tmp_path / "syn_ack.db"))
    _dc._reset_singleton_for_tests()
    yield
    _dc._reset_singleton_for_tests()


@pytest.fixture
def queue(tmp_path, bridge):  # noqa: F811
    from gateway.message_queue import PersistentMessageQueue
    q = PersistentMessageQueue(db_path=str(tmp_path / "queue.db"))
    bridge._register_rns_queue_senders(q)  # the production wiring
    bridge._persistent_queue = q
    return q


def _fake_rns():
    fake_rns = MagicMock(name="RNS")
    fake_rns.Transport.has_path.return_value = True
    fake_rns.Identity.recall.return_value = MagicMock(name="dest_identity")
    fake_rns.Destination.OUT = "OUT"
    fake_rns.Destination.SINGLE = "SINGLE"
    fake_lxmf = MagicMock(name="LXMF")
    fake_lxmf.LXMessage.return_value = MagicMock(name="lxm")
    return fake_rns, fake_lxmf


def _connect(bridge):  # noqa: F811
    bridge._connected_rns = True
    bridge._lxmf_source = MagicMock(name="lxmf_source")
    bridge._lxmf_router = MagicMock(name="lxmf_router")


def _make_due(q):
    with q._get_connection() as conn:
        conn.execute("UPDATE messages SET retry_after = NULL")


def _rows(q, destination):
    with q._get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT destination, status FROM messages WHERE destination = ?",
            (destination,))]


def _dm(dest_id="!dest0001", content="private hello"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(
        source_network="meshtastic", source_id="!aabb0042",
        destination_id=dest_id, content=content,
        is_broadcast=False, metadata={"channel": 0},
    )


def _broadcast(content="public hello"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(
        source_network="meshtastic", source_id="!aabb0042",
        destination_id="!ffffffff", content=content,
        is_broadcast=True, metadata={"channel": 0},
    )


class TestFailedDirectedDmRetries:

    def test_retry_reaches_the_resolved_hash_with_origin_title(
            self, bridge, queue):  # noqa: F811
        node = MagicMock(rns_hash=DEST)
        node.name = "Alice"
        node.short_name = "ALC"
        bridge.node_tracker.get_node_by_mesh_id.return_value = node
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_dm())

        fake_rns, fake_lxmf = _fake_rns()
        _make_due(queue)
        from utils.tx_guard import allow_rns_egress
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}), \
                allow_rns_egress():
            queue.process_once()

        fake_rns.Transport.has_path.assert_any_call(DEST)
        assert fake_lxmf.LXMessage.called, "retry never built an LXMF message"
        args = fake_lxmf.LXMessage.call_args
        assert args.args[2] == "private hello"
        assert "via Meshtastic" in args.args[3]
        assert args.kwargs["fields"]["meshforge_from_id"] == "!aabb0042"
        assert _rows(queue, "rns") == [
            {"destination": "rns", "status": "delivered"}]

    def test_fanout_failure_keeps_one_row_per_destination(
            self, bridge, queue):  # noqa: F811
        # A non-broadcast with NO destination keeps the default-list fan-out
        # (F14 refuses only an addressed DM); every failed leg must survive
        # the queue's content dedup as its own row.
        bridge.config.rns.get_lxmf_destinations.return_value = [
            DEST.hex(), DEST_B.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=False):
            bridge._process_mesh_to_rns(_dm(dest_id=None, content="to all"))
        assert len(_rows(queue, "rns")) == 2


class TestRnsDownRequeue:

    def test_rns_down_broadcast_waits_then_replays_through_the_pipeline(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._connected_rns = False
        assert bridge._requeue_failed_message(_broadcast(), "rns") is True

        sent = []
        with patch.object(bridge, "send_to_rns",
                          side_effect=lambda c, d, **k: sent.append(d) or True):
            queue.process_once()          # still down: must wait, not drop
            assert sent == []
            assert queue.get_dead_letters() == []
            _connect(bridge)
            _make_due(queue)
            queue.process_once()
        assert sent == [DEST]

    def test_private_dm_queued_while_down_is_still_refused_on_replay(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._connected_rns = False
        bridge._requeue_failed_message(_dm(), "rns")

        _connect(bridge)
        with patch.object(bridge, "send_to_rns", return_value=True) as send, \
                patch.object(bridge, "send_to_meshtastic", return_value=True):
            queue.process_once()
        assert not send.called, "a replayed DM was fanned to the default list"
        assert bridge.stats.get("mesh_to_rns_private_refused") == 1


class TestExceptionPath:

    def test_exception_after_a_send_is_not_requeued(
            self, bridge, queue):  # noqa: F811
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        bridge.health.record_message_sent.side_effect = RuntimeError("boom")
        with patch.object(bridge, "send_to_rns", return_value=True):
            bridge._process_mesh_to_rns(_broadcast())
        assert _rows(queue, "rns") == [] and _rows(queue, "rns_xform") == []
        bridge.health.record_message_failed.assert_called_with(
            "mesh_to_rns", requeued=False)

    def test_a_replay_that_raises_again_is_not_requeued_again(
            self, bridge, queue):  # noqa: F811
        bridge._connected_rns = False
        bridge._requeue_failed_message(_broadcast(), "rns")
        # The 60 s dedup window would hide the loop for one cycle only.
        queue.DEDUP_WINDOW = 0
        _connect(bridge)
        with patch.object(bridge, "_own_mesh_node_id",
                          side_effect=RuntimeError("boom")):
            queue.process_once()
        with queue._get_connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE status = 'pending'"
            ).fetchone()[0]
        assert pending == 0, "a raising replay re-enqueued itself"


# --- non-author review of a1a19a29 (2026-10-17): five confirmed holes -----

def _dead(q):
    with q._get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE status = 'dead_letter'"
        ).fetchone()[0]


class TestReviewFollowups:

    def test_session_send_then_raise_is_not_requeued(
            self, bridge, queue):  # noqa: F811
        # R1: the session leg sends without bumping sent_count, so a raise
        # after it requeued a private DM that was already delivered.
        bridge.config.rns.sessions_enabled = True
        bridge.config.meshtastic.gateway_node_id = "!00000001"
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        bridge._sessions = MagicMock()
        bridge._sessions.lookup.return_value = DEST.hex()
        bridge._sessions.touch.side_effect = RuntimeError("touch boom")
        sent = []
        with patch.object(bridge, "send_to_rns",
                          side_effect=lambda c, d, **k: sent.append(d) or True):
            bridge._process_mesh_to_rns(_dm(dest_id="!00000001"))
        assert sent == [DEST]
        assert _rows(queue, "rns_xform") == [] and _rows(queue, "rns") == []

    @pytest.mark.parametrize("dest", ["rns", "rns_xform"])
    def test_rows_wait_out_an_outage_longer_than_three_retries(
            self, bridge, queue, dest):  # noqa: F811
        # R2: no RetryPolicy → 3 False returns dead-letter in ~20 s, shorter
        # than an rnsd restart. While RNS is down the row must not be tried.
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = MagicMock(
            rns_hash=DEST)
        bridge._connected_rns = False
        if dest == "rns":
            bridge._requeue_failed_message(_dm(), "rns",
                                           rns_destinations=[DEST])
        else:
            bridge._requeue_failed_message(_broadcast(), "rns")
        for _ in range(5):
            _make_due(queue)
            queue.process_once()
        assert _dead(queue) == 0, "row dead-lettered while RNS was down"
        assert len(_rows(queue, dest)) == 1

    def test_replay_with_nowhere_to_send_does_not_reenqueue(
            self, bridge, queue):  # noqa: F811
        # R3: a non-broadcast with no destination and NO default list has no
        # leg to retry; requeueing it as a replay just loops on dedup.
        bridge.config.rns.get_lxmf_destinations.return_value = []
        queue.DEDUP_WINDOW = 0
        _connect(bridge)
        assert bridge._requeue_failed_message(
            _dm(dest_id=None), "rns", rns_destinations=[]) is False
        bridge._connected_rns = False
        bridge._requeue_failed_message(_dm(dest_id=None), "rns")
        _connect(bridge)
        for _ in range(3):
            _make_due(queue)
            queue.process_once()
        with queue._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert total == 1, "a replay with no destination re-enqueued itself"

    def test_requeue_reports_false_when_dedup_dropped_every_row(
            self, bridge, queue):  # noqa: F811
        # R4: requeued=True must mean a row exists.
        bridge._connected_rns = False
        assert bridge._requeue_failed_message(_broadcast(), "rns") is True
        assert bridge._requeue_failed_message(_broadcast(), "rns") is False
        assert bridge._requeue_failed_message(
            _dm(), "rns", rns_destinations=[DEST]) is True
        assert bridge._requeue_failed_message(
            _dm(), "rns", rns_destinations=[DEST]) is False

    def test_partial_fanout_requeues_only_the_failed_leg(
            self, bridge, queue):  # noqa: F811
        # R5: one sibling succeeding must not erase the other's retry.
        bridge.config.rns.get_lxmf_destinations.return_value = [
            DEST.hex(), DEST_B.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        _connect(bridge)
        with patch.object(bridge, "send_to_rns",
                          side_effect=lambda c, d, **k: d == DEST):
            bridge._process_mesh_to_rns(_broadcast())
        with queue._get_connection() as conn:
            rows = [r[0] for r in conn.execute(
                "SELECT payload FROM messages WHERE destination = 'rns'")]
        assert len(rows) == 1 and DEST_B.hex() in rows[0]

    def test_replay_keeps_the_original_channel(
            self, bridge, queue):  # noqa: F811
        # Review mutant M2 survived: a replay that drops metadata loses the
        # channel the LXMF fields carry (meshforge_channel).
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [DEST.hex()]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._connected_rns = False
        bridge._requeue_failed_message(BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!ffffffff", content="on ch3", is_broadcast=True,
            metadata={"channel": 3}), "rns")
        seen = {}
        _connect(bridge)
        with patch.object(bridge, "send_to_rns",
                          side_effect=lambda c, d, **k: seen.update(k) or True):
            queue.process_once()
        assert seen["fields"]["meshforge_channel"] == 3
