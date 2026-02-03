"""
Settings Menu Mixin - Application settings handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""


class SettingsMenuMixin:
    """Mixin providing application settings functionality."""

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("hamclock", "HamClock Settings"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Settings",
                "Configure MeshForge:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "connection":
                self._configure_connection()
            elif choice == "hamclock":
                self._configure_hamclock()

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "localhost":
            self._save_meshtasticd_connection("localhost", 4403)
            self.dialog.msgbox("Connection", "Connection set to localhost:4403")
        elif choice == "serial":
            port = self.dialog.inputbox("Serial Port", "Enter serial port:", "/dev/ttyUSB0")
            if port:
                self.dialog.msgbox("Connection", f"Connection set to {port}")
        elif choice == "remote":
            host_input = self.dialog.inputbox("Remote Host", "Enter host:port:", "192.168.1.100:4403")
            if host_input:
                # Parse host:port
                if ':' in host_input:
                    parts = host_input.rsplit(':', 1)
                    host = parts[0]
                    try:
                        port = int(parts[1])
                    except ValueError:
                        port = 4403
                else:
                    host = host_input
                    port = 4403
                self._save_meshtasticd_connection(host, port)
                self.dialog.msgbox("Connection", f"Connection set to {host}:{port}")

    def _save_meshtasticd_connection(self, host: str, port: int):
        """Save meshtasticd connection settings for MapDataCollector."""
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector()
            collector.set_meshtasticd_connection(host, port)
        except ImportError:
            pass  # MapDataCollector not available

    def _configure_hamclock(self):
        """Configure HamClock settings - test API connection."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:",
            "localhost"
        )

        if host:
            if not self._validate_hostname(host):
                self.dialog.msgbox("Error", "Invalid hostname or IP address.")
                return

            port = self.dialog.inputbox(
                "HamClock API Port",
                "Enter API port (default 8082):",
                "8082"
            )

            if port:
                if not self._validate_port(port):
                    self.dialog.msgbox("Error", "Invalid port number (1-65535).")
                    return

                try:
                    import urllib.request
                    url = f"http://{host}:{port}/get_de.txt"
                    req = urllib.request.urlopen(url, timeout=5)
                    data = req.read().decode()
                    self.dialog.msgbox("HamClock Connected", f"API: {host}:{port}\n\nDE Station:\n{data}")
                except Exception as e:
                    self.dialog.msgbox("Error", f"Cannot reach HamClock at {host}:{port}\n\n{e}\n\nMake sure HamClock is running.")
