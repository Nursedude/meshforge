"""
RNode Device Detection and Management

Provides device discovery for RNode LoRa interfaces.
RNodes are Reticulum-compatible LoRa transceivers that provide
long-range mesh networking capability.

Usage:
    from commands.rnode import detect_devices, get_device_info

    devices = detect_devices()
    for device in devices:
        print(f"Found: {device['port']} - {device['model']}")
"""

import os
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

from commands.base import CommandResult
from utils.safe_import import safe_import

_serial_tools, _HAS_SERIAL_TOOLS = safe_import('serial.tools.list_ports')
_serial_mod, _HAS_SERIAL = safe_import('serial')

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Common USB vendor/product IDs for RNode-compatible devices
RNODE_USB_IDS = [
    # ============================================================================
    # Official/Common RNode USB Chips
    # ============================================================================
    {'vid': '1a86', 'pid': '55d4', 'name': 'RNode (CH340)'},
    {'vid': '1a86', 'pid': '7523', 'name': 'RNode (CH340G)'},
    {'vid': '1a86', 'pid': '5523', 'name': 'RNode (CH341)'},
    {'vid': '10c4', 'pid': 'ea60', 'name': 'RNode (CP210x)'},
    {'vid': '10c4', 'pid': 'ea61', 'name': 'RNode (CP2102N)'},
    {'vid': '0403', 'pid': '6001', 'name': 'RNode (FTDI FT232)'},
    {'vid': '0403', 'pid': '6010', 'name': 'RNode (FTDI FT2232)'},
    {'vid': '0403', 'pid': '6015', 'name': 'RNode (FTDI FT231X)'},

    # ============================================================================
    # Lilygo T-Beam (common RNode platform)
    # ============================================================================
    {'vid': '303a', 'pid': '1001', 'name': 'T-Beam (ESP32-S3)'},
    {'vid': '303a', 'pid': '0002', 'name': 'T-Beam (ESP32-S2)'},
    {'vid': '303a', 'pid': '80d1', 'name': 'T-Beam (ESP32-C6)'},
    {'vid': '1a86', 'pid': '55d4', 'name': 'T-Beam v1.x (CH340)'},

    # ============================================================================
    # Heltec LoRa32 Boards
    # ============================================================================
    {'vid': '10c4', 'pid': 'ea60', 'name': 'Heltec LoRa32'},
    {'vid': '303a', 'pid': '1001', 'name': 'Heltec WiFi LoRa 32 V3'},

    # ============================================================================
    # RAK Wireless Boards (used for RNode)
    # ============================================================================
    {'vid': '239a', 'pid': '8029', 'name': 'RAK4631 (nRF52840)'},
    {'vid': '239a', 'pid': '0029', 'name': 'RAK WisBlock (nRF52840)'},
    {'vid': '1915', 'pid': '520f', 'name': 'RAK nRF52840'},

    # ============================================================================
    # Adafruit Feather LoRa Boards
    # ============================================================================
    {'vid': '239a', 'pid': '800b', 'name': 'Feather M0 LoRa'},
    {'vid': '239a', 'pid': '8023', 'name': 'Feather nRF52840'},
    {'vid': '239a', 'pid': '80cd', 'name': 'Feather ESP32-S3'},

    # ============================================================================
    # Generic ESP32/LoRa Modules
    # ============================================================================
    {'vid': '303a', 'pid': '0002', 'name': 'ESP32-S2'},
    {'vid': '303a', 'pid': '1001', 'name': 'ESP32-S3'},
    {'vid': '303a', 'pid': '80c6', 'name': 'ESP32-C3'},
    {'vid': '303a', 'pid': '80d1', 'name': 'ESP32-C6'},
    {'vid': '10c4', 'pid': 'ea70', 'name': 'ESP32 DevKit'},
]

# RNode-specific identification - boards that often have RNode firmware
RNODE_LIKELY_DEVICES = {
    'T-Beam': True,
    'Heltec': True,
    'LoRa32': True,
    'RNode': True,
    'RAK4631': True,
    'Feather': True,
}

# Serial port patterns to scan
SERIAL_PATTERNS = [
    '/dev/ttyUSB*',
    '/dev/ttyACM*',
    '/dev/tty.usb*',  # macOS
    '/dev/cu.usb*',   # macOS
]

# RNode identification strings
RNODE_ID_STRINGS = [
    b'RNode',
    b'Reticulum',
    b'rns_fw',
    b'T-Beam',
]


# ============================================================================
# Device Detection
# ============================================================================

@dataclass
class RNodeDevice:
    """Represents a detected RNode device."""
    port: str
    model: str = "Unknown"
    vid: str = ""
    pid: str = ""
    serial: str = ""
    firmware_version: str = ""
    is_rnode: bool = False
    is_configured: bool = False
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'port': self.port,
            'model': self.model,
            'vid': self.vid,
            'pid': self.pid,
            'serial': self.serial,
            'firmware_version': self.firmware_version,
            'is_rnode': self.is_rnode,
            'is_configured': self.is_configured,
            'details': self.details,
        }


def get_serial_ports() -> List[str]:
    """
    Get list of available serial ports.

    Returns:
        List of serial port paths
    """
    ports = []

    # Method 1: Glob /dev/tty*
    for pattern in SERIAL_PATTERNS:
        base_dir = Path(pattern).parent
        glob_pattern = Path(pattern).name
        if base_dir.exists():
            ports.extend([str(p) for p in base_dir.glob(glob_pattern)])

    # Method 2: Use pyserial if available
    if _HAS_SERIAL_TOOLS:
        for port in _serial_tools.comports():
            if port.device not in ports:
                ports.append(port.device)

    # Filter out non-existent
    ports = [p for p in ports if Path(p).exists()]

    return sorted(set(ports))


def get_usb_info(port: str) -> Dict[str, str]:
    """
    Get USB vendor/product info for a serial port.

    Args:
        port: Serial port path (e.g., /dev/ttyUSB0)

    Returns:
        Dict with vid, pid, serial, manufacturer, product
    """
    info = {'vid': '', 'pid': '', 'serial': '', 'manufacturer': '', 'product': ''}

    try:
        # Get device name from port
        dev_name = Path(port).name

        # Read from sysfs
        sysfs_paths = [
            f'/sys/class/tty/{dev_name}/device',
            f'/sys/class/tty/{dev_name}/device/..',
        ]

        for base in sysfs_paths:
            base_path = Path(base)
            if not base_path.exists():
                continue

            # Walk up to find USB device info
            for _ in range(5):  # Max depth
                vid_path = base_path / 'idVendor'
                if vid_path.exists():
                    info['vid'] = vid_path.read_text().strip()
                    info['pid'] = (base_path / 'idProduct').read_text().strip()

                    serial_path = base_path / 'serial'
                    if serial_path.exists():
                        info['serial'] = serial_path.read_text().strip()

                    mfr_path = base_path / 'manufacturer'
                    if mfr_path.exists():
                        info['manufacturer'] = mfr_path.read_text().strip()

                    prod_path = base_path / 'product'
                    if prod_path.exists():
                        info['product'] = prod_path.read_text().strip()

                    break

                base_path = base_path.parent
                if str(base_path) == '/sys':
                    break

    except (OSError, PermissionError) as e:
        logger.debug(f"Could not read USB info for {port}: {e}")

    # Fallback: use pyserial
    if not info['vid'] and _HAS_SERIAL_TOOLS:
        for p in _serial_tools.comports():
            if p.device == port:
                info['vid'] = f'{p.vid:04x}' if p.vid else ''
                info['pid'] = f'{p.pid:04x}' if p.pid else ''
                info['serial'] = p.serial_number or ''
                info['manufacturer'] = p.manufacturer or ''
                info['product'] = p.product or ''
                break

    return info


def identify_device_model(vid: str, pid: str, product: str = '', manufacturer: str = '') -> str:
    """
    Identify device model from USB IDs.

    Args:
        vid: USB vendor ID (hex string)
        pid: USB product ID (hex string)
        product: USB product string
        manufacturer: USB manufacturer string

    Returns:
        Device model name
    """
    vid_lower = vid.lower()
    pid_lower = pid.lower()

    # Check known IDs
    for known in RNODE_USB_IDS:
        if known['vid'] == vid_lower and known['pid'] == pid_lower:
            return known['name']

    # Check product string for RNode-likely keywords
    if product:
        product_upper = product.upper()
        for keyword in RNODE_LIKELY_DEVICES:
            if keyword.upper() in product_upper:
                return f"{product} (RNode compatible)"
        return product

    # Check manufacturer for hints
    if manufacturer:
        mfr_upper = manufacturer.upper()
        if 'LILYGO' in mfr_upper:
            return "Lilygo Device (RNode compatible)"
        if 'HELTEC' in mfr_upper:
            return "Heltec Device (RNode compatible)"
        if 'RAK' in mfr_upper:
            return "RAK Wireless (RNode compatible)"
        if 'ESPRESSIF' in mfr_upper or 'ESP' in mfr_upper:
            return "ESP32 Device (RNode compatible)"

    return "Unknown USB Serial"


def is_likely_rnode(model: str, product: str = '', manufacturer: str = '') -> bool:
    """
    Check if a device is likely an RNode based on identification.

    Args:
        model: Identified device model
        product: USB product string
        manufacturer: USB manufacturer string

    Returns:
        True if device is likely an RNode
    """
    # Check model name
    model_upper = model.upper()
    for keyword in RNODE_LIKELY_DEVICES:
        if keyword.upper() in model_upper:
            return True

    # Check product string
    if product:
        product_upper = product.upper()
        for keyword in RNODE_LIKELY_DEVICES:
            if keyword.upper() in product_upper:
                return True

    # Check manufacturer
    if manufacturer:
        mfr_upper = manufacturer.upper()
        if any(x in mfr_upper for x in ['LILYGO', 'HELTEC', 'RAK', 'RNODE']):
            return True

    return False


def probe_rnode(port: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """
    Probe a serial port for RNode firmware.

    Args:
        port: Serial port path
        timeout: Connection timeout in seconds

    Returns:
        Dict with firmware info or None if not an RNode
    """
    if not _HAS_SERIAL:
        logger.debug("pyserial not installed, skipping probe")
        return None

    result = {'is_rnode': False, 'firmware_version': '', 'details': {}}

    try:
        # Open port briefly
        with _serial_mod.Serial(port, 115200, timeout=timeout) as ser:
            # Send RNode identification command
            # RNode firmware responds to specific commands
            ser.write(b'\x00')  # Null byte often triggers response
            ser.flush()

            # Read response
            import time
            time.sleep(0.5)
            response = ser.read(256)

            # Check for RNode identification strings
            for id_str in RNODE_ID_STRINGS:
                if id_str in response:
                    result['is_rnode'] = True
                    break

            # Try to parse firmware version
            if b'RNode' in response:
                result['is_rnode'] = True
                # Look for version string like "v1.2.3"
                version_match = re.search(rb'v?\d+\.\d+(\.\d+)?', response)
                if version_match:
                    result['firmware_version'] = version_match.group(0).decode('utf-8', errors='ignore')

            result['details']['raw_response'] = response[:64].hex() if response else ''

    except _serial_mod.SerialException as e:
        logger.debug(f"Could not probe {port}: {e}")
        result['details']['error'] = str(e)
    except Exception as e:
        logger.debug(f"Probe error for {port}: {e}")

    return result


def check_rns_config(port: str) -> bool:
    """
    Check if a port is configured in RNS config.

    Args:
        port: Serial port path

    Returns:
        True if port is in RNS config
    """
    from utils.paths import ReticulumPaths
    config_path = ReticulumPaths.get_config_file()

    if not config_path.exists():
        return False

    try:
        content = config_path.read_text()
        return port in content
    except Exception as e:
        logger.debug(f"Could not read RNS config at {config_path}: {e}")
        return False


def detect_devices(probe: bool = False) -> List[RNodeDevice]:
    """
    Detect RNode-compatible devices.

    Args:
        probe: If True, probe each device to identify RNode firmware

    Returns:
        List of detected RNodeDevice objects
    """
    devices = []
    seen_ports = set()

    for port in get_serial_ports():
        if port in seen_ports:
            continue
        seen_ports.add(port)

        # Get USB info
        usb_info = get_usb_info(port)

        # Identify model (now includes manufacturer)
        model = identify_device_model(
            usb_info['vid'],
            usb_info['pid'],
            usb_info['product'],
            usb_info['manufacturer']
        )

        device = RNodeDevice(
            port=port,
            model=model,
            vid=usb_info['vid'],
            pid=usb_info['pid'],
            serial=usb_info['serial'],
        )

        # Check if configured in RNS
        device.is_configured = check_rns_config(port)

        # Check if device is likely an RNode based on identification
        likely_rnode = is_likely_rnode(
            model,
            usb_info['product'],
            usb_info['manufacturer']
        )

        # Probe for RNode firmware if requested
        if probe:
            probe_result = probe_rnode(port)
            if probe_result:
                device.is_rnode = probe_result['is_rnode']
                device.firmware_version = probe_result['firmware_version']
                device.details = probe_result['details']
        elif likely_rnode:
            # Mark as likely RNode even without probing
            device.details = {'likely_rnode': True, 'hint': 'Based on USB identification'}

        devices.append(device)

    return devices


# ============================================================================
# CLI Commands
# ============================================================================

def detect_rnode_devices(probe: bool = False) -> CommandResult:
    """
    Detect RNode devices connected to the system.

    Args:
        probe: If True, probe devices for RNode firmware

    Returns:
        CommandResult with list of detected devices
    """
    try:
        devices = detect_devices(probe=probe)

        if not devices:
            return CommandResult.fail(
                "No serial devices found",
                data={'devices': [], 'count': 0}
            )

        rnode_count = sum(1 for d in devices if d.is_rnode)
        configured_count = sum(1 for d in devices if d.is_configured)

        message = f"Found {len(devices)} serial device(s)"
        if probe and rnode_count > 0:
            message += f", {rnode_count} confirmed RNode(s)"
        if configured_count > 0:
            message += f", {configured_count} configured in RNS"

        return CommandResult.ok(
            message,
            data={
                'devices': [d.to_dict() for d in devices],
                'count': len(devices),
                'rnode_count': rnode_count,
                'configured_count': configured_count,
            }
        )

    except Exception as e:
        return CommandResult.fail(f"Device detection failed: {e}")


def get_device_info(port: str) -> CommandResult:
    """
    Get detailed info for a specific device.

    Args:
        port: Serial port path

    Returns:
        CommandResult with device info
    """
    if not Path(port).exists():
        return CommandResult.fail(f"Port not found: {port}")

    try:
        usb_info = get_usb_info(port)
        model = identify_device_model(usb_info['vid'], usb_info['pid'], usb_info['product'])

        device = RNodeDevice(
            port=port,
            model=model,
            vid=usb_info['vid'],
            pid=usb_info['pid'],
            serial=usb_info['serial'],
            is_configured=check_rns_config(port),
        )

        # Always probe for single device query
        probe_result = probe_rnode(port)
        if probe_result:
            device.is_rnode = probe_result['is_rnode']
            device.firmware_version = probe_result['firmware_version']
            device.details = probe_result['details']

        status = "RNode" if device.is_rnode else "Unknown device"
        if device.is_configured:
            status += " (configured)"

        return CommandResult.ok(
            f"{status} on {port}",
            data=device.to_dict()
        )

    except Exception as e:
        return CommandResult.fail(f"Failed to get device info: {e}")


def get_recommended_config(port: str, region: str = 'US') -> CommandResult:
    """
    Get recommended RNode configuration for a port.

    Args:
        port: Serial port path
        region: Regulatory region (US, EU, AU, etc.)

    Returns:
        CommandResult with recommended configuration
    """
    # One profile source (utils.rnode_profile): the operator's declaration,
    # else the region default — which for US is the fleet's measured profile.
    from utils.rnode_profile import ProfileError, rnode_profile
    region = region.upper()
    try:
        profile = rnode_profile(region)
    except ProfileError as e:
        return CommandResult.fail(str(e), data={'region': region})
    config = {k: v for k, v in profile.items() if k != 'source'}
    config['port'] = port
    config['region'] = region

    # Generate config snippet
    config_snippet = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {config['frequency']}
  bandwidth = {config['bandwidth']}
  txpower = {config['tx_power']}
  spreadingfactor = {config['spreading_factor']}
  codingrate = {config['coding_rate']}
"""

    return CommandResult.ok(
        f"Recommended config for {region} region ({profile['source']})",
        data={
            'config': config,
            'snippet': config_snippet,
            'source': profile['source'],
        }
    )


def is_available() -> bool:
    """Check if RNode functionality is available."""
    return True
