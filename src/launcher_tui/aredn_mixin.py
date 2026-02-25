"""
AREDN Menu Mixin - AREDN mesh network menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import logging
import subprocess
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# AREDN utilities - optional dependency
get_aredn_node, AREDNClient, AREDNScanner, _HAS_AREDN = safe_import(
    'utils.aredn', 'get_aredn_node', 'AREDNClient', 'AREDNScanner'
)


class AREDNMixin:
    """Mixin providing AREDN mesh network menu functionality."""

    def _aredn_menu(self):
        """AREDN mesh network tools."""
        while True:
            choices = [
                ("status", "Node Status"),
                ("neighbors", "Neighbors & Links"),
                ("services", "Advertised Services"),
                ("backhaul", "Backhaul Status     Gateway reachability via AREDN"),
                ("map", "Show on Map"),
                ("web", "Open AREDN Web UI"),
                ("scan", "Scan Network"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "AREDN Mesh",
                "AREDN mesh network tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("AREDN Status", self._aredn_node_status),
                "neighbors": ("Neighbors & Links", self._aredn_neighbors),
                "services": ("AREDN Services", self._aredn_services),
                "backhaul": ("Backhaul Status", self._aredn_backhaul_status),
                "map": ("Show on Map", self._aredn_map),
                "web": ("AREDN Web UI", self._aredn_web),
                "scan": ("Scan Network", self._aredn_scan),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _aredn_get_node_ip(self) -> str:
        """Get AREDN node IP - try common defaults."""
        import socket
        # Try common AREDN addresses
        for host in ['localnode.local.mesh', '10.0.0.1', 'localnode']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))  # AREDN serves on 8080
                    if result == 0:
                        return host
                finally:
                    sock.close()
            except OSError as e:
                logger.debug("AREDN probe %s failed: %s", host, e)
                continue
        return ""

    def _aredn_node_status(self):
        """Show local AREDN node status."""
        clear_screen()
        print("=== AREDN Node Status ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            print("Check: src/utils/aredn.py")
            self._wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found on local network.")
                print("\nTried: localnode.local.mesh, 10.0.0.1")
                print("\nIs your AREDN node connected?")
                self._wait_for_enter()
                return

            print(f"Connecting to {node_ip}...\n")
            node = get_aredn_node(node_ip)

            if node:
                print(f"  Hostname:  {node.hostname}")
                print(f"  IP:        {node.ip}")
                print(f"  Model:     {node.model}")
                print(f"  Firmware:  {node.firmware_version}")
                print(f"  SSID:      {node.ssid}")
                print(f"  Channel:   {node.channel} ({node.frequency})")
                print(f"  Width:     {node.channel_width}")
                print(f"  Status:    {node.mesh_status}")
                print(f"  Uptime:    {node.uptime}")
                print(f"  Tunnels:   {node.tunnel_count}")
                if node.loads:
                    print(f"  Load:      {', '.join(str(l) for l in node.loads)}")
            else:
                print(f"Connected to {node_ip} but couldn't parse node info.")
                print(f"Check: http://{node_ip}:8080/cgi-bin/sysinfo.json")

        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_neighbors(self):
        """Show AREDN neighbor links."""
        clear_screen()
        print("=== AREDN Neighbors ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self._wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found. Is it connected?")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            neighbors = client.get_neighbors()

            if neighbors:
                print(f"Found {len(neighbors)} neighbor(s):\n")
                for link in neighbors:
                    snr_str = f"SNR:{link.snr}dB" if link.snr else ""
                    print(f"  {link.link_type.value:4s} {link.hostname:<30s} {snr_str}")
                    if link.signal:
                        print(f"       Signal:{link.signal} Noise:{link.noise} Rate:{link.tx_rate}Mbps")
            else:
                print("No neighbors found.")
                print("Check that your AREDN node has active RF links.")

        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_services(self):
        """Show AREDN advertised services."""
        clear_screen()
        print("=== AREDN Services ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self._wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found.")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            sysinfo = client.get_sysinfo(services=True)

            if sysinfo and 'services' in sysinfo:
                services = sysinfo['services']
                if services:
                    print(f"Found {len(services)} service(s):\n")
                    for svc in services:
                        name = svc.get('name', 'Unknown')
                        protocol = svc.get('protocol', '')
                        url = svc.get('url', '')
                        print(f"  {name} ({protocol})")
                        if url:
                            print(f"    {url}")
                else:
                    print("No services advertised.")
            else:
                print("Could not retrieve services.")

        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_web(self):
        """Show AREDN web UI URL."""
        node_ip = self._aredn_get_node_ip()
        if node_ip:
            msg = (
                f"AREDN Node Web UI\n\n"
                f"  URL: http://{node_ip}:8080\n\n"
                f"Open in any browser on your network.\n\n"
                f"Provides: configuration, neighbor map,\n"
                f"  services, firmware updates"
            )
        else:
            msg = (
                "No AREDN node found on local network.\n\n"
                "Tried: localnode.local.mesh, 10.0.0.1\n\n"
                "Make sure your AREDN node is connected\n"
                "and accessible from this machine."
            )
        self.dialog.msgbox("AREDN Web UI", msg)

    def _aredn_scan(self):
        """Scan for AREDN nodes on network."""
        clear_screen()
        print("=== AREDN Network Scan ===\n")
        print("Scanning 10.0.0.0/24 for AREDN nodes...\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self._wait_for_enter()
            return

        try:
            scanner = AREDNScanner()
            nodes = scanner.scan_subnet("10.0.0.0/24")

            if nodes:
                print(f"Found {len(nodes)} node(s):\n")
                for node in nodes:
                    print(f"  {node.hostname:<30s} {node.ip:<15s} {node.model}")
            else:
                print("No AREDN nodes found on 10.0.0.0/24")
                print("\nYour network may use a different subnet.")
                print("Check your AREDN node's IP configuration.")

        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_map(self):
        """Show AREDN nodes on the unified network map.

        AREDN nodes are displayed alongside Meshtastic and RNS nodes
        on the MeshForge map. Nodes must have location configured.
        """
        clear_screen()
        print("=== AREDN Network Map ===\n")

        # Check for AREDN node
        node_ip = self._aredn_get_node_ip()
        if not node_ip:
            print("No AREDN node found on local network.\n")
            print("The map requires an AREDN node to be connected.")
            print("Tried: localnode.local.mesh, 10.0.0.1")
            self._wait_for_enter()
            return

        print(f"Connecting to AREDN node at {node_ip}...\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self._wait_for_enter()
            return

        try:
            node = get_aredn_node(node_ip)
            if not node:
                print("Could not retrieve node information.")
                self._wait_for_enter()
                return

            # Show node info
            print(f"  Local Node: {node.hostname}")
            print(f"  Model:      {node.model}")

            # Check if node has location
            if node.has_location():
                print(f"  Location:   {node.latitude:.6f}, {node.longitude:.6f}")
                if node.grid_square:
                    print(f"  Grid:       {node.grid_square}")
            else:
                print(f"  Location:   Not configured")
                print("\n  Note: Configure location on your AREDN node")
                print("  to see it on the map (Setup > Basic Setup > Location)")

            # Show neighbor count
            print(f"\n  Neighbors:  {len(node.links)}")

            # Count neighbors with location
            neighbors_with_loc = 0
            print("\n  Checking neighbor locations...")
            for link in node.links[:5]:  # Check first 5 to avoid long waits
                if link.ip:
                    try:
                        neighbor = get_aredn_node(link.ip)
                        if neighbor and neighbor.has_location():
                            neighbors_with_loc += 1
                            print(f"    ✓ {neighbor.hostname} has location")
                    except Exception as e:
                        logger.debug("AREDN neighbor check failed: %s", e)

            if len(node.links) > 5:
                print(f"    ... and {len(node.links) - 5} more neighbors")

            print(f"\n  AREDN nodes on map: {1 if node.has_location() else 0} + {neighbors_with_loc} neighbors")

            # Show map server info
            print("\n" + "=" * 50)
            print("\nAREDN nodes are included in the unified MeshForge map.")
            print("The map shows Meshtastic, RNS, and AREDN nodes together.")
            print("\nTo view the map:")
            print("  1. Main Menu > Maps & Viz > Coverage Map")
            print("  2. Or start the map server and open in browser")

            # Check if map server is running
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                if sock.connect_ex(('localhost', 5000)) == 0:
                    print("\n  Map server is running: http://localhost:5000")
                sock.close()
            except OSError as e:
                logger.debug("AREDN map server check failed: %s", e)

        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_backhaul_status(self):
        """Show AREDN backhaul status for gateway topology."""
        clear_screen()
        print("=== AREDN Backhaul Status ===\n")

        try:
            from gateway.aredn_topology import AREDNTopologyOverlay
        except ImportError:
            print("AREDN topology module not available.")
            self._wait_for_enter()
            return

        overlay = AREDNTopologyOverlay()
        summary = overlay.get_health_summary()

        if not summary.connected:
            print("No AREDN router detected.\n")
            print("Check:")
            print("  - AREDN router is on the local network")
            print("  - Router is accessible at 10.x.x.x or localnode.local.mesh")
            print("  - aredn module is installed")
            self._wait_for_enter()
            return

        print(f"Local Router: {summary.local_router_name} ({summary.local_router_ip})")
        print(f"Connected:    Yes")
        print(f"Neighbors:    {summary.neighbor_count}")
        print(f"Healthy:      {summary.healthy_links}")
        print(f"Degraded:     {summary.degraded_links}")
        print(f"Remote GWs:   {summary.remote_gateways}")

        if summary.last_scan:
            print(f"Last Scan:    {summary.last_scan.strftime('%Y-%m-%d %H:%M:%S')}")

        # Show links
        links = overlay.discover_backhaul_links()
        if links:
            print(f"\n{'Remote Node':<25} {'IP':<16} {'Type':<6} {'LQ':>5} {'NLQ':>5} {'Status':>8}")
            print("-" * 70)
            for link in links:
                status = "OK" if link.is_healthy else "WARN"
                print(
                    f"{link.remote_node_name:<25} "
                    f"{link.remote_aredn_ip:<16} "
                    f"{link.link_type.value:<6} "
                    f"{link.link_quality:>5.0%} "
                    f"{link.neighbor_quality:>5.0%} "
                    f"{status:>8}"
                )

        # Show discovered gateways
        gateways = overlay.get_remote_gateways()
        if gateways:
            print(f"\nRemote MeshForge Gateways via AREDN:")
            for ip, name in gateways.items():
                print(f"  {name} ({ip})")

        self._wait_for_enter()
