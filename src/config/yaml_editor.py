"""Comprehensive Interactive YAML configuration editor for meshtasticd"""

import os
import yaml
import shutil
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

console = Console()

CONFIG_PATH = "/etc/meshtasticd/config.yaml"


class ConfigYamlEditor:
    """Interactive editor for meshtasticd config.yaml"""

    # LoRa Module Types
    LORA_MODULES = {
        "1": ("auto", "Auto-detect module type"),
        "2": ("sim", "Simulation mode"),
        "3": ("sx1262", "SX1262 (Waveshare, etc)"),
        "4": ("sx1268", "SX1268 (Ebyte E22, etc)"),
        "5": ("sx1280", "SX1280 (2.4GHz)"),
        "6": ("RF95", "RF95/RFM95 (Elecrow, Adafruit)"),
    }

    # Display Panel Types
    DISPLAY_PANELS = {
        "1": ("ILI9341", "Adafruit PiTFT 2.8", 240, 320),
        "2": ("ILI9486", "SHCHV 3.5 RPi TFT", 320, 480),
        "3": ("ST7789", "TZT 2.0 Inch ST7789", 320, 240),
    }

    # Touchscreen Modules
    TOUCHSCREEN_MODULES = {
        "1": ("STMPE610", "STMPE610 (Adafruit PiTFT option 1)"),
        "2": ("FT5x06", "FT5x06 (Adafruit PiTFT option 2)"),
    }

    def __init__(self, config_path=CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = {}
        self.modified = False
        self._return_to_main = False

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
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            if self.config_path.exists():
                backup = self.config_path.with_suffix('.yaml.bak')
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

    def interactive_menu(self):
        """Main interactive configuration menu"""
        self.load_config()
        self._return_to_main = False

        while True:
            if self._return_to_main:
                break

            console.print("\n[bold cyan]═══════════════ Config.yaml Editor ═══════════════[/bold cyan]\n")

            if self.modified:
                console.print("[yellow]* Unsaved changes[/yellow]\n")

            console.print("[dim cyan]── Radio & Hardware ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. LoRa Radio Configuration")
            console.print(f"  [bold]2[/bold]. GPS Configuration")
            console.print(f"  [bold]3[/bold]. I2C Settings")
            console.print(f"  [bold]4[/bold]. Display Configuration")
            console.print(f"  [bold]5[/bold]. Touchscreen Configuration")
            console.print(f"  [bold]6[/bold]. Input Devices (Keyboard/Trackball)")

            console.print("\n[dim cyan]── System Configuration ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. Logging Settings")
            console.print(f"  [bold]8[/bold]. Webserver Settings")
            console.print(f"  [bold]9[/bold]. Host Metrics")
            console.print(f"  [bold]c[/bold]. Config Settings")
            console.print(f"  [bold]g[/bold]. General Settings")

            console.print("\n[dim cyan]── Actions ──[/dim cyan]")
            console.print(f"  [bold]v[/bold]. View Current Config")
            console.print(f"  [bold]s[/bold]. Save Configuration")
            console.print(f"  [bold]r[/bold]. Reload from File")

            console.print(f"\n  [bold]0[/bold]. Back to Main Menu")

            valid_choices = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "g", "v", "s", "r"]
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=valid_choices, default="0")

            if choice == "1":
                self.edit_lora()
            elif choice == "2":
                self.edit_gps()
            elif choice == "3":
                self.edit_i2c()
            elif choice == "4":
                self.edit_display()
            elif choice == "5":
                self.edit_touchscreen()
            elif choice == "6":
                self.edit_input()
            elif choice == "7":
                self.edit_logging()
            elif choice == "8":
                self.edit_webserver()
            elif choice == "9":
                self.edit_host_metrics()
            elif choice == "c":
                self.edit_config_section()
            elif choice == "g":
                self.edit_general()
            elif choice == "v":
                self.view_config()
            elif choice == "s":
                self.save_config()
            elif choice == "r":
                self.load_config()
                console.print("[green]Configuration reloaded[/green]")
            elif choice == "0":
                if self.modified:
                    if Confirm.ask("[yellow]You have unsaved changes. Save before exiting?[/yellow]"):
                        self.save_config()
                break

    def edit_lora(self):
        """Edit LoRa radio configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ LoRa Radio Configuration ═══════════════[/bold cyan]\n")

            if 'Lora' not in self.config:
                self.config['Lora'] = {}
            lora = self.config['Lora']

            # Show current settings
            console.print("[dim]Current Settings:[/dim]")
            console.print(f"  Module: {lora.get('Module', 'Not set')}")
            console.print(f"  CS: {lora.get('CS', 'Not set')}, IRQ: {lora.get('IRQ', 'Not set')}")
            console.print(f"  Busy: {lora.get('Busy', 'Not set')}, Reset: {lora.get('Reset', 'Not set')}")

            console.print("\n[dim cyan]── Options ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. Set Module Type")
            console.print(f"  [bold]2[/bold]. Set GPIO Pins (CS, IRQ, Busy, Reset)")
            console.print(f"  [bold]3[/bold]. Set DIO3 TCXO Voltage")
            console.print(f"  [bold]4[/bold]. Set DIO2 as RF Switch")
            console.print(f"  [bold]5[/bold]. Set TX/RX Enable Pins")
            console.print(f"  [bold]6[/bold]. Set SPI Device & Speed")
            console.print(f"  [bold]7[/bold]. Set GPIO Chip (Raspberry Pi 5)")
            console.print(f"  [bold]8[/bold]. Apply Hardware Preset")

            choices = self._prompt_back(["1", "2", "3", "4", "5", "6", "7", "8"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self._set_lora_module(lora)
            elif choice == "2":
                self._set_lora_pins(lora)
            elif choice == "3":
                self._set_dio3_tcxo(lora)
            elif choice == "4":
                self._set_dio2_rf_switch(lora)
            elif choice == "5":
                self._set_txrx_enable(lora)
            elif choice == "6":
                self._set_spi_settings(lora)
            elif choice == "7":
                self._set_gpio_chip(lora)
            elif choice == "8":
                self._apply_lora_preset(lora)

    def _set_lora_module(self, lora):
        """Set LoRa module type"""
        console.print("\n[bold]Select LoRa Module Type:[/bold]\n")
        for key, (module, desc) in self.LORA_MODULES.items():
            current = " [green]<-- current[/green]" if lora.get('Module') == module else ""
            console.print(f"  [bold]{key}[/bold]. {module} - {desc}{current}")

        choice = Prompt.ask("\n[cyan]Select module[/cyan]",
                           choices=list(self.LORA_MODULES.keys()) + ["0"],
                           default="0")
        if choice != "0":
            lora['Module'] = self.LORA_MODULES[choice][0]
            self.modified = True
            console.print(f"[green]Module set to {lora['Module']}[/green]")

    def _set_lora_pins(self, lora):
        """Set LoRa GPIO pins"""
        console.print("\n[bold]GPIO Pin Configuration[/bold]\n")
        console.print("[dim]Common configurations:[/dim]")
        console.print("  MeshAdv-Mini: CS=8, IRQ=16, Busy=20, Reset=24")
        console.print("  Waveshare:    CS=21, IRQ=16, Busy=20, Reset=18")
        console.print("  SX1280:       CS=21, IRQ=16, Busy=20, Reset=18")
        console.print("  Elecrow RFM95: CS=7, IRQ=25, Reset=22\n")

        current = lora.get('CS', 21)
        new_val = IntPrompt.ask("CS (Chip Select) GPIO", default=current)
        if new_val != current:
            lora['CS'] = new_val
            self.modified = True

        current = lora.get('IRQ', 16)
        new_val = IntPrompt.ask("IRQ GPIO", default=current)
        if new_val != current:
            lora['IRQ'] = new_val
            self.modified = True

        current = lora.get('Busy', 20)
        new_val = IntPrompt.ask("Busy GPIO", default=current)
        if new_val != current:
            lora['Busy'] = new_val
            self.modified = True

        current = lora.get('Reset', 18)
        new_val = IntPrompt.ask("Reset GPIO", default=current)
        if new_val != current:
            lora['Reset'] = new_val
            self.modified = True

        console.print("\n[green]GPIO pins updated[/green]")

    def _set_dio3_tcxo(self, lora):
        """Set DIO3 TCXO voltage"""
        console.print("\n[bold]DIO3 TCXO Voltage[/bold]")
        console.print("[dim]Required for Waveshare Core1262 and some other modules[/dim]\n")

        if Confirm.ask("Enable DIO3 TCXO Voltage?", default=lora.get('DIO3_TCXO_VOLTAGE', False)):
            console.print("\n[dim]Options: true, 1.6, 1.7, 1.8, 2.2, 2.4, 2.7, 3.0, 3.3[/dim]")
            value = Prompt.ask("Value", default="1.8")
            if value.lower() == "true":
                lora['DIO3_TCXO_VOLTAGE'] = True
            else:
                try:
                    lora['DIO3_TCXO_VOLTAGE'] = float(value)
                except ValueError:
                    lora['DIO3_TCXO_VOLTAGE'] = True
            self.modified = True
            console.print("[green]DIO3 TCXO enabled[/green]")
        elif 'DIO3_TCXO_VOLTAGE' in lora:
            del lora['DIO3_TCXO_VOLTAGE']
            self.modified = True
            console.print("[yellow]DIO3 TCXO disabled[/yellow]")

    def _set_dio2_rf_switch(self, lora):
        """Set DIO2 as RF switch"""
        console.print("\n[bold]DIO2 as RF Switch[/bold]")
        console.print("[dim]Used by some modules like Ebyte E22[/dim]\n")

        if Confirm.ask("Enable DIO2 as RF Switch?", default=lora.get('DIO2_AS_RF_SWITCH', False)):
            lora['DIO2_AS_RF_SWITCH'] = True
            self.modified = True
            console.print("[green]DIO2 RF Switch enabled[/green]")
        elif 'DIO2_AS_RF_SWITCH' in lora:
            del lora['DIO2_AS_RF_SWITCH']
            self.modified = True

    def _set_txrx_enable(self, lora):
        """Set TX/RX enable pins"""
        console.print("\n[bold]TX/RX Enable Pins[/bold]")
        console.print("[dim]For external PA/LNA control[/dim]\n")

        if Confirm.ask("Configure TX/RX Enable pins?", default=False):
            tx = Prompt.ask("TXen GPIO (or 'nc' for not connected)", default="nc")
            if tx.lower() != "nc":
                lora['TXen'] = int(tx)
            else:
                lora['TXen'] = "RADIOLIB_NC"

            rx = Prompt.ask("RXen GPIO", default="")
            if rx:
                lora['RXen'] = int(rx)

            self.modified = True
            console.print("[green]TX/RX pins configured[/green]")

    def _set_spi_settings(self, lora):
        """Set SPI device and speed"""
        console.print("\n[bold]SPI Settings[/bold]\n")

        current = lora.get('spidev', 'spidev0.0')
        new_val = Prompt.ask("SPI Device", default=current)
        if new_val != current:
            lora['spidev'] = new_val
            self.modified = True

        current = lora.get('spiSpeed', 2000000)
        new_val = IntPrompt.ask("SPI Speed (Hz)", default=current)
        if new_val != current:
            lora['spiSpeed'] = new_val
            self.modified = True

        console.print("[green]SPI settings updated[/green]")

    def _set_gpio_chip(self, lora):
        """Set GPIO chip for Raspberry Pi 5"""
        console.print("\n[bold]GPIO Chip[/bold]")
        console.print("[dim]Raspberry Pi 5 uses gpiochip4 for GPIO header[/dim]\n")

        current = lora.get('gpiochip', 0)
        new_val = IntPrompt.ask("GPIO Chip number", default=current)
        if new_val != current:
            lora['gpiochip'] = new_val
            self.modified = True
            console.print("[green]GPIO chip set[/green]")

    def _apply_lora_preset(self, lora):
        """Apply a hardware preset"""
        console.print("\n[bold]Hardware Presets[/bold]\n")
        console.print("  [bold]1[/bold]. MeshAdv-Mini (SX1262/SX1268)")
        console.print("  [bold]2[/bold]. Waveshare SX1262")
        console.print("  [bold]3[/bold]. Elecrow RFM95")
        console.print("  [bold]4[/bold]. SX1280 (2.4GHz)")
        console.print("  [bold]5[/bold]. Ebyte E22-900M30S")

        choice = Prompt.ask("\n[cyan]Select preset[/cyan]", choices=["1", "2", "3", "4", "5", "0"], default="0")

        presets = {
            "1": {"Module": "sx1262", "CS": 8, "IRQ": 16, "Busy": 20, "Reset": 24},
            "2": {"Module": "sx1262", "CS": 21, "IRQ": 16, "Busy": 20, "Reset": 18, "DIO3_TCXO_VOLTAGE": 1.8},
            "3": {"Module": "RF95", "CS": 7, "IRQ": 25, "Reset": 22},
            "4": {"Module": "sx1280", "CS": 21, "IRQ": 16, "Busy": 20, "Reset": 18},
            "5": {"Module": "sx1262", "CS": 21, "IRQ": 16, "Busy": 20, "Reset": 18,
                  "DIO2_AS_RF_SWITCH": True, "DIO3_TCXO_VOLTAGE": 1.8},
        }

        if choice in presets:
            lora.update(presets[choice])
            self.modified = True
            console.print(f"[green]Applied preset: {presets[choice]}[/green]")

    def edit_gps(self):
        """Edit GPS configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ GPS Configuration ═══════════════[/bold cyan]\n")

            if 'GPS' not in self.config:
                self.config['GPS'] = {}
            gps = self.config['GPS']

            console.print(f"[dim]Current: SerialPath = {gps.get('SerialPath', 'Not set')}[/dim]\n")

            console.print("  [bold]1[/bold]. Enable GPS with Serial")
            console.print("  [bold]2[/bold]. Disable GPS")

            choices = self._prompt_back(["1", "2"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                serial = Prompt.ask("GPS Serial Path", default=gps.get('SerialPath', '/dev/ttyS0'))
                gps['SerialPath'] = serial
                self.modified = True
                console.print(f"[green]GPS enabled on {serial}[/green]")
            elif choice == "2":
                self.config['GPS'] = {}
                self.modified = True
                console.print("[yellow]GPS disabled[/yellow]")

    def edit_i2c(self):
        """Edit I2C configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ I2C Configuration ═══════════════[/bold cyan]\n")

            if 'I2C' not in self.config:
                self.config['I2C'] = {}
            i2c = self.config['I2C']

            console.print(f"[dim]Current: I2CDevice = {i2c.get('I2CDevice', 'Not set')}[/dim]\n")

            console.print("  [bold]1[/bold]. Enable I2C")
            console.print("  [bold]2[/bold]. Disable I2C")

            choices = self._prompt_back(["1", "2"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                device = Prompt.ask("I2C Device", default=i2c.get('I2CDevice', '/dev/i2c-1'))
                i2c['I2CDevice'] = device
                self.modified = True
                console.print(f"[green]I2C enabled on {device}[/green]")
            elif choice == "2":
                self.config['I2C'] = {}
                self.modified = True
                console.print("[yellow]I2C disabled[/yellow]")

    def edit_display(self):
        """Edit display configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Display Configuration ═══════════════[/bold cyan]\n")

            if 'Display' not in self.config:
                self.config['Display'] = {}
            display = self.config['Display']

            console.print("[dim]Note: I2C displays are auto-detected. Configure SPI displays here.[/dim]\n")
            console.print(f"[dim]Current: Panel = {display.get('Panel', 'None/Auto')}[/dim]\n")

            console.print("  [bold]1[/bold]. Select Display Panel")
            console.print("  [bold]2[/bold]. Configure Display Pins")
            console.print("  [bold]3[/bold]. Configure Display Settings")
            console.print("  [bold]4[/bold]. Disable/Clear Display Config")

            choices = self._prompt_back(["1", "2", "3", "4"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self._select_display_panel(display)
            elif choice == "2":
                self._configure_display_pins(display)
            elif choice == "3":
                self._configure_display_settings(display)
            elif choice == "4":
                self.config['Display'] = {}
                self.modified = True
                console.print("[yellow]Display config cleared (using auto-detect)[/yellow]")

    def _select_display_panel(self, display):
        """Select display panel type"""
        console.print("\n[bold]Select Display Panel:[/bold]\n")
        for key, (panel, desc, w, h) in self.DISPLAY_PANELS.items():
            current = " [green]<-- current[/green]" if display.get('Panel') == panel else ""
            console.print(f"  [bold]{key}[/bold]. {panel} - {desc} ({w}x{h}){current}")

        choice = Prompt.ask("\n[cyan]Select panel[/cyan]",
                           choices=list(self.DISPLAY_PANELS.keys()) + ["0"],
                           default="0")
        if choice != "0":
            panel, desc, w, h = self.DISPLAY_PANELS[choice]
            display['Panel'] = panel
            display['Width'] = w
            display['Height'] = h
            self.modified = True
            console.print(f"[green]Panel set to {panel}[/green]")

    def _configure_display_pins(self, display):
        """Configure display GPIO pins"""
        console.print("\n[bold]Display Pin Configuration[/bold]\n")

        display['CS'] = IntPrompt.ask("CS GPIO", default=display.get('CS', 8))
        display['DC'] = IntPrompt.ask("DC GPIO", default=display.get('DC', 25))

        if Confirm.ask("Configure Reset pin?", default='Reset' in display):
            display['Reset'] = IntPrompt.ask("Reset GPIO", default=display.get('Reset', 25))

        if Confirm.ask("Configure SPI device?", default='spidev' in display):
            display['spidev'] = Prompt.ask("SPI device", default=display.get('spidev', 'spidev0.0'))

        self.modified = True
        console.print("[green]Display pins configured[/green]")

    def _configure_display_settings(self, display):
        """Configure display settings"""
        console.print("\n[bold]Display Settings[/bold]\n")

        display['Width'] = IntPrompt.ask("Width", default=display.get('Width', 320))
        display['Height'] = IntPrompt.ask("Height", default=display.get('Height', 240))

        if Confirm.ask("Enable rotation?", default=display.get('Rotate', False)):
            display['Rotate'] = True
            display['OffsetRotate'] = IntPrompt.ask("Offset Rotate (0-3)", default=display.get('OffsetRotate', 0))
        elif 'Rotate' in display:
            del display['Rotate']

        if Confirm.ask("Invert colors?", default=display.get('Invert', False)):
            display['Invert'] = True
        elif 'Invert' in display:
            del display['Invert']

        if Confirm.ask("Set bus frequency?", default='BusFrequency' in display):
            display['BusFrequency'] = IntPrompt.ask("Bus Frequency (Hz)",
                                                    default=display.get('BusFrequency', 30000000))

        self.modified = True
        console.print("[green]Display settings updated[/green]")

    def edit_touchscreen(self):
        """Edit touchscreen configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Touchscreen Configuration ═══════════════[/bold cyan]\n")

            if 'Touchscreen' not in self.config:
                self.config['Touchscreen'] = {}
            ts = self.config['Touchscreen']

            console.print(f"[dim]Current: Module = {ts.get('Module', 'Not configured')}[/dim]\n")

            console.print("  [bold]1[/bold]. Configure STMPE610 (SPI)")
            console.print("  [bold]2[/bold]. Configure FT5x06 (I2C)")
            console.print("  [bold]3[/bold]. Disable Touchscreen")

            choices = self._prompt_back(["1", "2", "3"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                ts['Module'] = 'STMPE610'
                ts['CS'] = IntPrompt.ask("CS GPIO", default=ts.get('CS', 7))
                ts['IRQ'] = IntPrompt.ask("IRQ GPIO", default=ts.get('IRQ', 24))
                self.modified = True
                console.print("[green]STMPE610 touchscreen configured[/green]")
            elif choice == "2":
                ts['Module'] = 'FT5x06'
                ts['IRQ'] = IntPrompt.ask("IRQ GPIO", default=ts.get('IRQ', 24))
                ts['I2CAddr'] = Prompt.ask("I2C Address", default=ts.get('I2CAddr', '0x38'))
                self.modified = True
                console.print("[green]FT5x06 touchscreen configured[/green]")
            elif choice == "3":
                self.config['Touchscreen'] = {}
                self.modified = True
                console.print("[yellow]Touchscreen disabled[/yellow]")

    def edit_input(self):
        """Edit input device configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Input Configuration ═══════════════[/bold cyan]\n")

            if 'Input' not in self.config:
                self.config['Input'] = {}
            inp = self.config['Input']

            console.print("[dim]Current settings:[/dim]")
            console.print(f"  Keyboard: {inp.get('KeyboardDevice', 'Not set')}")
            console.print(f"  User Button: {inp.get('UserButton', 'Not set')}")
            console.print("")

            console.print("  [bold]1[/bold]. Configure Keyboard Device")
            console.print("  [bold]2[/bold]. Configure User Button")
            console.print("  [bold]3[/bold]. Configure Trackball/Joystick")
            console.print("  [bold]4[/bold]. Clear Input Config")

            choices = self._prompt_back(["1", "2", "3", "4"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                path = Prompt.ask("Keyboard Device Path",
                                 default=inp.get('KeyboardDevice',
                                                '/dev/input/by-id/usb-_Raspberry_Pi_Internal_Keyboard-event-kbd'))
                inp['KeyboardDevice'] = path
                self.modified = True
            elif choice == "2":
                inp['UserButton'] = IntPrompt.ask("User Button GPIO", default=inp.get('UserButton', 6))
                self.modified = True
            elif choice == "3":
                inp['TrackballUp'] = IntPrompt.ask("Up GPIO", default=inp.get('TrackballUp', 6))
                inp['TrackballDown'] = IntPrompt.ask("Down GPIO", default=inp.get('TrackballDown', 19))
                inp['TrackballLeft'] = IntPrompt.ask("Left GPIO", default=inp.get('TrackballLeft', 5))
                inp['TrackballRight'] = IntPrompt.ask("Right GPIO", default=inp.get('TrackballRight', 26))
                inp['TrackballPress'] = IntPrompt.ask("Press GPIO", default=inp.get('TrackballPress', 13))
                self.modified = True
            elif choice == "4":
                self.config['Input'] = {}
                self.modified = True
                console.print("[yellow]Input config cleared[/yellow]")

    def edit_logging(self):
        """Edit logging configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Logging Configuration ═══════════════[/bold cyan]\n")

            if 'Logging' not in self.config:
                self.config['Logging'] = {}
            logging = self.config['Logging']

            console.print(f"[dim]Current: LogLevel = {logging.get('LogLevel', 'info')}[/dim]\n")

            console.print("  [bold]1[/bold]. Set Log Level")
            console.print("  [bold]2[/bold]. Configure Trace File")
            console.print("  [bold]3[/bold]. Configure JSON Packet Logging")
            console.print("  [bold]4[/bold]. Configure ASCII Logs")

            choices = self._prompt_back(["1", "2", "3", "4"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                level = Prompt.ask("Log Level", default=logging.get('LogLevel', 'info'),
                                  choices=["debug", "info", "warn", "error"])
                logging['LogLevel'] = level
                self.modified = True
            elif choice == "2":
                if Confirm.ask("Enable Trace File?", default='TraceFile' in logging):
                    logging['TraceFile'] = Prompt.ask("Trace File Path",
                                                      default=logging.get('TraceFile', '/var/log/meshtasticd.json'))
                elif 'TraceFile' in logging:
                    del logging['TraceFile']
                self.modified = True
            elif choice == "3":
                if Confirm.ask("Enable JSON Packet Logging?", default='JSONFile' in logging):
                    logging['JSONFile'] = Prompt.ask("JSON File Path",
                                                     default=logging.get('JSONFile', '/packets.json'))
                    logging['JSONFilter'] = Prompt.ask("JSON Filter (e.g., position)",
                                                       default=logging.get('JSONFilter', 'position'))
                else:
                    logging.pop('JSONFile', None)
                    logging.pop('JSONFilter', None)
                self.modified = True
            elif choice == "4":
                if Confirm.ask("Enable ASCII Logs?", default=logging.get('AsciiLogs', False)):
                    logging['AsciiLogs'] = True
                elif 'AsciiLogs' in logging:
                    del logging['AsciiLogs']
                self.modified = True

    def edit_webserver(self):
        """Edit webserver configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Webserver Configuration ═══════════════[/bold cyan]\n")

            if 'Webserver' not in self.config:
                self.config['Webserver'] = {}
            web = self.config['Webserver']

            console.print(f"[dim]Current: Port = {web.get('Port', 9443)}, RootPath = {web.get('RootPath', '/usr/share/meshtasticd/web')}[/dim]\n")

            console.print("  [bold]1[/bold]. Set Port")
            console.print("  [bold]2[/bold]. Set Root Path")
            console.print("  [bold]3[/bold]. Configure SSL Certificates")

            choices = self._prompt_back(["1", "2", "3"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                web['Port'] = IntPrompt.ask("HTTPS Port", default=web.get('Port', 9443))
                self.modified = True
            elif choice == "2":
                web['RootPath'] = Prompt.ask("Web Root Path",
                                             default=web.get('RootPath', '/usr/share/meshtasticd/web'))
                self.modified = True
            elif choice == "3":
                if Confirm.ask("Configure custom SSL certificates?", default='SSLKey' in web):
                    web['SSLKey'] = Prompt.ask("SSL Key Path",
                                               default=web.get('SSLKey', '/etc/meshtasticd/ssl/private_key.pem'))
                    web['SSLCert'] = Prompt.ask("SSL Cert Path",
                                                default=web.get('SSLCert', '/etc/meshtasticd/ssl/certificate.pem'))
                else:
                    web.pop('SSLKey', None)
                    web.pop('SSLCert', None)
                self.modified = True

    def edit_host_metrics(self):
        """Edit host metrics configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Host Metrics Configuration ═══════════════[/bold cyan]\n")

            if 'HostMetrics' not in self.config:
                self.config['HostMetrics'] = {}
            hm = self.config['HostMetrics']

            console.print(f"[dim]Current: ReportInterval = {hm.get('ReportInterval', 'Disabled')} min[/dim]\n")

            console.print("  [bold]1[/bold]. Set Report Interval")
            console.print("  [bold]2[/bold]. Set Channel")
            console.print("  [bold]3[/bold]. Set User String Command")
            console.print("  [bold]4[/bold]. Disable Host Metrics")

            choices = self._prompt_back(["1", "2", "3", "4"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                hm['ReportInterval'] = IntPrompt.ask("Report Interval (minutes, 0=disabled)",
                                                     default=hm.get('ReportInterval', 30))
                self.modified = True
            elif choice == "2":
                hm['Channel'] = IntPrompt.ask("Channel index", default=hm.get('Channel', 0))
                self.modified = True
            elif choice == "3":
                hm['UserStringCommand'] = Prompt.ask(
                    "Command to execute",
                    default=hm.get('UserStringCommand', 'cat /sys/firmware/devicetree/base/serial-number'))
                self.modified = True
            elif choice == "4":
                self.config['HostMetrics'] = {}
                self.modified = True

    def edit_config_section(self):
        """Edit Config section (display mode)"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Config Settings ═══════════════[/bold cyan]\n")

            if 'Config' not in self.config:
                self.config['Config'] = {}
            cfg = self.config['Config']

            console.print(f"[dim]Current: DisplayMode = {cfg.get('DisplayMode', 'Auto')}[/dim]\n")

            console.print("  [bold]1[/bold]. Set Display Mode to TWOCOLOR (BaseUI)")
            console.print("  [bold]2[/bold]. Set Display Mode to COLOR (MUI)")
            console.print("  [bold]3[/bold]. Clear (Auto-detect)")

            choices = self._prompt_back(["1", "2", "3"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                cfg['DisplayMode'] = 'TWOCOLOR'
                self.modified = True
            elif choice == "2":
                cfg['DisplayMode'] = 'COLOR'
                self.modified = True
            elif choice == "3":
                self.config['Config'] = {}
                self.modified = True

    def edit_general(self):
        """Edit general configuration"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ General Configuration ═══════════════[/bold cyan]\n")

            if 'General' not in self.config:
                self.config['General'] = {}
            general = self.config['General']

            console.print("[dim]Current settings:[/dim]")
            console.print(f"  MaxNodes: {general.get('MaxNodes', 200)}")
            console.print(f"  MaxMessageQueue: {general.get('MaxMessageQueue', 100)}")
            console.print(f"  ConfigDirectory: {general.get('ConfigDirectory', '/etc/meshtasticd/config.d/')}")
            console.print("")

            console.print("  [bold]1[/bold]. Set Max Nodes")
            console.print("  [bold]2[/bold]. Set Max Message Queue")
            console.print("  [bold]3[/bold]. Set Config Directory")
            console.print("  [bold]4[/bold]. Set Available Directory")
            console.print("  [bold]5[/bold]. Set MAC Address")

            choices = self._prompt_back(["1", "2", "3", "4", "5"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                general['MaxNodes'] = IntPrompt.ask("Max Nodes", default=general.get('MaxNodes', 200))
                self.modified = True
            elif choice == "2":
                general['MaxMessageQueue'] = IntPrompt.ask("Max Message Queue",
                                                           default=general.get('MaxMessageQueue', 100))
                self.modified = True
            elif choice == "3":
                general['ConfigDirectory'] = Prompt.ask("Config Directory",
                                                        default=general.get('ConfigDirectory', '/etc/meshtasticd/config.d/'))
                self.modified = True
            elif choice == "4":
                general['AvailableDirectory'] = Prompt.ask("Available Directory",
                                                           default=general.get('AvailableDirectory', '/etc/meshtasticd/available.d/'))
                self.modified = True
            elif choice == "5":
                console.print("\n[dim]Set MAC directly or from network interface[/dim]")
                if Confirm.ask("Set MAC from network interface?", default=False):
                    general['MACAddressSource'] = Prompt.ask("Interface", default="eth0")
                    general.pop('MACAddress', None)
                else:
                    general['MACAddress'] = Prompt.ask("MAC Address",
                                                       default=general.get('MACAddress', 'AA:BB:CC:DD:EE:FF'))
                    general.pop('MACAddressSource', None)
                self.modified = True

    def view_config(self):
        """View current configuration"""
        console.print("\n[bold cyan]Current Configuration[/bold cyan]\n")

        if not self.config:
            console.print("[yellow]No configuration loaded[/yellow]")
            return

        yaml_str = yaml.dump(self.config, default_flow_style=False, sort_keys=False)
        console.print(Panel(yaml_str, title=f"[cyan]{self.config_path}[/cyan]", border_style="cyan"))

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")
