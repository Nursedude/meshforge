"""Tests for startup health check system.

Validates health checking, profile-aware service checks, and output formatting.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.startup_health import (
    ServiceHealth,
    HardwareHealth,
    NetworkHealth,
    HealthSummary,
    run_health_check,
    print_health_summary,
    get_health_dict,
    get_traffic_light,
    get_compact_status,
    check_meshtasticd,
    check_rnsd,
    check_mosquitto,
)


class TestHealthSummary:
    """Test HealthSummary dataclass."""

    def test_default_status_is_unknown(self):
        """New HealthSummary should have unknown status."""
        summary = HealthSummary()
        assert summary.overall_status == "unknown"

    def test_is_ready_when_meshtasticd_running(self):
        """System is ready when meshtasticd is running."""
        summary = HealthSummary()
        summary.services = [ServiceHealth(name="meshtasticd", running=True)]
        assert summary.is_ready is True

    def test_not_ready_when_meshtasticd_down(self):
        """System is not ready when meshtasticd is not running."""
        summary = HealthSummary()
        summary.services = [ServiceHealth(name="meshtasticd", running=False)]
        assert summary.is_ready is False

    def test_profile_name_field(self):
        """HealthSummary should store profile name."""
        summary = HealthSummary(profile_name="Gateway")
        assert summary.profile_name == "Gateway"


class TestRunHealthCheck:
    """Test run_health_check() function."""

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_checks_all_three_services(self, mock_mesh, mock_rns, mock_mqtt,
                                        mock_hw, mock_nodes):
        """Health check should check meshtasticd, rnsd, and mosquitto."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=True, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=False, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=False, optional=True)
        mock_hw.return_value = HardwareHealth()

        summary = run_health_check()
        assert len(summary.services) == 3
        service_names = {s.name for s in summary.services}
        assert service_names == {"meshtasticd", "rnsd", "mosquitto"}

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_ready_when_critical_ok(self, mock_mesh, mock_rns, mock_mqtt,
                                     mock_hw, mock_nodes):
        """Overall status is ready when all services running."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=True, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=True, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=True, optional=True)
        mock_hw.return_value = HardwareHealth()

        summary = run_health_check()
        assert summary.overall_status == "ready"

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_degraded_when_optional_down(self, mock_mesh, mock_rns, mock_mqtt,
                                          mock_hw, mock_nodes):
        """Overall status is degraded when optional services down."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=True, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=False, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=False, optional=True)
        mock_hw.return_value = HardwareHealth()

        summary = run_health_check()
        assert summary.overall_status == "degraded"

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_error_when_critical_down(self, mock_mesh, mock_rns, mock_mqtt,
                                       mock_hw, mock_nodes):
        """Overall status is error when critical services down."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=False, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=False, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=False, optional=True)
        mock_hw.return_value = HardwareHealth()

        summary = run_health_check()
        assert summary.overall_status == "error"


class TestProfileAwareHealthCheck:
    """Test profile-aware health checking."""

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_profile_sets_required_services(self, mock_mesh, mock_rns, mock_mqtt,
                                             mock_hw, mock_nodes):
        """Profile should override which services are required."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=True, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=False, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=False, optional=True)
        mock_hw.return_value = HardwareHealth()

        # Create a mock profile that requires rnsd
        mock_profile = MagicMock()
        mock_profile.display_name = "Gateway"
        mock_profile.required_services = ["meshtasticd", "rnsd"]
        mock_profile.optional_services = ["mosquitto"]

        summary = run_health_check(profile=mock_profile)
        assert summary.profile_name == "Gateway"

        # rnsd should be marked as required (not optional)
        rnsd = next(s for s in summary.services if s.name == "rnsd")
        assert rnsd.optional is False

        # mosquitto should be optional
        mqtt = next(s for s in summary.services if s.name == "mosquitto")
        assert mqtt.optional is True

    @patch('utils.startup_health.get_node_count', return_value=0)
    @patch('utils.startup_health.detect_hardware')
    @patch('utils.startup_health.check_mosquitto')
    @patch('utils.startup_health.check_rnsd')
    @patch('utils.startup_health.check_meshtasticd')
    def test_monitor_profile_all_optional(self, mock_mesh, mock_rns, mock_mqtt,
                                           mock_hw, mock_nodes):
        """Monitor profile has no required services."""
        mock_mesh.return_value = ServiceHealth(name="meshtasticd", running=False, optional=False)
        mock_rns.return_value = ServiceHealth(name="rnsd", running=False, optional=True)
        mock_mqtt.return_value = ServiceHealth(name="mosquitto", running=False, optional=True)
        mock_hw.return_value = HardwareHealth()

        mock_profile = MagicMock()
        mock_profile.display_name = "Monitor"
        mock_profile.required_services = []
        mock_profile.optional_services = ["mosquitto", "meshtasticd"]

        summary = run_health_check(profile=mock_profile)
        # With no required services, all should be optional
        for svc in summary.services:
            assert svc.optional is True, f"{svc.name} should be optional for monitor"
        # No critical services => critical_ok is True (vacuously)
        # All optional services down => overall is "degraded"
        assert summary.overall_status == "degraded"


class TestPrintHealthSummary:
    """Test health summary formatting."""

    def test_contains_version(self):
        """Output should contain version string."""
        summary = HealthSummary(version="0.5.4-beta")
        output = print_health_summary(summary, use_color=False)
        assert "0.5.4-beta" in output

    def test_contains_profile_name_when_set(self):
        """Output should contain profile name when provided."""
        summary = HealthSummary(version="0.5.4-beta", profile_name="Gateway")
        output = print_health_summary(summary, use_color=False)
        assert "Gateway" in output

    def test_shows_service_status(self):
        """Output should show service names and running state."""
        summary = HealthSummary()
        summary.services = [
            ServiceHealth(name="meshtasticd", running=True, port=4403),
            ServiceHealth(name="rnsd", running=False, optional=True),
        ]
        output = print_health_summary(summary, use_color=False)
        assert "meshtasticd" in output
        assert "rnsd" in output
        assert "running" in output

    def test_ready_status_text(self):
        """Output should show 'Ready' when status is ready."""
        summary = HealthSummary(overall_status="ready")
        output = print_health_summary(summary, use_color=False)
        assert "Ready" in output

    def test_error_status_text(self):
        """Output should show 'Not Ready' when status is error."""
        summary = HealthSummary(overall_status="error")
        output = print_health_summary(summary, use_color=False)
        assert "Not Ready" in output


class TestGetHealthDict:
    """Test health summary serialization."""

    def test_dict_structure(self):
        """Health dict should have expected keys."""
        summary = HealthSummary(
            version="0.5.4-beta",
            overall_status="ready",
        )
        summary.services = [
            ServiceHealth(name="meshtasticd", running=True, port=4403),
        ]
        d = get_health_dict(summary)
        assert d['version'] == "0.5.4-beta"
        assert d['overall_status'] == "ready"
        assert d['is_ready'] is True
        assert len(d['services']) == 1
        assert 'hardware' in d
        assert 'network' in d

    def test_service_dict_structure(self):
        """Service entries in dict should have expected fields."""
        summary = HealthSummary()
        summary.services = [
            ServiceHealth(name="test", running=True, port=1234, optional=False),
        ]
        d = get_health_dict(summary)
        svc = d['services'][0]
        assert svc['name'] == "test"
        assert svc['running'] is True
        assert svc['port'] == 1234
        assert svc['optional'] is False


class TestTrafficLight:
    """Test traffic light indicator."""

    def test_ready_shows_ready(self):
        """Ready status shows READY."""
        summary = HealthSummary(overall_status="ready")
        assert "READY" in get_traffic_light(summary, use_color=False)

    def test_degraded_shows_degraded(self):
        """Degraded status shows DEGRADED."""
        summary = HealthSummary(overall_status="degraded")
        assert "DEGRADED" in get_traffic_light(summary, use_color=False)

    def test_error_shows_not_ready(self):
        """Error status shows NOT READY."""
        summary = HealthSummary(overall_status="error")
        assert "NOT READY" in get_traffic_light(summary, use_color=False)
