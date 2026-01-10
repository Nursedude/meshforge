"""
RNS Configuration Section for RNS Panel

Manage RNS configuration files and directories.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ConfigMixin:
    """
    Mixin class providing configuration management for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _get_real_username(): Method to get real username
    - _open_config_folder(path): Method to open folder in file manager
    - _edit_config(path): Method to edit config in GUI editor
    - _edit_config_terminal(path): Method to edit config in terminal
    - _open_rns_config_dialog(): Method to open RNS config dialog
    """

    def _build_config_section(self, parent):
        """Build RNS configuration section"""
        frame = Gtk.Frame()
        frame.set_label("Configuration")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Config file location - use real user's home when running as root
        config_path = self._get_real_user_home() / ".reticulum"

        config_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        config_label = Gtk.Label(label="Config Directory:")
        config_label.set_xalign(0)
        config_row.append(config_label)

        config_path_label = Gtk.Label(label=str(config_path))
        config_path_label.add_css_class("dim-label")
        config_path_label.set_selectable(True)
        config_row.append(config_path_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        config_row.append(spacer)

        if config_path.exists():
            open_btn = Gtk.Button(label="Open Folder")
            open_btn.connect("clicked", lambda b: self._open_config_folder(config_path))
            config_row.append(open_btn)

        box.append(config_row)

        # Main config file
        config_file = config_path / "config"
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        file_label = Gtk.Label(label="Main Config:")
        file_label.set_xalign(0)
        file_row.append(file_label)

        file_path_label = Gtk.Label(label=str(config_file))
        file_path_label.add_css_class("dim-label")
        file_row.append(file_path_label)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        file_row.append(spacer2)

        # Edit with external editor
        ext_edit_btn = Gtk.Button(label="Edit (GUI)")
        ext_edit_btn.set_tooltip_text("Open in GUI text editor")
        ext_edit_btn.connect("clicked", lambda b: self._edit_config(config_file))
        file_row.append(ext_edit_btn)

        # Edit in terminal with nano
        terminal_edit_btn = Gtk.Button(label="Edit (Terminal)")
        terminal_edit_btn.set_tooltip_text("Open in terminal with nano")
        terminal_edit_btn.connect("clicked", lambda b: self._edit_config_terminal(config_file))
        file_row.append(terminal_edit_btn)

        # Edit with built-in config editor
        config_edit_btn = Gtk.Button(label="Config Editor")
        config_edit_btn.add_css_class("suggested-action")
        config_edit_btn.set_tooltip_text("Open in MeshForge config editor with templates")
        config_edit_btn.connect("clicked", lambda b: self._open_rns_config_dialog())
        file_row.append(config_edit_btn)

        box.append(file_row)

        if not config_file.exists():
            # Add Create Default button
            create_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            note_label = Gtk.Label(
                label="No config file exists yet."
            )
            note_label.add_css_class("dim-label")
            note_label.set_xalign(0)
            create_row.append(note_label)

            create_btn = Gtk.Button(label="Create Default Config")
            create_btn.add_css_class("suggested-action")
            create_btn.set_tooltip_text("Create a default RNS config with AutoInterface enabled")
            create_btn.connect("clicked", lambda b: self._create_default_rns_config(config_file))
            create_row.append(create_btn)

            box.append(create_row)

        frame.set_child(box)
        parent.append(frame)

    def _create_default_rns_config(self, config_file):
        """Create a default RNS config file with sensible defaults"""
        default_config = '''# Reticulum Network Stack Configuration
# Reference: https://reticulum.network/manual/interfaces.html

[reticulum]
# Enable this node to act as a transport node
# and route traffic for other peers
enable_transport = False

# Share the Reticulum instance with locally
# running clients via a local socket
share_instance = Yes

# If running multiple instances, give them
# unique names to avoid conflicts
# instance_name = default

# Panic and forcibly close if a hardware
# interface experiences an unrecoverable error
panic_on_interface_error = No


[logging]
# Valid log levels are 0 through 7:
#   0: Log only critical information
#   1: Log errors and lower log levels
#   2: Log warnings and lower log levels
#   3: Log notices and lower log levels
#   4: Log info and lower (default)
#   5: Verbose logging
#   6: Debug logging
#   7: Extreme logging
loglevel = 4


[interfaces]
# Default AutoInterface for local network discovery
# Uses link-local UDP broadcasts for peer discovery
[[Default Interface]]
    type = AutoInterface
    enabled = Yes


# ===== RNS TESTNET CONNECTIONS =====
# Uncomment to connect to the public Reticulum Testnet

# [[RNS Testnet Dublin]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = dublin.connect.reticulum.network
#     target_port = 4965

# [[RNS Testnet BetweenTheBorders]]
#     type = TCPClientInterface
#     enabled = yes
#     target_host = reticulum.betweentheborders.com
#     target_port = 4242


# ===== TCP INTERFACES =====
# For hosting your own connectable node

# [[TCP Server Interface]]
#     type = TCPServerInterface
#     enabled = no
#     listen_ip = 0.0.0.0
#     listen_port = 4242

# [[TCP Client Interface]]
#     type = TCPClientInterface
#     enabled = no
#     target_host = example.com
#     target_port = 4242


# ===== RNODE LORA INTERFACE =====
# For LoRa communication using RNode devices

# [[RNode LoRa Interface]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = /dev/ttyUSB0
#     frequency = 867200000
#     bandwidth = 125000
#     txpower = 7
#     spreadingfactor = 8
#     codingrate = 5

# BLE RNode connection (must be paired first):
# [[RNode BLE]]
#     type = RNodeInterface
#     interface_enabled = False
#     port = ble://RNode 3B87


# ===== MESHTASTIC INTERFACE =====
# RNS over Meshtastic LoRa mesh
# Install: Click "Install Interface" in MeshForge RNS panel
# Source: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway

# [[Meshtastic Interface]]
#     type = Meshtastic_Interface
#     enabled = False
#     mode = gateway
#     # Connection: choose ONE (port, ble_port, or tcp_port)
#     port = /dev/ttyUSB0
#     # ble_port = RNode_1234
#     # tcp_port = 127.0.0.1:4403
#     # Speed: 0=LongFast, 1=LongSlow, 6=ShortFast, 8=Turbo
#     data_speed = 8
#     hop_limit = 3
#     bitrate = 500
'''

        try:
            config_path = Path(config_file)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(default_config)

            # Fix ownership if running as root
            real_user = self._get_real_username()
            is_root = os.geteuid() == 0
            if is_root and real_user != 'root':
                subprocess.run(['chown', '-R', f'{real_user}:{real_user}', str(config_path.parent)],
                               capture_output=True, timeout=10)

            logger.debug(f"[RNS] Created default RNS config: {config_path}")
            self.main_window.set_status_message("Created default RNS config")

            # Refresh the panel to show the config exists now
            GLib.timeout_add(500, self._refresh_panel)

        except Exception as e:
            logger.debug(f"[RNS] Failed to create default config: {e}")
            self.main_window.set_status_message(f"Failed to create config: {e}")

    def _refresh_panel(self):
        """Refresh the panel content"""
        # This is a simple refresh - in a full implementation we'd rebuild the config section
        return False
