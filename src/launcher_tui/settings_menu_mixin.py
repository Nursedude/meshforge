"""
Settings Menu Mixin - Application settings handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

from utils.safe_import import safe_import

MapDataCollector, _HAS_MAP_DATA_COLLECTOR = safe_import(
    'utils.map_data_collector', 'MapDataCollector'
)
from commands import propagation


class SettingsMenuMixin:
    """Mixin providing application settings functionality."""

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("propagation", "Propagation Data Sources"),
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

            dispatch = {
                "connection": ("Connection Settings", self._configure_connection),
                "propagation": ("Propagation Sources", self._configure_propagation_sources),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

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
        if not _HAS_MAP_DATA_COLLECTOR:
            return  # MapDataCollector not available
        collector = MapDataCollector()
        collector.set_meshtasticd_connection(host, port)

    def _configure_propagation_sources(self):
        """Configure propagation data sources.

        NOAA SWPC is always active (primary). Users can optionally
        enable HamClock or OpenHamClock for enhanced data.
        """
        while True:
            choices = [
                ("noaa", "NOAA SWPC (Primary - always active)"),
                ("pskreporter", "PSKReporter MQTT (Real-time spots)"),
                ("openhamclock", "OpenHamClock (Optional)"),
                ("hamclock", "HamClock Legacy (Optional)"),
                ("test", "Test All Sources"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Propagation Data Sources",
                "NOAA is always active as primary source.\n"
                "PSKReporter provides real-time HF spots via MQTT.\n"
                "OpenHamClock adds VOACAP, DX spots.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "noaa": ("NOAA SWPC Test", self._test_noaa_source),
                "pskreporter": ("PSKReporter Config", self._configure_pskreporter),
                "openhamclock": ("OpenHamClock Config", self._configure_openhamclock),
                "hamclock": ("HamClock Legacy Config", self._configure_hamclock_legacy),
                "test": ("Test All Sources", self._test_all_sources),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _test_noaa_source(self):
        """Test NOAA SWPC connectivity and show current data."""
        result = propagation.check_source(propagation.DataSource.NOAA)
        if result.success:
            # Also get current conditions
            wx = propagation.get_space_weather()
            if wx.success:
                d = wx.data
                lines = [
                    "NOAA SWPC - Connected",
                    "",
                    f"Solar Flux (SFI): {d.get('solar_flux', 'N/A')}",
                    f"Kp Index: {d.get('k_index', 'N/A')}",
                    f"A Index: {d.get('a_index', 'N/A')}",
                    f"X-ray: {d.get('xray_flux', 'N/A')}",
                    f"Geomagnetic: {d.get('geomag_storm', 'N/A')}",
                    "",
                    "Band Conditions:",
                ]
                for band, cond in d.get('band_conditions', {}).items():
                    lines.append(f"  {band}: {cond}")
                self.dialog.msgbox("NOAA Space Weather", "\n".join(lines))
            else:
                self.dialog.msgbox("NOAA SWPC", "Connected but no data available.")
        else:
            self.dialog.msgbox("Error", f"Cannot reach NOAA SWPC:\n{result.message}")

    def _configure_pskreporter(self):
        """Configure PSKReporter MQTT as propagation data source."""
        while True:
            # Get current config
            pskr_cfg = propagation._sources.get(propagation.DataSource.PSKREPORTER)
            current_call = pskr_cfg.callsign if pskr_cfg else ""
            current_bands = ", ".join(pskr_cfg.bands) if pskr_cfg and pskr_cfg.bands else "all"
            is_enabled = pskr_cfg.enabled if pskr_cfg else False
            status = "ENABLED" if is_enabled else "disabled"

            choices = [
                ("enable", f"Enable/Disable (currently: {status})"),
                ("callsign", f"Set Callsign Filter ({current_call or 'none'})"),
                ("bands", f"Set Band Filter ({current_bands})"),
                ("test", "Test Connection"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "PSKReporter MQTT",
                "Real-time amateur radio reception reports\n"
                "from mqtt.pskreporter.info (by M0LTE).\n"
                "Provides band activity & propagation data.\n"
                f"\nStatus: {status}  Callsign: {current_call or 'all'}",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "enable":
                new_state = not is_enabled
                result = propagation.configure_source(
                    propagation.DataSource.PSKREPORTER,
                    enabled=new_state,
                    callsign=current_call,
                    bands=pskr_cfg.bands if pskr_cfg else [],
                    modes=pskr_cfg.modes if pskr_cfg else [],
                )
                state_str = "enabled" if new_state else "disabled"
                self.dialog.msgbox(
                    "PSKReporter",
                    f"PSKReporter MQTT {state_str}.\n\n"
                    f"{'Spots will stream from mqtt.pskreporter.info' if new_state else 'Feed stopped.'}"
                )

            elif choice == "callsign":
                call = self.dialog.inputbox(
                    "Callsign Filter",
                    "Enter your callsign to filter spots:\n"
                    "(Leave empty to monitor all spots)\n\n"
                    "Examples: WH6GXZ, KH6RS",
                    current_call,
                )
                if call is not None:
                    propagation.configure_source(
                        propagation.DataSource.PSKREPORTER,
                        enabled=is_enabled,
                        callsign=call.strip(),
                        bands=pskr_cfg.bands if pskr_cfg else [],
                        modes=pskr_cfg.modes if pskr_cfg else [],
                    )

            elif choice == "bands":
                bands_input = self.dialog.inputbox(
                    "Band Filter",
                    "Enter bands to monitor (comma-separated):\n"
                    "(Leave empty for all bands)\n\n"
                    "Examples: 20m,40m,15m",
                    ", ".join(pskr_cfg.bands) if pskr_cfg and pskr_cfg.bands else "",
                )
                if bands_input is not None:
                    bands = [b.strip() for b in bands_input.split(",") if b.strip()]
                    propagation.configure_source(
                        propagation.DataSource.PSKREPORTER,
                        enabled=is_enabled,
                        callsign=current_call,
                        bands=bands,
                        modes=pskr_cfg.modes if pskr_cfg else [],
                    )

            elif choice == "test":
                result = propagation.check_source(propagation.DataSource.PSKREPORTER)
                if result.success:
                    self.dialog.msgbox(
                        "PSKReporter Connected",
                        f"{result.message}\n\n"
                        f"Spots: {result.data.get('spots_received', 0)}\n"
                        f"Bands active: {result.data.get('bands_active', 0)}"
                    )
                else:
                    self.dialog.msgbox(
                        "PSKReporter",
                        f"{result.message}\n\n"
                        "Ensure PSKReporter is enabled and\n"
                        "paho-mqtt is installed."
                    )

    def _configure_openhamclock(self):
        """Configure OpenHamClock as optional data source."""
        host = self.dialog.inputbox(
            "OpenHamClock Host",
            "Enter OpenHamClock hostname or IP:\n"
            "(Docker: localhost, Remote: IP address)",
            "localhost"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.dialog.inputbox(
            "OpenHamClock Port",
            "Enter port (default 3000):",
            "3000"
        )

        if not port:
            return

        if not self._validate_port(port):
            self.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        result = propagation.configure_source(
            propagation.DataSource.OPENHAMCLOCK,
            host=host,
            port=int(port),
        )
        if result.success:
            # Test connectivity
            test = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
            if test.success:
                self.dialog.msgbox(
                    "OpenHamClock Connected",
                    f"API: {host}:{port}\n\nOpenHamClock is now active as\n"
                    "an enhanced data source."
                )
            else:
                self.dialog.msgbox(
                    "OpenHamClock Configured",
                    f"Saved: {host}:{port}\n\n"
                    f"Connection test failed:\n{test.message}\n\n"
                    "Make sure OpenHamClock is running\n"
                    "(docker compose up)"
                )
        else:
            self.dialog.msgbox("Error", result.message)

    def _configure_hamclock_legacy(self):
        """Configure legacy HamClock as optional data source."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:\n"
            "(NOTE: Original HamClock sunsets June 2026)",
            "localhost"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.dialog.inputbox(
            "HamClock API Port",
            "Enter API port (default 8080):",
            "8080"
        )

        if not port:
            return

        if not self._validate_port(port):
            self.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        result = propagation.configure_source(
            propagation.DataSource.HAMCLOCK,
            host=host,
            port=int(port),
        )
        if result.success:
            test = propagation.check_source(propagation.DataSource.HAMCLOCK)
            if test.success:
                self.dialog.msgbox(
                    "HamClock Connected",
                    f"API: {host}:{port}\n\nHamClock is now active as\n"
                    "an enhanced data source.\n\n"
                    "NOTE: Consider migrating to OpenHamClock\n"
                    "(original HamClock sunsets June 2026)"
                )
            else:
                self.dialog.msgbox(
                    "HamClock Configured",
                    f"Saved: {host}:{port}\n\n"
                    f"Connection test failed:\n{test.message}\n\n"
                    "Make sure HamClock is running."
                )
        else:
            self.dialog.msgbox("Error", result.message)

    def _test_all_sources(self):
        """Test all configured propagation data sources."""
        lines = ["Propagation Source Status", "=" * 35, ""]

        # NOAA (always)
        noaa = propagation.check_source(propagation.DataSource.NOAA)
        status = "Connected" if noaa.success else "Unreachable"
        lines.append(f"NOAA SWPC (primary): {status}")

        # PSKReporter
        pskr = propagation.check_source(propagation.DataSource.PSKREPORTER)
        if pskr.success:
            spots = pskr.data.get('spots_received', 0)
            lines.append(f"PSKReporter MQTT: Connected ({spots} spots)")
        else:
            lines.append(f"PSKReporter MQTT: Not configured")

        # OpenHamClock
        ohc = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
        if ohc.success:
            lines.append(f"OpenHamClock: Connected")
        else:
            lines.append(f"OpenHamClock: Not configured")

        # HamClock
        hc = propagation.check_source(propagation.DataSource.HAMCLOCK)
        if hc.success:
            lines.append(f"HamClock (legacy): Connected")
        else:
            lines.append(f"HamClock (legacy): Not configured")

        # Current data
        wx = propagation.get_space_weather()
        if wx.success:
            d = wx.data
            lines.append("")
            lines.append("-" * 35)
            lines.append("Current Conditions:")
            sfi = d.get('solar_flux')
            kp = d.get('k_index')
            if sfi:
                lines.append(f"  SFI: {int(sfi)}")
            if kp is not None:
                lines.append(f"  Kp: {kp}")
            lines.append(f"  {d.get('geomag_storm', '')}")

        self.dialog.msgbox("Source Status", "\n".join(lines))
