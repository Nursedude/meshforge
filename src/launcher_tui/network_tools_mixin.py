"""
Network Tools Mixin for MeshForge Launcher TUI.

Provides network testing and diagnostics methods extracted from main launcher
to reduce file size and improve maintainability.

Includes:
- Ping test
- Meshtastic device discovery
- DNS lookup
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class NetworkToolsMixin:
    """Mixin providing network diagnostic tools for the TUI launcher."""

    def _ping_test(self):
        """Run ping test."""
        host = self.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        self.dialog.infobox("Pinging", f"Pinging {host}...")

        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=15
            )

            # Parse results
            output = result.stdout
            if 'transmitted' in output:
                stats_line = [l for l in output.split('\n') if 'transmitted' in l]
                time_line = [l for l in output.split('\n') if 'rtt' in l or 'round-trip' in l]

                text = f"Ping {host}:\n\n"
                if stats_line:
                    text += stats_line[0] + "\n"
                if time_line:
                    text += time_line[0]

                self.dialog.msgbox("Ping Results", text)
            else:
                self.dialog.msgbox("Ping Failed", output[:500])

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Ping timed out")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _meshtastic_discovery(self):
        """Discover Meshtastic devices."""
        self.dialog.infobox("Discovery", "Scanning for Meshtastic devices...")

        devices = []

        # Check TCP localhost
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
                if sock.connect_ex(('localhost', 4403)) == 0:
                    devices.append("TCP: localhost:4403 (meshtasticd)")
            finally:
                sock.close()
        except Exception as e:
            logger.debug(f"Socket check for meshtasticd failed: {e}")

        # Check serial ports
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        for port in serial_ports:
            devices.append(f"Serial: {port}")

        if not devices:
            text = "No Meshtastic devices found.\n\nMake sure meshtasticd is running."
        else:
            # BLE hint
            devices.append("")
            devices.append("BLE devices require scanning:")
            devices.append("  meshtastic --ble-scan")
            text = "Found devices:\n\n" + "\n".join(devices)

        self.dialog.msgbox("Meshtastic Discovery", text)

    def _dns_lookup(self):
        """Perform DNS lookup."""
        host = self.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname.")
            return

        try:
            import socket
            results = []
            for info in socket.getaddrinfo(host, None):
                addr = info[4][0]
                if addr not in [r.split(': ')[1] for r in results if ': ' in r]:
                    family = "IPv4" if info[0] == socket.AF_INET else "IPv6"
                    results.append(f"{family}: {addr}")

            self.dialog.msgbox(f"DNS: {host}", "\n".join(results) or "No results")
        except socket.gaierror as e:
            self.dialog.msgbox("Error", f"DNS lookup failed:\n{e}")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))
