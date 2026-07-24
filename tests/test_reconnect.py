"""
Tests for exponential backoff reconnection strategy and slow start recovery.

Covers ReconnectConfig, ReconnectStrategy (backoff, jitter, retry logic,
execute_with_retry), and SlowStartRecovery (throughput ramping, adjusted delays).

Run: python3 -m pytest tests/test_reconnect.py -v
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.reconnect import (
    ReconnectConfig,
    ReconnectStrategy,
    SlowStartConfig,
    SlowStartRecovery,
)


# ---------------------------------------------------------------------------
# ReconnectConfig
# ---------------------------------------------------------------------------

class TestReconnectConfig:
    """Tests for ReconnectConfig defaults."""

    def test_defaults(self):
        c = ReconnectConfig()
        assert c.initial_delay == 1.0
        assert c.max_delay == 60.0
        assert c.multiplier == 2.0
        assert c.jitter == 0.1
        assert c.max_attempts == 10

    def test_custom_values(self):
        c = ReconnectConfig(initial_delay=0.5, max_delay=30.0, max_attempts=5)
        assert c.initial_delay == 0.5
        assert c.max_delay == 30.0
        assert c.max_attempts == 5


# ---------------------------------------------------------------------------
# ReconnectStrategy — delay calculation
# ---------------------------------------------------------------------------

class TestReconnectDelay:
    """Tests for exponential backoff delay calculation."""

    def test_first_attempt_delay(self):
        s = ReconnectStrategy(config=ReconnectConfig(jitter=0.0))
        delay = s.get_delay(attempt=0)
        assert delay == 1.0

    def test_exponential_growth(self):
        s = ReconnectStrategy(config=ReconnectConfig(jitter=0.0))
        assert s.get_delay(0) == 1.0
        assert s.get_delay(1) == 2.0
        assert s.get_delay(2) == 4.0
        assert s.get_delay(3) == 8.0

    def test_capped_at_max_delay(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=1.0, max_delay=10.0, multiplier=2.0, jitter=0.0
        ))
        assert s.get_delay(10) == 10.0
        assert s.get_delay(100) == 10.0

    def test_jitter_applies_variation(self):
        s = ReconnectStrategy(config=ReconnectConfig(jitter=0.5))
        delays = [s.get_delay(0) for _ in range(100)]
        assert min(delays) != max(delays), "Jitter should produce varying delays"
        # With jitter=0.5, delay range is [0.5, 1.5] for attempt 0
        assert all(0.0 <= d <= 2.0 for d in delays)

    def test_uses_current_attempts_when_none(self):
        s = ReconnectStrategy(config=ReconnectConfig(jitter=0.0))
        s.attempts = 3
        assert s.get_delay() == 8.0

    def test_delay_never_negative(self):
        s = ReconnectStrategy(config=ReconnectConfig(jitter=0.5))
        for _ in range(200):
            assert s.get_delay(0) >= 0.0

    def test_custom_multiplier(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=1.0, multiplier=3.0, jitter=0.0
        ))
        assert s.get_delay(0) == 1.0
        assert s.get_delay(1) == 3.0
        assert s.get_delay(2) == 9.0


# ---------------------------------------------------------------------------
# ReconnectStrategy — state management
# ---------------------------------------------------------------------------

class TestReconnectState:
    """Tests for attempt tracking and retry logic."""

    def test_initial_attempts_zero(self):
        s = ReconnectStrategy()
        assert s.attempts == 0

    def test_record_failure_increments(self):
        s = ReconnectStrategy()
        s.record_failure()
        assert s.attempts == 1
        s.record_failure()
        assert s.attempts == 2

    def test_record_success_resets(self):
        s = ReconnectStrategy()
        s.record_failure()
        s.record_failure()
        s.record_success()
        assert s.attempts == 0

    def test_reset(self):
        s = ReconnectStrategy()
        s.attempts = 5
        s.reset()
        assert s.attempts == 0

    def test_should_retry_true(self):
        s = ReconnectStrategy(config=ReconnectConfig(max_attempts=3))
        assert s.should_retry() is True
        s.record_failure()
        assert s.should_retry() is True
        s.record_failure()
        assert s.should_retry() is True

    def test_should_retry_false_at_max(self):
        s = ReconnectStrategy(config=ReconnectConfig(max_attempts=2))
        s.record_failure()
        s.record_failure()
        assert s.should_retry() is False

    def test_should_retry_resets_after_success(self):
        s = ReconnectStrategy(config=ReconnectConfig(max_attempts=2))
        s.record_failure()
        s.record_failure()
        assert s.should_retry() is False
        s.record_success()
        assert s.should_retry() is True


# ---------------------------------------------------------------------------
# ReconnectStrategy — wait
# ---------------------------------------------------------------------------

class TestReconnectWait:
    """Tests for wait method."""

    def test_wait_returns_delay(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, jitter=0.0
        ))
        delay = s.wait()
        assert abs(delay - 0.01) < 0.005

    def test_wait_interruptible_by_stop_event(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=10.0, jitter=0.0
        ))
        stop = threading.Event()
        stop.set()  # Pre-set so wait returns immediately
        start = time.time()
        s.wait(stop_event=stop)
        elapsed = time.time() - start
        assert elapsed < 1.0, "Stop event should interrupt wait immediately"


# ---------------------------------------------------------------------------
# ReconnectStrategy — execute_with_retry
# ---------------------------------------------------------------------------

class TestExecuteWithRetry:
    """Tests for execute_with_retry."""

    def test_success_first_try(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, jitter=0.0
        ))
        result = s.execute_with_retry(lambda: 42)
        assert result == 42
        assert s.attempts == 0

    def test_success_after_failures(self):
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("not yet")
            return "ok"

        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, max_attempts=5, jitter=0.0
        ))
        result = s.execute_with_retry(flaky)
        assert result == "ok"
        assert s.attempts == 0  # Reset after success

    def test_all_retries_exhausted(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, max_attempts=3, jitter=0.0
        ))
        with pytest.raises(ConnectionError, match="fail"):
            s.execute_with_retry(lambda: (_ for _ in ()).throw(ConnectionError("fail")))

    def test_on_failure_callback_called(self):
        cb = MagicMock()
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, max_attempts=2, jitter=0.0
        ))
        with pytest.raises(RuntimeError):
            s.execute_with_retry(
                lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                on_failure=cb,
            )
        assert cb.call_count == 2

    def test_stop_event_interrupts(self):
        stop = threading.Event()
        stop.set()
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, max_attempts=10, jitter=0.0
        ))
        with pytest.raises(ConnectionError, match="interrupted"):
            s.execute_with_retry(
                lambda: (_ for _ in ()).throw(RuntimeError("fail")),
                stop_event=stop,
            )

    def test_preserves_exception_type(self):
        s = ReconnectStrategy(config=ReconnectConfig(
            initial_delay=0.01, max_attempts=1, jitter=0.0
        ))
        with pytest.raises(ValueError, match="specific"):
            s.execute_with_retry(
                lambda: (_ for _ in ()).throw(ValueError("specific"))
            )


# ---------------------------------------------------------------------------
# Factory methods
# ---------------------------------------------------------------------------

class TestFactoryMethods:
    """Tests for factory class methods."""

    def test_for_meshtastic(self):
        s = ReconnectStrategy.for_meshtastic()
        assert s.config.initial_delay == 1.0
        assert s.config.max_delay == 30.0
        assert s.config.max_attempts == 10

    def test_for_rns(self):
        s = ReconnectStrategy.for_rns()
        assert s.config.initial_delay == 2.0
        assert s.config.max_delay == 60.0
        assert s.config.max_attempts == 15
        assert s.config.jitter == 0.15


# ---------------------------------------------------------------------------
# SlowStartConfig
# ---------------------------------------------------------------------------

class TestSlowStartConfig:
    """Tests for SlowStartConfig defaults."""

    def test_defaults(self):
        c = SlowStartConfig()
        assert c.slow_start_seconds == 30.0
        assert c.min_multiplier == 0.1
        assert c.max_multiplier == 1.0


# ---------------------------------------------------------------------------
# SlowStartRecovery — basic operations
# ---------------------------------------------------------------------------

class TestSlowStartRecovery:
    """Tests for slow start throughput ramping."""

    def test_not_recovering_initially(self):
        ss = SlowStartRecovery()
        assert ss.is_recovering() is False

    def test_full_multiplier_when_not_recovering(self):
        ss = SlowStartRecovery()
        assert ss.get_throughput_multiplier() == 1.0

    def test_start_recovery_sets_recovering(self):
        ss = SlowStartRecovery(config=SlowStartConfig(slow_start_seconds=10.0))
        ss.start_recovery()
        assert ss.is_recovering() is True

    def test_multiplier_starts_low(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=10.0, min_multiplier=0.1
        ))
        ss.start_recovery()
        m = ss.get_throughput_multiplier()
        # Should be near min_multiplier right after start
        assert m < 0.3

    def test_multiplier_increases_over_time(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.1, min_multiplier=0.1
        ))
        ss.start_recovery()
        m1 = ss.get_throughput_multiplier()
        time.sleep(0.06)
        m2 = ss.get_throughput_multiplier()
        assert m2 > m1, "Multiplier should increase over time"

    def test_multiplier_returns_max_after_recovery(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.05
        ))
        ss.start_recovery()
        time.sleep(0.1)
        assert ss.get_throughput_multiplier() == 1.0
        assert ss.is_recovering() is False

    def test_end_recovery_manual(self):
        ss = SlowStartRecovery(config=SlowStartConfig(slow_start_seconds=60.0))
        ss.start_recovery()
        assert ss.is_recovering() is True
        ss.end_recovery()
        assert ss.is_recovering() is False
        assert ss.get_throughput_multiplier() == 1.0

    def test_end_recovery_when_not_recovering(self):
        ss = SlowStartRecovery()
        ss.end_recovery()  # Should not raise

    def test_progress_none_when_not_recovering(self):
        ss = SlowStartRecovery()
        assert ss.get_recovery_progress() is None

    def test_progress_increases(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.1
        ))
        ss.start_recovery()
        p1 = ss.get_recovery_progress()
        assert p1 is not None and p1 >= 0.0
        time.sleep(0.06)
        p2 = ss.get_recovery_progress()
        assert p2 is not None and p2 > p1

    def test_progress_100_after_recovery(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.02
        ))
        ss.start_recovery()
        time.sleep(0.05)
        p = ss.get_recovery_progress()
        assert p == 100.0


# ---------------------------------------------------------------------------
# SlowStartRecovery — adjusted delay
# ---------------------------------------------------------------------------

class TestSlowStartAdjustedDelay:
    """Tests for get_adjusted_delay."""

    def test_no_recovery_returns_base(self):
        ss = SlowStartRecovery()
        assert ss.get_adjusted_delay(1.0) == 1.0

    def test_during_recovery_delay_is_longer(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=10.0, min_multiplier=0.1
        ))
        ss.start_recovery()
        delay = ss.get_adjusted_delay(1.0)
        assert delay > 1.0, "Delay should be longer during recovery"

    def test_zero_multiplier_safety_cap(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            min_multiplier=0.0, slow_start_seconds=10.0
        ))
        # Manually set recovery start so elapsed ~0 and multiplier is
        # effectively 0 (min_multiplier=0.0 + 0*progress). Anchor on the same
        # monotonic clock the ramp measures against (Pri-6), not wall-clock.
        ss._recovery_start = time.monotonic()
        # At elapsed ~0, multiplier = min_multiplier + (1.0-0.0)*0 = 0.0
        # The get_adjusted_delay should hit the safety cap
        # But there's always a tiny elapsed time, so multiplier > 0
        # Test that delay is significantly larger than base_delay
        delay = ss.get_adjusted_delay(1.0)
        assert delay > 5.0, "Near-zero multiplier should produce very large delay"

    def test_after_recovery_delay_normal(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.02
        ))
        ss.start_recovery()
        time.sleep(0.05)
        assert ss.get_adjusted_delay(1.0) == 1.0


# ---------------------------------------------------------------------------
# SlowStartRecovery — factory methods
# ---------------------------------------------------------------------------

class TestSlowStartFactory:
    """Tests for slow start factory methods."""

    def test_for_meshtastic(self):
        ss = SlowStartRecovery.for_meshtastic()
        assert ss.config.slow_start_seconds == 30.0
        assert ss.config.min_multiplier == 0.1

    def test_for_rns(self):
        ss = SlowStartRecovery.for_rns()
        assert ss.config.slow_start_seconds == 15.0
        assert ss.config.min_multiplier == 0.2


# ---------------------------------------------------------------------------
# SlowStartRecovery — monotonic clock-step immunity (gateway review Pri-6)
# ---------------------------------------------------------------------------

class TestSlowStartMonotonicClockSteps:
    """Pri-6 (gateway review 2026-07-23): the slow-start ramp is a DURATION and
    must measure on time.monotonic(), so a wall-clock NTP step cannot end
    recovery early (blasting a just-recovered radio — the RATE_LIMIT burst) or
    drive the multiplier sub-min/negative and stuck-recovering
    (honest_failure_modes #6, the #74 class). We drive monotonic and the wall
    clock independently to prove the ramp follows monotonic only."""

    @staticmethod
    def _cfg():
        return SlowStartConfig(slow_start_seconds=30.0,
                               min_multiplier=0.1, max_multiplier=1.0)

    @patch("gateway.reconnect.time")
    def test_forward_wallclock_step_does_not_end_recovery_early(self, mt):
        mono = {"t": 1000.0}
        mt.monotonic.side_effect = lambda: mono["t"]
        mt.time.side_effect = lambda: 5_000.0  # wall clock, must be ignored
        ss = SlowStartRecovery(config=self._cfg())
        ss.start_recovery()                       # anchor at monotonic 1000
        # A +1h NTP forward step lands on the WALL clock; monotonic advances 3s.
        mt.time.side_effect = lambda: 5_000.0 + 3600.0
        mono["t"] = 1003.0
        assert ss.is_recovering() is True          # not falsely completed
        m = ss.get_throughput_multiplier()
        # 3/30 progress → 0.1 + 0.9*0.1 = 0.19, decidedly not full throughput.
        assert 0.15 < m < 0.25
        assert 0 < ss.get_recovery_progress() < 20

    @patch("gateway.reconnect.time")
    def test_backward_wallclock_step_keeps_multiplier_bounded(self, mt):
        mono = {"t": 2000.0}
        mt.monotonic.side_effect = lambda: mono["t"]
        mt.time.side_effect = lambda: 9_000.0
        ss = SlowStartRecovery(config=self._cfg())
        ss.start_recovery()
        # Wall clock jumps BACKWARD an hour; monotonic still moves forward 6s.
        mt.time.side_effect = lambda: 9_000.0 - 3600.0
        mono["t"] = 2006.0
        m = ss.get_throughput_multiplier()
        # Bounded in [min, max]; never negative, never stuck below min.
        assert ss.config.min_multiplier <= m <= ss.config.max_multiplier
        assert ss.is_recovering() is True

    @patch("gateway.reconnect.time")
    def test_recovery_completes_on_real_monotonic_elapsed(self, mt):
        mono = {"t": 500.0}
        mt.monotonic.side_effect = lambda: mono["t"]
        mt.time.side_effect = lambda: 500.0       # wall clock frozen entirely
        ss = SlowStartRecovery(config=self._cfg())
        ss.start_recovery()
        # Monotonic advances past the ramp with NO wall-clock movement at all —
        # proves completion is driven by monotonic, not time.time().
        mono["t"] = 531.0                          # 31s ≥ 30s
        assert ss.get_throughput_multiplier() == 1.0
        assert ss.is_recovering() is False
        assert ss._recovery_start is None          # completion cleared the anchor


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Verify thread safety of SlowStartRecovery."""

    def test_concurrent_access(self):
        ss = SlowStartRecovery(config=SlowStartConfig(
            slow_start_seconds=0.2
        ))
        ss.start_recovery()
        errors = []

        def reader():
            try:
                for _ in range(50):
                    ss.get_throughput_multiplier()
                    ss.is_recovering()
                    ss.get_recovery_progress()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"
