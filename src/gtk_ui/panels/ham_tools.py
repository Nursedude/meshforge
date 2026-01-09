"""
Ham Tools Panel - Consolidated amateur radio tools

Combines:
- HamClock (space weather, propagation)
- Propagation (band conditions, forecasts)
- Callsign Lookup (QRZ, callook)

With shared resizable output at bottom.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import subprocess
import urllib.request
import urllib.error
import json
import os
from pathlib import Path
from datetime import datetime

# Import UI standards
try:
    from utils.gtk_helpers import (
        UI, create_panel_header, create_standard_frame,
        ResizableLogViewer, StatusIndicator
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


class HamToolsPanel(Gtk.Box):
    """
    Consolidated amateur radio tools panel.

    Provides sub-tabs for HamClock, Propagation, and Callsign Lookup
    with a shared resizable output at the bottom.
    """

    SETTINGS_DEFAULTS = {
        "hamclock_url": "http://localhost",
        "hamclock_api_port": 8082,
        "hamclock_live_port": 8081,
        "qrz_username": "",
        "output_position": 350,
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
            self._settings_mgr = SettingsManager("ham_tools", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            self._settings = self.SETTINGS_DEFAULTS.copy()

        self._build_ui()

        # Initial checks
        GLib.timeout_add(500, self._check_hamclock_status)

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
                "Ham Tools",
                "HamClock, Propagation, and Callsign Lookup",
                "audio-speakers-symbolic"
            )
        else:
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            title = Gtk.Label(label="Ham Tools")
            title.add_css_class("title-1")
            title.set_xalign(0)
            header.append(title)

        self.append(header)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Main paned layout
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._paned.set_wide_handle(True)
        self._paned.set_vexpand(True)

        # Top: Notebook with tabs
        self._notebook = Gtk.Notebook()
        self._notebook.set_tab_pos(Gtk.PositionType.TOP)

        # Add tabs
        self._add_hamclock_tab()
        self._add_propagation_tab()
        self._add_callsign_tab()

        self._paned.set_start_child(self._notebook)

        # Bottom: Output viewer
        self._build_output_viewer()
        self._paned.set_end_child(self._output_frame)

        # Restore position
        self._paned.set_position(self._settings.get("output_position", 350))
        self._paned.connect("notify::position", self._on_paned_moved)

        self.append(self._paned)

    def _build_output_viewer(self):
        """Build the shared output viewer"""
        self._output_frame = Gtk.Frame()
        self._output_frame.set_label("Data Output")

        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_margin_start(10)
        controls.set_margin_end(10)
        controls.set_margin_top(5)
        controls.set_margin_bottom(5)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls.append(spacer)

        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda b: self._clear_output())
        controls.append(clear_btn)

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.connect("clicked", self._on_copy_output)
        controls.append(copy_btn)

        output_box.append(controls)
        output_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Text view
        self._output_text = Gtk.TextView()
        self._output_text.set_editable(False)
        self._output_text.set_monospace(True)
        self._output_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        output_scroll = Gtk.ScrolledWindow()
        output_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        output_scroll.set_vexpand(True)
        output_scroll.set_min_content_height(100)
        output_scroll.set_child(self._output_text)

        output_box.append(output_scroll)
        self._output_frame.set_child(output_box)

    def _on_paned_moved(self, paned, param):
        """Save paned position"""
        self._settings["output_position"] = paned.get_position()
        self._save_settings()

    # =========================================================================
    # HamClock Tab
    # =========================================================================

    def _add_hamclock_tab(self):
        """Add HamClock integration tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Connection section
        conn_frame = Gtk.Frame()
        conn_frame.set_label("HamClock Connection")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        conn_box.set_margin_start(15)
        conn_box.set_margin_end(15)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        self._hc_status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        self._hc_status_icon.set_pixel_size(24)
        status_row.append(self._hc_status_icon)

        self._hc_status_label = Gtk.Label(label="Not connected")
        self._hc_status_label.set_xalign(0)
        status_row.append(self._hc_status_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        connect_btn = Gtk.Button(label="Connect")
        connect_btn.add_css_class("suggested-action")
        connect_btn.connect("clicked", self._on_connect_hamclock)
        status_row.append(connect_btn)

        conn_box.append(status_row)

        # URL entry
        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        url_row.append(Gtk.Label(label="URL:"))
        self._hc_url_entry = Gtk.Entry()
        self._hc_url_entry.set_text(self._settings.get("hamclock_url", "http://localhost"))
        self._hc_url_entry.set_hexpand(True)
        self._hc_url_entry.set_placeholder_text("http://hamclock.local")
        url_row.append(self._hc_url_entry)

        url_row.append(Gtk.Label(label="API Port:"))
        self._hc_api_port = Gtk.SpinButton()
        self._hc_api_port.set_range(1, 65535)
        self._hc_api_port.set_value(self._settings.get("hamclock_api_port", 8082))
        self._hc_api_port.set_increments(1, 10)
        url_row.append(self._hc_api_port)

        conn_box.append(url_row)

        conn_frame.set_child(conn_box)
        box.append(conn_frame)

        # Space Weather section
        wx_frame = Gtk.Frame()
        wx_frame.set_label("Space Weather")
        wx_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wx_box.set_margin_start(15)
        wx_box.set_margin_end(15)
        wx_box.set_margin_top(10)
        wx_box.set_margin_bottom(10)

        # Weather data grid
        wx_grid = Gtk.Grid()
        wx_grid.set_column_spacing(20)
        wx_grid.set_row_spacing(5)

        self._wx_labels = {}
        wx_items = [
            ("sfi", "Solar Flux (SFI)"),
            ("kp", "Kp Index"),
            ("a", "A Index"),
            ("xray", "X-Ray Flux"),
            ("sunspots", "Sunspot Number"),
        ]

        for i, (key, label) in enumerate(wx_items):
            name_lbl = Gtk.Label(label=f"{label}:")
            name_lbl.set_xalign(0)
            wx_grid.attach(name_lbl, 0, i, 1, 1)

            value_lbl = Gtk.Label(label="--")
            value_lbl.set_xalign(0)
            value_lbl.add_css_class("heading")
            wx_grid.attach(value_lbl, 1, i, 1, 1)
            self._wx_labels[key] = value_lbl

        wx_box.append(wx_grid)

        # Fetch buttons
        fetch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        fetch_hc_btn = Gtk.Button(label="Fetch from HamClock")
        fetch_hc_btn.add_css_class("suggested-action")
        fetch_hc_btn.connect("clicked", self._on_fetch_hamclock_wx)
        fetch_row.append(fetch_hc_btn)

        fetch_noaa_btn = Gtk.Button(label="Fetch from NOAA")
        fetch_noaa_btn.connect("clicked", self._on_fetch_noaa_wx)
        fetch_row.append(fetch_noaa_btn)

        wx_box.append(fetch_row)

        wx_frame.set_child(wx_box)
        box.append(wx_frame)

        # Service section
        svc_frame = Gtk.Frame()
        svc_frame.set_label("HamClock Service")
        svc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        svc_box.set_margin_start(15)
        svc_box.set_margin_end(15)
        svc_box.set_margin_top(10)
        svc_box.set_margin_bottom(10)

        start_btn = Gtk.Button(label="Start Service")
        start_btn.connect("clicked", lambda b: self._hamclock_service("start"))
        svc_box.append(start_btn)

        stop_btn = Gtk.Button(label="Stop Service")
        stop_btn.connect("clicked", lambda b: self._hamclock_service("stop"))
        svc_box.append(stop_btn)

        open_btn = Gtk.Button(label="Open in Browser")
        open_btn.connect("clicked", self._on_open_hamclock_browser)
        svc_box.append(open_btn)

        install_btn = Gtk.Button(label="Install HamClock")
        install_btn.connect("clicked", self._on_install_hamclock)
        svc_box.append(install_btn)

        svc_frame.set_child(svc_box)
        box.append(svc_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("weather-clear-symbolic"))
        tab_box.append(Gtk.Label(label="HamClock"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Propagation Tab
    # =========================================================================

    def _add_propagation_tab(self):
        """Add Propagation predictions tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Band Conditions
        bands_frame = Gtk.Frame()
        bands_frame.set_label("HF Band Conditions")
        bands_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bands_box.set_margin_start(15)
        bands_box.set_margin_end(15)
        bands_box.set_margin_top(10)
        bands_box.set_margin_bottom(10)

        # Band grid
        bands_grid = Gtk.Grid()
        bands_grid.set_column_spacing(20)
        bands_grid.set_row_spacing(5)

        # Headers
        for i, header in enumerate(["Band", "Day", "Night"]):
            lbl = Gtk.Label(label=header)
            lbl.add_css_class("heading")
            bands_grid.attach(lbl, i, 0, 1, 1)

        self._band_labels = {}
        bands = ["80m-40m", "30m-20m", "17m-15m", "12m-10m"]

        for row, band in enumerate(bands, start=1):
            band_lbl = Gtk.Label(label=band)
            band_lbl.set_xalign(0)
            bands_grid.attach(band_lbl, 0, row, 1, 1)

            day_lbl = Gtk.Label(label="--")
            bands_grid.attach(day_lbl, 1, row, 1, 1)

            night_lbl = Gtk.Label(label="--")
            bands_grid.attach(night_lbl, 2, row, 1, 1)

            self._band_labels[band] = {"day": day_lbl, "night": night_lbl}

        bands_box.append(bands_grid)

        refresh_bands_btn = Gtk.Button(label="Refresh Band Conditions")
        refresh_bands_btn.connect("clicked", self._on_refresh_bands)
        bands_box.append(refresh_bands_btn)

        bands_frame.set_child(bands_box)
        box.append(bands_frame)

        # External Resources
        resources_frame = Gtk.Frame()
        resources_frame.set_label("Propagation Resources")
        resources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        resources_box.set_margin_start(15)
        resources_box.set_margin_end(15)
        resources_box.set_margin_top(10)
        resources_box.set_margin_bottom(10)

        resources = [
            ("VOACAP Online", "https://www.voacap.com/hf/", "Detailed HF propagation predictions"),
            ("Solar Ham", "https://www.solarham.net/", "Real-time solar activity"),
            ("DX Heat", "https://dxheat.com/", "DX cluster and propagation"),
            ("PSK Reporter", "https://pskreporter.info/pskmap.html", "Live propagation map"),
        ]

        for name, url, desc in resources:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            btn = Gtk.Button(label=name)
            btn.connect("clicked", lambda b, u=url: self._open_url(u))
            btn.set_tooltip_text(url)
            row.append(btn)

            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.add_css_class("dim-label")
            desc_lbl.set_xalign(0)
            row.append(desc_lbl)

            resources_box.append(row)

        resources_frame.set_child(resources_box)
        box.append(resources_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("network-transmit-symbolic"))
        tab_box.append(Gtk.Label(label="Propagation"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Callsign Tab
    # =========================================================================

    def _add_callsign_tab(self):
        """Add Callsign Lookup tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Lookup section
        lookup_frame = Gtk.Frame()
        lookup_frame.set_label("Callsign Lookup")
        lookup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        lookup_box.set_margin_start(15)
        lookup_box.set_margin_end(15)
        lookup_box.set_margin_top(10)
        lookup_box.set_margin_bottom(10)

        # Search row
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        search_row.append(Gtk.Label(label="Callsign:"))

        self._callsign_entry = Gtk.Entry()
        self._callsign_entry.set_placeholder_text("W1AW")
        self._callsign_entry.set_width_chars(12)
        self._callsign_entry.connect("activate", self._on_lookup_callsign)
        search_row.append(self._callsign_entry)

        lookup_btn = Gtk.Button(label="Lookup")
        lookup_btn.add_css_class("suggested-action")
        lookup_btn.connect("clicked", self._on_lookup_callsign)
        search_row.append(lookup_btn)

        # Source selector
        search_row.append(Gtk.Label(label="Source:"))
        self._lookup_source = Gtk.ComboBoxText()
        self._lookup_source.append("callook", "Callook.info (FCC)")
        self._lookup_source.append("hamqth", "HamQTH")
        self._lookup_source.append("qrz", "QRZ.com")
        self._lookup_source.set_active_id("callook")
        search_row.append(self._lookup_source)

        lookup_box.append(search_row)

        # Results area
        self._callsign_results = Gtk.TextView()
        self._callsign_results.set_editable(False)
        self._callsign_results.set_wrap_mode(Gtk.WrapMode.WORD)

        results_scroll = Gtk.ScrolledWindow()
        results_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        results_scroll.set_min_content_height(150)
        results_scroll.set_child(self._callsign_results)

        lookup_box.append(results_scroll)

        lookup_frame.set_child(lookup_box)
        box.append(lookup_frame)

        # Recent lookups
        recent_frame = Gtk.Frame()
        recent_frame.set_label("Recent Lookups")
        recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        recent_box.set_margin_start(15)
        recent_box.set_margin_end(15)
        recent_box.set_margin_top(10)
        recent_box.set_margin_bottom(10)

        self._recent_store = Gtk.ListStore(str, str, str)  # callsign, name, location

        recent_tree = Gtk.TreeView(model=self._recent_store)
        for i, title in enumerate(["Callsign", "Name", "Location"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            recent_tree.append_column(column)

        recent_scroll = Gtk.ScrolledWindow()
        recent_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        recent_scroll.set_min_content_height(100)
        recent_scroll.set_child(recent_tree)

        recent_box.append(recent_scroll)

        recent_frame.set_child(recent_box)
        box.append(recent_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("system-users-symbolic"))
        tab_box.append(Gtk.Label(label="Callsign"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _check_hamclock_status(self):
        """Check HamClock connection status"""
        url = self._settings.get("hamclock_url", "http://localhost")
        port = self._settings.get("hamclock_api_port", 8082)

        def check():
            try:
                test_url = f"{url}:{port}/get_sys.txt"
                req = urllib.request.Request(test_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=3) as response:
                    GLib.idle_add(self._update_hc_status, True, "Connected")
            except Exception:
                GLib.idle_add(self._update_hc_status, False, "Not connected")

        threading.Thread(target=check, daemon=True).start()
        return False

    def _update_hc_status(self, connected: bool, message: str):
        """Update HamClock status display"""
        if connected:
            self._hc_status_icon.set_from_icon_name("emblem-default-symbolic")
            self._hc_status_label.set_label(message)
        else:
            self._hc_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._hc_status_label.set_label(message)

    def _on_connect_hamclock(self, button):
        """Connect to HamClock"""
        url = self._hc_url_entry.get_text().strip()
        port = int(self._hc_api_port.get_value())

        self._settings["hamclock_url"] = url
        self._settings["hamclock_api_port"] = port
        self._save_settings()

        self._output_message(f"Connecting to HamClock at {url}:{port}...")
        self._check_hamclock_status()

    def _on_fetch_hamclock_wx(self, button):
        """Fetch space weather from HamClock"""
        url = self._settings.get("hamclock_url", "http://localhost")
        port = self._settings.get("hamclock_api_port", 8082)

        self._output_message("Fetching space weather from HamClock...")

        def fetch():
            try:
                api_url = f"{url}:{port}/get_spacewx.txt"
                req = urllib.request.Request(api_url)
                req.add_header('User-Agent', 'MeshForge/1.0')

                with urllib.request.urlopen(req, timeout=10) as response:
                    data = response.read().decode('utf-8')
                    GLib.idle_add(self._parse_hamclock_wx, data)

            except Exception as e:
                GLib.idle_add(self._output_message, f"Error: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_hamclock_wx(self, data: str):
        """Parse HamClock space weather response"""
        self._output_message(f"Raw data:\n{data}")

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().lower()
                value = value.strip()

                if key == 'sfi' and 'sfi' in self._wx_labels:
                    self._wx_labels['sfi'].set_label(value)
                elif key == 'kp' and 'kp' in self._wx_labels:
                    self._wx_labels['kp'].set_label(value)
                elif key == 'a' and 'a' in self._wx_labels:
                    self._wx_labels['a'].set_label(value)
                elif key == 'xray' and 'xray' in self._wx_labels:
                    self._wx_labels['xray'].set_label(value)
                elif key == 'ssn' and 'sunspots' in self._wx_labels:
                    self._wx_labels['sunspots'].set_label(value)

    def _on_fetch_noaa_wx(self, button):
        """Fetch space weather from NOAA"""
        self._output_message("Fetching space weather from NOAA...")

        def fetch():
            try:
                url = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'MeshForge/1.0')

                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode('utf-8'))

                if data:
                    latest = data[-1]
                    GLib.idle_add(self._apply_noaa_wx, latest)

            except Exception as e:
                GLib.idle_add(self._output_message, f"Error: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _apply_noaa_wx(self, data: dict):
        """Apply NOAA weather data"""
        self._output_message(f"NOAA data: {json.dumps(data, indent=2)}")

        if 'f10.7' in data:
            self._wx_labels['sfi'].set_label(str(data['f10.7']))
        if 'ssn' in data:
            self._wx_labels['sunspots'].set_label(str(data['ssn']))

    def _hamclock_service(self, action: str):
        """Control HamClock service"""
        self._output_message(f"{action.capitalize()}ing HamClock service...")

        def do_action():
            found_services = []

            # Search for service files directly in systemd directories
            search_dirs = ['/lib/systemd/system', '/etc/systemd/system', '/usr/lib/systemd/system']
            for sdir in search_dirs:
                try:
                    find_result = subprocess.run(
                        ['find', sdir, '-name', '*amclock*.service', '-o', '-name', '*HamClock*.service'],
                        capture_output=True, text=True, timeout=10
                    )
                    for line in find_result.stdout.strip().split('\n'):
                        if line and line.endswith('.service'):
                            svc_name = os.path.basename(line).replace('.service', '')
                            if svc_name not in found_services:
                                found_services.append(svc_name)
                except Exception:
                    continue

            # Also check common service names
            common_names = ['hamclock', 'hamclock-web', 'HamClock', 'hamclock-systemd']
            for name in common_names:
                if name not in found_services:
                    # Check if service exists
                    check = subprocess.run(
                        ['systemctl', 'cat', name],
                        capture_output=True, text=True, timeout=5
                    )
                    if check.returncode == 0:
                        found_services.append(name)

            if not found_services:
                GLib.idle_add(self._output_message, "No HamClock service found. Try Install HamClock first.")
                return

            # Try to perform the action on found services
            for name in found_services:
                try:
                    GLib.idle_add(self._output_message, f"Trying {name}...")
                    result = subprocess.run(
                        ['sudo', 'systemctl', action, name],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(self._output_message, f"Service {name} {action}ed successfully")
                        GLib.idle_add(self._check_hamclock_status)
                        return
                    else:
                        if result.stderr:
                            GLib.idle_add(self._output_message, f"  Error: {result.stderr.strip()}")
                except Exception as e:
                    GLib.idle_add(self._output_message, f"  Exception: {e}")
                    continue

            GLib.idle_add(self._output_message, f"Could not {action} any HamClock service")

        threading.Thread(target=do_action, daemon=True).start()

    def _on_open_hamclock_browser(self, button):
        """Open HamClock in browser"""
        url = self._settings.get("hamclock_url", "http://localhost")
        port = self._settings.get("hamclock_live_port", 8081)
        live_url = f"{url}:{port}/live.html"
        self._open_url(live_url)

    def _on_install_hamclock(self, button):
        """Install hamclock-web package for headless operation.

        Downloads .deb from GitHub releases (hamclock-systemd project).
        https://github.com/pa28/hamclock-systemd
        """
        self._output_message("Starting HamClock installation...")
        button.set_sensitive(False)

        def do_install():
            import tempfile
            errors = []

            try:
                # Check if already installed
                result = subprocess.run(
                    ['dpkg', '-l', 'hamclock-web'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and 'ii' in result.stdout:
                    GLib.idle_add(self._install_complete, True, "hamclock-web already installed", button)
                    return

                # Also check hamclock-systemd
                result2 = subprocess.run(
                    ['dpkg', '-l', 'hamclock-systemd'],
                    capture_output=True, text=True, timeout=10
                )
                if result2.returncode == 0 and 'ii' in result2.stdout:
                    GLib.idle_add(self._install_complete, True, "hamclock-systemd already installed", button)
                    return

                # Detect architecture
                arch_result = subprocess.run(['dpkg', '--print-architecture'], capture_output=True, text=True, timeout=5)
                arch = arch_result.stdout.strip() if arch_result.returncode == 0 else 'armhf'
                GLib.idle_add(self._output_message, f"Detected architecture: {arch}")

                # Version and package selection
                version = "2.65.5"
                if arch in ['armhf', 'arm64', 'aarch64']:
                    # arm64 Pi can run armhf packages
                    if arch in ['arm64', 'aarch64']:
                        GLib.idle_add(self._output_message, "Enabling armhf multiarch for arm64...")
                        subprocess.run(['sudo', 'dpkg', '--add-architecture', 'armhf'],
                                       capture_output=True, timeout=30)
                        subprocess.run(['sudo', 'apt', 'update'], capture_output=True, timeout=120)

                    # Use hamclock-systemd for Pi (includes web service)
                    deb_url = f"https://github.com/pa28/hamclock-systemd/releases/download/{version}/hamclock-systemd_{version}_armhf.deb"
                    pkg_name = "hamclock-systemd"
                elif arch == 'amd64':
                    deb_url = f"https://github.com/pa28/hamclock-systemd/releases/download/{version}/hamclock_{version}_amd64.deb"
                    pkg_name = "hamclock"
                else:
                    GLib.idle_add(self._install_complete, False, f"Unsupported architecture: {arch}", button)
                    return

                # Download .deb file
                GLib.idle_add(self._output_message, f"Downloading {pkg_name}...")

                with tempfile.TemporaryDirectory() as tmpdir:
                    deb_path = os.path.join(tmpdir, f"{pkg_name}.deb")

                    # Download
                    wget_result = subprocess.run(
                        ['wget', '-q', '-O', deb_path, deb_url],
                        capture_output=True, text=True, timeout=120
                    )

                    if wget_result.returncode != 0:
                        # Try curl
                        curl_result = subprocess.run(
                            ['curl', '-sL', '-o', deb_path, deb_url],
                            capture_output=True, text=True, timeout=120
                        )
                        if curl_result.returncode != 0:
                            GLib.idle_add(self._install_complete, False, "Download failed", button)
                            return

                    # Install with apt
                    GLib.idle_add(self._output_message, "Installing package...")

                    install_cmd = ['sudo', 'apt', 'install', '-y', '-f', deb_path]
                    install_result = subprocess.run(install_cmd, capture_output=True, text=True, timeout=300)

                    if install_result.returncode != 0:
                        # Try dpkg + fix dependencies
                        dpkg_cmd = ['sudo', 'dpkg', '-i', deb_path]
                        subprocess.run(dpkg_cmd, capture_output=True, text=True, timeout=120)

                        fix_cmd = ['sudo', 'apt-get', '-f', 'install', '-y']
                        subprocess.run(fix_cmd, capture_output=True, timeout=120)

                    # Enable and start service - find service files from installed package
                    GLib.idle_add(self._output_message, "Looking for installed services...")
                    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], capture_output=True, timeout=30)

                    # Check what files the package installed
                    dpkg_files = subprocess.run(
                        ['dpkg', '-L', pkg_name],
                        capture_output=True, text=True, timeout=10
                    )
                    service_files = [f for f in dpkg_files.stdout.split('\n') if f.endswith('.service')]

                    if service_files:
                        for sf in service_files:
                            GLib.idle_add(self._output_message, f"  Found: {sf}")

                    # Also search directly in systemd directories
                    active_service = None
                    search_dirs = ['/lib/systemd/system', '/etc/systemd/system', '/usr/lib/systemd/system']
                    found_services = []

                    for sdir in search_dirs:
                        try:
                            find_result = subprocess.run(
                                ['find', sdir, '-name', '*amclock*.service', '-o', '-name', '*HamClock*.service'],
                                capture_output=True, text=True, timeout=10
                            )
                            for line in find_result.stdout.strip().split('\n'):
                                if line and line.endswith('.service'):
                                    svc_name = os.path.basename(line).replace('.service', '')
                                    if svc_name not in found_services:
                                        found_services.append(svc_name)
                                        GLib.idle_add(self._output_message, f"  Service file: {line}")
                        except Exception:
                            continue

                    # Try to enable/start any found services
                    for svc_name in found_services:
                        GLib.idle_add(self._output_message, f"Trying to start {svc_name}...")
                        subprocess.run(['sudo', 'systemctl', 'enable', svc_name], capture_output=True, timeout=30)
                        start_result = subprocess.run(['sudo', 'systemctl', 'start', svc_name], capture_output=True, text=True, timeout=30)

                        if start_result.returncode == 0:
                            check = subprocess.run(['systemctl', 'is-active', svc_name], capture_output=True, text=True, timeout=10)
                            if check.stdout.strip() == 'active':
                                active_service = svc_name
                                break
                        else:
                            if start_result.stderr:
                                GLib.idle_add(self._output_message, f"    Error: {start_result.stderr.strip()}")

                    # Also try common service names even if not found in files
                    if not active_service:
                        common_names = ['hamclock', 'hamclock-web', 'HamClock', 'hamclock-systemd']
                        for svc_name in common_names:
                            if svc_name in found_services:
                                continue
                            try:
                                start_result = subprocess.run(
                                    ['sudo', 'systemctl', 'start', svc_name],
                                    capture_output=True, text=True, timeout=30
                                )
                                if start_result.returncode == 0:
                                    check = subprocess.run(['systemctl', 'is-active', svc_name], capture_output=True, text=True, timeout=10)
                                    if check.stdout.strip() == 'active':
                                        active_service = svc_name
                                        break
                            except Exception:
                                continue

                    if active_service:
                        GLib.idle_add(self._install_complete, True, f"HamClock installed and running ({active_service})!", button)
                    else:
                        # Check if HamClock binary exists - maybe needs manual setup
                        hamclock_bin = subprocess.run(['which', 'hamclock'], capture_output=True, text=True, timeout=5)
                        if hamclock_bin.returncode == 0:
                            GLib.idle_add(self._output_message, f"HamClock binary at: {hamclock_bin.stdout.strip()}")
                            GLib.idle_add(self._output_message, "Try: sudo hamclock -o 4 (headless web server mode)")
                        GLib.idle_add(self._install_complete, True, "HamClock installed (service setup needed)", button)

            except subprocess.TimeoutExpired:
                GLib.idle_add(self._install_complete, False, "Installation timed out", button)
            except Exception as e:
                GLib.idle_add(self._install_complete, False, str(e), button)

        threading.Thread(target=do_install, daemon=True).start()

    def _install_complete(self, success: bool, message: str, button):
        """Handle installation completion"""
        button.set_sensitive(True)
        self._output_message(message)

        if success:
            # Update URL to localhost
            self._hc_url_entry.set_text("http://localhost")
            # Check status
            GLib.timeout_add(2000, self._check_hamclock_status)

    def _on_refresh_bands(self, button):
        """Refresh band conditions from N0NBH solar data"""
        self._output_message("Fetching band conditions...")

        def fetch():
            try:
                import xml.etree.ElementTree as ET

                # Fetch N0NBH solar/band data
                url = "https://www.hamqsl.com/solarxml.php"
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'MeshForge/1.0')

                with urllib.request.urlopen(req, timeout=10) as response:
                    data = response.read().decode('utf-8')

                # Parse XML
                root = ET.fromstring(data)
                solar_data = root.find('.//solardata')

                if solar_data is not None:
                    # Extract solar indices
                    sfi = solar_data.findtext('solarflux', 'N/A')
                    a_index = solar_data.findtext('aindex', 'N/A')
                    k_index = solar_data.findtext('kindex', 'N/A')
                    sunspots = solar_data.findtext('sunspots', 'N/A')
                    xray = solar_data.findtext('xray', 'N/A')
                    updated = solar_data.findtext('updated', 'N/A')

                    lines = [
                        "=== Solar Conditions ===",
                        f"Updated: {updated}",
                        f"Solar Flux Index: {sfi}",
                        f"A-Index: {a_index}  K-Index: {k_index}",
                        f"Sunspots: {sunspots}  X-Ray: {xray}",
                        "",
                        "=== HF Band Conditions ==="
                    ]

                    # Parse band conditions (day/night)
                    for band_elem in solar_data.findall('.//band'):
                        name = band_elem.get('name', 'Unknown')
                        time = band_elem.get('time', '')
                        condition = band_elem.text or 'Unknown'
                        lines.append(f"  {name:10} ({time:5}): {condition}")

                    # Add VHF conditions if available
                    vhf_cond = solar_data.findtext('vhf', '')
                    if vhf_cond:
                        lines.append(f"\nVHF Conditions: {vhf_cond}")

                    sig_noise = solar_data.findtext('signalnoise', '')
                    if sig_noise:
                        lines.append(f"Signal Noise: {sig_noise}")

                    GLib.idle_add(self._set_output, '\n'.join(lines))
                else:
                    GLib.idle_add(self._output_message, "Could not parse solar data")

            except Exception as e:
                GLib.idle_add(self._output_message, f"Error fetching band data: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _on_lookup_callsign(self, widget):
        """Lookup callsign"""
        callsign = self._callsign_entry.get_text().strip().upper()
        if not callsign:
            return

        source = self._lookup_source.get_active_id()
        self._output_message(f"Looking up {callsign} via {source}...")

        def do_lookup():
            try:
                if source == "callook":
                    url = f"https://callook.info/{callsign}/json"
                    req = urllib.request.Request(url)
                    req.add_header('User-Agent', 'MeshForge/1.0')

                    with urllib.request.urlopen(req, timeout=10) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        GLib.idle_add(self._display_callsign_result, callsign, data)

                else:
                    GLib.idle_add(self._output_message, f"Source {source} not yet implemented")

            except Exception as e:
                GLib.idle_add(self._output_message, f"Lookup error: {e}")

        threading.Thread(target=do_lookup, daemon=True).start()

    def _display_callsign_result(self, callsign: str, data: dict):
        """Display callsign lookup result"""
        if data.get('status') == 'VALID':
            name = data.get('name', 'Unknown')
            addr = data.get('address', {})
            location = f"{addr.get('city', '')}, {addr.get('state', '')}"
            lic_class = data.get('current', {}).get('operClass', 'Unknown')
            grant_date = data.get('current', {}).get('grantDate', 'Unknown')

            result = f"""Callsign: {callsign}
Name: {name}
Location: {location}
Class: {lic_class}
Grant Date: {grant_date}
"""
            buffer = self._callsign_results.get_buffer()
            buffer.set_text(result)

            # Add to recent
            self._recent_store.insert(0, [callsign, name, location])

            self._output_message(f"Found: {callsign} - {name}")
        else:
            buffer = self._callsign_results.get_buffer()
            buffer.set_text(f"Callsign {callsign} not found")
            self._output_message(f"Callsign {callsign} not found")

    def _output_message(self, message: str):
        """Add message to output"""
        buffer = self._output_text.get_buffer()
        end_iter = buffer.get_end_iter()
        timestamp = datetime.now().strftime("%H:%M:%S")
        buffer.insert(end_iter, f"[{timestamp}] {message}\n")

    def _clear_output(self):
        """Clear output"""
        buffer = self._output_text.get_buffer()
        buffer.set_text("")

    def _on_copy_output(self, button):
        """Copy output to clipboard"""
        buffer = self._output_text.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False)

        clipboard = self.get_clipboard()
        clipboard.set(text)
        self._output_message("Copied to clipboard")

    def _open_url(self, url: str):
        """Open URL in browser"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        try:
            subprocess.Popen(
                ['sudo', '-u', real_user, 'xdg-open', url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            self._output_message(f"Opening {url}")
        except Exception as e:
            self._output_message(f"Error opening URL: {e}")

    def cleanup(self):
        """Clean up resources"""
        pass
