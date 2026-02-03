"""
Meshtasticd Configuration Mixin for MeshForge Launcher TUI.

Handles all meshtasticd service configuration, radio presets,
hardware config, and channel management.
Extracted from main.py to reduce file size.
"""

import subprocess
import sys
from pathlib import Path

# Import centralized service checker - SINGLE SOURCE OF TRUTH
try:
    from utils.service_check import (
        check_service,
        check_systemd_service,
        ServiceState,
        apply_config_and_restart,
    )
    _HAS_APPLY_RESTART = True
except ImportError:
    check_service = None
    check_systemd_service = None
    ServiceState = None
    _HAS_APPLY_RESTART = False


class MeshtasticdConfigMixin:
    """Mixin providing meshtasticd configuration methods for the launcher."""

    def _meshtasticd_menu(self):
        """Meshtasticd configuration menu."""
        while True:
            choices = [
                ("web", "Web Client (Full Config)"),
                ("status", "Service Status"),
                ("owner", "Set Owner/Node Name"),
                ("presets", "Radio Presets (LoRa)"),
                ("hardware", "Hardware Config"),
                ("channels", "Channel Config"),
                ("mqtt", "MQTT Uplink/Downlink"),
                ("gateway", "Gateway Template"),
                ("edit", "Edit Config Files"),
                ("restart", "Restart Service"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtasticd Config",
                "Configure meshtasticd radio daemon:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "web":
                self._show_web_client_info()
            elif choice == "status":
                self._meshtasticd_status()
            elif choice == "owner":
                self._set_owner_name()
            elif choice == "presets":
                self._radio_presets_menu()
            elif choice == "hardware":
                self._hardware_config_menu()
            elif choice == "channels":
                self._channel_config_menu()
            elif choice == "mqtt":
                self._mqtt_device_config()
            elif choice == "gateway":
                self._gateway_template_menu()
            elif choice == "edit":
                self._edit_config_menu()
            elif choice == "restart":
                self._restart_meshtasticd()

    def _show_web_client_info(self):
        """Show meshtasticd web client with browser launch option.

        Delegates to _open_web_client() in main.py which provides:
        - Browser launch functionality
        - URL display for copying
        - SSL certificate acceptance guidance
        """
        # Call the unified web client handler from main.py (inherited via mixin)
        if hasattr(self, '_open_web_client'):
            self._open_web_client()
        else:
            # Fallback if method not available (shouldn't happen)
            import socket
            try:
                local_ip = socket.gethostbyname(socket.gethostname())
                if local_ip.startswith('127.'):
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.settimeout(2)
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    s.close()
            except Exception:
                local_ip = "YOUR_PI_IP"

            self.dialog.msgbox(
                "Meshtastic Web Client",
                f"Full radio configuration via browser:\n\n"
                f"  URL: https://{local_ip}:9443\n\n"
                f"Set these to join your mesh network:\n"
                f"  Config → LoRa → Region  (US, EU_868, etc.)\n"
                f"  Config → LoRa → Preset  (LONG_FAST, etc.)\n"
                f"  Config → Channels       (PSK, name)\n\n"
                f"The web client gives full access to all\n"
                f"meshtasticd settings, maps, and messaging."
            )

    def _meshtasticd_status(self):
        """Show meshtasticd service status."""
        self.dialog.infobox("Status", "Checking meshtasticd status...")

        try:
            # Use centralized service checker (SINGLE SOURCE OF TRUTH)
            if check_service is not None and check_systemd_service is not None:
                status = check_service('meshtasticd')
                is_running = status.available
                _, is_enabled = check_systemd_service('meshtasticd')
                output = status.message
            else:
                # Fallback if service_check not available
                result = subprocess.run(
                    ['systemctl', 'status', 'meshtasticd'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                output = result.stdout
                is_running = "active (running)" in output
                is_enabled = subprocess.run(
                    ['systemctl', 'is-enabled', 'meshtasticd'],
                    capture_output=True, text=True, timeout=5
                ).returncode == 0

            # Get config file info
            config_path = Path('/etc/meshtasticd/config.yaml')
            config_exists = config_path.exists()

            # Check active configs
            config_d = Path('/etc/meshtasticd/config.d')
            active_configs = list(config_d.glob('*.yaml')) if config_d.exists() else []

            text = f"""Meshtasticd Service Status:

Service: {'running' if is_running else 'stopped'}
Boot:    {'enabled' if is_enabled else 'not enabled (will not start on reboot)'}

Config File: {config_path}
Config Exists: {'Yes' if config_exists else 'No'}

Active Hardware Configs: {len(active_configs)}"""

            for cfg in active_configs[:5]:
                text += f"\n  - {cfg.name}"

            if len(active_configs) > 5:
                text += f"\n  ... and {len(active_configs) - 5} more"

            self.dialog.msgbox("Meshtasticd Status", text)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get status:\n{e}")

    def _set_owner_name(self):
        """Set node owner name (long name and short name)."""
        self.dialog.infobox("Owner", "Getting current owner info...")

        try:
            sys.path.insert(0, str(self.src_dir))
            from commands import meshtastic as mesh_cmd

            # Try to get current owner info
            result = mesh_cmd.get_node_info()
            current_long = ""
            current_short = ""

            if result.success and result.raw:
                # Parse owner info from output
                for line in result.raw.split('\n'):
                    if 'longName' in line or 'long_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_long = parts[1].strip().strip('"')
                    elif 'shortName' in line or 'short_name' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            current_short = parts[1].strip().strip('"')

            # Show current info and get new values
            text = f"""Set your node's identity:

Current Long Name: {current_long or '(not set)'}
Current Short Name: {current_short or '(not set)'}

Long Name: Your node's full name (max 40 chars)
           Shown on other devices, maps, etc.

Short Name: 4-character abbreviation
           Shown in compact views

Press Cancel to keep current values."""

            # Get long name
            long_name = self.dialog.inputbox(
                "Set Long Name",
                f"Enter node name (current: {current_long or 'none'}):",
                current_long or ""
            )

            if long_name is None:  # Cancelled
                return

            # Get short name
            short_name = self.dialog.inputbox(
                "Set Short Name",
                f"Enter 4-char short name (current: {current_short or 'none'}):",
                current_short or ""
            )

            if short_name is None:  # Cancelled
                return

            # Validate
            if long_name:
                long_name = long_name[:40]
            if short_name:
                short_name = short_name[:4].upper()

            # Apply changes
            changes_made = []

            if long_name:
                self.dialog.infobox("Setting", f"Setting long name to: {long_name}")
                result = mesh_cmd.set_owner(long_name)
                if result.success:
                    changes_made.append(f"Long name: {long_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set long name:\n{result.message}")
                    return

            if short_name:
                self.dialog.infobox("Setting", f"Setting short name to: {short_name}")
                result = mesh_cmd.set_owner_short(short_name)
                if result.success:
                    changes_made.append(f"Short name: {short_name}")
                else:
                    self.dialog.msgbox("Error", f"Failed to set short name:\n{result.message}")
                    return

            if changes_made:
                self.dialog.msgbox("Success", f"Owner settings updated:\n\n" + "\n".join(changes_made))
            else:
                self.dialog.msgbox("Info", "No changes made.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to set owner name:\n{e}")

    def _radio_presets_menu(self):
        """Radio/LoRa preset selection via meshtastic CLI."""
        # Define modem presets with descriptions
        presets = [
            ("SHORT_TURBO", "500kHz SF7  - Max speed, <1km"),
            ("SHORT_FAST", "250kHz SF7  - Urban, 1-5km"),
            ("SHORT_SLOW", "125kHz SF7  - Reliable short"),
            ("MEDIUM_FAST", "250kHz SF10 - MtnMesh std, 5-20km"),
            ("MEDIUM_SLOW", "125kHz SF10 - Alt medium"),
            ("LONG_FAST", "250kHz SF11 - Default, 10-30km"),
            ("LONG_MODERATE", "125kHz SF11 - Extended, 15-40km"),
            ("LONG_SLOW", "125kHz SF12 - Max range, 20-50km"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Radio Presets",
            "Select LoRa modem preset:\n\n"
            "Higher speed = shorter range\n"
            "Lower speed = longer range",
            presets
        )

        if choice and choice != "back":
            self._apply_radio_preset(choice)

    def _apply_radio_preset(self, preset: str):
        """Apply a radio preset via meshtastic CLI (not config.d YAML)."""
        # Preset display info (for confirmation dialog only)
        preset_info = {
            "SHORT_TURBO": {"bw": 500, "sf": 7, "cr": 8},
            "SHORT_FAST": {"bw": 250, "sf": 7, "cr": 8},
            "SHORT_SLOW": {"bw": 125, "sf": 7, "cr": 8},
            "MEDIUM_FAST": {"bw": 250, "sf": 10, "cr": 8},
            "MEDIUM_SLOW": {"bw": 125, "sf": 10, "cr": 8},
            "LONG_FAST": {"bw": 250, "sf": 11, "cr": 8},
            "LONG_MODERATE": {"bw": 125, "sf": 11, "cr": 8},
            "LONG_SLOW": {"bw": 125, "sf": 12, "cr": 8},
        }

        info = preset_info.get(preset, {})
        if not info:
            return

        # Ask for frequency slot
        slot_input = self.dialog.inputbox(
            "Frequency Slot",
            f"Set frequency slot (channel_num) for {preset}:\n\n"
            "Slot determines the center frequency.\n"
            "US: 0=903.875 MHz (default), 12=903.625 (HawaiiNet)\n"
            "Must match your mesh network's slot.\n\n"
            "Leave empty or 0 for default:",
            "0"
        )

        if slot_input is None:  # Cancelled
            return

        try:
            freq_slot = int(slot_input) if slot_input.strip() else 0
        except ValueError:
            freq_slot = 0

        # Build confirmation text
        confirm_text = (
            f"Apply {preset} preset?\n\n"
            f"Bandwidth: {info['bw']} kHz\n"
            f"Spreading Factor: SF{info['sf']}\n"
            f"Coding Rate: 4/{info['cr']}\n"
            f"Frequency Slot: {freq_slot}\n\n"
            "Applied via meshtastic CLI (--set lora.modem_preset).\n"
            "Region must already be set (use Web Client)."
        )

        confirm = self.dialog.yesno(
            "Apply Preset",
            confirm_text,
            default_no=True
        )

        if not confirm:
            return

        self.dialog.infobox("Applying", f"Applying {preset} preset...")

        try:
            from core.meshtastic_cli import get_cli
            cli = get_cli()

            # Apply modem preset
            result = cli.set_lora_preset(preset)
            if not result.success:
                self.dialog.msgbox("Error",
                    f"Failed to set modem preset:\n{result.error}\n\n"
                    "Ensure meshtastic CLI is installed and\n"
                    "meshtasticd is running with region set.")
                return

            # Apply frequency slot
            slot_result = cli.set_channel_num(freq_slot)
            slot_msg = ""
            if not slot_result.success:
                slot_msg = f"\nFrequency slot: FAILED ({slot_result.error})"
            else:
                slot_msg = f"\nFrequency slot: {freq_slot}"

            self.dialog.msgbox("Success",
                f"{preset} preset applied!\n\n"
                f"Modem preset: {preset}{slot_msg}\n\n"
                "Settings applied via meshtastic CLI.\n"
                "Device will reboot to apply changes.")

        except ImportError:
            # Fallback: direct subprocess call
            try:
                cli_path = self._get_meshtastic_cli()
                result = subprocess.run(
                    [cli_path, '--host', 'localhost:4403',
                     '--set', 'lora.modem_preset', preset],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0:
                    self.dialog.msgbox("Error",
                        f"Failed to apply preset:\n{result.stderr or result.stdout}")
                    return

                subprocess.run(
                    [cli_path, '--host', 'localhost:4403',
                     '--set', 'lora.channel_num', str(freq_slot)],
                    capture_output=True, text=True, timeout=30
                )

                self.dialog.msgbox("Success",
                    f"{preset} preset applied!\n"
                    f"Frequency slot: {freq_slot}")

            except Exception as e:
                self.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to apply preset:\n{e}")

    def _hardware_config_menu(self):
        """Hardware configuration selection."""
        available_dir = Path('/etc/meshtasticd/available.d')
        config_d = Path('/etc/meshtasticd/config.d')

        if not available_dir.exists():
            self.dialog.msgbox("Error",
                "Hardware templates not found.\n\n"
                f"Expected: {available_dir}\n\n"
                "Run the installer to set up templates.")
            return

        # List available hardware configs
        available = list(available_dir.glob('*.yaml'))
        if not available:
            self.dialog.msgbox("Error", "No hardware templates found.")
            return

        # Get currently active configs
        active = set()
        if config_d.exists():
            active = {f.name for f in config_d.glob('*.yaml')}

        choices = []
        for cfg in sorted(available):
            status = "[ACTIVE]" if cfg.name in active else ""
            # Truncate name for display
            name = cfg.stem[:25]
            choices.append((cfg.name, f"{name} {status}"))

        choices.append(("view", "View Config Details"))
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "Hardware Config",
            "Select hardware configuration to activate:\n\n"
            f"Templates: {available_dir}\n"
            f"Active: {config_d}",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "view":
            self._view_hardware_config(available)
        else:
            self._activate_hardware_config(choice, available_dir, config_d)

    def _activate_hardware_config(self, config_name: str, available_dir: Path, config_d: Path):
        """Activate a hardware configuration."""
        src = available_dir / config_name

        if not src.exists():
            self.dialog.msgbox("Error", f"Config not found: {src}")
            return

        confirm = self.dialog.yesno(
            "Activate Config",
            f"Activate hardware config?\n\n"
            f"Template: {config_name}\n\n"
            "This will:\n"
            f"1. Copy to {config_d}/\n"
            "2. Restart meshtasticd service",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Activating", f"Activating {config_name}...")

            # Create config.d if needed
            config_d.mkdir(parents=True, exist_ok=True)

            # Copy config
            import shutil
            dst = config_d / config_name
            shutil.copy(src, dst)

            # Restart service
            if _HAS_APPLY_RESTART:
                success, msg = apply_config_and_restart('meshtasticd')
            else:
                subprocess.run(['systemctl', 'daemon-reload'],
                               capture_output=True, timeout=10)
                subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                               capture_output=True, timeout=30)

            self.dialog.msgbox("Success",
                f"Hardware config activated!\n\n"
                f"Config: {dst}\n\n"
                "Service restarted.")

        except Exception as e:
            self.dialog.msgbox("Error", f"Activation failed:\n{e}")

    def _view_hardware_config(self, configs: list):
        """View details of a hardware config."""
        choices = [(cfg.name, cfg.stem[:30]) for cfg in sorted(configs)]
        choices.append(("back", "Back"))

        choice = self.dialog.menu(
            "View Config",
            "Select config to view:",
            choices
        )

        if choice and choice != "back":
            config_path = Path('/etc/meshtasticd/available.d') / choice
            if config_path.exists():
                try:
                    content = config_path.read_text()[:1500]
                    self.dialog.msgbox(f"Config: {choice}", content)
                except Exception as e:
                    self.dialog.msgbox("Error", str(e))

    def _edit_config_menu(self):
        """Edit config files directly."""
        choices = [
            ("main", "Main Config (/etc/meshtasticd/config.yaml)"),
            ("active", "Active Hardware Configs"),
            ("templates", "Hardware Templates"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Edit Config Files",
            "Edit meshtasticd configuration files:\n\n"
            "Opens in nano editor.\n"
            "Save: Ctrl+O, Exit: Ctrl+X",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "main":
            self._edit_file('/etc/meshtasticd/config.yaml')
        elif choice == "active":
            self._edit_config_d()
        elif choice == "templates":
            self._edit_available_d()

    def _edit_file(self, path: str):
        """Edit a file with nano."""
        if not Path(path).exists():
            self.dialog.msgbox("Error", f"File not found:\n{path}")
            return

        # Clear screen and run nano
        subprocess.run(['clear'], check=False, timeout=5)
        subprocess.run(['nano', path])  # Interactive editor - no timeout

        # Ask to restart service
        if self.dialog.yesno(
            "Restart Service?",
            "Config file modified.\n\n"
            "Restart meshtasticd to apply changes?",
            default_no=False
        ):
            self._restart_meshtasticd()

    def _edit_config_d(self):
        """Edit files in config.d."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{config_d}")
            return

        configs = list(config_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No active configs in config.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
            "Active Configs",
            "Select config to edit (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _edit_available_d(self):
        """Edit files in available.d."""
        available_d = Path('/etc/meshtasticd/available.d')
        if not available_d.exists():
            self.dialog.msgbox("Error", f"Directory not found:\n{available_d}")
            return

        configs = list(available_d.glob('*.yaml'))
        if not configs:
            self.dialog.msgbox("Info", "No templates in available.d/")
            return

        choices = [(str(cfg), cfg.name) for cfg in sorted(configs)]

        choice = self.dialog.menu(
            "Hardware Templates",
            "Select template to view (Cancel to go back):",
            choices
        )

        if choice:
            self._edit_file(choice)

    def _restart_meshtasticd(self):
        """Restart meshtasticd service."""
        confirm = self.dialog.yesno(
            "Restart Service",
            "Restart meshtasticd?\n\n"
            "This will:\n"
            "1. Reload systemd daemon\n"
            "2. Restart meshtasticd service\n"
            "3. Apply any config changes",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.dialog.infobox("Restarting", "Restarting meshtasticd...")

            if _HAS_APPLY_RESTART:
                success, msg = apply_config_and_restart('meshtasticd')
                if success:
                    self.dialog.msgbox("Success", "meshtasticd restarted successfully!")
                else:
                    self.dialog.msgbox("Error", f"Restart failed:\n{msg}")
            else:
                subprocess.run(['systemctl', 'daemon-reload'],
                               capture_output=True, timeout=10)

                result = subprocess.run(
                    ['systemctl', 'restart', 'meshtasticd'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    self.dialog.msgbox("Success", "meshtasticd restarted successfully!")
                else:
                    self.dialog.msgbox("Error",
                        f"Restart failed:\n{result.stderr or result.stdout}")

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Restart timed out")
        except Exception as e:
            self.dialog.msgbox("Error", f"Restart failed:\n{e}")

    def _mqtt_device_config(self):
        """Configure MQTT uplink/downlink for the Meshtastic radio.

        This configures the radio to send/receive messages via an MQTT broker,
        enabling integration with the Meshtastic MQTT network.
        """
        while True:
            choices = [
                ("view", "View Current Settings"),
                ("enable", "Enable MQTT Uplink"),
                ("disable", "Disable MQTT"),
                ("broker", "Set Broker Address"),
                ("credentials", "Set Username/Password"),
                ("topic", "Set Root Topic"),
                ("encryption", "Encryption Key (PKC)"),
                ("uplink", "Configure Uplink Channels"),
                ("downlink", "Configure Downlink Channels"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "MQTT Device Config",
                "Configure radio MQTT uplink/downlink:\n\n"
                "This sends mesh traffic to an MQTT broker.",
                choices
            )

            if choice is None or choice == "back":
                break

            cli = self._get_meshtastic_cli()
            conn_args = ['--host', 'localhost']

            if choice == "view":
                self._mqtt_view_settings()
            elif choice == "enable":
                self._mqtt_set_enabled(True)
            elif choice == "disable":
                self._mqtt_set_enabled(False)
            elif choice == "broker":
                self._mqtt_set_broker()
            elif choice == "credentials":
                self._mqtt_set_credentials()
            elif choice == "topic":
                self._mqtt_set_topic()
            elif choice == "encryption":
                self._mqtt_set_encryption()
            elif choice == "uplink":
                self._mqtt_configure_uplink()
            elif choice == "downlink":
                self._mqtt_configure_downlink()

    def _mqtt_view_settings(self):
        """View current MQTT settings."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== MQTT Settings ===\n")
        cli = self._get_meshtastic_cli()
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--get', 'mqtt'],
                timeout=15
            )
            if result.returncode != 0:
                print("\nFailed to get MQTT settings.")
                print("Is meshtasticd running?")
        except FileNotFoundError:
            print("meshtastic CLI not found. Install via Radio Tools menu.")
        except subprocess.TimeoutExpired:
            print("\nCommand timed out.")
        except KeyboardInterrupt:
            print("\nAborted.")
        self._wait_for_enter()

    def _mqtt_set_enabled(self, enabled: bool):
        """Enable or disable MQTT."""
        action = "enable" if enabled else "disable"
        if not self.dialog.yesno(
            f"{'Enable' if enabled else 'Disable'} MQTT",
            f"{'Enable' if enabled else 'Disable'} MQTT uplink/downlink?\n\n"
            f"{'This will start sending mesh traffic to the MQTT broker.' if enabled else 'This will stop MQTT traffic.'}"
        ):
            return

        cli = self._get_meshtastic_cli()
        subprocess.run(['clear'], check=False, timeout=5)
        print(f"=== {'Enabling' if enabled else 'Disabling'} MQTT ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.enabled', str(enabled).lower()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT {'enabled' if enabled else 'disabled'} successfully.")
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_broker(self):
        """Set MQTT broker address."""
        broker = self.dialog.inputbox(
            "MQTT Broker",
            "Enter MQTT broker address:\n\n"
            "Examples:\n"
            "  mqtt.meshtastic.org (public)\n"
            "  192.168.1.100 (local)\n"
            "  mybroker.example.com:1883",
            init="mqtt.meshtastic.org"
        )

        if not broker:
            return

        cli = self._get_meshtastic_cli()
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Setting MQTT Broker ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.address', broker.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT broker set to: {broker}")
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_credentials(self):
        """Set MQTT username and password."""
        username = self.dialog.inputbox(
            "MQTT Username",
            "Enter MQTT username (blank for anonymous):",
            init=""
        )

        if username is None:
            return

        password = self.dialog.inputbox(
            "MQTT Password",
            "Enter MQTT password (blank for none):",
            init=""
        )

        if password is None:
            return

        cli = self._get_meshtastic_cli()
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Setting MQTT Credentials ===\n")
        try:
            cmd = [cli, '--host', 'localhost']
            if username:
                cmd.extend(['--set', 'mqtt.username', username])
            if password:
                cmd.extend(['--set', 'mqtt.password', password])

            if len(cmd) > 3:  # Has settings to apply
                result = subprocess.run(cmd, timeout=15)
                if result.returncode == 0:
                    print("\nMQTT credentials updated.")
                else:
                    print("\nCommand failed.")
            else:
                print("No credentials to set.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_topic(self):
        """Set MQTT root topic."""
        topic = self.dialog.inputbox(
            "MQTT Root Topic",
            "Enter MQTT root topic:\n\n"
            "Default: msh\n"
            "Full topic pattern: {root}/{region}/2/e/{channel}/...",
            init="msh"
        )

        if not topic:
            return

        cli = self._get_meshtastic_cli()
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Setting MQTT Topic ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.root', topic.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT root topic set to: {topic}")
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self._wait_for_enter()

    def _mqtt_set_encryption(self):
        """Configure MQTT encryption key (Public Key Cryptography)."""
        self.dialog.msgbox(
            "MQTT Encryption",
            "MQTT Encryption Options:\n\n"
            "1. JSON mode (default): Messages sent as plaintext JSON\n"
            "2. PKC mode: Messages encrypted with channel key\n\n"
            "PKC mode requires:\n"
            "  - encryption_enabled = true\n"
            "  - A valid channel PSK\n\n"
            "Configure encryption via:\n"
            "  --set mqtt.encryption_enabled true\n"
            "  --set mqtt.json_enabled false"
        )

        choices = [
            ("json", "JSON Mode (plaintext, human-readable)"),
            ("encrypted", "Encrypted Mode (PKC, secure)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "MQTT Encryption Mode",
            "Select MQTT message format:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self._get_meshtastic_cli()
        subprocess.run(['clear'], check=False, timeout=5)

        if choice == "json":
            print("=== Setting JSON Mode ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--set', 'mqtt.json_enabled', 'true',
                     '--set', 'mqtt.encryption_enabled', 'false'],
                    timeout=15
                )
                print("\nMQTT set to JSON mode (plaintext).")
            except Exception as e:
                print(f"\nError: {e}")
        else:
            print("=== Setting Encrypted Mode ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--set', 'mqtt.json_enabled', 'false',
                     '--set', 'mqtt.encryption_enabled', 'true'],
                    timeout=15
                )
                print("\nMQTT set to encrypted mode (PKC).")
                print("Messages will be encrypted with channel PSK.")
            except Exception as e:
                print(f"\nError: {e}")

        self._wait_for_enter()

    def _mqtt_configure_uplink(self):
        """Configure which channels uplink to MQTT."""
        self.dialog.msgbox(
            "MQTT Uplink",
            "MQTT Uplink sends local mesh messages to the broker.\n\n"
            "Per-channel uplink is configured via:\n"
            "  Channel Config → Edit Channel → Uplink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set uplink_enabled true"
        )

        # Offer to enable uplink on primary channel
        if self.dialog.yesno(
            "Enable Primary Uplink",
            "Enable MQTT uplink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self._get_meshtastic_cli()
            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Enabling Uplink ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--ch-index', '0', '--ch-set', 'uplink_enabled', 'true'],
                    timeout=15
                )
                print("\nUplink enabled on primary channel.")
            except Exception as e:
                print(f"\nError: {e}")
            self._wait_for_enter()

    def _mqtt_configure_downlink(self):
        """Configure which channels downlink from MQTT."""
        self.dialog.msgbox(
            "MQTT Downlink",
            "MQTT Downlink receives broker messages to local mesh.\n\n"
            "Per-channel downlink is configured via:\n"
            "  Channel Config → Edit Channel → Downlink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set downlink_enabled true"
        )

        # Offer to enable downlink on primary channel
        if self.dialog.yesno(
            "Enable Primary Downlink",
            "Enable MQTT downlink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self._get_meshtastic_cli()
            subprocess.run(['clear'], check=False, timeout=5)
            print("=== Enabling Downlink ===\n")
            try:
                subprocess.run(
                    [cli, '--host', 'localhost',
                     '--ch-index', '0', '--ch-set', 'downlink_enabled', 'true'],
                    timeout=15
                )
                print("\nDownlink enabled on primary channel.")
            except Exception as e:
                print(f"\nError: {e}")
            self._wait_for_enter()
