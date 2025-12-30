"""Interactive Meshtastic CLI command interface"""

import subprocess
import shutil
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.table import Table

console = Console()


class MeshtasticCLI:
    """Interactive interface for meshtastic CLI commands"""

    def __init__(self):
        self._return_to_main = False
        self._connection_type = "localhost"  # localhost, serial, ble
        self._connection_value = "localhost"
        self._check_cli_installed()

    def _check_cli_installed(self):
        """Check if meshtastic CLI is installed"""
        self._cli_available = shutil.which('meshtastic') is not None

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

    def _get_connection_args(self):
        """Get connection arguments based on current settings"""
        if self._connection_type == "localhost":
            return ["--host", self._connection_value]
        elif self._connection_type == "serial":
            return ["--port", self._connection_value]
        elif self._connection_type == "ble":
            return []  # BLE uses --ble-scan first
        return ["--host", "localhost"]

    def _run_command(self, args, show_output=True):
        """Run a meshtastic CLI command"""
        if not self._cli_available:
            console.print("[red]Meshtastic CLI not installed![/red]")
            console.print("[cyan]Install with: pipx install meshtastic[cli][/cyan]")
            return None

        full_args = ["meshtastic"] + self._get_connection_args() + args

        console.print(f"[dim]Running: {' '.join(full_args)}[/dim]\n")

        try:
            result = subprocess.run(full_args, capture_output=True, text=True, timeout=60)
            if show_output:
                if result.stdout:
                    console.print(result.stdout)
                if result.stderr:
                    console.print(f"[yellow]{result.stderr}[/yellow]")
            return result
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out[/red]")
            return None
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return None

    def _run_command_interactive(self, args):
        """Run command with direct output (no capture)"""
        if not self._cli_available:
            console.print("[red]Meshtastic CLI not installed![/red]")
            return None

        full_args = ["meshtastic"] + self._get_connection_args() + args
        console.print(f"[dim]Running: {' '.join(full_args)}[/dim]\n")

        try:
            subprocess.run(full_args, timeout=120)
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def interactive_menu(self):
        """Main meshtastic CLI menu"""
        self._return_to_main = False

        if not self._cli_available:
            console.print("\n[red]Meshtastic CLI is not installed![/red]")
            console.print("[cyan]Install with: sudo apt install pipx && pipx install 'meshtastic[cli]'[/cyan]")
            if not Confirm.ask("\nContinue anyway?", default=False):
                return

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Meshtastic CLI ═══════════════[/bold cyan]\n")
            console.print(f"[dim]Connection: {self._connection_type} -> {self._connection_value}[/dim]\n")

            console.print("[dim cyan]── Connection ──[/dim cyan]")
            console.print(f"  [bold]c[/bold]. Configure Connection")

            console.print("\n[dim cyan]── Information ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. Show Node Info (--info)")
            console.print(f"  [bold]2[/bold]. List All Nodes (--nodes)")
            console.print(f"  [bold]3[/bold]. Get All Settings (--get all)")
            console.print(f"  [bold]4[/bold]. Show Help (--help)")

            console.print("\n[dim cyan]── Location ──[/dim cyan]")
            console.print(f"  [bold]5[/bold]. Set Position (lat/lon/alt)")

            console.print("\n[dim cyan]── Network ──[/dim cyan]")
            console.print(f"  [bold]6[/bold]. Configure WiFi")
            console.print(f"  [bold]7[/bold]. Channel Configuration")

            console.print("\n[dim cyan]── Messaging ──[/dim cyan]")
            console.print(f"  [bold]8[/bold]. Send Message")
            console.print(f"  [bold]9[/bold]. Request Position from Node")
            console.print(f"  [bold]t[/bold]. Request Telemetry from Node")
            console.print(f"  [bold]r[/bold]. Traceroute to Node")

            console.print("\n[dim cyan]── Node Control ──[/dim cyan]")
            console.print(f"  [bold]n[/bold]. Set Node Owner Name")
            console.print(f"  [bold]b[/bold]. Reboot Node")
            console.print(f"  [bold]s[/bold]. Shutdown Node")
            console.print(f"  [bold]f[/bold]. [red]Factory Reset[/red]")
            console.print(f"  [bold]d[/bold]. [yellow]Reset Node Database[/yellow]")

            valid_choices = ["c", "1", "2", "3", "4", "5", "6", "7", "8", "9", "t", "r", "n", "b", "s", "f", "d"]
            choices = self._prompt_back(valid_choices)
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "c":
                self._configure_connection()
            elif choice == "1":
                self._show_info()
            elif choice == "2":
                self._list_nodes()
            elif choice == "3":
                self._get_all_settings()
            elif choice == "4":
                self._show_help()
            elif choice == "5":
                self._set_position()
            elif choice == "6":
                self._configure_wifi()
            elif choice == "7":
                self._configure_channels()
            elif choice == "8":
                self._send_message()
            elif choice == "9":
                self._request_position()
            elif choice == "t":
                self._request_telemetry()
            elif choice == "r":
                self._traceroute()
            elif choice == "n":
                self._set_owner()
            elif choice == "b":
                self._reboot_node()
            elif choice == "s":
                self._shutdown_node()
            elif choice == "f":
                self._factory_reset()
            elif choice == "d":
                self._reset_nodedb()

    def _configure_connection(self):
        """Configure connection type"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Connection Configuration ═══════════════[/bold cyan]\n")
            console.print(f"[dim]Current: {self._connection_type} -> {self._connection_value}[/dim]\n")

            console.print("  [bold]1[/bold]. Connect via localhost (default for meshtasticd)")
            console.print("  [bold]2[/bold]. Connect via network hostname/IP")
            console.print("  [bold]3[/bold]. Connect via serial port")
            console.print("  [bold]4[/bold]. Scan for Bluetooth devices")
            console.print("  [bold]5[/bold]. Test current connection")

            choices = self._prompt_back(["1", "2", "3", "4", "5"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self._connection_type = "localhost"
                self._connection_value = "localhost"
                console.print("[green]Connected to localhost[/green]")
            elif choice == "2":
                host = Prompt.ask("Hostname or IP", default="meshtastic.local")
                self._connection_type = "localhost"  # --host flag
                self._connection_value = host
                console.print(f"[green]Set connection to {host}[/green]")
            elif choice == "3":
                port = Prompt.ask("Serial port", default="/dev/ttyUSB0")
                self._connection_type = "serial"
                self._connection_value = port
                console.print(f"[green]Set connection to {port}[/green]")
            elif choice == "4":
                self._ble_scan()
            elif choice == "5":
                self._test_connection()

    def _test_connection(self):
        """Test the current connection"""
        console.print("\n[cyan]Testing connection...[/cyan]\n")
        result = self._run_command(["--info"])
        if result and result.returncode == 0:
            console.print("\n[green]Connection successful![/green]")
        else:
            console.print("\n[red]Connection failed[/red]")
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _ble_scan(self):
        """Scan for Bluetooth devices"""
        console.print("\n[cyan]Scanning for Bluetooth devices...[/cyan]")
        console.print("[dim]This may take a moment...[/dim]\n")
        self._run_command_interactive(["--ble-scan"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _show_info(self):
        """Show node information"""
        console.print("\n[bold cyan]Node Information[/bold cyan]\n")
        self._run_command(["--info"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _list_nodes(self):
        """List all known nodes"""
        console.print("\n[bold cyan]Known Nodes[/bold cyan]\n")
        self._run_command(["--nodes"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _get_all_settings(self):
        """Get all settings"""
        console.print("\n[bold cyan]All Settings[/bold cyan]\n")
        self._run_command(["--get", "all"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _show_help(self):
        """Show meshtastic CLI help"""
        console.print("\n[bold cyan]Meshtastic CLI Help[/bold cyan]\n")
        # Run without connection args for help
        try:
            result = subprocess.run(["meshtastic", "-h"], capture_output=True, text=True)
            console.print(result.stdout)
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _set_position(self):
        """Set node position"""
        console.print("\n[bold cyan]Set Position[/bold cyan]\n")
        console.print("[dim]Enter coordinates in decimal degrees (e.g., 19.435175)[/dim]\n")

        lat = Prompt.ask("Latitude", default="19.435175")
        lon = Prompt.ask("Longitude", default="-155.213842")
        alt = Prompt.ask("Altitude (meters)", default="0")

        if Confirm.ask(f"\nSet position to {lat}, {lon}, {alt}m?", default=True):
            self._run_command(["--setlat", lat, "--setlon", lon, "--setalt", alt])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _configure_wifi(self):
        """Configure WiFi settings"""
        console.print("\n[bold cyan]WiFi Configuration[/bold cyan]\n")

        ssid = Prompt.ask("WiFi SSID")
        if not ssid:
            console.print("[yellow]Cancelled[/yellow]")
            return

        psk = Prompt.ask("WiFi Password", password=True)

        if Confirm.ask(f"\nEnable WiFi with SSID '{ssid}'?", default=True):
            self._run_command([
                "--set", "network.wifi_ssid", ssid,
                "--set", "network.wifi_psk", psk,
                "--set", "network.wifi_enabled", "1"
            ])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _configure_channels(self):
        """Configure channels"""
        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Channel Configuration ═══════════════[/bold cyan]\n")

            console.print("  [bold]1[/bold]. View channel info")
            console.print("  [bold]2[/bold]. Set channel name")
            console.print("  [bold]3[/bold]. Set channel PSK (encryption key)")
            console.print("  [bold]4[/bold]. Set channel ID (hash)")

            choices = self._prompt_back(["1", "2", "3", "4"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                ch_index = Prompt.ask("Channel index", default="0")
                self._run_command(["--ch-index", ch_index, "--info"])
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "2":
                ch_index = Prompt.ask("Channel index", default="0")
                name = Prompt.ask("Channel name")
                if name:
                    self._run_command(["--ch-index", ch_index, "--ch-set", "name", name, "--info"])
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "3":
                console.print("\n[dim]PSK format: 32 hex bytes (e.g., 0x1a1a...)[/dim]")
                console.print("[dim]Or use 'random' for a random key, 'none' for no encryption[/dim]\n")
                ch_index = Prompt.ask("Channel index", default="0")
                psk = Prompt.ask("PSK")
                if psk:
                    self._run_command(["--ch-index", ch_index, "--ch-set", "psk", psk, "--info"])
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "4":
                ch_index = Prompt.ask("Channel index", default="0")
                ch_id = Prompt.ask("Channel ID (numeric)")
                if ch_id:
                    self._run_command(["--ch-index", ch_index, "--ch-set", "id", ch_id])
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _send_message(self):
        """Send a text message"""
        console.print("\n[bold cyan]Send Message[/bold cyan]\n")

        message = Prompt.ask("Message text")
        if not message:
            console.print("[yellow]Cancelled[/yellow]")
            return

        dest = Prompt.ask("Destination (leave empty for broadcast, or node ID like !ba4bf9d0)", default="")
        ch_index = Prompt.ask("Channel index", default="0")
        ack = Confirm.ask("Request acknowledgment?", default=False)

        args = ["--ch-index", ch_index, "--sendtext", message]
        if dest:
            args.extend(["--dest", dest])
        if ack:
            args.append("--ack")

        self._run_command(args)
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _request_position(self):
        """Request position from a node"""
        console.print("\n[bold cyan]Request Position[/bold cyan]\n")

        dest = Prompt.ask("Node ID (e.g., !ba4bf9d0)")
        if not dest:
            console.print("[yellow]Cancelled[/yellow]")
            return

        ch_index = Prompt.ask("Channel index", default="0")
        self._run_command(["--request-position", "--dest", dest, "--ch-index", ch_index])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _request_telemetry(self):
        """Request telemetry from a node"""
        console.print("\n[bold cyan]Request Telemetry[/bold cyan]\n")

        dest = Prompt.ask("Node ID (e.g., !ba4bf9d0 or numeric ID)")
        if not dest:
            console.print("[yellow]Cancelled[/yellow]")
            return

        self._run_command(["--request-telemetry", "--dest", dest])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _traceroute(self):
        """Perform traceroute to a node"""
        console.print("\n[bold cyan]Traceroute[/bold cyan]\n")

        dest = Prompt.ask("Node ID (e.g., !ba4bf9d0)")
        if not dest:
            console.print("[yellow]Cancelled[/yellow]")
            return

        console.print("\n[dim]This may take a moment...[/dim]\n")
        self._run_command(["--traceroute", dest])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _set_owner(self):
        """Set node owner name"""
        console.print("\n[bold cyan]Set Owner Name[/bold cyan]\n")

        dest = Prompt.ask("Node ID (leave empty for local node)", default="")
        name = Prompt.ask("Owner name")
        if not name:
            console.print("[yellow]Cancelled[/yellow]")
            return

        args = ["--set-owner", name]
        if dest:
            args = ["--dest", dest] + args

        self._run_command(args)
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _reboot_node(self):
        """Reboot the node"""
        console.print("\n[bold cyan]Reboot Node[/bold cyan]\n")

        if Confirm.ask("[yellow]Reboot the node?[/yellow]", default=False):
            self._run_command(["--reboot"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _shutdown_node(self):
        """Shutdown the node"""
        console.print("\n[bold cyan]Shutdown Node[/bold cyan]\n")

        if Confirm.ask("[yellow]Shutdown the node?[/yellow]", default=False):
            self._run_command(["--shutdown"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _factory_reset(self):
        """Factory reset the node"""
        console.print("\n[bold red]Factory Reset[/bold red]\n")
        console.print("[red]WARNING: This will erase all settings![/red]\n")

        if Confirm.ask("[red]Are you absolutely sure?[/red]", default=False):
            if Confirm.ask("[red]Type 'yes' to confirm factory reset[/red]", default=False):
                self._run_command(["--factory-reset"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _reset_nodedb(self):
        """Reset the node database"""
        console.print("\n[bold yellow]Reset Node Database[/bold yellow]\n")
        console.print("[yellow]This will clear the list of known nodes.[/yellow]\n")

        if Confirm.ask("[yellow]Reset node database?[/yellow]", default=False):
            self._run_command(["--reset-nodedb"])
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")
