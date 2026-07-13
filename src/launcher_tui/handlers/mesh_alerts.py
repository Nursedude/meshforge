"""
Mesh Alerts Handler — Configure and view mesh-level alerting.

Provides battery warnings, emergency keyword detection, disconnect alerts,
new node discovery, noisy node detection, and SNR monitoring.

Uses MeshAlertEngine from utils/mesh_alert_engine.py.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

def get_alert_engine():
    """Deferred import: utils.mesh_alert_engine pulls the meshtastic package
    (~200 ms) — resolved at menu time, never at TUI startup. Raises
    ImportError when the stack is missing; _mesh_alerts_menu (the only
    entry point) converts that to an honest dialog before any other site
    can run."""
    from utils.mesh_alert_engine import get_alert_engine as _real
    return _real()

# Alert type metadata for UI display
_ALERT_TYPES = [
    ("battery", "Battery Low", "Alert when node battery drops below threshold"),
    ("emergency", "Emergency Keywords", "Alert on emergency keywords in messages"),
    ("new_node", "New Node", "Alert when a new node joins the mesh"),
    ("disconnect", "Node Disconnect", "Alert when a node hasn't been heard from"),
    ("noisy_node", "Noisy Node", "Alert when a node sends too many messages"),
    ("snr", "Low SNR", "Alert when signal-to-noise ratio drops"),
]


class MeshAlertsHandler(BaseHandler):
    """TUI handler for mesh alert configuration and viewing."""

    handler_id = "mesh_alerts"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("mesh_alerts", "Mesh Alerts     Battery, emergency, disconnect", None),
        ]

    def execute(self, action):
        if action == "mesh_alerts":
            self.ctx.safe_call("Mesh Alerts", self._mesh_alerts_menu)

    def _mesh_alerts_menu(self):
        """Main mesh alerts configuration menu."""
        try:
            engine = get_alert_engine()
        except ImportError:
            self.ctx.dialog.msgbox(
                "Mesh Alerts Not Available",
                "The alert engine requires the meshtastic Python library.\n"
                "Install it in-app from Radio > Install/Reinstall CLI.",
            )
            return

        while True:
            active_count = len(engine.get_active_alerts())
            active_label = f" ({active_count})" if active_count > 0 else ""

            choices = [
                ("view", f"View Active Alerts{active_label}  Current warnings"),
                ("types", "Enable/Disable Types    Toggle alert categories"),
                ("keywords", "Emergency Keywords      Configure trigger words"),
                ("battery", "Battery Threshold       Set low battery level"),
                ("disconnect", "Disconnect Timeout      Set offline threshold"),
                ("cooldown", "Cooldown Period         Suppress repeat alerts"),
                ("ack", "Acknowledge All         Clear active alerts"),
                ("history", "Alert History           Recent alerts (all)"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Mesh Alerts",
                "Mesh network alerting configuration:",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "view": self._view_active_alerts,
                "types": self._toggle_alert_types,
                "keywords": self._configure_keywords,
                "battery": self._configure_battery,
                "disconnect": self._configure_disconnect,
                "cooldown": self._configure_cooldown,
                "ack": self._acknowledge_all,
                "history": self._view_history,
            }
            handler = dispatch.get(choice)
            if handler:
                self.ctx.safe_call("Mesh Alerts", handler)

    def _view_active_alerts(self):
        """Display current unacknowledged mesh alerts."""
        engine = get_alert_engine()
        alerts = engine.get_active_alerts()

        clear_screen()
        print("=== Active Mesh Alerts ===\n")

        if not alerts:
            print("  No active alerts.\n")
        else:
            severity_colors = {
                1: "\033[0;34m",   # Blue
                2: "\033[0;33m",   # Yellow
                3: "\033[0;31m",   # Red
                4: "\033[1;31m",   # Bold Red
            }
            reset = "\033[0m"

            for alert in alerts:
                color = severity_colors.get(alert.severity, "")
                ts = alert.timestamp.strftime("%H:%M:%S")
                print(f"  {color}[{alert.severity_label}]{reset} {alert.title}")
                print(f"           {alert.message}")
                print(f"           {ts}  type={alert.alert_type}")
                if alert.source_node:
                    print(f"           node={alert.source_node}")
                print()

        print(f"Total active: {len(alerts)}")
        counts = engine.get_alert_count_by_type()
        if counts:
            parts = [f"{k}={v}" for k, v in sorted(counts.items())]
            print(f"By type: {', '.join(parts)}")

        print()
        self.ctx.wait_for_enter()

    def _toggle_alert_types(self):
        """Enable/disable alert types via checklist."""
        engine = get_alert_engine()
        config = engine.config
        enabled = config.get("enabled_types", [])

        choices = []
        for type_id, label, desc in _ALERT_TYPES:
            is_on = type_id in enabled
            choices.append((type_id, f"{label:<22} {desc}", is_on))

        result = self.ctx.dialog.checklist(
            "Alert Types",
            "Select which alert types to enable:",
            choices,
        )

        if result is not None:
            ok = engine.update_config("enabled_types", list(result))
            self.ctx.report_action(
                ok,
                "Updated",
                f"Enabled alert types: {', '.join(result) or 'none'}",
                "Save Failed",
                "The setting was changed for this session but could NOT be "
                "saved to disk — it will revert on restart.",
            )

    def _configure_keywords(self):
        """Configure emergency trigger keywords."""
        engine = get_alert_engine()
        config = engine.config
        current = config.get("emergency_keywords", [])

        result = self.ctx.dialog.inputbox(
            "Emergency Keywords",
            "Enter emergency keywords (comma-separated):\n\n"
            "Messages containing these words will trigger alerts.",
            init=", ".join(current),
        )

        if result is not None and result.strip():
            keywords = [kw.strip().lower() for kw in result.split(",") if kw.strip()]
            ok = engine.update_config("emergency_keywords", keywords)
            self.ctx.report_action(
                ok,
                "Updated",
                f"Emergency keywords: {', '.join(keywords)}",
                "Save Failed",
                "The LIFE-SAFETY emergency keywords were changed for this "
                "session but could NOT be saved to disk — they will revert to "
                "the old list on restart. Emergency-keyword alerting may not "
                "trigger on the words you just entered after a restart.",
            )

    def _configure_battery(self):
        """Configure battery alert threshold."""
        engine = get_alert_engine()
        current = engine.config.get("battery_threshold", 20)

        result = self.ctx.dialog.inputbox(
            "Battery Threshold",
            "Alert when node battery drops below this percentage:",
            init=str(current),
        )

        if result is not None:
            try:
                value = int(result)
                if 1 <= value <= 100:
                    ok = engine.update_config("battery_threshold", value)
                    self.ctx.report_action(
                        ok,
                        "Updated",
                        f"Battery threshold: {value}%",
                        "Save Failed",
                        "The setting was changed for this session but could "
                        "NOT be saved to disk — it will revert on restart.",
                    )
                else:
                    self.ctx.dialog.msgbox("Invalid", "Value must be 1-100.")
            except ValueError:
                self.ctx.dialog.msgbox("Invalid", "Enter a number 1-100.")

    def _configure_disconnect(self):
        """Configure disconnect timeout."""
        engine = get_alert_engine()
        current = engine.config.get("disconnect_timeout_minutes", 30)

        result = self.ctx.dialog.inputbox(
            "Disconnect Timeout",
            "Alert when a node hasn't been heard for this many minutes:",
            init=str(current),
        )

        if result is not None:
            try:
                value = int(result)
                if 1 <= value <= 1440:
                    ok = engine.update_config("disconnect_timeout_minutes", value)
                    self.ctx.report_action(
                        ok,
                        "Updated",
                        f"Disconnect timeout: {value} minutes",
                        "Save Failed",
                        "The setting was changed for this session but could "
                        "NOT be saved to disk — it will revert on restart.",
                    )
                else:
                    self.ctx.dialog.msgbox("Invalid", "Value must be 1-1440 minutes.")
            except ValueError:
                self.ctx.dialog.msgbox("Invalid", "Enter a number 1-1440.")

    def _configure_cooldown(self):
        """Configure alert cooldown period."""
        engine = get_alert_engine()
        current = engine.config.get("cooldown_seconds", 300)

        result = self.ctx.dialog.inputbox(
            "Cooldown Period",
            "Suppress repeated alerts from the same node/type\n"
            "for this many seconds:",
            init=str(current),
        )

        if result is not None:
            try:
                value = int(result)
                if 0 <= value <= 86400:
                    ok = engine.update_config("cooldown_seconds", value)
                    self.ctx.report_action(
                        ok,
                        "Updated",
                        f"Cooldown: {value} seconds",
                        "Save Failed",
                        "The setting was changed for this session but could "
                        "NOT be saved to disk — it will revert on restart.",
                    )
                else:
                    self.ctx.dialog.msgbox("Invalid", "Value must be 0-86400.")
            except ValueError:
                self.ctx.dialog.msgbox("Invalid", "Enter a number 0-86400.")

    def _acknowledge_all(self):
        """Acknowledge all active alerts."""
        engine = get_alert_engine()
        count = engine.acknowledge_all()
        self.ctx.dialog.msgbox(
            "Acknowledged",
            f"Acknowledged {count} alert(s)." if count > 0 else "No active alerts.",
        )

    def _view_history(self):
        """Show recent alert history (acknowledged and unacknowledged)."""
        engine = get_alert_engine()
        alerts = engine.get_all_alerts(limit=50)

        clear_screen()
        print("=== Alert History (last 50) ===\n")

        if not alerts:
            print("  No alerts recorded.\n")
        else:
            severity_colors = {
                1: "\033[0;34m",
                2: "\033[0;33m",
                3: "\033[0;31m",
                4: "\033[1;31m",
            }
            reset = "\033[0m"

            for alert in reversed(alerts):
                color = severity_colors.get(alert.severity, "")
                ts = alert.timestamp.strftime("%H:%M:%S")
                ack = " [ACK]" if alert.acknowledged else ""
                print(f"  {color}[{alert.severity_label}]{reset}{ack} {ts} {alert.title}")
                print(f"           {alert.message}")
                print()

        print()
        self.ctx.wait_for_enter()
