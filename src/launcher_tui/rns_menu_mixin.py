"""
RNS Menu Mixin - Reticulum Network Stack menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
Sniffer methods further extracted to rns_sniffer_mixin.py.
Config methods extracted to rns_config_mixin.py.
Diagnostics methods extracted to rns_diagnostics_mixin.py.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from rns_sniffer_mixin import RNSSnifferMixin
from rns_config_mixin import RNSConfigMixin
from rns_diagnostics_mixin import RNSDiagnosticsMixin
from rns_monitor_mixin import RNSMonitorMixin

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen

from utils.service_check import (
    check_process_running, check_udp_port, check_rns_shared_instance,
    get_rns_shared_instance_info, get_udp_port_owner,
    start_service, stop_service, _sudo_cmd,
    daemon_reload, _sudo_write, enable_service,
)
from commands.rns import (
    get_identity_path, create_identities, list_known_destinations,
    check_connectivity, get_status,
)
from utils.config_drift import detect_rnsd_config_drift


class RNSMenuMixin(RNSSnifferMixin, RNSConfigMixin, RNSDiagnosticsMixin, RNSMonitorMixin):
    """Mixin providing RNS/Reticulum menu functionality.

    Inherits sniffer methods from RNSSnifferMixin.
    Inherits config methods from RNSConfigMixin.
    Inherits diagnostics methods from RNSDiagnosticsMixin.
    Inherits monitor methods from RNSMonitorMixin.
    """

    def _rns_menu(self):
        """Reticulum Network Stack tools."""
        while True:
            choices = [
                ("status", "RNS Status (rnstatus)"),
                ("monitor", "Live RNS Monitor (auto-refresh)"),
                ("paths", "RNS Path Table (rnpath)"),
                ("sniffer", "RNS Traffic Sniffer (Wireshark-grade)"),
                ("topology", "Network Topology (graph view)"),
                ("quality", "Link Quality Analysis"),
                ("probe", "Probe Destination (rnprobe)"),
                ("identity", "Identity Info (rnid)"),
                ("nodes", "Known Destinations"),
                ("positions", "Set Node Positions (for map)"),
                ("diag", "RNS Diagnostics"),
                ("repair", "Repair RNS (fix shared instance)"),
                ("drift", "Config Drift Check"),
                ("bridge", "Gateway Bridge (start/stop)"),
                ("nomadnet", "NomadNet Client"),
                ("meshchat", "MeshChat Client"),
                ("ifaces", "Manage Interfaces"),
                ("config", "View Reticulum Config"),
                ("edit", "Edit Reticulum Config"),
                ("check", "Check RNS Setup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS / Reticulum",
                "Reticulum Network Stack tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "monitor": ("Live RNS Monitor", self._rns_status_monitor),
                "sniffer": ("RNS Traffic Sniffer", self._rns_traffic_sniffer),
                "topology": ("Network Topology", self._topology_menu),
                "quality": ("Link Quality Analysis", self._link_quality_menu),
                "probe": ("Probe Destination", self._rns_probe_destination),
                "identity": ("Identity Info", self._rns_identity_info),
                "nodes": ("Known Destinations", self._rns_known_destinations),
                "positions": ("Set Node Positions", self._rns_set_node_positions),
                "diag": ("RNS Diagnostics", self._rns_diagnostics),
                "repair": ("Repair RNS", self._rns_repair_menu),
                "drift": ("Config Drift Check", self._rns_config_drift_check),
                "bridge": ("Gateway Bridge", self._run_bridge),
                "nomadnet": ("NomadNet Client", self._nomadnet_menu),
                "meshchat": ("MeshChat Client", self._meshchat_menu),
                "ifaces": ("Manage Interfaces", self._rns_interfaces_menu),
                "config": ("View RNS Config", self._view_rns_config),
                "edit": ("Edit RNS Config", self._edit_rns_config),
                "check": ("Check RNS Setup", self._check_rns_setup),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)
                continue

            # Inline RNS tool commands
            try:
                if choice == "status":
                    clear_screen()
                    print("=== RNS Status ===\n")
                    self._run_rns_tool(['rnstatus'], 'rnstatus')
                    self._wait_for_enter()
                elif choice == "paths":
                    clear_screen()
                    print("=== RNS Path Table ===\n")
                    self._run_rns_tool(['rnpath', '-t'], 'rnpath')
                    self._wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.dialog.msgbox(
                    "RNS Error",
                    f"Operation failed:\n{type(e).__name__}: {e}\n\n"
                    f"Check that rnsd is running:\n"
                    f"  sudo systemctl status rnsd"
                )

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
            self._wait_for_enter()
            return

        print(f"\nProbing {dest_hash}...\n")
        self._run_rns_tool(['rnprobe', dest_hash], 'rnprobe')
        self._wait_for_enter()

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

            choice = self.dialog.menu(
                "RNS Identity",
                "Identity management:",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                if choice == "create":
                    self._create_rns_identities()

                elif choice == "show":
                    clear_screen()
                    print("=== Local RNS Identity ===\n")

                    # Find the rnsd identity file
                    # RNS stores it at <configdir>/identity
                    rnsd_identity = config_dir / 'identity'
                    if rnsd_identity.exists():
                        print(f"rnsd identity: {rnsd_identity}")
                        self._run_rns_tool(
                            ['rnid', '-i', str(rnsd_identity), '-p'],
                            'rnid'
                        )
                    else:
                        print(f"rnsd identity: {rnsd_identity}")
                        print("  Not found — use 'Create identities' to generate.\n")

                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway identity: {gw_id}")
                    if gw_id.exists():
                        self._run_rns_tool(
                            ['rnid', '-i', str(gw_id), '-p'],
                            'rnid'
                        )
                    else:
                        print("  Not created — use 'Create identities' to generate.")
                    self._wait_for_enter()

                elif choice == "path":
                    clear_screen()
                    print("=== RNS Identity Paths ===\n")
                    config_dir = ReticulumPaths.get_config_dir()
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
                    self._wait_for_enter()

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
                        else:
                            self._run_rns_tool(['rnid', '--recall', dest_hash], 'rnid')
                    self._wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.dialog.msgbox(
                    "Identity Error",
                    f"Operation failed:\n{type(e).__name__}: {e}"
                )

    def _create_rns_identities(self):
        """Create RNS and gateway identities from the TUI.

        Calls commands.rns.create_identities() which generates keypairs
        for both the rnsd identity and the MeshForge gateway identity.
        No manual commands needed.
        """
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
                self._wait_for_enter()
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
        self._wait_for_enter()

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

        self._wait_for_enter()

    def _rns_set_node_positions(self):
        """Set GPS positions for RNS nodes so they appear on the map.

        NomadNet nodes don't broadcast location, so positions must be set manually.
        Sideband nodes with GPS sharing will be auto-populated.
        """
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
                self._wait_for_enter()
                return

            if not rns_nodes:
                print("No RNS nodes discovered yet.")
                print("\nMake sure rnsd is running and you've exchanged announces")
                print("with other nodes (via NomadNet or Sideband).")
                self._wait_for_enter()
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

            choice = self.dialog.menu(
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
                self._wait_for_enter()
                return

            lon_str = input("Longitude (e.g., -157.8583): ").strip()
            if not lon_str:
                print("Cancelled.")
                self._wait_for_enter()
                return

            lat = float(lat_str)
            lon = float(lon_str)

            # Validate
            if not (-90 <= lat <= 90):
                print(f"Invalid latitude: {lat} (must be -90 to 90)")
                self._wait_for_enter()
                return
            if not (-180 <= lon <= 180):
                print(f"Invalid longitude: {lon} (must be -180 to 180)")
                self._wait_for_enter()
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

        self._wait_for_enter()

    def _save_rns_node_position(self, node_id: str, name: str, lat: float, lon: float):
        """Save an RNS node position to the node cache."""
        import json

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

    def _rns_repair_menu(self):
        """RNS Repair Wizard — explicit user-initiated repair.

        Shows what the repair will do and requires user consent before
        making any changes. This replaces the old auto-fix behavior
        that ran from error handlers and caused config regressions.
        """
        if not self.dialog.yesno(
            "RNS Repair Wizard",
            "This will attempt to fix RNS shared instance issues.\n\n"
            "What it does:\n"
            "  1. Ensures /etc/reticulum/ dirs exist & deploys config if missing\n"
            "  2. Validates rnsd.service file (fixes ExecStart & directives)\n"
            "  3. Checks rnsd Python dependencies (meshtastic, etc.)\n"
            "  4. Clears stale auth tokens & restarts rnsd\n"
            "  5. Verifies port 37428 is listening\n\n"
            "Your existing RNS config will NOT be overwritten.\n\n"
            "Run diagnostics first? Use RNS > Diagnostics.\n\n"
            "Proceed with repair?",
        ):
            return

        clear_screen()
        self._repair_rns_shared_instance()
        self._wait_for_enter()

    def _repair_rns_shared_instance(self) -> bool:
        """Repair RNS shared instance — explicit user action only.

        This is a repair wizard method, NOT an error handler auto-fix.
        Must only be called from explicit user actions (RNS Diagnostics,
        Repair menu, etc.) — never from error handlers in _run_rns_tool().

        Steps:
        1. Ensures /etc/reticulum/ directories exist with correct permissions,
           deploys template ONLY if no config exists anywhere (never overwrites)
        2. Validates rnsd.service file (fixes ExecStart path & misplaced directives)
        3. Checks rnsd Python dependencies for enabled interface plugins
        4. Clears stale auth tokens, checks blocking interfaces, restarts rnsd
        5. Verifies shared instance is now available (UDP port 37428)

        Returns True if fix was successful.
        """
        import time

        print("\n" + "=" * 50)
        print("RNS REPAIR: Shared Instance")
        print("=" * 50)

        # Step 1: Fix directories and deploy config ONLY if none exists
        target_dir = Path('/etc/reticulum')
        target = target_dir / 'config'

        print(f"\n[1/5] Checking RNS config and directories...")

        try:
            # Create ALL /etc/reticulum/ subdirectories and fix file permissions.
            # ReticulumPaths.ensure_system_dirs() is the SINGLE SOURCE OF TRUTH:
            # creates storage/, ratchets/, resources/, cache/announces/, interfaces/
            # and fixes file permissions inside storage/ (0o666 files, 0o777 dirs).
            if ReticulumPaths.ensure_system_dirs():
                print(f"  Ensured: {ReticulumPaths.ETC_STORAGE}")
                print(f"  Ensured: {ReticulumPaths.ETC_INTERFACES}")
            else:
                print("  ERROR: Could not create /etc/reticulum/ directories")
                print("  (Run MeshForge with sudo)")
                return False

            # Only deploy template if NO config exists at ANY standard location.
            # Never overwrite an existing config — that destroys user interfaces.
            existing_config = ReticulumPaths.get_config_file()
            if existing_config.exists():
                print(f"  Existing config preserved: {existing_config}")
            else:
                template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
                if template.exists():
                    shutil.copy2(str(template), str(target))
                    target.chmod(0o644)
                    print(f"  No config found — deployed template to: {target}")
                else:
                    print("  WARNING: No config found and template missing")
                    print("  Run: rnsd --exampleconfig > /etc/reticulum/config")
        except (OSError, PermissionError) as e:
            print(f"  ERROR: {e}")
            print("  (Run MeshForge with sudo)")
            return False

        # Step 2: Validate rnsd.service file
        print(f"\n[2/5] Validating rnsd systemd service file...")
        service_path = Path('/etc/systemd/system/rnsd.service')
        if service_path.exists():
            service_fixed = self._validate_rnsd_service_file()
            if not service_fixed:
                print("  Service file: OK")
        else:
            print("  Service file: not found (rnsd may not be installed as service)")

        # Step 3: Check rnsd Python dependencies
        print(f"\n[3/5] Checking rnsd Python dependencies...")
        self._ensure_rnsd_dependencies()

        # Step 4: Stop rnsd, clear stale auth tokens, start rnsd
        print(f"\n[4/5] Restarting rnsd service...")

        # Stop rnsd first (must stop before clearing auth files)
        print("  Stopping rnsd...")
        success, msg = stop_service('rnsd')
        if not success:
            print(f"  Warning stopping rnsd: {msg}")
        time.sleep(1)  # Give it time to fully stop

        # Clear stale shared_instance_* files that cause AuthenticationError.
        # These files contain auth tokens that become invalid after config changes.
        # CRITICAL: Must clear from ALL locations — not just /etc and /root.
        # If the real user has ~/.reticulum/storage/ with stale tokens, NomadNet
        # (running as real user) will use those stale tokens → auth mismatch.
        print("  Clearing stale shared instance authentication files...")
        user_home = get_real_user_home()
        storage_dirs = [
            Path('/etc/reticulum/storage'),
            Path('/root/.reticulum/storage'),
            user_home / '.reticulum' / 'storage',
            user_home / '.config' / 'reticulum' / 'storage',
        ]
        files_cleared = 0
        for storage_dir in storage_dirs:
            if storage_dir.exists():
                for auth_file in storage_dir.glob('shared_instance_*'):
                    try:
                        auth_file.unlink()
                        files_cleared += 1
                        print(f"    Removed: {auth_file}")
                    except (OSError, PermissionError) as e:
                        print(f"    Warning: Could not remove {auth_file}: {e}")
        if files_cleared == 0:
            print("    No stale auth files found")

        # Pre-flight 4a: Validate share_instance = Yes BEFORE restart.
        # Without this, rnsd won't expose the shared instance at all.
        # Previously this was only checked POST-failure after 30s timeout.
        try:
            from commands.rns import _parse_share_instance
            config_path = ReticulumPaths.get_config_file()
            if config_path.exists():
                config_content = config_path.read_text()
                share_ok = _parse_share_instance(config_content)
                if share_ok:
                    print("  share_instance: Yes (OK)")
                else:
                    print("  share_instance: DISABLED")
                    print("  Without share_instance = Yes, rnsd won't accept")
                    print("  connections from gateway, rnstatus, or other tools.")
                    if self.dialog.yesno(
                        "Fix share_instance",
                        "share_instance is disabled in the RNS config.\n\n"
                        "Without it, rnsd won't expose the shared instance\n"
                        "and no client apps (gateway, rnstatus) can connect.\n\n"
                        f"Config: {config_path}\n\n"
                        "Set share_instance = Yes?"
                    ):
                        import re as _re
                        if _re.search(r'^\s*share_instance\s*=',
                                      config_content, _re.MULTILINE):
                            fixed = _re.sub(
                                r'^(\s*share_instance\s*=\s*).*$',
                                r'\1Yes',
                                config_content,
                                count=1,
                                flags=_re.MULTILINE
                            )
                        elif '[reticulum]' in config_content.lower():
                            fixed = config_content.replace(
                                '[reticulum]',
                                '[reticulum]\n  share_instance = Yes',
                                1
                            )
                        else:
                            fixed = ('[reticulum]\n  share_instance = Yes\n\n'
                                     + config_content)
                        ok, msg = _sudo_write(str(config_path), fixed)
                        if ok:
                            verify = config_path.read_text()
                            if _parse_share_instance(verify):
                                print("  Fixed: share_instance = Yes")
                            else:
                                print("  WARNING: Config write did not take effect")
                        else:
                            print(f"  Could not write config: {msg}")
            else:
                print(f"  Config not found at {config_path}")
        except Exception as e:
            logger.debug("Pre-flight share_instance check failed: %s", e)

        # Pre-flight 4b: Check config drift (gateway vs rnsd config paths).
        # If rnsd reads a different config file, the repair may fix the wrong one.
        try:
            drift = detect_rnsd_config_drift()
            if drift.drifted:
                print(f"\n  WARNING: Config drift detected!")
                print(f"    Gateway reads: {drift.gateway_config_dir}")
                print(f"    rnsd reads:    {drift.rnsd_config_dir}")
                print(f"    Fix: {drift.fix_hint}")
                print("    The checks above may have validated the wrong config.")
        except Exception as e:
            logger.debug("Pre-flight config drift check failed: %s", e)

        # Pre-flight 4c: Check NomadNet conflict.
        # NomadNet can hold the shared instance, preventing rnsd from binding.
        try:
            if self._check_nomadnet_conflict():
                print("\n  WARNING: NomadNet is running!")
                print("  NomadNet may hold the RNS shared instance,")
                print("  preventing rnsd from becoming the shared instance.")
                owner = get_udp_port_owner(37428)
                if owner:
                    print(f"  Port 37428 held by: {owner[0]} (PID {owner[1]})")
                if self.dialog.yesno(
                    "Stop NomadNet?",
                    "NomadNet is running and may hold the\n"
                    "RNS shared instance port.\n\n"
                    "Stop NomadNet before starting rnsd?\n"
                    "(NomadNet can be restarted afterward as a client)",
                ):
                    try:
                        subprocess.run(
                            ['pkill', '-f', 'nomadnet'],
                            capture_output=True, timeout=5
                        )
                        time.sleep(1)
                        print("  NomadNet stopped")
                    except (subprocess.SubprocessError, OSError) as e:
                        print(f"  Could not stop NomadNet: {e}")
                else:
                    print("  Proceeding with NomadNet running (rnsd may fail)...")
        except Exception as e:
            logger.debug("Pre-flight NomadNet check failed: %s", e)

        # Pre-flight: check for blocking interfaces BEFORE starting rnsd.
        # If enabled interfaces have missing dependencies (e.g., meshtasticd
        # not running), rnsd will hang during init and never bind port 37428.
        user_declined_disable = False
        blocking = self._find_blocking_interfaces()
        if blocking:
            print("\n  WARNING: Enabled interfaces have missing dependencies:")
            for iface_name, reason, fix in blocking:
                print(f"    [{iface_name}] {reason}")
                print(f"    Fix: {fix}")
            print()
            print("  rnsd will hang if these interfaces can't connect.")

            # Offer to temporarily disable blocking interfaces
            iface_names = [b[0] for b in blocking]
            names_str = ", ".join(iface_names)
            if self.dialog.yesno(
                "Disable Blocking Interfaces?",
                f"These interfaces will prevent rnsd from starting:\n"
                f"  {names_str}\n\n"
                f"Temporarily disable them in the RNS config?\n"
                f"(You can re-enable them later from the RNS menu)\n\n"
                f"If you choose No, rnsd may hang on startup.",
            ):
                disabled = self._disable_interfaces_in_config(iface_names)
                if disabled:
                    print(f"  Disabled {len(disabled)} blocking interface(s):")
                    for name in disabled:
                        print(f"    [{name}] set enabled = no")
                else:
                    print("  Could not disable interfaces — rnsd may hang")
            else:
                user_declined_disable = True
                print("  Proceeding without disabling (rnsd may hang)...\n")

        # Clear any systemd start limit (after 5 crashes, systemd refuses to start)
        try:
            subprocess.run(
                ['systemctl', 'reset-failed', 'rnsd'],
                capture_output=True, timeout=5
            )
        except (subprocess.SubprocessError, OSError):
            pass

        # Start rnsd with fresh state
        print("  Starting rnsd...")
        try:
            success, msg = start_service('rnsd')
            if success:
                print("  rnsd started successfully")
            else:
                print(f"  Warning: {msg}")
        except Exception as e:
            print(f"  Warning: {e}")

        # Step 5: Wait for shared instance and verify
        print(f"\n[5/5] Verifying shared instance...")
        print("  Waiting for rnsd shared instance...")

        # Poll for shared instance with early crash detection (up to 30 seconds)
        # rnsd can take 20-30s to initialize on slower hardware (Pi)
        # RNS uses abstract Unix domain sockets on Linux (\0rns/default),
        # NOT UDP port 37428. check_rns_shared_instance() checks both.
        instance_ok = False
        rnsd_crashed = False
        for i in range(30):
            # Check if shared instance is reachable (domain socket, TCP, or UDP)
            instance_ok = check_rns_shared_instance()
            if instance_ok:
                break

            # Early exit: check if rnsd has already crashed
            try:
                r = subprocess.run(
                    ['systemctl', 'is-active', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                state = r.stdout.strip()
                if state in ('failed', 'inactive'):
                    rnsd_crashed = True
                    break
            except (subprocess.SubprocessError, OSError):
                pass

            time.sleep(1)

        if instance_ok:
            info = get_rns_shared_instance_info()
            print(f"  SUCCESS: RNS shared instance is available")
            print(f"  Method: {info['detail']}")
            print("\n" + "=" * 50)
            print("RNS shared instance is now available!")
            print("=" * 50 + "\n")
            return True

        if rnsd_crashed:
            print("  FAILED: rnsd crashed on startup")
            print()
            # Capture the actual traceback by running rnsd directly
            # Use venv rnsd if available (matches service file)
            venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')
            rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
                shutil.which('rnsd') or '/usr/local/bin/rnsd'
            )
            print("  Running rnsd directly to capture error...")
            output = ""
            try:
                r = subprocess.run(
                    [rnsd_path],
                    capture_output=True, text=True, timeout=10
                )
                output = ((r.stdout or "") + (r.stderr or "")).strip()
                if output:
                    # Detect known error patterns and show actionable messages
                    lower_output = output.lower()
                    if 'address already in use' in lower_output:
                        print("  Cause: Port conflict (another process holds the port)")
                    elif 'permission' in lower_output and 'denied' in lower_output:
                        print("  Cause: Permission denied")
                    elif 'connection refused' in lower_output:
                        print("  Cause: meshtasticd not running (Connection refused)")
                        print("  Fix: sudo systemctl start meshtasticd")
                    else:
                        # Unknown error — show last 5 lines (not raw 20-line traceback)
                        for line in output.splitlines()[-5:]:
                            print(f"  {line}")
                    print(f"  Full log: sudo journalctl -u rnsd -n 20")
                else:
                    print("  (no output captured)")
            except subprocess.TimeoutExpired:
                print("  rnsd hung (no crash within 10s — likely a blocking interface)")
            except (OSError, FileNotFoundError) as e:
                print(f"  Could not run rnsd: {e}")

            # Detect missing meshtastic module and offer to install
            if 'meshtastic module' in output.lower() or 'meshtastic' in output.lower():
                print()
                print("  Cause: Meshtastic_Interface.py plugin needs the meshtastic module")
                venv_pip = Path('/opt/meshforge/venv/bin/pip')
                if venv_pip.exists() and self.dialog.yesno(
                    "Install meshtastic Module",
                    "The Meshtastic_Interface.py plugin requires the\n"
                    "meshtastic Python module, which is not installed.\n\n"
                    "Install it now?\n\n"
                    "  pip install meshtastic (into MeshForge venv)",
                ):
                    print("  Installing meshtastic module...")
                    pip_r = subprocess.run(
                        [str(venv_pip), 'install', 'meshtastic'],
                        capture_output=True, text=True, timeout=120
                    )
                    if pip_r.returncode != 0:
                        # Retry with --ignore-installed for Debian package conflicts
                        err_text = (pip_r.stderr or pip_r.stdout or '').lower()
                        if 'installed by' in err_text or 'externally-managed' in err_text:
                            print("  Debian package conflict, retrying with --ignore-installed...")
                            pip_r = subprocess.run(
                                [str(venv_pip), 'install', '--ignore-installed', 'meshtastic'],
                                capture_output=True, text=True, timeout=120
                            )
                    if pip_r.returncode == 0:
                        print("  meshtastic installed. Restarting rnsd...")
                        # Reset failed state and restart
                        subprocess.run(
                            ['systemctl', 'reset-failed', 'rnsd'],
                            capture_output=True, timeout=5
                        )
                        start_service('rnsd')
                        time.sleep(3)
                        if check_rns_shared_instance():
                            print("  SUCCESS: RNS shared instance is available")
                            print("\n" + "=" * 50)
                            print("RNS shared instance is now available!")
                            print("=" * 50 + "\n")
                            return True
                        print("  rnsd restarted — check with RNS > Diagnostics")
                    else:
                        print(f"  pip install failed: {pip_r.stderr.strip()[:200]}")

            return False

        # Shared instance not available after 30s but rnsd didn't crash.
        # Run comprehensive diagnostics to identify the root cause.
        print("  WARNING: Shared instance not available after 30s")
        print()
        print("  --- Diagnosing root cause ---")

        # Diagnostic 1: Show shared instance detection details
        info = get_rns_shared_instance_info()
        print(f"  Shared instance: {info['detail']}")

        # Diagnostic 2: Check who owns port 37428 (if TCP/UDP mode)
        try:
            owner = get_udp_port_owner(37428)
            if owner:
                print(f"  Port 37428 owner: {owner[0]} (PID {owner[1]})")
                if owner[0] in ('nomadnet', 'python', 'python3'):
                    print("  Likely cause: NomadNet is holding the port")
        except Exception:
            pass

        # Diagnostic 3: Re-check share_instance (config drift may mean
        # rnsd read a different config than pre-flight validated)
        try:
            from commands.rns import _parse_share_instance
            config_path = ReticulumPaths.get_config_file()
            if config_path.exists():
                config_content = config_path.read_text()
                if not _parse_share_instance(config_content):
                    print("  Cause: share_instance not enabled in config")
                    print("  (pre-flight fix may not have applied due to config drift)")
        except Exception:
            pass

        # Diagnostic 4: Config drift (more accurate now that rnsd is running,
        # can read /proc/<pid>/cmdline for actual config path)
        try:
            drift = detect_rnsd_config_drift()
            if drift.drifted:
                print(f"  Config drift: gateway reads {drift.gateway_config_dir}")
                print(f"                rnsd reads    {drift.rnsd_config_dir}")
                print(f"  Fix: {drift.fix_hint}")
        except Exception:
            pass

        # Diagnostic 5: NomadNet conflict (may have started after pre-flight)
        try:
            if self._check_nomadnet_conflict():
                print("  NomadNet is running (may hold the shared instance)")
        except Exception:
            pass

        # Diagnostic 6: Blocking interfaces — offer second chance
        try:
            post_blocking = self._find_blocking_interfaces()
            if post_blocking:
                print("\n  Blocking interfaces detected:")
                for iface_name, reason, fix in post_blocking:
                    print(f"    [{iface_name}] {reason}")
                if user_declined_disable:
                    print("\n  These are likely why rnsd is stuck.")
                    iface_names = [b[0] for b in post_blocking]
                    names_str = ", ".join(iface_names)
                    if self.dialog.yesno(
                        "Disable Blocking Interfaces?",
                        f"Blocking interfaces are preventing rnsd\n"
                        f"from initializing:\n"
                        f"  {names_str}\n\n"
                        f"Disable them and restart rnsd?"
                    ):
                        disabled = self._disable_interfaces_in_config(iface_names)
                        if disabled:
                            print(f"  Disabled {len(disabled)} interface(s)")
                            stop_service('rnsd')
                            time.sleep(1)
                            start_service('rnsd')
                            print("  Waiting for shared instance...")
                            for _ in range(15):
                                time.sleep(1)
                                if check_rns_shared_instance():
                                    si = get_rns_shared_instance_info()
                                    print(f"  SUCCESS: {si['detail']}")
                                    print("\n" + "=" * 50)
                                    print("RNS shared instance is now available!")
                                    print("=" * 50 + "\n")
                                    return True
                            print("  Still not available after disabling interfaces")
        except Exception:
            pass

        # Diagnostic 7: Unfiltered journal (no -p warning filter).
        # Info-level messages reveal where rnsd is stuck during init.
        print()
        try:
            r = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager',
                 '-q', '--no-hostname'],
                capture_output=True, text=True, timeout=10
            )
            if r.stdout and r.stdout.strip():
                print("  Recent rnsd log:")
                for line in r.stdout.strip().splitlines()[-10:]:
                    print(f"    {line.strip()[:100]}")
            else:
                print("  No journal entries for rnsd")
        except (subprocess.SubprocessError, OSError):
            print("  Check logs: sudo journalctl -u rnsd -n 20")

        print("\n  Run RNS > Diagnostics for a full health check.")
        return False

    def _validate_rnsd_service_file(self) -> bool:
        """Validate and fix the rnsd systemd service file.

        Detects and fixes:
        - StartLimitIntervalSec in [Service] instead of [Unit]
        - ExecStart pointing to system rnsd instead of venv rnsd
          (venv has all dependencies like meshtastic)
        - Missing After=meshtasticd.service when MeshtasticInterface is
          configured (rnsd crashes if meshtasticd isn't ready yet)

        Returns True if the service file was fixed (daemon-reload needed).
        """
        service_path = Path('/etc/systemd/system/rnsd.service')
        if not service_path.exists():
            return False

        try:
            content = service_path.read_text()
        except (OSError, PermissionError):
            return False

        # Check for StartLimitIntervalSec in [Service] section (should be in [Unit])
        misplaced_directives = False
        current_section = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                current_section = stripped
            elif current_section == '[Service]' and (
                'StartLimitIntervalSec' in stripped
                or 'StartLimitBurst' in stripped
            ):
                misplaced_directives = True
                break

        # Check ExecStart — two problems to detect:
        # 1. ExecStart points to a binary that doesn't exist on disk (critical)
        # 2. ExecStart uses system rnsd when venv rnsd is available (venv has deps)
        wrong_rnsd_path = False
        current_rnsd = None
        current_rnsd_binary = None
        exec_match = re.search(r'ExecStart\s*=\s*(.+)', content)
        if exec_match:
            current_rnsd = exec_match.group(1).strip()
            # Extract just the binary path, stripping args like --service
            current_rnsd_binary = current_rnsd.split()[0]

        venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')

        if current_rnsd_binary:
            # Critical: does the ExecStart binary actually exist?
            if not Path(current_rnsd_binary).exists():
                wrong_rnsd_path = True
            # Secondary: prefer venv rnsd if available (has all dependencies)
            elif venv_rnsd.exists() and current_rnsd_binary != str(venv_rnsd):
                wrong_rnsd_path = True

        # Check for missing meshtasticd ordering dependency.
        # If a MeshtasticInterface is configured, rnsd must start AFTER
        # meshtasticd — otherwise the initial TCP connect fails and rnsd
        # crashes with "Connection refused".
        missing_ordering = False
        if 'meshtasticd.service' not in content:
            try:
                rns_config = ReticulumPaths.get_config_file()
                if rns_config.exists():
                    rns_content = rns_config.read_text()
                    # Match actual interface section, not comments
                    if re.search(r'^\s*\[\[.*Meshtastic', rns_content, re.MULTILINE):
                        missing_ordering = True
            except Exception as e:
                print(f"  Warning: Could not check RNS config: {e}")

        if not misplaced_directives and not wrong_rnsd_path and not missing_ordering:
            return False

        # Report what we're fixing
        if misplaced_directives:
            print("  Found: StartLimitIntervalSec in [Service] (should be [Unit])")
        if wrong_rnsd_path and current_rnsd_binary:
            if not Path(current_rnsd_binary).exists():
                print(f"  Found: ExecStart binary missing: {current_rnsd_binary}")
            elif venv_rnsd.exists():
                print(f"  Found: ExecStart uses {current_rnsd_binary}")
                print(f"         Should use venv: {venv_rnsd}")
        if missing_ordering:
            print("  Found: Missing After=meshtasticd.service")
            print("         rnsd can crash if meshtasticd isn't ready")
        print("  Regenerating rnsd.service...")

        # Prefer venv rnsd — it has all dependencies
        rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
            shutil.which('rnsd') or '/usr/local/bin/rnsd'
        )

        # Final sanity: make sure the resolved binary actually exists
        if not Path(rnsd_path).exists():
            print(f"  ERROR: No rnsd binary found on this system.")
            print(f"  Checked: /opt/meshforge/venv/bin/rnsd, PATH, /usr/local/bin/rnsd")
            print(f"  Install RNS: pip install rns")
            return False
        service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network-online.target meshtasticd.service
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
# (e.g., NomadNet holding port 37428)
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={rnsd_path} --service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
        write_ok, write_msg = _sudo_write(str(service_path), service_content)
        if write_ok:
            print("  Fixed: rnsd.service regenerated")
            # daemon-reload so systemd picks up the change
            ok, msg = daemon_reload()
            if ok:
                print("  Reloaded: systemd daemon-reload complete")
            else:
                print(f"  Warning: daemon-reload failed: {msg}")
            # Re-enable so rnsd starts on boot (regenerating the
            # service file can drop the symlink)
            ok, msg = enable_service('rnsd')
            if ok:
                print("  Enabled: rnsd will start on boot")
            else:
                print(f"  Warning: could not enable rnsd: {msg}")
            return True
        else:
            print(f"  Warning: Could not write service file: {write_msg}")
            return False

    def _check_meshtastic_plugin(self) -> bool:
        """Check if Meshtastic_Interface.py plugin is installed.

        The plugin bridges RNS over Meshtastic LoRa and must be in
        the RNS interfaces directory (e.g., ~/.reticulum/interfaces/ or
        /etc/reticulum/interfaces/).

        Returns True if plugin is installed.
        """
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        return plugin_path.exists()

    def _install_meshtastic_interface_plugin(self):
        """Install or update Meshtastic_Interface.py from vendored template.

        MeshForge maintains its own copy in templates/interfaces/.
        Deploys to the RNS interfaces directory and installs dependencies.
        """
        interfaces_dir = ReticulumPaths.get_interfaces_dir()
        plugin_path = interfaces_dir / 'Meshtastic_Interface.py'

        # Locate vendored source
        vendored = (Path(__file__).parent.parent.parent
                    / 'templates' / 'interfaces' / 'Meshtastic_Interface.py')
        if not vendored.exists():
            self.dialog.msgbox(
                "Template Missing",
                "Vendored Meshtastic_Interface.py not found.\n\n"
                f"Expected at:\n  {vendored}\n\n"
                "Reinstall MeshForge to restore templates."
            )
            return

        # Check if already installed and up to date
        action = "Install"
        if plugin_path.exists():
            import filecmp
            if filecmp.cmp(str(vendored), str(plugin_path), shallow=False):
                self.dialog.msgbox(
                    "Up to Date",
                    f"Meshtastic_Interface.py is already current at:\n"
                    f"  {plugin_path}"
                )
                return
            action = "Update"

        if not self.dialog.yesno(
            f"{action} Meshtastic Interface Plugin",
            f"The Meshtastic_Interface.py plugin bridges RNS over\n"
            f"Meshtastic LoRa mesh networks.\n\n"
            f"{action} to:\n  {plugin_path}\n\n"
            f"Source:\n  {vendored}\n\n"
            f"{action} now?"
        ):
            return

        try:
            interfaces_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(vendored), str(plugin_path))
            plugin_path.chmod(0o644)
            print(f"  {action}d: {plugin_path}")

            # Install meshtastic Python module — required by the plugin.
            meshtastic_installed = False
            venv_pip = Path('/opt/meshforge/venv/bin/pip')
            if venv_pip.exists():
                print("  Installing meshtastic Python module...")
                pip_result = subprocess.run(
                    [str(venv_pip), 'install', '-q', 'meshtastic'],
                    capture_output=True, text=True, timeout=120
                )
                if pip_result.returncode != 0:
                    err_text = (pip_result.stderr or pip_result.stdout or '').lower()
                    if 'installed by' in err_text or 'externally-managed' in err_text:
                        print("  Debian package conflict, retrying...")
                        pip_result = subprocess.run(
                            [str(venv_pip), 'install', '-q',
                             '--ignore-installed', 'meshtastic'],
                            capture_output=True, text=True, timeout=120
                        )
                meshtastic_installed = pip_result.returncode == 0

            restart_hint = ("Restart rnsd to load the new interface:\n"
                            "  sudo systemctl restart rnsd")
            if not meshtastic_installed:
                restart_hint = (
                    "NOTE: The meshtastic Python module is also required.\n"
                    "Install it: /opt/meshforge/venv/bin/pip install meshtastic"
                    "\n\nThen restart rnsd:\n  sudo systemctl restart rnsd"
                )

            self.dialog.msgbox(
                f"Plugin {action}d",
                f"Meshtastic_Interface.py {action.lower()}d at:\n"
                f"  {plugin_path}\n\n"
                f"{restart_hint}"
            )

        except (OSError, PermissionError) as e:
            self.dialog.msgbox(
                f"{action} Failed",
                f"Failed to {action.lower()} plugin:\n{e}\n\n"
                f"Try running with sudo, or manually copy:\n"
                f"  sudo cp {vendored} {interfaces_dir}/"
            )

    def _find_blocking_interfaces(self) -> list:
        """Check if enabled RNS interfaces have missing dependencies.

        Parses /etc/reticulum/config for enabled interfaces and checks
        whether their required services/hosts are available. Returns a
        list of (interface_name, problem, fix) tuples for blocking interfaces.

        This is the root cause of "rnsd active but not listening on 37428":
        rnsd initializes interfaces BEFORE binding the shared instance port.
        A blocking interface (e.g., TCP connect to dead host, missing serial
        device) prevents the shared instance from ever becoming available.
        """
        blocking = []
        config_file = ReticulumPaths.get_config_file()
        if not config_file.exists():
            return blocking

        try:
            content = config_file.read_text()
        except (OSError, PermissionError):
            return blocking

        # Parse enabled interfaces from the config
        # RNS config uses [[InterfaceName]] sections with type= and enabled=
        # Match interface sections: [[Name]] ... type = ... enabled = yes
        iface_pattern = re.compile(
            r'^\s*\[\[(.+?)\]\]\s*$'
            r'(.*?)'
            r'(?=^\s*\[\[|\Z)',
            re.MULTILINE | re.DOTALL
        )

        for match in iface_pattern.finditer(content):
            name = match.group(1).strip()
            body = match.group(2)

            # Check if enabled (RNS uses both 'enabled' and 'interface_enabled')
            enabled_match = re.search(
                r'^\s*(?:interface_)?enabled\s*=\s*(yes|true|1)',
                body, re.IGNORECASE | re.MULTILINE
            )
            if not enabled_match:
                continue

            # Check interface type
            type_match = re.search(r'^\s*type\s*=\s*(\S+)', body,
                                   re.IGNORECASE | re.MULTILINE)
            if not type_match:
                continue

            iface_type = type_match.group(1)

            # Check Meshtastic_Interface — tcp_port, serial port, or BLE
            if iface_type == 'Meshtastic_Interface':
                tcp_match = re.search(r'^\s*tcp_port\s*=\s*(\S+)', body,
                                      re.IGNORECASE | re.MULTILINE)
                port_match = re.search(r'^\s*port\s*=\s*(\S+)', body,
                                       re.IGNORECASE | re.MULTILINE)
                ble_match = re.search(r'^\s*ble_port\s*=\s*(\S+)', body,
                                      re.IGNORECASE | re.MULTILINE)

                if tcp_match:
                    # TCP mode → needs meshtasticd running AND its TCP port
                    # accepting connections. systemd "active" only means the
                    # process started, NOT that port 4403 is ready.
                    host_port = tcp_match.group(1)
                    try:
                        r = subprocess.run(
                            ['systemctl', 'is-active', 'meshtasticd'],
                            capture_output=True, text=True, timeout=5
                        )
                        if r.stdout.strip() != 'active':
                            blocking.append((
                                name,
                                f"needs meshtasticd ({host_port}) but it is not running",
                                "sudo systemctl start meshtasticd"
                            ))
                        else:
                            # meshtasticd is "active" — verify TCP port is ready
                            import socket
                            tcp_host = host_port
                            tcp_port_num = 4403
                            if ':' in host_port:
                                parts = host_port.rsplit(':', 1)
                                tcp_host = parts[0]
                                try:
                                    tcp_port_num = int(parts[1])
                                except ValueError:
                                    pass
                            try:
                                sock = socket.socket(
                                    socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(2)
                                sock.connect((tcp_host, tcp_port_num))
                                sock.close()
                            except (socket.timeout, ConnectionRefusedError,
                                    OSError):
                                blocking.append((
                                    name,
                                    f"meshtasticd running but TCP port "
                                    f"{tcp_host}:{tcp_port_num} not accepting "
                                    f"connections (still starting?)",
                                    f"Wait for meshtasticd to finish starting, "
                                    f"or: sudo systemctl restart meshtasticd"
                                ))
                    except (subprocess.SubprocessError, OSError):
                        pass
                elif port_match:
                    # Serial mode → device must exist
                    dev = port_match.group(1)
                    if dev.startswith('/dev/') and not Path(dev).exists():
                        blocking.append((
                            name,
                            f"serial device {dev} not found (disconnected?)",
                            f"Connect the device or disable this interface"
                        ))
                elif ble_match:
                    # BLE mode — can't easily verify, note it as possible blocker
                    ble_target = ble_match.group(1)
                    blocking.append((
                        name,
                        f"BLE connection to {ble_target} may block if device is off",
                        "Ensure BLE device is powered on, or disable this interface"
                    ))

            # Check TCPClientInterface → needs reachable host
            elif iface_type == 'TCPClientInterface':
                host_match = re.search(r'^\s*target_host\s*=\s*(\S+)', body,
                                       re.IGNORECASE | re.MULTILINE)
                port_match = re.search(r'^\s*target_port\s*=\s*(\d+)', body,
                                       re.IGNORECASE | re.MULTILINE)
                if host_match and port_match:
                    host = host_match.group(1)
                    port = port_match.group(1)
                    import socket
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(1)
                        sock.connect((host, int(port)))
                        sock.close()
                    except (socket.timeout, ConnectionRefusedError, OSError):
                        blocking.append((
                            name,
                            f"target {host}:{port} is unreachable",
                            f"Check if {host}:{port} is online, or disable this interface"
                        ))

            # Check RNodeInterface / SerialInterface → serial device must exist
            elif iface_type in ('RNodeInterface', 'SerialInterface', 'KISSInterface'):
                port_match = re.search(r'^\s*port\s*=\s*(\S+)', body,
                                       re.IGNORECASE | re.MULTILINE)
                if port_match:
                    dev = port_match.group(1)
                    if dev.startswith('/dev/') and not Path(dev).exists():
                        blocking.append((
                            name,
                            f"serial device {dev} not found (disconnected?)",
                            f"Connect the device or disable this interface"
                        ))

        return blocking

    def _disable_interfaces_in_config(self, interface_names: list) -> list:
        """Disable specific interfaces in the RNS config file.

        Changes 'enabled = yes' to 'enabled = no' for the named interfaces.
        Only modifies /etc/reticulum/config (the system config used by rnsd).

        Args:
            interface_names: List of interface names (matching [[Name]] sections)

        Returns:
            List of interface names that were successfully disabled.
        """
        config_file = ReticulumPaths.get_config_file()
        if not config_file.exists():
            return []

        try:
            content = config_file.read_text()
        except (OSError, PermissionError) as e:
            logger.error("Cannot read RNS config: %s", e)
            return []

        disabled = []
        for name in interface_names:
            # Find the [[Name]] section and change its enabled = yes to enabled = no
            # Pattern: [[Name]] followed by enabled = yes/true/1 before the next [[ or EOF
            pattern = re.compile(
                r'(^\s*\[\[' + re.escape(name) + r'\]\]\s*$'
                r'.*?)'
                r'(^\s*enabled\s*=\s*)(yes|true|1)',
                re.MULTILINE | re.DOTALL | re.IGNORECASE
            )
            new_content, count = pattern.subn(r'\1\g<2>no', content)
            if count > 0:
                content = new_content
                disabled.append(name)

        if disabled:
            try:
                config_file.write_text(content)
                logger.info("Disabled %d blocking interface(s): %s",
                            len(disabled), ", ".join(disabled))
            except (OSError, PermissionError) as e:
                logger.error("Cannot write RNS config: %s", e)
                return []

        return disabled

    # Sniffer methods (_rns_traffic_sniffer, _rns_sniffer_*) are inherited
    # from RNSSnifferMixin - see rns_sniffer_mixin.py
    #
    # Config methods (_view_rns_config, _edit_rns_config, etc.) are inherited
    # from RNSConfigMixin - see rns_config_mixin.py
    #
    # Diagnostics methods (_rns_diagnostics, _run_rns_tool, etc.) are inherited
    # from RNSDiagnosticsMixin - see rns_diagnostics_mixin.py
