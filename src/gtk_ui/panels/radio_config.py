"""
Radio Configuration Panel - Full radio settings for Meshtastic devices
Based on https://meshtastic.org/docs/overview/radio-settings/
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import os


class RadioConfigPanel(Gtk.Box):
    """Radio configuration panel with all Meshtastic radio settings"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._cli_path = None
        self._config_loaded = False
        self._build_ui()

        # Auto-load config when panel is first shown
        GLib.timeout_add(500, self._auto_load_config)

    def _build_ui(self):
        """Build the radio configuration UI"""
        # Title
        title = Gtk.Label(label="Radio Configuration")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Configure Mesh, LoRa, Position, Power, MQTT, and Telemetry settings")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content_box.set_margin_top(15)

        # Create expandable sections
        self._add_radio_info_section(content_box)
        self._add_device_section(content_box)
        self._add_lora_section(content_box)
        self._add_position_section(content_box)
        self._add_power_section(content_box)
        self._add_mqtt_section(content_box)
        self._add_telemetry_section(content_box)
        self._add_actions_section(content_box)

        scrolled.set_child(content_box)
        self.append(scrolled)

        # Status bar at bottom
        self.status_label = Gtk.Label(label="Ready - Click 'Load Current Config' to view settings")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        self.append(self.status_label)

    def _create_expander_row(self, title, icon_name):
        """Create a styled expander row"""
        expander = Gtk.Expander(label=title)
        expander.set_expanded(False)

        # Style the header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        header_box.append(icon)
        header_box.append(Gtk.Label(label=title))

        return expander

    def _add_radio_info_section(self, parent):
        """Add connected radio information section"""
        frame = Gtk.Frame()
        frame.set_label("Connected Radio")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Create a grid for radio info display
        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(5)

        # Labels for radio info (will be populated when config loads)
        labels = [
            ("Node ID:", "radio_node_id"),
            ("Long Name:", "radio_long_name"),
            ("Short Name:", "radio_short_name"),
            ("Hardware:", "radio_hardware"),
            ("Firmware:", "radio_firmware"),
            ("Region:", "radio_region"),
            ("Modem Preset:", "radio_preset"),
            ("Channels:", "radio_channels"),
        ]

        for row, (label_text, attr_name) in enumerate(labels):
            label = Gtk.Label(label=label_text)
            label.set_xalign(1)
            label.add_css_class("dim-label")
            grid.attach(label, 0, row, 1, 1)

            value_label = Gtk.Label(label="--")
            value_label.set_xalign(0)
            value_label.set_hexpand(True)
            value_label.set_selectable(True)
            grid.attach(value_label, 1, row, 1, 1)
            setattr(self, attr_name, value_label)

        box.append(grid)

        # Refresh button
        refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_box.set_margin_top(10)

        refresh_btn = Gtk.Button(label="Refresh Radio Info")
        refresh_btn.connect("clicked", lambda b: self._load_radio_info())
        refresh_box.append(refresh_btn)

        box.append(refresh_box)
        frame.set_child(box)
        parent.append(frame)

    def _load_radio_info(self):
        """Load radio info from device using --info command"""
        self.status_label.set_label("Loading radio info...")

        def on_result(success, stdout, stderr):
            if success:
                self._parse_radio_info(stdout)
                self.status_label.set_label("Radio info loaded")
            else:
                self.status_label.set_label(f"Failed to load radio info: {stderr}")

        self._run_cli(['--info'], on_result)

    def _parse_radio_info(self, output):
        """Parse --info output and populate radio info section"""
        import re
        import json

        # Try to extract node ID from "Owner: Name (!abcd1234)" format
        owner_match = re.search(r'Owner[:\s]+([^(]+)\s*\((!?[0-9a-fA-F]{8})\)', output)
        if owner_match:
            self.radio_long_name.set_label(owner_match.group(1).strip())
            self.radio_node_id.set_label(owner_match.group(2))

        # Try to parse "My info:" JSON block
        my_info_match = re.search(r"My info:\s*(\{[^}]+\})", output, re.DOTALL)
        if my_info_match:
            try:
                # Clean up the pseudo-JSON (Python dict format with single quotes)
                info_str = my_info_match.group(1).replace("'", '"').replace("True", "true").replace("False", "false")
                info = json.loads(info_str)
                if 'numChannels' in info:
                    self.radio_channels.set_label(str(info['numChannels']))
            except (json.JSONDecodeError, KeyError):
                pass

        # Try to parse "Metadata:" block
        metadata_match = re.search(r"Metadata:\s*(\{[^}]+\})", output, re.DOTALL)
        if metadata_match:
            try:
                meta_str = metadata_match.group(1).replace("'", '"').replace("True", "true").replace("False", "false")
                meta = json.loads(meta_str)
                if 'firmwareVersion' in meta:
                    self.radio_firmware.set_label(meta['firmwareVersion'])
                if 'hwModel' in meta:
                    self.radio_hardware.set_label(meta['hwModel'])
            except (json.JSONDecodeError, KeyError):
                pass

        # Parse line by line for remaining fields
        lines = output.strip().split('\n')
        for line in lines:
            line_lower = line.lower()

            # Short name
            if 'short name' in line_lower or 'shortname' in line_lower:
                match = re.search(r'[:\s]+([A-Za-z0-9_-]+)\s*$', line)
                if match:
                    self.radio_short_name.set_label(match.group(1).strip())

            # Region from config
            elif 'region' in line_lower and ':' in line:
                match = re.search(r':\s*([A-Z_0-9]+)', line)
                if match and match.group(1) not in ['True', 'False', 'None']:
                    self.radio_region.set_label(match.group(1).strip())

            # Modem preset
            elif 'modem' in line_lower and 'preset' in line_lower:
                match = re.search(r':\s*([A-Z_]+)', line)
                if match:
                    self.radio_preset.set_label(match.group(1).strip())

            # Hardware model fallback
            elif ('hardware' in line_lower or 'hw_model' in line_lower) and self.radio_hardware.get_label() == "--":
                match = re.search(r':\s*(.+)', line)
                if match:
                    self.radio_hardware.set_label(match.group(1).strip())

            # Firmware version fallback
            elif 'firmware' in line_lower and 'version' in line_lower and self.radio_firmware.get_label() == "--":
                match = re.search(r':\s*(.+)', line)
                if match:
                    self.radio_firmware.set_label(match.group(1).strip())

    def _add_device_section(self, parent):
        """Add device/mesh settings section"""
        frame = Gtk.Frame()
        frame.set_label("Device & Mesh Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Device Role
        role_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        role_box.append(Gtk.Label(label="Device Role:"))
        self.role_dropdown = Gtk.DropDown.new_from_strings([
            "CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT",
            "REPEATER", "TRACKER", "SENSOR", "TAK", "TAK_TRACKER", "CLIENT_HIDDEN", "LOST_AND_FOUND"
        ])
        self.role_dropdown.set_selected(0)
        role_box.append(self.role_dropdown)

        role_apply = Gtk.Button(label="Apply")
        role_apply.connect("clicked", lambda b: self._apply_setting("device.role", self._get_role()))
        role_box.append(role_apply)
        box.append(role_box)

        # Rebroadcast Mode
        rebroadcast_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rebroadcast_box.append(Gtk.Label(label="Rebroadcast Mode:"))
        self.rebroadcast_dropdown = Gtk.DropDown.new_from_strings([
            "ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY", "NONE"
        ])
        self.rebroadcast_dropdown.set_selected(0)
        rebroadcast_box.append(self.rebroadcast_dropdown)

        rebroadcast_apply = Gtk.Button(label="Apply")
        rebroadcast_apply.connect("clicked", lambda b: self._apply_setting("device.rebroadcast_mode", self._get_rebroadcast()))
        rebroadcast_box.append(rebroadcast_apply)
        box.append(rebroadcast_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_lora_section(self, parent):
        """Add LoRa settings section"""
        frame = Gtk.Frame()
        frame.set_label("LoRa Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Region
        region_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        region_box.append(Gtk.Label(label="Region:"))
        self.region_dropdown = Gtk.DropDown.new_from_strings([
            "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
            "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923"
        ])
        self.region_dropdown.set_selected(1)  # Default to US
        region_box.append(self.region_dropdown)

        region_apply = Gtk.Button(label="Apply")
        region_apply.connect("clicked", lambda b: self._apply_setting("lora.region", self._get_region()))
        region_box.append(region_apply)
        box.append(region_box)

        # Modem Preset
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        preset_box.append(Gtk.Label(label="Modem Preset:"))
        self.preset_dropdown = Gtk.DropDown.new_from_strings([
            "LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
            "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"
        ])
        self.preset_dropdown.set_selected(0)
        preset_box.append(self.preset_dropdown)

        preset_apply = Gtk.Button(label="Apply")
        preset_apply.connect("clicked", lambda b: self._apply_setting("lora.modem_preset", self._get_preset()))
        preset_box.append(preset_apply)
        box.append(preset_box)

        # Hop Limit
        hop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hop_box.append(Gtk.Label(label="Hop Limit:"))
        self.hop_spin = Gtk.SpinButton()
        self.hop_spin.set_range(1, 7)
        self.hop_spin.set_value(3)
        self.hop_spin.set_increments(1, 1)
        hop_box.append(self.hop_spin)

        hop_apply = Gtk.Button(label="Apply")
        hop_apply.connect("clicked", lambda b: self._apply_setting("lora.hop_limit", str(int(self.hop_spin.get_value()))))
        hop_box.append(hop_apply)
        box.append(hop_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_position_section(self, parent):
        """Add position settings section"""
        frame = Gtk.Frame()
        frame.set_label("Position Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Current Position Display
        current_pos_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        current_pos_box.append(Gtk.Label(label="Current Position:"))
        self.current_pos_label = Gtk.Label(label="Loading...")
        self.current_pos_label.set_xalign(0)
        self.current_pos_label.add_css_class("dim-label")
        current_pos_box.append(self.current_pos_label)
        box.append(current_pos_box)

        # GPS Enabled
        gps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        gps_box.append(Gtk.Label(label="GPS Mode:"))
        self.gps_dropdown = Gtk.DropDown.new_from_strings([
            "DISABLED", "ENABLED", "NOT_PRESENT"
        ])
        self.gps_dropdown.set_selected(1)
        gps_box.append(self.gps_dropdown)

        gps_apply = Gtk.Button(label="Apply")
        gps_apply.connect("clicked", lambda b: self._apply_setting("position.gps_mode", self._get_gps_mode()))
        gps_box.append(gps_apply)
        box.append(gps_box)

        # Position broadcast interval
        pos_interval_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        pos_interval_box.append(Gtk.Label(label="Broadcast Interval (sec):"))
        self.pos_interval_spin = Gtk.SpinButton()
        self.pos_interval_spin.set_range(0, 86400)
        self.pos_interval_spin.set_value(900)  # 15 min default
        self.pos_interval_spin.set_increments(60, 300)
        pos_interval_box.append(self.pos_interval_spin)

        pos_apply = Gtk.Button(label="Apply")
        pos_apply.connect("clicked", lambda b: self._apply_setting(
            "position.position_broadcast_secs", str(int(self.pos_interval_spin.get_value()))))
        pos_interval_box.append(pos_apply)
        box.append(pos_interval_box)

        # Fixed Position Entry with format hint
        hint_label = Gtk.Label(label="Set Fixed Position (format: --setlat 19.435175 --setlon -155.213842)")
        hint_label.set_xalign(0)
        hint_label.add_css_class("dim-label")
        box.append(hint_label)

        fixed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fixed_box.append(Gtk.Label(label="Lat:"))

        self.lat_entry = Gtk.Entry()
        self.lat_entry.set_placeholder_text("19.435175")
        self.lat_entry.set_width_chars(14)
        fixed_box.append(self.lat_entry)

        fixed_box.append(Gtk.Label(label="Lon:"))
        self.lon_entry = Gtk.Entry()
        self.lon_entry.set_placeholder_text("-155.213842")
        self.lon_entry.set_width_chars(14)
        fixed_box.append(self.lon_entry)

        fixed_box.append(Gtk.Label(label="Alt:"))
        self.alt_entry = Gtk.Entry()
        self.alt_entry.set_placeholder_text("100")
        self.alt_entry.set_width_chars(8)
        fixed_box.append(self.alt_entry)

        fixed_apply = Gtk.Button(label="Set Fixed Position")
        fixed_apply.add_css_class("suggested-action")
        fixed_apply.connect("clicked", self._set_fixed_position)
        fixed_box.append(fixed_apply)
        box.append(fixed_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_power_section(self, parent):
        """Add power settings section"""
        frame = Gtk.Frame()
        frame.set_label("Power Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # TX Power
        tx_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tx_box.append(Gtk.Label(label="TX Power (dBm):"))
        self.tx_power_spin = Gtk.SpinButton()
        self.tx_power_spin.set_range(0, 30)
        self.tx_power_spin.set_value(0)  # 0 = use default
        self.tx_power_spin.set_increments(1, 5)
        tx_box.append(self.tx_power_spin)

        tx_apply = Gtk.Button(label="Apply")
        tx_apply.connect("clicked", lambda b: self._apply_setting(
            "lora.tx_power", str(int(self.tx_power_spin.get_value()))))
        tx_box.append(tx_apply)
        box.append(tx_box)

        # Power Saving
        power_save_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.power_save_check = Gtk.CheckButton(label="Enable Power Saving Mode")
        power_save_box.append(self.power_save_check)

        ps_apply = Gtk.Button(label="Apply")
        ps_apply.connect("clicked", lambda b: self._apply_setting(
            "power.is_power_saving", "true" if self.power_save_check.get_active() else "false"))
        power_save_box.append(ps_apply)
        box.append(power_save_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_mqtt_section(self, parent):
        """Add MQTT settings section"""
        frame = Gtk.Frame()
        frame.set_label("MQTT Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # MQTT Enable
        mqtt_enable_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.mqtt_enabled_check = Gtk.CheckButton(label="Enable MQTT")
        mqtt_enable_box.append(self.mqtt_enabled_check)

        mqtt_enable_apply = Gtk.Button(label="Apply")
        mqtt_enable_apply.connect("clicked", lambda b: self._apply_setting(
            "mqtt.enabled", "true" if self.mqtt_enabled_check.get_active() else "false"))
        mqtt_enable_box.append(mqtt_enable_apply)
        box.append(mqtt_enable_box)

        # MQTT Server
        server_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        server_box.append(Gtk.Label(label="Server:"))
        self.mqtt_server_entry = Gtk.Entry()
        self.mqtt_server_entry.set_placeholder_text("mqtt.meshtastic.org")
        self.mqtt_server_entry.set_hexpand(True)
        server_box.append(self.mqtt_server_entry)

        server_apply = Gtk.Button(label="Apply")
        server_apply.connect("clicked", lambda b: self._apply_setting(
            "mqtt.address", self.mqtt_server_entry.get_text()))
        server_box.append(server_apply)
        box.append(server_box)

        # MQTT Username/Password
        auth_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        auth_box.append(Gtk.Label(label="Username:"))
        self.mqtt_user_entry = Gtk.Entry()
        self.mqtt_user_entry.set_width_chars(15)
        auth_box.append(self.mqtt_user_entry)

        auth_box.append(Gtk.Label(label="Password:"))
        self.mqtt_pass_entry = Gtk.Entry()
        self.mqtt_pass_entry.set_visibility(False)
        self.mqtt_pass_entry.set_width_chars(15)
        auth_box.append(self.mqtt_pass_entry)

        auth_apply = Gtk.Button(label="Apply Auth")
        auth_apply.connect("clicked", self._apply_mqtt_auth)
        auth_box.append(auth_apply)
        box.append(auth_box)

        # MQTT Encryption
        enc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.mqtt_enc_check = Gtk.CheckButton(label="Encryption Enabled")
        enc_box.append(self.mqtt_enc_check)

        self.mqtt_json_check = Gtk.CheckButton(label="JSON Enabled")
        enc_box.append(self.mqtt_json_check)

        self.mqtt_tls_check = Gtk.CheckButton(label="TLS Enabled")
        enc_box.append(self.mqtt_tls_check)
        box.append(enc_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_telemetry_section(self, parent):
        """Add telemetry settings section"""
        frame = Gtk.Frame()
        frame.set_label("Telemetry Settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Device Metrics Interval
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        device_box.append(Gtk.Label(label="Device Metrics Interval (sec):"))
        self.device_metrics_spin = Gtk.SpinButton()
        self.device_metrics_spin.set_range(0, 86400)
        self.device_metrics_spin.set_value(1800)  # 30 min default
        self.device_metrics_spin.set_increments(60, 300)
        device_box.append(self.device_metrics_spin)

        device_apply = Gtk.Button(label="Apply")
        device_apply.connect("clicked", lambda b: self._apply_setting(
            "telemetry.device_update_interval", str(int(self.device_metrics_spin.get_value()))))
        device_box.append(device_apply)
        box.append(device_box)

        # Environment Metrics Interval
        env_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        env_box.append(Gtk.Label(label="Environment Metrics Interval (sec):"))
        self.env_metrics_spin = Gtk.SpinButton()
        self.env_metrics_spin.set_range(0, 86400)
        self.env_metrics_spin.set_value(1800)
        self.env_metrics_spin.set_increments(60, 300)
        env_box.append(self.env_metrics_spin)

        env_apply = Gtk.Button(label="Apply")
        env_apply.connect("clicked", lambda b: self._apply_setting(
            "telemetry.environment_update_interval", str(int(self.env_metrics_spin.get_value()))))
        env_box.append(env_apply)
        box.append(env_box)

        frame.set_child(box)
        parent.append(frame)

    def _add_actions_section(self, parent):
        """Add action buttons section"""
        frame = Gtk.Frame()
        frame.set_label("Actions")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_halign(Gtk.Align.CENTER)

        # Load Current Config
        load_btn = Gtk.Button(label="Load Current Config")
        load_btn.add_css_class("suggested-action")
        load_btn.connect("clicked", lambda b: self._load_current_config())
        box.append(load_btn)

        # View Full Config
        view_btn = Gtk.Button(label="View Full Config")
        view_btn.connect("clicked", lambda b: self._view_full_config())
        box.append(view_btn)

        # Factory Reset
        reset_btn = Gtk.Button(label="Factory Reset")
        reset_btn.add_css_class("destructive-action")
        reset_btn.connect("clicked", lambda b: self._factory_reset())
        box.append(reset_btn)

        # Reboot Node
        reboot_btn = Gtk.Button(label="Reboot Node")
        reboot_btn.connect("clicked", lambda b: self._reboot_node())
        box.append(reboot_btn)

        frame.set_child(box)
        parent.append(frame)

    def _find_cli(self):
        """Find the meshtastic CLI path"""
        if self._cli_path:
            return self._cli_path

        cli_paths = [
            '/root/.local/bin/meshtastic',
            '/home/pi/.local/bin/meshtastic',
            os.path.expanduser('~/.local/bin/meshtastic'),
        ]

        for path in cli_paths:
            if os.path.exists(path):
                self._cli_path = path
                return path

        # Check if in PATH
        result = subprocess.run(['which', 'meshtastic'], capture_output=True, text=True)
        if result.returncode == 0:
            self._cli_path = result.stdout.strip()
            return self._cli_path

        return None

    def _run_cli(self, args, callback=None):
        """Run meshtastic CLI command in background thread"""
        def do_run():
            cli = self._find_cli()
            if not cli:
                if callback:
                    GLib.idle_add(callback, False, "", "Meshtastic CLI not found")
                return

            cmd = [cli, '--host', 'localhost'] + args
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if callback:
                    GLib.idle_add(callback, result.returncode == 0, result.stdout, result.stderr)
            except subprocess.TimeoutExpired:
                if callback:
                    GLib.idle_add(callback, False, "", "Command timed out")
            except Exception as e:
                if callback:
                    GLib.idle_add(callback, False, "", str(e))

        thread = threading.Thread(target=do_run, daemon=True)
        thread.start()

    def _get_role(self):
        """Get selected device role"""
        roles = ["CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT",
                 "REPEATER", "TRACKER", "SENSOR", "TAK", "TAK_TRACKER", "CLIENT_HIDDEN", "LOST_AND_FOUND"]
        return roles[self.role_dropdown.get_selected()]

    def _get_rebroadcast(self):
        """Get selected rebroadcast mode"""
        modes = ["ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY", "NONE"]
        return modes[self.rebroadcast_dropdown.get_selected()]

    def _get_region(self):
        """Get selected region"""
        regions = ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
                   "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923"]
        return regions[self.region_dropdown.get_selected()]

    def _get_preset(self):
        """Get selected modem preset"""
        presets = ["LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
                   "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]
        return presets[self.preset_dropdown.get_selected()]

    def _get_gps_mode(self):
        """Get selected GPS mode"""
        modes = ["DISABLED", "ENABLED", "NOT_PRESENT"]
        return modes[self.gps_dropdown.get_selected()]

    def _apply_setting(self, setting, value):
        """Apply a single setting"""
        self.status_label.set_label(f"Applying {setting}={value}...")

        def on_result(success, stdout, stderr):
            if success:
                self.status_label.set_label(f"Applied {setting}={value}")
                self.main_window.set_status_message(f"Setting applied: {setting}")
            else:
                self.status_label.set_label(f"Failed: {stderr}")
                self.main_window.set_status_message(f"Failed to apply setting: {stderr}")

        self._run_cli(['--set', setting, value], on_result)

    def _set_fixed_position(self, button):
        """Set fixed position from entry fields using --setlat --setlon format"""
        lat_text = self.lat_entry.get_text().strip()
        lon_text = self.lon_entry.get_text().strip()
        alt_text = self.alt_entry.get_text().strip()

        if not lat_text or not lon_text:
            self.status_label.set_label("Error: Latitude and Longitude are required")
            return

        try:
            lat = float(lat_text)
            lon = float(lon_text)
            alt = int(alt_text) if alt_text else 0

            # Validate ranges
            if not (-90 <= lat <= 90):
                self.status_label.set_label("Error: Latitude must be between -90 and 90")
                return
            if not (-180 <= lon <= 180):
                self.status_label.set_label("Error: Longitude must be between -180 and 180")
                return

            self.status_label.set_label(f"Setting: --setlat {lat} --setlon {lon} --setalt {alt}...")

            def on_result(success, stdout, stderr):
                if success:
                    self.status_label.set_label(f"Fixed position set: {lat}, {lon}, {alt}m")
                    self.main_window.set_status_message(f"Position set: {lat}, {lon}")
                else:
                    error_msg = stderr.strip() if stderr else "Unknown error"
                    self.status_label.set_label(f"Failed: {error_msg}")

            # Use exact format: --setlat 19.435175 --setlon -155.213842 --setalt 100
            self._run_cli(['--setlat', str(lat), '--setlon', str(lon), '--setalt', str(alt)], on_result)

        except ValueError as e:
            self.status_label.set_label(f"Error: Invalid coordinates - {e}")

    def _apply_mqtt_auth(self, button):
        """Apply MQTT username and password"""
        user = self.mqtt_user_entry.get_text()
        passwd = self.mqtt_pass_entry.get_text()

        if user:
            self._apply_setting("mqtt.username", user)
        if passwd:
            self._apply_setting("mqtt.password", passwd)

    def _load_current_config(self):
        """Load current configuration from device"""
        self.status_label.set_label("Loading current configuration...")

        def on_result(success, stdout, stderr):
            if success:
                self.status_label.set_label("Configuration loaded")
                self._parse_and_populate_config(stdout)
            else:
                self.status_label.set_label(f"Failed to load config: {stderr}")

        self._run_cli(['--get', 'lora', '--get', 'device', '--get', 'position', '--get', 'mqtt', '--get', 'telemetry'], on_result)

    def _auto_load_config(self):
        """Auto-load configuration when panel is first shown"""
        if not self._config_loaded:
            self._config_loaded = True
            self._load_radio_info()  # Load radio info first
            self._load_current_config()  # Then load config settings
        return False  # Don't repeat

    def _parse_and_populate_config(self, output):
        """Parse CLI output and populate UI fields"""
        import re

        lines = output.strip().split('\n')

        # Role mapping
        roles = ["CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT",
                 "REPEATER", "TRACKER", "SENSOR", "TAK", "TAK_TRACKER", "CLIENT_HIDDEN", "LOST_AND_FOUND"]
        regions = ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
                   "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923"]
        presets = ["LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
                   "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]
        gps_modes = ["DISABLED", "ENABLED", "NOT_PRESENT"]

        for line in lines:
            line_lower = line.lower()

            # Device role
            if 'role:' in line_lower:
                for i, role in enumerate(roles):
                    if role.lower() in line_lower:
                        self.role_dropdown.set_selected(i)
                        break

            # Region
            elif 'region:' in line_lower:
                for i, region in enumerate(regions):
                    if region.lower() in line_lower or region in line:
                        self.region_dropdown.set_selected(i)
                        break

            # Modem preset
            elif 'modem_preset:' in line_lower or 'modempreset:' in line_lower:
                for i, preset in enumerate(presets):
                    if preset.lower() in line_lower or preset in line:
                        self.preset_dropdown.set_selected(i)
                        break

            # Hop limit
            elif 'hop_limit:' in line_lower or 'hoplimit:' in line_lower:
                match = re.search(r'(\d+)', line)
                if match:
                    self.hop_spin.set_value(int(match.group(1)))

            # GPS mode
            elif 'gps_mode:' in line_lower or 'gpsmode:' in line_lower:
                for i, mode in enumerate(gps_modes):
                    if mode.lower() in line_lower:
                        self.gps_dropdown.set_selected(i)
                        break

            # Position broadcast interval
            elif 'position_broadcast_secs:' in line_lower:
                match = re.search(r'(\d+)', line)
                if match:
                    self.pos_interval_spin.set_value(int(match.group(1)))

            # TX Power
            elif 'tx_power:' in line_lower:
                match = re.search(r'(\d+)', line)
                if match:
                    self.tx_power_spin.set_value(int(match.group(1)))

            # MQTT enabled
            elif 'mqtt' in line_lower and 'enabled:' in line_lower:
                if 'true' in line_lower:
                    self.mqtt_enabled_check.set_active(True)
                else:
                    self.mqtt_enabled_check.set_active(False)

            # MQTT server/address
            elif ('mqtt' in line_lower and 'address:' in line_lower) or 'mqtt_server:' in line_lower:
                match = re.search(r':\s*(.+)', line)
                if match:
                    server = match.group(1).strip().strip('"\'')
                    if server and server != 'None':
                        self.mqtt_server_entry.set_text(server)

            # MQTT username
            elif 'mqtt' in line_lower and 'username:' in line_lower:
                match = re.search(r':\s*(.+)', line)
                if match:
                    user = match.group(1).strip().strip('"\'')
                    if user and user != 'None':
                        self.mqtt_user_entry.set_text(user)

            # MQTT encryption enabled
            elif 'mqtt' in line_lower and 'encryption_enabled:' in line_lower:
                if 'true' in line_lower:
                    self.mqtt_enc_check.set_active(True)

            # MQTT JSON enabled
            elif 'mqtt' in line_lower and 'json_enabled:' in line_lower:
                if 'true' in line_lower:
                    self.mqtt_json_check.set_active(True)

            # MQTT TLS enabled
            elif 'mqtt' in line_lower and 'tls_enabled:' in line_lower:
                if 'true' in line_lower:
                    self.mqtt_tls_check.set_active(True)

            # Rebroadcast mode
            elif 'rebroadcast_mode:' in line_lower:
                modes = ["ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY", "NONE"]
                for i, mode in enumerate(modes):
                    if mode.lower() in line_lower:
                        self.rebroadcast_dropdown.set_selected(i)
                        break

            # Latitude
            elif 'latitude:' in line_lower or 'lat:' in line_lower:
                match = re.search(r'[-+]?\d*\.?\d+', line.split(':')[-1])
                if match:
                    lat_val = float(match.group())
                    if lat_val != 0:
                        self.lat_entry.set_text(str(lat_val))
                        # Update current position display
                        if hasattr(self, 'current_pos_label'):
                            current = self.current_pos_label.get_label()
                            if 'Loading' in current or 'Not set' in current:
                                self.current_pos_label.set_label(f"Lat: {lat_val}")
                            elif 'Lat:' in current and 'Lon:' not in current:
                                self.current_pos_label.set_label(f"Lat: {lat_val}")

            # Longitude
            elif 'longitude:' in line_lower or 'lon:' in line_lower:
                match = re.search(r'[-+]?\d*\.?\d+', line.split(':')[-1])
                if match:
                    lon_val = float(match.group())
                    if lon_val != 0:
                        self.lon_entry.set_text(str(lon_val))
                        # Update current position display
                        if hasattr(self, 'current_pos_label'):
                            lat_text = self.lat_entry.get_text()
                            if lat_text:
                                self.current_pos_label.set_label(f"Lat: {lat_text}, Lon: {lon_val}")

            # Altitude
            elif 'altitude:' in line_lower or 'alt:' in line_lower:
                match = re.search(r'[-+]?\d+', line.split(':')[-1])
                if match:
                    alt_val = int(match.group())
                    if alt_val != 0:
                        self.alt_entry.set_text(str(alt_val))

        # Update current position label if we have coordinates
        lat = self.lat_entry.get_text()
        lon = self.lon_entry.get_text()
        alt = self.alt_entry.get_text()
        if lat and lon:
            pos_str = f"Lat: {lat}, Lon: {lon}"
            if alt:
                pos_str += f", Alt: {alt}m"
            self.current_pos_label.set_label(pos_str)
        else:
            self.current_pos_label.set_label("Not set or GPS disabled")

        self.main_window.set_status_message("Configuration loaded from device")

    def _view_full_config(self):
        """Show full device configuration in a dialog"""
        self.status_label.set_label("Fetching full configuration...")

        def on_result(success, stdout, stderr):
            if success:
                self._show_config_dialog("Full Configuration", stdout)
            else:
                self._show_config_dialog("Error", f"Failed to get configuration:\n{stderr}")
            self.status_label.set_label("Ready")

        self._run_cli(['--info'], on_result)

    def _show_config_dialog(self, title, content):
        """Show configuration in a scrollable dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading=title,
            body=""
        )

        # Create scrollable text view for content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(600, 400)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text(content)
        scrolled.set_child(text_view)

        dialog.set_extra_child(scrolled)
        dialog.add_response("close", "Close")
        dialog.present()

    def _factory_reset(self):
        """Perform factory reset with confirmation"""
        self.main_window.show_confirm_dialog(
            "Factory Reset",
            "This will reset all settings to factory defaults.\n\n"
            "Are you sure you want to continue?",
            self._do_factory_reset
        )

    def _do_factory_reset(self, confirmed):
        """Perform the actual factory reset"""
        if not confirmed:
            return

        self.status_label.set_label("Performing factory reset...")

        def on_result(success, stdout, stderr):
            if success:
                self.status_label.set_label("Factory reset complete - node will reboot")
                self.main_window.set_status_message("Factory reset complete")
            else:
                self.status_label.set_label(f"Factory reset failed: {stderr}")

        self._run_cli(['--factory-reset'], on_result)

    def _reboot_node(self):
        """Reboot the Meshtastic node"""
        self.main_window.show_confirm_dialog(
            "Reboot Node",
            "This will reboot the Meshtastic node.\n\n"
            "Are you sure you want to continue?",
            self._do_reboot
        )

    def _do_reboot(self, confirmed):
        """Perform the actual reboot"""
        if not confirmed:
            return

        self.status_label.set_label("Rebooting node...")

        def on_result(success, stdout, stderr):
            if success:
                self.status_label.set_label("Reboot command sent")
                self.main_window.set_status_message("Node reboot initiated")
            else:
                self.status_label.set_label(f"Reboot failed: {stderr}")

        self._run_cli(['--reboot'], on_result)
