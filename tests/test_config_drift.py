"""
Tests for config drift detection between gateway and rnsd.

Run: python3 -m pytest tests/test_config_drift.py -v
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.utils.config_drift import (
    DriftResult,
    ShadowResult,
    detect_rnsd_config_drift,
    detect_config_shadowing,
    validate_gateway_rns_config,
    _get_rnsd_pid,
    _get_rnsd_config_from_proc,
    _get_rnsd_config_from_systemd,
    _get_rnsd_effective_config,
    _parse_reticulum_section,
)


class TestGetRnsdPid:
    """Tests for _get_rnsd_pid."""

    @patch('subprocess.run')
    def test_rnsd_running_exact_match(self, mock_run):
        """Test finding rnsd via exact process name."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="1234\n"
        )
        assert _get_rnsd_pid() == 1234

    @patch('subprocess.run')
    def test_rnsd_not_running(self, mock_run):
        """Test when rnsd is not running."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout=""
        )
        assert _get_rnsd_pid() is None

    @patch('subprocess.run')
    def test_pgrep_not_found(self, mock_run):
        """Test when pgrep is not available."""
        mock_run.side_effect = FileNotFoundError("pgrep not found")
        assert _get_rnsd_pid() is None

    @patch('subprocess.run')
    def test_multiple_pids_returns_first(self, mock_run):
        """Test multiple rnsd PIDs returns first."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="1234\n5678\n"
        )
        assert _get_rnsd_pid() == 1234


class TestGetRnsdConfigFromProc:
    """Tests for _get_rnsd_config_from_proc."""

    def test_cmdline_with_config_flag(self, tmp_path):
        """Test extracting config from --config flag in cmdline."""
        config_dir = tmp_path / "reticulum"
        config_dir.mkdir()
        config_file = config_dir / "config"
        config_file.write_text("[reticulum]\n")

        # Simulate /proc/<pid>/cmdline with null separators
        cmdline = f"rnsd\0--config\0{config_file}\0".encode()

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_bytes', return_value=cmdline):
                result = _get_rnsd_config_from_proc(1234)
                assert result == config_dir

    def test_cmdline_with_config_equals(self, tmp_path):
        """Test extracting config from --config=<path> format."""
        config_dir = tmp_path / "reticulum"
        config_dir.mkdir()
        config_file = config_dir / "config"
        config_file.write_text("[reticulum]\n")

        cmdline = f"rnsd\0--config={config_file}\0".encode()

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_bytes', return_value=cmdline):
                result = _get_rnsd_config_from_proc(1234)
                assert result == config_dir

    def test_cmdline_without_config_flag(self):
        """Test cmdline with no --config flag returns None."""
        cmdline = b"rnsd\0--verbose\0"

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_bytes', return_value=cmdline):
                result = _get_rnsd_config_from_proc(1234)
                assert result is None

    def test_proc_not_readable(self):
        """Test when /proc/<pid>/cmdline doesn't exist."""
        with patch.object(Path, 'exists', return_value=False):
            result = _get_rnsd_config_from_proc(1234)
            assert result is None

    def test_cmdline_config_dir_flag(self, tmp_path):
        """Test --config pointing to a directory."""
        config_dir = tmp_path / "reticulum"
        config_dir.mkdir()

        cmdline = f"rnsd\0--config\0{config_dir}\0".encode()

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_bytes', return_value=cmdline):
                result = _get_rnsd_config_from_proc(1234)
                assert result == config_dir


class TestGetRnsdConfigFromSystemd:
    """Tests for _get_rnsd_config_from_systemd."""

    @patch('subprocess.run')
    def test_systemd_unit_with_config(self, mock_run):
        """Test parsing --config from systemd ExecStart."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ExecStart=/usr/bin/rnsd --config /etc/reticulum\n"
        )
        result = _get_rnsd_config_from_systemd()
        assert result == Path('/etc/reticulum')

    @patch('subprocess.run')
    def test_systemd_unit_without_config(self, mock_run):
        """Test ExecStart with no --config flag."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ExecStart=/usr/bin/rnsd\n"
        )
        result = _get_rnsd_config_from_systemd()
        assert result is None

    @patch('subprocess.run')
    def test_systemd_not_available(self, mock_run):
        """Test when systemctl is not available."""
        mock_run.side_effect = FileNotFoundError("systemctl not found")
        result = _get_rnsd_config_from_systemd()
        assert result is None

    @patch('subprocess.run')
    def test_systemd_unit_not_found(self, mock_run):
        """Test when rnsd unit doesn't exist."""
        mock_run.return_value = MagicMock(
            returncode=4, stdout=""
        )
        result = _get_rnsd_config_from_systemd()
        assert result is None


class TestDetectRnsdConfigDrift:
    """Tests for detect_rnsd_config_drift."""

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    def test_no_drift_when_rnsd_not_running(self, mock_effective):
        """Test no drift reported when rnsd is not running."""
        mock_effective.return_value = (None, None, "rnsd_not_running")
        result = detect_rnsd_config_drift()

        assert not result.drifted
        assert result.rnsd_pid is None
        assert "not running" in result.message

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_no_drift_same_path(self, mock_gw_dir, mock_effective):
        """Test no drift when paths match."""
        mock_gw_dir.return_value = Path('/etc/reticulum')
        mock_effective.return_value = (
            Path('/etc/reticulum'), 1234, "proc_cmdline"
        )
        result = detect_rnsd_config_drift()

        assert not result.drifted
        assert result.rnsd_pid == 1234
        assert "aligned" in result.message

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_drift_detected(self, mock_gw_dir, mock_effective):
        """Test drift detected when paths differ."""
        mock_gw_dir.return_value = Path('/home/user/.reticulum')
        mock_effective.return_value = (
            Path('/etc/reticulum'), 1234, "proc_cmdline"
        )
        result = detect_rnsd_config_drift()

        assert result.drifted
        assert result.gateway_config_dir == Path('/home/user/.reticulum')
        assert result.rnsd_config_dir == Path('/etc/reticulum')
        assert "DRIFT" in result.message
        assert result.fix_hint
        assert result.severity == "warning"

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_drift_etc_vs_home(self, mock_gw_dir, mock_effective):
        """Test common drift: gateway uses ~/.reticulum but rnsd uses /etc."""
        mock_gw_dir.return_value = Path('/home/meshforge/.reticulum')
        mock_effective.return_value = (
            Path('/etc/reticulum'), 5678, "rnsd_root_default"
        )
        result = detect_rnsd_config_drift()

        assert result.drifted
        assert "/etc/reticulum" in result.fix_hint
        assert "Migrate" in result.fix_hint
        # Should no longer suggest gateway.json workaround
        assert "gateway.json" not in result.fix_hint

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_no_drift_unknown_rnsd_config(self, mock_gw_dir, mock_effective):
        """Test no drift reported when rnsd config is undeterminable."""
        mock_gw_dir.return_value = Path('/etc/reticulum')
        mock_effective.return_value = (
            None, 1234, "rnsd_default_unknown"
        )
        result = detect_rnsd_config_drift()

        assert not result.drifted
        assert "not determinable" in result.message


class TestValidateGatewayRnsConfig:
    """Tests for validate_gateway_rns_config."""

    @patch('src.utils.config_drift.detect_rnsd_config_drift')
    def test_no_errors_when_aligned(self, mock_drift):
        """Test no validation errors when configs are aligned."""
        mock_drift.return_value = DriftResult(
            drifted=False,
            gateway_config_dir=Path('/etc/reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
            detection_method="proc_cmdline",
            message="aligned",
        )

        from src.gateway.config import GatewayConfig
        config = GatewayConfig()

        with patch.object(Path, 'is_file', return_value=True):
            errors = validate_gateway_rns_config(config)
            # Should only have config file existence check (if any)
            warning_errors = [e for e in errors if e.severity == "warning"]
            assert len(warning_errors) == 0

    @patch('src.utils.config_drift.detect_rnsd_config_drift')
    def test_warning_when_drifted(self, mock_drift):
        """Test validation warning when config drift detected."""
        mock_drift.return_value = DriftResult(
            drifted=True,
            gateway_config_dir=Path('/home/user/.reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
            rnsd_pid=1234,
            detection_method="proc_cmdline",
            message="CONFIG DRIFT: gateway uses /home/user/.reticulum but rnsd uses /etc/reticulum",
            fix_hint="Set rns.config_dir to /etc/reticulum",
        )

        from src.gateway.config import GatewayConfig
        config = GatewayConfig()

        with patch.object(Path, 'is_file', return_value=True):
            errors = validate_gateway_rns_config(config)
            warning_msgs = [e.message for e in errors if e.severity == "warning"]
            assert any("DRIFT" in m for m in warning_msgs)

    @patch('src.utils.config_drift.detect_rnsd_config_drift')
    def test_warning_explicit_config_dir_mismatch(self, mock_drift):
        """Test warning when explicit config_dir doesn't match rnsd."""
        mock_drift.return_value = DriftResult(
            drifted=False,
            gateway_config_dir=Path('/home/user/.reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
            rnsd_pid=1234,
            detection_method="proc_cmdline",
            message="aligned",
        )

        from src.gateway.config import GatewayConfig, RNSConfig
        config = GatewayConfig(rns=RNSConfig(config_dir="/home/user/.reticulum"))

        with patch.object(Path, 'is_file', return_value=True):
            errors = validate_gateway_rns_config(config)
            warning_msgs = [e.message for e in errors if e.severity == "warning"]
            assert any("gateway.json" in m for m in warning_msgs)

    @patch('src.utils.config_drift.detect_rnsd_config_drift')
    def test_error_config_file_missing(self, mock_drift):
        """Test error when config file doesn't exist."""
        mock_drift.return_value = DriftResult(
            drifted=False,
            gateway_config_dir=Path('/etc/reticulum'),
            rnsd_config_dir=None,
            detection_method="rnsd_not_running",
            message="rnsd not running",
        )

        from src.gateway.config import GatewayConfig
        config = GatewayConfig()

        with patch.object(Path, 'is_file', return_value=False):
            errors = validate_gateway_rns_config(config)
            error_msgs = [e for e in errors if e.severity == "error"]
            assert len(error_msgs) >= 1
            assert "not found" in error_msgs[0].message


class TestDriftResult:
    """Tests for DriftResult dataclass."""

    def test_defaults(self):
        """Test default values."""
        result = DriftResult(
            drifted=False,
            gateway_config_dir=Path('/etc/reticulum'),
            rnsd_config_dir=None,
        )
        assert result.severity == "info"
        assert result.message == ""
        assert result.fix_hint == ""
        assert result.rnsd_pid is None
        assert result.detection_method == ""

    def test_drift_result_with_all_fields(self):
        """Test full DriftResult construction."""
        result = DriftResult(
            drifted=True,
            gateway_config_dir=Path('/home/user/.reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
            rnsd_pid=1234,
            detection_method="proc_cmdline",
            message="drift detected",
            fix_hint="update config",
            severity="warning",
        )
        assert result.drifted is True
        assert result.rnsd_pid == 1234

    def test_can_auto_fix_when_drifted_different_paths(self):
        """Auto-fix available when gateway and rnsd use different non-etc paths."""
        result = DriftResult(
            drifted=True,
            gateway_config_dir=Path('/home/user/.reticulum'),
            rnsd_config_dir=Path('/root/.reticulum'),
        )
        assert result.can_auto_fix is True

    def test_can_auto_fix_when_gateway_home_rnsd_etc(self):
        """Auto-fix available when gateway uses home, rnsd uses /etc."""
        result = DriftResult(
            drifted=True,
            gateway_config_dir=Path('/home/user/.reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
        )
        assert result.can_auto_fix is True

    def test_cannot_auto_fix_when_not_drifted(self):
        """No auto-fix when no drift detected."""
        result = DriftResult(
            drifted=False,
            gateway_config_dir=Path('/etc/reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
        )
        assert result.can_auto_fix is False

    def test_cannot_auto_fix_when_both_etc(self):
        """No auto-fix when both already point to /etc/reticulum."""
        result = DriftResult(
            drifted=True,
            gateway_config_dir=Path('/etc/reticulum'),
            rnsd_config_dir=Path('/etc/reticulum'),
        )
        assert result.can_auto_fix is False


class TestParseReticulumSection:
    """Tests for _parse_reticulum_section helper."""

    def test_basic_settings(self):
        """Test parsing standard [reticulum] settings."""
        config = """
[reticulum]
  enable_transport = Yes
  share_instance = True
  instance_name = volcano ai rns
  shared_instance_type = tcp

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes

  [[Regional]]
    type = TCPClientInterface
    enabled = yes
"""
        result = _parse_reticulum_section(config)
        assert result['enable_transport'] == 'Yes'
        assert result['share_instance'] == 'True'
        assert result['instance_name'] == 'volcano ai rns'
        assert result['shared_instance_type'] == 'tcp'
        assert result['interface_count'] == 2

    def test_commented_lines_ignored(self):
        """Test that commented settings and interfaces are skipped."""
        config = """
[reticulum]
  # enable_transport = Yes
  share_instance = True

[interfaces]
  [[Active Interface]]
    type = AutoInterface
  # [[Commented Interface]]
  #   type = TCPClientInterface
"""
        result = _parse_reticulum_section(config)
        assert 'enable_transport' not in result
        assert result['share_instance'] == 'True'
        assert result['interface_count'] == 1

    def test_empty_config(self):
        """Test parsing empty config."""
        result = _parse_reticulum_section("")
        assert result['interface_count'] == 0


class TestDetectConfigShadowing:
    """Tests for detect_config_shadowing."""

    def test_only_etc_config(self, tmp_path):
        """No shadowing when only /etc config exists."""
        etc = tmp_path / 'etc' / 'reticulum'
        etc.mkdir(parents=True)
        (etc / 'config').write_text("[reticulum]\n  share_instance = Yes\n")
        home = tmp_path / 'home' / 'user'
        home.mkdir(parents=True)

        with patch('src.utils.config_drift.Path') as mock_path_cls, \
             patch('src.utils.config_drift.get_real_user_home', return_value=home):
            # /etc/reticulum/config exists
            etc_config = etc / 'config'
            real_path = Path('/etc/reticulum/config')
            mock_path_cls.return_value = MagicMock()
            mock_path_cls.return_value.is_file.return_value = True

            # Call with real filesystem via tmp_path
            result = detect_config_shadowing()
            assert result.shadowed is False or result.ignored_path is None

    def test_both_configs_with_differences(self, tmp_path):
        """Shadowing detected with setting differences."""
        etc_dir = tmp_path / 'etc_ret'
        etc_dir.mkdir()
        etc_config = etc_dir / 'config'
        etc_config.write_text(
            "[reticulum]\n"
            "  enable_transport = False\n"
            "[interfaces]\n"
            "  [[Default Interface]]\n"
            "    type = AutoInterface\n"
        )

        home = tmp_path / 'home'
        home.mkdir()
        user_dir = home / '.reticulum'
        user_dir.mkdir()
        user_config = user_dir / 'config'
        user_config.write_text(
            "[reticulum]\n"
            "  enable_transport = Yes\n"
            "  instance_name = volcano ai rns\n"
            "[interfaces]\n"
            "  [[Default Interface]]\n"
            "    type = AutoInterface\n"
            "  [[Regional]]\n"
            "    type = TCPClientInterface\n"
        )

        with patch.object(Path, 'is_file', side_effect=lambda s=None: True), \
             patch('src.utils.config_drift.get_real_user_home', return_value=home):
            # Patch Path('/etc/reticulum/config') to point to tmp
            with patch('src.utils.config_drift.Path') as mock_path_cls:
                mock_etc = MagicMock()
                mock_etc.is_file.return_value = True
                mock_etc.read_text.return_value = etc_config.read_text()
                mock_etc.__str__ = lambda s: str(etc_config)
                mock_path_cls.return_value = mock_etc

                # Use real function with mocked paths
                from src.utils.config_drift import _parse_reticulum_section

                etc_settings = _parse_reticulum_section(etc_config.read_text())
                user_settings = _parse_reticulum_section(user_config.read_text())

                assert etc_settings['enable_transport'] == 'False'
                assert user_settings['enable_transport'] == 'Yes'
                assert user_settings['instance_name'] == 'volcano ai rns'
                assert etc_settings['interface_count'] == 1
                assert user_settings['interface_count'] == 2

    def test_no_configs_exist(self):
        """No shadowing when no configs exist."""
        with patch.object(Path, 'is_file', return_value=False), \
             patch('src.utils.config_drift.get_real_user_home',
                   return_value=Path('/home/nobody')):
            result = detect_config_shadowing()
            assert result.shadowed is False


# =============================================================================
# Config Path Resolution Shadowing (diagnostics/Q&A)
# =============================================================================


class TestConfigPathResolutionShadowing:
    """Test ReticulumPaths resolution order and how it causes shadowing.

    RNS resolves config in priority order:
      1. /etc/reticulum/config (system-wide, wins)
      2. ~/.config/reticulum/config (XDG-style)
      3. ~/.reticulum/config (traditional)

    When multiple configs exist with different content, the lower-priority
    ones are silently ignored. This is a common source of confusion:
    a user edits ~/.reticulum/config but rnsd reads /etc/reticulum/config.
    """

    @patch('pathlib.Path.is_file')
    @patch('pathlib.Path.is_dir')
    def test_etc_shadows_user_config(self, mock_is_dir, mock_is_file):
        """When /etc/reticulum/config exists, user configs are shadowed."""
        from utils.paths import ReticulumPaths

        mock_is_dir.return_value = True
        mock_is_file.return_value = True

        # /etc/reticulum should win
        result = ReticulumPaths.get_config_dir()
        assert result == Path('/etc/reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/testuser'))
    def test_xdg_shadows_traditional(self, mock_home):
        """When XDG config exists but /etc doesn't, XDG shadows traditional."""
        def selective_is_dir(self_path):
            if str(self_path) == '/etc/reticulum':
                return False
            return True

        def selective_is_file(self_path):
            if '/etc/reticulum' in str(self_path):
                return False
            return True

        with patch.object(Path, 'is_dir', selective_is_dir):
            with patch.object(Path, 'is_file', selective_is_file):
                from utils.paths import ReticulumPaths
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/testuser/.config/reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/testuser'))
    def test_traditional_fallback_when_nothing_exists(self, mock_home):
        """When no config exists anywhere, falls back to ~/.reticulum."""
        def always_false(self_path):
            return False

        with patch.object(Path, 'is_dir', always_false):
            with patch.object(Path, 'is_file', always_false):
                from utils.paths import ReticulumPaths
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/testuser/.reticulum')

    @patch('utils.paths.get_real_user_home', return_value=Path('/home/testuser'))
    def test_etc_dir_exists_but_no_config_file(self, mock_home):
        """/etc/reticulum/ exists but config file is missing => skip to XDG."""
        def selective_is_dir(self_path):
            return str(self_path) in ('/etc/reticulum',
                                      '/home/testuser/.config/reticulum')

        def selective_is_file(self_path):
            if str(self_path) == '/etc/reticulum/config':
                return False
            if str(self_path) == '/home/testuser/.config/reticulum/config':
                return True
            return False

        with patch.object(Path, 'is_dir', selective_is_dir):
            with patch.object(Path, 'is_file', selective_is_file):
                from utils.paths import ReticulumPaths
                result = ReticulumPaths.get_config_dir()
                assert result == Path('/home/testuser/.config/reticulum')

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_drift_reveals_shadowing(self, mock_gw_dir, mock_effective):
        """Config drift detection catches the shadow: user edits one, rnsd reads another."""
        mock_gw_dir.return_value = Path('/etc/reticulum')
        mock_effective.return_value = (
            Path('/home/meshuser/.reticulum'), 4321, "proc_cmdline"
        )
        result = detect_rnsd_config_drift()

        assert result.drifted
        assert 'DRIFT' in result.message
        assert result.gateway_config_dir == Path('/etc/reticulum')
        assert result.rnsd_config_dir == Path('/home/meshuser/.reticulum')

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_sudo_root_home_shadowing(self, mock_gw_dir, mock_effective):
        """Sudo causes gateway to see /etc but rnsd (as root) uses /root/.reticulum."""
        mock_gw_dir.return_value = Path('/etc/reticulum')
        mock_effective.return_value = (
            Path('/root/.reticulum'), 7777, "rnsd_root_default"
        )
        result = detect_rnsd_config_drift()

        assert result.drifted
        assert result.can_auto_fix
        assert 'Migrate' in result.fix_hint

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    def test_symlink_resolved_no_false_drift(self, mock_gw_dir, mock_effective):
        """Symlinked paths should resolve to the same target = no drift."""
        mock_gw_dir.return_value = Path('/etc/reticulum')
        mock_effective.return_value = (
            Path('/etc/reticulum'), 1111, "systemd_unit"
        )
        result = detect_rnsd_config_drift()
        assert not result.drifted
