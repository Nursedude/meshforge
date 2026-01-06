"""
Minimal Radio Configuration Panel

Direct library access - no CLI parsing, no fallbacks, just works.
~200 lines instead of 1700.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import logging

logger = logging.getLogger(__name__)

# Modem presets - enum value to display name
PRESETS = {
    0: "LONG_FAST",
    1: "LONG_SLOW",
    2: "VERY_LONG_SLOW",
    3: "MEDIUM_SLOW",
    4: "MEDIUM_FAST",
    5: "SHORT_SLOW",
    6: "SHORT_FAST",
    7: "LONG_MODERATE",
    8: "SHORT_TURBO",
}
PRESET_NAMES = list(PRESETS.values())

# Device roles
ROLES = {
    0: "CLIENT",
    1: "CLIENT_MUTE",
    2: "ROUTER",
    3: "ROUTER_CLIENT",
    4: "REPEATER",
    5: "TRACKER",
    6: "SENSOR",
    7: "TAK",
    8: "TAK_TRACKER",
    9: "CLIENT_HIDDEN",
    10: "LOST_AND_FOUND",
}
ROLE_NAMES = list(ROLES.values())


class RadioConfigSimple(Gtk.Box):
    """Simple radio configuration panel using direct library access."""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._interface = None
        self._build_ui()
        GLib.timeout_add(500, self._load_config)

    def _build_ui(self):
        """Build the UI."""
        # Title
        title = Gtk.Label(label="Radio Configuration")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # Status
        self.status_label = Gtk.Label(label="Loading...")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        self.append(self.status_label)

        # Main content in a frame
        frame = Gtk.Frame()
        frame.set_label("Device Settings")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_start(15)
        content.set_margin_end(15)
        content.set_margin_top(10)
        content.set_margin_bottom(10)

        # Modem Preset
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        preset_row.append(Gtk.Label(label="Modem Preset:"))
        self.preset_dropdown = Gtk.DropDown.new_from_strings(PRESET_NAMES)
        self.preset_dropdown.set_hexpand(True)
        preset_row.append(self.preset_dropdown)
        preset_apply = Gtk.Button(label="Apply")
        preset_apply.connect("clicked", self._apply_preset)
        preset_row.append(preset_apply)
        content.append(preset_row)

        # Device Role
        role_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        role_row.append(Gtk.Label(label="Device Role:"))
        self.role_dropdown = Gtk.DropDown.new_from_strings(ROLE_NAMES)
        self.role_dropdown.set_hexpand(True)
        role_row.append(self.role_dropdown)
        role_apply = Gtk.Button(label="Apply")
        role_apply.connect("clicked", self._apply_role)
        role_row.append(role_apply)
        content.append(role_row)

        # Hop Limit
        hop_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hop_row.append(Gtk.Label(label="Hop Limit:"))
        self.hop_spin = Gtk.SpinButton.new_with_range(1, 7, 1)
        self.hop_spin.set_value(3)
        hop_row.append(self.hop_spin)
        hop_apply = Gtk.Button(label="Apply")
        hop_apply.connect("clicked", self._apply_hop_limit)
        hop_row.append(hop_apply)
        content.append(hop_row)

        # TX Power
        tx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tx_row.append(Gtk.Label(label="TX Power (dBm):"))
        self.tx_spin = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.tx_spin.set_value(20)
        tx_row.append(self.tx_spin)
        tx_apply = Gtk.Button(label="Apply")
        tx_apply.connect("clicked", self._apply_tx_power)
        tx_row.append(tx_apply)
        content.append(tx_row)

        frame.set_child(content)
        self.append(frame)

        # Refresh button
        refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_box.set_halign(Gtk.Align.CENTER)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._load_config())
        refresh_box.append(refresh_btn)
        self.append(refresh_box)

        # Info display
        info_frame = Gtk.Frame()
        info_frame.set_label("Current Config (Read-Only)")
        self.info_label = Gtk.Label(label="Loading...")
        self.info_label.set_xalign(0)
        self.info_label.set_wrap(True)
        self.info_label.set_selectable(True)
        self.info_label.set_margin_start(10)
        self.info_label.set_margin_end(10)
        self.info_label.set_margin_top(10)
        self.info_label.set_margin_bottom(10)
        info_frame.set_child(self.info_label)
        self.append(info_frame)

    def _get_interface(self):
        """Get a meshtastic TCP interface."""
        try:
            from meshtastic.tcp_interface import TCPInterface
            return TCPInterface(hostname='localhost', noProto=False)
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return None

    def _load_config(self):
        """Load current config from device."""
        def do_load():
            try:
                iface = self._get_interface()
                if not iface:
                    GLib.idle_add(self._update_status, "Failed to connect to meshtasticd")
                    return

                config = iface.localNode.localConfig
                lora = config.lora
                device = config.device

                # Get values
                preset_val = int(lora.modem_preset) if hasattr(lora, 'modem_preset') else 0
                role_val = int(device.role) if hasattr(device, 'role') else 0
                hop_val = int(lora.hop_limit) if hasattr(lora, 'hop_limit') else 3
                tx_val = int(lora.tx_power) if hasattr(lora, 'tx_power') else 20

                # Build info text
                info = f"Preset: {PRESETS.get(preset_val, preset_val)} ({preset_val})\n"
                info += f"Role: {ROLES.get(role_val, role_val)} ({role_val})\n"
                info += f"Hop Limit: {hop_val}\n"
                info += f"TX Power: {tx_val} dBm\n"
                info += f"Region: {lora.region if hasattr(lora, 'region') else 'Unknown'}"

                iface.close()

                # Update UI
                GLib.idle_add(self._update_ui, preset_val, role_val, hop_val, tx_val, info)
                GLib.idle_add(self._update_status, "Connected")

            except Exception as e:
                logger.error(f"Load config error: {e}")
                GLib.idle_add(self._update_status, f"Error: {e}")

        threading.Thread(target=do_load, daemon=True).start()
        return False  # Don't repeat

    def _update_ui(self, preset, role, hop, tx, info):
        """Update UI with loaded values."""
        self.preset_dropdown.set_selected(preset)
        self.role_dropdown.set_selected(role)
        self.hop_spin.set_value(hop)
        self.tx_spin.set_value(tx)
        self.info_label.set_label(info)

    def _update_status(self, msg):
        """Update status label."""
        self.status_label.set_label(msg)

    def _apply_preset(self, button):
        """Apply modem preset."""
        preset_idx = self.preset_dropdown.get_selected()
        preset_name = PRESET_NAMES[preset_idx]
        self._apply_setting("lora.modem_preset", preset_name, f"Preset: {preset_name}")

    def _apply_role(self, button):
        """Apply device role."""
        role_idx = self.role_dropdown.get_selected()
        role_name = ROLE_NAMES[role_idx]
        self._apply_setting("device.role", role_name, f"Role: {role_name}")

    def _apply_hop_limit(self, button):
        """Apply hop limit."""
        hop = int(self.hop_spin.get_value())
        self._apply_setting("lora.hop_limit", str(hop), f"Hop Limit: {hop}")

    def _apply_tx_power(self, button):
        """Apply TX power."""
        tx = int(self.tx_spin.get_value())
        self._apply_setting("lora.tx_power", str(tx), f"TX Power: {tx}")

    def _apply_setting(self, setting, value, desc):
        """Apply a setting using meshtastic CLI."""
        def do_apply():
            try:
                import subprocess
                cmd = ['/usr/local/bin/meshtastic', '--host', 'localhost', '--set', setting, value]
                logger.info(f"Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    GLib.idle_add(self._update_status, f"Applied: {desc}")
                    GLib.timeout_add(2000, self._load_config)  # Refresh after 2s
                else:
                    error = result.stderr or result.stdout or "Unknown error"
                    GLib.idle_add(self._update_status, f"Failed: {error[:50]}")
                    logger.error(f"Apply failed: {error}")
            except Exception as e:
                logger.error(f"Apply error: {e}")
                GLib.idle_add(self._update_status, f"Error: {e}")

        self._update_status(f"Applying {desc}...")
        threading.Thread(target=do_apply, daemon=True).start()
