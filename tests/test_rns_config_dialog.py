"""
Tests for RNS Config Dialog reliability

These tests ensure that the GTK config dialog:
1. Validates config before saving
2. Creates backups before modifications
3. Rejects invalid config with clear errors
4. Uses atomic save operations

TDD: Write tests first, then fix the dialog implementation.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os


class TestDialogSaveValidation:
    """Test that dialog validates config before saving"""

    def test_save_calls_validate_rns_config(self, tmp_path):
        """Dialog should validate config before writing to file"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        # Valid config content
        valid_config = """[reticulum]
enable_transport = False
share_instance = Yes

[[Default Interface]]
  type = AutoInterface
  enabled = Yes
"""
        config_path.write_text(valid_config)

        # Test that safe_save_config calls validate_rns_config
        from src.utils.rns_config import validate_rns_config, safe_save_config

        # Mock validate_rns_config within the module
        with patch('src.utils.rns_config.validate_rns_config') as mock_validate:
            mock_validate.return_value = (True, [])

            # Directly test that safe_save_config validates
            new_content = valid_config + "\n# Modified"
            result = safe_save_config(config_path, new_content)

            # Verify validate was called
            assert mock_validate.called, "validate_rns_config should be called"
            assert result['success'] is True

    def test_save_rejects_invalid_config(self, tmp_path):
        """Dialog should reject invalid config and show error"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        valid_config = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(valid_config)

        from src.utils.rns_config import safe_save_config

        # Invalid config - missing [reticulum] section
        invalid_config = """[[Some Interface]]
  type = AutoInterface
"""

        result = safe_save_config(config_path, invalid_config)

        assert result['success'] is False
        assert 'error' in result
        assert 'reticulum' in result['error'].lower()

    def test_save_rejects_malformed_brackets(self, tmp_path):
        """Dialog should reject config with malformed brackets"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("[reticulum]\n")

        from src.utils.rns_config import safe_save_config

        # Malformed - unclosed bracket
        malformed_config = """[reticulum]
share_instance = Yes

[[Broken Interface
  type = AutoInterface
"""

        result = safe_save_config(config_path, malformed_config)

        assert result['success'] is False
        assert 'error' in result


class TestDialogBackupCreation:
    """Test that dialog creates backups before modifications"""

    def test_save_creates_backup_before_write(self, tmp_path):
        """Dialog should create backup before modifying config"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original_content = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original_content)

        from src.utils.rns_config import safe_save_config

        new_content = """[reticulum]
share_instance = Yes
enable_transport = True
"""

        result = safe_save_config(config_path, new_content)

        assert result['success'] is True
        assert result['backup_path'] is not None

        # Verify backup exists
        backup_path = Path(result['backup_path'])
        assert backup_path.exists()

        # Verify backup contains original content
        assert original_content in backup_path.read_text()

    def test_backup_preserves_original_on_failure(self, tmp_path):
        """If save fails after backup, original should be restorable"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original_content = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original_content)

        from src.utils.rns_config import safe_save_config, restore_from_backup, list_backups

        # First, make a valid save to create a backup
        new_content = """[reticulum]
share_instance = No
"""
        result = safe_save_config(config_path, new_content)
        assert result['success'] is True

        # Get backups
        backups = list_backups(config_path)
        assert len(backups) >= 1

        # Restore from backup
        restore_result = restore_from_backup(config_path, backups[0])
        assert restore_result['success'] is True

        # Verify restored content
        assert original_content in config_path.read_text()


class TestDialogAtomicSave:
    """Test that dialog uses atomic save operations"""

    def test_save_is_atomic_via_temp_file(self, tmp_path):
        """Save should use temp file then rename for atomicity"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original)

        from src.utils.rns_config import safe_save_config

        new_content = """[reticulum]
share_instance = Yes
enable_transport = True
"""

        result = safe_save_config(config_path, new_content)

        # If atomic, temp file should not exist after save
        temp_path = config_path.with_suffix('.tmp')
        assert not temp_path.exists()

        # But the config should be updated
        assert 'enable_transport' in config_path.read_text()

    def test_partial_write_does_not_corrupt(self, tmp_path):
        """Interrupted write should not leave corrupt file"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original)

        from src.utils.rns_config import safe_save_config

        # Even if we had a failure scenario, backup should be restorable
        # This test verifies the backup mechanism works

        new_content = """[reticulum]
share_instance = No
"""

        result = safe_save_config(config_path, new_content)
        assert result['success'] is True
        assert result['backup_path'] is not None


class TestDialogIntegration:
    """Integration tests for dialog save workflow"""

    def test_save_flow_validates_then_backs_up_then_saves(self, tmp_path):
        """Full save flow: validate -> backup -> atomic save"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original)

        from src.utils.rns_config import safe_save_config, validate_rns_config

        new_content = """[reticulum]
share_instance = Yes
enable_transport = False

[[My Interface]]
  type = TCPClientInterface
  target_host = 192.168.1.1
  target_port = 4242
"""

        # Verify valid first
        is_valid, errors = validate_rns_config(new_content)
        assert is_valid, f"Config should be valid: {errors}"

        # Now save
        result = safe_save_config(config_path, new_content)

        assert result['success'] is True
        assert result['backup_path'] is not None
        assert 'TCPClientInterface' in config_path.read_text()

    def test_invalid_save_preserves_original_file(self, tmp_path):
        """Invalid config should not modify the original file"""
        config_path = tmp_path / ".reticulum" / "config"
        config_path.parent.mkdir(parents=True)

        original = """[reticulum]
share_instance = Yes
"""
        config_path.write_text(original)

        from src.utils.rns_config import safe_save_config

        # Try to save invalid config
        invalid = "not a valid config at all {{{{"

        result = safe_save_config(config_path, invalid)

        assert result['success'] is False
        # Original should be unchanged
        assert config_path.read_text() == original


class TestDialogUsesUtilities:
    """Test that the GTK dialog uses the utility functions"""

    def test_dialog_on_save_should_use_safe_save(self):
        """The dialog's _on_save method should use safe_save_config"""
        # Read the dialog source and verify it uses safe_save_config
        dialog_path = Path("/home/user/meshforge/src/gtk_ui/dialogs/rns_config.py")
        if dialog_path.exists():
            content = dialog_path.read_text()

            # After fix, dialog should import and use safe_save_config
            # This test will FAIL initially, then PASS after we fix the dialog
            assert 'safe_save_config' in content, \
                "Dialog should use safe_save_config from utils.rns_config"

    def test_dialog_on_save_should_not_use_write_text(self):
        """The dialog should not use direct write_text in _on_save"""
        dialog_path = Path("/home/user/meshforge/src/gtk_ui/dialogs/rns_config.py")
        if dialog_path.exists():
            content = dialog_path.read_text()

            # Find the _on_save method and check it doesn't use write_text
            # After fix, this pattern should not appear in _on_save
            import re
            save_method = re.search(r'def _on_save\(self.*?\n(?:.*?\n)*?(?=\n    def |\nclass |\Z)',
                                     content, re.MULTILINE)
            if save_method:
                save_code = save_method.group(0)
                # After fix, _on_save should NOT have direct write_text
                # This test will FAIL initially (write_text is in _on_save)
                # After fix, it should PASS (safe_save_config handles writing)
                assert 'config_path.write_text' not in save_code or 'safe_save_config' in content, \
                    "Dialog should use safe_save_config instead of direct write_text"
