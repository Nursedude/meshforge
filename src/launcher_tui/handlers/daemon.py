"""
Daemon mode handler — start/stop/status for headless NOC services.

Batch 10b: Extracted from MeshForgeLauncher._daemon_menu() in main.py.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class DaemonHandler(BaseHandler):
    """Daemon Mode — start/stop headless NOC services."""

    handler_id = "daemon"
    menu_section = "system"

    def menu_items(self):
        return [
            ("daemon", "Daemon Mode         Start/stop headless NOC", None),
        ]

    def execute(self, action):
        if action == "daemon":
            self._daemon_menu()

    def _daemon_menu(self):
        """Daemon Mode - Start/stop headless NOC services."""
        from utils.paths import get_real_user_home

        while True:
            # Check if daemon is running
            daemon_status = "unknown"
            try:
                pid_file = Path("/run/meshforge/meshforged.pid")
                if pid_file.exists():
                    pid = int(pid_file.read_text().strip())
                    try:
                        os.kill(pid, 0)
                        daemon_status = f"running (PID {pid})"
                    except ProcessLookupError:
                        daemon_status = "stopped (stale PID)"
                else:
                    daemon_status = "stopped"
            except Exception:
                daemon_status = "unknown"

            choices = [
                ("status", f"Status              Daemon: {daemon_status}"),
                ("start", "Start Daemon        Launch headless NOC"),
                ("stop", "Stop Daemon         Stop headless NOC"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Daemon Mode",
                "Headless NOC service manager:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._daemon_show_status()
            elif choice == "start":
                self._daemon_start()
            elif choice == "stop":
                self._daemon_stop()

    def _daemon_show_status(self):
        """Show daemon status in a dialog."""
        from utils.paths import get_real_user_home

        try:
            status_file = get_real_user_home() / ".config" / "meshforge" / "daemon_status.json"
            if not status_file.exists():
                self.ctx.dialog.msgbox("Daemon Status", "No status file found.\nDaemon may not be running.")
                return

            with open(status_file, 'r') as f:
                data = json.load(f)

            daemon = data.get("daemon", {})
            services = data.get("services", {})
            uptime = daemon.get("uptime_seconds", 0)
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60

            lines = [
                f"Status:  {daemon.get('status', '?')}",
                f"PID:     {daemon.get('pid', '?')}",
                f"Profile: {daemon.get('profile', '?')}",
                f"Uptime:  {hours}h {minutes}m",
                "",
                "Services:",
            ]

            for name, svc in services.items():
                alive = svc.get("alive", False)
                marker = "*" if alive else "-"
                lines.append(f"  {marker} {name}")

            self.ctx.dialog.msgbox("Daemon Status", "\n".join(lines))

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Could not read daemon status:\n{e}")

    def _daemon_start(self):
        """Start the daemon via subprocess."""
        if not self.ctx.dialog.yesno(
            "Start Daemon",
            "Start MeshForge daemon (headless mode)?\n\n"
            "This will run gateway bridge, health monitoring,\n"
            "and other configured services in the background."
        ):
            return

        try:
            daemon_script = self.ctx.src_dir / "daemon.py"
            proc = subprocess.Popen(
                [sys.executable, str(daemon_script), "start", "--foreground"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Verify daemon started successfully
            import time
            time.sleep(2)
            if proc.poll() is not None:
                self.ctx.dialog.msgbox("Error", f"Daemon exited immediately (rc={proc.returncode})")
                return
            self.ctx.dialog.msgbox("Daemon Started", "Daemon launched in background.\nCheck status for details.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to start daemon:\n{e}")

    def _daemon_stop(self):
        """Stop the daemon via subprocess."""
        try:
            daemon_script = self.ctx.src_dir / "daemon.py"
            result = subprocess.run(
                [sys.executable, str(daemon_script), "stop"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip() or result.stderr.strip() or "Stop signal sent."
            self.ctx.dialog.msgbox("Stop Daemon", output)
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop daemon:\n{e}")
