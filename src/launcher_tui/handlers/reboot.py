"""
Reboot/shutdown handler — safe system power control.

Batch 10b: Extracted from MeshForgeLauncher._reboot_menu() in main.py.
"""

import logging
import subprocess

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class RebootHandler(BaseHandler):
    """Reboot/Shutdown — safe system power control."""

    handler_id = "reboot"
    menu_section = "system"

    def menu_items(self):
        return [
            ("reboot", "Reboot/Shutdown     Safe system control", None),
        ]

    def execute(self, action):
        if action == "reboot":
            self._reboot_menu()

    def _reboot_menu(self):
        """Safe reboot/shutdown options."""
        from utils.service_check import _sudo_cmd

        while True:
            choices = [
                ("reboot", "Reboot              Restart system"),
                ("shutdown", "Shutdown            Power off"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Reboot / Shutdown",
                "System power options:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "reboot":
                if self.ctx.dialog.yesno("Confirm Reboot", "Reboot the system now?"):
                    subprocess.run(_sudo_cmd(['systemctl', 'reboot']), timeout=30)
            elif choice == "shutdown":
                if self.ctx.dialog.yesno("Confirm Shutdown", "Shutdown the system now?"):
                    subprocess.run(_sudo_cmd(['systemctl', 'poweroff']), timeout=30)
