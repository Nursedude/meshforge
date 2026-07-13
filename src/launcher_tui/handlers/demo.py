"""
Demo Mode Handler — Simulated mesh traffic for hardware-free testing.

Launches simulated mesh traffic that appears in the dashboard, alert views,
and message feeds. Useful for demonstrations and testing without radios.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class DemoHandler(BaseHandler):
    """TUI handler for demo mode control."""

    handler_id = "demo_mode"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("demo", "Demo Mode       Simulated mesh traffic", None),
        ]

    def execute(self, action):
        if action == "demo":
            self.ctx.safe_call("Demo Mode", self._demo_menu)

    def _demo_menu(self):
        """Demo mode submenu."""
        # Deferred: utils.demo_mode pulls meshing_around_clients + the
        # meshtastic package (~230 ms) — menu-time cost, not startup cost.
        from utils.demo_mode import get_demo_manager
        manager = get_demo_manager()

        while True:
            is_running = manager.is_running()
            status = "RUNNING" if is_running else "stopped"

            choices = []
            if not is_running:
                choices.append(("start", "Start Demo Mode    Begin simulated traffic"))
            else:
                choices.append(("stop", "Stop Demo Mode     End simulation"))
            choices.append(("status", f"Demo Status        Currently: {status}"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "Demo Mode",
                f"Simulated mesh traffic generator.  Status: {status}",
                choices,
            )

            if choice is None or choice == "back":
                break

            if choice == "start":
                self._start_demo(manager)
            elif choice == "stop":
                self._stop_demo(manager)
            elif choice == "status":
                self._show_status(manager)

    def _start_demo(self, manager):
        """Start demo mode."""
        if manager.is_running():
            self.ctx.dialog.msgbox("Already Running", "Demo mode is already active.")
            return

        success = manager.start()
        if success:
            self.ctx.env['demo_mode'] = True

            # Attach alert engine to demo traffic if available
            try:
                from utils.mesh_alert_engine import get_alert_engine
                engine = get_alert_engine()
                engine.start()
            except Exception:
                pass

            self.ctx.dialog.msgbox(
                "Demo Started",
                "Demo mode is now active!\n\n"
                "Simulated nodes and messages will appear in:\n"
                "  - Dashboard > View Alerts\n"
                "  - MQTT Monitor (if running)\n\n"
                "Use 'Stop Demo Mode' to end simulation."
            )
        else:
            self.ctx.dialog.msgbox("Failed", "Could not start demo mode.")

    def _stop_demo(self, manager):
        """Stop demo mode."""
        if not manager.is_running():
            self.ctx.dialog.msgbox("Not Running", "Demo mode is not active.")
            return

        manager.stop()
        self.ctx.env['demo_mode'] = False
        self.ctx.dialog.msgbox("Demo Stopped", "Simulated traffic has been stopped.")

    def _show_status(self, manager):
        """Show demo mode statistics."""
        stats = manager.get_stats()
        is_running = manager.is_running()

        lines = [
            f"Status:    {'RUNNING' if is_running else 'Stopped'}",
            f"Nodes:     {stats.get('node_count', 0)}",
            f"Messages:  {stats.get('message_count', 0)}",
            f"Alerts:    {stats.get('alert_count', 0)}",
        ]

        if is_running:
            lines.append("")
            lines.append("Demo traffic is being generated every 5-15 seconds.")
            lines.append("Events appear in Dashboard and alert views.")

        self.ctx.dialog.msgbox("Demo Mode Status", "\n".join(lines))
