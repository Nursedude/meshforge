"""System Tools Handler — Linux diagnostic tools for NOC operations.

Interactive monitoring, process management, and network diagnostics live here.
Hardware, performance, storage, service, and log tools are delegated to
``_system_hw_perf.py`` for file-size compliance.
"""

import subprocess
import shutil
import logging

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# Hardware, performance, storage, service, and log helpers (split for file-size compliance)
from handlers._system_hw_perf import (
    hardware_info_menu,
    performance_tools_menu,
    storage_tools_menu,
    service_management_menu,
    advanced_logs_menu,
)


class SystemToolsHandler(BaseHandler):
    """Comprehensive Linux diagnostic tools for NOC operations."""

    handler_id = "system_tools"
    menu_section = "system"

    def menu_items(self):
        return [
            ("shell", "Linux Shell         Drop to bash", None),
        ]

    def execute(self, action):
        if action == "shell":
            self._drop_to_shell()

    # --- Main System Tools Menu ---

    def _system_tools_menu(self):
        """Full Linux diagnostic tools menu - like being on the terminal."""
        choices = [
            ("monitor", "Interactive Monitoring (top/htop/btop)"),
            ("process", "Process Management"),
            ("network", "Network Diagnostics"),
            ("hardware", "Hardware Information"),
            ("performance", "Performance & Memory"),
            ("storage", "Storage & Disk"),
            ("services", "Service Management"),
            ("logs", "Advanced Log Analysis"),
            ("shell", "Drop to Shell"),
        ]
        dispatch = {
            "monitor": ("Interactive Monitoring", self._interactive_monitoring_menu),
            "process": ("Process Management", self._process_tools_menu),
            "network": ("Network Diagnostics", self._network_diagnostics_menu),
            "hardware": ("Hardware Information", self._hardware_info_menu),
            "performance": ("Performance Tools", self._performance_tools_menu),
            "storage": ("Storage Tools", self._storage_tools_menu),
            "services": ("Service Management", self._service_management_menu),
            "logs": ("Advanced Log Analysis", self._advanced_logs_menu),
            "shell": ("Drop to Shell", self._drop_to_shell),
        }
        self.run_menu_loop("System Tools", "Full Linux diagnostic capabilities:", choices, dispatch)

    # --- Interactive Monitoring (top, htop, btop) ---

    def _interactive_monitoring_menu(self):
        """Interactive system monitoring tools."""
        def _monitoring_choices():
            items = []
            if shutil.which('btop'):
                items.append(("btop", "btop (Best - Resource Monitor)"))
            if shutil.which('htop'):
                items.append(("htop", "htop (Interactive Process Viewer)"))
            items.append(("top", "top (Classic Process Viewer)"))
            if shutil.which('glances'):
                items.append(("glances", "glances (System Overview)"))
            if shutil.which('nmon'):
                items.append(("nmon", "nmon (Performance Monitor)"))
            items.extend([
                ("watch_ps", "watch ps (Auto-refresh processes)"),
                ("iotop", "iotop (I/O by Process)"),
            ])
            return items

        self.run_menu_loop(
            "Interactive Monitoring",
            "Real-time system monitoring (Ctrl+C to exit):",
            _monitoring_choices,
            default_handler=self._run_interactive_tool,
        )

    def _run_interactive_tool(self, tool: str):
        """Run an interactive monitoring tool."""
        clear_screen()

        tool_commands = {
            'btop': ['btop'],
            'htop': ['htop'],
            'top': ['top'],
            'glances': ['glances'],
            'nmon': ['nmon'],
            'watch_ps': ['watch', '-n', '2', 'ps', 'aux', '--sort=-%mem'],
            'iotop': ['sudo', 'iotop', '-o'],
        }

        cmd = tool_commands.get(tool)
        if not cmd:
            return

        # Check if tool exists
        if not shutil.which(cmd[0] if cmd[0] != 'sudo' else cmd[1]):
            self.ctx.dialog.msgbox(
                "Tool Not Found",
                f"'{tool}' is not installed.\n\n"
                f"Install with: sudo apt install {tool}\n"
                f"Or: sudo dnf install {tool}"
            )
            return

        print(f"=== Running {tool} (Ctrl+C to exit) ===\n")
        try:
            subprocess.run(cmd, timeout=None)
        except KeyboardInterrupt:
            print("\n\nStopped.")
        except FileNotFoundError:
            print(f"\n{tool} not found. Install it first.")
        except Exception as e:
            print(f"\nError: {e}")

        self.ctx.wait_for_enter()

    # --- Process Management ---

    def _process_tools_menu(self):
        """Process management tools."""
        choices = [
            ("ps_all", "ps aux (All Processes)"),
            ("ps_tree", "pstree (Process Tree)"),
            ("ps_mem", "ps (Sorted by Memory)"),
            ("ps_cpu", "ps (Sorted by CPU)"),
            ("ps_mesh", "Mesh-Related Processes"),
            ("lsof", "lsof (Open Files)"),
            ("lsof_net", "lsof -i (Network Connections)"),
            ("fuser", "fuser (Who's Using a Port)"),
        ]
        self.run_menu_loop(
            "Process Management", "View and manage processes:",
            choices, default_handler=self._run_process_command,
        )

    def _run_process_command(self, cmd_type: str):
        """Run process-related command."""
        clear_screen()

        commands = {
            'ps_all': (['ps', 'aux', '--forest'], "All Processes (ps aux --forest)"),
            'ps_tree': (['pstree', '-p'], "Process Tree (pstree -p)"),
            'ps_mem': (['ps', 'aux', '--sort=-%mem'], "Processes by Memory"),
            'ps_cpu': (['ps', 'aux', '--sort=-%cpu'], "Processes by CPU"),
            'ps_mesh': None,  # Special handling
            'lsof': (['lsof', '-n'], "Open Files (lsof)"),
            'lsof_net': (['lsof', '-i', '-P', '-n'], "Network Connections (lsof -i)"),
            'fuser': None,  # Special handling - needs port input
        }

        if cmd_type == 'ps_mesh':
            self._show_mesh_processes()
            return
        elif cmd_type == 'fuser':
            self._fuser_port_check()
            return

        cmd_info = commands.get(cmd_type)
        if not cmd_info:
            return

        cmd, title = cmd_info
        print(f"=== {title} ===\n")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            # Show first 100 lines
            lines = result.stdout.strip().split('\n')[:100]
            print('\n'.join(lines))
            if len(result.stdout.strip().split('\n')) > 100:
                print(f"\n... (truncated, {len(result.stdout.strip().split(chr(10)))} total lines)")
        except FileNotFoundError:
            print("Command not found. Install required package.")
        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    def _show_mesh_processes(self):
        """Show mesh-related processes."""
        clear_screen()
        print("=== Mesh-Related Processes ===\n")

        patterns = ['meshtastic', 'rnsd', 'lxmf', 'nomadnet', 'meshforge', 'python.*mesh']

        try:
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                timeout=10
            )

            # Header
            lines = result.stdout.strip().split('\n')
            if lines and lines[0]:
                print(lines[0])  # Header
                print("-" * 80)
            else:
                print("No process information available")
                print("-" * 80)

            found = False
            for line in lines[1:]:
                for pattern in patterns:
                    if pattern.replace('.*', '') in line.lower():
                        print(line)
                        found = True
                        break

            if not found:
                print("\nNo mesh-related processes found.")

        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    def _fuser_port_check(self):
        """Check what's using a specific port."""
        port = self.ctx.dialog.inputbox(
            "Port Check",
            "Enter port number to check:",
            "4403"
        )

        if not port:
            return

        # Validate port is a valid number
        try:
            port_num = int(port.strip())
            if not (1 <= port_num <= 65535):
                raise ValueError
            port = str(port_num)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Port must be a number between 1 and 65535")
            return

        clear_screen()
        print(f"=== Who's Using Port {port}? ===\n")

        try:
            # Try fuser
            result = subprocess.run(
                ['fuser', '-v', f'{port}/tcp'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout or result.stderr:
                print("fuser output:")
                print(result.stdout + result.stderr)
            else:
                print(f"No process found using port {port}")

            # Also try ss
            print("\nss output:")
            result = subprocess.run(
                ['ss', '-tlnp', f'sport = :{port}'],
                capture_output=True,
                text=True,
                timeout=10
            )
            print(result.stdout if result.stdout else f"No listeners on port {port}")

        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    # --- Network Diagnostics ---

    def _network_diagnostics_menu(self):
        """Comprehensive network diagnostics."""
        choices = [
            ("tcp_monitor", "TCP Monitor (Meshtasticd Connections)"),
            ("network_scan", "Discover Meshtasticd Devices"),
            ("ss", "ss -tuln (Listening Ports)"),
            ("ss_all", "ss -tunap (All Connections)"),
            ("netstat", "netstat -an (Legacy - All)"),
            ("ip_addr", "ip addr (IP Addresses)"),
            ("ip_route", "ip route (Routing Table)"),
            ("ip_link", "ip link (Interface Status)"),
            ("arp", "arp -a (ARP Table)"),
            ("dns", "DNS Lookup"),
            ("traceroute", "Traceroute"),
            ("ping", "Ping Test"),
            ("iptables", "iptables -L (Firewall Rules)"),
            ("nft", "nft list ruleset (nftables)"),
            ("wifi", "WiFi Status (iwconfig/iw)"),
        ]
        self.run_menu_loop(
            "Network Diagnostics", "Network troubleshooting tools:",
            choices, default_handler=self._run_network_command,
        )

    def _run_network_command(self, cmd_type: str):
        """Run network diagnostic command."""
        clear_screen()

        simple_commands = {
            'ss': (['ss', '-tuln'], "Listening Ports (ss -tuln)"),
            'ss_all': (['ss', '-tunap'], "All Connections (ss -tunap)"),
            'netstat': (['netstat', '-an'], "Network Statistics (netstat -an)"),
            'ip_addr': (['ip', 'addr'], "IP Addresses"),
            'ip_route': (['ip', 'route'], "Routing Table"),
            'ip_link': (['ip', '-s', 'link'], "Interface Statistics"),
            'arp': (['arp', '-a'], "ARP Table"),
            'iptables': (['sudo', 'iptables', '-L', '-n', '-v'], "Firewall Rules (iptables)"),
            'nft': (['sudo', 'nft', 'list', 'ruleset'], "nftables Rules"),
        }

        if cmd_type in simple_commands:
            cmd, title = simple_commands[cmd_type]
            print(f"=== {title} ===\n")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                print(result.stdout)
                if result.stderr:
                    print(f"stderr: {result.stderr}")
            except FileNotFoundError:
                print("Command not found.")
            except Exception as e:
                print(f"Error: {e}")

            print("\n" + "=" * 60)
            self.ctx.wait_for_enter()

        elif cmd_type == 'dns':
            self._dns_lookup()
        elif cmd_type == 'traceroute':
            self._traceroute()
        elif cmd_type == 'ping':
            self._ping_test()
        elif cmd_type == 'wifi':
            self._wifi_status()
        elif cmd_type == 'tcp_monitor':
            self._tcp_monitor_view()
        elif cmd_type == 'network_scan':
            self._network_scan_view()

    def _dns_lookup(self):
        """Perform DNS lookup."""
        host = self.ctx.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        clear_screen()
        print(f"=== DNS Lookup: {host} ===\n")

        # Try multiple DNS tools
        tools = [
            (['dig', host, '+short'], "dig"),
            (['nslookup', host], "nslookup"),
            (['host', host], "host"),
        ]

        for cmd, name in tools:
            if shutil.which(cmd[0]):
                print(f"\n--- {name} ---")
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    print(result.stdout)
                except Exception as e:
                    print(f"Error: {e}")
                break
        else:
            # Fallback to Python
            import socket
            try:
                ip = socket.gethostbyname(host)
                print(f"Resolved to: {ip}")
            except Exception as e:
                print(f"Resolution failed: {e}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    def _traceroute(self):
        """Run traceroute to a host."""
        host = self.ctx.dialog.inputbox(
            "Traceroute",
            "Enter destination host:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        clear_screen()
        print(f"=== Traceroute to {host} ===\n")
        print("(Ctrl+C to stop)\n")

        # Try traceroute, then tracepath, then mtr
        for cmd in [['traceroute', host], ['tracepath', host], ['mtr', '-r', '-c', '3', host]]:
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, timeout=60)
                except KeyboardInterrupt:
                    print("\n\nStopped.")
                except Exception as e:
                    print(f"Error: {e}")
                break
        else:
            print("No traceroute tool found. Install: sudo apt install traceroute")

        self.ctx.wait_for_enter()

    def _ping_test(self):
        """Interactive ping test."""
        host = self.ctx.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        count = self.ctx.dialog.inputbox(
            "Ping Count",
            "Number of pings (0 for continuous):",
            "5"
        )

        try:
            count = int(count) if count else 5
        except ValueError:
            count = 5

        clear_screen()
        print(f"=== Ping {host} ===\n")

        cmd = ['ping', host]
        if count > 0:
            cmd.extend(['-c', str(count)])

        try:
            subprocess.run(cmd, timeout=None if count == 0 else count * 5)
        except KeyboardInterrupt:
            print("\n\nStopped.")
        except Exception as e:
            print(f"Error: {e}")

        self.ctx.wait_for_enter()

    def _wifi_status(self):
        """Show WiFi status."""
        clear_screen()
        print("=== WiFi Status ===\n")

        # Try iw first (modern), then iwconfig (legacy)
        if shutil.which('iw'):
            print("--- iw dev ---")
            subprocess.run(['iw', 'dev'], timeout=10)
            print("\n--- iw dev wlan0 info ---")
            subprocess.run(['iw', 'dev', 'wlan0', 'info'], capture_output=False, timeout=10)
            print("\n--- iw dev wlan0 station dump ---")
            subprocess.run(['iw', 'dev', 'wlan0', 'station', 'dump'], capture_output=False, timeout=10)
        elif shutil.which('iwconfig'):
            print("--- iwconfig ---")
            subprocess.run(['iwconfig'], timeout=10)
        else:
            print("No WiFi tools found. Install: sudo apt install iw")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    @staticmethod
    def _print_conn_table(title, conns, empty_msg, limit=0, port_key="remote_port"):
        """Print a formatted connection table."""
        print(f"--- {title} ---")
        if not conns:
            print(f"  {empty_msg}")
            print()
            return
        print(f"{'Remote Address':<20} {'Port':<8} {'State':<15} {'Process':<20}")
        print("-" * 65)
        display = conns[:limit] if limit else conns
        for conn in display:
            remote = conn["remote_addr"]
            port = conn[port_key] if conn[port_key] != 4403 else conn["local_port"]
            print(f"{remote:<20} {port:<8} {conn['state'].value:<15} "
                  f"{conn.get('process_name') or 'unknown':<20}")
        if limit and len(conns) > limit:
            print(f"  ... and {len(conns) - limit} more")
        print()

    def _tcp_monitor_view(self):
        """Display TCP connections related to Meshtastic."""
        clear_screen()
        print("=== TCP Connection Monitor ===\n")
        print("Monitoring connections to meshtasticd (port 4403) and web interfaces\n")
        try:
            from monitoring.tcp_monitor import TCPMonitor, TCPState
        except ImportError:
            print("TCP Monitor not available.")
            self.ctx.wait_for_enter()
            return

        connections = TCPMonitor()._get_tcp_connections()
        mesh = [c for c in connections if 4403 in (c["local_port"], c["remote_port"])]
        web = [c for c in connections
               if any(p in (c["local_port"], c["remote_port"]) for p in (80, 443, 8080))
               and c not in mesh]
        other = [c for c in connections if c not in mesh and c not in web]

        self._print_conn_table("Meshtasticd Connections (port 4403)",
                               mesh, "No active meshtasticd connections")
        self._print_conn_table("Web Interface Connections (ports 80, 443, 8080)",
                               web, "No active web connections", limit=10)

        print("--- Summary ---")
        state_counts = {}
        for conn in connections:
            s = conn["state"].value
            state_counts[s] = state_counts.get(s, 0) + 1
        print(f"Total: {len(connections)}  Meshtasticd: {len(mesh)}  "
              f"Web: {len(web)}  Other: {len(other)}")
        for state, count in sorted(state_counts.items()):
            print(f"  {state}: {count}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    def _network_scan_view(self):
        """Scan network for meshtasticd devices."""
        clear_screen()
        print("=== Network Device Discovery ===\n")
        try:
            from monitoring.tcp_monitor import NetworkScanner
        except ImportError:
            print("Network Scanner not available.")
            self.ctx.wait_for_enter()
            return

        subnet = self.ctx.dialog.inputbox(
            "Network Scan",
            "Enter subnet to scan (CIDR notation):\n\n"
            "Examples: 192.168.1.0/24, 10.0.0.0/24\n"
            "Leave blank for auto-detect", ""
        )

        clear_screen()
        print("=== Scanning Network ===\n")
        scanner = NetworkScanner(timeout=1.0, max_threads=50)

        def on_progress(current, total):
            pct = (current / total) * 100
            filled = int(40 * current / total)
            bar = "=" * filled + "-" * (40 - filled)
            print(f"\rProgress: [{bar}] {pct:.0f}% ({current}/{total})", end="", flush=True)
        scanner.on_progress = on_progress

        try:
            devices = scanner.scan_subnet(subnet) if subnet else scanner.scan_local_network()
        except Exception as e:
            print(f"\nError scanning network: {e}")
            self.ctx.wait_for_enter()
            return

        print("\n\n")
        if not devices:
            print("No devices with open Meshtastic ports found.")
            print("\nNote: Devices must have port 4403 (meshtasticd) or web ports open.")
        else:
            mesh_devs = [d for d in devices if d.is_meshtasticd]
            web_devs = [d for d in devices if d.is_web_enabled and not d.is_meshtasticd]
            if mesh_devs:
                print("--- Meshtasticd Devices (port 4403) ---")
                print(f"{'IP Address':<18} {'Hostname':<30} {'Response':<12}")
                print("-" * 60)
                for d in mesh_devs:
                    hn = d.hostname or "(no hostname)"
                    rt = f"{d.response_time_ms:.1f}ms" if d.response_time_ms else "N/A"
                    print(f"{d.ip_address:<18} {hn:<30} {rt:<12}")
                print()
            if web_devs:
                print("--- Web-Enabled Devices ---")
                print(f"{'IP Address':<18} {'Hostname':<30} {'Ports':<20}")
                print("-" * 70)
                for d in web_devs:
                    hn = d.hostname or "(no hostname)"
                    print(f"{d.ip_address:<18} {hn:<30} {', '.join(str(p) for p in d.ports):<20}")
                print()
            print(f"Total: {len(devices)}  Meshtasticd: {len(mesh_devs)}  Web: {len(web_devs)}")

        print("\n" + "=" * 60)
        self.ctx.wait_for_enter()

    # --- Hardware, Performance, Storage, Service, Logs (delegated) ---

    def _hardware_info_menu(self):
        """Hardware information tools (delegated to _system_hw_perf)."""
        hardware_info_menu(self)

    def _performance_tools_menu(self):
        """Performance monitoring tools (delegated to _system_hw_perf)."""
        performance_tools_menu(self)

    def _storage_tools_menu(self):
        """Storage and disk tools (delegated to _system_hw_perf)."""
        storage_tools_menu(self)

    def _service_management_menu(self):
        """SystemD service management (delegated to _system_hw_perf)."""
        service_management_menu(self)

    def _advanced_logs_menu(self):
        """Advanced log analysis tools (delegated to _system_hw_perf)."""
        advanced_logs_menu(self)

    # --- Drop to Shell ---

    def _drop_to_shell(self):
        """Drop to an interactive shell."""
        self.ctx.dialog.msgbox(
            "Shell Access",
            "Dropping to shell...\n\n"
            "Type 'exit' to return to MeshForge.\n\n"
            "Useful commands:\n"
            "  meshtastic --info\n"
            "  rnstatus\n"
            "  journalctl -f\n"
            "  systemctl status meshtasticd"
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
