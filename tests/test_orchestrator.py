"""
Tests for ServiceOrchestrator startup logic.

Covers:
- Polling retry with crash detection
- Journalctl diagnostics on failure
- Port readiness waiting
- Service already running

Run: python3 -m pytest tests/test_orchestrator.py -v
"""

import subprocess
import sys
import pytest
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass
from pathlib import Path

from utils.service_check import (
    ServiceState as _CheckState,
    ServiceStatus as _CheckStatus,
)


def _make_status(available, state, name='meshtasticd', message=''):
    """Helper to build a service_check.ServiceStatus."""
    return _CheckStatus(
        name=name,
        available=available,
        state=state,
        message=message,
    )


def _available():
    return _make_status(True, _CheckState.AVAILABLE, message='running')


def _not_running():
    return _make_status(False, _CheckState.NOT_RUNNING, message='not running')


def _failed():
    return _make_status(False, _CheckState.FAILED, message='has failed')


@pytest.fixture
def orchestrator():
    """Create a ServiceOrchestrator with mocked config loading."""
    with patch('core.orchestrator.Path.exists', return_value=False), \
         patch('core.orchestrator._detect_radio_hardware', return_value={
             'has_spi': False, 'has_usb': True,
             'spi_devices': [], 'usb_devices': ['/dev/ttyUSB0'],
             'usb_device': '/dev/ttyUSB0', 'hardware_type': 'usb',
         }):
        from core.orchestrator import ServiceOrchestrator
        orch = ServiceOrchestrator(config_path=Path('/nonexistent'))
        return orch


class TestStartServiceCrashDetection:
    """Test crash detection and restart logic in start_service()."""

    def test_crash_detected_and_restarted_successfully(self, orchestrator):
        """When service crashes, orchestrator restarts it once and succeeds."""
        # check_service returns: FAILED on first poll, AVAILABLE after restart
        check_responses = [_failed(), _available()]
        check_iter = iter(check_responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=lambda _: next(check_iter)), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=True), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True
            # Journal should have been logged on crash detection
            mock_journal.assert_called()

    def test_crash_permanent_failure(self, orchestrator):
        """When service crashes and restart also fails, return False with diagnostics."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_failed()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit') as mock_emit:

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            # Journal diagnostics should be logged (at least once for crash, once for final failure)
            assert mock_journal.call_count >= 2
            mock_emit.assert_called_with('service_failed', 'meshtasticd')


class TestStartServiceSlowStartup:
    """Test polling handles slow service startups."""

    def test_service_available_after_several_polls(self, orchestrator):
        """Service takes a few seconds to start — polling waits for it."""
        # NOT_RUNNING for 3 polls, then AVAILABLE
        responses = [_not_running(), _not_running(), _not_running(), _available()]
        response_iter = iter(responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=lambda _: next(response_iter)), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=True), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True

    def test_service_timeout(self, orchestrator):
        """Service never starts within max_wait — timeout with diagnostics."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_not_running()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit') as mock_emit:

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            mock_journal.assert_called()
            mock_emit.assert_called_with('service_failed', 'meshtasticd')


class TestStartServicePortReadiness:
    """Test port readiness retry logic."""

    def test_port_ready_after_retries(self, orchestrator):
        """Port comes up after a few retries — logged as ready."""
        port_responses = [False, False, True]
        port_iter = iter(port_responses)

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_available()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', side_effect=lambda _: next(port_iter)), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is True

    def test_port_never_ready_service_alive_succeeds(self, orchestrator):
        """Port never binds but service still alive — warning only, return True."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', return_value=_available()), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=False), \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            # Service is up, port not bound is just a warning
            assert result is True

    def test_port_never_ready_service_crashed_returns_false(self, orchestrator):
        """Port never binds AND service crashed — return False."""
        # check_service returns available during startup polling,
        # then not-running on post-port recheck (service crashed)
        call_count = {'n': 0}

        def mock_check_service(name):
            call_count['n'] += 1
            if call_count['n'] <= 1:
                return _available()  # During startup polling
            return _not_running()    # Post-port recheck — crashed

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=mock_check_service), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=False), \
             patch.object(orchestrator, '_log_journal_tail'), \
             patch.object(orchestrator, '_emit') as mock_emit:

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            mock_emit.assert_called_with('service_failed', 'meshtasticd')


class TestStartServiceAlreadyRunning:
    """Test early return when service is already running."""

    def test_already_running(self, orchestrator):
        """Service already running — return True without checking config.d."""
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=True), \
             patch.object(orchestrator, '_check_meshtasticd_config') as mock_config_check:

            result = orchestrator.start_service('meshtasticd')

            assert result is True
            # Config check must NOT be called when service is already running
            mock_config_check.assert_not_called()

    def test_already_running_empty_config_d(self, orchestrator):
        """Service running with empty config.d — return True (bug fix).

        Regression test: meshtasticd configured via config.yaml (not config.d/)
        should not be rejected when it is already running and healthy.
        """
        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=True), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=False) as mock_config_check:

            result = orchestrator.start_service('meshtasticd')

            assert result is True
            # Config check must not be called — service is already running
            mock_config_check.assert_not_called()


class TestLogJournalTail:
    """Test _log_journal_tail helper."""

    def test_logs_journal_output(self, orchestrator):
        """Journal tail is logged for known service."""
        mock_result = MagicMock()
        mock_result.stdout = "2026-02-24 line1\n2026-02-24 line2"
        mock_result.returncode = 0

        with patch('subprocess.run', return_value=mock_result) as mock_run:
            orchestrator._log_journal_tail('meshtasticd', lines=5)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert 'journalctl' in args
            assert '-n' in args
            assert '5' in args

    def test_handles_missing_journalctl(self, orchestrator):
        """No crash when journalctl not available."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            # Should not raise
            orchestrator._log_journal_tail('meshtasticd')

    def test_handles_timeout(self, orchestrator):
        """No crash when journalctl times out."""
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='', timeout=5)):
            # Should not raise
            orchestrator._log_journal_tail('meshtasticd')

    def test_unknown_service(self, orchestrator):
        """No crash for unknown service name."""
        orchestrator._log_journal_tail('nonexistent')


class TestFixStalePlaceholder:
    """Test _fix_stale_placeholder() auto-fix for stale placeholder services."""

    def test_regenerates_service_when_placeholder_and_binary_exists(self, orchestrator):
        """When ExecStart is /bin/echo but real binary exists, regenerate service file."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == 'systemctl' and '--property=ExecStart' in cmd:
                result.stdout = 'ExecStart=/bin/echo "Install native meshtasticd"'
                result.returncode = 0
            elif cmd[0] == 'which':
                result.stdout = '/usr/bin/meshtasticd\n'
                result.returncode = 0
            else:
                result.stdout = ''
                result.returncode = 0
            return result

        with patch('subprocess.run', side_effect=mock_run), \
             patch('core.orchestrator._sudo_write', return_value=(True, 'ok')) as mock_write, \
             patch('core.orchestrator._sudo_cmd', side_effect=lambda c: c), \
             patch('pathlib.Path.exists', return_value=False), \
             patch('pathlib.Path.read_text', return_value=''):

            result = orchestrator._fix_stale_placeholder('meshtasticd')

            assert result is True
            mock_write.assert_called_once()
            written_path = mock_write.call_args[0][0]
            written_content = mock_write.call_args[0][1]
            assert written_path == '/etc/systemd/system/meshtasticd.service'
            assert '/usr/bin/meshtasticd' in written_content
            assert 'Type=simple' in written_content

    def test_no_fix_when_binary_not_found(self, orchestrator):
        """When ExecStart is /bin/echo and binary doesn't exist, log error, return False."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == 'systemctl' and '--property=ExecStart' in cmd:
                result.stdout = 'ExecStart=/bin/echo "No radio detected"'
                result.returncode = 0
            elif cmd[0] == 'which':
                result.stdout = ''
                result.returncode = 1
            else:
                result.stdout = ''
                result.returncode = 0
            return result

        with patch('subprocess.run', side_effect=mock_run), \
             patch('core.orchestrator._sudo_write') as mock_write:

            result = orchestrator._fix_stale_placeholder('meshtasticd')

            assert result is False
            mock_write.assert_not_called()

    def test_real_service_unchanged(self, orchestrator):
        """When ExecStart points to real binary, no fix needed."""
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == 'systemctl' and '--property=ExecStart' in cmd:
                result.stdout = 'ExecStart=/usr/bin/meshtasticd -c /etc/meshtasticd/config.yaml'
                result.returncode = 0
            else:
                result.stdout = ''
                result.returncode = 0
            return result

        with patch('subprocess.run', side_effect=mock_run), \
             patch('core.orchestrator._sudo_write') as mock_write:

            result = orchestrator._fix_stale_placeholder('meshtasticd')

            assert result is False
            mock_write.assert_not_called()

    def test_uses_template_when_available(self, orchestrator):
        """When service template exists, use it instead of inline fallback."""
        template_content = (
            "[Unit]\n"
            "Description=Meshtastic Daemon\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStart=@MESHTASTICD_BIN@ -c /etc/meshtasticd/config.yaml\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == 'systemctl' and '--property=ExecStart' in cmd:
                result.stdout = 'ExecStart=/bin/echo "placeholder"'
                result.returncode = 0
            elif cmd[0] == 'which':
                result.stdout = '/usr/bin/meshtasticd\n'
                result.returncode = 0
            else:
                result.stdout = ''
                result.returncode = 0
            return result

        with patch('subprocess.run', side_effect=mock_run), \
             patch('core.orchestrator._sudo_write', return_value=(True, 'ok')) as mock_write, \
             patch('core.orchestrator._sudo_cmd', side_effect=lambda c: c), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.read_text', return_value=template_content):

            result = orchestrator._fix_stale_placeholder('meshtasticd')

            assert result is True
            written_content = mock_write.call_args[0][1]
            assert '/usr/bin/meshtasticd' in written_content
            assert '@MESHTASTICD_BIN@' not in written_content


class TestConfigDValidation:
    """Test _check_meshtasticd_config validates config.d/ state."""

    def test_existing_config_returns_true(self, orchestrator, tmp_path):
        """When config.d/ has a config, return True."""
        config_d = tmp_path / "config.d"
        config_d.mkdir()
        (config_d / "my-radio.yaml").write_text("Lora:\n  Module: sx1262\n  CS: 8\n")

        with patch('core.orchestrator.MESHTASTICD_CONFIG_DIR', tmp_path):
            result = orchestrator._check_meshtasticd_config()
            assert result is True

    def test_empty_config_d_returns_false(self, orchestrator, tmp_path):
        """When config.d/ is empty, refuse to start."""
        config_d = tmp_path / "config.d"
        available_d = tmp_path / "available.d"
        config_d.mkdir()
        available_d.mkdir()
        (available_d / "meshtoad-spi.yaml").write_text("Lora:\n  CS: 8\n")

        with patch('core.orchestrator.MESHTASTICD_CONFIG_DIR', tmp_path):
            result = orchestrator._check_meshtasticd_config()
            assert result is False

    def test_no_config_d_returns_false(self, orchestrator, tmp_path):
        """When config.d/ doesn't exist, refuse to start."""
        with patch('core.orchestrator.MESHTASTICD_CONFIG_DIR', tmp_path):
            result = orchestrator._check_meshtasticd_config()
            assert result is False

    def test_logs_available_templates(self, orchestrator, tmp_path, caplog):
        """When refusing to start, log available templates."""
        import logging
        config_d = tmp_path / "config.d"
        available_d = tmp_path / "available.d"
        config_d.mkdir()
        available_d.mkdir()
        (available_d / "meshtoad-spi.yaml").write_text("Lora:\n")
        (available_d / "heltec-usb.yaml").write_text("Serial:\n")

        with patch('core.orchestrator.MESHTASTICD_CONFIG_DIR', tmp_path), \
             caplog.at_level(logging.ERROR):
            result = orchestrator._check_meshtasticd_config()
            assert result is False
            assert "meshtoad-spi.yaml" in caplog.text
            assert "heltec-usb.yaml" in caplog.text

    def test_no_auto_deployment(self, orchestrator, tmp_path):
        """When config.d/ is empty, no templates are auto-deployed."""
        config_d = tmp_path / "config.d"
        available_d = tmp_path / "available.d"
        config_d.mkdir()
        available_d.mkdir()
        (available_d / "heltec-usb.yaml").write_text("Serial:\n  Device: auto\n")

        with patch('core.orchestrator.MESHTASTICD_CONFIG_DIR', tmp_path):
            result = orchestrator._check_meshtasticd_config()
            assert result is False
            # config_d should remain empty — no auto-deployment
            assert list(config_d.glob("*.yaml")) == []


class TestPostPortCrashDetection:
    """Test that service crash after port timeout is detected."""

    def test_config_hint_on_crash(self, orchestrator):
        """When service crashes after start, error includes config check hint."""
        call_count = {'n': 0}

        def mock_check_service(name):
            call_count['n'] += 1
            if call_count['n'] <= 1:
                return _available()
            return _not_running()

        with patch.object(orchestrator, 'is_installed', return_value=True), \
             patch.object(orchestrator, 'is_running', return_value=False), \
             patch.object(orchestrator, '_check_meshtasticd_config', return_value=True), \
             patch('core.orchestrator.check_service', side_effect=mock_check_service), \
             patch('subprocess.run', return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep'), \
             patch.object(orchestrator, '_check_port', return_value=False), \
             patch.object(orchestrator, '_log_journal_tail') as mock_journal, \
             patch.object(orchestrator, '_emit'):

            result = orchestrator.start_service('meshtasticd')

            assert result is False
            mock_journal.assert_called()


class TestEEPROMTemplateMatching:
    """Test HardwareDetector.match_eeprom_to_template classmethod."""

    @pytest.fixture(autouse=True)
    def _import_hardware(self):
        """Import HardwareDetector with mocked dependencies."""
        # Mock rich and utils.system/utils.logger which may not be installed
        mock_rich = MagicMock()
        mock_utils_system = MagicMock()
        mock_utils_logger = MagicMock()
        mock_utils_logger.log = MagicMock()

        with patch.dict(sys.modules, {
            'rich': mock_rich,
            'rich.console': mock_rich,
            'utils.system': mock_utils_system,
            'utils.logger': mock_utils_logger,
        }):
            # Force re-import to pick up mocks
            if 'config.hardware' in sys.modules:
                del sys.modules['config.hardware']
            from config.hardware import HardwareDetector
            self.HardwareDetector = HardwareDetector
            yield
            # Clean up
            if 'config.hardware' in sys.modules:
                del sys.modules['config.hardware']

    def test_known_hat_matches(self):
        """EEPROM product matching a known HAT returns correct template."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'MeshAdv-Mini\x00'

        with patch('config.hardware.Path', return_value=mock_path):
            result = self.HardwareDetector.match_eeprom_to_template()
            assert result == 'meshadv-mini.yaml'

    def test_rak_hat_matches(self):
        """EEPROM product with RAK2287 returns rak-hat-spi template."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'RAKwireless RAK2287\x00'

        with patch('config.hardware.Path', return_value=mock_path):
            result = self.HardwareDetector.match_eeprom_to_template()
            assert result == 'rak-hat-spi.yaml'

    def test_unknown_hat_returns_none(self):
        """EEPROM product not matching any known HAT returns None."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = 'UnknownDevice v3'

        with patch('config.hardware.Path', return_value=mock_path):
            result = self.HardwareDetector.match_eeprom_to_template()
            assert result is None

    def test_no_eeprom_file_returns_none(self):
        """When /proc/device-tree/hat/product doesn't exist, returns None."""
        mock_path = MagicMock()
        mock_path.exists.return_value = False

        with patch('config.hardware.Path', return_value=mock_path):
            result = self.HardwareDetector.match_eeprom_to_template()
            assert result is None

    def test_empty_eeprom_returns_none(self):
        """When EEPROM file exists but is empty, returns None."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = '\x00'

        with patch('config.hardware.Path', return_value=mock_path):
            result = self.HardwareDetector.match_eeprom_to_template()
            assert result is None
