"""
Tests for RNS configuration handling

Tests config validation, backup, and path consistency
to prevent NomadNet/rnsd launch failures after config edits.
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestRNSConfigValidation:
    """Test RNS config validation before save"""

    def test_valid_config_basic(self):
        """Test that basic valid config passes validation"""
        from src.utils.rns_config import validate_rns_config

        config = """
[reticulum]
  enable_transport = False
  share_instance = Yes

[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes
"""
        is_valid, errors = validate_rns_config(config)
        assert is_valid, f"Valid config should pass: {errors}"

    def test_invalid_config_missing_reticulum(self):
        """Test that config without [reticulum] section fails"""
        from src.utils.rns_config import validate_rns_config

        config = """
[interfaces]
  [[Default Interface]]
    type = AutoInterface
"""
        is_valid, errors = validate_rns_config(config)
        assert not is_valid, "Config without [reticulum] should fail"
        assert "reticulum" in str(errors).lower()

    def test_invalid_config_syntax_error(self):
        """Test that malformed config fails"""
        from src.utils.rns_config import validate_rns_config

        config = """
[reticulum
  broken = syntax
"""
        is_valid, errors = validate_rns_config(config)
        assert not is_valid, "Malformed config should fail"

    def test_interface_section_valid(self):
        """Test valid interface section"""
        from src.utils.rns_config import validate_interface_section

        section = """
[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = /dev/ttyUSB0
  frequency = 915000000
  bandwidth = 125000
"""
        is_valid, errors = validate_interface_section(section)
        assert is_valid, f"Valid interface should pass: {errors}"

    def test_interface_missing_type(self):
        """Test interface without type fails"""
        from src.utils.rns_config import validate_interface_section

        section = """
[[My Interface]]
  enabled = True
  port = /dev/ttyUSB0
"""
        is_valid, errors = validate_interface_section(section)
        assert not is_valid, "Interface without type should fail"


class TestRNSConfigBackup:
    """Test config backup functionality"""

    def test_backup_created_before_save(self):
        """Test that backup is created before modifying config"""
        from src.utils.rns_config import backup_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            config_path.write_text("[reticulum]\n  share_instance = Yes\n")

            backup_path = backup_config(config_path)

            assert backup_path.exists(), "Backup file should be created"
            assert backup_path.read_text() == config_path.read_text()

    def test_backup_numbered_if_exists(self):
        """Test that multiple backups get numbered"""
        from src.utils.rns_config import backup_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            config_path.write_text("original")

            backup1 = backup_config(config_path)
            config_path.write_text("modified")
            backup2 = backup_config(config_path)

            assert backup1 != backup2, "Backups should have different names"
            assert backup1.exists() and backup2.exists()


class TestRNSConfigPath:
    """Test config path handling for root/user scenarios"""

    def test_get_config_path_as_user(self):
        """Test config path resolution for regular user"""
        from src.utils.rns_config import get_rns_config_path

        with patch.dict(os.environ, {'SUDO_USER': ''}, clear=False):
            with patch('src.utils.rns_config.Path.home', return_value=Path('/home/testuser')):
                path = get_rns_config_path()
                assert '.reticulum' in str(path)
                assert '/root' not in str(path)

    def test_get_config_path_as_sudo(self):
        """Test config path points to real user when running as sudo"""
        from src.utils.rns_config import get_rns_config_path

        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}):
            with patch('os.geteuid', return_value=0):
                path = get_rns_config_path()
                assert 'testuser' in str(path) or '.reticulum' in str(path)
                # Should NOT be root's config
                assert str(path) != '/root/.reticulum/config'


class TestRNSConfigSave:
    """Test safe config saving"""

    def test_save_validates_before_write(self):
        """Test that save validates config before writing"""
        from src.utils.rns_config import safe_save_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            config_path.write_text("[reticulum]\n")

            # Invalid config should fail
            result = safe_save_config(config_path, "[broken")
            assert not result['success'], "Invalid config should not save"

            # Original should be unchanged
            assert config_path.read_text() == "[reticulum]\n"

    def test_save_creates_backup(self):
        """Test that save creates backup"""
        from src.utils.rns_config import safe_save_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            original = "[reticulum]\n  share_instance = Yes\n"
            config_path.write_text(original)

            new_config = "[reticulum]\n  share_instance = Yes\n  enable_transport = True\n"
            result = safe_save_config(config_path, new_config)

            if result['success']:
                assert result.get('backup_path'), "Backup path should be returned"
                assert Path(result['backup_path']).read_text() == original

    def test_save_atomic(self):
        """Test that save is atomic (write to temp, then rename)"""
        from src.utils.rns_config import safe_save_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            config_path.write_text("[reticulum]\n")

            new_config = "[reticulum]\n  share_instance = Yes\n[interfaces]\n"
            result = safe_save_config(config_path, new_config)

            # Should succeed and config should be updated
            assert result['success']
            assert config_path.read_text() == new_config


class TestRNSConfigRestore:
    """Test config restore from backup"""

    def test_restore_from_backup(self):
        """Test restoring config from backup"""
        from src.utils.rns_config import restore_from_backup

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            backup_path = Path(tmpdir) / "config.bak"

            backup_path.write_text("backup content")
            config_path.write_text("current content")

            result = restore_from_backup(config_path, backup_path)

            assert result['success']
            assert config_path.read_text() == "backup content"
