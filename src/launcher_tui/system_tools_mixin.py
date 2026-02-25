"""
System Tools Mixin - Comprehensive Linux Diagnostic Tools

Provides full Linux terminal-like diagnostic capabilities:
- Interactive monitoring (top, htop, btop)
- Process management (ps, pstree, lsof, kill)
- Network diagnostics (netstat, ss, ip, traceroute, nslookup)
- Hardware info (lsusb, lspci, lscpu, lsblk)
- Performance tools (vmstat, iostat, free, sar)
- Log analysis (journalctl, dmesg, syslog)
"""

import subprocess
import shutil
from pathlib import Path
from typing import Optional

import logging
from backend import clear_screen
logger = logging.getLogger(__name__)

# Import centralized service checker - SINGLE SOURCE OF TRUTH
from utils.service_check import check_service, check_port, ServiceState, apply_config_and_restart

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home


class SystemToolsMixin:
    """Comprehensive Linux diagnostic tools for NOC operations."""

    # =========================================================================
    # Main System Tools Menu
    # =========================================================================

    def _system_tools_menu(self):
        """Full Linux diagnostic tools menu - like being on the terminal."""
        while True:
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
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "System Tools",
                "Full Linux diagnostic capabilities:",
                choices
            )

            if choice is None or choice == "back":
                break

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
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # =========================================================================
    # Interactive Monitoring (top, htop, btop)
    # =========================================================================

    def _interactive_monitoring_menu(self):
        """Interactive system monitoring tools."""
        # Detect available tools
        has_htop = shutil.which('htop') is not None
        has_btop = shutil.which('btop') is not None
        has_glances = shutil.which('glances') is not None
        has_nmon = shutil.which('nmon') is not None

        while True:
            choices = []

            # Prefer btop > htop > top
            if has_btop:
                choices.append(("btop", "btop (Best - Resource Monitor)"))
            if has_htop:
                choices.append(("htop", "htop (Interactive Process Viewer)"))
            choices.append(("top", "top (Classic Process Viewer)"))

            if has_glances:
                choices.append(("glances", "glances (System Overview)"))
            if has_nmon:
                choices.append(("nmon", "nmon (Performance Monitor)"))

            choices.extend([
                ("watch_ps", "watch ps (Auto-refresh processes)"),
                ("iotop", "iotop (I/O by Process)"),
                ("back", "Back"),
            ])

            choice = self.dialog.menu(
                "Interactive Monitoring",
                "Real-time system monitoring (Ctrl+C to exit):",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Monitor: {choice}", self._run_interactive_tool, choice)

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
            self.dialog.msgbox(
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

        self._wait_for_enter()

    # =========================================================================
    # Process Management
    # =========================================================================

    def _process_tools_menu(self):
        """Process management tools."""
        while True:
            choices = [
                ("ps_all", "ps aux (All Processes)"),
                ("ps_tree", "pstree (Process Tree)"),
                ("ps_mem", "ps (Sorted by Memory)"),
                ("ps_cpu", "ps (Sorted by CPU)"),
                ("ps_mesh", "Mesh-Related Processes"),
                ("lsof", "lsof (Open Files)"),
                ("lsof_net", "lsof -i (Network Connections)"),
                ("fuser", "fuser (Who's Using a Port)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Process Management",
                "View and manage processes:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Process: {choice}", self._run_process_command, choice)

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
            print(f"Command not found. Install required package.")
        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        self._wait_for_enter()

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
        self._wait_for_enter()

    def _fuser_port_check(self):
        """Check what's using a specific port."""
        port = self.dialog.inputbox(
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
            self.dialog.msgbox("Error", "Port must be a number between 1 and 65535")
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
        self._wait_for_enter()

    # =========================================================================
    # Network Diagnostics
    # =========================================================================

    def _network_diagnostics_menu(self):
        """Comprehensive network diagnostics."""
        while True:
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
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Network Diagnostics",
                "Network troubleshooting tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Network: {choice}", self._run_network_command, choice)

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
            self._wait_for_enter()

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
        host = self.dialog.inputbox(
            "DNS Lookup",
            "Enter hostname to lookup:",
            "meshtastic.org"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
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
        self._wait_for_enter()

    def _traceroute(self):
        """Run traceroute to a host."""
        host = self.dialog.inputbox(
            "Traceroute",
            "Enter destination host:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
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

        self._wait_for_enter()

    def _ping_test(self):
        """Interactive ping test."""
        host = self.dialog.inputbox(
            "Ping Test",
            "Enter host to ping:",
            "8.8.8.8"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        count = self.dialog.inputbox(
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

        self._wait_for_enter()

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
        self._wait_for_enter()

    def _tcp_monitor_view(self):
        """Display TCP connections related to Meshtastic."""
        clear_screen()
        print("=== TCP Connection Monitor ===\n")
        print("Monitoring connections to meshtasticd (port 4403) and web interfaces\n")

        try:
            from monitoring.tcp_monitor import TCPMonitor, TCPState
        except ImportError:
            print("TCP Monitor not available.")
            print("Check that the monitoring module is installed correctly.")
            self._wait_for_enter()
            return

        monitor = TCPMonitor()
        connections = monitor._get_tcp_connections()

        # Group by type
        meshtasticd_conns = []
        web_conns = []
        other_conns = []

        for conn in connections:
            if 4403 in (conn["local_port"], conn["remote_port"]):
                meshtasticd_conns.append(conn)
            elif any(p in (conn["local_port"], conn["remote_port"]) for p in (80, 443, 8080)):
                web_conns.append(conn)
            else:
                other_conns.append(conn)

        # Display meshtasticd connections
        print("--- Meshtasticd Connections (port 4403) ---")
        if meshtasticd_conns:
            print(f"{'Remote Address':<20} {'Port':<8} {'State':<15} {'Process':<20}")
            print("-" * 65)
            for conn in meshtasticd_conns:
                remote = conn["remote_addr"]
                port = conn["remote_port"] if conn["remote_port"] != 4403 else conn["local_port"]
                state = conn["state"].value
                proc = conn.get("process_name", "unknown") or "unknown"
                print(f"{remote:<20} {port:<8} {state:<15} {proc:<20}")
        else:
            print("  No active meshtasticd connections")
        print()

        # Display web connections
        print("--- Web Interface Connections (ports 80, 443, 8080) ---")
        if web_conns:
            print(f"{'Remote Address':<20} {'Port':<8} {'State':<15} {'Process':<20}")
            print("-" * 65)
            for conn in web_conns[:10]:  # Limit to 10
                remote = conn["remote_addr"]
                port = conn["remote_port"]
                state = conn["state"].value
                proc = conn.get("process_name", "unknown") or "unknown"
                print(f"{remote:<20} {port:<8} {state:<15} {proc:<20}")
            if len(web_conns) > 10:
                print(f"  ... and {len(web_conns) - 10} more")
        else:
            print("  No active web connections")
        print()

        # Summary
        print("--- Summary ---")
        state_counts = {}
        for conn in connections:
            state = conn["state"].value
            state_counts[state] = state_counts.get(state, 0) + 1

        print(f"Total connections: {len(connections)}")
        print(f"  Meshtasticd: {len(meshtasticd_conns)}")
        print(f"  Web: {len(web_conns)}")
        print(f"  Other: {len(other_conns)}")
        print()
        print("Connection states:")
        for state, count in sorted(state_counts.items()):
            print(f"  {state}: {count}")

        print("\n" + "=" * 60)
        self._wait_for_enter()

    def _network_scan_view(self):
        """Scan network for meshtasticd devices."""
        clear_screen()
        print("=== Network Device Discovery ===\n")
        print("Scanning for Meshtastic devices on the local network...\n")

        try:
            from monitoring.tcp_monitor import NetworkScanner
        except ImportError:
            print("Network Scanner not available.")
            print("Check that the monitoring module is installed correctly.")
            self._wait_for_enter()
            return

        # Get subnet to scan
        subnet = self.dialog.inputbox(
            "Network Scan",
            "Enter subnet to scan (CIDR notation):\n\n"
            "Examples:\n"
            "  192.168.1.0/24 (256 hosts)\n"
            "  10.0.0.0/24 (256 hosts)\n"
            "  Leave blank for auto-detect",
            ""
        )

        clear_screen()
        print("=== Scanning Network ===\n")

        scanner = NetworkScanner(timeout=1.0, max_threads=50)

        # Progress callback
        def on_progress(current, total):
            pct = (current / total) * 100
            bar_len = 40
            filled = int(bar_len * current / total)
            bar = "=" * filled + "-" * (bar_len - filled)
            print(f"\rProgress: [{bar}] {pct:.0f}% ({current}/{total})", end="", flush=True)

        scanner.on_progress = on_progress

        try:
            if subnet:
                devices = scanner.scan_subnet(subnet)
            else:
                devices = scanner.scan_local_network()
        except Exception as e:
            print(f"\nError scanning network: {e}")
            self._wait_for_enter()
            return

        print("\n\n")

        if not devices:
            print("No devices with open Meshtastic ports found.")
            print("\nNote: Devices must have port 4403 (meshtasticd) or web ports open.")
        else:
            # Show meshtasticd devices first
            meshtasticd_devices = [d for d in devices if d.is_meshtasticd]
            web_devices = [d for d in devices if d.is_web_enabled and not d.is_meshtasticd]

            if meshtasticd_devices:
                print("--- Meshtasticd Devices (port 4403) ---")
                print(f"{'IP Address':<18} {'Hostname':<30} {'Response':<12}")
                print("-" * 60)
                for dev in meshtasticd_devices:
                    hostname = dev.hostname or "(no hostname)"
                    response = f"{dev.response_time_ms:.1f}ms" if dev.response_time_ms else "N/A"
                    print(f"{dev.ip_address:<18} {hostname:<30} {response:<12}")
                print()

            if web_devices:
                print("--- Web-Enabled Devices ---")
                print(f"{'IP Address':<18} {'Hostname':<30} {'Ports':<20}")
                print("-" * 70)
                for dev in web_devices:
                    hostname = dev.hostname or "(no hostname)"
                    ports = ", ".join(f"{p}" for p in dev.ports.keys())
                    print(f"{dev.ip_address:<18} {hostname:<30} {ports:<20}")
                print()

            print(f"Total devices found: {len(devices)}")
            print(f"  With meshtasticd: {len(meshtasticd_devices)}")
            print(f"  With web interface: {len(web_devices)}")

        print("\n" + "=" * 60)
        self._wait_for_enter()

    # =========================================================================
    # Hardware Information
    # =========================================================================

    def _hardware_info_menu(self):
        """Hardware information tools."""
        while True:
            choices = [
                ("lscpu", "lscpu (CPU Info)"),
                ("lsmem", "lsmem (Memory Layout)"),
                ("lsusb", "lsusb (USB Devices)"),
                ("lsusb_v", "lsusb -v (USB Verbose)"),
                ("lspci", "lspci (PCI Devices)"),
                ("lsblk", "lsblk (Block Devices)"),
                ("lshw", "lshw (Full Hardware Summary)"),
                ("dmidecode", "dmidecode (BIOS/System Info)"),
                ("sensors", "sensors (Temperature/Voltage)"),
                ("gpio", "GPIO Status (Pi/SBC)"),
                ("spi_i2c", "SPI/I2C Status"),
                ("uname", "uname -a (Kernel Info)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Hardware Information",
                "System hardware details:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Hardware: {choice}", self._run_hardware_command, choice)

    def _run_hardware_command(self, cmd_type: str):
        """Run hardware info command."""
        clear_screen()

        simple_commands = {
            'lscpu': (['lscpu'], "CPU Information"),
            'lsmem': (['lsmem'], "Memory Layout"),
            'lsusb': (['lsusb'], "USB Devices"),
            'lsusb_v': (['lsusb', '-v'], "USB Devices (Verbose)"),
            'lspci': (['lspci', '-v'], "PCI Devices"),
            'lsblk': (['lsblk', '-f'], "Block Devices"),
            'lshw': (['sudo', 'lshw', '-short'], "Hardware Summary"),
            'dmidecode': (['sudo', 'dmidecode', '-t', 'system'], "System Info (BIOS)"),
            'sensors': (['sensors'], "Sensors"),
            'uname': (['uname', '-a'], "Kernel Info"),
        }

        if cmd_type in simple_commands:
            cmd, title = simple_commands[cmd_type]
            print(f"=== {title} ===\n")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                print(result.stdout)
                if result.stderr and 'not found' in result.stderr.lower():
                    print(f"\nTool not found. Install required package.")
            except FileNotFoundError:
                print("Command not found. Install required package.")
            except Exception as e:
                print(f"Error: {e}")

            print("\n" + "=" * 60)
            self._wait_for_enter()

        elif cmd_type == 'gpio':
            self._gpio_status()
        elif cmd_type == 'spi_i2c':
            self._spi_i2c_status()

    def _gpio_status(self):
        """Show GPIO status for Pi/SBC."""
        clear_screen()
        print("=== GPIO Status ===\n")

        # Try raspi-gpio first, then gpioinfo
        if shutil.which('raspi-gpio'):
            print("--- raspi-gpio get ---")
            subprocess.run(['raspi-gpio', 'get'], timeout=10)
        elif shutil.which('gpioinfo'):
            print("--- gpioinfo ---")
            subprocess.run(['gpioinfo'], timeout=10)
        elif Path('/sys/class/gpio').exists():
            print("--- /sys/class/gpio ---")
            for gpio_dir in Path('/sys/class/gpio').glob('gpio*'):
                if gpio_dir.is_dir() and gpio_dir.name.startswith('gpio') and gpio_dir.name[4:].isdigit():
                    try:
                        direction = (gpio_dir / 'direction').read_text().strip()
                        value = (gpio_dir / 'value').read_text().strip()
                        print(f"{gpio_dir.name}: direction={direction}, value={value}")
                    except OSError as e:
                        logger.debug("GPIO %s read failed: %s", gpio_dir.name, e)
        else:
            print("No GPIO interface found.")

        print("\n" + "=" * 60)
        self._wait_for_enter()

    def _spi_i2c_status(self):
        """Show SPI and I2C status."""
        clear_screen()
        print("=== SPI/I2C Status ===\n")

        # SPI
        print("--- SPI Devices ---")
        spi_devs = list(Path('/dev').glob('spidev*'))
        if spi_devs:
            for dev in spi_devs:
                print(f"  {dev}")
        else:
            print("  No SPI devices found (enable with raspi-config or modprobe)")

        # I2C
        print("\n--- I2C Devices ---")
        i2c_devs = list(Path('/dev').glob('i2c-*'))
        if i2c_devs:
            for dev in i2c_devs:
                print(f"  {dev}")
                # Try i2cdetect
                if shutil.which('i2cdetect'):
                    bus = dev.name.split('-')[1]
                    print(f"    Scanning bus {bus}...")
                    result = subprocess.run(
                        ['i2cdetect', '-y', bus],
                        capture_output=True, text=True, timeout=10
                    )
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            print(f"    {line}")
        else:
            print("  No I2C devices found")

        print("\n" + "=" * 60)
        self._wait_for_enter()

    # =========================================================================
    # Performance Tools
    # =========================================================================

    def _performance_tools_menu(self):
        """Performance monitoring tools."""
        while True:
            choices = [
                ("free", "free -h (Memory Usage)"),
                ("vmstat", "vmstat (Virtual Memory Stats)"),
                ("vmstat_live", "vmstat 1 (Live - 1s interval)"),
                ("iostat", "iostat (I/O Statistics)"),
                ("mpstat", "mpstat (CPU per Core)"),
                ("uptime", "uptime (Load Average)"),
                ("sar", "sar (System Activity)"),
                ("stress", "Stress Test (careful!)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Performance Tools",
                "System performance analysis:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Performance: {choice}", self._run_performance_command, choice)

    def _run_performance_command(self, cmd_type: str):
        """Run performance monitoring command."""
        clear_screen()

        simple_commands = {
            'free': (['free', '-h'], "Memory Usage"),
            'vmstat': (['vmstat', '-w'], "Virtual Memory Stats"),
            'iostat': (['iostat', '-x', '1', '3'], "I/O Statistics"),
            'mpstat': (['mpstat', '-P', 'ALL'], "CPU per Core"),
            'uptime': (['uptime'], "System Uptime & Load"),
            'sar': (['sar', '-u', '1', '5'], "CPU Activity (5 samples)"),
        }

        if cmd_type in simple_commands:
            cmd, title = simple_commands[cmd_type]
            print(f"=== {title} ===\n")

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                print(result.stdout)
            except FileNotFoundError:
                print(f"Command not found. Install: sudo apt install sysstat")
            except Exception as e:
                print(f"Error: {e}")

            print("\n" + "=" * 60)
            self._wait_for_enter()

        elif cmd_type == 'vmstat_live':
            print("=== vmstat 1 (Live - Ctrl+C to stop) ===\n")
            try:
                subprocess.run(['vmstat', '1'], timeout=None)
            except KeyboardInterrupt:
                print("\n\nStopped.")
            self._wait_for_enter()

        elif cmd_type == 'stress':
            self._stress_test_menu()

    def _stress_test_menu(self):
        """CPU/Memory stress test (careful!)."""
        confirm = self.dialog.yesno(
            "Stress Test",
            "WARNING: This will stress your system!\n\n"
            "Only use for testing stability.\n"
            "System may become unresponsive.\n\n"
            "Continue?"
        )

        if not confirm:
            return

        if not shutil.which('stress'):
            self.dialog.msgbox(
                "Not Installed",
                "stress tool not found.\n\n"
                "Install: sudo apt install stress"
            )
            return

        duration = self.dialog.inputbox(
            "Duration",
            "Stress test duration in seconds:",
            "10"
        )

        try:
            duration = int(duration) if duration else 10
        except ValueError:
            duration = 10

        clear_screen()
        print(f"=== Stress Test ({duration}s) ===\n")
        print("Running CPU stress test...\n")

        try:
            subprocess.run(
                ['stress', '--cpu', '2', '--timeout', str(duration)],
                timeout=duration + 10
            )
            print("\nStress test completed!")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    # =========================================================================
    # Storage Tools
    # =========================================================================

    def _storage_tools_menu(self):
        """Storage and disk tools."""
        while True:
            choices = [
                ("df", "df -h (Disk Free Space)"),
                ("du_home", "du (Home Directory Usage)"),
                ("du_etc", "du /etc/meshtasticd (Config Size)"),
                ("mount", "mount (Mounted Filesystems)"),
                ("findmnt", "findmnt (Mount Tree)"),
                ("fdisk", "fdisk -l (Partition Table)"),
                ("smartctl", "smartctl (Disk Health)"),
                ("ncdu", "ncdu (Interactive Disk Usage)"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Storage Tools",
                "Disk and storage analysis:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Storage: {choice}", self._run_storage_command, choice)

    def _run_storage_command(self, cmd_type: str):
        """Run storage command."""
        clear_screen()

        if cmd_type == 'df':
            print("=== Disk Free Space ===\n")
            subprocess.run(['df', '-h'], timeout=10)

        elif cmd_type == 'du_home':
            print("=== Home Directory Usage (top 20) ===\n")
            try:
                result = subprocess.run(
                    ['du', '-h', '--max-depth=1', str(get_real_user_home())],
                    capture_output=True, text=True, timeout=60
                )
                lines = sorted(result.stdout.strip().split('\n'), key=lambda x: x.split()[0] if x else '0')
                for line in lines[-20:]:
                    print(line)
            except Exception as e:
                print(f"Error: {e}")

        elif cmd_type == 'du_etc':
            print("=== /etc/meshtasticd Size ===\n")
            subprocess.run(['du', '-ah', '/etc/meshtasticd'], timeout=30)

        elif cmd_type == 'mount':
            print("=== Mounted Filesystems ===\n")
            subprocess.run(['mount'], timeout=10)

        elif cmd_type == 'findmnt':
            print("=== Mount Tree ===\n")
            subprocess.run(['findmnt'], timeout=10)

        elif cmd_type == 'fdisk':
            print("=== Partition Table ===\n")
            subprocess.run(['sudo', 'fdisk', '-l'], timeout=10)

        elif cmd_type == 'smartctl':
            print("=== Disk Health (SMART) ===\n")
            if not shutil.which('smartctl'):
                print("smartctl not found. Install: sudo apt install smartmontools")
            else:
                # Find first disk
                result = subprocess.run(['lsblk', '-d', '-o', 'NAME'], capture_output=True, text=True, timeout=10)
                disks = [l.strip() for l in result.stdout.split('\n')[1:] if l.strip() and not l.strip().startswith('loop')]
                if disks:
                    subprocess.run(['sudo', 'smartctl', '-H', f'/dev/{disks[0]}'], timeout=30)
                else:
                    print("No disks found")

        elif cmd_type == 'ncdu':
            if shutil.which('ncdu'):
                print("=== Interactive Disk Usage (q to quit) ===\n")
                subprocess.run(['ncdu', '/'], timeout=None)
            else:
                print("ncdu not found. Install: sudo apt install ncdu")

        print("\n" + "=" * 60)
        self._wait_for_enter()

    # =========================================================================
    # Service Management
    # =========================================================================

    def _service_management_menu(self):
        """SystemD service management."""
        while True:
            choices = [
                ("status_all", "All Services Status"),
                ("status_mesh", "Mesh Services Status"),
                ("failed", "Failed Services"),
                ("timers", "System Timers"),
                ("recent", "Recently Changed"),
                ("analyze", "Boot Time Analysis"),
                ("logs_unit", "Logs for Specific Unit"),
                ("restart", "Restart a Service"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Service Management",
                "SystemD service control:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Service: {choice}", self._run_service_command, choice)

    def _run_service_command(self, cmd_type: str):
        """Run service management command."""
        clear_screen()

        if cmd_type == 'status_all':
            print("=== All Services Status ===\n")
            subprocess.run(['systemctl', 'list-units', '--type=service', '--no-pager'], timeout=30)

        elif cmd_type == 'status_mesh':
            print("=== Mesh Services Status ===\n")
            for svc in ['meshtasticd', 'rnsd', 'lxmf.delivery', 'nomadnetd']:
                print(f"\n--- {svc} ---")
                result = subprocess.run(
                    ['systemctl', 'status', svc, '--no-pager', '-l'],
                    capture_output=True, text=True, timeout=10
                )
                print(result.stdout[:1000] if result.stdout else "Not found")

        elif cmd_type == 'failed':
            print("=== Failed Services ===\n")
            subprocess.run(['systemctl', '--failed', '--no-pager'], timeout=10)

        elif cmd_type == 'timers':
            print("=== System Timers ===\n")
            subprocess.run(['systemctl', 'list-timers', '--no-pager'], timeout=10)

        elif cmd_type == 'recent':
            print("=== Recently Changed Services ===\n")
            subprocess.run(
                ['systemctl', 'list-units', '--type=service', '--state=activating,deactivating,reloading', '--no-pager'],
                timeout=10
            )

        elif cmd_type == 'analyze':
            print("=== Boot Time Analysis ===\n")
            subprocess.run(['systemd-analyze'], timeout=10)
            print("\n--- Blame (slowest units) ---")
            subprocess.run(['systemd-analyze', 'blame', '--no-pager'], timeout=10)

        elif cmd_type == 'logs_unit':
            unit = self.dialog.inputbox(
                "Service Logs",
                "Enter service/unit name:",
                "meshtasticd"
            )
            if unit:
                print(f"\n=== Logs for {unit} ===\n")
                subprocess.run(['journalctl', '-u', unit, '-n', '100', '--no-pager'], timeout=30)

        elif cmd_type == 'restart':
            unit = self.dialog.inputbox(
                "Restart Service",
                "Enter service to restart:",
                "meshtasticd"
            )
            if unit:
                confirm = self.dialog.yesno(
                    "Confirm Restart",
                    f"Restart {unit}?"
                )
                if confirm:
                    print(f"\n=== Restarting {unit} ===\n")
                    success, msg = apply_config_and_restart(unit)
                    print(msg)
                    subprocess.run(['systemctl', 'status', unit, '--no-pager'], timeout=10)

        print("\n" + "=" * 60)
        self._wait_for_enter()

    # =========================================================================
    # Advanced Log Analysis
    # =========================================================================

    def _advanced_logs_menu(self):
        """Advanced log analysis tools."""
        while True:
            choices = [
                ("journal_size", "Journal Disk Usage"),
                ("journal_boots", "Previous Boots"),
                ("priority", "Filter by Priority"),
                ("since", "Logs Since Time"),
                ("grep_logs", "Search Logs (grep)"),
                ("export", "Export Logs to File"),
                ("rotate", "Rotate Logs Now"),
                ("clear_journal", "Clear Old Journals"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Advanced Log Analysis",
                "Deep log inspection:",
                choices
            )

            if choice is None or choice == "back":
                break

            self._safe_call(f"Logs: {choice}", self._run_advanced_log_command, choice)

    def _run_advanced_log_command(self, cmd_type: str):
        """Run advanced log command."""
        clear_screen()

        if cmd_type == 'journal_size':
            print("=== Journal Disk Usage ===\n")
            subprocess.run(['journalctl', '--disk-usage'], timeout=10)

        elif cmd_type == 'journal_boots':
            print("=== Previous Boots ===\n")
            subprocess.run(['journalctl', '--list-boots'], timeout=10)
            boot = self.dialog.inputbox(
                "View Boot",
                "Enter boot number (0=current, -1=previous):",
                "0"
            )
            if boot:
                subprocess.run(['journalctl', '-b', boot, '-n', '50', '--no-pager'], timeout=30)

        elif cmd_type == 'priority':
            choices = [
                ("0", "emerg - System is unusable"),
                ("1", "alert - Action must be taken"),
                ("2", "crit - Critical conditions"),
                ("3", "err - Error conditions"),
                ("4", "warning - Warning conditions"),
                ("5", "notice - Normal but significant"),
                ("6", "info - Informational"),
                ("7", "debug - Debug messages"),
            ]
            prio = self.dialog.menu("Log Priority", "Show logs at priority level and above:", choices)
            if prio:
                subprocess.run(['journalctl', '-p', prio, '-n', '100', '--no-pager'], timeout=30)

        elif cmd_type == 'since':
            since = self.dialog.inputbox(
                "Logs Since",
                "Enter time (e.g., '1 hour ago', '2024-01-20', 'today'):",
                "1 hour ago"
            )
            if since:
                subprocess.run(['journalctl', '--since', since, '-n', '100', '--no-pager'], timeout=30)

        elif cmd_type == 'grep_logs':
            pattern = self.dialog.inputbox(
                "Search Logs",
                "Enter search pattern:",
                "error"
            )
            if pattern:
                print(f"=== Searching for: {pattern} ===\n")
                subprocess.run(['journalctl', '-g', pattern, '-n', '50', '--no-pager'], timeout=30)

        elif cmd_type == 'export':
            filename = self.dialog.inputbox(
                "Export Logs",
                "Export filename:",
                "/tmp/meshforge_logs.txt"
            )
            if filename:
                export_path = Path(filename).resolve()
                safe_dirs = [Path('/tmp'), get_real_user_home(), Path('/var/log')]
                if not any(str(export_path).startswith(str(d.resolve())) for d in safe_dirs):
                    self.dialog.msgbox(
                        "Error",
                        "Export path must be under /tmp, home directory, or /var/log"
                    )
                    return
                print(f"=== Exporting to {export_path} ===\n")
                with open(str(export_path), 'w') as f:
                    subprocess.run(
                        ['journalctl', '-u', 'meshtasticd', '-u', 'rnsd', '-n', '1000', '--no-pager'],
                        stdout=f, timeout=60
                    )
                print(f"Logs exported to: {export_path}")

        elif cmd_type == 'rotate':
            print("=== Rotating Logs ===\n")
            subprocess.run(['sudo', 'journalctl', '--rotate'], timeout=30)
            print("Done!")

        elif cmd_type == 'clear_journal':
            confirm = self.dialog.yesno(
                "Clear Journals",
                "Clear journal logs older than 2 days?\n\n"
                "This frees disk space but removes history."
            )
            if confirm:
                subprocess.run(['sudo', 'journalctl', '--vacuum-time=2d'], timeout=60)

        print("\n" + "=" * 60)
        self._wait_for_enter()

    # =========================================================================
    # Drop to Shell
    # =========================================================================

    def _drop_to_shell(self):
        """Drop to an interactive shell."""
        self.dialog.msgbox(
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
            self._wait_for_enter()
