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
        self._add_frequency_calculator_section(content_box)
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

        # Connection status indicator
        self.connection_status = Gtk.Label(label="Checking connection...")
        self.connection_status.set_xalign(0)
        self.connection_status.add_css_class("dim-label")
        box.append(self.connection_status)

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
        import socket

        # Reset all fields to indicate loading
        self._clear_radio_info()

        # Quick pre-check: is meshtasticd reachable?
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(("localhost", 4403))
            sock.close()
            self.connection_status.set_label("meshtasticd is running on port 4403")
        except (socket.timeout, socket.error, OSError):
            self.connection_status.set_label("Not connected - meshtasticd not running on port 4403")
            self.status_label.set_label("Start meshtasticd service to see radio info")
            self._set_no_radio_message()
            return

        self.status_label.set_label("Loading radio info...")
        self.connection_status.set_label("Connecting to radio...")

        def on_result(success, stdout, stderr):
            if success and stdout.strip():
                self._parse_radio_info(stdout)
                self.connection_status.set_label("Connected to radio")
                self.status_label.set_label("Radio info loaded")
            elif "not found" in stderr.lower() or not self._find_cli():
                self.connection_status.set_label("CLI not installed")
                self.status_label.set_label("Meshtastic CLI not found - install with: pipx install meshtastic")
                self._set_no_radio_message()
            else:
                error_msg = stderr.strip() if stderr else "No response from device"
                if "timed out" in error_msg.lower() or not stdout.strip():
                    self.connection_status.set_label("No radio detected - check hardware connection")
                    self._set_no_radio_message()
                else:
                    self.connection_status.set_label("Connection issue")
                self.status_label.set_label(f"Failed: {error_msg[:50]}")

        self._run_cli(['--info'], on_result)

    def _clear_radio_info(self):
        """Clear all radio info fields"""
        self.radio_node_id.set_label("--")
        self.radio_long_name.set_label("--")
        self.radio_short_name.set_label("--")
        self.radio_hardware.set_label("--")
        self.radio_firmware.set_label("--")
        self.radio_region.set_label("--")
        self.radio_preset.set_label("--")
        self.radio_channels.set_label("--")

    def _set_no_radio_message(self):
        """Set message indicating no radio is connected"""
        self.radio_node_id.set_label("No radio connected")
        self.radio_long_name.set_label("--")
        self.radio_short_name.set_label("--")
        self.radio_hardware.set_label("Connect a radio or start meshtasticd")
        self.radio_firmware.set_label("--")
        self.radio_region.set_label("--")
        self.radio_preset.set_label("--")
        self.radio_channels.set_label("--")

    def _parse_radio_info(self, output):
        """Parse --info output and populate radio info section"""
        import re
        import ast

        # Extract Owner line - format: "Owner: LongName (ShortName/NodeID)"
        # Examples: "Owner: MyNode (MYND) !abcd1234" or "Owner: MyNode (!abcd1234)"
        owner_patterns = [
            # Owner: LongName (ShortName) !nodeId
            r'Owner[:\s]+(.+?)\s+\(([A-Za-z0-9]{1,4})\)\s+(!?[0-9a-fA-F]{8})',
            # Owner: LongName (!nodeId)
            r'Owner[:\s]+(.+?)\s+\((!?[0-9a-fA-F]{8})\)',
            # Owner: LongName !nodeId
            r'Owner[:\s]+(.+?)\s+(!?[0-9a-fA-F]{8})',
        ]

        for pattern in owner_patterns:
            owner_match = re.search(pattern, output)
            if owner_match:
                groups = owner_match.groups()
                if len(groups) == 3:
                    # LongName, ShortName, NodeID
                    self.radio_long_name.set_label(groups[0].strip())
                    self.radio_short_name.set_label(groups[1].strip())
                    self.radio_node_id.set_label(groups[2])
                elif len(groups) == 2:
                    # LongName, NodeID (no short name in parens)
                    self.radio_long_name.set_label(groups[0].strip())
                    self.radio_node_id.set_label(groups[1])
                break

        # Helper to safely parse Python dict strings
        def parse_python_dict(dict_str):
            """Parse a Python dict string (with single quotes, True/False)"""
            try:
                # Use ast.literal_eval for safe Python literal parsing
                return ast.literal_eval(dict_str)
            except (ValueError, SyntaxError):
                try:
                    # Fallback: convert to JSON format
                    import json
                    json_str = dict_str.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
                    return json.loads(json_str)
                except (ValueError, SyntaxError, json.JSONDecodeError):
                    return {}

        # Parse "My info:" block - may contain numChannels, myNodeNum, etc.
        my_info_match = re.search(r"My info:\s*(\{[^}]+\})", output, re.DOTALL)
        if my_info_match:
            info = parse_python_dict(my_info_match.group(1))
            if info.get('numChannels'):
                self.radio_channels.set_label(str(info['numChannels']))
            if info.get('myNodeNum'):
                node_hex = hex(info['myNodeNum'])[2:]
                if self.radio_node_id.get_label() == "--":
                    self.radio_node_id.set_label(f"!{node_hex}")

        # Also try to extract channels from "Channels:" section
        if self.radio_channels.get_label() == "--":
            channels_match = re.search(r"Channels:\s*(\{.+?\})\s*(?=\n[A-Z]|\Z)", output, re.DOTALL)
            if channels_match:
                try:
                    channels_block = channels_match.group(1)
                    channels = parse_python_dict(channels_block)
                    num_channels = len(channels) if isinstance(channels, dict) else 0
                    if num_channels > 0:
                        self.radio_channels.set_label(str(num_channels))
                except (ValueError, SyntaxError):
                    pass

            # Fallback: count channel entries in output
            channel_entries = re.findall(r'(?:PRIMARY|SECONDARY|DISABLED)\s*(?:psk|name)', output, re.IGNORECASE)
            if channel_entries and self.radio_channels.get_label() == "--":
                self.radio_channels.set_label(str(len(channel_entries)))

        # Parse "Metadata:" block - contains firmwareVersion, hwModel, etc.
        metadata_match = re.search(r"Metadata:\s*(\{[^}]+\})", output, re.DOTALL)
        if metadata_match:
            meta = parse_python_dict(metadata_match.group(1))
            if meta.get('firmwareVersion') and self.radio_firmware.get_label() == "--":
                self.radio_firmware.set_label(str(meta['firmwareVersion']))
            if meta.get('hwModel') and self.radio_hardware.get_label() == "--":
                self.radio_hardware.set_label(str(meta['hwModel']))

        # Parse "Nodes in mesh:" block for local node info
        nodes_match = re.search(r"Nodes in mesh:\s*(\{.+?\})\s*(?=\n[A-Z]|\nPreferences|\nChannels|\Z)", output, re.DOTALL)
        if nodes_match:
            try:
                nodes_block = nodes_match.group(1)
                nodes = parse_python_dict(nodes_block)
                # Find local node (usually first or marked with specific user info)
                for node_id, node_data in nodes.items():
                    user = node_data.get('user', {})
                    if user:
                        if self.radio_long_name.get_label() == "--":
                            self.radio_long_name.set_label(user.get('longName', '--'))
                        if self.radio_short_name.get_label() == "--":
                            self.radio_short_name.set_label(user.get('shortName', '--'))
                        if self.radio_hardware.get_label() == "--":
                            self.radio_hardware.set_label(user.get('hwModel', '--'))
                        break  # Only get first node (local node)
            except (ValueError, SyntaxError, KeyError, TypeError):
                pass

        # Extract individual fields using flexible patterns (handles both ' and " quotes)
        field_patterns = {
            'firmwareVersion': (self.radio_firmware, r"['\"]firmwareVersion['\"]\s*:\s*['\"]([^'\"]+)['\"]"),
            'hwModel': (self.radio_hardware, r"['\"]hwModel['\"]\s*:\s*['\"]([^'\"]+)['\"]"),
            'region': (self.radio_region, r"['\"]region['\"]\s*:\s*['\"]?([A-Z_0-9]+)['\"]?"),
            'modemPreset': (self.radio_preset, r"['\"]modem_?[Pp]reset['\"]\s*:\s*['\"]?([A-Z_]+)['\"]?"),
            'shortName': (self.radio_short_name, r"['\"]shortName['\"]\s*:\s*['\"]([^'\"]+)['\"]"),
            'longName': (self.radio_long_name, r"['\"]longName['\"]\s*:\s*['\"]([^'\"]+)['\"]"),
        }

        for field_name, (label, pattern) in field_patterns.items():
            if label.get_label() == "--":
                match = re.search(pattern, output)
                if match:
                    label.set_label(match.group(1).strip())

        # Parse Preferences block for region and modem_preset
        prefs_match = re.search(r"Preferences:\s*(\{.+?\})", output, re.DOTALL)
        if prefs_match:
            prefs = parse_python_dict(prefs_match.group(1))
            if prefs.get('region') and self.radio_region.get_label() == "--":
                self.radio_region.set_label(str(prefs['region']))
            if prefs.get('modem_preset') and self.radio_preset.get_label() == "--":
                self.radio_preset.set_label(str(prefs['modem_preset']))
            if prefs.get('modemPreset') and self.radio_preset.get_label() == "--":
                self.radio_preset.set_label(str(prefs['modemPreset']))

        # Fallback: Parse line by line for any remaining "--" fields
        lines = output.strip().split('\n')
        for line in lines:
            line_lower = line.lower().strip()

            # Skip empty lines and JSON-like content
            if not line_lower or line_lower.startswith('{') or line_lower.startswith('}'):
                continue

            # Short name from "Short name: XXXX" format
            if self.radio_short_name.get_label() == "--":
                if 'short' in line_lower and 'name' in line_lower:
                    match = re.search(r':\s*([A-Za-z0-9]{1,4})\s*$', line)
                    if match:
                        self.radio_short_name.set_label(match.group(1))

            # Region from "region: US" or "Region: US" format
            if self.radio_region.get_label() == "--":
                if 'region' in line_lower and ':' in line:
                    match = re.search(r':\s*([A-Z_0-9]+)', line)
                    if match and match.group(1) not in ['True', 'False', 'None', 'UNSET']:
                        self.radio_region.set_label(match.group(1))

            # Modem preset from various formats
            if self.radio_preset.get_label() == "--":
                if 'modem' in line_lower and ('preset' in line_lower or ':' in line):
                    match = re.search(r':\s*([A-Z_]+(?:FAST|SLOW|TURBO|MODERATE))', line, re.IGNORECASE)
                    if match:
                        self.radio_preset.set_label(match.group(1).upper())

            # Hardware model fallback
            if self.radio_hardware.get_label() == "--":
                if ('hardware' in line_lower or 'hw_model' in line_lower or 'hwmodel' in line_lower):
                    match = re.search(r':\s*([A-Z0-9_]+)', line.upper())
                    if match and len(match.group(1)) > 2:
                        self.radio_hardware.set_label(match.group(1))

            # Firmware version fallback
            if self.radio_firmware.get_label() == "--":
                if 'firmware' in line_lower or 'version' in line_lower:
                    match = re.search(r':\s*(\d+\.\d+\.\d+[a-zA-Z0-9.-]*)', line)
                    if match:
                        self.radio_firmware.set_label(match.group(1))

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

        # Refresh Device Settings button (below device role)
        refresh_device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_device_box.set_margin_top(5)
        refresh_device_btn = Gtk.Button(label="Refresh Device Settings")
        refresh_device_btn.connect("clicked", lambda b: self._load_current_config())
        refresh_device_box.append(refresh_device_btn)
        box.append(refresh_device_box)

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

        # Region - all Meshtastic supported regions
        region_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        region_box.append(Gtk.Label(label="Region:"))
        self.region_dropdown = Gtk.DropDown.new_from_strings([
            "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
            "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
            "SG_923", "PH", "UK_868", "SINGAPORE"
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

    def _add_frequency_calculator_section(self, parent):
        """Add frequency slot calculator section"""
        frame = Gtk.Frame()
        frame.set_label("Frequency Slot Calculator")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Create a grid for cleaner layout
        grid = Gtk.Grid()
        grid.set_column_spacing(15)
        grid.set_row_spacing(10)

        row = 0

        # Modem Preset
        preset_label = Gtk.Label(label="Modem Preset:")
        preset_label.set_xalign(1)
        grid.attach(preset_label, 0, row, 1, 1)
        self.freq_calc_preset = Gtk.DropDown.new_from_strings([
            "LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
            "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"
        ])
        self.freq_calc_preset.set_selected(0)
        self.freq_calc_preset.connect("notify::selected", self._on_freq_calc_params_changed)
        grid.attach(self.freq_calc_preset, 1, row, 1, 1)
        row += 1

        # Region - all Meshtastic supported regions
        region_label = Gtk.Label(label="Region:")
        region_label.set_xalign(1)
        grid.attach(region_label, 0, row, 1, 1)
        self.freq_calc_region = Gtk.DropDown.new_from_strings([
            "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
            "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
            "SG_923", "PH", "UK_868", "SINGAPORE"
        ])
        self.freq_calc_region.set_selected(1)  # Default to US
        self.freq_calc_region.connect("notify::selected", self._on_freq_calc_params_changed)
        grid.attach(self.freq_calc_region, 1, row, 1, 1)
        row += 1

        # Default Frequency Slot (for "LongFast" default channel)
        default_slot_label = Gtk.Label(label="Default Frequency Slot:")
        default_slot_label.set_xalign(1)
        grid.attach(default_slot_label, 0, row, 1, 1)
        self.freq_calc_default_slot = Gtk.Label(label="--")
        self.freq_calc_default_slot.set_xalign(0)
        self.freq_calc_default_slot.add_css_class("monospace")
        grid.attach(self.freq_calc_default_slot, 1, row, 1, 1)
        row += 1

        # Number of slots
        num_slots_label = Gtk.Label(label="Number of slots:")
        num_slots_label.set_xalign(1)
        grid.attach(num_slots_label, 0, row, 1, 1)
        self.freq_calc_num_slots = Gtk.Label(label="--")
        self.freq_calc_num_slots.set_xalign(0)
        grid.attach(self.freq_calc_num_slots, 1, row, 1, 1)
        row += 1

        # Frequency Slot dropdown (manual selection)
        slot_label = Gtk.Label(label="Frequency Slot:")
        slot_label.set_xalign(1)
        grid.attach(slot_label, 0, row, 1, 1)
        # Create dropdown with placeholder - will be populated when params change
        self.freq_calc_slot_dropdown = Gtk.DropDown.new_from_strings(["1"])
        self.freq_calc_slot_dropdown.set_selected(0)
        self.freq_calc_slot_dropdown.connect("notify::selected", self._on_freq_slot_selected)
        grid.attach(self.freq_calc_slot_dropdown, 1, row, 1, 1)
        row += 1

        # Channel Preset dropdown (Meshtastic default channel names)
        preset_channel_label = Gtk.Label(label="Channel Preset:")
        preset_channel_label.set_xalign(1)
        grid.attach(preset_channel_label, 0, row, 1, 1)
        self.freq_calc_channel_preset = Gtk.DropDown.new_from_strings([
            "(Manual Slot)",
            "LongFast",
            "LongSlow",
            "LongModerate",
            "MediumSlow",
            "MediumFast",
            "ShortSlow",
            "ShortFast",
            "ShortTurbo"
        ])
        self.freq_calc_channel_preset.set_selected(1)  # Default to LongFast
        self.freq_calc_channel_preset.connect("notify::selected", self._on_channel_preset_selected)
        grid.attach(self.freq_calc_channel_preset, 1, row, 1, 1)
        row += 1

        # Frequency of slot (calculated)
        freq_label = Gtk.Label(label="Frequency of slot:")
        freq_label.set_xalign(1)
        grid.attach(freq_label, 0, row, 1, 1)
        self.freq_calc_freq = Gtk.Label(label="--")
        self.freq_calc_freq.set_xalign(0)
        self.freq_calc_freq.add_css_class("success")
        grid.attach(self.freq_calc_freq, 1, row, 1, 1)

        box.append(grid)

        # Store current params for frequency calculation
        self._freq_calc_num_slots = 104  # Default for US/LONG_FAST
        self._freq_calc_freq_start = 902.0
        self._freq_calc_bw = 250

        # Calculate on load
        GLib.idle_add(self._update_frequency_calculator)

        frame.set_child(box)
        parent.append(frame)

    def _on_freq_calc_params_changed(self, dropdown, param):
        """Called when region or preset dropdown changes - rebuild slot dropdown"""
        self._update_frequency_calculator()

    def _on_freq_slot_selected(self, dropdown, param):
        """Called when frequency slot dropdown selection changes"""
        # Set channel preset to Manual when slot is manually changed
        self.freq_calc_channel_preset.set_selected(0)  # (Manual Slot)
        self._update_slot_frequency()

    def _on_channel_preset_selected(self, dropdown, param):
        """Called when channel preset dropdown selection changes"""
        presets = [
            "(Manual Slot)",
            "LongFast",
            "LongSlow",
            "LongModerate",
            "MediumSlow",
            "MediumFast",
            "ShortSlow",
            "ShortFast",
            "ShortTurbo"
        ]
        preset_idx = dropdown.get_selected()

        if preset_idx == 0:
            # Manual mode - just update frequency for current slot
            self._update_slot_frequency()
            return

        # Get channel name and calculate its slot
        channel_name = presets[preset_idx]
        h = self._djb2_hash(channel_name)
        slot = h % self._freq_calc_num_slots

        # Update slot dropdown to match (without triggering the slot changed handler)
        self.freq_calc_slot_dropdown.handler_block_by_func(self._on_freq_slot_selected)
        self.freq_calc_slot_dropdown.set_selected(slot)
        self.freq_calc_slot_dropdown.handler_unblock_by_func(self._on_freq_slot_selected)

        # Update frequency display
        self._update_slot_frequency()

    def _update_frequency_calculator(self):
        """Update all frequency calculator fields and rebuild slot dropdown"""
        import math

        # Get selected region - must match dropdown order exactly
        regions = ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
                   "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
                   "SG_923", "PH", "UK_868", "SINGAPORE"]
        region_idx = self.freq_calc_region.get_selected()
        region = regions[region_idx] if region_idx < len(regions) else "US"

        # Get selected preset
        presets = ["LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
                   "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]
        preset_idx = self.freq_calc_preset.get_selected()
        preset = presets[preset_idx] if preset_idx < len(presets) else "LONG_FAST"

        # Get region parameters and bandwidth
        freq_start, freq_end, spacing, _ = self._get_region_params(region)
        bw = self._get_preset_bandwidth(preset)

        # Calculate number of slots
        num_slots = int(math.floor((freq_end - freq_start) / (spacing + (bw / 1000))))

        # Store for frequency calculation
        self._freq_calc_num_slots = num_slots
        self._freq_calc_freq_start = freq_start
        self._freq_calc_bw = bw

        # Update Number of slots label
        self.freq_calc_num_slots.set_label(str(num_slots))

        # Calculate default slot (for "LongFast" channel name)
        default_hash = self._djb2_hash("LongFast")
        default_slot = default_hash % num_slots
        self.freq_calc_default_slot.set_label(str(default_slot + 1))  # 1-indexed display

        # Rebuild slot dropdown with new range
        slot_strings = [str(i) for i in range(1, num_slots + 1)]
        slot_model = Gtk.StringList.new(slot_strings)
        self.freq_calc_slot_dropdown.set_model(slot_model)

        # Set to default slot (LongFast)
        if default_slot < num_slots:
            self.freq_calc_slot_dropdown.set_selected(default_slot)
        else:
            self.freq_calc_slot_dropdown.set_selected(0)

        # Set channel preset to LongFast
        self.freq_calc_channel_preset.set_selected(1)  # LongFast

        # Update frequency display
        self._update_slot_frequency()

    def _update_slot_frequency(self):
        """Calculate and display frequency for selected slot"""
        slot_idx = self.freq_calc_slot_dropdown.get_selected()
        slot = slot_idx  # 0-indexed internally

        # Calculate center frequency: freqStart + (bw / 2000) + (slot * (bw / 1000))
        center_freq = self._freq_calc_freq_start + (self._freq_calc_bw / 2000) + (slot * (self._freq_calc_bw / 1000))
        self.freq_calc_freq.set_label(f"{center_freq:.3f} MHz")

    def _djb2_hash(self, s):
        """Calculate djb2 hash - same algorithm as Meshtastic firmware"""
        h = 5381
        for c in s:
            h = ((h << 5) + h) + ord(c)
        return h & 0xFFFFFFFF  # Keep it 32-bit unsigned

    def _get_region_params(self, region):
        """Get frequency parameters for a region"""
        # Region parameters: (freqStart, freqEnd, spacing, dutyCycle)
        # Spacing is typically 0 for most regions (calculated from bandwidth)
        # Based on https://meshtastic.org/docs/overview/radio-settings/
        regions = {
            "UNSET": (902.0, 928.0, 0, 100),      # Defaults to US
            "US": (902.0, 928.0, 0, 100),
            "EU_433": (433.0, 434.0, 0, 10),
            "EU_868": (869.4, 869.65, 0, 10),
            "CN": (470.0, 510.0, 0, 100),
            "JP": (920.8, 923.8, 0, 100),
            "ANZ": (915.0, 928.0, 0, 100),
            "KR": (920.0, 923.0, 0, 100),
            "TW": (920.0, 925.0, 0, 100),
            "RU": (868.7, 869.2, 0, 100),
            "IN": (865.0, 867.0, 0, 100),
            "NZ_865": (864.0, 868.0, 0, 100),
            "TH": (920.0, 925.0, 0, 100),
            "LORA_24": (2400.0, 2483.5, 0, 100),
            "UA_433": (433.0, 434.79, 0, 10),
            "UA_868": (868.0, 868.6, 0, 1),
            "MY_433": (433.0, 435.0, 0, 100),
            "MY_919": (919.0, 924.0, 0, 100),
            "SG_923": (920.0, 925.0, 0, 100),
            "PH": (920.0, 925.0, 0, 100),        # Philippines 920 MHz band
            "UK_868": (869.4, 869.65, 0, 10),    # UK 868 MHz (same as EU_868)
            "SINGAPORE": (920.0, 925.0, 0, 100), # Same as SG_923
        }
        return regions.get(region, (902.0, 928.0, 0, 100))

    def _get_preset_bandwidth(self, preset):
        """Get bandwidth in kHz for a modem preset"""
        # Bandwidths from Meshtastic modem presets
        presets = {
            "LONG_FAST": 250,
            "LONG_SLOW": 125,
            "LONG_MODERATE": 125,
            "MEDIUM_SLOW": 250,
            "MEDIUM_FAST": 250,
            "SHORT_SLOW": 250,
            "SHORT_FAST": 250,
            "SHORT_TURBO": 500,
        }
        return presets.get(preset, 250)

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

        # Also check for the original user's home if running with sudo
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            cli_paths.insert(0, f'/home/{sudo_user}/.local/bin/meshtastic')

        for path in cli_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
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
                   "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
                   "SG_923", "PH", "UK_868", "SINGAPORE"]
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
        import socket

        # Quick pre-check: is meshtasticd reachable?
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(("localhost", 4403))
            sock.close()
        except (socket.timeout, socket.error, OSError):
            self.status_label.set_label("Cannot connect to meshtasticd (port 4403)")
            return

        self.status_label.set_label("Loading current configuration...")

        def on_result(success, stdout, stderr):
            if success and stdout.strip():
                self.status_label.set_label("Configuration loaded")
                self._parse_and_populate_config(stdout)
            elif "not found" in stderr.lower() or not self._find_cli():
                self.status_label.set_label("Meshtastic CLI not found")
            else:
                error_msg = stderr.strip() if stderr else "No response"
                self.status_label.set_label(f"Failed: {error_msg[:50]}")

        self._run_cli(['--get', 'lora', '--get', 'device', '--get', 'position', '--get', 'mqtt', '--get', 'telemetry'], on_result)

    def _auto_load_config(self):
        """Auto-load configuration when panel is first shown"""
        if not self._config_loaded:
            self._config_loaded = True
            # Use GLib.idle_add to avoid race conditions
            GLib.idle_add(self._load_radio_info)  # Load radio info first
            GLib.timeout_add(1000, self._delayed_load_config)  # Then load config after 1s
        return False  # Don't repeat

    def _delayed_load_config(self):
        """Load config after radio info has had time to load"""
        self._load_current_config()
        return False  # Don't repeat

    def _parse_and_populate_config(self, output):
        """Parse CLI output and populate UI fields"""
        import re
        import ast

        # Option lists for dropdowns (must match dropdown order)
        roles = ["CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT",
                 "REPEATER", "TRACKER", "SENSOR", "TAK", "TAK_TRACKER", "CLIENT_HIDDEN", "LOST_AND_FOUND"]
        regions = ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
                   "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
                   "SG_923", "PH", "UK_868", "SINGAPORE"]
        presets = ["LONG_FAST", "LONG_SLOW", "LONG_MODERATE", "MEDIUM_SLOW", "MEDIUM_FAST",
                   "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]
        gps_modes = ["DISABLED", "ENABLED", "NOT_PRESENT"]
        rebroadcast_modes = ["ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY", "NONE"]

        # Track which fields we've set
        fields_set = {
            'role': False, 'region': False, 'preset': False, 'hop_limit': False,
            'gps_mode': False, 'rebroadcast': False, 'tx_power': False,
            'pos_interval': False, 'mqtt_enabled': False, 'lat': False, 'lon': False, 'alt': False
        }

        # Helper to safely set dropdown by matching value
        def set_dropdown_by_value(dropdown, options, value):
            """Set dropdown selection by matching value in options list"""
            value_upper = value.upper().strip()
            # First try exact match
            for i, option in enumerate(options):
                if option == value_upper:
                    dropdown.set_selected(i)
                    return True
            # Try matching with underscores replaced (CLIENT_MUTE vs CLIENTMUTE)
            value_no_underscore = value_upper.replace('_', '')
            for i, option in enumerate(options):
                if option.replace('_', '') == value_no_underscore:
                    dropdown.set_selected(i)
                    return True
            # DO NOT use partial substring matching - it causes CLIENT to match before CLIENT_MUTE
            return False

        # Helper to extract value after colon
        def extract_value(line):
            """Extract value after colon, handling quotes"""
            if ':' in line:
                val = line.split(':', 1)[1].strip()
                return val.strip('"\'')
            return None

        # First pass: Try to parse structured output sections
        # meshtastic --get outputs sections like "lora:", "device:", etc.
        sections = {}
        current_section = None

        lines = output.strip().split('\n')
        for line in lines:
            stripped = line.strip()

            # Detect section headers (like "lora:" or "Lora settings:")
            if stripped.endswith(':') and not ' ' in stripped.rstrip(':'):
                current_section = stripped.rstrip(':').lower()
                sections[current_section] = []
            elif current_section and stripped:
                sections[current_section].append(stripped)

        # Process each line for config values
        for line in lines:
            line_stripped = line.strip()
            line_lower = line_stripped.lower()

            # Skip empty lines and section headers
            if not line_stripped or line_stripped.endswith(':'):
                continue

            # --- Device Role ---
            # Skip if this line is about rebroadcast_mode or other role-containing words
            if not fields_set['role'] and 'role' in line_lower and 'rebroadcast' not in line_lower:
                # Match specific formats for device role only:
                # "  role: CLIENT_MUTE" (indented in config output)
                # "device.role: CLIENT_MUTE"
                # "deviceRole: clientMute"
                # But NOT lines like "Owner: Name (CLIENT)" or other contexts

                # Pattern: look for role at word boundary, followed by colon and value
                match = re.search(r'^\s*(?:device[._]?)?role\s*:\s*["\']?([A-Za-z_]+)["\']?\s*$', line, re.IGNORECASE)
                if not match:
                    # Try Python dict format: 'role': 'CLIENT_MUTE' or "role": "CLIENT_MUTE"
                    match = re.search(r"['\"]role['\"]\s*:\s*['\"]([A-Za-z_]+)['\"]", line, re.IGNORECASE)
                if not match:
                    # Also try format like "role: VALUE" anywhere in line but not after Owner/other fields
                    if 'owner' not in line_lower and ':' in line:
                        match = re.search(r'\brole\s*:\s*["\']?([A-Za-z_]+)["\']?', line, re.IGNORECASE)

                if match:
                    role_raw = match.group(1)
                    role_value = role_raw.upper()
                    # Convert camelCase to UPPER_SNAKE_CASE if needed (e.g., clientMute -> CLIENT_MUTE)
                    if '_' not in role_value and any(c.islower() for c in role_raw) and any(c.isupper() for c in role_raw):
                        # Has mixed case - convert camelCase to SNAKE_CASE
                        role_value = re.sub(r'([a-z])([A-Z])', r'\1_\2', role_raw).upper()

                    # Debug: print what we found
                    print(f"[RadioConfig] Found role: '{role_raw}' -> '{role_value}'")

                    if set_dropdown_by_value(self.role_dropdown, roles, role_value):
                        fields_set['role'] = True
                        print(f"[RadioConfig] Set role dropdown to: {role_value}")

            # --- Region ---
            if not fields_set['region'] and 'region' in line_lower:
                match = re.search(r'region[:\s]+([A-Z_0-9]+)', line, re.IGNORECASE)
                if match and match.group(1).upper() not in ['TRUE', 'FALSE', 'NONE']:
                    if set_dropdown_by_value(self.region_dropdown, regions, match.group(1)):
                        fields_set['region'] = True

            # --- Modem Preset ---
            if not fields_set['preset'] and ('modem' in line_lower and 'preset' in line_lower):
                match = re.search(r'(?:modem_?preset|preset)[:\s]+([A-Z_]+)', line, re.IGNORECASE)
                if match:
                    if set_dropdown_by_value(self.preset_dropdown, presets, match.group(1)):
                        fields_set['preset'] = True

            # --- Hop Limit ---
            if not fields_set['hop_limit'] and 'hop' in line_lower and 'limit' in line_lower:
                match = re.search(r'hop_?limit[:\s]+(\d+)', line, re.IGNORECASE)
                if match:
                    self.hop_spin.set_value(int(match.group(1)))
                    fields_set['hop_limit'] = True

            # --- TX Power ---
            if not fields_set['tx_power'] and 'tx' in line_lower and 'power' in line_lower:
                match = re.search(r'tx_?power[:\s]+(\d+)', line, re.IGNORECASE)
                if match:
                    self.tx_power_spin.set_value(int(match.group(1)))
                    fields_set['tx_power'] = True

            # --- GPS Mode ---
            if not fields_set['gps_mode'] and 'gps' in line_lower and 'mode' in line_lower:
                match = re.search(r'gps_?mode[:\s]+([A-Z_]+)', line, re.IGNORECASE)
                if match:
                    if set_dropdown_by_value(self.gps_dropdown, gps_modes, match.group(1)):
                        fields_set['gps_mode'] = True

            # --- Rebroadcast Mode ---
            if not fields_set['rebroadcast'] and 'rebroadcast' in line_lower and 'mode' in line_lower:
                match = re.search(r'rebroadcast_?mode[:\s]+([A-Z_]+)', line, re.IGNORECASE)
                if match:
                    if set_dropdown_by_value(self.rebroadcast_dropdown, rebroadcast_modes, match.group(1)):
                        fields_set['rebroadcast'] = True

            # --- Position Broadcast Interval ---
            if not fields_set['pos_interval'] and 'position' in line_lower and 'broadcast' in line_lower:
                match = re.search(r'position_?broadcast_?secs?[:\s]+(\d+)', line, re.IGNORECASE)
                if match:
                    self.pos_interval_spin.set_value(int(match.group(1)))
                    fields_set['pos_interval'] = True

            # --- MQTT Settings ---
            if 'mqtt' in line_lower:
                # MQTT enabled
                if 'enabled' in line_lower and 'encryption' not in line_lower:
                    if 'true' in line_lower:
                        self.mqtt_enabled_check.set_active(True)
                        fields_set['mqtt_enabled'] = True
                    elif 'false' in line_lower:
                        self.mqtt_enabled_check.set_active(False)
                        fields_set['mqtt_enabled'] = True

                # MQTT server/address
                elif 'address' in line_lower or 'server' in line_lower:
                    val = extract_value(line_stripped)
                    if val and val.lower() not in ['none', '']:
                        self.mqtt_server_entry.set_text(val)

                # MQTT username
                elif 'username' in line_lower:
                    val = extract_value(line_stripped)
                    if val and val.lower() not in ['none', '']:
                        self.mqtt_user_entry.set_text(val)

                # MQTT encryption enabled
                elif 'encryption' in line_lower and 'enabled' in line_lower:
                    self.mqtt_enc_check.set_active('true' in line_lower)

                # MQTT JSON enabled
                elif 'json' in line_lower and 'enabled' in line_lower:
                    self.mqtt_json_check.set_active('true' in line_lower)

                # MQTT TLS enabled
                elif 'tls' in line_lower and 'enabled' in line_lower:
                    self.mqtt_tls_check.set_active('true' in line_lower)

            # --- Position: Latitude ---
            if not fields_set['lat'] and ('latitude' in line_lower or line_lower.startswith('lat:')):
                match = re.search(r'(?:latitude|lat)[:\s]+([-+]?\d+\.?\d*)', line, re.IGNORECASE)
                if match:
                    lat_val = float(match.group(1))
                    if lat_val != 0:
                        self.lat_entry.set_text(str(lat_val))
                        fields_set['lat'] = True

            # --- Position: Longitude ---
            if not fields_set['lon'] and ('longitude' in line_lower or line_lower.startswith('lon:')):
                match = re.search(r'(?:longitude|lon)[:\s]+([-+]?\d+\.?\d*)', line, re.IGNORECASE)
                if match:
                    lon_val = float(match.group(1))
                    if lon_val != 0:
                        self.lon_entry.set_text(str(lon_val))
                        fields_set['lon'] = True

            # --- Position: Altitude ---
            if not fields_set['alt'] and ('altitude' in line_lower or line_lower.startswith('alt:')):
                match = re.search(r'(?:altitude|alt)[:\s]+([-+]?\d+)', line, re.IGNORECASE)
                if match:
                    alt_val = int(match.group(1))
                    if alt_val != 0:
                        self.alt_entry.set_text(str(alt_val))
                        fields_set['alt'] = True

        # Update current position label
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
