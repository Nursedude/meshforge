"""
Tests for Device Scanner utility

TDD approach: Tests written for USB/Serial port detection.
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.device_scanner import (
    DeviceScanner,
    DeviceType,
    USBDevice,
    SerialPort,
)


class TestDeviceType(unittest.TestCase):
    """Test DeviceType enum"""

    def test_device_types_exist(self):
        """All expected device types should exist"""
        self.assertEqual(DeviceType.LORA_USB.value, "LoRa USB Module")
        self.assertEqual(DeviceType.SERIAL_ADAPTER.value, "USB-Serial Adapter")
        self.assertEqual(DeviceType.GPS.value, "GPS Module")
        self.assertEqual(DeviceType.SDR.value, "SDR Receiver")
        self.assertEqual(DeviceType.UNKNOWN.value, "Unknown Device")


class TestUSBDevice(unittest.TestCase):
    """Test USBDevice dataclass"""

    def test_usb_device_creation(self):
        """USBDevice should store all fields"""
        dev = USBDevice(
            bus="001",
            device="003",
            vendor_id="1a86",
            product_id="7523",
            description="CH340 serial converter",
            manufacturer="QinHeng",
            serial="",
            driver="ch341",
            device_type=DeviceType.SERIAL_ADAPTER,
            meshtastic_compatible=True,
            notes="Common CH340"
        )

        self.assertEqual(dev.bus, "001")
        self.assertEqual(dev.vendor_id, "1a86")
        self.assertEqual(dev.product_id, "7523")
        self.assertTrue(dev.meshtastic_compatible)
        self.assertEqual(dev.device_type, DeviceType.SERIAL_ADAPTER)


class TestSerialPort(unittest.TestCase):
    """Test SerialPort dataclass"""

    def test_serial_port_creation(self):
        """SerialPort should store device info"""
        port = SerialPort(
            device="/dev/ttyUSB0",
            by_id="/dev/serial/by-id/usb-Silicon_Labs_CP2102",
            by_path="/dev/serial/by-path/pci-0000:00:14.0-usb-0:1:1.0",
            usb_vendor="10c4",
            usb_product="ea60",
            driver="cp210x",
            description="CP2102 USB to UART",
            meshtastic_compatible=True,
        )

        self.assertEqual(port.device, "/dev/ttyUSB0")
        self.assertIn("CP2102", port.by_id)
        self.assertTrue(port.meshtastic_compatible)

    def test_serial_port_defaults(self):
        """SerialPort should have sensible defaults"""
        port = SerialPort(device="/dev/ttyUSB0")

        self.assertEqual(port.by_id, "")
        self.assertEqual(port.by_path, "")
        self.assertFalse(port.meshtastic_compatible)
        self.assertEqual(port.recommended_for, [])


class TestDeviceScanner(unittest.TestCase):
    """Test DeviceScanner class"""

    def test_known_devices_database(self):
        """Scanner should have known device database"""
        scanner = DeviceScanner()

        # CH340 should be known
        self.assertIn('1a86:7523', scanner.KNOWN_DEVICES)
        ch340 = scanner.KNOWN_DEVICES['1a86:7523']
        self.assertEqual(ch340['name'], 'CH340/CH341')
        self.assertTrue(ch340['meshtastic'])

        # CP2102 should be known
        self.assertIn('10c4:ea60', scanner.KNOWN_DEVICES)
        cp2102 = scanner.KNOWN_DEVICES['10c4:ea60']
        self.assertEqual(cp2102['type'], DeviceType.SERIAL_ADAPTER)

        # ESP32-S3 native USB should be known
        self.assertIn('303a:1002', scanner.KNOWN_DEVICES)
        esp32s3 = scanner.KNOWN_DEVICES['303a:1002']
        self.assertTrue(esp32s3['meshtastic'])
        self.assertIn('ESP32-S3', esp32s3['devices'][0])

    def test_known_devices_have_required_fields(self):
        """All known devices should have required fields"""
        scanner = DeviceScanner()

        for usb_id, info in scanner.KNOWN_DEVICES.items():
            self.assertIn('name', info, f"{usb_id} missing 'name'")
            self.assertIn('type', info, f"{usb_id} missing 'type'")
            self.assertIn('meshtastic', info, f"{usb_id} missing 'meshtastic'")
            self.assertIsInstance(info['type'], DeviceType)

    def test_sdr_devices_not_meshtastic(self):
        """SDR devices should not be marked as Meshtastic compatible"""
        scanner = DeviceScanner()

        # RTL-SDR
        self.assertIn('0bda:2832', scanner.KNOWN_DEVICES)
        rtlsdr = scanner.KNOWN_DEVICES['0bda:2832']
        self.assertEqual(rtlsdr['type'], DeviceType.SDR)
        self.assertFalse(rtlsdr['meshtastic'])

    @patch('subprocess.run')
    def test_scan_usb_devices_parses_lsusb(self, mock_run):
        """Scanner should parse lsusb output correctly"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 003: ID 1a86:7523 QinHeng Electronics CH340 serial converter
Bus 001 Device 004: ID 10c4:ea60 Silicon Labs CP210x UART Bridge
"""
        )

        scanner = DeviceScanner()
        devices = scanner._scan_usb_devices()

        self.assertEqual(len(devices), 3)

        # Check CH340 was identified
        ch340_devs = [d for d in devices if d.vendor_id == '1a86']
        self.assertEqual(len(ch340_devs), 1)
        self.assertTrue(ch340_devs[0].meshtastic_compatible)

        # Check CP210x was identified
        cp210x_devs = [d for d in devices if d.vendor_id == '10c4']
        self.assertEqual(len(cp210x_devs), 1)
        self.assertTrue(cp210x_devs[0].meshtastic_compatible)

    @patch('subprocess.run')
    def test_scan_handles_empty_lsusb(self, mock_run):
        """Scanner should handle empty lsusb output"""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        scanner = DeviceScanner()
        devices = scanner._scan_usb_devices()

        self.assertEqual(devices, [])

    @patch('subprocess.run')
    def test_scan_handles_lsusb_failure(self, mock_run):
        """Scanner should handle lsusb command failure"""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        scanner = DeviceScanner()
        devices = scanner._scan_usb_devices()

        self.assertEqual(devices, [])


class TestUdevRules(unittest.TestCase):
    """Test udev rules generation"""

    def test_generate_udev_rules(self):
        """Scanner should generate valid udev rules"""
        scanner = DeviceScanner()
        rules = scanner.generate_udev_rules()

        # Should contain header
        self.assertIn("MeshForge", rules)
        self.assertIn("99-meshtastic.rules", rules)

        # Should contain CH340 rule
        self.assertIn("1a86", rules)
        self.assertIn("7523", rules)

        # Should contain CP210x rule
        self.assertIn("10c4", rules)
        self.assertIn("ea60", rules)

        # Should set permissions
        self.assertIn('MODE="0666"', rules)
        self.assertIn('GROUP="dialout"', rules)

    def test_udev_rules_only_meshtastic_devices(self):
        """udev rules should only include Meshtastic devices"""
        scanner = DeviceScanner()
        rules = scanner.generate_udev_rules()

        # RTL-SDR (not Meshtastic) should not have a specific rule
        # Note: It may appear in comments but not as a rule
        lines = [l for l in rules.split('\n') if l.startswith('SUBSYSTEM')]
        rtlsdr_rules = [l for l in lines if '0bda' in l]
        self.assertEqual(len(rtlsdr_rules), 0)


class TestRecommendedPort(unittest.TestCase):
    """Test port recommendation logic"""

    def test_prefer_by_id_path(self):
        """Should prefer /dev/serial/by-id paths"""
        scanner = DeviceScanner()
        scanner.serial_ports = [
            SerialPort(
                device="/dev/ttyUSB0",
                by_id="/dev/serial/by-id/usb-CP2102",
                meshtastic_compatible=True,
            ),
            SerialPort(
                device="/dev/ttyUSB1",
                meshtastic_compatible=True,
            ),
        ]

        recommended = scanner._find_recommended_port()
        self.assertIn("by-id", recommended)

    def test_prefer_meshtastic_compatible(self):
        """Should prefer Meshtastic-compatible ports"""
        scanner = DeviceScanner()
        scanner.serial_ports = [
            SerialPort(
                device="/dev/ttyUSB0",
                meshtastic_compatible=False,
            ),
            SerialPort(
                device="/dev/ttyUSB1",
                meshtastic_compatible=True,
            ),
        ]

        recommended = scanner._find_recommended_port()
        self.assertEqual(recommended, "/dev/ttyUSB1")

    def test_fallback_to_first_usb(self):
        """Should fallback to first USB port if no compatible"""
        scanner = DeviceScanner()
        scanner.serial_ports = [
            SerialPort(device="/dev/ttyS0", meshtastic_compatible=False),
            SerialPort(device="/dev/ttyUSB0", meshtastic_compatible=False),
        ]

        recommended = scanner._find_recommended_port()
        self.assertEqual(recommended, "/dev/ttyUSB0")


class TestConfigHint(unittest.TestCase):
    """Test configuration hint generation"""

    def test_config_hint_includes_device_path(self):
        """Config hint should include device path"""
        scanner = DeviceScanner()
        port = SerialPort(
            device="/dev/ttyUSB0",
            by_id="/dev/serial/by-id/usb-CP2102",
            usb_vendor="10c4",
            usb_product="ea60",
        )

        hint = scanner.get_meshtastic_config_hint(port)

        # Should prefer by-id path
        self.assertEqual(hint['device_path'], "/dev/serial/by-id/usb-CP2102")
        self.assertTrue(hint['use_persistent_name'])
        self.assertIn("SerialPath:", hint['config_example'])

    def test_config_hint_fallback_device(self):
        """Config hint should fallback to device path"""
        scanner = DeviceScanner()
        port = SerialPort(device="/dev/ttyUSB0")

        hint = scanner.get_meshtastic_config_hint(port)

        self.assertEqual(hint['device_path'], "/dev/ttyUSB0")
        self.assertFalse(hint['use_persistent_name'])


class TestReportGeneration(unittest.TestCase):
    """Test report formatting"""

    def test_format_report_includes_sections(self):
        """Report should include all sections"""
        scanner = DeviceScanner()
        scanner.usb_devices = []
        scanner.serial_ports = []

        report = scanner.format_report()

        self.assertIn("MESHFORGE DEVICE SCANNER", report)
        self.assertIn("USB DEVICES", report)
        self.assertIn("SERIAL PORTS", report)
        self.assertIn("RECOMMENDATION", report)

    def test_format_report_shows_meshtastic_compatible(self):
        """Report should indicate Meshtastic compatibility"""
        scanner = DeviceScanner()
        scanner.usb_devices = [
            USBDevice(
                bus="001",
                device="003",
                vendor_id="1a86",
                product_id="7523",
                description="CH340",
                meshtastic_compatible=True,
            )
        ]
        scanner.serial_ports = []

        report = scanner.format_report()

        self.assertIn("Meshtastic", report)


if __name__ == '__main__':
    unittest.main()
