"""
Logs Handler — Log viewing functionality.

Converted from logs_menu_mixin.py as part of the mixin-to-registry migration.
"""

import subprocess
from pathlib import Path
from typing import List

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.paths import get_real_user_home


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
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Log Viewer",
                "Terminal-native logs (real journalctl):",
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
                    proc.wait()

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
        clear_screen()
        print("=== Mesh Service Errors (last hour, priority err+) ===\n")
        cmd = ['journalctl', '-p', 'err', '--since', '1 hour ago', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        subprocess.run(cmd, timeout=30)
        self.ctx.wait_for_enter()

    def _view_meshtasticd_recent(self):
        clear_screen()
        print("=== meshtasticd (last 50 lines) ===\n")
        subprocess.run(
            ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'],
            timeout=15
        )
        self.ctx.wait_for_enter()

    def _view_rnsd_recent(self):
        clear_screen()
        print("=== rnsd (last 50 lines) ===\n")
        subprocess.run(
            ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
            timeout=15
        )
        self.ctx.wait_for_enter()

    def _view_boot_messages(self):
        clear_screen()
        print("=== Mesh Service Boot Messages (this boot) ===\n")
        cmd = ['journalctl', '-b', '-n', '100', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        subprocess.run(cmd, timeout=15)
        self.ctx.wait_for_enter()

    def _view_kernel_messages(self):
        clear_screen()
        print("=== Kernel messages (dmesg) ===\n")
        subprocess.run(['dmesg', '--time-format=reltime'], timeout=10)
        self.ctx.wait_for_enter()

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
        try:
            content = log_path.read_text()
            lines = content.strip().split('\n')
            total = len(lines)
            shown = lines[-tail_lines:]

            clear_screen()
            print(f"=== {log_path.name} ({total} total lines, showing last {len(shown)}) ===\n")
            print('\n'.join(shown))
            print(f"\n{'=' * 60}")
            print(f"Full path: {log_path}")
            print(f"Size: {log_path.stat().st_size / 1024:.1f} KB")
            self.ctx.wait_for_enter()
        except Exception as e:
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
