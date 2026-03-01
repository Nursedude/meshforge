"""Tests for core.radio_mode — RadioMode abstraction layer."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.radio_mode import (
    RadioMode,
    get_radio_mode,
    set_radio_mode,
    get_default_bridge_mode,
    is_meshcore_primary,
    is_meshtastic_primary,
    is_dual_mode,
    DEFAULT_MODE,
    _load_user_mode,
    _load_admin_mode,
    _save_user_mode,
    _save_admin_mode,
)


class TestRadioModeEnum:
    """Test RadioMode enum values."""

    def test_meshtastic_value(self):
        assert RadioMode.MESHTASTIC.value == "meshtastic"

    def test_meshcore_value(self):
        assert RadioMode.MESHCORE.value == "meshcore"

    def test_dual_value(self):
        assert RadioMode.DUAL.value == "dual"

    def test_default_is_meshtastic(self):
        assert DEFAULT_MODE == RadioMode.MESHTASTIC

    def test_enum_from_string(self):
        assert RadioMode("meshtastic") == RadioMode.MESHTASTIC
        assert RadioMode("meshcore") == RadioMode.MESHCORE
        assert RadioMode("dual") == RadioMode.DUAL

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            RadioMode("invalid")


class TestGetRadioMode:
    """Test get_radio_mode() resolution order."""

    def test_default_is_meshtastic(self):
        """When no config files exist, default to MESHTASTIC."""
        with patch('core.radio_mode._load_admin_mode', return_value=None):
            with patch('core.radio_mode._load_user_mode', return_value=None):
                assert get_radio_mode() == RadioMode.MESHTASTIC

    def test_user_config_overrides_default(self):
        """User config takes precedence over default."""
        with patch('core.radio_mode._load_admin_mode', return_value=None):
            with patch('core.radio_mode._load_user_mode', return_value=RadioMode.MESHCORE):
                assert get_radio_mode() == RadioMode.MESHCORE

    def test_admin_config_overrides_user(self):
        """Admin config takes precedence over user config."""
        with patch('core.radio_mode._load_admin_mode', return_value=RadioMode.DUAL):
            with patch('core.radio_mode._load_user_mode', return_value=RadioMode.MESHCORE):
                assert get_radio_mode() == RadioMode.DUAL

    def test_admin_only(self):
        """Admin config works without user config."""
        with patch('core.radio_mode._load_admin_mode', return_value=RadioMode.MESHCORE):
            with patch('core.radio_mode._load_user_mode', return_value=None):
                assert get_radio_mode() == RadioMode.MESHCORE


class TestUserModePersistence:
    """Test user config file reading and writing."""

    def test_save_and_load_user_mode(self):
        """Save then load radio mode from user config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "radio_mode.json"
            with patch('core.radio_mode._get_user_config_path', return_value=config_path):
                assert _save_user_mode(RadioMode.MESHCORE) is True
                assert _load_user_mode() == RadioMode.MESHCORE

    def test_load_nonexistent_returns_none(self):
        """Loading from nonexistent file returns None."""
        with patch('core.radio_mode._get_user_config_path',
                   return_value=Path("/nonexistent/radio_mode.json")):
            assert _load_user_mode() is None

    def test_load_corrupt_json_returns_none(self):
        """Corrupt JSON returns None gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "radio_mode.json"
            config_path.write_text("not valid json{{{")
            with patch('core.radio_mode._get_user_config_path', return_value=config_path):
                assert _load_user_mode() is None

    def test_save_preserves_existing_config(self):
        """Save merges with existing config data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "radio_mode.json"
            config_path.write_text(json.dumps({"other_setting": "value"}) + "\n")
            with patch('core.radio_mode._get_user_config_path', return_value=config_path):
                _save_user_mode(RadioMode.DUAL)
                data = json.loads(config_path.read_text())
                assert data["radio_mode"] == "dual"
                assert data["other_setting"] == "value"


class TestAdminModePersistence:
    """Test admin config file reading and writing."""

    def test_save_and_load_admin_mode(self):
        """Save then load radio mode from admin config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_dir = Path(tmpdir) / "meshforge"
            with patch('core.radio_mode._ADMIN_CONFIG_DIR', admin_dir):
                assert _save_admin_mode(RadioMode.MESHCORE) is True
                assert _load_admin_mode() == RadioMode.MESHCORE

    def test_load_nonexistent_returns_none(self):
        """No admin config returns None."""
        with patch('core.radio_mode._ADMIN_CONFIG_DIR', Path("/nonexistent")):
            assert _load_admin_mode() is None

    def test_save_updates_existing_line(self):
        """Save updates existing radio_mode line in noc.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            admin_dir = Path(tmpdir) / "meshforge"
            admin_dir.mkdir()
            noc_yaml = admin_dir / "noc.yaml"
            noc_yaml.write_text("radio_mode: meshtastic\nother: value\n")
            with patch('core.radio_mode._ADMIN_CONFIG_DIR', admin_dir):
                _save_admin_mode(RadioMode.DUAL)
                content = noc_yaml.read_text()
                assert "radio_mode: dual" in content
                assert "other: value" in content
                assert "meshtastic" not in content


class TestDefaultBridgeMode:
    """Test get_default_bridge_mode() mapping."""

    def test_meshtastic_maps_to_mqtt_bridge(self):
        assert get_default_bridge_mode(RadioMode.MESHTASTIC) == "mqtt_bridge"

    def test_meshcore_maps_to_meshcore_primary(self):
        assert get_default_bridge_mode(RadioMode.MESHCORE) == "meshcore_primary"

    def test_dual_maps_to_tri_bridge(self):
        assert get_default_bridge_mode(RadioMode.DUAL) == "tri_bridge"

    def test_uses_current_mode_when_none(self):
        """When no mode is passed, use current active mode."""
        with patch('core.radio_mode.get_radio_mode', return_value=RadioMode.MESHCORE):
            assert get_default_bridge_mode() == "meshcore_primary"


class TestConvenienceFunctions:
    """Test is_meshcore_primary(), is_meshtastic_primary(), is_dual_mode()."""

    def test_is_meshcore_primary(self):
        with patch('core.radio_mode.get_radio_mode', return_value=RadioMode.MESHCORE):
            assert is_meshcore_primary() is True
            assert is_meshtastic_primary() is False
            assert is_dual_mode() is False

    def test_is_meshtastic_primary(self):
        with patch('core.radio_mode.get_radio_mode', return_value=RadioMode.MESHTASTIC):
            assert is_meshtastic_primary() is True
            assert is_meshcore_primary() is False
            assert is_dual_mode() is False

    def test_is_dual_mode(self):
        with patch('core.radio_mode.get_radio_mode', return_value=RadioMode.DUAL):
            assert is_dual_mode() is True
            assert is_meshcore_primary() is False
            assert is_meshtastic_primary() is False
