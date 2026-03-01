"""
RNS Menu Handler — Thin dispatcher for the RNS / Reticulum submenu.

Converted from rns_menu_mixin.py as part of the mixin-to-registry migration.
Routes to sub-handlers (rns_config, rns_diagnostics, rns_interfaces,
rns_monitor, rns_sniffer) via the handler registry, and handles its own
inline items (status, paths, probe, identity, nodes, positions).
"""

import json
import logging
import os
import re
import shutil
from pathlib import Path

from handler_protocol import BaseHandler
from backend import clear_screen
from utils.paths import get_real_user_home, ReticulumPaths
from commands.rns import (
    get_identity_path, create_identities, list_known_destinations,
)

logger = logging.getLogger(__name__)

# Desired menu order for the RNS submenu.
# Sub-handler items are merged from the "rns" section; own items are inline.
_RNS_ORDERING = [
    "status", "monitor", "paths", "sniffer",
    "probe", "identity", "nodes", "positions",
    "diag", "repair", "drift",
    "ifaces", "config", "edit", "check",
]


class RNSMenuHandler(BaseHandler):
    """TUI handler for the RNS / Reticulum submenu."""

    handler_id = "rns_menu"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("rns", "RNS / Reticulum     Status, gateway, messaging", "rns"),
        ]

    def execute(self, action):
        if action == "rns":
            self._rns_submenu()

    # ------------------------------------------------------------------
    # Submenu dispatcher
    # ------------------------------------------------------------------

    def _rns_submenu(self):
        """RNS / Reticulum submenu — dispatches to sub-handlers and own methods."""
        while True:
            # Build choices from "rns" section handlers + own items
            registry_items = {}
            if self.ctx.registry:
                for tag, desc in self.ctx.registry.get_menu_items("rns"):
                    registry_items[tag] = desc

            # Own items (not from sub-handlers)
            own_items = {
                "status": "RNS Status (rnstatus)",
                "paths": "RNS Path Table (rnpath)",
                "probe": "Probe Destination (rnprobe)",
                "identity": "Identity Info (rnid)",
                "nodes": "Known Destinations",
                "positions": "Set Node Positions (for map)",
            }

            # Merge: registry items + own items
            all_items = {}
            all_items.update(own_items)
            all_items.update(registry_items)  # registry wins on conflicts

            # Build ordered choices list
            choices = []
            seen = set()
            for tag in _RNS_ORDERING:
                if tag in all_items and tag not in seen:
                    choices.append((tag, all_items[tag]))
                    seen.add(tag)
            # Add any items not in the ordering list
            for tag, desc in all_items.items():
                if tag not in seen:
                    choices.append((tag, desc))
                    seen.add(tag)
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "RNS / Reticulum",
                "Reticulum Network Stack tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            # Try sub-handler dispatch first (rns section)
            if self.ctx.registry and self.ctx.registry.dispatch("rns", choice):
                continue

            # Own inline dispatches
            own_dispatch = {
                "probe": ("Probe Destination", self._rns_probe_destination),
                "identity": ("Identity Info", self._rns_identity_info),
                "nodes": ("Known Destinations", self._rns_known_destinations),
                "positions": ("Set Node Positions", self._rns_set_node_positions),
            }
            entry = own_dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                continue

            # Inline RNS tool commands (status, paths) — need diagnostics handler
            try:
                diag = self.ctx.registry.get_handler("rns_diagnostics") if self.ctx.registry else None
                if choice == "status":
                    clear_screen()
                    print("=== RNS Status ===\n")
                    if diag:
                        diag._run_rns_tool(['rnstatus'], 'rnstatus')
                    else:
                        print("RNS diagnostics handler not available.")
                    self.ctx.wait_for_enter()
                elif choice == "paths":
                    clear_screen()
                    print("=== RNS Path Table ===\n")
                    if diag:
                        diag._run_rns_tool(['rnpath', '-t'], 'rnpath')
                    else:
                        print("RNS diagnostics handler not available.")
                    self.ctx.wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.ctx.dialog.msgbox(
                    "RNS Error",
                    f"Operation failed:\n{type(e).__name__}: {e}\n\n"
                    f"Check that rnsd is running:\n"
                    f"  sudo systemctl status rnsd"
                )

    # ------------------------------------------------------------------
    # Own methods (from rns_menu_mixin.py)
    # ------------------------------------------------------------------

    def _rns_probe_destination(self):
        """Probe an RNS destination to test reachability."""
        clear_screen()
        print("=== Probe RNS Destination ===\n")
        print("Probe tests reachability of a destination on the RNS network.")
        print("Enter the full destination hash (32 hex chars), or a partial hash.\n")

        try:
            dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not dest_hash or dest_hash.lower() == 'q':
            return

        # Validate hex format to prevent flag injection
        if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
            print("Error: Hash must contain only hex characters (0-9, a-f).")
            self.ctx.wait_for_enter()
            return

        print(f"\nProbing {dest_hash}...\n")
        diag = self.ctx.registry.get_handler("rns_diagnostics") if self.ctx.registry else None
        if diag:
            diag._run_rns_tool(['rnprobe', dest_hash], 'rnprobe')
        else:
            print("RNS diagnostics handler not available.")
        self.ctx.wait_for_enter()

    def _rns_identity_info(self):
        """Show RNS identity information."""
        clear_screen()
        print("=== RNS Identity Info ===\n")

        while True:
            # Check identity status for menu hints
            config_dir = ReticulumPaths.get_config_dir()
            rnsd_exists = (config_dir / 'identity').exists()
            gw_exists = get_identity_path().exists()

            choices = [
                ("show", "Show local identity"),
                ("create", "Create identities" + (
                    "" if not rnsd_exists or not gw_exists else " (all exist)")),
                ("path", "Show identity file paths"),
                ("recall", "Recall identity by hash"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "RNS Identity",
                "Identity management:",
                choices
            )

            if choice is None or choice == "back":
                break

            diag = self.ctx.registry.get_handler("rns_diagnostics") if self.ctx.registry else None

            try:
                if choice == "create":
                    self._create_rns_identities()

                elif choice == "show":
                    clear_screen()
                    print("=== Local RNS Identity ===\n")

                    rnsd_identity = config_dir / 'identity'
                    if rnsd_identity.exists():
                        print(f"rnsd identity: {rnsd_identity}")
                        if diag:
                            diag._run_rns_tool(
                                ['rnid', '-i', str(rnsd_identity), '-p'],
                                'rnid'
                            )
                    else:
                        print(f"rnsd identity: {rnsd_identity}")
                        print("  Not found — use 'Create identities' to generate.\n")

                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway identity: {gw_id}")
                    if gw_id.exists():
                        if diag:
                            diag._run_rns_tool(
                                ['rnid', '-i', str(gw_id), '-p'],
                                'rnid'
                            )
                    else:
                        print("  Not created — use 'Create identities' to generate.")
                    self.ctx.wait_for_enter()

                elif choice == "path":
                    clear_screen()
                    print("=== RNS Identity Paths ===\n")
                    identity_path = config_dir / 'identity'
                    print(f"RNS config dir:    {config_dir}")
                    print(f"RNS identity file: {identity_path}")
                    if identity_path.exists():
                        stat = identity_path.stat()
                        print(f"  Size: {stat.st_size} bytes")
                        from datetime import datetime
                        mtime = datetime.fromtimestamp(stat.st_mtime)
                        print(f"  Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        print("  Not found (created on first rnsd start)")

                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway:  {gw_id}")
                    if gw_id.exists():
                        stat = gw_id.stat()
                        print(f"  Size: {stat.st_size} bytes")
                    else:
                        print("  Not created yet")
                    self.ctx.wait_for_enter()

                elif choice == "recall":
                    clear_screen()
                    print("=== Recall RNS Identity ===\n")
                    print("Look up a known identity by its destination hash.\n")
                    try:
                        dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        continue
                    if dest_hash and dest_hash.lower() != 'q':
                        if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
                            print("Error: Hash must contain only hex characters (0-9, a-f).")
                        elif diag:
                            diag._run_rns_tool(['rnid', '--recall', dest_hash], 'rnid')
                        else:
                            print("RNS diagnostics handler not available.")
                    self.ctx.wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.ctx.dialog.msgbox(
                    "Identity Error",
                    f"Operation failed:\n{type(e).__name__}: {e}"
                )

    def _create_rns_identities(self):
        """Create RNS and gateway identities from the TUI."""
        clear_screen()
        print("=== Create RNS Identities ===\n")

        try:
            config_dir = ReticulumPaths.get_config_dir()

            # Show current state
            rns_id = config_dir / 'identity'
            gw_id = get_identity_path()
            print(f"RNS identity:     {rns_id}")
            print(f"  Status: {'EXISTS' if rns_id.exists() else 'MISSING'}")
            print(f"Gateway identity: {gw_id}")
            print(f"  Status: {'EXISTS' if gw_id.exists() else 'MISSING'}\n")

            if rns_id.exists() and gw_id.exists():
                print("Both identities already exist. Nothing to create.")
                self.ctx.wait_for_enter()
                return

            result = create_identities()
            if result.success:
                print(f"OK: {result.message}")
                created = result.data.get('created', [])
                if 'rns' in created:
                    print(f"  Created: {result.data['rns_identity']}")
                if 'gateway' in created:
                    print(f"  Created: {result.data['gateway_identity']}")
                if not created:
                    print("  All identities already existed.")
            else:
                print(f"ERROR: {result.message}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        self.ctx.wait_for_enter()

    def _rns_known_destinations(self):
        """Show known RNS destinations from the running rnsd instance."""
        clear_screen()
        print("=== Known RNS Destinations ===\n")

        result = list_known_destinations()

        if result.success:
            nodes = result.data.get('nodes', [])
            count = result.data.get('count', 0)

            if count == 0:
                print("No known destinations yet.")
                print("\nNodes appear when they announce or when you request paths.")
                print("Make sure rnsd is running: sudo systemctl start rnsd")
            else:
                print(f"Found {count} destination(s):\n")
                print(f"{'Hash':>10}  {'Hops':>5}  {'Source':<20}  {'Name'}")
                print("-" * 60)
                for node in nodes:
                    short = node.get('short_hash', '?')
                    hops = node.get('hops', -1)
                    hops_str = str(hops) if hops >= 0 else '?'
                    source = node.get('source', 'unknown')
                    name = node.get('name', '')
                    print(f"{short:>10}  {hops_str:>5}  {source:<20}  {name}")
        else:
            print(f"Error: {result.message}")
            fix_hint = (result.data or {}).get('fix_hint', '')
            if fix_hint:
                print(f"Fix: {fix_hint}")

        self.ctx.wait_for_enter()

    def _rns_set_node_positions(self):
        """Set GPS positions for RNS nodes so they appear on the map."""
        while True:
            clear_screen()
            print("=== Set RNS Node Positions ===\n")
            print("NomadNet nodes don't broadcast GPS. Set positions manually")
            print("so your RNS nodes appear on the live network map.\n")

            # Load node tracker and cache
            try:
                from gateway.node_tracker import UnifiedNodeTracker
                tracker = UnifiedNodeTracker()
                rns_nodes = tracker.get_rns_nodes()
            except Exception as e:
                print(f"Error loading node tracker: {e}")
                self.ctx.wait_for_enter()
                return

            if not rns_nodes:
                print("No RNS nodes discovered yet.")
                print("\nMake sure rnsd is running and you've exchanged announces")
                print("with other nodes (via NomadNet or Sideband).")
                self.ctx.wait_for_enter()
                return

            # Build menu of nodes
            choices = []
            print(f"{'#':<3} {'Name':<20} {'Hash':<12} {'Position'}")
            print("-" * 60)
            for i, node in enumerate(rns_nodes):
                if node.position.is_valid():
                    pos_str = f"({node.position.latitude:.4f}, {node.position.longitude:.4f})"
                else:
                    pos_str = "NOT SET"
                name = node.name[:18] if node.name else node.id[:18]
                hash_short = node.id.replace('rns_', '')[:10]
                print(f"{i+1:<3} {name:<20} {hash_short:<12} {pos_str}")
                choices.append((str(i), f"{name} - {pos_str}"))

            choices.append(("back", "Back to RNS Menu"))
            print()

            choice = self.ctx.dialog.menu(
                "Select Node",
                "Choose a node to set its position:",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                idx = int(choice)
                if 0 <= idx < len(rns_nodes):
                    self._set_single_node_position(rns_nodes[idx])
            except ValueError:
                pass

    def _set_single_node_position(self, node):
        """Set position for a single RNS node."""
        clear_screen()
        print(f"=== Set Position for {node.name} ===\n")
        print(f"Node ID: {node.id}")
        if node.position.is_valid():
            print(f"Current: ({node.position.latitude:.6f}, {node.position.longitude:.6f})")
        else:
            print("Current: NOT SET")
        print()
        print("Enter coordinates in decimal degrees (e.g., 21.3069 for latitude)")
        print("Tip: Get coords from Google Maps by right-clicking a location\n")

        try:
            lat_str = input("Latitude (e.g., 21.3069): ").strip()
            if not lat_str:
                print("Cancelled.")
                self.ctx.wait_for_enter()
                return

            lon_str = input("Longitude (e.g., -157.8583): ").strip()
            if not lon_str:
                print("Cancelled.")
                self.ctx.wait_for_enter()
                return

            lat = float(lat_str)
            lon = float(lon_str)

            # Validate
            if not (-90 <= lat <= 90):
                print(f"Invalid latitude: {lat} (must be -90 to 90)")
                self.ctx.wait_for_enter()
                return
            if not (-180 <= lon <= 180):
                print(f"Invalid longitude: {lon} (must be -180 to 180)")
                self.ctx.wait_for_enter()
                return

            # Optional: name
            name_input = input(f"Name [{node.name}]: ").strip()
            new_name = name_input if name_input else node.name

            # Save to cache
            self._save_rns_node_position(node.id, new_name, lat, lon)
            print(f"\nSaved: {new_name} at ({lat:.6f}, {lon:.6f})")
            print("Refresh the map to see the updated position.")

        except ValueError as e:
            print(f"Invalid input: {e}")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")

        self.ctx.wait_for_enter()

    def _save_rns_node_position(self, node_id: str, name: str, lat: float, lon: float):
        """Save an RNS node position to the node cache."""
        cache_path = get_real_user_home() / '.config' / 'meshforge' / 'node_cache.json'
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing cache
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {'version': 1, 'nodes': []}
        else:
            data = {'version': 1, 'nodes': []}

        if 'nodes' not in data:
            data['nodes'] = []

        # Find and update or add node
        found = False
        for node in data['nodes']:
            if node.get('id') == node_id:
                node['name'] = name
                node['position'] = {'latitude': lat, 'longitude': lon, 'altitude': 0}
                node['network'] = 'rns'
                found = True
                break

        if not found:
            data['nodes'].append({
                'id': node_id,
                'name': name,
                'network': 'rns',
                'position': {'latitude': lat, 'longitude': lon, 'altitude': 0},
                'is_online': True,
            })

        # Save
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)
