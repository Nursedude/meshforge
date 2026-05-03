"""
Tests for RNS zombie shared instance detection.

A "zombie" shared instance is when rnsd appears to be running (systemd reports
active, pgrep finds a PID) but the shared instance socket is not actually
accepting connections. This causes NomadNet and gateway to report "connected"
when they can't actually communicate.

These tests validate the diagnostic chain that detects and reports zombie states:
  - Socket exists in /proc/net/unix but rnsd process is dead
  - rnsd process alive but socket missing (hung during init)
  - rnsd process alive, socket listed, but not responsive (stale auth)
  - False-positive process detection (pgrep matching shell scripts)
  - Status bar correctly downgrades rnsd to STOPPED on zombie detection
  - 3-tier detection fallback: unix socket -> TCP -> UDP

Run: python3 -m pytest tests/test_zombie_shared_instance.py -v
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest

# Import from utils (not src.utils) to match the module's own globals.
# pytest adds both 'src/' and '.' to sys.path, creating separate module
# namespaces for 'utils.X' vs 'src.utils.X'. Patching must target the
# same namespace that the function's __globals__ references.
from utils._port_detection import (
    _check_proc_net_unix,
    _verify_process_cmdline,
    check_process_running,
    get_rns_shared_instance_info,
)
from utils.service_check import check_rns_shared_instance

# Patch target — must match the module's own globals namespace
_PD = 'utils._port_detection'


# =============================================================================
# Zombie diagnostic field (new in get_rns_shared_instance_info)
# =============================================================================


class TestZombieSharedInstance:
    """Tests for zombie shared instance diagnostic field."""

    @patch(f'{_PD}.check_process_running', return_value=True)
    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_rnsd_running_no_socket_returns_diagnostic(
            self, mock_unix, mock_tcp, mock_udp, mock_proc):
        """When rnsd runs but no shared instance found, include diagnostic."""
        result = get_rns_shared_instance_info()

        assert result['available'] is False
        assert result['method'] == 'none'
        assert 'diagnostic' in result
        assert 'shared_instance_type' in result['diagnostic']

    @patch(f'{_PD}.check_process_running', return_value=False)
    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_rnsd_not_running_no_diagnostic(
            self, mock_unix, mock_tcp, mock_udp, mock_proc):
        """When rnsd is not running, no zombie diagnostic needed."""
        result = get_rns_shared_instance_info()

        assert result['available'] is False
        assert result['method'] == 'none'
        assert 'diagnostic' not in result

    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_socket_available_no_zombie_check(self, mock_unix):
        """When shared instance is available, skip zombie detection."""
        result = get_rns_shared_instance_info()

        assert result['available'] is True
        assert result['method'] == 'unix_socket'
        assert 'diagnostic' not in result


# =============================================================================
# Zombie detection: socket vs process state matrix
# =============================================================================


class TestZombieDetection:
    """Test the state matrix of process-alive vs socket-available."""

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_socket_exists_returns_available(self, mock_unix, mock_tcp, mock_udp):
        """Socket in /proc/net/unix => shared instance available."""
        info = get_rns_shared_instance_info()
        assert info['available'] is True
        assert info['method'] == 'unix_socket'
        assert '@rns/default' in info['detail']

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_no_socket_no_ports_returns_unavailable(self, mock_unix, mock_tcp, mock_udp):
        """No socket, no ports => shared instance not available."""
        info = get_rns_shared_instance_info()
        assert info['available'] is False
        assert info['method'] == 'none'

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=True)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_tcp_fallback_when_no_socket(self, mock_unix, mock_tcp, mock_udp):
        """TCP port listening but no unix socket => fallback to TCP."""
        info = get_rns_shared_instance_info()
        assert info['available'] is True
        assert info['method'] == 'tcp'

    @patch(f'{_PD}.check_udp_port', return_value=True)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_udp_fallback_when_no_socket_no_tcp(self, mock_unix, mock_tcp, mock_udp):
        """UDP port open, no socket, no TCP => fallback to UDP."""
        info = get_rns_shared_instance_info()
        assert info['available'] is True
        assert info['method'] == 'udp'

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_detail_includes_all_checked_methods(self, mock_unix, mock_tcp, mock_udp):
        """When unavailable, detail should list what was checked."""
        info = get_rns_shared_instance_info()
        assert '@rns/default' in info['detail']
        assert 'TCP' in info['detail']
        assert 'UDP' in info['detail']

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=True)
    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_unix_socket_takes_priority_over_tcp(self, mock_unix, mock_tcp, mock_udp):
        """Unix socket should win even if TCP port is also open."""
        info = get_rns_shared_instance_info()
        assert info['method'] == 'unix_socket'

    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_custom_instance_name(self, mock_unix):
        """Non-default instance name appears in socket check."""
        info = get_rns_shared_instance_info(instance_name='custom')
        mock_unix.assert_called_with('rns/custom')
        assert '@rns/custom' in info['detail']


# =============================================================================
# /proc/net/unix socket scanning
# =============================================================================


class TestProcNetUnix:
    """Test passive socket detection via /proc/net/unix."""

    SAMPLE_PROC_NET_UNIX = """\
Num       RefCount Protocol Flags    Type St Inode Path
ffff0000 00000002 00000000 00010000 0001 01 12345 /var/run/dbus/system_bus_socket
ffff0001 00000002 00000000 00010000 0001 01 12346 @rns/default
ffff0002 00000002 00000000 00010000 0001 01 12347 /tmp/.X11-unix/X0
"""

    def test_finds_rns_default_socket(self):
        """Detects @rns/default in /proc/net/unix."""
        with patch('builtins.open', mock_open(read_data=self.SAMPLE_PROC_NET_UNIX)):
            assert _check_proc_net_unix('rns/default') is True

    def test_missing_socket_returns_false(self):
        """Returns False when socket name not present."""
        with patch('builtins.open', mock_open(read_data=self.SAMPLE_PROC_NET_UNIX)):
            assert _check_proc_net_unix('rns/nonexistent') is False

    def test_handles_proc_not_readable(self):
        """Graceful fallback when /proc/net/unix can't be read."""
        with patch('builtins.open', side_effect=OSError("Permission denied")):
            assert _check_proc_net_unix('rns/default') is False

    def test_empty_proc_net_unix(self):
        """Handles empty /proc/net/unix file."""
        with patch('builtins.open', mock_open(read_data="")):
            assert _check_proc_net_unix('rns/default') is False

    def test_substring_match_behavior(self):
        """Document: _check_proc_net_unix uses substring match.

        @rns/default will match @rns/default_backup. This is a known
        characteristic -- abstract socket names in practice don't collide
        because RNS only creates @rns/{instance_name} sockets.
        """
        data = "ffff0000 00000002 00000000 00010000 0001 01 12345 @rns/default_backup\n"
        with patch('builtins.open', mock_open(read_data=data)):
            result = _check_proc_net_unix('rns/default')
            assert result is True  # Documents current substring behavior

    def test_abstract_socket_cleanup_on_crash(self):
        """Abstract unix sockets are auto-cleaned by kernel on process exit.

        Unlike filesystem sockets, abstract sockets (\\0rns/default) vanish
        when the last fd is closed. The zombie case is "process alive but
        socket missing" (hung during init), not "socket exists but process dead".
        """
        data = "ffff0000 00000002 00000000 00010000 0001 01 12345 /var/run/dbus\n"
        with patch('builtins.open', mock_open(read_data=data)):
            assert _check_proc_net_unix('rns/default') is False


# =============================================================================
# Process cmdline verification (Issue #32 false-positive prevention)
# =============================================================================


class TestVerifyProcessCmdline:
    """Test /proc/{pid}/cmdline verification to prevent false positives."""

    def test_genuine_rnsd_binary(self):
        """Matches /usr/bin/rnsd as genuine rnsd process."""
        cmdline = b'/usr/bin/rnsd\0--config\0/etc/reticulum\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is True

    def test_python_module_invocation(self):
        """Matches python3 -m rnsd as genuine rnsd process."""
        cmdline = b'/usr/bin/python3\0-m\0rnsd\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is True

    def test_rejects_shell_script_mentioning_rnsd(self):
        """Shell script containing 'rnsd' as argument should NOT match."""
        cmdline = b'bash\0-c\0systemctl restart rnsd\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is False

    def test_rejects_editor_with_rnsd_in_path(self):
        """Editor opening rnsd-related file should NOT match."""
        cmdline = b'vim\0/etc/systemd/system/rnsd.service\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is False

    def test_grep_for_rnsd_matches_as_basename(self):
        """grep with 'rnsd' as standalone arg matches because basename('rnsd') == 'rnsd'.

        This is a known characteristic of the cmdline verifier: it checks
        if any arg's basename equals the process name. 'rnsd' as a grep
        pattern arg has basename 'rnsd'. The pgrep regex tier (tier 2)
        prevents this from being a real issue because pgrep won't match
        grep processes with its regex pattern.
        """
        cmdline = b'grep\0-r\0rnsd\0/var/log\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is True

    def test_rejects_superrnsd(self):
        """Binary named /usr/bin/superrnsd should NOT match rnsd."""
        cmdline = b'/usr/bin/superrnsd\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is False

    def test_rnsd_in_path_subdir(self):
        """Matches rnsd even when invoked from deep path."""
        cmdline = b'/opt/rns/venv/bin/rnsd\0--verbose\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('1234', 'rnsd') is True

    def test_handles_proc_not_readable(self):
        """Returns False when /proc/{pid}/cmdline is not readable."""
        with patch('builtins.open', side_effect=OSError("No such file")):
            assert _verify_process_cmdline('1234', 'rnsd') is False

    def test_rejects_non_numeric_pid(self):
        """Non-numeric PID string rejected immediately."""
        assert _verify_process_cmdline('abc', 'rnsd') is False

    def test_rejects_empty_pid(self):
        """Empty PID string rejected."""
        assert _verify_process_cmdline('', 'rnsd') is False

    def test_nomadnet_detection(self):
        """Verifies nomadnet process detection (same pattern as rnsd)."""
        cmdline = b'/usr/bin/python3\0-m\0nomadnet\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('5678', 'nomadnet') is True

    def test_rejects_test_runner_mentioning_rnsd(self):
        """pytest running rnsd tests should NOT match as rnsd process."""
        cmdline = b'/usr/bin/python3\0-m\0pytest\0tests/test_rnsd.py\0'
        with patch('builtins.open', mock_open(read_data=cmdline)):
            assert _verify_process_cmdline('9999', 'rnsd') is False


# =============================================================================
# check_process_running -- 3-tier detection with verification
# =============================================================================


class TestCheckProcessRunning:
    """Test 3-tier process detection with cmdline verification."""

    @patch('subprocess.run')
    def test_exact_match_pgrep_x(self, mock_run):
        """Tier 1: pgrep -x finds exact binary name."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        assert check_process_running('rnsd') is True
        assert mock_run.call_args_list[0][0][0] == ['pgrep', '-x', 'rnsd']

    @patch(f'{_PD}._verify_process_cmdline', return_value=True)
    @patch('subprocess.run')
    def test_falls_through_to_flexible_match(self, mock_run, mock_verify):
        """Tier 2: flexible pgrep -f with cmdline verification."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),      # pgrep -x
            MagicMock(returncode=0, stdout="5678\n"),  # pgrep -f
        ]
        assert check_process_running('rnsd') is True

    @patch(f'{_PD}._verify_process_cmdline', return_value=False)
    @patch('subprocess.run')
    def test_flexible_match_rejected_by_cmdline(self, mock_run, mock_verify):
        """Tier 2: pgrep -f match rejected by cmdline verification."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),      # pgrep -x
            MagicMock(returncode=0, stdout="5678\n"),  # pgrep -f
            MagicMock(returncode=1, stdout=""),      # tier 3 pgrep
        ]
        assert check_process_running('rnsd') is False

    @patch(f'{_PD}._verify_process_cmdline', return_value=True)
    @patch('subprocess.run')
    def test_python_tier3_for_rnsd(self, mock_run, mock_verify):
        """Tier 3: python-specific detection for rnsd."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),      # pgrep -x
            MagicMock(returncode=1, stdout=""),      # pgrep -f regex
            MagicMock(returncode=0, stdout="9999\n"),  # python3 rnsd
        ]
        assert check_process_running('rnsd') is True

    @patch('subprocess.run')
    def test_timeout_returns_false(self, mock_run):
        """Subprocess timeout should return False, not crash."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='pgrep', timeout=5)
        assert check_process_running('rnsd') is False

    @patch('subprocess.run')
    def test_pgrep_not_installed(self, mock_run):
        """Missing pgrep binary returns False."""
        mock_run.side_effect = FileNotFoundError("pgrep not found")
        assert check_process_running('rnsd') is False

    @patch('subprocess.run')
    def test_non_python_process_skips_tier3(self, mock_run):
        """Tier 3 (python-specific) only runs for rnsd/nomadnet."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),  # pgrep -x
            MagicMock(returncode=1, stdout=""),  # pgrep -f
        ]
        assert check_process_running('meshtasticd') is False
        assert mock_run.call_count == 2


# =============================================================================
# Status bar zombie detection integration
# =============================================================================


class TestStatusBarZombieDetection:
    """Test that status bar correctly reports zombie rnsd as STOPPED."""

    @patch('src.launcher_tui.status_bar.check_rns_shared_instance', return_value=False)
    @patch('src.launcher_tui.status_bar.check_systemd_service', return_value=(True, None))
    def test_rnsd_systemd_active_but_no_shared_instance(self, mock_systemd, mock_rns):
        """rnsd active in systemd but shared instance unavailable => STOPPED symbol."""
        from src.launcher_tui.status_bar import StatusBar, SYM_STOPPED
        sb = StatusBar.__new__(StatusBar)
        result = sb._check_systemd_active('rnsd')
        assert result == SYM_STOPPED

    @patch('src.launcher_tui.status_bar.check_rns_shared_instance', return_value=True)
    @patch('src.launcher_tui.status_bar.check_systemd_service', return_value=(True, None))
    def test_rnsd_healthy_shows_running(self, mock_systemd, mock_rns):
        """rnsd active AND shared instance available => RUNNING symbol."""
        from src.launcher_tui.status_bar import StatusBar, SYM_RUNNING
        sb = StatusBar.__new__(StatusBar)
        result = sb._check_systemd_active('rnsd')
        assert result == SYM_RUNNING

    @patch('src.launcher_tui.status_bar.check_systemd_service', return_value=(False, None))
    def test_rnsd_systemd_inactive_shows_stopped(self, mock_systemd):
        """rnsd not active in systemd => STOPPED (no shared instance check needed)."""
        from src.launcher_tui.status_bar import StatusBar, SYM_STOPPED
        sb = StatusBar.__new__(StatusBar)
        result = sb._check_systemd_active('rnsd')
        assert result == SYM_STOPPED

    @patch('src.launcher_tui.status_bar.check_systemd_service', return_value=(True, None))
    def test_non_rnsd_service_skips_zombie_check(self, mock_systemd):
        """Non-rnsd services (meshtasticd) don't need zombie detection."""
        from src.launcher_tui.status_bar import StatusBar, SYM_RUNNING
        sb = StatusBar.__new__(StatusBar)
        result = sb._check_systemd_active('meshtasticd')
        assert result == SYM_RUNNING


# =============================================================================
# Diagnostic scenario: rnsd hung during initialization
# =============================================================================


class TestRnsdHungDuringInit:
    """Diagnostic tests for rnsd that starts but never opens the shared socket.

    This happens when rnsd encounters a blocking interface during startup
    (e.g., serial port locked by another process) and hangs in __init__.
    """

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    @patch('subprocess.run')
    def test_process_alive_socket_missing(self, mock_run, mock_unix, mock_tcp, mock_udp):
        """rnsd PID exists but no socket anywhere => hung during init."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1234\n")
        assert check_process_running('rnsd') is True

        info = get_rns_shared_instance_info()
        assert info['available'] is False

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix')
    def test_socket_appears_after_delay(self, mock_unix, mock_tcp, mock_udp):
        """Simulate rnsd finishing init: socket absent then present."""
        mock_unix.side_effect = [False, True]

        info1 = get_rns_shared_instance_info()
        assert info1['available'] is False

        info2 = get_rns_shared_instance_info()
        assert info2['available'] is True
        assert info2['method'] == 'unix_socket'


# =============================================================================
# check_rns_shared_instance() convenience wrapper
# =============================================================================


class TestCheckRnsSharedInstance:
    """Test the boolean convenience wrapper."""

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_returns_true_when_available(self, mock_unix, mock_tcp, mock_udp):
        assert check_rns_shared_instance() is True

    @patch(f'{_PD}.check_udp_port', return_value=False)
    @patch(f'{_PD}.check_port', return_value=False)
    @patch(f'{_PD}._check_proc_net_unix', return_value=False)
    def test_returns_false_when_unavailable(self, mock_unix, mock_tcp, mock_udp):
        assert check_rns_shared_instance() is False

    @patch(f'{_PD}._check_proc_net_unix', return_value=True)
    def test_passes_custom_instance_name(self, mock_unix):
        """Custom instance name reaches _check_proc_net_unix correctly."""
        check_rns_shared_instance(instance_name='test')
        mock_unix.assert_called_with('rns/test')


# =============================================================================
# TUI callsites must honor the configured instance_name (fleet-host regression).
# Six TUI handlers default-called check_rns_shared_instance() / get_rns_
# shared_instance_info() with instance_name='default', which produced false
# "shared instance NOT available" warnings on fleet-host (instance_name =
# "volcano ai rns"). Each callsite must now pull the configured name from
# ReticulumPaths.get_configured_instance_name() and pass it explicitly.
# =============================================================================


class TestTUIHandlersHonorConfiguredInstanceName:
    """Regression: TUI handlers must pass configured instance_name."""

    @patch('launcher_tui.status_bar.check_rns_shared_instance', return_value=True)
    @patch('launcher_tui.status_bar.check_systemd_service', return_value=(True, None))
    @patch('utils.paths.ReticulumPaths.get_configured_instance_name',
           return_value='volcano ai rns')
    def test_status_bar_passes_configured_name(
            self, mock_inst, mock_systemd, mock_check):
        from launcher_tui.status_bar import StatusBar
        sb = StatusBar.__new__(StatusBar)
        sb._check_systemd_active('rnsd')
        mock_check.assert_called_with('volcano ai rns')

    @patch('src.launcher_tui.handlers.quick_actions.check_rns_shared_instance',
           return_value=True)
    @patch('src.utils.paths.ReticulumPaths.get_configured_instance_name',
           return_value='volcano ai rns')
    def test_quick_actions_port_check_passes_configured_name(
            self, mock_inst, mock_check):
        # Import the module and reach the call indirectly is heavy; assert
        # the module-level imports are wired such that a direct call site
        # uses the helper. Smoke-test by re-importing and inspecting the
        # source for the configured-name wiring (cheap structural check).
        import src.launcher_tui.handlers.quick_actions as qa
        src = open(qa.__file__).read()
        assert 'ReticulumPaths.get_configured_instance_name()' in src, \
            "quick_actions must call get_configured_instance_name() before check_rns_shared_instance"

    @patch('src.launcher_tui.handlers.service_menu.check_rns_shared_instance',
           return_value=True)
    @patch('src.utils.paths.ReticulumPaths.get_configured_instance_name',
           return_value='volcano ai rns')
    def test_service_menu_passes_configured_name(self, mock_inst, mock_check):
        import src.launcher_tui.handlers.service_menu as sm
        src = open(sm.__file__).read()
        assert 'ReticulumPaths.get_configured_instance_name()' in src, \
            "service_menu must pass configured instance_name to check_rns_shared_instance"

    def test_nomadnet_passes_configured_name(self):
        import src.launcher_tui.handlers.nomadnet as nm
        src = open(nm.__file__).read()
        assert 'ReticulumPaths.get_configured_instance_name()' in src, \
            "nomadnet must pass configured instance_name to get_rns_shared_instance_info"

    def test_rns_diagnostics_passes_configured_name(self):
        import src.launcher_tui.handlers.rns_diagnostics as rd
        src = open(rd.__file__).read()
        assert 'ReticulumPaths.get_configured_instance_name()' in src, \
            "rns_diagnostics must pass configured instance_name to get_rns_shared_instance_info"

    def test_rns_diagnostics_engine_passes_configured_name(self):
        import src.launcher_tui.handlers._rns_diagnostics_engine as rde
        src = open(rde.__file__).read()
        assert 'ReticulumPaths.get_configured_instance_name()' in src, \
            "_rns_diagnostics_engine must pass configured instance_name"

    @patch(f'{_PD}._check_proc_net_unix')
    def test_multi_word_instance_name_threads_through_to_socket_check(
            self, mock_unix):
        """fleet-host's 'volcano ai rns' must reach _check_proc_net_unix verbatim."""
        mock_unix.return_value = True
        check_rns_shared_instance(instance_name='volcano ai rns')
        mock_unix.assert_called_with('rns/volcano ai rns')
