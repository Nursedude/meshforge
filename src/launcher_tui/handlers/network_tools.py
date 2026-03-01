"""
Network Tools Handler — Ping, DNS, discovery, port checks.

Converted from network_tools_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import socket as sock
import subprocess
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.service_check import check_udp_port

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
                ("ports", "Listening Ports (ss -tlnp)"),
                ("ifaces", "Network Interfaces (ip addr)"),
                ("conns", "Active Connections (ss -tunp)"),
                ("routes", "Routing Table (ip route)"),
                ("ping", "Ping Test"),
                ("dns", "DNS Lookup"),
                ("discover", "Meshtastic Device Discovery"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu("Network & Ports", "Network diagnostics (terminal-native):", choices)
            if choice is None or choice == "back":
                break
            dispatch = {
                "status": ("Network Status", self._run_terminal_network),
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
                self.ctx.dialog.msgbox("Network Tools Error", f"Operation failed:\n{type(e).__name__}: {e}")

    def _ping_test(self):
        host = self.ctx.dialog.inputbox("Ping Test", "Enter host to ping:", "8.8.8.8")
        if not host:
            return
        if not self.ctx.validate_hostname(host):
            self.ctx.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return
        self.ctx.dialog.infobox("Pinging", f"Pinging {host}...")
        try:
            result = subprocess.run(['ping', '-c', '4', host], capture_output=True, text=True, timeout=15)
            output = result.stdout
            if 'transmitted' in output:
                stats_line = [l for l in output.split('\n') if 'transmitted' in l]
                time_line = [l for l in output.split('\n') if 'rtt' in l or 'round-trip' in l]
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
        self.ctx.dialog.infobox("Discovery", "Scanning for Meshtastic devices...")
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
        serial_ports = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
        for port in serial_ports:
            devices.append(f"Serial: {port}")
        if not devices:
            text = "No Meshtastic devices found.\n\nMake sure meshtasticd is running."
        else:
            devices.extend(["", "BLE devices require scanning:", "  meshtastic --ble-scan"])
            text = "Found devices:\n\n" + "\n".join(devices)
        self.ctx.dialog.msgbox("Meshtastic Discovery", text)

    def _dns_lookup(self):
        host = self.ctx.dialog.inputbox("DNS Lookup", "Enter hostname to lookup:", "meshtastic.org")
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
                if addr not in [r.split(': ')[1] for r in results if ': ' in r]:
                    family = "IPv4" if info[0] == socket.AF_INET else "IPv6"
                    results.append(f"{family}: {addr}")
            self.ctx.dialog.msgbox(f"DNS: {host}", "\n".join(results) or "No results")
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
                    print(f"  \033[2m○\033[0m {port:<6} {desc} (not listening)")
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
                print(f"  \033[0;31m●\033[0m Internet (no route to 8.8.8.8)")
        except OSError as e:
            logger.debug("Internet connectivity check failed: %s", e)
            print(f"  \033[0;31m●\033[0m Internet (unreachable)")
        print()
        print("-" * 50)
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()
