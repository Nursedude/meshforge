"""
MUDP Tools - Meshtastic UDP utilities for meshtasticd-installer

Provides UDP/Multicast tools for Meshtastic:
- MUDP monitor (listen for mesh traffic)
- UDP packet testing
- Multicast group management
- Virtual node simulation

Reference: https://github.com/pdxlocations/mudp
"""

import subprocess
import socket
import struct
import os
import sys
import json
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.live import Live

console = Console()

# MUDP Constants
MUDP_MULTICAST_GROUP = "224.0.0.69"
MUDP_PORT = 4403


class MUDPTools:
    """MUDP (Meshtastic UDP) tools"""

    def __init__(self):
        self._return_to_main = False
        self._mudp_installed = self._check_mudp_installed()

    def _check_mudp_installed(self) -> bool:
        """Check if MUDP package is installed"""
        try:
            result = subprocess.run(
                ['pip', 'show', 'mudp'],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    def interactive_menu(self):
        """Main MUDP tools menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════ MUDP Tools ═══════════[/bold cyan]\n")

            if not self._mudp_installed:
                console.print("[yellow]MUDP package not installed[/yellow]")
                console.print("[dim]Install with: pip install mudp[/dim]\n")

            console.print("[dim cyan]── Monitoring ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Monitor UDP Traffic (mudp CLI)")
            console.print("  [bold]2[/bold]. Listen to Multicast Group")
            console.print("  [bold]3[/bold]. View Active UDP Sockets")

            console.print("\n[dim cyan]── Testing ──[/dim cyan]")
            console.print("  [bold]4[/bold]. Send Test UDP Packet")
            console.print("  [bold]5[/bold]. UDP Echo Test")
            console.print("  [bold]6[/bold]. Multicast Join Test")

            console.print("\n[dim cyan]── Configuration ──[/dim cyan]")
            console.print("  [bold]7[/bold]. Show MUDP Configuration")
            console.print("  [bold]8[/bold]. Network Interface for Multicast")

            console.print("\n[dim cyan]── Installation ──[/dim cyan]")
            console.print("  [bold]9[/bold]. Install/Update MUDP Package")

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
                self._monitor_mudp()
            elif choice == "2":
                self._listen_multicast()
            elif choice == "3":
                self._show_udp_sockets()
            elif choice == "4":
                self._send_test_packet()
            elif choice == "5":
                self._udp_echo_test()
            elif choice == "6":
                self._multicast_join_test()
            elif choice == "7":
                self._show_config()
            elif choice == "8":
                self._multicast_interface()
            elif choice == "9":
                self._install_mudp()

    def _monitor_mudp(self):
        """Run MUDP CLI monitor"""
        console.print("\n[bold cyan]── MUDP Monitor ──[/bold cyan]\n")

        if not self._mudp_installed:
            console.print("[red]MUDP package not installed[/red]")
            console.print("Install with: pip install mudp")
            input("\nPress Enter to continue...")
            return

        console.print("[cyan]Starting MUDP monitor...[/cyan]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        try:
            # Run mudp CLI
            subprocess.run(['mudp'], check=False)  # Interactive monitor, no timeout
        except KeyboardInterrupt:
            console.print("\n[yellow]Monitor stopped[/yellow]")
        except FileNotFoundError:
            console.print("[red]mudp command not found[/red]")
            console.print("[dim]Try: pip install mudp[/dim]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _listen_multicast(self):
        """Listen to Meshtastic multicast group"""
        console.print("\n[bold cyan]── Multicast Listener ──[/bold cyan]\n")

        group = Prompt.ask("Multicast group", default=MUDP_MULTICAST_GROUP)
        port = int(Prompt.ask("Port", default=str(MUDP_PORT)))
        timeout = int(Prompt.ask("Timeout (seconds)", default="30"))

        console.print(f"\n[cyan]Listening on {group}:{port} for {timeout}s...[/cyan]")
        console.print("[dim]Press Ctrl+C to stop early[/dim]\n")

        try:
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind to port
            sock.bind(('', port))

            # Join multicast group
            mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            sock.settimeout(timeout)

            packet_count = 0
            start_time = datetime.now()

            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                    packet_count += 1
                    elapsed = (datetime.now() - start_time).total_seconds()

                    console.print(f"[green]Packet #{packet_count}[/green] from {addr[0]}:{addr[1]}")
                    console.print(f"  Size: {len(data)} bytes")
                    console.print(f"  Hex: {data[:32].hex()}{'...' if len(data) > 32 else ''}")

                    if elapsed > timeout:
                        break

                except socket.timeout:
                    break

            sock.close()
            console.print(f"\n[cyan]Received {packet_count} packet(s)[/cyan]")

        except PermissionError:
            console.print("[red]Permission denied. Try running with sudo.[/red]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Listener stopped[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _show_udp_sockets(self):
        """Show active UDP sockets"""
        console.print("\n[bold cyan]── Active UDP Sockets ──[/bold cyan]\n")

        try:
            result = subprocess.run(
                ['ss', '-uln'],
                capture_output=True, text=True, timeout=10
            )
            console.print(result.stdout)
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    ['netstat', '-uln'],
                    capture_output=True, text=True, timeout=10
                )
                console.print(result.stdout)
            except FileNotFoundError:
                console.print("[red]Neither ss nor netstat found[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        # Check for MUDP port
        console.print(f"\n[cyan]Checking for Meshtastic UDP (port {MUDP_PORT})...[/cyan]")
        try:
            result = subprocess.run(
                ['ss', '-uln', f'sport = :{MUDP_PORT}'],
                capture_output=True, text=True, timeout=10
            )
            if str(MUDP_PORT) in result.stdout:
                console.print(f"[green]Port {MUDP_PORT} is in use[/green]")
            else:
                console.print(f"[yellow]Port {MUDP_PORT} is not in use[/yellow]")
        except Exception:
            pass

        input("\nPress Enter to continue...")

    def _send_test_packet(self):
        """Send a test UDP packet"""
        console.print("\n[bold cyan]── Send Test UDP Packet ──[/bold cyan]\n")

        console.print("[dim]This sends a basic UDP packet (not a valid Meshtastic packet)[/dim]\n")

        host = Prompt.ask("Destination host", default="127.0.0.1")
        port = int(Prompt.ask("Destination port", default=str(MUDP_PORT)))
        message = Prompt.ask("Message", default="Test from meshtasticd-installer")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(message.encode(), (host, port))
            sock.close()

            console.print(f"[green]Sent {len(message)} bytes to {host}:{port}[/green]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _udp_echo_test(self):
        """UDP echo test (requires echo server)"""
        console.print("\n[bold cyan]── UDP Echo Test ──[/bold cyan]\n")

        host = Prompt.ask("Echo server host", default="localhost")
        port = int(Prompt.ask("Echo server port", default="7"))

        console.print(f"\n[cyan]Testing UDP echo to {host}:{port}...[/cyan]")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)

            message = b"ECHO_TEST_" + str(datetime.now().timestamp()).encode()
            sock.sendto(message, (host, port))

            console.print(f"  Sent: {message}")

            try:
                data, addr = sock.recvfrom(1024)
                console.print(f"  Received: {data}")
                console.print(f"  From: {addr}")

                if data == message:
                    console.print("[green]Echo test PASSED[/green]")
                else:
                    console.print("[yellow]Echo test: Response differs[/yellow]")
            except socket.timeout:
                console.print("[yellow]No response (timeout)[/yellow]")
                console.print("[dim]Note: UDP echo requires a running echo server[/dim]")

            sock.close()

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _multicast_join_test(self):
        """Test joining a multicast group"""
        console.print("\n[bold cyan]── Multicast Join Test ──[/bold cyan]\n")

        group = Prompt.ask("Multicast group", default=MUDP_MULTICAST_GROUP)

        console.print(f"\n[cyan]Testing multicast group join: {group}[/cyan]")

        try:
            # Create socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind
            sock.bind(('', MUDP_PORT))

            # Try to join multicast group
            mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            console.print(f"[green]Successfully joined multicast group {group}[/green]")

            # Leave group
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            sock.close()

            console.print("[green]Successfully left multicast group[/green]")

        except PermissionError:
            console.print("[red]Permission denied. Try running with sudo.[/red]")
        except OSError as e:
            if "Address already in use" in str(e):
                console.print(f"[yellow]Port {MUDP_PORT} already in use (meshtasticd running?)[/yellow]")
                console.print("[green]This is expected if meshtasticd is active[/green]")
            else:
                console.print(f"[red]Error: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _show_config(self):
        """Show MUDP configuration details"""
        console.print("\n[bold cyan]── MUDP Configuration ──[/bold cyan]\n")

        table = Table(show_header=True)
        table.add_column("Parameter", style="cyan")
        table.add_column("Value")
        table.add_column("Description")

        table.add_row("Multicast Group", MUDP_MULTICAST_GROUP, "Default MUDP multicast address")
        table.add_row("Port", str(MUDP_PORT), "Meshtastic TCP/UDP port")
        table.add_row("Protocol", "UDP/Multicast", "Transport layer")

        console.print(table)

        console.print("\n[cyan]PubSub Topics:[/cyan]")
        topics = [
            ("mesh.rx.raw", "Raw UDP packet bytes"),
            ("mesh.rx.packet", "Parsed MeshPacket objects"),
            ("mesh.rx.decoded", "Decoded payloads with port IDs"),
            ("mesh.rx.port.<portnum>", "Port-specific filtering"),
            ("mesh.rx.decode_error", "Decoding failures"),
        ]
        for topic, desc in topics:
            console.print(f"  [green]{topic}[/green] - {desc}")

        console.print("\n[cyan]Send Functions:[/cyan]")
        functions = [
            "send_text_message()",
            "send_nodeinfo()",
            "send_device_telemetry()",
            "send_position()",
            "send_environment_metrics()",
            "send_power_metrics()",
            "send_health_metrics()",
            "send_waypoint()",
            "send_data()",
        ]
        for func in functions:
            console.print(f"  [green]{func}[/green]")

        console.print("\n[dim]Reference: https://github.com/pdxlocations/mudp[/dim]")

        input("\nPress Enter to continue...")

    def _multicast_interface(self):
        """Configure network interface for multicast"""
        console.print("\n[bold cyan]── Multicast Interface ──[/bold cyan]\n")

        # List network interfaces
        console.print("[cyan]Available network interfaces:[/cyan]")
        try:
            result = subprocess.run(
                ['ip', '-o', 'link', 'show'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2:
                    iface = parts[1].strip()
                    console.print(f"  {iface}")
        except Exception:
            pass

        console.print("\n[cyan]Multicast routing:[/cyan]")
        try:
            result = subprocess.run(
                ['ip', 'mroute', 'show'],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                console.print(result.stdout)
            else:
                console.print("  [dim]No multicast routes configured[/dim]")
        except Exception:
            console.print("  [dim]Unable to query multicast routes[/dim]")

        # Check if multicast is enabled
        console.print("\n[cyan]Multicast status:[/cyan]")
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/net/ipv4/icmp_echo_ignore_broadcasts'],
                capture_output=True, text=True, timeout=5
            )
            val = result.stdout.strip()
            if val == "0":
                console.print("  [green]Broadcast/multicast responses enabled[/green]")
            else:
                console.print("  [yellow]Broadcast/multicast responses disabled[/yellow]")
        except Exception:
            pass

        input("\nPress Enter to continue...")

    def _install_mudp(self):
        """Install or update MUDP package"""
        console.print("\n[bold cyan]── Install MUDP ──[/bold cyan]\n")

        if self._mudp_installed:
            action = "upgrade"
            console.print("[cyan]MUDP is already installed. Checking for updates...[/cyan]")
        else:
            action = "install"
            console.print("[cyan]Installing MUDP package...[/cyan]")

        if not Confirm.ask(f"Continue with {action}?", default=True):
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task(f"{action.capitalize()}ing mudp...", total=None)

            try:
                result = subprocess.run(
                    ['pip', 'install', '--upgrade', '--break-system-packages', '--ignore-installed', 'mudp'],
                    capture_output=True, text=True, timeout=120
                )

                if result.returncode == 0:
                    console.print(f"[green]MUDP {action}ed successfully![/green]")
                    self._mudp_installed = True
                else:
                    console.print(f"[red]Installation failed:[/red]")
                    console.print(result.stderr)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def is_mudp_installed(self) -> bool:
        """Check if MUDP is installed"""
        return self._mudp_installed
