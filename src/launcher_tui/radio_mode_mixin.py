"""
Radio Mode Mixin — Primary radio selection in the TUI.

Allows the user to select which LoRa radio firmware is primary:
  - Meshtastic (default, current behavior)
  - MeshCore (companion radio managed directly by MeshForge)
  - Dual (both radios active, gateway bridges all networks)

Uses core.radio_mode for persistence and detection.
"""

import logging
from backend import clear_screen

logger = logging.getLogger(__name__)

# Import radio mode module — first-party, always available
try:
    from core.radio_mode import (
        RadioMode, get_radio_mode, set_radio_mode, get_default_bridge_mode
    )
    _HAS_RADIO_MODE = True
except ImportError:
    _HAS_RADIO_MODE = False


class RadioModeMixin:
    """TUI mixin for primary radio mode selection."""

    def _radio_mode_menu(self):
        """Radio Mode — select primary LoRa radio."""
        if not _HAS_RADIO_MODE:
            self.dialog.msgbox(
                "Radio Mode",
                "Radio mode module not available.\n\n"
                "This feature requires core.radio_mode."
            )
            return

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

            choice = self.dialog.menu(
                "Radio Mode",
                subtitle,
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "info":
                self._safe_call("Radio Mode Info", self._radio_mode_info)
            elif choice in ("meshtastic", "meshcore", "dual"):
                self._safe_call(
                    "Set Radio Mode",
                    self._radio_mode_set,
                    RadioMode(choice)
                )

    def _radio_mode_set(self, mode: 'RadioMode'):
        """Set the primary radio mode."""
        current = get_radio_mode()
        if mode == current:
            self.dialog.msgbox(
                "Radio Mode",
                f"Already set to {mode.value.upper()}.\n\n"
                "No changes needed."
            )
            return

        # Confirm the change
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
        confirm = self.dialog.yesno(
            f"Switch to {mode.value.upper()}?",
            f"{desc}\n\n"
            f"Change from {current.value.upper()} to {mode.value.upper()}?\n\n"
            "Note: The gateway bridge must be restarted for changes to take effect."
        )

        if not confirm:
            return

        # Try admin config first (if running as root), then user config
        import os
        is_root = os.geteuid() == 0
        success = set_radio_mode(mode, admin=is_root)

        if success:
            bridge_mode = get_default_bridge_mode(mode)
            self.dialog.msgbox(
                "Radio Mode Updated",
                f"Primary radio set to: {mode.value.upper()}\n"
                f"Default bridge mode: {bridge_mode}\n\n"
                "Restart the gateway bridge for changes to take effect."
            )
        else:
            self.dialog.msgbox(
                "Error",
                f"Failed to save radio mode.\n\n"
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
        self.dialog.msgbox("Radio Modes", info, height=30, width=65)

    def _get_radio_mode_label(self) -> str:
        """Get short label for current radio mode (for status bar)."""
        if not _HAS_RADIO_MODE:
            return "meshtastic"
        return get_radio_mode().value
