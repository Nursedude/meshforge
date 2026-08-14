"""
System Tools Handler — drop-to-shell access for NOC operations.

Converted from system_tools_mixin.py as part of the mixin-to-registry
migration (Batch 8).

History: this handler once carried a full in-app Linux diagnostic menu
(top/htop/btop, ps/kill, netstat/ss, lsusb/lspci, vmstat/free, df, systemd,
journalctl). That menu (~1300 lines) was removed 2026-05-29 as dead code — the
TUI reliability audit found ``execute()`` only ever dispatched ``shell`` and the
menu was unreachable. Network, hardware, log, and service diagnostics each have
a dedicated live handler (NetworkTools/Hardware/Logs/ServiceMenu); the remaining
interactive tools are reachable from the shell this handler drops into.
See .claude/plans/tui_audit_register.md (#103).
"""

import subprocess
import logging

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class SystemToolsHandler(BaseHandler):
    """Drop-to-shell access for NOC operations."""

    handler_id = "system_tools"
    menu_section = "system"

    def menu_items(self):
        return [
            ("shell", "Linux Shell         Drop to bash", None),
        ]

    def execute(self, action):
        if action == "shell":
            self._drop_to_shell()

    def _drop_to_shell(self):
        """Drop to an interactive shell."""
        self.ctx.dialog.msgbox(
            "Shell Access",
            "Dropping to shell...\n\n"
            "Type 'exit' to return to MeshForge.\n\n"
            "Useful commands:\n"
            "  meshtastic --info\n"
            "  rnstatus\n"
            "  journalctl -f\n"  # in-domain-ok: usage examples FOR the sanctioned shell escape-hatch
            "  systemctl status meshtasticd"  # in-domain-ok: usage examples FOR the sanctioned shell escape-hatch
        )

        clear_screen()
        print("=== MeshForge Shell ===")
        print("Type 'exit' to return to the menu.\n")

        # Try to use user's preferred shell
        import os
        shell = os.environ.get('SHELL', '/bin/bash')

        try:
            subprocess.run([shell], timeout=None)
        except Exception as e:
            print(f"Shell error: {e}")
            self.ctx.wait_for_enter()
