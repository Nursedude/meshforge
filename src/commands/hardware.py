"""
Hardware Commands

Provides unified interface for hardware detection and configuration.
Used by both GTK and CLI interfaces.
"""

import subprocess
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from .base import CommandResult

logger = logging.getLogger(__name__)


def check_spi() -> CommandResult:
    """
    Check if SPI is enabled.

    Returns:
        CommandResult with SPI status
    """
    spi_devices = list(Path('/dev').glob('spidev*'))
    enabled = len(spi_devices) > 0

    return CommandResult(
        success=enabled,
        message="SPI enabled" if enabled else "SPI not enabled",
        data={
            'enabled': enabled,
            'devices': [str(d) for d in spi_devices],
            'fix_hint': 'Enable SPI in raspi-config or /boot/config.txt' if not enabled else ''
        }
    )


def check_i2c() -> CommandResult:
    """
    Check if I2C is enabled.

    Returns:
        CommandResult with I2C status
    """
    i2c_devices = list(Path('/dev').glob('i2c-*'))
    enabled = len(i2c_devices) > 0

    return CommandResult(
        success=enabled,
        message="I2C enabled" if enabled else "I2C not enabled",
        data={
            'enabled': enabled,
            'devices': [str(d) for d in i2c_devices],
            'fix_hint': 'Enable I2C in raspi-config or /boot/config.txt' if not enabled else ''
        }
    )


def check_gpio() -> CommandResult:
    """Check GPIO availability."""
    gpio_path = Path('/sys/class/gpio')
    gpiomem = Path('/dev/gpiomem')

    available = gpio_path.exists() or gpiomem.exists()

    return CommandResult(
        success=available,
        message="GPIO available" if available else "GPIO not available",
        data={
            'available': available,
            'gpio_path': gpio_path.exists(),
            'gpiomem': gpiomem.exists()
        }
    )


def scan_serial_ports() -> CommandResult:
    """
    Scan for serial ports that might have Meshtastic devices.

    Returns:
        CommandResult with list of serial ports
    """
    ports = []

    # Check /dev for serial devices
    serial_patterns = ['ttyUSB*', 'ttyACM*', 'ttyAMA*', 'serial*']
    for pattern in serial_patterns:
        for device in Path('/dev').glob(pattern):
            ports.append({
                'device': str(device),
                'type': pattern.replace('*', ''),
                'exists': device.exists()
            })

    # Try to get more info with lsusb
    usb_devices = []
    try:
        result = subprocess.run(
            ['lsusb'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if any(kw in line.lower() for kw in ['cp210', 'ch340', 'ch341', 'ftdi', 'silabs']):
                    usb_devices.append(line)
    except Exception:  # USB enumeration may fail - non-critical
        pass

    return CommandResult.ok(
        f"Found {len(ports)} serial ports",
        data={
            'ports': ports,
            'usb_devices': usb_devices,
            'count': len(ports)
        }
    )


def detect_lora_hardware() -> CommandResult:
    """
    Detect LoRa hardware (SX127x, SX126x, etc.).

    Returns:
        CommandResult with LoRa hardware info
    """
    detected = []

    # Check SPI devices
    spi_result = check_spi()
    if not spi_result.success:
        return CommandResult(
            success=False,
            message="SPI not enabled - LoRa detection requires SPI",
            data={'detected': [], 'fix_hint': 'Enable SPI first'}
        )

    # Check for common LoRa module configurations
    # These would be in meshtasticd config
    config_dir = Path('/etc/meshtasticd')
    lora_configs = []

    if config_dir.exists():
        for config_file in config_dir.glob('**/*.yaml'):
            try:
                content = config_file.read_text()
                if 'lora' in content.lower() or 'sx12' in content.lower():
                    lora_configs.append(str(config_file))
            except Exception:
                # Config file read may fail (permissions, encoding) - skip file
                pass

    # Check device tree overlays for LoRa
    overlays = []
    config_txt = Path('/boot/config.txt')
    if config_txt.exists():
        try:
            content = config_txt.read_text()
            for line in content.split('\n'):
                if 'dtoverlay=' in line and 'spi' in line.lower():
                    overlays.append(line.strip())
        except Exception:  # Boot config may be unreadable - non-critical
            pass

    has_lora = len(lora_configs) > 0 or len(overlays) > 0

    return CommandResult(
        success=has_lora,
        message="LoRa configuration detected" if has_lora else "No LoRa configuration found",
        data={
            'lora_configs': lora_configs,
            'overlays': overlays,
            'spi_enabled': True
        }
    )


def detect_devices() -> CommandResult:
    """
    Detect all relevant hardware devices.

    Returns:
        CommandResult with comprehensive hardware info
    """
    # Gather all hardware info
    spi = check_spi()
    i2c = check_i2c()
    gpio = check_gpio()
    serial = scan_serial_ports()
    lora = detect_lora_hardware()

    # Build summary
    summary = []
    if spi.success:
        summary.append("SPI")
    if i2c.success:
        summary.append("I2C")
    if gpio.success:
        summary.append("GPIO")
    if serial.data.get('count', 0) > 0:
        summary.append(f"{serial.data['count']} serial")
    if lora.success:
        summary.append("LoRa")

    status_msg = ", ".join(summary) if summary else "No hardware detected"

    return CommandResult.ok(
        status_msg,
        data={
            'spi': spi.data,
            'i2c': i2c.data,
            'gpio': gpio.data,
            'serial': serial.data,
            'lora': lora.data,
            'summary': summary
        }
    )


def get_platform_info() -> CommandResult:
    """
    Get platform/system information.

    Returns:
        CommandResult with platform info
    """
    import platform

    info = {
        'system': platform.system(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': platform.python_version(),
    }

    # Check if Raspberry Pi
    is_raspberry_pi = False
    model = "Unknown"
    try:
        model_path = Path('/proc/device-tree/model')
        if model_path.exists():
            model = model_path.read_text().strip('\x00')
            is_raspberry_pi = 'raspberry' in model.lower()
    except Exception:
        # Device tree may not exist on non-Pi systems - use defaults
        pass

    info['model'] = model
    info['is_raspberry_pi'] = is_raspberry_pi

    # Get memory info
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    mem_kb = int(line.split()[1])
                    info['memory_mb'] = mem_kb // 1024
                    break
    except Exception:  # meminfo may not be available on some systems
        info['memory_mb'] = 0

    return CommandResult.ok(
        f"{info['system']} {info['machine']} - {model}",
        data=info
    )


def scan_i2c_bus(bus: int = 1) -> CommandResult:
    """
    Scan I2C bus for devices.

    Args:
        bus: I2C bus number (default 1)
    """
    try:
        result = subprocess.run(
            ['i2cdetect', '-y', str(bus)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            # Parse output to find addresses
            addresses = []
            for line in result.stdout.split('\n')[1:]:
                parts = line.split()[1:]  # Skip row header
                for i, val in enumerate(parts):
                    if val != '--' and val != 'UU':
                        try:
                            addr = int(val, 16)
                            addresses.append(f"0x{addr:02x}")
                        except ValueError:
                            pass

            return CommandResult.ok(
                f"Found {len(addresses)} I2C devices",
                data={'addresses': addresses, 'raw': result.stdout},
                raw=result.stdout
            )
        else:
            return CommandResult.fail(
                f"I2C scan failed: {result.stderr}",
                error=result.stderr
            )
    except FileNotFoundError:
        return CommandResult.not_available(
            "i2cdetect not installed",
            fix_hint="apt install i2c-tools"
        )
    except Exception as e:
        return CommandResult.fail(f"I2C scan error: {e}")


def check_usb_devices() -> CommandResult:
    """List USB devices."""
    try:
        result = subprocess.run(
            ['lsusb'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            devices = [line.strip() for line in result.stdout.split('\n') if line.strip()]
            return CommandResult.ok(
                f"Found {len(devices)} USB devices",
                data={'devices': devices},
                raw=result.stdout
            )
        else:
            return CommandResult.fail("lsusb failed")
    except FileNotFoundError:
        return CommandResult.not_available(
            "lsusb not installed",
            fix_hint="apt install usbutils"
        )
    except Exception as e:
        return CommandResult.fail(f"USB scan error: {e}")


def enable_spi() -> CommandResult:
    """
    Enable SPI (requires sudo, modifies /boot/config.txt).

    Returns instructions rather than making changes.
    """
    return CommandResult.warn(
        "SPI enablement requires manual configuration",
        data={
            'instructions': [
                "Run: sudo raspi-config",
                "Navigate to: Interface Options > SPI",
                "Enable SPI",
                "Reboot the system",
                "",
                "Or manually add to /boot/config.txt:",
                "dtparam=spi=on"
            ]
        }
    )


def enable_i2c() -> CommandResult:
    """
    Enable I2C (requires sudo, modifies /boot/config.txt).

    Returns instructions rather than making changes.
    """
    return CommandResult.warn(
        "I2C enablement requires manual configuration",
        data={
            'instructions': [
                "Run: sudo raspi-config",
                "Navigate to: Interface Options > I2C",
                "Enable I2C",
                "Reboot the system",
                "",
                "Or manually add to /boot/config.txt:",
                "dtparam=i2c_arm=on"
            ]
        }
    )


# --- SPI/I2C Bus Classification (Step 2) ---

def _parse_bus_number(device_name: str, prefix: str) -> Optional[int]:
    """Extract bus number from a device name like 'spidev10.0' or 'i2c-14'."""
    import re
    if prefix == 'spidev':
        match = re.match(r'spidev(\d+)\.\d+', device_name)
    else:
        match = re.match(r'i2c-(\d+)', device_name)
    if match:
        return int(match.group(1))
    return None


def _find_usb_parent(sysfs_path: Path) -> Optional[Dict[str, str]]:
    """Follow sysfs symlinks to find a USB parent device and its VID:PID.

    Walks up the device tree from sysfs_path looking for idVendor/idProduct files
    that identify USB devices.
    """
    try:
        # Resolve to real path and walk up
        real_path = sysfs_path.resolve()
        current = real_path
        for _ in range(15):  # Limit traversal depth
            vid_file = current / 'idVendor'
            pid_file = current / 'idProduct'
            if vid_file.exists() and pid_file.exists():
                vid = vid_file.read_text().strip()
                pid = pid_file.read_text().strip()
                return {'vid': vid, 'pid': pid, 'vid_pid': f"{vid}:{pid}", 'path': str(current)}
            parent = current.parent
            if parent == current:
                break
            current = parent
    except (OSError, PermissionError) as e:
        logger.debug("USB parent lookup failed for %s: %s", sysfs_path, e)
    return None


def classify_spi_bus(device: Path) -> Dict[str, Any]:
    """Classify an SPI bus device — native RPi vs USB-bridged.

    Args:
        device: Path to /dev/spidevN.M

    Returns:
        Dict with keys: bus_number, is_native, parent_usb, parent_name
    """
    bus_num = _parse_bus_number(device.name, 'spidev')
    result: Dict[str, Any] = {
        'device': str(device),
        'name': device.name,
        'bus_number': bus_num,
        'is_native': bus_num is not None and bus_num <= 1,
        'parent_usb': None,
        'parent_name': None,
    }

    if bus_num is not None:
        # Try sysfs lookup for USB parent
        sysfs_master = Path(f'/sys/class/spi_master/spi{bus_num}')
        if sysfs_master.exists():
            device_link = sysfs_master / 'device'
            usb_info = _find_usb_parent(device_link)
            if usb_info:
                result['parent_usb'] = usb_info
                result['is_native'] = False  # Has USB parent = not native
                # Try to match against known devices
                try:
                    from config.hardware import HardwareDetector
                    name = HardwareDetector.get_device_name_for_usb_id(usb_info['vid_pid'])
                    if name:
                        result['parent_name'] = name
                except ImportError:
                    pass

    return result


def classify_i2c_bus(device: Path) -> Dict[str, Any]:
    """Classify an I2C bus device — native RPi vs USB-bridged.

    Args:
        device: Path to /dev/i2c-N

    Returns:
        Dict with keys: bus_number, is_native, parent_usb, parent_name
    """
    bus_num = _parse_bus_number(device.name, 'i2c')
    result: Dict[str, Any] = {
        'device': str(device),
        'name': device.name,
        'bus_number': bus_num,
        'is_native': bus_num is not None and bus_num <= 1,
        'parent_usb': None,
        'parent_name': None,
    }

    if bus_num is not None:
        # Try sysfs lookup for USB parent
        sysfs_adapter = Path(f'/sys/class/i2c-adapter/i2c-{bus_num}')
        if sysfs_adapter.exists():
            device_link = sysfs_adapter / 'device'
            usb_info = _find_usb_parent(device_link)
            if usb_info:
                result['parent_usb'] = usb_info
                result['is_native'] = False
                try:
                    from config.hardware import HardwareDetector
                    name = HardwareDetector.get_device_name_for_usb_id(usb_info['vid_pid'])
                    if name:
                        result['parent_name'] = name
                except ImportError:
                    pass

    return result


def match_config_to_hardware() -> Dict[str, Any]:
    """Cross-reference active meshtasticd config.d/ with detected USB hardware.

    Returns:
        Dict with keys: configs, usb_devices, matches, warnings
    """
    result: Dict[str, Any] = {
        'configs': [],
        'usb_match': None,
        'config_match': False,
        'warnings': [],
    }

    config_d = Path('/etc/meshtasticd/config.d')
    if not config_d.exists():
        result['warnings'].append('config.d/ directory not found')
        return result

    configs = list(config_d.glob('*.yaml'))
    result['configs'] = [c.name for c in configs]

    # Check for ch341/spidev references in active configs
    has_ch341_config = False
    for cfg in configs:
        try:
            content = cfg.read_text().lower()
            if 'spidev: ch341' in content or 'ch341' in content:
                has_ch341_config = True
                break
        except (OSError, PermissionError):
            pass

    # Check for CH341 USB device present
    ch341_usb_present = False
    try:
        lsusb_result = subprocess.run(
            ['lsusb'], capture_output=True, text=True, timeout=5
        )
        if lsusb_result.returncode == 0:
            output_lower = lsusb_result.stdout.lower()
            # CH341 SPI bridge PIDs
            if '1a86:5512' in output_lower:
                ch341_usb_present = True
                result['usb_match'] = '1a86:5512'
            elif '1a86:7523' in output_lower:
                ch341_usb_present = True
                result['usb_match'] = '1a86:7523'
    except Exception:
        pass

    # Cross-reference
    if has_ch341_config and ch341_usb_present:
        result['config_match'] = True
    elif has_ch341_config and not ch341_usb_present:
        result['warnings'].append('Config references CH341 but no CH341 USB device detected')
    elif not has_ch341_config and ch341_usb_present:
        result['warnings'].append('CH341 USB device detected but no matching config in config.d/')

    # Check main config.yaml for Webserver section (Issue #22)
    main_config = Path('/etc/meshtasticd/config.yaml')
    if main_config.exists():
        try:
            content = main_config.read_text()
            if 'Webserver:' not in content:
                result['warnings'].append(
                    'config.yaml missing Webserver: section — web UI (:9443) may not work'
                )
        except (OSError, PermissionError):
            pass
    else:
        result['warnings'].append('config.yaml not found')

    return result


# --- Radio Health Diagnostics (Step 3) ---

def get_radio_health() -> CommandResult:
    """Collect radio health metrics from all available data sources.

    Uses HTTP API (no TCP lock), CLI, and service checks.
    Returns comprehensive diagnostic data with smart warnings.
    """
    health: Dict[str, Any] = {
        'http_nodes': None,
        'cli_node_count': None,
        'report': None,
        'service_status': None,
        'port_4403': None,
        'port_9443': None,
        'warnings': [],
        'snr_stats': None,
        'rssi_anomalies': [],
    }

    # 1. Check meshtasticd service status
    try:
        from utils.service_check import check_service, check_port
        status = check_service('meshtasticd')
        health['service_status'] = {
            'available': status.available,
            'state': status.state.value,
            'message': status.message,
        }
        health['port_4403'] = check_port(4403)
        health['port_9443'] = check_port(9443)
    except ImportError:
        logger.debug("service_check not available")

    # 2. Get nodes via HTTP API (no TCP lock contention)
    try:
        from utils.meshtastic_http import get_http_client
        client = get_http_client()
        if client.is_available:
            nodes = client.get_nodes()
            health['http_nodes'] = len(nodes)

            # Analyze SNR/RSSI
            snr_values = [n.snr for n in nodes if n.snr != 0.0]
            rssi_zero_count = sum(1 for n in nodes if n.snr != 0.0 and not hasattr(n, 'rssi'))

            if snr_values:
                health['snr_stats'] = {
                    'min': min(snr_values),
                    'max': max(snr_values),
                    'count': len(snr_values),
                }

            # Check for RSSI:0 anomaly (common on CH341 SPI bridges)
            for node in nodes:
                if hasattr(node, 'rssi') and node.rssi == 0 and node.snr != 0.0:
                    health['rssi_anomalies'].append(node.node_id)

            # Get device report (airtime, battery, etc.)
            report = client.get_report()
            if report:
                health['report'] = {
                    'channel_utilization': report.channel_utilization,
                    'tx_utilization': report.tx_utilization,
                    'frequency': report.frequency,
                    'lora_channel': report.lora_channel,
                    'battery_percent': report.battery_percent,
                    'has_battery': report.has_battery,
                    'has_usb': report.has_usb,
                    'seconds_since_boot': report.seconds_since_boot,
                }
        else:
            health['http_nodes'] = 0
    except ImportError:
        logger.debug("meshtastic_http not available")
    except Exception as e:
        logger.debug("HTTP API error: %s", e)

    # 3. Get node count via CLI (for cross-check)
    try:
        from core.meshtastic_cli import get_cli
        cli = get_cli()
        result = cli.get_nodes()
        if result.success and result.output:
            # Count lines that look like node entries
            lines = [l for l in result.output.split('\n') if l.strip() and '!' in l]
            health['cli_node_count'] = len(lines)
    except ImportError:
        logger.debug("meshtastic_cli not available")
    except Exception as e:
        logger.debug("CLI error: %s", e)

    # 4. Generate smart warnings
    warnings = health['warnings']

    # Node count mismatch (HTTP vs CLI)
    http_count = health.get('http_nodes')
    cli_count = health.get('cli_node_count')
    if http_count is not None and cli_count is not None:
        if http_count == 0 and cli_count > 0:
            warnings.append(
                f"Web module mismatch: HTTP API shows 0 nodes but CLI sees {cli_count} "
                "— web UI may be disconnected from radio module"
            )
        elif http_count > 0 and cli_count > 0 and abs(http_count - cli_count) > cli_count * 0.5:
            warnings.append(
                f"Node count divergence: HTTP={http_count}, CLI={cli_count}"
            )

    # RSSI:0 anomaly
    if health['rssi_anomalies']:
        count = len(health['rssi_anomalies'])
        warnings.append(
            f"RSSI=0 reported for {count} node(s) — possible SPI register read "
            "issue on CH341 bridge"
        )

    # Delivery ACK detection via TX utilization with no HTTP nodes
    report = health.get('report')
    if report and report.get('tx_utilization', 0) > 0 and http_count == 0:
        warnings.append(
            "TX active but web API shows 0 nodes — delivery ACKs may not be received"
        )

    # Power asymmetry hint (E22 = 30dBm/1W)
    # Check if config references E22 or high-power module
    config_d = Path('/etc/meshtasticd/config.d')
    if config_d.exists():
        for cfg in config_d.glob('*.yaml'):
            try:
                content = cfg.read_text().lower()
                if 'e22' in content or '900m30s' in content:
                    warnings.append(
                        "E22 module detected (30dBm/1W TX) — remote nodes at lower power "
                        "may not reach back, causing 'waiting for delivery'"
                    )
                    break
            except (OSError, PermissionError):
                pass

    has_data = any(v is not None for k, v in health.items()
                   if k not in ('warnings', 'rssi_anomalies'))

    return CommandResult(
        success=has_data,
        message=f"Radio health: {len(warnings)} warning(s)" if warnings else "Radio health: OK",
        data=health,
    )
