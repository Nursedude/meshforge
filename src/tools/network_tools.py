"""
Network Tools - TCP/IP utilities for meshtasticd-installer

Provides interactive network diagnostic and testing tools:
- Ping tests
- Port scanning
- TCP connection testing
- Network interface info
- Routing table
- DNS lookups
"""

import subprocess
import socket
import os
from typing import Optional, List, Dict
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.live import Live

console = Console()

# Meshtastic default ports
MESHTASTIC_TCP_PORT = 4403
MESHTASTIC_HTTP_PORT = 80
MESHTASTIC_HTTPS_PORT = 443


class NetworkTools:
    """TCP/IP network diagnostic tools"""

    def __init__(self):
        self._return_to_main = False

    def interactive_menu(self):
        """Main network tools menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════ Network Tools ═══════════[/bold cyan]\n")

            console.print("[dim cyan]── Connectivity ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Ping Test")
            console.print("  [bold]2[/bold]. TCP Port Test")
            console.print("  [bold]3[/bold]. Test Meshtastic TCP (4403)")

            console.print("\n[dim cyan]── Network Info ──[/dim cyan]")
            console.print("  [bold]4[/bold]. Network Interfaces")
            console.print("  [bold]5[/bold]. Routing Table")
            console.print("  [bold]6[/bold]. DNS Lookup")
            console.print("  [bold]7[/bold]. Active Connections")

            console.print("\n[dim cyan]── Scanning ──[/dim cyan]")
            console.print("  [bold]8[/bold]. Scan Local Network")
            console.print("  [bold]9[/bold]. Find Meshtastic Devices")

            console.print("\n  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")
            console.print()

            choice = Prompt.ask("Select option", default="0")

            if choice == "0":
                return
            elif choice.lower() == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self._ping_test()
            elif choice == "2":
                self._tcp_port_test()
            elif choice == "3":
                self._test_meshtastic_tcp()
            elif choice == "4":
                self._show_interfaces()
            elif choice == "5":
                self._show_routing()
            elif choice == "6":
                self._dns_lookup()
            elif choice == "7":
                self._show_connections()
            elif choice == "8":
                self._scan_network()
            elif choice == "9":
                self._find_meshtastic_devices()

    def _ping_test(self):
        """Interactive ping test"""
        console.print("\n[bold cyan]── Ping Test ──[/bold cyan]\n")

        host = Prompt.ask("Enter hostname or IP", default="8.8.8.8")
        count = Prompt.ask("Number of pings", default="4")

        try:
            count = int(count)
        except ValueError:
            count = 4

        console.print(f"\n[cyan]Pinging {host}...[/cyan]\n")

        try:
            result = subprocess.run(
                ['ping', '-c', str(count), host],
                capture_output=True, text=True, timeout=30
            )
            console.print(result.stdout)
            if result.returncode != 0:
                console.print(f"[red]{result.stderr}[/red]")
        except subprocess.TimeoutExpired:
            console.print("[red]Ping timed out[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _tcp_port_test(self):
        """Test TCP port connectivity"""
        console.print("\n[bold cyan]── TCP Port Test ──[/bold cyan]\n")

        host = Prompt.ask("Enter hostname or IP", default="localhost")
        port = Prompt.ask("Enter port number", default="4403")

        try:
            port = int(port)
        except ValueError:
            console.print("[red]Invalid port number[/red]")
            input("\nPress Enter to continue...")
            return

        console.print(f"\n[cyan]Testing TCP connection to {host}:{port}...[/cyan]")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                console.print(f"[green]Port {port} is OPEN on {host}[/green]")
            else:
                console.print(f"[red]Port {port} is CLOSED on {host}[/red]")
        except socket.gaierror:
            console.print(f"[red]Could not resolve hostname: {host}[/red]")
        except socket.timeout:
            console.print(f"[red]Connection timed out[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _test_meshtastic_tcp(self):
        """Test Meshtastic TCP port on local or remote host"""
        console.print("\n[bold cyan]── Meshtastic TCP Test ──[/bold cyan]\n")

        hosts = ["localhost", "127.0.0.1"]

        # Try to detect local IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            hosts.append(local_ip)
        except Exception:
            pass

        console.print("[cyan]Testing Meshtastic TCP port 4403...[/cyan]\n")

        table = Table(show_header=True)
        table.add_column("Host")
        table.add_column("Port 4403")
        table.add_column("Status")

        for host in hosts:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, MESHTASTIC_TCP_PORT))
                sock.close()

                if result == 0:
                    table.add_row(host, "4403", "[green]OPEN[/green]")
                else:
                    table.add_row(host, "4403", "[red]CLOSED[/red]")
            except Exception:
                table.add_row(host, "4403", "[yellow]ERROR[/yellow]")

        console.print(table)

        # Custom host test
        console.print()
        if Confirm.ask("Test a custom host?", default=False):
            custom_host = Prompt.ask("Enter hostname or IP")
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((custom_host, MESHTASTIC_TCP_PORT))
                sock.close()

                if result == 0:
                    console.print(f"[green]Port 4403 is OPEN on {custom_host}[/green]")
                else:
                    console.print(f"[red]Port 4403 is CLOSED on {custom_host}[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _show_interfaces(self):
        """Show network interfaces"""
        console.print("\n[bold cyan]── Network Interfaces ──[/bold cyan]\n")

        # Try ip command first, fallback to ifconfig
        try:
            result = subprocess.run(
                ['ip', '-c', 'addr'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                console.print(result.stdout)
            else:
                # Fallback to ifconfig
                result = subprocess.run(
                    ['ifconfig'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['ifconfig'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
            except FileNotFoundError:
                console.print("[red]Neither 'ip' nor 'ifconfig' found. Install iproute2 or net-tools.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _show_routing(self):
        """Show routing table"""
        console.print("\n[bold cyan]── Routing Table ──[/bold cyan]\n")

        try:
            result = subprocess.run(
                ['ip', 'route'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                console.print(result.stdout)
            else:
                # Fallback to netstat
                result = subprocess.run(
                    ['netstat', '-rn'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['netstat', '-rn'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
            except FileNotFoundError:
                console.print("[red]Neither 'ip' nor 'netstat' found.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _dns_lookup(self):
        """DNS lookup tool"""
        console.print("\n[bold cyan]── DNS Lookup ──[/bold cyan]\n")

        host = Prompt.ask("Enter hostname", default="meshtastic.org")

        console.print(f"\n[cyan]Looking up {host}...[/cyan]\n")

        try:
            # Get all address info
            addrs = socket.getaddrinfo(host, None)
            seen = set()

            table = Table(show_header=True)
            table.add_column("Type")
            table.add_column("Address")

            for addr in addrs:
                family, _, _, _, sockaddr = addr
                ip = sockaddr[0]
                if ip not in seen:
                    seen.add(ip)
                    addr_type = "IPv6" if family == socket.AF_INET6 else "IPv4"
                    table.add_row(addr_type, ip)

            console.print(table)

            # Reverse lookup
            if Confirm.ask("\nPerform reverse DNS lookup?", default=False):
                for ip in list(seen)[:3]:  # Limit to first 3
                    try:
                        hostname = socket.gethostbyaddr(ip)
                        console.print(f"  {ip} -> {hostname[0]}")
                    except socket.herror:
                        console.print(f"  {ip} -> [dim]No reverse DNS[/dim]")

        except socket.gaierror as e:
            console.print(f"[red]DNS lookup failed: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _show_connections(self):
        """Show active network connections"""
        console.print("\n[bold cyan]── Active Connections ──[/bold cyan]\n")

        try:
            # Use ss (modern) or netstat (legacy)
            result = subprocess.run(
                ['ss', '-tuln'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                console.print(result.stdout)
            else:
                result = subprocess.run(
                    ['netstat', '-tuln'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['netstat', '-tuln'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
            except FileNotFoundError:
                console.print("[red]Neither 'ss' nor 'netstat' found.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        # Highlight Meshtastic port
        console.print("\n[dim]Looking for Meshtastic (port 4403)...[/dim]")
        try:
            result = subprocess.run(
                ['ss', '-tuln', 'sport', '=', '4403'],
                capture_output=True, text=True, timeout=10
            )
            if "4403" in result.stdout:
                console.print("[green]Meshtastic TCP port 4403 is listening![/green]")
            else:
                console.print("[yellow]Meshtastic TCP port 4403 is not listening[/yellow]")
        except Exception:
            pass

        input("\nPress Enter to continue...")

    def _scan_network(self):
        """Scan local network for devices"""
        console.print("\n[bold cyan]── Network Scan ──[/bold cyan]\n")

        # Check for nmap
        nmap_check = subprocess.run(['which', 'nmap'], capture_output=True, timeout=5)
        if nmap_check.returncode != 0:
            console.print("[yellow]nmap not installed. Install with:[/yellow]")
            console.print("  sudo apt install nmap")
            input("\nPress Enter to continue...")
            return

        # Determine network range
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=10
            )
            # Extract gateway and guess network
            parts = result.stdout.split()
            if 'via' in parts:
                gateway_idx = parts.index('via') + 1
                gateway = parts[gateway_idx]
                network = '.'.join(gateway.split('.')[:3]) + '.0/24'
            else:
                network = "192.168.1.0/24"
        except Exception:
            network = "192.168.1.0/24"

        network = Prompt.ask("Enter network range to scan", default=network)

        console.print(f"\n[cyan]Scanning {network}...[/cyan]")
        console.print("[dim]This may take a minute...[/dim]\n")

        try:
            result = subprocess.run(
                ['sudo', 'nmap', '-sn', network],
                capture_output=True, text=True, timeout=120
            )
            console.print(result.stdout)
        except subprocess.TimeoutExpired:
            console.print("[red]Scan timed out[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _find_meshtastic_devices(self):
        """Find Meshtastic devices on the network"""
        console.print("\n[bold cyan]── Find Meshtastic Devices ──[/bold cyan]\n")

        # Check for nmap
        nmap_check = subprocess.run(['which', 'nmap'], capture_output=True, timeout=5)
        has_nmap = nmap_check.returncode == 0

        # Determine network range
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=10
            )
            parts = result.stdout.split()
            if 'via' in parts:
                gateway_idx = parts.index('via') + 1
                gateway = parts[gateway_idx]
                network = '.'.join(gateway.split('.')[:3]) + '.0/24'
            else:
                network = "192.168.1.0/24"
        except Exception:
            network = "192.168.1.0/24"

        network = Prompt.ask("Enter network range", default=network)

        console.print(f"\n[cyan]Scanning for Meshtastic devices (port 4403)...[/cyan]")
        console.print("[dim]This may take a minute...[/dim]\n")

        if has_nmap:
            try:
                result = subprocess.run(
                    ['sudo', 'nmap', '-p', '4403', '--open', network],
                    capture_output=True, text=True, timeout=180
                )
                console.print(result.stdout)
            except subprocess.TimeoutExpired:
                console.print("[red]Scan timed out[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
        else:
            # Manual scan without nmap
            console.print("[yellow]nmap not found, using basic scan...[/yellow]\n")
            base = '.'.join(network.split('.')[:3])
            found = []

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("Scanning...", total=254)

                for i in range(1, 255):
                    ip = f"{base}.{i}"
                    progress.update(task, description=f"Scanning {ip}...")

                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.5)
                        result = sock.connect_ex((ip, 4403))
                        sock.close()

                        if result == 0:
                            found.append(ip)
                            console.print(f"[green]Found: {ip}:4403[/green]")
                    except Exception:
                        pass

                    progress.advance(task)

            if found:
                console.print(f"\n[green]Found {len(found)} Meshtastic device(s)![/green]")
            else:
                console.print("\n[yellow]No Meshtastic devices found on port 4403[/yellow]")

        input("\nPress Enter to continue...")

    def test_tcp_connection(self, host: str, port: int = 4403, timeout: float = 5.0) -> bool:
        """Test TCP connection (non-interactive)"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def get_local_ip(self) -> Optional[str]:
        """Get local IP address"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None
