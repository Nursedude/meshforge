"""
Tests for service availability checker utility.

Run: python3 -m pytest tests/test_service_check.py -v
"""

import pytest
import socket
from unittest.mock import patch, MagicMock
import subprocess

from src.utils.service_check import (
    check_port,
    check_service,
    check_systemd_service,
    require_service,
    ServiceState,
    ServiceStatus,
    KNOWN_SERVICES,
)


class TestCheckPort:
    """Tests for check_port function."""

    def test_port_open(self):
        """Test detection of open port."""
        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value = mock_sock
            mock_sock.connect_ex.return_value = 0

            result = check_port(8080)

            assert result is True
            mock_sock.settimeout.assert_called_once_with(2.0)
            mock_sock.connect_ex.assert_called_once_with(('localhost', 8080))
            mock_sock.close.assert_called_once()

    def test_port_closed(self):
        """Test detection of closed port."""
        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value = mock_sock
            mock_sock.connect_ex.return_value = 111  # Connection refused

            result = check_port(8080)

            assert result is False

    def test_port_timeout(self):
        """Test handling of connection timeout."""
        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value = mock_sock
            mock_sock.connect_ex.side_effect = socket.timeout("timeout")

            result = check_port(8080, timeout=1.0)

            assert result is False

    def test_custom_host(self):
        """Test checking port on custom host."""
        with patch('socket.socket') as mock_socket:
            mock_sock = MagicMock()
            mock_socket.return_value = mock_sock
            mock_sock.connect_ex.return_value = 0

            result = check_port(8080, host='192.168.1.100')

            mock_sock.connect_ex.assert_called_once_with(('192.168.1.100', 8080))


class TestCheckSystemdService:
    """Tests for check_systemd_service function."""

    def test_service_running_and_enabled(self):
        """Test detection of running and enabled service."""
        with patch('subprocess.run') as mock_run:
            # First call: is-active returns success
            # Second call: is-enabled returns success
            mock_run.side_effect = [
                MagicMock(returncode=0),
                MagicMock(returncode=0),
            ]

            is_running, is_enabled = check_systemd_service('meshtasticd')

            assert is_running is True
            assert is_enabled is True

    def test_service_not_running(self):
        """Test detection of stopped service."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=3),  # inactive
                MagicMock(returncode=0),  # enabled
            ]

            is_running, is_enabled = check_systemd_service('meshtasticd')

            assert is_running is False
            assert is_enabled is True

    def test_systemctl_not_found(self):
        """Test handling when systemctl is not available."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("systemctl not found")

            is_running, is_enabled = check_systemd_service('meshtasticd')

            assert is_running is False
            assert is_enabled is False


class TestCheckService:
    """Tests for check_service function."""

    def test_meshtasticd_available(self):
        """Test detection of available meshtasticd."""
        with patch('src.utils.service_check.check_port') as mock_port:
            mock_port.return_value = True

            status = check_service('meshtasticd')

            assert status.available is True
            assert status.state == ServiceState.AVAILABLE
            assert status.port == 4403
            mock_port.assert_called_once_with(4403, 'localhost')

    def test_hamclock_not_running(self):
        """Test detection of stopped hamclock."""
        with patch('src.utils.service_check.check_port') as mock_port:
            with patch('src.utils.service_check.check_systemd_service') as mock_systemd:
                mock_port.return_value = False
                mock_systemd.return_value = (False, True)  # not running, but enabled

                status = check_service('hamclock')

                assert status.available is False
                assert status.state == ServiceState.NOT_RUNNING
                assert 'not running' in status.message.lower()
                assert 'systemctl start' in status.fix_hint.lower()

    def test_unknown_service(self):
        """Test handling of unknown service."""
        with patch('src.utils.service_check.check_port') as mock_port:
            with patch('src.utils.service_check.check_systemd_service') as mock_systemd:
                mock_port.return_value = False
                mock_systemd.return_value = (False, False)

                status = check_service('unknown_service', port=9999)

                assert status.available is False
                assert status.port == 9999

    def test_service_status_bool(self):
        """Test ServiceStatus boolean conversion."""
        available = ServiceStatus(
            name='test',
            available=True,
            state=ServiceState.AVAILABLE,
            message='running'
        )
        unavailable = ServiceStatus(
            name='test',
            available=False,
            state=ServiceState.NOT_RUNNING,
            message='stopped'
        )

        assert bool(available) is True
        assert bool(unavailable) is False


class TestRequireService:
    """Tests for require_service function."""

    def test_logs_warning_on_unavailable(self):
        """Test that warning is logged when service unavailable."""
        with patch('src.utils.service_check.check_service') as mock_check:
            with patch('src.utils.service_check.logger') as mock_logger:
                mock_check.return_value = ServiceStatus(
                    name='test',
                    available=False,
                    state=ServiceState.NOT_RUNNING,
                    message='Service not running',
                    fix_hint='Start it'
                )

                status = require_service('test')

                assert status.available is False
                mock_logger.warning.assert_called_once()


class TestKnownServices:
    """Tests for known services configuration."""

    def test_meshtasticd_config(self):
        """Test meshtasticd configuration."""
        assert 'meshtasticd' in KNOWN_SERVICES
        config = KNOWN_SERVICES['meshtasticd']
        assert config['port'] == 4403
        assert 'systemctl' in config['fix_hint']

    def test_hamclock_config(self):
        """Test hamclock configuration."""
        assert 'hamclock' in KNOWN_SERVICES
        config = KNOWN_SERVICES['hamclock']
        assert config['port'] == 8080

    def test_rnsd_config(self):
        """Test rnsd configuration."""
        assert 'rnsd' in KNOWN_SERVICES
        config = KNOWN_SERVICES['rnsd']
        # rnsd doesn't use TCP port by default
        assert config['port'] is None
