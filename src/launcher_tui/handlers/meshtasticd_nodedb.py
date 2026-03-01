"""
Meshtasticd Node DB Cleanup Handler — Phantom node detection and removal.

Converted from meshtasticd_config_mixin.py (lines 697-1045) as part of
the mixin-to-registry migration (Batch 9).

Sub-handler registered in section "meshtasticd", dispatched from
MeshtasticdConfigHandler's thin dispatcher menu.
"""

import logging
import re
import subprocess

from handler_protocol import BaseHandler
from handlers.meshtasticd_config import (
    OVERLAY_PATH, read_overlay, write_overlay,
)
logger = logging.getLogger(__name__)

from utils.meshtastic_http import get_http_client as _get_http_client


class MeshtasticdNodeDBHandler(BaseHandler):
    """TUI handler for node database cleanup operations."""

    handler_id = "meshtasticd_nodedb"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("cleanup", "Node DB Cleanup", None),
        ]

    def execute(self, action):
        if action == "cleanup":
            self._node_db_cleanup_menu()

    def _node_db_cleanup_menu(self):
        """Node database cleanup — identify and remove phantom/incomplete nodes."""
        while True:
            choices = [
                ("scan", "Scan for Phantom Nodes"),
                ("reset", "Reset Node Database (removes ALL nodes)"),
                ("maxnodes", "Check MaxNodes Setting"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Node DB Cleanup",
                "Clean up the meshtasticd node database.\n\n"
                "Phantom nodes (incomplete data from MQTT) can\n"
                "crash the web client when clicked.\n\n"
                "Scan identifies nodes with missing info.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "scan": ("Scan Phantom Nodes", self._scan_phantom_nodes),
                "reset": ("Reset Node DB", self._reset_node_database),
                "maxnodes": ("Check MaxNodes", self._check_maxnodes),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _scan_phantom_nodes(self):
        """Scan for phantom/incomplete nodes via HTTP API."""
        self.ctx.dialog.infobox("Scanning", "Fetching node list from meshtasticd...")

        try:
            client = _get_http_client()

            if not client.is_available:
                self.ctx.dialog.msgbox(
                    "Not Available",
                    "meshtasticd HTTP API not reachable.\n\n"
                    "Ensure meshtasticd is running:\n"
                    "  sudo systemctl start meshtasticd"
                )
                return

            nodes = client.get_nodes()

            if not nodes:
                self.ctx.dialog.msgbox("No Nodes", "No nodes found in the device database.")
                return

            phantom = []
            healthy = []
            for node in nodes:
                has_name = bool(node.long_name.strip()) or bool(node.short_name.strip())
                if not has_name:
                    phantom.append(node)
                else:
                    healthy.append(node)

            if not phantom:
                self.ctx.dialog.msgbox(
                    "All Clear",
                    f"All {len(nodes)} nodes have valid names.\n\n"
                    "No phantom nodes detected.\n\n"
                    "If the web client still crashes on search,\n"
                    "this may be an upstream Meshtastic bug.\n"
                    "See: github.com/meshtastic/web/issues/862"
                )
                return

            lines = [
                f"Found {len(phantom)} phantom node(s) "
                f"(of {len(nodes)} total)\n",
                "Phantom nodes have no name data and can crash",
                "the web client when clicked in search results.\n",
            ]

            for node in phantom[:20]:
                node_id = node.node_id
                hw = node.hw_model or "unknown hw"
                heard = ""
                if node.last_heard > 0:
                    import time
                    age = time.time() - node.last_heard
                    if age < 3600:
                        heard = f"{age / 60:.0f}m ago"
                    elif age < 86400:
                        heard = f"{age / 3600:.0f}h ago"
                    else:
                        heard = f"{age / 86400:.0f}d ago"
                mqtt_tag = " [MQTT]" if node.via_mqtt else ""
                lines.append(f"  {node_id} ({hw}) {heard}{mqtt_tag}")

            if len(phantom) > 20:
                lines.append(f"  ... and {len(phantom) - 20} more")

            lines.append("")
            lines.append("Options:")
            lines.append("  - Remove individually (if CLI supports it)")
            lines.append("  - Reset entire node DB (re-discovers all)")
            lines.append("  - Reduce MaxNodes to limit MQTT phantoms")

            self.ctx.dialog.msgbox("Phantom Nodes Found", "\n".join(lines))

            if self.ctx.dialog.yesno(
                "Remove Phantom Nodes?",
                f"Try to remove {len(phantom)} phantom node(s)?\n\n"
                "Uses 'meshtastic --remove-node' for each.\n"
                "If that command isn't available, will offer\n"
                "to reset the full node database instead.",
                default_no=True
            ):
                self._remove_phantom_nodes(phantom)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Scan failed:\n{e}")

    def _remove_phantom_nodes(self, phantom_nodes):
        """Try to remove phantom nodes individually via CLI."""
        cli = self.ctx.get_meshtastic_cli()
        removed = 0
        failed = 0
        cli_unsupported = False

        self.ctx.dialog.infobox(
            "Removing",
            f"Removing {len(phantom_nodes)} phantom node(s)..."
        )

        for node in phantom_nodes:
            node_id = node.node_id
            try:
                if node_id.startswith('!'):
                    node_num = str(int(node_id[1:], 16))
                else:
                    node_num = node_id
            except ValueError:
                node_num = node_id

            try:
                result = subprocess.run(
                    [cli, '--host', 'localhost:4403',
                     '--remove-node', node_num],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    removed += 1
                else:
                    stderr = result.stderr or ""
                    if "unrecognized" in stderr.lower() or "unknown" in stderr.lower():
                        cli_unsupported = True
                        break
                    failed += 1
            except FileNotFoundError:
                self.ctx.dialog.msgbox(
                    "CLI Not Found",
                    "meshtastic CLI not installed.\n\n"
                    "Install: pip install meshtastic"
                )
                return
            except subprocess.TimeoutExpired:
                failed += 1

        if cli_unsupported:
            if self.ctx.dialog.yesno(
                "CLI Too Old",
                "'meshtastic --remove-node' not available.\n\n"
                "Your meshtastic CLI version doesn't support\n"
                "individual node removal.\n\n"
                "Options:\n"
                "  - Upgrade: pip install --upgrade meshtastic\n"
                "  - Reset entire node DB (re-discovers all)\n\n"
                "Reset entire node database now?",
                default_no=True
            ):
                self._reset_node_database()
        elif removed > 0 or failed > 0:
            self.ctx.dialog.msgbox(
                "Cleanup Complete",
                f"Removed: {removed} phantom node(s)\n"
                f"Failed:  {failed}\n\n"
                "The device will re-discover legitimate nodes\n"
                "through normal mesh traffic."
            )
        else:
            self.ctx.dialog.msgbox("No Changes", "No nodes were removed.")

    def _reset_node_database(self):
        """Reset the entire node database (nuclear option)."""
        confirm = self.ctx.dialog.yesno(
            "Reset Node Database",
            "This will CLEAR ALL known nodes from the device.\n\n"
            "The device will re-discover nodes through\n"
            "normal mesh traffic (may take minutes to hours).\n\n"
            "This fixes web client crashes caused by phantom\n"
            "nodes with incomplete data.\n\n"
            "Proceed?",
            default_no=True
        )

        if not confirm:
            return

        cli = self.ctx.get_meshtastic_cli()
        try:
            result = subprocess.run(
                [cli, '--host', 'localhost:4403', '--reset-nodedb'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                self.ctx.dialog.msgbox(
                    "Database Reset",
                    "Node database cleared.\n\n"
                    "Nodes will re-appear as they are heard\n"
                    "over the mesh (a few minutes for nearby nodes)."
                )
            else:
                self.ctx.dialog.msgbox(
                    "Reset Failed",
                    f"Command failed:\n{result.stderr or result.stdout}"
                )
        except FileNotFoundError:
            self.ctx.dialog.msgbox("Error", "meshtastic CLI not found.")
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Command timed out.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Reset failed:\n{e}")

    def _check_maxnodes(self):
        """Check and optionally reduce MaxNodes in config.yaml."""
        from pathlib import Path

        config_path = Path('/etc/meshtasticd/config.yaml')

        if not config_path.exists():
            self.ctx.dialog.msgbox(
                "Config Not Found",
                f"{config_path} not found.\n\n"
                "meshtasticd may not be installed."
            )
            return

        try:
            content = config_path.read_text()
        except OSError as e:
            self.ctx.dialog.msgbox("Error", f"Cannot read config:\n{e}")
            return

        overlay = read_overlay()
        overlay_maxnodes = overlay.get('General', {}).get('MaxNodes')

        match = re.search(r'MaxNodes:\s*(\d+)', content)
        base_value = int(match.group(1)) if match else None
        current = overlay_maxnodes if overlay_maxnodes is not None else base_value

        if current is None:
            self.ctx.dialog.msgbox(
                "MaxNodes Not Set",
                "MaxNodes is not configured in config.yaml.\n\n"
                "Default is typically 200 (device dependent).\n\n"
                "Add to General section:\n"
                "  General:\n"
                "    MaxNodes: 100"
            )
            return

        source = "overlay" if overlay_maxnodes is not None else "config.yaml"
        text = (
            f"Current MaxNodes: {current} (from {source})\n\n"
            "MaxNodes limits how many nodes the device tracks.\n"
            "High values accumulate phantom MQTT nodes that\n"
            "can crash the web client.\n\n"
            "Recommended values:\n"
            "  50  — Small local mesh\n"
            "  100 — Medium mesh with MQTT\n"
            "  200 — Large mesh (default)\n\n"
        )

        if current > 100:
            text += (
                f"Your value ({current}) is high. Reducing to 100\n"
                "limits phantom node accumulation."
            )

        new_val = self.ctx.dialog.inputbox(
            "MaxNodes Setting",
            text + "\n\nEnter new MaxNodes value (or Cancel to keep):",
            str(current)
        )

        if new_val is None:
            return

        try:
            new_int = int(new_val)
            if new_int < 10 or new_int > 500:
                self.ctx.dialog.msgbox("Invalid", "MaxNodes must be between 10 and 500.")
                return
        except ValueError:
            self.ctx.dialog.msgbox("Invalid", "Enter a number between 10 and 500.")
            return

        if new_int == current:
            self.ctx.dialog.msgbox("No Change", f"MaxNodes remains at {current}.")
            return

        overlay = read_overlay()
        if 'General' not in overlay:
            overlay['General'] = {}
        overlay['General']['MaxNodes'] = new_int

        if not write_overlay(overlay, self.ctx.dialog):
            return

        if self.ctx.dialog.yesno(
            "Restart Service?",
            f"MaxNodes override: {current} -> {new_int}\n\n"
            f"Saved to: {OVERLAY_PATH}\n"
            "(config.yaml unchanged)\n\n"
            "Restart meshtasticd to apply?",
            default_no=False
        ):
            handler = self.ctx.registry.get_handler("meshtasticd_config") if self.ctx.registry else None
            if handler and hasattr(handler, '_restart_meshtasticd'):
                handler._restart_meshtasticd()
        else:
            self.ctx.dialog.msgbox(
                "Config Updated",
                f"MaxNodes set to {new_int}.\n\n"
                f"Overlay: {OVERLAY_PATH}\n"
                "(config.yaml unchanged)\n\n"
                "Restart meshtasticd to apply:\n"
                "  sudo systemctl restart meshtasticd"
            )
