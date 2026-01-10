"""
HF Propagation Mixin - Solar data and HF band conditions

Provides solar flux, K-index, and HF propagation tools for amateur radio.
Extracted from tools.py for maintainability.
"""

import threading
import urllib.request
import urllib.error
import webbrowser
import xml.etree.ElementTree as ET

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib


class HFPropagationMixin:
    """Mixin providing HF propagation tools for ToolsPanel"""

    # HamQSL solar data URL
    HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"

    def _on_refresh_solar(self, button=None):
        """Fetch current solar conditions"""
        threading.Thread(target=self._fetch_solar_data, daemon=True).start()

    def _fetch_solar_data(self):
        """Fetch solar data in background"""
        GLib.idle_add(self._log, "Fetching solar data from HamQSL...")

        try:
            req = urllib.request.Request(
                self.HAMQSL_URL,
                headers={'User-Agent': 'MeshForge/1.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            solar = root.find('.//solardata')

            if solar is not None:
                data = {
                    'sfi': solar.findtext('solarflux', '--'),
                    'sunspots': solar.findtext('sunspots', '--'),
                    'aindex': solar.findtext('aindex', '--'),
                    'kindex': solar.findtext('kindex', '--'),
                    'xray': solar.findtext('xray', '--'),
                    'protonflux': solar.findtext('protonflux', '--'),
                    'updated': solar.findtext('updated', '--'),
                }
                GLib.idle_add(self._update_solar_display, data)
            else:
                GLib.idle_add(self._log, "Could not parse solar data")

        except urllib.error.URLError as e:
            GLib.idle_add(self._log, f"Network error: {e}")
        except ET.ParseError as e:
            GLib.idle_add(self._log, f"XML parse error: {e}")
        except Exception as e:
            GLib.idle_add(self._log, f"Error fetching solar data: {e}")

    def _update_solar_display(self, data: dict):
        """Update UI with solar data"""
        self._log("\n=== Solar Conditions ===")
        self._log(f"  Solar Flux Index (SFI): {data['sfi']}")
        self._log(f"  Sunspot Number: {data['sunspots']}")
        self._log(f"  A-Index: {data['aindex']}")
        self._log(f"  K-Index: {data['kindex']}")
        self._log(f"  X-Ray: {data['xray']}")
        self._log(f"  Updated: {data['updated']}")

        # Interpret conditions
        try:
            sfi = int(data['sfi'])
            if sfi >= 150:
                self._log("  Conditions: EXCELLENT")
            elif sfi >= 100:
                self._log("  Conditions: GOOD")
            elif sfi >= 70:
                self._log("  Conditions: FAIR")
            else:
                self._log("  Conditions: POOR")
        except ValueError:
            pass

        # Update labels if they exist
        if hasattr(self, 'sfi_label'):
            GLib.idle_add(self.sfi_label.set_label, str(data['sfi']))
        if hasattr(self, 'kindex_label'):
            GLib.idle_add(self.kindex_label.set_label, str(data['kindex']))

    def _on_voacap_online(self, button=None):
        """Open VOACAP Online propagation predictor"""
        url = "https://www.voacap.com/hf/"
        self._open_url_in_browser(url, "VOACAP Online")

    def _on_hf_conditions(self, button=None):
        """Open HF conditions page"""
        url = "https://www.hamqsl.com/solar.html"
        self._open_url_in_browser(url, "HF Conditions")

    def _on_psk_reporter(self, button=None):
        """Open PSK Reporter map"""
        url = "https://pskreporter.info/pskmap.html"
        self._open_url_in_browser(url, "PSK Reporter")

    def _on_dx_maps(self, button=None):
        """Open DX Maps"""
        url = "https://www.dxmaps.com/"
        self._open_url_in_browser(url, "DX Maps")

    def _on_solar_data(self, button=None):
        """Open NOAA space weather"""
        url = "https://www.swpc.noaa.gov/"
        self._open_url_in_browser(url, "NOAA Space Weather")

    def _on_hamqsl(self, button=None):
        """Open HamQSL widget"""
        url = "https://www.hamqsl.com/solar.html"
        self._open_url_in_browser(url, "HamQSL")

    def _on_contest_calendar(self, button=None):
        """Open contest calendar"""
        url = "https://www.contestcalendar.com/"
        self._open_url_in_browser(url, "Contest Calendar")

    def _open_url_in_browser(self, url: str, description: str = ""):
        """Open URL in default browser (helper method)"""
        try:
            webbrowser.open(url)
            if description:
                GLib.idle_add(self._log, f"Opened {description} in browser")
        except Exception as e:
            GLib.idle_add(self._log, f"Error opening browser: {e}")
