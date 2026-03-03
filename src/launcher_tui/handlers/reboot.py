"""
Reboot/shutdown handler — safe system power control.

Batch 10b: Extracted from MeshForgeLauncher._reboot_menu() in main.py.
"""

import logging
import subprocess

from handler_protocol import BaseHandler, PRIVILEGE_ADMIN

logger = logging.getLogger(__name__)


class RebootHandler(BaseHandler):
    """Reboot/Shutdown — safe system power control."""

    handler_id = "reboot"
    menu_section = "system"
    privilege_level = PRIVILEGE_ADMIN

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

        def _do_reboot():
            if self.ctx.dialog.yesno("Confirm Reboot", "Reboot the system now?"):
                subprocess.run(_sudo_cmd(['systemctl', 'reboot']), timeout=30)

        def _do_shutdown():
            if self.ctx.dialog.yesno("Confirm Shutdown", "Shutdown the system now?"):
                subprocess.run(_sudo_cmd(['systemctl', 'poweroff']), timeout=30)

        choices = [
            ("reboot", "Reboot              Restart system"),
            ("shutdown", "Shutdown            Power off"),
        ]
        dispatch = {
            "reboot": ("Reboot", _do_reboot),
            "shutdown": ("Shutdown", _do_shutdown),
        }
        self.run_menu_loop(
            "Reboot / Shutdown",
            "System power options:",
            choices, dispatch,
        )
