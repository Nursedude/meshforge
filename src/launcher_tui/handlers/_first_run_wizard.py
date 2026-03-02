"""First-Run Wizard helpers — extracted from first_run.py for file size compliance.

Extracted per CLAUDE.md #6 (split files exceeding 1,500 lines).
Functions take the handler instance as first parameter for TUI interaction
via handler.ctx.dialog, handler.ctx.registry, etc.

Contains:
- wizard_step_usb_manual()       USB manual device fallback
- wizard_step_network_config()   Network (TCP) connection setup
- wizard_step_region()           Regulatory region selection
- find_usb_serial_devices()      USB serial device detection
- apply_hardware_config()        Apply hardcoded hardware config
- create_usb_config()            Create minimal USB YAML config
- wizard_step_hardware()         Legacy hardware detection step
- is_raspberry_pi()              Raspberry Pi detection
- offer_enable_spi()             SPI enable offer for RPi
- wizard_step_services()         Service status check
- wizard_step_config()           Legacy quick configuration
- wizard_complete()              Wizard completion & mark done
- settings_run_wizard()          Settings menu entry point
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# Import service check
check_service, apply_config_and_restart, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'apply_config_and_restart'
)

# Import device scanner
from utils.device_scanner import DeviceScanner

logger = logging.getLogger(__name__)


# -- USB manual fallback ---------------------------------------------------

def wizard_step_usb_manual(handler):
    """Fallback: manually select USB device when no templates available."""
    devices = find_usb_serial_devices(handler)

    if not devices:
        handler.ctx.dialog.msgbox(
            "No USB Devices",
            "No USB serial devices detected.\n\n"
            "Connect your Meshtastic device via USB and try again.\n\n"
            "Supported devices:\n"
            "  - T-Beam (CP2102 or CH340)\n"
            "  - Heltec LoRa\n"
            "  - RAK WisBlock\n"
            "  - LilyGo T-Deck"
        )
        return

    # Build device selection menu
    choices = []
    for dev in devices:
        path = dev.get('path', '/dev/ttyUSB0')
        name = dev.get('name', 'Unknown')
        likely = " *" if dev.get('likely_meshtastic', False) else ""
        choices.append((path, f"{name[:25]}{likely}"))

    choices.append(("rescan", "Rescan        Detect devices again"))

    choice = handler.ctx.dialog.menu(
        "Select USB Device",
        "No hardware templates found. Select device manually:\n"
        "(* = likely Meshtastic)",
        choices
    )

    if choice == "rescan":
        wizard_step_usb_manual(handler)
        return

    if choice is None:
        return

    # Use the generic USB template if available, else create minimal config
    available_d = Path('/etc/meshtasticd/available.d')
    generic_template = available_d / 'usb-serial-generic.yaml'
    config_d = Path('/etc/meshtasticd/config.d')

    if generic_template.exists():
        handler._apply_hardware_config_from_file(generic_template, config_d)
    else:
        create_usb_config(handler, choice)


# -- Step 2c: Network config -----------------------------------------------

def wizard_step_network_config(handler):
    """Configure network connection to remote meshtasticd."""
    host = handler.ctx.dialog.inputbox(
        "Step 2: Network Host",
        "Enter the hostname or IP of the meshtasticd server:",
        "localhost"
    )

    if not host:
        return

    port = handler.ctx.dialog.inputbox(
        "Network Port",
        "Enter the port number (default 4403):",
        "4403"
    )

    if not port:
        port = "4403"

    # Save network configuration
    try:
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)

        settings_file = config_dir / "settings.json"
        settings = {}
        if settings_file.exists():
            settings = json.loads(settings_file.read_text())

        settings['meshtasticd_host'] = host
        settings['meshtasticd_port'] = int(port)

        settings_file.write_text(json.dumps(settings, indent=2))

        handler.ctx.dialog.msgbox(
            "Network Configured",
            f"Configured to connect to:\n\n"
            f"  Host: {host}\n"
            f"  Port: {port}\n\n"
            f"Make sure meshtasticd is running on the remote host."
        )
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to save settings: {e}")


# -- Step 3: Region selection ------------------------------------------

def wizard_step_region(handler):
    """Step 3: Select regulatory region."""
    from .first_run import MESHTASTIC_REGIONS

    choices = [(code, desc) for code, desc in MESHTASTIC_REGIONS]

    choice = handler.ctx.dialog.menu(
        "Step 3: Region Selection",
        "Select your regulatory region:\n(This determines allowed frequencies)",
        choices
    )

    if choice and choice != 'UNSET':
        # Save region to settings
        try:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)

            settings_file = config_dir / "settings.json"
            settings = {}
            if settings_file.exists():
                settings = json.loads(settings_file.read_text())

            settings['region'] = choice

            settings_file.write_text(json.dumps(settings, indent=2))
        except (OSError, ValueError) as e:
            logger.debug("Failed to save region setting: %s", e)


# -- USB device detection ----------------------------------------------

def find_usb_serial_devices(handler) -> List[Dict[str, str]]:
    """Find USB serial devices."""
    devices = []

    for pattern in ['ttyUSB*', 'ttyACM*']:
        for path in Path('/dev').glob(pattern):
            device = {'path': str(path), 'name': 'Unknown', 'likely_meshtastic': False}

            try:
                result = subprocess.run(
                    ['udevadm', 'info', '--query=property', str(path)],
                    capture_output=True, text=True, timeout=5
                )
                props = {}
                for line in result.stdout.splitlines():
                    if '=' in line:
                        key, value = line.split('=', 1)
                        props[key] = value

                vendor = props.get('ID_VENDOR', '')
                model = props.get('ID_MODEL', '')
                if vendor or model:
                    device['name'] = f"{vendor} {model}".strip()

                device['likely_meshtastic'] = any(
                    kw in (vendor + model).lower()
                    for kw in ['meshtastic', 't-beam', 'heltec', 'rak', 'lilygo', 'cp210', 'ch340']
                )
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("USB device detection for %s failed: %s", path, e)

            devices.append(device)

    return devices


# -- Apply hardware config (hardcoded) ---------------------------------

def apply_hardware_config(handler, hw_config: dict):
    """Apply a hardware configuration file."""
    config_file = hw_config.get('config_file')
    if not config_file:
        return

    # Source and destination paths
    available_dir = Path('/etc/meshtasticd/available.d')
    config_d = Path('/etc/meshtasticd/config.d')
    source = available_dir / config_file

    if not source.exists():
        handler.ctx.dialog.msgbox(
            "Config Not Found",
            f"Configuration file not found:\n{source}\n\n"
            f"You may need to install or update meshtasticd."
        )
        return

    try:
        config_d.mkdir(parents=True, exist_ok=True)
        dest = config_d / config_file

        # Copy config file
        shutil.copy2(source, dest)

        # Restart meshtasticd
        apply_config_and_restart('meshtasticd')

        handler.ctx.dialog.msgbox(
            "Configuration Applied",
            f"Applied configuration for {hw_config['name']}.\n\n"
            f"Config: {dest}\n\n"
            f"meshtasticd has been restarted.\n"
            f"Check: systemctl status meshtasticd"
        )
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to apply config: {e}")


# -- Create USB config -------------------------------------------------

def create_usb_config(handler, device_path: str):
    """Create USB serial configuration."""
    config_d = Path('/etc/meshtasticd/config.d')

    try:
        config_d.mkdir(parents=True, exist_ok=True)
        config_file = config_d / 'usb-serial.yaml'

        config_content = f"""# USB Serial Configuration
# Generated by MeshForge Setup Wizard
#
# The device handles its own LoRa configuration.
# Radio settings are configured via Meshtastic app/CLI:
#   meshtastic --host localhost --set lora.region US
#   meshtastic --host localhost --set lora.modem_preset LONG_FAST

Serial:
  Device: {device_path}

TCP:
  Port: 4403

Webserver:
  Port: 443

Logging:
  LogLevel: info
"""
        config_file.write_text(config_content)

        # Restart meshtasticd
        apply_config_and_restart('meshtasticd')

        handler.ctx.dialog.msgbox(
            "USB Configured",
            f"USB serial configuration created.\n\n"
            f"Device: {device_path}\n"
            f"Config: {config_file}\n\n"
            f"meshtasticd has been restarted."
        )
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to create config: {e}")


# -- Legacy hardware detection step ------------------------------------

def wizard_step_hardware(handler):
    """Wizard Step 1: Hardware Detection"""
    handler.ctx.dialog.infobox("Step 1/4", "Detecting connected hardware...")

    lines = ["Hardware Detection\n"]
    lines.append("=" * 40)

    # Check for SPI devices (HAT-based radios like MeshAdv-Pi-Hat)
    spi_devices = list(Path('/dev').glob('spidev*'))
    is_pi = is_raspberry_pi(handler)

    if spi_devices:
        lines.append(f"\n✓ SPI Interface Available:")
        for spi in spi_devices[:3]:
            lines.append(f"  • {spi.name}")
        lines.append("  (Supports HAT radios: MeshAdv-Pi-Hat, Waveshare)")
    elif is_pi:
        # No SPI but on Pi - offer to enable it
        lines.append("\n✗ SPI Interface Not Enabled")
        lines.append("  HAT radios require SPI to be enabled.")
        handler.ctx.dialog.msgbox("Step 1: Hardware", "\n".join(lines))

        # Ask if they want to enable SPI
        if offer_enable_spi(handler):
            # Re-check after enable
            spi_devices = list(Path('/dev').glob('spidev*'))
            if spi_devices:
                handler.ctx.dialog.msgbox(
                    "SPI Enabled",
                    "SPI has been enabled!\n\n"
                    "A REBOOT is required for changes to take effect.\n\n"
                    "After reboot, your HAT radio will be detected."
                )
                lines = ["Hardware Detection\n", "=" * 40]
                lines.append("\n✓ SPI Enabled (reboot required)")

    scanner = DeviceScanner()
    results = scanner.scan_all()

    if results['meshtastic_candidates']:
        lines.append(f"\n✓ Found {len(results['meshtastic_candidates'])} Meshtastic-compatible device(s):\n")
        for dev in results['meshtastic_candidates']:
            lines.append(f"  • {dev.description}")
    elif not spi_devices:
        lines.append("\n✗ No Meshtastic devices detected")
        lines.append("\nTo use MeshForge with a radio:")
        lines.append("  1. Connect a Meshtastic device via USB")
        lines.append("  2. Or configure meshtasticd for HAT/SPI")

    if results['serial_ports']:
        compat_ports = [p for p in results['serial_ports'] if p.meshtastic_compatible]
        if compat_ports:
            lines.append(f"\n✓ Serial Ports Available:")
            for port in compat_ports[:3]:  # Show first 3
                lines.append(f"  • {port.device}")

    if results['recommended_port']:
        lines.append(f"\n→ Recommended port: {results['recommended_port']}")

    # Summary for new users
    if spi_devices or results.get('meshtastic_candidates'):
        lines.append("\n" + "-" * 40)
        lines.append("Hardware detected! Continue to configure.")
    else:
        lines.append("\n" + "-" * 40)
        lines.append("No radio found - you can still explore")
        lines.append("the interface and configure later.")

    handler.ctx.dialog.msgbox("Step 1: Hardware", "\n".join(lines))


# -- Raspberry Pi detection --------------------------------------------

def is_raspberry_pi(handler) -> bool:
    """Check if running on Raspberry Pi."""
    try:
        # Check /proc/cpuinfo for Raspberry Pi
        cpuinfo = Path('/proc/cpuinfo')
        if cpuinfo.exists():
            content = cpuinfo.read_text()
            if 'Raspberry Pi' in content or 'BCM' in content:
                return True
        # Check device tree model
        model = Path('/proc/device-tree/model')
        if model.exists():
            if 'Raspberry Pi' in model.read_text():
                return True
    except OSError as e:
        logger.debug("RPi detection failed: %s", e)
    return False


# -- SPI enable offer --------------------------------------------------

def offer_enable_spi(handler) -> bool:
    """Offer to enable SPI on Raspberry Pi. Returns True if enabled."""
    result = handler.ctx.dialog.yesno(
        "Enable SPI?",
        "No SPI interface detected.\n\n"
        "HAT-based radios (MeshAdv-Pi-Hat, Waveshare, etc.)\n"
        "require SPI to be enabled.\n\n"
        "Would you like to enable SPI now?\n\n"
        "(Requires reboot to take effect)"
    )

    if not result:
        return False

    handler.ctx.dialog.infobox("Enabling SPI", "Configuring SPI interface...")

    try:
        # Find the boot config file
        boot_config = None
        for path in ['/boot/firmware/config.txt', '/boot/config.txt']:
            if Path(path).exists():
                boot_config = path
                break

        if not boot_config:
            handler.ctx.dialog.msgbox("Error", "Could not find boot config file.")
            return False

        # Enable SPI using raspi-config if available
        raspi_config = shutil.which('raspi-config')
        if raspi_config:
            subprocess.run(
                ['raspi-config', 'nonint', 'set_config_var', 'dtparam=spi', 'on', boot_config],
                timeout=30,
                check=False
            )

        # Add dtoverlay=spi0-0cs if not present (for HAT compatibility)
        config_content = Path(boot_config).read_text()
        if 'dtoverlay=spi0-0cs' not in config_content:
            # Find dtparam=spi=on line and add overlay after it
            lines = config_content.split('\n')
            new_lines = []
            added = False
            for line in lines:
                new_lines.append(line)
                if 'dtparam=spi=on' in line and not added:
                    new_lines.append('dtoverlay=spi0-0cs')
                    added = True

            # If dtparam=spi=on wasn't found, add both at the end
            if not added:
                new_lines.append('dtparam=spi=on')
                new_lines.append('dtoverlay=spi0-0cs')

            Path(boot_config).write_text('\n'.join(new_lines))

        return True

    except subprocess.TimeoutExpired:
        handler.ctx.dialog.msgbox("Error", "Timeout while configuring SPI.")
        return False
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to enable SPI: {e}")
        return False


# -- Step 4: Service status --------------------------------------------

def wizard_step_services(handler):
    """Wizard Step 2: Service Status"""
    handler.ctx.dialog.infobox("Step 2/4", "Checking mesh services...")

    services = [
        ('meshtasticd', 'Meshtastic Daemon', 'Required for radio communication'),
        ('rnsd', 'Reticulum Network Stack', 'Optional - enables RNS mesh'),
    ]

    lines = ["Service Status\n"]
    lines.append("=" * 40)

    all_running = True
    for svc_id, svc_name, description in services:
        status = check_service(svc_id)
        if status.available:
            lines.append(f"\n✓ {svc_name}")
            lines.append(f"  Status: running")
        else:
            all_running = False
            lines.append(f"\n✗ {svc_name}")
            lines.append(f"  Status: {status.message}")
            lines.append(f"  ({description})")
            if status.fix_hint:
                lines.append(f"  Fix: {status.fix_hint}")

    if all_running:
        lines.append("\n" + "-" * 40)
        lines.append("All services are running!")
    else:
        lines.append("\n" + "-" * 40)
        lines.append("Some services need to be started.")
        lines.append("Use Service Manager from the main menu.")

    handler.ctx.dialog.msgbox("Step 2: Services", "\n".join(lines))


# -- Legacy config step ------------------------------------------------

def wizard_step_config(handler):
    """Wizard Step 3: Quick Configuration"""
    # Check if basic config exists
    config_dir = get_real_user_home() / ".config" / "meshforge"
    settings_file = config_dir / "settings.json"

    if settings_file.exists():
        handler.ctx.dialog.msgbox(
            "Step 3: Configuration",
            "Configuration file found.\n\n"
            "Your settings are preserved from a previous install.\n\n"
            "You can modify settings from:\n"
            "  Main Menu → Settings"
        )
        return

    # Offer basic setup
    result = handler.ctx.dialog.yesno(
        "Step 3: Configuration",
        "Would you like to configure basic settings?\n\n"
        "This includes:\n"
        "• Callsign (for ham operators)\n"
        "• Default region\n"
        "• UI preferences"
    )

    if result:
        # Get callsign
        callsign = handler.ctx.dialog.inputbox(
            "Callsign",
            "Enter your callsign (optional):",
            ""
        )

        if callsign:
            # Save to settings
            try:
                from utils.common import SettingsManager
                settings = SettingsManager("meshforge")
                settings.set("callsign", callsign.upper())
                settings.save()
                handler.ctx.dialog.msgbox("Saved", f"Callsign set to: {callsign.upper()}")
            except Exception as e:
                handler.ctx.dialog.msgbox("Note", f"Could not save settings: {e}")


# -- Wizard completion -------------------------------------------------

def wizard_complete(handler):
    """Wizard completion"""
    handler._mark_setup_complete()

    handler.ctx.dialog.msgbox(
        "Setup Complete!",
        "MeshForge is ready to use!\n\n"
        "Next Steps:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. Service Manager → Start meshtasticd\n"
        "2. Meshtasticd Config → Configure your radio\n"
        "3. Diagnostics → Verify everything works\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\nNeed Help?\n"
        "  • Run diagnostics for system health\n"
        "  • Check GitHub issues for known fixes\n"
        "  • HAM community: 73s and good luck!\n\n"
        "Press Enter to continue to main menu."
    )


# -- Settings menu entry point -----------------------------------------

def settings_run_wizard(handler):
    """Run wizard from settings menu."""
    result = handler.ctx.dialog.yesno(
        "Run Setup Wizard",
        "Run the first-run setup wizard again?\n\n"
        "This will walk through hardware detection,\n"
        "service checks, and basic configuration."
    )

    if result:
        handler._run_first_run_wizard()
