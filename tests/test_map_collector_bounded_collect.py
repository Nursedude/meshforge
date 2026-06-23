"""Bounded meshtasticd collect — a wedged daemon must not pin _collect_lock.

Regression cover for the 2026-06-23 moc1 map spin: the meshtastic TCPInterface
constructor blocks until the nodedb sync completes, and a wedged daemon can
stall it up to its ~900s idle timeout while the map collector holds
_collect_lock, wedging every /api/nodes/geojson + /api/network/topology request
behind it (667 stuck handler threads, fd exhaustion). _collect_interface_bounded
runs the blocking connect+read+close in a worker and abandons the wait after a
hard wall-clock cap.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from utils.map_data_collector import MapDataCollector


@pytest.fixture
def collector(tmp_path):
    return MapDataCollector(
        cache_dir=tmp_path, config_dir=tmp_path, enable_history=False
    )


def _iface(nodes=None):
    iface = MagicMock()
    iface.nodes = nodes or {}
    return iface


def _positioned_node(num, lat, lon, name="N"):
    return {
        "num": num,
        "position": {"latitude": lat, "longitude": lon},
        "user": {"longName": name},
        "lastHeard": int(time.time()),
    }


def _no_position_node(num, name="N"):
    return {"num": num, "user": {"longName": name}, "lastHeard": int(time.time())}


class TestBoundedCleanJoin:
    def test_returns_features_total_and_publishes_no_position(self, collector):
        nodes = {
            "!a": _positioned_node(0xA, 19.7, -155.0, "A"),
            "!b": _no_position_node(0xB, "B"),
        }
        manager = MagicMock()
        manager.acquire_lock.return_value = True
        manager._create_interface.return_value = _iface(nodes)

        with patch(
            "utils._map_collector_meshtastic.safe_close_interface"
        ) as close:
            feats, total, timed_out = collector._collect_interface_bounded(
                manager, "meshtasticd", 5.0
            )

        assert timed_out is False
        assert total == 2
        assert len(feats) == 1  # only the positioned node yields a feature
        # The no-position node is published by the MAIN thread on a clean join.
        assert len(collector._nodes_without_position) == 1
        close.assert_called_once()
        manager.release_lock.assert_called_once()

    def test_lock_contention_returns_empty_fast_no_timeout(self, collector):
        manager = MagicMock()
        manager.acquire_lock.return_value = False  # someone else holds it

        start = time.perf_counter()
        feats, total, timed_out = collector._collect_interface_bounded(
            manager, "meshtasticd", 5.0
        )
        elapsed = time.perf_counter() - start

        assert (feats, total, timed_out) == ([], 0, False)
        assert elapsed < 1.0  # never waited the 5s cap
        manager._create_interface.assert_not_called()
        # A lock-contention miss is NOT a wedge — no timeout diagnostic.
        assert "meshtasticd" not in collector.get_source_diagnostics()


class TestBoundedTimeout:
    def test_wedged_create_returns_within_cap_and_records_diagnostic(
        self, collector
    ):
        release = threading.Event()

        def blocking_create():
            release.wait(timeout=5)  # simulate a wedged nodedb sync
            return _iface({})

        manager = MagicMock()
        manager.acquire_lock.return_value = True
        manager._create_interface.side_effect = blocking_create

        try:
            with patch("utils._map_collector_meshtastic.safe_close_interface"):
                start = time.perf_counter()
                feats, total, timed_out = collector._collect_interface_bounded(
                    manager, "meshtasticd", 0.3
                )
                elapsed = time.perf_counter() - start

            assert (feats, total, timed_out) == ([], 0, True)
            # Bounded: returned near the cap, NOT the 5s wedge.
            assert 0.3 <= elapsed < 2.0
            # Witness: a collect_timeout diagnostic was recorded.
            diag = collector.get_source_diagnostics()["meshtasticd"]
            assert diag["reason_if_zero"] == "collect_timeout"
        finally:
            release.set()  # let the abandoned worker finish + clean up

    def test_abandoned_worker_does_not_mutate_self_node_list(self, collector):
        """The worker writes only to its local result; an abandoned worker that
        finishes later must NOT touch self._nodes_without_position (else it
        corrupts a LATER collect cycle — honest-failure-modes #8)."""
        collector._nodes_without_position = ["SENTINEL"]
        release = threading.Event()
        done = threading.Event()

        def blocking_create():
            release.wait(timeout=5)
            return _iface({"!z": _no_position_node(0x2A, "Z")})

        manager = MagicMock()
        manager.acquire_lock.return_value = True
        manager._create_interface.side_effect = blocking_create
        manager.release_lock.side_effect = lambda: done.set()

        with patch("utils._map_collector_meshtastic.safe_close_interface"):
            feats, total, timed_out = collector._collect_interface_bounded(
                manager, "meshtasticd", 0.3
            )
            assert timed_out is True
            # Let the abandoned worker run to completion.
            release.set()
            assert done.wait(timeout=5), "worker never finished/cleaned up"
            # brief beat for the worker's finally to fully unwind
            time.sleep(0.05)

        # The late worker drained into its LOCAL result, never into self.
        assert collector._nodes_without_position == ["SENTINEL"]


class TestTcpInterfaceWiring:
    def test_timeout_sets_wedge_flag_and_returns_empty(self, collector):
        with patch.object(
            collector, "_collect_interface_bounded", return_value=([], 0, True)
        ):
            out = collector._collect_via_tcp_interface()
        assert out == []
        assert collector._meshtasticd_collect_wedged is True

    def test_clean_collect_clears_flag_and_sets_total(self, collector):
        feat = {"type": "Feature"}
        with patch.object(
            collector, "_collect_interface_bounded", return_value=([feat], 7, False)
        ):
            out = collector._collect_via_tcp_interface()
        assert out == [feat]
        assert collector._meshtasticd_collect_wedged is False
        assert collector._total_nodes_seen == 7


class TestCliFallbackSkippedOnWedge:
    def test_wedged_tcp_skips_cli_fallback(self, collector):
        """On a TCP timeout the daemon is wedged; the CLI fallback hits the same
        daemon and only adds latency to the held lock — it must be skipped."""

        def fake_tcp():
            collector._meshtasticd_collect_wedged = True
            return []

        sock = MagicMock()
        sock.connect_ex.return_value = 0  # port open → reaches the TCP strategy

        with patch.object(collector, "_collect_via_http", return_value=[]), \
             patch.object(collector, "_gateway_owns_phoneapi", return_value=False), \
             patch.object(collector, "_collect_via_tcp_interface", side_effect=fake_tcp), \
             patch.object(collector, "_collect_via_cli") as cli, \
             patch("utils._map_collector_meshtastic.socket.socket", return_value=sock):
            out = collector._collect_meshtasticd()

        assert out == []
        cli.assert_not_called()

    def test_empty_tcp_without_wedge_still_tries_cli(self, collector):
        def fake_tcp():
            collector._meshtasticd_collect_wedged = False
            return []

        sock = MagicMock()
        sock.connect_ex.return_value = 0

        with patch.object(collector, "_collect_via_http", return_value=[]), \
             patch.object(collector, "_gateway_owns_phoneapi", return_value=False), \
             patch.object(collector, "_collect_via_tcp_interface", side_effect=fake_tcp), \
             patch.object(collector, "_collect_via_cli", return_value=[]) as cli, \
             patch("utils._map_collector_meshtastic.socket.socket", return_value=sock):
            collector._collect_meshtasticd()

        cli.assert_called_once()


class TestTimeoutSetting:
    def test_default_is_45(self, collector):
        assert collector.get_meshtasticd_tcp_collect_timeout_seconds() == 45.0

    def test_reads_override_from_settings(self, collector):
        collector._settings.set("meshtasticd_tcp_collect_timeout_seconds", 90)
        assert collector.get_meshtasticd_tcp_collect_timeout_seconds() == 90.0
