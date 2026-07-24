"""Tests for BridgeHealthMonitor, error classification, and SubsystemState."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import time
import threading
from unittest.mock import patch

import pytest

from gateway.bridge_health import (
    BridgeHealthMonitor,
    DeliveryTracker,
    DeliveryRecord,
    classify_error,
    ConnectionEvent,
    ErrorEvent,
    SubsystemState,
    BridgeStatus,
)


class TestClassifyError:
    """Test error classification logic."""

    def test_transient_connection_reset(self):
        assert classify_error(ConnectionResetError("Connection reset by peer")) == "transient"

    def test_transient_broken_pipe(self):
        assert classify_error(BrokenPipeError("Broken pipe")) == "transient"

    def test_transient_timeout(self):
        assert classify_error(TimeoutError("Connection timed out")) == "transient"

    def test_transient_connection_refused(self):
        assert classify_error(ConnectionRefusedError("Connection refused")) == "transient"

    def test_transient_os_error(self):
        assert classify_error(OSError("Network unreachable")) == "transient"

    def test_permanent_signal_error(self):
        err = RuntimeError("signal only works in main thread")
        assert classify_error(err) == "permanent"

    def test_permanent_reinitialise(self):
        err = Exception("Cannot reinitialise RNS")
        assert classify_error(err) == "permanent"

    def test_permanent_permission_denied(self):
        err = PermissionError("Permission denied")
        assert classify_error(err) == "permanent"

    def test_unknown_generic_error(self):
        err = ValueError("Something unexpected")
        assert classify_error(err) == "unknown"

    def test_transient_address_in_use(self):
        err = OSError("Address already in use")
        assert classify_error(err) == "transient"


class TestBridgeHealthMonitor:
    """Test health monitoring and metrics."""

    def test_initial_state(self):
        """Monitor starts with no connections and zero counts."""
        health = BridgeHealthMonitor()
        summary = health.get_summary()

        assert summary["connections"]["meshtastic"]["connected"] is False
        assert summary["connections"]["rns"]["connected"] is False
        assert summary["messages"]["mesh_to_rns"] == 0
        assert summary["messages"]["rns_to_mesh"] == 0

    def test_connection_event_connected(self):
        """Records connection and updates state."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        summary = health.get_summary()
        assert summary["connections"]["meshtastic"]["connected"] is True
        assert summary["connections"]["meshtastic"]["reconnect_count"] == 1

    def test_connection_event_disconnected(self):
        """Records disconnection and tracks uptime."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        time.sleep(0.1)
        health.record_connection_event("meshtastic", "disconnected", "test")

        summary = health.get_summary()
        assert summary["connections"]["meshtastic"]["connected"] is False
        assert summary["connections"]["meshtastic"]["last_disconnected"] is not None

    def test_multiple_reconnections(self):
        """Tracks reconnection count."""
        health = BridgeHealthMonitor()

        for _ in range(3):
            health.record_connection_event("rns", "connected")
            health.record_connection_event("rns", "disconnected")

        summary = health.get_summary()
        assert summary["connections"]["rns"]["reconnect_count"] == 3

    def test_message_sent_counting(self):
        """Counts sent messages by direction."""
        health = BridgeHealthMonitor()

        health.record_message_sent("mesh_to_rns")
        health.record_message_sent("mesh_to_rns")
        health.record_message_sent("rns_to_mesh")

        summary = health.get_summary()
        assert summary["messages"]["mesh_to_rns"] == 2
        assert summary["messages"]["rns_to_mesh"] == 1

    def test_message_failed_counting(self):
        """Counts failed messages and requeue status."""
        health = BridgeHealthMonitor()

        health.record_message_failed("mesh_to_rns", requeued=True)
        health.record_message_failed("mesh_to_rns", requeued=False)
        health.record_message_failed("rns_to_mesh", requeued=True)

        summary = health.get_summary()
        assert summary["messages"]["failed_mesh_to_rns"] == 2
        assert summary["messages"]["failed_rns_to_mesh"] == 1
        assert summary["messages"]["requeued"] == 2

    def test_record_error_classification(self):
        """Records and classifies errors."""
        health = BridgeHealthMonitor()

        cat1 = health.record_error("meshtastic", ConnectionResetError("reset"))
        cat2 = health.record_error("rns", RuntimeError("signal only works in main thread"))

        assert cat1 == "transient"
        assert cat2 == "permanent"

    def test_error_rate_windowed(self):
        """Error rate only counts recent errors."""
        health = BridgeHealthMonitor()

        health.record_error("meshtastic", OSError("timeout"))
        health.record_error("meshtastic", OSError("reset"))

        errors = health.get_error_rate(window_seconds=60)
        assert errors["transient"] == 2
        assert errors["permanent"] == 0

    def test_message_rate_calculation(self):
        """Message rate calculated over window."""
        health = BridgeHealthMonitor()

        for _ in range(10):
            health.record_message_sent("mesh_to_rns")

        rate = health.get_message_rate(window_seconds=60)
        assert rate == 10.0  # 10 messages in 60s = 10/min

    def test_uptime_percent_connected(self):
        """Uptime includes current connected time."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        time.sleep(0.1)

        uptime = health.get_uptime_percent("meshtastic")
        assert uptime > 0  # Should be positive

    def test_uptime_percent_never_connected(self):
        """Uptime is 0% for never-connected service."""
        health = BridgeHealthMonitor()
        time.sleep(0.05)

        uptime = health.get_uptime_percent("rns")
        assert uptime == 0.0

    def test_is_healthy_when_connected(self):
        """Bridge is healthy with active connection and low errors."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        assert health.is_healthy() is True

    def test_is_healthy_when_disconnected(self):
        """Bridge is unhealthy when nothing connected."""
        health = BridgeHealthMonitor()
        assert health.is_healthy() is False

    def test_is_healthy_high_error_rate(self):
        """Bridge is unhealthy with excessive errors."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        # Flood with errors
        for _ in range(15):
            health.record_error("meshtastic", OSError("fail"))

        assert health.is_healthy() is False

    def test_window_size_limits_memory(self):
        """Rolling windows don't grow unbounded."""
        health = BridgeHealthMonitor(window_size=10)

        for i in range(100):
            health.record_connection_event("meshtastic", "retry")
            health.record_error("meshtastic", OSError(f"err {i}"))

        assert len(health._connection_events) <= 10
        assert len(health._error_events) <= 10

    def test_thread_safety(self):
        """Concurrent access doesn't corrupt state."""
        health = BridgeHealthMonitor()
        errors = []

        def writer(prefix):
            try:
                for i in range(50):
                    health.record_message_sent("mesh_to_rns")
                    health.record_connection_event("meshtastic", "retry")
                    health.record_error("rns", OSError(f"{prefix}_{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        summary = health.get_summary()
        assert summary["messages"]["mesh_to_rns"] == 250  # 5 threads * 50

    def test_summary_has_all_fields(self):
        """Summary includes all expected metric sections."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        health.record_message_sent("mesh_to_rns")

        summary = health.get_summary()

        assert "uptime_seconds" in summary
        assert "connections" in summary
        assert "messages" in summary
        assert "errors" in summary
        assert "meshtastic" in summary["connections"]
        assert "rns" in summary["connections"]
        assert "rate_per_min" in summary["messages"]


class TestDeliveryTracker:
    """Tests for LXMF delivery confirmation tracking."""

    def test_track_message(self):
        """Tracking a message records it as pending."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd\xef\x01', "Hello world")

        stats = tracker.get_stats()
        assert stats["total_sent"] == 1
        assert stats["pending_count"] == 1
        assert stats["confirmed"] == 0

    def test_confirm_delivery(self):
        """Confirming delivery updates stats."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd\xef\x01', "Test")

        result = tracker.confirm_delivery("msg-1")
        assert result is True

        stats = tracker.get_stats()
        assert stats["confirmed"] == 1
        assert stats["pending_count"] == 0
        assert stats["confirmation_rate_pct"] == 100.0

    def test_confirm_unknown_message(self):
        """Confirming unknown message returns False."""
        tracker = DeliveryTracker()
        assert tracker.confirm_delivery("nonexistent") is False

    def test_confirm_failure(self):
        """Recording failure updates stats."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd', "Test")

        result = tracker.confirm_failure("msg-1", "no_path")
        assert result is True

        stats = tracker.get_stats()
        assert stats["failed"] == 1
        assert stats["pending_count"] == 0

    def test_confirm_failure_unknown_returns_false(self):
        """Failure of unknown message returns False."""
        tracker = DeliveryTracker()
        assert tracker.confirm_failure("nonexistent", "reason") is False

    def test_check_timeouts(self):
        """Messages past timeout are marked failed."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd', "Old message")

        # Backdate the record
        with tracker._lock:
            tracker._pending["msg-1"].sent_at = time.time() - 400

        timed_out = tracker.check_timeouts()
        assert timed_out == 1

        stats = tracker.get_stats()
        assert stats["timed_out"] == 1
        assert stats["pending_count"] == 0

    def test_check_timeouts_ignores_recent(self):
        """Recent messages are not timed out."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd', "Recent")

        timed_out = tracker.check_timeouts()
        assert timed_out == 0
        assert tracker.get_stats()["pending_count"] == 1

    def test_get_pending(self):
        """Get pending returns current unconfirmed deliveries."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd\xef\x01\x23\x45\x67\x89', "First")
        tracker.track_message("msg-2", b'\x11\x22\x33\x44\x55\x66\x77\x88', "Second")

        pending = tracker.get_pending()
        assert len(pending) == 2
        assert all("msg_id" in p for p in pending)
        assert all("age_seconds" in p for p in pending)

    def test_get_recent_deliveries(self):
        """Recent deliveries returns confirmed/failed history."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xab\xcd', "First")
        tracker.track_message("msg-2", b'\xef\x01', "Second")

        tracker.confirm_delivery("msg-1")
        tracker.confirm_failure("msg-2", "timeout")

        recent = tracker.get_recent_deliveries()
        assert len(recent) == 2
        # Most recent first
        assert recent[0]["status"] == "failed"
        assert recent[0]["failure_reason"] == "timeout"
        assert recent[1]["status"] == "delivered"
        assert recent[1]["latency_seconds"] is not None

    def test_confirmation_rate(self):
        """Confirmation rate calculated correctly."""
        tracker = DeliveryTracker()
        for i in range(10):
            tracker.track_message(f"msg-{i}", b'\x00' * 8, f"Msg {i}")

        # Confirm 7 out of 10
        for i in range(7):
            tracker.confirm_delivery(f"msg-{i}")
        for i in range(7, 10):
            tracker.confirm_failure(f"msg-{i}", "failed")

        stats = tracker.get_stats()
        assert stats["confirmation_rate_pct"] == 70.0

    def test_destination_hash_as_hex(self):
        """Destination hash stored as hex string."""
        tracker = DeliveryTracker()
        tracker.track_message("msg-1", b'\xde\xad\xbe\xef', "Test")

        pending = tracker.get_pending()
        assert pending[0]["destination"] == "deadbeef"

    def test_content_preview_truncated(self):
        """Long content is truncated to 50 chars."""
        tracker = DeliveryTracker()
        long_msg = "A" * 200
        tracker.track_message("msg-1", b'\xab\xcd', long_msg)

        with tracker._lock:
            assert len(tracker._pending["msg-1"].content_preview) == 50

    def test_thread_safety(self):
        """Concurrent tracking doesn't corrupt state."""
        tracker = DeliveryTracker()
        errors = []

        def worker(prefix):
            try:
                for i in range(50):
                    msg_id = f"{prefix}-{i}"
                    tracker.track_message(msg_id, b'\x00' * 8, f"msg {i}")
                    if i % 2 == 0:
                        tracker.confirm_delivery(msg_id)
                    else:
                        tracker.confirm_failure(msg_id, "test")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stats = tracker.get_stats()
        assert stats["total_sent"] == 250
        assert stats["confirmed"] + stats["failed"] == 250
        assert stats["pending_count"] == 0

    def test_max_history_bounded(self):
        """History deque is bounded to MAX_HISTORY."""
        tracker = DeliveryTracker()

        for i in range(600):
            msg_id = f"msg-{i}"
            tracker.track_message(msg_id, b'\x00' * 8, "test")
            tracker.confirm_delivery(msg_id)

        # History should be capped
        with tracker._lock:
            assert len(tracker._history) <= tracker.MAX_HISTORY


# ---------------------------------------------------------------------------
# SubsystemState (Phase 2: Circuit Breakers)
# ---------------------------------------------------------------------------

class TestSubsystemState:
    """Test SubsystemState enum and BridgeHealthMonitor subsystem tracking."""

    def test_enum_values(self):
        """SubsystemState has correct values."""
        assert SubsystemState.HEALTHY.value == "healthy"
        assert SubsystemState.DEGRADED.value == "degraded"
        assert SubsystemState.DISCONNECTED.value == "disconnected"
        assert SubsystemState.DISABLED.value == "disabled"

    def test_initial_state_is_disconnected(self):
        """Subsystems start in DISCONNECTED state."""
        health = BridgeHealthMonitor()
        assert health.get_subsystem_state("meshtastic") == SubsystemState.DISCONNECTED
        assert health.get_subsystem_state("rns") == SubsystemState.DISCONNECTED

    def test_set_subsystem_state(self):
        """Setting state returns previous state."""
        health = BridgeHealthMonitor()
        old = health.set_subsystem_state("rns", SubsystemState.HEALTHY)
        assert old == SubsystemState.DISCONNECTED
        assert health.get_subsystem_state("rns") == SubsystemState.HEALTHY

    def test_set_subsystem_state_same_value(self):
        """Setting same state is a no-op but returns same value."""
        health = BridgeHealthMonitor()
        health.set_subsystem_state("rns", SubsystemState.HEALTHY)
        old = health.set_subsystem_state("rns", SubsystemState.HEALTHY)
        assert old == SubsystemState.HEALTHY

    def test_unknown_subsystem_returns_disconnected(self):
        """Unknown subsystem defaults to DISCONNECTED."""
        health = BridgeHealthMonitor()
        assert health.get_subsystem_state("unknown") == SubsystemState.DISCONNECTED

    def test_set_unknown_subsystem_returns_none(self):
        """Setting unknown subsystem returns None."""
        health = BridgeHealthMonitor()
        result = health.set_subsystem_state("unknown", SubsystemState.HEALTHY)
        assert result is None

    def test_get_subsystem_states_dict(self):
        """get_subsystem_states returns string-valued dict."""
        health = BridgeHealthMonitor()
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        health.set_subsystem_state("rns", SubsystemState.DISCONNECTED)
        states = health.get_subsystem_states()
        assert states == {"meshtastic": "healthy", "rns": "disconnected"}

    def test_subsystem_states_in_summary(self):
        """get_summary includes subsystem states."""
        health = BridgeHealthMonitor()
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        health.set_subsystem_state("rns", SubsystemState.DEGRADED)
        summary = health.get_summary()
        assert "subsystems" in summary
        assert summary["subsystems"]["meshtastic"] == "healthy"
        assert summary["subsystems"]["rns"] == "degraded"

    def test_record_message_queued_degraded(self):
        """Track messages queued during degraded state."""
        health = BridgeHealthMonitor()
        assert health.get_degraded_queue_count() == 0
        health.record_message_queued_degraded()
        health.record_message_queued_degraded()
        assert health.get_degraded_queue_count() == 2

    def test_messages_queued_degraded_in_summary(self):
        """Summary includes queued-during-degraded count."""
        health = BridgeHealthMonitor()
        health.record_message_queued_degraded()
        summary = health.get_summary()
        assert summary["messages_queued_degraded"] == 1

    def test_get_bridge_status_detailed(self):
        """Detailed status includes subsystem states and degraded reason."""
        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        detailed = health.get_bridge_status_detailed()
        assert "bridge_status" in detailed
        assert "subsystems" in detailed
        assert "degraded_reason" in detailed
        assert detailed["subsystems"]["meshtastic"] == "healthy"

    def test_independent_subsystem_lifecycle(self):
        """Each subsystem transitions independently."""
        health = BridgeHealthMonitor()
        # Mesh comes up
        health.set_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        # RNS still down
        assert health.get_subsystem_state("rns") == SubsystemState.DISCONNECTED
        # RNS comes up
        health.set_subsystem_state("rns", SubsystemState.HEALTHY)
        assert health.get_subsystem_state("meshtastic") == SubsystemState.HEALTHY
        assert health.get_subsystem_state("rns") == SubsystemState.HEALTHY
        # Mesh goes down
        health.set_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)
        assert health.get_subsystem_state("rns") == SubsystemState.HEALTHY
        assert health.get_subsystem_state("meshtastic") == SubsystemState.DISCONNECTED

    def test_disabled_state(self):
        """DISABLED state for permanently failed subsystems."""
        health = BridgeHealthMonitor()
        health.set_subsystem_state("rns", SubsystemState.DISABLED)
        assert health.get_subsystem_state("rns") == SubsystemState.DISABLED
        states = health.get_subsystem_states()
        assert states["rns"] == "disabled"

    def test_thread_safety(self):
        """Concurrent state updates don't corrupt data."""
        health = BridgeHealthMonitor()
        states = [SubsystemState.HEALTHY, SubsystemState.DISCONNECTED,
                  SubsystemState.DEGRADED, SubsystemState.DISABLED]
        errors = []

        def toggle(subsystem, iterations):
            try:
                for i in range(iterations):
                    health.set_subsystem_state(subsystem, states[i % len(states)])
                    health.get_subsystem_state(subsystem)
                    health.get_subsystem_states()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=toggle, args=("meshtastic", 100)),
            threading.Thread(target=toggle, args=("rns", 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0


class TestConfirmationRateSentinelIssue74:
    """Zero traffic must read as 'no data' (None), not '0% = total
    failure' — aligns with the DeliveryCounters sentinel (rate None
    iff sent == 0). A dashboard polling at startup must not see 0%."""

    def test_confirmation_rate_none_at_zero_traffic(self):
        tracker = DeliveryTracker()
        stats = tracker.get_stats()
        assert stats["confirmation_rate_pct"] is None

    def test_confirmation_rate_zero_is_still_zero_with_traffic(self):
        """Real 0% (sends but no confirms) stays 0.0 — only the
        no-data case maps to None."""
        tracker = DeliveryTracker()
        tracker.track_message("m1", b"\x00" * 8, "Msg")
        stats = tracker.get_stats()
        assert stats["confirmation_rate_pct"] == 0.0


class TestUptimeClockStepClampPri10:
    """Pri-10 (gateway review 2026-07-23): uptime accounting is wall-clock; a
    backward NTP step must not subtract from accumulated uptime or push the
    percent out of [0,100] (honest_failure_modes #6 clamp; display-only)."""

    @patch("gateway.bridge_health.time")
    def test_backward_step_does_not_reduce_uptime(self, mt):
        clock = {"t": 1000.0}
        mt.time.side_effect = lambda: clock["t"]
        h = BridgeHealthMonitor()                    # _start_time @1000
        h.record_connection_event("meshtastic", "connected")   # connected @1000
        clock["t"] = 1000.0 - 3600.0                 # NTP jumps BACKWARD 1h
        h.record_connection_event("meshtastic", "disconnected")
        assert h._uptime_seconds["meshtastic"] >= 0.0          # not negative
        assert 0.0 <= h.get_uptime_percent("meshtastic") <= 100.0
