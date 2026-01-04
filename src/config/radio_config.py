"""
Comprehensive Radio Configuration - Mesh, MQTT, Channel, Position Settings

Provides full configuration for Meshtastic node settings via meshtastic CLI.
Matches the web UI configuration categories.
"""

import subprocess
import shutil
import os
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table
from rich.panel import Panel

console = Console()


class RadioConfig:
    """Comprehensive radio configuration manager"""

    def __init__(self):
        self._return_to_main = False
        self._cli_path = self._find_meshtastic_cli()

    def _find_meshtastic_cli(self):
        """Find the meshtastic CLI executable - uses centralized utils.cli"""
        try:
            from utils.cli import find_meshtastic_cli
            return find_meshtastic_cli()
        except ImportError:
            # Fallback if utils not available
            return shutil.which('meshtastic')

    def _run_cli(self, args, timeout=30):
        """Run meshtastic CLI command"""
        if not self._cli_path:
            console.print("[red]Meshtastic CLI not found. Install with: pipx install meshtastic[cli][/red]")
            return None

        try:
            cmd = [self._cli_path, '--host', 'localhost'] + args
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out[/red]")
            return None
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return None

    def interactive_menu(self):
        """Main radio configuration menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════ Radio Configuration ═══════════[/bold cyan]\n")

            console.print("[dim cyan]── Device Settings ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Mesh Settings [dim](Role, Rebroadcast, Node Info)[/dim]")
            console.print("  [bold]2[/bold]. Position Settings [dim](GPS, Location Sharing)[/dim]")
            console.print("  [bold]3[/bold]. Power Settings [dim](TX Power, Low Power Mode)[/dim]")

            console.print("\n[dim cyan]── Network Settings ──[/dim cyan]")
            console.print("  [bold]4[/bold]. LoRa Settings [dim](Region, Modem Preset, Hop Limit)[/dim]")
            console.print("  [bold]5[/bold]. Channel Settings [dim](PSK, Name, MQTT uplink/downlink)[/dim]")
            console.print("  [bold]6[/bold]. MQTT Settings [dim](Server, Encryption, Topics)[/dim]")

            console.print("\n[dim cyan]── Module Settings ──[/dim cyan]")
            console.print("  [bold]7[/bold]. Telemetry Settings [dim](Device, Environment, Power)[/dim]")
            console.print("  [bold]8[/bold]. Store & Forward [dim](Message history server)[/dim]")
            console.print("  [bold]9[/bold]. All Modules [dim](Serial, Audio, Range Test, etc.)[/dim]")

            console.print("\n[dim cyan]── Quick Actions ──[/dim cyan]")
            console.print("  [bold]v[/bold]. View Current Config [dim](Show all settings)[/dim]")
            console.print("  [bold]r[/bold]. Reset to Defaults [dim](Factory reset node)[/dim]")

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
                self._configure_mesh_settings()
            elif choice == "2":
                self._configure_position_settings()
            elif choice == "3":
                self._configure_power_settings()
            elif choice == "4":
                self._configure_lora_settings()
            elif choice == "5":
                self._configure_channel_settings()
            elif choice == "6":
                self._configure_mqtt_settings()
            elif choice == "7":
                self._configure_telemetry_settings()
            elif choice == "8":
                self._configure_store_forward()
            elif choice == "9":
                self._configure_all_modules()
            elif choice.lower() == "v":
                self._view_current_config()
            elif choice.lower() == "r":
                self._reset_to_defaults()

    def _configure_mesh_settings(self):
        """Configure mesh network settings"""
        console.print("\n[bold cyan]── Mesh Settings ──[/bold cyan]\n")

        console.print("[dim]Device role determines how your node participates in the mesh.[/dim]\n")

        # Device Role
        console.print("[cyan]Device Roles:[/cyan]")
        roles = {
            "1": ("CLIENT", "Normal node, can sleep. For portables/handhelds."),
            "2": ("CLIENT_MUTE", "Like CLIENT but doesn't broadcast position."),
            "3": ("CLIENT_HIDDEN", "Like CLIENT_MUTE but also hidden from node list."),
            "4": ("ROUTER", "Always-on infrastructure. Forwards packets, no sleep."),
            "5": ("ROUTER_CLIENT", "Router that also acts as a client."),
            "6": ("REPEATER", "Simple repeater, no other functions."),
            "7": ("TRACKER", "Optimized for GPS tracking."),
            "8": ("SENSOR", "Low-power sensor node."),
        }

        for key, (role, desc) in roles.items():
            console.print(f"  [bold]{key}[/bold]. {role} [dim]- {desc}[/dim]")

        console.print("\n  [bold]0[/bold]. Back")

        role_choice = Prompt.ask("\n[cyan]Select device role[/cyan]", default="0")

        if role_choice == "0":
            return

        if role_choice in roles:
            role_name = roles[role_choice][0]
            console.print(f"\n[cyan]Setting device role to {role_name}...[/cyan]")
            result = self._run_cli(['--set', 'device.role', role_name])
            if result and result.returncode == 0:
                console.print(f"[green]Device role set to {role_name}[/green]")
            else:
                console.print(f"[yellow]Could not set role: {result.stderr if result else 'CLI error'}[/yellow]")

        # Rebroadcast mode
        console.print("\n[cyan]Rebroadcast Mode:[/cyan]")
        console.print("  [bold]1[/bold]. ALL - Rebroadcast all messages")
        console.print("  [bold]2[/bold]. ALL_SKIP_DECODING - Rebroadcast without decoding")
        console.print("  [bold]3[/bold]. LOCAL_ONLY - Only rebroadcast local messages")
        console.print("  [bold]4[/bold]. KNOWN_ONLY - Only rebroadcast from known nodes")
        console.print("  [bold]0[/bold]. Skip")

        rebroadcast = Prompt.ask("\n[cyan]Select rebroadcast mode[/cyan]", default="0")

        rebroadcast_modes = {
            "1": "ALL", "2": "ALL_SKIP_DECODING",
            "3": "LOCAL_ONLY", "4": "KNOWN_ONLY"
        }

        if rebroadcast in rebroadcast_modes:
            mode = rebroadcast_modes[rebroadcast]
            result = self._run_cli(['--set', 'device.rebroadcast_mode', mode])
            if result and result.returncode == 0:
                console.print(f"[green]Rebroadcast mode set to {mode}[/green]")

        # Node info broadcast interval
        if Confirm.ask("\n[cyan]Configure node info broadcast interval?[/cyan]", default=False):
            interval = IntPrompt.ask("Broadcast interval (seconds)", default=900)
            result = self._run_cli(['--set', 'device.node_info_broadcast_secs', str(interval)])
            if result and result.returncode == 0:
                console.print(f"[green]Node info interval set to {interval}s[/green]")

        input("\nPress Enter to continue...")

    def _configure_position_settings(self):
        """Configure position/GPS settings"""
        console.print("\n[bold cyan]── Position Settings ──[/bold cyan]\n")

        console.print("[dim]Configure GPS and location sharing settings.[/dim]\n")

        # GPS enabled
        gps_enabled = Confirm.ask("[cyan]Enable GPS?[/cyan]", default=True)
        result = self._run_cli(['--set', 'position.gps_enabled', 'true' if gps_enabled else 'false'])
        if result and result.returncode == 0:
            console.print(f"[green]GPS {'enabled' if gps_enabled else 'disabled'}[/green]")

        if gps_enabled:
            # GPS update interval
            console.print("\n[cyan]GPS Update Interval:[/cyan]")
            console.print("[dim]How often to check GPS (in seconds). Lower = more battery usage.[/dim]")
            interval = IntPrompt.ask("GPS update interval", default=120)
            self._run_cli(['--set', 'position.gps_update_interval', str(interval)])

            # Position broadcast interval
            console.print("\n[cyan]Position Broadcast Interval:[/cyan]")
            console.print("[dim]How often to share your position with the mesh (in seconds).[/dim]")
            broadcast = IntPrompt.ask("Position broadcast interval", default=900)
            self._run_cli(['--set', 'position.position_broadcast_secs', str(broadcast)])

        # Fixed position
        if Confirm.ask("\n[cyan]Set fixed position (no GPS needed)?[/cyan]", default=False):
            console.print("[dim]Enter coordinates in decimal degrees.[/dim]")
            lat = Prompt.ask("Latitude", default="0.0")
            lon = Prompt.ask("Longitude", default="0.0")
            alt = IntPrompt.ask("Altitude (meters)", default=0)

            self._run_cli(['--set', 'position.fixed_position', 'true'])
            self._run_cli(['--setlat', lat])
            self._run_cli(['--setlon', lon])
            self._run_cli(['--setalt', str(alt)])
            console.print("[green]Fixed position set[/green]")

        # Smart position
        if Confirm.ask("\n[cyan]Enable smart position broadcasting?[/cyan]", default=True):
            console.print("[dim]Only broadcast when position changes significantly.[/dim]")
            self._run_cli(['--set', 'position.position_broadcast_smart_enabled', 'true'])
            min_dist = IntPrompt.ask("Minimum distance for update (meters)", default=100)
            self._run_cli(['--set', 'position.broadcast_smart_minimum_distance', str(min_dist)])

        input("\nPress Enter to continue...")

    def _configure_power_settings(self):
        """Configure power settings"""
        console.print("\n[bold cyan]── Power Settings ──[/bold cyan]\n")

        # TX Power
        console.print("[cyan]Transmit Power:[/cyan]")
        console.print("[dim]Higher power = longer range but more battery usage.[/dim]")
        console.print("  Standard modules: 0-22 dBm")
        console.print("  High-power (MeshAdv-Pi-Hat): 0-30 dBm")
        console.print("  MeshAdv 33S variants: 0-33 dBm")

        tx_power = IntPrompt.ask("\nTX Power (dBm)", default=22)
        if 0 <= tx_power <= 33:
            result = self._run_cli(['--set', 'lora.tx_power', str(tx_power)])
            if result and result.returncode == 0:
                console.print(f"[green]TX power set to {tx_power} dBm[/green]")

        # Low power mode
        console.print("\n[cyan]Power Saving:[/cyan]")
        if Confirm.ask("Enable power saving (reduces TX power when battery low)?", default=False):
            self._run_cli(['--set', 'power.is_power_saving', 'true'])

        # Screen timeout
        console.print("\n[cyan]Screen Settings:[/cyan]")
        if Confirm.ask("Configure screen timeout?", default=False):
            timeout = IntPrompt.ask("Screen timeout (seconds, 0=always on)", default=60)
            self._run_cli(['--set', 'display.screen_on_secs', str(timeout)])

        input("\nPress Enter to continue...")

    def _configure_lora_settings(self):
        """Configure LoRa radio settings"""
        console.print("\n[bold cyan]── LoRa Settings ──[/bold cyan]\n")

        # Region
        console.print("[cyan]Region Configuration:[/cyan]")
        console.print("[yellow]IMPORTANT: Set region matching your location![/yellow]\n")

        regions = {
            "1": "US", "2": "EU_868", "3": "EU_433",
            "4": "CN", "5": "JP", "6": "ANZ",
            "7": "KR", "8": "TW", "9": "RU", "10": "IN"
        }

        for key, region in regions.items():
            console.print(f"  [bold]{key}[/bold]. {region}")

        region_choice = Prompt.ask("\n[cyan]Select region[/cyan]", default="1")
        if region_choice in regions:
            result = self._run_cli(['--set', 'lora.region', regions[region_choice]])
            if result and result.returncode == 0:
                console.print(f"[green]Region set to {regions[region_choice]}[/green]")

        # Modem Preset
        console.print("\n[cyan]Modem Preset (Speed vs Range):[/cyan]")
        presets = {
            "1": ("SHORT_TURBO", "Fastest, shortest range (500kHz BW)"),
            "2": ("SHORT_FAST", "Very fast, short range"),
            "3": ("MEDIUM_FAST", "Good balance - MtnMesh standard"),
            "4": ("LONG_FAST", "Default - great range, ~1kbps"),
            "5": ("LONG_MODERATE", "Extended range, slower"),
            "6": ("LONG_SLOW", "Very long range, slow"),
            "7": ("VERY_LONG_SLOW", "Maximum range, very slow"),
        }

        for key, (preset, desc) in presets.items():
            console.print(f"  [bold]{key}[/bold]. {preset} [dim]- {desc}[/dim]")

        preset_choice = Prompt.ask("\n[cyan]Select modem preset[/cyan]", default="4")
        if preset_choice in presets:
            preset_name = presets[preset_choice][0]
            result = self._run_cli(['--set', 'lora.modem_preset', preset_name])
            if result and result.returncode == 0:
                console.print(f"[green]Modem preset set to {preset_name}[/green]")

        # Hop limit
        console.print("\n[cyan]Hop Limit:[/cyan]")
        console.print("[dim]Maximum times a message is retransmitted (1-7).[/dim]")
        hop_limit = IntPrompt.ask("Hop limit", default=3)
        if 1 <= hop_limit <= 7:
            self._run_cli(['--set', 'lora.hop_limit', str(hop_limit)])
            console.print(f"[green]Hop limit set to {hop_limit}[/green]")

        input("\nPress Enter to continue...")

    def _configure_channel_settings(self):
        """Configure channel settings"""
        console.print("\n[bold cyan]── Channel Settings ──[/bold cyan]\n")

        # Use existing LoRa configurator for full channel config
        from config.lora import LoRaConfigurator
        lora_config = LoRaConfigurator()
        lora_config.configure_channels()

    def _configure_mqtt_settings(self):
        """Configure MQTT settings"""
        console.print("\n[bold cyan]── MQTT Settings ──[/bold cyan]\n")

        console.print("[dim]MQTT bridges your mesh to the internet for remote monitoring.[/dim]\n")

        # Enable MQTT
        mqtt_enabled = Confirm.ask("[cyan]Enable MQTT?[/cyan]", default=False)
        self._run_cli(['--set', 'mqtt.enabled', 'true' if mqtt_enabled else 'false'])

        if not mqtt_enabled:
            console.print("[green]MQTT disabled[/green]")
            input("\nPress Enter to continue...")
            return

        # Server address
        console.print("\n[cyan]MQTT Server:[/cyan]")
        server = Prompt.ask("Server address", default="mqtt.meshtastic.org")
        self._run_cli(['--set', 'mqtt.address', server])

        # Username/Password
        if Confirm.ask("\n[cyan]Configure authentication?[/cyan]", default=False):
            username = Prompt.ask("Username", default="")
            if username:
                self._run_cli(['--set', 'mqtt.username', username])
                password = Prompt.ask("Password", password=True)
                self._run_cli(['--set', 'mqtt.password', password])

        # Encryption
        console.print("\n[cyan]MQTT Encryption:[/cyan]")
        console.print("[dim]Encryption protects mesh traffic over MQTT.[/dim]")
        encryption = Confirm.ask("Enable MQTT encryption?", default=True)
        self._run_cli(['--set', 'mqtt.encryption_enabled', 'true' if encryption else 'false'])

        # JSON output
        json_enabled = Confirm.ask("Enable JSON output (for integrations)?", default=False)
        self._run_cli(['--set', 'mqtt.json_enabled', 'true' if json_enabled else 'false'])

        # TLS
        tls_enabled = Confirm.ask("Enable TLS (secure connection)?", default=True)
        self._run_cli(['--set', 'mqtt.tls_enabled', 'true' if tls_enabled else 'false'])

        # Root topic
        console.print("\n[cyan]MQTT Root Topic:[/cyan]")
        root = Prompt.ask("Root topic", default="msh/US")
        self._run_cli(['--set', 'mqtt.root', root])

        console.print("\n[green]MQTT settings configured![/green]")
        console.print("[yellow]Remember to enable uplink/downlink per channel.[/yellow]")
        input("\nPress Enter to continue...")

    def _configure_telemetry_settings(self):
        """Configure telemetry settings"""
        console.print("\n[bold cyan]── Telemetry Settings ──[/bold cyan]\n")

        # Device metrics
        console.print("[cyan]Device Metrics:[/cyan]")
        console.print("[dim]Battery, voltage, channel utilization.[/dim]")
        device_interval = IntPrompt.ask("Device metrics interval (seconds)", default=900)
        self._run_cli(['--set', 'telemetry.device_update_interval', str(device_interval)])

        # Environment metrics
        console.print("\n[cyan]Environment Metrics:[/cyan]")
        console.print("[dim]Temperature, humidity, pressure (requires sensors).[/dim]")
        env_enabled = Confirm.ask("Enable environment telemetry?", default=False)
        self._run_cli(['--set', 'telemetry.environment_measurement_enabled', 'true' if env_enabled else 'false'])

        if env_enabled:
            env_interval = IntPrompt.ask("Environment update interval (seconds)", default=900)
            self._run_cli(['--set', 'telemetry.environment_update_interval', str(env_interval)])

            fahrenheit = Confirm.ask("Display temperature in Fahrenheit?", default=False)
            self._run_cli(['--set', 'telemetry.environment_display_fahrenheit', 'true' if fahrenheit else 'false'])

        # Power metrics
        console.print("\n[cyan]Power Metrics:[/cyan]")
        power_enabled = Confirm.ask("Enable power telemetry (INA sensors)?", default=False)
        self._run_cli(['--set', 'telemetry.power_measurement_enabled', 'true' if power_enabled else 'false'])

        console.print("\n[green]Telemetry settings configured![/green]")
        input("\nPress Enter to continue...")

    def _configure_store_forward(self):
        """Configure Store & Forward module"""
        console.print("\n[bold cyan]── Store & Forward ──[/bold cyan]\n")

        console.print("[dim]Store messages for nodes that were offline and deliver when they return.[/dim]\n")

        enabled = Confirm.ask("[cyan]Enable Store & Forward?[/cyan]", default=False)
        self._run_cli(['--set', 'store_forward.enabled', 'true' if enabled else 'false'])

        if not enabled:
            input("\nPress Enter to continue...")
            return

        # Heartbeat
        heartbeat = Confirm.ask("Send heartbeat broadcasts?", default=True)
        self._run_cli(['--set', 'store_forward.heartbeat', 'true' if heartbeat else 'false'])

        # Records
        records = IntPrompt.ask("Maximum messages to store", default=100)
        self._run_cli(['--set', 'store_forward.records', str(records)])

        # History return max
        history_max = IntPrompt.ask("Maximum messages to return per request", default=25)
        self._run_cli(['--set', 'store_forward.history_return_max', str(history_max)])

        console.print("\n[green]Store & Forward configured![/green]")
        input("\nPress Enter to continue...")

    def _configure_all_modules(self):
        """Configure all modules menu"""
        from config.modules import ModuleConfigurator
        configurator = ModuleConfigurator()
        configurator.interactive_module_config()

    def _view_current_config(self):
        """View current device configuration"""
        console.print("\n[bold cyan]── Current Configuration ──[/bold cyan]\n")

        console.print("[cyan]Fetching device configuration...[/cyan]\n")

        result = self._run_cli(['--info'], timeout=60)

        if result and result.returncode == 0:
            console.print(result.stdout)
        else:
            console.print("[yellow]Could not retrieve device info[/yellow]")
            if result:
                console.print(f"[dim]{result.stderr}[/dim]")

        input("\nPress Enter to continue...")

    def _reset_to_defaults(self):
        """Reset node to factory defaults"""
        console.print("\n[bold red]── Reset to Factory Defaults ──[/bold red]\n")

        console.print("[yellow]WARNING: This will erase all settings and reset the node![/yellow]")
        console.print("[dim]- All channels will be reset to defaults")
        console.print("- All module settings will be cleared")
        console.print("- Device will need to be reconfigured[/dim]\n")

        if not Confirm.ask("[red]Are you sure you want to reset?[/red]", default=False):
            console.print("[green]Reset cancelled[/green]")
            input("\nPress Enter to continue...")
            return

        # Double confirm
        if not Confirm.ask("[red]This cannot be undone. Really reset?[/red]", default=False):
            console.print("[green]Reset cancelled[/green]")
            input("\nPress Enter to continue...")
            return

        console.print("\n[cyan]Resetting node to factory defaults...[/cyan]")
        result = self._run_cli(['--factory-reset'])

        if result and result.returncode == 0:
            console.print("[green]Node reset successfully![/green]")
            console.print("[yellow]Please reconfigure your node.[/yellow]")
        else:
            console.print("[red]Reset may have failed. Check device.[/red]")

        input("\nPress Enter to continue...")


def radio_config_menu():
    """Entry point for radio configuration"""
    config = RadioConfig()
    config.interactive_menu()
