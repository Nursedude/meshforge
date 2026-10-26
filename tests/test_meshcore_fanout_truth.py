"""Bridge matrix F16 + F5 (MF side): the Meshtastic/RNS → MeshCore fan-out.

F16: the bridge loop put EVERY Mesh→RNS message on the MeshCore queue,
whatever the Mesh→RNS echo guard decided, so a bridge-tagged ``[RNS:…]``
line heard on Meshtastic left for MeshCore as ``[Mesh:x] [RNS:…] …``; a
full queue dropped it at DEBUG with no counter.

F5: MeshForge has no outbound slot routing, so every fan-out broadcast is
refused by the handler on a real radio (``dropped, unsendable``) — while
``send_text`` returned True on ENQUEUE and health recorded *sent*. Two views
of one message disagreed.

The guard sits on the MeshCore leg's own facts — never on what the Mesh→RNS
leg decided — so an RNS outage cannot starve MeshCore.

Run: python3 -m pytest tests/test_meshcore_fanout_truth.py -v
"""

import logging
import sys
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tests.test_rns_bridge import bridge  # noqa: F401 — fixture re-export


def _mesh_msg(content, source_id="!aabb0042"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(source_network="meshtastic", source_id=source_id,
                          destination_id=None, content=content,
                          is_broadcast=True)


def _rns_msg(content, source_id="0123456789abcdef"):
    from gateway.rns_bridge import BridgedMessage
    return BridgedMessage(source_network="rns", source_id=source_id,
                          destination_id=None, content=content,
                          is_broadcast=True)


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _handler(can_broadcast):
    h = MagicMock()
    h.can_broadcast = can_broadcast
    return h


@pytest.fixture
def ledger():
    from gateway import delivery_counters as dc
    dc._reset_singleton_for_tests()
    dc.get_singleton()._reset_for_tests()
    yield lambda: [(e.state.value, e.drop_reason and e.drop_reason.value)
                   for e in dc.get_singleton().recent()
                   if e.protocol == "meshcore"]
    dc._reset_singleton_for_tests()


class TestEchoNeverFansOut:
    """F16: a bridge-tagged Meshtastic line is already bridged."""

    def test_rns_tagged_mesh_line_is_not_queued(self, bridge):
        bridge._meshcore_handler = _handler(True)
        bridge._fan_out_to_meshcore(_mesh_msg("[RNS:ab12] hello"))
        assert _drain(bridge._bridge_to_meshcore_queue) == []
        assert bridge.stats.get("meshcore_fanout_echo_suppressed") == 1

    def test_plain_mesh_line_is_queued(self, bridge):
        """The control: genuine Meshtastic text still reaches MeshCore."""
        bridge._meshcore_handler = _handler(True)
        msg = _mesh_msg("hello")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]
        assert not bridge.stats.get("meshcore_fanout_echo_suppressed")

    def test_echo_guard_does_not_read_rns_down(self, bridge):
        """Independent legs: the fan-out never consults the RNS subsystem."""
        bridge._meshcore_handler = _handler(True)
        bridge.health.get_subsystem_state.side_effect = AssertionError(
            "MeshCore fan-out must not depend on the RNS leg's state")
        msg = _mesh_msg("hello")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]


class TestUnsendableIsNotSent:
    """F5: on a real radio MF cannot broadcast — say so, never 'sent'."""

    def test_real_radio_never_enqueues(self, bridge, ledger):
        bridge._meshcore_handler = _handler(False)
        bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        assert _drain(bridge._bridge_to_meshcore_queue) == []
        assert bridge.stats.get("meshcore_fanout_unsendable") == 1
        bridge.health.record_message_sent.assert_not_called()

    def test_real_radio_refusal_reaches_the_delivery_ledger(self, bridge, ledger):
        """BIC-2: gating early must not erase the leg's outcome."""
        bridge._meshcore_handler = _handler(False)
        bridge._fan_out_to_meshcore(_rns_msg("from lxmf"))
        assert ledger() == [("dropped", "unsendable")]

    def test_handler_without_can_broadcast_enqueues_as_before(self, bridge):
        """A handler predating F5 must not raise inside the bridge loop."""
        bridge._meshcore_handler = SimpleNamespace()
        msg = _mesh_msg("hello")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]

    def test_refusal_warns_once_then_debug(self, bridge, caplog):
        bridge._meshcore_handler = _handler(False)
        with caplog.at_level(logging.DEBUG, logger="gateway.meshcore_bridge_mixin"):
            for _ in range(3):
                bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        warns = [r for r in caplog.records
                 if r.levelno == logging.WARNING and "unsendable" in r.message]
        assert len(warns) == 1
        assert bridge.stats.get("meshcore_fanout_unsendable") == 3

    def test_simulator_still_enqueues(self, bridge, ledger):
        bridge._meshcore_handler = _handler(True)
        msg = _rns_msg("from lxmf")
        bridge._fan_out_to_meshcore(msg)
        assert _drain(bridge._bridge_to_meshcore_queue) == [msg]
        assert ledger() == []

    def test_origin_filtered_message_is_not_recorded_unsendable(self, bridge, ledger):
        """Internet/MQTT origin was never a MeshCore attempt; keep it quiet."""
        from gateway.bridge_health import MessageOrigin
        bridge._meshcore_handler = _handler(False)
        msg = _mesh_msg("hello")
        msg.origin = MessageOrigin.MQTT
        bridge._fan_out_to_meshcore(msg)
        assert ledger() == []
        assert not bridge.stats.get("meshcore_fanout_unsendable")


class TestQueueFullIsCounted:
    """F16b: a full queue is a lost message — counted, WARNING once."""

    def test_each_reason_gets_its_own_warning(self, bridge, caplog):
        """One reason's warning must not silence another's window."""
        bridge._bridge_to_meshcore_queue = Queue(maxsize=1)
        bridge._bridge_to_meshcore_queue.put_nowait(object())
        with caplog.at_level(logging.DEBUG, logger="gateway.meshcore_bridge_mixin"):
            bridge._meshcore_handler = _handler(False)
            bridge._fan_out_to_meshcore(_mesh_msg("a"))
            bridge._meshcore_handler = _handler(True)
            bridge._fan_out_to_meshcore(_mesh_msg("b"))
        warns = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("unsendable" in w for w in warns)
        assert any("queue full" in w for w in warns)

    def test_full_queue_counts_and_warns_once(self, bridge, caplog):
        bridge._meshcore_handler = _handler(True)
        bridge._bridge_to_meshcore_queue = Queue(maxsize=1)
        bridge._bridge_to_meshcore_queue.put_nowait(object())
        with caplog.at_level(logging.DEBUG, logger="gateway.meshcore_bridge_mixin"):
            for _ in range(3):
                bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        assert bridge.stats.get("meshcore_fanout_dropped_queue_full") == 3
        warns = [r for r in caplog.records
                 if r.levelno == logging.WARNING and "queue full" in r.message]
        assert len(warns) == 1

    def test_no_handler_is_a_no_op(self, bridge):
        bridge._meshcore_handler = None
        bridge._fan_out_to_meshcore(_mesh_msg("hello"))
        assert _drain(bridge._bridge_to_meshcore_queue) == []


class TestBridgeLoopUsesTheOneFanOut:
    """Both loop sites go through _fan_out_to_meshcore — no raw put_nowait
    left behind to bypass the guard (the F16 shape was a second path)."""

    def test_no_raw_meshcore_put_in_rns_bridge(self):
        src = (Path(__file__).parent.parent / "src" / "gateway"
               / "rns_bridge.py").read_text()
        assert "_bridge_to_meshcore_queue.put_nowait" not in src
        assert src.count("self._fan_out_to_meshcore(msg)") == 2


class TestHandlerCanBroadcast:
    """The capability is a CONFIG fact: only the simulator can broadcast."""

    @pytest.mark.parametrize("sim,expected", [(True, True), (False, False)])
    def test_can_broadcast_follows_simulation_mode(self, sim, expected):
        from gateway.meshcore_handler import MeshCoreHandler
        cfg = SimpleNamespace(
            meshcore=SimpleNamespace(
                enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
                connection_type="serial", tcp_host="localhost", tcp_port=4000,
                auto_fetch_messages=True, bridge_channels=True,
                bridge_dms=True, simulation_mode=sim,
                channel_poll_interval_sec=5),
            meshtastic=SimpleNamespace(host="localhost", port=4403))
        # Without meshcore_py the handler is a simulator whatever the config
        # says; pin the library present so the config is what decides.
        with patch("gateway.meshcore_handler._HAS_MESHCORE", True):
            h = MeshCoreHandler(config=cfg, node_tracker=MagicMock(),
                                health=MagicMock(), stop_event=threading.Event(),
                                stats={"errors": 0}, stats_lock=threading.Lock(),
                                message_queue=Queue(maxsize=10))
        assert h.can_broadcast is expected


class TestHealthFollowsTheOutcome:
    """F5, the other half: ENQUEUE is not delivery. Health counts the TX
    worker's outcome (``to_meshcore``), never the hand-off to its queue."""

    def test_enqueue_is_not_counted_sent(self, bridge):
        bridge._meshcore_handler = _handler(True)
        with patch.object(bridge, "send_to_meshcore", return_value=True):
            bridge._process_bridge_to_meshcore(_mesh_msg("hello"))
        bridge.health.record_message_sent.assert_not_called()

    def _worker(self, send_ok):
        import asyncio
        from gateway.canonical_message import CanonicalMessage
        from gateway.meshcore_handler import MeshCoreHandler
        cfg = SimpleNamespace(
            meshcore=SimpleNamespace(
                enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
                connection_type="serial", tcp_host="localhost", tcp_port=4000,
                auto_fetch_messages=True, bridge_channels=True,
                bridge_dms=True, simulation_mode=True,
                channel_poll_interval_sec=5),
            meshtastic=SimpleNamespace(host="localhost", port=4403))
        health = MagicMock()
        h = MeshCoreHandler(config=cfg, node_tracker=MagicMock(),
                            health=health, stop_event=threading.Event(),
                            stats={"errors": 0}, stats_lock=threading.Lock(),
                            message_queue=Queue(maxsize=10))
        h._send_queue.put_nowait(CanonicalMessage(
            content="x", destination_address=None, is_broadcast=True))

        async def fake_send(*a, **k):
            return send_ok
        loop = asyncio.new_event_loop()
        try:
            with patch.object(h, "_send_message", side_effect=fake_send):
                loop.run_until_complete(h._process_outbound())
        finally:
            loop.close()
        return health

    def test_worker_success_counts_sent(self):
        health = self._worker(True)
        health.record_message_sent.assert_called_once_with("to_meshcore")
        health.record_message_failed.assert_not_called()

    def test_worker_failure_counts_failed(self):
        health = self._worker(False)
        health.record_message_sent.assert_not_called()
        health.record_message_failed.assert_called_once_with(
            "to_meshcore", requeued=False)
