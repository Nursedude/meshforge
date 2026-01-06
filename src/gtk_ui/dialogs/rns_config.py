"""
RNS Configuration Editor Dialog
Allows editing ~/.reticulum/config with a structured interface
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class RNSConfigDialog(Adw.Window):
    """Dialog for editing RNS configuration file"""

    DEFAULT_CONFIG_PATH = get_real_user_home() / ".reticulum" / "config"

    # Default RNS configuration template
    DEFAULT_CONFIG = """# Reticulum Network Stack Configuration
#
# This file configures the RNS instance. For detailed documentation
# see: https://reticulum.network/manual/using.html

[reticulum]
# Enable or disable transport mode. When enabled, the node will
# route packets for other nodes.
enable_transport = False

# Share instance mode allows multiple programs to use this RNS
# instance by sharing the socket interface.
share_instance = Yes

# Shared instance UDP port
shared_instance_port = 37428

# Instance control port for management
instance_control_port = 37429

# Path to identity file (auto-generated if not exists)
# identity_path = ~/.reticulum/identity

[logging]
# Log level: 0=critical, 1=error, 2=warning, 3=notice, 4=info, 5=verbose, 6=debug, 7=extreme
loglevel = 4


# ============================================================
# INTERFACES
# ============================================================
#
# Uncomment and configure interfaces as needed.
# See https://reticulum.network/manual/interfaces.html


# --- TCP Client Interface ---
# Connect to a remote RNS instance via TCP
#
# [TCP Client]
#   type = TCPClientInterface
#   enabled = True
#   target_host = rns.example.com
#   target_port = 4965


# --- TCP Server Interface ---
# Accept incoming RNS connections via TCP
#
# [TCP Server]
#   type = TCPServerInterface
#   enabled = True
#   listen_ip = 0.0.0.0
#   listen_port = 4965


# --- UDP Interface ---
# Communicate over UDP (local network or internet)
#
# [UDP Interface]
#   type = UDPInterface
#   enabled = True
#   listen_ip = 0.0.0.0
#   listen_port = 4966
#   forward_ip = 255.255.255.255
#   forward_port = 4966


# --- LoRa Interface (RNode) ---
# Use a LoRa radio via RNode firmware
#
# [RNode LoRa]
#   type = RNodeInterface
#   enabled = True
#   port = /dev/ttyUSB0
#   frequency = 915000000
#   bandwidth = 125000
#   txpower = 7
#   spreadingfactor = 8
#   codingrate = 5


# --- Serial Interface ---
# Point-to-point serial connection
#
# [Serial Link]
#   type = SerialInterface
#   enabled = True
#   port = /dev/ttyUSB0
#   speed = 115200
#   databits = 8
#   parity = none
#   stopbits = 1


# --- KISS Interface (for external TNC) ---
# Use external TNC in KISS mode
#
# [KISS TNC]
#   type = KISSInterface
#   enabled = True
#   port = /dev/ttyUSB0
#   speed = 9600


# --- I2P Interface ---
# Tunnel RNS over I2P network
#
# [I2P Interface]
#   type = I2PInterface
#   enabled = True
#   peers = i2p_destination_b32.b32.i2p


# --- AutoInterface ---
# Auto-discovery on local network
# Useful for finding RNS peers automatically
#
# [Auto Discovery]
#   type = AutoInterface
#   enabled = True
#   group_id = reticulum


"""

    def __init__(self, parent=None, config_path=None):
        super().__init__()
        self.parent_window = parent
        self.config_path = Path(config_path) if config_path else self.DEFAULT_CONFIG_PATH
        self.modified = False

        self.set_title("RNS Configuration Editor")
        self.set_default_size(800, 700)
        self.set_modal(True)
        if parent:
            self.set_transient_for(parent)

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        """Build the dialog UI"""
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Title with modified indicator
        self.title_label = Gtk.Label(label="RNS Configuration")
        header.set_title_widget(self.title_label)

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        # Cancel button
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        main_box.append(header)

        # Toolbar with common actions
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.set_margin_start(10)
        toolbar.set_margin_end(10)
        toolbar.set_margin_top(10)
        toolbar.set_margin_bottom(5)

        # Config file path display
        path_label = Gtk.Label(label="File:")
        path_label.add_css_class("dim-label")
        toolbar.append(path_label)

        self.path_entry = Gtk.Entry()
        self.path_entry.set_text(str(self.config_path))
        self.path_entry.set_hexpand(True)
        self.path_entry.set_editable(False)
        toolbar.append(self.path_entry)

        # Browse button
        browse_btn = Gtk.Button()
        browse_btn.set_icon_name("folder-open-symbolic")
        browse_btn.set_tooltip_text("Browse for config file")
        browse_btn.connect("clicked", self._on_browse)
        toolbar.append(browse_btn)

        # Reload button
        reload_btn = Gtk.Button()
        reload_btn.set_icon_name("view-refresh-symbolic")
        reload_btn.set_tooltip_text("Reload from file")
        reload_btn.connect("clicked", self._on_reload)
        toolbar.append(reload_btn)

        main_box.append(toolbar)

        # Quick actions bar
        actions_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        actions_bar.set_margin_start(10)
        actions_bar.set_margin_end(10)
        actions_bar.set_margin_bottom(10)

        # Template insertions
        actions_bar.append(Gtk.Label(label="Insert:"))

        tcp_client_btn = Gtk.Button(label="TCP Client")
        tcp_client_btn.set_tooltip_text("Add TCP Client interface template")
        tcp_client_btn.connect("clicked", lambda b: self._insert_template("tcp_client"))
        actions_bar.append(tcp_client_btn)

        tcp_server_btn = Gtk.Button(label="TCP Server")
        tcp_server_btn.set_tooltip_text("Add TCP Server interface template")
        tcp_server_btn.connect("clicked", lambda b: self._insert_template("tcp_server"))
        actions_bar.append(tcp_server_btn)

        udp_btn = Gtk.Button(label="UDP")
        udp_btn.set_tooltip_text("Add UDP interface template")
        udp_btn.connect("clicked", lambda b: self._insert_template("udp"))
        actions_bar.append(udp_btn)

        rnode_btn = Gtk.Button(label="RNode LoRa")
        rnode_btn.set_tooltip_text("Add RNode LoRa interface template")
        rnode_btn.connect("clicked", lambda b: self._insert_template("rnode"))
        actions_bar.append(rnode_btn)

        auto_btn = Gtk.Button(label="AutoInterface")
        auto_btn.set_tooltip_text("Add Auto-discovery interface template")
        auto_btn.connect("clicked", lambda b: self._insert_template("auto"))
        actions_bar.append(auto_btn)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        actions_bar.append(spacer)

        # Validate button
        validate_btn = Gtk.Button(label="Validate")
        validate_btn.set_tooltip_text("Check configuration for errors")
        validate_btn.connect("clicked", self._on_validate)
        actions_bar.append(validate_btn)

        # Reset to default
        reset_btn = Gtk.Button(label="Reset to Default")
        reset_btn.set_tooltip_text("Reset to default configuration template")
        reset_btn.connect("clicked", self._on_reset_default)
        actions_bar.append(reset_btn)

        main_box.append(actions_bar)

        # Text editor
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.set_left_margin(10)
        self.text_view.set_right_margin(10)
        self.text_view.set_top_margin(10)
        self.text_view.set_bottom_margin(10)

        self.text_buffer = self.text_view.get_buffer()
        self.text_buffer.connect("changed", self._on_text_changed)

        scrolled.set_child(self.text_view)

        # Frame for editor
        frame = Gtk.Frame()
        frame.set_margin_start(10)
        frame.set_margin_end(10)
        frame.set_child(scrolled)

        main_box.append(frame)

        # Status bar
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_margin_start(10)
        status_box.set_margin_end(10)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)

        # Line/column indicator
        self.position_label = Gtk.Label(label="Ln 1, Col 1")
        self.position_label.add_css_class("dim-label")
        status_box.append(self.position_label)

        main_box.append(status_box)

        # Track cursor position
        self.text_buffer.connect("notify::cursor-position", self._on_cursor_moved)

    def _load_config(self):
        """Load configuration from file"""
        try:
            if self.config_path.exists():
                content = self.config_path.read_text()
                self.text_buffer.set_text(content)
                self.status_label.set_label(f"Loaded: {self.config_path}")
            else:
                # Create with default config
                self.text_buffer.set_text(self.DEFAULT_CONFIG)
                self.status_label.set_label("New configuration (file will be created on save)")

            self.modified = False
            self._update_title()

        except Exception as e:
            self.status_label.set_label(f"Error loading: {e}")
            logger.error(f"Failed to load RNS config: {e}")

    def _on_save(self, button):
        """Save configuration to file"""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Get content
            start = self.text_buffer.get_start_iter()
            end = self.text_buffer.get_end_iter()
            content = self.text_buffer.get_text(start, end, True)

            # Write file
            self.config_path.write_text(content)

            self.modified = False
            self._update_title()
            self.status_label.set_label(f"Saved: {self.config_path}")

            logger.info(f"Saved RNS config to {self.config_path}")

        except Exception as e:
            self.status_label.set_label(f"Error saving: {e}")
            logger.error(f"Failed to save RNS config: {e}")

            # Show error dialog
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Save Error",
                body=f"Could not save configuration:\n{e}"
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _on_browse(self, button):
        """Browse for config file"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select RNS Configuration File")

        # Set initial folder
        if self.config_path.parent.exists():
            initial_folder = Gio.File.new_for_path(str(self.config_path.parent))
            dialog.set_initial_folder(initial_folder)

        def on_response(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file:
                    self.config_path = Path(file.get_path())
                    self.path_entry.set_text(str(self.config_path))
                    self._load_config()
            except Exception as e:
                if "Dismissed" not in str(e):
                    logger.error(f"File dialog error: {e}")

        dialog.open(self, None, on_response)

    def _on_reload(self, button):
        """Reload config from file"""
        if self.modified:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Unsaved Changes",
                body="You have unsaved changes. Reload will discard them."
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("reload", "Reload")
            dialog.set_response_appearance("reload", Adw.ResponseAppearance.DESTRUCTIVE)

            def on_response(d, response):
                if response == "reload":
                    self._load_config()
                d.destroy()

            dialog.connect("response", on_response)
            dialog.present()
        else:
            self._load_config()

    def _on_validate(self, button):
        """Validate the configuration"""
        start = self.text_buffer.get_start_iter()
        end = self.text_buffer.get_end_iter()
        content = self.text_buffer.get_text(start, end, True)

        errors = []
        warnings = []

        # Basic validation
        lines = content.split('\n')
        in_section = None
        line_num = 0

        for line in lines:
            line_num += 1
            stripped = line.strip()

            # Skip comments and empty lines
            if not stripped or stripped.startswith('#'):
                continue

            # Check for section header
            if stripped.startswith('[') and stripped.endswith(']'):
                in_section = stripped[1:-1]
                continue

            # Check for key = value format
            if '=' in stripped:
                key, value = stripped.split('=', 1)
                key = key.strip()
                value = value.strip()

                # Check for common issues
                if not key:
                    errors.append(f"Line {line_num}: Empty key")
                if key.startswith('#'):
                    # Comment in wrong place
                    pass

                # Check for required settings
                if in_section and in_section.lower() == 'reticulum':
                    if key == 'enable_transport' and value.lower() not in ['true', 'false', 'yes', 'no']:
                        warnings.append(f"Line {line_num}: enable_transport should be True/False")

            elif not stripped.startswith('['):
                # Line that's not a comment, section, or key=value
                if stripped and not stripped.startswith('#'):
                    errors.append(f"Line {line_num}: Invalid syntax: {stripped[:30]}")

        # Show results
        if errors:
            msg = "Errors found:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... and {len(errors) - 10} more"
            self.status_label.set_label(f"Validation: {len(errors)} errors found")
        elif warnings:
            msg = "Warnings:\n" + "\n".join(warnings[:10])
            self.status_label.set_label(f"Validation: {len(warnings)} warnings")
        else:
            msg = "Configuration appears valid."
            self.status_label.set_label("Validation: OK")

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Configuration Validation",
            body=msg
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_reset_default(self, button):
        """Reset to default configuration"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Reset Configuration",
            body="This will replace the current configuration with the default template. Continue?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "reset":
                self.text_buffer.set_text(self.DEFAULT_CONFIG)
                self.modified = True
                self._update_title()
                self.status_label.set_label("Reset to default template")
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _insert_template(self, template_type):
        """Insert an interface template at cursor position"""
        templates = {
            "tcp_client": """
[[My RNS Network]]
  type = TCPClientInterface
  enabled = yes
  target_host = 192.168.1.100
  target_port = 4242

""",
            "tcp_server": """
[[RNS Server]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242

""",
            "udp": """
[[UDP Interface]]
  type = UDPInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4966
  forward_ip = 255.255.255.255
  forward_port = 4966

""",
            "rnode": """
[[RNode LoRa]]
  type = RNodeInterface
  interface_enabled = True
  port = /dev/ttyACM0
  frequency = 903625000
  bandwidth = 250000
  txpower = 22
  spreadingfactor = 7
  codingrate = 5
  name = rnode

""",
            "auto": """
[[Default Interface]]
  type = AutoInterface
  enabled = Yes

"""
        }

        template = templates.get(template_type, "")
        if template:
            self.text_buffer.insert_at_cursor(template)
            self.status_label.set_label(f"Inserted {template_type} template")

    def _on_text_changed(self, buffer):
        """Handle text changes"""
        self.modified = True
        self._update_title()

    def _on_cursor_moved(self, buffer, param):
        """Update cursor position display"""
        mark = buffer.get_insert()
        iter = buffer.get_iter_at_mark(mark)
        line = iter.get_line() + 1
        col = iter.get_line_offset() + 1
        self.position_label.set_label(f"Ln {line}, Col {col}")

    def _update_title(self):
        """Update title to show modified state"""
        title = "RNS Configuration"
        if self.modified:
            title += " *"
        self.title_label.set_label(title)


# Import Gio for file dialog
from gi.repository import Gio
