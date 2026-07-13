"""
Network Tools Handler — Ping, DNS, discovery, port checks, mesh traceroute.

Converted from network_tools_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import re
import socket as sock
import subprocess
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.service_check import check_udp_port
from utils.safe_import import safe_import

# utils.automation_engine pulls the meshtastic protobuf stack (~200 ms);
# resolve it (and node_inventory) at menu time, never at TUI startup.


def _load_engine_fns():
    """Returns (get_automation_engine, validate_node_id) or (None, None)."""
    try:
        from utils.automation_engine import (
            get_automation_engine, validate_node_id,
        )
        return get_automation_engine, validate_node_id
    except ImportError:
        return None, None


def _load_node_inventory():
    """Returns the NodeInventory class, or None when unavailable."""
    try:
        from utils.node_inventory import NodeInventory
        return NodeInventory
    except ImportError:
        return None

logger = logging.getLogger(__name__)


class NetworkToolsHandler(BaseHandler):
    """TUI handler for network diagnostic tools."""

    handler_id = "network_tools"
    menu_section = "system"

    def menu_items(self):
        return [
            ("network", "Network Tools       Ping, ports, interfaces", None),
        ]

    def execute(self, action):
        if action == "network":
            self._network_menu()

    def _network_menu(self):
        while True:
            choices = [
                ("status", "Quick Network Status"),
                ("traceroute", "Mesh Traceroute"),
                ("ports", "Listening Ports (ss -tlnp)"),
                ("ifaces", "Network Interfaces (ip addr)"),
                ("conns", "Active Connections (ss -tunp)"),
                ("routes", "Routing Table (ip route)"),
                ("ping", "Ping Test"),
                ("dns", "DNS Lookup"),
                ("discover", "Meshtastic Device Discovery"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "Network & Ports",
                "Network diagnostics (terminal-native):",
                choices,
            )
            if choice is None or choice == "back":
                break
            dispatch = {
                "status": ("Network Status", self._run_terminal_network),
                "traceroute": ("Mesh Traceroute", self._mesh_traceroute),
                "ping": ("Ping Test", self._ping_test),
                "dns": ("DNS Lookup", self._dns_lookup),
                "discover": ("Device Discovery", self._meshtastic_discovery),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
                continue
            try:
                if choice == "ports":
                    clear_screen()
                    print("=== Listening Ports ===\n")
                    subprocess.run(['ss', '-tlnp'], timeout=10)
                    self.ctx.wait_for_enter()
                elif choice == "ifaces":
                    clear_screen()
                    print("=== Network Interfaces ===\n")
                    subprocess.run(['ip', '-c', 'addr'], timeout=10)
                    self.ctx.wait_for_enter()
                elif choice == "conns":
                    clear_screen()
                    print("=== Active Connections ===\n")
                    subprocess.run(['ss', '-tunp'], timeout=10)
                    self.ctx.wait_for_enter()
                elif choice == "routes":
                    clear_screen()
                    print("=== Routing Table ===\n")
                    subprocess.run(['ip', 'route'], timeout=10)
                    self.ctx.wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.ctx.dialog.msgbox(
                    "Network Tools Error",
                    f"Operation failed:\n{type(e).__name__}: {e}",
                )

    # --- Mesh Traceroute ---

    def _mesh_traceroute(self):
        """Mesh traceroute submenu — on-demand route tracing."""
        _get_automation_engine, _ = _load_engine_fns()
        if _get_automation_engine is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Automation engine not available.\n"
                "Mesh traceroute requires the automation module.",
                height=7, width=50,
            )
            return

        while True:
            choice = self.ctx.dialog.menu(
                "Mesh Traceroute",
                "Trace routes through the LoRa mesh network",
                choices=[
                    ("single", "Traceroute Single Node"),
                    ("all", "Traceroute All Active Nodes"),
                    ("history", "View Traceroute History"),
                    ("back", "Back"),
                ],
                height=12, width=52,
            )
            if choice is None or choice == "back":
                return

            if choice == "single":
                self.ctx.safe_call(
                    "Single Traceroute", self._traceroute_single
                )
            elif choice == "all":
                self.ctx.safe_call(
                    "Traceroute All", self._traceroute_all_active
                )
            elif choice == "history":
                self.ctx.safe_call(
                    "Traceroute History", self._traceroute_history
                )

    def _get_online_node_choices(self):
        """Build menu choices from online nodes in inventory."""
        _NodeInventory = _load_node_inventory()
        if _NodeInventory is None:
            return []
        try:
            inv = _NodeInventory()
            online = inv.get_online_nodes()
            choices = []
            for n in online[:20]:  # Cap at 20 nodes for menu
                label = n.short_name or n.long_name or n.node_id
                desc = n.node_id
                if n.short_name and n.long_name:
                    desc = f"{n.long_name} ({n.node_id})"
                choices.append((n.node_id, f"{label:<12} {desc}"))
            return choices
        except Exception as e:
            logger.debug(f"Could not get online nodes: {e}")
            return []

    def _traceroute_single(self):
        """Traceroute to a single node (pick from list or enter ID)."""
        get_engine, _ = _load_engine_fns()
        if get_engine is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "The automation engine module is not available.",
                height=6, width=50,
            )
            return
        try:
            engine = get_engine()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Error", f"Could not get automation engine:\n{e}",
                height=7, width=50,
            )
            return

        # Try to offer node selection from inventory
        node_choices = self._get_online_node_choices()
        node_id = None

        if node_choices:
            # Add manual entry option
            all_choices = list(node_choices) + [
                ("manual", "Enter node ID manually"),
            ]
            selected = self.ctx.dialog.menu(
                "Select Target",
                "Choose a node to traceroute (online nodes shown):",
                choices=all_choices,
                height=min(len(all_choices) + 8, 22),
                width=62,
            )
            if not selected:
                return
            if selected == "manual":
                node_id = None  # Will prompt below
            else:
                node_id = selected

        if not node_id:
            node_id = self.ctx.dialog.inputbox(
                "Traceroute Target",
                "Enter mesh node ID (e.g. !abc12345):",
                height=8, width=45,
            )
            if not node_id:
                return

        node_id = node_id.strip()

        # Validate node ID format
        _, _validate_node_id = _load_engine_fns()
        if _validate_node_id:
            if not _validate_node_id(node_id):
                self.ctx.dialog.msgbox(
                    "Invalid Node ID",
                    f"'{node_id}' is not a valid node ID.\n"
                    "Expected format: !hex (e.g. !abc12345)",
                    height=7, width=50,
                )
                return
        elif not re.match(r'^![0-9a-fA-F]{1,8}$', node_id):
            self.ctx.dialog.msgbox(
                "Invalid Node ID",
                f"'{node_id}' is not a valid node ID.\n"
                "Expected format: !hex (e.g. !abc12345)",
                height=7, width=50,
            )
            return

        self.ctx.dialog.infobox(
            "Tracing Route",
            f"Sending traceroute to {node_id}...\n"
            "This may take up to 60 seconds.",
        )

        result = engine.run_single_traceroute(node_id)
        self._display_traceroute_result(result)

    def _display_traceroute_result(self, result):
        """Display a single traceroute result in a formatted dialog."""
        name = f" ({result.node_name})" if result.node_name else ""
        ts = result.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"Traceroute to {result.node_id}{name}",
            "-" * 40,
        ]

        if result.success:
            lines.append(f"Status:  \u2713 SUCCESS ({result.hops} hops)")
            lines.append("")
            lines.append(f"Forward: {result.format_route()}")
            if result.route_back:
                lines.append(f"Return:  {result.format_return_route()}")
            if result.snr_towards:
                snr_strs = [f"{s:+.1f}dB" for s in result.snr_towards]
                lines.append(f"SNR:     {', '.join(snr_strs)}")
        else:
            lines.append(f"Status:  \u2717 FAILED")
            lines.append(f"Error:   {result.error or 'No response'}")

        if result.output and not result.route:
            lines.append("")
            lines.append("CLI Output:")
            for out_line in result.output.split("\n")[:8]:
                lines.append(f"  {out_line}")

        lines.append("")
        lines.append(f"Time: {ts}")

        self.ctx.dialog.msgbox(
            "Traceroute Result",
            "\n".join(lines),
            height=max(len(lines) + 4, 12),
            width=65,
        )

    def _traceroute_all_active(self):
        """Traceroute all active nodes from inventory."""
        _NodeInventory = _load_node_inventory()
        if _NodeInventory is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Node inventory not available.\n"
                "Cannot discover active nodes.",
                height=7, width=45,
            )
            return

        get_engine, _ = _load_engine_fns()
        if get_engine is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "The automation engine module is not available.",
                height=6, width=50,
            )
            return
        try:
            engine = get_engine()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Error", f"Could not get automation engine:\n{e}",
                height=7, width=50,
            )
            return

        try:
            inv = _NodeInventory()
            online = inv.get_online_nodes()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Error", f"Could not get node inventory:\n{e}",
                height=7, width=50,
            )
            return

        if not online:
            self.ctx.dialog.msgbox(
                "No Active Nodes",
                "No online nodes found in the inventory.\n"
                "Nodes must be seen recently to appear here.",
                height=7, width=50,
            )
            return

        if not self.ctx.dialog.yesno(
            "Traceroute All Active",
            f"Trace route to {len(online)} online node(s)?\n\n"
            "This will send a traceroute request to each node.\n"
            "Rate-limited to 1 per 5 seconds.\n\n"
            f"Estimated time: ~{len(online) * 10} seconds.",
        ):
            return

        clear_screen()
        print(f"=== Mesh Traceroute — {len(online)} Active Nodes ===\n")
        print(
            f"{'#':<4} {'Node':<14} {'Name':<12} {'Status':<8} "
            f"{'Hops':<6} {'Route'}"
        )
        print("-" * 72)

        successes = 0
        failures = 0

        for i, node in enumerate(online, 1):
            node_id = node.node_id
            name = (node.short_name or node.long_name or "")[:11]
            print(
                f"{i:<4} {node_id:<14} {name:<12} ",
                end="", flush=True,
            )

            result = engine.run_single_traceroute(node_id)

            if result.success:
                successes += 1
                route_str = result.format_route()
                if len(route_str) > 30:
                    route_str = route_str[:27] + "..."
                print(
                    f"\033[0;32mOK\033[0m     "
                    f"{result.hops:<6} {route_str}"
                )
            else:
                failures += 1
                err = (result.error or "timeout")[:30]
                print(f"\033[0;31mFAIL\033[0m   -      {err}")

        print()
        print("-" * 72)
        print(
            f"Complete: {successes} OK, {failures} failed, "
            f"{len(online)} total"
        )
        print("Results saved to traceroute history.")
        self.ctx.wait_for_enter()

    def _traceroute_history(self):
        """Quick view of recent traceroute history."""
        get_engine, _ = _load_engine_fns()
        if get_engine is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "The automation engine module is not available.",
                height=6, width=50,
            )
            return
        try:
            engine = get_engine()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Error", f"Could not get automation engine:\n{e}",
                height=7, width=50,
            )
            return

        store = engine.get_traceroute_store()
        results = store.get_recent(limit=30)

        if not results:
            self.ctx.dialog.msgbox(
                "No History",
                "No traceroute results recorded yet.\n"
                "Run a traceroute to generate history.",
                height=7, width=45,
            )
            return

        clear_screen()
        print("=== Recent Traceroute History ===\n")
        print(f"{'Time':<20} {'Node':<14} {'Status':<8} {'Hops':<6} {'Route'}")
        print("-" * 72)

        for r in results:
            ts = r.get("timestamp_dt", "")[:19]
            node = r.get("node_id", "?")[:13]
            name = r.get("node_name", "")
            if name:
                node = f"{name[:10]}"
            ok = r.get("success", False)
            status = "\033[0;32mOK\033[0m    " if ok else "\033[0;31mFAIL\033[0m  "
            hops = str(r.get("hops", 0)) if ok else "-"

            route_hops = r.get("route_json", [])
            if route_hops:
                route_str = " -> ".join(
                    f"!{h:08x}" if isinstance(h, int) else str(h)
                    for h in route_hops[:4]
                )
                if len(route_hops) > 4:
                    route_str += " ..."
            elif not ok:
                route_str = r.get("error", "")[:25]
            else:
                route_str = ""

            print(
                f"{ts:<20} {node:<14} {status} {hops:<6} {route_str}"
            )

        print(f"\nShowing {len(results)} most recent results")
        self.ctx.wait_for_enter()

    # --- Original tools ---

    def _ping_test(self):
        host = self.ctx.dialog.inputbox(
            "Ping Test", "Enter host to ping:", "8.8.8.8"
        )
        if not host:
            return
        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return
        self.ctx.dialog.infobox("Pinging", f"Pinging {host}...")
        try:
            result = subprocess.run(
                ['ping', '-c', '4', host],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout
            if 'transmitted' in output:
                stats_line = [
                    line for line in output.split('\n')
                    if 'transmitted' in line
                ]
                time_line = [
                    line for line in output.split('\n')
                    if 'rtt' in line or 'round-trip' in line
                ]
                text = f"Ping {host}:\n\n"
                if stats_line:
                    text += stats_line[0] + "\n"
                if time_line:
                    text += time_line[0]
                self.ctx.dialog.msgbox("Ping Results", text)
            else:
                self.ctx.dialog.msgbox("Ping Failed", output[:500])
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Ping timed out")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _meshtastic_discovery(self):
        self.ctx.dialog.infobox(
            "Discovery", "Scanning for Meshtastic devices..."
        )
        devices = []
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.settimeout(2)
                if s.connect_ex(('localhost', 4403)) == 0:
                    devices.append("TCP: localhost:4403 (meshtasticd)")
            finally:
                s.close()
        except Exception as e:
            logger.debug(f"Socket check for meshtasticd failed: {e}")
        serial_ports = (
            list(Path('/dev').glob('ttyUSB*'))
            + list(Path('/dev').glob('ttyACM*'))
        )
        for port in serial_ports:
            devices.append(f"Serial: {port}")
        if not devices:
            text = (
                "No Meshtastic devices found.\n\n"
                "Make sure meshtasticd is running."
            )
        else:
            devices.extend([
                "", "BLE devices require scanning:", "  meshtastic --ble-scan"
            ])
            text = "Found devices:\n\n" + "\n".join(devices)
        self.ctx.dialog.msgbox("Meshtastic Discovery", text)

    def _dns_lookup(self):
        host = self.ctx.dialog.inputbox(
            "DNS Lookup", "Enter hostname to lookup:", "meshtastic.org"
        )
        if not host:
            return
        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname.")
            return
        try:
            import socket
            results = []
            for info in socket.getaddrinfo(host, None):
                addr = info[4][0]
                if addr not in [
                    r.split(': ')[1] for r in results if ': ' in r
                ]:
                    family = (
                        "IPv4" if info[0] == socket.AF_INET else "IPv6"
                    )
                    results.append(f"{family}: {addr}")
            self.ctx.dialog.msgbox(
                f"DNS: {host}", "\n".join(results) or "No results"
            )
        except sock.gaierror as e:
            self.ctx.dialog.msgbox("Error", f"DNS lookup failed:\n{e}")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _run_terminal_network(self):
        clear_screen()
        print("MeshForge Network Status")
        print("=" * 50)
        print()
        print("Port Checks:")
        ports = [
            (4403, 'tcp', 'meshtasticd TCP API'),
            (9443, 'tcp', 'meshtasticd Web Client'),
            (37428, 'udp', 'rnsd (RNS shared instance)'),
            (1883, 'tcp', 'MQTT broker'),
        ]
        for port, proto, desc in ports:
            try:
                if proto == 'udp':
                    is_open = check_udp_port(port)
                else:
                    s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
                    try:
                        s.settimeout(1)
                        result = s.connect_ex(('127.0.0.1', port))
                    finally:
                        s.close()
                    is_open = (result == 0)
                if is_open:
                    print(f"  \033[0;32m●\033[0m {port:<6} {desc}")
                else:
                    print(
                        f"  \033[2m○\033[0m {port:<6} {desc} (not listening)"
                    )
            except OSError as e:
                logger.debug("Port %d check failed: %s", port, e)
                print(f"  ? {port:<6} {desc} (check failed)")
        print()
        try:
            s = sock.socket(sock.AF_INET, sock.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
            print(f"  Local IP: {local_ip}")
        except OSError as e:
            logger.debug("Local IP detection failed: %s", e)
            print("  Local IP: Unable to determine")
        print()
        print("Connectivity:")
        try:
            s = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
            try:
                s.settimeout(3)
                result = s.connect_ex(('8.8.8.8', 53))
            finally:
                s.close()
            if result == 0:
                print(f"  \033[0;32m●\033[0m Internet (Google DNS)")
            else:
                print(
                    f"  \033[0;31m●\033[0m Internet (no route to 8.8.8.8)"
                )
        except OSError as e:
            logger.debug("Internet connectivity check failed: %s", e)
            print(f"  \033[0;31m●\033[0m Internet (unreachable)")
        print()
        print("-" * 50)
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()
