"""
Settings Handler — Application settings and configuration.

Converted from settings_menu_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

MapDataCollector, _HAS_MAP_DATA_COLLECTOR = safe_import(
    'utils.map_data_collector', 'MapDataCollector'
)
from commands import propagation


class SettingsHandler(BaseHandler):
    """TUI handler for MeshForge application settings."""

    handler_id = "settings"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("meshforge", "MeshForge Settings  App preferences", None),
        ]

    def execute(self, action):
        if action == "meshforge":
            self._settings_menu()

    def _settings_menu(self):
        """Settings menu - connection, sources, logging, profile."""
        while True:
            choices = [
                ("connection", "Meshtastic Connection   TCP, serial, remote"),
                ("propagation", "Propagation Sources     NOAA, HamClock, PSK"),
                ("loglevel", "Log Level               Adjust verbosity"),
                ("profile", "Deployment Profile      Select feature set"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshForge Settings",
                "Application configuration:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "connection": ("Connection Settings", self._configure_connection),
                "propagation": ("Propagation Sources", self._configure_propagation_sources),
                "loglevel": ("Log Level", self._configure_log_level),
                "profile": ("Deployment Profile", self._configure_deployment_profile),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _configure_log_level(self):
        """Configure application log verbosity."""
        from utils.logging_config import set_log_level, _component_levels, _global_log_level

        current_name = logging.getLevelName(_global_log_level)

        while True:
            choices = [
                ("global", f"Global Level            Currently: {current_name}"),
                ("gateway", "Gateway Bridge          " + logging.getLevelName(_component_levels.get('gateway', logging.DEBUG))),
                ("meshtastic", "Meshtastic              " + logging.getLevelName(_component_levels.get('meshtastic', logging.INFO))),
                ("rns", "RNS / Reticulum         " + logging.getLevelName(_component_levels.get('rns', logging.DEBUG))),
                ("hamclock", "HamClock                " + logging.getLevelName(_component_levels.get('hamclock', logging.DEBUG))),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Log Level Configuration",
                "Adjust logging verbosity.\nLogs are written to ~/.config/meshforge/logs/",
                choices
            )

            if choice is None or choice == "back":
                break

            level_choices = [
                ("DEBUG", "Debug        Most verbose, all details"),
                ("INFO", "Info         Normal operation messages"),
                ("WARNING", "Warning      Potential issues only"),
                ("ERROR", "Error        Errors only"),
            ]

            selected = self.ctx.dialog.menu(
                f"Set {choice.title()} Log Level",
                "Select verbosity level:",
                level_choices
            )

            if selected and selected in ("DEBUG", "INFO", "WARNING", "ERROR"):
                level = getattr(logging, selected)
                if choice == "global":
                    set_log_level(level)
                    current_name = selected
                else:
                    set_log_level(level, component=choice)
                self.ctx.dialog.msgbox(
                    "Log Level Updated",
                    f"{choice.title()} log level set to {selected}.\n\n"
                    "Change takes effect immediately for this session."
                )

    def _configure_deployment_profile(self):
        """Select deployment profile."""
        profiles = [
            ("full", "Full Install        All features enabled"),
            ("gateway", "Gateway Bridge      Meshtastic + RNS bridge"),
            ("monitor", "Monitor             MQTT monitoring only"),
            ("radio_maps", "Radio + Maps        Radio config + coverage"),
            ("meshcore", "MeshCore            Companion radio only"),
        ]

        current = "full"
        if self.ctx.profile:
            current = getattr(self.ctx.profile, 'name', 'full')

        choice = self.ctx.dialog.menu(
            "Deployment Profile",
            f"Current profile: {current}\n"
            "Profiles control which menu sections are visible.",
            profiles
        )

        if choice and choice != current:
            try:
                from utils.deployment_profiles import get_profile, save_profile
                profile = get_profile(choice)
                save_profile(choice)
                self.ctx.profile = profile
                self.ctx.feature_flags = getattr(profile, 'feature_flags', {})
                self.ctx.dialog.msgbox(
                    "Profile Updated",
                    f"Deployment profile set to: {choice}\n\n"
                    "Menu sections will update on next navigation.\n"
                    "Restart MeshForge for full effect."
                )
            except Exception as e:
                self.ctx.dialog.msgbox("Error", f"Failed to set profile:\n{e}")

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "localhost":
            self._save_meshtasticd_connection("localhost", 4403)
            self.ctx.dialog.msgbox("Connection", "Connection set to localhost:4403")
        elif choice == "serial":
            port = self.ctx.dialog.inputbox("Serial Port", "Enter serial port:", "/dev/ttyUSB0")
            if port:
                self.ctx.dialog.msgbox("Connection", f"Connection set to {port}")
        elif choice == "remote":
            host_input = self.ctx.dialog.inputbox("Remote Host", "Enter host:port:", "192.168.1.100:4403")
            if host_input:
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
                self.ctx.dialog.msgbox("Connection", f"Connection set to {host}:{port}")

    def _save_meshtasticd_connection(self, host: str, port: int):
        """Save meshtasticd connection settings."""
        if not _HAS_MAP_DATA_COLLECTOR:
            return
        collector = MapDataCollector()
        collector.set_meshtasticd_connection(host, port)

    def _configure_propagation_sources(self):
        """Configure propagation data sources."""
        while True:
            choices = [
                ("noaa", "NOAA SWPC (Primary - always active)"),
                ("pskreporter", "PSKReporter MQTT (Real-time spots)"),
                ("openhamclock", "OpenHamClock (Optional)"),
                ("hamclock", "HamClock Legacy (Optional)"),
                ("test", "Test All Sources"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
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
                self.ctx.safe_call(*entry)

    def _test_noaa_source(self):
        """Test NOAA SWPC connectivity."""
        result = propagation.check_source(propagation.DataSource.NOAA)
        if result.success:
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
                self.ctx.dialog.msgbox("NOAA Space Weather", "\n".join(lines))
            else:
                self.ctx.dialog.msgbox("NOAA SWPC", "Connected but no data available.")
        else:
            self.ctx.dialog.msgbox("Error", f"Cannot reach NOAA SWPC:\n{result.message}")

    def _configure_pskreporter(self):
        """Configure PSKReporter MQTT."""
        while True:
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

            choice = self.ctx.dialog.menu(
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
                propagation.configure_source(
                    propagation.DataSource.PSKREPORTER,
                    enabled=new_state,
                    callsign=current_call,
                    bands=pskr_cfg.bands if pskr_cfg else [],
                    modes=pskr_cfg.modes if pskr_cfg else [],
                )
                state_str = "enabled" if new_state else "disabled"
                self.ctx.dialog.msgbox(
                    "PSKReporter",
                    f"PSKReporter MQTT {state_str}.\n\n"
                    f"{'Spots will stream from mqtt.pskreporter.info' if new_state else 'Feed stopped.'}"
                )

            elif choice == "callsign":
                call = self.ctx.dialog.inputbox(
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
                bands_input = self.ctx.dialog.inputbox(
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
                    self.ctx.dialog.msgbox(
                        "PSKReporter Connected",
                        f"{result.message}\n\n"
                        f"Spots: {result.data.get('spots_received', 0)}\n"
                        f"Bands active: {result.data.get('bands_active', 0)}"
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "PSKReporter",
                        f"{result.message}\n\n"
                        "Ensure PSKReporter is enabled and\n"
                        "paho-mqtt is installed."
                    )

    def _configure_openhamclock(self):
        """Configure OpenHamClock."""
        host = self.ctx.dialog.inputbox(
            "OpenHamClock Host",
            "Enter OpenHamClock hostname or IP:\n"
            "(Docker: localhost, Remote: IP address)",
            "localhost"
        )

        if not host:
            return

        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.ctx.dialog.inputbox(
            "OpenHamClock Port",
            "Enter port (default 3000):",
            "3000"
        )

        if not port:
            return

        if not self.ctx.validate_port(port):
            self.ctx.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        result = propagation.configure_source(
            propagation.DataSource.OPENHAMCLOCK,
            host=host,
            port=int(port),
        )
        if result.success:
            test = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
            if test.success:
                self.ctx.dialog.msgbox(
                    "OpenHamClock Connected",
                    f"API: {host}:{port}\n\nOpenHamClock is now active as\n"
                    "an enhanced data source."
                )
            else:
                self.ctx.dialog.msgbox(
                    "OpenHamClock Configured",
                    f"Saved: {host}:{port}\n\n"
                    f"Connection test failed:\n{test.message}\n\n"
                    "Make sure OpenHamClock is running\n"
                    "(docker compose up)"
                )
        else:
            self.ctx.dialog.msgbox("Error", result.message)

    def _configure_hamclock_legacy(self):
        """Configure legacy HamClock."""
        host = self.ctx.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:\n"
            "(NOTE: Original HamClock sunsets June 2026)",
            "localhost"
        )

        if not host:
            return

        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.ctx.dialog.inputbox(
            "HamClock API Port",
            "Enter API port (default 8080):",
            "8080"
        )

        if not port:
            return

        if not self.ctx.validate_port(port):
            self.ctx.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        result = propagation.configure_source(
            propagation.DataSource.HAMCLOCK,
            host=host,
            port=int(port),
        )
        if result.success:
            test = propagation.check_source(propagation.DataSource.HAMCLOCK)
            if test.success:
                self.ctx.dialog.msgbox(
                    "HamClock Connected",
                    f"API: {host}:{port}\n\nHamClock is now active as\n"
                    "an enhanced data source.\n\n"
                    "NOTE: Consider migrating to OpenHamClock\n"
                    "(original HamClock sunsets June 2026)"
                )
            else:
                self.ctx.dialog.msgbox(
                    "HamClock Configured",
                    f"Saved: {host}:{port}\n\n"
                    f"Connection test failed:\n{test.message}\n\n"
                    "Make sure HamClock is running."
                )
        else:
            self.ctx.dialog.msgbox("Error", result.message)

    def _test_all_sources(self):
        """Test all configured propagation data sources."""
        lines = ["Propagation Source Status", "=" * 35, ""]

        noaa = propagation.check_source(propagation.DataSource.NOAA)
        status = "Connected" if noaa.success else "Unreachable"
        lines.append(f"NOAA SWPC (primary): {status}")

        pskr = propagation.check_source(propagation.DataSource.PSKREPORTER)
        if pskr.success:
            spots = pskr.data.get('spots_received', 0)
            lines.append(f"PSKReporter MQTT: Connected ({spots} spots)")
        else:
            lines.append(f"PSKReporter MQTT: Not configured")

        ohc = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
        if ohc.success:
            lines.append(f"OpenHamClock: Connected")
        else:
            lines.append(f"OpenHamClock: Not configured")

        hc = propagation.check_source(propagation.DataSource.HAMCLOCK)
        if hc.success:
            lines.append(f"HamClock (legacy): Connected")
        else:
            lines.append(f"HamClock (legacy): Not configured")

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

        self.ctx.dialog.msgbox("Source Status", "\n".join(lines))
