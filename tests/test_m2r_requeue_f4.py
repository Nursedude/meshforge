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
    q.register_sender("rns", bridge._queue_send_rns)
    q.register_sender("rns_xform", bridge._dispatch_rns_xform_spill)
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
