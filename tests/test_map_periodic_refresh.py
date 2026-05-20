"""Tests for MapDataCollector.start_periodic_refresh.

Pins the fix for the silent-stall class verified 2026-05-20 on
meshanchor-server: 8.5 d freeze of node_history.db on a box whose
map service was running fine but nothing ever called the trigger
endpoint (/api/nodes/geojson). The fix is a periodic background
thread that drives _collect_locked() on a fixed cadence so the
directory stays fresh without an HTTP caller.
"""

import threading
import time
from unittest.mock import patch

import pytest

from utils.map_data_collector import MapDataCollector


@pytest.fixture
def collector(tmp_path):
    """Collector with isolated home/config; no fleet.json bootstrap."""
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    with patch(
        "utils.map_data_collector.get_real_user_home",
        return_value=fake_home,
    ):
        c = MapDataCollector(
            cache_dir=tmp_path / "cache",
            enable_history=False,
            config_dir=cfg_dir,
        )
    yield c
    c.stop_periodic_refresh()


class TestStartPeriodicRefresh:
    def test_drives_collect_locked_on_cadence(self, collector):
        """The whole point of the fix — _collect_locked fires without HTTP."""
        calls = []
        with patch.object(
            collector,
            "_collect_locked",
            side_effect=lambda: calls.append(time.time()),
        ):
            collector.start_periodic_refresh(interval_seconds=0.05)
            time.sleep(0.25)
            collector.stop_periodic_refresh()
        # Loop waits BEFORE the first collect (so start doesn't double-collect
        # right after a warmup). 0.25 s / 0.05 s ≈ 5 ticks, allow >=2 for jitter.
        assert len(calls) >= 2, (
            f"Expected periodic refresh to fire >=2 times in 0.25 s with "
            f"50 ms interval; got {len(calls)} call(s). Loop wedged or "
            f"interval reading failed."
        )

    def test_zero_interval_disables(self, collector):
        with patch.object(collector, "_collect_locked") as collect_mock:
            collector.start_periodic_refresh(interval_seconds=0)
            time.sleep(0.05)
            assert collect_mock.call_count == 0
            assert collector._periodic_refresh_thread is None

    def test_negative_interval_disables(self, collector):
        collector.start_periodic_refresh(interval_seconds=-1)
        assert collector._periodic_refresh_thread is None

    def test_reads_interval_from_settings(self, collector):
        """No explicit arg → reads ``periodic_refresh_seconds`` from settings."""
        # Settings shape-check: the key must default to a positive value so
        # production daemons get a periodic driver out of the box.
        default = collector._settings.get("periodic_refresh_seconds")
        assert isinstance(default, (int, float))
        assert default > 0

        # When called with no interval, the loop should use the settings value.
        # Patch the setting low so the test runs fast.
        collector._settings.set("periodic_refresh_seconds", 0.05)
        calls = []
        with patch.object(
            collector,
            "_collect_locked",
            side_effect=lambda: calls.append(1),
        ):
            collector.start_periodic_refresh()  # no arg
            time.sleep(0.18)
            collector.stop_periodic_refresh()
        assert len(calls) >= 1


class TestStopPeriodicRefresh:
    def test_stop_joins_thread_cleanly(self, collector):
        with patch.object(collector, "_collect_locked"):
            collector.start_periodic_refresh(interval_seconds=0.05)
            assert collector._periodic_refresh_thread is not None
            t = collector._periodic_refresh_thread
            assert t.is_alive()
            collector.stop_periodic_refresh()
            assert not t.is_alive()
            assert collector._periodic_refresh_thread is None

    def test_stop_is_idempotent(self, collector):
        collector.stop_periodic_refresh()  # never started
        collector.stop_periodic_refresh()  # called twice
        assert collector._periodic_refresh_thread is None


class TestRefreshLoopRobustness:
    def test_exception_does_not_kill_loop(self, collector):
        """A bad collect cycle must not stop subsequent ticks — fleet boxes
        can't afford to silently lose the heartbeat after one bad cycle."""
        fail_then_pass = [True]

        def maybe_fail():
            if fail_then_pass[0]:
                fail_then_pass[0] = False
                raise RuntimeError("simulated collect failure")
            fail_then_pass.append("ok")

        with patch.object(
            collector, "_collect_locked", side_effect=maybe_fail,
        ):
            collector.start_periodic_refresh(interval_seconds=0.05)
            time.sleep(0.25)
            collector.stop_periodic_refresh()
        # First call raised; loop kept going and at least one subsequent
        # call appended "ok".
        assert "ok" in fail_then_pass

    def test_skips_tick_when_collect_lock_held(self, collector):
        """If an HTTP-driven collect() is in flight, the periodic loop must
        skip its tick rather than wait — backed-up refresh ticks would
        burn CPU during an already-slow cycle."""
        with patch.object(collector, "_collect_locked") as collect_mock:
            with collector._collect_lock:
                collector.start_periodic_refresh(interval_seconds=0.05)
                time.sleep(0.18)
                # Periodic loop tried to acquire and got non-blocking-refused.
                # _collect_locked must NOT have been called.
                assert collect_mock.call_count == 0
            # Releasing the outer lock; next tick should now fire.
            time.sleep(0.18)
            collector.stop_periodic_refresh()
            assert collect_mock.call_count >= 1


class TestIdempotentStart:
    def test_second_start_replaces_first_thread(self, collector):
        with patch.object(collector, "_collect_locked"):
            collector.start_periodic_refresh(interval_seconds=0.5)
            t1 = collector._periodic_refresh_thread
            assert t1 is not None and t1.is_alive()

            collector.start_periodic_refresh(interval_seconds=0.5)
            t2 = collector._periodic_refresh_thread
            assert t2 is not None and t2.is_alive()
            assert t2 is not t1
            assert not t1.is_alive(), "first thread should be joined"

            collector.stop_periodic_refresh()


class TestIssue66RegressionPin:
    """Direct pin for the 2026-05-20 cloud-server stall.

    Symptom: meshanchor-server's node_history.db sat at May 11 21:55 for
    8.5 days while meshanchor-map.service reported active(running) the
    whole time. Root cause: collect() ran only on /api/nodes/geojson
    requests; without a caller, the directory froze.

    This test pins the contract that the daemon-mode driver causes
    _collect_locked to fire even when no HTTP request ever happens.
    """

    def test_collect_locked_fires_without_any_http_caller(self, collector):
        # No collect() — only the periodic driver.
        call_threads = []
        with patch.object(
            collector,
            "_collect_locked",
            side_effect=lambda: call_threads.append(
                threading.current_thread().name
            ),
        ):
            collector.start_periodic_refresh(interval_seconds=0.05)
            time.sleep(0.18)
            collector.stop_periodic_refresh()

        assert len(call_threads) >= 1, (
            "Without a periodic driver, the cloud-server class stall "
            "recurs: daemon up, DB frozen until someone visits the map."
        )
        # Calls came from the dedicated refresh thread, not random ones.
        for name in call_threads:
            assert name == "MapDataCollector-periodic-refresh"
