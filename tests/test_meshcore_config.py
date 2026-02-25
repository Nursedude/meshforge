"""Tests for core.meshcore_config — MeshCore /etc/meshcore/ config manager."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.meshcore_config import (
    MeshcoreConfig,
    MeshCoreRadioType,
    MeshCoreRadioConfig,
    MESHCORE_TEMPLATES,
    _check_meshcore_py,
)


class TestMeshCoreRadioType:
    """Test MeshCoreRadioType enum."""

    def test_usb_serial_value(self):
        assert MeshCoreRadioType.USB_SERIAL.value == "usb_serial"

    def test_tcp_bridge_value(self):
        assert MeshCoreRadioType.TCP_BRIDGE.value == "tcp_bridge"

    def test_ble_value(self):
        assert MeshCoreRadioType.BLE.value == "ble"

    def test_unknown_value(self):
        assert MeshCoreRadioType.UNKNOWN.value == "unknown"


class TestMeshCoreRadioConfig:
    """Test MeshCoreRadioConfig dataclass."""

    def test_default_values(self):
        config = MeshCoreRadioConfig(
            name="test",
            radio_type=MeshCoreRadioType.USB_SERIAL,
        )
        assert config.baud_rate == 115200
        assert config.tcp_port == 4000
        assert config.enabled is False

    def test_custom_values(self):
        config = MeshCoreRadioConfig(
            name="custom-tcp",
            radio_type=MeshCoreRadioType.TCP_BRIDGE,
            tcp_host="192.168.1.100",
            tcp_port=5000,
            enabled=True,
        )
        assert config.tcp_host == "192.168.1.100"
        assert config.tcp_port == 5000
        assert config.enabled is True


class TestMeshcoreConfigTemplates:
    """Test built-in templates."""

    def test_companion_usb_template_exists(self):
        assert "companion-usb" in MESHCORE_TEMPLATES

    def test_companion_tcp_template_exists(self):
        assert "companion-tcp" in MESHCORE_TEMPLATES

    def test_companion_ble_template_exists(self):
        assert "companion-ble" in MESHCORE_TEMPLATES

    def test_simulation_template_exists(self):
        assert "companion-simulation" in MESHCORE_TEMPLATES

    def test_usb_template_has_serial_config(self):
        config = MESHCORE_TEMPLATES["companion-usb"]["config"]
        assert "Type: serial" in config
        assert "BaudRate: 115200" in config

    def test_tcp_template_has_tcp_config(self):
        config = MESHCORE_TEMPLATES["companion-tcp"]["config"]
        assert "Type: tcp" in config
        assert "Port: 4000" in config


class TestMeshcoreConfigStructure:
    """Test /etc/meshcore/ directory structure management."""

    def test_ensure_structure_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)

            assert mc.ensure_structure() is True
            assert mc.available_dir.exists()
            assert mc.config_d_dir.exists()
            assert mc.main_config.exists()

    def test_ensure_structure_creates_templates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            templates = list(mc.available_dir.glob("*.yaml"))
            assert len(templates) == len(MESHCORE_TEMPLATES)

    def test_ensure_structure_preserves_existing_config(self):
        """Ensure we don't overwrite existing config.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            config_dir.mkdir()
            config_yaml = config_dir / "config.yaml"
            config_yaml.write_text("# User customized config\n")

            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            assert config_yaml.read_text() == "# User customized config\n"

    def test_ensure_structure_idempotent(self):
        """Running ensure_structure twice is safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            assert mc.ensure_structure() is True
            assert mc.ensure_structure() is True


class TestMeshcoreConfigListing:
    """Test list_available() and list_enabled()."""

    def test_list_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            available = mc.list_available()
            assert len(available) == len(MESHCORE_TEMPLATES)
            assert all(isinstance(c, MeshCoreRadioConfig) for c in available)

    def test_list_enabled_empty_initially(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            enabled = mc.list_enabled()
            assert len(enabled) == 0

    def test_enable_shows_in_list_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            mc.enable("companion-usb")
            enabled = mc.list_enabled()
            assert len(enabled) == 1
            assert enabled[0].name == "companion-usb"


class TestMeshcoreConfigEnableDisable:
    """Test enable/disable operations."""

    def test_enable_creates_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            assert mc.enable("companion-usb") is True
            target = mc.config_d_dir / "companion-usb.yaml"
            assert target.is_symlink()

    def test_disable_removes_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            mc.enable("companion-usb")
            assert mc.disable("companion-usb") is True
            target = mc.config_d_dir / "companion-usb.yaml"
            assert not target.exists()

    def test_enable_nonexistent_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            assert mc.enable("nonexistent") is False

    def test_disable_nonexistent_succeeds(self):
        """Disabling something not enabled is a no-op."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            assert mc.disable("nonexistent") is True

    def test_re_enable_replaces_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            mc.enable("companion-usb")
            mc.enable("companion-usb")  # Should not error
            assert len(mc.list_enabled()) == 1


class TestMeshcoreConfigDetection:
    """Test device detection."""

    def test_detect_radio_type_no_devices(self):
        """No USB devices returns UNKNOWN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MeshcoreConfig(config_dir=Path(tmpdir))
            with patch('pathlib.Path.glob', return_value=[]):
                result = mc.detect_radio_type()
                assert result == MeshCoreRadioType.UNKNOWN

    def test_detect_devices_returns_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MeshcoreConfig(config_dir=Path(tmpdir))
            devices = mc.detect_devices()
            assert isinstance(devices, list)


class TestMeshcoreConfigCustom:
    """Test custom config operations."""

    def test_add_custom_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            content = "# My custom config\nConnection:\n  Type: tcp\n"
            assert mc.add_custom_config("my-radio", content) is True
            assert (mc.available_dir / "my-radio.yaml").exists()

    def test_read_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            content = mc.read_config("companion-usb")
            assert content is not None
            assert "serial" in content.lower()

    def test_read_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            assert mc.read_config("nonexistent") is None


class TestMeshcoreConfigStatus:
    """Test get_status() comprehensive output."""

    def test_get_status_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            status = mc.get_status()
            assert "config_dir" in status
            assert "structure_exists" in status
            assert "radio_type" in status
            assert "available_configs" in status
            assert "enabled_configs" in status
            assert "detected_devices" in status
            assert "meshcore_py_installed" in status

    def test_get_status_structure_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "meshcore"
            mc = MeshcoreConfig(config_dir=config_dir)
            mc.ensure_structure()

            status = mc.get_status()
            assert status["structure_exists"] is True
            assert status["available_configs"] == len(MESHCORE_TEMPLATES)


class TestCheckMeshcorePy:
    """Test meshcore_py library detection."""

    def test_check_meshcore_py_not_installed(self):
        """meshcore_py is likely not installed in test environment."""
        # This may be True or False depending on environment
        result = _check_meshcore_py()
        assert isinstance(result, bool)
