"""
Radio Configuration Panel

Comprehensive radio configuration with direct library access.
Organized into sections: LoRa, Device, Position, Power.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import logging

logger = logging.getLogger(__name__)

# Modem presets - enum value to display name (from meshtastic/config.proto)
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
    9: "LONG_TURBO",  # Added from protobuf
}
PRESET_NAMES = list(PRESETS.values())

# LoRa regions
REGIONS = [
    "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
    "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923"
]

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

# Rebroadcast modes
REBROADCAST_MODES = ["ALL", "ALL_SKIP_DECODING", "LOCAL_ONLY", "KNOWN_ONLY"]

# GPS modes
GPS_MODES = ["DISABLED", "ENABLED", "NOT_PRESENT"]

# Display units
DISPLAY_UNITS = ["METRIC", "IMPERIAL"]

# Bluetooth modes
BT_MODES = ["RANDOM_PIN", "FIXED_PIN", "NO_PIN"]

# Buzzer modes
BUZZER_MODES = ["SYSTEM_DEFAULT", "OFF", "ON"]

# OLED types
OLED_TYPES = ["AUTO", "SSD1306", "SH1106", "SH1107"]


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
        """Build the UI with organized sections."""
        # Scrollable container
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_start(10)
        main_box.set_margin_end(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)

        # Title and status row
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label="Radio Configuration")
        title.add_css_class("title-1")
        title.set_xalign(0)
        title.set_hexpand(True)
        header.append(title)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda b: self._load_config())
        header.append(refresh_btn)
        main_box.append(header)

        # Status
        self.status_label = Gtk.Label(label="Loading...")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        main_box.append(self.status_label)

        # === LORA SETTINGS ===
        lora_frame = Gtk.Frame()
        lora_frame.set_label("LoRa Settings")
        lora_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lora_box.set_margin_start(12)
        lora_box.set_margin_end(12)
        lora_box.set_margin_top(8)
        lora_box.set_margin_bottom(8)

        # Region (display only - dangerous to change)
        region_row = self._make_row("Region:", None)
        self.region_label = Gtk.Label(label="...")
        self.region_label.set_hexpand(True)
        self.region_label.set_xalign(0)
        region_row.append(self.region_label)
        lora_box.append(region_row)

        # Modem Preset
        preset_row = self._make_row("Modem Preset:", None)
        self.preset_dropdown = Gtk.DropDown.new_from_strings(PRESET_NAMES)
        self.preset_dropdown.set_hexpand(True)
        preset_row.append(self.preset_dropdown)
        preset_row.append(self._make_apply_btn(self._apply_preset))
        lora_box.append(preset_row)

        # Hop Limit
        hop_row = self._make_row("Hop Limit:", None)
        self.hop_spin = Gtk.SpinButton.new_with_range(1, 7, 1)
        self.hop_spin.set_value(3)
        hop_row.append(self.hop_spin)
        hop_row.append(self._make_apply_btn(self._apply_hop_limit))
        lora_box.append(hop_row)

        # TX Power
        tx_row = self._make_row("TX Power (dBm):", None)
        self.tx_spin = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.tx_spin.set_value(20)
        tx_row.append(self.tx_spin)
        tx_row.append(self._make_apply_btn(self._apply_tx_power))
        lora_box.append(tx_row)

        # TX Enabled
        txen_row = self._make_row("TX Enabled:", None)
        self.txen_switch = Gtk.Switch()
        self.txen_switch.set_halign(Gtk.Align.START)
        self.txen_switch.set_active(True)
        txen_row.append(self.txen_switch)
        txen_row.append(self._make_apply_btn(self._apply_tx_enabled))
        lora_box.append(txen_row)

        # Channel Number (advanced)
        chan_row = self._make_row("Channel Num:", None)
        self.chan_spin = Gtk.SpinButton.new_with_range(0, 255, 1)
        self.chan_spin.set_value(0)
        self.chan_spin.set_tooltip_text("Override channel number (0=auto)")
        chan_row.append(self.chan_spin)
        chan_row.append(self._make_apply_btn(self._apply_channel_num))
        lora_box.append(chan_row)

        lora_frame.set_child(lora_box)
        main_box.append(lora_frame)

        # === DEVICE SETTINGS ===
        device_frame = Gtk.Frame()
        device_frame.set_label("Device Settings")
        device_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        device_box.set_margin_start(12)
        device_box.set_margin_end(12)
        device_box.set_margin_top(8)
        device_box.set_margin_bottom(8)

        # Device Role
        role_row = self._make_row("Device Role:", None)
        self.role_dropdown = Gtk.DropDown.new_from_strings(ROLE_NAMES)
        self.role_dropdown.set_hexpand(True)
        role_row.append(self.role_dropdown)
        role_row.append(self._make_apply_btn(self._apply_role))
        device_box.append(role_row)

        # Rebroadcast Mode
        rebroadcast_row = self._make_row("Rebroadcast:", None)
        self.rebroadcast_dropdown = Gtk.DropDown.new_from_strings(REBROADCAST_MODES)
        self.rebroadcast_dropdown.set_hexpand(True)
        rebroadcast_row.append(self.rebroadcast_dropdown)
        rebroadcast_row.append(self._make_apply_btn(self._apply_rebroadcast))
        device_box.append(rebroadcast_row)

        # Node Info Broadcast Interval
        nodeinfo_row = self._make_row("Node Info (sec):", None)
        self.nodeinfo_spin = Gtk.SpinButton.new_with_range(0, 86400, 60)
        self.nodeinfo_spin.set_value(900)
        nodeinfo_row.append(self.nodeinfo_spin)
        nodeinfo_row.append(self._make_apply_btn(self._apply_nodeinfo))
        device_box.append(nodeinfo_row)

        # Buzzer Mode
        buzzer_row = self._make_row("Buzzer Mode:", None)
        self.buzzer_dropdown = Gtk.DropDown.new_from_strings(BUZZER_MODES)
        self.buzzer_dropdown.set_hexpand(True)
        buzzer_row.append(self.buzzer_dropdown)
        buzzer_row.append(self._make_apply_btn(self._apply_buzzer))
        device_box.append(buzzer_row)

        # LED Heartbeat
        led_row = self._make_row("LED Heartbeat:", None)
        self.led_switch = Gtk.Switch()
        self.led_switch.set_halign(Gtk.Align.START)
        self.led_switch.set_active(True)
        led_row.append(self.led_switch)
        led_row.append(self._make_apply_btn(self._apply_led))
        device_box.append(led_row)

        device_frame.set_child(device_box)
        main_box.append(device_frame)

        # === POSITION SETTINGS ===
        pos_frame = Gtk.Frame()
        pos_frame.set_label("Position Settings")
        pos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pos_box.set_margin_start(12)
        pos_box.set_margin_end(12)
        pos_box.set_margin_top(8)
        pos_box.set_margin_bottom(8)

        # GPS Mode
        gpsmode_row = self._make_row("GPS Mode:", None)
        self.gpsmode_dropdown = Gtk.DropDown.new_from_strings(GPS_MODES)
        self.gpsmode_dropdown.set_hexpand(True)
        gpsmode_row.append(self.gpsmode_dropdown)
        gpsmode_row.append(self._make_apply_btn(self._apply_gps_mode))
        pos_box.append(gpsmode_row)

        # Position Broadcast Interval
        posint_row = self._make_row("Broadcast (sec):", None)
        self.posint_spin = Gtk.SpinButton.new_with_range(0, 86400, 60)
        self.posint_spin.set_value(900)
        posint_row.append(self.posint_spin)
        posint_row.append(self._make_apply_btn(self._apply_pos_interval))
        pos_box.append(posint_row)

        # Smart Position
        smart_row = self._make_row("Smart Broadcast:", None)
        self.smart_switch = Gtk.Switch()
        self.smart_switch.set_halign(Gtk.Align.START)
        smart_row.append(self.smart_switch)
        smart_row.append(self._make_apply_btn(self._apply_smart_pos))
        pos_box.append(smart_row)

        # Fixed Position
        fixed_row = self._make_row("Fixed Position:", None)
        self.fixed_switch = Gtk.Switch()
        self.fixed_switch.set_halign(Gtk.Align.START)
        fixed_row.append(self.fixed_switch)
        fixed_row.append(self._make_apply_btn(self._apply_fixed_pos))
        pos_box.append(fixed_row)

        pos_frame.set_child(pos_box)
        main_box.append(pos_frame)

        # === POWER SETTINGS ===
        power_frame = Gtk.Frame()
        power_frame.set_label("Power Settings")
        power_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        power_box.set_margin_start(12)
        power_box.set_margin_end(12)
        power_box.set_margin_top(8)
        power_box.set_margin_bottom(8)

        # Power Saving Mode
        powersave_row = self._make_row("Power Saving:", None)
        self.powersave_switch = Gtk.Switch()
        self.powersave_switch.set_halign(Gtk.Align.START)
        powersave_row.append(self.powersave_switch)
        powersave_row.append(self._make_apply_btn(self._apply_powersave))
        power_box.append(powersave_row)

        power_frame.set_child(power_box)
        main_box.append(power_frame)

        # === DISPLAY SETTINGS ===
        display_frame = Gtk.Frame()
        display_frame.set_label("Display Settings")
        display_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        display_box.set_margin_start(12)
        display_box.set_margin_end(12)
        display_box.set_margin_top(8)
        display_box.set_margin_bottom(8)

        # Screen On Time
        screen_row = self._make_row("Screen On (sec):", None)
        self.screen_spin = Gtk.SpinButton.new_with_range(0, 3600, 10)
        self.screen_spin.set_value(60)
        self.screen_spin.set_tooltip_text("0 = always on")
        screen_row.append(self.screen_spin)
        screen_row.append(self._make_apply_btn(self._apply_screen_time))
        display_box.append(screen_row)

        # Flip Screen
        flip_row = self._make_row("Flip Screen:", None)
        self.flip_switch = Gtk.Switch()
        self.flip_switch.set_halign(Gtk.Align.START)
        flip_row.append(self.flip_switch)
        flip_row.append(self._make_apply_btn(self._apply_flip))
        display_box.append(flip_row)

        # Units
        units_row = self._make_row("Units:", None)
        self.units_dropdown = Gtk.DropDown.new_from_strings(DISPLAY_UNITS)
        self.units_dropdown.set_hexpand(True)
        units_row.append(self.units_dropdown)
        units_row.append(self._make_apply_btn(self._apply_units))
        display_box.append(units_row)

        # OLED Type
        oled_row = self._make_row("OLED Type:", None)
        self.oled_dropdown = Gtk.DropDown.new_from_strings(OLED_TYPES)
        self.oled_dropdown.set_hexpand(True)
        oled_row.append(self.oled_dropdown)
        oled_row.append(self._make_apply_btn(self._apply_oled))
        display_box.append(oled_row)

        display_frame.set_child(display_box)
        main_box.append(display_frame)

        # === BLUETOOTH SETTINGS ===
        bt_frame = Gtk.Frame()
        bt_frame.set_label("Bluetooth Settings")
        bt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bt_box.set_margin_start(12)
        bt_box.set_margin_end(12)
        bt_box.set_margin_top(8)
        bt_box.set_margin_bottom(8)

        # BT Enabled
        bten_row = self._make_row("Bluetooth:", None)
        self.bt_switch = Gtk.Switch()
        self.bt_switch.set_halign(Gtk.Align.START)
        self.bt_switch.set_active(True)
        bten_row.append(self.bt_switch)
        bten_row.append(self._make_apply_btn(self._apply_bt_enabled))
        bt_box.append(bten_row)

        # BT Mode
        btmode_row = self._make_row("Pairing Mode:", None)
        self.btmode_dropdown = Gtk.DropDown.new_from_strings(BT_MODES)
        self.btmode_dropdown.set_hexpand(True)
        btmode_row.append(self.btmode_dropdown)
        btmode_row.append(self._make_apply_btn(self._apply_bt_mode))
        bt_box.append(btmode_row)

        # Fixed PIN
        btpin_row = self._make_row("Fixed PIN:", None)
        self.btpin_entry = Gtk.Entry()
        self.btpin_entry.set_max_length(6)
        self.btpin_entry.set_placeholder_text("123456")
        self.btpin_entry.set_hexpand(True)
        btpin_row.append(self.btpin_entry)
        btpin_row.append(self._make_apply_btn(self._apply_bt_pin))
        bt_box.append(btpin_row)

        bt_frame.set_child(bt_box)
        main_box.append(bt_frame)

        # === INFO DISPLAY ===
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
        main_box.append(info_frame)

        scroll.set_child(main_box)
        self.append(scroll)

    def _make_row(self, label_text, widget):
        """Create a settings row with label."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        label = Gtk.Label(label=label_text)
        label.set_width_chars(16)
        label.set_xalign(0)
        row.append(label)
        if widget:
            row.append(widget)
        return row

    def _make_apply_btn(self, callback):
        """Create an apply button."""
        btn = Gtk.Button(label="Apply")
        btn.connect("clicked", callback)
        return btn

    def _get_interface(self):
        """Get a meshtastic TCP interface."""
        try:
            from meshtastic.tcp_interface import TCPInterface
            import time
            iface = TCPInterface(hostname='localhost', noProto=False)
            # Give it a moment to receive config from device
            time.sleep(0.5)
            return iface
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return None

    def _load_config(self):
        """Load current config from device."""
        def do_load():
            iface = None
            try:
                iface = self._get_interface()
                if not iface:
                    GLib.idle_add(self._update_status, "Failed to connect to meshtasticd")
                    return

                # Wait for config to be received
                import time
                max_wait = 3.0  # seconds
                waited = 0
                while waited < max_wait:
                    config = iface.localNode.localConfig
                    # Check if region is set (indicates config was received)
                    if hasattr(config.lora, 'region') and str(config.lora.region) != '0':
                        break
                    time.sleep(0.2)
                    waited += 0.2

                config = iface.localNode.localConfig
                lora = config.lora
                device = config.device
                position = config.position
                power = config.power

                # Debug: log raw values
                logger.debug(f"[RadioConfig] Raw modem_preset: {lora.modem_preset} type: {type(lora.modem_preset)}")
                logger.debug(f"[RadioConfig] Raw region: {lora.region}")

                # Get LoRa values
                preset_val = int(lora.modem_preset) if hasattr(lora, 'modem_preset') else 0
                hop_val = int(lora.hop_limit) if hasattr(lora, 'hop_limit') else 3
                tx_val = int(lora.tx_power) if hasattr(lora, 'tx_power') else 20
                region_val = str(lora.region) if hasattr(lora, 'region') else 'Unknown'

                # Get device values
                role_val = int(device.role) if hasattr(device, 'role') else 0
                rebroadcast_val = int(device.rebroadcast_mode) if hasattr(device, 'rebroadcast_mode') else 0
                nodeinfo_val = int(device.node_info_broadcast_secs) if hasattr(device, 'node_info_broadcast_secs') else 900

                # Get position values
                gps_enabled = bool(position.gps_enabled) if hasattr(position, 'gps_enabled') else True
                pos_broadcast = int(position.position_broadcast_secs) if hasattr(position, 'position_broadcast_secs') else 900
                smart_pos = bool(position.position_broadcast_smart_enabled) if hasattr(position, 'position_broadcast_smart_enabled') else True
                fixed_pos = bool(position.fixed_position) if hasattr(position, 'fixed_position') else False

                # Get power values
                power_saving = bool(power.is_power_saving) if hasattr(power, 'is_power_saving') else False

                # Build info text
                info = f"=== LoRa ===\n"
                info += f"Region: {region_val}\n"
                info += f"Preset: {PRESETS.get(preset_val, preset_val)} ({preset_val})\n"
                info += f"Hop Limit: {hop_val}\n"
                info += f"TX Power: {tx_val} dBm\n\n"
                info += f"=== Device ===\n"
                info += f"Role: {ROLES.get(role_val, role_val)} ({role_val})\n"
                info += f"Rebroadcast: {REBROADCAST_MODES[rebroadcast_val] if rebroadcast_val < len(REBROADCAST_MODES) else rebroadcast_val}\n"
                info += f"Node Info: {nodeinfo_val}s\n\n"
                info += f"=== Position ===\n"
                info += f"GPS: {'Enabled' if gps_enabled else 'Disabled'}\n"
                info += f"Broadcast: {pos_broadcast}s\n"
                info += f"Smart: {'Yes' if smart_pos else 'No'}\n"
                info += f"Fixed: {'Yes' if fixed_pos else 'No'}\n\n"
                info += f"=== Power ===\n"
                info += f"Power Saving: {'Yes' if power_saving else 'No'}"

                # Check if config was actually received
                config_warning = ""
                if region_val == '0' or region_val == 'UNSET':
                    config_warning = " (config may be stale - try again)"
                    logger.warning("[RadioConfig] Config may not have been fully received (region=0)")

                # Update UI
                config_data = {
                    'preset': preset_val, 'hop': hop_val, 'tx': tx_val, 'region': region_val,
                    'role': role_val, 'rebroadcast': rebroadcast_val, 'nodeinfo': nodeinfo_val,
                    'gps': gps_enabled, 'pos_broadcast': pos_broadcast, 'smart': smart_pos,
                    'fixed': fixed_pos, 'power_saving': power_saving, 'info': info
                }
                GLib.idle_add(self._update_ui, config_data)
                GLib.idle_add(self._update_status, f"Connected{config_warning}")

            except Exception as e:
                logger.error(f"Load config error: {e}")
                GLib.idle_add(self._update_status, f"Error: {e}")
            finally:
                if iface:
                    try:
                        iface.close()
                    except Exception:
                        pass

        threading.Thread(target=do_load, daemon=True).start()
        return False  # Don't repeat

    def _update_ui(self, data):
        """Update UI with loaded values."""
        # LoRa
        self.region_label.set_label(data.get('region', '...'))
        self.preset_dropdown.set_selected(data.get('preset', 0))
        self.hop_spin.set_value(data.get('hop', 3))
        self.tx_spin.set_value(data.get('tx', 20))

        # Device
        self.role_dropdown.set_selected(data.get('role', 0))
        self.rebroadcast_dropdown.set_selected(data.get('rebroadcast', 0))
        self.nodeinfo_spin.set_value(data.get('nodeinfo', 900))

        # Position
        self.gps_switch.set_active(data.get('gps', True))
        self.posint_spin.set_value(data.get('pos_broadcast', 900))
        self.smart_switch.set_active(data.get('smart', True))
        self.fixed_switch.set_active(data.get('fixed', False))

        # Power
        self.powersave_switch.set_active(data.get('power_saving', False))

        # Info
        self.info_label.set_label(data.get('info', ''))

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

    def _apply_rebroadcast(self, button):
        """Apply rebroadcast mode."""
        idx = self.rebroadcast_dropdown.get_selected()
        mode = REBROADCAST_MODES[idx]
        self._apply_setting("device.rebroadcast_mode", mode, f"Rebroadcast: {mode}")

    def _apply_nodeinfo(self, button):
        """Apply node info broadcast interval."""
        secs = int(self.nodeinfo_spin.get_value())
        self._apply_setting("device.node_info_broadcast_secs", str(secs), f"Node Info: {secs}s")

    def _apply_pos_interval(self, button):
        """Apply position broadcast interval."""
        secs = int(self.posint_spin.get_value())
        self._apply_setting("position.position_broadcast_secs", str(secs), f"Position Broadcast: {secs}s")

    def _apply_smart_pos(self, button):
        """Apply smart position broadcast setting."""
        enabled = self.smart_switch.get_active()
        self._apply_setting("position.position_broadcast_smart_enabled", str(enabled).lower(), f"Smart Broadcast: {'On' if enabled else 'Off'}")

    def _apply_fixed_pos(self, button):
        """Apply fixed position setting."""
        enabled = self.fixed_switch.get_active()
        self._apply_setting("position.fixed_position", str(enabled).lower(), f"Fixed Position: {'On' if enabled else 'Off'}")

    def _apply_powersave(self, button):
        """Apply power saving mode."""
        enabled = self.powersave_switch.get_active()
        self._apply_setting("power.is_power_saving", str(enabled).lower(), f"Power Saving: {'On' if enabled else 'Off'}")

    def _apply_tx_enabled(self, button):
        """Apply TX enabled setting."""
        enabled = self.txen_switch.get_active()
        self._apply_setting("lora.tx_enabled", str(enabled).lower(), f"TX: {'Enabled' if enabled else 'Disabled'}")

    def _apply_channel_num(self, button):
        """Apply channel number override."""
        val = int(self.chan_spin.get_value())
        self._apply_setting("lora.channel_num", str(val), f"Channel: {val}")

    def _apply_buzzer(self, button):
        """Apply buzzer mode."""
        idx = self.buzzer_dropdown.get_selected()
        mode = BUZZER_MODES[idx]
        self._apply_setting("device.buzzer_mode", mode, f"Buzzer: {mode}")

    def _apply_led(self, button):
        """Apply LED heartbeat setting."""
        # Note: led_heartbeat_disabled is inverted
        enabled = self.led_switch.get_active()
        self._apply_setting("device.led_heartbeat_disabled", str(not enabled).lower(), f"LED: {'On' if enabled else 'Off'}")

    def _apply_gps_mode(self, button):
        """Apply GPS mode."""
        idx = self.gpsmode_dropdown.get_selected()
        mode = GPS_MODES[idx]
        self._apply_setting("position.gps_mode", mode, f"GPS Mode: {mode}")

    def _apply_screen_time(self, button):
        """Apply screen on time."""
        val = int(self.screen_spin.get_value())
        self._apply_setting("display.screen_on_secs", str(val), f"Screen: {val}s")

    def _apply_flip(self, button):
        """Apply flip screen setting."""
        enabled = self.flip_switch.get_active()
        self._apply_setting("display.flip_screen", str(enabled).lower(), f"Flip: {'Yes' if enabled else 'No'}")

    def _apply_units(self, button):
        """Apply display units."""
        idx = self.units_dropdown.get_selected()
        unit = DISPLAY_UNITS[idx]
        self._apply_setting("display.units", unit, f"Units: {unit}")

    def _apply_oled(self, button):
        """Apply OLED type."""
        idx = self.oled_dropdown.get_selected()
        oled = OLED_TYPES[idx]
        self._apply_setting("display.oled", oled, f"OLED: {oled}")

    def _apply_bt_enabled(self, button):
        """Apply Bluetooth enabled setting."""
        enabled = self.bt_switch.get_active()
        self._apply_setting("bluetooth.enabled", str(enabled).lower(), f"Bluetooth: {'On' if enabled else 'Off'}")

    def _apply_bt_mode(self, button):
        """Apply Bluetooth pairing mode."""
        idx = self.btmode_dropdown.get_selected()
        mode = BT_MODES[idx]
        self._apply_setting("bluetooth.mode", mode, f"BT Mode: {mode}")

    def _apply_bt_pin(self, button):
        """Apply Bluetooth fixed PIN."""
        pin = self.btpin_entry.get_text().strip()
        if pin and len(pin) >= 4:
            self._apply_setting("bluetooth.fixed_pin", pin, f"BT PIN: {pin}")
        else:
            self._update_status("PIN must be at least 4 digits")

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
