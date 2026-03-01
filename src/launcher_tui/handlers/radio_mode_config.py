"""
Radio Mode Config Handler — Primary radio selection in the TUI.

Converted from radio_mode_mixin.py as part of the mixin-to-registry migration.
Allows the user to select which LoRa radio firmware is primary:
  - Meshtastic (default, current behavior)
  - MeshCore (companion radio managed directly by MeshForge)
  - Dual (both radios active, gateway bridges all networks)
"""

import logging
import os

from handler_protocol import BaseHandler
from core.radio_mode import (
    RadioMode, get_radio_mode, set_radio_mode, get_default_bridge_mode
)

logger = logging.getLogger(__name__)


class RadioModeConfigHandler(BaseHandler):
    """TUI handler for primary radio mode selection."""

    handler_id = "radio_mode_config"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("radio_mode_config", "Radio Mode          Primary radio selection", "meshcore"),
        ]

    def execute(self, action):
        if action == "radio_mode_config":
            self._radio_mode_menu()

    def _radio_mode_menu(self):
        """Radio Mode — select primary LoRa radio."""
        while True:
            current = get_radio_mode()
            bridge_mode = get_default_bridge_mode(current)

            subtitle = (
                f"Current: {current.value.upper()}\n"
                f"Bridge mode: {bridge_mode}"
            )

            choices = [
                ("meshtastic", "Meshtastic       Standard mesh (meshtasticd)"),
                ("meshcore", "MeshCore          Lightweight multi-hop (companion radio)"),
                ("dual", "Dual Radio        Both radios active"),
                ("info", "About Radio Modes"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Radio Mode",
                subtitle,
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "info":
                self.ctx.safe_call("Radio Mode Info", self._radio_mode_info)
            elif choice in ("meshtastic", "meshcore", "dual"):
                self.ctx.safe_call(
                    "Set Radio Mode",
                    self._radio_mode_set,
                    RadioMode(choice)
                )

    def _radio_mode_set(self, mode: RadioMode):
        """Set the primary radio mode."""
        current = get_radio_mode()
        if mode == current:
            self.ctx.dialog.msgbox(
                "Radio Mode",
                f"Already set to {mode.value.upper()}.\n\n"
                "No changes needed."
            )
            return

        mode_descriptions = {
            RadioMode.MESHTASTIC: (
                "Meshtastic will be the primary radio.\n\n"
                "- meshtasticd manages the LoRa radio\n"
                "- MeshCore is optional (bridge target)\n"
                "- Gateway bridge mode: mqtt_bridge"
            ),
            RadioMode.MESHCORE: (
                "MeshCore will be the primary radio.\n\n"
                "- MeshForge manages the companion radio directly\n"
                "- Meshtastic is optional (bridge target if available)\n"
                "- RNS provides long-haul backhaul\n"
                "- Gateway bridge mode: meshcore_primary\n"
                "- Requires: pip install meshcore"
            ),
            RadioMode.DUAL: (
                "Both radios will be active as equal peers.\n\n"
                "- Meshtastic and MeshCore both bridge to RNS\n"
                "- Gateway bridges all three networks\n"
                "- Gateway bridge mode: tri_bridge\n"
                "- Requires both radios connected"
            ),
        }

        desc = mode_descriptions.get(mode, "")
        confirm = self.ctx.dialog.yesno(
            f"Switch to {mode.value.upper()}?",
            f"{desc}\n\n"
            f"Change from {current.value.upper()} to {mode.value.upper()}?\n\n"
            "Note: The gateway bridge must be restarted for changes to take effect."
        )

        if not confirm:
            return

        is_root = os.geteuid() == 0
        success = set_radio_mode(mode, admin=is_root)

        if success:
            bridge_mode = get_default_bridge_mode(mode)
            self.ctx.dialog.msgbox(
                "Radio Mode Updated",
                f"Primary radio set to: {mode.value.upper()}\n"
                f"Default bridge mode: {bridge_mode}\n\n"
                "Restart the gateway bridge for changes to take effect."
            )
        else:
            self.ctx.dialog.msgbox(
                "Error",
                "Failed to save radio mode.\n\n"
                "Check file permissions and try again."
            )

    def _radio_mode_info(self):
        """Show information about radio modes."""
        info = (
            "RADIO MODE SELECTION\n"
            "====================\n\n"
            "MeshForge supports two LoRa mesh firmware ecosystems:\n\n"
            "MESHTASTIC (default)\n"
            "  - Runs meshtasticd as a systemd service\n"
            "  - MeshForge connects via MQTT or TCP\n"
            "  - 237 byte payload, 7 max hops\n"
            "  - Web client on port 9443\n"
            "  - MQTT uplink support\n\n"
            "MESHCORE\n"
            "  - No daemon — MeshForge connects directly\n"
            "  - Companion radio protocol (binary framed)\n"
            "  - 160 byte text, 64 max hops\n"
            "  - Pure radio (no internet uplink)\n"
            "  - Async event-driven API\n\n"
            "DUAL MODE\n"
            "  - Both radios active simultaneously\n"
            "  - Gateway bridges Meshtastic <> MeshCore <> RNS\n"
            "  - Requires two physical radios\n\n"
            "The radio mode determines:\n"
            "  1. Which handler starts first in the gateway\n"
            "  2. Default bridge mode configuration\n"
            "  3. TUI menu layout and status display\n"
            "  4. Which radio is required vs optional"
        )
        self.ctx.dialog.msgbox("Radio Modes", info, height=30, width=65)

    def get_radio_mode_label(self) -> str:
        """Get short label for current radio mode (for status bar)."""
        return get_radio_mode().value
