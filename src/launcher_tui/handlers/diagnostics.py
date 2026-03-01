"""
CLI diagnostics handler — run diagnostic and status scripts.

Batch 10b: Extracted from MeshForgeLauncher._run_diagnostics()
and _run_terminal_status() in main.py.
"""

import logging
import subprocess
import sys

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class DiagnosticsHandler(BaseHandler):
    """Diagnostics — run CLI diagnostic and status tools."""

    handler_id = "diagnostics"
    menu_section = "system"

    def menu_items(self):
        return [
            ("diagnose", "Diagnostics         System health check", None),
            ("status", "Quick Status        One-shot status display", None),
        ]

    def execute(self, action):
        dispatch = {
            "diagnose": ("Diagnostics", self._run_diagnostics),
            "status": ("Quick Status", self._run_terminal_status),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    def _run_diagnostics(self):
        """Run the MeshForge diagnostic tool."""
        from backend import clear_screen

        clear_screen()
        try:
            result = subprocess.run(
                [sys.executable, str(self.ctx.src_dir / 'cli' / 'diagnose.py')],
                timeout=30
            )
            if result.returncode != 0:
                print("\nDiagnostics encountered an error.")
        except subprocess.TimeoutExpired:
            print("\n\nDiagnostics timed out (30s).")
        except FileNotFoundError:
            print("\nDiagnostic tool not found at: src/cli/diagnose.py")
        except KeyboardInterrupt:
            print("\n\nAborted.")

        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _run_terminal_status(self):
        """Run meshforge-status (terminal-native one-shot status)."""
        from backend import clear_screen

        clear_screen()
        try:
            result = subprocess.run(
                [sys.executable, str(self.ctx.src_dir / 'cli' / 'status.py')],
                timeout=20
            )
            if result.returncode != 0:
                print("\nStatus check encountered an error.")
        except subprocess.TimeoutExpired:
            print("\n\nStatus check timed out (20s).")
        except KeyboardInterrupt:
            print("\n\nAborted.")

        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()
