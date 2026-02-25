"""
Tests for MeshForge Device Config Store.

Tests save/load/apply/verify/clear operations for device-level
Meshtastic settings persistence.
"""

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure src is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory for tests."""
    config_dir = tmp_path / '.config' / 'meshforge'
    config_dir.mkdir(parents=True)
    return config_dir


@pytest.fixture
def mock_config_path(tmp_config_dir, monkeypatch):
    """Patch device_config_store to use temp directory."""
    config_file = tmp_config_dir / 'device_config.yaml'
    monkeypatch.setattr(
        'utils.device_config_store._get_config_path',
        lambda: config_file
    )
    return config_file


class TestSaveDeviceSetting:
    """Test save_device_setting()."""

    def test_save_single_setting(self, mock_config_path):
        from utils.device_config_store import save_device_setting, load_device_config

        result = save_device_setting('lora', 'modem_preset', 'LONG_FAST')
        assert result is True

        config = load_device_config()
        assert config['lora']['modem_preset'] == 'LONG_FAST'

    def test_save_multiple_settings_same_section(self, mock_config_path):
        from utils.device_config_store import save_device_setting, load_device_config

        save_device_setting('lora', 'modem_preset', 'LONG_FAST')
        save_device_setting('lora', 'channel_num', 12)

        config = load_device_config()
        assert config['lora']['modem_preset'] == 'LONG_FAST'
        assert config['lora']['channel_num'] == 12

    def test_save_different_sections(self, mock_config_path):
        from utils.device_config_store import save_device_setting, load_device_config

        save_device_setting('lora', 'modem_preset', 'LONG_FAST')
        save_device_setting('owner', 'long_name', 'TestNode')

        config = load_device_config()
        assert config['lora']['modem_preset'] == 'LONG_FAST'
        assert config['owner']['long_name'] == 'TestNode'

    def test_overwrite_existing_setting(self, mock_config_path):
        from utils.device_config_store import save_device_setting, load_device_config

        save_device_setting('lora', 'modem_preset', 'LONG_FAST')
        save_device_setting('lora', 'modem_preset', 'SHORT_TURBO')

        config = load_device_config()
        assert config['lora']['modem_preset'] == 'SHORT_TURBO'

    def test_save_creates_file(self, mock_config_path):
        from utils.device_config_store import save_device_setting

        assert not mock_config_path.exists()
        save_device_setting('lora', 'region', 'US')
        assert mock_config_path.exists()

    def test_file_contains_header(self, mock_config_path):
        from utils.device_config_store import save_device_setting

        save_device_setting('lora', 'region', 'US')
        content = mock_config_path.read_text()
        assert '# MeshForge saved device settings' in content


class TestSaveDeviceSettings:
    """Test save_device_settings() (batch save)."""

    def test_save_batch(self, mock_config_path):
        from utils.device_config_store import save_device_settings, load_device_config

        result = save_device_settings({
            'lora': {'modem_preset': 'LONG_FAST', 'channel_num': 0},
            'owner': {'long_name': 'MyNode', 'short_name': 'MYND'},
        })
        assert result is True

        config = load_device_config()
        assert config['lora']['modem_preset'] == 'LONG_FAST'
        assert config['lora']['channel_num'] == 0
        assert config['owner']['long_name'] == 'MyNode'
        assert config['owner']['short_name'] == 'MYND'

    def test_batch_merges_with_existing(self, mock_config_path):
        from utils.device_config_store import save_device_setting, save_device_settings, load_device_config

        save_device_setting('lora', 'region', 'US')
        save_device_settings({'lora': {'modem_preset': 'LONG_FAST'}})

        config = load_device_config()
        assert config['lora']['region'] == 'US'
        assert config['lora']['modem_preset'] == 'LONG_FAST'


class TestLoadDeviceConfig:
    """Test load_device_config()."""

    def test_load_empty_returns_dict(self, mock_config_path):
        from utils.device_config_store import load_device_config

        config = load_device_config()
        assert config == {}

    def test_load_corrupted_file(self, mock_config_path):
        from utils.device_config_store import load_device_config

        mock_config_path.write_text("not: valid: yaml: [[[")
        config = load_device_config()
        # Should return whatever yaml.safe_load returns, not crash
        assert isinstance(config, dict)

    def test_load_non_dict_file(self, mock_config_path):
        from utils.device_config_store import load_device_config

        mock_config_path.write_text("- just\n- a\n- list\n")
        config = load_device_config()
        assert config == {}


class TestClearDeviceConfig:
    """Test clear_device_config()."""

    def test_clear_removes_file(self, mock_config_path):
        from utils.device_config_store import save_device_setting, clear_device_config

        save_device_setting('lora', 'region', 'US')
        assert mock_config_path.exists()

        result = clear_device_config()
        assert result is True
        assert not mock_config_path.exists()

    def test_clear_nonexistent_is_ok(self, mock_config_path):
        from utils.device_config_store import clear_device_config

        result = clear_device_config()
        assert result is True


class TestVerifySetting:
    """Test verify_setting()."""

    def test_verify_success(self, mock_config_path):
        from utils.device_config_store import verify_setting

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(
            success=True,
            output='lora.modem_preset: LONG_FAST\nlora.region: US'
        )

        result = verify_setting(mock_cli, 'lora.modem_preset', 'LONG_FAST')
        assert result is True

    def test_verify_failure_wrong_value(self, mock_config_path):
        from utils.device_config_store import verify_setting

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(
            success=True,
            output='lora.modem_preset: SHORT_FAST'
        )

        result = verify_setting(mock_cli, 'lora.modem_preset', 'LONG_FAST')
        assert result is False

    def test_verify_failure_cli_error(self, mock_config_path):
        from utils.device_config_store import verify_setting

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(success=False, output='')

        result = verify_setting(mock_cli, 'lora.modem_preset', 'LONG_FAST')
        assert result is False

    def test_verify_case_insensitive(self, mock_config_path):
        from utils.device_config_store import verify_setting

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(
            success=True,
            output='lora.modem_preset: long_fast'
        )

        result = verify_setting(mock_cli, 'lora.modem_preset', 'LONG_FAST')
        assert result is True


class TestApplySavedConfig:
    """Test apply_saved_config()."""

    def test_apply_empty_config(self, mock_config_path):
        from utils.device_config_store import apply_saved_config

        mock_cli = MagicMock()
        ok, msg = apply_saved_config(mock_cli)
        assert ok is True
        assert "No saved" in msg

    def test_apply_lora_settings(self, mock_config_path):
        from utils.device_config_store import save_device_settings, apply_saved_config

        save_device_settings({
            'lora': {'modem_preset': 'LONG_FAST', 'channel_num': 12}
        })

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(success=True, output='OK')

        ok, msg = apply_saved_config(mock_cli)
        assert ok is True
        assert 'modem_preset=LONG_FAST: OK' in msg
        assert 'channel_num=12: OK' in msg

    def test_apply_owner_settings(self, mock_config_path):
        from utils.device_config_store import save_device_settings, apply_saved_config

        save_device_settings({
            'owner': {'long_name': 'TestNode', 'short_name': 'TEST'}
        })

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(success=True, output='OK')

        ok, msg = apply_saved_config(mock_cli)
        assert ok is True
        assert 'long_name=TestNode: OK' in msg
        assert 'short_name=TEST: OK' in msg

    def test_apply_partial_failure(self, mock_config_path):
        from utils.device_config_store import save_device_settings, apply_saved_config

        save_device_settings({
            'lora': {'modem_preset': 'LONG_FAST', 'region': 'US'}
        })

        mock_cli = MagicMock()
        # modem_preset succeeds (1 call), region fails on both attempts (2 calls)
        mock_cli.run.side_effect = [
            MagicMock(success=True, output='OK'),    # set modem_preset (attempt 1 - success)
            MagicMock(success=False, output='ERR'),   # set region (attempt 1 - fail)
            MagicMock(success=False, output='ERR'),   # set region (attempt 2 - fail)
        ]

        with patch('utils.device_config_store.time.sleep'):
            ok, msg = apply_saved_config(mock_cli)

        # At least one setting failed
        assert 'FAILED' in msg
        assert ok is False

    def test_apply_mqtt_settings(self, mock_config_path):
        from utils.device_config_store import save_device_settings, apply_saved_config

        save_device_settings({
            'mqtt': {'enabled': True, 'address': 'mqtt.example.com'}
        })

        mock_cli = MagicMock()
        mock_cli.run.return_value = MagicMock(success=True, output='OK')

        ok, msg = apply_saved_config(mock_cli)
        assert ok is True
        assert 'mqtt.enabled=true: OK' in msg
        assert 'mqtt.address=mqtt.example.com: OK' in msg


class TestPathCompliance:
    """Test MF001 compliance - never uses Path.home() directly."""

    def test_uses_meshforge_paths(self):
        """Verify device_config_store uses MeshForgePaths, not Path.home()."""
        import inspect
        from utils import device_config_store

        source = inspect.getsource(device_config_store)
        assert 'Path.home()' not in source
        assert 'MeshForgePaths' in source


class TestTcpReadinessCheck:
    """Test the TCP readiness check in service_check.py."""

    def test_wait_for_tcp_ready_immediate(self):
        from utils.service_check import _wait_for_tcp_ready

        with patch('utils.service_check.socket.create_connection') as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            result = _wait_for_tcp_ready(4403, max_wait=3)
            assert result is True

    def test_wait_for_tcp_timeout(self):
        from utils.service_check import _wait_for_tcp_ready

        with patch('utils.service_check.socket.create_connection') as mock_conn:
            mock_conn.side_effect = ConnectionRefusedError()
            with patch('utils.service_check.time.sleep'):
                result = _wait_for_tcp_ready(4403, max_wait=2)
                assert result is False


class TestSetWithVerify:
    """Test MeshtasticCLI.set_with_verify()."""

    def test_set_with_verify_success(self):
        from core.meshtastic_cli import MeshtasticCLI, CLIResult

        cli = MeshtasticCLI(cli_path='/usr/bin/meshtastic')

        with patch.object(cli, 'run') as mock_run:
            mock_run.side_effect = [
                CLIResult(success=True, output='Set modem_preset to LONG_FAST'),
                CLIResult(success=True, output='lora.modem_preset: LONG_FAST'),
            ]
            with patch('core.meshtastic_cli.time.sleep'):
                result = cli.set_with_verify('lora.modem_preset', 'LONG_FAST')

            assert result.success is True
            assert '[verified]' in result.output

    def test_set_with_verify_unverified(self):
        from core.meshtastic_cli import MeshtasticCLI, CLIResult

        cli = MeshtasticCLI(cli_path='/usr/bin/meshtastic')

        with patch.object(cli, 'run') as mock_run:
            mock_run.side_effect = [
                CLIResult(success=True, output='Set modem_preset'),
                CLIResult(success=True, output='lora.modem_preset: SHORT_FAST'),
            ]
            with patch('core.meshtastic_cli.time.sleep'):
                result = cli.set_with_verify('lora.modem_preset', 'LONG_FAST')

            assert result.success is True
            assert '[unverified]' in result.output

    def test_set_with_verify_set_fails(self):
        from core.meshtastic_cli import MeshtasticCLI, CLIResult

        cli = MeshtasticCLI(cli_path='/usr/bin/meshtastic')

        with patch.object(cli, 'run') as mock_run:
            mock_run.return_value = CLIResult(success=False, error='Connection refused')
            result = cli.set_with_verify('lora.modem_preset', 'LONG_FAST')

            assert result.success is False
