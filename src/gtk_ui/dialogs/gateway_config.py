"""
Gateway Configuration Dialog
Full GUI editor for RNS-Meshtastic gateway settings
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import json
import os
from pathlib import Path
import sys

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

try:
    from gateway.config import GatewayConfig, MeshtasticConfig, RNSConfig, TelemetryConfig, RoutingRule
except ImportError:
    GatewayConfig = None


class GatewayConfigDialog(Adw.Window):
    """Full configuration editor for the RNS-Meshtastic gateway"""

    def __init__(self, parent_window):
        super().__init__(
            title="Gateway Configuration",
            transient_for=parent_window,
            modal=True,
            default_width=700,
            default_height=600,
        )

        self.parent_window = parent_window
        self._config = None
        self._modified = False

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        """Build the configuration UI"""
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Gateway Configuration"))

        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        # Reset button
        reset_btn = Gtk.Button(label="Reset")
        reset_btn.connect("clicked", self._on_reset)
        header.pack_start(reset_btn)

        main_box.append(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(20)
        content.set_margin_bottom(20)

        # Build sections
        self._build_general_section(content)
        self._build_meshtastic_section(content)
        self._build_rns_section(content)
        self._build_telemetry_section(content)
        self._build_routing_section(content)
        self._build_advanced_section(content)

        scrolled.set_child(content)
        main_box.append(scrolled)

        # Status bar
        self.status_bar = Gtk.Label(label="")
        self.status_bar.set_xalign(0)
        self.status_bar.set_margin_start(20)
        self.status_bar.set_margin_bottom(10)
        self.status_bar.add_css_class("dim-label")
        main_box.append(self.status_bar)

    def _build_general_section(self, parent):
        """Build general settings section"""
        frame = Gtk.Frame()
        frame.set_label("General Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Enable gateway
        row1 = self._create_switch_row("Enable Gateway", "enabled")
        box.append(row1)

        # Auto-start
        row2 = self._create_switch_row("Auto-start on Launch", "auto_start")
        box.append(row2)

        # Log level
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row3.append(Gtk.Label(label="Log Level:"))
        self.log_level_dropdown = Gtk.DropDown.new_from_strings(
            ["DEBUG", "INFO", "WARNING", "ERROR"]
        )
        self.log_level_dropdown.connect("notify::selected", self._on_value_changed)
        row3.append(self.log_level_dropdown)
        box.append(row3)

        # Log messages
        row4 = self._create_switch_row("Log Bridged Messages", "log_messages")
        box.append(row4)

        frame.set_child(box)
        parent.append(frame)

    def _build_meshtastic_section(self, parent):
        """Build Meshtastic settings section"""
        frame = Gtk.Frame()
        frame.set_label("Meshtastic Connection")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Host
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row1.append(Gtk.Label(label="Host:"))
        self.mesh_host_entry = Gtk.Entry()
        self.mesh_host_entry.set_hexpand(True)
        self.mesh_host_entry.set_placeholder_text("localhost")
        self.mesh_host_entry.connect("changed", self._on_value_changed)
        row1.append(self.mesh_host_entry)
        box.append(row1)

        # Port
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row2.append(Gtk.Label(label="Port:"))
        self.mesh_port_spin = Gtk.SpinButton()
        self.mesh_port_spin.set_range(1, 65535)
        self.mesh_port_spin.set_value(4403)
        self.mesh_port_spin.set_increments(1, 100)
        self.mesh_port_spin.connect("value-changed", self._on_value_changed)
        row2.append(self.mesh_port_spin)
        box.append(row2)

        # Channel
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row3.append(Gtk.Label(label="Gateway Channel:"))
        self.mesh_channel_spin = Gtk.SpinButton()
        self.mesh_channel_spin.set_range(0, 7)
        self.mesh_channel_spin.set_value(0)
        self.mesh_channel_spin.set_increments(1, 1)
        self.mesh_channel_spin.connect("value-changed", self._on_value_changed)
        row3.append(self.mesh_channel_spin)

        help_label = Gtk.Label(label="(Channel for gateway messages)")
        help_label.add_css_class("dim-label")
        row3.append(help_label)
        box.append(row3)

        # MQTT option
        row4 = self._create_switch_row("Use MQTT", "mesh_use_mqtt")
        box.append(row4)

        frame.set_child(box)
        parent.append(frame)

    def _build_rns_section(self, parent):
        """Build RNS settings section"""
        frame = Gtk.Frame()
        frame.set_label("Reticulum (RNS) Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Config directory
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row1.append(Gtk.Label(label="Config Directory:"))
        self.rns_config_dir = Gtk.Entry()
        self.rns_config_dir.set_hexpand(True)
        self.rns_config_dir.set_placeholder_text("~/.reticulum (default)")
        self.rns_config_dir.connect("changed", self._on_value_changed)
        row1.append(self.rns_config_dir)

        browse_btn = Gtk.Button(label="Browse")
        browse_btn.connect("clicked", self._on_browse_rns_dir)
        row1.append(browse_btn)
        box.append(row1)

        # Identity name
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row2.append(Gtk.Label(label="Identity Name:"))
        self.rns_identity_entry = Gtk.Entry()
        self.rns_identity_entry.set_hexpand(True)
        self.rns_identity_entry.set_placeholder_text("meshforge_gateway")
        self.rns_identity_entry.connect("changed", self._on_value_changed)
        row2.append(self.rns_identity_entry)
        box.append(row2)

        # Announce interval
        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row3.append(Gtk.Label(label="Announce Interval (sec):"))
        self.rns_announce_spin = Gtk.SpinButton()
        self.rns_announce_spin.set_range(60, 3600)
        self.rns_announce_spin.set_value(300)
        self.rns_announce_spin.set_increments(60, 300)
        self.rns_announce_spin.connect("value-changed", self._on_value_changed)
        row3.append(self.rns_announce_spin)
        box.append(row3)

        # Propagation node
        row4 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row4.append(Gtk.Label(label="Propagation Node:"))
        self.rns_prop_node = Gtk.Entry()
        self.rns_prop_node.set_hexpand(True)
        self.rns_prop_node.set_placeholder_text("(optional) hex hash of propagation node")
        self.rns_prop_node.connect("changed", self._on_value_changed)
        row4.append(self.rns_prop_node)
        box.append(row4)

        frame.set_child(box)
        parent.append(frame)

    def _build_telemetry_section(self, parent):
        """Build telemetry settings section"""
        frame = Gtk.Frame()
        frame.set_label("Telemetry Sharing")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Share options
        self.telem_position_switch = self._create_switch_row("Share Position", "telem_position")
        box.append(self.telem_position_switch)

        self.telem_battery_switch = self._create_switch_row("Share Battery", "telem_battery")
        box.append(self.telem_battery_switch)

        self.telem_env_switch = self._create_switch_row("Share Environment", "telem_environment")
        box.append(self.telem_env_switch)

        # Position precision
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(Gtk.Label(label="Position Precision:"))
        self.telem_precision_spin = Gtk.SpinButton()
        self.telem_precision_spin.set_range(1, 8)
        self.telem_precision_spin.set_value(5)
        self.telem_precision_spin.set_increments(1, 1)
        self.telem_precision_spin.connect("value-changed", self._on_value_changed)
        row.append(self.telem_precision_spin)

        precision_help = Gtk.Label(label="decimal places (5 = ~1m)")
        precision_help.add_css_class("dim-label")
        row.append(precision_help)
        box.append(row)

        # Update interval
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row2.append(Gtk.Label(label="Update Interval (sec):"))
        self.telem_interval_spin = Gtk.SpinButton()
        self.telem_interval_spin.set_range(10, 3600)
        self.telem_interval_spin.set_value(60)
        self.telem_interval_spin.set_increments(10, 60)
        self.telem_interval_spin.connect("value-changed", self._on_value_changed)
        row2.append(self.telem_interval_spin)
        box.append(row2)

        frame.set_child(box)
        parent.append(frame)

    def _build_routing_section(self, parent):
        """Build routing rules section"""
        frame = Gtk.Frame()
        frame.set_label("Message Routing")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Default route
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row1.append(Gtk.Label(label="Default Route:"))
        self.default_route_dropdown = Gtk.DropDown.new_from_strings([
            "Bidirectional",
            "Meshtastic → RNS Only",
            "RNS → Meshtastic Only",
            "Disabled"
        ])
        self.default_route_dropdown.connect("notify::selected", self._on_value_changed)
        row1.append(self.default_route_dropdown)
        box.append(row1)

        # Routing rules list
        rules_label = Gtk.Label(label="Custom Routing Rules:")
        rules_label.set_xalign(0)
        rules_label.set_margin_top(10)
        box.append(rules_label)

        # Rules list container
        self.rules_listbox = Gtk.ListBox()
        self.rules_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.rules_listbox.add_css_class("boxed-list")

        rules_scroll = Gtk.ScrolledWindow()
        rules_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rules_scroll.set_min_content_height(100)
        rules_scroll.set_max_content_height(150)
        rules_scroll.set_child(self.rules_listbox)
        box.append(rules_scroll)

        # Rule buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        btn_box.set_halign(Gtk.Align.END)

        add_rule_btn = Gtk.Button(label="Add Rule")
        add_rule_btn.connect("clicked", self._on_add_rule)
        btn_box.append(add_rule_btn)

        edit_rule_btn = Gtk.Button(label="Edit")
        edit_rule_btn.connect("clicked", self._on_edit_rule)
        btn_box.append(edit_rule_btn)

        remove_rule_btn = Gtk.Button(label="Remove")
        remove_rule_btn.add_css_class("destructive-action")
        remove_rule_btn.connect("clicked", self._on_remove_rule)
        btn_box.append(remove_rule_btn)

        box.append(btn_box)

        frame.set_child(box)
        parent.append(frame)

    def _build_advanced_section(self, parent):
        """Build advanced settings section"""
        expander = Gtk.Expander(label="Advanced / AI Diagnostics")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # AI diagnostics
        self.ai_enabled_switch = self._create_switch_row("Enable AI Diagnostics", "ai_enabled")
        box.append(self.ai_enabled_switch)

        self.snr_analysis_switch = self._create_switch_row("SNR Analysis", "snr_analysis")
        box.append(self.snr_analysis_switch)

        self.anomaly_switch = self._create_switch_row("Anomaly Detection", "anomaly_detection")
        box.append(self.anomaly_switch)

        # Config file location
        config_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_row.set_margin_top(10)
        path_label = Gtk.Label(label="Config File:")
        path_label.set_xalign(0)
        path_row.append(path_label)

        path_value = Gtk.Label(label=str(config_path))
        path_value.add_css_class("dim-label")
        path_value.set_selectable(True)
        path_row.append(path_value)
        box.append(path_row)

        # Export/Import buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_margin_top(10)

        export_btn = Gtk.Button(label="Export Config")
        export_btn.connect("clicked", self._on_export)
        btn_row.append(export_btn)

        import_btn = Gtk.Button(label="Import Config")
        import_btn.connect("clicked", self._on_import)
        btn_row.append(import_btn)

        box.append(btn_row)

        expander.set_child(box)
        parent.append(expander)

    def _create_switch_row(self, label: str, name: str) -> Gtk.Box:
        """Create a row with label and switch"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        lbl = Gtk.Label(label=label)
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        row.append(lbl)

        switch = Gtk.Switch()
        switch.set_name(name)
        switch.connect("state-set", self._on_switch_changed)
        row.append(switch)

        # Store reference
        setattr(self, f"switch_{name}", switch)

        return row

    def _load_config(self):
        """Load configuration from file"""
        if GatewayConfig is None:
            self.status_bar.set_label("Error: Gateway module not available")
            return

        try:
            self._config = GatewayConfig.load()
            self._populate_ui()
            self.status_bar.set_label("Configuration loaded")
        except Exception as e:
            self.status_bar.set_label(f"Error loading config: {e}")

    def _populate_ui(self):
        """Populate UI from config"""
        if not self._config:
            return

        # General
        self.switch_enabled.set_active(self._config.enabled)
        self.switch_auto_start.set_active(self._config.auto_start)
        self.switch_log_messages.set_active(self._config.log_messages)

        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if self._config.log_level in log_levels:
            self.log_level_dropdown.set_selected(log_levels.index(self._config.log_level))

        # Meshtastic
        self.mesh_host_entry.set_text(self._config.meshtastic.host)
        self.mesh_port_spin.set_value(self._config.meshtastic.port)
        self.mesh_channel_spin.set_value(self._config.meshtastic.channel)
        self.switch_mesh_use_mqtt.set_active(self._config.meshtastic.use_mqtt)

        # RNS
        self.rns_config_dir.set_text(self._config.rns.config_dir)
        self.rns_identity_entry.set_text(self._config.rns.identity_name)
        self.rns_announce_spin.set_value(self._config.rns.announce_interval)
        self.rns_prop_node.set_text(self._config.rns.propagation_node)

        # Telemetry
        self.switch_telem_position.set_active(self._config.telemetry.share_position)
        self.switch_telem_battery.set_active(self._config.telemetry.share_battery)
        self.switch_telem_environment.set_active(self._config.telemetry.share_environment)
        self.telem_precision_spin.set_value(self._config.telemetry.position_precision)
        self.telem_interval_spin.set_value(self._config.telemetry.update_interval)

        # Routing
        route_map = {
            "bidirectional": 0,
            "mesh_to_rns": 1,
            "rns_to_mesh": 2,
            "disabled": 3,
        }
        self.default_route_dropdown.set_selected(route_map.get(self._config.default_route, 0))

        # Populate rules list
        self._update_rules_list()

        # Advanced
        self.switch_ai_enabled.set_active(self._config.ai_diagnostics_enabled)
        self.switch_snr_analysis.set_active(self._config.snr_analysis)
        self.switch_anomaly_detection.set_active(self._config.anomaly_detection)

        self._modified = False

    def _update_rules_list(self):
        """Update the routing rules listbox"""
        # Clear existing
        while True:
            row = self.rules_listbox.get_row_at_index(0)
            if row is None:
                break
            self.rules_listbox.remove(row)

        # Add rules
        if self._config and self._config.routing_rules:
            for rule in self._config.routing_rules:
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(5)
                box.set_margin_bottom(5)

                # Enabled indicator
                if rule.enabled:
                    icon = Gtk.Image.new_from_icon_name("emblem-default-symbolic")
                else:
                    icon = Gtk.Image.new_from_icon_name("window-close-symbolic")
                icon.set_pixel_size(16)
                box.append(icon)

                # Name
                name_lbl = Gtk.Label(label=rule.name)
                name_lbl.set_xalign(0)
                name_lbl.set_hexpand(True)
                box.append(name_lbl)

                # Direction
                dir_lbl = Gtk.Label(label=rule.direction)
                dir_lbl.add_css_class("dim-label")
                box.append(dir_lbl)

                row.set_child(box)
                row.rule = rule  # Store reference
                self.rules_listbox.append(row)
        else:
            # No rules placeholder
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label="No custom routing rules defined")
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(10)
            lbl.set_margin_bottom(10)
            row.set_child(lbl)
            self.rules_listbox.append(row)

    def _collect_config(self):
        """Collect configuration from UI"""
        if not self._config:
            return

        # General
        self._config.enabled = self.switch_enabled.get_active()
        self._config.auto_start = self.switch_auto_start.get_active()
        self._config.log_messages = self.switch_log_messages.get_active()

        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        self._config.log_level = log_levels[self.log_level_dropdown.get_selected()]

        # Meshtastic
        self._config.meshtastic.host = self.mesh_host_entry.get_text() or "localhost"
        self._config.meshtastic.port = int(self.mesh_port_spin.get_value())
        self._config.meshtastic.channel = int(self.mesh_channel_spin.get_value())
        self._config.meshtastic.use_mqtt = self.switch_mesh_use_mqtt.get_active()

        # RNS
        self._config.rns.config_dir = self.rns_config_dir.get_text()
        self._config.rns.identity_name = self.rns_identity_entry.get_text() or "meshforge_gateway"
        self._config.rns.announce_interval = int(self.rns_announce_spin.get_value())
        self._config.rns.propagation_node = self.rns_prop_node.get_text()

        # Telemetry
        self._config.telemetry.share_position = self.switch_telem_position.get_active()
        self._config.telemetry.share_battery = self.switch_telem_battery.get_active()
        self._config.telemetry.share_environment = self.switch_telem_environment.get_active()
        self._config.telemetry.position_precision = int(self.telem_precision_spin.get_value())
        self._config.telemetry.update_interval = int(self.telem_interval_spin.get_value())

        # Routing
        route_values = ["bidirectional", "mesh_to_rns", "rns_to_mesh", "disabled"]
        self._config.default_route = route_values[self.default_route_dropdown.get_selected()]

        # Advanced
        self._config.ai_diagnostics_enabled = self.switch_ai_enabled.get_active()
        self._config.snr_analysis = self.switch_snr_analysis.get_active()
        self._config.anomaly_detection = self.switch_anomaly_detection.get_active()

    # Event handlers
    def _on_value_changed(self, *args):
        """Mark config as modified"""
        self._modified = True
        self.status_bar.set_label("* Modified (unsaved)")

    def _on_switch_changed(self, switch, state):
        """Handle switch state change"""
        self._modified = True
        self.status_bar.set_label("* Modified (unsaved)")
        return False

    def _on_save(self, button):
        """Save configuration"""
        self._collect_config()

        if self._config and self._config.save():
            self._modified = False
            self.status_bar.set_label("Configuration saved successfully")
        else:
            self.status_bar.set_label("Error saving configuration")

    def _on_reset(self, button):
        """Reset to saved configuration"""
        self._load_config()
        self._modified = False
        self.status_bar.set_label("Configuration reset to saved values")

    def _on_browse_rns_dir(self, button):
        """Browse for RNS config directory"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select RNS Config Directory")

        def on_response(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
                if folder:
                    self.rns_config_dir.set_text(folder.get_path())
            except Exception:
                pass

        dialog.select_folder(self, None, on_response)

    def _on_add_rule(self, button):
        """Add new routing rule"""
        self._show_rule_dialog(None)

    def _on_edit_rule(self, button):
        """Edit selected routing rule"""
        row = self.rules_listbox.get_selected_row()
        if row and hasattr(row, 'rule'):
            self._show_rule_dialog(row.rule)

    def _on_remove_rule(self, button):
        """Remove selected routing rule"""
        row = self.rules_listbox.get_selected_row()
        if row and hasattr(row, 'rule'):
            self._config.remove_routing_rule(row.rule.name)
            self._update_rules_list()
            self._modified = True
            self.status_bar.set_label("* Modified (unsaved)")

    def _show_rule_dialog(self, rule):
        """Show dialog to add/edit routing rule"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Add Routing Rule" if rule is None else "Edit Routing Rule",
            body="Rule editing dialog coming soon.\n\nManually edit ~/.config/meshforge/gateway.json for now."
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_export(self, button):
        """Export configuration to file"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Export Configuration")
        dialog.set_initial_name("gateway_config.json")

        def on_response(dialog, result):
            try:
                file = dialog.save_finish(result)
                if file:
                    self._collect_config()
                    # Export as JSON
                    from dataclasses import asdict
                    data = {
                        'enabled': self._config.enabled,
                        'auto_start': self._config.auto_start,
                        'meshtastic': asdict(self._config.meshtastic),
                        'rns': asdict(self._config.rns),
                        'telemetry': asdict(self._config.telemetry),
                        'routing_rules': [asdict(r) for r in self._config.routing_rules],
                        'default_route': self._config.default_route,
                        'log_level': self._config.log_level,
                        'log_messages': self._config.log_messages,
                        'ai_diagnostics_enabled': self._config.ai_diagnostics_enabled,
                        'snr_analysis': self._config.snr_analysis,
                        'anomaly_detection': self._config.anomaly_detection,
                    }
                    with open(file.get_path(), 'w') as f:
                        json.dump(data, f, indent=2)
                    self.status_bar.set_label(f"Exported to {file.get_path()}")
            except Exception as e:
                self.status_bar.set_label(f"Export error: {e}")

        dialog.save(self, None, on_response)

    def _on_import(self, button):
        """Import configuration from file"""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import Configuration")

        def on_response(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file:
                    with open(file.get_path(), 'r') as f:
                        data = json.load(f)
                    # Apply to current config
                    # (simplified - would need proper deserialization)
                    self._config.enabled = data.get('enabled', False)
                    self._config.auto_start = data.get('auto_start', False)
                    self._populate_ui()
                    self._modified = True
                    self.status_bar.set_label(f"Imported from {file.get_path()}")
            except Exception as e:
                self.status_bar.set_label(f"Import error: {e}")

        dialog.open(self, None, on_response)
