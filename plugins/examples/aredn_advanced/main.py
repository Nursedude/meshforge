"""
AREDN Advanced Configuration Plugin

Provides comprehensive AREDN mesh network configuration tools:
- Hardware database browser
- MikroTik configuration wizard
- Network topology simulation
- Link budget calculator for AREDN
- Configuration export/import

This plugin integrates with MeshForge's AREDN hardware module for
advanced configuration beyond the built-in AREDN panel.
"""

import sys
from pathlib import Path

# Add parent paths for imports when running as plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'src'))

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw, GLib, Pango
    GTK_AVAILABLE = True
except ImportError:
    GTK_AVAILABLE = False

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import json
import threading


# Import AREDN hardware module
try:
    from utils.aredn_hardware import (
        DeviceDatabase, DeviceSpec, DeviceType, FrequencyBand,
        MikroTikConfig, NetworkSimulator, create_sample_network
    )
    AREDN_HARDWARE_AVAILABLE = True
except ImportError:
    AREDN_HARDWARE_AVAILABLE = False


@dataclass
class PluginContext:
    """Context provided by MeshForge plugin system"""
    settings: Dict[str, Any]
    data_dir: Path
    app_version: str


class AREDNAdvancedPlugin:
    """
    AREDN Advanced Configuration Plugin

    Extends MeshForge with comprehensive AREDN configuration tools.
    """

    def __init__(self, context: PluginContext):
        self.context = context
        self.settings = context.settings
        self.simulator: Optional[NetworkSimulator] = None
        self._config_cache: Dict[str, MikroTikConfig] = {}

    def get_panel(self) -> Optional[Gtk.Widget]:
        """Return the plugin's GTK panel"""
        if not GTK_AVAILABLE:
            return None

        if not AREDN_HARDWARE_AVAILABLE:
            # Return error message panel
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_top(20)
            box.set_margin_bottom(20)
            box.set_margin_start(20)
            box.set_margin_end(20)

            label = Gtk.Label(label="AREDN Hardware Module Not Available")
            label.add_css_class("title-1")
            box.append(label)

            info = Gtk.Label(label="The aredn_hardware module could not be loaded.\nEnsure MeshForge is properly installed.")
            info.add_css_class("dim-label")
            box.append(info)

            return box

        return AREDNAdvancedPanel(self)


class AREDNAdvancedPanel(Gtk.Box):
    """Main panel for AREDN Advanced Configuration"""

    def __init__(self, plugin: AREDNAdvancedPlugin):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.plugin = plugin
        self._selected_device: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        """Build the plugin UI"""
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(15)
        header.set_margin_bottom(10)
        header.set_margin_start(15)
        header.set_margin_end(15)

        title = Gtk.Label(label="AREDN Advanced Configuration")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.START)
        header.append(title)

        self.append(header)

        # Notebook for tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)

        # Tab 1: Hardware Browser
        self.notebook.append_page(
            self._build_hardware_tab(),
            Gtk.Label(label="Hardware Database")
        )

        # Tab 2: Configuration Wizard
        self.notebook.append_page(
            self._build_config_tab(),
            Gtk.Label(label="Configuration")
        )

        # Tab 3: Network Simulation
        self.notebook.append_page(
            self._build_simulation_tab(),
            Gtk.Label(label="Network Simulation")
        )

        # Tab 4: Link Budget
        self.notebook.append_page(
            self._build_link_budget_tab(),
            Gtk.Label(label="Link Budget")
        )

        self.append(self.notebook)

    def _build_hardware_tab(self) -> Gtk.Widget:
        """Build hardware database browser tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        # Filter controls
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Manufacturer filter
        mfg_label = Gtk.Label(label="Manufacturer:")
        filter_box.append(mfg_label)

        self.mfg_combo = Gtk.ComboBoxText()
        self.mfg_combo.append_text("All")
        self.mfg_combo.append_text("MikroTik")
        self.mfg_combo.append_text("Ubiquiti")
        self.mfg_combo.append_text("GL.iNet")
        self.mfg_combo.set_active(0)
        self.mfg_combo.connect("changed", self._on_filter_changed)
        filter_box.append(self.mfg_combo)

        # Type filter
        type_label = Gtk.Label(label="Type:")
        filter_box.append(type_label)

        self.type_combo = Gtk.ComboBoxText()
        self.type_combo.append_text("All")
        self.type_combo.append_text("Router")
        self.type_combo.append_text("Sector")
        self.type_combo.append_text("Dish")
        self.type_combo.append_text("Panel")
        self.type_combo.set_active(0)
        self.type_combo.connect("changed", self._on_filter_changed)
        filter_box.append(self.type_combo)

        box.append(filter_box)

        # Hardware list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Create list store: device_id, manufacturer, model, type, bands, ram, status
        self.hw_store = Gtk.ListStore(str, str, str, str, str, str, str)
        self.hw_tree = Gtk.TreeView(model=self.hw_store)
        self.hw_tree.set_headers_visible(True)

        columns = [
            ("Manufacturer", 1),
            ("Model", 2),
            ("Type", 3),
            ("Bands", 4),
            ("RAM", 5),
            ("Status", 6),
        ]
        for title, col_idx in columns:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=col_idx)
            column.set_resizable(True)
            column.set_min_width(80)
            self.hw_tree.append_column(column)

        self.hw_tree.connect("cursor-changed", self._on_device_selected)
        scrolled.set_child(self.hw_tree)
        box.append(scrolled)

        # Device details panel
        details_frame = Gtk.Frame(label="Device Details")
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.details_box.set_margin_top(10)
        self.details_box.set_margin_bottom(10)
        self.details_box.set_margin_start(10)
        self.details_box.set_margin_end(10)

        self.details_label = Gtk.Label(label="Select a device to view details")
        self.details_label.set_halign(Gtk.Align.START)
        self.details_label.add_css_class("dim-label")
        self.details_box.append(self.details_label)

        details_frame.set_child(self.details_box)
        box.append(details_frame)

        # Load initial data
        self._populate_hardware_list()

        return box

    def _populate_hardware_list(self):
        """Populate hardware list from database"""
        self.hw_store.clear()

        mfg_filter = self.mfg_combo.get_active_text()
        type_filter = self.type_combo.get_active_text()

        devices = DeviceDatabase.get_all_devices()
        for device_id, device in devices.items():
            # Apply filters
            if mfg_filter != "All" and device.manufacturer != mfg_filter:
                continue
            if type_filter != "All" and device.device_type.value.capitalize() != type_filter:
                continue

            bands = ", ".join(b.value for b in device.frequency_bands)
            ram = f"{device.ram_mb} MB" if device.ram_mb else "N/A"
            status = "✓ Supported" if device.supported else "Limited"

            self.hw_store.append([
                device_id,
                device.manufacturer,
                device.model,
                device.device_type.value.capitalize(),
                bands,
                ram,
                status
            ])

    def _on_filter_changed(self, combo):
        """Handle filter combo change"""
        self._populate_hardware_list()

    def _on_device_selected(self, tree):
        """Handle device selection in tree"""
        selection = tree.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter:
            device_id = model[tree_iter][0]
            self._selected_device = device_id
            self._show_device_details(device_id)

    def _show_device_details(self, device_id: str):
        """Show detailed device information"""
        device = DeviceDatabase.get_device(device_id)
        if not device:
            return

        # Clear existing details
        while self.details_box.get_first_child():
            self.details_box.remove(self.details_box.get_first_child())

        # Device name
        name_label = Gtk.Label(label=f"{device.manufacturer} {device.model}")
        name_label.add_css_class("title-2")
        name_label.set_halign(Gtk.Align.START)
        self.details_box.append(name_label)

        # Grid for specs
        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(20)

        specs = [
            ("Type", device.device_type.value.capitalize()),
            ("CPU", device.cpu or "N/A"),
            ("RAM", f"{device.ram_mb} MB" if device.ram_mb else "N/A"),
            ("Flash", f"{device.flash_mb} MB" if device.flash_mb else "N/A"),
            ("Ethernet Ports", str(device.ethernet_ports)),
            ("WiFi Radios", str(device.wifi_radios)),
            ("Max TX Power", f"{device.max_tx_power_dbm} dBm" if device.max_tx_power_dbm else "N/A"),
            ("Antenna Gain", f"{device.antenna_gain_dbi} dBi" if device.antenna_gain_dbi else "External"),
            ("PoE Input", "Yes" if device.poe_input else "No"),
            ("PoE Output", "Yes" if device.poe_output else "No"),
            ("Min Firmware", device.min_firmware or "N/A"),
        ]

        for i, (label, value) in enumerate(specs):
            row = i // 2
            col = (i % 2) * 2

            lbl = Gtk.Label(label=f"{label}:")
            lbl.add_css_class("dim-label")
            lbl.set_halign(Gtk.Align.END)
            grid.attach(lbl, col, row, 1, 1)

            val = Gtk.Label(label=value)
            val.set_halign(Gtk.Align.START)
            grid.attach(val, col + 1, row, 1, 1)

        self.details_box.append(grid)

        # Notes
        if device.notes:
            notes_label = Gtk.Label(label=f"Notes: {device.notes}")
            notes_label.set_wrap(True)
            notes_label.set_halign(Gtk.Align.START)
            notes_label.add_css_class("dim-label")
            self.details_box.append(notes_label)

        # Configure button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_margin_top(10)

        config_btn = Gtk.Button(label="Configure This Device")
        config_btn.add_css_class("suggested-action")
        config_btn.connect("clicked", self._on_configure_device)
        btn_box.append(config_btn)

        self.details_box.append(btn_box)

    def _on_configure_device(self, button):
        """Switch to configuration tab with selected device"""
        if self._selected_device:
            device = DeviceDatabase.get_device(self._selected_device)
            if device:
                self._load_device_config(device)
                self.notebook.set_current_page(1)  # Switch to Config tab

    def _build_config_tab(self) -> Gtk.Widget:
        """Build configuration wizard tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        # Device selection
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        device_label = Gtk.Label(label="Target Device:")
        device_box.append(device_label)

        self.device_combo = Gtk.ComboBoxText()
        devices = DeviceDatabase.get_all_devices()
        for device_id, device in devices.items():
            self.device_combo.append(device_id, f"{device.manufacturer} {device.model}")
        self.device_combo.connect("changed", self._on_config_device_changed)
        device_box.append(self.device_combo)

        box.append(device_box)

        # Configuration form in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form_box.set_margin_top(10)
        form_box.set_margin_bottom(10)

        # Basic Settings frame
        basic_frame = Gtk.Frame(label="Basic Settings")
        basic_grid = Gtk.Grid()
        basic_grid.set_row_spacing(8)
        basic_grid.set_column_spacing(12)
        basic_grid.set_margin_top(10)
        basic_grid.set_margin_bottom(10)
        basic_grid.set_margin_start(10)
        basic_grid.set_margin_end(10)

        # Hostname
        basic_grid.attach(Gtk.Label(label="Hostname:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.hostname_entry = Gtk.Entry()
        self.hostname_entry.set_placeholder_text("e.g., KK6ABC-TOWER")
        self.hostname_entry.set_hexpand(True)
        basic_grid.attach(self.hostname_entry, 1, 0, 1, 1)

        # Callsign
        basic_grid.attach(Gtk.Label(label="Callsign:", halign=Gtk.Align.END), 0, 1, 1, 1)
        self.callsign_entry = Gtk.Entry()
        self.callsign_entry.set_placeholder_text("e.g., KK6ABC")
        self.callsign_entry.set_text(self.plugin.settings.get('callsign', ''))
        basic_grid.attach(self.callsign_entry, 1, 1, 1, 1)

        # Mesh IP
        basic_grid.attach(Gtk.Label(label="Mesh IP:", halign=Gtk.Align.END), 0, 2, 1, 1)
        self.mesh_ip_entry = Gtk.Entry()
        self.mesh_ip_entry.set_placeholder_text("e.g., 10.0.0.5")
        basic_grid.attach(self.mesh_ip_entry, 1, 2, 1, 1)

        basic_frame.set_child(basic_grid)
        form_box.append(basic_frame)

        # RF Settings frame
        rf_frame = Gtk.Frame(label="RF Configuration")
        rf_grid = Gtk.Grid()
        rf_grid.set_row_spacing(8)
        rf_grid.set_column_spacing(12)
        rf_grid.set_margin_top(10)
        rf_grid.set_margin_bottom(10)
        rf_grid.set_margin_start(10)
        rf_grid.set_margin_end(10)

        # Channel
        rf_grid.attach(Gtk.Label(label="Channel:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.channel_spin = Gtk.SpinButton.new_with_range(36, 177, 4)
        self.channel_spin.set_value(self.plugin.settings.get('default_channel', 177))
        rf_grid.attach(self.channel_spin, 1, 0, 1, 1)

        # Channel Width
        rf_grid.attach(Gtk.Label(label="Channel Width:", halign=Gtk.Align.END), 0, 1, 1, 1)
        self.width_combo = Gtk.ComboBoxText()
        self.width_combo.append_text("5 MHz")
        self.width_combo.append_text("10 MHz")
        self.width_combo.append_text("20 MHz")
        self.width_combo.set_active(2)  # 20 MHz default
        rf_grid.attach(self.width_combo, 1, 1, 1, 1)

        # TX Power
        rf_grid.attach(Gtk.Label(label="TX Power (dBm):", halign=Gtk.Align.END), 0, 2, 1, 1)
        self.power_spin = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.power_spin.set_value(self.plugin.settings.get('default_power', 17))
        rf_grid.attach(self.power_spin, 1, 2, 1, 1)

        rf_frame.set_child(rf_grid)
        form_box.append(rf_frame)

        # Network Settings frame
        net_frame = Gtk.Frame(label="Network Configuration")
        net_grid = Gtk.Grid()
        net_grid.set_row_spacing(8)
        net_grid.set_column_spacing(12)
        net_grid.set_margin_top(10)
        net_grid.set_margin_bottom(10)
        net_grid.set_margin_start(10)
        net_grid.set_margin_end(10)

        # WAN Mode
        net_grid.attach(Gtk.Label(label="WAN Mode:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.wan_combo = Gtk.ComboBoxText()
        self.wan_combo.append_text("DHCP")
        self.wan_combo.append_text("Static")
        self.wan_combo.append_text("Disabled")
        self.wan_combo.set_active(0)
        net_grid.attach(self.wan_combo, 1, 0, 1, 1)

        # DtD Enable
        self.dtd_check = Gtk.CheckButton(label="Enable Device-to-Device (DtD) Linking")
        self.dtd_check.set_active(True)
        net_grid.attach(self.dtd_check, 0, 1, 2, 1)

        # Tunnel Enable
        self.tunnel_check = Gtk.CheckButton(label="Enable Tunnel Server")
        net_grid.attach(self.tunnel_check, 0, 2, 2, 1)

        net_frame.set_child(net_grid)
        form_box.append(net_frame)

        scrolled.set_child(form_box)
        box.append(scrolled)

        # Action buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)

        validate_btn = Gtk.Button(label="Validate")
        validate_btn.connect("clicked", self._on_validate_config)
        btn_box.append(validate_btn)

        generate_btn = Gtk.Button(label="Generate Script")
        generate_btn.add_css_class("suggested-action")
        generate_btn.connect("clicked", self._on_generate_script)
        btn_box.append(generate_btn)

        box.append(btn_box)

        # Output area
        output_frame = Gtk.Frame(label="Generated Configuration")
        self.config_output = Gtk.TextView()
        self.config_output.set_editable(False)
        self.config_output.set_monospace(True)
        self.config_output.set_wrap_mode(Gtk.WrapMode.WORD)

        output_scroll = Gtk.ScrolledWindow()
        output_scroll.set_min_content_height(150)
        output_scroll.set_child(self.config_output)
        output_frame.set_child(output_scroll)
        box.append(output_frame)

        return box

    def _load_device_config(self, device: DeviceSpec):
        """Load device into configuration form"""
        # Find device in combo
        model = self.device_combo.get_model()
        for i, row in enumerate(model):
            if row[0] == f"{device.manufacturer} {device.model}":
                self.device_combo.set_active(i)
                break

        # Update max power based on device
        if device.max_tx_power_dbm:
            self.power_spin.set_range(1, device.max_tx_power_dbm)

    def _on_config_device_changed(self, combo):
        """Handle device selection change in config tab"""
        device_id = combo.get_active_id()
        if device_id:
            device = DeviceDatabase.get_device(device_id)
            if device and device.max_tx_power_dbm:
                self.power_spin.set_range(1, device.max_tx_power_dbm)

    def _on_validate_config(self, button):
        """Validate current configuration"""
        device_id = self.device_combo.get_active_id()
        if not device_id:
            self._show_output("Error: Please select a device first")
            return

        device = DeviceDatabase.get_device(device_id)
        if not device:
            self._show_output("Error: Device not found")
            return

        width_text = self.width_combo.get_active_text()
        width = int(width_text.split()[0]) if width_text else 20

        config = MikroTikConfig(
            device=device,
            hostname=self.hostname_entry.get_text(),
            callsign=self.callsign_entry.get_text(),
            mesh_ip=self.mesh_ip_entry.get_text(),
            channel=int(self.channel_spin.get_value()),
            channel_width=width,
            tx_power=int(self.power_spin.get_value()),
            wan_mode=["dhcp", "static", "disabled"][self.wan_combo.get_active()],
            dtd_enabled=self.dtd_check.get_active(),
            tunnel_enabled=self.tunnel_check.get_active()
        )

        errors = config.validate()
        if errors:
            self._show_output("Validation Errors:\n\n" + "\n".join(f"• {e}" for e in errors))
        else:
            self._show_output("✓ Configuration is valid!")

    def _on_generate_script(self, button):
        """Generate configuration script"""
        device_id = self.device_combo.get_active_id()
        if not device_id:
            self._show_output("Error: Please select a device first")
            return

        device = DeviceDatabase.get_device(device_id)
        if not device:
            self._show_output("Error: Device not found")
            return

        width_text = self.width_combo.get_active_text()
        width = int(width_text.split()[0]) if width_text else 20

        config = MikroTikConfig(
            device=device,
            hostname=self.hostname_entry.get_text() or "AREDN-NODE",
            callsign=self.callsign_entry.get_text(),
            mesh_ip=self.mesh_ip_entry.get_text(),
            channel=int(self.channel_spin.get_value()),
            channel_width=width,
            tx_power=int(self.power_spin.get_value()),
            wan_mode=["dhcp", "static", "disabled"][self.wan_combo.get_active()],
            dtd_enabled=self.dtd_check.get_active(),
            tunnel_enabled=self.tunnel_check.get_active()
        )

        # Validate first
        errors = config.validate()
        if errors:
            self._show_output("Fix validation errors first:\n\n" + "\n".join(f"• {e}" for e in errors))
            return

        script = config.generate_setup_script()
        self._show_output(script)

    def _show_output(self, text: str):
        """Display text in output area"""
        buffer = self.config_output.get_buffer()
        buffer.set_text(text)

    def _build_simulation_tab(self) -> Gtk.Widget:
        """Build network simulation tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        # Description
        desc = Gtk.Label(label="Design and test AREDN network topologies before deployment")
        desc.add_css_class("dim-label")
        desc.set_halign(Gtk.Align.START)
        box.append(desc)

        # Control buttons
        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        new_btn = Gtk.Button(label="New Simulation")
        new_btn.connect("clicked", self._on_new_simulation)
        ctrl_box.append(new_btn)

        sample_btn = Gtk.Button(label="Load Sample Network")
        sample_btn.add_css_class("suggested-action")
        sample_btn.connect("clicked", self._on_load_sample)
        ctrl_box.append(sample_btn)

        analyze_btn = Gtk.Button(label="Analyze Network")
        analyze_btn.connect("clicked", self._on_analyze_network)
        ctrl_box.append(analyze_btn)

        export_btn = Gtk.Button(label="Export Graphviz")
        export_btn.connect("clicked", self._on_export_graphviz)
        ctrl_box.append(export_btn)

        box.append(ctrl_box)

        # Split pane for node list and details
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)

        # Node list
        node_frame = Gtk.Frame(label="Simulated Nodes")
        node_scroll = Gtk.ScrolledWindow()
        node_scroll.set_min_content_width(300)

        self.sim_store = Gtk.ListStore(str, str, str, str, str)  # id, hostname, model, ip, links
        self.sim_tree = Gtk.TreeView(model=self.sim_store)
        self.sim_tree.set_headers_visible(True)

        for i, title in enumerate(["Hostname", "Device", "IP", "Links"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i+1)
            column.set_resizable(True)
            self.sim_tree.append_column(column)

        self.sim_tree.connect("cursor-changed", self._on_sim_node_selected)
        node_scroll.set_child(self.sim_tree)
        node_frame.set_child(node_scroll)
        paned.set_start_child(node_frame)

        # Details/Analysis
        details_frame = Gtk.Frame(label="Network Analysis")
        self.sim_output = Gtk.TextView()
        self.sim_output.set_editable(False)
        self.sim_output.set_monospace(True)
        self.sim_output.set_wrap_mode(Gtk.WrapMode.WORD)

        details_scroll = Gtk.ScrolledWindow()
        details_scroll.set_child(self.sim_output)
        details_frame.set_child(details_scroll)
        paned.set_end_child(details_frame)

        paned.set_position(350)
        box.append(paned)

        # Add node controls
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        add_box.set_margin_top(10)

        add_box.append(Gtk.Label(label="Add Node:"))

        self.add_hostname_entry = Gtk.Entry()
        self.add_hostname_entry.set_placeholder_text("Hostname")
        self.add_hostname_entry.set_width_chars(15)
        add_box.append(self.add_hostname_entry)

        self.add_device_combo = Gtk.ComboBoxText()
        devices = DeviceDatabase.get_all_devices()
        for device_id, device in devices.items():
            self.add_device_combo.append(device_id, f"{device.model}")
        self.add_device_combo.set_active(0)
        add_box.append(self.add_device_combo)

        self.add_x_spin = Gtk.SpinButton.new_with_range(-10000, 10000, 100)
        self.add_x_spin.set_value(0)
        add_box.append(Gtk.Label(label="X:"))
        add_box.append(self.add_x_spin)

        self.add_y_spin = Gtk.SpinButton.new_with_range(-10000, 10000, 100)
        self.add_y_spin.set_value(0)
        add_box.append(Gtk.Label(label="Y:"))
        add_box.append(self.add_y_spin)

        add_btn = Gtk.Button(label="Add")
        add_btn.connect("clicked", self._on_add_sim_node)
        add_box.append(add_btn)

        box.append(add_box)

        return box

    def _on_new_simulation(self, button):
        """Create new empty simulation"""
        self.plugin.simulator = NetworkSimulator()
        self._refresh_sim_tree()
        self._show_sim_output("New simulation created.\nAdd nodes to begin designing your network.")

    def _on_load_sample(self, button):
        """Load sample network"""
        self.plugin.simulator = create_sample_network()
        self._refresh_sim_tree()
        self._on_analyze_network(None)

    def _refresh_sim_tree(self):
        """Refresh simulation node tree"""
        self.sim_store.clear()
        if not self.plugin.simulator:
            return

        for node_id, node in self.plugin.simulator.nodes.items():
            self.sim_store.append([
                node_id,
                node.hostname,
                node.device.model,
                node.ip_address,
                str(len(node.links))
            ])

    def _on_sim_node_selected(self, tree):
        """Handle node selection in simulation"""
        selection = tree.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter and self.plugin.simulator:
            node_id = model[tree_iter][0]
            node = self.plugin.simulator.nodes.get(node_id)
            if node:
                lines = [
                    f"Node: {node.hostname}",
                    f"Device: {node.device.manufacturer} {node.device.model}",
                    f"IP: {node.ip_address}",
                    f"Position: ({node.position[0]}, {node.position[1]})",
                    "",
                    "Links:"
                ]
                for link_id in node.links:
                    quality = node.link_qualities.get(link_id, 0)
                    linked_node = self.plugin.simulator.nodes.get(link_id)
                    if linked_node:
                        lines.append(f"  → {linked_node.hostname}: {quality:.0f}%")

                self._show_sim_output("\n".join(lines))

    def _on_analyze_network(self, button):
        """Analyze network topology"""
        if not self.plugin.simulator:
            self._show_sim_output("No simulation loaded. Create new or load sample.")
            return

        analysis = self.plugin.simulator.analyze_network()

        lines = [
            "═══════════════════════════════════════",
            "       NETWORK ANALYSIS REPORT",
            "═══════════════════════════════════════",
            "",
            f"Total Nodes:     {analysis['node_count']}",
            f"Total Links:     {analysis['total_links']}",
            f"Avg Links/Node:  {analysis['average_links_per_node']:.1f}",
            f"Network Connected: {'Yes ✓' if analysis['is_connected'] else 'No ✗'}",
            f"Network Diameter:  {analysis['network_diameter']} hops",
            "",
        ]

        if analysis['isolated_nodes']:
            lines.append("⚠ Isolated Nodes (no links):")
            for node_id in analysis['isolated_nodes']:
                node = self.plugin.simulator.nodes.get(node_id)
                if node:
                    lines.append(f"  • {node.hostname}")
            lines.append("")

        if analysis['hub_nodes']:
            lines.append("★ Hub Nodes (high connectivity):")
            for hub in analysis['hub_nodes']:
                node = self.plugin.simulator.nodes.get(hub['node_id'])
                if node:
                    lines.append(f"  • {node.hostname}: {hub['link_count']} links")
            lines.append("")

        if analysis['weak_links']:
            lines.append("⚠ Weak Links (<70% quality):")
            shown = 0
            for link in analysis['weak_links']:
                if shown >= 5:
                    lines.append(f"  ... and {len(analysis['weak_links']) - 5} more")
                    break
                from_node = self.plugin.simulator.nodes.get(link['from'])
                to_node = self.plugin.simulator.nodes.get(link['to'])
                if from_node and to_node:
                    lines.append(f"  • {from_node.hostname} ↔ {to_node.hostname}: {link['quality']:.0f}%")
                    shown += 1

        self._show_sim_output("\n".join(lines))

    def _on_export_graphviz(self, button):
        """Export network as Graphviz DOT"""
        if not self.plugin.simulator:
            self._show_sim_output("No simulation loaded.")
            return

        dot = self.plugin.simulator.generate_graphviz()
        self._show_sim_output(f"# Graphviz DOT Format\n# Save to file.dot and run: dot -Tpng file.dot -o network.png\n\n{dot}")

    def _on_add_sim_node(self, button):
        """Add node to simulation"""
        if not self.plugin.simulator:
            self.plugin.simulator = NetworkSimulator()

        hostname = self.add_hostname_entry.get_text()
        if not hostname:
            hostname = f"NODE-{len(self.plugin.simulator.nodes) + 1}"

        device_id = self.add_device_combo.get_active_id()
        if not device_id:
            return

        x = self.add_x_spin.get_value()
        y = self.add_y_spin.get_value()

        try:
            node = self.plugin.simulator.add_node(hostname, device_id, (x, y))
            # Auto-create links
            self.plugin.simulator.auto_create_links()
            self._refresh_sim_tree()
            self._show_sim_output(f"Added node: {hostname}\nAuto-linked to nearby nodes.")
        except Exception as e:
            self._show_sim_output(f"Error adding node: {e}")

    def _show_sim_output(self, text: str):
        """Show text in simulation output"""
        buffer = self.sim_output.get_buffer()
        buffer.set_text(text)

    def _build_link_budget_tab(self) -> Gtk.Widget:
        """Build link budget calculator tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)

        desc = Gtk.Label(label="Calculate link viability between AREDN nodes")
        desc.add_css_class("dim-label")
        desc.set_halign(Gtk.Align.START)
        box.append(desc)

        # Form grid
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(15)
        grid.set_margin_top(10)

        # TX Side
        tx_label = Gtk.Label(label="Transmitter Side")
        tx_label.add_css_class("title-3")
        grid.attach(tx_label, 0, 0, 2, 1)

        grid.attach(Gtk.Label(label="TX Power (dBm):", halign=Gtk.Align.END), 0, 1, 1, 1)
        self.lb_tx_power = Gtk.SpinButton.new_with_range(1, 30, 1)
        self.lb_tx_power.set_value(17)
        grid.attach(self.lb_tx_power, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="TX Antenna Gain (dBi):", halign=Gtk.Align.END), 0, 2, 1, 1)
        self.lb_tx_gain = Gtk.SpinButton.new_with_range(0, 30, 0.5)
        self.lb_tx_gain.set_value(8)
        grid.attach(self.lb_tx_gain, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="TX Cable Loss (dB):", halign=Gtk.Align.END), 0, 3, 1, 1)
        self.lb_tx_loss = Gtk.SpinButton.new_with_range(0, 10, 0.5)
        self.lb_tx_loss.set_value(1)
        grid.attach(self.lb_tx_loss, 1, 3, 1, 1)

        # RX Side
        rx_label = Gtk.Label(label="Receiver Side")
        rx_label.add_css_class("title-3")
        grid.attach(rx_label, 2, 0, 2, 1)

        grid.attach(Gtk.Label(label="RX Antenna Gain (dBi):", halign=Gtk.Align.END), 2, 1, 1, 1)
        self.lb_rx_gain = Gtk.SpinButton.new_with_range(0, 30, 0.5)
        self.lb_rx_gain.set_value(8)
        grid.attach(self.lb_rx_gain, 3, 1, 1, 1)

        grid.attach(Gtk.Label(label="RX Cable Loss (dB):", halign=Gtk.Align.END), 2, 2, 1, 1)
        self.lb_rx_loss = Gtk.SpinButton.new_with_range(0, 10, 0.5)
        self.lb_rx_loss.set_value(1)
        grid.attach(self.lb_rx_loss, 3, 2, 1, 1)

        grid.attach(Gtk.Label(label="RX Sensitivity (dBm):", halign=Gtk.Align.END), 2, 3, 1, 1)
        self.lb_sensitivity = Gtk.SpinButton.new_with_range(-100, -50, 1)
        self.lb_sensitivity.set_value(-90)
        grid.attach(self.lb_sensitivity, 3, 3, 1, 1)

        # Path
        path_label = Gtk.Label(label="Path")
        path_label.add_css_class("title-3")
        grid.attach(path_label, 0, 5, 2, 1)

        grid.attach(Gtk.Label(label="Distance (km):", halign=Gtk.Align.END), 0, 6, 1, 1)
        self.lb_distance = Gtk.SpinButton.new_with_range(0.1, 100, 0.1)
        self.lb_distance.set_value(5)
        grid.attach(self.lb_distance, 1, 6, 1, 1)

        grid.attach(Gtk.Label(label="Frequency (MHz):", halign=Gtk.Align.END), 0, 7, 1, 1)
        self.lb_freq = Gtk.SpinButton.new_with_range(900, 6000, 1)
        self.lb_freq.set_value(5800)
        grid.attach(self.lb_freq, 1, 7, 1, 1)

        grid.attach(Gtk.Label(label="Additional Loss (dB):", halign=Gtk.Align.END), 0, 8, 1, 1)
        self.lb_extra_loss = Gtk.SpinButton.new_with_range(0, 30, 1)
        self.lb_extra_loss.set_value(0)
        grid.attach(self.lb_extra_loss, 1, 8, 1, 1)

        box.append(grid)

        # Calculate button
        calc_btn = Gtk.Button(label="Calculate Link Budget")
        calc_btn.add_css_class("suggested-action")
        calc_btn.connect("clicked", self._on_calculate_link_budget)
        box.append(calc_btn)

        # Results
        result_frame = Gtk.Frame(label="Link Budget Results")
        self.lb_output = Gtk.TextView()
        self.lb_output.set_editable(False)
        self.lb_output.set_monospace(True)

        result_scroll = Gtk.ScrolledWindow()
        result_scroll.set_min_content_height(200)
        result_scroll.set_child(self.lb_output)
        result_frame.set_child(result_scroll)
        box.append(result_frame)

        return box

    def _on_calculate_link_budget(self, button):
        """Calculate link budget"""
        import math

        tx_power = self.lb_tx_power.get_value()
        tx_gain = self.lb_tx_gain.get_value()
        tx_loss = self.lb_tx_loss.get_value()
        rx_gain = self.lb_rx_gain.get_value()
        rx_loss = self.lb_rx_loss.get_value()
        sensitivity = self.lb_sensitivity.get_value()
        distance = self.lb_distance.get_value()
        freq = self.lb_freq.get_value()
        extra_loss = self.lb_extra_loss.get_value()

        # EIRP
        eirp = tx_power + tx_gain - tx_loss

        # Free Space Path Loss
        # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
        # Simplified: FSPL = 20*log10(d_km) + 20*log10(f_MHz) + 32.44
        fspl = 20 * math.log10(distance) + 20 * math.log10(freq) + 32.44

        # Total path loss
        total_loss = fspl + extra_loss

        # Received signal
        rx_signal = eirp - total_loss + rx_gain - rx_loss

        # Link margin
        margin = rx_signal - sensitivity

        # Determine viability
        if margin > 20:
            status = "EXCELLENT - Strong link with good fade margin"
            status_icon = "✓✓"
        elif margin > 10:
            status = "GOOD - Reliable link"
            status_icon = "✓"
        elif margin > 0:
            status = "MARGINAL - May work but unreliable"
            status_icon = "⚠"
        else:
            status = "NOT VIABLE - Insufficient signal"
            status_icon = "✗"

        lines = [
            "═══════════════════════════════════════════",
            "           LINK BUDGET CALCULATION",
            "═══════════════════════════════════════════",
            "",
            "TRANSMITTER",
            f"  TX Power:         {tx_power:+.1f} dBm",
            f"  TX Antenna Gain:  {tx_gain:+.1f} dBi",
            f"  TX Cable Loss:    {tx_loss:-.1f} dB",
            f"  ─────────────────────────",
            f"  EIRP:             {eirp:+.1f} dBm",
            "",
            "PATH",
            f"  Distance:         {distance:.1f} km",
            f"  Frequency:        {freq:.0f} MHz",
            f"  Free Space Loss:  {fspl:-.1f} dB",
            f"  Additional Loss:  {extra_loss:-.1f} dB",
            f"  ─────────────────────────",
            f"  Total Path Loss:  {total_loss:-.1f} dB",
            "",
            "RECEIVER",
            f"  RX Antenna Gain:  {rx_gain:+.1f} dBi",
            f"  RX Cable Loss:    {rx_loss:-.1f} dB",
            f"  ─────────────────────────",
            f"  Received Signal:  {rx_signal:+.1f} dBm",
            f"  RX Sensitivity:   {sensitivity:.1f} dBm",
            "",
            "═══════════════════════════════════════════",
            f"  LINK MARGIN:      {margin:+.1f} dB  {status_icon}",
            "═══════════════════════════════════════════",
            "",
            f"Status: {status}",
        ]

        buffer = self.lb_output.get_buffer()
        buffer.set_text("\n".join(lines))


# Plugin entry point
def create_plugin(context: PluginContext) -> AREDNAdvancedPlugin:
    """Create plugin instance"""
    return AREDNAdvancedPlugin(context)


def get_panel(plugin: AREDNAdvancedPlugin) -> Optional[Gtk.Widget]:
    """Get plugin panel widget"""
    return plugin.get_panel()
