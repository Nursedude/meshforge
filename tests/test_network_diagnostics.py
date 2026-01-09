"""
Tests for network diagnostics system.

Run: python3 -m pytest tests/test_network_diagnostics.py -v
"""

import pytest
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.utils.network_diagnostics import (
    EventCategory,
    EventSeverity,
    HealthStatus,
    DiagnosticEvent,
    HealthCheck,
    NetworkDiagnostics,
)


class TestEventCategory:
    """Tests for EventCategory enum."""

    def test_all_categories_exist(self):
        """Test all expected categories exist."""
        assert EventCategory.NETWORK.value == "network"
        assert EventCategory.SECURITY.value == "security"
        assert EventCategory.PERFORMANCE.value == "perf"
        assert EventCategory.SYSTEM.value == "system"
        assert EventCategory.ERROR.value == "error"
        assert EventCategory.AUDIT.value == "audit"

    def test_category_count(self):
        """Test expected number of categories."""
        assert len(EventCategory) == 6


class TestEventSeverity:
    """Tests for EventSeverity enum."""

    def test_all_severities_exist(self):
        """Test all expected severities exist."""
        assert EventSeverity.DEBUG.value == "debug"
        assert EventSeverity.INFO.value == "info"
        assert EventSeverity.WARNING.value == "warning"
        assert EventSeverity.ERROR.value == "error"
        assert EventSeverity.CRITICAL.value == "critical"

    def test_severity_count(self):
        """Test expected number of severities."""
        assert len(EventSeverity) == 5


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_all_statuses_exist(self):
        """Test all expected statuses exist."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestDiagnosticEvent:
    """Tests for DiagnosticEvent dataclass."""

    def test_creation(self):
        """Test creating a diagnostic event."""
        event = DiagnosticEvent(
            timestamp=datetime(2026, 1, 9, 12, 0, 0),
            category=EventCategory.NETWORK,
            severity=EventSeverity.INFO,
            source="meshtastic",
            message="Connected to device"
        )

        assert event.category == EventCategory.NETWORK
        assert event.severity == EventSeverity.INFO
        assert event.source == "meshtastic"
        assert event.message == "Connected to device"
        assert event.details is None
        assert event.fix_hint is None

    def test_with_details(self):
        """Test event with details and fix hint."""
        event = DiagnosticEvent(
            timestamp=datetime.now(),
            category=EventCategory.ERROR,
            severity=EventSeverity.ERROR,
            source="rns",
            message="Connection failed",
            details={"port": 4403, "error_code": 111},
            fix_hint="Check if rnsd is running"
        )

        assert event.details == {"port": 4403, "error_code": 111}
        assert event.fix_hint == "Check if rnsd is running"

    def test_to_dict(self):
        """Test serialization to dict."""
        ts = datetime(2026, 1, 9, 12, 0, 0)
        event = DiagnosticEvent(
            timestamp=ts,
            category=EventCategory.SECURITY,
            severity=EventSeverity.WARNING,
            source="auth",
            message="Login attempt",
            details={"user": "admin"}
        )

        d = event.to_dict()

        assert d['timestamp'] == ts.isoformat()
        assert d['category'] == "security"
        assert d['severity'] == "warning"
        assert d['source'] == "auth"
        assert d['message'] == "Login attempt"
        assert d['details'] == {"user": "admin"}

    def test_to_log_line(self):
        """Test formatting as log line."""
        ts = datetime(2026, 1, 9, 12, 30, 45)
        event = DiagnosticEvent(
            timestamp=ts,
            category=EventCategory.NETWORK,
            severity=EventSeverity.INFO,
            source="mesh",
            message="Node discovered"
        )

        line = event.to_log_line()

        assert "2026-01-09 12:30:45" in line
        assert "INFO" in line
        assert "NETW" in line
        assert "mesh" in line
        assert "Node discovered" in line


class TestHealthCheck:
    """Tests for HealthCheck dataclass."""

    def test_creation(self):
        """Test creating a health check."""
        check = HealthCheck(
            name="meshtasticd",
            status=HealthStatus.HEALTHY,
            message="Service running",
            last_check=datetime.now()
        )

        assert check.name == "meshtasticd"
        assert check.status == HealthStatus.HEALTHY
        assert check.message == "Service running"

    def test_with_fix_hint(self):
        """Test health check with fix hint."""
        check = HealthCheck(
            name="rnsd",
            status=HealthStatus.UNHEALTHY,
            message="Service not running",
            last_check=datetime.now(),
            fix_hint="Run: sudo systemctl start rnsd"
        )

        assert check.fix_hint == "Run: sudo systemctl start rnsd"

    def test_to_dict(self):
        """Test serialization to dict."""
        ts = datetime(2026, 1, 9, 12, 0, 0)
        check = HealthCheck(
            name="test",
            status=HealthStatus.DEGRADED,
            message="High latency",
            last_check=ts,
            details={"latency_ms": 500}
        )

        d = check.to_dict()

        assert d['name'] == "test"
        assert d['status'] == "degraded"
        assert d['message'] == "High latency"
        assert d['last_check'] == ts.isoformat()
        assert d['details'] == {"latency_ms": 500}


class TestNetworkDiagnostics:
    """Tests for NetworkDiagnostics singleton."""

    @pytest.fixture
    def diag(self):
        """Create a fresh diagnostics instance for testing."""
        # Reset singleton for testing
        NetworkDiagnostics._instance = None

        with patch.object(NetworkDiagnostics, '_ensure_dirs'):
            with patch.object(NetworkDiagnostics, '_health_monitor_loop'):
                instance = NetworkDiagnostics()
                instance._monitor_running = False
                yield instance

                # Cleanup
                instance._monitor_running = False
                NetworkDiagnostics._instance = None

    def test_singleton(self):
        """Test that NetworkDiagnostics is a singleton."""
        NetworkDiagnostics._instance = None

        with patch.object(NetworkDiagnostics, '_ensure_dirs'):
            with patch.object(NetworkDiagnostics, '_health_monitor_loop'):
                diag1 = NetworkDiagnostics()
                diag2 = NetworkDiagnostics()

                assert diag1 is diag2

                NetworkDiagnostics._instance = None

    def test_log_event(self, diag):
        """Test logging an event."""
        with patch.object(diag, '_write_event_to_file'):
            event = diag.log_event(
                EventCategory.NETWORK,
                EventSeverity.INFO,
                "test",
                "Test message"
            )

            assert event.category == EventCategory.NETWORK
            assert event.source == "test"
            assert len(diag._events) == 1

    def test_log_event_with_callback(self, diag):
        """Test that event logging triggers callbacks."""
        callback = MagicMock()
        diag._event_callbacks.append(callback)

        with patch.object(diag, '_write_event_to_file'):
            event = diag.log_event(
                EventCategory.SYSTEM,
                EventSeverity.WARNING,
                "test",
                "Test warning"
            )

        callback.assert_called_once_with(event)

    def test_log_connection_success(self, diag):
        """Test logging successful connection."""
        with patch.object(diag, 'log_event') as mock_log:
            diag.log_connection("meshtastic", "localhost:4403", True)

            mock_log.assert_called_once()
            args = mock_log.call_args
            assert args[0][0] == EventCategory.NETWORK
            assert args[0][1] == EventSeverity.INFO

    def test_log_connection_failure(self, diag):
        """Test logging connection failure."""
        with patch.object(diag, 'log_event') as mock_log:
            with patch.object(diag, '_get_connection_fix_hint', return_value="Fix hint"):
                diag.log_connection("rns", "localhost", False, "Connection refused")

                mock_log.assert_called_once()
                args = mock_log.call_args
                assert args[0][0] == EventCategory.NETWORK
                assert args[0][1] == EventSeverity.ERROR

    def test_log_disconnection(self, diag):
        """Test logging disconnection."""
        with patch.object(diag, 'log_event') as mock_log:
            diag.log_disconnection("mesh", "device", "timeout")

            mock_log.assert_called_once()
            args = mock_log.call_args
            assert args[0][1] == EventSeverity.WARNING

    def test_log_security_event(self, diag):
        """Test logging security event."""
        with patch.object(diag, 'log_event') as mock_log:
            diag.log_security_event("auth", "Failed login attempt")

            mock_log.assert_called_once()
            args = mock_log.call_args
            assert args[0][0] == EventCategory.SECURITY

    def test_log_performance(self, diag):
        """Test logging performance metric."""
        with patch.object(diag, 'log_event') as mock_log:
            diag.log_performance("network", "latency", 50, "ms")

            mock_log.assert_called_once()
            args = mock_log.call_args
            assert args[0][0] == EventCategory.PERFORMANCE
            assert "latency" in args[0][3]

    def test_log_config_change(self, diag):
        """Test logging config change for audit."""
        with patch.object(diag, 'log_event') as mock_log:
            diag.log_config_change("gateway", "enabled", False, True)

            mock_log.assert_called_once()
            args = mock_log.call_args
            assert args[0][0] == EventCategory.AUDIT

    def test_events_limited_to_maxlen(self, diag):
        """Test that events are limited by maxlen."""
        with patch.object(diag, '_write_event_to_file'):
            # Add more than maxlen events
            for i in range(1100):
                diag.log_event(
                    EventCategory.SYSTEM,
                    EventSeverity.DEBUG,
                    "test",
                    f"Event {i}"
                )

            # Should be capped at 1000
            assert len(diag._events) == 1000

    def test_callback_error_handling(self, diag):
        """Test that callback errors don't break logging."""
        bad_callback = MagicMock(side_effect=Exception("Callback error"))
        good_callback = MagicMock()

        diag._event_callbacks = [bad_callback, good_callback]

        with patch.object(diag, '_write_event_to_file'):
            # Should not raise
            diag.log_event(
                EventCategory.SYSTEM,
                EventSeverity.INFO,
                "test",
                "Test"
            )

        # Good callback should still be called
        good_callback.assert_called_once()


class TestHealthCheckMethods:
    """Tests for health check functionality."""

    @pytest.fixture
    def diag(self):
        """Create diagnostics instance."""
        NetworkDiagnostics._instance = None

        with patch.object(NetworkDiagnostics, '_ensure_dirs'):
            with patch.object(NetworkDiagnostics, '_health_monitor_loop'):
                instance = NetworkDiagnostics()
                instance._monitor_running = False
                yield instance

                instance._monitor_running = False
                NetworkDiagnostics._instance = None

    def test_update_health(self, diag):
        """Test updating health status."""
        with patch.object(diag, '_write_event_to_file'):
            diag.update_health(
                "test_service",
                HealthStatus.HEALTHY,
                "Running"
            )

        assert "test_service" in diag._health
        assert diag._health["test_service"].status == HealthStatus.HEALTHY

    def test_update_health_with_details(self, diag):
        """Test updating health with details and fix hint."""
        with patch.object(diag, '_write_event_to_file'):
            diag.update_health(
                "test",
                HealthStatus.UNHEALTHY,
                "Service down",
                details={"error": "timeout"},
                fix_hint="Restart service"
            )

        check = diag._health["test"]
        assert check.details == {"error": "timeout"}
        assert check.fix_hint == "Restart service"

    def test_get_health_single(self, diag):
        """Test getting single health status."""
        check = HealthCheck(
            name="test",
            status=HealthStatus.DEGRADED,
            message="High load",
            last_check=datetime.now()
        )
        diag._health["test"] = check

        result = diag.get_health("test")

        assert "test" in result
        assert result["test"] == check

    def test_get_health_unknown(self, diag):
        """Test getting health for unknown service returns empty dict."""
        result = diag.get_health("unknown_service")

        assert result == {}

    def test_get_health_all(self, diag):
        """Test getting all health statuses."""
        diag._health["svc1"] = HealthCheck(
            name="svc1", status=HealthStatus.HEALTHY,
            message="OK", last_check=datetime.now()
        )
        diag._health["svc2"] = HealthCheck(
            name="svc2", status=HealthStatus.UNHEALTHY,
            message="Down", last_check=datetime.now()
        )

        all_health = diag.get_health()  # No name = get all

        assert len(all_health) == 2
        assert "svc1" in all_health
        assert "svc2" in all_health
