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

        config_frame.set_child(config_box)
        box.append(config_frame)

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
        self._update_health_cards()
        return False  # Don't repeat

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
        """Start MeshBot"""
        meshbot_path = self._path_entry.get_text().strip()
        script_path = Path(meshbot_path) / "mesh_bot.py"

        if not script_path.exists():
            self._log_message("Error: mesh_bot.py not found")
            return

        self._log_message("Starting MeshBot...")

        def do_start():
            try:
                launch_script = Path(meshbot_path) / "launch.sh"
                if launch_script.exists():
                    cmd = ['bash', str(launch_script)]
                else:
                    cmd = ['python3', str(script_path)]

                process = subprocess.Popen(
                    cmd,
                    cwd=meshbot_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )

                import time
                time.sleep(2)

                if process.poll() is None:
                    GLib.idle_add(self._log_message, "MeshBot started successfully")
                    GLib.idle_add(self._check_meshbot_status)
                else:
                    GLib.idle_add(self._log_message, "MeshBot failed to start")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Start error: {e}")

        threading.Thread(target=do_start, daemon=True).start()

    def _on_stop_bot(self, button):
        """Stop MeshBot"""
        self._log_message("Stopping MeshBot...")

        def do_stop():
            try:
                subprocess.run(['pkill', '-f', 'mesh_bot.py'], timeout=10)
                import time
                time.sleep(1)
                GLib.idle_add(self._log_message, "MeshBot stopped")
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

                if result.returncode == 0:
                    GLib.idle_add(self._log_message, "Installation complete!")
                    GLib.idle_add(self._check_meshbot_status)
                else:
                    GLib.idle_add(self._log_message, f"Install failed: {result.stderr}")

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
                subprocess.run(['cp', str(template), str(config_path)])
            else:
                self._log_message("No config file found")
                return

        # Open in default editor
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
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

    def _on_refresh_map(self, button):
        """Refresh node list"""
        self._log_message("Refreshing node list...")
        # TODO: Integrate with actual node tracking
        self._node_store.clear()
        self._node_stats_label.set_label(f"Nodes: 0 | Last update: {datetime.now().strftime('%H:%M:%S')}")

    def _on_open_full_map(self, button):
        """Open full map view"""
        self._log_message("Opening map in browser...")
        # TODO: Launch map view

    def _on_export_geojson(self, button):
        """Export nodes as GeoJSON"""
        self._log_message("Exporting GeoJSON...")
        # TODO: Export functionality

    def _on_run_health_check(self, button):
        """Run full health check"""
        self._log_message("Running health check...")
        self._update_health_cards()

    def _on_ping_test(self, button):
        """Run ping test"""
        self._run_network_test("Ping Test", ['ping', '-c', '4', '8.8.8.8'])

    def _on_traceroute_test(self, button):
        """Run traceroute"""
        self._run_network_test("Traceroute", ['traceroute', '-m', '10', '8.8.8.8'])

    def _on_dns_test(self, button):
        """Run DNS check"""
        self._run_network_test("DNS Check", ['nslookup', 'google.com'])

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
        # TODO: Generate comprehensive report

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
        """Refresh current log"""
        source = self._log_source.get_active_id()
        self._log_message(f"Refreshing {source} log...")
        # TODO: Implement per-source refresh

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

    def cleanup(self):
        """Clean up resources"""
        if self._log_timer_id:
            GLib.source_remove(self._log_timer_id)
