"""
Tests for TUI runtime stability fixes.

Covers: EventBus shutdown, StatusBar thread-safety, health probe watchdog,
and non-blocking space weather fetch.

Run: python3 -m pytest tests/test_tui_runtime_stability.py -v
"""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Path setup matching test_status_bar.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from src.utils.event_bus import EventBus, MessageEvent, NodeEvent
from src.utils.active_health_probe import ActiveHealthProbe, HealthResult

from status_bar import StatusBar


class TestEventBusShutdown:
    """Verify EventBus shutdown is safe and idempotent."""

    def test_shutdown_is_idempotent(self):
        """Calling shutdown() twice must not raise."""
        bus = EventBus()
        bus.shutdown()
        bus.shutdown()  # Second call should be safe

    def test_emit_after_shutdown_is_silent(self):
        """emit() after shutdown must not raise (RuntimeError caught)."""
        bus = EventBus()
        received = []
        bus.subscribe('test', lambda e: received.append(e))
        bus.shutdown()
        # Should not raise — RuntimeError from executor is caught
        bus.emit('test', 'hello')
        time.sleep(0.1)
        assert len(received) == 0

    def test_emit_sync_after_shutdown_has_no_subscribers(self):
        """After shutdown, subscribers are cleared so emit_sync delivers nothing."""
        bus = EventBus()
        received = []
        bus.subscribe('test', lambda e: received.append(e))
        bus.shutdown()
        # Shutdown clears subscribers to prevent new work being queued
        bus.emit_sync('test', 'hello')
        assert len(received) == 0


class TestStatusBarThreadSafety:
    """Verify StatusBar counters are thread-safe."""

    def test_concurrent_message_increments(self):
        """Multiple threads incrementing _unread_messages must not lose counts."""
        bar = StatusBar(version="test")

        num_threads = 10
        increments_per_thread = 100
        barrier = threading.Barrier(num_threads)

        def increment():
            barrier.wait()
            for _ in range(increments_per_thread):
                event = MagicMock()
                event.direction = 'rx'
                bar._on_message_event(event)

        threads = [threading.Thread(target=increment) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert bar._unread_messages == num_threads * increments_per_thread

    def test_concurrent_node_increments(self):
        """Multiple threads incrementing _node_count must not lose counts."""
        bar = StatusBar(version="test")
        bar._node_count = 0

        num_threads = 10
        increments_per_thread = 100
        barrier = threading.Barrier(num_threads)

        def increment():
            barrier.wait()
            for _ in range(increments_per_thread):
                event = MagicMock()
                event.event_type = 'discovered'
                bar._on_node_event(event)

        threads = [threading.Thread(target=increment) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert bar._node_count == num_threads * increments_per_thread


class TestStatusBarCleanup:
    """Verify StatusBar cleanup unsubscribes from EventBus."""

    def test_cleanup_unsubscribes(self):
        """cleanup() must call unsubscribe for all event types."""
        bar = StatusBar(version="test")

        with patch('status_bar.event_bus') as mock_bus:
            bar.cleanup()

        unsub_events = {call.args[0] for call in mock_bus.unsubscribe.call_args_list}
        assert unsub_events == {'service', 'message', 'node'}
        assert not bar._event_subscribed

    def test_cleanup_idempotent(self):
        """Calling cleanup() twice must not double-unsubscribe."""
        bar = StatusBar(version="test")

        with patch('status_bar.event_bus') as mock_bus:
            bar.cleanup()
            bar.cleanup()

        # Should still be 3, not 6
        assert mock_bus.unsubscribe.call_count == 3


class TestStatusBarSpaceWeatherAsync:
    """Verify space weather fetch runs in background thread."""

    def test_fetch_does_not_block(self):
        """_fetch_space_weather_async must return immediately."""
        bar = StatusBar(version="test")

        # Make the API call slow
        mock_data = MagicMock()
        mock_data.solar_flux = 150
        mock_data.k_index = 3

        def slow_fetch():
            time.sleep(1)
            return mock_data

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.side_effect = slow_fetch

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            start = time.time()
            bar._fetch_space_weather_async()
            elapsed = time.time() - start

        # Must return immediately (< 0.5s), not block for 1s
        assert elapsed < 0.5

    def test_no_stacking_fetches(self):
        """Concurrent calls must not stack fetch threads."""
        bar = StatusBar(version="test")
        call_count = 0
        lock = threading.Lock()

        def slow_fetch():
            nonlocal call_count
            with lock:
                call_count += 1
            time.sleep(0.5)
            data = MagicMock()
            data.solar_flux = 100
            data.k_index = 2
            return data

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.side_effect = slow_fetch

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            bar._fetch_space_weather_async()
            bar._fetch_space_weather_async()  # Should be skipped
            bar._fetch_space_weather_async()  # Should be skipped

        time.sleep(0.8)
        assert call_count == 1

    def test_fetch_resets_flag_on_error(self):
        """_space_weather_fetching flag must reset even on fetch error."""
        bar = StatusBar(version="test")

        MockAPI = MagicMock()
        MockAPI.return_value.get_current_conditions.side_effect = Exception("network error")

        with patch('status_bar.SpaceWeatherAPI', MockAPI):
            bar._fetch_space_weather_async()
            time.sleep(0.3)

        # Flag must be reset so next fetch can proceed
        assert not bar._space_weather_fetching


class TestHealthProbeWatchdog:
    """Verify health probe loop survives unexpected exceptions."""

    def test_probe_survives_check_exception(self):
        """Probe must continue running even if a check raises unexpectedly."""
        probe = ActiveHealthProbe(interval=1, fails=1, passes=1)
        call_count = 0

        def flaky_check():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated unexpected error")
            return HealthResult(healthy=True, reason="ok")

        probe.register_check("flaky", flaky_check)
        probe.start()
        time.sleep(3)  # Allow at least 2 check cycles
        probe.stop(timeout=2)

        # Must have run more than once (i.e., survived the first error)
        assert call_count >= 2

    def test_probe_stops_cleanly(self):
        """Probe must stop within timeout even after errors."""
        probe = ActiveHealthProbe(interval=1, fails=1, passes=1)

        def always_fail():
            raise ValueError("permanent failure")

        probe.register_check("broken", always_fail)
        probe.start()
        time.sleep(1.5)

        probe.stop(timeout=3)
        assert not probe._thread.is_alive()
