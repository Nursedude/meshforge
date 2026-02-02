"""
Tests for Circuit Breaker pattern implementation.
"""

import pytest
import time
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.circuit_breaker import (
    CircuitBreaker, CircuitBreakerRegistry, CircuitState
)


class TestCircuitBreaker:
    """Test individual circuit breaker behavior."""

    def test_initial_state_is_closed(self):
        """Circuit starts in closed state."""
        cb = CircuitBreaker(destination="test-node")
        assert cb.state == CircuitState.CLOSED
        assert cb.is_closed
        assert not cb.is_open

    def test_can_execute_when_closed(self):
        """Requests allowed when circuit is closed."""
        cb = CircuitBreaker(destination="test-node")
        assert cb.can_execute() is True

    def test_opens_after_threshold_failures(self):
        """Circuit opens after failure threshold reached."""
        cb = CircuitBreaker(destination="test-node", failure_threshold=3)

        # First two failures - still closed
        cb.record_failure("error 1")
        assert cb.state == CircuitState.CLOSED
        cb.record_failure("error 2")
        assert cb.state == CircuitState.CLOSED

        # Third failure - opens
        cb.record_failure("error 3")
        assert cb.state == CircuitState.OPEN
        assert cb.is_open

    def test_blocks_requests_when_open(self):
        """Requests blocked when circuit is open."""
        cb = CircuitBreaker(destination="test-node", failure_threshold=1)
        cb.record_failure("error")

        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_half_open_after_recovery_timeout(self):
        """Circuit transitions to half-open after recovery timeout."""
        cb = CircuitBreaker(
            destination="test-node",
            failure_threshold=1,
            recovery_timeout=0.1  # 100ms for testing
        )
        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Should transition to half-open on next check
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_on_success_in_half_open(self):
        """Circuit closes when success recorded in half-open state."""
        cb = CircuitBreaker(
            destination="test-node",
            failure_threshold=1,
            recovery_timeout=0.1
        )
        cb.record_failure("error")
        time.sleep(0.15)
        cb.can_execute()  # Transition to half-open

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        """Circuit re-opens when failure recorded in half-open state."""
        cb = CircuitBreaker(
            destination="test-node",
            failure_threshold=1,
            recovery_timeout=0.1
        )
        cb.record_failure("error")
        time.sleep(0.15)
        cb.can_execute()  # Transition to half-open

        cb.record_failure("error again")
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        """Success resets failure count."""
        cb = CircuitBreaker(destination="test-node", failure_threshold=3)

        cb.record_failure("error 1")
        cb.record_failure("error 2")
        cb.record_success()

        # Failure count reset, need 3 more to open
        cb.record_failure("error 3")
        cb.record_failure("error 4")
        assert cb.state == CircuitState.CLOSED

    def test_manual_reset(self):
        """Circuit can be manually reset."""
        cb = CircuitBreaker(destination="test-node", failure_threshold=1)
        cb.record_failure("error")
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_stats_tracking(self):
        """Statistics are tracked correctly."""
        cb = CircuitBreaker(destination="test-node", failure_threshold=2)

        cb.record_success()
        cb.record_failure("error")
        cb.record_failure("error")  # Opens circuit
        cb.can_execute()  # Blocked

        stats = cb.get_stats()
        assert stats["destination"] == "test-node"
        assert stats["state"] == "open"
        assert stats["failure_count"] == 2
        assert stats["success_count"] == 1
        assert stats["total_blocked"] == 1


class TestCircuitBreakerRegistry:
    """Test circuit breaker registry."""

    def test_creates_circuits_on_demand(self):
        """Circuits created automatically when needed."""
        registry = CircuitBreakerRegistry()

        assert registry.can_send("node-1") is True
        assert registry.can_send("node-2") is True

        stats = registry.get_stats()
        assert stats["total_circuits"] == 2

    def test_tracks_failures_per_destination(self):
        """Failures tracked separately per destination."""
        registry = CircuitBreakerRegistry(failure_threshold=2)

        # Fail node-1
        registry.record_failure("node-1", "error")
        registry.record_failure("node-1", "error")

        # node-1 should be blocked, node-2 should be open
        assert registry.can_send("node-1") is False
        assert registry.can_send("node-2") is True

    def test_get_open_circuits(self):
        """Can retrieve all open circuits."""
        registry = CircuitBreakerRegistry(failure_threshold=1)

        registry.record_failure("node-1", "error")
        registry.record_failure("node-2", "error")
        registry.can_send("node-3")  # Just access, no failure

        open_circuits = registry.get_open_circuits()
        assert "node-1" in open_circuits
        assert "node-2" in open_circuits
        assert "node-3" not in open_circuits

    def test_reset_single_circuit(self):
        """Can reset a single circuit."""
        registry = CircuitBreakerRegistry(failure_threshold=1)
        registry.record_failure("node-1", "error")

        assert registry.can_send("node-1") is False
        registry.reset("node-1")
        assert registry.can_send("node-1") is True

    def test_reset_all_circuits(self):
        """Can reset all circuits."""
        registry = CircuitBreakerRegistry(failure_threshold=1)
        registry.record_failure("node-1", "error")
        registry.record_failure("node-2", "error")

        count = registry.reset_all()
        assert count == 2
        assert registry.can_send("node-1") is True
        assert registry.can_send("node-2") is True

    def test_registry_stats(self):
        """Registry provides aggregate statistics."""
        registry = CircuitBreakerRegistry(failure_threshold=1)

        registry.record_failure("node-1", "error")
        registry.can_send("node-1")  # Blocked
        registry.record_success("node-2")

        stats = registry.get_stats()
        assert stats["total_circuits"] == 2
        assert stats["circuits_by_state"]["open"] == 1
        assert stats["circuits_by_state"]["closed"] == 1
        assert stats["total_blocked"] >= 1

    def test_max_circuits_limit(self):
        """Registry enforces maximum circuit limit."""
        registry = CircuitBreakerRegistry()
        registry.MAX_CIRCUITS = 5  # Override for testing

        # Create 6 circuits
        for i in range(6):
            registry.can_send(f"node-{i}")

        stats = registry.get_stats()
        assert stats["total_circuits"] <= 5


class TestBridgeHealthIntegration:
    """Test bridge health and circuit breaker integration."""

    def test_bridge_status_enum(self):
        """BridgeStatus enum is importable."""
        from gateway.bridge_health import BridgeStatus, MessageOrigin

        assert BridgeStatus.HEALTHY.value == "healthy"
        assert BridgeStatus.DEGRADED.value == "degraded"
        assert BridgeStatus.OFFLINE.value == "offline"

        assert MessageOrigin.RADIO.value == "radio"
        assert MessageOrigin.MQTT.value == "mqtt"

    def test_bridge_health_monitor_status(self):
        """BridgeHealthMonitor provides bridge status."""
        from gateway.bridge_health import BridgeHealthMonitor, BridgeStatus

        health = BridgeHealthMonitor()

        # Initially offline
        assert health.get_bridge_status() == BridgeStatus.OFFLINE

        # One network connected = degraded
        health.record_connection_event("meshtastic", "connected")
        assert health.get_bridge_status() == BridgeStatus.DEGRADED

        # Both networks connected = healthy
        health.record_connection_event("rns", "connected")
        assert health.get_bridge_status() == BridgeStatus.HEALTHY

        # One disconnects = degraded again
        health.record_connection_event("rns", "disconnected")
        assert health.get_bridge_status() == BridgeStatus.DEGRADED

    def test_degraded_reason(self):
        """Can get reason for degraded status."""
        from gateway.bridge_health import BridgeHealthMonitor

        health = BridgeHealthMonitor()
        health.record_connection_event("meshtastic", "connected")

        reason = health.get_degraded_reason()
        assert "RNS disconnected" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
