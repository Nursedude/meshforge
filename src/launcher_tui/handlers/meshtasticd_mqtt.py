"""
Meshtasticd MQTT Device Handler — Configure radio MQTT uplink/downlink.

Converted from meshtasticd_config_mixin.py (lines 1681-2016) as part of
the mixin-to-registry migration (Batch 9).

Sub-handler registered in section "meshtasticd", dispatched from
MeshtasticdConfigHandler's thin dispatcher menu.
"""

import logging
import subprocess

from handler_protocol import BaseHandler
from backend import clear_screen
logger = logging.getLogger(__name__)

from utils.broker_profiles import get_active_profile as _get_active_profile


class MeshtasticdDeviceMQTTHandler(BaseHandler):
    """TUI handler for Meshtastic radio MQTT uplink/downlink configuration."""

    handler_id = "meshtasticd_device_mqtt"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("mqtt", "MQTT Uplink/Downlink", None),
        ]

    def execute(self, action):
        if action == "mqtt":
            self._mqtt_device_config()

    def _mqtt_device_config(self):
        """Configure MQTT uplink/downlink for the Meshtastic radio."""
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

            choice = self.ctx.dialog.menu(
                "MQTT Device Config",
                "Configure radio MQTT uplink/downlink:\n\n"
                "This sends mesh traffic to an MQTT broker.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "view": ("MQTT View Settings", self._mqtt_view_settings),
                "enable": ("Enable MQTT", lambda: self._mqtt_set_enabled(True)),
                "disable": ("Disable MQTT", lambda: self._mqtt_set_enabled(False)),
                "broker": ("Set MQTT Broker", self._mqtt_set_broker),
                "credentials": ("Set MQTT Credentials", self._mqtt_set_credentials),
                "topic": ("Set MQTT Topic", self._mqtt_set_topic),
                "encryption": ("Set MQTT Encryption", self._mqtt_set_encryption),
                "uplink": ("Configure Uplink", self._mqtt_configure_uplink),
                "downlink": ("Configure Downlink", self._mqtt_configure_downlink),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _mqtt_view_settings(self):
        """View current MQTT settings."""
        clear_screen()
        print("=== MQTT Settings ===\n")
        cli = self.ctx.get_meshtastic_cli()
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
        self.ctx.wait_for_enter()

    def _mqtt_set_enabled(self, enabled: bool):
        """Enable or disable MQTT."""
        if not self.ctx.dialog.yesno(
            f"{'Enable' if enabled else 'Disable'} MQTT",
            f"{'Enable' if enabled else 'Disable'} MQTT uplink/downlink?\n\n"
            f"{'This will start sending mesh traffic to the MQTT broker.' if enabled else 'This will stop MQTT traffic.'}"
        ):
            return

        cli = self.ctx.get_meshtastic_cli()
        clear_screen()
        print(f"=== {'Enabling' if enabled else 'Disabling'} MQTT ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.enabled', str(enabled).lower()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT {'enabled' if enabled else 'disabled'} successfully.")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'enabled', enabled)
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self.ctx.wait_for_enter()

    def _mqtt_set_broker(self):
        """Set MQTT broker address."""
        default_broker = "mqtt.meshtastic.org"
        try:
            active = _get_active_profile()
            if active:
                default_broker = active.host
        except Exception:
            pass

        broker = self.ctx.dialog.inputbox(
            "MQTT Broker",
            "Enter MQTT broker address:\n\n"
            "Examples:\n"
            "  localhost (private broker on this machine)\n"
            "  192.168.1.100 (private broker on LAN)\n"
            "  mqtt.meshtastic.org (public)",
            init=default_broker
        )

        if not broker:
            return

        cli = self.ctx.get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Broker ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.address', broker.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT broker set to: {broker}")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'address', broker.strip())
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self.ctx.wait_for_enter()

    def _mqtt_set_credentials(self):
        """Set MQTT username and password."""
        username = self.ctx.dialog.inputbox(
            "MQTT Username",
            "Enter MQTT username (blank for anonymous):",
            init=""
        )

        if username is None:
            return

        password = self.ctx.dialog.inputbox(
            "MQTT Password",
            "Enter MQTT password (blank for none):",
            init=""
        )

        if password is None:
            return

        cli = self.ctx.get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Credentials ===\n")
        try:
            cmd = [cli, '--host', 'localhost']
            if username:
                cmd.extend(['--set', 'mqtt.username', username])
            if password:
                cmd.extend(['--set', 'mqtt.password', password])

            if len(cmd) > 3:
                result = subprocess.run(cmd, timeout=15)
                if result.returncode == 0:
                    print("\nMQTT credentials updated.")
                    from utils.device_config_store import save_device_settings
                    cred_data = {}
                    if username:
                        cred_data['username'] = username
                    if password:
                        cred_data['password'] = password
                    if cred_data:
                        save_device_settings({'mqtt': cred_data})
                else:
                    print("\nCommand failed.")
            else:
                print("No credentials to set.")
        except Exception as e:
            print(f"\nError: {e}")
        self.ctx.wait_for_enter()

    def _mqtt_set_topic(self):
        """Set MQTT root topic."""
        topic = self.ctx.dialog.inputbox(
            "MQTT Root Topic",
            "Enter MQTT root topic:\n\n"
            "Default: msh\n"
            "Full topic pattern: {root}/{region}/2/e/{channel}/...",
            init="msh"
        )

        if not topic:
            return

        cli = self.ctx.get_meshtastic_cli()
        clear_screen()
        print("=== Setting MQTT Topic ===\n")
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'mqtt.root', topic.strip()],
                timeout=15
            )
            if result.returncode == 0:
                print(f"\nMQTT root topic set to: {topic}")
                from utils.device_config_store import save_device_setting
                save_device_setting('mqtt', 'root_topic', topic.strip())
            else:
                print("\nCommand failed.")
        except Exception as e:
            print(f"\nError: {e}")
        self.ctx.wait_for_enter()

    def _mqtt_set_encryption(self):
        """Configure MQTT encryption key (Public Key Cryptography)."""
        self.ctx.dialog.msgbox(
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

        choice = self.ctx.dialog.menu(
            "MQTT Encryption Mode",
            "Select MQTT message format:",
            choices
        )

        if choice is None or choice == "back":
            return

        cli = self.ctx.get_meshtastic_cli()
        clear_screen()

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

        self.ctx.wait_for_enter()

    def _mqtt_configure_uplink(self):
        """Configure which channels uplink to MQTT."""
        self.ctx.dialog.msgbox(
            "MQTT Uplink",
            "MQTT Uplink sends local mesh messages to the broker.\n\n"
            "Per-channel uplink is configured via:\n"
            "  Channel Config > Edit Channel > Uplink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set uplink_enabled true"
        )

        if self.ctx.dialog.yesno(
            "Enable Primary Uplink",
            "Enable MQTT uplink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self.ctx.get_meshtastic_cli()
            clear_screen()
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
            self.ctx.wait_for_enter()

    def _mqtt_configure_downlink(self):
        """Configure which channels downlink from MQTT."""
        self.ctx.dialog.msgbox(
            "MQTT Downlink",
            "MQTT Downlink receives broker messages to local mesh.\n\n"
            "Per-channel downlink is configured via:\n"
            "  Channel Config > Edit Channel > Downlink Enabled\n\n"
            "Or via CLI:\n"
            "  meshtastic --ch-index 0 --ch-set downlink_enabled true"
        )

        if self.ctx.dialog.yesno(
            "Enable Primary Downlink",
            "Enable MQTT downlink on primary channel (index 0)?",
            default_no=True
        ):
            cli = self.ctx.get_meshtastic_cli()
            clear_screen()
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
            self.ctx.wait_for_enter()
