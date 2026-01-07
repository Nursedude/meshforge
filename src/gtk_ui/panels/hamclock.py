"""
HamClock Panel - Integration with HamClock for propagation and space weather

HamClock by Clear Sky Institute provides:
- VOACAP propagation predictions
- Solar flux and A/K index
- Gray line visualization
- DX cluster spots
- Satellite tracking

Reference: https://www.clearskyinstitute.com/ham/HamClock/
SystemD packages: https://github.com/pa28/hamclock-systemd
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
import json
import threading
import subprocess
import time
import urllib.request
import urllib.error
import logging
from pathlib import Path
import os

logger = logging.getLogger(__name__)

# Import the proper path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    # Fallback for standalone usage
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Use centralized settings manager
try:
    from utils.common import SettingsManager
    HAS_SETTINGS_MANAGER = True
except ImportError:
    HAS_SETTINGS_MANAGER = False

# Try to import WebKit for embedded view
# Note: WebKit doesn't work when running as root (sandbox issues)
_is_root = os.geteuid() == 0

try:
    if _is_root:
        # WebKit doesn't work as root due to sandbox restrictions
        HAS_WEBKIT = False
        logger.info("WebKit disabled (running as root)")
    else:
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit
        HAS_WEBKIT = True
except (ValueError, ImportError):
    try:
        if not _is_root:
            gi.require_version('WebKit2', '4.1')
            from gi.repository import WebKit2 as WebKit
            HAS_WEBKIT = True
        else:
            HAS_WEBKIT = False
    except (ValueError, ImportError):
        HAS_WEBKIT = False


class HamClockPanel(Gtk.Box):
    """Panel for HamClock integration"""

    # Settings defaults
    SETTINGS_DEFAULTS = {
        "url": "",
        "api_port": 8080,
        "live_port": 8081,
        "auto_refresh_enabled": False,
        "auto_refresh_minutes": 10,
    }

    # Stale data threshold (minutes without update before showing warning)
    STALE_DATA_THRESHOLD_MINUTES = 15

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.webview = None

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        # Auto-refresh state
        self._auto_refresh_timer_id = None
        self._last_update_time = None
        self._update_check_timer_id = None
        self._retry_count = 0
        self._max_retries = 3

        # Use centralized settings manager
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr = SettingsManager("hamclock", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            self._settings = self._load_settings_legacy()

        self._build_ui()

        # Check service status on startup
        GLib.timeout_add(500, self._check_service_status)

        # Auto-connect if URL is configured
        if self._settings.get("url"):
            GLib.timeout_add(1000, self._auto_connect)

        # Start auto-refresh if enabled
        if self._settings.get("auto_refresh_enabled"):
            GLib.timeout_add(2000, self._start_auto_refresh)

        # Start update time checker (updates "last updated" display)
        self._update_check_timer_id = GLib.timeout_add(60000, self._check_data_freshness)

    def _load_settings_legacy(self):
        """Legacy settings load for fallback"""
        settings_file = get_real_user_home() / ".config" / "meshforge" / "hamclock.json"
        defaults = self.SETTINGS_DEFAULTS.copy()
        try:
            if settings_file.exists():
                with open(settings_file) as f:
                    saved = json.load(f)
                    defaults.update(saved)
                logger.debug(f"[HamClock] Loaded settings from {settings_file}")
        except Exception as e:
            logger.error(f"Error loading HamClock settings: {e}")
        return defaults

    def _save_settings(self):
        """Save HamClock settings"""
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()
            logger.debug(f"[HamClock] Settings saved via SettingsManager")
        else:
            # Legacy fallback - use real user's home, not root's
            settings_file = get_real_user_home() / ".config" / "meshforge" / "hamclock.json"
            try:
                settings_file.parent.mkdir(parents=True, exist_ok=True)
                with open(settings_file, 'w') as f:
                    json.dump(self._settings, f, indent=2)
                logger.debug(f"[HamClock] Settings saved to {settings_file}")
            except Exception as e:
                logger.error(f"Error saving HamClock settings: {e}")

    def _build_ui(self):
        """Build the HamClock panel UI"""
        # Header (fixed, not scrolled)
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        title = Gtk.Label(label="HamClock")
        title.add_css_class("title-1")
        title.set_xalign(0)
        header_box.append(title)

        # Status indicator
        self.status_label = Gtk.Label(label="Not connected")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_hexpand(True)
        self.status_label.set_xalign(1)
        header_box.append(self.status_label)

        self.append(header_box)

        subtitle = Gtk.Label(label="Space weather and propagation from HamClock")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        self.append(subtitle)

        # Scrollable content area
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(400)

        # Content box inside scroll
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(10)

        # Service status section
        self._build_service_section(content_box)

        # Connection settings
        settings_frame = Gtk.Frame()
        settings_frame.set_label("Connection")
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        settings_box.set_margin_start(15)
        settings_box.set_margin_end(15)
        settings_box.set_margin_top(10)
        settings_box.set_margin_bottom(10)

        # URL entry
        url_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        url_box.append(Gtk.Label(label="HamClock URL:"))

        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("http://hamclock.local or http://192.168.1.100")
        self.url_entry.set_text(self._settings.get("url", ""))
        self.url_entry.set_hexpand(True)
        url_box.append(self.url_entry)

        connect_btn = Gtk.Button(label="Connect")
        connect_btn.set_tooltip_text("Connect to HamClock instance and fetch space weather data")
        connect_btn.connect("clicked", self._on_connect)
        connect_btn.add_css_class("suggested-action")
        url_box.append(connect_btn)

        settings_box.append(url_box)

        # Port settings
        port_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        port_box.append(Gtk.Label(label="API Port:"))

        self.api_port_spin = Gtk.SpinButton()
        self.api_port_spin.set_range(1, 65535)
        self.api_port_spin.set_value(self._settings.get("api_port", 8080))
        self.api_port_spin.set_increments(1, 10)
        self.api_port_spin.set_width_chars(6)  # Wide enough for 5-digit port
        port_box.append(self.api_port_spin)

        port_box.append(Gtk.Label(label="Live Port:"))

        self.live_port_spin = Gtk.SpinButton()
        self.live_port_spin.set_range(1, 65535)
        self.live_port_spin.set_value(self._settings.get("live_port", 8081))
        self.live_port_spin.set_increments(1, 10)
        self.live_port_spin.set_width_chars(6)  # Wide enough for 5-digit port
        port_box.append(self.live_port_spin)

        settings_box.append(port_box)
        settings_frame.set_child(settings_box)
        content_box.append(settings_frame)

        # Space weather info
        weather_frame = Gtk.Frame()
        weather_frame.set_label("Space Weather")
        weather_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        weather_box.set_margin_start(15)
        weather_box.set_margin_end(15)
        weather_box.set_margin_top(10)
        weather_box.set_margin_bottom(10)

        # Create stat rows
        self.stat_labels = {}
        stats = [
            ("sfi", "Solar Flux Index (SFI)"),
            ("kp", "Kp Index"),
            ("a", "A Index"),
            ("xray", "X-Ray Flux"),
            ("sunspots", "Sunspot Number"),
            ("conditions", "Band Conditions"),
            ("aurora", "Aurora Activity"),
            ("proton", "Proton Flux"),
        ]

        for key, label_text in stats:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(label=f"{label_text}:")
            label.set_xalign(0)
            label.set_hexpand(True)
            row.append(label)

            value = Gtk.Label(label="--")
            value.set_xalign(1)
            row.append(value)
            self.stat_labels[key] = value

            weather_box.append(row)

        # Last updated indicator
        self.last_update_label = Gtk.Label(label="Last updated: Never")
        self.last_update_label.set_xalign(0)
        self.last_update_label.add_css_class("dim-label")
        self.last_update_label.set_margin_top(10)
        weather_box.append(self.last_update_label)

        # Refresh and auto-refresh controls
        refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_box.set_margin_top(10)

        refresh_btn = Gtk.Button(label="Refresh Now")
        refresh_btn.set_tooltip_text("Refresh space weather data from HamClock")
        refresh_btn.connect("clicked", self._on_refresh)
        refresh_box.append(refresh_btn)

        # Auto-refresh toggle
        self.auto_refresh_switch = Gtk.Switch()
        self.auto_refresh_switch.set_active(self._settings.get("auto_refresh_enabled", False))
        self.auto_refresh_switch.set_tooltip_text("Enable automatic data refresh")
        self.auto_refresh_switch.connect("notify::active", self._on_auto_refresh_toggled)

        auto_refresh_label = Gtk.Label(label="Auto-refresh:")
        refresh_box.append(auto_refresh_label)
        refresh_box.append(self.auto_refresh_switch)

        # Interval spinner
        interval_label = Gtk.Label(label="every")
        refresh_box.append(interval_label)

        self.refresh_interval_spin = Gtk.SpinButton()
        self.refresh_interval_spin.set_range(1, 60)
        self.refresh_interval_spin.set_value(self._settings.get("auto_refresh_minutes", 10))
        self.refresh_interval_spin.set_increments(1, 5)
        self.refresh_interval_spin.set_width_chars(3)
        self.refresh_interval_spin.set_tooltip_text("Refresh interval in minutes")
        self.refresh_interval_spin.connect("value-changed", self._on_refresh_interval_changed)
        refresh_box.append(self.refresh_interval_spin)

        min_label = Gtk.Label(label="min")
        refresh_box.append(min_label)

        weather_box.append(refresh_box)

        weather_frame.set_child(weather_box)
        content_box.append(weather_frame)

        # HF Band Conditions Frame
        bands_frame = Gtk.Frame()
        bands_frame.set_label("HF Band Conditions (Day/Night)")
        bands_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        bands_box.set_margin_start(15)
        bands_box.set_margin_end(15)
        bands_box.set_margin_top(10)
        bands_box.set_margin_bottom(10)

        # Band condition labels
        self.band_labels = {}
        bands = [
            ("80m-40m", "80m-40m (Low)"),
            ("30m-20m", "30m-20m (Mid)"),
            ("17m-15m", "17m-15m (High)"),
            ("12m-10m", "12m-10m (VHF)"),
        ]

        for key, label_text in bands:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(label=f"{label_text}:")
            label.set_xalign(0)
            label.set_hexpand(True)
            row.append(label)

            value = Gtk.Label(label="--/--")
            value.set_xalign(1)
            row.append(value)
            self.band_labels[key] = value

            bands_box.append(row)

        # NOAA fetch button
        noaa_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        noaa_row.set_margin_top(5)

        noaa_btn = Gtk.Button(label="Fetch NOAA Data")
        noaa_btn.connect("clicked", self._on_fetch_noaa)
        noaa_btn.set_tooltip_text("Get latest from NOAA Space Weather")
        noaa_row.append(noaa_btn)

        prop_btn = Gtk.Button(label="DX Propagation")
        prop_btn.connect("clicked", self._on_open_dx_propagation)
        prop_btn.set_tooltip_text("Open DX propagation charts in browser")
        noaa_row.append(prop_btn)

        bands_box.append(noaa_row)
        bands_frame.set_child(bands_box)
        content_box.append(bands_frame)

        # VOACAP Propagation Predictions Frame
        voacap_frame = Gtk.Frame()
        voacap_frame.set_label("VOACAP Propagation Predictions")
        voacap_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        voacap_box.set_margin_start(15)
        voacap_box.set_margin_end(15)
        voacap_box.set_margin_top(10)
        voacap_box.set_margin_bottom(10)

        # VOACAP explanation
        voacap_info = Gtk.Label(
            label="Voice of America Coverage Analysis Program - HF propagation reliability"
        )
        voacap_info.set_xalign(0)
        voacap_info.add_css_class("dim-label")
        voacap_box.append(voacap_info)

        # Individual band predictions with reliability percentages
        self.voacap_labels = {}
        voacap_bands = [
            ("160m", "160m (1.8 MHz)"),
            ("80m", "80m (3.5 MHz)"),
            ("40m", "40m (7 MHz)"),
            ("30m", "30m (10 MHz)"),
            ("20m", "20m (14 MHz)"),
            ("17m", "17m (18 MHz)"),
            ("15m", "15m (21 MHz)"),
            ("12m", "12m (24 MHz)"),
            ("10m", "10m (28 MHz)"),
            ("6m", "6m (50 MHz)"),
        ]

        # Create a grid for better layout
        voacap_grid = Gtk.Grid()
        voacap_grid.set_column_spacing(15)
        voacap_grid.set_row_spacing(5)
        voacap_grid.set_margin_top(10)

        # Headers
        band_header = Gtk.Label(label="Band")
        band_header.add_css_class("heading")
        band_header.set_xalign(0)
        voacap_grid.attach(band_header, 0, 0, 1, 1)

        rel_header = Gtk.Label(label="Reliability")
        rel_header.add_css_class("heading")
        rel_header.set_xalign(0)
        voacap_grid.attach(rel_header, 1, 0, 1, 1)

        snr_header = Gtk.Label(label="SNR")
        snr_header.add_css_class("heading")
        snr_header.set_xalign(0)
        voacap_grid.attach(snr_header, 2, 0, 1, 1)

        for i, (key, label_text) in enumerate(voacap_bands):
            row = i + 1

            band_label = Gtk.Label(label=label_text)
            band_label.set_xalign(0)
            voacap_grid.attach(band_label, 0, row, 1, 1)

            rel_label = Gtk.Label(label="--")
            rel_label.set_xalign(0)
            voacap_grid.attach(rel_label, 1, row, 1, 1)

            snr_label = Gtk.Label(label="--")
            snr_label.set_xalign(0)
            voacap_grid.attach(snr_label, 2, row, 1, 1)

            self.voacap_labels[key] = {
                'reliability': rel_label,
                'snr': snr_label
            }

        voacap_box.append(voacap_grid)

        # VOACAP target/path info
        self.voacap_path_label = Gtk.Label(label="Path: --")
        self.voacap_path_label.set_xalign(0)
        self.voacap_path_label.add_css_class("dim-label")
        self.voacap_path_label.set_margin_top(10)
        voacap_box.append(self.voacap_path_label)

        # VOACAP buttons
        voacap_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        voacap_btn_row.set_margin_top(10)

        voacap_btn = Gtk.Button(label="Fetch VOACAP")
        voacap_btn.connect("clicked", self._on_fetch_voacap)
        voacap_btn.set_tooltip_text("Get VOACAP predictions from HamClock")
        voacap_btn.add_css_class("suggested-action")
        voacap_btn_row.append(voacap_btn)

        voacap_web_btn = Gtk.Button(label="VOACAP Online")
        voacap_web_btn.connect("clicked", self._on_open_voacap_online)
        voacap_web_btn.set_tooltip_text("Open VOACAP Online in browser for detailed analysis")
        voacap_btn_row.append(voacap_web_btn)

        voacap_box.append(voacap_btn_row)
        voacap_frame.set_child(voacap_box)
        content_box.append(voacap_frame)

        # Live view (if WebKit available)
        if HAS_WEBKIT:
            view_frame = Gtk.Frame()
            view_frame.set_label("Live View")
            view_frame.set_vexpand(True)

            self.webview = WebKit.WebView()
            self.webview.set_vexpand(True)
            self.webview.set_hexpand(True)

            view_frame.set_child(self.webview)
            content_box.append(view_frame)
        else:
            # Open in browser button - explain why embedded view isn't available
            browser_frame = Gtk.Frame()
            browser_frame.set_label("Live View")
            browser_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            browser_box.set_margin_start(15)
            browser_box.set_margin_end(15)
            browser_box.set_margin_top(10)
            browser_box.set_margin_bottom(10)

            # Explain why embedded view isn't available
            if _is_root:
                info_label = Gtk.Label(label="Embedded view disabled (running as root)")
                info_label.set_tooltip_text(
                    "WebKit cannot run embedded when MeshForge is started with sudo. "
                    "Use the button below to open HamClock in your browser instead."
                )
            else:
                info_label = Gtk.Label(label="WebKit not installed - open in browser")
                info_label.set_tooltip_text(
                    "Install gir1.2-webkit2-4.1 for embedded HamClock view"
                )
            info_label.add_css_class("dim-label")
            browser_box.append(info_label)

            open_btn = Gtk.Button(label="Open HamClock in Browser")
            open_btn.set_tooltip_text("Open the HamClock live view in your default browser")
            open_btn.connect("clicked", self._on_open_browser)
            open_btn.add_css_class("suggested-action")
            browser_box.append(open_btn)

            browser_frame.set_child(browser_box)
            content_box.append(browser_frame)

        # Add scrollable content to the panel
        scrolled.set_child(content_box)
        self.append(scrolled)

    def _build_service_section(self, parent):
        """Build HamClock service status and control section"""
        frame = Gtk.Frame()
        frame.set_label("HamClock Service")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.service_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.service_status_icon.set_pixel_size(32)
        status_row.append(self.service_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.service_status_label = Gtk.Label(label="Checking...")
        self.service_status_label.set_xalign(0)
        self.service_status_label.add_css_class("heading")
        status_info.append(self.service_status_label)

        self.service_detail_label = Gtk.Label(label="")
        self.service_detail_label.set_xalign(0)
        self.service_detail_label.add_css_class("dim-label")
        status_info.append(self.service_detail_label)

        status_row.append(status_info)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.service_start_btn = Gtk.Button(label="Start")
        self.service_start_btn.set_tooltip_text("Start the HamClock systemd service")
        self.service_start_btn.add_css_class("suggested-action")
        self.service_start_btn.connect("clicked", lambda b: self._service_action("start"))
        btn_box.append(self.service_start_btn)

        self.service_stop_btn = Gtk.Button(label="Stop")
        self.service_stop_btn.set_tooltip_text("Stop the HamClock systemd service")
        self.service_stop_btn.add_css_class("destructive-action")
        self.service_stop_btn.connect("clicked", lambda b: self._service_action("stop"))
        btn_box.append(self.service_stop_btn)

        self.service_restart_btn = Gtk.Button(label="Restart")
        self.service_restart_btn.set_tooltip_text("Restart the HamClock systemd service")
        self.service_restart_btn.connect("clicked", lambda b: self._service_action("restart"))
        btn_box.append(self.service_restart_btn)

        status_row.append(btn_box)
        box.append(status_row)

        # Install info row
        install_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        install_label = Gtk.Label(label="Install HamClock:")
        install_label.set_xalign(0)
        install_row.append(install_label)

        # One-click install button for hamclock-web (headless Pi)
        self.install_btn = Gtk.Button(label="Install hamclock-web")
        self.install_btn.set_tooltip_text("Install hamclock-web for headless Pi (requires internet)")
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", self._install_hamclock_web)
        install_row.append(self.install_btn)

        # Link to hamclock-systemd - use button instead of LinkButton (works as root)
        link_btn = Gtk.Button(label="Packages Info")
        link_btn.set_tooltip_text("https://github.com/pa28/hamclock-systemd")
        link_btn.connect("clicked", lambda b: self._open_url_in_browser("https://github.com/pa28/hamclock-systemd"))
        install_row.append(link_btn)

        # Or official site - use button instead of LinkButton (works as root)
        official_link = Gtk.Button(label="Official Site")
        official_link.set_tooltip_text("https://www.clearskyinstitute.com/ham/HamClock/")
        official_link.connect("clicked", lambda b: self._open_url_in_browser("https://www.clearskyinstitute.com/ham/HamClock/"))
        install_row.append(official_link)

        box.append(install_row)
        frame.set_child(box)
        parent.append(frame)

    def _check_service_status(self):
        """Check if HamClock service is running"""
        logger.debug("[HamClock] Checking service status...")

        def check():
            status = {
                'installed': False,
                'running': False,
                'service_name': None,
                'error': None
            }

            # Check for different HamClock service names
            service_names = ['hamclock', 'hamclock-web', 'hamclock-systemd']

            for name in service_names:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', name],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip() == 'active':
                        status['installed'] = True
                        status['running'] = True
                        status['service_name'] = name
                        break

                    # Check if installed but not running
                    result2 = subprocess.run(
                        ['systemctl', 'is-enabled', name],
                        capture_output=True, text=True, timeout=5
                    )
                    if result2.returncode == 0 or 'disabled' in result2.stdout:
                        status['installed'] = True
                        status['service_name'] = name

                except subprocess.TimeoutExpired:
                    logger.debug(f"[HamClock] Timeout checking service: {name}")
                except FileNotFoundError:
                    # systemctl not available (non-systemd system)
                    logger.debug("[HamClock] systemctl not found")
                    break
                except Exception as e:
                    logger.debug(f"[HamClock] Error checking service {name}: {e}")

            # Also check for running hamclock process (might be started manually)
            if not status['running']:
                try:
                    result = subprocess.run(
                        ['pgrep', '-f', 'hamclock'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status['running'] = True
                        status['service_name'] = 'hamclock (process)'
                except subprocess.TimeoutExpired:
                    logger.debug("[HamClock] Timeout checking process")
                except FileNotFoundError:
                    logger.debug("[HamClock] pgrep not found")
                except Exception as e:
                    logger.debug(f"[HamClock] Error checking process: {e}")

            GLib.idle_add(self._update_service_status, status)

        threading.Thread(target=check, daemon=True).start()
        return False  # Don't repeat

    def _update_service_status(self, status):
        """Update the service status display"""
        if status['running']:
            self.service_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.service_status_label.set_label("HamClock Running")
            if status['service_name']:
                self.service_detail_label.set_label(f"Service: {status['service_name']}")
            self.service_start_btn.set_sensitive(False)
            self.service_stop_btn.set_sensitive(True)
            self.service_restart_btn.set_sensitive(True)
            logger.debug(f"[HamClock] Service running: {status['service_name']}")
        elif status['installed']:
            self.service_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.service_status_label.set_label("HamClock Stopped")
            self.service_detail_label.set_label(f"Service: {status['service_name']}")
            self.service_start_btn.set_sensitive(True)
            self.service_stop_btn.set_sensitive(False)
            self.service_restart_btn.set_sensitive(False)
            logger.debug(f"[HamClock] Service installed but stopped")
        else:
            self.service_status_icon.set_from_icon_name("dialog-question-symbolic")
            self.service_status_label.set_label("HamClock Not Installed")
            self.service_detail_label.set_label("Install via hamclock-systemd or official packages")
            self.service_start_btn.set_sensitive(False)
            self.service_stop_btn.set_sensitive(False)
            self.service_restart_btn.set_sensitive(False)
            logger.debug("[HamClock] Service not found")

        return False

    def _service_action(self, action):
        """Perform HamClock service action (start/stop/restart)"""
        logger.debug(f"[HamClock] Service action: {action}...")
        self.main_window.set_status_message(f"{action.capitalize()}ing HamClock...")

        def do_action():
            service_names = ['hamclock', 'hamclock-web', 'hamclock-systemd']
            success = False
            error = None

            for name in service_names:
                try:
                    # Check if this service exists
                    check = subprocess.run(
                        ['systemctl', 'status', name],
                        capture_output=True, text=True, timeout=5
                    )
                    if check.returncode == 4:  # Unit not found
                        continue

                    # Try the action
                    result = subprocess.run(
                        ['sudo', 'systemctl', action, name],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        success = True
                        logger.debug(f"[HamClock] {action} {name}: OK")
                        break
                    else:
                        error = result.stderr.strip()
                except subprocess.TimeoutExpired:
                    error = "Command timed out"
                except Exception as e:
                    error = str(e)

            if not success and not error:
                error = "No HamClock service found"

            GLib.idle_add(self._service_action_complete, action, success, error)

        threading.Thread(target=do_action, daemon=True).start()

    def _service_action_complete(self, action, success, error):
        """Handle service action completion"""
        if success:
            self.main_window.set_status_message(f"HamClock {action} successful")
            logger.debug(f"[HamClock] {action}: OK")
        else:
            self.main_window.set_status_message(f"HamClock {action} failed: {error}")
            logger.debug(f"[HamClock] {action}: FAILED - {error}")

        # Refresh status
        GLib.timeout_add(1000, self._check_service_status)
        return False

    def _install_hamclock_web(self, button):
        """Install hamclock-web package for headless Pi operation.

        Uses pa28 repository: https://github.com/pa28/hamclock-systemd
        """
        logger.info("[HamClock] Starting hamclock-web installation")
        self.main_window.set_status_message("Installing hamclock-web...")
        button.set_sensitive(False)

        def do_install():
            import subprocess
            import shutil

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

                # Step 1: Download GPG key
                GLib.idle_add(self.main_window.set_status_message, "Adding pa28 repository key...")

                # Create keyrings directory if needed
                keyring_dir = '/usr/share/keyrings'
                key_path = f'{keyring_dir}/pa28-archive-keyring.gpg'

                # Download key using wget
                key_url = 'https://pa28.github.io/pa28-pkg/pa28-pkg.gpg.key'
                wget_cmd = ['wget', '-qO-', key_url]

                wget_result = subprocess.run(wget_cmd, capture_output=True, timeout=30)
                if wget_result.returncode != 0:
                    errors.append(f"Failed to download GPG key: {wget_result.stderr.decode()}")
                    GLib.idle_add(self._install_complete, False, "; ".join(errors), button)
                    return

                # Convert key to GPG format and save (requires sudo)
                gpg_cmd = ['sudo', 'gpg', '--dearmor', '-o', key_path]
                gpg_result = subprocess.run(gpg_cmd, input=wget_result.stdout, capture_output=True, timeout=30)

                if gpg_result.returncode != 0:
                    # Try with pkexec for graphical sudo
                    gpg_cmd = ['pkexec', 'gpg', '--dearmor', '-o', key_path]
                    gpg_result = subprocess.run(gpg_cmd, input=wget_result.stdout, capture_output=True, timeout=60)
                    if gpg_result.returncode != 0:
                        errors.append(f"Failed to install GPG key: {gpg_result.stderr.decode()}")
                        GLib.idle_add(self._install_complete, False, "; ".join(errors), button)
                        return

                # Step 2: Add repository
                GLib.idle_add(self.main_window.set_status_message, "Adding pa28 repository...")

                repo_line = f'deb [signed-by={key_path}] https://pa28.github.io/pa28-pkg ./'
                sources_file = '/etc/apt/sources.list.d/pa28.list'

                # Write sources file (requires sudo)
                echo_cmd = ['sudo', 'tee', sources_file]
                echo_result = subprocess.run(echo_cmd, input=repo_line.encode(), capture_output=True, timeout=10)

                if echo_result.returncode != 0:
                    echo_cmd = ['pkexec', 'tee', sources_file]
                    echo_result = subprocess.run(echo_cmd, input=repo_line.encode(), capture_output=True, timeout=30)
                    if echo_result.returncode != 0:
                        errors.append(f"Failed to add repository: {echo_result.stderr.decode()}")
                        GLib.idle_add(self._install_complete, False, "; ".join(errors), button)
                        return

                # Step 3: Update apt
                GLib.idle_add(self.main_window.set_status_message, "Updating package lists...")

                update_cmd = ['sudo', 'apt', 'update']
                update_result = subprocess.run(update_cmd, capture_output=True, timeout=120)

                if update_result.returncode != 0:
                    update_cmd = ['pkexec', 'apt', 'update']
                    update_result = subprocess.run(update_cmd, capture_output=True, timeout=120)

                # Step 4: Install hamclock-web
                GLib.idle_add(self.main_window.set_status_message, "Installing hamclock-web package...")

                install_cmd = ['sudo', 'apt', 'install', '-y', 'hamclock-web']
                install_result = subprocess.run(install_cmd, capture_output=True, text=True, timeout=300)

                if install_result.returncode != 0:
                    install_cmd = ['pkexec', 'apt', 'install', '-y', 'hamclock-web']
                    install_result = subprocess.run(install_cmd, capture_output=True, text=True, timeout=300)
                    if install_result.returncode != 0:
                        errors.append(f"Failed to install: {install_result.stderr}")
                        GLib.idle_add(self._install_complete, False, "; ".join(errors), button)
                        return

                # Step 5: Enable and start service
                GLib.idle_add(self.main_window.set_status_message, "Enabling hamclock service...")

                enable_cmd = ['sudo', 'systemctl', 'enable', '--now', 'hamclock']
                enable_result = subprocess.run(enable_cmd, capture_output=True, text=True, timeout=30)

                if enable_result.returncode != 0:
                    enable_cmd = ['pkexec', 'systemctl', 'enable', '--now', 'hamclock']
                    subprocess.run(enable_cmd, capture_output=True, timeout=60)

                GLib.idle_add(self._install_complete, True, "hamclock-web installed successfully!", button)

            except subprocess.TimeoutExpired:
                errors.append("Installation timed out")
                GLib.idle_add(self._install_complete, False, "; ".join(errors), button)
            except Exception as e:
                logger.error(f"[HamClock] Install error: {e}")
                errors.append(str(e))
                GLib.idle_add(self._install_complete, False, "; ".join(errors), button)

        threading.Thread(target=do_install, daemon=True).start()

    def _install_complete(self, success, message, button):
        """Handle installation completion."""
        button.set_sensitive(True)

        if success:
            self.main_window.set_status_message(message)
            logger.info(f"[HamClock] Install: {message}")
            # Update URL to localhost since we just installed locally
            self.url_entry.set_text("http://localhost")
            # Refresh service status
            GLib.timeout_add(2000, self._check_service_status)
        else:
            self.main_window.set_status_message(f"Install failed: {message}")
            logger.error(f"[HamClock] Install failed: {message}")

        return False

    def _auto_connect(self):
        """Auto-connect on startup"""
        self._on_connect(None)
        return False  # Don't repeat

    def _validate_url(self, url):
        """Validate URL format and return (valid, error_message)"""
        if not url:
            return False, "Enter HamClock URL"

        # Basic URL format validation
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)

            # Must have a valid host
            if not parsed.netloc and not parsed.path:
                return False, "Invalid URL format"

            # Check for obvious mistakes
            if parsed.netloc and ':' in parsed.netloc:
                # URL includes port - warn user to use port fields instead
                host_part = parsed.netloc.split(':')[0]
                port_part = parsed.netloc.split(':')[1]
                if port_part.isdigit():
                    return False, f"Don't include port in URL. Use '{parsed.scheme}://{host_part}' and set ports below"

        except Exception:
            return False, "Invalid URL format"

        return True, None

    def _on_connect(self, button):
        """Connect to HamClock"""
        logger.info("[HamClock] Connect button clicked")

        try:
            url = self.url_entry.get_text().strip()
            if not url:
                logger.debug("[HamClock] No URL entered")
                self.status_label.set_label("Enter HamClock URL")
                return

            # Add http:// prefix if missing
            if not url.startswith('http://') and not url.startswith('https://'):
                url = f'http://{url}'

            # Remove trailing slash
            url = url.rstrip('/')

            # Validate URL format
            valid, error = self._validate_url(url)
            if not valid:
                logger.warning(f"[HamClock] URL validation failed: {error}")
                self.status_label.set_label(error)
                return

            # Validate ports
            api_port = int(self.api_port_spin.get_value())
            live_port = int(self.live_port_spin.get_value())
            if api_port < 1 or api_port > 65535:
                logger.warning(f"[HamClock] Invalid API port: {api_port}")
                self.status_label.set_label("API port must be 1-65535")
                return
            if live_port < 1 or live_port > 65535:
                logger.warning(f"[HamClock] Invalid Live port: {live_port}")
                self.status_label.set_label("Live port must be 1-65535")
                return

            logger.info(f"[HamClock] Connecting to {url} (API:{api_port}, Live:{live_port})")

            # Save settings
            self._settings["url"] = url
            self._settings["api_port"] = api_port
            self._settings["live_port"] = live_port
            self._save_settings()

            # Update entry with corrected URL
            self.url_entry.set_text(url)

            self.status_label.set_label("Connecting...")
        except Exception as e:
            logger.error(f"[HamClock] Error in connect handler: {e}", exc_info=True)
            self.status_label.set_label(f"Error: {e}")
            return

        def check_connection():
            api_url = f"{url}:{self._settings['api_port']}"
            full_url = f"{api_url}/get_sys.txt"

            logger.debug(f"[HamClock] Testing connection: {full_url}")

            try:
                # Try to get version or any API response
                req = urllib.request.Request(full_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    logger.debug(f"[HamClock] Connection successful, got {len(data)} bytes")
                    GLib.idle_add(self._on_connected, url, data)
            except urllib.error.HTTPError as e:
                error_msg = f"HTTP {e.code}: {e.reason}"
                logger.debug(f"[HamClock] HTTP error: {error_msg}")
                GLib.idle_add(self._on_connection_failed, error_msg)
            except urllib.error.URLError as e:
                error_msg = str(e.reason)
                logger.debug(f"[HamClock] URL error: {error_msg}")
                GLib.idle_add(self._on_connection_failed, error_msg)
            except Exception as e:
                logger.debug(f"[HamClock] Connection error: {e}")
                GLib.idle_add(self._on_connection_failed, str(e))

        threading.Thread(target=check_connection, daemon=True).start()

    def _on_connected(self, url, sys_data):
        """Handle successful connection"""
        self.status_label.set_label(f"Connected to {url}")
        self.main_window.set_status_message("HamClock connected")

        # Load live view if WebKit available
        if self.webview:
            live_url = f"{url}:{self._settings['live_port']}/live.html"
            self.webview.load_uri(live_url)

        # Fetch space weather data
        self._fetch_space_weather()

    def _on_connection_failed(self, error):
        """Handle connection failure with actionable feedback"""
        error_str = str(error).lower()

        # Provide specific guidance based on error type
        if 'connection refused' in error_str:
            self.status_label.set_label("Connection refused - is HamClock running?")
            logger.info(f"[HamClock] Connection refused - HamClock service may not be running")
        elif 'name or service not known' in error_str or 'nodename nor servname' in error_str:
            self.status_label.set_label("Host not found - check URL")
            logger.info(f"[HamClock] DNS resolution failed for configured host")
        elif 'timed out' in error_str or 'timeout' in error_str:
            self.status_label.set_label("Timeout - check network/firewall")
            logger.info(f"[HamClock] Connection timed out")
        elif 'no route to host' in error_str:
            self.status_label.set_label("No route to host - check network")
            logger.info(f"[HamClock] No route to host")
        else:
            # Generic error - show abbreviated version
            short_error = str(error)[:40]
            self.status_label.set_label(f"Error: {short_error}")
            logger.info(f"[HamClock] Connection failed: {error}")

    def _on_refresh(self, button):
        """Refresh space weather data"""
        logger.info("[HamClock] Refresh button clicked")
        if not self._settings.get("url"):
            logger.debug("[HamClock] No URL configured, cannot refresh")
            self.status_label.set_label("Not connected")
            return
        self._fetch_space_weather()

    def _fetch_space_weather(self):
        """Fetch space weather data from HamClock"""
        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            logger.debug("[HamClock] No URL configured, skipping fetch")
            return

        def fetch():
            api_url = f"{url}:{api_port}"
            weather_data = {}
            success_count = 0

            logger.debug(f"[HamClock] Fetching from {api_url}...")

            # Try various HamClock endpoints
            endpoints = [
                ("get_sys.txt", self._parse_sys),
                ("get_spacewx.txt", self._parse_spacewx),
                ("get_bc.txt", self._parse_band_conditions),
            ]

            for endpoint, parser in endpoints:
                try:
                    full_url = f"{api_url}/{endpoint}"
                    logger.debug(f"[HamClock] Trying {full_url}...")

                    req = urllib.request.Request(full_url, method='GET')
                    req.add_header('User-Agent', 'MeshForge/1.0')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        data = response.read().decode('utf-8')
                        logger.debug(f"[HamClock] {endpoint} response: {data[:100]}...")
                        parsed = parser(data)
                        weather_data.update(parsed)
                        success_count += 1
                except urllib.error.HTTPError as e:
                    logger.debug(f"[HamClock] {endpoint}: HTTP {e.code} - {e.reason}")
                except urllib.error.URLError as e:
                    logger.debug(f"[HamClock] {endpoint}: URL Error - {e.reason}")
                except Exception as e:
                    logger.debug(f"[HamClock] {endpoint}: Error - {e}")

            logger.debug(f"[HamClock] Fetched {success_count} endpoints, {len(weather_data)} values")
            GLib.idle_add(self._update_weather_display, weather_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_band_conditions(self, data):
        """Parse band conditions response from HamClock API (get_bc.txt)"""
        result = {}
        logger.debug(f"[HamClock] Parsing band conditions: {data[:200]}...")

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                # Map band condition keys
                if '80' in key_lower or '40' in key_lower:
                    result['80m-40m'] = value
                elif '30' in key_lower or '20' in key_lower:
                    result['30m-20m'] = value
                elif '17' in key_lower or '15' in key_lower:
                    result['17m-15m'] = value
                elif '12' in key_lower or '10' in key_lower:
                    result['12m-10m'] = value

        return result

    def _parse_sys(self, data):
        """Parse system info response"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result

    def _parse_spacewx(self, data):
        """Parse space weather response from HamClock API

        Expected format:
            SFI=156
            Kp=2
            A=8
            XRay=B5.2
            SSN=112
        """
        result = {}
        logger.debug(f"[HamClock] Parsing spacewx data: {data[:200]}...")

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                # Map HamClock keys to our display keys
                if key_lower == 'sfi' or 'flux' in key_lower:
                    result['sfi'] = value
                elif key_lower == 'kp':
                    result['kp'] = value
                elif key_lower == 'a' or 'a_index' in key_lower:
                    result['a'] = value
                elif key_lower == 'xray':
                    result['xray'] = value
                elif key_lower == 'ssn' or 'sunspot' in key_lower:
                    result['sunspots'] = value
                elif key_lower == 'proton' or 'pf' in key_lower:
                    result['proton'] = value
                elif key_lower == 'aurora' or 'aur' in key_lower:
                    result['aurora'] = value

        logger.debug(f"[HamClock] Parsed values: {result}")
        return result

    def _update_weather_display(self, data):
        """Update the weather display with fetched data"""
        logger.debug(f"[HamClock] Updating display with: {data}")

        updated_count = 0

        # Update stat labels
        for key, label in self.stat_labels.items():
            if key in data:
                label.set_label(str(data[key]))
                updated_count += 1
            # Also check for capitalized versions
            elif key.upper() in data:
                label.set_label(str(data[key.upper()]))
                updated_count += 1

        # Update band condition labels
        for key, label in self.band_labels.items():
            if key in data:
                label.set_label(str(data[key]))
                updated_count += 1

        # Update conditions based on Kp
        if 'kp' in data:
            try:
                kp = float(data['kp'])
                if kp < 3:
                    self.stat_labels['conditions'].set_label("Good")
                elif kp < 5:
                    self.stat_labels['conditions'].set_label("Moderate")
                else:
                    self.stat_labels['conditions'].set_label("Disturbed")
                updated_count += 1
            except ValueError:
                pass

        if updated_count > 0:
            self.status_label.set_label(f"Updated {updated_count} values")
            logger.debug(f"[HamClock] Updated {updated_count} UI labels")
            # Record successful update time
            self._record_update_time()
        else:
            self.status_label.set_label("No data received")
            logger.debug("[HamClock] No values to update")

    def _on_open_browser(self, button):
        """Open HamClock live view in browser"""
        logger.info("[HamClock] Open Browser button clicked")
        url = self._settings.get("url", "").rstrip('/')
        live_port = self._settings.get("live_port", 8081)

        if not url:
            logger.debug("[HamClock] No URL configured")
            self.status_label.set_label("Enter HamClock URL first")
            return

        live_url = f"{url}:{live_port}/live.html"
        self._open_url_in_browser(live_url)

    def _open_url_in_browser(self, url):
        """Open a URL in the user's default browser (handles running as root)"""
        logger.info(f"[HamClock] Opening URL in browser: {url}")

        user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        is_root = os.geteuid() == 0

        # Detect display environment (Wayland or X11)
        wayland_display = os.environ.get('WAYLAND_DISPLAY', '')
        x11_display = os.environ.get('DISPLAY', '')

        logger.debug(f"[HamClock] User: {user}, Root: {is_root}, WAYLAND: {wayland_display}, X11: {x11_display}")

        # Build environment variables for browser
        env_vars = []
        if wayland_display:
            env_vars.append(f'WAYLAND_DISPLAY={wayland_display}')
            runtime_dir = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
            env_vars.append(f'XDG_RUNTIME_DIR={runtime_dir}')
        if x11_display:
            env_vars.append(f'DISPLAY={x11_display}')
        if not env_vars:
            env_vars.append('DISPLAY=:0')

        logger.debug(f"[HamClock] Environment vars: {env_vars}")

        # Define browser commands with descriptions
        browser_methods = []

        if is_root and user:
            browser_methods.append(('xdg-open as user', ['sudo', '-u', user, 'env'] + env_vars + ['xdg-open', url]))

        browser_methods.append(('xdg-open direct', ['xdg-open', url]))

        if is_root and user:
            browser_methods.extend([
                ('chromium as user', ['sudo', '-u', user, 'env'] + env_vars + ['chromium-browser', url]),
                ('firefox as user', ['sudo', '-u', user, 'env'] + env_vars + ['firefox', url]),
            ])
        else:
            browser_methods.extend([
                ('chromium direct', ['chromium-browser', url]),
                ('firefox direct', ['firefox', url]),
            ])

        # Try each method
        for i, (desc, cmd) in enumerate(browser_methods):
            try:
                logger.debug(f"[HamClock] Trying method {i+1} ({desc}): {' '.join(cmd[:4])}...")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True  # Detach from parent
                )

                # Give it a moment to start, then check if it failed immediately
                time.sleep(0.2)

                # Check if process started (None means still running)
                ret = proc.poll()
                if ret is None or ret == 0:
                    logger.info(f"[HamClock] Browser opened successfully using {desc}")
                    self.status_label.set_label(f"Opened in browser")
                    return
                else:
                    stderr = proc.stderr.read().decode('utf-8', errors='ignore')[:200]
                    logger.debug(f"[HamClock] Method {desc} returned {ret}: {stderr}")

            except FileNotFoundError as e:
                logger.debug(f"[HamClock] Method {desc}: command not found - {e}")
            except PermissionError as e:
                logger.debug(f"[HamClock] Method {desc}: permission denied - {e}")
            except Exception as e:
                logger.warning(f"[HamClock] Method {desc} failed: {type(e).__name__}: {e}")

        # All methods failed
        error_msg = "Could not open browser - copy URL manually"
        logger.error(f"[HamClock] {error_msg}: {url}")
        self.status_label.set_label(error_msg)

    def _on_fetch_noaa(self, button):
        """Fetch space weather data from NOAA"""
        logger.info("[HamClock] NOAA fetch button clicked")
        self.status_label.set_label("Fetching NOAA data...")

        def fetch():
            try:
                # NOAA Space Weather Prediction Center - Solar data
                noaa_url = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
                req = urllib.request.Request(noaa_url)
                req.add_header('User-Agent', 'MeshForge/1.0')

                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode('utf-8'))

                # Get most recent entry
                if data and len(data) > 0:
                    latest = data[-1]
                    GLib.idle_add(self._update_noaa_display, latest)
                else:
                    GLib.idle_add(lambda: self.status_label.set_label("No NOAA data"))

            except Exception as e:
                logger.error(f"NOAA fetch error: {e}")
                GLib.idle_add(lambda: self.status_label.set_label(f"NOAA error: {e}"))

        threading.Thread(target=fetch, daemon=True).start()

    def _update_noaa_display(self, data):
        """Update display with NOAA solar data"""
        try:
            # Solar Flux Index
            if 'f10.7' in data:
                self.stat_labels['sfi'].set_label(str(data['f10.7']))

            # Sunspot number
            if 'ssn' in data:
                self.stat_labels['sunspots'].set_label(str(data['ssn']))

            # Estimate band conditions based on SFI
            sfi = float(data.get('f10.7', 0))
            if sfi >= 150:
                conditions = "Excellent"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Excellent/Good",
                         "17m-15m": "Excellent/Fair", "12m-10m": "Good/Poor"}
            elif sfi >= 120:
                conditions = "Good"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Good/Good",
                         "17m-15m": "Good/Fair", "12m-10m": "Fair/Poor"}
            elif sfi >= 90:
                conditions = "Fair"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Fair/Fair",
                         "17m-15m": "Fair/Poor", "12m-10m": "Poor/Poor"}
            else:
                conditions = "Poor"
                bands = {"80m-40m": "Fair/Good", "30m-20m": "Poor/Fair",
                         "17m-15m": "Poor/Poor", "12m-10m": "Poor/Poor"}

            self.stat_labels['conditions'].set_label(conditions)

            for band, condition in bands.items():
                if band in self.band_labels:
                    self.band_labels[band].set_label(condition)

            self.status_label.set_label(f"NOAA data updated (SFI: {sfi})")
        except Exception as e:
            self.status_label.set_label(f"Parse error: {e}")

    def _on_open_dx_propagation(self, button):
        """Open DX propagation charts in browser"""
        logger.info("[HamClock] DX Propagation button clicked")
        # N0NBH Solar-Terrestrial Data page
        url = "https://www.hamqsl.com/solar.html"
        self._open_url_in_browser(url)

    def _on_fetch_voacap(self, button):
        """Fetch VOACAP propagation predictions from HamClock"""
        logger.info("[HamClock] VOACAP fetch button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching VOACAP data...")

        def fetch():
            api_url = f"{url}:{api_port}"
            voacap_data = {}

            try:
                # HamClock VOACAP endpoint
                full_url = f"{api_url}/get_voacap.txt"
                logger.debug(f"[HamClock] Fetching VOACAP: {full_url}")

                req = urllib.request.Request(full_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = response.read().decode('utf-8')
                    logger.debug(f"[HamClock] VOACAP response: {data[:500]}...")
                    voacap_data = self._parse_voacap(data)

            except urllib.error.HTTPError as e:
                logger.debug(f"[HamClock] VOACAP HTTP error: {e.code}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"VOACAP not available (HTTP {e.code})")
                )
                return
            except urllib.error.URLError as e:
                logger.debug(f"[HamClock] VOACAP URL error: {e.reason}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"Connection error: {e.reason}")
                )
                return
            except Exception as e:
                logger.error(f"[HamClock] VOACAP fetch error: {e}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"VOACAP error: {e}")
                )
                return

            GLib.idle_add(self._update_voacap_display, voacap_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_voacap(self, data):
        """
        Parse VOACAP response from HamClock.

        HamClock VOACAP response format (example):
            Path=DE to DX
            UTC=14
            80m=23,12
            40m=67,24
            30m=89,32
            20m=95,38
            17m=78,28
            15m=45,18
            12m=12,8
            10m=5,2

        Where values are reliability%,SNR_dB

        Args:
            data: Raw text response from HamClock

        Returns:
            Dictionary with parsed VOACAP data
        """
        result = {
            'bands': {},
            'path': '',
            'utc': '',
            'raw': data
        }

        logger.debug(f"[HamClock] Parsing VOACAP data...")

        for line in data.strip().split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue

            key, value = line.split('=', 1)
            key = key.strip().lower()
            value = value.strip()

            if key == 'path':
                result['path'] = value
            elif key == 'utc':
                result['utc'] = value
            elif 'm' in key:
                # Band data (e.g., "80m", "40m")
                band_key = key.replace('m', 'm')  # Normalize
                try:
                    if ',' in value:
                        rel, snr = value.split(',', 1)
                        result['bands'][band_key] = {
                            'reliability': int(rel.strip()),
                            'snr': int(snr.strip())
                        }
                    else:
                        # Just reliability
                        result['bands'][band_key] = {
                            'reliability': int(value),
                            'snr': 0
                        }
                except ValueError as e:
                    logger.debug(f"[HamClock] Could not parse band {key}: {value} - {e}")

        logger.debug(f"[HamClock] Parsed VOACAP: {len(result['bands'])} bands, path={result['path']}")
        return result

    def _update_voacap_display(self, data):
        """Update the VOACAP display with parsed data"""
        logger.debug(f"[HamClock] Updating VOACAP display: {data}")

        if not data.get('bands'):
            self.status_label.set_label("No VOACAP data available")
            # Reset labels
            for band_key, labels in self.voacap_labels.items():
                labels['reliability'].set_label("--")
                labels['snr'].set_label("--")
            self.voacap_path_label.set_label("Path: --")
            return

        updated = 0

        for band_key, labels in self.voacap_labels.items():
            if band_key in data['bands']:
                band_data = data['bands'][band_key]
                rel = band_data.get('reliability', 0)
                snr = band_data.get('snr', 0)

                # Color code reliability
                if rel >= 80:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].remove_css_class("error")
                    labels['reliability'].add_css_class("success")
                elif rel >= 50:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("error")
                    labels['reliability'].add_css_class("warning")
                elif rel > 0:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].add_css_class("error")
                else:
                    rel_text = "Closed"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].add_css_class("error")

                labels['reliability'].set_label(rel_text)
                labels['snr'].set_label(f"{snr} dB" if snr else "--")
                updated += 1
            else:
                labels['reliability'].set_label("--")
                labels['snr'].set_label("--")

        # Update path info
        path_info = []
        if data.get('path'):
            path_info.append(data['path'])
        if data.get('utc'):
            path_info.append(f"UTC {data['utc']}:00")
        self.voacap_path_label.set_label(f"Path: {' | '.join(path_info) if path_info else '--'}")

        if updated > 0:
            self.status_label.set_label(f"VOACAP: {updated} bands updated")
        else:
            self.status_label.set_label("VOACAP data received but no bands parsed")

    def _on_open_voacap_online(self, button):
        """Open VOACAP Online in browser for detailed propagation analysis"""
        logger.info("[HamClock] VOACAP Online button clicked")
        # VOACAP Online - comprehensive HF propagation prediction
        url = "https://www.voacap.com/hf/"
        self._open_url_in_browser(url)

    # ==================== Auto-Refresh Methods ====================

    def _on_auto_refresh_toggled(self, switch, gparam):
        """Handle auto-refresh toggle"""
        enabled = switch.get_active()
        logger.info(f"[HamClock] Auto-refresh toggled: {enabled}")

        self._settings["auto_refresh_enabled"] = enabled
        self._save_settings()

        if enabled:
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()

    def _on_refresh_interval_changed(self, spinbutton):
        """Handle refresh interval change"""
        interval = int(spinbutton.get_value())
        logger.info(f"[HamClock] Refresh interval changed: {interval} minutes")

        self._settings["auto_refresh_minutes"] = interval
        self._save_settings()

        # Restart auto-refresh with new interval if enabled
        if self._settings.get("auto_refresh_enabled"):
            self._stop_auto_refresh()
            self._start_auto_refresh()

    def _start_auto_refresh(self):
        """Start the auto-refresh timer"""
        if self._auto_refresh_timer_id:
            # Already running
            return False

        interval_minutes = self._settings.get("auto_refresh_minutes", 10)
        interval_ms = interval_minutes * 60 * 1000

        logger.info(f"[HamClock] Starting auto-refresh every {interval_minutes} minutes")

        # Reset retry count
        self._retry_count = 0

        # Schedule periodic refresh
        self._auto_refresh_timer_id = GLib.timeout_add(interval_ms, self._do_auto_refresh)

        # Update UI
        self.status_label.set_label(f"Auto-refresh: every {interval_minutes} min")

        return False  # Don't repeat (used with GLib.timeout_add)

    def _stop_auto_refresh(self):
        """Stop the auto-refresh timer"""
        if self._auto_refresh_timer_id:
            GLib.source_remove(self._auto_refresh_timer_id)
            self._auto_refresh_timer_id = None
            logger.info("[HamClock] Auto-refresh stopped")
            self.status_label.set_label("Auto-refresh disabled")

    def _do_auto_refresh(self):
        """Perform auto-refresh (called by timer)"""
        logger.debug("[HamClock] Auto-refresh triggered")

        url = self._settings.get("url", "")
        if not url:
            logger.debug("[HamClock] No URL configured, skipping auto-refresh")
            return True  # Keep timer running

        # Perform the fetch
        self._fetch_space_weather_with_retry()

        return True  # Keep timer running

    def _fetch_space_weather_with_retry(self):
        """Fetch space weather with retry on failure"""
        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            return

        def fetch():
            api_url = f"{url}:{api_port}"
            weather_data = {}
            success = False

            logger.debug(f"[HamClock] Auto-refresh fetch from {api_url}...")

            endpoints = [
                ("get_sys.txt", self._parse_sys),
                ("get_spacewx.txt", self._parse_spacewx),
                ("get_bc.txt", self._parse_band_conditions),
            ]

            for endpoint, parser in endpoints:
                try:
                    full_url = f"{api_url}/{endpoint}"
                    req = urllib.request.Request(full_url, method='GET')
                    req.add_header('User-Agent', 'MeshForge/1.0')
                    with urllib.request.urlopen(req, timeout=10) as response:
                        data = response.read().decode('utf-8')
                        parsed = parser(data)
                        weather_data.update(parsed)
                        success = True
                except Exception as e:
                    logger.debug(f"[HamClock] Auto-refresh {endpoint}: {e}")

            if success:
                self._retry_count = 0
                GLib.idle_add(self._update_weather_display, weather_data)
            else:
                self._retry_count += 1
                if self._retry_count <= self._max_retries:
                    # Schedule retry with exponential backoff (2s, 4s, 8s)
                    backoff_ms = 2000 * (2 ** (self._retry_count - 1))
                    logger.info(f"[HamClock] Auto-refresh failed, retry {self._retry_count}/{self._max_retries} in {backoff_ms}ms")
                    GLib.timeout_add(backoff_ms, lambda: self._fetch_space_weather_with_retry() or False)
                else:
                    logger.warning(f"[HamClock] Auto-refresh failed after {self._max_retries} retries")
                    GLib.idle_add(
                        lambda: self.status_label.set_label("Auto-refresh failed - check connection")
                    )

        threading.Thread(target=fetch, daemon=True).start()

    def _check_data_freshness(self):
        """Check if data is stale and update the last-updated display"""
        if self._last_update_time:
            elapsed = time.time() - self._last_update_time
            elapsed_minutes = int(elapsed / 60)

            if elapsed_minutes < 1:
                time_str = "just now"
            elif elapsed_minutes == 1:
                time_str = "1 minute ago"
            elif elapsed_minutes < 60:
                time_str = f"{elapsed_minutes} minutes ago"
            else:
                hours = elapsed_minutes // 60
                time_str = f"{hours} hour{'s' if hours > 1 else ''} ago"

            # Check for stale data
            if elapsed_minutes >= self.STALE_DATA_THRESHOLD_MINUTES:
                self.last_update_label.set_label(f"Last updated: {time_str} (STALE)")
                self.last_update_label.add_css_class("warning")
            else:
                self.last_update_label.set_label(f"Last updated: {time_str}")
                self.last_update_label.remove_css_class("warning")
        else:
            self.last_update_label.set_label("Last updated: Never")

        return True  # Keep timer running

    def _record_update_time(self):
        """Record that data was updated"""
        self._last_update_time = time.time()
        self._check_data_freshness()  # Immediately update display

    def cleanup(self):
        """Clean up resources when panel is destroyed"""
        logger.debug("[HamClock] Cleaning up panel resources")
        self._stop_auto_refresh()
        if self._update_check_timer_id:
            GLib.source_remove(self._update_check_timer_id)
            self._update_check_timer_id = None
