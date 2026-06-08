"""
Dual-Radio Failover Handler — Configure, test, and deploy failover.

Provides TUI controls for the full dual-radio failover lifecycle:
status display, pre-flight verification, threshold configuration,
secondary radio deployment, enable/disable toggle, and event log.
"""

import logging
import os
import shutil
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# Optional imports — failover requires gateway module
try:
    from gateway.radio_failover import (
        FailoverManager, FailoverConfig, FailoverState, FailoverEvent,
    )
    _HAS_FAILOVER = True
except ImportError:
    _HAS_FAILOVER = False

try:
    from gateway.config import GatewayConfig
    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False

try:
    from utils.meshtastic_http import get_http_client
    _HAS_HTTP = True
except ImportError:
    _HAS_HTTP = False

try:
    from utils.service_check import (
        check_service, enable_service, _sudo_write, _sudo_cmd,
    )
    _HAS_SERVICE = True
except ImportError:
    _HAS_SERVICE = False

try:
    from utils._port_detection import check_port
    _HAS_PORT_DETECTION = True
except ImportError:
    _HAS_PORT_DETECTION = False

from utils.ports import (
    MESHTASTICD_PORT, MESHTASTICD_ALT_PORT, MESHTASTICD_WEB_PORT,
)

# Secondary HTTP port (matches FailoverConfig default)
MESHTASTICD_ALT_WEB_PORT = 9444


class DualRadioFailoverHandler(BaseHandler):
    """TUI handler for dual-radio failover setup and management."""

    handler_id = "dual_radio_failover"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("dual_failover",
             "Dual-Radio Failover    Configure, test, deploy failover",
             None),
        ]

    def execute(self, action):
        if action == "dual_failover":
            self._failover_menu()

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_failover_manager(self):
        """Get the active FailoverManager instance, or None."""
        if not _HAS_FAILOVER:
            return None
        return getattr(self.ctx, 'failover_manager', None)

    def _load_config(self):
        """Load GatewayConfig, returning None on failure."""
        if not _HAS_CONFIG:
            return None
        try:
            return GatewayConfig.load()
        except Exception as e:
            logger.warning("Failed to load gateway config: %s", e)
            return None

    def _get_quick_status(self):
        """One-line status summary for the menu header."""
        fm = self._get_failover_manager()
        if fm:
            status = fm.get_status()
            state = status['state'].upper()
            wd = "ON" if status['watchdog']['enabled'] else "OFF"
            count = status['failover_count_1h']
            return f"State: {state} | Watchdog: {wd} | Failovers (1h): {count}"

        cfg = self._load_config()
        if cfg and cfg.failover_enabled:
            return "Enabled (bridge not running)"
        return "Disabled"

    # ── Main menu ──────────────────────────────────────────────────────

    def _failover_menu(self):
        """Main dual-radio failover menu loop."""
        if not _HAS_FAILOVER:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Failover module not available.\n\n"
                "Requires: src/gateway/radio_failover.py"
            )
            return

        while True:
            header = self._get_quick_status()

            choices = [
                ("status", "Status              Radio health & failover state"),
                ("preflight", "Pre-flight Check     Verify both radios reachable"),
                ("configure", "Configure            Thresholds, watchdog, services"),
                ("deploy", "Deploy Secondary     Create meshtasticd-alt service"),
                ("toggle", "Enable/Disable       Toggle failover on/off"),
                ("events", "Event Log            Recent failover transitions"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Dual-Radio Failover",
                header,
                choices,
            )

            if choice is None or choice == "back":
                break
            elif choice == "status":
                self._show_status()
            elif choice == "preflight":
                self._preflight_check()
            elif choice == "configure":
                self._configure()
            elif choice == "deploy":
                self._deploy_secondary()
            elif choice == "toggle":
                self._toggle_failover()
            elif choice == "events":
                self._show_event_log()

    # ── Status ─────────────────────────────────────────────────────────

    def _show_status(self):
        """Show detailed failover status with radio health."""
        fm = self._get_failover_manager()
        if not fm:
            # Fallback: show config values
            cfg = self._load_config()
            if not cfg:
                self.ctx.dialog.msgbox(
                    "Dual-Radio Failover",
                    "No gateway config found.\n\n"
                    "Run Configure to set up failover."
                )
                return

            lines = [
                "FAILOVER STATUS (bridge not running)",
                "",
                f"Enabled:               {cfg.failover_enabled}",
                f"Utilization Threshold: {cfg.failover_utilization_threshold}%",
                f"Duration:              {cfg.failover_utilization_duration}s",
                f"Recovery Threshold:    {cfg.failover_recovery_threshold}%",
                f"Recovery Duration:     {cfg.failover_recovery_duration}s",
                f"Poll Interval:         {cfg.failover_health_poll_interval}s",
                "",
                f"Watchdog Enabled:      {cfg.failover_watchdog_enabled}",
                f"Primary Service:       {cfg.failover_primary_service}",
                f"Secondary Service:     {cfg.failover_secondary_service}",
                "",
                "Start the gateway bridge to see live status.",
            ]
            self.ctx.dialog.msgbox("Dual-Radio Failover", "\n".join(lines))
            return

        status = fm.get_status()
        state = status['state'].upper()
        p = status['primary']
        s = status['secondary']
        wd = status['watchdog']
        thresholds = status['thresholds']

        p_status = "ONLINE" if p['reachable'] else "OFFLINE"
        s_status = "ONLINE" if s['reachable'] else "OFFLINE"

        lines = [
            f"STATE: {state}",
            f"Active TX Port: {status['active_port']}",
            "",
            f"Primary Radio   [{p_status}]",
            f"  Port: {p['port']}",
            f"  Channel Util: {p['channel_utilization']:.1f}%",
            f"  TX Util:      {p['tx_utilization']:.1f}%",
            f"  Overloaded:   {p['overloaded']}",
            "",
            f"Secondary Radio [{s_status}]",
            f"  Port: {s['port']}",
            f"  Channel Util: {s['channel_utilization']:.1f}%",
            f"  TX Util:      {s['tx_utilization']:.1f}%",
            f"  Overloaded:   {s['overloaded']}",
            "",
            "Watchdog:",
            f"  Enabled:           {wd['enabled']}",
            f"  Primary Restarts:  {wd['primary_restarts_1h']}",
            f"  Secondary Restarts:{wd['secondary_restarts_1h']}",
            f"  Primary Down:      {wd['primary_down']}",
            f"  Secondary Down:    {wd['secondary_down']}",
            "",
            "Thresholds:",
            f"  Utilization: {thresholds['utilization']}%",
            f"  Recovery:    {thresholds['recovery']}%",
            f"  Duration:    {thresholds['duration']}s",
            "",
            f"Failovers (1h): {status['failover_count_1h']}",
        ]

        if status.get('last_event'):
            lines.append(f"Last Event: {status['last_event']}")

        self.ctx.dialog.msgbox("Dual-Radio Failover Status", "\n".join(lines))

    # ── Pre-flight Check ───────────────────────────────────────────────

    def _preflight_check(self):
        """Verify both radios are reachable and services are running."""
        results = []
        passes = 0
        fails = 0

        def _check(label, ok, detail=""):
            nonlocal passes, fails
            if ok:
                passes += 1
                results.append(f"[PASS] {label}")
            else:
                fails += 1
                results.append(f"[FAIL] {label}")
            if detail:
                results.append(f"       {detail}")

        # 1. Primary meshtasticd service
        if _HAS_SERVICE:
            svc = check_service("meshtasticd")
            _check("Primary service (meshtasticd)",
                   svc.available, svc.message if not svc.available else "")
        else:
            _check("Primary service (meshtasticd)", False,
                   "service_check module not available")

        # 2. Secondary meshtasticd service
        if _HAS_SERVICE:
            svc = check_service("meshtasticd-alt")
            _check("Secondary service (meshtasticd-alt)",
                   svc.available, svc.message if not svc.available else "")
        else:
            _check("Secondary service (meshtasticd-alt)", False,
                   "service_check module not available")

        # 3. Primary HTTP health
        if _HAS_HTTP:
            try:
                client = get_http_client(
                    host='localhost',
                    port=MESHTASTICD_WEB_PORT,
                    auto_detect=False,
                )
                report = client.get_report()
                if report:
                    _check("Primary HTTP health (port %d)" % MESHTASTICD_WEB_PORT,
                           True,
                           "ch=%.1f%% tx=%.1f%%" % (
                               report.channel_utilization,
                               report.tx_utilization))
                else:
                    _check("Primary HTTP health (port %d)" % MESHTASTICD_WEB_PORT,
                           False, "No report returned")
            except Exception as e:
                _check("Primary HTTP health (port %d)" % MESHTASTICD_WEB_PORT,
                       False, str(e))
        else:
            _check("Primary HTTP health", False,
                   "meshtastic_http module not available")

        # 4. Secondary HTTP health
        if _HAS_HTTP:
            try:
                client = get_http_client(
                    host='localhost',
                    port=MESHTASTICD_ALT_WEB_PORT,
                    auto_detect=False,
                )
                report = client.get_report()
                if report:
                    _check("Secondary HTTP health (port %d)" % MESHTASTICD_ALT_WEB_PORT,
                           True,
                           "ch=%.1f%% tx=%.1f%%" % (
                               report.channel_utilization,
                               report.tx_utilization))
                else:
                    _check("Secondary HTTP health (port %d)" % MESHTASTICD_ALT_WEB_PORT,
                           False, "No report returned")
            except Exception as e:
                _check("Secondary HTTP health (port %d)" % MESHTASTICD_ALT_WEB_PORT,
                       False, str(e))
        else:
            _check("Secondary HTTP health", False,
                   "meshtastic_http module not available")

        # 5. Primary TCP port
        if _HAS_SERVICE and _HAS_PORT_DETECTION:
            ok = check_port(MESHTASTICD_PORT)
            _check("Primary TCP port (%d)" % MESHTASTICD_PORT, ok)
        else:
            _check("Primary TCP port", False, "port check not available")

        # 6. Secondary TCP port
        if _HAS_SERVICE and _HAS_PORT_DETECTION:
            ok = check_port(MESHTASTICD_ALT_PORT)
            _check("Secondary TCP port (%d)" % MESHTASTICD_ALT_PORT, ok)
        else:
            _check("Secondary TCP port", False, "port check not available")

        # 7. Config check
        cfg = self._load_config()
        if cfg:
            _check("Failover enabled in config", cfg.failover_enabled,
                   "" if cfg.failover_enabled else "Set Enable/Disable to turn on")
        else:
            _check("Gateway config", False, "Could not load config")

        # Summary
        results.append("")
        results.append("=" * 40)
        if fails == 0:
            results.append(f"READY  ({passes}/{passes} checks passed)")
        else:
            results.append(
                f"NOT READY  ({passes} passed, {fails} failed)")

        self.ctx.dialog.msgbox("Pre-flight Check", "\n".join(results))

    # ── Configure ──────────────────────────────────────────────────────

    def _configure(self):
        """Edit failover configuration via input dialogs."""
        if not _HAS_CONFIG:
            self.ctx.dialog.msgbox(
                "Configure",
                "Gateway config module not available."
            )
            return

        while True:
            cfg = self._load_config()
            if not cfg:
                self.ctx.dialog.msgbox("Configure", "Failed to load config.")
                return

            choices = [
                ("thresh", "Utilization Threshold  %.1f%%" % cfg.failover_utilization_threshold),
                ("dur", "Utilization Duration   %ds" % cfg.failover_utilization_duration),
                ("recov", "Recovery Threshold     %.1f%%" % cfg.failover_recovery_threshold),
                ("recdur", "Recovery Duration      %ds" % cfg.failover_recovery_duration),
                ("poll", "Poll Interval          %.1fs" % cfg.failover_health_poll_interval),
                ("wd", "Watchdog Enabled       %s" % cfg.failover_watchdog_enabled),
                ("wdfail", "Restart After Failures %d" % cfg.failover_restart_after_failures),
                ("wdmax", "Max Restarts/Hour      %d" % cfg.failover_max_restarts_per_hour),
                ("wdcool", "Restart Cooldown       %ds" % cfg.failover_restart_cooldown),
                ("svcpri", "Primary Service        %s" % cfg.failover_primary_service),
                ("svcsec", "Secondary Service      %s" % cfg.failover_secondary_service),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Configure Failover",
                "Select a setting to modify",
                choices,
            )

            if choice is None or choice == "back":
                break

            self._edit_config_field(choice, cfg)

    def _edit_config_field(self, field_tag, cfg):
        """Edit a single config field via inputbox."""
        field_map = {
            "thresh": ("failover_utilization_threshold", "Utilization Threshold (%)",
                       float, 1.0, 100.0),
            "dur": ("failover_utilization_duration", "Utilization Duration (seconds)",
                    int, 5, 300),
            "recov": ("failover_recovery_threshold", "Recovery Threshold (%)",
                      float, 1.0, 100.0),
            "recdur": ("failover_recovery_duration", "Recovery Duration (seconds)",
                       int, 5, 600),
            "poll": ("failover_health_poll_interval", "Poll Interval (seconds)",
                     float, 1.0, 60.0),
            "wdfail": ("failover_restart_after_failures",
                       "Restart After N Poll Failures", int, 1, 20),
            "wdmax": ("failover_max_restarts_per_hour",
                      "Max Restarts Per Hour", int, 1, 10),
            "wdcool": ("failover_restart_cooldown",
                       "Restart Cooldown (seconds)", int, 10, 600),
        }

        # Watchdog toggle uses yesno
        if field_tag == "wd":
            current = cfg.failover_watchdog_enabled
            action = "Disable" if current else "Enable"
            if self.ctx.dialog.yesno(
                "Watchdog",
                f"Watchdog is currently {'ENABLED' if current else 'DISABLED'}.\n\n"
                f"{action} the service watchdog?"
            ):
                cfg.failover_watchdog_enabled = not current
                if cfg.save():
                    self.ctx.dialog.msgbox(
                        "Watchdog",
                        f"Watchdog {'enabled' if not current else 'disabled'}.\n\n"
                        "Restart the gateway bridge for changes to take effect."
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "Watchdog",
                        "Failed to save the gateway config — change NOT persisted."
                    )
            return

        # Service name fields use inputbox with string validation
        if field_tag in ("svcpri", "svcsec"):
            attr = "failover_primary_service" if field_tag == "svcpri" else "failover_secondary_service"
            label = "Primary" if field_tag == "svcpri" else "Secondary"
            current = getattr(cfg, attr)
            result = self.ctx.dialog.inputbox(
                f"{label} Service Name",
                f"systemd service name for the {label.lower()} radio:",
                current,
            )
            if result and result.strip():
                setattr(cfg, attr, result.strip())
                if not cfg.save():
                    self.ctx.dialog.msgbox(
                        "Save Failed",
                        f"{label} service name NOT saved (config write failed)."
                    )
            return

        # Numeric fields
        if field_tag not in field_map:
            return

        attr, label, cast, min_val, max_val = field_map[field_tag]
        current = getattr(cfg, attr)

        result = self.ctx.dialog.inputbox(
            label,
            f"Range: {min_val} - {max_val}",
            str(current),
        )
        if result is None:
            return

        try:
            value = cast(result)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Invalid Input", f"Must be a valid {cast.__name__}.")
            return

        if value < min_val or value > max_val:
            self.ctx.dialog.msgbox(
                "Out of Range",
                f"Value must be between {min_val} and {max_val}."
            )
            return

        # Cross-validation: recovery must be less than utilization threshold
        if field_tag == "recov" and value >= cfg.failover_utilization_threshold:
            self.ctx.dialog.msgbox(
                "Invalid",
                f"Recovery threshold ({value}%) must be less than\n"
                f"utilization threshold ({cfg.failover_utilization_threshold}%)."
            )
            return
        if field_tag == "thresh" and cfg.failover_recovery_threshold >= value:
            self.ctx.dialog.msgbox(
                "Invalid",
                f"Utilization threshold ({value}%) must be greater than\n"
                f"recovery threshold ({cfg.failover_recovery_threshold}%)."
            )
            return

        setattr(cfg, attr, value)
        if not cfg.save():
            self.ctx.dialog.msgbox(
                "Save Failed",
                f"{label} NOT saved (config write failed)."
            )

    # ── Deploy Secondary ───────────────────────────────────────────────

    def _deploy_secondary(self):
        """Deploy the secondary meshtasticd service and config."""
        if not _HAS_SERVICE:
            self.ctx.dialog.msgbox(
                "Deploy",
                "Service management module not available."
            )
            return

        # Confirm
        if not self.ctx.dialog.yesno(
            "Deploy Secondary Radio",
            "This will:\n\n"
            "1. Create /etc/systemd/system/meshtasticd-alt.service\n"
            "2. Create /etc/meshtasticd-alt/ config directory\n"
            "3. Generate a minimal config (port 4404, HTTP 9444)\n"
            "4. Enable and start the service\n\n"
            "Requires sudo. Continue?"
        ):
            return

        # Find meshtasticd binary
        meshtasticd_bin = shutil.which("meshtasticd")
        if not meshtasticd_bin:
            self.ctx.dialog.msgbox(
                "Deploy Failed",
                "meshtasticd binary not found in PATH.\n\n"
                "Install meshtasticd first:\n"
                "  apt install meshtasticd\n"
                "  or build from source"
            )
            return

        steps_done = []
        try:
            # Step 1: Generate systemd unit from template
            template_path = (
                Path(__file__).parent.parent.parent.parent
                / "templates" / "systemd" / "meshtasticd-alt.service"
            )
            if not template_path.exists():
                self.ctx.dialog.msgbox(
                    "Deploy Failed",
                    f"Template not found:\n{template_path}"
                )
                return

            unit_content = template_path.read_text()
            unit_content = unit_content.replace("@MESHTASTICD_BIN@", meshtasticd_bin)

            success, msg = _sudo_write(
                "/etc/systemd/system/meshtasticd-alt.service",
                unit_content,
            )
            if not success:
                self.ctx.dialog.msgbox("Deploy Failed", f"Service file:\n{msg}")
                return
            steps_done.append("Created meshtasticd-alt.service")

            # Step 2: Create config directory
            import subprocess
            result = subprocess.run(
                _sudo_cmd(["mkdir", "-p", "/etc/meshtasticd-alt"]),
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Deploy Failed",
                    f"mkdir /etc/meshtasticd-alt failed:\n{result.stderr}"
                )
                return
            steps_done.append("Created /etc/meshtasticd-alt/")

            # Step 3: Generate minimal secondary config
            # Check if existing primary config can be used as a base
            secondary_config = self._generate_secondary_config()
            success, msg = _sudo_write(
                "/etc/meshtasticd-alt/config.yaml",
                secondary_config,
            )
            if not success:
                self.ctx.dialog.msgbox("Deploy Failed", f"Config file:\n{msg}")
                return
            steps_done.append("Generated config.yaml (port 4404, HTTP 9444)")

            # Step 4: Enable and start
            success, msg = enable_service("meshtasticd-alt", start=True)
            if not success:
                self.ctx.dialog.msgbox(
                    "Deploy Partial",
                    f"Files created but service failed to start:\n{msg}\n\n"
                    "Completed:\n" + "\n".join(f"  - {s}" for s in steps_done)
                )
                return
            steps_done.append("Enabled and started meshtasticd-alt")

            # Step 5: Verify
            svc = check_service("meshtasticd-alt")
            if svc.available:
                steps_done.append("Service verified running")
                service_ok = True
            else:
                steps_done.append(f"Service check: {svc.message}")
                service_ok = False

        except Exception as e:
            self.ctx.dialog.msgbox(
                "Deploy Error",
                f"Unexpected error: {e}\n\n"
                "Completed:\n" + "\n".join(f"  - {s}" for s in steps_done)
            )
            return

        # Deploy summary — headline honest about the service verification.
        body = (
            "\n".join(f"  - {s}" for s in steps_done)
            + "\n\nNext steps:\n"
            "  1. Edit /etc/meshtasticd-alt/config.yaml for your hardware\n"
            "  2. Run Pre-flight Check to verify both radios\n"
            "  3. Use Enable/Disable to turn on failover"
        )
        if service_ok:
            self.ctx.dialog.msgbox(
                "Deploy Complete",
                "Secondary radio deployed successfully!\n\n" + body,
            )
        else:
            self.ctx.dialog.msgbox(
                "Deploy Incomplete",
                "Secondary radio files deployed, but meshtasticd-alt is NOT "
                "running yet — see the Service step above.\n\n" + body,
            )

    def _generate_secondary_config(self):
        """Generate minimal meshtasticd config for the secondary radio."""
        # Start from primary config if it exists, otherwise use minimal
        primary_config = Path("/etc/meshtasticd/config.yaml")
        if primary_config.exists():
            try:
                base = primary_config.read_text()
                # Add/override port settings for secondary
                lines = [
                    "# MeshForge Secondary Radio Configuration",
                    "# Generated by MeshForge dual-radio failover deploy",
                    "# Base: copied from primary, ports changed for secondary",
                    "#",
                    "# IMPORTANT: Edit Lora section for your secondary hardware",
                    "",
                ]
                lines.append(base)
                lines.extend([
                    "",
                    "# Secondary radio port overrides",
                    "Webserver:",
                    "  Port: %d" % MESHTASTICD_ALT_WEB_PORT,
                    "",
                ])
                return "\n".join(lines)
            except Exception:
                pass

        # Minimal fallback config
        return (
            "# MeshForge Secondary Radio Configuration\n"
            "# Generated by MeshForge dual-radio failover deploy\n"
            "#\n"
            "# IMPORTANT: Configure Lora section for your secondary hardware\n"
            "#   - Set Module (sx1262, sx1276, etc.)\n"
            "#   - Set Region (US, EU_868, etc.)\n"
            "\n"
            "Lora:\n"
            "  Module: sx1262\n"
            "  Region: US\n"
            "  ModemPreset: LONG_FAST\n"
            "  TxEnabled: true\n"
            "\n"
            "Bluetooth:\n"
            "  Enabled: false\n"
            "\n"
            "WiFi:\n"
            "  Enabled: false\n"
            "\n"
            "Webserver:\n"
            "  Port: %d\n"
            "\n"
            "Device:\n"
            "  Role: ROUTER\n"
        ) % MESHTASTICD_ALT_WEB_PORT

    # ── Enable / Disable ───────────────────────────────────────────────

    def _toggle_failover(self):
        """Toggle failover_enabled in gateway config."""
        cfg = self._load_config()
        if not cfg:
            self.ctx.dialog.msgbox("Toggle", "Failed to load gateway config.")
            return

        current = cfg.failover_enabled
        action = "Disable" if current else "Enable"

        if not self.ctx.dialog.yesno(
            f"{action} Failover",
            f"Failover is currently {'ENABLED' if current else 'DISABLED'}.\n\n"
            f"{action} dual-radio failover?"
        ):
            return

        cfg.failover_enabled = not current
        if not cfg.save():
            self.ctx.dialog.msgbox(
                "Toggle Failed",
                "Failed to save the gateway config — failover state NOT changed."
            )
            return

        new_state = "ENABLED" if not current else "DISABLED"
        self.ctx.dialog.msgbox(
            "Failover Toggled",
            f"Failover is now {new_state}.\n\n"
            "Restart the gateway bridge for changes to take effect."
        )

    # ── Event Log ──────────────────────────────────────────────────────

    def _show_event_log(self):
        """Show recent failover state transition events."""
        fm = self._get_failover_manager()
        if not fm:
            self.ctx.dialog.msgbox(
                "Event Log",
                "Failover manager not active.\n\n"
                "Start the gateway bridge to see events."
            )
            return

        events = list(fm._events)
        if not events:
            self.ctx.dialog.msgbox("Event Log", "No state transitions recorded.")
            return

        lines = ["Recent Failover Transitions", "=" * 40, ""]
        for event in reversed(events[-20:]):
            ts = event.timestamp.strftime("%H:%M:%S")
            lines.append(
                f"{ts}  {event.from_state.value} -> {event.to_state.value}"
            )
            lines.append(
                f"         ch_pri={event.primary_utilization:.1f}%  "
                f"ch_sec={event.secondary_utilization:.1f}%"
            )
            lines.append(f"         {event.reason}")
            lines.append("")

        self.ctx.dialog.msgbox("Event Log", "\n".join(lines))
