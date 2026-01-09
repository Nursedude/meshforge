"""
Mesh Tools Panel - Consolidated mesh network tools

Combines:
- MeshBot (BBS, games, inventory, weather)
- Node Map (network visualization)
- Diagnostics (health monitoring, event log)

With shared resizable log output at bottom.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import subprocess
import os
import json
from pathlib import Path
from datetime import datetime

# Import UI standards
try:
    from utils.gtk_helpers import (
        UI, create_panel_header, create_standard_frame,
        ResizableLogViewer, ResizablePanedLayout, StatusIndicator
    )
    HAS_UI_HELPERS = True
except ImportError:
    HAS_UI_HELPERS = False

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import path utilities
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Import settings manager
try:
    from utils.common import SettingsManager
    HAS_SETTINGS = True
except ImportError:
    HAS_SETTINGS = False

# Import diagnostics
try:
    from utils.network_diagnostics import (
        get_diagnostics, EventCategory, EventSeverity, HealthStatus
    )
    HAS_DIAGNOSTICS = True
except ImportError:
    HAS_DIAGNOSTICS = False

# Import service availability checker
try:
    from utils.service_check import check_port, check_service, require_service
    HAS_SERVICE_CHECK = True
except ImportError:
    HAS_SERVICE_CHECK = False
    def check_port(port, host='localhost', timeout=2.0):
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False


class MeshToolsPanel(Gtk.Box):
    """
    Consolidated mesh network tools panel.

    Provides sub-tabs for MeshBot, Node Map, and Diagnostics
    with a shared resizable log output at the bottom.
    """

    SETTINGS_DEFAULTS = {
        "meshbot_path": "/opt/meshing-around",
        "log_position": 300,  # Paned position
        "active_tab": 0,
    }

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_window = main_window

        # Apply standard margins
        margin = UI.MARGIN_PANEL if HAS_UI_HELPERS else 20
        self.set_margin_start(margin)
        self.set_margin_end(margin)
        self.set_margin_top(margin)
        self.set_margin_bottom(margin)

        # Load settings
        if HAS_SETTINGS:
            self._settings_mgr = SettingsManager("mesh_tools", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            self._settings = self.SETTINGS_DEFAULTS.copy()

        # Bot process tracking
        self._bot_process = None
        self._log_timer_id = None

        self._build_ui()

        # Initial status check
        GLib.timeout_add(500, self._check_all_status)

    def _save_settings(self):
        """Save settings"""
        if HAS_SETTINGS:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()

    def _build_ui(self):
        """Build the main UI with paned layout"""
        # Header
        if HAS_UI_HELPERS:
            header = create_panel_header(
                "Mesh Tools",
                "MeshBot, Node Map, and Network Diagnostics",
                "network-workgroup-symbolic"
            )
        else:
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            title = Gtk.Label(label="Mesh Tools")
            title.add_css_class("title-1")
            title.set_xalign(0)
            header.append(title)

        self.append(header)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Main paned layout: tabs on top, log on bottom
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._paned.set_wide_handle(True)
        self._paned.set_vexpand(True)

        # Top: Notebook with tabs
        self._notebook = Gtk.Notebook()
        self._notebook.set_tab_pos(Gtk.PositionType.TOP)

        # Add tabs
        self._add_meshbot_tab()
        self._add_map_tab()
        self._add_diagnostics_tab()

        self._paned.set_start_child(self._notebook)

        # Bottom: Shared log viewer
        self._build_log_viewer()
        self._paned.set_end_child(self._log_frame)

        # Restore paned position
        self._paned.set_position(self._settings.get("log_position", 300))
        self._paned.connect("notify::position", self._on_paned_moved)

        self.append(self._paned)

    def _build_log_viewer(self):
        """Build the shared log viewer at the bottom"""
        self._log_frame = Gtk.Frame()
        self._log_frame.set_label("Output Log")

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Controls bar
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_margin_start(10)
        controls.set_margin_end(10)
        controls.set_margin_top(5)
        controls.set_margin_bottom(5)

        # Source selector
        controls.append(Gtk.Label(label="Source:"))
        self._log_source = Gtk.ComboBoxText()
        self._log_source.append("meshbot", "MeshBot Output")
        self._log_source.append("diagnostics", "Diagnostics Events")
        self._log_source.append("system", "System Log")
        self._log_source.set_active_id("meshbot")
        self._log_source.connect("changed", self._on_log_source_changed)
        controls.append(self._log_source)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls.append(spacer)

        # Auto-scroll toggle
        self._auto_scroll = Gtk.CheckButton(label="Auto-scroll")
        self._auto_scroll.set_active(True)
        controls.append(self._auto_scroll)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh Log")
        refresh_btn.connect("clicked", self._on_refresh_log)
        controls.append(refresh_btn)

        # Clear button
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_log)
        controls.append(clear_btn)

        log_box.append(controls)
        log_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Log text view
        self._log_text = Gtk.TextView()
        self._log_text.set_editable(False)
        self._log_text.set_monospace(True)
        self._log_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_text.set_cursor_visible(False)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        log_scroll.set_min_content_height(100)
        log_scroll.set_child(self._log_text)
        self._log_scroll = log_scroll

        log_box.append(log_scroll)
        self._log_frame.set_child(log_box)

    def _on_paned_moved(self, paned, param):
        """Save paned position when moved"""
        self._settings["log_position"] = paned.get_position()
        self._save_settings()

    # =========================================================================
    # MeshBot Tab
    # =========================================================================

    def _add_meshbot_tab(self):
        """Add MeshBot management tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Status section
        status_frame = Gtk.Frame()
        status_frame.set_label("MeshBot Status")
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        status_box.set_margin_start(15)
        status_box.set_margin_end(15)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self._meshbot_status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        self._meshbot_status_icon.set_pixel_size(32)
        status_row.append(self._meshbot_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._meshbot_status_label = Gtk.Label(label="Checking...")
        self._meshbot_status_label.set_xalign(0)
        self._meshbot_status_label.add_css_class("heading")
        status_info.append(self._meshbot_status_label)

        self._meshbot_detail_label = Gtk.Label(label="")
        self._meshbot_detail_label.set_xalign(0)
        self._meshbot_detail_label.add_css_class("dim-label")
        status_info.append(self._meshbot_detail_label)

        status_row.append(status_info)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self._start_btn = Gtk.Button(label="Start")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.connect("clicked", self._on_start_bot)
        btn_box.append(self._start_btn)

        self._stop_btn = Gtk.Button(label="Stop")
        self._stop_btn.add_css_class("destructive-action")
        self._stop_btn.connect("clicked", self._on_stop_bot)
        btn_box.append(self._stop_btn)

        install_btn = Gtk.Button(label="Install")
        install_btn.connect("clicked", self._on_install_bot)
        btn_box.append(install_btn)

        status_row.append(btn_box)
        status_box.append(status_row)

        status_frame.set_child(status_box)
        box.append(status_frame)

        # Config section
        config_frame = Gtk.Frame()
        config_frame.set_label("Configuration")
        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config_box.set_margin_start(15)
        config_box.set_margin_end(15)
        config_box.set_margin_top(10)
        config_box.set_margin_bottom(10)

        # Path entry
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_row.append(Gtk.Label(label="Install Path:"))
        self._path_entry = Gtk.Entry()
        self._path_entry.set_text(self._settings.get("meshbot_path", "/opt/meshing-around"))
        self._path_entry.set_hexpand(True)
        path_row.append(self._path_entry)
        config_box.append(path_row)

        # Config file buttons
        config_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        edit_config_btn = Gtk.Button(label="Edit Config")
        edit_config_btn.connect("clicked", self._on_edit_config)
        edit_config_btn.set_tooltip_text("Edit config.ini in text editor")
        config_btn_row.append(edit_config_btn)

        view_config_btn = Gtk.Button(label="View Config")
        view_config_btn.connect("clicked", self._on_view_config)
        view_config_btn.set_tooltip_text("View config.ini in log viewer below")
        config_btn_row.append(view_config_btn)

        open_folder_btn = Gtk.Button(label="Open Folder")
        open_folder_btn.connect("clicked", self._on_open_meshbot_folder)
        config_btn_row.append(open_folder_btn)

        config_box.append(config_btn_row)

        # Log/Debug buttons row
        log_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        view_log_btn = Gtk.Button(label="View Log")
        view_log_btn.connect("clicked", self._on_view_meshbot_log)
        view_log_btn.set_tooltip_text("View MeshBot log file")
        log_btn_row.append(view_log_btn)

        tail_log_btn = Gtk.Button(label="Tail Log")
        tail_log_btn.connect("clicked", self._on_tail_meshbot_log)
        tail_log_btn.set_tooltip_text("Show last 50 lines of log")
        log_btn_row.append(tail_log_btn)

        journal_btn = Gtk.Button(label="Journal")
        journal_btn.connect("clicked", self._on_view_journal)
        journal_btn.set_tooltip_text("View systemd journal for mesh_bot")
        log_btn_row.append(journal_btn)

        config_box.append(log_btn_row)

        config_frame.set_child(config_box)
        box.append(config_frame)

        # Connection Mode section - IMPORTANT for browser compatibility
        conn_frame = Gtk.Frame()
        conn_frame.set_label("Connection Mode")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        conn_box.set_margin_start(15)
        conn_box.set_margin_end(15)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        # Warning info
        warn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        warn_box.append(warn_icon)
        warn_label = Gtk.Label(
            label="Serial mode blocks browser access. Use TCP mode for shared access."
        )
        warn_label.set_wrap(True)
        warn_label.set_xalign(0)
        warn_label.add_css_class("dim-label")
        warn_box.append(warn_label)
        conn_box.append(warn_box)

        # meshtasticd status
        meshtasticd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        meshtasticd_row.append(Gtk.Label(label="meshtasticd:"))
        self._meshtasticd_status = Gtk.Label(label="Checking...")
        self._meshtasticd_status.add_css_class("dim-label")
        meshtasticd_row.append(self._meshtasticd_status)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        meshtasticd_row.append(spacer2)

        start_meshtasticd_btn = Gtk.Button(label="Start meshtasticd")
        start_meshtasticd_btn.connect("clicked", self._on_start_meshtasticd)
        meshtasticd_row.append(start_meshtasticd_btn)

        conn_box.append(meshtasticd_row)

        # Connection mode selector
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mode_row.append(Gtk.Label(label="MeshBot connects via:"))

        self._conn_mode = Gtk.ComboBoxText()
        self._conn_mode.append("serial", "Serial (exclusive - blocks browser)")
        self._conn_mode.append("tcp", "TCP (shared - browser compatible)")
        self._conn_mode.set_active_id("tcp")
        self._conn_mode.set_tooltip_text("TCP mode requires meshtasticd running")
        mode_row.append(self._conn_mode)

        apply_mode_btn = Gtk.Button(label="Apply to Config")
        apply_mode_btn.add_css_class("suggested-action")
        apply_mode_btn.connect("clicked", self._on_apply_connection_mode)
        apply_mode_btn.set_tooltip_text("Update mesh_bot config.ini with selected mode")
        mode_row.append(apply_mode_btn)

        conn_box.append(mode_row)

        # Quick browser access
        browser_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        browser_label = Gtk.Label(label="Quick access:")
        browser_label.add_css_class("dim-label")
        browser_row.append(browser_label)

        open_browser_btn = Gtk.Button(label="Open Meshtastic Web")
        open_browser_btn.connect("clicked", self._on_open_meshtastic_web)
        open_browser_btn.set_tooltip_text("Open Meshtastic web interface (stops MeshBot if running in serial mode)")
        browser_row.append(open_browser_btn)

        conn_box.append(browser_row)

        conn_frame.set_child(conn_box)
        box.append(conn_frame)

        # Features overview
        features_frame = Gtk.Frame()
        features_frame.set_label("MeshBot Features")
        features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        features_box.set_margin_start(15)
        features_box.set_margin_end(15)
        features_box.set_margin_top(10)
        features_box.set_margin_bottom(10)

        features = [
            ("BBS Messaging", "Store-and-forward bulletin board"),
            ("Weather/Alerts", "NOAA, USGS, FEMA emergency data"),
            ("Games", "DopeWars, BlackJack, Poker"),
            ("Inventory/POS", "Point-of-sale with cart system"),
        ]

        for title, desc in features:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title_lbl = Gtk.Label(label=f"{title}:")
            title_lbl.set_xalign(0)
            title_lbl.add_css_class("heading")
            title_lbl.set_width_chars(15)
            row.append(title_lbl)

            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.set_xalign(0)
            desc_lbl.add_css_class("dim-label")
            row.append(desc_lbl)
            features_box.append(row)

        features_frame.set_child(features_box)
        box.append(features_frame)

        scrolled.set_child(box)

        # Tab label with icon
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("mail-send-symbolic"))
        tab_box.append(Gtk.Label(label="MeshBot"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Node Map Tab
    # =========================================================================

    def _add_map_tab(self):
        """Add Node Map tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Map controls
        controls_frame = Gtk.Frame()
        controls_frame.set_label("Node Map Controls")
        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        controls_box.set_margin_start(15)
        controls_box.set_margin_end(15)
        controls_box.set_margin_top(10)
        controls_box.set_margin_bottom(10)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        refresh_map_btn = Gtk.Button(label="Refresh Nodes")
        refresh_map_btn.add_css_class("suggested-action")
        refresh_map_btn.connect("clicked", self._on_refresh_map)
        btn_row.append(refresh_map_btn)

        open_map_btn = Gtk.Button(label="Open Full Map")
        open_map_btn.connect("clicked", self._on_open_full_map)
        open_map_btn.set_tooltip_text("Open node map in browser")
        btn_row.append(open_map_btn)

        export_btn = Gtk.Button(label="Export GeoJSON")
        export_btn.connect("clicked", self._on_export_geojson)
        btn_row.append(export_btn)

        controls_box.append(btn_row)
        controls_frame.set_child(controls_box)
        box.append(controls_frame)

        # Node list
        nodes_frame = Gtk.Frame()
        nodes_frame.set_label("Discovered Nodes")
        nodes_frame.set_vexpand(True)
        nodes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        nodes_box.set_margin_start(15)
        nodes_box.set_margin_end(15)
        nodes_box.set_margin_top(10)
        nodes_box.set_margin_bottom(10)

        # Node list store
        self._node_store = Gtk.ListStore(str, str, str, str)  # ID, Name, Type, Last Seen

        self._node_tree = Gtk.TreeView(model=self._node_store)
        self._node_tree.set_headers_visible(True)

        for i, title in enumerate(["Node ID", "Name", "Type", "Last Seen"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(80)
            self._node_tree.append_column(column)

        node_scroll = Gtk.ScrolledWindow()
        node_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        node_scroll.set_min_content_height(150)
        node_scroll.set_vexpand(True)
        node_scroll.set_child(self._node_tree)

        nodes_box.append(node_scroll)

        # Stats row
        self._node_stats_label = Gtk.Label(label="Nodes: 0 | Last update: Never")
        self._node_stats_label.set_xalign(0)
        self._node_stats_label.add_css_class("dim-label")
        nodes_box.append(self._node_stats_label)

        nodes_frame.set_child(nodes_box)
        box.append(nodes_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("mark-location-symbolic"))
        tab_box.append(Gtk.Label(label="Node Map"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Diagnostics Tab
    # =========================================================================

    def _add_diagnostics_tab(self):
        """Add Diagnostics tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Health status
        health_frame = Gtk.Frame()
        health_frame.set_label("System Health")
        health_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        health_box.set_margin_start(15)
        health_box.set_margin_end(15)
        health_box.set_margin_top(10)
        health_box.set_margin_bottom(10)

        # Health cards grid
        health_grid = Gtk.Grid()
        health_grid.set_column_spacing(15)
        health_grid.set_row_spacing(10)

        self._health_cards = {}
        checks = [
            ("meshtastic", "Meshtastic", "network-wireless-symbolic"),
            ("reticulum", "Reticulum", "network-transmit-receive-symbolic"),
            ("meshbot", "MeshBot", "mail-send-symbolic"),
            ("network", "Network", "network-wired-symbolic"),
        ]

        for i, (key, label, icon) in enumerate(checks):
            card = self._create_health_card(label, icon)
            health_grid.attach(card, i % 2, i // 2, 1, 1)
            self._health_cards[key] = card

        health_box.append(health_grid)

        # Refresh button
        refresh_health_btn = Gtk.Button(label="Run Health Check")
        refresh_health_btn.connect("clicked", self._on_run_health_check)
        health_box.append(refresh_health_btn)

        health_frame.set_child(health_box)
        box.append(health_frame)

        # Quick tests
        tests_frame = Gtk.Frame()
        tests_frame.set_label("Network Tests")
        tests_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tests_box.set_margin_start(15)
        tests_box.set_margin_end(15)
        tests_box.set_margin_top(10)
        tests_box.set_margin_bottom(10)

        tests_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        ping_btn = Gtk.Button(label="Ping Test")
        ping_btn.connect("clicked", self._on_ping_test)
        tests_row.append(ping_btn)

        traceroute_btn = Gtk.Button(label="Traceroute")
        traceroute_btn.connect("clicked", self._on_traceroute_test)
        tests_row.append(traceroute_btn)

        dns_btn = Gtk.Button(label="DNS Check")
        dns_btn.connect("clicked", self._on_dns_test)
        tests_row.append(dns_btn)

        port_btn = Gtk.Button(label="Port Scan")
        port_btn.connect("clicked", self._on_port_scan)
        tests_row.append(port_btn)

        tests_box.append(tests_row)
        tests_frame.set_child(tests_box)
        box.append(tests_frame)

        # Report generation
        report_frame = Gtk.Frame()
        report_frame.set_label("Diagnostic Reports")
        report_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        report_box.set_margin_start(15)
        report_box.set_margin_end(15)
        report_box.set_margin_top(10)
        report_box.set_margin_bottom(10)

        gen_report_btn = Gtk.Button(label="Generate Full Report")
        gen_report_btn.add_css_class("suggested-action")
        gen_report_btn.connect("clicked", self._on_generate_report)
        report_box.append(gen_report_btn)

        open_reports_btn = Gtk.Button(label="Open Reports Folder")
        open_reports_btn.connect("clicked", self._on_open_reports_folder)
        report_box.append(open_reports_btn)

        report_frame.set_child(report_box)
        box.append(report_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        tab_box.append(Gtk.Label(label="Diagnostics"))

        self._notebook.append_page(scrolled, tab_box)

    def _create_health_card(self, label: str, icon_name: str) -> Gtk.Box:
        """Create a health status card"""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.add_css_class("card")
        card.set_margin_start(10)
        card.set_margin_end(10)
        card.set_margin_top(5)
        card.set_margin_bottom(5)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(24)
        card.append(icon)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=label)
        name.set_xalign(0)
        name.add_css_class("heading")
        info.append(name)

        status = Gtk.Label(label="Unknown")
        status.set_xalign(0)
        status.add_css_class("dim-label")
        status.set_name("status")
        info.append(status)

        card.append(info)

        # Store reference to status label
        card._status_label = status
        return card

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _check_all_status(self):
        """Check status of all services"""
        self._check_meshbot_status()
        self._check_meshtasticd_status()
        self._update_health_cards()
        return False  # Don't repeat

    def _check_meshtasticd_status(self):
        """Check if meshtasticd is running"""
        def check():
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshtasticd'],
                    capture_output=True, text=True, timeout=5
                )
                running = result.returncode == 0 and result.stdout.strip()
                GLib.idle_add(self._update_meshtasticd_status, running)
            except Exception:
                GLib.idle_add(self._update_meshtasticd_status, False)

        threading.Thread(target=check, daemon=True).start()

    def _update_meshtasticd_status(self, running: bool):
        """Update meshtasticd status display"""
        if running:
            self._meshtasticd_status.set_label("Running (TCP available)")
            self._meshtasticd_status.remove_css_class("error")
            self._meshtasticd_status.add_css_class("success")
        else:
            self._meshtasticd_status.set_label("Not Running")
            self._meshtasticd_status.remove_css_class("success")
            self._meshtasticd_status.add_css_class("error")

    def _on_start_meshtasticd(self, button):
        """Start meshtasticd service"""
        self._log_message("Starting meshtasticd...")

        def do_start():
            try:
                # Try systemctl first
                result = subprocess.run(
                    ['sudo', 'systemctl', 'start', 'meshtasticd'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    GLib.idle_add(self._log_message, "meshtasticd started via systemctl")
                else:
                    # Try direct start
                    subprocess.Popen(
                        ['sudo', 'meshtasticd'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    GLib.idle_add(self._log_message, "meshtasticd started directly")

                import time
                time.sleep(2)
                GLib.idle_add(self._check_meshtasticd_status)

            except Exception as e:
                GLib.idle_add(self._log_message, f"Failed to start meshtasticd: {e}")

        threading.Thread(target=do_start, daemon=True).start()

    def _on_apply_connection_mode(self, button):
        """Apply connection mode to mesh_bot config"""
        mode = self._conn_mode.get_active_id()
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        config_path = Path(meshbot_path) / "config.ini"

        if not config_path.exists():
            self._log_message(f"Config not found: {config_path}")
            return

        self._log_message(f"Applying {mode} connection mode...")

        def do_apply():
            try:
                import configparser
                config = configparser.ConfigParser()
                config.read(str(config_path))

                # Update interface settings
                if 'interface' not in config:
                    config['interface'] = {}

                if mode == 'tcp':
                    config['interface']['type'] = 'tcp'
                    config['interface']['hostname'] = 'localhost'
                    config['interface']['port'] = '4403'
                    # Remove serial settings
                    if 'port' in config['interface'] and config['interface']['port'].startswith('/dev'):
                        del config['interface']['port']
                else:  # serial
                    config['interface']['type'] = 'serial'
                    # Default to common serial ports
                    if 'port' not in config['interface'] or not config['interface']['port'].startswith('/dev'):
                        config['interface']['port'] = '/dev/ttyUSB0'

                # Write config
                with open(str(config_path), 'w') as f:
                    config.write(f)

                GLib.idle_add(self._log_message, f"Config updated to {mode} mode")
                GLib.idle_add(self._log_message, "Restart MeshBot for changes to take effect")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Failed to update config: {e}")

        threading.Thread(target=do_apply, daemon=True).start()

    def _on_open_meshtastic_web(self, button):
        """Open Meshtastic web interface"""
        # Check if mesh_bot is running in serial mode - offer to stop it
        def check_and_open():
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, text=True, timeout=5
                )
                meshbot_running = result.returncode == 0 and result.stdout.strip()

                if meshbot_running:
                    GLib.idle_add(self._log_message, "MeshBot is running - may conflict with browser")
                    GLib.idle_add(self._log_message, "Consider using TCP mode or stopping MeshBot")

                # Open web interface anyway (meshtasticd uses port 9443 with HTTPS)
                GLib.idle_add(self._open_url, "https://localhost:9443")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Error: {e}")
                GLib.idle_add(self._open_url, "https://localhost:9443")

        threading.Thread(target=check_and_open, daemon=True).start()

    def _check_meshbot_status(self):
        """Check MeshBot installation and running status"""
        def check():
            status = {"installed": False, "running": False, "path": None}

            meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"

            if Path(meshbot_path).exists() and (Path(meshbot_path) / "mesh_bot.py").exists():
                status["installed"] = True
                status["path"] = meshbot_path

            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    status["running"] = True
            except Exception:
                pass

            GLib.idle_add(self._update_meshbot_status, status)

        threading.Thread(target=check, daemon=True).start()

    def _update_meshbot_status(self, status):
        """Update MeshBot status display"""
        if status["running"]:
            self._meshbot_status_icon.set_from_icon_name("emblem-default-symbolic")
            self._meshbot_status_label.set_label("MeshBot Running")
            self._meshbot_detail_label.set_label(f"Path: {status['path']}")
            self._start_btn.set_sensitive(False)
            self._stop_btn.set_sensitive(True)
        elif status["installed"]:
            self._meshbot_status_icon.set_from_icon_name("media-playback-stop-symbolic")
            self._meshbot_status_label.set_label("MeshBot Stopped")
            self._meshbot_detail_label.set_label(f"Path: {status['path']}")
            self._start_btn.set_sensitive(True)
            self._stop_btn.set_sensitive(False)
        else:
            self._meshbot_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._meshbot_status_label.set_label("MeshBot Not Installed")
            self._meshbot_detail_label.set_label("Click Install to set up")
            self._start_btn.set_sensitive(False)
            self._stop_btn.set_sensitive(False)

    def _update_health_cards(self):
        """Update health status cards"""
        for key, card in self._health_cards.items():
            # Default to unknown
            card._status_label.set_label("Checking...")

        # Check each service
        def check_health():
            results = {}

            # Check Meshtastic
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshtasticd'],
                    capture_output=True, timeout=5
                )
                results["meshtastic"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["meshtastic"] = "Unknown"

            # Check Reticulum
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, timeout=5
                )
                results["reticulum"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["reticulum"] = "Unknown"

            # Check MeshBot
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, timeout=5
                )
                results["meshbot"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["meshbot"] = "Unknown"

            # Check Network
            try:
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
                    capture_output=True, timeout=5
                )
                results["network"] = "Connected" if result.returncode == 0 else "No Internet"
            except Exception:
                results["network"] = "Unknown"

            GLib.idle_add(self._apply_health_results, results)

        threading.Thread(target=check_health, daemon=True).start()

    def _apply_health_results(self, results):
        """Apply health check results to cards"""
        for key, status in results.items():
            if key in self._health_cards:
                card = self._health_cards[key]
                card._status_label.set_label(status)

                # Color code
                if "Running" in status or "Connected" in status:
                    card._status_label.remove_css_class("error")
                    card._status_label.add_css_class("success")
                elif "Not" in status or "No " in status:
                    card._status_label.remove_css_class("success")
                    card._status_label.add_css_class("error")

    def _on_start_bot(self, button):
        """Start MeshBot - use systemd service if available, otherwise direct execution"""
        meshbot_path = self._path_entry.get_text().strip()
        script_path = Path(meshbot_path) / "mesh_bot.py"

        if not script_path.exists():
            self._log_message("Error: mesh_bot.py not found")
            return

        # Check for config.ini
        config_path = Path(meshbot_path) / "config.ini"
        if not config_path.exists():
            self._log_message("Warning: config.ini not found - creating from template...")
            template = Path(meshbot_path) / "config.template"
            if template.exists():
                real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
                subprocess.run(['sudo', 'cp', str(template), str(config_path)], timeout=10)
                subprocess.run(['sudo', 'chown', f'{real_user}:{real_user}', str(config_path)], timeout=10)
            else:
                self._log_message("Error: No config.ini or config.template found")
                return

        self._log_message("Starting MeshBot...")

        def do_start():
            try:
                # Check if systemd service exists
                service_check = subprocess.run(
                    ['systemctl', 'cat', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=5
                )

                if service_check.returncode == 0:
                    # Use systemd service
                    GLib.idle_add(self._log_message, "Using systemd service...")
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'start', 'mesh_bot.service'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(self._log_message, "MeshBot service started")
                        GLib.idle_add(self._check_meshbot_status)
                    else:
                        GLib.idle_add(self._log_message, f"Service start failed: {result.stderr}")
                    return

                # Fall back to direct execution
                # Check for virtual environment
                venv_path = Path(meshbot_path) / "venv"
                venv_python = venv_path / "bin" / "python3"

                if venv_python.exists():
                    # Use venv python
                    cmd = [str(venv_python), str(script_path)]
                    GLib.idle_add(self._log_message, "Using virtual environment")
                else:
                    # Run directly with system python3
                    cmd = ['python3', str(script_path)]
                    GLib.idle_add(self._log_message, "Using system Python (no venv found)")

                GLib.idle_add(self._log_message, f"Running: {' '.join(cmd)}")

                process = subprocess.Popen(
                    cmd,
                    cwd=meshbot_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True
                )

                import time
                time.sleep(3)  # Give it a bit more time

                if process.poll() is None:
                    GLib.idle_add(self._log_message, "MeshBot started successfully")
                    GLib.idle_add(self._check_meshbot_status)
                else:
                    # Process exited - capture output to show why
                    exit_code = process.returncode
                    output = process.stdout.read() if process.stdout else ""
                    GLib.idle_add(self._log_message, f"MeshBot exited with code {exit_code}")
                    if output:
                        # Show first few lines of error
                        lines = output.strip().split('\n')[:10]
                        for line in lines:
                            GLib.idle_add(self._log_message, f"  {line}")
                    # Suggest venv setup if missing
                    if not venv_python.exists():
                        GLib.idle_add(self._log_message, "Tip: Create venv with:")
                        GLib.idle_add(self._log_message, f"  cd {meshbot_path} && python3 -m venv venv")
                        GLib.idle_add(self._log_message, "  venv/bin/pip install -r requirements.txt")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Start error: {e}")

        threading.Thread(target=do_start, daemon=True).start()

    def _on_stop_bot(self, button):
        """Stop MeshBot - use systemd service if available"""
        self._log_message("Stopping MeshBot...")

        def do_stop():
            try:
                # Check if systemd service exists and is running
                service_check = subprocess.run(
                    ['systemctl', 'is-active', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=5
                )

                if service_check.stdout.strip() == 'active':
                    # Stop via systemd
                    GLib.idle_add(self._log_message, "Stopping systemd service...")
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'stop', 'mesh_bot.service'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(self._log_message, "MeshBot service stopped")
                    else:
                        GLib.idle_add(self._log_message, f"Service stop failed: {result.stderr}")
                else:
                    # Kill any running process
                    subprocess.run(['pkill', '-f', 'mesh_bot.py'], timeout=10)
                    GLib.idle_add(self._log_message, "MeshBot stopped")

                import time
                time.sleep(1)
                GLib.idle_add(self._check_meshbot_status)
            except Exception as e:
                GLib.idle_add(self._log_message, f"Stop error: {e}")

        threading.Thread(target=do_stop, daemon=True).start()

    def _on_install_bot(self, button):
        """Install MeshBot"""
        install_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        self._log_message(f"Installing MeshBot to {install_path}...")

        def do_install():
            try:
                install_dir = Path(install_path)
                real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

                if install_dir.exists():
                    GLib.idle_add(self._log_message, "Updating existing installation...")
                    result = subprocess.run(
                        ['git', '-C', str(install_dir), 'pull'],
                        capture_output=True, text=True, timeout=120
                    )
                else:
                    GLib.idle_add(self._log_message, "Cloning repository...")
                    result = subprocess.run(
                        ['sudo', 'git', 'clone',
                         'https://github.com/SpudGunMan/meshing-around.git',
                         str(install_dir)],
                        capture_output=True, text=True, timeout=300
                    )

                if result.returncode != 0:
                    GLib.idle_add(self._log_message, f"Install failed: {result.stderr}")
                    return

                # Set ownership to real user
                GLib.idle_add(self._log_message, "Setting permissions...")
                subprocess.run(['sudo', 'chown', '-R', f'{real_user}:{real_user}', str(install_dir)], timeout=60)

                # Set up virtual environment if not exists
                venv_path = install_dir / "venv"
                if not venv_path.exists():
                    GLib.idle_add(self._log_message, "Creating virtual environment...")
                    venv_result = subprocess.run(
                        ['python3', '-m', 'venv', str(venv_path)],
                        capture_output=True, text=True, timeout=120,
                        cwd=str(install_dir)
                    )
                    if venv_result.returncode != 0:
                        GLib.idle_add(self._log_message, f"Venv creation failed: {venv_result.stderr}")
                        GLib.idle_add(self._log_message, "You can create it manually later")
                    else:
                        # Install requirements
                        req_file = install_dir / "requirements.txt"
                        if req_file.exists():
                            GLib.idle_add(self._log_message, "Installing dependencies (this may take a while)...")
                            pip_path = venv_path / "bin" / "pip"
                            pip_result = subprocess.run(
                                [str(pip_path), 'install', '-r', str(req_file)],
                                capture_output=True, text=True, timeout=600,
                                cwd=str(install_dir)
                            )
                            if pip_result.returncode == 0:
                                GLib.idle_add(self._log_message, "Dependencies installed!")
                            else:
                                GLib.idle_add(self._log_message, f"Pip install warning: {pip_result.stderr[:200]}")

                GLib.idle_add(self._log_message, "Installation complete!")
                GLib.idle_add(self._check_meshbot_status)

            except Exception as e:
                GLib.idle_add(self._log_message, f"Install error: {e}")

        threading.Thread(target=do_install, daemon=True).start()

    def _on_edit_config(self, button):
        """Open config file in editor"""
        meshbot_path = self._path_entry.get_text().strip()
        config_path = Path(meshbot_path) / "config.ini"

        if not config_path.exists():
            template = Path(meshbot_path) / "config.template"
            if template.exists():
                subprocess.run(['sudo', 'cp', str(template), str(config_path)], timeout=10)
            else:
                self._log_message("No config file found")
                return

        # Ensure config file is writable by real user
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        subprocess.run(['sudo', 'chown', f'{real_user}:{real_user}', str(config_path)], timeout=10)
        subprocess.run(['sudo', 'chmod', '644', str(config_path)], timeout=10)

        # Open in default editor
        try:
            subprocess.Popen(
                ['sudo', '-u', real_user, 'xdg-open', str(config_path)],
                start_new_session=True
            )
            self._log_message(f"Opening {config_path} in editor")
        except Exception as e:
            self._log_message(f"Error opening editor: {e}")

    def _on_view_config(self, button):
        """View config in log viewer"""
        meshbot_path = self._path_entry.get_text().strip()
        config_path = Path(meshbot_path) / "config.ini"

        if config_path.exists():
            content = config_path.read_text()
            self._set_log_text(f"=== {config_path} ===\n\n{content}")
        else:
            self._log_message("Config file not found")

    def _on_open_meshbot_folder(self, button):
        """Open MeshBot folder"""
        meshbot_path = self._path_entry.get_text().strip()
        self._open_folder(meshbot_path)

    def _on_view_meshbot_log(self, button):
        """View MeshBot log file"""
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        log_files = [
            # Current meshing-around log locations
            Path(meshbot_path) / "logs" / "meshbot.log",
            Path(meshbot_path) / "logs" / "messages.log",
            # Legacy/alternative locations
            Path(meshbot_path) / "prior_log.txt",
            Path(meshbot_path) / "prior_debug_log.txt",
        ]

        for log_path in log_files:
            if log_path.exists():
                try:
                    content = log_path.read_text()
                    self._set_log_text(f"=== {log_path} ===\n\n{content[-10000:]}")  # Last 10k chars
                    self._log_message(f"Loaded log from {log_path}")
                    return
                except Exception as e:
                    self._log_message(f"Error reading {log_path}: {e}")

        self._log_message("No log files found")
        self._log_message(f"Checked: {meshbot_path}/logs/meshbot.log")
        self._log_message("Logs are created after MeshBot runs with logging enabled")

    def _on_tail_meshbot_log(self, button):
        """Show last 50 lines of log"""
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        log_files = [
            # Current meshing-around log locations
            Path(meshbot_path) / "logs" / "meshbot.log",
            Path(meshbot_path) / "logs" / "messages.log",
            # Legacy/alternative locations
            Path(meshbot_path) / "prior_log.txt",
            Path(meshbot_path) / "prior_debug_log.txt",
        ]

        for log_path in log_files:
            if log_path.exists():
                try:
                    lines = log_path.read_text().strip().split('\n')
                    tail = '\n'.join(lines[-50:])
                    self._set_log_text(f"=== Last 50 lines of {log_path.name} ===\n\n{tail}")
                    return
                except Exception as e:
                    self._log_message(f"Error reading {log_path}: {e}")

        self._log_message("No log files found")
        self._log_message("Enable logging in config.ini: syslog_to_file = True")

    def _on_view_journal(self, button):
        """View systemd journal for mesh_bot service"""
        self._log_message("Fetching journal entries...")

        def fetch_journal():
            try:
                # First try the mesh_bot.service
                result = subprocess.run(
                    ['journalctl', '--no-pager', '-n', '100', '-u', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=30
                )

                if result.stdout.strip() and '-- No entries --' not in result.stdout:
                    GLib.idle_add(self._set_log_text, f"=== Journal (mesh_bot.service) ===\n\n{result.stdout}")
                    return

                # Try grep pattern
                result2 = subprocess.run(
                    ['journalctl', '--no-pager', '-n', '100', '-g', 'mesh_bot'],
                    capture_output=True, text=True, timeout=30
                )

                if result2.stdout.strip() and '-- No entries --' not in result2.stdout:
                    GLib.idle_add(self._set_log_text, f"=== Journal (mesh_bot) ===\n\n{result2.stdout}")
                else:
                    GLib.idle_add(self._log_message, "No journal entries found")
                    GLib.idle_add(self._log_message, "Use View Log or Tail Log to see file-based logs")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Journal error: {e}")

        threading.Thread(target=fetch_journal, daemon=True).start()

    def _on_refresh_map(self, button):
        """Refresh node list from meshtasticd or mesh_bot data"""
        self._log_message("Refreshing node list...")

        def fetch_nodes():
            import socket
            nodes = []

            # Method 1: Check if meshtasticd TCP port is available first
            tcp_available = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                tcp_available = (result == 0)
            except Exception:
                pass

            if tcp_available:
                try:
                    import meshtastic.tcp_interface
                    GLib.idle_add(self._log_message, "Connecting to meshtasticd via TCP...")

                    interface = meshtastic.tcp_interface.TCPInterface('localhost', 4403)

                    # Wait briefly for node info to populate
                    import time
                    time.sleep(2)

                    # Get nodes from interface
                    if hasattr(interface, 'nodes') and interface.nodes:
                        for node_id, node in interface.nodes.items():
                            user = node.get('user', {})
                            name = user.get('longName', user.get('shortName', 'Unknown'))
                            hw_model = user.get('hwModel', 'Unknown')

                            last_heard = node.get('lastHeard', 0)
                            if last_heard:
                                last_seen = datetime.fromtimestamp(last_heard).strftime('%H:%M:%S')
                            else:
                                last_seen = 'Never'

                            nodes.append((str(node_id), name, hw_model, last_seen))

                    interface.close()
                    if nodes:
                        GLib.idle_add(self._log_message, f"Found {len(nodes)} nodes via meshtastic TCP")

                except ImportError:
                    GLib.idle_add(self._log_message, "Meshtastic Python library not installed")
                    GLib.idle_add(self._log_message, "Install with: pip install meshtastic")
                except Exception as e:
                    GLib.idle_add(self._log_message, f"TCP connection error: {e}")
            else:
                GLib.idle_add(self._log_message, "meshtasticd TCP port 4403 not available")

            # Method 2: Try reading mesh_bot data files
            if not nodes:
                meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                data_files = [
                    Path(meshbot_path) / "prior_node_db.json",
                    Path(meshbot_path) / "node_db.json",
                    Path(meshbot_path) / "data" / "nodes.json",
                ]

                found_file = False
                for data_file in data_files:
                    if data_file.exists():
                        found_file = True
                        try:
                            with open(data_file) as f:
                                data = json.load(f)

                            if isinstance(data, dict):
                                for node_id, node in data.items():
                                    if isinstance(node, dict):
                                        name = node.get('longName', node.get('shortName', node.get('name', 'Unknown')))
                                        hw = node.get('hwModel', node.get('type', 'Unknown'))
                                        last = node.get('lastHeard', node.get('last_seen', ''))
                                        if isinstance(last, (int, float)) and last > 0:
                                            last = datetime.fromtimestamp(last).strftime('%H:%M:%S')
                                        nodes.append((str(node_id), name, hw, str(last) if last else 'Unknown'))

                            if nodes:
                                GLib.idle_add(self._log_message, f"Loaded {len(nodes)} nodes from {data_file.name}")
                            break
                        except Exception as e:
                            GLib.idle_add(self._log_message, f"Error reading {data_file.name}: {e}")

                if not found_file:
                    GLib.idle_add(self._log_message, "No mesh_bot node database files found")

            # Show help if no nodes found
            if not nodes:
                GLib.idle_add(self._log_message, "")
                GLib.idle_add(self._log_message, "=== No nodes found ===")
                GLib.idle_add(self._log_message, "To get node data:")
                GLib.idle_add(self._log_message, "  1. Start meshtasticd (systemctl start meshtasticd)")
                GLib.idle_add(self._log_message, "  2. Install meshtastic: pip install meshtastic")
                GLib.idle_add(self._log_message, "  3. Or run MeshBot to create node database")

            # Update UI
            GLib.idle_add(self._update_node_list, nodes)

        threading.Thread(target=fetch_nodes, daemon=True).start()

    def _update_node_list(self, nodes: list):
        """Update the node list store with new data"""
        self._node_store.clear()

        for node_id, name, hw_model, last_seen in nodes:
            self._node_store.append([node_id, name, hw_model, last_seen])

        count = len(nodes)
        self._node_stats_label.set_label(f"Nodes: {count} | Last update: {datetime.now().strftime('%H:%M:%S')}")

    def _on_open_full_map(self, button):
        """Open map view - check which ports are available first"""
        import socket

        # Map URLs to check with their ports (9443 uses HTTPS)
        map_options = [
            ("https://localhost:9443", 9443, "Meshtastic Web"),  # meshtasticd (HTTPS)
            ("http://localhost:8080", 8080, "Meshtastic Alt"),
            ("http://localhost:5000", 5000, "Flask/MeshBot"),
            ("http://localhost:8000", 8000, "MeshMap"),
        ]

        self._log_message("Checking available map servers...")
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

        # Check which ports are actually open
        for url, port, name in map_options:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))
                sock.close()

                if result == 0:
                    self._log_message(f"Found {name} on port {port}")
                    subprocess.Popen(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    self._log_message(f"Opening {url}")
                    return
            except Exception:
                continue

        self._log_message("No map server found on common ports (9443, 8080, 5000, 8000)")
        self._log_message("Make sure meshtasticd or meshbot web is running")

    def _on_export_geojson(self, button):
        """Export nodes as GeoJSON"""
        self._log_message("Exporting GeoJSON...")

        def do_export():
            try:
                # Collect nodes from store
                nodes = []
                model = self._node_store
                iter = model.get_iter_first()
                while iter:
                    node_id = model.get_value(iter, 0)
                    name = model.get_value(iter, 1)
                    hw_model = model.get_value(iter, 2)
                    last_seen = model.get_value(iter, 3)
                    nodes.append({
                        'id': node_id,
                        'name': name,
                        'hw_model': hw_model,
                        'last_seen': last_seen
                    })
                    iter = model.iter_next(iter)

                if not nodes:
                    GLib.idle_add(self._log_message, "No nodes to export. Refresh node list first.")
                    return

                # Try to get coordinates from meshtasticd or mesh_bot data
                meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                node_coords = {}

                # Try reading coordinate data from mesh_bot files
                data_files = [
                    Path(meshbot_path) / "prior_node_db.json",
                    Path(meshbot_path) / "node_db.json",
                ]
                for data_file in data_files:
                    if data_file.exists():
                        try:
                            with open(data_file) as f:
                                data = json.load(f)
                            for nid, node in data.items():
                                if isinstance(node, dict):
                                    pos = node.get('position', {})
                                    lat = pos.get('latitude') or pos.get('lat')
                                    lon = pos.get('longitude') or pos.get('lon')
                                    if lat and lon:
                                        node_coords[str(nid)] = (lon, lat)
                            break
                        except Exception:
                            pass

                # Build GeoJSON
                features = []
                for node in nodes:
                    coords = node_coords.get(node['id'])
                    if coords:
                        feature = {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": list(coords)
                            },
                            "properties": {
                                "id": node['id'],
                                "name": node['name'],
                                "hw_model": node['hw_model'],
                                "last_seen": node['last_seen']
                            }
                        }
                    else:
                        # No coords - create feature with null geometry
                        feature = {
                            "type": "Feature",
                            "geometry": None,
                            "properties": {
                                "id": node['id'],
                                "name": node['name'],
                                "hw_model": node['hw_model'],
                                "last_seen": node['last_seen'],
                                "note": "No coordinates available"
                            }
                        }
                    features.append(feature)

                geojson = {
                    "type": "FeatureCollection",
                    "features": features,
                    "properties": {
                        "exported": datetime.now().isoformat(),
                        "source": "MeshForge"
                    }
                }

                # Save to file
                export_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "exports"
                export_dir.mkdir(parents=True, exist_ok=True)
                filename = f"nodes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.geojson"
                export_path = export_dir / filename

                with open(export_path, 'w') as f:
                    json.dump(geojson, f, indent=2)

                nodes_with_coords = sum(1 for f in features if f['geometry'])
                GLib.idle_add(self._log_message, f"Exported {len(nodes)} nodes ({nodes_with_coords} with coordinates)")
                GLib.idle_add(self._log_message, f"Saved to: {export_path}")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Export error: {e}")

        threading.Thread(target=do_export, daemon=True).start()

    def _on_run_health_check(self, button):
        """Run full health check"""
        self._log_message("Running health check...")
        self._update_health_cards()

    def _on_ping_test(self, button):
        """Run ping test"""
        self._run_network_test("Ping Test", ['ping', '-c', '4', '8.8.8.8'])

    def _on_traceroute_test(self, button):
        """Run traceroute - try multiple tools"""
        self._log_message("Running route trace...")

        def do_trace():
            # Try different traceroute tools in order of preference
            trace_cmds = [
                (['traceroute', '-m', '10', '8.8.8.8'], 'traceroute'),
                (['mtr', '-r', '-c', '3', '8.8.8.8'], 'mtr'),
                (['tracepath', '8.8.8.8'], 'tracepath'),
            ]

            for cmd, name in trace_cmds:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0 or result.stdout:
                        output = result.stdout if result.stdout else result.stderr
                        GLib.idle_add(self._log_message, f"\n=== {name} ===\n{output}")
                        return
                except FileNotFoundError:
                    continue
                except Exception as e:
                    GLib.idle_add(self._log_message, f"{name} error: {e}")
                    continue

            GLib.idle_add(self._log_message, "No trace tools found. Install: sudo apt install traceroute")

        threading.Thread(target=do_trace, daemon=True).start()

    def _on_dns_test(self, button):
        """Run DNS check - try multiple tools"""
        self._log_message("Running DNS Check...")

        def do_dns_test():
            import socket
            test_hosts = ['google.com', 'cloudflare.com', '1.1.1.1']
            results = []

            for host in test_hosts:
                try:
                    ip = socket.gethostbyname(host)
                    results.append(f"  {host} -> {ip}")
                except socket.gaierror as e:
                    results.append(f"  {host} -> FAILED ({e})")

            output = "=== DNS Resolution Test ===\n" + "\n".join(results)

            # Also try to get nameservers from resolv.conf
            try:
                with open('/etc/resolv.conf', 'r') as f:
                    resolv = f.read()
                    nameservers = [line for line in resolv.split('\n') if line.startswith('nameserver')]
                    if nameservers:
                        output += "\n\n=== Nameservers ===\n" + "\n".join(nameservers)
            except Exception:
                pass

            GLib.idle_add(self._log_message, output)

        threading.Thread(target=do_dns_test, daemon=True).start()

    def _on_port_scan(self, button):
        """Run port scan on localhost"""
        self._run_network_test("Port Scan", ['ss', '-tuln'])

    def _run_network_test(self, name: str, cmd: list):
        """Run a network test and show output"""
        self._log_message(f"Running {name}...")

        def do_test():
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                output = result.stdout if result.stdout else result.stderr
                GLib.idle_add(self._log_message, f"\n=== {name} ===\n{output}")
            except subprocess.TimeoutExpired:
                GLib.idle_add(self._log_message, f"{name}: Timed out")
            except Exception as e:
                GLib.idle_add(self._log_message, f"{name} error: {e}")

        threading.Thread(target=do_test, daemon=True).start()

    def _on_generate_report(self, button):
        """Generate diagnostic report"""
        self._log_message("Generating diagnostic report...")

        def do_report():
            import socket
            from datetime import datetime

            report_lines = []
            report_lines.append("=" * 60)
            report_lines.append("MeshForge Diagnostic Report")
            report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report_lines.append("=" * 60)

            # System info
            report_lines.append("\n[System Info]")
            try:
                uname = subprocess.run(['uname', '-a'], capture_output=True, text=True, timeout=5)
                report_lines.append(f"  {uname.stdout.strip()}")
            except Exception:
                pass

            # MeshBot status
            report_lines.append("\n[MeshBot Status]")
            meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
            if Path(meshbot_path).exists():
                report_lines.append(f"  Installed: Yes ({meshbot_path})")
                venv = Path(meshbot_path) / "venv"
                report_lines.append(f"  Venv: {'Yes' if venv.exists() else 'No'}")
            else:
                report_lines.append("  Installed: No")

            # Check if running
            try:
                result = subprocess.run(['pgrep', '-f', 'mesh_bot.py'], capture_output=True, timeout=5)
                report_lines.append(f"  Running: {'Yes' if result.returncode == 0 else 'No'}")
            except Exception:
                pass

            # Network
            report_lines.append("\n[Network]")
            try:
                ip = socket.gethostbyname('google.com')
                report_lines.append(f"  DNS: Working ({ip})")
            except Exception:
                report_lines.append("  DNS: Failed")

            # Meshtasticd
            report_lines.append("\n[Meshtasticd]")
            try:
                result = subprocess.run(['systemctl', 'is-active', 'meshtasticd'], capture_output=True, text=True, timeout=5)
                report_lines.append(f"  Service: {result.stdout.strip()}")
            except Exception:
                report_lines.append("  Service: Unknown")

            # Check TCP port
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                report_lines.append(f"  TCP 4403: {'Open' if result == 0 else 'Closed'}")
            except Exception:
                pass

            report_lines.append("\n" + "=" * 60)

            # Display report
            report_text = "\n".join(report_lines)
            GLib.idle_add(self._set_log_text, report_text)
            GLib.idle_add(self._log_message, "Report generated")

        threading.Thread(target=do_report, daemon=True).start()

    def _on_open_reports_folder(self, button):
        """Open reports folder"""
        reports_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "diagnostics"
        reports_dir.mkdir(parents=True, exist_ok=True)
        self._open_folder(str(reports_dir))

    def _on_log_source_changed(self, combo):
        """Handle log source change"""
        source = combo.get_active_id()
        self._log_message(f"Switched to {source} log source")

    def _on_refresh_log(self, button):
        """Refresh current log based on selected source"""
        source = self._log_source.get_active_id()
        self._log_message(f"Refreshing {source} log...")

        def fetch_log():
            content = ""
            try:
                if source == "meshbot":
                    # Read MeshBot log
                    meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                    log_file = Path(meshbot_path) / "logs" / "meshbot.log"
                    if log_file.exists():
                        content = log_file.read_text()[-10000:]  # Last 10KB
                    else:
                        content = f"MeshBot log not found at {log_file}"

                elif source == "diagnostics":
                    # Get recent diagnostic events
                    if HAS_DIAGNOSTICS:
                        diag = get_diagnostics()
                        events = diag.get_recent_events(50)
                        lines = []
                        for e in events:
                            lines.append(f"[{e.timestamp.strftime('%H:%M:%S')}] {e.category.value}: {e.message}")
                        content = "\n".join(lines) if lines else "No diagnostic events"
                    else:
                        content = "Diagnostics module not available"

                elif source == "system":
                    # Read system log for meshforge/meshtastic
                    result = subprocess.run(
                        ['journalctl', '-u', 'meshtasticd', '-n', '100', '--no-pager'],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    content = result.stdout if result.returncode == 0 else "No system log available"

            except Exception as e:
                content = f"Error fetching log: {e}"

            GLib.idle_add(self._set_log_text, content)

        threading.Thread(target=fetch_log, daemon=True).start()

    def _on_clear_log(self, button):
        """Clear log output"""
        buffer = self._log_text.get_buffer()
        buffer.set_text("")

    def _log_message(self, message: str):
        """Add message to log output"""
        buffer = self._log_text.get_buffer()
        end_iter = buffer.get_end_iter()
        timestamp = datetime.now().strftime("%H:%M:%S")
        buffer.insert(end_iter, f"[{timestamp}] {message}\n")

        if self._auto_scroll.get_active():
            end_iter = buffer.get_end_iter()
            self._log_text.scroll_to_iter(end_iter, 0, False, 0, 0)

    def _set_log_text(self, text: str):
        """Set log text (replace all)"""
        buffer = self._log_text.get_buffer()
        buffer.set_text(text)

    def _open_folder(self, path: str):
        """Open folder in file manager"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        try:
            subprocess.Popen(
                ['sudo', '-u', real_user, 'xdg-open', path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            self._log_message(f"Error opening folder: {e}")

    def _open_url(self, url: str):
        """Open URL in browser - runs in thread to avoid blocking GTK"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

        def do_open():
            # Try multiple methods to open browser
            browsers = [
                ['sudo', '-u', real_user, 'xdg-open', url],
                ['sudo', '-u', real_user, 'firefox', url],
                ['sudo', '-u', real_user, 'chromium-browser', url],
                ['sudo', '-u', real_user, 'chromium', url],
                ['sudo', '-u', real_user, 'google-chrome', url],
            ]

            for cmd in browsers:
                try:
                    # Use Popen with full detachment
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                        env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
                    )
                    GLib.idle_add(self._log_message, f"Opening {url}")
                    GLib.idle_add(self._log_message, f"(Note: HTTPS uses self-signed cert - accept in browser)")
                    return
                except FileNotFoundError:
                    continue
                except Exception as e:
                    GLib.idle_add(self._log_message, f"Browser error: {e}")
                    continue

            GLib.idle_add(self._log_message, f"Could not open browser for {url}")
            GLib.idle_add(self._log_message, "Copy URL and open manually in browser")

        threading.Thread(target=do_open, daemon=True).start()

    def cleanup(self):
        """Clean up resources"""
        if self._log_timer_id:
            GLib.source_remove(self._log_timer_id)
