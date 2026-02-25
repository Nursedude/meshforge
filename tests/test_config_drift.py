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
    detect_rnsd_config_drift,
    get_rnsd_effective_config_dir,
    validate_gateway_rns_config,
    _get_rnsd_pid,
    _get_rnsd_config_from_proc,
    _get_rnsd_config_from_systemd,
    _get_rnsd_effective_config,
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


class TestGetRnsdEffectiveConfigDir:
    """Tests for get_rnsd_effective_config_dir."""

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    def test_returns_rnsd_path_when_available(self, mock_effective):
        """Test returns rnsd's actual path when determinable."""
        mock_effective.return_value = (
            Path('/etc/reticulum'), 1234, "proc_cmdline"
        )
        result = get_rnsd_effective_config_dir()
        assert result == Path('/etc/reticulum')

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('src.utils.config_drift.ReticulumPaths.get_config_dir')
    @patch('os.geteuid', return_value=1000)
    def test_falls_back_to_default_for_non_root(self, mock_euid,
                                                  mock_gw_dir, mock_effective):
        """Test fallback to ReticulumPaths when rnsd config unknown."""
        mock_effective.return_value = (None, None, "rnsd_not_running")
        mock_gw_dir.return_value = Path('/home/user/.reticulum')
        result = get_rnsd_effective_config_dir()
        assert result == Path('/home/user/.reticulum')

    @patch('src.utils.config_drift._get_rnsd_effective_config')
    @patch('os.geteuid', return_value=0)
    def test_prefers_etc_for_root(self, mock_euid, mock_effective):
        """Test root prefers /etc/reticulum when config exists."""
        mock_effective.return_value = (None, None, "rnsd_not_running")

        with patch.object(Path, 'is_file', return_value=True):
            result = get_rnsd_effective_config_dir()
            assert result == Path('/etc/reticulum')


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
