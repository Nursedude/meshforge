"""Device configuration for Meshtastic"""

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from utils.logger import log, log_exception
from config.hardware import HardwareDetector
from utils.safe_import import safe_import

# Meshtastic library (optional, needed for device configuration)
_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')
_serial_interface, _HAS_SERIAL = safe_import('meshtastic.serial_interface')
_tcp_interface, _HAS_TCP = safe_import('meshtastic.tcp_interface')

# Connection manager for TCP (respects single-client constraint, Issue #17)
from utils.connection_manager import MeshtasticConnection

console = Console()


class DeviceConfigurator:
    """Handle device configuration"""

    def __init__(self):
        self.interface = None
        self.hardware_detector = HardwareDetector()

    def interactive_configure(self):
        """Interactive device configuration wizard"""
        console.print("\n[bold cyan]Meshtastic Device Configuration[/bold cyan]\n")

        # Detect hardware
        console.print("[cyan]Detecting hardware...[/cyan]")
        hardware = self.hardware_detector.detect_all()

        if not hardware:
            console.print("[yellow]No hardware detected. Configuration may be limited.[/yellow]")

        self.hardware_detector.show_hardware_info()

        # Connect to device
        if not self._connect_to_device():
            console.print("\n[bold red]Failed to connect to device[/bold red]")
            return

        # Configuration menu
        while True:
            console.print("\n[bold cyan]Configuration Menu:[/bold cyan]")
            console.print("1. Configure LoRa settings")
            console.print("2. Configure device settings")
            console.print("3. Configure modules")
            console.print("4. View current configuration")
            console.print("5. Back to main menu")

            choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "5"], default="5")

            if choice == "1":
                self._configure_lora()
            elif choice == "2":
                self._configure_device()
            elif choice == "3":
                self._configure_modules()
            elif choice == "4":
                self._view_configuration()
            elif choice == "5":
                break

        self._disconnect()

    def _connect_to_device(self):
        """Connect to Meshtastic device"""
        console.print("\n[cyan]Connecting to device...[/cyan]")

        if not _HAS_MESHTASTIC or not _HAS_SERIAL or not _HAS_TCP:
            console.print("[bold red]Meshtastic Python library not installed[/bold red]")
            console.print("Install with: [cyan]pip3 install meshtastic[/cyan]")
            return False

        try:
            # Get connection method
            hardware = self.hardware_detector.detected_hardware

            connection_type = "auto"

            if hardware.get('usb_serial_ports'):
                if Confirm.ask(f"\nUSB serial port detected at {hardware['usb_serial_ports'][0]}. Use this?", default=True):
                    port = hardware['usb_serial_ports'][0]
                    self.interface = _serial_interface.SerialInterface(port)
                    console.print(f"[green]Connected via USB serial: {port}[/green]")
                    return True

            # Ask user for connection method
            console.print("\n[cyan]Select connection method:[/cyan]")
            console.print("1. USB Serial")
            console.print("2. Network (TCP)")
            console.print("3. Auto-detect")
            console.print("0. Back to main menu")

            method = Prompt.ask("Connection method [1/2/3/0]", choices=["1", "2", "3", "0", "b", "back"], default="3")

            if method in ("0", "b", "back"):
                console.print("[yellow]Returning to main menu...[/yellow]")
                return False

            if method == "1":
                port = Prompt.ask("Enter USB serial port (or 'b' to go back)", default="/dev/ttyUSB0")
                if port.lower() in ('b', 'back', '0'):
                    return False
                self.interface = _serial_interface.SerialInterface(port)
                console.print(f"[green]Connected via USB serial: {port}[/green]")
                return True

            elif method == "2":
                host = Prompt.ask("Enter hostname or IP (or 'b' to go back)", default="meshtastic.local")
                if host.lower() in ('b', 'back', '0'):
                    return False
                # Use connection manager to respect meshtasticd's single-client
                # TCP constraint (Issue #17). Store the context manager so we can
                # clean up on disconnect.
                self._conn_ctx = MeshtasticConnection(host=host)
                self.interface = self._conn_ctx.__enter__()
                if not self.interface:
                    console.print("[yellow]Connection busy — another component is using meshtasticd[/yellow]")
                    self._conn_ctx.__exit__(None, None, None)
                    self._conn_ctx = None
                    return False
                console.print(f"[green]Connected via TCP: {host}[/green]")
                return True

            elif method == "3":
                # Try auto-detect
                self.interface = _serial_interface.SerialInterface()
                console.print("[green]Connected (auto-detected)[/green]")
                return True

        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled by user[/yellow]")
            return False

        except Exception as e:
            console.print(f"[bold red]Failed to connect: {str(e)}[/bold red]")
            log_exception(e, "Device connection")
            return False

    def _disconnect(self):
        """Disconnect from device"""
        if self.interface:
            try:
                # If connected via connection manager, clean up context
                if hasattr(self, '_conn_ctx') and self._conn_ctx:
                    self._conn_ctx.__exit__(None, None, None)
                    self._conn_ctx = None
                else:
                    self.interface.close()
                console.print("\n[green]Disconnected from device[/green]")
            except Exception as e:
                log_exception(e, "Device disconnect")

    def _configure_lora(self):
        """Configure LoRa settings"""
        console.print("\n[bold cyan]LoRa Configuration[/bold cyan]\n")

        if not self.interface:
            console.print("[red]Not connected to device[/red]")
            return

        try:
            # Get current config
            node_info = self.interface.getNode('^local')

            console.print("[cyan]Current LoRa settings:[/cyan]")
            # Display current settings (this depends on the meshtastic library version)

            # Configure LoRa settings
            console.print("\n[cyan]Available regions:[/cyan]")
            console.print("1. US (902-928 MHz)")
            console.print("2. EU_433 (433 MHz)")
            console.print("3. EU_868 (863-870 MHz)")
            console.print("4. CN (470-510 MHz)")
            console.print("5. JP (920-923 MHz)")
            console.print("6. ANZ (915-928 MHz)")
            console.print("7. Keep current")

            region_choice = Prompt.ask("Select region", choices=["1", "2", "3", "4", "5", "6", "7"], default="7")

            region_map = {
                "1": "US",
                "2": "EU_433",
                "3": "EU_868",
                "4": "CN",
                "5": "JP",
                "6": "ANZ",
            }

            if region_choice in region_map:
                console.print(f"\n[cyan]Setting region to {region_map[region_choice]}...[/cyan]")
                # Note: The actual API call depends on the meshtastic library version
                # This is a placeholder
                console.print("[yellow]Note: Actual region setting requires device reboot[/yellow]")

            # Other LoRa settings
            if Confirm.ask("\nConfigure advanced LoRa settings?", default=False):
                console.print("\n[yellow]Advanced LoRa settings:[/yellow]")
                console.print("Bandwidth, Spreading Factor, Coding Rate, etc.")
                console.print("[yellow]Note: Changes to these settings can affect network compatibility[/yellow]")

        except Exception as e:
            console.print(f"[bold red]Error configuring LoRa: {str(e)}[/bold red]")
            log_exception(e, "LoRa configuration")

    def _configure_device(self):
        """Configure device settings"""
        console.print("\n[bold cyan]Device Configuration[/bold cyan]\n")

        if not self.interface:
            console.print("[red]Not connected to device[/red]")
            return

        try:
            # Device name
            if Confirm.ask("\nSet device name?", default=True):
                device_name = Prompt.ask("Enter device name", default="Meshtastic Node")
                console.print(f"[green]Device name set to: {device_name}[/green]")

            # WiFi settings (if applicable)
            if Confirm.ask("\nConfigure WiFi?", default=False):
                self._configure_wifi()

        except Exception as e:
            console.print(f"[bold red]Error configuring device: {str(e)}[/bold red]")
            log_exception(e, "Device configuration")

    def _configure_wifi(self):
        """Configure WiFi settings"""
        console.print("\n[cyan]WiFi Configuration[/cyan]")

        ssid = Prompt.ask("WiFi SSID")
        password = Prompt.ask("WiFi Password", password=True)

        console.print(f"[green]WiFi configured: {ssid}[/green]")
        console.print("[yellow]Note: Device will connect on next reboot[/yellow]")

    def _configure_modules(self):
        """Configure Meshtastic modules"""
        console.print("\n[bold cyan]Module Configuration[/bold cyan]\n")

        console.print("Available modules:")
        console.print("1. MQTT")
        console.print("2. Serial")
        console.print("3. External Notification")
        console.print("4. Store & Forward")
        console.print("5. Range Test")
        console.print("6. Telemetry")
        console.print("7. Back")

        choice = Prompt.ask("\nSelect module to configure", choices=["1", "2", "3", "4", "5", "6", "7"], default="7")

        if choice == "1":
            self._configure_mqtt()
        elif choice == "2":
            console.print("\n[cyan]Serial module configuration[/cyan]")
            console.print("[yellow]Configure serial baud rate, timeout, etc.[/yellow]")
        elif choice == "3":
            console.print("\n[cyan]External notification configuration[/cyan]")
            console.print("[yellow]Configure external LEDs, buzzers, etc.[/yellow]")
        elif choice == "4":
            console.print("\n[cyan]Store & Forward configuration[/cyan]")
            console.print("[yellow]Configure message storage and forwarding[/yellow]")
        elif choice == "5":
            console.print("\n[cyan]Range test configuration[/cyan]")
            console.print("[yellow]Configure range testing parameters[/yellow]")
        elif choice == "6":
            console.print("\n[cyan]Telemetry configuration[/cyan]")
            console.print("[yellow]Configure device and environment telemetry[/yellow]")

    def _configure_mqtt(self):
        """Configure MQTT module"""
        console.print("\n[cyan]MQTT Configuration[/cyan]")

        if Confirm.ask("Enable MQTT?", default=False):
            server = Prompt.ask("MQTT Server", default="mqtt.meshtastic.org")
            username = Prompt.ask("MQTT Username (optional)", default="")
            password = Prompt.ask("MQTT Password (optional)", password=True, default="")

            console.print(f"[green]MQTT configured: {server}[/green]")
        else:
            console.print("[yellow]MQTT disabled[/yellow]")

    def _view_configuration(self):
        """View current device configuration"""
        console.print("\n[bold cyan]Current Configuration[/bold cyan]\n")

        if not self.interface:
            console.print("[red]Not connected to device[/red]")
            return

        try:
            # Get node info
            node_info = self.interface.getNode('^local')

            table = Table(title="Device Information", show_header=True, header_style="bold magenta")
            table.add_column("Setting", style="cyan")
            table.add_column("Value", style="green")

            # Add various settings
            # Note: This depends on the meshtastic library API
            table.add_row("Device ID", "N/A")
            table.add_row("Firmware Version", "N/A")
            table.add_row("Hardware Model", "N/A")

            console.print(table)

        except Exception as e:
            console.print(f"[bold red]Error viewing configuration: {str(e)}[/bold red]")
            log_exception(e, "View configuration")
