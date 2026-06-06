"""
Circuit Breaker Pattern for MeshForge Gateway.

Prevents cascading failures by temporarily blocking requests to failing
destinations. Based on the Netflix Hystrix pattern.

States:
- CLOSED: Normal operation, requests flow through
- OPEN: Blocking requests, destination is failing
- HALF_OPEN: Testing if destination has recovered

Usage:
    from gateway.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry

    # Per-destination circuit breaker
    registry = CircuitBreakerRegistry()

    # Before sending to a destination:
    if registry.can_send("!abc12345"):
        try:
            send_message(destination)
            registry.record_success("!abc12345")
        except Exception as e:
            registry.record_failure("!abc12345")
    else:
        # Circuit is open, skip this destination
        log.warning("Circuit open for !abc12345, message queued")

Reference:
    https://martinfowler.com/bliki/CircuitBreaker.html
"""

import functools
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Any

from utils.timeouts import CIRCUIT_RECOVERY as _CIRCUIT_RECOVERY_TIMEOUT

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Blocking requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitStats:
    """Statistics for a single circuit."""
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_blocked: int = 0
    state_changes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API/display."""
        return {
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
            "total_blocked": self.total_blocked,
            "state_changes": self.state_changes,
        }


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for a single destination.

    Tracks failures and blocks requests when a destination is unhealthy,
    then periodically tests for recovery.
    """
    destination: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 1

    # Internal state (not in __init__)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _last_state_change: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _stats: CircuitStats = field(default_factory=CircuitStats, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self):
        # Issue #74: elapsed-time math (_last_state_change,
        # _last_failure_time) runs on time.monotonic() — Pi fleet boxes
        # NTP-step after boot, and a wall-clock step would freeze an
        # OPEN circuit (backward) or recover it prematurely (forward).
        # Wall clock is kept ONLY for the operator-facing _stats
        # timestamps.
        self._last_state_change = time.monotonic()
        if self.half_open_max_calls != 1:
            # >1 admits multiple concurrent trial calls into a
            # known-bad destination and the slot is never returned on
            # completion — unsupported until the slot becomes a true
            # gauge. Clamp loudly rather than misbehave quietly.
            logger.warning(
                f"Circuit {self.destination}: half_open_max_calls="
                f"{self.half_open_max_calls} unsupported, clamping to 1"
            )
            self.half_open_max_calls = 1

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def is_closed(self) -> bool:
        """True if circuit is allowing requests."""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """True if circuit is blocking requests."""
        return self._state == CircuitState.OPEN

    def can_execute(self) -> bool:
        """
        Check if a request can proceed.

        Returns:
            True if the request should proceed, False if blocked.
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed (monotonic —
                # immune to NTP steps, see __post_init__)
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    # The transitioning caller IS the trial call — take
                    # the slot, or a second caller slips through before
                    # the trial resolves (off-by-one found by the
                    # Issue #74 review: _transition_to zeroes the
                    # counter and this path returned True without
                    # incrementing it).
                    self._half_open_calls = 1
                    return True
                # Still in recovery period
                self._stats.total_blocked += 1
                return False

            if self._state == CircuitState.HALF_OPEN:
                # Allow limited calls to test recovery
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                # Already have test call in flight
                self._stats.total_blocked += 1
                return False

            return False

    def record_success(self) -> None:
        """Record a successful request."""
        with self._lock:
            self._success_count += 1
            self._stats.success_count += 1
            self._stats.last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Recovery confirmed, close the circuit
                self._transition_to(CircuitState.CLOSED)
                logger.info(f"Circuit CLOSED for {self.destination} - recovered")

            # Reset failure count on success
            self._failure_count = 0

    def record_failure(self, error: str = "") -> None:
        """Record a failed request."""
        with self._lock:
            self._failure_count += 1
            self._stats.failure_count += 1
            # Wall clock for the operator-facing stat, monotonic for
            # the recovery-elapsed math in can_execute().
            self._stats.last_failure_time = time.time()
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Recovery failed, re-open the circuit
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    f"Circuit OPEN for {self.destination} - "
                    f"recovery failed: {error[:50] if error else 'unknown'}"
                )

            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    logger.warning(
                        f"Circuit OPEN for {self.destination} - "
                        f"{self._failure_count} consecutive failures"
                    )

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state (must hold lock)."""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._last_state_change = time.monotonic()
            self._stats.state_changes += 1

            if new_state == CircuitState.HALF_OPEN:
                self._half_open_calls = 0

            if new_state == CircuitState.CLOSED:
                self._failure_count = 0

            logger.debug(
                f"Circuit {self.destination}: {old_state.value} -> {new_state.value}"
            )

    def reset(self) -> None:
        """Manually reset the circuit to closed state."""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._half_open_calls = 0
            logger.info(f"Circuit manually reset for {self.destination}")

    def trip_open(self, reason: str = "") -> bool:
        """Fast-path to OPEN regardless of failure count.

        A single-event-fatal condition (an rnsd RPC wedge surfacing
        as a `WedgeTimeout`) should bounce the circuit immediately;
        waiting for `failure_threshold` consecutive `record_failure`
        calls is the wrong shape because the wedged path won't even
        emit those calls — the process aborts first.

        Returns True if the transition was CLOSED/HALF_OPEN → OPEN
        (i.e. this call actually opened the circuit). Idempotent on
        an already-open circuit: returns False without log spam.
        Reason is recorded on the last-failure metadata so the
        recovery actor's audit trail (Fork B) can correlate.
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                return False
            # `can_execute()` reads `_last_failure_time` (the dataclass
            # field, monotonic) to compute recovery-timeout elapsed;
            # `get_stats()` surfaces `_stats.last_failure_time` (the
            # sub-dataclass attribute, wall clock). They are independent
            # stores — update both, or the circuit will immediately
            # transition to HALF_OPEN on the next `can_execute()`
            # because `_last_failure_time=0.0` is ~boot-time ago.
            self._last_failure_time = time.monotonic()
            self._stats.last_failure_time = time.time()
            # Bump both internal counter AND stats counter — get_stats()
            # merges `_stats.to_dict()` over the per-circuit dict, so a
            # solo `_failure_count` write would be shadowed when surfaced
            # to operators.
            self._failure_count = self.failure_threshold
            self._stats.failure_count += 1
            self._transition_to(CircuitState.OPEN)
            logger.warning(
                f"Circuit {self.destination}: tripped open (reason: "
                f"{reason or 'unspecified'})"
            )
            return True

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit statistics."""
        with self._lock:
            return {
                "destination": self.destination,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "time_in_state": time.monotonic() - self._last_state_change,
                "recovery_timeout": self.recovery_timeout,
                **self._stats.to_dict(),
            }


class CircuitBreakerRegistry:
    """
    Registry of circuit breakers for multiple destinations.

    Creates circuit breakers on-demand for each destination and provides
    a unified interface for checking and recording results.

    Thread-safe.
    """

    # Default settings
    DEFAULT_FAILURE_THRESHOLD = 5
    DEFAULT_RECOVERY_TIMEOUT = _CIRCUIT_RECOVERY_TIMEOUT
    DEFAULT_HALF_OPEN_MAX_CALLS = 1

    # Maximum tracked destinations (prevent unbounded growth)
    MAX_CIRCUITS = 1000

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT,
        half_open_max_calls: int = DEFAULT_HALF_OPEN_MAX_CALLS,
    ):
        """
        Initialize the registry.

        Args:
            failure_threshold: Failures before opening circuit (default: 5)
            recovery_timeout: Seconds before testing recovery (default: 60)
            half_open_max_calls: Test calls in half-open state (default: 1)
        """
        self._circuits: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls

        # Global stats
        self._total_blocked = 0
        self._total_opened = 0

    def _get_or_create(self, destination: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a destination."""
        with self._lock:
            if destination not in self._circuits:
                # Prevent unbounded growth
                if len(self._circuits) >= self.MAX_CIRCUITS:
                    self._evict_oldest_closed()

                self._circuits[destination] = CircuitBreaker(
                    destination=destination,
                    failure_threshold=self._failure_threshold,
                    recovery_timeout=self._recovery_timeout,
                    half_open_max_calls=self._half_open_max_calls,
                )
            return self._circuits[destination]

    def _evict_oldest_closed(self) -> None:
        """Evict the oldest closed circuit to make room (must hold lock)."""
        # Find closed circuits, sorted by last state change
        closed = [
            (dest, cb)
            for dest, cb in self._circuits.items()
            if cb.state == CircuitState.CLOSED
        ]
        if closed:
            oldest = min(closed, key=lambda x: x[1]._last_state_change)
            del self._circuits[oldest[0]]
            logger.debug(f"Evicted circuit for {oldest[0]} (capacity limit)")

    def can_send(self, destination: str) -> bool:
        """
        Check if we can send to a destination.

        Args:
            destination: Target destination ID

        Returns:
            True if allowed, False if circuit is open
        """
        circuit = self._get_or_create(destination)
        allowed = circuit.can_execute()
        if not allowed:
            with self._lock:
                self._total_blocked += 1
        return allowed

    def record_success(self, destination: str) -> None:
        """Record a successful send to a destination."""
        circuit = self._get_or_create(destination)
        circuit.record_success()

    def record_failure(self, destination: str, error: str = "") -> None:
        """
        Record a failed send to a destination.

        Args:
            destination: Target destination ID
            error: Optional error message
        """
        circuit = self._get_or_create(destination)
        was_closed = circuit.is_closed
        circuit.record_failure(error)
        if was_closed and circuit.is_open:
            with self._lock:
                self._total_opened += 1

    def trip_open(self, destination: str, reason: str = "") -> bool:
        """Force a destination's circuit to OPEN on a single event.

        Convenience wrapper over `CircuitBreaker.trip_open` —
        intended for `bounded_rpc.WedgeTimeout` handlers in the
        gateway that must shed a wedged destination immediately,
        bypassing the multi-failure threshold. Updates the registry's
        opened-circuits counter when this call actually transitions
        a previously-closed circuit. Idempotent on an already-open
        circuit (returns False, no counter bump).
        """
        circuit = self._get_or_create(destination)
        opened = circuit.trip_open(reason)
        if opened:
            with self._lock:
                self._total_opened += 1
        return opened

    def reset(self, destination: str) -> bool:
        """
        Manually reset a circuit to closed state.

        Args:
            destination: Target destination ID

        Returns:
            True if circuit existed and was reset
        """
        with self._lock:
            if destination in self._circuits:
                self._circuits[destination].reset()
                return True
            return False

    def reset_all(self) -> int:
        """
        Reset all circuits to closed state.

        Returns:
            Number of circuits reset
        """
        with self._lock:
            count = 0
            for circuit in self._circuits.values():
                if circuit.state != CircuitState.CLOSED:
                    circuit.reset()
                    count += 1
            return count

    def get_open_circuits(self) -> Dict[str, Dict[str, Any]]:
        """Get all circuits that are currently open (blocking)."""
        with self._lock:
            return {
                dest: cb.get_stats()
                for dest, cb in self._circuits.items()
                if cb.state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
            }

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            states = {"closed": 0, "open": 0, "half_open": 0}
            for cb in self._circuits.values():
                states[cb.state.value] += 1

            return {
                "total_circuits": len(self._circuits),
                "circuits_by_state": states,
                "total_blocked": self._total_blocked,
                "total_opened": self._total_opened,
                "max_circuits": self.MAX_CIRCUITS,
            }

    def get_circuit_stats(self, destination: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific circuit."""
        with self._lock:
            if destination in self._circuits:
                return self._circuits[destination].get_stats()
            return None


# =============================================================================
# Global Registry Tracker — allows TUI to discover all active registries
# =============================================================================

_active_registries: Dict[str, CircuitBreakerRegistry] = {}
_registries_lock = threading.Lock()


def register_service_breaker(name: str, registry: CircuitBreakerRegistry) -> None:
    """Register a circuit breaker registry for TUI visibility."""
    with _registries_lock:
        _active_registries[name] = registry


def get_all_registries() -> Dict[str, CircuitBreakerRegistry]:
    """Get all registered circuit breaker registries."""
    with _registries_lock:
        return dict(_active_registries)


def create_service_registry(
    service_name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = _CIRCUIT_RECOVERY_TIMEOUT,
) -> CircuitBreakerRegistry:
    """Factory for creating per-service circuit breaker registries.

    Creates a registry and auto-registers it for TUI discovery.

    Args:
        service_name: Human-readable name (for logging/TUI display)
        failure_threshold: Failures before opening circuit
        recovery_timeout: Seconds before testing recovery

    Returns:
        A configured CircuitBreakerRegistry instance
    """
    registry = CircuitBreakerRegistry(
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
    )
    register_service_breaker(service_name, registry)
    return registry


# =============================================================================
# Decorator — @circuit_protected for easy adoption
# =============================================================================

def circuit_protected(
    registry: CircuitBreakerRegistry,
    destination_key: Optional[Callable] = None,
    fallback: Optional[Callable] = None,
):
    """Decorator that wraps a function with circuit breaker protection.

    Args:
        registry: The CircuitBreakerRegistry to use
        destination_key: Callable that extracts the destination from args.
                        If None, uses str(first argument).
        fallback: Optional fallback called when circuit is open.
                 Receives same arguments. Returns None if not provided.

    Usage:
        registry = CircuitBreakerRegistry()

        @circuit_protected(registry, destination_key=lambda host, **kw: host)
        def send_http_request(host, path, data):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if destination_key:
                dest = destination_key(*args, **kwargs)
            elif args:
                dest = str(args[0])
            else:
                dest = "default"

            if not registry.can_send(dest):
                logger.debug(f"Circuit open for {dest}, skipping {func.__name__}")
                if fallback:
                    return fallback(*args, **kwargs)
                return None

            try:
                result = func(*args, **kwargs)
                registry.record_success(dest)
                return result
            except Exception as e:
                registry.record_failure(dest, str(e))
                raise

        wrapper._circuit_registry = registry
        return wrapper
    return decorator
