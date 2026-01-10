"""
SDR Tools Mixin - Software Defined Radio utilities

Provides OpenWebRX, RTL-SDR, GQRX, and CubicSDR integration.
Extracted from tools.py for maintainability.
"""

import shutil
import subprocess
import threading
import webbrowser

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib


class SDRToolsMixin:
    """Mixin providing SDR tool functionality for ToolsPanel"""

    def _check_sdr_status(self):
        """Check status of SDR tools"""
        status = {
            'openwebrx': False,
            'rtl_sdr': False,
            'gqrx': False,
            'cubicsdr': False,
        }

        # Check OpenWebRX
        if shutil.which('openwebrx'):
            status['openwebrx'] = True

        # Check RTL-SDR
        if shutil.which('rtl_test'):
            status['rtl_sdr'] = True

        # Check GQRX
        if shutil.which('gqrx'):
            status['gqrx'] = True

        # Check CubicSDR
        if shutil.which('CubicSDR'):
            status['cubicsdr'] = True

        return status

    def _on_open_webrx(self, button=None):
        """Open OpenWebRX in browser"""
        url = "http://localhost:8073"
        try:
            webbrowser.open(url)
            GLib.idle_add(self._log, "Opening OpenWebRX at http://localhost:8073")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_install_openwebrx(self, button=None):
        """Show OpenWebRX installation instructions"""
        GLib.idle_add(self._log, "\n=== OpenWebRX Installation ===")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "Option 1: Docker (recommended)")
        GLib.idle_add(self._log, "  docker run -d -p 8073:8073 jketterl/openwebrx")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "Option 2: Manual installation")
        GLib.idle_add(self._log, "  sudo apt install openwebrx")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "See: https://www.openwebrx.de/")

    def _on_rtl_test(self, button=None):
        """Test RTL-SDR device"""
        threading.Thread(target=self._run_rtl_test, daemon=True).start()

    def _run_rtl_test(self):
        """Run RTL-SDR test in background"""
        GLib.idle_add(self._log, "Testing RTL-SDR device...")

        if not shutil.which('rtl_test'):
            GLib.idle_add(self._log, "rtl_test not found. Install with:")
            GLib.idle_add(self._log, "  sudo apt install rtl-sdr")
            return

        try:
            result = subprocess.run(
                ['rtl_test', '-t'],
                capture_output=True, text=True, timeout=10
            )
            GLib.idle_add(self._log, result.stdout)
            if result.returncode != 0:
                GLib.idle_add(self._log, result.stderr)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._log, "RTL-SDR test timed out")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_sdr_config(self, button=None):
        """Show SDR configuration help"""
        GLib.idle_add(self._log, "\n=== SDR Configuration ===")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "RTL-SDR udev rules:")
        GLib.idle_add(self._log, "  sudo wget -O /etc/udev/rules.d/rtl-sdr.rules \\")
        GLib.idle_add(self._log, "    https://raw.githubusercontent.com/osmocom/rtl-sdr/master/rtl-sdr.rules")
        GLib.idle_add(self._log, "  sudo udevadm control --reload-rules")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "Blacklist kernel DVB driver:")
        GLib.idle_add(self._log, "  echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf")
        GLib.idle_add(self._log, "  sudo modprobe -r dvb_usb_rtl28xxu")

    def _on_launch_gqrx(self, button=None):
        """Launch GQRX"""
        if not shutil.which('gqrx'):
            GLib.idle_add(self._log, "GQRX not installed. Install with:")
            GLib.idle_add(self._log, "  sudo apt install gqrx-sdr")
            return

        try:
            subprocess.Popen(['gqrx'], start_new_session=True)
            GLib.idle_add(self._log, "Launching GQRX...")
        except Exception as e:
            GLib.idle_add(self._log, f"Error launching GQRX: {e}")

    def _on_launch_cubicsdr(self, button=None):
        """Launch CubicSDR"""
        if not shutil.which('CubicSDR'):
            GLib.idle_add(self._log, "CubicSDR not installed. Install with:")
            GLib.idle_add(self._log, "  sudo apt install cubicsdr")
            return

        try:
            subprocess.Popen(['CubicSDR'], start_new_session=True)
            GLib.idle_add(self._log, "Launching CubicSDR...")
        except Exception as e:
            GLib.idle_add(self._log, f"Error launching CubicSDR: {e}")

    def _on_spectrum_scan(self, button=None):
        """Run spectrum scan with rtl_power"""
        threading.Thread(target=self._run_spectrum_scan, daemon=True).start()

    def _run_spectrum_scan(self):
        """Run 915 MHz spectrum scan in background"""
        GLib.idle_add(self._log, "Running 915 MHz spectrum scan...")

        if not shutil.which('rtl_power'):
            GLib.idle_add(self._log, "rtl_power not found. Install with:")
            GLib.idle_add(self._log, "  sudo apt install rtl-sdr")
            return

        try:
            # Quick scan of 915 MHz ISM band
            result = subprocess.run(
                ['rtl_power', '-f', '902M:928M:100k', '-g', '40', '-i', '1', '-e', '5'],
                capture_output=True, text=True, timeout=30
            )
            if result.stdout:
                GLib.idle_add(self._log, "Scan complete. Sample output:")
                lines = result.stdout.strip().split('\n')[:5]
                for line in lines:
                    GLib.idle_add(self._log, f"  {line[:80]}")
            if result.returncode != 0 and result.stderr:
                GLib.idle_add(self._log, result.stderr)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._log, "Spectrum scan complete (timeout)")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")
