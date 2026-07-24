"""
Exponential backoff reconnection strategy for gateway services.

Provides reliable reconnection with exponential backoff and jitter
for both Meshtastic and RNS connections.

Also includes SlowStartRecovery for gradual throughput increase
after service recovery (based on NGINX slow_start pattern).
"""

import random
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, TypeVar, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Never-set fallback event so wait() is always Event.wait-based — a
# bare time.sleep here was an MF010 loaded gun: any daemon caller that
# omitted stop_event slept uninterruptibly for up to max_delay (60s)
# on shutdown (Issue #74).
_NEVER_SET = threading.Event()


@dataclass
class ReconnectConfig:
    """Configuration for reconnection behavior."""

    initial_delay: float = 1.0
    """Initial delay in seconds before first retry."""

    max_delay: float = 60.0
    """Maximum delay in seconds between retries."""

    multiplier: float = 2.0
    """Multiplier for exponential backoff."""

    jitter: float = 0.1
    """Random jitter factor (0.0-0.5) to prevent thundering herd."""

    max_attempts: int = 10
    """Maximum number of retry attempts before giving up."""


@dataclass
class ReconnectStrategy:
    """
    Implements exponential backoff with jitter for reconnection.

    Usage:
        strategy = ReconnectStrategy.for_meshtastic()

        while strategy.should_retry():
            try:
                connect()
                strategy.record_success()
                break
            except ConnectionError:
                strategy.record_failure()
                strategy.wait()
    """

    config: ReconnectConfig = field(default_factory=ReconnectConfig)
    attempts: int = field(default=0, init=False)

    def get_delay(self, attempt: Optional[int] = None) -> float:
        """
        Calculate delay for a given attempt number.

        Args:
            attempt: Attempt number (0-based). If None, uses current attempts.

        Returns:
            Delay in seconds with jitter applied.
        """
        if attempt is None:
            attempt = self.attempts

        # Calculate base delay with exponential backoff
        base_delay = self.config.initial_delay * (self.config.multiplier ** attempt)

        # Cap at max_delay
        base_delay = min(base_delay, self.config.max_delay)

        # Apply jitter
        jitter_range = base_delay * self.config.jitter
        jitter = random.uniform(-jitter_range, jitter_range)

        return max(0.0, base_delay + jitter)

    def wait(self, stop_event: Optional[threading.Event] = None) -> float:
        """
        Wait for the current backoff delay, interruptible via stop_event.

        Args:
            stop_event: If provided, wait is interrupted when event is set,
                        allowing fast shutdown instead of sleeping up to max_delay.

        Returns:
            The actual delay slept (may be shorter if interrupted).
        """
        delay = self.get_delay()
        logger.debug(f"Reconnect backoff: waiting {delay:.2f}s (attempt {self.attempts})")
        # Always Event.wait-based: interruptible when a stop_event is
        # provided, and timing-identical (without the MF010 hazard)
        # when it isn't.
        (stop_event or _NEVER_SET).wait(delay)
        return delay

    def record_failure(self) -> None:
        """Record a connection failure, incrementing attempt counter."""
        self.attempts += 1
        logger.debug(f"Connection failure recorded, attempts: {self.attempts}")

    def record_success(self) -> None:
        """Record a successful connection, resetting attempt counter."""
        if self.attempts > 0:
            logger.info(f"Connection succeeded after {self.attempts} attempts")
        self.attempts = 0

    def reset(self) -> None:
        """Reset the attempt counter."""
        self.attempts = 0

    def should_retry(self) -> bool:
        """
        Check if another retry attempt should be made.

        Returns:
            True if attempts < max_attempts, False otherwise.
        """
        return self.attempts < self.config.max_attempts

    def execute_with_retry(
        self,
        func: Callable[[], T],
        on_failure: Optional[Callable[[Exception], None]] = None,
        stop_event: Optional[threading.Event] = None
    ) -> T:
        """
        Execute a function with automatic retry on failure.

        Args:
            func: The function to execute.
            on_failure: Optional callback for each failure.
            stop_event: If provided, wait is interruptible for clean shutdown.

        Returns:
            The result of the function.

        Raises:
            The last exception if all retries are exhausted.
        """
        last_exception = None

        while self.should_retry():
            if stop_event and stop_event.is_set():
                break
            try:
                result = func()
                self.record_success()
                return result
            except Exception as e:
                last_exception = e
                self.record_failure()

                if on_failure:
                    on_failure(e)

                if self.should_retry():
                    logger.warning(
                        f"Retry {self.attempts}/{self.config.max_attempts} "
                        f"after error: {e}"
                    )
                    self.wait(stop_event)

        # All retries exhausted or interrupted
        if last_exception is None:
            # Interrupted before first attempt (stop_event set early)
            raise ConnectionError("Connection interrupted before first attempt")
        logger.error(
            f"All {self.config.max_attempts} retry attempts exhausted"
        )
        raise last_exception

    @classmethod
    def for_meshtastic(cls) -> 'ReconnectStrategy':
        """
        Create a strategy optimized for Meshtastic connections.

        Meshtastic devices may need quick reconnection after
        brief disconnects, but should back off for longer issues.
        """
        config = ReconnectConfig(
            initial_delay=1.0,
            max_delay=30.0,
            multiplier=2.0,
            jitter=0.1,
            max_attempts=10
        )
        return cls(config=config)

    @classmethod
    def for_rns(cls) -> 'ReconnectStrategy':
        """
        Create a strategy optimized for RNS connections.

        RNS may need longer delays for transport initialization
        and network stabilization.
        """
        config = ReconnectConfig(
            initial_delay=2.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter=0.15,
            max_attempts=15
        )
        return cls(config=config)


@dataclass
class SlowStartConfig:
    """Configuration for slow start recovery behavior."""

    slow_start_seconds: float = 30.0
    """Duration of the slow start period in seconds."""

    min_multiplier: float = 0.1
    """Starting throughput multiplier (0.0-1.0)."""

    max_multiplier: float = 1.0
    """Final throughput multiplier after recovery."""


@dataclass
class SlowStartRecovery:
    """
    Gradually increase message throughput after service recovery.

    Based on NGINX slow_start pattern:
    - After connection recovery, don't immediately blast full throughput
    - Linearly ramp up over slow_start_seconds
    - Prevents overwhelming recently-recovered Meshtastic radio

    Usage:
        slow_start = SlowStartRecovery()

        # When connection is recovered:
        slow_start.start_recovery()

        # When sending messages:
        multiplier = slow_start.get_throughput_multiplier()
        delay = base_delay / multiplier  # Longer delays during recovery
        time.sleep(delay)
        send_message()

    Reference:
        NGINX slow_start: http://nginx.org/en/docs/http/ngx_http_upstream_module.html
    """

    config: SlowStartConfig = field(default_factory=SlowStartConfig)
    # Monotonic anchor (gateway review Pri-6, 2026-07-23): the slow-start ramp
    # is a DURATION, and wall-clock durations are forgeable on this fleet's
    # RTC-less Pis (honest_failure_modes #6, the #74 class). Measuring elapsed
    # with time.time() let an NTP forward step jump elapsed past
    # slow_start_seconds → recovery ends early → full throughput blasted at a
    # just-recovered radio (the RATE_LIMIT burst); a backward step drove
    # elapsed negative → sub-min / negative multiplier and stuck-recovering.
    # time.monotonic() never steps or reverses, so the ramp is immune. In-memory
    # only (never persisted/serialized), so no cross-restart concern.
    _recovery_start: Optional[float] = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def start_recovery(self) -> None:
        """Mark start of recovery period."""
        with self._lock:
            self._recovery_start = time.monotonic()
            logger.info(
                f"Slow start recovery initiated "
                f"(duration: {self.config.slow_start_seconds}s)"
            )

    def end_recovery(self) -> None:
        """Manually end recovery period (return to full throughput)."""
        with self._lock:
            if self._recovery_start is not None:
                elapsed = time.monotonic() - self._recovery_start
                logger.info(f"Slow start recovery ended after {elapsed:.1f}s")
            self._recovery_start = None

    def is_recovering(self) -> bool:
        """Check if currently in recovery period."""
        with self._lock:
            if self._recovery_start is None:
                return False

            elapsed = time.monotonic() - self._recovery_start
            return elapsed < self.config.slow_start_seconds

    def get_throughput_multiplier(self) -> float:
        """
        Get current throughput multiplier (0.1-1.0).

        Returns:
            Multiplier that linearly increases from min_multiplier to
            max_multiplier over slow_start_seconds.

            Returns max_multiplier (1.0) if not in recovery.

        Usage:
            # Apply to sending delay
            delay = base_delay / multiplier

            # Or apply to batch size
            batch_size = int(max_batch * multiplier)
        """
        with self._lock:
            if self._recovery_start is None:
                return self.config.max_multiplier

            elapsed = time.monotonic() - self._recovery_start

            # Recovery period complete
            if elapsed >= self.config.slow_start_seconds:
                self._recovery_start = None
                logger.debug("Slow start recovery complete, full throughput restored")
                return self.config.max_multiplier

            # Linear interpolation from min to max
            progress = elapsed / self.config.slow_start_seconds
            multiplier = (
                self.config.min_multiplier +
                (self.config.max_multiplier - self.config.min_multiplier) * progress
            )

            return multiplier

    def get_adjusted_delay(self, base_delay: float) -> float:
        """
        Get delay adjusted for current recovery state.

        Args:
            base_delay: Normal delay between operations

        Returns:
            Adjusted delay (longer during recovery, normal when recovered)
        """
        multiplier = self.get_throughput_multiplier()
        if multiplier <= 0:
            return base_delay * 10  # Safety cap
        return base_delay / multiplier

    def get_recovery_progress(self) -> Optional[float]:
        """
        Get recovery progress as percentage (0-100).

        Returns:
            Progress percentage, or None if not recovering
        """
        with self._lock:
            if self._recovery_start is None:
                return None

            elapsed = time.monotonic() - self._recovery_start
            if elapsed >= self.config.slow_start_seconds:
                return 100.0

            return (elapsed / self.config.slow_start_seconds) * 100

    @classmethod
    def for_meshtastic(cls) -> 'SlowStartRecovery':
        """
        Create slow start recovery for Meshtastic connections.

        Meshtastic radios can be overwhelmed after reconnection,
        so we use a moderate 30-second ramp-up.
        """
        config = SlowStartConfig(
            slow_start_seconds=30.0,
            min_multiplier=0.1,
            max_multiplier=1.0,
        )
        return cls(config=config)

    @classmethod
    def for_rns(cls) -> 'SlowStartRecovery':
        """
        Create slow start recovery for RNS connections.

        RNS is more robust, so we use a shorter 15-second ramp-up.
        """
        config = SlowStartConfig(
            slow_start_seconds=15.0,
            min_multiplier=0.2,
            max_multiplier=1.0,
        )
        return cls(config=config)
