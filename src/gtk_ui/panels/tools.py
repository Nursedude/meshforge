"""
Tools Panel - Network, RF, and MUDP tools for GTK4 interface
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

import json
import math
import os
import shlex
import shutil
import socket
import struct
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class ToolsPanel(Gtk.Box):
    """Tools panel for network, RF, and MUDP utilities"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        GLib.idle_add(self._refresh_status)

    def _build_ui(self):
        """Build the tools panel UI"""
        # Title
        title = Gtk.Label(label="System Tools")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Network, RF, and MUDP utilities")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        self.append(subtitle)

        # Scrolled container for tools
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # System Monitor Section
        sys_frame = Gtk.Frame()
        sys_frame.set_label("System Monitor")
        sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sys_box.set_margin_start(15)
        sys_box.set_margin_end(15)
        sys_box.set_margin_top(10)
        sys_box.set_margin_bottom(10)

        # System stats grid
        sys_grid = Gtk.Grid()
        sys_grid.set_row_spacing(5)
        sys_grid.set_column_spacing(15)

        # CPU usage
        cpu_lbl = Gtk.Label(label="CPU:")
        cpu_lbl.set_xalign(1)
        sys_grid.attach(cpu_lbl, 0, 0, 1, 1)
        self.cpu_label = Gtk.Label(label="--")
        self.cpu_label.set_xalign(0)
        sys_grid.attach(self.cpu_label, 1, 0, 1, 1)
        self.cpu_bar = Gtk.ProgressBar()
        self.cpu_bar.set_hexpand(True)
        sys_grid.attach(self.cpu_bar, 2, 0, 1, 1)

        # Memory usage
        mem_lbl = Gtk.Label(label="Memory:")
        mem_lbl.set_xalign(1)
        sys_grid.attach(mem_lbl, 0, 1, 1, 1)
        self.mem_label = Gtk.Label(label="--")
        self.mem_label.set_xalign(0)
        sys_grid.attach(self.mem_label, 1, 1, 1, 1)
        self.mem_bar = Gtk.ProgressBar()
        self.mem_bar.set_hexpand(True)
        sys_grid.attach(self.mem_bar, 2, 1, 1, 1)

        # Disk usage
        disk_lbl = Gtk.Label(label="Disk:")
        disk_lbl.set_xalign(1)
        sys_grid.attach(disk_lbl, 0, 2, 1, 1)
        self.disk_label = Gtk.Label(label="--")
        self.disk_label.set_xalign(0)
        sys_grid.attach(self.disk_label, 1, 2, 1, 1)
        self.disk_bar = Gtk.ProgressBar()
        self.disk_bar.set_hexpand(True)
        sys_grid.attach(self.disk_bar, 2, 2, 1, 1)

        # Temperature
        temp_lbl = Gtk.Label(label="CPU Temp:")
        temp_lbl.set_xalign(1)
        sys_grid.attach(temp_lbl, 0, 3, 1, 1)
        self.temp_label = Gtk.Label(label="--")
        self.temp_label.set_xalign(0)
        sys_grid.attach(self.temp_label, 1, 3, 1, 1)
        self.temp_bar = Gtk.ProgressBar()
        self.temp_bar.set_hexpand(True)
        sys_grid.attach(self.temp_bar, 2, 3, 1, 1)

        # Uptime
        uptime_lbl = Gtk.Label(label="Uptime:")
        uptime_lbl.set_xalign(1)
        sys_grid.attach(uptime_lbl, 0, 4, 1, 1)
        self.uptime_label = Gtk.Label(label="--")
        self.uptime_label.set_xalign(0)
        sys_grid.attach(self.uptime_label, 1, 4, 2, 1)

        sys_box.append(sys_grid)

        # System monitor buttons
        sys_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sys_buttons.set_margin_top(10)

        htop_btn = Gtk.Button(label="Open htop")
        htop_btn.connect("clicked", self._on_open_htop)
        sys_buttons.append(htop_btn)

        top_btn = Gtk.Button(label="Show Processes")
        top_btn.connect("clicked", self._on_show_processes)
        sys_buttons.append(top_btn)

        sys_box.append(sys_buttons)
        sys_frame.set_child(sys_box)
        content.append(sys_frame)

        # Start system monitor update timer
        GLib.timeout_add_seconds(2, self._update_system_stats)

        # Network Tools Section
        net_frame = Gtk.Frame()
        net_frame.set_label("Network Tools")
        net_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        net_box.set_margin_start(15)
        net_box.set_margin_end(15)
        net_box.set_margin_top(10)
        net_box.set_margin_bottom(10)

        # Network status
        net_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        net_status.append(Gtk.Label(label="Local IP:"))
        self.local_ip_label = Gtk.Label(label="--")
        net_status.append(self.local_ip_label)
        net_status.append(Gtk.Label(label="   Port 4403:"))
        self.port_status_label = Gtk.Label(label="--")
        net_status.append(self.port_status_label)
        net_box.append(net_status)

        # Network action buttons
        net_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        net_buttons.set_margin_top(10)

        ping_btn = Gtk.Button(label="Ping Test")
        ping_btn.connect("clicked", self._on_ping_test)
        net_buttons.append(ping_btn)

        port_btn = Gtk.Button(label="TCP Port Test")
        port_btn.connect("clicked", self._on_port_test)
        net_buttons.append(port_btn)

        scan_btn = Gtk.Button(label="Find Meshtastic Devices")
        scan_btn.connect("clicked", self._on_scan_devices)
        net_buttons.append(scan_btn)

        net_box.append(net_buttons)
        net_frame.set_child(net_box)
        content.append(net_frame)

        # Network Diagnostics Section
        diag_frame = Gtk.Frame()
        diag_frame.set_label("Network Diagnostics")
        diag_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        diag_box.set_margin_start(15)
        diag_box.set_margin_end(15)
        diag_box.set_margin_top(10)
        diag_box.set_margin_bottom(10)

        diag_desc = Gtk.Label(label="Port listeners, multicast groups, and process mapping")
        diag_desc.add_css_class("dim-label")
        diag_desc.set_xalign(0)
        diag_box.append(diag_desc)

        # Diagnostic buttons row 1
        diag_buttons1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        diag_buttons1.set_margin_top(5)

        udp_btn = Gtk.Button(label="UDP Listeners")
        udp_btn.set_tooltip_text("Show UDP ports in use (/proc/net/udp)")
        udp_btn.connect("clicked", self._on_show_udp_listeners)
        diag_buttons1.append(udp_btn)

        tcp_btn = Gtk.Button(label="TCP Listeners")
        tcp_btn.set_tooltip_text("Show TCP ports in use (/proc/net/tcp)")
        tcp_btn.connect("clicked", self._on_show_tcp_listeners)
        diag_buttons1.append(tcp_btn)

        mcast_btn = Gtk.Button(label="Multicast Groups")
        mcast_btn.set_tooltip_text("Show multicast group memberships")
        mcast_btn.connect("clicked", self._on_show_multicast)
        diag_buttons1.append(mcast_btn)

        proc_btn = Gtk.Button(label="Process→Port Map")
        proc_btn.set_tooltip_text("Show which processes own which ports")
        proc_btn.connect("clicked", self._on_show_process_ports)
        diag_buttons1.append(proc_btn)

        diag_box.append(diag_buttons1)

        # Diagnostic buttons row 2 - RNS specific
        diag_buttons2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        rns_port_btn = Gtk.Button(label="Check RNS Ports")
        rns_port_btn.set_tooltip_text("Check RNS AutoInterface port 29716")
        rns_port_btn.add_css_class("suggested-action")
        rns_port_btn.connect("clicked", self._on_check_rns_ports)
        diag_buttons2.append(rns_port_btn)

        mesh_port_btn = Gtk.Button(label="Check Meshtastic Ports")
        mesh_port_btn.set_tooltip_text("Check meshtasticd ports 4403, 9443")
        mesh_port_btn.connect("clicked", self._on_check_mesh_ports)
        diag_buttons2.append(mesh_port_btn)

        all_diag_btn = Gtk.Button(label="Full Diagnostics")
        all_diag_btn.set_tooltip_text("Run all network diagnostics")
        all_diag_btn.connect("clicked", self._on_full_network_diagnostics)
        diag_buttons2.append(all_diag_btn)

        diag_box.append(diag_buttons2)

        # Diagnostic buttons row 3 - Quick actions
        diag_buttons3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        watch_btn = Gtk.Button(label="Watch API Connections")
        watch_btn.set_tooltip_text("Live view of connections to meshtasticd (port 4403)")
        watch_btn.connect("clicked", self._on_watch_api_connections)
        diag_buttons3.append(watch_btn)

        kill_clients_btn = Gtk.Button(label="Kill Competing Clients")
        kill_clients_btn.set_tooltip_text("Stop nomadnet/python meshtastic clients")
        kill_clients_btn.add_css_class("destructive-action")
        kill_clients_btn.connect("clicked", self._on_kill_competing_clients)
        diag_buttons3.append(kill_clients_btn)

        stop_rns_btn = Gtk.Button(label="Stop All RNS")
        stop_rns_btn.set_tooltip_text("Kill rnsd, nomadnet, lxmf processes")
        stop_rns_btn.add_css_class("destructive-action")
        stop_rns_btn.connect("clicked", self._on_stop_all_rns)
        diag_buttons3.append(stop_rns_btn)

        diag_box.append(diag_buttons3)
        diag_frame.set_child(diag_box)
        content.append(diag_frame)

        # RF Tools Section
        rf_frame = Gtk.Frame()
        rf_frame.set_label("RF Tools")
        rf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        rf_box.set_margin_start(15)
        rf_box.set_margin_end(15)
        rf_box.set_margin_top(10)
        rf_box.set_margin_bottom(10)

        rf_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Site Planner button - prominent position
        planner_btn = Gtk.Button(label="Site Planner")
        planner_btn.add_css_class("suggested-action")
        planner_btn.connect("clicked", self._on_site_planner)
        rf_buttons.append(planner_btn)

        # External Line of Sight tool (backup)
        los_btn = Gtk.Button(label="External LOS Tool")
        los_btn.set_tooltip_text("Open ScadaCore RF LOS tool in browser")
        los_btn.connect("clicked", self._on_line_of_sight)
        rf_buttons.append(los_btn)

        link_btn = Gtk.Button(label="Link Budget Calculator")
        link_btn.connect("clicked", self._on_link_budget)
        rf_buttons.append(link_btn)

        preset_btn = Gtk.Button(label="LoRa Preset Comparison")
        preset_btn.connect("clicked", self._on_preset_compare)
        rf_buttons.append(preset_btn)

        detect_btn = Gtk.Button(label="Detect Radio Hardware")
        detect_btn.connect("clicked", self._on_detect_radio)
        rf_buttons.append(detect_btn)

        rf_box.append(rf_buttons)

        # Line of Sight Calculator - integrated tool
        los_frame = Gtk.Frame()
        los_frame.set_label("RF Line of Sight Calculator")
        los_frame.set_margin_top(10)
        los_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        los_inner.set_margin_start(10)
        los_inner.set_margin_end(10)
        los_inner.set_margin_top(8)
        los_inner.set_margin_bottom(8)

        # Location presets and history
        self._load_los_locations()

        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preset_box.append(Gtk.Label(label="Presets:"))

        # Build location list for dropdown
        location_names = ["-- Select Location --"] + [loc["name"] for loc in self.los_locations]
        self.los_preset_dropdown = Gtk.DropDown.new_from_strings(location_names)
        self.los_preset_dropdown.set_selected(0)
        self.los_preset_dropdown.connect("notify::selected", self._on_los_preset_selected)
        preset_box.append(self.los_preset_dropdown)

        # Point selector (A or B)
        preset_box.append(Gtk.Label(label="to:"))
        self.los_point_selector = Gtk.DropDown.new_from_strings(["Point A", "Point B"])
        self.los_point_selector.set_selected(0)
        preset_box.append(self.los_point_selector)

        # Save current location button
        save_loc_btn = Gtk.Button(label="Save Location")
        save_loc_btn.set_tooltip_text("Save current Point A as a named location")
        save_loc_btn.connect("clicked", self._on_save_los_location)
        preset_box.append(save_loc_btn)

        los_inner.append(preset_box)

        # Point A
        point_a_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        point_a_box.append(Gtk.Label(label="Point A:"))
        self.los_lat_a = Gtk.Entry()
        self.los_lat_a.set_placeholder_text("Latitude")
        self.los_lat_a.set_width_chars(12)
        point_a_box.append(self.los_lat_a)
        self.los_lon_a = Gtk.Entry()
        self.los_lon_a.set_placeholder_text("Longitude")
        self.los_lon_a.set_width_chars(12)
        point_a_box.append(self.los_lon_a)
        point_a_box.append(Gtk.Label(label="Height (m):"))
        self.los_height_a = Gtk.SpinButton()
        self.los_height_a.set_range(0, 500)
        self.los_height_a.set_value(2)
        self.los_height_a.set_increments(1, 10)
        point_a_box.append(self.los_height_a)
        los_inner.append(point_a_box)

        # Point B
        point_b_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        point_b_box.append(Gtk.Label(label="Point B:"))
        self.los_lat_b = Gtk.Entry()
        self.los_lat_b.set_placeholder_text("Latitude")
        self.los_lat_b.set_width_chars(12)
        point_b_box.append(self.los_lat_b)
        self.los_lon_b = Gtk.Entry()
        self.los_lon_b.set_placeholder_text("Longitude")
        self.los_lon_b.set_width_chars(12)
        point_b_box.append(self.los_lon_b)
        point_b_box.append(Gtk.Label(label="Height (m):"))
        self.los_height_b = Gtk.SpinButton()
        self.los_height_b.set_range(0, 500)
        self.los_height_b.set_value(2)
        self.los_height_b.set_increments(1, 10)
        point_b_box.append(self.los_height_b)
        los_inner.append(point_b_box)

        # Frequency and Calculate
        calc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        calc_box.append(Gtk.Label(label="Frequency (MHz):"))
        self.los_freq = Gtk.DropDown.new_from_strings([
            "915 (US)", "868 (EU)", "433 (EU/Asia)", "923 (AU/NZ)"
        ])
        self.los_freq.set_selected(0)
        calc_box.append(self.los_freq)

        los_calc_btn = Gtk.Button(label="Calculate LOS")
        los_calc_btn.add_css_class("suggested-action")
        los_calc_btn.connect("clicked", self._on_calculate_los)
        calc_box.append(los_calc_btn)

        los_viz_btn = Gtk.Button(label="Visualize")
        los_viz_btn.set_tooltip_text("Open visualization in browser")
        los_viz_btn.connect("clicked", self._on_visualize_los)
        calc_box.append(los_viz_btn)

        los_inner.append(calc_box)

        # Results area
        self.los_results = Gtk.Label(label="Enter coordinates and click Calculate")
        self.los_results.set_xalign(0)
        self.los_results.set_wrap(True)
        self.los_results.add_css_class("dim-label")
        los_inner.append(self.los_results)

        los_frame.set_child(los_inner)
        rf_box.append(los_frame)

        rf_frame.set_child(rf_box)
        content.append(rf_frame)

        # VOACAP HF Propagation Section
        voacap_frame = Gtk.Frame()
        voacap_frame.set_label("HF Propagation (VOACAP)")
        voacap_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        voacap_box.set_margin_start(15)
        voacap_box.set_margin_end(15)
        voacap_box.set_margin_top(10)
        voacap_box.set_margin_bottom(10)

        voacap_desc = Gtk.Label(label="HF propagation prediction tools for amateur radio")
        voacap_desc.add_css_class("dim-label")
        voacap_desc.set_xalign(0)
        voacap_box.append(voacap_desc)

        voacap_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        voacap_buttons.set_margin_top(5)

        voacap_online_btn = Gtk.Button(label="VOACAP Online")
        voacap_online_btn.set_tooltip_text("HF propagation prediction - coverage maps, point-to-point")
        voacap_online_btn.add_css_class("suggested-action")
        voacap_online_btn.connect("clicked", self._on_voacap_online)
        voacap_buttons.append(voacap_online_btn)

        hfprop_btn = Gtk.Button(label="HF Prop Conditions")
        hfprop_btn.set_tooltip_text("Current HF band conditions and solar data")
        hfprop_btn.connect("clicked", self._on_hf_conditions)
        voacap_buttons.append(hfprop_btn)

        psk_btn = Gtk.Button(label="PSK Reporter")
        psk_btn.set_tooltip_text("Real-time digital mode reception reports")
        psk_btn.connect("clicked", self._on_psk_reporter)
        voacap_buttons.append(psk_btn)

        dxmaps_btn = Gtk.Button(label="DX Maps")
        dxmaps_btn.set_tooltip_text("VHF/UHF propagation maps and DX clusters")
        dxmaps_btn.connect("clicked", self._on_dx_maps)
        voacap_buttons.append(dxmaps_btn)

        voacap_box.append(voacap_buttons)

        # Second row - solar data and contests
        voacap_buttons2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        solar_btn = Gtk.Button(label="Solar Data")
        solar_btn.set_tooltip_text("Solar flux, K-index, A-index from NOAA")
        solar_btn.connect("clicked", self._on_solar_data)
        voacap_buttons2.append(solar_btn)

        hamqsl_btn = Gtk.Button(label="HamQSL Prop")
        hamqsl_btn.set_tooltip_text("Band conditions widget and propagation forecast")
        hamqsl_btn.connect("clicked", self._on_hamqsl)
        voacap_buttons2.append(hamqsl_btn)

        contest_btn = Gtk.Button(label="Contest Calendar")
        contest_btn.set_tooltip_text("Upcoming amateur radio contests")
        contest_btn.connect("clicked", self._on_contest_calendar)
        voacap_buttons2.append(contest_btn)

        voacap_box.append(voacap_buttons2)

        voacap_frame.set_child(voacap_box)
        content.append(voacap_frame)

        # MUDP Tools Section
        mudp_frame = Gtk.Frame()
        mudp_frame.set_label("MUDP Tools (Meshtastic UDP)")
        mudp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        mudp_box.set_margin_start(15)
        mudp_box.set_margin_end(15)
        mudp_box.set_margin_top(10)
        mudp_box.set_margin_bottom(10)

        # MUDP status
        mudp_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mudp_status.append(Gtk.Label(label="MUDP Package:"))
        self.mudp_status_label = Gtk.Label(label="--")
        mudp_status.append(self.mudp_status_label)
        mudp_box.append(mudp_status)

        mudp_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mudp_buttons.set_margin_top(10)

        install_mudp_btn = Gtk.Button(label="Install/Update MUDP")
        install_mudp_btn.connect("clicked", self._on_install_mudp)
        mudp_buttons.append(install_mudp_btn)

        multicast_btn = Gtk.Button(label="Test Multicast")
        multicast_btn.connect("clicked", self._on_test_multicast)
        mudp_buttons.append(multicast_btn)

        mudp_box.append(mudp_buttons)
        mudp_frame.set_child(mudp_box)
        content.append(mudp_frame)

        # Config Editor Section
        cfg_frame = Gtk.Frame()
        cfg_frame.set_label("Configuration Files (nano)")
        cfg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        cfg_box.set_margin_start(15)
        cfg_box.set_margin_end(15)
        cfg_box.set_margin_top(10)
        cfg_box.set_margin_bottom(10)

        cfg_desc = Gtk.Label(label="Edit configuration files in terminal with nano editor")
        cfg_desc.add_css_class("dim-label")
        cfg_desc.set_xalign(0)
        cfg_box.append(cfg_desc)

        # Row 1: Meshtastic configs
        mesh_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mesh_row.set_margin_top(5)

        mesh_lbl = Gtk.Label(label="Meshtastic:")
        mesh_lbl.set_width_chars(12)
        mesh_lbl.set_xalign(0)
        mesh_row.append(mesh_lbl)

        mesh_cfg_btn = Gtk.Button(label="config.yaml")
        mesh_cfg_btn.set_tooltip_text("/etc/meshtasticd/config.yaml")
        mesh_cfg_btn.connect("clicked", lambda b: self._edit_config("/etc/meshtasticd/config.yaml"))
        mesh_row.append(mesh_cfg_btn)

        mesh_avail_btn = Gtk.Button(label="available.d/")
        mesh_avail_btn.set_tooltip_text("Browse /etc/meshtasticd/available.d/")
        mesh_avail_btn.connect("clicked", lambda b: self._browse_config_dir("/etc/meshtasticd/available.d"))
        mesh_row.append(mesh_avail_btn)

        mesh_active_btn = Gtk.Button(label="config.d/")
        mesh_active_btn.set_tooltip_text("Browse /etc/meshtasticd/config.d/")
        mesh_active_btn.connect("clicked", lambda b: self._browse_config_dir("/etc/meshtasticd/config.d"))
        mesh_row.append(mesh_active_btn)

        cfg_box.append(mesh_row)

        # Row 2: RNS configs
        rns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        rns_lbl = Gtk.Label(label="Reticulum:")
        rns_lbl.set_width_chars(12)
        rns_lbl.set_xalign(0)
        rns_row.append(rns_lbl)

        rns_cfg_btn = Gtk.Button(label="RNS config")
        rns_cfg_btn.set_tooltip_text("~/.reticulum/config")
        rns_cfg_btn.connect("clicked", lambda b: self._edit_config_user(".reticulum/config"))
        rns_row.append(rns_cfg_btn)

        nomad_cfg_btn = Gtk.Button(label="NomadNet config")
        nomad_cfg_btn.set_tooltip_text("~/.nomadnetwork/config")
        nomad_cfg_btn.connect("clicked", lambda b: self._edit_config_user(".nomadnetwork/config"))
        rns_row.append(nomad_cfg_btn)

        rns_iface_btn = Gtk.Button(label="interfaces/")
        rns_iface_btn.set_tooltip_text("~/.reticulum/interfaces/")
        rns_iface_btn.connect("clicked", lambda b: self._browse_config_dir_user(".reticulum/interfaces"))
        rns_row.append(rns_iface_btn)

        cfg_box.append(rns_row)

        # Row 3: MeshForge and system configs
        app_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        app_lbl = Gtk.Label(label="MeshForge:")
        app_lbl.set_width_chars(12)
        app_lbl.set_xalign(0)
        app_row.append(app_lbl)

        mf_cfg_btn = Gtk.Button(label="settings.json")
        mf_cfg_btn.set_tooltip_text("~/.config/meshforge/settings.json")
        mf_cfg_btn.connect("clicked", lambda b: self._edit_config_user(".config/meshforge/settings.json"))
        app_row.append(mf_cfg_btn)

        hosts_btn = Gtk.Button(label="/etc/hosts")
        hosts_btn.connect("clicked", lambda b: self._edit_config("/etc/hosts"))
        app_row.append(hosts_btn)

        fstab_btn = Gtk.Button(label="/etc/fstab")
        fstab_btn.connect("clicked", lambda b: self._edit_config("/etc/fstab"))
        app_row.append(fstab_btn)

        cfg_box.append(app_row)

        cfg_frame.set_child(cfg_box)
        content.append(cfg_frame)

        # SDR / OpenWebRX Section
        sdr_frame = Gtk.Frame()
        sdr_frame.set_label("SDR / Software Defined Radio")
        sdr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sdr_box.set_margin_start(15)
        sdr_box.set_margin_end(15)
        sdr_box.set_margin_top(10)
        sdr_box.set_margin_bottom(10)

        # SDR status row
        sdr_status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sdr_status_row.append(Gtk.Label(label="OpenWebRX:"))
        self.openwebrx_status = Gtk.Label(label="--")
        sdr_status_row.append(self.openwebrx_status)
        sdr_status_row.append(Gtk.Label(label="   RTL-SDR:"))
        self.rtlsdr_status = Gtk.Label(label="--")
        sdr_status_row.append(self.rtlsdr_status)
        sdr_box.append(sdr_status_row)

        # SDR buttons row
        sdr_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sdr_buttons.set_margin_top(5)

        openwebrx_btn = Gtk.Button(label="Open WebRX")
        openwebrx_btn.add_css_class("suggested-action")
        openwebrx_btn.set_tooltip_text("Open OpenWebRX in browser (port 8073)")
        openwebrx_btn.connect("clicked", self._on_open_webrx)
        sdr_buttons.append(openwebrx_btn)

        sdr_install_btn = Gtk.Button(label="Install OpenWebRX")
        sdr_install_btn.connect("clicked", self._on_install_openwebrx)
        sdr_buttons.append(sdr_install_btn)

        rtl_test_btn = Gtk.Button(label="RTL-SDR Test")
        rtl_test_btn.set_tooltip_text("Test RTL-SDR device detection")
        rtl_test_btn.connect("clicked", self._on_rtl_test)
        sdr_buttons.append(rtl_test_btn)

        sdr_cfg_btn = Gtk.Button(label="SDR Config")
        sdr_cfg_btn.set_tooltip_text("Edit OpenWebRX config")
        sdr_cfg_btn.connect("clicked", self._on_sdr_config)
        sdr_buttons.append(sdr_cfg_btn)

        sdr_box.append(sdr_buttons)

        # SDR tools row
        sdr_tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        gqrx_btn = Gtk.Button(label="GQRX")
        gqrx_btn.set_tooltip_text("Launch GQRX SDR receiver")
        gqrx_btn.connect("clicked", self._on_launch_gqrx)
        sdr_tools.append(gqrx_btn)

        cubicsdr_btn = Gtk.Button(label="CubicSDR")
        cubicsdr_btn.set_tooltip_text("Launch CubicSDR")
        cubicsdr_btn.connect("clicked", self._on_launch_cubicsdr)
        sdr_tools.append(cubicsdr_btn)

        spectrum_btn = Gtk.Button(label="Spectrum Scan")
        spectrum_btn.set_tooltip_text("Run RTL-SDR power scan")
        spectrum_btn.connect("clicked", self._on_spectrum_scan)
        sdr_tools.append(spectrum_btn)

        sdr_box.append(sdr_tools)
        sdr_frame.set_child(sdr_box)
        content.append(sdr_frame)

        # Tool Manager Section
        mgr_frame = Gtk.Frame()
        mgr_frame.set_label("Tool Manager")
        mgr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        mgr_box.set_margin_start(15)
        mgr_box.set_margin_end(15)
        mgr_box.set_margin_top(10)
        mgr_box.set_margin_bottom(10)

        mgr_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        check_btn = Gtk.Button(label="Check for Updates")
        check_btn.connect("clicked", self._on_check_updates)
        mgr_buttons.append(check_btn)

        install_all_btn = Gtk.Button(label="Install All Tools")
        install_all_btn.connect("clicked", self._on_install_all)
        mgr_buttons.append(install_all_btn)

        mgr_box.append(mgr_buttons)
        mgr_frame.set_child(mgr_box)
        content.append(mgr_frame)

        # Output log
        log_frame = Gtk.Frame()
        log_frame.set_label("Output")
        log_frame.set_vexpand(True)

        self.output_view = Gtk.TextView()
        self.output_view.set_editable(False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.output_buffer = self.output_view.get_buffer()

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(200)
        log_scroll.set_child(self.output_view)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        log_box.set_margin_start(10)
        log_box.set_margin_end(10)
        log_box.set_margin_top(10)
        log_box.set_margin_bottom(10)
        log_box.append(log_scroll)

        clear_btn = Gtk.Button(label="Clear Log")
        clear_btn.connect("clicked", lambda b: self.output_buffer.set_text(""))
        log_box.append(clear_btn)

        log_frame.set_child(log_box)
        content.append(log_frame)

        scrolled.set_child(content)
        self.append(scrolled)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh Status")
        refresh_btn.connect("clicked", lambda b: self._refresh_status())
        self.append(refresh_btn)

    def _log(self, message):
        """Add message to log"""
        end_iter = self.output_buffer.get_end_iter()
        self.output_buffer.insert(end_iter, message + "\n")

    def _update_system_stats(self):
        """Update system statistics"""
        threading.Thread(target=self._fetch_system_stats, daemon=True).start()
        return True  # Continue timer

    def _fetch_system_stats(self):
        """Fetch system stats in background"""

        # CPU usage
        try:
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            cpu_vals = [int(x) for x in line.split()[1:8]]
            idle = cpu_vals[3]
            total = sum(cpu_vals)
            if hasattr(self, '_last_cpu'):
                diff_idle = idle - self._last_cpu[0]
                diff_total = total - self._last_cpu[1]
                cpu_pct = 100 * (1 - diff_idle / diff_total) if diff_total > 0 else 0
            else:
                cpu_pct = 0
            self._last_cpu = (idle, total)
            GLib.idle_add(self.cpu_label.set_label, f"{cpu_pct:.1f}%")
            GLib.idle_add(self.cpu_bar.set_fraction, cpu_pct / 100)
        except Exception:
            pass

        # Memory usage
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_info = {}
            for line in lines:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].split()[0])  # kB
                    mem_info[key] = val
            total = mem_info.get('MemTotal', 1)
            avail = mem_info.get('MemAvailable', mem_info.get('MemFree', 0))
            used = total - avail
            mem_pct = 100 * used / total if total > 0 else 0
            used_mb = used / 1024
            total_mb = total / 1024
            GLib.idle_add(self.mem_label.set_label, f"{used_mb:.0f}/{total_mb:.0f} MB")
            GLib.idle_add(self.mem_bar.set_fraction, mem_pct / 100)
        except Exception:
            pass

        # Disk usage
        try:
            stat = os.statvfs('/')
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            used = total - free
            disk_pct = 100 * used / total if total > 0 else 0
            used_gb = used / (1024**3)
            total_gb = total / (1024**3)
            GLib.idle_add(self.disk_label.set_label, f"{used_gb:.1f}/{total_gb:.1f} GB")
            GLib.idle_add(self.disk_bar.set_fraction, disk_pct / 100)
        except Exception:
            pass

        # Temperature
        try:
            temp = None
            # Try thermal zone first
            temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
            if temp_file.exists():
                temp = int(temp_file.read_text().strip()) / 1000
            # Try vcgencmd (Raspberry Pi)
            if temp is None:
                result = subprocess.run(['vcgencmd', 'measure_temp'],
                                       capture_output=True, text=True)
                if result.returncode == 0:
                    # Format: temp=45.0'C
                    match = result.stdout.strip()
                    if 'temp=' in match:
                        temp = float(match.split('=')[1].replace("'C", ""))
            if temp is not None:
                temp_pct = min(temp / 85, 1.0)  # 85°C as max
                GLib.idle_add(self.temp_label.set_label, f"{temp:.1f}°C")
                GLib.idle_add(self.temp_bar.set_fraction, temp_pct)
        except Exception:
            pass

        # Uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_sec = float(f.read().split()[0])
            days = int(uptime_sec // 86400)
            hours = int((uptime_sec % 86400) // 3600)
            mins = int((uptime_sec % 3600) // 60)
            if days > 0:
                uptime_str = f"{days}d {hours}h {mins}m"
            elif hours > 0:
                uptime_str = f"{hours}h {mins}m"
            else:
                uptime_str = f"{mins}m"
            GLib.idle_add(self.uptime_label.set_label, uptime_str)
        except Exception:
            pass

    def _on_open_htop(self, button):
        """Open htop in a terminal"""
        threading.Thread(target=self._run_htop, daemon=True).start()

    def _run_htop(self):
        """Run htop in terminal"""
        terminals = [
            ['x-terminal-emulator', '-e', 'htop'],
            ['gnome-terminal', '--', 'htop'],
            ['xfce4-terminal', '-e', 'htop'],
            ['lxterminal', '-e', 'htop'],
            ['xterm', '-e', 'htop'],
        ]
        for term in terminals:
            try:
                subprocess.Popen(term, start_new_session=True)
                GLib.idle_add(self._log, "htop opened in terminal")
                return
            except FileNotFoundError:
                continue
        GLib.idle_add(self._log, "No terminal emulator found. Install htop and run manually.")

    def _on_show_processes(self, button):
        """Show top processes"""
        GLib.idle_add(self._log, "\n=== Top Processes (by CPU) ===")
        threading.Thread(target=self._fetch_processes, daemon=True).start()

    def _fetch_processes(self):
        """Fetch process list"""
        try:
            result = subprocess.run(
                ['ps', 'aux', '--sort=-%cpu'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')[:11]  # Header + 10 processes
                for line in lines:
                    GLib.idle_add(self._log, line[:100])
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _refresh_status(self):
        """Refresh tool status"""
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()

    def _refresh_status_thread(self):
        """Background thread for status refresh"""
        # Get local IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            GLib.idle_add(self.local_ip_label.set_text, local_ip)
        except Exception:
            GLib.idle_add(self.local_ip_label.set_text, "Unknown")

        # Check port 4403
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 4403))
            sock.close()
            status = "OPEN" if result == 0 else "CLOSED"
            GLib.idle_add(self.port_status_label.set_text, status)
        except Exception:
            GLib.idle_add(self.port_status_label.set_text, "Error")

        # Check MUDP
        try:
            result = subprocess.run(['pip', 'show', 'mudp'], capture_output=True, timeout=10)
            status = "Installed" if result.returncode == 0 else "Not Installed"
            GLib.idle_add(self.mudp_status_label.set_text, status)
        except Exception:
            GLib.idle_add(self.mudp_status_label.set_text, "Unknown")

        # Check SDR status
        self._check_sdr_status()

    def _on_ping_test(self, button):
        """Run ping test"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="Ping Test",
            body="Enter hostname or IP address:"
        )

        entry = Gtk.Entry()
        entry.set_text("8.8.8.8")
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ping", "Ping")
        dialog.set_response_appearance("ping", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "ping":
                host = entry.get_text()
                threading.Thread(target=self._run_ping, args=(host,), daemon=True).start()
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_ping(self, host):
        """Run ping in background"""
        GLib.idle_add(self._log, f"Pinging {host}...")
        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=30
            )
            GLib.idle_add(self._log, result.stdout)
            if result.returncode != 0:
                GLib.idle_add(self._log, result.stderr)
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_port_test(self, button):
        """Run TCP port test"""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="TCP Port Test",
            body="Enter host:port (e.g., localhost:4403)"
        )

        entry = Gtk.Entry()
        entry.set_text("localhost:4403")
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("test", "Test")
        dialog.set_response_appearance("test", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "test":
                addr = entry.get_text()
                parts = addr.split(':')
                host = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 4403
                threading.Thread(target=self._run_port_test, args=(host, port), daemon=True).start()
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_port_test(self, host, port):
        """Run port test in background"""
        GLib.idle_add(self._log, f"Testing TCP {host}:{port}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                GLib.idle_add(self._log, f"Port {port} is OPEN on {host}")
            else:
                GLib.idle_add(self._log, f"Port {port} is CLOSED on {host}")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_scan_devices(self, button):
        """Scan for Meshtastic devices"""
        GLib.idle_add(self._log, "Scanning for Meshtastic devices (port 4403)...")
        threading.Thread(target=self._run_scan, daemon=True).start()

    def _run_scan(self):
        """Run device scan in background"""
        try:
            # Get local network
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            base = '.'.join(local_ip.split('.')[:3])
            found = []

            for i in range(1, 255):
                ip = f"{base}.{i}"
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.3)
                    result = sock.connect_ex((ip, 4403))
                    sock.close()
                    if result == 0:
                        found.append(ip)
                        GLib.idle_add(self._log, f"Found: {ip}:4403")
                except Exception:
                    pass

            if found:
                GLib.idle_add(self._log, f"\nFound {len(found)} device(s)")
            else:
                GLib.idle_add(self._log, "No Meshtastic devices found on port 4403")
        except Exception as e:
            GLib.idle_add(self._log, f"Scan error: {e}")

    def _on_site_planner(self, button):
        """Open Meshtastic Site Planner in browser"""
        self._log("\nFeatures: RF coverage (ITM model), terrain analysis, multi-node planning")
        self._open_url_in_browser("https://site.meshtastic.org/", "Meshtastic Site Planner")

    def _on_line_of_sight(self, button):
        """Open RF Line of Sight tool in browser"""
        self._log("\nFeatures: Elevation profile, Fresnel zones, Earth curvature")
        self._open_url_in_browser(
            "https://www.scadacore.com/tools/rf-path/rf-line-of-sight/",
            "RF Line of Sight Tool"
        )

    # --- VOACAP / HF Propagation Methods ---

    def _on_voacap_online(self, button):
        """Open VOACAP Online HF propagation prediction"""
        self._log("\nVOACAP Online - HF propagation prediction")
        self._log("Features: Coverage maps, point-to-point prediction, antenna patterns")
        self._open_url_in_browser(
            "https://www.voacap.com/hf/",
            "VOACAP Online"
        )

    def _on_hf_conditions(self, button):
        """Open HF propagation conditions page"""
        self._log("\nCurrent HF band conditions")
        self._log("Shows: Solar flux index, sunspot number, K/A indices")
        self._open_url_in_browser(
            "https://www.hamqsl.com/solar.html",
            "HF Propagation Conditions"
        )

    def _on_psk_reporter(self, button):
        """Open PSK Reporter for digital mode reception reports"""
        self._log("\nPSK Reporter - Real-time digital mode spots")
        self._log("Shows: FT8, JS8, WSPR, PSK31 reception reports worldwide")
        self._open_url_in_browser(
            "https://pskreporter.info/pskmap.html",
            "PSK Reporter Map"
        )

    def _on_dx_maps(self, button):
        """Open DX Maps for VHF/UHF propagation"""
        self._log("\nDX Maps - VHF/UHF propagation monitoring")
        self._log("Shows: Sporadic-E, tropospheric ducting, meteor scatter")
        self._open_url_in_browser(
            "https://www.dxmaps.com/spots/mapg.php",
            "DX Maps"
        )

    def _on_solar_data(self, button):
        """Open NOAA space weather data"""
        self._log("\nNOAA Space Weather - Solar data for HF propagation")
        self._log("Shows: Solar wind, geomagnetic activity, aurora forecast")
        self._open_url_in_browser(
            "https://www.swpc.noaa.gov/products/solar-cycle-progression",
            "NOAA Space Weather"
        )

    def _on_hamqsl(self, button):
        """Open HamQSL propagation widget page"""
        self._log("\nHamQSL Solar-Terrestrial Data")
        self._log("Shows: Band conditions, solar flux, MUF predictions")
        self._open_url_in_browser(
            "https://www.hamqsl.com/solar.html",
            "HamQSL Propagation"
        )

    def _on_contest_calendar(self, button):
        """Open amateur radio contest calendar"""
        self._log("\nAmateur Radio Contest Calendar")
        self._log("Shows: Upcoming contests, rules, exchange info")
        self._open_url_in_browser(
            "https://www.contestcalendar.com/contestcal.html",
            "Contest Calendar"
        )

    def _on_calculate_los(self, button):
        """Calculate RF Line of Sight between two points"""

        # Get coordinates
        try:
            lat_a = float(self.los_lat_a.get_text().strip())
            lon_a = float(self.los_lon_a.get_text().strip())
            lat_b = float(self.los_lat_b.get_text().strip())
            lon_b = float(self.los_lon_b.get_text().strip())
        except ValueError:
            self.los_results.set_label("Error: Enter valid coordinates (e.g., 37.7749, -122.4194)")
            return

        height_a = self.los_height_a.get_value()
        height_b = self.los_height_b.get_value()

        # Get frequency
        freq_options = [915, 868, 433, 923]
        freq_mhz = freq_options[self.los_freq.get_selected()]

        self.los_results.set_label("Calculating... fetching elevation data...")

        def calculate():
            try:
                # Calculate distance using Haversine formula
                R = 6371000  # Earth radius in meters
                lat1_rad = math.radians(lat_a)
                lat2_rad = math.radians(lat_b)
                delta_lat = math.radians(lat_b - lat_a)
                delta_lon = math.radians(lon_b - lon_a)

                a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                distance_m = R * c
                distance_km = distance_m / 1000

                # Calculate earth bulge at midpoint (for long distances)
                # h = d^2 / (8 * R) where d is distance, R is earth radius
                earth_bulge_m = (distance_m ** 2) / (8 * R * (4/3))  # 4/3 earth radius for RF refraction

                # Calculate first Fresnel zone radius at midpoint
                # r = 17.3 * sqrt(d / (4 * f)) where d is in km, f is in GHz
                freq_ghz = freq_mhz / 1000
                fresnel_radius_m = 17.3 * math.sqrt(distance_km / (4 * freq_ghz))

                # 60% Fresnel zone clearance needed for good LOS
                fresnel_60_m = fresnel_radius_m * 0.6

                # Try to get elevation data from Open-Elevation API
                elev_a = None
                elev_b = None
                elev_mid = None

                try:
                    # Query elevations for point A, B, and midpoint
                    mid_lat = (lat_a + lat_b) / 2
                    mid_lon = (lon_a + lon_b) / 2

                    url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat_a},{lon_a}|{mid_lat},{mid_lon}|{lat_b},{lon_b}"
                    req = urllib.request.Request(url, headers={'User-Agent': 'MeshForge/4.1'})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        data = json.loads(response.read().decode())
                        results = data.get('results', [])
                        if len(results) >= 3:
                            elev_a = results[0].get('elevation', 0)
                            elev_mid = results[1].get('elevation', 0)
                            elev_b = results[2].get('elevation', 0)
                except Exception as e:
                    print(f"[LOS] Elevation API error: {e}")

                # Build results
                results = []
                results.append(f"Distance: {distance_km:.2f} km ({distance_m:.0f} m)")
                results.append(f"Earth Bulge at Midpoint: {earth_bulge_m:.1f} m")
                results.append(f"1st Fresnel Zone Radius: {fresnel_radius_m:.1f} m")
                results.append(f"60% Fresnel Clearance Needed: {fresnel_60_m:.1f} m")

                if elev_a is not None:
                    results.append(f"\nElevation Data:")
                    results.append(f"  Point A: {elev_a:.0f} m + {height_a:.0f} m antenna = {elev_a + height_a:.0f} m")
                    results.append(f"  Point B: {elev_b:.0f} m + {height_b:.0f} m antenna = {elev_b + height_b:.0f} m")
                    results.append(f"  Midpoint Ground: {elev_mid:.0f} m")

                    # Simple LOS check
                    # Line from A to B at midpoint height
                    line_height_mid = ((elev_a + height_a) + (elev_b + height_b)) / 2
                    clearance = line_height_mid - elev_mid - earth_bulge_m

                    results.append(f"\nMidpoint Clearance: {clearance:.1f} m")

                    if clearance >= fresnel_60_m:
                        results.append("✓ Good LOS - 60% Fresnel zone clear")
                    elif clearance > 0:
                        results.append("⚠ Marginal LOS - some Fresnel obstruction")
                    else:
                        results.append("✗ No LOS - terrain blocks path")
                else:
                    results.append("\n(Elevation API unavailable - basic calculations only)")

                # Free Space Path Loss
                fspl = 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz) - 27.55
                results.append(f"\nFree Space Path Loss: {fspl:.1f} dB")

                GLib.idle_add(self.los_results.set_label, "\n".join(results))
                GLib.idle_add(self.los_results.remove_css_class, "dim-label")

            except Exception as e:
                GLib.idle_add(self.los_results.set_label, f"Error: {e}")

        threading.Thread(target=calculate, daemon=True).start()

    def _on_visualize_los(self, button):
        """Open LOS visualization in browser"""

        # Get coordinates
        try:
            lat_a = float(self.los_lat_a.get_text().strip())
            lon_a = float(self.los_lon_a.get_text().strip())
            lat_b = float(self.los_lat_b.get_text().strip())
            lon_b = float(self.los_lon_b.get_text().strip())
        except ValueError:
            self.los_results.set_label("Error: Enter valid coordinates first")
            return

        height_a = self.los_height_a.get_value()
        height_b = self.los_height_b.get_value()

        freq_options = [915, 868, 433, 923]
        freq_mhz = freq_options[self.los_freq.get_selected()]

        # Build URL with parameters
        viz_path = Path(__file__).parent.parent.parent.parent / "web" / "los_visualization.html"
        if not viz_path.exists():
            # Try alternate path
            viz_path = Path("/opt/meshforge/web/los_visualization.html")
        if not viz_path.exists():
            self.los_results.set_label("Error: Visualization file not found")
            return

        params = urllib.parse.urlencode({
            'latA': lat_a,
            'lonA': lon_a,
            'latB': lat_b,
            'lonB': lon_b,
            'heightA': height_a,
            'heightB': height_b,
            'freq': freq_mhz
        })

        url = f"file://{viz_path}?{params}"
        self._open_url_in_browser(url, "LOS Visualization")

    def _on_link_budget(self, button):
        """Show link budget info"""
        GLib.idle_add(self._log, "\n=== Link Budget Calculator ===")
        GLib.idle_add(self._log, "For interactive calculator, use CLI: python3 src/main.py -> r")
        GLib.idle_add(self._log, "\nQuick Reference:")
        GLib.idle_add(self._log, "  LONG_FAST: ~30km LOS, -123dBm sensitivity")
        GLib.idle_add(self._log, "  LONG_SLOW: ~80km LOS, -129dBm sensitivity")
        GLib.idle_add(self._log, "  FSPL at 10km, 915MHz: ~112dB")

    def _on_preset_compare(self, button):
        """Show preset comparison"""
        GLib.idle_add(self._log, "\n=== LoRa Preset Comparison ===")
        presets = [
            ("SHORT_TURBO", "21875 bps", "-108 dBm", "~3 km"),
            ("SHORT_FAST", "10937 bps", "-111 dBm", "~5 km"),
            ("MEDIUM_FAST", "3516 bps", "-117 dBm", "~12 km"),
            ("LONG_FAST", "1066 bps", "-123 dBm", "~30 km"),
            ("LONG_SLOW", "293 bps", "-129 dBm", "~80 km"),
            ("VERY_LONG_SLOW", "146 bps", "-132 dBm", "~120 km"),
        ]
        for name, rate, sens, range_ in presets:
            GLib.idle_add(self._log, f"  {name}: {rate}, {sens}, {range_}")

    def _on_detect_radio(self, button):
        """Detect radio hardware"""
        GLib.idle_add(self._log, "\n=== Radio Hardware Detection ===")
        threading.Thread(target=self._run_detect_radio, daemon=True).start()

    def _run_detect_radio(self):
        """Run radio detection in background"""
        # Check SPI
        spi_devs = list(Path('/dev').glob('spidev*'))
        if spi_devs:
            GLib.idle_add(self._log, f"SPI: Enabled ({len(spi_devs)} devices)")
        else:
            GLib.idle_add(self._log, "SPI: Not enabled")

        # Check I2C
        i2c_devs = list(Path('/dev').glob('i2c-*'))
        if i2c_devs:
            GLib.idle_add(self._log, f"I2C: Enabled ({len(i2c_devs)} devices)")
        else:
            GLib.idle_add(self._log, "I2C: Not enabled")

        # Check GPIO
        if Path('/sys/class/gpio').exists():
            GLib.idle_add(self._log, "GPIO: Available")
        else:
            GLib.idle_add(self._log, "GPIO: Not available")

    def _on_install_mudp(self, button):
        """Install MUDP package"""
        GLib.idle_add(self._log, "\nInstalling/Updating MUDP...")
        threading.Thread(target=self._run_install_mudp, daemon=True).start()

    def _run_install_mudp(self):
        """Run MUDP install in background"""
        try:
            result = subprocess.run(
                ['pip', 'install', '--upgrade', '--break-system-packages', 'mudp'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "MUDP installed/updated successfully")
            else:
                GLib.idle_add(self._log, f"Installation failed: {result.stderr}")
            GLib.idle_add(self._refresh_status)
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_test_multicast(self, button):
        """Test multicast join"""
        GLib.idle_add(self._log, "\n=== Multicast Test ===")
        threading.Thread(target=self._run_multicast_test, daemon=True).start()

    def _run_multicast_test(self):
        """Run multicast test in background"""
        group = "224.0.0.69"
        port = 4403

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', port))

            mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            GLib.idle_add(self._log, f"Successfully joined multicast group {group}")

            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            sock.close()
            GLib.idle_add(self._log, "Successfully left multicast group")

        except OSError as e:
            if "Address already in use" in str(e):
                GLib.idle_add(self._log, f"Port {port} in use (meshtasticd running?) - OK")
            else:
                GLib.idle_add(self._log, f"Error: {e}")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_check_updates(self, button):
        """Check for tool updates"""
        GLib.idle_add(self._log, "\n=== Checking for Updates ===")
        threading.Thread(target=self._run_check_updates, daemon=True).start()

    def _run_check_updates(self):
        """Run update check in background"""
        packages = ['mudp', 'meshtastic']
        for pkg in packages:
            try:
                result = subprocess.run(
                    ['pip', 'show', pkg],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if line.startswith('Version:'):
                            version = line.split(':', 1)[1].strip()
                            GLib.idle_add(self._log, f"{pkg}: {version}")
                            break
                else:
                    GLib.idle_add(self._log, f"{pkg}: Not installed")
            except Exception:
                GLib.idle_add(self._log, f"{pkg}: Check failed")

    def _on_install_all(self, button):
        """Install all tools"""
        GLib.idle_add(self._log, "\n=== Installing All Tools ===")
        threading.Thread(target=self._run_install_all, daemon=True).start()

    def _run_install_all(self):
        """Run install all in background"""
        # pip packages
        pip_pkgs = ['mudp']
        for pkg in pip_pkgs:
            GLib.idle_add(self._log, f"Installing {pkg}...")
            subprocess.run(
                ['pip', 'install', '--break-system-packages', pkg],
                capture_output=True, timeout=120
            )

        # apt packages
        apt_pkgs = ['nmap', 'net-tools', 'socat']
        GLib.idle_add(self._log, f"Installing apt packages: {', '.join(apt_pkgs)}")
        subprocess.run(['sudo', 'apt', 'install', '-y'] + apt_pkgs, capture_output=True)

        GLib.idle_add(self._log, "Installation complete")
        GLib.idle_add(self._refresh_status)

    # === LOS Location Management ===

    LOS_LOCATIONS_FILE = get_real_user_home() / ".config" / "meshforge" / "los_locations.json"

    # Default preset locations - major cities and Meshtastic community areas
    DEFAULT_LOCATIONS = [
        # Hawaii - Big Island
        {"name": "Hilo, HI", "lat": 19.7297, "lon": -155.0900},
        {"name": "Volcano, HI", "lat": 19.4300, "lon": -155.2900},
        {"name": "Kona, HI", "lat": 19.6400, "lon": -155.9969},
        {"name": "Waimea (Kamuela), HI", "lat": 20.0234, "lon": -155.6728},
        {"name": "Big Island - Mauna Kea", "lat": 19.8207, "lon": -155.4680},
        # Hawaii - Other Islands
        {"name": "Honolulu, HI", "lat": 21.3069, "lon": -157.8583},
        {"name": "Maui - Haleakala", "lat": 20.7097, "lon": -156.2533},
        # US West Coast
        {"name": "San Francisco, CA", "lat": 37.7749, "lon": -122.4194},
        {"name": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2437},
        {"name": "Seattle, WA", "lat": 47.6062, "lon": -122.3321},
        {"name": "Portland, OR", "lat": 45.5155, "lon": -122.6789},
        {"name": "San Diego, CA", "lat": 32.7157, "lon": -117.1611},
        {"name": "Denver, CO", "lat": 39.7392, "lon": -104.9903},
        {"name": "Phoenix, AZ", "lat": 33.4484, "lon": -112.0740},
        # US East Coast
        {"name": "New York, NY", "lat": 40.7128, "lon": -74.0060},
        {"name": "Boston, MA", "lat": 42.3601, "lon": -71.0589},
        {"name": "Miami, FL", "lat": 25.7617, "lon": -80.1918},
        {"name": "Atlanta, GA", "lat": 33.7490, "lon": -84.3880},
        {"name": "Washington, DC", "lat": 38.9072, "lon": -77.0369},
        # US Central
        {"name": "Chicago, IL", "lat": 41.8781, "lon": -87.6298},
        {"name": "Austin, TX", "lat": 30.2672, "lon": -97.7431},
        {"name": "Dallas, TX", "lat": 32.7767, "lon": -96.7970},
        # Europe
        {"name": "London, UK", "lat": 51.5074, "lon": -0.1278},
        {"name": "Berlin, DE", "lat": 52.5200, "lon": 13.4050},
        {"name": "Paris, FR", "lat": 48.8566, "lon": 2.3522},
        {"name": "Amsterdam, NL", "lat": 52.3676, "lon": 4.9041},
        # Australia/NZ
        {"name": "Sydney, AU", "lat": -33.8688, "lon": 151.2093},
        {"name": "Melbourne, AU", "lat": -37.8136, "lon": 144.9631},
        {"name": "Auckland, NZ", "lat": -36.8509, "lon": 174.7645},
    ]

    def _load_los_locations(self):
        """Load saved locations from file, merge with defaults"""
        self.los_locations = list(self.DEFAULT_LOCATIONS)
        self.los_custom_locations = []
        self.los_history = []

        try:
            if self.LOS_LOCATIONS_FILE.exists():
                with open(self.LOS_LOCATIONS_FILE) as f:
                    data = json.load(f)
                    self.los_custom_locations = data.get("custom", [])
                    self.los_history = data.get("history", [])[-10:]  # Keep last 10

                    # Add custom locations to the list
                    for loc in self.los_custom_locations:
                        self.los_locations.append(loc)

                    # Add history items (with prefix)
                    for loc in self.los_history:
                        loc_copy = loc.copy()
                        loc_copy["name"] = f"[Recent] {loc['name']}"
                        self.los_locations.append(loc_copy)
        except Exception as e:
            print(f"[LOS] Error loading locations: {e}")

    def _save_los_locations(self):
        """Save custom locations and history to file"""
        try:
            self.LOS_LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "custom": self.los_custom_locations,
                "history": self.los_history[-10:]  # Keep last 10
            }
            with open(self.LOS_LOCATIONS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[LOS] Error saving locations: {e}")

    def _on_los_preset_selected(self, dropdown, _):
        """Handle location preset selection"""
        selected = dropdown.get_selected()
        if selected == 0:  # "-- Select Location --"
            return

        # Get the location (offset by 1 for the placeholder)
        loc = self.los_locations[selected - 1]

        # Determine which point to fill
        point = self.los_point_selector.get_selected()

        if point == 0:  # Point A
            self.los_lat_a.set_text(str(loc["lat"]))
            self.los_lon_a.set_text(str(loc["lon"]))
        else:  # Point B
            self.los_lat_b.set_text(str(loc["lat"]))
            self.los_lon_b.set_text(str(loc["lon"]))

        self._log(f"Loaded {loc['name']} to Point {'A' if point == 0 else 'B'}")

    def _on_save_los_location(self, button):
        """Save current Point A as a new named location"""
        try:
            lat = float(self.los_lat_a.get_text().strip())
            lon = float(self.los_lon_a.get_text().strip())
        except ValueError:
            self.los_results.set_label("Error: Enter valid coordinates in Point A first")
            return

        # Show dialog to get name
        dialog = Gtk.MessageDialog(
            transient_for=self.main_window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Save Location"
        )
        dialog.format_secondary_text("Enter a name for this location:")

        # Add entry to dialog
        content_area = dialog.get_content_area()
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g., My Home, Repeater Site 1")
        entry.set_margin_start(20)
        entry.set_margin_end(20)
        entry.set_margin_bottom(10)
        content_area.append(entry)

        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                name = entry.get_text().strip()
                if name:
                    new_loc = {"name": name, "lat": lat, "lon": lon}
                    self.los_custom_locations.append(new_loc)
                    self.los_locations.append(new_loc)
                    self._save_los_locations()

                    # Update dropdown
                    location_names = ["-- Select Location --"] + [loc["name"] for loc in self.los_locations]
                    self.los_preset_dropdown.set_model(Gtk.StringList.new(location_names))

                    self._log(f"Saved location: {name} ({lat}, {lon})")
                else:
                    self.los_results.set_label("Error: Please enter a name")
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _add_to_los_history(self, lat, lon, name=None):
        """Add a location to history"""
        if name is None:
            name = f"{lat:.4f}, {lon:.4f}"

        # Check if already in history
        for h in self.los_history:
            if abs(h["lat"] - lat) < 0.0001 and abs(h["lon"] - lon) < 0.0001:
                return  # Already exists

        self.los_history.append({"name": name, "lat": lat, "lon": lon})
        if len(self.los_history) > 10:
            self.los_history = self.los_history[-10:]

        self._save_los_locations()

    # =====================
    # Utility Methods
    # =====================

    def _open_url_in_browser(self, url: str, description: str = ""):
        """Open URL in browser with fallback methods. Thread-safe."""
        if description:
            self._log(f"\n=== {description} ===")
        self._log(f"Opening {url}")

        def try_open():
            user = self._get_real_username()

            # Method 1: xdg-open as the real user
            try:
                result = subprocess.run(
                    ['sudo', '-u', user, 'xdg-open', url],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    GLib.idle_add(lambda: self._log("Browser opened successfully"))
                    return True
            except Exception:
                pass

            # Method 2: Try common browsers directly
            for browser in ['chromium-browser', 'firefox', 'epiphany-browser']:
                try:
                    subprocess.Popen(
                        ['sudo', '-u', user, browser, url],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    GLib.idle_add(lambda: self._log(f"Browser opened ({browser})"))
                    return True
                except Exception:
                    continue

            # Method 3: webbrowser module fallback
            try:
                webbrowser.open(url)
                GLib.idle_add(lambda: self._log("Browser opened"))
                return True
            except Exception:
                pass

            GLib.idle_add(lambda: self._log(f"Could not open browser. Visit: {url}"))
            return False

        threading.Thread(target=try_open, daemon=True).start()

    def _get_real_username(self):
        """Get the real username even when running as root via sudo"""
        return os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))

    def _get_real_user_home(self):
        """Get the real user's home directory"""
        return get_real_user_home()

    def _launch_terminal_with_command(self, command: str, description: str = "") -> bool:
        """Launch a terminal emulator with the given command. Returns True on success.

        Security: Uses argument lists instead of shell=True to prevent command injection.
        """
        # Terminal configs: (binary, args_before_command, args_for_command)
        # Some terminals use -e, others use -- to separate the command
        terminals = [
            ('lxterminal', ['-e'], False),      # lxterminal -e "command"
            ('xfce4-terminal', ['-e'], False),  # xfce4-terminal -e "command"
            ('gnome-terminal', ['--'], True),   # gnome-terminal -- command args
            ('konsole', ['-e'], True),          # konsole -e command args
            ('xterm', ['-e'], True),            # xterm -e command args
        ]

        for term_name, term_args, split_command in terminals:
            term_path = shutil.which(term_name)
            if term_path:
                try:
                    if split_command:
                        # Terminal expects command as separate arguments
                        cmd_parts = shlex.split(command)
                        full_cmd = [term_path] + term_args + cmd_parts
                    else:
                        # Terminal expects command as single string argument
                        full_cmd = [term_path] + term_args + [command]

                    subprocess.Popen(full_cmd, start_new_session=True)
                    if description:
                        self._log(f"{description} in {term_name}")
                        self.main_window.set_status_message(description)
                    return True
                except Exception as e:
                    logger.debug(f"Failed to launch {term_name}: {e}")
                    continue

        self._log("No terminal emulator found")
        self.main_window.set_status_message("No terminal emulator found")
        return False

    def _launch_desktop_app(self, app_name: str, package_hint: str = None) -> bool:
        """Launch a desktop application. Returns True on success."""
        if shutil.which(app_name):
            subprocess.Popen([app_name], start_new_session=True)
            self._log(f"Launching {app_name}...")
            self.main_window.set_status_message(f"Launching {app_name}")
            return True
        else:
            hint = f" Install with: sudo apt install {package_hint}" if package_hint else ""
            self._log(f"{app_name} not found.{hint}")
            self.main_window.set_status_message(f"{app_name} not installed")
            return False

    def _edit_config(self, config_path):
        """Open a config file in terminal with nano"""
        try:
            config_path = Path(config_path)
            if not config_path.exists():
                self._log(f"File not found: {config_path}")
                self.main_window.set_status_message(f"File not found: {config_path}")
                return

            safe_path = shlex.quote(str(config_path))
            self._launch_terminal_with_command(f"nano {safe_path}", f"Editing {config_path.name}")
        except Exception as e:
            self._log(f"Error opening config: {e}")
            self.main_window.set_status_message(f"Error: {e}")

    def _edit_config_user(self, relative_path):
        """Open a user config file (relative to home) in terminal with nano"""
        try:
            real_home = self._get_real_user_home()
            config_path = real_home / relative_path

            # Create parent directory and file if needed
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if not config_path.exists():
                config_path.touch()
                self._log(f"Created: {config_path}")

            safe_path = shlex.quote(str(config_path))
            real_user = self._get_real_username()

            # When running as root, run nano as the real user
            if os.geteuid() == 0 and real_user != 'root':
                cmd = f"sudo -i -u {shlex.quote(real_user)} nano {safe_path}"
            else:
                cmd = f"nano {safe_path}"

            self._launch_terminal_with_command(cmd, f"Editing {config_path.name}")
        except PermissionError:
            self._log("Permission denied")
            self.main_window.set_status_message("Permission denied")
        except Exception as e:
            self._log(f"Error: {e}")
            self.main_window.set_status_message(f"Error: {e}")

    def _browse_config_dir(self, dir_path):
        """Show files in a config directory and let user select one to edit"""
        try:
            dir_path = Path(dir_path)

            if not dir_path.exists():
                self._log(f"Directory not found: {dir_path}")
                self.main_window.set_status_message(f"Directory not found: {dir_path}")
                return

            files = sorted([f.name for f in dir_path.iterdir() if f.is_file()])
        except PermissionError:
            self._log(f"Permission denied: {dir_path}")
            self.main_window.set_status_message("Permission denied")
            return
        except Exception as e:
            self._log(f"Error reading directory: {e}")
            self.main_window.set_status_message(f"Error: {e}")
            return

        if not files:
            self._log(f"No files in {dir_path}")
            self.main_window.set_status_message("No config files found")
            return

        # Create a dialog to select file
        dialog = Gtk.Dialog(
            title=f"Select Config File",
            transient_for=self.main_window,
            modal=True
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Edit", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_spacing(10)

        label = Gtk.Label(label=f"Files in {dir_path}:")
        label.set_xalign(0)
        content.append(label)

        # File list
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for f in files:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=f, xalign=0))
            row.file_name = f
            listbox.append(row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(200)
        scroll.set_child(listbox)
        content.append(scroll)

        def on_response(dialog, response):
            if response == Gtk.ResponseType.OK:
                row = listbox.get_selected_row()
                if row:
                    self._edit_config(dir_path / row.file_name)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _browse_config_dir_user(self, relative_path):
        """Browse a user config directory (relative to home)"""
        real_home = self._get_real_user_home()
        dir_path = real_home / relative_path

        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            self._log(f"Created directory: {dir_path}")

        self._browse_config_dir(dir_path)

    # =====================
    # SDR / OpenWebRX Methods
    # =====================

    def _check_sdr_status(self):
        """Check SDR and OpenWebRX status"""

        # Check OpenWebRX
        if shutil.which('openwebrx'):
            try:
                result = subprocess.run(['systemctl', 'is-active', 'openwebrx'],
                                       capture_output=True, text=True)
                status = result.stdout.strip()
                if status == 'active':
                    GLib.idle_add(lambda: self.openwebrx_status.set_text("Running"))
                else:
                    GLib.idle_add(lambda: self.openwebrx_status.set_text("Installed"))
            except Exception:
                GLib.idle_add(lambda: self.openwebrx_status.set_text("Installed"))
        else:
            GLib.idle_add(lambda: self.openwebrx_status.set_text("Not installed"))

        # Check RTL-SDR
        if shutil.which('rtl_test'):
            try:
                result = subprocess.run(['rtl_test', '-t'],
                                       capture_output=True, text=True, timeout=3)
                if 'Found' in result.stderr or 'Found' in result.stdout:
                    GLib.idle_add(lambda: self.rtlsdr_status.set_text("Device found"))
                else:
                    GLib.idle_add(lambda: self.rtlsdr_status.set_text("No device"))
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self.rtlsdr_status.set_text("Device found"))
            except Exception:
                GLib.idle_add(lambda: self.rtlsdr_status.set_text("Tools installed"))
        else:
            GLib.idle_add(lambda: self.rtlsdr_status.set_text("Not installed"))

    def _on_open_webrx(self, button):
        """Open OpenWebRX in browser"""
        try:
            webbrowser.open('http://localhost:8073')
            self._log("Opening OpenWebRX at http://localhost:8073")
            self.main_window.set_status_message("Opening OpenWebRX in browser")
        except Exception as e:
            self._log(f"Failed to open browser: {e}")
            self.main_window.set_status_message(f"Failed to open browser: {e}")

    def _on_install_openwebrx(self, button):
        """Show OpenWebRX installation instructions"""
        dialog = Gtk.MessageDialog(
            transient_for=self.main_window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Install OpenWebRX"
        )
        dialog.format_secondary_text(
            "OpenWebRX Installation:\n\n"
            "Debian/Ubuntu:\n"
            "  wget -O - https://repo.openwebrx.de/install.sh | sudo bash\n"
            "  sudo apt install openwebrx\n\n"
            "Raspberry Pi OS:\n"
            "  Same as above, or use pre-built image from:\n"
            "  https://www.openwebrx.de/download/\n\n"
            "RTL-SDR drivers:\n"
            "  sudo apt install rtl-sdr librtlsdr-dev\n\n"
            "After install:\n"
            "  sudo systemctl enable openwebrx\n"
            "  sudo systemctl start openwebrx"
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()
        self._log("Showed OpenWebRX installation instructions")

    def _on_rtl_test(self, button):
        """Test RTL-SDR device"""
        self._log("Testing RTL-SDR device...")

        def run_test():
            if not shutil.which('rtl_test'):
                GLib.idle_add(lambda: self._log("rtl_test not found. Install with: sudo apt install rtl-sdr"))
                return

            try:
                result = subprocess.run(['rtl_test', '-t'],
                                       capture_output=True, text=True, timeout=5)
                output = result.stderr + result.stdout
                GLib.idle_add(lambda: self._log(f"RTL-SDR Test:\n{output[:500]}"))

                if 'Found' in output:
                    GLib.idle_add(lambda: self.rtlsdr_status.set_text("Device found"))
                else:
                    GLib.idle_add(lambda: self.rtlsdr_status.set_text("No device"))
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self._log("RTL-SDR device found (test timed out - normal)"))
                GLib.idle_add(lambda: self.rtlsdr_status.set_text("Device found"))
            except Exception as e:
                err = str(e)
                GLib.idle_add(lambda: self._log(f"RTL-SDR test error: {err}"))

        threading.Thread(target=run_test, daemon=True).start()

    def _on_sdr_config(self, button):
        """Edit OpenWebRX configuration"""
        config_paths = [
            '/etc/openwebrx/config_webrx.py',
            '/etc/openwebrx/openwebrx.conf',
            '/var/lib/openwebrx/settings.json',
        ]

        for path in config_paths:
            if Path(path).exists():
                self._edit_config(path)
                return

        self._log("OpenWebRX config not found. Is OpenWebRX installed?")
        self.main_window.set_status_message("OpenWebRX config not found")

    def _on_launch_gqrx(self, button):
        """Launch GQRX SDR receiver"""
        self._launch_desktop_app('gqrx', 'gqrx-sdr')

    def _on_launch_cubicsdr(self, button):
        """Launch CubicSDR"""
        self._launch_desktop_app('CubicSDR', 'cubicsdr')

    def _on_spectrum_scan(self, button):
        """Run RTL-SDR power spectrum scan"""
        if not shutil.which('rtl_power'):
            self._log("rtl_power not found. Install with: sudo apt install rtl-sdr")
            return

        self._log("Starting spectrum scan (915 MHz band)...")
        self.main_window.set_status_message("Scanning spectrum...")

        def run_scan():
            try:
                # Quick scan of 915 MHz band (US ISM)
                result = subprocess.run(
                    ['rtl_power', '-f', '902M:928M:100k', '-g', '40', '-i', '1', '-1'],
                    capture_output=True, text=True, timeout=30
                )
                if result.stdout:
                    lines = result.stdout.strip().split('\n')
                    GLib.idle_add(lambda: self._log(f"Spectrum scan complete: {len(lines)} samples"))
                    GLib.idle_add(lambda: self._log("Scan data: Use rtl_power for full analysis"))
                else:
                    GLib.idle_add(lambda: self._log("Scan complete (no output)"))
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self._log("Spectrum scan timed out"))
            except Exception as e:
                err = str(e)
                GLib.idle_add(lambda: self._log(f"Spectrum scan error: {err}"))

            GLib.idle_add(lambda: self.main_window.set_status_message("Scan complete"))

        threading.Thread(target=run_scan, daemon=True).start()

    # =====================
    # Network Diagnostics Methods
    # =====================

    def _parse_proc_net(self, protocol: str) -> list:
        """Parse /proc/net/udp or /proc/net/tcp and return list of (local_addr, port, state, inode)"""
        results = []
        proc_file = f"/proc/net/{protocol}"

        try:
            with open(proc_file, 'r') as f:
                lines = f.readlines()[1:]  # Skip header

            for line in lines:
                parts = line.split()
                if len(parts) >= 10:
                    # local_address is in hex format: IP:PORT
                    local_addr = parts[1]
                    state = parts[3]
                    inode = parts[9]

                    # Parse hex address
                    addr_parts = local_addr.split(':')
                    hex_ip = addr_parts[0]
                    hex_port = addr_parts[1]

                    # Convert hex IP (little-endian for IPv4)
                    try:
                        ip_int = int(hex_ip, 16)
                        ip_bytes = [
                            (ip_int >> 0) & 0xFF,
                            (ip_int >> 8) & 0xFF,
                            (ip_int >> 16) & 0xFF,
                            (ip_int >> 24) & 0xFF,
                        ]
                        ip_str = '.'.join(str(b) for b in ip_bytes)
                        port = int(hex_port, 16)

                        # State names (TCP only, UDP is stateless)
                        state_names = {
                            '01': 'ESTABLISHED',
                            '02': 'SYN_SENT',
                            '03': 'SYN_RECV',
                            '04': 'FIN_WAIT1',
                            '05': 'FIN_WAIT2',
                            '06': 'TIME_WAIT',
                            '07': 'CLOSE',
                            '08': 'CLOSE_WAIT',
                            '09': 'LAST_ACK',
                            '0A': 'LISTEN',
                            '0B': 'CLOSING',
                        }
                        state_str = state_names.get(state.upper(), state)

                        results.append({
                            'ip': ip_str,
                            'port': port,
                            'state': state_str,
                            'inode': inode
                        })
                    except (ValueError, IndexError):
                        continue

        except FileNotFoundError:
            pass
        except PermissionError:
            pass

        return results

    def _get_inode_to_process(self) -> dict:
        """Map socket inodes to process names"""
        inode_map = {}

        try:
            # Iterate through /proc/*/fd/* to find socket inodes
            for pid_dir in Path('/proc').iterdir():
                if not pid_dir.name.isdigit():
                    continue

                pid = pid_dir.name
                try:
                    # Get process name
                    comm_file = pid_dir / 'comm'
                    if comm_file.exists():
                        proc_name = comm_file.read_text().strip()
                    else:
                        proc_name = "unknown"

                    # Check fd directory for sockets
                    fd_dir = pid_dir / 'fd'
                    if fd_dir.exists():
                        for fd_link in fd_dir.iterdir():
                            try:
                                target = fd_link.resolve()
                                target_str = str(fd_link.readlink())
                                if target_str.startswith('socket:['):
                                    inode = target_str[8:-1]  # Extract inode from socket:[12345]
                                    inode_map[inode] = f"{proc_name} (PID {pid})"
                            except (OSError, PermissionError):
                                continue
                except (OSError, PermissionError):
                    continue
        except Exception:
            pass

        return inode_map

    def _parse_proc_net_v6(self, protocol: str) -> list:
        """Parse /proc/net/udp6 or /proc/net/tcp6 for IPv6 sockets"""
        results = []
        proc_file = f"/proc/net/{protocol}"

        try:
            with open(proc_file, 'r') as f:
                lines = f.readlines()[1:]  # Skip header

            for line in lines:
                parts = line.split()
                if len(parts) >= 10:
                    local_addr = parts[1]
                    state = parts[3]
                    inode = parts[9]

                    # Parse hex address (IPv6:PORT)
                    addr_parts = local_addr.split(':')
                    hex_ip = addr_parts[0]
                    hex_port = addr_parts[1]

                    try:
                        port = int(hex_port, 16)

                        # Convert hex IPv6 to readable format
                        # IPv6 is stored as 32 hex chars
                        if len(hex_ip) == 32:
                            # Split into 8 groups of 4 hex chars
                            groups = []
                            for i in range(0, 32, 8):
                                # Each 8-char segment is little-endian 32-bit
                                segment = hex_ip[i:i+8]
                                # Reverse byte order within each 32-bit word
                                reversed_seg = segment[6:8] + segment[4:6] + segment[2:4] + segment[0:2]
                                groups.append(reversed_seg[0:4])
                                groups.append(reversed_seg[4:8])
                            ip_str = ':'.join(groups)
                            # Simplify :: notation
                            ip_str = ip_str.lower()
                        else:
                            ip_str = hex_ip

                        state_names = {
                            '01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
                            '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
                            '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
                            '0A': 'LISTEN', '0B': 'CLOSING',
                        }
                        state_str = state_names.get(state.upper(), state)

                        results.append({
                            'ip': ip_str,
                            'port': port,
                            'state': state_str,
                            'inode': inode
                        })
                    except (ValueError, IndexError):
                        continue

        except FileNotFoundError:
            pass
        except PermissionError:
            pass

        return results

    def _on_show_udp_listeners(self, button):
        """Show UDP port listeners"""
        self._log("\n=== UDP Listeners ===")
        threading.Thread(target=self._fetch_udp_listeners, daemon=True).start()

    def _fetch_udp_listeners(self):
        """Fetch UDP listeners in background (IPv4 and IPv6)"""
        entries_v4 = self._parse_proc_net('udp')
        entries_v6 = self._parse_proc_net_v6('udp6')
        inode_map = self._get_inode_to_process()

        # IPv4
        GLib.idle_add(self._log, "\n-- IPv4 (/proc/net/udp) --")
        v4_count = 0
        if entries_v4:
            GLib.idle_add(self._log, f"{'IP Address':>15} : {'Port':>5}  Process")
            GLib.idle_add(self._log, "-" * 50)
            for entry in entries_v4:
                if entry['port'] == 0:
                    continue
                v4_count += 1
                proc = inode_map.get(entry['inode'], 'unknown')
                line = f"{entry['ip']:>15} : {entry['port']:>5}  {proc}"
                GLib.idle_add(self._log, line)
        if v4_count == 0:
            GLib.idle_add(self._log, "  No IPv4 UDP listeners")

        # IPv6
        GLib.idle_add(self._log, "\n-- IPv6 (/proc/net/udp6) --")
        v6_count = 0
        if entries_v6:
            for entry in entries_v6:
                if entry['port'] == 0:
                    continue
                v6_count += 1
                proc = inode_map.get(entry['inode'], 'unknown')
                # Truncate long IPv6 addresses for display
                ip_short = entry['ip'][:32] + "..." if len(entry['ip']) > 35 else entry['ip']
                line = f"  [{ip_short}]:{entry['port']} - {proc}"
                GLib.idle_add(self._log, line)
        if v6_count == 0:
            GLib.idle_add(self._log, "  No IPv6 UDP listeners")

        GLib.idle_add(self._log, f"\nTotal: {v4_count} IPv4 + {v6_count} IPv6 = {v4_count + v6_count} UDP sockets")

    def _on_show_tcp_listeners(self, button):
        """Show TCP port listeners"""
        self._log("\n=== TCP Listeners (/proc/net/tcp) ===")
        threading.Thread(target=self._fetch_tcp_listeners, daemon=True).start()

    def _fetch_tcp_listeners(self):
        """Fetch TCP listeners in background"""
        entries = self._parse_proc_net('tcp')
        inode_map = self._get_inode_to_process()

        if not entries:
            GLib.idle_add(self._log, "No TCP listeners found")
            return

        GLib.idle_add(self._log, f"{'IP Address':>15} : {'Port':>5}  {'State':<12} Process")
        GLib.idle_add(self._log, "-" * 65)

        # Show LISTEN sockets first
        listen_entries = [e for e in entries if e['state'] == 'LISTEN']
        other_entries = [e for e in entries if e['state'] != 'LISTEN']

        for entry in listen_entries:
            proc = inode_map.get(entry['inode'], 'unknown')
            line = f"{entry['ip']:>15} : {entry['port']:>5}  {entry['state']:<12} {proc}"
            GLib.idle_add(self._log, line)

        if other_entries:
            GLib.idle_add(self._log, "\n-- Other TCP states --")
            for entry in other_entries[:10]:  # Limit to avoid spam
                proc = inode_map.get(entry['inode'], 'unknown')
                line = f"{entry['ip']:>15} : {entry['port']:>5}  {entry['state']:<12} {proc}"
                GLib.idle_add(self._log, line)

        GLib.idle_add(self._log, f"\nTotal: {len(listen_entries)} listening, {len(other_entries)} other")

    def _on_show_multicast(self, button):
        """Show multicast group memberships"""
        self._log("\n=== Multicast Group Memberships ===")
        threading.Thread(target=self._fetch_multicast, daemon=True).start()

    def _fetch_multicast(self):
        """Fetch multicast groups in background"""
        # Method 1: Parse /proc/net/igmp
        GLib.idle_add(self._log, "\n-- IGMP Groups (/proc/net/igmp) --")
        try:
            with open('/proc/net/igmp', 'r') as f:
                lines = f.readlines()

            current_device = None
            for line in lines[1:]:  # Skip header
                if line[0].isdigit():
                    # Device line
                    parts = line.split()
                    if len(parts) >= 2:
                        current_device = parts[1].rstrip(':')
                        GLib.idle_add(self._log, f"\n{current_device}:")
                elif line.strip().startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'A', 'B', 'C', 'D', 'E', 'F')):
                    # Group line - hex address (little-endian)
                    parts = line.split()
                    if parts:
                        hex_group = parts[0]
                        try:
                            # Convert hex to IP (multicast addresses)
                            group_int = int(hex_group, 16)
                            group_bytes = [
                                (group_int >> 0) & 0xFF,
                                (group_int >> 8) & 0xFF,
                                (group_int >> 16) & 0xFF,
                                (group_int >> 24) & 0xFF,
                            ]
                            group_ip = '.'.join(str(b) for b in group_bytes)
                            GLib.idle_add(self._log, f"  {group_ip}")
                        except ValueError:
                            GLib.idle_add(self._log, f"  {hex_group}")

        except FileNotFoundError:
            GLib.idle_add(self._log, "  /proc/net/igmp not found")
        except PermissionError:
            GLib.idle_add(self._log, "  Permission denied")

        # Method 2: Use ip maddr command
        GLib.idle_add(self._log, "\n-- IP Multicast Addresses (ip maddr) --")
        try:
            result = subprocess.run(
                ['ip', 'maddr', 'show'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line)
            else:
                GLib.idle_add(self._log, "  ip maddr command failed")
        except FileNotFoundError:
            GLib.idle_add(self._log, "  ip command not found")
        except Exception as e:
            GLib.idle_add(self._log, f"  Error: {e}")

    def _on_show_process_ports(self, button):
        """Show process to port mapping"""
        self._log("\n=== Process → Port Mapping ===")
        threading.Thread(target=self._fetch_process_ports, daemon=True).start()

    def _fetch_process_ports(self):
        """Fetch process-port mapping using ss"""
        # Try ss first (faster), fall back to netstat
        try:
            result = subprocess.run(
                ['ss', '-tulnp'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "Using: ss -tulnp")
                GLib.idle_add(self._log, "-" * 80)
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line[:100])
                return
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Fall back to netstat
        try:
            result = subprocess.run(
                ['netstat', '-tulnp'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "Using: netstat -tulnp")
                GLib.idle_add(self._log, "-" * 80)
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line[:100])
                return
        except FileNotFoundError:
            GLib.idle_add(self._log, "Neither ss nor netstat found. Install: sudo apt install iproute2")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_check_rns_ports(self, button):
        """Check RNS-specific ports"""
        self._log("\n=== RNS Port Check ===")
        threading.Thread(target=self._check_rns_ports_thread, daemon=True).start()

    def _check_rns_ports_thread(self):
        """Check RNS ports in background"""
        # RNS AutoInterface uses UDP multicast on port 29716
        rns_port = 29716

        GLib.idle_add(self._log, f"Checking RNS AutoInterface port: {rns_port}")

        # Check /proc/net/udp for the port (IPv4)
        entries_v4 = self._parse_proc_net('udp')
        entries_v6 = self._parse_proc_net_v6('udp6')
        inode_map = self._get_inode_to_process()

        found_v4 = False
        found_v6 = False

        # Check IPv4
        for entry in entries_v4:
            if entry['port'] == rns_port:
                found_v4 = True
                proc = inode_map.get(entry['inode'], 'unknown')
                GLib.idle_add(self._log, f"  ✗ IPv4 Port {rns_port} IN USE by: {proc}")

        # Check IPv6 (RNS AutoInterface often uses IPv6 multicast!)
        for entry in entries_v6:
            if entry['port'] == rns_port:
                found_v6 = True
                proc = inode_map.get(entry['inode'], 'unknown')
                ip_short = entry['ip'][:24] + "..." if len(entry['ip']) > 27 else entry['ip']
                GLib.idle_add(self._log, f"  ✗ IPv6 Port {rns_port} IN USE: [{ip_short}]")
                GLib.idle_add(self._log, f"    Process: {proc}")

        if not found_v4 and not found_v6:
            GLib.idle_add(self._log, f"  ✓ Port {rns_port} is FREE (IPv4 and IPv6)")
        elif found_v6 and not found_v4:
            GLib.idle_add(self._log, f"  ! IPv6 multicast bound - this is the RNS AutoInterface")

        # Quick check with ss for more detail
        try:
            result = subprocess.run(
                ['ss', '-ulnp'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if '29716' in line:
                        GLib.idle_add(self._log, f"\nss output: {line[:90]}")
        except Exception:
            pass

        # Also check if rnsd is running
        GLib.idle_add(self._log, "\nRNS Processes:")
        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, f"  {line}")
            else:
                GLib.idle_add(self._log, "  No RNS processes running")
        except Exception:
            GLib.idle_add(self._log, "  Could not check processes")

        # Check rnstatus if available
        try:
            result = subprocess.run(
                ['rnstatus'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "\nrnstatus output:")
                for line in result.stdout.strip().split('\n')[:10]:
                    GLib.idle_add(self._log, f"  {line}")
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _on_check_mesh_ports(self, button):
        """Check Meshtastic-specific ports"""
        self._log("\n=== Meshtastic Port Check ===")
        threading.Thread(target=self._check_mesh_ports_thread, daemon=True).start()

    def _check_mesh_ports_thread(self):
        """Check Meshtastic ports in background"""
        # meshtasticd uses TCP 4403 (API) and TCP 9443 (HTTPS)
        tcp_ports = [4403, 9443]
        udp_broadcast = 4403  # For multicast discovery

        GLib.idle_add(self._log, "Checking meshtasticd ports:")

        # Check TCP ports
        tcp_entries = self._parse_proc_net('tcp')
        inode_map = self._get_inode_to_process()

        for port in tcp_ports:
            found = False
            for entry in tcp_entries:
                if entry['port'] == port and entry['state'] == 'LISTEN':
                    found = True
                    proc = inode_map.get(entry['inode'], 'unknown')
                    GLib.idle_add(self._log, f"  ✓ TCP {port} LISTENING ({proc})")
                    break
            if not found:
                GLib.idle_add(self._log, f"  ✗ TCP {port} NOT listening")

        # Check if meshtasticd is running
        GLib.idle_add(self._log, "\nMeshtastic Processes:")
        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, f"  {line}")
            else:
                GLib.idle_add(self._log, "  meshtasticd not running")
        except Exception:
            GLib.idle_add(self._log, "  Could not check processes")

        # Check systemd service status
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            GLib.idle_add(self._log, f"\nService status: {status}")
        except Exception:
            pass

    def _on_full_network_diagnostics(self, button):
        """Run all network diagnostics"""
        self._log("\n" + "=" * 60)
        self._log("FULL NETWORK DIAGNOSTICS")
        self._log("=" * 60)
        threading.Thread(target=self._run_full_diagnostics, daemon=True).start()

    def _run_full_diagnostics(self):
        """Run comprehensive network diagnostics"""
        # 1. UDP Listeners
        self._fetch_udp_listeners()
        GLib.idle_add(self._log, "")

        # 2. TCP Listeners
        self._fetch_tcp_listeners()
        GLib.idle_add(self._log, "")

        # 3. RNS Ports
        self._check_rns_ports_thread()
        GLib.idle_add(self._log, "")

        # 4. Meshtastic Ports
        self._check_mesh_ports_thread()
        GLib.idle_add(self._log, "")

        # 5. Multicast Groups
        self._fetch_multicast()

        GLib.idle_add(self._log, "\n" + "=" * 60)
        GLib.idle_add(self._log, "DIAGNOSTICS COMPLETE")
        GLib.idle_add(self._log, "=" * 60)

    def _on_watch_api_connections(self, button):
        """Watch connections to meshtasticd API port"""
        self._log("\n=== Watching API Connections (port 4403) ===")
        self._log("Refreshing every 2 seconds... Click again to refresh.\n")
        threading.Thread(target=self._watch_api_connections_thread, daemon=True).start()

    def _watch_api_connections_thread(self):
        """Show current connections to port 4403"""
        try:
            # Use ss to show connections
            result = subprocess.run(
                ['ss', '-tnp', 'sport', '=', ':4403', 'or', 'dport', '=', ':4403'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                GLib.idle_add(self._log, f"Active connections to meshtasticd:")
                for line in lines:
                    GLib.idle_add(self._log, f"  {line[:90]}")
            else:
                # Fallback - check who has the port open
                result = subprocess.run(
                    ['ss', '-tlnp'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if '4403' in line:
                            GLib.idle_add(self._log, line[:90])

            # Also show established connections
            tcp_entries = self._parse_proc_net('tcp')
            inode_map = self._get_inode_to_process()

            connected = []
            for entry in tcp_entries:
                if entry['port'] == 4403 and entry['state'] == 'ESTABLISHED':
                    proc = inode_map.get(entry['inode'], 'unknown')
                    connected.append(f"{entry['ip']}:{entry['port']} - {proc}")

            if connected:
                GLib.idle_add(self._log, f"\nEstablished connections:")
                for c in connected:
                    GLib.idle_add(self._log, f"  {c}")
            else:
                GLib.idle_add(self._log, "\nNo active client connections to port 4403")

        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_kill_competing_clients(self, button):
        """Kill processes that compete for meshtasticd connection"""
        self._log("\n=== Killing Competing Clients ===")
        threading.Thread(target=self._kill_competing_clients_thread, daemon=True).start()

    def _kill_competing_clients_thread(self):
        """Kill nomadnet and python meshtastic clients"""
        killed = []

        # Kill nomadnet
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'nomadnet'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('nomadnet')
        except Exception:
            pass

        # Kill python meshtastic clients (but not meshtasticd itself)
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'python.*meshtastic'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('python meshtastic')
        except Exception:
            pass

        # Kill any lxmf processes
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'lxmf'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('lxmf')
        except Exception:
            pass

        if killed:
            GLib.idle_add(self._log, f"Killed: {', '.join(killed)}")
        else:
            GLib.idle_add(self._log, "No competing clients found to kill")

        # Verify
        GLib.idle_add(self._log, "\nRemaining processes:")
        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'nomadnet|lxmf|meshtastic'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    # Filter out meshtasticd itself
                    if 'meshtasticd' not in line:
                        GLib.idle_add(self._log, f"  {line}")
                    else:
                        GLib.idle_add(self._log, f"  {line} (daemon - OK)")
            else:
                GLib.idle_add(self._log, "  None (clean)")
        except Exception:
            pass

    def _on_stop_all_rns(self, button):
        """Stop all RNS-related processes"""
        self._log("\n=== Stopping All RNS Processes ===")
        threading.Thread(target=self._stop_all_rns_thread, daemon=True).start()

    def _stop_all_rns_thread(self):
        """Kill all RNS processes"""
        killed = []

        processes = ['rnsd', 'nomadnet', 'lxmf', 'RNS']
        for proc in processes:
            try:
                result = subprocess.run(
                    ['pkill', '-9', '-f', proc],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    killed.append(proc)
            except Exception:
                pass

        if killed:
            GLib.idle_add(self._log, f"Killed: {', '.join(killed)}")
        else:
            GLib.idle_add(self._log, "No RNS processes found")

        # Check what's left
        GLib.idle_add(self._log, "\nVerifying...")
        import time
        time.sleep(0.5)

        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf|RNS'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                GLib.idle_add(self._log, "Still running:")
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, f"  {line}")
            else:
                GLib.idle_add(self._log, "All RNS processes stopped ✓")
        except Exception:
            GLib.idle_add(self._log, "Verification complete")

        # Check port 29716
        GLib.idle_add(self._log, "\nChecking port 29716...")
        try:
            result = subprocess.run(
                ['ss', '-ulnp'],
                capture_output=True, text=True, timeout=5
            )
            found = False
            for line in result.stdout.split('\n'):
                if '29716' in line:
                    found = True
                    GLib.idle_add(self._log, f"  Still bound: {line[:80]}")
            if not found:
                GLib.idle_add(self._log, "  Port 29716 is free ✓")
        except Exception:
            pass
