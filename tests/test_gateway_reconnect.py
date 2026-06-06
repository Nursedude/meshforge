"""
Tests for gateway exponential backoff reconnection.

Tests the ReconnectStrategy class that implements exponential backoff
with jitter for reliable reconnection to Meshtastic and RNS services.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from gateway.reconnect import ReconnectStrategy, ReconnectConfig


@pytest.fixture
def default_config():
    """Default reconnect configuration."""
    return ReconnectConfig(
        initial_delay=1.0,
        max_delay=60.0,
        multiplier=2.0,
        jitter=0.1,
        max_attempts=10
    )


@pytest.fixture
def strategy(default_config):
    """Create a reconnect strategy with default config."""
    return ReconnectStrategy(default_config)


class TestReconnectConfig:
    """Test ReconnectConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ReconnectConfig()
        assert config.initial_delay == 1.0
        assert config.max_delay == 60.0
        assert config.multiplier == 2.0
        assert 0.0 <= config.jitter <= 0.5
        assert config.max_attempts == 10

    def test_custom_values(self):
        """Test custom configuration values."""
        config = ReconnectConfig(
            initial_delay=0.5,
            max_delay=30.0,
            multiplier=1.5,
            jitter=0.2,
            max_attempts=5
        )
        assert config.initial_delay == 0.5
        assert config.max_delay == 30.0
        assert config.multiplier == 1.5
        assert config.jitter == 0.2
        assert config.max_attempts == 5


class TestReconnectStrategy:
    """Test ReconnectStrategy class."""

    def test_initial_delay(self, strategy):
        """First attempt should use initial delay."""
        delay = strategy.get_delay(attempt=0)
        # Should be close to initial_delay (with jitter)
        assert 0.9 <= delay <= 1.1

    def test_exponential_increase(self, strategy):
        """Delay should increase exponentially."""
        delay1 = strategy.get_delay(attempt=0)
        delay2 = strategy.get_delay(attempt=1)
        delay3 = strategy.get_delay(attempt=2)

        # Each delay should roughly double (with jitter)
        assert delay2 > delay1
        assert delay3 > delay2
        # delay2 should be ~2x delay1 (multiplier=2.0)
        assert 1.5 < delay2 / delay1 < 2.5

    def test_max_delay_cap(self, strategy):
        """Delay should not exceed max_delay."""
        # After many attempts, should hit the cap
        delay = strategy.get_delay(attempt=20)
        # Should be capped at max_delay (60.0) plus jitter
        assert delay <= 66.0  # 60 + 10% jitter

    def test_jitter_adds_randomness(self, strategy):
        """Jitter should add randomness to delays."""
        delays = [strategy.get_delay(attempt=0) for _ in range(10)]
        # Not all delays should be identical
        assert len(set(delays)) > 1

    def test_reset_clears_attempts(self, strategy):
        """Reset should clear attempt counter."""
        strategy.record_failure()
        strategy.record_failure()
        assert strategy.attempts == 2

        strategy.reset()
        assert strategy.attempts == 0

    def test_record_failure_increments(self, strategy):
        """record_failure should increment attempt counter."""
        assert strategy.attempts == 0
        strategy.record_failure()
        assert strategy.attempts == 1
        strategy.record_failure()
        assert strategy.attempts == 2

    def test_record_success_resets(self, strategy):
        """record_success should reset attempt counter."""
        strategy.record_failure()
        strategy.record_failure()
        assert strategy.attempts == 2

        strategy.record_success()
        assert strategy.attempts == 0

    def test_should_retry_within_max(self, strategy):
        """should_retry returns True within max_attempts."""
        for _ in range(9):
            assert strategy.should_retry()
            strategy.record_failure()
        assert strategy.should_retry()  # 10th attempt allowed

    def test_should_retry_exceeds_max(self, strategy):
        """should_retry returns False after max_attempts."""
        for _ in range(10):
            strategy.record_failure()
        assert not strategy.should_retry()  # 11th attempt denied


class TestReconnectStrategyIntegration:
    """Integration tests for reconnect behavior."""

    def test_wait_returns_actual_delay(self, strategy):
        """wait() should block for (and return) the calculated delay.

        Issue #74: wait() is Event.wait-based now (no time.sleep) —
        patch the never-set fallback event with a pre-set one so the
        test is instant while still asserting the computed delay."""
        import threading
        preset = threading.Event()
        preset.set()
        with patch('gateway.reconnect._NEVER_SET', preset):
            delay = strategy.wait()
        assert 0.9 <= delay <= 1.1

    def test_execute_with_retry_succeeds(self, strategy):
        """execute_with_retry should succeed when function succeeds."""
        mock_func = Mock(return_value="success")

        result = strategy.execute_with_retry(mock_func)

        assert result == "success"
        mock_func.assert_called_once()
        assert strategy.attempts == 0  # Reset after success

    def test_execute_with_retry_retries_on_failure(self, strategy):
        """execute_with_retry should retry on failure."""
        # Fail twice, then succeed
        mock_func = Mock(side_effect=[Exception("fail1"), Exception("fail2"), "success"])

        with patch.object(strategy, 'wait'):  # Don't actually wait
            result = strategy.execute_with_retry(mock_func)

        assert result == "success"
        assert mock_func.call_count == 3
        assert strategy.attempts == 0  # Reset after success

    def test_execute_with_retry_gives_up(self, strategy):
        """execute_with_retry should give up after max_attempts."""
        mock_func = Mock(side_effect=Exception("always fails"))

        with patch.object(strategy, 'wait'):  # Don't actually wait
            with pytest.raises(Exception, match="always fails"):
                strategy.execute_with_retry(mock_func)

        assert mock_func.call_count == 10  # max_attempts


class TestMeshtasticReconnect:
    """Test Meshtastic-specific reconnection."""

    def test_meshtastic_default_config(self):
        """Meshtastic should use appropriate default config."""
        strategy = ReconnectStrategy.for_meshtastic()
        config = strategy.config

        # Meshtastic reconnect should be quick initially
        assert config.initial_delay <= 2.0
        # But not hammer the connection
        assert config.max_delay >= 30.0


class TestRNSReconnect:
    """Test RNS-specific reconnection."""

    def test_rns_default_config(self):
        """RNS should use appropriate default config."""
        strategy = ReconnectStrategy.for_rns()
        config = strategy.config

        # RNS may need longer initial delay for transport init
        assert config.initial_delay >= 1.0
        # Allow longer max for network issues
        assert config.max_delay >= 60.0


class TestWaitNeverSleepsIssue74:
    """ReconnectStrategy.wait() must always be Event.wait-based — the
    old bare time.sleep fallback (no stop_event) was an MF010 loaded
    gun: a daemon caller omitting the event slept uninterruptibly for
    up to max_delay (60s) on shutdown."""

    def test_wait_with_preset_stop_event_returns_immediately(self):
        import threading
        import time as _time
        strategy = ReconnectStrategy.for_rns()
        strategy.record_failure()  # arm a real (multi-second) delay
        stop = threading.Event()
        stop.set()
        t0 = _time.monotonic()
        strategy.wait(stop)
        assert _time.monotonic() - t0 < 0.5

    def test_wait_source_contains_no_time_sleep(self):
        import inspect
        from gateway.reconnect import ReconnectStrategy as _RS
        src = inspect.getsource(_RS.wait)
        assert "time.sleep" not in src, (
            "wait() regressed to a bare time.sleep — MF010; use the "
            "never-set Event fallback"
        )

    def test_wait_without_event_still_respects_delay(self):
        import time as _time
        strategy = ReconnectStrategy(
            config=ReconnectConfig(initial_delay=0.1, jitter=0.0)
        )
        strategy.record_failure()
        t0 = _time.monotonic()
        strategy.wait(None)
        assert _time.monotonic() - t0 >= 0.09
