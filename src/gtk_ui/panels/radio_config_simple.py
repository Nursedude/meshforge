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

# LoRa regions (from Meshtastic protobuf)
REGIONS = [
    "UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU",
    "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919",
    "SG_923", "PH", "UK_868", "SINGAPORE"
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

# Display modes
DISPLAY_MODES = ["DEFAULT", "TWOCOLOR", "INVERTED", "COLOR"]

# Bandwidths in kHz
BANDWIDTHS = ["31.25", "62.5", "125", "250", "500"]

# Spreading factors
SPREADING_FACTORS = ["7", "8", "9", "10", "11", "12"]

# Coding rates
CODING_RATES = ["4/5", "4/6", "4/7", "4/8"]


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

        # Region - dropdown with warning (must match local regulations)
        region_row = self._make_row("Region:", None)
        self.region_dropdown = Gtk.DropDown.new_from_strings(REGIONS)
        self.region_dropdown.set_hexpand(True)
        self.region_dropdown.set_tooltip_text("⚠️ IMPORTANT: Must match your local radio regulations!")
        region_row.append(self.region_dropdown)
        region_row.append(self._make_apply_btn(self._apply_region))
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

        # Advanced LoRa Settings (expander)
        adv_lora_expander = Gtk.Expander(label="Advanced LoRa (override preset)")
        adv_lora_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        adv_lora_box.set_margin_start(12)
        adv_lora_box.set_margin_top(8)

        # Bandwidth
        bw_row = self._make_row("Bandwidth (kHz):", None)
        self.bw_dropdown = Gtk.DropDown.new_from_strings(BANDWIDTHS)
        self.bw_dropdown.set_hexpand(True)
        self.bw_dropdown.set_selected(3)  # Default 250kHz
        bw_row.append(self.bw_dropdown)
        bw_row.append(self._make_apply_btn(self._apply_bandwidth))
        adv_lora_box.append(bw_row)

        # Spreading Factor
        sf_row = self._make_row("Spreading Factor:", None)
        self.sf_dropdown = Gtk.DropDown.new_from_strings(SPREADING_FACTORS)
        self.sf_dropdown.set_hexpand(True)
        sf_row.append(self.sf_dropdown)
        sf_row.append(self._make_apply_btn(self._apply_spread_factor))
        adv_lora_box.append(sf_row)

        # Coding Rate
        cr_row = self._make_row("Coding Rate:", None)
        self.cr_dropdown = Gtk.DropDown.new_from_strings(CODING_RATES)
        self.cr_dropdown.set_hexpand(True)
        cr_row.append(self.cr_dropdown)
        cr_row.append(self._make_apply_btn(self._apply_coding_rate))
        adv_lora_box.append(cr_row)

        # Frequency Offset
        freq_row = self._make_row("Freq Offset (Hz):", None)
        self.freq_offset_spin = Gtk.SpinButton.new_with_range(-100000, 100000, 100)
        self.freq_offset_spin.set_value(0)
        freq_row.append(self.freq_offset_spin)
        freq_row.append(self._make_apply_btn(self._apply_freq_offset))
        adv_lora_box.append(freq_row)

        # RX Boosted Gain
        rxboost_row = self._make_row("RX Boost (SX126x):", None)
        self.rxboost_switch = Gtk.Switch()
        self.rxboost_switch.set_halign(Gtk.Align.START)
        rxboost_row.append(self.rxboost_switch)
        rxboost_row.append(self._make_apply_btn(self._apply_rx_boost))
        adv_lora_box.append(rxboost_row)

        # Override Duty Cycle
        duty_row = self._make_row("Override Duty Cycle:", None)
        self.duty_switch = Gtk.Switch()
        self.duty_switch.set_halign(Gtk.Align.START)
        self.duty_switch.set_tooltip_text("Override regional duty cycle limits (use responsibly)")
        duty_row.append(self.duty_switch)
        duty_row.append(self._make_apply_btn(self._apply_duty_cycle))
        adv_lora_box.append(duty_row)

        adv_lora_expander.set_child(adv_lora_box)
        lora_box.append(adv_lora_expander)

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

        # === NETWORK SETTINGS (WiFi) ===
        net_frame = Gtk.Frame()
        net_frame.set_label("Network Settings (WiFi)")
        net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        net_box.set_margin_start(12)
        net_box.set_margin_end(12)
        net_box.set_margin_top(8)
        net_box.set_margin_bottom(8)

        # WiFi Enabled
        wifi_row = self._make_row("WiFi Enabled:", None)
        self.wifi_switch = Gtk.Switch()
        self.wifi_switch.set_halign(Gtk.Align.START)
        wifi_row.append(self.wifi_switch)
        wifi_row.append(self._make_apply_btn(self._apply_wifi_enabled))
        net_box.append(wifi_row)

        # WiFi SSID
        ssid_row = self._make_row("WiFi SSID:", None)
        self.ssid_entry = Gtk.Entry()
        self.ssid_entry.set_max_length(32)
        self.ssid_entry.set_placeholder_text("Network name")
        self.ssid_entry.set_hexpand(True)
        ssid_row.append(self.ssid_entry)
        ssid_row.append(self._make_apply_btn(self._apply_wifi_ssid))
        net_box.append(ssid_row)

        # WiFi Password
        psk_row = self._make_row("WiFi Password:", None)
        self.psk_entry = Gtk.Entry()
        self.psk_entry.set_visibility(False)
        self.psk_entry.set_placeholder_text("Password")
        self.psk_entry.set_hexpand(True)
        psk_row.append(self.psk_entry)
        psk_row.append(self._make_apply_btn(self._apply_wifi_psk))
        net_box.append(psk_row)

        # NTP Server
        ntp_row = self._make_row("NTP Server:", None)
        self.ntp_entry = Gtk.Entry()
        self.ntp_entry.set_placeholder_text("pool.ntp.org")
        self.ntp_entry.set_hexpand(True)
        ntp_row.append(self.ntp_entry)
        ntp_row.append(self._make_apply_btn(self._apply_ntp))
        net_box.append(ntp_row)

        net_frame.set_child(net_box)
        main_box.append(net_frame)

        # === CHANNEL SETTINGS ===
        chan_frame = Gtk.Frame()
        chan_frame.set_label("Channel Settings")
        chan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        chan_box.set_margin_start(12)
        chan_box.set_margin_end(12)
        chan_box.set_margin_top(8)
        chan_box.set_margin_bottom(8)

        # Channel selector
        chansel_row = self._make_row("Channel Index:", None)
        self.chanidx_spin = Gtk.SpinButton.new_with_range(0, 7, 1)
        self.chanidx_spin.set_value(0)
        self.chanidx_spin.set_tooltip_text("0=Primary, 1-7=Secondary channels")
        chansel_row.append(self.chanidx_spin)
        chan_box.append(chansel_row)

        # Channel Name
        channame_row = self._make_row("Channel Name:", None)
        self.channame_entry = Gtk.Entry()
        self.channame_entry.set_max_length(12)
        self.channame_entry.set_placeholder_text("LongFast")
        self.channame_entry.set_hexpand(True)
        channame_row.append(self.channame_entry)
        channame_row.append(self._make_apply_btn(self._apply_channel_name))
        chan_box.append(channame_row)

        # Channel PSK (Pre-Shared Key)
        psk_row = self._make_row("Channel PSK:", None)
        self.chanpsk_entry = Gtk.Entry()
        self.chanpsk_entry.set_visibility(False)
        self.chanpsk_entry.set_placeholder_text("base64 or 'random' or 'none' or 'default'")
        self.chanpsk_entry.set_hexpand(True)
        psk_row.append(self.chanpsk_entry)
        psk_row.append(self._make_apply_btn(self._apply_channel_psk))
        chan_box.append(psk_row)

        # PSK options row
        psk_opts_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        psk_opts_row.set_margin_start(120)

        default_btn = Gtk.Button(label="Default")
        default_btn.set_tooltip_text("Use default PSK (AQ==)")
        default_btn.connect("clicked", lambda b: self.chanpsk_entry.set_text("default"))
        psk_opts_row.append(default_btn)

        random_btn = Gtk.Button(label="Random")
        random_btn.set_tooltip_text("Generate random PSK")
        random_btn.connect("clicked", lambda b: self.chanpsk_entry.set_text("random"))
        psk_opts_row.append(random_btn)

        none_btn = Gtk.Button(label="None")
        none_btn.set_tooltip_text("No encryption (open channel)")
        none_btn.connect("clicked", lambda b: self.chanpsk_entry.set_text("none"))
        psk_opts_row.append(none_btn)

        chan_box.append(psk_opts_row)

        # Uplink Enabled
        uplink_row = self._make_row("Uplink Enabled:", None)
        self.uplink_switch = Gtk.Switch()
        self.uplink_switch.set_halign(Gtk.Align.START)
        self.uplink_switch.set_tooltip_text("Allow MQTT uplink for this channel")
        uplink_row.append(self.uplink_switch)
        uplink_row.append(self._make_apply_btn(self._apply_channel_uplink))
        chan_box.append(uplink_row)

        # Downlink Enabled
        downlink_row = self._make_row("Downlink Enabled:", None)
        self.downlink_switch = Gtk.Switch()
        self.downlink_switch.set_halign(Gtk.Align.START)
        self.downlink_switch.set_tooltip_text("Allow MQTT downlink for this channel")
        downlink_row.append(self.downlink_switch)
        downlink_row.append(self._make_apply_btn(self._apply_channel_downlink))
        chan_box.append(downlink_row)

        # Channel URL (display/generate)
        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        url_label = Gtk.Label(label="Share URL:")
        url_label.set_width_chars(16)
        url_label.set_xalign(0)
        url_row.append(url_label)

        gen_url_btn = Gtk.Button(label="Generate Channel URL")
        gen_url_btn.set_tooltip_text("Generate shareable URL for current channel config")
        gen_url_btn.connect("clicked", self._generate_channel_url)
        url_row.append(gen_url_btn)

        chan_box.append(url_row)

        # Channel URL display
        self.channel_url_label = Gtk.Label(label="")
        self.channel_url_label.set_wrap(True)
        self.channel_url_label.set_selectable(True)
        self.channel_url_label.set_xalign(0)
        self.channel_url_label.add_css_class("dim-label")
        chan_box.append(self.channel_url_label)

        chan_frame.set_child(chan_box)
        main_box.append(chan_frame)

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
        # LoRa - region dropdown
        region_val = data.get('region', 'UNSET')
        if region_val in REGIONS:
            self.region_dropdown.set_selected(REGIONS.index(region_val))
        else:
            # Try to match by stripping Config.LoRaConfig.RegionCode prefix
            for i, r in enumerate(REGIONS):
                if r in region_val:
                    self.region_dropdown.set_selected(i)
                    break
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

    def _apply_region(self, button):
        """Apply LoRa region. WARNING: Must comply with local regulations!"""
        region_idx = self.region_dropdown.get_selected()
        region_name = REGIONS[region_idx]
        self._apply_setting("lora.region", region_name, f"Region: {region_name}")

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

    # === ADVANCED LORA ===
    def _apply_bandwidth(self, button):
        """Apply bandwidth setting."""
        idx = self.bw_dropdown.get_selected()
        bw = BANDWIDTHS[idx]
        # Convert kHz to Hz for meshtastic
        bw_hz = int(float(bw) * 1000)
        self._apply_setting("lora.bandwidth", str(bw_hz), f"Bandwidth: {bw} kHz")

    def _apply_spread_factor(self, button):
        """Apply spreading factor."""
        idx = self.sf_dropdown.get_selected()
        sf = SPREADING_FACTORS[idx]
        self._apply_setting("lora.spread_factor", sf, f"SF: {sf}")

    def _apply_coding_rate(self, button):
        """Apply coding rate."""
        idx = self.cr_dropdown.get_selected()
        cr = CODING_RATES[idx]
        # Convert 4/5 -> 5, 4/6 -> 6, etc.
        cr_val = cr.split('/')[1]
        self._apply_setting("lora.coding_rate", cr_val, f"CR: {cr}")

    def _apply_freq_offset(self, button):
        """Apply frequency offset."""
        val = int(self.freq_offset_spin.get_value())
        self._apply_setting("lora.frequency_offset", str(val), f"Freq Offset: {val} Hz")

    def _apply_rx_boost(self, button):
        """Apply RX boosted gain (SX126x)."""
        enabled = self.rxboost_switch.get_active()
        self._apply_setting("lora.sx126x_rx_boosted_gain", str(enabled).lower(), f"RX Boost: {'On' if enabled else 'Off'}")

    def _apply_duty_cycle(self, button):
        """Apply duty cycle override."""
        enabled = self.duty_switch.get_active()
        self._apply_setting("lora.override_duty_cycle", str(enabled).lower(), f"Duty Override: {'On' if enabled else 'Off'}")

    # === NETWORK/WIFI ===
    def _apply_wifi_enabled(self, button):
        """Apply WiFi enabled setting."""
        enabled = self.wifi_switch.get_active()
        self._apply_setting("network.wifi_enabled", str(enabled).lower(), f"WiFi: {'On' if enabled else 'Off'}")

    def _apply_wifi_ssid(self, button):
        """Apply WiFi SSID."""
        ssid = self.ssid_entry.get_text().strip()
        if ssid:
            self._apply_setting("network.wifi_ssid", ssid, f"SSID: {ssid}")
        else:
            self._update_status("Enter an SSID")

    def _apply_wifi_psk(self, button):
        """Apply WiFi password."""
        psk = self.psk_entry.get_text()
        if psk and len(psk) >= 8:
            self._apply_setting("network.wifi_psk", psk, "WiFi Password: [set]")
        else:
            self._update_status("Password must be at least 8 characters")

    def _apply_ntp(self, button):
        """Apply NTP server."""
        ntp = self.ntp_entry.get_text().strip()
        if ntp:
            self._apply_setting("network.ntp_server", ntp, f"NTP: {ntp}")
        else:
            self._update_status("Enter an NTP server")

    # === CHANNEL SETTINGS ===
    def _apply_channel_name(self, button):
        """Apply channel name."""
        idx = int(self.chanidx_spin.get_value())
        name = self.channame_entry.get_text().strip()
        if name:
            self._apply_channel_setting(idx, "name", name, f"Ch{idx} Name: {name}")
        else:
            self._update_status("Enter a channel name")

    def _apply_channel_psk(self, button):
        """Apply channel PSK."""
        idx = int(self.chanidx_spin.get_value())
        psk = self.chanpsk_entry.get_text().strip()
        if psk:
            self._apply_channel_setting(idx, "psk", psk, f"Ch{idx} PSK: [set]")
        else:
            self._update_status("Enter a PSK (or 'default', 'random', 'none')")

    def _apply_channel_uplink(self, button):
        """Apply channel uplink setting."""
        idx = int(self.chanidx_spin.get_value())
        enabled = self.uplink_switch.get_active()
        self._apply_channel_setting(idx, "uplink_enabled", str(enabled).lower(),
                                    f"Ch{idx} Uplink: {'On' if enabled else 'Off'}")

    def _apply_channel_downlink(self, button):
        """Apply channel downlink setting."""
        idx = int(self.chanidx_spin.get_value())
        enabled = self.downlink_switch.get_active()
        self._apply_channel_setting(idx, "downlink_enabled", str(enabled).lower(),
                                    f"Ch{idx} Downlink: {'On' if enabled else 'Off'}")

    def _apply_channel_setting(self, channel_idx, setting, value, desc):
        """Apply a channel setting using meshtastic CLI."""
        def do_apply():
            try:
                import subprocess
                cmd = ['/usr/local/bin/meshtastic', '--host', 'localhost',
                       '--ch-index', str(channel_idx), f'--ch-set', setting, value]
                logger.info(f"Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    GLib.idle_add(self._update_status, f"Applied: {desc}")
                    GLib.timeout_add(2000, self._load_config)
                else:
                    error = result.stderr or result.stdout or "Unknown error"
                    GLib.idle_add(self._update_status, f"Failed: {error[:50]}")
                    logger.error(f"Apply failed: {error}")
            except Exception as e:
                logger.error(f"Apply error: {e}")
                GLib.idle_add(self._update_status, f"Error: {e}")

        self._update_status(f"Applying {desc}...")
        threading.Thread(target=do_apply, daemon=True).start()

    def _generate_channel_url(self, button):
        """Generate shareable channel URL."""
        def do_generate():
            try:
                import subprocess
                cmd = ['/usr/local/bin/meshtastic', '--host', 'localhost', '--qr']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    # Extract URL from output
                    output = result.stdout
                    # Look for URL pattern
                    import re
                    url_match = re.search(r'https://meshtastic\.org/e/#[^\s]+', output)
                    if url_match:
                        url = url_match.group(0)
                        GLib.idle_add(self.channel_url_label.set_label, url)
                        GLib.idle_add(self._update_status, "Channel URL generated")
                    else:
                        GLib.idle_add(self.channel_url_label.set_label, output[:200])
                        GLib.idle_add(self._update_status, "URL generated (see output)")
                else:
                    error = result.stderr or result.stdout or "Unknown error"
                    GLib.idle_add(self._update_status, f"Failed: {error[:50]}")
            except Exception as e:
                logger.error(f"Generate URL error: {e}")
                GLib.idle_add(self._update_status, f"Error: {e}")

        self._update_status("Generating channel URL...")
        threading.Thread(target=do_generate, daemon=True).start()

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
