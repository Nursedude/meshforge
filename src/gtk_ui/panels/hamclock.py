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
import threading
import subprocess
import urllib.request
import urllib.error
import logging
from pathlib import Path
import os

logger = logging.getLogger(__name__)

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
    }

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.webview = None

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

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

    def _load_settings_legacy(self):
        """Legacy settings load for fallback"""
        import json
        settings_file = Path.home() / ".config" / "meshforge" / "hamclock.json"
        defaults = self.SETTINGS_DEFAULTS.copy()
        try:
            if settings_file.exists():
                with open(settings_file) as f:
                    saved = json.load(f)
                    defaults.update(saved)
        except Exception as e:
            logger.error(f"Error loading HamClock settings: {e}")
        return defaults

    def _save_settings(self):
        """Save HamClock settings"""
        if HAS_SETTINGS_MANAGER:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()
        else:
            # Legacy fallback
            import json
            settings_file = Path.home() / ".config" / "meshforge" / "hamclock.json"
            try:
                settings_file.parent.mkdir(parents=True, exist_ok=True)
                with open(settings_file, 'w') as f:
                    json.dump(self._settings, f, indent=2)
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

        # Refresh button
        refresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        refresh_box.set_margin_top(10)
        refresh_btn = Gtk.Button(label="Refresh Data")
        refresh_btn.set_tooltip_text("Refresh space weather data from HamClock")
        refresh_btn.connect("clicked", self._on_refresh)
        refresh_box.append(refresh_btn)
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
            # Open in browser button
            browser_frame = Gtk.Frame()
            browser_frame.set_label("Live View")
            browser_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            browser_box.set_margin_start(15)
            browser_box.set_margin_end(15)
            browser_box.set_margin_top(10)
            browser_box.set_margin_bottom(10)

            info_label = Gtk.Label(label="WebKit not available - open HamClock in browser")
            info_label.add_css_class("dim-label")
            browser_box.append(info_label)

            open_btn = Gtk.Button(label="Open HamClock in Browser")
            open_btn.set_tooltip_text("Open the HamClock live view in your default browser")
            open_btn.connect("clicked", self._on_open_browser)
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

        # Link to hamclock-systemd
        link_btn = Gtk.LinkButton.new_with_label(
            "https://github.com/pa28/hamclock-systemd",
            "hamclock-systemd packages"
        )
        install_row.append(link_btn)

        # Or official site
        official_link = Gtk.LinkButton.new_with_label(
            "https://www.clearskyinstitute.com/ham/HamClock/",
            "Official HamClock"
        )
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

                except Exception:
                    pass

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
                except Exception:
                    pass

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

    def _auto_connect(self):
        """Auto-connect on startup"""
        self._on_connect(None)
        return False  # Don't repeat

    def _on_connect(self, button):
        """Connect to HamClock"""
        url = self.url_entry.get_text().strip()
        if not url:
            self.status_label.set_label("Enter HamClock URL")
            return

        # Add http:// prefix if missing
        if not url.startswith('http://') and not url.startswith('https://'):
            url = f'http://{url}'

        # Remove trailing slash
        url = url.rstrip('/')

        # Save settings
        self._settings["url"] = url
        self._settings["api_port"] = int(self.api_port_spin.get_value())
        self._settings["live_port"] = int(self.live_port_spin.get_value())
        self._save_settings()

        # Update entry with corrected URL
        self.url_entry.set_text(url)

        self.status_label.set_label("Connecting...")
        logger.debug(f"[HamClock] Connecting to {url}...")

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
        """Handle connection failure"""
        # Truncate long error messages for display
        short_error = str(error)[:50]
        self.status_label.set_label(f"Not connected")
        # Only log once, not spam
        logger.debug(f"HamClock connection failed: {error}")

    def _on_refresh(self, button):
        """Refresh space weather data"""
        if not self._settings.get("url"):
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
        else:
            self.status_label.set_label("No data received")
            logger.debug("[HamClock] No values to update")

    def _on_open_browser(self, button):
        """Open HamClock live view in browser"""
        url = self._settings.get("url", "").rstrip('/')
        live_port = self._settings.get("live_port", 8081)

        if not url:
            self.status_label.set_label("Enter HamClock URL first")
            return

        live_url = f"{url}:{live_port}/live.html"
        self._open_url_in_browser(live_url)

    def _open_url_in_browser(self, url):
        """Open a URL in the user's default browser (handles running as root)"""
        logger.debug(f"[HamClock] Opening URL: {url}")

        user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        display = os.environ.get('DISPLAY', ':0')

        # Try multiple browser open methods
        methods = [
            # Method 1: Run as original user with their display
            lambda: subprocess.Popen(
                ['sudo', '-u', user, 'env', f'DISPLAY={display}', 'xdg-open', url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ),
            # Method 2: Direct xdg-open (if not root)
            lambda: subprocess.Popen(
                ['xdg-open', url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ),
            # Method 3: Try specific browsers
            lambda: subprocess.Popen(
                ['sudo', '-u', user, 'chromium-browser', url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ),
            lambda: subprocess.Popen(
                ['sudo', '-u', user, 'firefox', url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ),
        ]

        for i, method in enumerate(methods):
            try:
                method()
                self.status_label.set_label(f"Opened in browser")
                logger.debug(f"[HamClock] Browser opened using method {i+1}")
                return
            except Exception as e:
                logger.debug(f"[HamClock] Method {i+1} failed: {e}")
                continue

        self.status_label.set_label("Could not open browser - copy URL manually")
        logger.debug(f"[HamClock] All browser methods failed for {url}")

    def _on_fetch_noaa(self, button):
        """Fetch space weather data from NOAA"""
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
        # N0NBH Solar-Terrestrial Data page
        url = "https://www.hamqsl.com/solar.html"
        self._open_url_in_browser(url)
