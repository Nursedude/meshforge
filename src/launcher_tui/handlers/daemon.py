"""
Daemon mode handler — start/stop/status for headless NOC services.

The MeshForge daemon runs gateway bridge, maps, RNS, NomadNet
and other services in the background without the TUI.

Batch 10b: Extracted from MeshForgeLauncher._daemon_menu() in main.py.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen

logger = logging.getLogger(__name__)


class DaemonHandler(BaseHandler):
    """MeshForge Daemon — headless NOC services."""

    handler_id = "daemon"
    menu_section = "system"

    def menu_items(self):
        return [
            ("daemon", "MeshForge Daemon    Headless NOC (maps, RNS, chat)", None),
        ]

    def execute(self, action):
        if action == "daemon":
            self._daemon_menu()

    def _daemon_menu(self):
        """MeshForge Daemon - headless NOC service manager."""
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
                ("config", "View Config         Enabled services & settings"),
                ("logs", "Daemon Logs         View daemon output"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshForge Daemon",
                "Headless NOC — runs services without the TUI:\n"
                "  Gateway bridge, maps, RNS, and NomadNet",
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
            elif choice == "config":
                self._daemon_view_config()
            elif choice == "logs":
                self._daemon_logs()

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
            # The returncode was discarded, so a failed stop still read as the
            # neutral "Stop Daemon" title (#74-#77). Title on rc.
            if result.returncode == 0:
                output = result.stdout.strip() or "Stop signal sent."
                self.ctx.dialog.msgbox("Stop Daemon", output)
            else:
                detail = (result.stderr.strip() or result.stdout.strip()
                          or f"daemon stop exited with code {result.returncode}")
                self.ctx.dialog.msgbox("Stop Failed", detail)
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop daemon:\n{e}")

    def _daemon_view_config(self):
        """Show daemon configuration — which services are enabled."""
        from utils.paths import get_real_user_home

        try:
            from daemon_config import DaemonConfig, SYSTEM_CONFIG, USER_CONFIG_RELATIVE
            config = DaemonConfig.load()

            user_config = get_real_user_home() / USER_CONFIG_RELATIVE

            # Show which config file is active
            config_source = "defaults"
            if user_config.exists():
                config_source = str(user_config)
            elif SYSTEM_CONFIG.exists():
                config_source = str(SYSTEM_CONFIG)

            services = [
                ("Gateway Bridge", config.gateway_enabled, ""),
                ("Health Probe", config.health_probe_enabled,
                 f" (every {config.health_probe_interval}s)"),
                ("MQTT Monitor", config.mqtt_enabled,
                 f" ({config.mqtt_broker}:{config.mqtt_port})"),
                ("Config API", config.config_api_enabled,
                 f" (port {config.config_api_port})"),
                ("Map Server", config.map_server_enabled,
                 f" (port {config.map_server_port})"),
                ("Telemetry", config.telemetry_enabled,
                 f" (every {config.telemetry_poll_interval_minutes}min)"),
                ("Node Tracker", config.node_tracker_enabled, ""),
            ]

            lines = [f"Config: {config_source}", ""]
            lines.append("Services:")
            for name, enabled, detail in services:
                marker = "[ON] " if enabled else "[OFF]"
                lines.append(f"  {marker} {name}{detail}")

            lines.append("")
            lines.append(f"Watchdog: every {config.watchdog_interval}s, "
                         f"max {config.max_restarts} restarts")
            lines.append(f"Log level: {config.log_level}")

            if config_source == "defaults":
                lines.append("")
                lines.append("No config file found. Using defaults.")
                lines.append(f"Create: {user_config}")

            self.ctx.dialog.msgbox("Daemon Config", "\n".join(lines))

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Could not load daemon config:\n{e}")

    def _daemon_logs(self):
        """Show daemon logs in-app (In-Domain Class 3 — no terminal eject).

        Captures journalctl (or the daemon log file as a fallback) into one
        scrollable in-pane view instead of printing to the terminal.
        """
        title = "MeshForge Daemon Logs (last 100 lines)"
        try:
            # Try journalctl first (systemd)
            result = subprocess.run(
                ['journalctl', '-u', 'meshforge', '-n', '100',
                 '--no-pager', '--output=short-iso'],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.strip()
            if output and "No entries" not in output:
                out = output
            else:
                # Fall back to daemon log file
                from utils.paths import get_real_user_home
                log_file = get_real_user_home() / ".local" / "share" / "meshforge" / "daemon.log"
                if log_file.exists():
                    out = "\n".join(log_file.read_text().splitlines()[-100:])
                else:
                    out = (
                        "No daemon logs found.\n\n"
                        "The daemon writes logs to journald (if running as a\n"
                        "systemd service) or to stderr (foreground mode).\n\n"
                        f"Log file path: {log_file}"
                    )
        except FileNotFoundError:
            out = "journalctl not available (not a systemd system)."
        except subprocess.TimeoutExpired:
            out = "Timed out reading logs."
        except Exception as e:
            out = f"Failed to read logs: {e}"

        self.ctx.dialog.textbox(title, out)
