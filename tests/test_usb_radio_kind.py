"""USB radio detection routes by device KIND, and no shipped overlay is inert.

meshtasticd 2.7.26 parses 11 top-level keys and `Serial:` is not one
(PortduinoGlue.cpp; queued B7, re-measured 2026-09-30). The old
USB_ID_TO_TEMPLATE sent every standalone node (RAK/Heltec/T-Beam/G2) to a
`Serial:` overlay meshtasticd ignores, and mapped CH340 UARTs to "MeshToad".
Now an ID says only what the device IS:
  'ch341' — CH341 USB-SPI board, driven by a `Lora: … spidev: ch341` overlay
  'node'  — a tty running its own Meshtastic firmware, reached over serial
"""

import pytest
from pathlib import Path

pytest.importorskip("rich", reason="rich required for config.hardware module")

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from config.hardware import HardwareDetector
from core.meshtasticd_templates import (
    RADIO_TEMPLATES, MESHTASTICD_TOP_LEVEL_KEYS, classify_overlay,
)

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates' / 'available.d'


class TestUSBRadioKind:

    def test_ch341_spi_bridge_is_ch341(self):
        assert HardwareDetector.usb_radio_kind('1a86:5512') == 'ch341'

    @pytest.mark.parametrize('usb_id', [
        '303a:1001', '303a:4001', '303a:1002', '239a:8029', '239a:0029',
        '19d2:0016', '10c4:ea60', '1a86:55d3', '1a86:7523', '1a86:55d4',
        '1a86:7522', '0403:6001', '0403:6015',
    ])
    def test_tty_chips_are_standalone_nodes(self, usb_id):
        assert HardwareDetector.usb_radio_kind(usb_id) == 'node'

    def test_only_one_id_is_ch341(self):
        # every CH341 board shares 1a86:5512 — the ID cannot pick an overlay
        assert [k for k, v in HardwareDetector.USB_ID_KIND.items() if v == 'ch341'] == ['1a86:5512']

    def test_unknown_and_case(self):
        assert HardwareDetector.usb_radio_kind('ffff:ffff') is None
        assert HardwareDetector.usb_radio_kind('') is None
        assert HardwareDetector.usb_radio_kind('1A86:5512') == 'ch341'

    def test_no_template_map_survives(self):
        assert not hasattr(HardwareDetector, 'USB_ID_TO_TEMPLATE')
        assert not hasattr(HardwareDetector, 'match_usb_to_template')


class TestGetDeviceNameForUSBID:

    def test_heltec_name(self):
        assert 'Heltec' in HardwareDetector.get_device_name_for_usb_id('303a:1001')

    def test_rak_name(self):
        assert HardwareDetector.get_device_name_for_usb_id('239a:8029') is not None

    def test_tbeam_name(self):
        assert HardwareDetector.get_device_name_for_usb_id('1a86:55d3') is not None

    def test_unknown_id_returns_none(self):
        assert HardwareDetector.get_device_name_for_usb_id('ffff:ffff') is None


class TestClassifyOverlay:

    def test_serial_only_is_ignored(self):
        assert classify_overlay("Serial:\n  Device: auto\n") == 'ignored'

    def test_not_yaml_or_empty_is_ignored(self):
        for text in ("", "# only a comment\n", "just text", "Lora: [unclosed"):
            assert classify_overlay(text) == 'ignored', text

    def test_judged_after_the_sanitizer_strips(self):
        # the old wizard's usb-serial.yaml / historical heltec-usb.yaml shape:
        # activation strips Webserver/Logging/TCP -> a Serial-only file
        old = "Serial:\n  Device: auto\nTCP:\n  Port: 4403\nWebserver:\n  Port: 443\nLogging:\n  LogLevel: info\n"
        assert classify_overlay(old) == 'ignored'
        for only in ("General:\n  MaxNodes: 1\n", "Logging:\n  LogLevel: debug\n",
                     "Webserver:\n  Port: 9443\n"):
            assert classify_overlay(only) == 'ignored', only

    def test_ch341_is_exact_like_the_firmware(self):
        assert classify_overlay("Lora:\n  Module: sx1262\n  spidev: ch341\n") == 'ch341'
        # PortduinoGlue.cpp compares lora_spi_dev == "ch341" exactly
        assert classify_overlay("Lora:\n  spidev: CH341\n") == 'spi'

    def test_spi_hat_is_spi(self):
        assert classify_overlay("Lora:\n  Module: sx1262\n  CS: 21\n") == 'spi'
        assert classify_overlay("Lora:\n  spidev: /dev/spidev0.0\n") == 'spi'

    def test_non_radio_overlays_are_aux(self):
        for text in ("GPS:\n  SerialPath: /dev/ttyS0\n", "Display:\n  Panel: ST7789\n",
                     "I2C:\n  I2CDevice: /dev/i2c-7\n"):
            assert classify_overlay(text) == 'aux', text

    def test_first_document_only_like_yaml_cpp(self):
        assert classify_overlay("Lora:\n  spidev: ch341\n---\nSerial: {}\n") == 'ch341'

    def test_key_set_is_measured_from_the_firmware(self):
        import re
        from utils.paths import get_real_user_home
        src = get_real_user_home() / "mtd-build" / "firmware" / "src" / "platform" / "portduino" / "PortduinoGlue.cpp"
        if not src.exists():
            pytest.skip("firmware source not on this box")
        found = set(re.findall(r'yamlConfig\["([A-Za-z0-9_]+)"\]', src.read_text(errors="replace")))
        assert found == set(MESHTASTICD_TOP_LEVEL_KEYS), found ^ set(MESHTASTICD_TOP_LEVEL_KEYS)
        assert 'Serial' not in MESHTASTICD_TOP_LEVEL_KEYS


class TestShippedTemplatesAreReal:
    """Nothing MeshForge ships as a radio config may be one meshtasticd ignores."""

    def test_no_shipped_file_is_ignored(self):
        files = sorted(TEMPLATES_DIR.glob('*.yaml'))
        assert files, TEMPLATES_DIR
        inert = [f.name for f in files if classify_overlay(f.read_text()) == 'ignored']
        assert inert == [], inert

    def test_no_inline_fallback_is_ignored(self):
        inert = [n for n, t in RADIO_TEMPLATES.items() if classify_overlay(t['config']) == 'ignored']
        assert inert == [], inert

    def test_upstream_ch341_overlays_ship_and_classify(self):
        for name in ('lora-usb-meshtoad-e22.yaml', 'lora-pinedio-usb-sx1262.yaml'):
            assert classify_overlay((TEMPLATES_DIR / name).read_text()) == 'ch341', name
