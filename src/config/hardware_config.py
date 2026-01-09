"""
Hardware Configuration - SPI, Serial, GPIO, and device config management

Provides interactive tools for:
- Raspberry Pi boot configuration (config.txt)
- SPI/Serial/I2C interface management
- Hardware detection and selection
- Config file management for meshtasticd
- Safe reboot with application check
"""

import subprocess
import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Paths
BOOT_CONFIG = Path('/boot/firmware/config.txt')
BOOT_CONFIG_LEGACY = Path('/boot/config.txt')
MESHTASTICD_CONFIG_D = Path('/etc/meshtasticd/config.d')
MESHTASTICD_AVAILABLE_D = Path('/etc/meshtasticd/available.d')


@dataclass
class HardwareDevice:
    """Hardware device configuration"""
    name: str
    description: str
    yaml_file: str
    requires_spi: bool = True
    requires_serial: bool = False
    requires_i2c: bool = False
    spi_overlay: Optional[str] = None
    notes: str = ""


# Known Meshtastic hardware devices
HARDWARE_DEVICES = {
    # LoRa HATs - 900MHz (US/Americas)
    'lora-900m30s': HardwareDevice(
        name='LoRa 900MHz 30dBm SX1262',
        description='High power 900MHz LoRa module (US region)',
        yaml_file='lora-MeshAdv-900M30S.yaml',
        requires_spi=True,
        spi_overlay='spi0-0cs',
        notes='Requires SPI enabled, uses CE0'
    ),
    'lora-900m22s': HardwareDevice(
        name='LoRa 900MHz 22dBm SX1262',
        description='Standard power 900MHz LoRa module',
        yaml_file='lora-MeshAdv-900M22S.yaml',
        requires_spi=True,
        spi_overlay='spi0-0cs'
    ),
    # LoRa HATs - 868MHz (EU)
    'lora-868m22s': HardwareDevice(
        name='LoRa 868MHz 22dBm SX1262',
        description='EU region 868MHz LoRa module',
        yaml_file='lora-MeshAdv-868M22S.yaml',
        requires_spi=True,
        spi_overlay='spi0-0cs'
    ),
    # Waveshare displays
    'display-waveshare-1.44': HardwareDevice(
        name='Waveshare 1.44" LCD',
        description='128x128 SPI LCD display',
        yaml_file='display-waveshare-1.44.yaml',
        requires_spi=True,
        notes='ST7735 controller'
    ),
    'display-waveshare-2.8': HardwareDevice(
        name='Waveshare 2.8" LCD',
        description='320x240 SPI LCD display',
        yaml_file='display-waveshare-2.8.yaml',
        requires_spi=True,
        notes='ILI9341 controller'
    ),
    # I2C devices
    'i2c-oled-128x64': HardwareDevice(
        name='I2C OLED 128x64',
        description='SSD1306 I2C OLED display',
        yaml_file='display-i2c-oled.yaml',
        requires_spi=False,
        requires_i2c=True
    ),
    # GPS modules
    'gps-serial': HardwareDevice(
        name='Serial GPS Module',
        description='UART GPS (NEO-6M, NEO-7M, etc.)',
        yaml_file='gps-serial.yaml',
        requires_spi=False,
        requires_serial=True,
        notes='Uses /dev/ttyAMA0 or /dev/serial0'
    ),
}


class HardwareConfigurator:
    """Hardware configuration manager"""

    def __init__(self):
        self._return_to_main = False
        self._boot_config_path = self._get_boot_config_path()

    def _get_boot_config_path(self) -> Path:
        """Get the correct boot config path"""
        if BOOT_CONFIG.exists():
            return BOOT_CONFIG
        elif BOOT_CONFIG_LEGACY.exists():
            return BOOT_CONFIG_LEGACY
        return BOOT_CONFIG  # Default to new path

    def interactive_menu(self):
        """Main hardware configuration menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════ Hardware Configuration ═══════════[/bold cyan]\n")

            console.print("[dim cyan]── Interface Configuration ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Enable/Disable SPI")
            console.print("  [bold]2[/bold]. Enable/Disable I2C")
            console.print("  [bold]3[/bold]. Configure Serial Port")
            console.print("  [bold]4[/bold]. Add SPI Overlay (spi0-0cs)")

            console.print("\n[dim cyan]── Hardware Selection ──[/dim cyan]")
            console.print("  [bold]5[/bold]. Detect Connected Hardware")
            console.print("  [bold]6[/bold]. Select & Configure Device")
            console.print("  [bold]7[/bold]. View Active Configurations")

            console.print("\n[dim cyan]── Config Files ──[/dim cyan]")
            console.print("  [bold]8[/bold]. Copy Config to config.d")
            console.print("  [bold]9[/bold]. Edit Config File")

            console.print("\n[dim cyan]── System ──[/dim cyan]")
            console.print("  [bold]b[/bold]. View boot/firmware/config.txt")
            console.print("  [bold]r[/bold]. [yellow]Safe Reboot[/yellow] (checks running apps)")

            console.print("\n  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")
            console.print()

            choice = Prompt.ask("Select option", default="0")

            if choice == "0":
                return
            elif choice.lower() == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self._configure_spi()
            elif choice == "2":
                self._configure_i2c()
            elif choice == "3":
                self._configure_serial()
            elif choice == "4":
                self._add_spi_overlay()
            elif choice == "5":
                self._detect_hardware()
            elif choice == "6":
                self._select_device()
            elif choice == "7":
                self._view_active_configs()
            elif choice == "8":
                self._copy_config()
            elif choice == "9":
                self._edit_config()
            elif choice.lower() == "b":
                self._view_boot_config()
            elif choice.lower() == "r":
                self._safe_reboot()

    def _configure_spi(self):
        """Configure SPI interface"""
        console.print("\n[bold cyan]── SPI Configuration ──[/bold cyan]\n")

        # Check current status
        spi_enabled = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        console.print(f"Current SPI status: {'[green]Enabled[/green]' if spi_enabled else '[red]Disabled[/red]'}")

        action = Prompt.ask("Action", choices=["enable", "disable", "cancel"], default="enable")

        if action == "cancel":
            return

        try:
            if action == "enable":
                console.print("\n[cyan]Enabling SPI...[/cyan]")
                subprocess.run([
                    'sudo', 'raspi-config', 'nonint', 'set_config_var',
                    'dtparam=spi', 'on', str(self._boot_config_path)
                ], check=True, timeout=30)
                console.print("[green]SPI enabled in config.txt[/green]")
            else:
                console.print("\n[cyan]Disabling SPI...[/cyan]")
                subprocess.run([
                    'sudo', 'raspi-config', 'nonint', 'set_config_var',
                    'dtparam=spi', 'off', str(self._boot_config_path)
                ], check=True, timeout=30)
                console.print("[yellow]SPI disabled in config.txt[/yellow]")

            console.print("\n[yellow]A reboot is required for changes to take effect.[/yellow]")
            if Confirm.ask("Reboot now?", default=False):
                self._safe_reboot()

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _configure_i2c(self):
        """Configure I2C interface"""
        console.print("\n[bold cyan]── I2C Configuration ──[/bold cyan]\n")

        # Check current status
        i2c_enabled = Path('/dev/i2c-1').exists()
        console.print(f"Current I2C status: {'[green]Enabled[/green]' if i2c_enabled else '[red]Disabled[/red]'}")

        action = Prompt.ask("Action", choices=["enable", "disable", "cancel"], default="enable")

        if action == "cancel":
            return

        try:
            if action == "enable":
                console.print("\n[cyan]Enabling I2C...[/cyan]")
                subprocess.run([
                    'sudo', 'raspi-config', 'nonint', 'do_i2c', '0'
                ], check=True, timeout=30)
                console.print("[green]I2C enabled[/green]")
            else:
                console.print("\n[cyan]Disabling I2C...[/cyan]")
                subprocess.run([
                    'sudo', 'raspi-config', 'nonint', 'do_i2c', '1'
                ], check=True, timeout=30)
                console.print("[yellow]I2C disabled[/yellow]")

            console.print("\n[yellow]A reboot is required for changes to take effect.[/yellow]")
            if Confirm.ask("Reboot now?", default=False):
                self._safe_reboot()

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _configure_serial(self):
        """Configure serial port"""
        console.print("\n[bold cyan]── Serial Port Configuration ──[/bold cyan]\n")

        console.print("[dim]For GPS modules and serial devices:[/dim]")
        console.print("  - Enable Serial Port Hardware (UART)")
        console.print("  - Disable Serial Console (frees the port)")
        console.print()

        if not Confirm.ask("Configure serial for hardware use?", default=True):
            return

        try:
            console.print("\n[cyan]Enabling Serial Port hardware...[/cyan]")
            subprocess.run([
                'sudo', 'raspi-config', 'nonint', 'do_serial_hw', '0'
            ], check=True, timeout=30)
            console.print("[green]Serial hardware enabled (enable_uart=1)[/green]")

            console.print("\n[cyan]Disabling Serial Console...[/cyan]")
            subprocess.run([
                'sudo', 'raspi-config', 'nonint', 'do_serial_cons', '1'
            ], check=True, timeout=30)
            console.print("[green]Serial console disabled (port freed for hardware)[/green]")

            console.print("\n[yellow]A reboot is required for changes to take effect.[/yellow]")
            if Confirm.ask("Reboot now?", default=False):
                self._safe_reboot()

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _add_spi_overlay(self):
        """Add SPI overlay for LoRa devices"""
        console.print("\n[bold cyan]── Add SPI Overlay ──[/bold cyan]\n")

        console.print("[dim]Many LoRa HATs require the spi0-0cs overlay[/dim]")
        console.print("[dim]This configures SPI with no automatic chip select[/dim]\n")

        # Check if already present
        try:
            result = subprocess.run(
                ['grep', '-q', r'^\s*dtoverlay=spi0-0cs', str(self._boot_config_path)],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                console.print("[green]dtoverlay=spi0-0cs is already configured[/green]")
                input("\nPress Enter to continue...")
                return
        except Exception:
            pass

        if not Confirm.ask("Add dtoverlay=spi0-0cs to config.txt?", default=True):
            return

        try:
            # First ensure SPI is enabled
            console.print("[cyan]Ensuring SPI is enabled...[/cyan]")
            subprocess.run([
                'sudo', 'raspi-config', 'nonint', 'set_config_var',
                'dtparam=spi', 'on', str(self._boot_config_path)
            ], check=True, timeout=30)

            # Add the overlay after dtparam=spi=on
            console.print("[cyan]Adding spi0-0cs overlay...[/cyan]")
            subprocess.run([
                'sudo', 'sed', '-i',
                '/^\\s*dtparam=spi=on/a dtoverlay=spi0-0cs',
                str(self._boot_config_path)
            ], check=True, timeout=10)

            console.print("[green]SPI overlay added successfully![/green]")
            console.print("\n[yellow]A reboot is required for changes to take effect.[/yellow]")

            if Confirm.ask("Reboot now?", default=False):
                self._safe_reboot()

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _detect_hardware(self):
        """Detect connected hardware"""
        console.print("\n[bold cyan]── Hardware Detection ──[/bold cyan]\n")

        table = Table(title="Detected Hardware", show_header=True)
        table.add_column("Interface", style="cyan")
        table.add_column("Status")
        table.add_column("Devices")

        # SPI
        spi_devs = list(Path('/dev').glob('spidev*'))
        spi_status = "[green]Enabled[/green]" if spi_devs else "[red]Disabled[/red]"
        table.add_row("SPI", spi_status, ', '.join([d.name for d in spi_devs]) or "-")

        # I2C
        i2c_devs = list(Path('/dev').glob('i2c-*'))
        i2c_status = "[green]Enabled[/green]" if i2c_devs else "[red]Disabled[/red]"
        i2c_devices = []
        if i2c_devs:
            try:
                result = subprocess.run(['i2cdetect', '-y', '1'], capture_output=True, text=True, timeout=5)
                # Count detected addresses (not -- or empty)
                lines = result.stdout.split('\n')[1:]  # Skip header
                for line in lines:
                    parts = line.split()[1:] if ':' in line else []
                    for p in parts:
                        if p not in ['--', '']:
                            i2c_devices.append(f"0x{p}")
            except Exception:
                pass
        table.add_row("I2C", i2c_status, ', '.join(i2c_devices[:5]) or "-")

        # Serial
        serial_devs = []
        for dev in ['/dev/ttyAMA0', '/dev/serial0', '/dev/ttyUSB0']:
            if Path(dev).exists():
                serial_devs.append(Path(dev).name)
        serial_status = "[green]Available[/green]" if serial_devs else "[yellow]None[/yellow]"
        table.add_row("Serial", serial_status, ', '.join(serial_devs) or "-")

        # GPIO
        gpio_path = Path('/sys/class/gpio')
        gpio_status = "[green]Available[/green]" if gpio_path.exists() else "[red]Not available[/red]"
        table.add_row("GPIO", gpio_status, "-")

        console.print(table)

        # Check for LoRa devices
        console.print("\n[cyan]Checking for LoRa devices...[/cyan]")
        if spi_devs:
            console.print("  SPI available - LoRa HAT can be connected")
            # Check for common LoRa drivers
            for driver in ['sx126x', 'sx127x', 'lora']:
                driver_path = Path(f'/sys/bus/spi/drivers/{driver}')
                if driver_path.exists():
                    console.print(f"  [green]Driver found: {driver}[/green]")
        else:
            console.print("  [yellow]Enable SPI to use LoRa HATs[/yellow]")

        input("\nPress Enter to continue...")

    def _select_device(self):
        """Select and configure a hardware device"""
        console.print("\n[bold cyan]── Select Hardware Device ──[/bold cyan]\n")

        # Show available devices
        table = Table(title="Available Devices", show_header=True)
        table.add_column("#", style="bold")
        table.add_column("Device", style="cyan")
        table.add_column("Description")
        table.add_column("Requirements")

        devices = list(HARDWARE_DEVICES.items())
        for i, (key, device) in enumerate(devices, 1):
            reqs = []
            if device.requires_spi:
                reqs.append("SPI")
            if device.requires_i2c:
                reqs.append("I2C")
            if device.requires_serial:
                reqs.append("Serial")
            table.add_row(str(i), device.name, device.description, ', '.join(reqs))

        console.print(table)
        console.print()

        choice = Prompt.ask("Select device number (or 0 to cancel)", default="0")

        try:
            idx = int(choice)
            if idx == 0:
                return
            if idx < 1 or idx > len(devices):
                console.print("[red]Invalid selection[/red]")
                return

            key, device = devices[idx - 1]
        except ValueError:
            console.print("[red]Invalid input[/red]")
            return

        console.print(f"\n[cyan]Selected: {device.name}[/cyan]")
        if device.notes:
            console.print(f"[dim]{device.notes}[/dim]")

        # Check requirements
        needs_reboot = False

        if device.requires_spi:
            spi_enabled = Path('/dev/spidev0.0').exists()
            if not spi_enabled:
                console.print("\n[yellow]SPI is required but not enabled[/yellow]")
                if Confirm.ask("Enable SPI now?", default=True):
                    subprocess.run([
                        'sudo', 'raspi-config', 'nonint', 'set_config_var',
                        'dtparam=spi', 'on', str(self._boot_config_path)
                    ], check=False, timeout=30)
                    needs_reboot = True

            if device.spi_overlay:
                console.print(f"\n[cyan]Adding SPI overlay: {device.spi_overlay}[/cyan]")
                # Check if already present
                result = subprocess.run(
                    ['grep', '-q', f'dtoverlay={device.spi_overlay}', str(self._boot_config_path)],
                    capture_output=True, timeout=10
                )
                if result.returncode != 0:
                    subprocess.run([
                        'sudo', 'sed', '-i',
                        f'/^\\s*dtparam=spi=on/a dtoverlay={device.spi_overlay}',
                        str(self._boot_config_path)
                    ], check=False, timeout=10)
                    needs_reboot = True

        if device.requires_serial:
            if not Path('/dev/serial0').exists():
                console.print("\n[yellow]Serial port is required[/yellow]")
                if Confirm.ask("Configure serial for hardware use?", default=True):
                    subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_serial_hw', '0'], check=False, timeout=30)
                    subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_serial_cons', '1'], check=False, timeout=30)
                    needs_reboot = True

        if device.requires_i2c:
            if not Path('/dev/i2c-1').exists():
                console.print("\n[yellow]I2C is required but not enabled[/yellow]")
                if Confirm.ask("Enable I2C now?", default=True):
                    subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_i2c', '0'], check=False, timeout=30)
                    needs_reboot = True

        # Copy config file
        if device.yaml_file:
            src = MESHTASTICD_AVAILABLE_D / device.yaml_file
            dst = MESHTASTICD_CONFIG_D / device.yaml_file

            if src.exists():
                console.print(f"\n[cyan]Config file: {device.yaml_file}[/cyan]")
                if Confirm.ask("Copy config file to config.d?", default=True):
                    MESHTASTICD_CONFIG_D.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    console.print(f"[green]Copied to {dst}[/green]")
            else:
                console.print(f"\n[yellow]Config file not found: {src}[/yellow]")

        if needs_reboot:
            console.print("\n[yellow]A reboot is required for hardware changes to take effect.[/yellow]")
            if Confirm.ask("Reboot now?", default=False):
                self._safe_reboot()

        input("\nPress Enter to continue...")

    def _view_active_configs(self):
        """View active configuration files"""
        console.print("\n[bold cyan]── Active Configurations ──[/bold cyan]\n")

        if not MESHTASTICD_CONFIG_D.exists():
            console.print("[yellow]No config.d directory found[/yellow]")
            input("\nPress Enter to continue...")
            return

        configs = list(MESHTASTICD_CONFIG_D.glob("*.yaml"))

        if not configs:
            console.print("[yellow]No active configurations in config.d[/yellow]")
        else:
            table = Table(title=f"Active Configs ({MESHTASTICD_CONFIG_D})", show_header=True)
            table.add_column("File", style="cyan")
            table.add_column("Size")
            table.add_column("Modified")

            for cfg in sorted(configs):
                stat = cfg.stat()
                size = f"{stat.st_size} bytes"
                from datetime import datetime
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                table.add_row(cfg.name, size, mtime)

            console.print(table)

        input("\nPress Enter to continue...")

    def _copy_config(self):
        """Copy config file from available.d to config.d"""
        console.print("\n[bold cyan]── Copy Configuration File ──[/bold cyan]\n")

        if not MESHTASTICD_AVAILABLE_D.exists():
            console.print("[red]available.d directory not found[/red]")
            input("\nPress Enter to continue...")
            return

        available = list(MESHTASTICD_AVAILABLE_D.glob("*.yaml"))

        if not available:
            console.print("[yellow]No available configurations[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print(f"[cyan]Available configs in {MESHTASTICD_AVAILABLE_D}:[/cyan]\n")
        for i, cfg in enumerate(sorted(available), 1):
            console.print(f"  {i}. {cfg.name}")

        console.print()
        choice = Prompt.ask("Select config number (or 0 to cancel)", default="0")

        try:
            idx = int(choice)
            if idx == 0:
                return
            if idx < 1 or idx > len(available):
                console.print("[red]Invalid selection[/red]")
                return

            src = sorted(available)[idx - 1]
        except ValueError:
            console.print("[red]Invalid input[/red]")
            return

        dst = MESHTASTICD_CONFIG_D / src.name

        if dst.exists():
            if not Confirm.ask(f"[yellow]{dst.name} already exists. Overwrite?[/yellow]", default=False):
                return

        try:
            MESHTASTICD_CONFIG_D.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            console.print(f"\n[green]Copied: {src.name} -> config.d/[/green]")

            if Confirm.ask("Edit the config file now?", default=False):
                subprocess.run(['sudo', 'nano', str(dst)])  # Interactive, no timeout

            if Confirm.ask("Restart meshtasticd to apply?", default=False):
                subprocess.run(['sudo', 'systemctl', 'restart', 'meshtasticd'], timeout=30)
                console.print("[green]Service restarted[/green]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _edit_config(self):
        """Edit a configuration file"""
        console.print("\n[bold cyan]── Edit Configuration File ──[/bold cyan]\n")

        # Combine available and active configs
        configs = []

        if MESHTASTICD_CONFIG_D.exists():
            for cfg in MESHTASTICD_CONFIG_D.glob("*.yaml"):
                configs.append(("config.d", cfg))

        if MESHTASTICD_AVAILABLE_D.exists():
            for cfg in MESHTASTICD_AVAILABLE_D.glob("*.yaml"):
                configs.append(("available.d", cfg))

        # Also add main config
        main_config = Path('/etc/meshtasticd/config.yaml')
        if main_config.exists():
            configs.insert(0, ("main", main_config))

        if not configs:
            console.print("[yellow]No configuration files found[/yellow]")
            input("\nPress Enter to continue...")
            return

        console.print("[cyan]Available configuration files:[/cyan]\n")
        for i, (location, cfg) in enumerate(configs, 1):
            console.print(f"  {i}. [{location}] {cfg.name}")

        console.print()
        choice = Prompt.ask("Select file to edit (or 0 to cancel)", default="0")

        try:
            idx = int(choice)
            if idx == 0:
                return
            if idx < 1 or idx > len(configs):
                console.print("[red]Invalid selection[/red]")
                return

            _, cfg_path = configs[idx - 1]
        except ValueError:
            console.print("[red]Invalid input[/red]")
            return

        subprocess.run(['sudo', 'nano', str(cfg_path)])  # Interactive editor, no timeout

        # Validate YAML
        console.print("\n[cyan]Validating YAML syntax...[/cyan]")
        try:
            import yaml
            with open(cfg_path) as f:
                yaml.safe_load(f)
            console.print("[green]YAML syntax is valid[/green]")
        except ImportError:
            console.print("[dim]PyYAML not installed, skipping validation[/dim]")
        except yaml.YAMLError as e:
            console.print(f"[red]YAML Error: {e}[/red]")
            console.print("[yellow]Please fix the syntax before applying[/yellow]")

        if Confirm.ask("Restart meshtasticd to apply changes?", default=False):
            subprocess.run(['sudo', 'systemctl', 'restart', 'meshtasticd'], timeout=30)
            console.print("[green]Service restarted[/green]")

        input("\nPress Enter to continue...")

    def _view_boot_config(self):
        """View boot config.txt"""
        console.print(f"\n[bold cyan]── {self._boot_config_path} ──[/bold cyan]\n")

        try:
            content = self._boot_config_path.read_text()
            # Show relevant sections
            lines = content.split('\n')
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('#'):
                    console.print(f"[dim]{line}[/dim]")
                elif 'dtparam=' in line or 'dtoverlay=' in line:
                    console.print(f"[cyan]{line}[/cyan]")
                elif stripped:
                    console.print(line)
        except Exception as e:
            console.print(f"[red]Error reading config: {e}[/red]")

        input("\nPress Enter to continue...")

    def _safe_reboot(self):
        """Perform a safe reboot with application checks"""
        console.print("\n[bold yellow]═══════════ Safe Reboot ═══════════[/bold yellow]\n")

        # Check for running processes that might be affected
        console.print("[cyan]Checking for running applications...[/cyan]\n")

        warnings = []

        # Check for running editors
        editors = ['nano', 'vim', 'vi', 'emacs', 'code']
        for editor in editors:
            result = subprocess.run(['pgrep', '-x', editor], capture_output=True, timeout=5)
            if result.returncode == 0:
                warnings.append(f"  - {editor} is running (unsaved changes may be lost)")

        # Check for SSH sessions
        result = subprocess.run(['who'], capture_output=True, text=True, timeout=5)
        ssh_sessions = [l for l in result.stdout.split('\n') if 'pts/' in l]
        if len(ssh_sessions) > 1:
            warnings.append(f"  - {len(ssh_sessions)} SSH sessions active")

        # Check for running Python scripts
        result = subprocess.run(['pgrep', '-f', 'python.*meshtastic'], capture_output=True, timeout=5)
        if result.returncode == 0:
            warnings.append("  - Meshtastic Python scripts are running")

        # Check for apt/dpkg locks
        if Path('/var/lib/dpkg/lock-frontend').exists():
            result = subprocess.run(['fuser', '/var/lib/dpkg/lock-frontend'], capture_output=True, timeout=5)
            if result.returncode == 0:
                warnings.append("  - Package manager (apt) is running")

        # Display warnings
        if warnings:
            console.print("[yellow]The following applications are running:[/yellow]\n")
            for warning in warnings:
                console.print(f"[yellow]{warning}[/yellow]")
            console.print()
            console.print("[yellow]Please save your work and close applications before rebooting.[/yellow]")
        else:
            console.print("[green]No conflicting applications detected.[/green]")

        console.print()

        # Show what will happen
        console.print("[cyan]The following will happen on reboot:[/cyan]")
        console.print("  - All running applications will be stopped")
        console.print("  - Hardware interface changes will take effect (SPI, I2C, Serial)")
        console.print("  - meshtasticd service will restart automatically")
        console.print()

        if not Confirm.ask("[bold red]Are you sure you want to reboot now?[/bold red]", default=False):
            console.print("\n[green]Reboot cancelled.[/green]")
            input("\nPress Enter to continue...")
            return

        # Stop meshtasticd gracefully
        console.print("\n[cyan]Stopping meshtasticd service...[/cyan]")
        subprocess.run(['sudo', 'systemctl', 'stop', 'meshtasticd'], check=False, timeout=30)

        console.print("[yellow]Rebooting in 5 seconds... Press Ctrl+C to cancel[/yellow]")
        try:
            import time
            for i in range(5, 0, -1):
                console.print(f"  {i}...")
                time.sleep(1)
            subprocess.run(['sudo', 'reboot'], timeout=10)
        except KeyboardInterrupt:
            console.print("\n[green]Reboot cancelled.[/green]")
            subprocess.run(['sudo', 'systemctl', 'start', 'meshtasticd'], check=False, timeout=30)

        input("\nPress Enter to continue...")


def hardware_config_menu():
    """Entry point for hardware configuration"""
    configurator = HardwareConfigurator()
    configurator.interactive_menu()
