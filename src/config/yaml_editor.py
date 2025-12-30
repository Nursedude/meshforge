"""Interactive YAML configuration editor for meshtasticd"""

import os
import yaml
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

console = Console()

CONFIG_PATH = "/etc/meshtasticd/config.yaml"


class ConfigYamlEditor:
    """Interactive editor for meshtasticd config.yaml"""

    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = {}
        self.modified = False

    def load_config(self):
        """Load existing configuration"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    self.config = yaml.safe_load(f) or {}
                return True
            except Exception as e:
                console.print(f"[red]Error loading config: {e}[/red]")
                return False
        else:
            console.print("[yellow]No existing config found, starting fresh[/yellow]")
            self.config = {}
            return True

    def save_config(self):
        """Save configuration to file"""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Backup existing
            if self.config_path.exists():
                backup = self.config_path.with_suffix('.yaml.bak')
                import shutil
                shutil.copy2(self.config_path, backup)
                console.print(f"[dim]Backed up to {backup}[/dim]")

            with open(self.config_path, 'w') as f:
                yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)

            console.print(f"[green]Configuration saved to {self.config_path}[/green]")
            self.modified = False
            return True
        except PermissionError:
            console.print("[red]Permission denied. Run with sudo.[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Error saving config: {e}[/red]")
            return False

    def interactive_menu(self):
        """Main interactive configuration menu"""
        self.load_config()

        while True:
            console.print("\n[bold cyan]═══════════════ Config.yaml Editor ═══════════════[/bold cyan]\n")

            if self.modified:
                console.print("[yellow]* Unsaved changes[/yellow]\n")

            console.print("[dim cyan]── Hardware Configuration ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. LoRa Radio (CS, IRQ, Busy, Reset pins)")
            console.print(f"  [bold]2[/bold]. GPS Configuration")
            console.print(f"  [bold]3[/bold]. I2C Settings")
            console.print(f"  [bold]4[/bold]. Display Configuration")

            console.print("\n[dim cyan]── System Configuration ──[/dim cyan]")
            console.print(f"  [bold]5[/bold]. Logging Settings")
            console.print(f"  [bold]6[/bold]. Webserver Settings")
            console.print(f"  [bold]7[/bold]. General Settings")

            console.print("\n[dim cyan]── Actions ──[/dim cyan]")
            console.print(f"  [bold]8[/bold]. View Current Config")
            console.print(f"  [bold]9[/bold]. Save Configuration")

            console.print(f"\n  [bold]0[/bold]. Back to Main Menu")

            choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                              choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                              default="0")

            if choice == "1":
                self.edit_lora()
            elif choice == "2":
                self.edit_gps()
            elif choice == "3":
                self.edit_i2c()
            elif choice == "4":
                self.edit_display()
            elif choice == "5":
                self.edit_logging()
            elif choice == "6":
                self.edit_webserver()
            elif choice == "7":
                self.edit_general()
            elif choice == "8":
                self.view_config()
            elif choice == "9":
                self.save_config()
            elif choice == "0":
                if self.modified:
                    if Confirm.ask("[yellow]You have unsaved changes. Save before exiting?[/yellow]"):
                        self.save_config()
                break

    def edit_lora(self):
        """Edit LoRa radio configuration"""
        console.print("\n[bold cyan]LoRa Radio Configuration[/bold cyan]\n")

        if 'Lora' not in self.config:
            self.config['Lora'] = {}

        lora = self.config['Lora']

        # Common pin configurations
        console.print("[dim]Common configurations:[/dim]")
        console.print("  MeshAdv-Mini: CS=8, IRQ=16, Busy=20, Reset=24")
        console.print("  Waveshare:    CS=21, IRQ=16, Busy=20, Reset=18")
        console.print("")

        # CS Pin
        current = lora.get('CS', 21)
        new_val = IntPrompt.ask("CS (Chip Select) GPIO", default=current)
        if new_val != current:
            lora['CS'] = new_val
            self.modified = True

        # IRQ Pin
        current = lora.get('IRQ', 16)
        new_val = IntPrompt.ask("IRQ GPIO", default=current)
        if new_val != current:
            lora['IRQ'] = new_val
            self.modified = True

        # Busy Pin
        current = lora.get('Busy', 20)
        new_val = IntPrompt.ask("Busy GPIO", default=current)
        if new_val != current:
            lora['Busy'] = new_val
            self.modified = True

        # Reset Pin
        current = lora.get('Reset', 18)
        new_val = IntPrompt.ask("Reset GPIO", default=current)
        if new_val != current:
            lora['Reset'] = new_val
            self.modified = True

        # DIO3 TCXO
        if Confirm.ask("\nEnable DIO3 TCXO Voltage? (for Waveshare/some SX126x)", default=False):
            lora['DIO3_TCXO_VOLTAGE'] = True
            self.modified = True

        console.print("\n[green]LoRa configuration updated[/green]")

    def edit_gps(self):
        """Edit GPS configuration"""
        console.print("\n[bold cyan]GPS Configuration[/bold cyan]\n")

        if 'GPS' not in self.config:
            self.config['GPS'] = {}

        gps = self.config['GPS']

        if Confirm.ask("Enable GPS?", default=bool(gps)):
            current = gps.get('SerialPath', '/dev/ttyS0')
            new_val = Prompt.ask("GPS Serial Port", default=current)
            if new_val:
                gps['SerialPath'] = new_val
                self.modified = True
            console.print("[green]GPS configuration updated[/green]")
        else:
            if 'GPS' in self.config:
                del self.config['GPS']
                self.modified = True
            console.print("[yellow]GPS disabled[/yellow]")

    def edit_i2c(self):
        """Edit I2C configuration"""
        console.print("\n[bold cyan]I2C Configuration[/bold cyan]\n")

        if 'I2C' not in self.config:
            self.config['I2C'] = {}

        i2c = self.config['I2C']

        if Confirm.ask("Enable I2C? (for sensors, displays)", default=bool(i2c)):
            current = i2c.get('I2CDevice', '/dev/i2c-1')
            new_val = Prompt.ask("I2C Device", default=current)
            if new_val:
                i2c['I2CDevice'] = new_val
                self.modified = True
            console.print("[green]I2C configuration updated[/green]")
        else:
            if 'I2C' in self.config:
                del self.config['I2C']
                self.modified = True
            console.print("[yellow]I2C disabled[/yellow]")

    def edit_display(self):
        """Edit display configuration"""
        console.print("\n[bold cyan]Display Configuration[/bold cyan]\n")
        console.print("[dim]Note: I2C displays are auto-detected. SPI displays need configuration.[/dim]\n")

        if 'Display' not in self.config:
            self.config['Display'] = {}

        display = self.config['Display']

        console.print("Display panels:")
        console.print("  1. ILI9341 (Adafruit PiTFT 2.8)")
        console.print("  2. ILI9486 (SHCHV 3.5 TFT)")
        console.print("  3. ST7789 (TZT 2.0 inch)")
        console.print("  4. None / I2C auto-detect")

        choice = Prompt.ask("Select display type", choices=["1", "2", "3", "4"], default="4")

        if choice == "4":
            # Clear display config for auto-detect
            self.config['Display'] = {}
            console.print("[green]Using I2C auto-detect[/green]")
        else:
            panels = {"1": "ILI9341", "2": "ILI9486", "3": "ST7789"}
            display['Panel'] = panels[choice]
            display['CS'] = IntPrompt.ask("Display CS GPIO", default=8)
            display['DC'] = IntPrompt.ask("Display DC GPIO", default=24)
            display['Width'] = IntPrompt.ask("Width", default=320)
            display['Height'] = IntPrompt.ask("Height", default=240)
            self.modified = True
            console.print("[green]Display configuration updated[/green]")

    def edit_logging(self):
        """Edit logging configuration"""
        console.print("\n[bold cyan]Logging Configuration[/bold cyan]\n")

        if 'Logging' not in self.config:
            self.config['Logging'] = {}

        logging = self.config['Logging']

        console.print("Log levels: debug, info, warn, error")
        current = logging.get('LogLevel', 'info')
        new_val = Prompt.ask("Log Level", default=current,
                            choices=["debug", "info", "warn", "error"])
        if new_val != current:
            logging['LogLevel'] = new_val
            self.modified = True

        console.print("[green]Logging configuration updated[/green]")

    def edit_webserver(self):
        """Edit webserver configuration"""
        console.print("\n[bold cyan]Webserver Configuration[/bold cyan]\n")

        if 'Webserver' not in self.config:
            self.config['Webserver'] = {}

        web = self.config['Webserver']

        current = web.get('Port', 9443)
        new_val = IntPrompt.ask("HTTPS Port", default=current)
        if new_val != current:
            web['Port'] = new_val
            self.modified = True

        current = web.get('RootPath', '/usr/share/meshtasticd/web')
        new_val = Prompt.ask("Web Root Path", default=current)
        if new_val != current:
            web['RootPath'] = new_val
            self.modified = True

        console.print("[green]Webserver configuration updated[/green]")

    def edit_general(self):
        """Edit general configuration"""
        console.print("\n[bold cyan]General Configuration[/bold cyan]\n")

        if 'General' not in self.config:
            self.config['General'] = {}

        general = self.config['General']

        current = general.get('MaxNodes', 200)
        new_val = IntPrompt.ask("Max Nodes", default=current)
        if new_val != current:
            general['MaxNodes'] = new_val
            self.modified = True

        current = general.get('MaxMessageQueue', 100)
        new_val = IntPrompt.ask("Max Message Queue", default=current)
        if new_val != current:
            general['MaxMessageQueue'] = new_val
            self.modified = True

        current = general.get('ConfigDirectory', '/etc/meshtasticd/config.d/')
        new_val = Prompt.ask("Config Directory", default=current)
        if new_val != current:
            general['ConfigDirectory'] = new_val
            self.modified = True

        console.print("[green]General configuration updated[/green]")

    def view_config(self):
        """View current configuration"""
        console.print("\n[bold cyan]Current Configuration[/bold cyan]\n")

        if not self.config:
            console.print("[yellow]No configuration loaded[/yellow]")
            return

        # Convert to YAML for display
        yaml_str = yaml.dump(self.config, default_flow_style=False, sort_keys=False)
        console.print(Panel(yaml_str, title=f"[cyan]{self.config_path}[/cyan]", border_style="cyan"))

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")
