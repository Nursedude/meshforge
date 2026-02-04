"""
Tests for ServiceMenuMixin - Service and bridge management.

Tests the service control workflows used by the TUI.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
import subprocess


class TestBridgeDetection:
    """Test bridge running detection."""

    @patch('launcher_tui.service_menu_mixin._HAS_SERVICE_CHECK', True)
    @patch('launcher_tui.service_menu_mixin.check_process_running')
    def test_is_bridge_running_uses_service_check(self, mock_check):
        """Should use centralized service_check when available."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_check.return_value = True
        mixin = ServiceMenuMixin()

        result = mixin._is_bridge_running()

        mock_check.assert_called_once_with('bridge_cli.py')
        assert result is True

    @patch('launcher_tui.service_menu_mixin._HAS_SERVICE_CHECK', False)
    @patch('subprocess.run')
    def test_is_bridge_running_fallback(self, mock_run):
        """Should fall back to pgrep when service_check unavailable."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=0)
        mixin = ServiceMenuMixin()

        result = mixin._is_bridge_running()

        assert result is True
        mock_run.assert_called()
        # Verify pgrep was called with bridge_cli.py
        call_args = mock_run.call_args[0][0]
        assert 'pgrep' in call_args
        assert 'bridge_cli.py' in call_args

    @patch('launcher_tui.service_menu_mixin._HAS_SERVICE_CHECK', False)
    @patch('subprocess.run')
    def test_is_bridge_running_not_running(self, mock_run):
        """Should return False when bridge is not running."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=1)
        mixin = ServiceMenuMixin()

        result = mixin._is_bridge_running()

        assert result is False


class TestRnsdDetection:
    """Test rnsd running detection."""

    @patch('launcher_tui.service_menu_mixin._HAS_SERVICE_CHECK', True)
    @patch('launcher_tui.service_menu_mixin.check_process_running')
    def test_is_rnsd_running_uses_service_check(self, mock_check):
        """Should use centralized service_check when available."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_check.return_value = True
        mixin = ServiceMenuMixin()

        result = mixin._is_rnsd_running()

        mock_check.assert_called_once_with('rnsd')
        assert result is True

    @patch('launcher_tui.service_menu_mixin._HAS_SERVICE_CHECK', False)
    @patch('subprocess.run')
    def test_is_rnsd_running_fallback(self, mock_run):
        """Should fall back to pgrep when service_check unavailable."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=0)
        mixin = ServiceMenuMixin()

        result = mixin._is_rnsd_running()

        assert result is True


class TestSystemdUnitDetection:
    """Test systemd unit file detection."""

    @patch('subprocess.run')
    def test_has_systemd_unit_exists(self, mock_run):
        """Should return True when systemctl cat succeeds."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=0)
        mixin = ServiceMenuMixin()

        result = mixin._has_systemd_unit('meshtasticd')

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert 'systemctl' in call_args
        assert 'cat' in call_args
        assert 'meshtasticd' in call_args

    @patch('subprocess.run')
    def test_has_systemd_unit_not_exists(self, mock_run):
        """Should return False when service has no unit file."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=1)
        mixin = ServiceMenuMixin()

        result = mixin._has_systemd_unit('nonexistent')

        assert result is False


class TestBridgeLogFinding:
    """Test bridge log file discovery."""

    def test_find_bridge_log_returns_stored_path(self):
        """Should return stored log path if it exists."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mixin._bridge_log_path = mock_path

        result = mixin._find_bridge_log()

        assert result == mock_path

    @patch('pathlib.Path.glob')
    def test_find_bridge_log_searches_tmp(self, mock_glob):
        """Should search /tmp for gateway logs when no stored path."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mixin._bridge_log_path = None

        # Create mock log files
        mock_log1 = MagicMock()
        mock_log1.stat.return_value.st_mtime = 1000
        mock_log2 = MagicMock()
        mock_log2.stat.return_value.st_mtime = 2000  # More recent

        mock_glob.return_value = [mock_log1, mock_log2]

        result = mixin._find_bridge_log()

        # Should return the most recent log
        assert result == mock_log2

    @patch('pathlib.Path.glob')
    def test_find_bridge_log_returns_none_when_no_logs(self, mock_glob):
        """Should return None when no log files found."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mixin._bridge_log_path = None
        mock_glob.return_value = []

        result = mixin._find_bridge_log()

        assert result is None


class TestMosquittoInstallation:
    """Test mosquitto installation detection."""

    @patch('subprocess.run')
    def test_is_mosquitto_installed_true(self, mock_run):
        """Should return True when mosquitto binary found."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='/usr/sbin/mosquitto'
        )
        mixin = ServiceMenuMixin()

        result = mixin._is_mosquitto_installed()

        assert result is True

    @patch('subprocess.run')
    def test_is_mosquitto_installed_false(self, mock_run):
        """Should return False when mosquitto not found."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mock_run.return_value = MagicMock(returncode=1)
        mixin = ServiceMenuMixin()

        result = mixin._is_mosquitto_installed()

        assert result is False


class TestServiceMenuWorkflows:
    """Test service menu dialog workflows."""

    @pytest.fixture
    def mixin_with_dialog(self):
        """Create mixin with mocked dialog."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mixin.dialog = MagicMock()
        mixin.src_dir = Path('/tmp/meshforge/src')
        mixin._bridge_log_path = None
        return mixin

    def test_run_bridge_shows_start_options_when_stopped(self, mixin_with_dialog):
        """When bridge stopped, should show start options."""
        mixin = mixin_with_dialog

        # Bridge not running, user selects back
        with patch.object(mixin, '_is_bridge_running', return_value=False):
            mixin.dialog.menu.return_value = 'back'
            mixin._run_bridge()

        # Verify menu was called with start options
        call_args = mixin.dialog.menu.call_args
        choices = call_args[0][2]  # Third positional arg is choices
        choice_keys = [c[0] for c in choices]
        assert 'start' in choice_keys
        assert 'start-fg' in choice_keys

    def test_run_bridge_shows_stop_options_when_running(self, mixin_with_dialog):
        """When bridge running, should show stop/status options."""
        mixin = mixin_with_dialog

        # Bridge running, user selects back
        with patch.object(mixin, '_is_bridge_running', return_value=True):
            mixin.dialog.menu.return_value = 'back'
            mixin._run_bridge()

        # Verify menu was called with stop options
        call_args = mixin.dialog.menu.call_args
        choices = call_args[0][2]
        choice_keys = [c[0] for c in choices]
        assert 'stop' in choice_keys
        assert 'status' in choice_keys
        assert 'logs' in choice_keys


class TestRnsdDirectControl:
    """Test direct rnsd process control (no systemd)."""

    @patch('shutil.which')
    @patch('subprocess.run')
    def test_start_rnsd_direct_checks_already_running(self, mock_run, mock_which):
        """Should skip start if already running."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()

        with patch.object(mixin, '_is_rnsd_running', return_value=True):
            result = mixin._start_rnsd_direct()

        assert result is True
        # subprocess.run should not be called for starting
        # (only for _is_rnsd_running check if not using service_check)

    @patch('shutil.which', return_value=None)
    def test_start_rnsd_direct_fails_when_not_installed(self, mock_which):
        """Should fail gracefully when rnsd not in PATH."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()

        with patch.object(mixin, '_is_rnsd_running', return_value=False):
            result = mixin._start_rnsd_direct()

        assert result is False

    @patch('subprocess.run')
    def test_stop_rnsd_direct_when_not_running(self, mock_run):
        """Should return True immediately if not running."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()

        with patch.object(mixin, '_is_rnsd_running', return_value=False):
            result = mixin._stop_rnsd_direct()

        assert result is True


class TestMQTTSetupWizard:
    """Test MQTT setup wizard flow."""

    @pytest.fixture
    def mixin_for_mqtt(self):
        """Create mixin configured for MQTT testing."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mixin.dialog = MagicMock()
        return mixin

    def test_mqtt_wizard_cancelled_at_intro(self, mixin_for_mqtt):
        """Should exit cleanly when user cancels at intro."""
        mixin = mixin_for_mqtt
        mixin.dialog.yesno.return_value = False

        mixin._mqtt_setup_wizard()

        # Should only call yesno once (the intro)
        assert mixin.dialog.yesno.call_count == 1

    @patch.object(
        __import__('launcher_tui.service_menu_mixin', fromlist=['ServiceMenuMixin']).ServiceMenuMixin,
        '_is_mosquitto_installed',
        return_value=False
    )
    def test_mqtt_wizard_offers_install(self, mock_installed, mixin_for_mqtt):
        """Should offer to install mosquitto when not present."""
        mixin = mixin_for_mqtt

        # User accepts intro, declines install
        mixin.dialog.yesno.side_effect = [True, False]

        with patch.object(mixin, '_is_mosquitto_installed', return_value=False):
            mixin._mqtt_setup_wizard()

        # Should show cancelled message
        assert mixin.dialog.msgbox.called


class TestConfigFixing:
    """Test SPI config fixing."""

    @pytest.fixture
    def mixin_for_config(self):
        """Create mixin for config testing."""
        from launcher_tui.service_menu_mixin import ServiceMenuMixin

        mixin = ServiceMenuMixin()
        mixin.dialog = MagicMock()
        return mixin

    @patch('pathlib.Path.exists')
    @patch('pathlib.Path.unlink')
    @patch('pathlib.Path.read_text')
    @patch('pathlib.Path.write_text')
    def test_fix_spi_config_removes_usb_config(
        self, mock_write, mock_read, mock_unlink, mock_exists, mixin_for_config
    ):
        """Should remove usb-serial.yaml from config.d."""
        mixin = mixin_for_config

        # USB config exists, main config has Webserver section
        mock_exists.return_value = True
        mock_read.return_value = 'Webserver:\n  Port: 9443'

        with patch('launcher_tui.service_menu_mixin._HAS_APPLY_RESTART', False):
            with patch('subprocess.run'):
                mixin._fix_spi_config(has_native=True)

        # Should have tried to unlink the USB config
        mock_unlink.assert_called()
