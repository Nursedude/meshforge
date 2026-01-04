"""
HamClock Panel - Integration with HamClock for propagation and space weather

HamClock by Clear Sky Institute provides:
- VOACAP propagation predictions
- Solar flux and A/K index
- Gray line visualization
- DX cluster spots
- Satellite tracking

Reference: https://www.clearskyinstitute.com/ham/HamClock/
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
import threading
import json
import urllib.request
import urllib.error
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import WebKit for embedded view
try:
    gi.require_version('WebKit', '6.0')
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    try:
        gi.require_version('WebKit2', '4.1')
        from gi.repository import WebKit2 as WebKit
        HAS_WEBKIT = True
    except (ValueError, ImportError):
        HAS_WEBKIT = False


class HamClockPanel(Gtk.Box):
    """Panel for HamClock integration"""

    SETTINGS_FILE = Path.home() / ".config" / "meshforge" / "hamclock.json"

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.main_window = main_window
        self.webview = None

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._settings = self._load_settings()
        self._build_ui()

        # Auto-connect if URL is configured
        if self._settings.get("url"):
            GLib.timeout_add(500, self._auto_connect)

    def _load_settings(self):
        """Load HamClock settings"""
        defaults = {
            "url": "",
            "api_port": 8080,
            "live_port": 8081,
        }
        try:
            if self.SETTINGS_FILE.exists():
                with open(self.SETTINGS_FILE) as f:
                    saved = json.load(f)
                    defaults.update(saved)
        except Exception as e:
            logger.error(f"Error loading HamClock settings: {e}")
        return defaults

    def _save_settings(self):
        """Save HamClock settings"""
        try:
            self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.SETTINGS_FILE, 'w') as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving HamClock settings: {e}")

    def _build_ui(self):
        """Build the HamClock panel UI"""
        # Header
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
        port_box.append(self.api_port_spin)

        port_box.append(Gtk.Label(label="Live Port:"))

        self.live_port_spin = Gtk.SpinButton()
        self.live_port_spin.set_range(1, 65535)
        self.live_port_spin.set_value(self._settings.get("live_port", 8081))
        self.live_port_spin.set_increments(1, 10)
        port_box.append(self.live_port_spin)

        settings_box.append(port_box)
        settings_frame.set_child(settings_box)
        self.append(settings_frame)

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
        refresh_btn.connect("clicked", self._on_refresh)
        refresh_box.append(refresh_btn)
        weather_box.append(refresh_box)

        weather_frame.set_child(weather_box)
        self.append(weather_frame)

        # Live view (if WebKit available)
        if HAS_WEBKIT:
            view_frame = Gtk.Frame()
            view_frame.set_label("Live View")
            view_frame.set_vexpand(True)

            self.webview = WebKit.WebView()
            self.webview.set_vexpand(True)
            self.webview.set_hexpand(True)

            view_frame.set_child(self.webview)
            self.append(view_frame)
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
            open_btn.connect("clicked", self._on_open_browser)
            browser_box.append(open_btn)

            browser_frame.set_child(browser_box)
            self.append(browser_frame)

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

        # Remove trailing slash
        url = url.rstrip('/')

        # Save settings
        self._settings["url"] = url
        self._settings["api_port"] = int(self.api_port_spin.get_value())
        self._settings["live_port"] = int(self.live_port_spin.get_value())
        self._save_settings()

        self.status_label.set_label("Connecting...")

        def check_connection():
            api_url = f"{url}:{self._settings['api_port']}"

            try:
                # Try to get version or any API response
                req = urllib.request.Request(f"{api_url}/get_sys.txt", method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    GLib.idle_add(self._on_connected, url, data)
            except urllib.error.URLError as e:
                GLib.idle_add(self._on_connection_failed, str(e))
            except Exception as e:
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
        self.status_label.set_label(f"Connection failed: {error[:40]}")
        logger.error(f"HamClock connection failed: {error}")

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
            return

        def fetch():
            api_url = f"{url}:{api_port}"
            weather_data = {}

            # Try various HamClock endpoints
            endpoints = [
                ("get_sys.txt", self._parse_sys),
                ("get_spacewx.txt", self._parse_spacewx),
            ]

            for endpoint, parser in endpoints:
                try:
                    req = urllib.request.Request(f"{api_url}/{endpoint}", method='GET')
                    req.add_header('User-Agent', 'MeshForge/1.0')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        data = response.read().decode('utf-8')
                        parsed = parser(data)
                        weather_data.update(parsed)
                except Exception as e:
                    logger.debug(f"HamClock {endpoint} failed: {e}")

            GLib.idle_add(self._update_weather_display, weather_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_sys(self, data):
        """Parse system info response"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result

    def _parse_spacewx(self, data):
        """Parse space weather response"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().lower()
                value = value.strip()

                if 'sfi' in key or 'flux' in key:
                    result['sfi'] = value
                elif 'kp' in key:
                    result['kp'] = value
                elif 'a_index' in key or key == 'a':
                    result['a'] = value
                elif 'xray' in key:
                    result['xray'] = value
                elif 'ssn' in key or 'sunspot' in key:
                    result['sunspots'] = value
        return result

    def _update_weather_display(self, data):
        """Update the weather display with fetched data"""
        for key, label in self.stat_labels.items():
            if key in data:
                label.set_label(str(data[key]))
            # Also check for capitalized versions
            elif key.upper() in data:
                label.set_label(str(data[key.upper()]))

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
            except ValueError:
                pass

        self.status_label.set_label(f"Updated {len(data)} values")

    def _on_open_browser(self, button):
        """Open HamClock live view in browser"""
        import subprocess
        import os

        url = self._settings.get("url", "").rstrip('/')
        live_port = self._settings.get("live_port", 8081)

        if not url:
            self.status_label.set_label("Enter HamClock URL first")
            return

        live_url = f"{url}:{live_port}/live.html"
        user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

        try:
            subprocess.Popen(
                ['sudo', '-u', user, 'xdg-open', live_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.status_label.set_label("Opened in browser")
        except Exception as e:
            self.status_label.set_label(f"Failed to open browser: {e}")
