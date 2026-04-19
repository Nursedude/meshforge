"""RNS Tools — read-only RNS CLI wrappers safe to run alongside the daemon.

These tools all attach to rnsd's shared instance for queries — no new RNS
listener is created, no LXMF identity is touched. Safe to use while
NomadNet daemon is serving the gateway.

Surfaces the most common day-to-day questions:
  - What does my network look like? (rnstatus)
  - Who can I reach? (rnpath -t)
  - Is this destination reachable? (rnpath <hash>)
  - What hash do I give peers so they can chat with me? (rnid on the
    gateway identity file)
"""

import logging
import os
import re
import subprocess
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Gateway identity location — created by the gateway on first start.
# See src/gateway/_rns_bridge_connection.py.
GATEWAY_IDENTITY_REL = ".config/meshforge/gateway_identity"

# 32 hex chars = 16-byte RNS destination hash
HEX_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class RNSToolsHandler(BaseHandler):
    """Read-only RNS CLI utilities — coexist with running daemon."""

    handler_id = "rns_tools"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("tools", "RNS Tools          rnstatus, paths, identity", None),
        ]

    def execute(self, action):
        if action == "tools":
            self._tools_menu()

    def _tools_menu(self):
        while True:
            choices = [
                ("status", "Network Status (rnstatus)"),
                ("paths", "List Known Paths (rnpath -t)"),
                ("lookup", "Look Up a Destination Hash"),
                ("identity", "Show My Gateway Hash (share to receive chats)"),
                ("probe", "Probe a Destination (round-trip test)"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "RNS Tools",
                "Read-only RNS utilities — safe to run while the\n"
                "MeshForge gateway / NomadNet daemon is active.",
                choices,
            )
            if choice is None or choice == "back":
                break
            dispatch = {
                "status": ("Network Status", self._show_status),
                "paths": ("Known Paths", self._show_paths),
                "lookup": ("Path Lookup", self._lookup_path),
                "identity": ("Gateway Hash", self._show_gateway_identity),
                "probe": ("Probe Destination", self._probe_destination),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _show_status(self):
        clear_screen()
        print("=== Network Status (rnstatus) ===\n")
        self._run_and_print(["rnstatus"], timeout=15)
        self.ctx.wait_for_enter()

    def _show_paths(self):
        clear_screen()
        print("=== Known Paths (rnpath -t) ===\n")
        print("All destinations RNS currently has a path to.\n")
        self._run_and_print(["rnpath", "-t"], timeout=15)
        self.ctx.wait_for_enter()

    def _lookup_path(self):
        dest = self.ctx.dialog.inputbox(
            "Look Up Destination",
            "Enter the 32-char hex destination hash to query.\n"
            "Example: 0123456789abcdef0123456789abcdef",
            "",
        )
        if not dest:
            return
        dest = dest.strip().lower()
        if not HEX_HASH_RE.match(dest):
            self.ctx.dialog.msgbox(
                "Invalid Hash",
                "Hash must be exactly 32 hexadecimal characters.",
            )
            return
        clear_screen()
        print(f"=== Path Lookup: {dest} ===\n")
        self._run_and_print(["rnpath", dest], timeout=20)
        self.ctx.wait_for_enter()

    def _show_gateway_identity(self):
        clear_screen()
        print("=== Gateway Identity ===\n")
        identity_path = get_real_user_home() / GATEWAY_IDENTITY_REL
        if not identity_path.exists():
            print(f"  No gateway identity found at:\n    {identity_path}\n")
            print("  The gateway creates this on first start.")
            print("  Is the meshforge-gateway service running?")
            self.ctx.wait_for_enter()
            return

        print(f"  Identity file: {identity_path}\n")
        # rnid -i <path> -p prints destination hashes for known aspects.
        # We want the lxmf.delivery hash (what peers DM via NomadNet/Sideband).
        rc = self._run_and_print(
            ["rnid", "-i", str(identity_path), "-H", "lxmf.delivery"],
            timeout=10,
        )
        if rc == 0:
            print()
            print("  Share this hash with peers so they can chat with you.")
            print("  In NomadNet/Sideband: New Conversation → paste the hash.")
        self.ctx.wait_for_enter()

    def _probe_destination(self):
        dest = self.ctx.dialog.inputbox(
            "Probe Destination",
            "Enter the 32-char hex destination hash to probe.\n"
            "Sends a small probe and waits for echo (round-trip test).",
            "",
        )
        if not dest:
            return
        dest = dest.strip().lower()
        if not HEX_HASH_RE.match(dest):
            self.ctx.dialog.msgbox(
                "Invalid Hash",
                "Hash must be exactly 32 hexadecimal characters.",
            )
            return
        # rnprobe needs an aspect; lxmf.delivery is the standard chat aspect.
        # Default 15s timeout matches rnprobe's own usage suggestions.
        clear_screen()
        print(f"=== Probe: {dest} (lxmf.delivery, 15s timeout) ===\n")
        self._run_and_print(
            ["rnprobe", "-w", "15", "lxmf.delivery", dest],
            timeout=25,
        )
        self.ctx.wait_for_enter()

    def _run_and_print(self, cmd, timeout):
        """Run an RNS CLI tool, stream stdout+stderr to the console.

        Returns the exit code, or -1 on subprocess error / timeout.
        """
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if r.stdout:
                print(r.stdout)
            if r.stderr:
                print(r.stderr)
            if r.returncode != 0:
                print(f"\n  (exit code {r.returncode})")
            return r.returncode
        except subprocess.TimeoutExpired:
            print(f"\n  Command timed out after {timeout}s.")
            return -1
        except FileNotFoundError:
            print(f"\n  Command not found: {cmd[0]}")
            print("  Install RNS: sudo pip3 install --break-system-packages rns")
            return -1
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\n  Failed to run: {e}")
            return -1
