"""
Logs Handler — Log viewing functionality.

Converted from logs_menu_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import subprocess
from pathlib import Path
from typing import List

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.paths import get_real_user_home

try:
    from utils.logging_config import set_log_level, get_current_log_level, cleanup_old_logs
    _HAS_LOG_LEVEL = True
    _HAS_GET_LEVEL = True
    _HAS_CLEANUP = True
except ImportError:
    _HAS_LOG_LEVEL = False
    _HAS_GET_LEVEL = False
    _HAS_CLEANUP = False


class LogsHandler(BaseHandler):
    """TUI handler for log viewing."""

    handler_id = "logs"
    menu_section = "system"

    MESH_UNITS = ['meshtasticd', 'rnsd', 'mosquitto', 'nomadnet']

    def menu_items(self):
        return [
            ("logs", "Logs                View/follow logs", None),
        ]

    def execute(self, action):
        if action == "logs":
            self._logs_menu()

    def _logs_menu(self):
        while True:
            choices = [
                ("live-mesh", "Live: meshtasticd      (Ctrl+C to stop)"),
                ("live-rns", "Live: rnsd             (Ctrl+C to stop)"),
                ("live-all", "Live: all services     (Ctrl+C to stop)"),
                ("errors", "Errors                 Last hour, priority err+"),
                ("mesh-50", "meshtasticd            Last 50 lines"),
                ("rns-50", "rnsd                   Last 50 lines"),
                ("boot", "Boot Messages          This boot"),
                ("kernel", "Kernel Messages        dmesg"),
                ("meshforge", "MeshForge App Logs     Browse log files"),
                ("crash", "Crash Log              TUI error output"),
                ("level", "Log Level              Change runtime verbosity"),
                ("cleanup", "Log Cleanup            Remove old log files"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Log Viewer",
                "Snapshots open in an in-app scrollable pane; "
                "Live views stream in the terminal (Ctrl+C to stop):",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "live-mesh": ("Live meshtasticd Logs", self._view_live_meshtasticd),
                "live-rns": ("Live rnsd Logs", self._view_live_rnsd),
                "live-all": ("Live All Logs", self._view_live_all),
                "errors": ("Error Logs", self._view_error_logs),
                "mesh-50": ("meshtasticd Logs", self._view_meshtasticd_recent),
                "rns-50": ("rnsd Logs", self._view_rnsd_recent),
                "boot": ("Boot Messages", self._view_boot_messages),
                "kernel": ("Kernel Messages", self._view_kernel_messages),
                "meshforge": ("MeshForge Logs", self._view_meshforge_logs),
                "crash": ("Crash Log", self._view_crash_log),
                "level": ("Log Level", self._change_log_level),
                "cleanup": ("Log Cleanup", self._cleanup_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _view_live_log(self, title: str, cmd: List[str]) -> None:
        clear_screen()
        print(f"=== {title} (Ctrl+C to stop) ===\n")
        proc = None
        try:
            proc = subprocess.Popen(cmd)
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            print("\n[Log view timed out after 5 minutes]")
        except KeyboardInterrupt:
            pass
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def _show_command_output(self, title: str, cmd: List[str],
                             timeout: int = 15) -> None:
        """Run a read-only diagnostic command, CAPTURE its output, and show it
        in an in-app scrollable pane (In-Domain/MF018 Class 3 — no terminal
        eject). A timeout or a missing binary is shown in-pane, never dumped to
        the terminal and never silently swallowed.
        """
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout)
            out = r.stdout or ""
            if r.stderr:
                out += ("\n[stderr]\n" + r.stderr)
        except subprocess.TimeoutExpired:
            out = f"[{cmd[0]} timed out after {timeout}s]"
        except (subprocess.SubprocessError, OSError) as e:
            out = f"[could not run {cmd[0]}: {e}]"
        self.ctx.dialog.textbox(title, out)

    def _view_live_meshtasticd(self):
        self._view_live_log(
            "meshtasticd live log",
            ['journalctl', '-u', 'meshtasticd', '-f', '-n', '30', '--no-pager'],
        )

    def _view_live_rnsd(self):
        self._view_live_log(
            "rnsd live log",
            ['journalctl', '-u', 'rnsd', '-f', '-n', '30', '--no-pager'],
        )

    def _view_live_all(self):
        cmd = ['journalctl', '-f', '-n', '30', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        self._view_live_log("Mesh services live log", cmd)

    def _view_error_logs(self):
        cmd = ['journalctl', '-p', 'err', '--since', '1 hour ago', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        self._show_command_output(
            "Mesh Service Errors (last hour, priority err+)", cmd, timeout=30)

    def _view_meshtasticd_recent(self):
        self._show_command_output(
            "meshtasticd (last 50 lines)",
            ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'])

    def _view_rnsd_recent(self):
        self._show_command_output(
            "rnsd (last 50 lines)",
            ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'])

    def _view_boot_messages(self):
        cmd = ['journalctl', '-b', '-n', '100', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        self._show_command_output(
            "Mesh Service Boot Messages (this boot)", cmd)

    def _view_kernel_messages(self):
        self._show_command_output(
            "Kernel messages (dmesg)",
            ['dmesg', '--time-format=reltime'], timeout=10)

    def _view_meshforge_logs(self):
        home = get_real_user_home()
        log_dirs = [
            home / ".config" / "meshforge" / "logs",
            home / ".cache" / "meshforge" / "logs",
        ]

        all_logs = []
        for d in log_dirs:
            if d.exists():
                all_logs.extend(d.glob("meshforge_*.log"))
                all_logs.extend(d.glob("meshforge_*.log.*"))

        if not all_logs:
            self.ctx.dialog.msgbox(
                "MeshForge Logs",
                "No MeshForge application logs found.\n\n"
                "Logs are written to:\n"
                f"  {log_dirs[0]}\n\n"
                "Logs are created automatically during each session."
            )
            return

        all_logs.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        if len(all_logs) == 1:
            self._display_log_file(all_logs[0])
            return

        choices = []
        for i, log_file in enumerate(all_logs[:10]):
            stat = log_file.stat()
            size_kb = stat.st_size / 1024
            from datetime import datetime
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            label = f"{log_file.name:<30s} {size_kb:>6.1f}KB  {mtime}"
            choices.append((str(i), label))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "MeshForge Log Files",
            f"Found {len(all_logs)} log file(s). Newest first:",
            choices
        )

        if choice is None or choice == "back":
            return

        try:
            idx = int(choice)
            self._display_log_file(all_logs[idx])
        except (ValueError, IndexError):
            pass

    def _display_log_file(self, log_path: Path, tail_lines: int = 80) -> None:
        # In-app scrollable pane (In-Domain/MF018 Class 3) — was a print-to-
        # terminal + wait-for-enter.
        try:
            content = log_path.read_text()
            lines = content.strip().split('\n')
            total = len(lines)
            shown = lines[-tail_lines:]
            header = (
                f"{log_path.name} — {total} total lines, "
                f"showing last {len(shown)}\n"
                f"Path: {log_path}  "
                f"({log_path.stat().st_size / 1024:.1f} KB)\n"
                + "=" * 60)
            self.ctx.dialog.textbox(
                log_path.name, header + "\n" + '\n'.join(shown))
        except OSError as e:
            self.ctx.dialog.msgbox("Error", f"Failed to read log file:\n{e}")

    def _view_crash_log(self):
        crash_paths = [
            get_real_user_home() / ".cache" / "meshforge" / "logs" / "tui_errors.log",
            Path("/tmp") / "tui_errors.log",
        ]

        crash_log = None
        for p in crash_paths:
            if p.exists() and p.stat().st_size > 0:
                crash_log = p
                break

        if not crash_log:
            self.ctx.dialog.msgbox(
                "Crash Log",
                "No crash log found (good news!).\n\n"
                "The crash log captures unhandled exceptions\n"
                "and stderr output from the TUI process."
            )
            return

        self._display_log_file(crash_log, tail_lines=50)

    def _change_log_level(self):
        """Change the runtime log level."""
        if not _HAS_LOG_LEVEL:
            self.ctx.dialog.msgbox("Error", "Log level control unavailable.")
            return

        current = get_current_log_level() if _HAS_GET_LEVEL else "UNKNOWN"

        choices = [
            ("DEBUG", f"DEBUG          {'(current)' if current == 'DEBUG' else 'Verbose'}"),
            ("INFO", f"INFO           {'(current)' if current == 'INFO' else 'Normal'}"),
            ("WARNING", f"WARNING        {'(current)' if current == 'WARNING' else 'Quiet'}"),
            ("ERROR", f"ERROR          {'(current)' if current == 'ERROR' else 'Errors only'}"),
        ]

        choice = self.ctx.dialog.menu(
            "Log Level",
            f"Current level: {current}\nChange runtime log verbosity:",
            choices
        )

        if choice and choice in ("DEBUG", "INFO", "WARNING", "ERROR"):
            level = getattr(logging, choice)
            set_log_level(level)
            self.ctx.dialog.msgbox(
                "Log Level Changed",
                f"Log level set to {choice}.\n\n"
                "This affects the current session only.\n"
                "File logging always captures DEBUG level."
            )

    def _cleanup_logs(self):
        """Remove old log files."""
        if not _HAS_CLEANUP:
            self.ctx.dialog.msgbox("Error", "Log cleanup unavailable.")
            return

        choice = self.ctx.dialog.yesno(
            "Log Cleanup",
            "Remove log files older than 30 days?\n\n"
            "This frees disk space on long-running deployments.\n"
            "Current session logs will not be affected."
        )

        if choice:
            deleted = cleanup_old_logs(max_age_days=30)
            self.ctx.dialog.msgbox(
                "Log Cleanup Complete",
                f"Removed {deleted} old log file(s)." if deleted
                else "No old log files found."
            )
