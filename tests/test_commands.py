"""
Commands Layer Tests

Tests the unified command interface used by both GTK and CLI.
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from commands import meshtastic, service, hardware, gateway
from commands.base import CommandResult, ResultStatus


class TestCommandResult:
    """Test CommandResult base class."""

    def test_ok_result(self):
        """Test creating a successful result."""
        result = CommandResult.ok("Success", data={'key': 'value'})
        assert result.success is True
        assert result.status == ResultStatus.SUCCESS
        assert result.message == "Success"
        assert result.data == {'key': 'value'}
        assert bool(result) is True

    def test_fail_result(self):
        """Test creating a failed result."""
        result = CommandResult.fail("Failed", error="Error details")
        assert result.success is False
        assert result.status == ResultStatus.ERROR
        assert result.error == "Error details"
        assert bool(result) is False

    def test_warn_result(self):
        """Test creating a warning result."""
        result = CommandResult.warn("Warning", data={'partial': True})
        assert result.success is True  # Warnings are still "successful"
        assert result.status == ResultStatus.WARNING
        assert result.data.get('partial') is True

    def test_not_available_result(self):
        """Test creating a not-available result."""
        result = CommandResult.not_available(
            "Tool not found",
            fix_hint="Install with: apt install tool"
        )
        assert result.success is False
        assert result.status == ResultStatus.NOT_AVAILABLE
        assert "apt install" in result.data.get('fix_hint', '')


class TestMeshtasticCommands:
    """Test Meshtastic command module."""

    def test_is_available(self):
        """Test checking if meshtastic CLI is available."""
        result = meshtastic.is_available()
        assert isinstance(result, bool)

    def test_get_connection(self):
        """Test getting connection config."""
        conn = meshtastic.get_connection()
        assert hasattr(conn, 'type')
        assert hasattr(conn, 'value')

    def test_set_connection(self):
        """Test setting connection."""
        result = meshtastic.set_connection("localhost", "127.0.0.1")
        assert result.success is True

        conn = meshtastic.get_connection()
        assert conn.type == "localhost"
        assert conn.value == "127.0.0.1"

    def test_set_invalid_connection_type(self):
        """Test invalid connection type."""
        result = meshtastic.set_connection("invalid", "value")
        assert result.success is False


class TestServiceCommands:
    """Test service command module."""

    def test_check_status_known_service(self):
        """Test checking status of known service."""
        result = service.check_status('meshtasticd')
        assert isinstance(result, CommandResult)
        assert 'running' in result.data
        assert 'enabled' in result.data
        assert 'description' in result.data

    def test_check_status_unknown_service(self):
        """Test checking status of unknown service."""
        result = service.check_status('nonexistent_service_xyz')
        assert isinstance(result, CommandResult)
        assert result.data.get('running') is False

    def test_list_all(self):
        """Test listing all known services."""
        result = service.list_all()
        assert result.success is True
        assert 'services' in result.data
        assert 'meshtasticd' in result.data['services']

    def test_get_logs(self):
        """Test getting service logs."""
        result = service.get_logs('meshtasticd', lines=5)
        assert isinstance(result, CommandResult)
        # Should succeed even if no logs (just empty)


class TestHardwareCommands:
    """Test hardware command module."""

    def test_check_spi(self):
        """Test SPI detection."""
        result = hardware.check_spi()
        assert isinstance(result, CommandResult)
        assert 'enabled' in result.data

    def test_check_i2c(self):
        """Test I2C detection."""
        result = hardware.check_i2c()
        assert isinstance(result, CommandResult)
        assert 'enabled' in result.data

    def test_check_gpio(self):
        """Test GPIO detection."""
        result = hardware.check_gpio()
        assert isinstance(result, CommandResult)
        assert 'available' in result.data

    def test_scan_serial_ports(self):
        """Test serial port scanning."""
        result = hardware.scan_serial_ports()
        assert isinstance(result, CommandResult)
        assert 'ports' in result.data
        assert 'count' in result.data

    def test_detect_devices(self):
        """Test comprehensive device detection."""
        result = hardware.detect_devices()
        assert isinstance(result, CommandResult)
        assert 'spi' in result.data
        assert 'i2c' in result.data
        assert 'serial' in result.data
        assert 'summary' in result.data

    def test_get_platform_info(self):
        """Test platform info retrieval."""
        result = hardware.get_platform_info()
        assert result.success is True
        assert 'system' in result.data
        assert 'machine' in result.data
        assert 'python_version' in result.data


class TestGatewayCommands:
    """Test gateway command module."""

    def test_is_available(self):
        """Test checking if gateway is available."""
        result = gateway.is_available()
        assert isinstance(result, bool)

    def test_get_config(self):
        """Test getting gateway config."""
        result = gateway.get_config()
        # May fail if config not found, but should return CommandResult
        assert isinstance(result, CommandResult)

    def test_check_prerequisites(self):
        """Test checking gateway prerequisites."""
        result = gateway.check_prerequisites()
        assert isinstance(result, CommandResult)
        assert 'checks' in result.data
        # Check for expected prerequisite keys
        checks = result.data['checks']
        assert 'meshtasticd' in checks
        assert 'rnsd' in checks

    def test_get_status_when_not_running(self):
        """Test getting status when gateway is not running."""
        result = gateway.get_status()
        assert isinstance(result, CommandResult)
        # Should have status info even if not running


class TestIntegration:
    """Integration tests for commands layer."""

    def test_commands_module_exports(self):
        """Test that main commands module exports correctly."""
        from commands import meshtastic, service, hardware, gateway
        from commands import CommandResult, CommandError

        # All modules should be importable
        assert meshtastic is not None
        assert service is not None
        assert hardware is not None
        assert gateway is not None
        assert CommandResult is not None
        assert CommandError is not None

    def test_consistent_result_format(self):
        """Test that all commands return consistent result format."""
        results = [
            service.check_status('meshtasticd'),
            hardware.check_spi(),
            hardware.detect_devices(),
            gateway.get_status(),
        ]

        for result in results:
            assert isinstance(result, CommandResult)
            assert hasattr(result, 'success')
            assert hasattr(result, 'message')
            assert hasattr(result, 'data')
            assert isinstance(result.data, dict)
