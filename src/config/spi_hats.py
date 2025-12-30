"""SPI HAT configuration module for LoRa devices"""

import os
import subprocess
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

from config.hardware import HardwareDetector
from utils.logger import log

console = Console()

# Path for auto-resume after reboot
RESUME_FLAG_FILE = "/tmp/meshtasticd-installer-resume"
AUTOSTART_SERVICE = "/etc/systemd/system/meshtasticd-installer-resume.service"


class SPIHatConfigurator:
    """Configure SPI-based LoRa HATs including MeshAdv-Mini"""

    # Frequency bands for SX1262/SX1268
    FREQUENCY_BANDS = {
        'SX1262_900': {
            'name': 'SX1262 (900 MHz)',
            'module': 'E22-900M22S',
            'frequency_range': '850-930 MHz',
            'regions': ['US', 'ANZ', 'JP', 'KR', 'TW'],
            'default_region': 'US'
        },
        'SX1268_400': {
            'name': 'SX1268 (400 MHz)',
            'module': 'E22-400M22S',
            'frequency_range': '410-510 MHz',
            'regions': ['EU_433', 'CN'],
            'default_region': 'EU_433'
        }
    }

    def __init__(self):
        self.hardware_detector = HardwareDetector()
        self.selected_hat = None
        self.config = {}

    def list_available_hats(self):
        """Display all available SPI HATs"""
        console.print("\n[bold cyan]Available SPI LoRa HATs[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="cyan", width=3)
        table.add_column("HAT Name", style="green", width=20)
        table.add_column("Radio Module", style="yellow", width=12)
        table.add_column("Description", style="blue", width=40)

        for idx, (hat_key, hat_info) in enumerate(self.hardware_detector.KNOWN_SPI_HATS.items(), 1):
            table.add_row(
                str(idx),
                hat_info['name'],
                hat_info.get('radio_module', 'Unknown'),
                hat_info.get('description', '')
            )

        console.print(table)

    def select_hat(self):
        """Interactively select an SPI HAT"""
        self.list_available_hats()

        hat_keys = list(self.hardware_detector.KNOWN_SPI_HATS.keys())
        choices = [str(i) for i in range(1, len(hat_keys) + 1)]

        console.print("\n[yellow]Select your SPI HAT (or 0 for auto-detect):[/yellow]")
        choice = Prompt.ask("HAT selection", choices=["0"] + choices, default="1")

        if choice == "0":
            # Try auto-detection
            detected = self._auto_detect_hat()
            if detected:
                self.selected_hat = detected
                console.print(f"[green]Auto-detected: {detected}[/green]")
            else:
                console.print("[yellow]Could not auto-detect HAT. Please select manually.[/yellow]")
                return self.select_hat()
        else:
            self.selected_hat = hat_keys[int(choice) - 1]

        return self.selected_hat

    def _auto_detect_hat(self):
        """Try to auto-detect the installed HAT"""
        self.hardware_detector.detect_all()

        # Check for HAT EEPROM info
        if 'hat_info' in self.hardware_detector.detected_hardware:
            product = self.hardware_detector.detected_hardware['hat_info'].get('product', '')
            for hat_key, hat_info in self.hardware_detector.KNOWN_SPI_HATS.items():
                if hat_key.lower() in product.lower():
                    return hat_key

        return None

    def configure_meshadv_mini(self):
        """Interactive configuration for MeshAdv-Mini HAT"""
        console.print("\n")
        console.print(Panel.fit(
            "[bold cyan]MeshAdv-Mini Configuration Wizard[/bold cyan]\n"
            "[dim]LoRa/GPS Raspberry Pi HAT with SX1262/SX1268[/dim]",
            border_style="cyan"
        ))

        hat_info = self.hardware_detector.KNOWN_SPI_HATS['MeshAdv-Mini']
        self.config = {'hat': 'MeshAdv-Mini'}

        # Step 1: Select frequency band
        console.print("\n[bold cyan]Step 1: Frequency Band Selection[/bold cyan]")
        console.print("[dim]Choose based on your LoRa module variant[/dim]\n")

        console.print("1. SX1262 (900 MHz) - E22-900M22S [yellow](US, Australia, Asia)[/yellow]")
        console.print("2. SX1268 (400 MHz) - E22-400M22S [yellow](EU 433, China)[/yellow]")

        band_choice = Prompt.ask("Select frequency band", choices=["1", "2"], default="1")
        self.config['frequency_band'] = 'SX1262_900' if band_choice == "1" else 'SX1268_400'

        band_info = self.FREQUENCY_BANDS[self.config['frequency_band']]
        console.print(f"[green]Selected: {band_info['name']} ({band_info['frequency_range']})[/green]")

        # Step 2: GPIO Configuration
        console.print("\n[bold cyan]Step 2: GPIO Configuration[/bold cyan]")
        console.print("[dim]Using default MeshAdv-Mini GPIO pinout[/dim]\n")

        gpio_config = hat_info['gpio_config'].copy()

        if Confirm.ask("Use default GPIO configuration?", default=True):
            self.config['gpio'] = gpio_config
        else:
            self.config['gpio'] = self._configure_custom_gpio(gpio_config)

        self._display_gpio_config(self.config['gpio'])

        # Step 3: SPI Configuration
        console.print("\n[bold cyan]Step 3: SPI Configuration[/bold cyan]")

        spi_config = hat_info['spi_config'].copy()
        self.config['spi'] = spi_config

        console.print(f"  SPI Bus: [green]0[/green]")
        console.print(f"  MOSI: GPIO [green]{spi_config['MOSI']}[/green]")
        console.print(f"  MISO: GPIO [green]{spi_config['MISO']}[/green]")
        console.print(f"  CLK:  GPIO [green]{spi_config['CLK']}[/green]")

        # Step 4: GPS Configuration
        console.print("\n[bold cyan]Step 4: GPS Configuration[/bold cyan]")
        gps_config = hat_info['gps_config'].copy()

        if Confirm.ask("Enable GPS?", default=True):
            self.config['gps_enabled'] = True
            self.config['gps'] = self._configure_gps(gps_config)
        else:
            self.config['gps_enabled'] = False
            console.print("[yellow]GPS disabled[/yellow]")

        # Step 5: Temperature Sensor
        console.print("\n[bold cyan]Step 5: Temperature Sensor (TMP102)[/bold cyan]")

        if Confirm.ask("Enable temperature sensor?", default=True):
            self.config['temp_sensor_enabled'] = True
            self.config['i2c'] = hat_info['i2c_config'].copy()
            console.print(f"[green]Temperature sensor enabled at I2C address {hat_info['i2c_config']['temp_sensor_addr']}[/green]")
        else:
            self.config['temp_sensor_enabled'] = False

        # Step 6: LoRa Module Options
        console.print("\n[bold cyan]Step 6: LoRa Module Options[/bold cyan]")
        self.config['lora_options'] = hat_info['lora_options'].copy()

        console.print(f"  DIO2 as RF Switch: [green]{'Enabled' if self.config['lora_options']['DIO2_AS_RF_SWITCH'] else 'Disabled'}[/green]")
        console.print(f"  DIO3 TCXO Voltage: [green]{'Enabled' if self.config['lora_options']['DIO3_TCXO_VOLTAGE'] else 'Disabled'}[/green]")

        # Summary
        self._display_config_summary()

        return self.config

    def _configure_custom_gpio(self, default_config):
        """Allow custom GPIO configuration"""
        console.print("\n[yellow]Custom GPIO Configuration:[/yellow]")
        console.print("[dim]Press Enter to accept default values[/dim]\n")

        custom_config = {}

        for pin_name, default_gpio in default_config.items():
            new_gpio = IntPrompt.ask(
                f"{pin_name} GPIO",
                default=default_gpio,
                show_default=True
            )
            custom_config[pin_name] = new_gpio

        return custom_config

    def _display_gpio_config(self, gpio_config):
        """Display GPIO configuration in a table"""
        table = Table(title="GPIO Configuration", show_header=True, header_style="bold magenta")
        table.add_column("Function", style="cyan")
        table.add_column("GPIO", style="green")
        table.add_column("BCM Pin", style="yellow")

        # BCM to physical pin mapping (approximate)
        bcm_to_pin = {
            8: 24, 16: 36, 20: 38, 24: 18, 12: 32,
            10: 19, 9: 21, 11: 23, 2: 3, 3: 5, 4: 7, 17: 11
        }

        for func, gpio in gpio_config.items():
            physical_pin = bcm_to_pin.get(gpio, 'N/A')
            table.add_row(func, str(gpio), str(physical_pin))

        console.print(table)

    def _configure_gps(self, default_gps_config):
        """Configure GPS settings"""
        console.print(f"\n[cyan]GPS Module: {default_gps_config['module']}[/cyan]")

        gps_config = default_gps_config.copy()

        # Serial port selection
        console.print("\n[dim]Select GPS serial port:[/dim]")
        console.print(f"1. {default_gps_config['serial_path']} (default)")
        console.print(f"2. {default_gps_config['serial_path_alt']}")
        console.print("3. Custom")

        port_choice = Prompt.ask("Serial port", choices=["1", "2", "3"], default="1")

        if port_choice == "1":
            gps_config['serial_port'] = default_gps_config['serial_path']
        elif port_choice == "2":
            gps_config['serial_port'] = default_gps_config['serial_path_alt']
        else:
            gps_config['serial_port'] = Prompt.ask("Enter custom serial port", default="/dev/ttyS0")

        console.print(f"[green]GPS serial port: {gps_config['serial_port']}[/green]")

        # GPS enable GPIO
        console.print(f"\n[dim]GPS Enable GPIO: {default_gps_config['enable_gpio']}[/dim]")
        console.print(f"[dim]GPS PPS GPIO: {default_gps_config['pps_gpio']}[/dim]")

        return gps_config

    def _display_config_summary(self):
        """Display complete configuration summary"""
        console.print("\n")
        console.print(Panel.fit(
            "[bold green]Configuration Summary[/bold green]",
            border_style="green"
        ))

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Setting", style="cyan", width=25)
        table.add_column("Value", style="green", width=40)

        table.add_row("HAT", self.config.get('hat', 'MeshAdv-Mini'))
        table.add_row("Frequency Band", self.FREQUENCY_BANDS[self.config['frequency_band']]['name'])

        gpio = self.config.get('gpio', {})
        table.add_row("LoRa CS", f"GPIO {gpio.get('CS', 'N/A')}")
        table.add_row("LoRa IRQ", f"GPIO {gpio.get('IRQ', 'N/A')}")
        table.add_row("LoRa Busy", f"GPIO {gpio.get('Busy', 'N/A')}")
        table.add_row("LoRa Reset", f"GPIO {gpio.get('Reset', 'N/A')}")
        table.add_row("LoRa RXen", f"GPIO {gpio.get('RXen', 'N/A')}")

        table.add_row("GPS Enabled", "Yes" if self.config.get('gps_enabled') else "No")
        if self.config.get('gps_enabled') and 'gps' in self.config:
            table.add_row("GPS Serial", self.config['gps'].get('serial_port', 'N/A'))

        table.add_row("Temp Sensor", "Yes" if self.config.get('temp_sensor_enabled') else "No")

        console.print(table)

    def configure_generic_hat(self, hat_key):
        """Configure a generic SPI HAT"""
        if hat_key not in self.hardware_detector.KNOWN_SPI_HATS:
            console.print(f"[red]Unknown HAT: {hat_key}[/red]")
            return None

        hat_info = self.hardware_detector.KNOWN_SPI_HATS[hat_key]

        console.print(f"\n[bold cyan]Configuring {hat_info['name']}[/bold cyan]\n")
        console.print(f"[dim]{hat_info.get('description', '')}[/dim]")

        self.config = {
            'hat': hat_key,
            'gpio': hat_info.get('gpio_config', {}),
            'lora_options': hat_info.get('lora_options', {}),
        }

        # Display default configuration
        if 'gpio_config' in hat_info:
            self._display_gpio_config(hat_info['gpio_config'])

        if Confirm.ask("\nUse default configuration?", default=True):
            console.print("[green]Using default configuration[/green]")
        else:
            self.config['gpio'] = self._configure_custom_gpio(hat_info.get('gpio_config', {}))

        return self.config

    def interactive_configure(self):
        """Main interactive configuration wizard"""
        console.print("\n[bold cyan]SPI HAT Configuration Wizard[/bold cyan]\n")

        # Select HAT
        selected_hat = self.select_hat()

        if selected_hat == 'MeshAdv-Mini':
            config = self.configure_meshadv_mini()
        else:
            config = self.configure_generic_hat(selected_hat)

        if config and Confirm.ask("\nSave configuration?", default=True):
            saved_files = self.save_configuration(config)
            console.print("\n[bold green]Configuration saved![/bold green]")
            for path in saved_files:
                console.print(f"  [dim]{path}[/dim]")

        return config

    def save_configuration(self, config):
        """Save configuration to meshtasticd config files"""
        saved_files = []

        # Ensure config directories exist
        config_dirs = [
            '/etc/meshtasticd',
            '/etc/meshtasticd/available.d',
            '/etc/meshtasticd/config.d'
        ]

        for dir_path in config_dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

        # Generate config.yaml content
        yaml_content = self.generate_config_yaml(config)

        # Save to config.yaml
        config_yaml_path = '/etc/meshtasticd/config.yaml'
        try:
            with open(config_yaml_path, 'w') as f:
                f.write(yaml_content)
            saved_files.append(config_yaml_path)
        except PermissionError:
            console.print(f"[red]Permission denied: {config_yaml_path}[/red]")
            console.print("[yellow]Try running with sudo[/yellow]")

        # Save device-specific config to available.d
        hat_name = config.get('hat', 'unknown').lower().replace(' ', '-')
        available_path = f'/etc/meshtasticd/available.d/{hat_name}.yaml'
        try:
            with open(available_path, 'w') as f:
                f.write(yaml_content)
            saved_files.append(available_path)

            # Create symlink in config.d to enable
            config_d_path = f'/etc/meshtasticd/config.d/{hat_name}.yaml'
            if os.path.exists(config_d_path):
                os.remove(config_d_path)
            os.symlink(available_path, config_d_path)
            saved_files.append(config_d_path)
        except PermissionError:
            console.print(f"[red]Permission denied writing to available.d[/red]")

        return saved_files

    def generate_config_yaml(self, config):
        """Generate meshtasticd config.yaml content"""
        lines = []
        lines.append("# Meshtasticd Configuration")
        lines.append(f"# Generated for: {config.get('hat', 'Unknown HAT')}")
        lines.append("")

        # LoRa section
        lines.append("Lora:")

        gpio = config.get('gpio', {})
        if 'CS' in gpio:
            lines.append(f"  CS: {gpio['CS']}")
        if 'IRQ' in gpio:
            lines.append(f"  IRQ: {gpio['IRQ']}")
        if 'Busy' in gpio:
            lines.append(f"  Busy: {gpio['Busy']}")
        if 'Reset' in gpio:
            lines.append(f"  Reset: {gpio['Reset']}")
        if 'RXen' in gpio:
            lines.append(f"  RXen: {gpio['RXen']}")

        # LoRa options
        lora_options = config.get('lora_options', {})
        if lora_options.get('DIO2_AS_RF_SWITCH'):
            lines.append("  DIO2_AS_RF_SWITCH: true")
        if lora_options.get('DIO3_TCXO_VOLTAGE'):
            lines.append("  DIO3_TCXO_VOLTAGE: true")

        lines.append("")

        # GPS section
        if config.get('gps_enabled') and 'gps' in config:
            gps = config['gps']
            lines.append("GPS:")
            lines.append(f"  SerialPath: {gps.get('serial_port', '/dev/ttyS0')}")
            if 'enable_gpio' in gps:
                lines.append(f"  GPSEnableGpio: {gps['enable_gpio']}")
            lines.append("")

        # I2C / Temperature sensor
        if config.get('temp_sensor_enabled') and 'i2c' in config:
            lines.append("I2C:")
            lines.append("  I2CDevice: /dev/i2c-1")
            lines.append("")

        # Webserver section (common default)
        lines.append("Webserver:")
        lines.append("  Port: 443")
        lines.append("")

        return "\n".join(lines)

    def show_hat_info(self, hat_key):
        """Display detailed information about a specific HAT"""
        if hat_key not in self.hardware_detector.KNOWN_SPI_HATS:
            console.print(f"[red]Unknown HAT: {hat_key}[/red]")
            return

        hat_info = self.hardware_detector.KNOWN_SPI_HATS[hat_key]

        console.print(f"\n[bold cyan]{hat_info['name']}[/bold cyan]")
        console.print(f"[dim]{hat_info.get('description', '')}[/dim]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Manufacturer", hat_info.get('manufacturer', 'Unknown'))
        table.add_row("Radio Module", hat_info.get('radio_module', 'Unknown'))
        table.add_row("Meshtastic Compatible", "Yes" if hat_info.get('meshtastic_compatible') else "No")

        if 'power_output' in hat_info:
            table.add_row("Power Output", hat_info['power_output'])

        if 'features' in hat_info:
            table.add_row("Features", ", ".join(hat_info['features']))

        if 'compatible_boards' in hat_info:
            table.add_row("Compatible Boards", ", ".join(hat_info['compatible_boards']))

        if 'notes' in hat_info:
            table.add_row("Notes", hat_info['notes'])

        console.print(table)

        # GPIO configuration
        if 'gpio_config' in hat_info:
            console.print("\n[cyan]GPIO Configuration:[/cyan]")
            self._display_gpio_config(hat_info['gpio_config'])

    def prompt_reboot_for_spi(self):
        """Prompt user to reboot for SPI/I2C changes with auto-return to installer"""
        console.print("\n[bold yellow]SPI/I2C Configuration Changed[/bold yellow]")
        console.print("[dim]A reboot is required for SPI/I2C changes to take effect.[/dim]\n")

        if Confirm.ask("Would you like to reboot now?", default=True):
            # Set up auto-resume service
            self._setup_resume_service()
            console.print("\n[green]Rebooting system...[/green]")
            console.print("[cyan]The installer will automatically restart after reboot.[/cyan]\n")

            # Give user time to read
            import time
            time.sleep(2)

            # Reboot
            subprocess.run(['reboot'], check=False)
        else:
            console.print("\n[yellow]Please reboot manually when ready:[/yellow]")
            console.print("  [cyan]sudo reboot[/cyan]\n")
            console.print("[dim]After reboot, run: sudo meshtasticd-installer[/dim]")

    def _setup_resume_service(self):
        """Create a one-shot systemd service to resume installer after reboot"""
        service_content = """[Unit]
Description=Resume Meshtasticd Installer after reboot
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/meshtasticd-installer
ExecStartPost=/bin/rm -f /etc/systemd/system/meshtasticd-installer-resume.service
ExecStartPost=/bin/systemctl daemon-reload
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes

[Install]
WantedBy=multi-user.target
"""
        try:
            # Write service file
            with open(AUTOSTART_SERVICE, 'w') as f:
                f.write(service_content)

            # Enable the service for next boot
            subprocess.run(['systemctl', 'daemon-reload'], check=False)
            subprocess.run(['systemctl', 'enable', 'meshtasticd-installer-resume.service'], check=False)

            log("Resume service created for post-reboot installer start")
        except PermissionError:
            console.print("[yellow]Could not set up auto-resume (permission denied)[/yellow]")
            console.print("[dim]Run 'sudo meshtasticd-installer' after reboot[/dim]")
        except Exception as e:
            log(f"Error setting up resume service: {e}", 'error')


def prompt_reboot_if_needed(changes_made=False):
    """Utility function to prompt for reboot if SPI/I2C changes were made"""
    if changes_made:
        configurator = SPIHatConfigurator()
        configurator.prompt_reboot_for_spi()
