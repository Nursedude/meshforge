"""
AREDN Handler — AREDN mesh network tools.

Converted from aredn_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import subprocess

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

get_aredn_node, AREDNClient, AREDNScanner, _HAS_AREDN = safe_import(
    'utils.aredn', 'get_aredn_node', 'AREDNClient', 'AREDNScanner'
)

_is_gateway_running, _HAS_GW_STATUS = safe_import(
    'gateway.gateway_cli', 'is_gateway_running'
)


class AREDNHandler(BaseHandler):
    """TUI handler for AREDN mesh network tools."""

    handler_id = "aredn"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("aredn", "AREDN Mesh          AREDN integration", None),
        ]

    def execute(self, action):
        if action == "aredn":
            self._aredn_menu()

    def _aredn_menu(self):
        while True:
            choices = [
                ("status", "Node Status"),
                ("neighbors", "Neighbors & Links"),
                ("services", "Advertised Services"),
                ("routing", "Routing Influence     AREDN backhaul hints"),
                ("map", "Show on Map"),
                ("web", "Open AREDN Web UI"),
                ("scan", "Scan Network"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
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
                "routing": ("Routing Influence", self._aredn_routing_influence),
                "map": ("Show on Map", self._aredn_map),
                "web": ("AREDN Web UI", self._aredn_web),
                "scan": ("Scan Network", self._aredn_scan),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _aredn_routing_influence(self):
        """Show AREDN routing influence — which gateways have backhaul paths."""
        clear_screen()
        print("=== AREDN Routing Influence ===\n")

        # Check if gateway bridge is running
        if _HAS_GW_STATUS and not _is_gateway_running():
            print("  Gateway bridge is not running.")
            print("  Start the bridge to see AREDN routing influence.")
            print("\n  AREDN routing hints are active when the bridge")
            print("  has an AREDN overlay connected to a local router.")
            self.ctx.wait_for_enter()
            return

        # Try to get routing info from the active bridge's router
        try:
            from gateway.gateway_cli import get_active_bridge
            bridge = get_active_bridge()
            if not bridge or not hasattr(bridge, '_router'):
                print("  Bridge router not accessible.")
                self.ctx.wait_for_enter()
                return

            info = bridge._router.get_aredn_routing_info()
        except Exception as e:
            logger.debug(f"AREDN routing info failed: {e}")
            print("  Could not retrieve AREDN routing information.")
            print(f"  Error: {e}")
            self.ctx.wait_for_enter()
            return

        if not info.get("available"):
            reason = info.get("reason", "AREDN overlay not configured")
            print(f"  {reason}")
            print("\n  To enable AREDN routing hints:")
            print("  1. Configure aredn_backhaul in gateway.yaml")
            print("  2. Set enabled: true")
            print("  3. Restart the gateway bridge")
            self.ctx.wait_for_enter()
            return

        connected = info.get("connected", False)
        status_color = "\033[0;32m" if connected else "\033[0;31m"
        print(f"  AREDN Overlay:  {status_color}{'Connected' if connected else 'Disconnected'}\033[0m")

        gateways = info.get("gateways", {})
        print(f"  Remote Gateways: {len(gateways)}\n")

        if not gateways:
            print("  No remote MeshForge gateways found via AREDN.")
            print("  Routing hints activate when AREDN discovers peer gateways")
            print("  running meshtasticd, rnsd, or meshforge.")
        else:
            print(f"  {'Gateway':<25} {'IP':<16} {'Quality':>8} {'Type':<8} {'Status'}")
            print(f"  {'-'*70}")

            for ip, hint in gateways.items():
                name = hint.get("gateway_name", "") or ip
                quality = hint.get("quality", 0)
                link_type = hint.get("link_type", "?")
                available = hint.get("available", False)

                if quality > 0.7:
                    q_color = "\033[0;32m"
                elif quality > 0.5:
                    q_color = "\033[0;33m"
                else:
                    q_color = "\033[0;31m"

                status = "\033[0;32mActive\033[0m" if available else "\033[0;31mDown\033[0m"
                print(f"  {name[:24]:<25} {ip:<16} {q_color}{quality:>6.0%}\033[0m   {link_type:<8} {status}")

            print(f"\n  Messages to nodes reachable via these gateways will")
            print(f"  be annotated with AREDN backhaul preference hints.")

        if info.get("error"):
            print(f"\n  \033[0;33mWarning:\033[0m {info['error']}")

        print()
        self.ctx.wait_for_enter()

    def _aredn_get_node_ip(self) -> str:
        import socket
        for host in ['localnode.local.mesh', '10.0.0.1', 'localnode']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))
                    if result == 0:
                        return host
                finally:
                    sock.close()
            except OSError as e:
                logger.debug("AREDN probe %s failed: %s", host, e)
                continue
        return ""

    def _aredn_node_status(self):
        clear_screen()
        print("=== AREDN Node Status ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            print("Check: src/utils/aredn.py")
            self.ctx.wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found on local network.")
                print("\nTried: localnode.local.mesh, 10.0.0.1")
                print("\nIs your AREDN node connected?")
                self.ctx.wait_for_enter()
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

        self.ctx.wait_for_enter()

    def _aredn_neighbors(self):
        clear_screen()
        print("=== AREDN Neighbors ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self.ctx.wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found. Is it connected?")
                self.ctx.wait_for_enter()
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

        self.ctx.wait_for_enter()

    def _aredn_services(self):
        clear_screen()
        print("=== AREDN Services ===\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self.ctx.wait_for_enter()
            return

        try:
            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found.")
                self.ctx.wait_for_enter()
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

        self.ctx.wait_for_enter()

    def _aredn_web(self):
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
        self.ctx.dialog.msgbox("AREDN Web UI", msg)

    def _aredn_scan(self):
        clear_screen()
        print("=== AREDN Network Scan ===\n")
        print("Scanning 10.0.0.0/24 for AREDN nodes...\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self.ctx.wait_for_enter()
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

        self.ctx.wait_for_enter()

    def _aredn_map(self):
        clear_screen()
        print("=== AREDN Network Map ===\n")

        node_ip = self._aredn_get_node_ip()
        if not node_ip:
            print("No AREDN node found on local network.\n")
            print("The map requires an AREDN node to be connected.")
            print("Tried: localnode.local.mesh, 10.0.0.1")
            self.ctx.wait_for_enter()
            return

        print(f"Connecting to AREDN node at {node_ip}...\n")

        if not _HAS_AREDN:
            print("AREDN utilities not available.")
            self.ctx.wait_for_enter()
            return

        try:
            node = get_aredn_node(node_ip)
            if not node:
                print("Could not retrieve node information.")
                self.ctx.wait_for_enter()
                return

            print(f"  Local Node: {node.hostname}")
            print(f"  Model:      {node.model}")

            if node.has_location():
                print(f"  Location:   {node.latitude:.6f}, {node.longitude:.6f}")
                if node.grid_square:
                    print(f"  Grid:       {node.grid_square}")
            else:
                print(f"  Location:   Not configured")
                print("\n  Note: Configure location on your AREDN node")
                print("  to see it on the map (Setup > Basic Setup > Location)")

            print(f"\n  Neighbors:  {len(node.links)}")

            neighbors_with_loc = 0
            print("\n  Checking neighbor locations...")
            for link in node.links[:5]:
                if link.ip:
                    try:
                        neighbor = get_aredn_node(link.ip)
                        if neighbor and neighbor.has_location():
                            neighbors_with_loc += 1
                            print(f"    + {neighbor.hostname} has location")
                    except Exception as e:
                        logger.debug("AREDN neighbor check failed: %s", e)

            if len(node.links) > 5:
                print(f"    ... and {len(node.links) - 5} more neighbors")

            print(f"\n  AREDN nodes on map: {1 if node.has_location() else 0} + {neighbors_with_loc} neighbors")

            print("\n" + "=" * 50)
            print("\nAREDN nodes are included in the unified MeshForge map.")
            print("The map shows Meshtastic, RNS, and AREDN nodes together.")
            print("\nTo view the map:")
            print("  1. Main Menu > Maps & Viz > Coverage Map")
            print("  2. Or start the map server and open in browser")

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

        self.ctx.wait_for_enter()
