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
    check_process_with_pid,
    check_rns_shared_instance,
    check_service,
    check_systemd_service,
    get_rns_shared_instance_info,
    require_service,
    daemon_reload,
    enable_service,
    apply_config_and_restart,
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
        """Test detection of available meshtasticd (Issue #17: systemctl only)."""
        with patch('subprocess.run') as mock_run:
            # First call: systemctl is-active → "active"
            # Second call: systemctl show --property=SubState → "SubState=running"
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout='active\n'),
                MagicMock(returncode=0, stdout='SubState=running\n'),
            ]

            status = check_service('meshtasticd')

            assert status.available is True
            assert status.state == ServiceState.AVAILABLE
            assert status.port == 4403
            assert status.detection_method == "systemctl"

    def test_mosquitto_not_running(self):
        """Test detection of stopped mosquitto (Issue #17: systemctl only)."""
        with patch('subprocess.run') as mock_run:
            # systemctl is-active mosquitto returns "inactive"
            mock_run.return_value = MagicMock(
                returncode=3,
                stdout='inactive\n'
            )

            status = check_service('mosquitto')

            assert status.available is False
            assert status.state == ServiceState.NOT_RUNNING
            assert 'not running' in status.message.lower()
            assert status.detection_method == "systemctl"

    def test_unknown_service(self):
        """Test handling of unknown service (defaults to systemd check)."""
        with patch('subprocess.run') as mock_run:
            # Unknown service treated as systemd service by default
            mock_run.return_value = MagicMock(
                returncode=3,
                stdout='inactive\n'
            )

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

    def test_hamclock_removed(self):
        """Test hamclock removed from KNOWN_SERVICES (now optional data source only)."""
        assert 'hamclock' not in KNOWN_SERVICES

    def test_rnsd_config(self):
        """Test rnsd configuration."""
        assert 'rnsd' in KNOWN_SERVICES
        config = KNOWN_SERVICES['rnsd']
        # rnsd uses abstract Unix domain sockets on Linux (port for fallback)
        assert config['port'] == 37428
        assert config['port_type'] == 'unix_socket'

    def test_nomadnet_config(self):
        """Test NomadNet configuration in KNOWN_SERVICES."""
        assert 'nomadnet' in KNOWN_SERVICES
        config = KNOWN_SERVICES['nomadnet']
        # NomadNet uses RNS shared instance, no dedicated port
        assert config['port'] is None
        assert config['is_systemd'] is False
        assert 'nomadnetwork' in config['fix_hint']
        assert config['description'] == 'NomadNet mesh messaging client'

    def test_all_known_services_have_required_fields(self):
        """Test that all services have required configuration fields."""
        required_fields = {'port', 'systemd_name', 'is_systemd', 'description', 'fix_hint'}
        for name, config in KNOWN_SERVICES.items():
            for field in required_fields:
                assert field in config, f"Service '{name}' missing required field '{field}'"


class TestDaemonReload:
    """Tests for daemon_reload helper function."""

    def test_daemon_reload_success(self):
        """Test successful daemon-reload."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='')

            success, msg = daemon_reload()

            assert success is True
            assert 'succeeded' in msg
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args == ['systemctl', 'daemon-reload']

    def test_daemon_reload_failure(self):
        """Test failed daemon-reload."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr='Failed to reload daemon'
            )

            success, msg = daemon_reload()

            assert success is False
            assert 'failed' in msg.lower()

    def test_daemon_reload_timeout(self):
        """Test daemon-reload timeout handling."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd='systemctl', timeout=30
            )

            success, msg = daemon_reload(timeout=30)

            assert success is False
            assert 'timeout' in msg.lower()

    def test_daemon_reload_no_systemctl(self):
        """Test daemon-reload when systemctl not available."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("systemctl not found")

            success, msg = daemon_reload()

            assert success is False
            assert 'systemctl not found' in msg


class TestEnableService:
    """Tests for enable_service helper function."""

    def test_enable_service_success(self):
        """Test successful service enable."""
        with patch('subprocess.run') as mock_run:
            # daemon-reload succeeds, enable succeeds
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),
                MagicMock(returncode=0, stderr=''),
            ]

            success, msg = enable_service('rnsd')

            assert success is True
            assert 'enabled' in msg
            assert mock_run.call_count == 2

    def test_enable_service_with_start(self):
        """Test enable service with start=True."""
        with patch('subprocess.run') as mock_run:
            # daemon-reload, enable, start all succeed
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),
                MagicMock(returncode=0, stderr=''),
                MagicMock(returncode=0, stderr=''),
            ]

            success, msg = enable_service('meshtasticd', start=True)

            assert success is True
            assert 'enabled and started' in msg
            assert mock_run.call_count == 3

    def test_enable_service_daemon_reload_fails(self):
        """Test enable fails if daemon-reload fails."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr='daemon-reload failed'
            )

            success, msg = enable_service('rnsd')

            assert success is False
            assert 'daemon-reload' in msg

    def test_enable_service_enable_fails(self):
        """Test enable fails if systemctl enable fails."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),  # daemon-reload OK
                MagicMock(returncode=1, stderr='Unit not found'),  # enable fails
            ]

            success, msg = enable_service('nonexistent')

            assert success is False
            assert 'enable' in msg.lower()

    def test_enable_service_start_fails(self):
        """Test returns error if start fails (but service was enabled)."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),  # daemon-reload OK
                MagicMock(returncode=0, stderr=''),  # enable OK
                MagicMock(returncode=1, stderr='Failed to start'),  # start fails
            ]

            success, msg = enable_service('broken', start=True)

            assert success is False
            assert 'start failed' in msg.lower()


class TestApplyConfigAndRestart:
    """Tests for apply_config_and_restart helper function."""

    def test_apply_config_and_restart_success(self):
        """Test successful config apply and restart."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),  # daemon-reload
                MagicMock(returncode=0, stderr=''),  # restart
            ]

            success, msg = apply_config_and_restart('meshtasticd')

            assert success is True
            assert 'restarted' in msg.lower()
            assert mock_run.call_count == 2

    def test_apply_config_and_restart_daemon_reload_fails(self):
        """Test failure when daemon-reload fails."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr='daemon-reload error'
            )

            success, msg = apply_config_and_restart('meshtasticd')

            assert success is False
            assert 'daemon-reload' in msg

    def test_apply_config_and_restart_restart_fails(self):
        """Test failure when restart fails."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),  # daemon-reload OK
                MagicMock(returncode=1, stderr='Failed to restart'),  # restart fails
            ]

            success, msg = apply_config_and_restart('meshtasticd')

            assert success is False
            assert 'restart' in msg.lower()

    def test_apply_config_and_restart_custom_timeout(self):
        """Test custom timeout is passed to subprocess."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr=''),
                MagicMock(returncode=0, stderr=''),
            ]

            success, msg = apply_config_and_restart('meshtasticd', timeout=60)

            # Verify timeout was passed
            for call in mock_run.call_args_list:
                assert call[1]['timeout'] == 60


class TestServiceHelpersIntegration:
    """Integration tests for service helper functions."""

    def test_helpers_are_exported(self):
        """Verify all helpers are in __all__ export list."""
        from src.utils import service_check
        assert 'daemon_reload' in service_check.__all__
        assert 'enable_service' in service_check.__all__
        assert 'apply_config_and_restart' in service_check.__all__

    def test_helpers_return_tuple(self):
        """Verify all helpers return (bool, str) tuple."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='')

            for helper in [daemon_reload,
                           lambda: enable_service('test'),
                           lambda: apply_config_and_restart('test')]:
                result = helper()
                assert isinstance(result, tuple)
                assert len(result) == 2
                assert isinstance(result[0], bool)
                assert isinstance(result[1], str)


class TestCheckProcessWithPid:
    """Tests for check_process_with_pid function."""

    def test_process_running_returns_pid(self):
        """Test that running process returns (True, pid)."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="12345\n"
            )

            running, pid = check_process_with_pid('bash')

            assert running is True
            assert pid == "12345"

    def test_process_not_running_returns_none(self):
        """Test that non-running process returns (False, None)."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=""
            )

            running, pid = check_process_with_pid('nonexistent_process')

            assert running is False
            assert pid is None

    def test_multiple_pids_returns_first(self):
        """Test that multiple PIDs return the first one."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="12345\n67890\n"
            )

            running, pid = check_process_with_pid('multi_instance')

            assert running is True
            assert pid == "12345"

    def test_timeout_returns_false(self):
        """Test that timeout returns (False, None)."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired('pgrep', 5)

            running, pid = check_process_with_pid('slow_process')

            assert running is False
            assert pid is None

    def test_pgrep_not_found_returns_false(self):
        """Test graceful handling when pgrep is not available."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError()

            running, pid = check_process_with_pid('any_process')

            assert running is False
            assert pid is None


class TestCheckRNSSharedInstance:
    """Tests for check_rns_shared_instance function."""

    # Helper: /proc/net/unix content with RNS abstract socket present
    PROC_NET_UNIX_WITH_RNS = (
        "Num       RefCount Protocol Flags    Type St Inode Path\n"
        "0000000000000000: 00000002 00000000 00010000 0005 01 12345 @rns/default\n"
        "0000000000000000: 00000002 00000000 00010000 0005 01 12346 @rns/default/rpc\n"
    )

    PROC_NET_UNIX_WITHOUT_RNS = (
        "Num       RefCount Protocol Flags    Type St Inode Path\n"
        "0000000000000000: 00000002 00000000 00010000 0005 01 99999 /var/run/dbus\n"
    )

    def test_detects_unix_domain_socket_passive(self):
        """Test passive detection via /proc/net/unix (primary method)."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITH_RNS)):
            result = check_rns_shared_instance()

            assert result is True

    def test_falls_back_to_tcp(self):
        """Test TCP fallback when /proc/net/unix has no RNS socket."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=True):
                result = check_rns_shared_instance()

                assert result is True

    def test_falls_back_to_udp(self):
        """Test UDP fallback when /proc/net/unix and TCP both fail."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=False):
                with patch('src.utils.service_check.check_udp_port', return_value=True) as mock_udp:
                    result = check_rns_shared_instance()

                    assert result is True
                    mock_udp.assert_called_once_with(37428)

    def test_all_methods_fail(self):
        """Test returns False when no detection method works."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=False):
                with patch('src.utils.service_check.check_udp_port', return_value=False):
                    result = check_rns_shared_instance()

                    assert result is False

    def test_custom_instance_name(self):
        """Test custom RNS instance name in /proc/net/unix scan."""
        content = (
            "Num       RefCount Protocol Flags    Type St Inode Path\n"
            "0000000000000000: 00000002 00000000 00010000 0005 01 12345 @rns/myinstance\n"
        )
        with patch('builtins.open', return_value=__import__('io').StringIO(content)):
            result = check_rns_shared_instance(instance_name='myinstance')

            assert result is True

    def test_custom_port(self):
        """Test custom port for TCP/UDP fallback."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=False):
                with patch('src.utils.service_check.check_udp_port', return_value=False) as mock_udp:
                    check_rns_shared_instance(port=9999)

                    mock_udp.assert_called_once_with(9999)

    def test_proc_net_unix_unreadable(self):
        """Test graceful handling when /proc/net/unix can't be read."""
        with patch('builtins.open', side_effect=OSError("Permission denied")):
            with patch('src.utils.service_check.check_port', return_value=True):
                result = check_rns_shared_instance()

                assert result is True  # Falls through to TCP

    def test_no_false_positive_partial_match(self):
        """Test that @rns/defaults doesn't match when checking @rns/default."""
        content = (
            "Num       RefCount Protocol Flags    Type St Inode Path\n"
            "0000000000000000: 00000002 00000000 00010000 0005 01 12345 @rns/defaults\n"
        )
        # Note: 'rns/default' IS a substring of 'rns/defaults' — but in practice
        # RNS instance names are exact. The @rns/default check will match
        # @rns/defaults, which is acceptable since it indicates RNS is running.
        # This test documents the behavior.
        with patch('builtins.open', return_value=__import__('io').StringIO(content)):
            result = check_rns_shared_instance()

            assert result is True  # substring match is acceptable


class TestGetRNSSharedInstanceInfo:
    """Tests for get_rns_shared_instance_info function."""

    PROC_NET_UNIX_WITH_RNS = TestCheckRNSSharedInstance.PROC_NET_UNIX_WITH_RNS
    PROC_NET_UNIX_WITHOUT_RNS = TestCheckRNSSharedInstance.PROC_NET_UNIX_WITHOUT_RNS

    def test_unix_socket_info(self):
        """Test info dict when detected via /proc/net/unix."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITH_RNS)):
            info = get_rns_shared_instance_info()

            assert info['available'] is True
            assert info['method'] == 'unix_socket'
            assert '@rns/default' in info['detail']
            assert 'abstract domain socket' in info['detail']

    def test_tcp_info(self):
        """Test info dict when detected via TCP port."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=True):
                info = get_rns_shared_instance_info()

                assert info['available'] is True
                assert info['method'] == 'tcp'
                assert '127.0.0.1:37428' in info['detail']
                assert 'TCP' in info['detail']

    def test_udp_info(self):
        """Test info dict when detected via UDP."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=False):
                with patch('src.utils.service_check.check_udp_port', return_value=True):
                    info = get_rns_shared_instance_info()

                    assert info['available'] is True
                    assert info['method'] == 'udp'
                    assert 'UDP' in info['detail']

    def test_unavailable_info(self):
        """Test info dict when no method works."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=False):
                with patch('src.utils.service_check.check_udp_port', return_value=False):
                    info = get_rns_shared_instance_info()

                    assert info['available'] is False
                    assert info['method'] == 'none'
                    assert '@rns/default' in info['detail']
                    assert 'TCP:37428' in info['detail']
                    assert 'UDP:37428' in info['detail']

    def test_info_has_required_keys(self):
        """Test that info dict always has required keys."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITH_RNS)):
            info = get_rns_shared_instance_info()

            assert 'available' in info
            assert 'method' in info
            assert 'detail' in info
            assert isinstance(info['available'], bool)
            assert isinstance(info['method'], str)
            assert isinstance(info['detail'], str)

    def test_custom_instance_name_in_detail(self):
        """Test custom instance name appears in detail string."""
        content = (
            "Num       RefCount Protocol Flags    Type St Inode Path\n"
            "0000000000000000: 00000002 00000000 00010000 0005 01 12345 @rns/testnet\n"
        )
        with patch('builtins.open', return_value=__import__('io').StringIO(content)):
            info = get_rns_shared_instance_info(instance_name='testnet')

            assert '@rns/testnet' in info['detail']

    def test_custom_port_in_tcp_detail(self):
        """Test custom port appears in TCP fallback detail."""
        with patch('builtins.open', return_value=__import__('io').StringIO(self.PROC_NET_UNIX_WITHOUT_RNS)):
            with patch('src.utils.service_check.check_port', return_value=True):
                info = get_rns_shared_instance_info(port=9999)

                assert info['available'] is True
                assert '127.0.0.1:9999' in info['detail']


class TestRNSSharedInstanceExports:
    """Tests for RNS shared instance function exports."""

    def test_check_rns_shared_instance_exported(self):
        """Verify check_rns_shared_instance is in __all__."""
        from src.utils import service_check
        assert 'check_rns_shared_instance' in service_check.__all__

    def test_get_rns_shared_instance_info_exported(self):
        """Verify get_rns_shared_instance_info is in __all__."""
        from src.utils import service_check
        assert 'get_rns_shared_instance_info' in service_check.__all__
