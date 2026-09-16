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
                if self.ctx.dialog.yesno("Confirm Reboot", "Reboot the system now?",
                                         default_no=True):
                    self._power_action('reboot', "Reboot")
            elif choice == "shutdown":
                if self.ctx.dialog.yesno("Confirm Shutdown", "Shutdown the system now?",
                                         default_no=True):
                    self._power_action('poweroff', "Shutdown")

    def _power_action(self, verb: str, label: str) -> None:
        """Request a power transition and REPORT what systemd actually said.

        Before 2026-09-15 both branches were a bare
        ``subprocess.run(_sudo_cmd([...]), timeout=30)`` with the result
        discarded, and this module contained neither a msgbox nor a print —
        the only handler with no user-facing output at all. A polkit denial,
        a missing systemctl or a read-only /run therefore produced a silent
        menu re-render: the operator pressed Reboot, confirmed, and watched
        nothing happen with no idea why.

        Asymmetric by design. A SUCCESSFUL request needs no dialog (the box
        is going down; whatever we draw is a race). A FAILED one is the whole
        reason this function exists, so it always speaks and it quotes the
        real stderr rather than a guess.
        """
        from utils.service_check import _sudo_cmd

        argv = _sudo_cmd(['systemctl', verb])
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("%s request timed out after 30s", label)
            self.ctx.dialog.msgbox(
                f"{label} Timed Out",
                f"'systemctl {verb}' did not return within 30 seconds.\n\n"
                f"The system may still be shutting down. If it is still up in\n"
                f"a minute, the request did not take effect.",
            )
            return
        except (OSError, ValueError) as e:
            logger.error("%s request could not run: %s", label, e)
            self.ctx.dialog.msgbox(
                f"{label} Failed",
                f"Could not run 'systemctl {verb}':\n\n{type(e).__name__}: {e}",
            )
            return

        if result.returncode == 0:
            # Best-effort notice only — we are racing the shutdown.
            logger.info("%s requested (systemctl %s returned 0)", label, verb)
            try:
                self.ctx.dialog.infobox(
                    f"{label} Requested",
                    "The system is going down now.",
                )
            except Exception:  # in-domain-ok: we are mid-shutdown, nothing to fix
                pass
            return

        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"systemctl exited {result.returncode} with no message."
        logger.error("%s refused: rc=%s %s", label, result.returncode, detail)
        self.ctx.dialog.msgbox(
            f"{label} Refused",
            f"'systemctl {verb}' exited {result.returncode}.\n\n"
            f"{detail[:500]}\n\n"
            f"The system is still running.",
        )
