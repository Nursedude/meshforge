"""Tests for USB hardware auto-detection and template matching.

Validates:
- USB vendor:product ID to template mapping
- Device name lookup
- Template matching for all known USB radio types
- Startup health USB identification
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from config.hardware import HardwareDetector


class TestUSBIDToTemplate:
    """Test USB_ID_TO_TEMPLATE mapping."""

    def test_mapping_exists(self):
        """USB_ID_TO_TEMPLATE should be defined."""
        assert hasattr(HardwareDetector, 'USB_ID_TO_TEMPLATE')
        assert isinstance(HardwareDetector.USB_ID_TO_TEMPLATE, dict)
        assert len(HardwareDetector.USB_ID_TO_TEMPLATE) > 0

    def test_all_templates_are_yaml(self):
        """All template values should end with .yaml."""
        for usb_id, template in HardwareDetector.USB_ID_TO_TEMPLATE.items():
            assert template.endswith('.yaml'), f"{usb_id} maps to non-yaml: {template}"

    def test_all_ids_are_valid_format(self):
        """All USB IDs should be in vendor:product format."""
        for usb_id in HardwareDetector.USB_ID_TO_TEMPLATE:
            parts = usb_id.split(':')
            assert len(parts) == 2, f"Invalid USB ID format: {usb_id}"
            assert len(parts[0]) == 4, f"Vendor ID wrong length: {usb_id}"
            assert len(parts[1]) == 4, f"Product ID wrong length: {usb_id}"


class TestMatchUSBToTemplate:
    """Test HardwareDetector.match_usb_to_template()."""

    def test_heltec_cdc(self):
        """Heltec ESP32-S3 CDC should match heltec-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('303a:1001') == 'heltec-usb.yaml'

    def test_heltec_jtag(self):
        """Heltec ESP32-S3 JTAG should match heltec-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('303a:4001') == 'heltec-usb.yaml'

    def test_meshstick(self):
        """MeshStick should match meshstick-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('1209:0000') == 'meshstick-usb.yaml'

    def test_meshtoad_ch340(self):
        """MeshToad CH340 should match meshtoad-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('1a86:7523') == 'meshtoad-usb.yaml'

    def test_meshtoad_ch341(self):
        """MeshToad CH341 alternate should match meshtoad-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('1a86:55d4') == 'meshtoad-usb.yaml'

    def test_meshtoad_ch340k(self):
        """MeshToad CH340K variant should match meshtoad-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('1a86:7522') == 'meshtoad-usb.yaml'

    def test_rak4631(self):
        """RAK4631 nRF52840 should match rak4631-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('239a:8029') == 'rak4631-usb.yaml'

    def test_rak4631_bootloader(self):
        """RAK4631 in bootloader should match rak4631-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('239a:0029') == 'rak4631-usb.yaml'

    def test_station_g2(self):
        """Station G2 CP2102 should match station-g2-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('10c4:ea60') == 'station-g2-usb.yaml'

    def test_tbeam_s3(self):
        """T-Beam S3 CH9102 should match tbeam-usb.yaml."""
        assert HardwareDetector.match_usb_to_template('1a86:55d3') == 'tbeam-usb.yaml'

    def test_ftdi_generic(self):
        """FTDI FT232R should match usb-serial-generic.yaml."""
        assert HardwareDetector.match_usb_to_template('0403:6001') == 'usb-serial-generic.yaml'

    def test_unknown_id_returns_none(self):
        """Unknown USB ID should return None."""
        assert HardwareDetector.match_usb_to_template('ffff:ffff') is None

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        assert HardwareDetector.match_usb_to_template('303A:1001') == 'heltec-usb.yaml'
        assert HardwareDetector.match_usb_to_template('1A86:7523') == 'meshtoad-usb.yaml'


class TestGetDeviceNameForUSBID:
    """Test HardwareDetector.get_device_name_for_usb_id()."""

    def test_heltec_name(self):
        """Heltec ID should return device name with common devices."""
        name = HardwareDetector.get_device_name_for_usb_id('303a:1001')
        assert name is not None
        assert 'Heltec' in name

    def test_meshtoad_name(self):
        """MeshToad ID should return device name."""
        name = HardwareDetector.get_device_name_for_usb_id('1a86:7523')
        assert name is not None
        assert 'MeshToad' in name

    def test_rak_name(self):
        """RAK4631 ID should return device name."""
        name = HardwareDetector.get_device_name_for_usb_id('239a:8029')
        assert name is not None

    def test_tbeam_name(self):
        """T-Beam ID should return device name."""
        name = HardwareDetector.get_device_name_for_usb_id('1a86:55d3')
        assert name is not None

    def test_unknown_id_returns_none(self):
        """Unknown USB ID should return None."""
        assert HardwareDetector.get_device_name_for_usb_id('ffff:ffff') is None


class TestTemplateFilesExist:
    """Verify that all referenced template files actually exist in the repo."""

    TEMPLATES_DIR = Path(__file__).parent.parent / 'templates' / 'available.d'

    def test_all_mapped_templates_exist(self):
        """Every template referenced in USB_ID_TO_TEMPLATE should exist on disk."""
        unique_templates = set(HardwareDetector.USB_ID_TO_TEMPLATE.values())
        for template in unique_templates:
            template_path = self.TEMPLATES_DIR / template
            assert template_path.exists(), (
                f"Template {template} referenced in USB_ID_TO_TEMPLATE "
                f"but not found at {template_path}"
            )
