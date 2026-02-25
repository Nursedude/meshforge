"""
Radio Menu Mixin - Meshtastic radio control menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import os
import shutil
import subprocess
from pathlib import Path
from backend import clear_screen
from utils.paths import get_real_user_home


class RadioMenuMixin:
    """Mixin providing Meshtastic radio control menu functionality."""

    def _radio_menu(self):
        """Radio tools using meshtastic CLI directly."""
        while True:
            # Check if CLI is available and actually working
            cli_path = self._get_meshtastic_cli()
            has_cli = cli_path != 'meshtastic'

            # Even if found, verify it's actually executable
            cli_works = False
            cli_location = ""
            if has_cli:
                cli_location = cli_path
                try:
                    # Quick test - just check if we can run --version
                    result = subprocess.run(
                        [cli_path, '--version'],
                        capture_output=True, timeout=5
                    )
                    cli_works = result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
                    cli_works = False

            choices = []
            if not cli_works:
                choices.append(("install-cli", "** Install meshtastic CLI **"))
            choices.extend([
                ("info", "Radio Info (meshtastic --info)"),
                ("nodes", "Node List (meshtastic --nodes)"),
                ("favorites", "Favorites (BaseUI 2.7+)"),
                ("channels", "Channel Info"),
                ("send", "Send Message"),
                ("position", "Position (view/set)"),
                ("set-region", "Set Region"),
                ("set-txpower", "Set TX Power"),
                ("set-name", "Set Node Name"),
                ("reboot", "Reboot Radio"),
                ("reinstall-cli", "Reinstall/Update CLI"),
                ("back", "Back"),
            ])

            if cli_works:
                status = f"\n[CLI: {cli_location}]"
            elif has_cli:
                status = f"\n[CLI found but not working: {cli_location}]"
            else:
                status = "\n[CLI not installed]"

            choice = self.dialog.menu(
                "Radio Tools",
                f"Meshtastic radio control (terminal-native):{status}",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice in ("install-cli", "reinstall-cli"):
                self._safe_call("Install CLI", self._install_meshtastic_cli)
                continue

            dispatch = {
                "favorites": ("Favorites", self._favorites_menu),
                "position": ("Position", self._radio_position_menu),
                "send": ("Send Message", self._radio_send_message),
                "set-region": ("Set Region", self._radio_set_region),
                "set-txpower": ("Set TX Power", self._radio_set_tx_power),
                "set-name": ("Set Node Name", self._radio_set_name),
                "reboot": ("Reboot Radio", self._radio_reboot),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)
                continue

            # CLI commands that build args dynamically
            try:
                cli = self._get_meshtastic_cli()
                conn_args = ['--host', 'localhost']
                if choice == "info":
                    self._radio_run([cli] + conn_args + ['--info'], "Radio Info")
                elif choice == "nodes":
                    self._radio_run([cli] + conn_args + ['--nodes'], "Node List")
                elif choice == "channels":
                    self._radio_run([cli] + conn_args + ['--ch-index', '0', '--ch-getall'], "Channels")
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.dialog.msgbox(
                    "Radio Error",
                    f"Operation failed:\n{type(e).__name__}: {e}\n\n"
                    f"Check that meshtasticd is running:\n"
                    f"  sudo systemctl status meshtasticd"
                )

    def _radio_run(self, cmd: list, title: str):
        """Run a meshtastic CLI command and show output in terminal."""
        clear_screen()
        print(f"=== {title} ===")
        print("(Ctrl+C to abort)\n")
        try:
            result = subprocess.run(cmd, timeout=30)
            if result.returncode != 0:
                print(f"\nCommand failed (exit {result.returncode})")
                print("Is meshtasticd running? Check: systemctl status meshtasticd")
        except FileNotFoundError:
            self._offer_install_meshtastic_cli()
            return
        except subprocess.TimeoutExpired:
            print("\n\nCommand timed out (30s). Radio may not be connected.")
            print("Check: systemctl status meshtasticd")
        except KeyboardInterrupt:
            print("\n\nAborted.")
        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _offer_install_meshtastic_cli(self):
        """Offer to install meshtastic CLI when it's missing (from error handler)."""
        install = self.dialog.yesno(
            "Meshtastic CLI Not Found",
            "The 'meshtastic' CLI is not installed.\n\n"
            "This is needed to configure the radio\n"
            "(set presets, region, node name, etc.).\n\n"
            "Install meshtastic CLI now?",
            default_no=False
        )
        if install:
            self._install_meshtastic_cli()

    def _install_meshtastic_cli(self):
        """Install meshtastic CLI via pipx with live terminal output."""
        clear_screen()
        print("=== Installing Meshtastic CLI ===\n")

        # Determine if we should install as a different user (when running via sudo)
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Ensure pipx is available (this needs root for apt)
            if not shutil.which('pipx'):
                print("Installing pipx...\n")
                result = subprocess.run(
                    ['apt-get', 'install', '-y', 'pipx'],
                    timeout=60
                )
                if result.returncode != 0:
                    print("\nFailed to install pipx.")
                    print("Try manually: sudo apt install pipx")
                    self._wait_for_enter()
                    return

            # Build pipx commands - run as real user if we're under sudo
            def run_pipx_cmd(args, timeout_sec=300):
                """Run pipx command, as real user if running via sudo."""
                if run_as_user:
                    # Run as the real user with login shell (-i) to set HOME correctly
                    # Without -i, HOME stays as /root and pipx installs there
                    cmd = ['sudo', '-i', '-u', run_as_user] + args
                else:
                    cmd = args
                return subprocess.run(cmd, timeout=timeout_sec)

            # Ensure pipx bin dir is in PATH for this session
            print("Ensuring pipx paths...\n")
            run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

            # Add common pipx bin dirs to current process PATH
            for bindir in [
                get_real_user_home() / '.local' / 'bin',
                Path('/root/.local/bin'),
                Path('/usr/local/bin'),
            ]:
                if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

            # Install meshtastic with CLI extras (live output)
            if run_as_user:
                print(f"\nInstalling meshtastic CLI via pipx (as {run_as_user})...\n")
            else:
                print("\nInstalling meshtastic CLI via pipx...\n")
            result = run_pipx_cmd(['pipx', 'install', 'meshtastic[cli]', '--force'])

            if result.returncode != 0:
                print("\nRetrying without [cli] extras...\n")
                result = run_pipx_cmd(['pipx', 'install', 'meshtastic', '--force'])

            if result.returncode == 0:
                # Clear cached path so it gets re-resolved
                self._meshtastic_path = None
                cli_path = self._get_meshtastic_cli()
                if cli_path and cli_path != 'meshtastic':
                    print(f"\n** meshtastic CLI installed: {cli_path} **")
                else:
                    print("\n** meshtastic installed but not found in PATH **")
                    print("You may need to log out and back in,")
                    print("or run: eval \"$(pipx ensurepath)\"")
            else:
                print("\nInstallation failed.")
                print("Try manually: pipx install meshtastic")

        except FileNotFoundError:
            print("pipx not found.")
            print("Try: sudo apt install pipx && pipx install meshtastic")
        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except subprocess.TimeoutExpired:
            print("\n\nInstallation timed out.")
            print("Try manually: pipx install meshtastic")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            print("Try manually: pipx install meshtastic")

        try:
            self._wait_for_enter()
        except KeyboardInterrupt:
            print()

    def _radio_send_message(self):
        """Send a mesh message via meshtastic CLI."""
        msg = self.dialog.inputbox(
            "Send Message",
            "Message text (broadcast to default channel):",
            ""
        )
        if not msg:
            return

        dest = self.dialog.inputbox(
            "Destination",
            "Node ID (e.g. !abc12345)\nLeave empty for broadcast:",
            ""
        )

        cmd = [self._get_meshtastic_cli(), '--host', 'localhost', '--sendtext', msg]
        if dest and dest.strip():
            dest = dest.strip()
            if not dest.startswith('!'):
                dest = '!' + dest
            cmd.extend(['--dest', dest])

        self._radio_run(cmd, "Sending Message")

    def _radio_set_region(self):
        """Set LoRa region via meshtastic CLI."""
        choices = [
            ("US", "US (902-928 MHz)"),
            ("EU_868", "EU_868 (863-870 MHz)"),
            ("CN", "CN (470-510 MHz)"),
            ("JP", "JP (920-925 MHz)"),
            ("ANZ", "ANZ (915-928 MHz)"),
            ("KR", "KR (920-923 MHz)"),
            ("TW", "TW (920-925 MHz)"),
            ("RU", "RU (868-870 MHz)"),
            ("IN", "IN (865-867 MHz)"),
            ("NZ_865", "NZ_865 (864-868 MHz)"),
            ("TH", "TH (920-925 MHz)"),
            ("UA_868", "UA_868 (863-870 MHz)"),
            ("LORA_24", "LORA_24 (2.4 GHz)"),
            ("UNSET", "UNSET (clear region)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Set Region",
            "Select your LoRa region:",
            choices
        )

        if choice is None or choice == "back":
            return

        if self.dialog.yesno("Confirm", f"Set region to {choice}?\n\nRadio will restart."):
            self._radio_run(
                [self._get_meshtastic_cli(), '--host', 'localhost', '--set', 'lora.region', choice],
                f"Setting Region: {choice}"
            )

    def _radio_set_tx_power(self):
        """Set LoRa TX power via meshtastic CLI."""
        # TX power limits by region (dBm)
        # These are approximate regulatory maximums
        region_limits = {
            "US": 30, "EU_868": 14, "EU_433": 10, "CN": 20,
            "JP": 13, "ANZ": 30, "KR": 10, "TW": 14,
            "RU": 20, "IN": 30, "NZ_865": 27, "TH": 20,
            "UA_868": 14, "LORA_24": 10,
        }

        choices = [
            ("1", "1 dBm   (1 mW) - Minimum"),
            ("5", "5 dBm   (3 mW) - Very low"),
            ("10", "10 dBm  (10 mW) - Low"),
            ("14", "14 dBm  (25 mW) - EU limit"),
            ("17", "17 dBm  (50 mW) - Medium"),
            ("20", "20 dBm  (100 mW) - Standard"),
            ("22", "22 dBm  (158 mW) - High"),
            ("27", "27 dBm  (500 mW) - Very high"),
            ("30", "30 dBm  (1 W) - US max"),
            ("custom", "Custom value..."),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Set TX Power",
            "Select transmit power level:\n\n"
            "Note: Check your region's legal limit.\n"
            "Higher power = more range but more battery use.",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "custom":
            power_str = self.dialog.inputbox(
                "Custom TX Power",
                "Enter TX power in dBm (1-30):\n\n"
                "Common limits:\n"
                "  US: 30 dBm (1W)\n"
                "  EU: 14 dBm (25mW)\n"
                "  JP: 13 dBm",
                "20"
            )
            if not power_str:
                return
            try:
                power = int(power_str.strip())
            except ValueError:
                self.dialog.msgbox("Error", "Invalid power value. Enter a number 1-30.")
                return
        else:
            power = int(choice)

        if not (1 <= power <= 30):
            self.dialog.msgbox("Error", "TX power must be between 1 and 30 dBm.")
            return

        if self.dialog.yesno(
            "Confirm TX Power",
            f"Set TX power to {power} dBm?\n\n"
            f"This is approximately {10 ** (power / 10):.0f} mW.\n\n"
            "Ensure this complies with your region's regulations."
        ):
            self._radio_run(
                [self._get_meshtastic_cli(), '--host', 'localhost', '--set', 'lora.tx_power', str(power)],
                f"Setting TX Power: {power} dBm"
            )

    def _radio_set_name(self):
        """Set node long name via meshtastic CLI."""
        name = self.dialog.inputbox(
            "Node Name",
            "Enter node long name:",
            ""
        )
        if not name:
            return

        short = self.dialog.inputbox(
            "Short Name",
            "Enter short name (max 4 chars):",
            name[:4]
        )

        cmd = [self._get_meshtastic_cli(), '--host', 'localhost', '--set-owner', name]
        if short:
            cmd.extend(['--set-owner-short', short[:4]])
        self._radio_run(cmd, "Setting Node Name")

    def _radio_position_menu(self):
        """Position submenu: view settings or set fixed lat/lon."""
        choices = [
            ("view", "View position settings"),
            ("set", "Set fixed position (lat/lon)"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Position",
            "View or set node position:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self._get_meshtastic_cli()
        conn_args = ['--host', 'localhost']

        if choice == "view":
            self._radio_run([cli] + conn_args + ['--get', 'position'], "Position Settings")
        elif choice == "set":
            lat = self.dialog.inputbox(
                "Latitude",
                "Enter latitude (decimal degrees):\n\n"
                "Example: 19.435175",
                ""
            )
            if not lat:
                return

            lon = self.dialog.inputbox(
                "Longitude",
                "Enter longitude (decimal degrees):\n\n"
                "Example: -155.213842",
                ""
            )
            if not lon:
                return

            # Validate numeric input
            try:
                lat_f = float(lat.strip())
                lon_f = float(lon.strip())
            except ValueError:
                self.dialog.msgbox("Error", "Invalid coordinates. Use decimal degrees.")
                return

            if not (-90 <= lat_f <= 90) or not (-180 <= lon_f <= 180):
                self.dialog.msgbox("Error",
                    "Coordinates out of range.\n\n"
                    "Latitude: -90 to 90\n"
                    "Longitude: -180 to 180")
                return

            confirm = self.dialog.yesno(
                "Confirm Position",
                f"Set fixed position?\n\n"
                f"Latitude:  {lat_f}\n"
                f"Longitude: {lon_f}",
                default_no=True
            )
            if not confirm:
                return

            self._radio_run(
                [cli] + conn_args + ['--setlat', str(lat_f), '--setlon', str(lon_f)],
                "Setting Position"
            )

    def _radio_reboot(self):
        """Reboot the radio via meshtastic CLI."""
        if self.dialog.yesno("Reboot Radio", "Reboot the Meshtastic radio?\n\nThis restarts the firmware.", default_no=True):
            self._radio_run(
                [self._get_meshtastic_cli(), '--host', 'localhost', '--reboot'],
                "Rebooting Radio"
            )
