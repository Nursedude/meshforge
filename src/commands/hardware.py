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
                if any(kw in line.lower() for kw in ['cp210', 'ch340', 'ftdi', 'silabs']):
                    usb_devices.append(line)
    except Exception:
        # USB enumeration may fail - non-critical for overall detection
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
        except Exception:
            # Boot config may be unreadable - non-critical
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
    except Exception:
        # meminfo may not be available on some systems
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
