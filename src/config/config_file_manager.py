"""Configuration file manager - select yaml from available.d and edit with nano

Enhanced with visual validation, status dashboard, and guided setup.
"""

import os
import subprocess
import shutil
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

from utils.safe_import import safe_import

# Centralized service checker (first-party — direct import per CLAUDE.md)
from utils.service_check import (
    check_service as _check_service,
    check_systemd_service as _check_systemd_service,
    ServiceState as _ServiceState,
    apply_config_and_restart as _apply_config_and_restart,
    daemon_reload as _daemon_reload_fn,
    _sudo_cmd,
    _sudo_write,
)

# YAML support
_yaml_mod, _HAS_YAML = safe_import('yaml')

# Rich syntax highlighting
_Syntax, _HAS_SYNTAX = safe_import('rich.syntax', 'Syntax')

console = Console()


class ConfigFileManager:
    """Manage meshtasticd configuration files with visual feedback"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"
    MAIN_CONFIG = CONFIG_BASE / "config.yaml"
    BOOT_CONFIG = Path("/boot/firmware/config.txt")
    BOOT_CONFIG_ALT = Path("/boot/config.txt")

    # Project templates directory (for MeshForge-provided configs)
    TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "available.d"

    # Required config sections for a working setup
    REQUIRED_SECTIONS = {
        'Lora': 'LoRa radio settings (Module, Region, etc.)',
        'General': 'Node settings (MaxNodes, etc.)',
    }

    # Recommended sections
    RECOMMENDED_SECTIONS = {
        'Webserver': 'Web interface (Port, RootPath)',
        'Logging': 'Log verbosity settings',
    }

    def __init__(self):
        self._return_to_main = False

    def _prompt_back(self, additional_choices=None):
        """Standard prompt with back options"""
        choices = list(additional_choices) if additional_choices else []
        console.print(f"\n  [bold]0[/bold]. Back")
        console.print(f"  [bold]m[/bold]. Main Menu")
        return choices + ["0", "m"]

    def _handle_back(self, choice):
        """Handle back navigation"""
        if choice == "m":
            self._return_to_main = True
            return True
        if choice == "0":
            return True
        return False

    def list_available_configs(self):
        """List all yaml files in available.d"""
        configs = set()

        # System available.d
        if self.AVAILABLE_D.exists():
            configs.update(f.name for f in self.AVAILABLE_D.glob("*.yaml"))

        return sorted(configs)

    def list_template_configs(self):
        """List template configs that can be installed"""
        templates = set()
        if self.TEMPLATES_DIR.exists():
            templates.update(f.name for f in self.TEMPLATES_DIR.glob("*.yaml"))
        return sorted(templates)

    def get_missing_templates(self):
        """Get templates that exist in project but not in system"""
        system_configs = set(self.list_available_configs())
        template_configs = set(self.list_template_configs())
        return sorted(template_configs - system_configs)

    def install_templates(self, templates=None):
        """Install templates from project directory to system available.d"""
        if templates is None:
            templates = self.get_missing_templates()

        if not templates:
            console.print("[green]All templates already installed[/green]")
            return []

        if not self.TEMPLATES_DIR.exists():
            console.print("[red]Templates directory not found[/red]")
            return []

        # Create system directory if needed
        self.AVAILABLE_D.mkdir(parents=True, exist_ok=True)

        installed = []
        for template in templates:
            src = self.TEMPLATES_DIR / template
            dst = self.AVAILABLE_D / template
            if src.exists() and not dst.exists():
                try:
                    shutil.copy2(src, dst)
                    installed.append(template)
                    console.print(f"  [green]+[/green] {template}")
                except Exception as e:
                    console.print(f"  [red]X[/red] {template}: {e}")

        return installed

    def list_active_configs(self):
        """List all yaml files in config.d"""
        if not self.CONFIG_D.exists():
            return []
        return sorted([f.name for f in self.CONFIG_D.glob("*.yaml")])

    def _get_config_content(self):
        """Read and return main config.yaml content"""
        if self.MAIN_CONFIG.exists():
            try:
                return self.MAIN_CONFIG.read_text()
            except Exception:
                return None
        return None

    def _validate_config(self):
        """Validate configuration and return status dict"""
        status = {
            'config_exists': self.MAIN_CONFIG.exists(),
            'config_valid': False,
            'missing_required': [],
            'missing_recommended': [],
            'lora_config_active': False,
            'lora_config_name': None,
            'active_count': 0,
            'spi_enabled': False,
            'i2c_enabled': False,
            'errors': [],
            'warnings': [],
        }

        # Check SPI/I2C hardware
        status['spi_enabled'] = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        status['i2c_enabled'] = Path('/dev/i2c-1').exists()

        # Check config.d
        if self.CONFIG_D.exists():
            active = list(self.CONFIG_D.glob("*.yaml"))
            status['active_count'] = len(active)
            lora_configs = [f for f in active if f.name.startswith("lora-")]
            if lora_configs:
                status['lora_config_active'] = True
                status['lora_config_name'] = lora_configs[0].name

        # Check main config.yaml
        if status['config_exists']:
            content = self._get_config_content()
            if content:
                if _HAS_YAML:
                    try:
                        data = _yaml_mod.safe_load(content)
                        status['config_valid'] = True

                        # Check required sections
                        for section in self.REQUIRED_SECTIONS:
                            if section not in data or data[section] is None:
                                status['missing_required'].append(section)

                        # Check recommended sections
                        for section in self.RECOMMENDED_SECTIONS:
                            if section not in data or data[section] is None:
                                status['missing_recommended'].append(section)

                    except _yaml_mod.YAMLError as e:
                        status['errors'].append(f"YAML syntax error: {str(e)[:50]}")
                else:
                    # YAML not available, do text-based check
                    for section in self.REQUIRED_SECTIONS:
                        if f"{section}:" not in content:
                            status['missing_required'].append(section)

        # Generate warnings
        if not status['spi_enabled']:
            status['warnings'].append("SPI not enabled - required for LoRa radio")
        if not status['lora_config_active']:
            status['warnings'].append("No LoRa hardware config active")
        if status['missing_required']:
            status['warnings'].append(f"Missing required sections: {', '.join(status['missing_required'])}")

        return status

    def _show_status_dashboard(self):
        """Display configuration status dashboard"""
        status = self._validate_config()

        console.print("\n[bold cyan]═══════════════ Configuration Status ═══════════════[/bold cyan]\n")

        # Create status table
        table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
        table.add_column("Component", style="cyan", width=20)
        table.add_column("Status", width=15)
        table.add_column("Details", style="dim", width=40)

        # Main config.yaml
        if status['config_exists']:
            if status['config_valid'] and not status['missing_required']:
                table.add_row("config.yaml", "[green]OK[/green]", "Valid configuration")
            elif status['config_valid']:
                table.add_row("config.yaml", "[yellow]Incomplete[/yellow]",
                            f"Missing: {', '.join(status['missing_required'])}")
            else:
                table.add_row("config.yaml", "[red]Invalid[/red]",
                            status['errors'][0] if status['errors'] else "Check syntax")
        else:
            table.add_row("config.yaml", "[red]Missing[/red]", "Create with option 2")

        # Hardware config
        if status['lora_config_active']:
            table.add_row("LoRa Config", "[green]Active[/green]", status['lora_config_name'])
        else:
            table.add_row("LoRa Config", "[red]Not Active[/red]", "Activate with option 1")

        # Active configs count
        table.add_row("Active Configs", f"[cyan]{status['active_count']}[/cyan]",
                     f"in {self.CONFIG_D}")

        # Hardware interfaces
        if status['spi_enabled']:
            table.add_row("SPI Interface", "[green]Enabled[/green]", "/dev/spidev0.*")
        else:
            table.add_row("SPI Interface", "[red]Disabled[/red]", "Enable in raspi-config")

        if status['i2c_enabled']:
            table.add_row("I2C Interface", "[green]Enabled[/green]", "/dev/i2c-1")
        else:
            table.add_row("I2C Interface", "[yellow]Disabled[/yellow]", "Optional for sensors")

        console.print(table)

        # Show warnings
        if status['warnings']:
            console.print("\n[yellow bold]Warnings:[/yellow bold]")
            for warning in status['warnings']:
                console.print(f"  [yellow]![/yellow] {warning}")

        # Show errors
        if status['errors']:
            console.print("\n[red bold]Errors:[/red bold]")
            for error in status['errors']:
                console.print(f"  [red]X[/red] {error}")

        # Show quick help based on status
        if not status['config_exists'] or status['missing_required'] or not status['lora_config_active']:
            console.print("\n[cyan bold]Quick Setup:[/cyan bold]")
            if not status['lora_config_active']:
                console.print("  1. Select your LoRa hardware config (option [bold]1[/bold])")
            if not status['config_exists']:
                console.print("  2. Create/edit config.yaml (option [bold]2[/bold])")
            elif status['missing_required']:
                console.print(f"  2. Add missing sections to config.yaml (option [bold]2[/bold])")
            console.print("  3. Apply changes and restart (option [bold]7[/bold])")

        return status

    def _daemon_reload(self):
        """Run systemctl daemon-reload"""
        console.print("[cyan]Running systemctl daemon-reload...[/cyan]")
        success, msg = _daemon_reload_fn()
        if success:
            console.print("[green]Daemon reloaded[/green]")
        else:
            console.print(f"[red]Failed to reload daemon: {msg}[/red]")
        return success

    def _restart_service(self):
        """Restart meshtasticd service"""
        console.print("[cyan]Restarting meshtasticd service...[/cyan]")
        success, msg = _apply_config_and_restart('meshtasticd')
        if success:
            console.print("[green]Service restarted[/green]")
        else:
            console.print(f"[red]Failed to restart service: {msg}[/red]")
        return success

    def _open_nano(self, file_path):
        """Open a file in nano editor"""
        console.print(f"\n[cyan]Opening {file_path} in nano...[/cyan]")
        console.print("[dim]Press Ctrl+X to exit, Y to save changes[/dim]\n")
        try:
            subprocess.run(["nano", str(file_path)], timeout=600)  # 10 minute timeout
            return True
        except FileNotFoundError:
            console.print("[red]nano not found. Install with: sudo apt install nano[/red]")
            return False
        except subprocess.TimeoutExpired:
            console.print("[yellow]Editor session timed out[/yellow]")
            return False
        except Exception as e:
            console.print(f"[red]Error opening nano: {e}[/red]")
            return False

    def interactive_menu(self):
        """Main configuration file management menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            # Show status dashboard
            status = self._show_status_dashboard()

            console.print("\n[dim cyan]── Configuration Actions ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. Select & Activate Hardware Config (lora-*.yaml)")
            console.print(f"  [bold]2[/bold]. Edit Main config.yaml")
            console.print(f"  [bold]3[/bold]. Edit Active Config File")
            console.print(f"  [bold]4[/bold]. View Available Configurations ({len(self.list_available_configs())} files)")
            console.print(f"  [bold]5[/bold]. View Active Configurations ({len(self.list_active_configs())} files)")
            console.print(f"  [bold]6[/bold]. Deactivate Config (remove from config.d)")

            console.print("\n[dim cyan]── Validation & Service ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. Apply Changes (daemon-reload + restart)")
            console.print(f"  [bold]8[/bold]. View Current config.yaml")
            console.print(f"  [bold]9[/bold]. Validate Configuration (detailed check)")

            console.print("\n[dim cyan]── Setup Helpers ──[/dim cyan]")
            console.print(f"  [bold]g[/bold]. Guided Setup Wizard")
            console.print(f"  [bold]h[/bold]. Check Hardware (SPI, I2C)")
            console.print(f"  [bold]p[/bold]. Set GPS Position (manual coordinates)")

            choices = self._prompt_back(["1", "2", "3", "4", "5", "6", "7", "8", "9", "g", "h", "p"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self._select_and_activate()
            elif choice == "2":
                self._edit_main_config()
            elif choice == "3":
                self._edit_active_config()
            elif choice == "4":
                self._view_available()
            elif choice == "5":
                self._view_active()
            elif choice == "6":
                self._deactivate_config()
            elif choice == "7":
                self._apply_changes()
            elif choice == "8":
                self._view_current_config()
            elif choice == "9":
                self._detailed_validation()
            elif choice == "g":
                self._guided_setup()
            elif choice == "h":
                self._check_hardware()
            elif choice == "p":
                self._set_gps_position()

    def _guided_setup(self):
        """Guided setup wizard for new installations"""
        console.print("\n[bold cyan]═══════════════ Guided Setup Wizard ═══════════════[/bold cyan]\n")
        console.print("This wizard will help you configure meshtasticd step by step.\n")

        status = self._validate_config()

        # Step 1: Check hardware
        console.print("[bold]Step 1: Hardware Check[/bold]")
        if not status['spi_enabled']:
            console.print("[red]X[/red] SPI is not enabled. LoRa radios require SPI.")
            console.print("\n[yellow]To enable SPI:[/yellow]")
            console.print("  sudo raspi-config nonint do_spi 0")
            console.print("  Then add to /boot/firmware/config.txt:")
            console.print("    dtparam=spi=on")
            console.print("    dtoverlay=spi0-0cs")
            console.print("  Reboot required after changes.")

            if not Confirm.ask("\n[cyan]Continue anyway?[/cyan]", default=False):
                return
        else:
            console.print("[green]OK[/green] SPI is enabled")

        # Step 2: Select LoRa hardware
        console.print("\n[bold]Step 2: Select Your LoRa Hardware[/bold]")
        if status['lora_config_active']:
            console.print(f"[green]OK[/green] Currently active: {status['lora_config_name']}")
            if not Confirm.ask("Change hardware config?", default=False):
                pass
            else:
                self._select_and_activate()
        else:
            console.print("[yellow]No LoRa hardware config is active.[/yellow]")
            console.print("\nYou need to select the configuration for your LoRa radio.")
            console.print("[dim]Common options:[/dim]")
            console.print("  - lora-MeshAdv-900M30S.yaml for MeshToad/MeshAdv boards")
            console.print("  - lora-Adafruit-RFM9x*.yaml for Adafruit LoRa bonnets")
            console.print("  - lora-Elecrow-RFM95*.yaml for Elecrow boards")

            if Confirm.ask("\n[cyan]Select hardware config now?[/cyan]", default=True):
                self._select_and_activate()

        # Step 3: Configure config.yaml
        console.print("\n[bold]Step 3: Main Configuration (config.yaml)[/bold]")
        if not status['config_exists']:
            console.print("[yellow]config.yaml does not exist.[/yellow]")
            if Confirm.ask("Create a basic config.yaml?", default=True):
                self._create_basic_config()
        elif status['missing_required']:
            console.print(f"[yellow]Missing required sections: {', '.join(status['missing_required'])}[/yellow]")
            console.print("\n[dim]Your config.yaml needs these sections:[/dim]")
            for section, desc in self.REQUIRED_SECTIONS.items():
                if section in status['missing_required']:
                    console.print(f"  [yellow]{section}:[/yellow] {desc}")
            if Confirm.ask("\n[cyan]Edit config.yaml now?[/cyan]", default=True):
                self._edit_main_config()
        else:
            console.print("[green]OK[/green] config.yaml looks good")

        # Step 4: Apply and test
        console.print("\n[bold]Step 4: Apply Configuration[/bold]")
        if Confirm.ask("Apply configuration and restart service?", default=True):
            self._apply_changes()

            # Show service status
            console.print("\n[cyan]Checking service status...[/cyan]")
            import time
            time.sleep(2)

            status = _check_service('meshtasticd')
            if status.available:
                console.print("[bold green]Service is running![/bold green]")
                console.print("\n[cyan]Check the web interface at:[/cyan]")
                console.print("  https://<your-ip>:9443")
            else:
                console.print("[yellow]Service may not be running properly.[/yellow]")
                console.print("Check logs with: journalctl -u meshtasticd -f")

        console.print("\n[bold green]Setup wizard complete![/bold green]")
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _check_hardware(self):
        """Check hardware interfaces"""
        console.print("\n[bold cyan]═══════════════ Hardware Check ═══════════════[/bold cyan]\n")

        table = Table(box=box.ROUNDED, show_header=True)
        table.add_column("Interface", style="cyan")
        table.add_column("Status")
        table.add_column("Device")
        table.add_column("Notes", style="dim")

        # SPI
        spi0 = Path('/dev/spidev0.0').exists()
        spi1 = Path('/dev/spidev0.1').exists()
        if spi0 or spi1:
            devices = []
            if spi0:
                devices.append("spidev0.0")
            if spi1:
                devices.append("spidev0.1")
            table.add_row("SPI", "[green]Enabled[/green]", ", ".join(devices), "Required for LoRa")
        else:
            table.add_row("SPI", "[red]Disabled[/red]", "-", "Required for LoRa radio")

        # I2C
        i2c1 = Path('/dev/i2c-1').exists()
        if i2c1:
            table.add_row("I2C", "[green]Enabled[/green]", "i2c-1", "For sensors/displays")
        else:
            table.add_row("I2C", "[yellow]Disabled[/yellow]", "-", "Optional")

        # Serial/UART
        serial0 = Path('/dev/serial0').exists()
        ttyS0 = Path('/dev/ttyS0').exists()
        if serial0 or ttyS0:
            dev = "serial0" if serial0 else "ttyS0"
            table.add_row("UART", "[green]Enabled[/green]", dev, "For GPS modules")
        else:
            table.add_row("UART", "[yellow]Disabled[/yellow]", "-", "Optional (for GPS)")

        # GPIO
        gpio = Path('/sys/class/gpio').exists()
        if gpio:
            table.add_row("GPIO", "[green]Available[/green]", "/sys/class/gpio", "For buttons/LEDs")
        else:
            table.add_row("GPIO", "[yellow]Not found[/yellow]", "-", "-")

        console.print(table)

        # Check boot config
        boot_config = self.BOOT_CONFIG if self.BOOT_CONFIG.exists() else self.BOOT_CONFIG_ALT

        console.print(f"\n[bold]Boot Configuration ({boot_config}):[/bold]")
        if boot_config.exists():
            try:
                content = boot_config.read_text()
                checks = [
                    ('dtparam=spi=on', 'SPI enabled'),
                    ('dtoverlay=spi0-0cs', 'SPI overlay (for LoRa)'),
                    ('dtparam=i2c_arm=on', 'I2C enabled'),
                    ('enable_uart=1', 'UART enabled'),
                ]

                for setting, desc in checks:
                    # Check if setting is present and not commented
                    lines = content.split('\n')
                    found = any(setting in line and not line.strip().startswith('#') for line in lines)
                    if found:
                        console.print(f"  [green]OK[/green] {desc}")
                    else:
                        console.print(f"  [dim]-[/dim]  {desc} (not set)")

            except Exception as e:
                console.print(f"[red]Error reading boot config: {e}[/red]")
        else:
            console.print("[yellow]Boot config not found[/yellow]")

        # Instructions if SPI not enabled
        if not (spi0 or spi1):
            console.print("\n[yellow bold]To enable SPI for LoRa radio:[/yellow bold]")
            console.print("  1. Run: sudo raspi-config")
            console.print("  2. Go to: Interface Options → SPI → Enable")
            console.print("  3. Add to /boot/firmware/config.txt:")
            console.print("       dtparam=spi=on")
            console.print("       dtoverlay=spi0-0cs")
            console.print("  4. Reboot the system")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _detailed_validation(self):
        """Run detailed configuration validation"""
        console.print("\n[bold cyan]═══════════════ Configuration Validation ═══════════════[/bold cyan]\n")

        issues = []
        warnings = []
        info = []

        # Check config.yaml exists
        console.print("[bold]Checking config.yaml...[/bold]")
        if not self.MAIN_CONFIG.exists():
            issues.append("config.yaml not found")
            console.print(f"  [red]X[/red] File not found: {self.MAIN_CONFIG}")
        else:
            console.print(f"  [green]OK[/green] File exists: {self.MAIN_CONFIG}")

            content = self._get_config_content()
            if content:
                # YAML syntax check
                if _HAS_YAML:
                    try:
                        data = _yaml_mod.safe_load(content)
                        console.print("  [green]OK[/green] YAML syntax valid")

                        # Check required sections
                        console.print("\n[bold]Checking required sections...[/bold]")
                        for section, desc in self.REQUIRED_SECTIONS.items():
                            if section in data and data[section]:
                                console.print(f"  [green]OK[/green] {section}: present")
                                # Show key settings
                                if isinstance(data[section], dict):
                                    for key, value in list(data[section].items())[:3]:
                                        console.print(f"      [dim]{key}: {value}[/dim]")
                            else:
                                issues.append(f"Missing required section: {section}")
                                console.print(f"  [red]X[/red] {section}: MISSING ({desc})")

                        # Check recommended sections
                        console.print("\n[bold]Checking recommended sections...[/bold]")
                        for section, desc in self.RECOMMENDED_SECTIONS.items():
                            if section in data and data[section]:
                                console.print(f"  [green]OK[/green] {section}: present")
                            else:
                                warnings.append(f"Missing recommended section: {section}")
                                console.print(f"  [yellow]~[/yellow] {section}: not set ({desc})")

                    except _yaml_mod.YAMLError as e:
                        issues.append(f"YAML syntax error: {e}")
                        console.print(f"  [red]X[/red] YAML syntax error: {e}")
                else:
                    console.print("  [yellow]~[/yellow] PyYAML not installed, skipping deep validation")

        # Check config.d
        console.print("\n[bold]Checking config.d...[/bold]")
        if self.CONFIG_D.exists():
            active = list(self.CONFIG_D.glob("*.yaml"))
            console.print(f"  [green]OK[/green] Directory exists with {len(active)} config(s)")

            lora_found = False
            for f in active:
                if f.name.startswith("lora-"):
                    lora_found = True
                    console.print(f"  [green]OK[/green] LoRa config active: {f.name}")
                    break

            if not lora_found:
                issues.append("No LoRa hardware config active")
                console.print("  [red]X[/red] No lora-*.yaml active (required for radio)")
        else:
            warnings.append("config.d directory not found")
            console.print(f"  [yellow]~[/yellow] Directory not found: {self.CONFIG_D}")

        # Check service
        console.print("\n[bold]Checking service status...[/bold]")
        try:
            status = _check_service('meshtasticd')
            if status.available:
                console.print("  [green]OK[/green] meshtasticd service is running")
            else:
                info.append("meshtasticd service not running")
                console.print(f"  [yellow]~[/yellow] Service status: {status.state.value}")
        except Exception as e:
            console.print(f"  [yellow]~[/yellow] Could not check service: {e}")

        # Summary
        console.print("\n" + "=" * 50)
        if issues:
            console.print(f"\n[red bold]ERRORS ({len(issues)}):[/red bold]")
            for issue in issues:
                console.print(f"  [red]X[/red] {issue}")
        else:
            console.print("\n[green bold]No critical errors found[/green bold]")

        if warnings:
            console.print(f"\n[yellow bold]WARNINGS ({len(warnings)}):[/yellow bold]")
            for warning in warnings:
                console.print(f"  [yellow]![/yellow] {warning}")

        if not issues:
            console.print("\n[green]Configuration appears valid![/green]")
        else:
            console.print("\n[red]Fix the errors above before starting the service.[/red]")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _select_and_activate(self):
        """Select a config from available.d and copy to config.d"""
        available = self.list_available_configs()
        missing_templates = self.get_missing_templates()

        if not available:
            console.print("[yellow]No configuration files found in available.d[/yellow]")
            console.print(f"[dim]Directory: {self.AVAILABLE_D}[/dim]")

            # Check if we have templates to install
            if missing_templates:
                console.print(f"\n[cyan]Found {len(missing_templates)} MeshForge templates that can be installed:[/cyan]")
                for t in missing_templates[:5]:
                    console.print(f"  [dim]{t}[/dim]")
                if len(missing_templates) > 5:
                    console.print(f"  [dim]... and {len(missing_templates) - 5} more[/dim]")

                if Confirm.ask("\n[cyan]Install MeshForge templates?[/cyan]", default=True):
                    console.print("\n[cyan]Installing templates...[/cyan]")
                    self.install_templates()
                    available = self.list_available_configs()
                    if not available:
                        console.print("[red]No templates installed. Check permissions.[/red]")
                        Prompt.ask("\n[dim]Press Enter to continue[/dim]")
                        return
                else:
                    Prompt.ask("\n[dim]Press Enter to continue[/dim]")
                    return
            else:
                console.print("[dim]Install meshtasticd to get configuration templates[/dim]")
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
                return
        elif missing_templates:
            # Some templates exist but there are new ones available
            console.print(f"\n[dim]Tip: {len(missing_templates)} additional MeshForge templates available[/dim]")
            console.print(f"[dim]New: {', '.join(missing_templates[:3])}{'...' if len(missing_templates) > 3 else ''}[/dim]")
            if Confirm.ask("[cyan]Install new templates?[/cyan]", default=False):
                console.print("\n[cyan]Installing templates...[/cyan]")
                self.install_templates(missing_templates)
                available = self.list_available_configs()

        console.print("\n[bold cyan]═══════════════ Select Hardware Configuration ═══════════════[/bold cyan]\n")
        console.print("[dim]Select the configuration that matches your LoRa hardware.[/dim]")
        console.print("[dim]Tip: MeshToad/MeshTadpole uses MeshAdv configs.[/dim]\n")

        # Group configs by type
        lora_configs = [f for f in available if f.startswith("lora-") or "lora" in f.lower()]
        display_configs = [f for f in available if f.startswith("display-")]
        preset_configs = [f for f in available if any(x in f.lower() for x in ["mtnmesh", "emergency", "urban", "repeater"])]
        other_configs = [f for f in available if f not in lora_configs + display_configs + preset_configs]

        all_configs = []
        idx = 1

        if lora_configs:
            console.print("[bold cyan]── LoRa Hardware Configs ──[/bold cyan]")
            console.print("[dim]Select one for your radio module[/dim]")
            for cfg in lora_configs:
                # Try to get description
                desc = self._get_config_description(cfg)
                if desc:
                    console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                    console.print(f"       [dim]{desc}[/dim]")
                else:
                    console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if display_configs:
            console.print("\n[bold cyan]── Display Configs ──[/bold cyan]")
            for cfg in display_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if preset_configs:
            console.print("\n[bold cyan]── Network Presets ──[/bold cyan]")
            for cfg in preset_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if other_configs:
            console.print("\n[bold cyan]── Other Configs ──[/bold cyan]")
            for cfg in other_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        console.print(f"\n  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(all_configs) + 1)]
        choice = Prompt.ask("\n[cyan]Select configuration[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = all_configs[int(choice) - 1]
        src = self.AVAILABLE_D / selected
        dst = self.CONFIG_D / selected

        # Show preview
        console.print(f"\n[bold]Preview of {selected}:[/bold]")
        console.print("-" * 50)
        try:
            with open(src, 'r') as f:
                content = f.read()
                lines = content.split('\n')[:25]
                for line in lines:
                    console.print(f"[dim]{line}[/dim]")
                if len(content.split('\n')) > 25:
                    console.print("[dim]... (truncated)[/dim]")
        except Exception as e:
            console.print(f"[red]Could not read file: {e}[/red]")
            return

        console.print("-" * 50)

        # Confirm and copy
        if Confirm.ask(f"\n[yellow]Activate {selected}?[/yellow]", default=True):
            try:
                self.CONFIG_D.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                console.print(f"[green]Activated: {selected}[/green]")
                console.print(f"[dim]Copied to: {dst}[/dim]")

                if Confirm.ask("\n[cyan]Edit this config before applying?[/cyan]", default=False):
                    self._open_nano(dst)

                if Confirm.ask("\n[cyan]Apply changes now (daemon-reload + restart)?[/cyan]", default=True):
                    self._apply_changes()

            except Exception as e:
                console.print(f"[red]Failed to activate config: {e}[/red]")

    def _get_config_description(self, filename):
        """Get description from config file's first comment line"""
        try:
            with open(self.AVAILABLE_D / filename, 'r') as f:
                first_line = f.readline().strip()
                if first_line.startswith("#"):
                    return first_line[1:].strip()[:60]
        except (OSError, UnicodeDecodeError):
            pass
        return None

    def _edit_main_config(self):
        """Edit the main config.yaml with guidance"""
        if not self.MAIN_CONFIG.exists():
            console.print("[yellow]Main config.yaml does not exist[/yellow]")
            if Confirm.ask("Create a basic config.yaml?", default=True):
                self._create_basic_config()
            else:
                return

        # Show current validation status before editing
        console.print("\n[cyan]Current config.yaml status:[/cyan]")
        status = self._validate_config()
        if status['missing_required']:
            console.print(f"[yellow]Missing required sections: {', '.join(status['missing_required'])}[/yellow]")
            console.print("\n[dim]Make sure to add these sections:[/dim]")
            for section in status['missing_required']:
                console.print(f"  [yellow]{section}:[/yellow] {self.REQUIRED_SECTIONS.get(section, '')}")

        self._open_nano(self.MAIN_CONFIG)

        # Validate after editing
        console.print("\n[cyan]Validating changes...[/cyan]")
        new_status = self._validate_config()
        if new_status['errors']:
            console.print(f"[red]Errors found: {new_status['errors']}[/red]")
        elif new_status['missing_required']:
            console.print(f"[yellow]Still missing: {', '.join(new_status['missing_required'])}[/yellow]")
        else:
            console.print("[green]Config looks good![/green]")

        if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
            self._apply_changes()

    def _create_basic_config(self):
        """Create a basic config.yaml — only if it doesn't already exist.

        NEVER overwrites the user's config.yaml (MaxNodes, etc.).
        """
        if self.MAIN_CONFIG.exists():
            console.print(f"[yellow]{self.MAIN_CONFIG} already exists — not overwriting.[/yellow]")
            return
        basic_config = """# Meshtasticd Configuration
# See: https://meshtastic.org/docs/hardware/devices/linux-native-hardware/
# Hardware-specific LoRa settings should be in config.d/*.yaml

# General node settings (REQUIRED)
General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/

# LoRa radio settings (REQUIRED)
# Most settings come from the hardware config in config.d/
# You can override region here if needed:
Lora:
  Region: US  # Change to your region: US, EU_868, AU_915, etc.

# Web server settings (RECOMMENDED)
Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

# Logging settings
Logging:
  LogLevel: info  # debug, info, warn, error

# GPS settings (optional - uncomment if using GPS module)
# GPS:
#   SerialPath: /dev/serial0

# I2C devices (optional - for sensors/displays)
# I2C:
#   I2CDevice: /dev/i2c-1
"""
        try:
            subprocess.run(
                _sudo_cmd(['mkdir', '-p', str(self.CONFIG_BASE)]),
                check=True, timeout=10
            )
            success, msg = _sudo_write(str(self.MAIN_CONFIG), basic_config)
            if success:
                console.print(f"[green]Created: {self.MAIN_CONFIG}[/green]")
                console.print("[dim]Template includes all required sections with comments.[/dim]")
            else:
                console.print(f"[red]Failed to create config: {msg}[/red]")
        except Exception as e:
            console.print(f"[red]Failed to create config: {e}[/red]")

    def _edit_active_config(self):
        """Edit an active config from config.d"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs in config.d[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        console.print("\n[bold cyan]Active Configurations:[/bold cyan]\n")
        for i, cfg in enumerate(active, 1):
            console.print(f"  [bold]{i}[/bold]. {cfg}")
        console.print(f"  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(active) + 1)]
        choice = Prompt.ask("\n[cyan]Select config to edit[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = active[int(choice) - 1]
        self._open_nano(self.CONFIG_D / selected)

        if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
            self._apply_changes()

    def _view_available(self):
        """View available configurations with descriptions"""
        available = self.list_available_configs()

        if not available:
            console.print("[yellow]No configuration files found[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        table = Table(title=f"Available Configs ({self.AVAILABLE_D})", box=box.ROUNDED)
        table.add_column("#", style="cyan", width=4)
        table.add_column("Filename", style="green")
        table.add_column("Description", style="dim", width=50)

        for i, cfg in enumerate(available, 1):
            desc = self._get_config_description(cfg) or ""
            table.add_row(str(i), cfg, desc)

        console.print(table)
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _view_active(self):
        """View active configurations"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs in config.d[/yellow]")
        else:
            table = Table(title=f"Active Configs ({self.CONFIG_D})", box=box.ROUNDED)
            table.add_column("#", style="cyan", width=4)
            table.add_column("Filename", style="green")
            table.add_column("Size", style="dim")

            for i, cfg in enumerate(active, 1):
                path = self.CONFIG_D / cfg
                try:
                    size = f"{path.stat().st_size} bytes"
                except (OSError, AttributeError):
                    size = "-"
                table.add_row(str(i), cfg, size)

            console.print(table)

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _deactivate_config(self):
        """Remove a config from config.d"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs to deactivate[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        console.print("\n[bold cyan]Active Configurations:[/bold cyan]\n")
        for i, cfg in enumerate(active, 1):
            console.print(f"  [bold]{i}[/bold]. {cfg}")
        console.print(f"  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(active) + 1)]
        choice = Prompt.ask("\n[cyan]Select config to deactivate[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = active[int(choice) - 1]
        cfg_path = self.CONFIG_D / selected

        if Confirm.ask(f"[yellow]Remove {selected} from config.d?[/yellow]", default=False):
            try:
                cfg_path.unlink()
                console.print(f"[green]Deactivated: {selected}[/green]")

                if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
                    self._apply_changes()
            except Exception as e:
                console.print(f"[red]Failed to deactivate: {e}[/red]")

    def _apply_changes(self):
        """Apply configuration changes with status feedback"""
        console.print("\n[bold cyan]Applying Configuration Changes[/bold cyan]\n")

        self._daemon_reload()

        if Confirm.ask("\n[cyan]Restart meshtasticd service?[/cyan]", default=True):
            self._restart_service()

            # Wait and show status
            console.print("\n[cyan]Waiting for service to start...[/cyan]")
            import time
            time.sleep(3)

            console.print("\n[bold]Service status:[/bold]")
            try:
                result = subprocess.run(
                    _sudo_cmd(["systemctl", "status", "meshtasticd", "--no-pager", "-l"]),
                    capture_output=True, text=True, timeout=15
                )
                # Show just the relevant parts
                lines = result.stdout.split('\n')
                for line in lines[:15]:  # First 15 lines
                    console.print(line)

                # Check if running using centralized service checker
                status = _check_service('meshtasticd')
                if status.available:
                    console.print("\n[bold green]Service is running![/bold green]")
                else:
                    console.print(f"\n[yellow]Service status: {status.state.value}[/yellow]")
                    console.print("[dim]Check logs with: journalctl -u meshtasticd -f[/dim]")

            except subprocess.TimeoutExpired:
                console.print("[yellow]Timeout checking status[/yellow]")
            except Exception as e:
                console.print(f"[red]Error checking status: {e}[/red]")

    def _view_current_config(self):
        """View the current main config with syntax highlighting"""
        if not self.MAIN_CONFIG.exists():
            console.print("[yellow]No config.yaml found[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        try:
            with open(self.MAIN_CONFIG, 'r') as f:
                content = f.read()

            # Use syntax highlighting if available
            if _HAS_SYNTAX:
                syntax = _Syntax(content, "yaml", theme="monokai", line_numbers=True)
                console.print(Panel(syntax, title=f"[cyan]{self.MAIN_CONFIG}[/cyan]", border_style="cyan"))
            else:
                console.print(Panel(content, title=f"[cyan]{self.MAIN_CONFIG}[/cyan]", border_style="cyan"))

        except Exception as e:
            console.print(f"[red]Error reading config: {e}[/red]")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _set_gps_position(self):
        """Set GPS position manually"""
        console.print("\n[bold cyan]═══════════════ Set GPS Position ═══════════════[/bold cyan]\n")
        console.print("Set your node's fixed GPS position for the mesh network.\n")

        console.print("[cyan]Options:[/cyan]")
        console.print("  1. Set coordinates manually (latitude/longitude)")
        console.print("  2. Use GPS module (configure in config.yaml)")
        console.print("  0. Cancel")

        choice = Prompt.ask("\nSelect option", choices=["0", "1", "2"], default="0")

        if choice == "0":
            return

        if choice == "2":
            console.print("\n[cyan]GPS Module Configuration[/cyan]")
            console.print("To use a GPS module, add this to your config.yaml:\n")
            console.print("[dim]GPS:[/dim]")
            console.print("[dim]  SerialPath: /dev/serial0  # or /dev/ttyS0[/dim]")
            console.print("[dim]  # GPSEnableGpio: 4  # Optional: GPIO to enable GPS[/dim]")
            console.print("\n[yellow]Make sure UART is enabled in raspi-config[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        # Manual coordinate entry
        console.print("\n[yellow]Enter coordinates in decimal degrees[/yellow]")
        console.print("[dim]Find your coordinates:[/dim]")
        console.print("[dim]  - Google Maps: right-click → 'What's here?'[/dim]")
        console.print("[dim]  - GPS app on phone[/dim]")
        console.print("[dim]Example: Hawaii Big Island: 19.435175, -155.213842[/dim]\n")

        # Latitude
        while True:
            lat_str = Prompt.ask("Latitude (-90 to 90)", default="")
            if not lat_str:
                console.print("[yellow]Cancelled[/yellow]")
                return
            try:
                latitude = float(lat_str)
                if -90 <= latitude <= 90:
                    break
                console.print("[red]Latitude must be between -90 and 90[/red]")
            except ValueError:
                console.print("[red]Enter a valid number (e.g., 19.435175)[/red]")

        # Longitude
        while True:
            lon_str = Prompt.ask("Longitude (-180 to 180)", default="")
            if not lon_str:
                console.print("[yellow]Cancelled[/yellow]")
                return
            try:
                longitude = float(lon_str)
                if -180 <= longitude <= 180:
                    break
                console.print("[red]Longitude must be between -180 and 180[/red]")
            except ValueError:
                console.print("[red]Enter a valid number (e.g., -155.213842)[/red]")

        # Altitude (optional)
        altitude = None
        if Confirm.ask("\nSet altitude? (optional)", default=False):
            alt_str = Prompt.ask("Altitude in meters", default="0")
            try:
                altitude = int(float(alt_str))
            except ValueError:
                altitude = 0

        # Display summary
        console.print(f"\n[bold]Position Summary:[/bold]")
        console.print(f"  Latitude:  [green]{latitude}[/green]")
        console.print(f"  Longitude: [green]{longitude}[/green]")
        if altitude is not None:
            console.print(f"  Altitude:  [green]{altitude}m[/green]")

        # Show CLI command
        console.print(f"\n[dim]Meshtastic CLI command:[/dim]")
        cmd = f"meshtastic --host localhost --setlat {latitude} --setlon {longitude}"
        if altitude is not None:
            cmd += f" --setalt {altitude}"
        console.print(f"[cyan]{cmd}[/cyan]")

        if Confirm.ask("\n[yellow]Apply this position now?[/yellow]", default=True):
            self._apply_gps_position(latitude, longitude, altitude)
        else:
            console.print("\n[dim]You can apply manually with the command above.[/dim]")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _apply_gps_position(self, latitude, longitude, altitude=None):
        """Apply GPS position using meshtastic CLI"""
        console.print(f"\n[cyan]Setting position to {latitude}, {longitude}...[/cyan]")

        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                console.print("[red]meshtastic CLI not found[/red]")
                console.print("[dim]Install with: pipx install meshtastic[cli][/dim]")
                return False

            cmd = [cli_path, '--host', 'localhost', '--setlat', str(latitude), '--setlon', str(longitude)]
            if altitude is not None:
                cmd.extend(['--setalt', str(altitude)])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                console.print("[bold green]Position set successfully![/bold green]")
                if result.stdout:
                    console.print(f"[dim]{result.stdout.strip()}[/dim]")
                return True
            else:
                console.print(f"[red]Error setting position[/red]")
                if result.stderr:
                    console.print(f"[dim]{result.stderr.strip()}[/dim]")
                console.print("\n[yellow]Troubleshooting:[/yellow]")
                console.print("  - Is meshtasticd running? Check: systemctl status meshtasticd")
                console.print("  - Is meshtastic CLI installed? pipx install meshtastic[cli]")
                return False

        except FileNotFoundError:
            console.print("[red]meshtastic CLI not found[/red]")
            console.print("[dim]Install with: pipx install meshtastic[cli][/dim]")
            return False
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out (30s)[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return False
