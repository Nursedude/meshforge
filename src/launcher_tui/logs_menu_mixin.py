"""
Logs Menu Mixin - Log viewing functionality.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import os
import subprocess
from pathlib import Path
from backend import clear_screen
from utils.paths import get_real_user_home


class LogsMenuMixin:
    """Mixin providing log viewing functionality."""

    # Systemd units MeshForge monitors — used to scope journalctl queries
    # so we never surface unrelated OS errors (bluetooth, NFS, etc.)
    MESH_UNITS = ['meshtasticd', 'rnsd', 'mosquitto', 'nomadnet']

    def _logs_menu(self):
        """Log viewer - all terminal-native."""
        while True:
            choices = [
                ("live-mesh", "Live: meshtasticd (Ctrl+C to stop)"),
                ("live-rns", "Live: rnsd (Ctrl+C to stop)"),
                ("live-all", "Live: all services (Ctrl+C to stop)"),
                ("errors", "Errors (last hour)"),
                ("mesh-50", "meshtasticd (last 50 lines)"),
                ("rns-50", "rnsd (last 50 lines)"),
                ("boot", "Boot messages (this boot)"),
                ("kernel", "Kernel messages (dmesg)"),
                ("meshforge", "MeshForge app logs"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
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
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _view_live_meshtasticd(self):
        """View live meshtasticd log stream."""
        clear_screen()
        print("=== meshtasticd live log (Ctrl+C to stop) ===\n")
        try:
            subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '-f', '-n', '30', '--no-pager'],
                timeout=None
            )
        except KeyboardInterrupt:
            pass

    def _view_live_rnsd(self):
        """View live rnsd log stream."""
        clear_screen()
        print("=== rnsd live log (Ctrl+C to stop) ===\n")
        try:
            subprocess.run(
                ['journalctl', '-u', 'rnsd', '-f', '-n', '30', '--no-pager'],
                timeout=None
            )
        except KeyboardInterrupt:
            pass

    def _view_live_all(self):
        """View live log stream for all mesh services."""
        clear_screen()
        print("=== Mesh services live log (Ctrl+C to stop) ===\n")
        cmd = ['journalctl', '-f', '-n', '30', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        try:
            subprocess.run(cmd, timeout=None)
        except KeyboardInterrupt:
            pass

    def _view_error_logs(self):
        """View error-level logs from mesh services in the last hour."""
        clear_screen()
        print("=== Mesh Service Errors (last hour, priority err+) ===\n")
        cmd = ['journalctl', '-p', 'err', '--since', '1 hour ago', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        subprocess.run(cmd, timeout=30)
        self._wait_for_enter()

    def _view_meshtasticd_recent(self):
        """View recent meshtasticd log lines."""
        clear_screen()
        print("=== meshtasticd (last 50 lines) ===\n")
        subprocess.run(
            ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'],
            timeout=15
        )
        self._wait_for_enter()

    def _view_rnsd_recent(self):
        """View recent rnsd log lines."""
        clear_screen()
        print("=== rnsd (last 50 lines) ===\n")
        subprocess.run(
            ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
            timeout=15
        )
        self._wait_for_enter()

    def _view_boot_messages(self):
        """View mesh service boot messages from this boot."""
        clear_screen()
        print("=== Mesh Service Boot Messages (this boot) ===\n")
        cmd = ['journalctl', '-b', '-n', '100', '--no-pager']
        for unit in self.MESH_UNITS:
            cmd.extend(['-u', unit])
        subprocess.run(cmd, timeout=15)
        self._wait_for_enter()

    def _view_kernel_messages(self):
        """View kernel messages via dmesg."""
        clear_screen()
        print("=== Kernel messages (dmesg) ===\n")
        subprocess.run(['dmesg', '--time-format=reltime'], timeout=10)
        self._wait_for_enter()

    def _view_meshforge_logs(self):
        """View MeshForge application logs."""
        log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"

        if not log_dir.exists():
            self.dialog.msgbox("Logs", "No MeshForge logs found yet.\n\nLogs are created when you use MeshForge.")
            return

        log_files = list(log_dir.glob("*.log"))
        if not log_files:
            self.dialog.msgbox("Logs", "No log files found in:\n" + str(log_dir))
            return

        # Show most recent log
        latest_log = max(log_files, key=lambda f: f.stat().st_mtime)

        try:
            content = latest_log.read_text()
            lines = content.strip().split('\n')[-50:]  # Last 50 lines

            clear_screen()
            print(f"=== MeshForge Log: {latest_log.name} ===\n")
            print('\n'.join(lines))
            print("\n" + "=" * 50)
            self._wait_for_enter()
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log: {e}")
