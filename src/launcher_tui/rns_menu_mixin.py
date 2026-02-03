"""
RNS Menu Mixin - Reticulum Network Stack menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Import centralized service checking
try:
    from utils.service_check import check_process_running
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

# Import centralized path utility
try:
    from utils.paths import get_real_user_home, ReticulumPaths
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')

    class ReticulumPaths:
        @classmethod
        def get_config_dir(cls) -> Path:
            if Path('/etc/reticulum/config').is_file():
                return Path('/etc/reticulum')
            home = get_real_user_home()
            xdg = home / '.config' / 'reticulum'
            if (xdg / 'config').is_file():
                return xdg
            return home / '.reticulum'

        @classmethod
        def get_config_file(cls) -> Path:
            return cls.get_config_dir() / 'config'

        @classmethod
        def get_interfaces_dir(cls) -> Path:
            return cls.get_config_dir() / 'interfaces'


class RNSMenuMixin:
    """Mixin providing RNS/Reticulum menu functionality."""

    def _rns_menu(self):
        """Reticulum Network Stack tools."""
        while True:
            choices = [
                ("status", "RNS Status (rnstatus)"),
                ("paths", "RNS Path Table (rnpath)"),
                ("sniffer", "RNS Traffic Sniffer (Wireshark-grade)"),
                ("topology", "Network Topology (graph view)"),
                ("quality", "Link Quality Analysis"),
                ("probe", "Probe Destination (rnprobe)"),
                ("identity", "Identity Info (rnid)"),
                ("nodes", "Known Destinations"),
                ("positions", "Set Node Positions (for map)"),
                ("diag", "RNS Diagnostics"),
                ("bridge", "Gateway Bridge (start/stop)"),
                ("nomadnet", "NomadNet Client"),
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

            if choice == "status":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Status ===\n")
                self._run_rns_tool(['rnstatus'], 'rnstatus')
                self._wait_for_enter()
            elif choice == "paths":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== RNS Path Table ===\n")
                self._run_rns_tool(['rnpath', '-t'], 'rnpath')
                self._wait_for_enter()
            elif choice == "sniffer":
                self._rns_traffic_sniffer()
            elif choice == "topology":
                self._topology_menu()
            elif choice == "quality":
                self._link_quality_menu()
            elif choice == "probe":
                self._rns_probe_destination()
            elif choice == "identity":
                self._rns_identity_info()
            elif choice == "nodes":
                self._rns_known_destinations()
            elif choice == "positions":
                self._rns_set_node_positions()
            elif choice == "diag":
                self._rns_diagnostics()
            elif choice == "bridge":
                self._run_bridge()
            elif choice == "nomadnet":
                self._nomadnet_menu()
            elif choice == "ifaces":
                self._rns_interfaces_menu()
            elif choice == "config":
                self._view_rns_config()
            elif choice == "edit":
                self._edit_rns_config()
            elif choice == "check":
                self._check_rns_setup()

    def _rns_probe_destination(self):
        """Probe an RNS destination to test reachability."""
        subprocess.run(['clear'], check=False, timeout=5)
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
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== RNS Identity Info ===\n")

        while True:
            choices = [
                ("show", "Show local identity"),
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

            if choice == "show":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Local RNS Identity ===\n")
                # rnid with no args shows the local identity
                self._run_rns_tool(['rnid'], 'rnid')

                # Also show MeshForge gateway identity path
                try:
                    from commands.rns import get_identity_path
                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway identity: {gw_id}")
                    if gw_id.exists():
                        print("  Status: exists")
                    else:
                        print("  Status: not created (starts on first bridge run)")
                except ImportError:
                    pass
                self._wait_for_enter()

            elif choice == "path":
                subprocess.run(['clear'], check=False, timeout=5)
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

                # Show gateway identity
                try:
                    from commands.rns import get_identity_path
                    gw_id = get_identity_path()
                    print(f"\nMeshForge gateway:  {gw_id}")
                    if gw_id.exists():
                        stat = gw_id.stat()
                        print(f"  Size: {stat.st_size} bytes")
                    else:
                        print("  Not created yet")
                except ImportError:
                    pass
                self._wait_for_enter()

            elif choice == "recall":
                subprocess.run(['clear'], check=False, timeout=5)
                print("=== Recall RNS Identity ===\n")
                print("Look up a known identity by its destination hash.\n")
                try:
                    dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
                except (KeyboardInterrupt, EOFError):
                    print()
                    continue
                if dest_hash and dest_hash.lower() != 'q':
                    # Validate hex format to prevent flag injection
                    if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
                        print("Error: Hash must contain only hex characters (0-9, a-f).")
                    else:
                        self._run_rns_tool(['rnid', '--recall', dest_hash], 'rnid')
                self._wait_for_enter()

    def _rns_known_destinations(self):
        """Show known RNS destinations from the running rnsd instance."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Known RNS Destinations ===\n")

        try:
            from commands.rns import list_known_destinations
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
        except ImportError:
            # Fallback: use rnstatus which also shows some destination info
            print("Commands module not available, falling back to rnstatus...\n")
            self._run_rns_tool(['rnstatus', '-a'], 'rnstatus')

        self._wait_for_enter()

    def _rns_set_node_positions(self):
        """Set GPS positions for RNS nodes so they appear on the map.

        NomadNet nodes don't broadcast location, so positions must be set manually.
        Sideband nodes with GPS sharing will be auto-populated.
        """
        while True:
            subprocess.run(['clear'], check=False, timeout=5)
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
        subprocess.run(['clear'], check=False, timeout=5)
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

    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== RNS Diagnostics ===\n")

        try:
            from commands.rns import check_connectivity, get_status
        except ImportError:
            print("RNS commands module not available.")
            print("Run from MeshForge root: sudo python3 src/launcher_tui/main.py")
            self._wait_for_enter()
            return

        # 1. Service status
        print("[1/4] Checking rnsd service...")
        status = get_status()
        status_data = status.data or {}
        running = status_data.get('rnsd_running', False)
        print(f"  rnsd: {'RUNNING' if running else 'NOT RUNNING'}")
        if status_data.get('rnsd_pid'):
            print(f"  PID: {status_data['rnsd_pid']}")
        if status_data.get('service_state'):
            print(f"  State: {status_data['service_state']}")

        # 2. Config check
        print("\n[2/4] Checking configuration...")
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/4] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
        rns_identity = config_dir / 'identity'
        print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

        # 4. Full connectivity check
        print("\n[4/4] Running connectivity check...")
        conn = check_connectivity()
        conn_data = conn.data or {}
        print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
        if conn_data.get('rns_version'):
            print(f"  RNS version: {conn_data['rns_version']}")
        print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
        print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

        # Summary
        issues = conn_data.get('issues', [])
        if issues:
            print(f"\n--- Issues Found ({len(issues)}) ---")
            for issue in issues:
                print(f"  ! {issue}")
        else:
            print("\n--- All checks passed ---")

        # RNS tool availability
        print("\n--- RNS Tool Availability ---")
        for tool in ['rnsd', 'rnstatus', 'rnpath', 'rnprobe', 'rnid', 'rncp', 'rnx']:
            path = shutil.which(tool)
            if path:
                print(f"  {tool}: {path}")
            else:
                print(f"  {tool}: not found")

        self._wait_for_enter()

    @staticmethod
    def _is_root_owned_rns_config(config_path: Path) -> bool:
        """Check if the RNS config is in a root-only location (/root/)."""
        try:
            return str(config_path.resolve()).startswith('/root/')
        except OSError:
            return str(config_path).startswith('/root/')

    def _migrate_rns_config_to_etc(self, source: Path) -> bool:
        """Migrate RNS config from root-owned location to /etc/reticulum/config.

        Copies the config to /etc/reticulum/config (system-wide, preferred location),
        sets world-readable permissions, and renames the old file to avoid confusion.

        Returns True if migration succeeded.
        """
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Cannot Migrate",
                f"Config already exists at:\n  {target}\n\n"
                f"Remove it first if you want to migrate from:\n  {source}"
            )
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            target.chmod(0o644)
            # Rename old config so rnsd picks up the /etc/ one
            backup = source.with_suffix('.migrated')
            source.rename(backup)
            return True
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to migrate config:\n{e}")
            return False

    def _deploy_rns_template(self) -> Optional[Path]:
        """Deploy RNS template to /etc/reticulum/config (system-wide).

        Returns the path where the config was deployed, or None on failure.
        """
        template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
        if not template.exists():
            return None

        # Always deploy to /etc/reticulum/ (system-wide, first in search order)
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Config Exists",
                f"Config already exists at:\n  {target}\n\n"
                f"Use 'Edit Reticulum Config' to modify it."
            )
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(template), str(target))
            target.chmod(0o644)  # World-readable so all users and rnsd can read it
            return target
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to deploy config:\n{e}")
            return None

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
        """Download and install Meshtastic_Interface.py plugin from GitHub.

        Clones the RNS_Over_Meshtastic_Gateway repository and copies the
        Meshtastic_Interface.py file to the RNS interfaces directory.
        """
        interfaces_dir = ReticulumPaths.get_interfaces_dir()
        plugin_path = interfaces_dir / 'Meshtastic_Interface.py'

        if plugin_path.exists():
            self.dialog.msgbox(
                "Already Installed",
                f"Meshtastic_Interface.py is already installed at:\n"
                f"  {plugin_path}\n\n"
                f"Size: {plugin_path.stat().st_size} bytes"
            )
            return

        if not self.dialog.yesno(
            "Install Meshtastic Interface Plugin",
            "The Meshtastic_Interface.py plugin is required for\n"
            "bridging RNS over Meshtastic LoRa mesh networks.\n\n"
            "Source: github.com/landandair/RNS_Over_Meshtastic\n\n"
            f"Install to:\n  {plugin_path}\n\n"
            "Requires: git and internet connection.\n\n"
            "Install now?"
        ):
            return

        # Clone repo to temp dir and copy plugin
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix='meshforge_rns_plugin_')
        clone_url = "https://github.com/landandair/RNS_Over_Meshtastic.git"

        try:
            # Clone the repository
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, tmp_dir],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                self.dialog.msgbox(
                    "Clone Failed",
                    f"Failed to clone repository:\n{result.stderr}\n\n"
                    f"Manual install:\n"
                    f"  git clone {clone_url}\n"
                    f"  cp RNS_Over_Meshtastic/Interface/Meshtastic_Interface.py \\\n"
                    f"    {interfaces_dir}/"
                )
                return

            # Find the plugin file (in Interface/ subfolder per upstream repo)
            source_file = Path(tmp_dir) / 'Interface' / 'Meshtastic_Interface.py'
            if not source_file.exists():
                # Fallback: check repo root in case structure changes
                source_file = Path(tmp_dir) / 'Meshtastic_Interface.py'
            if not source_file.exists():
                self.dialog.msgbox(
                    "Plugin Not Found",
                    f"Meshtastic_Interface.py not found in repository.\n\n"
                    f"Expected at: Interface/Meshtastic_Interface.py\n"
                    f"Check: {clone_url}"
                )
                return

            # Create interfaces directory and copy plugin
            interfaces_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_file), str(plugin_path))
            plugin_path.chmod(0o644)

            self.dialog.msgbox(
                "Plugin Installed",
                f"Meshtastic_Interface.py installed to:\n"
                f"  {plugin_path}\n\n"
                f"Restart rnsd to load the new interface:\n"
                f"  sudo systemctl restart rnsd"
            )

        except FileNotFoundError:
            self.dialog.msgbox(
                "Git Not Found",
                "git is required to download the plugin.\n\n"
                "Install git: sudo apt install git\n\n"
                "Or manually download from:\n"
                f"  {clone_url}"
            )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox(
                "Timeout",
                "Download timed out. Check your internet connection."
            )
        except (OSError, PermissionError) as e:
            self.dialog.msgbox(
                "Install Failed",
                f"Failed to install plugin:\n{e}\n\n"
                f"Try running with sudo, or manually copy:\n"
                f"  sudo cp Meshtastic_Interface.py {interfaces_dir}/"
            )
        finally:
            # Clean up temp dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _view_rns_config(self):
        """View current Reticulum config."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Reticulum Configuration ===\n")

        config_path = ReticulumPaths.get_config_file()

        if config_path.exists():
            # Warn if config is in root-only location
            if self._is_root_owned_rns_config(config_path):
                print(f"Config: {config_path}")
                print(f"  ** This config is in /root/ - not editable without sudo **")
                print(f"  ** Use 'Edit Reticulum Config' to migrate to /etc/reticulum/ **\n")
            else:
                print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                # Show validation warnings inline
                issues = self._validate_rns_config_content(content)
                if issues:
                    print("\n--- Config Issues ---")
                    for issue in issues:
                        print(f"  ! {issue}")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
                print(f"Try: sudo cat {config_path}")
        else:
            print(f"No Reticulum config found at: {config_path}")
            user_home = get_real_user_home()
            print(f"\nMeshForge checks (in order):")
            print(f"  1. /etc/reticulum/config  (system-wide, preferred)")
            print(f"  2. {user_home}/.config/reticulum/config")
            print(f"  3. {user_home}/.reticulum/config")
            if os.geteuid() == 0 and os.environ.get('SUDO_USER'):
                print(f"\nNote: rnsd (running as root) uses /root/.reticulum/config")
                print(f"  For shared use, deploy to /etc/reticulum/config")
            print(f"\nTo create: use 'Edit Reticulum Config' to deploy template")
            print(f"Template:  templates/reticulum.conf")

        # Show Meshtastic_Interface plugin status
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        print(f"\n--- Meshtastic Interface Plugin ---")
        if plugin_path.exists():
            print(f"  Installed: {plugin_path}")
            print(f"  Size: {plugin_path.stat().st_size} bytes")
        else:
            print(f"  NOT INSTALLED")
            print(f"  Expected at: {plugin_path}")
            print(f"  Source: https://github.com/landandair/RNS_Over_Meshtastic")
            print(f"  Use 'Install Meshtastic Interface' from the RNS menu to install.")

        self._wait_for_enter()

    def _edit_rns_config(self):
        """Edit Reticulum config with available editor. Deploys template if no config exists."""
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            # Offer to deploy from template to /etc/reticulum/config (system-wide)
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'

            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "Deploy Reticulum Config",
                    f"No Reticulum config found.\n\n"
                    f"Deploy template to:\n  {target}\n\n"
                    f"This sets up RNS with:\n"
                    f"  - share_instance = Yes (required for rnstatus)\n"
                    f"  - AutoInterface (local network discovery)\n"
                    f"  - Meshtastic_Interface on port 4403\n\n"
                    f"You can edit it after deployment."
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        config_path = deployed
                    else:
                        return
                else:  # User said No
                    return
            else:
                self.dialog.msgbox(
                    "No Config",
                    "No Reticulum config found and template missing.\n\n"
                    "Install RNS first: pipx install rns\n"
                    "Then run rnsd once to generate default config."
                )
                return

        # If config is in /root/, offer to migrate to /etc/reticulum/
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Migrate Config",
                f"Config is at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by rnsd and all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )
                # If migration failed, continue with original path

        # Find editor
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break

        if not editor:
            self.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, str(config_path)], timeout=None)

    def _validate_rns_config_content(self, content: str) -> list:
        """Validate RNS config content and return list of issues found.

        Checks for common misconfigurations that cause rnstatus/rnpath failures:
        - Missing [reticulum] section
        - Missing share_instance = Yes (required for client apps to connect)
        - No interfaces configured
        - No Meshtastic_Interface (needed for mesh bridging)
        - Meshtastic_Interface.py plugin not installed
        """
        issues = []
        content_lower = content.lower()

        # Check [reticulum] section exists
        if '[reticulum]' not in content_lower:
            issues.append("Missing [reticulum] section")

        # Check share_instance (required for rnstatus/rnpath to connect to rnsd)
        has_share = False
        for line in content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('#'):
                continue
            if 'share_instance' in stripped:
                if 'yes' in stripped or 'true' in stripped:
                    has_share = True
                break
        if not has_share:
            issues.append("share_instance not set to Yes (rnstatus/client apps won't connect)")

        # Check for at least one active interface
        has_interface = False
        has_meshtastic = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('[[') and stripped.endswith(']]'):
                has_interface = True
            if 'meshtastic_interface' in stripped.lower() and 'type' in stripped.lower():
                has_meshtastic = True

        if not has_interface:
            issues.append("No interfaces configured")

        # Check Meshtastic_Interface status: config reference + plugin file
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        plugin_installed = plugin_path.exists()

        if not has_meshtastic and not plugin_installed:
            issues.append("No Meshtastic_Interface configured (needed for mesh bridging)")
        elif has_meshtastic and not plugin_installed:
            issues.append(
                f"Meshtastic_Interface.py plugin not installed at "
                f"{ReticulumPaths.get_interfaces_dir()}/\n"
                f"    Install from: https://github.com/landandair/RNS_Over_Meshtastic"
            )

        return issues

    def _check_rns_setup(self) -> bool:
        """Check RNS setup and offer to fix common issues.

        Available via 'Check RNS Setup' menu item. Returns True if setup
        looks OK or user chose to continue anyway, False if user wants
        to go back.
        """
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "RNS Not Configured",
                    f"No Reticulum config found.\n\n"
                    f"RNS tools (rnstatus, rnpath) and the gateway bridge\n"
                    f"require a config file to function.\n\n"
                    f"Deploy MeshForge template to:\n"
                    f"  {target}\n\n"
                    f"(Sets up shared instance + Meshtastic bridge)"
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        self.dialog.msgbox(
                            "Config Deployed",
                            f"Deployed to: {deployed}\n\n"
                            f"Restart rnsd to apply:\n"
                            f"  sudo systemctl restart rnsd"
                        )
                        config_path = deployed
            return True  # Continue to menu either way

        # Config exists - check if it's in a root-only location
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Config in /root/",
                f"RNS config found at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )

        # Config exists - validate it
        try:
            content = config_path.read_text()
            issues = self._validate_rns_config_content(content)
            if issues:
                msg = f"Config: {config_path}\n\nIssues found:\n"
                for issue in issues:
                    msg += f"  - {issue}\n"
                msg += f"\nUse 'Edit Reticulum Config' to fix these issues."
                self.dialog.msgbox("RNS Config Issues", msg)
        except PermissionError:
            self.dialog.msgbox(
                "Permission Denied",
                f"Cannot read config at:\n  {config_path}\n\n"
                f"Run MeshForge with sudo to access this file,\n"
                f"or use 'Edit Reticulum Config' to migrate it."
            )

        # Check for Meshtastic_Interface.py plugin (separate from config validation)
        if not self._check_meshtastic_plugin():
            if self.dialog.yesno(
                "Meshtastic Interface Plugin Missing",
                "The Meshtastic_Interface.py plugin is not installed.\n\n"
                "This plugin is required for bridging RNS over\n"
                "Meshtastic LoRa mesh networks.\n\n"
                f"Expected at:\n"
                f"  {ReticulumPaths.get_interfaces_dir()}/Meshtastic_Interface.py\n\n"
                "Download and install it now?"
            ):
                self._install_meshtastic_interface_plugin()

        return True

    def _run_rns_tool(self, cmd: list, tool_name: str):
        """Run an RNS CLI tool with address-in-use error detection.

        Captures both stdout and stderr to detect specific error patterns.
        RNS logs errors to stdout in some configurations, so both streams
        must be checked for the 'Address already in use' pattern.

        Args:
            cmd: Command and arguments to run
            tool_name: Display name for error messages (e.g., "rnpath")
        """
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            # RNS tools may log errors to stdout or stderr depending on config
            combined = (result.stdout or "") + (result.stderr or "")

            if result.returncode == 0:
                # Success - show normal output
                if result.stdout:
                    print(result.stdout, end='')
            elif "address already in use" in combined.lower():
                # Suppress noisy traceback, show actionable diagnostics
                print("\nError: RNS port conflict (Address already in use)")
                print("Another process is bound to the RNS AutoInterface port.\n")
                self._diagnose_rns_port_conflict()
            elif "no shared" in combined.lower():
                # rnsd not running or share_instance not enabled
                print("\nNo shared RNS instance available.")
                # Check if config has share_instance
                cfg_path = ReticulumPaths.get_config_file()
                if cfg_path.exists():
                    try:
                        cfg_content = cfg_path.read_text()
                        issues = self._validate_rns_config_content(cfg_content)
                        if issues:
                            print(f"\nConfig issues ({cfg_path}):")
                            for issue in issues:
                                print(f"  - {issue}")
                            print("\nFix config: use 'Edit Reticulum Config' menu")
                        else:
                            print(f"\nConfig looks OK ({cfg_path})")
                            print("rnsd may not be running:")
                            print("  sudo systemctl start rnsd")
                    except PermissionError:
                        print(f"\nCannot read config: {cfg_path}")
                        print("  Run MeshForge with sudo")
                else:
                    print(f"\nNo config found at: {cfg_path}")
                    print("Use 'Edit Reticulum Config' to deploy template")
            else:
                # Generic failure - show output and suggestions
                if result.stdout:
                    print(result.stdout, end='')
                print(f"\n{tool_name} failed. Possible causes:")
                print("  - rnsd not running: sudo systemctl start rnsd")
                print("  - RNS not installed: pipx install rns")
                if result.stderr and result.stderr.strip():
                    # Show last 3 lines of stderr for context
                    err_lines = result.stderr.strip().split('\n')[-3:]
                    print("\nDetails:")
                    for line in err_lines:
                        print(f"  {line}")
        except FileNotFoundError:
            print(f"\n{tool_name} not found. Is RNS installed?")
            print("Install: pipx install rns")
        except subprocess.TimeoutExpired:
            print(f"\n{tool_name} timed out. RNS may be unresponsive.")
            print("Try restarting rnsd: sudo systemctl restart rnsd")

    def _diagnose_rns_port_conflict(self):
        """Print diagnostic info for RNS Address-in-use port conflicts."""
        try:
            # Use centralized service check when available
            if _HAS_SERVICE_CHECK:
                rnsd_running = check_process_running('rnsd')
            else:
                # Fallback to direct pgrep
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0

            if rnsd_running:
                # Get PID for diagnostic message
                try:
                    pid_result = subprocess.run(
                        ['pgrep', '-f', 'rnsd'],
                        capture_output=True, text=True, timeout=5
                    )
                    pid = pid_result.stdout.strip().split('\n')[0] if pid_result.stdout else 'unknown'
                except Exception:
                    pid = 'unknown'
                print(f"rnsd is running (PID: {pid}) but may need a restart:")
                print("  sudo systemctl restart rnsd")
            else:
                print("No rnsd found. A stale process may be holding the port.")
                print("  Find it:    sudo lsof -i UDP:29716")
                print("  Kill stale: pkill -f rnsd")
                print("  Or wait ~30s for the socket to timeout")
        except Exception:
            print("  Try: sudo systemctl restart rnsd")

    def _rns_traffic_sniffer(self):
        """RNS Traffic Sniffer - Wireshark-grade packet capture for RNS."""
        # Import RNS sniffer components
        try:
            from monitoring.rns_sniffer import (
                get_rns_sniffer, start_rns_capture, stop_rns_capture,
                RNSPacketType, integrate_with_traffic_inspector
            )
            HAS_RNS_SNIFFER = True
        except ImportError:
            HAS_RNS_SNIFFER = False

        if not HAS_RNS_SNIFFER:
            self.dialog.msgbox(
                "RNS Sniffer Not Available",
                "The RNS traffic sniffer module is not installed.\n\n"
                "Required: monitoring/rns_sniffer.py",
                height=8, width=50
            )
            return

        while True:
            sniffer = get_rns_sniffer()
            capturing = sniffer._running if sniffer else False
            stats = sniffer.get_stats() if sniffer else {}

            capture_status = "CAPTURING" if capturing else "STOPPED"
            capture_action = "Stop Capture" if capturing else "Start Capture"

            packets = stats.get("packets_captured", 0)
            announces = stats.get("announces_seen", 0)
            paths = stats.get("paths_discovered", 0)

            choice = self.dialog.menu(
                "RNS Traffic Sniffer",
                f"Wireshark-grade RNS packet visibility\n"
                f"Status: {capture_status} | Packets: {packets} | "
                f"Announces: {announces} | Paths: {paths}",
                choices=[
                    ("capture", f"{capture_action}        - {'Stop' if capturing else 'Start'} RNS capture"),
                    ("1", "View Live Traffic      - Recent RNS packets"),
                    ("2", "View Path Table        - Discovered routes"),
                    ("3", "View Announces         - Node discoveries"),
                    ("4", "Filter by Destination  - Search by hash"),
                    ("5", "Probe Destination      - Request path + capture"),
                    ("6", "View Links             - Active RNS links"),
                    ("7", "Traffic Statistics     - Packet stats"),
                    ("8", "Test Known Node        - Test 17a4dcfd..."),
                    ("0", "Clear Capture          - Clear captured data"),
                ],
                height=20, width=70
            )

            if not choice:
                return

            if choice == "capture":
                self._rns_sniffer_toggle_capture(sniffer, capturing)
            elif choice == "1":
                self._rns_sniffer_live_traffic(sniffer)
            elif choice == "2":
                self._rns_sniffer_path_table(sniffer)
            elif choice == "3":
                self._rns_sniffer_announces(sniffer)
            elif choice == "4":
                self._rns_sniffer_filter_destination(sniffer)
            elif choice == "5":
                self._rns_sniffer_probe_destination(sniffer)
            elif choice == "6":
                self._rns_sniffer_links(sniffer)
            elif choice == "7":
                self._rns_sniffer_statistics(sniffer)
            elif choice == "8":
                self._rns_sniffer_test_known_node(sniffer)
            elif choice == "0":
                self._rns_sniffer_clear(sniffer)

    def _rns_sniffer_toggle_capture(self, sniffer, capturing):
        """Toggle RNS packet capture."""
        from monitoring.rns_sniffer import start_rns_capture, stop_rns_capture

        if capturing:
            stop_rns_capture()
            self.dialog.msgbox(
                "Capture Stopped",
                "RNS packet capture has been stopped.\n\n"
                "Captured packets are preserved.",
                height=8, width=45
            )
        else:
            if start_rns_capture():
                self.dialog.msgbox(
                    "Capture Started",
                    "RNS packet capture is now active.\n\n"
                    "Listening for RNS announces, links, and packets.\n"
                    "Packets will appear in Live Traffic view.",
                    height=10, width=50
                )
            else:
                self.dialog.msgbox(
                    "Capture Started (No RNS)",
                    "Capture mode enabled but RNS not detected.\n\n"
                    "Once rnsd or the gateway bridge starts,\n"
                    "packets will be captured automatically.",
                    height=10, width=50
                )

    def _rns_sniffer_live_traffic(self, sniffer):
        """View live RNS traffic."""
        if not sniffer:
            return

        stats = sniffer.get_stats()
        packets = sniffer.get_packets(limit=30)

        lines = [
            "RNS Live Traffic",
            "=" * 70,
            "",
            f"Capture: {'ACTIVE' if sniffer._running else 'STOPPED'}",
            f"Packets: {stats.get('packets_captured', 0)} | "
            f"Announces: {stats.get('announces_seen', 0)} | "
            f"Paths: {stats.get('paths_discovered', 0)}",
            "",
            "Recent Packets:",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:20]:
                summary = pkt.get_summary()
                if len(summary) > 68:
                    summary = summary[:65] + "..."
                lines.append(summary)
        else:
            lines.append("No packets captured yet.")
            if not sniffer._running:
                lines.append("")
                lines.append("Use 'Start Capture' to begin capturing RNS traffic.")

        self.dialog.msgbox(
            "RNS Live Traffic",
            "\n".join(lines),
            height=30, width=75
        )

    def _rns_sniffer_path_table(self, sniffer):
        """View discovered RNS paths."""
        if not sniffer:
            return

        paths = sniffer.get_path_table()

        lines = [
            "RNS Path Table",
            "=" * 70,
            "",
            f"Discovered Paths: {len(paths)}",
            "",
            f"{'Destination Hash':<34} {'Hops':<6} {'Announces':<10} {'Last Seen':<20}",
            "-" * 70,
        ]

        if paths:
            for path in sorted(paths, key=lambda p: p.last_seen, reverse=True)[:25]:
                dest = path.destination_hash.hex()[:32]
                hops = str(path.hops)
                ann = str(path.announce_count)
                last = path.last_seen.strftime("%H:%M:%S")
                lines.append(f"{dest:<34} {hops:<6} {ann:<10} {last:<20}")
        else:
            lines.append("No paths discovered yet.")
            lines.append("")
            lines.append("Paths are discovered when RNS announces are received.")

        self.dialog.msgbox(
            "RNS Path Table",
            "\n".join(lines),
            height=32, width=75
        )

    def _rns_sniffer_announces(self, sniffer):
        """View RNS announce packets."""
        if not sniffer:
            return

        from monitoring.rns_sniffer import RNSPacketType

        packets = sniffer.get_packets(
            limit=50,
            packet_type=RNSPacketType.ANNOUNCE
        )

        lines = [
            "RNS Announces",
            "=" * 70,
            "",
            f"Announce Packets: {len(packets)}",
            "",
            f"{'Time':<10} {'Destination':<34} {'Aspect':<20} {'Hops':<6}",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:25]:
                time_str = pkt.timestamp.strftime("%H:%M:%S")
                dest = pkt.destination_hash.hex()[:32] if pkt.destination_hash else "?"
                aspect = pkt.announce_aspect[:18] if pkt.announce_aspect else "?"
                hops = str(pkt.hops)
                lines.append(f"{time_str:<10} {dest:<34} {aspect:<20} {hops:<6}")
        else:
            lines.append("No announces captured yet.")
            lines.append("")
            lines.append("Enable capture and wait for nodes to announce.")

        self.dialog.msgbox(
            "RNS Announces",
            "\n".join(lines),
            height=32, width=75
        )

    def _rns_sniffer_filter_destination(self, sniffer):
        """Filter packets by destination hash."""
        if not sniffer:
            return

        dest = self.dialog.inputbox(
            "Filter by Destination",
            "Enter destination hash prefix (hex):\n\n"
            "Examples:\n"
            "  17a4dcfd  (first 8 chars)\n"
            "  17a4dcfd433f57c7  (16 chars)\n\n"
            "Leave empty to see all packets.",
            height=14, width=55
        )

        if dest is None:
            return

        packets = sniffer.get_packets(
            limit=50,
            destination=dest if dest else None
        )

        lines = [
            f"RNS Packets" + (f" (dest: {dest})" if dest else ""),
            "=" * 70,
            "",
            f"Matching Packets: {len(packets)}",
            "",
            "-" * 70,
        ]

        if packets:
            for pkt in packets[:20]:
                summary = pkt.get_summary()
                lines.append(summary[:68])
        else:
            lines.append("No packets match the filter.")

        self.dialog.msgbox(
            "Filtered Packets",
            "\n".join(lines),
            height=28, width=75
        )

    def _rns_sniffer_probe_destination(self, sniffer):
        """Probe a destination and capture the traffic."""
        if not sniffer:
            return

        dest = self.dialog.inputbox(
            "Probe Destination",
            "Enter destination hash to probe (hex):\n\n"
            "This will:\n"
            "1. Request path to destination\n"
            "2. Capture any response packets\n\n"
            "Example: 17a4dcfd433f57c7ec445d103a65e7a3",
            height=14, width=60
        )

        if not dest:
            return

        # Validate hex
        if not re.match(r'^[0-9a-fA-F]+$', dest):
            self.dialog.msgbox(
                "Invalid Hash",
                "Hash must contain only hex characters (0-9, a-f).",
                height=6, width=45
            )
            return

        # Start capture if not running
        if not sniffer._running:
            from monitoring.rns_sniffer import start_rns_capture
            start_rns_capture()

        # Probe
        success = sniffer.probe_destination(dest)

        if success:
            self.dialog.msgbox(
                "Probe Sent",
                f"Path request sent for:\n{dest}\n\n"
                "Check Live Traffic for responses.\n"
                "Use 'rnpath -t' to see if path was discovered.",
                height=11, width=60
            )
        else:
            self.dialog.msgbox(
                "Probe Failed",
                "Could not send path request.\n\n"
                "RNS may not be available.",
                height=8, width=45
            )

    def _rns_sniffer_links(self, sniffer):
        """View active RNS links."""
        if not sniffer:
            return

        links = sniffer.get_links()

        lines = [
            "RNS Links",
            "=" * 70,
            "",
            f"Tracked Links: {len(links)}",
            "",
            f"{'Link ID':<18} {'Destination':<18} {'State':<12} {'RTT':<10}",
            "-" * 70,
        ]

        if links:
            for link in links:
                link_id = link.link_id.hex()[:16] if link.link_id else "?"
                dest = link.destination_hash.hex()[:16] if link.destination_hash else "?"
                state = link.state.value[:10]
                rtt = f"{link.rtt_ms:.1f}ms" if link.rtt_ms else "-"
                lines.append(f"{link_id:<18} {dest:<18} {state:<12} {rtt:<10}")
        else:
            lines.append("No links tracked yet.")
            lines.append("")
            lines.append("Links appear when RNS connections are established.")

        self.dialog.msgbox(
            "RNS Links",
            "\n".join(lines),
            height=24, width=75
        )

    def _rns_sniffer_statistics(self, sniffer):
        """View RNS traffic statistics."""
        if not sniffer:
            return

        stats = sniffer.get_stats()

        lines = [
            "RNS Traffic Statistics",
            "=" * 50,
            "",
            f"Capture Status:    {'ACTIVE' if sniffer._running else 'STOPPED'}",
            f"Start Time:        {stats.get('start_time', 'N/A')}",
            "",
            "Packet Counts:",
            f"  Total Captured:  {stats.get('packets_captured', 0):,}",
            f"  Announces:       {stats.get('announces_seen', 0):,}",
            f"  Bytes Captured:  {stats.get('bytes_captured', 0):,}",
            "",
            "Network Discovery:",
            f"  Paths Discovered: {stats.get('paths_discovered', 0)}",
            f"  Current Paths:    {stats.get('path_count', 0)}",
            f"  Links Tracked:    {stats.get('link_count', 0)}",
            f"  Active Links:     {stats.get('active_links', 0)}",
            "",
            "Links Established: {stats.get('links_established', 0)}",
        ]

        self.dialog.msgbox(
            "RNS Statistics",
            "\n".join(lines),
            height=24, width=55
        )

    def _rns_sniffer_test_known_node(self, sniffer):
        """Test connectivity to the known working RNS node."""
        if not sniffer:
            return

        # Known working node from session notes
        identity_hash = "17a4dcfd433f57c7ec445d103a65e7a3"
        lxmf_address = "02ddf7b650daa8b73132badb18a8ce84"

        choice = self.dialog.menu(
            "Test Known RNS Node",
            f"Working RNS node for testing:\n"
            f"Identity: {identity_hash}\n"
            f"LXMF:     {lxmf_address}",
            choices=[
                ("1", "Probe Identity Hash"),
                ("2", "Probe LXMF Address"),
                ("3", "Filter Packets by Identity"),
                ("4", "Run rnprobe CLI"),
            ],
            height=15, width=60
        )

        if not choice:
            return

        # Start capture if not running
        if not sniffer._running:
            from monitoring.rns_sniffer import start_rns_capture
            start_rns_capture()

        if choice == "1":
            success = sniffer.probe_destination(identity_hash)
            msg = "Path request sent" if success else "Failed to send"
            self.dialog.msgbox("Probe Result", f"{msg} for:\n{identity_hash}", height=8, width=55)

        elif choice == "2":
            success = sniffer.probe_destination(lxmf_address)
            msg = "Path request sent" if success else "Failed to send"
            self.dialog.msgbox("Probe Result", f"{msg} for:\n{lxmf_address}", height=8, width=55)

        elif choice == "3":
            packets = sniffer.get_packets(limit=50, destination=identity_hash[:8])
            lines = [f"Packets for {identity_hash[:16]}...", "=" * 50, ""]
            if packets:
                for pkt in packets[:15]:
                    lines.append(pkt.get_summary()[:48])
            else:
                lines.append("No packets found for this destination.")
                lines.append("Try probing the node first.")
            self.dialog.msgbox("Filtered Packets", "\n".join(lines), height=22, width=55)

        elif choice == "4":
            subprocess.run(['clear'], check=False, timeout=5)
            print(f"=== Probing {identity_hash} ===\n")
            self._run_rns_tool(['rnprobe', identity_hash], 'rnprobe')
            self._wait_for_enter()

    def _rns_sniffer_clear(self, sniffer):
        """Clear captured RNS packets."""
        if not sniffer:
            return

        confirm = self.dialog.yesno(
            "Clear Capture",
            "Clear all captured RNS packets?\n\n"
            "This cannot be undone.",
            height=8, width=40
        )

        if confirm:
            count = sniffer.clear()
            self.dialog.msgbox(
                "Cleared",
                f"Cleared {count} captured packets.",
                height=6, width=35
            )
