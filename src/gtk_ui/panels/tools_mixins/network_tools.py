"""
Network Tools Mixin - Basic network testing utilities

Provides ping, port testing, and device scanning functionality.
Extracted from tools.py for maintainability.
"""

import socket
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib


class NetworkToolsMixin:
    """Mixin providing network testing functionality for ToolsPanel"""

    def _on_ping_test(self, button):
        """Show ping test dialog"""
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
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._log, f"Ping to {host} timed out")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_port_test(self, button):
        """Show TCP port test dialog"""
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
        except socket.timeout:
            GLib.idle_add(self._log, f"Connection to {host}:{port} timed out")
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

            GLib.idle_add(self._log, f"Scanning {base}.1-254...")

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

    def _refresh_status(self):
        """Refresh network status (can be overridden)"""
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()

    def _refresh_status_thread(self):
        """Check network status in background"""
        # Check meshtasticd port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            meshtastic_status = "Connected" if result == 0 else "Not Connected"
        except Exception:
            meshtastic_status = "Error"

        if hasattr(self, 'mesh_status_label'):
            GLib.idle_add(self.mesh_status_label.set_label, meshtastic_status)
