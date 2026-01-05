"""
Device Scanner - USB and Serial Port Detection Utility

Linux equivalent to Windows tools like USBDeview (NirSoft) and
Advanced Serial Port Monitor (AGG Software).

Provides easy hardware detection for Meshtastic, LoRa, and RF devices.

Safety: Core utility with no external dependencies beyond standard library.
"""

import os
import subprocess
import glob
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class DeviceType(Enum):
    """Device classification"""
    LORA_USB = "LoRa USB Module"
    SERIAL_ADAPTER = "USB-Serial Adapter"
    GPS = "GPS Module"
    SDR = "SDR Receiver"
    BLUETOOTH = "Bluetooth Adapter"
    UNKNOWN = "Unknown Device"


@dataclass
class USBDevice:
    """USB device information"""
    bus: str
    device: str
    vendor_id: str
    product_id: str
    description: str
    manufacturer: str = ""
    serial: str = ""
    driver: str = ""
    device_type: DeviceType = DeviceType.UNKNOWN
    meshtastic_compatible: bool = False
    notes: str = ""


@dataclass
class SerialPort:
    """Serial port information"""
    device: str  # /dev/ttyUSB0
    by_id: str = ""  # /dev/serial/by-id/...
    by_path: str = ""  # /dev/serial/by-path/...
    usb_vendor: str = ""
    usb_product: str = ""
    driver: str = ""
    description: str = ""
    subsystem: str = ""  # usb, pci, platform
    meshtastic_compatible: bool = False
    recommended_for: List[str] = field(default_factory=list)


class DeviceScanner:
    """
    Comprehensive USB and Serial Port Scanner

    Similar to:
    - USBDeview (NirSoft) for Windows
    - Advanced Serial Port Monitor (AGG Software)

    But designed for Linux/Raspberry Pi and Meshtastic hardware.
    """

    # Known USB Vendor:Product IDs for LoRa/Meshtastic devices
    KNOWN_DEVICES = {
        # USB-Serial Chips (common in LoRa modules)
        '1a86:7523': {
            'name': 'CH340/CH341',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['MeshToad', 'MeshTadpole', 'Heltec V2', 'TTGO LoRa'],
            'notes': 'Common in Chinese LoRa modules. No unique serial - use by-path.',
        },
        '1a86:55d4': {
            'name': 'CH9102',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['ESP32-S3 boards'],
            'notes': 'Newer CH341 variant with better stability.',
        },
        '10c4:ea60': {
            'name': 'CP2102/CP2104',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['Heltec V3', 'T-Beam', 'RAK4631'],
            'notes': 'Silicon Labs. Supports unique serial numbers.',
        },
        '10c4:ea70': {
            'name': 'CP2105',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['Dual-port modules'],
            'notes': 'Dual UART variant.',
        },
        '0403:6001': {
            'name': 'FT232R',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['FTDI-based LoRa modules'],
            'notes': 'FTDI. Reliable, unique serial numbers.',
        },
        '0403:6015': {
            'name': 'FT231X',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': True,
            'devices': ['Adafruit Feather'],
            'notes': 'FTDI. Good for Adafruit boards.',
        },
        '303a:1001': {
            'name': 'ESP32-S2 Native USB',
            'type': DeviceType.LORA_USB,
            'meshtastic': True,
            'devices': ['ESP32-S2 DevKit'],
            'notes': 'Native USB CDC/JTAG.',
        },
        '303a:1002': {
            'name': 'ESP32-S3 Native USB',
            'type': DeviceType.LORA_USB,
            'meshtastic': True,
            'devices': ['ESP32-S3 DevKit', 'Heltec V3', 'T-Deck'],
            'notes': 'Native USB CDC/JTAG. Preferred for new designs.',
        },
        '239a:8029': {
            'name': 'Adafruit Feather nRF52840',
            'type': DeviceType.LORA_USB,
            'meshtastic': True,
            'devices': ['Feather nRF52840'],
            'notes': 'Nordic nRF52840 with Adafruit bootloader.',
        },
        '2e8a:0005': {
            'name': 'Raspberry Pi Pico',
            'type': DeviceType.SERIAL_ADAPTER,
            'meshtastic': False,
            'devices': ['RP2040 boards'],
            'notes': 'RP2040 USB. Not commonly used for Meshtastic.',
        },
        # SDR devices
        '0bda:2832': {
            'name': 'RTL2832U SDR',
            'type': DeviceType.SDR,
            'meshtastic': False,
            'devices': ['RTL-SDR', 'NooElec', 'HackerGadgets'],
            'notes': 'Software Defined Radio. Use for spectrum analysis.',
        },
        '0bda:2838': {
            'name': 'RTL2838 SDR',
            'type': DeviceType.SDR,
            'meshtastic': False,
            'devices': ['RTL-SDR v3'],
            'notes': 'Common RTL-SDR variant.',
        },
        # GPS receivers
        '1546:01a7': {
            'name': 'u-blox 7 GPS',
            'type': DeviceType.GPS,
            'meshtastic': False,
            'devices': ['NEO-7M', 'VK-172'],
            'notes': 'USB GPS receiver.',
        },
        '067b:2303': {
            'name': 'PL2303 (GPS common)',
            'type': DeviceType.GPS,
            'meshtastic': False,
            'devices': ['Generic USB GPS'],
            'notes': 'Prolific. Common in GPS modules.',
        },
    }

    def __init__(self):
        self.usb_devices: List[USBDevice] = []
        self.serial_ports: List[SerialPort] = []

    def scan_all(self) -> Dict:
        """
        Perform full system scan.

        Returns dict with:
        - usb_devices: List of USB devices
        - serial_ports: List of serial ports
        - meshtastic_candidates: Devices likely to be Meshtastic nodes
        - recommended_port: Best port to use for Meshtastic connection
        """
        self.usb_devices = self._scan_usb_devices()
        self.serial_ports = self._scan_serial_ports()

        meshtastic_candidates = [
            d for d in self.usb_devices if d.meshtastic_compatible
        ]

        recommended = self._find_recommended_port()

        return {
            'usb_devices': self.usb_devices,
            'serial_ports': self.serial_ports,
            'meshtastic_candidates': meshtastic_candidates,
            'recommended_port': recommended,
        }

    def _scan_usb_devices(self) -> List[USBDevice]:
        """Scan USB bus for devices"""
        devices = []

        try:
            # Use lsusb for basic enumeration
            result = subprocess.run(
                ['lsusb'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return devices

            # Parse lsusb output
            # Format: Bus 001 Device 003: ID 1a86:7523 QinHeng Electronics CH340 serial converter
            pattern = r'Bus (\d+) Device (\d+): ID ([0-9a-f]{4}):([0-9a-f]{4})\s*(.*)'

            for line in result.stdout.strip().split('\n'):
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    bus, device, vendor, product, desc = match.groups()

                    usb_id = f"{vendor}:{product}"
                    known = self.KNOWN_DEVICES.get(usb_id, {})

                    dev = USBDevice(
                        bus=bus,
                        device=device,
                        vendor_id=vendor,
                        product_id=product,
                        description=desc.strip() or known.get('name', 'Unknown'),
                        device_type=known.get('type', DeviceType.UNKNOWN),
                        meshtastic_compatible=known.get('meshtastic', False),
                        notes=known.get('notes', ''),
                    )

                    # Try to get more details via sysfs
                    self._enrich_usb_device(dev)

                    devices.append(dev)

        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            # lsusb not available
            pass
        except Exception:
            pass

        return devices

    def _enrich_usb_device(self, dev: USBDevice):
        """Add details from sysfs"""
        try:
            # Find device in sysfs
            usb_path = Path(f"/sys/bus/usb/devices/{dev.bus}-{dev.device}")
            if not usb_path.exists():
                # Try alternative path format
                for p in Path("/sys/bus/usb/devices").glob(f"*"):
                    try:
                        busnum = (p / "busnum").read_text().strip()
                        devnum = (p / "devnum").read_text().strip()
                        if busnum == dev.bus and devnum == dev.device:
                            usb_path = p
                            break
                    except Exception:
                        continue

            if usb_path.exists():
                # Read manufacturer
                mfg_path = usb_path / "manufacturer"
                if mfg_path.exists():
                    dev.manufacturer = mfg_path.read_text().strip()

                # Read serial
                serial_path = usb_path / "serial"
                if serial_path.exists():
                    dev.serial = serial_path.read_text().strip()

                # Read driver
                driver_link = usb_path / "driver"
                if driver_link.is_symlink():
                    dev.driver = driver_link.resolve().name

        except Exception:
            pass

    def _scan_serial_ports(self) -> List[SerialPort]:
        """Scan for serial ports"""
        ports = []

        # Find all tty devices
        tty_patterns = [
            '/dev/ttyUSB*',
            '/dev/ttyACM*',
            '/dev/ttyAMA*',
            '/dev/ttyS*',
            '/dev/serial*',
        ]

        seen_devices = set()

        for pattern in tty_patterns:
            for device_path in glob.glob(pattern):
                device = os.path.basename(device_path)

                # Skip duplicates and non-existent
                if device in seen_devices:
                    continue
                if not os.path.exists(device_path):
                    continue

                seen_devices.add(device)

                port = SerialPort(device=device_path)
                self._enrich_serial_port(port)

                # Only include real devices (not virtual ttyS0-3)
                if port.subsystem == 'platform' and device.startswith('ttyS'):
                    # Skip virtual serial ports
                    continue

                ports.append(port)

        # Also check by-id and by-path symlinks
        self._add_persistent_names(ports)

        return ports

    def _enrich_serial_port(self, port: SerialPort):
        """Add details from udev/sysfs"""
        try:
            device_name = os.path.basename(port.device)

            # Use udevadm to get device info
            result = subprocess.run(
                ['udevadm', 'info', '--query=all', '--name', port.device],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'ID_VENDOR_ID=' in line:
                        port.usb_vendor = line.split('=')[1].strip()
                    elif 'ID_MODEL_ID=' in line:
                        port.usb_product = line.split('=')[1].strip()
                    elif 'ID_USB_DRIVER=' in line:
                        port.driver = line.split('=')[1].strip()
                    elif 'ID_MODEL=' in line:
                        port.description = line.split('=')[1].strip().replace('_', ' ')
                    elif 'SUBSYSTEM=' in line:
                        port.subsystem = line.split('=')[1].strip()

                # Check if Meshtastic compatible
                usb_id = f"{port.usb_vendor}:{port.usb_product}"
                known = self.KNOWN_DEVICES.get(usb_id, {})
                port.meshtastic_compatible = known.get('meshtastic', False)
                port.recommended_for = known.get('devices', [])

        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            # udevadm not available
            pass
        except Exception:
            pass

    def _add_persistent_names(self, ports: List[SerialPort]):
        """Add persistent /dev/serial/by-id and by-path names"""

        # by-id symlinks (stable across reboots if device has serial)
        by_id_dir = Path('/dev/serial/by-id')
        if by_id_dir.exists():
            for link in by_id_dir.iterdir():
                if link.is_symlink():
                    target = os.path.realpath(link)
                    for port in ports:
                        if port.device == target or port.device == os.path.basename(target):
                            port.by_id = str(link)

        # by-path symlinks (stable by physical USB port)
        by_path_dir = Path('/dev/serial/by-path')
        if by_path_dir.exists():
            for link in by_path_dir.iterdir():
                if link.is_symlink():
                    target = os.path.realpath(link)
                    for port in ports:
                        if port.device == target or port.device == os.path.basename(target):
                            port.by_path = str(link)

    def _find_recommended_port(self) -> Optional[str]:
        """Find the best port for Meshtastic connection"""

        # Priority:
        # 1. Known Meshtastic-compatible with by-id path
        # 2. Known Meshtastic-compatible with by-path
        # 3. Known Meshtastic-compatible any ttyUSB/ttyACM
        # 4. Any ttyUSB0/ttyACM0

        meshtastic_ports = [
            p for p in self.serial_ports if p.meshtastic_compatible
        ]

        if meshtastic_ports:
            # Prefer ports with persistent naming
            for port in meshtastic_ports:
                if port.by_id:
                    return port.by_id

            for port in meshtastic_ports:
                if port.by_path:
                    return port.by_path

            return meshtastic_ports[0].device

        # Fallback to first USB serial
        usb_ports = [
            p for p in self.serial_ports
            if 'ttyUSB' in p.device or 'ttyACM' in p.device
        ]

        if usb_ports:
            return usb_ports[0].device

        return None

    def get_meshtastic_config_hint(self, port: SerialPort) -> Dict:
        """
        Get configuration hints for a port.

        Returns dict suitable for meshtasticd config or Python API.
        """
        usb_id = f"{port.usb_vendor}:{port.usb_product}"
        known = self.KNOWN_DEVICES.get(usb_id, {})

        return {
            'device_path': port.by_id or port.by_path or port.device,
            'driver': port.driver,
            'chip': known.get('name', 'Unknown'),
            'use_persistent_name': bool(port.by_id or port.by_path),
            'config_example': f"""
# meshtasticd config.yaml
Lora:
  SerialPath: {port.by_id or port.by_path or port.device}

# Python API
import meshtastic.serial_interface
interface = meshtastic.serial_interface.SerialInterface(
    devPath="{port.by_id or port.by_path or port.device}"
)
""",
        }

    def generate_udev_rules(self) -> str:
        """
        Generate udev rules for persistent device naming.

        Install to: /etc/udev/rules.d/99-meshtastic.rules
        """
        rules = [
            "# MeshForge - Meshtastic Device udev Rules",
            "# Install to: /etc/udev/rules.d/99-meshtastic.rules",
            "# Reload: sudo udevadm control --reload-rules && sudo udevadm trigger",
            "",
            "# Grant non-root access to Meshtastic-compatible USB-serial devices",
            "",
        ]

        for usb_id, info in self.KNOWN_DEVICES.items():
            if not info.get('meshtastic', False):
                continue

            vendor, product = usb_id.split(':')
            device_list = ', '.join(info.get('devices', []))

            rules.append(f"# {info['name']} ({device_list})")
            rules.append(
                f'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{vendor}", '
                f'ATTRS{{idProduct}}=="{product}", '
                f'MODE="0666", GROUP="dialout", '
                f'SYMLINK+="meshtastic_%n"'
            )
            rules.append("")

        # Add rule for all Meshtastic devices
        rules.extend([
            "# Allow non-root access to all ttyUSB and ttyACM devices",
            'KERNEL=="ttyUSB[0-9]*", MODE="0666", GROUP="dialout"',
            'KERNEL=="ttyACM[0-9]*", MODE="0666", GROUP="dialout"',
            "",
            "# ESP32 native USB (bootloader and application)",
            'SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", MODE="0666", GROUP="dialout"',
        ])

        return '\n'.join(rules)

    def format_report(self) -> str:
        """Generate human-readable scan report"""
        lines = [
            "=" * 70,
            "  MESHFORGE DEVICE SCANNER",
            "  USB & Serial Port Detection Report",
            "=" * 70,
            "",
        ]

        # USB Devices
        lines.append("USB DEVICES")
        lines.append("-" * 70)

        if not self.usb_devices:
            lines.append("  No USB devices detected")
        else:
            for dev in self.usb_devices:
                compat = "✓ Meshtastic" if dev.meshtastic_compatible else ""
                lines.append(
                    f"  [{dev.vendor_id}:{dev.product_id}] {dev.description}"
                )
                if compat:
                    lines.append(f"    {compat}")
                if dev.serial:
                    lines.append(f"    Serial: {dev.serial}")
                if dev.notes:
                    lines.append(f"    Note: {dev.notes}")
                lines.append("")

        # Serial Ports
        lines.append("\nSERIAL PORTS")
        lines.append("-" * 70)

        if not self.serial_ports:
            lines.append("  No serial ports detected")
        else:
            for port in self.serial_ports:
                compat = "✓" if port.meshtastic_compatible else " "
                lines.append(f"  [{compat}] {port.device}")
                if port.description:
                    lines.append(f"      Chip: {port.description}")
                if port.by_id:
                    lines.append(f"      by-id: {port.by_id}")
                if port.by_path:
                    lines.append(f"      by-path: {port.by_path}")
                if port.recommended_for:
                    lines.append(f"      Known in: {', '.join(port.recommended_for)}")
                lines.append("")

        # Recommendation
        recommended = self._find_recommended_port()
        lines.append("\nRECOMMENDATION")
        lines.append("-" * 70)

        if recommended:
            lines.append(f"  Use: {recommended}")
            lines.append("")
            lines.append("  For meshtasticd config.yaml:")
            lines.append(f"    SerialPath: {recommended}")
            lines.append("")
            lines.append("  For Python API:")
            lines.append(f'    interface = meshtastic.serial_interface.SerialInterface("{recommended}")')
        else:
            lines.append("  No Meshtastic-compatible devices detected")
            lines.append("  Ensure device is connected and drivers are loaded")

        lines.append("")
        lines.append("=" * 70)

        return '\n'.join(lines)


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='MeshForge Device Scanner - USB & Serial Port Detection'
    )
    parser.add_argument(
        '--json', action='store_true',
        help='Output as JSON'
    )
    parser.add_argument(
        '--udev', action='store_true',
        help='Generate udev rules for persistent naming'
    )
    parser.add_argument(
        '--meshtastic-only', action='store_true',
        help='Only show Meshtastic-compatible devices'
    )

    args = parser.parse_args()

    scanner = DeviceScanner()
    results = scanner.scan_all()

    if args.udev:
        print(scanner.generate_udev_rules())
    elif args.json:
        import json
        # Convert to JSON-serializable format
        output = {
            'usb_devices': [
                {
                    'vendor_id': d.vendor_id,
                    'product_id': d.product_id,
                    'description': d.description,
                    'serial': d.serial,
                    'meshtastic_compatible': d.meshtastic_compatible,
                }
                for d in results['usb_devices']
            ],
            'serial_ports': [
                {
                    'device': p.device,
                    'by_id': p.by_id,
                    'by_path': p.by_path,
                    'description': p.description,
                    'meshtastic_compatible': p.meshtastic_compatible,
                }
                for p in results['serial_ports']
            ],
            'recommended_port': results['recommended_port'],
        }
        print(json.dumps(output, indent=2))
    else:
        print(scanner.format_report())


if __name__ == '__main__':
    main()
