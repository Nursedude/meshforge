#!/usr/bin/env python3
"""
Meshtasticd Interactive Installer & Manager
Main entry point for the application

Version: 2.0.0
Features:
- Quick Status Dashboard
- Interactive Channel Configuration with Presets
- Automatic Update Notifications
- Configuration Templates for Common Setups
- Version Control
- Environment Configuration (.env support)
"""

import os
import sys
import subprocess
import click
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.system import check_root, get_system_info
from utils.logger import setup_logger, log
from utils import emoji as em
from utils.paths import get_real_user_home
from utils.env_config import initialize_config, get_config_bool
from installer.meshtasticd import MeshtasticdInstaller
from config.device import DeviceConfigurator
from __version__ import __version__, get_full_version

console = Console()

def get_banner():
    """Generate banner with emoji support"""
    mesh = em.get('🌐', '[MESH]')
    ant = em.get('📡', '[ANT]')
    return f"""
    {mesh} MeshForge - Meshtasticd Manager v{__version__}
    {ant} Install - Configure - Monitor - Update
"""

BANNER = get_banner()


def show_banner():
    """Display application banner"""
    console.print(BANNER, style="bold cyan")
    console.print("[dim]Type '?' for help at any menu prompt[/dim]\n")


def show_system_info():
    """Display system information"""
    info = get_system_info()

    table = Table(title="System Information", show_header=True, header_style="bold magenta")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("OS", info.get('os', 'Unknown'))
    table.add_row("Architecture", info.get('arch', 'Unknown'))
    table.add_row("Platform", info.get('platform', 'Unknown'))
    table.add_row("Python Version", info.get('python', 'Unknown'))
    table.add_row("Kernel", info.get('kernel', 'Unknown'))

    console.print(table)
    console.print()


def check_for_updates_on_startup():
    """Check for updates on startup and show notification if available"""
    try:
        from installer.update_notifier import UpdateNotifier
        notifier = UpdateNotifier()
        notifier.startup_update_check()
    except Exception as e:
        # Don't let update check failures interrupt startup
        from utils.logger import log_exception
        log_exception(e, "Update check on startup failed")


def show_quick_status():
    """Show quick status line in menu"""
    try:
        from dashboard import StatusDashboard
        dashboard = StatusDashboard()
        status_line = dashboard.get_quick_status_line()
        console.print(f"\n[dim]Status:[/dim] {status_line}")
    except Exception as e:
        from utils.logger import get_logger
        logger = get_logger()
        logger.debug(f"Could not display quick status: {e}")


def interactive_menu():
    """Show interactive menu and handle user choices"""
    show_banner()

    # Check if running as root
    if not check_root():
        console.print("[bold red]Error:[/bold red] This tool requires root/sudo privileges")
        console.print("Please run with: [cyan]sudo python3 src/main.py[/cyan]")
        sys.exit(1)

    # Check for updates on startup
    check_for_updates_on_startup()

    show_system_info()

    while True:
        # Show quick status
        show_quick_status()

        console.print("\n[bold cyan]=================== Main Menu ===================[/bold cyan]")

        # Status & Monitoring Section
        console.print("\n[dim cyan]-- Status & Monitoring --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('📊')} [green]Quick Status Dashboard[/green]")
        console.print(f"  [bold]2[/bold]. {em.get('🔧')} [green]Service Management[/green] [dim](Start/Stop/Logs)[/dim]")

        # Installation Section
        console.print("\n[dim cyan]-- Installation --[/dim cyan]")
        console.print(f"  [bold]3[/bold]. {em.get('📦')} Install meshtasticd")
        console.print(f"  [bold]4[/bold]. {em.get('⬆️')}  Update meshtasticd")

        # Configuration Section
        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('⚙️')}  Configure device")
        console.print(f"  [bold]6[/bold]. {em.get('📻')} [yellow]Channel Presets[/yellow] [dim](Quick Setup)[/dim]")
        console.print(f"  [bold]7[/bold]. {em.get('📋')} Configuration Templates")
        console.print(f"  [bold]8[/bold]. {em.get('📁')} [green]Config File Manager[/green] [dim](Select YAML + nano)[/dim]")
        console.print(f"  [bold]f[/bold]. {em.get('📶')} [yellow]Full Radio Config[/yellow] [dim](Mesh, MQTT, Position)[/dim]")

        # Meshtastic CLI Section
        console.print("\n[dim cyan]-- Meshtastic CLI --[/dim cyan]")
        console.print(f"  [bold]c[/bold]. {em.get('💻')} [yellow]Meshtastic CLI Commands[/yellow]")

        # RNS/Gateway Section
        console.print("\n[dim cyan]-- RNS & Gateway --[/dim cyan]")
        console.print(f"  [bold]s[/bold]. {em.get('🌐')} [green]RNS Tools[/green] [dim](rnsd, NomadNet, LXMF)[/dim]")
        console.print(f"  [bold]b[/bold]. {em.get('🔗')} [green]Gateway Bridge[/green] [dim](Meshtastic ↔ RNS)[/dim]")

        # Ham Radio Section
        console.print("\n[dim cyan]-- Ham Radio --[/dim cyan]")
        console.print(f"  [bold]k[/bold]. {em.get('🌍')} [yellow]HamClock & Propagation[/yellow] [dim](Space Weather, Solar)[/dim]")
        console.print(f"  [bold]a[/bold]. {em.get('📡')} [yellow]AREDN Tools[/yellow] [dim](Node Discovery, Topology)[/dim]")

        # Tools Section
        console.print("\n[dim cyan]-- Tools --[/dim cyan]")
        console.print(f"  [bold]t[/bold]. {em.get('🔧')} [cyan]System Diagnostics[/cyan] [dim](Network, Hardware, Health)[/dim]")
        console.print(f"  [bold]p[/bold]. {em.get('📡')} [cyan]Site Planner[/cyan] [dim](Coverage, Link Budget)[/dim]")
        console.print(f"  [bold]n[/bold]. {em.get('🌐')} [cyan]Network Tools[/cyan] [dim](TCP/IP, Ping, Scanning)[/dim]")
        console.print(f"  [bold]r[/bold]. {em.get('📻')} [cyan]RF Tools[/cyan] [dim](Link Budget, LoRa Analysis)[/dim]")
        console.print(f"  [bold]m[/bold]. {em.get('📡')} [cyan]MUDP Tools[/cyan] [dim](UDP, Multicast, Virtual Node)[/dim]")
        console.print(f"  [bold]g[/bold]. {em.get('📦')} [cyan]Tool Manager[/cyan] [dim](Install, Update, Version)[/dim]")

        # System Section
        console.print("\n[dim cyan]-- System --[/dim cyan]")
        console.print(f"  [bold]9[/bold]. {em.get('🔍')} Check dependencies")
        console.print(f"  [bold]h[/bold]. {em.get('🔌')} Hardware detection")
        console.print(f"  [bold]x[/bold]. {em.get('🎯')} [bold green]Device Wizard[/bold green] [dim](Scan + Configure all ports)[/dim]")
        console.print(f"  [bold]w[/bold]. {em.get('🛠️')} [yellow]Hardware Configuration[/yellow] [dim](SPI, Serial, GPIO)[/dim]")
        console.print(f"  [bold]d[/bold]. {em.get('🐛')} Debug & troubleshooting")
        console.print(f"  [bold]u[/bold]. {em.get('🗑️', '[DEL]')} [red]Uninstall[/red]")

        console.print(f"\n  [bold]q[/bold]. {em.get('🚪')} Exit")
        console.print(f"  [bold]?[/bold]. {em.get('❓')} Help")

        choice = Prompt.ask("\n[cyan]Select an option[/cyan]", choices=["q", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "f", "s", "b", "k", "a", "t", "p", "n", "r", "m", "g", "h", "x", "w", "d", "u", "?"], default="1")

        if choice == "1":
            show_dashboard()
        elif choice == "2":
            service_management_menu()
        elif choice == "3":
            install_meshtasticd()
        elif choice == "4":
            update_meshtasticd()
        elif choice == "5":
            configure_device()
        elif choice == "6":
            configure_channel_presets()
        elif choice == "7":
            manage_templates()
        elif choice == "8":
            config_file_manager_menu()
        elif choice == "c":
            meshtastic_cli_menu()
        elif choice == "f":
            full_radio_config_menu()
        elif choice == "s":
            rns_tools_menu()
        elif choice == "b":
            gateway_bridge_menu()
        elif choice == "k":
            ham_radio_menu()
        elif choice == "a":
            aredn_menu()
        elif choice == "t":
            system_diagnostics_menu()
        elif choice == "p":
            site_planner_menu()
        elif choice == "n":
            network_tools_menu()
        elif choice == "r":
            rf_tools_menu()
        elif choice == "m":
            mudp_tools_menu()
        elif choice == "g":
            tool_manager_menu()
        elif choice == "9":
            check_dependencies()
        elif choice == "h":
            detect_hardware()
        elif choice == "x":
            device_wizard()
        elif choice == "w":
            hardware_config_menu()
        elif choice == "d":
            debug_menu()
        elif choice == "u":
            uninstall_menu()
        elif choice == "?":
            show_help()
        elif choice == "q":
            console.print(f"\n[green]{em.get('🤙')} A Hui Hou! Happy meshing![/green]")
            sys.exit(0)


def uninstall_menu():
    """Show uninstall menu"""
    console.print("\n[bold red]=============== Uninstall ===============[/bold red]\n")

    console.print("[yellow]Warning: This will remove meshtasticd and related components.[/yellow]")
    console.print("[dim]You can choose which components to remove.[/dim]\n")

    if not Confirm.ask("[red]Are you sure you want to proceed with uninstall?[/red]", default=False):
        console.print("\n[green]Uninstall cancelled.[/green]")
        return

    from installer.uninstaller import MeshtasticdUninstaller
    uninstaller = MeshtasticdUninstaller()
    uninstaller.uninstall(interactive=True)

    Prompt.ask("\n[dim]Press Enter to return to menu[/dim]")


def system_diagnostics_menu():
    """System diagnostics menu"""
    from diagnostics.system_diagnostics import SystemDiagnostics

    diagnostics = SystemDiagnostics()
    diagnostics.interactive_menu()


def site_planner_menu():
    """Site planner and coverage tools menu"""
    from diagnostics.site_planner import SitePlanner

    planner = SitePlanner()
    planner.interactive_menu()


def rns_tools_menu():
    """RNS/Reticulum tools menu"""
    console.print("\n[bold cyan]═══════════ RNS Tools ═══════════[/bold cyan]\n")

    while True:
        # Check RNS installation status
        rns_installed = False
        try:
            import RNS
            rns_installed = True
            rns_version = getattr(RNS, '__version__', 'unknown')
        except ImportError:
            rns_version = "Not installed"

        # Check rnsd service status
        rnsd_status = "unknown"
        try:
            result = subprocess.run(['systemctl', 'is-active', 'rnsd'],
                                   capture_output=True, text=True, timeout=5)
            rnsd_status = result.stdout.strip()
        except Exception:
            pass

        console.print(f"[dim]RNS: {rns_version} | rnsd: {rnsd_status}[/dim]\n")

        console.print("[dim cyan]-- Service Control --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('▶️')}  Start rnsd")
        console.print(f"  [bold]2[/bold]. {em.get('⏹️')}  Stop rnsd")
        console.print(f"  [bold]3[/bold]. {em.get('🔄')} Restart rnsd")
        console.print(f"  [bold]4[/bold]. {em.get('📋')} View rnsd logs")

        console.print("\n[dim cyan]-- Installation --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('📦')} Install/Update RNS")
        console.print(f"  [bold]6[/bold]. {em.get('📦')} Install NomadNet")
        console.print(f"  [bold]7[/bold]. {em.get('📦')} Install LXMF")
        console.print(f"  [bold]i[/bold]. {em.get('🔌')} Install Meshtastic Interface")

        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]c[/bold]. {em.get('🛠️')} [green]Create/Setup RNS Config[/green] [dim](Templates)[/dim]")
        console.print(f"  [bold]8[/bold]. {em.get('📝')} Edit RNS config")
        console.print(f"  [bold]9[/bold]. {em.get('📊')} Show rnstatus")

        console.print("\n[dim cyan]-- Applications --[/dim cyan]")
        console.print(f"  [bold]n[/bold]. {em.get('🌐')} Launch NomadNet")
        console.print(f"  [bold]m[/bold]. {em.get('💬')} Launch MeshChat Web UI")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "i", "n", "m"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            _run_service_command('rnsd', 'start')
        elif choice == "2":
            _run_service_command('rnsd', 'stop')
        elif choice == "3":
            _run_service_command('rnsd', 'restart')
        elif choice == "4":
            console.print("\n[cyan]Recent rnsd logs:[/cyan]")
            subprocess.run(['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'], timeout=10)
            input("\nPress Enter to continue...")
        elif choice == "5":
            console.print("\n[cyan]Installing RNS...[/cyan]")
            subprocess.run(['pip3', 'install', '--upgrade', '--break-system-packages', 'rns'], timeout=120)
            input("\nPress Enter to continue...")
        elif choice == "6":
            console.print("\n[cyan]Installing NomadNet...[/cyan]")
            subprocess.run(['pip3', 'install', '--upgrade', '--break-system-packages', 'nomadnet'], timeout=120)
            input("\nPress Enter to continue...")
        elif choice == "7":
            console.print("\n[cyan]Installing LXMF...[/cyan]")
            subprocess.run(['pip3', 'install', '--upgrade', '--break-system-packages', 'lxmf'], timeout=120)
            input("\nPress Enter to continue...")
        elif choice == "i":
            _install_meshtastic_interface()
        elif choice == "c":
            _create_rns_config_wizard()
        elif choice == "8":
            config_path = get_real_user_home() / '.reticulum' / 'config'
            if config_path.exists():
                subprocess.run(['nano', str(config_path)])  # No timeout for editor
            else:
                console.print(f"[yellow]Config not found at {config_path}[/yellow]")
                console.print("[dim]Run 'rnsd' once to create default config[/dim]")
                input("\nPress Enter to continue...")
        elif choice == "9":
            console.print("\n[cyan]RNS Status:[/cyan]")
            subprocess.run(['rnstatus'], timeout=10)
            input("\nPress Enter to continue...")
        elif choice == "n":
            console.print("\n[cyan]Launching NomadNet...[/cyan]")
            console.print("[dim]Press Ctrl+C to exit NomadNet[/dim]\n")
            try:
                subprocess.run(['nomadnet'], timeout=None)
            except KeyboardInterrupt:
                pass
            except FileNotFoundError:
                console.print("[red]NomadNet not installed. Use option 6 to install.[/red]")
                input("\nPress Enter to continue...")
        elif choice == "m":
            _launch_meshchat()


def _launch_meshchat():
    """Launch MeshChat web interface"""
    console.print("\n[bold cyan]═══ MeshChat Web Interface ═══[/bold cyan]\n")

    # Check if meshchat service is running
    meshchat_status = "unknown"
    try:
        result = subprocess.run(['systemctl', 'is-active', 'reticulum-meshchat'],
                               capture_output=True, text=True, timeout=5)
        meshchat_status = result.stdout.strip()
    except Exception:
        pass

    console.print(f"[dim]MeshChat service: {meshchat_status}[/dim]\n")

    if meshchat_status != "active":
        console.print("[yellow]MeshChat service is not running[/yellow]")
        if Confirm.ask("[cyan]Start the MeshChat service?[/cyan]", default=True):
            _run_service_command('reticulum-meshchat', 'start')

    # Get local IP for URL
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"

    meshchat_port = 8000  # Default MeshChat port
    url = f"http://{local_ip}:{meshchat_port}"

    console.print(f"\n[cyan]MeshChat URL: {url}[/cyan]")
    console.print("[dim]Opening in browser (if available)...[/dim]")

    try:
        import webbrowser
        webbrowser.open(url)
        console.print("[green]Browser launched[/green]")
    except Exception:
        console.print(f"[yellow]Open manually: {url}[/yellow]")

    input("\nPress Enter to continue...")


def gateway_bridge_menu():
    """Gateway bridge menu (Meshtastic ↔ RNS)"""
    console.print("\n[bold cyan]═══════════ Gateway Bridge ═══════════[/bold cyan]\n")

    while True:
        # Check gateway status using the bridge module
        gateway_running = False
        try:
            from gateway.rns_bridge import is_gateway_running
            gateway_running = is_gateway_running()
        except ImportError:
            # Fall back to process check if module not available
            try:
                result = subprocess.run(['pgrep', '-f', 'gateway.*bridge'],
                                       capture_output=True, text=True, timeout=5)
                gateway_running = result.returncode == 0
            except Exception:
                pass

        status = "[green]Running[/green]" if gateway_running else "[yellow]Stopped[/yellow]"
        console.print(f"[dim]Gateway Status: {status}[/dim]\n")

        console.print("[dim cyan]-- Bridge Control --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('▶️')}  Start Gateway Bridge")
        console.print(f"  [bold]2[/bold]. {em.get('⏹️')}  Stop Gateway Bridge")
        console.print(f"  [bold]3[/bold]. {em.get('📊')} View Bridge Stats")

        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]4[/bold]. {em.get('⚙️')}  Configure Gateway")
        console.print(f"  [bold]5[/bold]. {em.get('📋')} Apply Gateway Template")

        console.print("\n[dim cyan]-- Diagnostics --[/dim cyan]")
        console.print(f"  [bold]6[/bold]. {em.get('🔍')} Test Meshtastic Connection")
        console.print(f"  [bold]7[/bold]. {em.get('🔍')} Test RNS Connection")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6", "7"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            console.print("\n[cyan]Starting Gateway Bridge...[/cyan]")
            try:
                from gateway.rns_bridge import start_gateway_headless
                start_gateway_headless()
            except ImportError as e:
                console.print(f"[red]Failed to import gateway: {e}[/red]")
                console.print("[dim]Make sure RNS is installed: pip3 install rns[/dim]")
            except Exception as e:
                console.print(f"[red]Error starting gateway: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "2":
            console.print("\n[cyan]Stopping Gateway Bridge...[/cyan]")
            subprocess.run(['pkill', '-f', 'gateway.*bridge'], timeout=10)
            console.print("[green]Gateway stopped[/green]")
            input("\nPress Enter to continue...")
        elif choice == "3":
            console.print("\n[cyan]Gateway Stats:[/cyan]")
            try:
                from gateway.rns_bridge import get_gateway_stats
                stats = get_gateway_stats()
                if stats:
                    console.print(f"  Messages Mesh→RNS: {stats.get('messages_mesh_to_rns', 0)}")
                    console.print(f"  Messages RNS→Mesh: {stats.get('messages_rns_to_mesh', 0)}")
                    console.print(f"  Errors: {stats.get('errors', 0)}")
                    console.print(f"  Bounced: {stats.get('bounced', 0)}")
                else:
                    console.print("[yellow]Gateway not running or no stats available[/yellow]")
            except Exception as e:
                console.print(f"[red]Error getting stats: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "4":
            console.print("\n[cyan]Gateway Configuration:[/cyan]")
            config_path = get_real_user_home() / '.config' / 'meshforge' / 'gateway.json'
            if config_path.exists():
                subprocess.run(['nano', str(config_path)])  # No timeout for editor
            else:
                console.print(f"[yellow]Config not found at {config_path}[/yellow]")
                console.print("[dim]Start the gateway once to create default config[/dim]")
                input("\nPress Enter to continue...")
        elif choice == "5":
            # Show gateway templates
            template_dir = Path(__file__).parent.parent / 'templates' / 'available.d'
            gateway_templates = list(template_dir.glob('gateway-*.yaml'))
            if gateway_templates:
                console.print("\n[cyan]Available Gateway Templates:[/cyan]")
                for i, t in enumerate(gateway_templates, 1):
                    console.print(f"  {i}. {t.name}")
                # TODO: implement template selection
            else:
                console.print("[yellow]No gateway templates found[/yellow]")
            input("\nPress Enter to continue...")
        elif choice == "6":
            console.print("\n[cyan]Testing Meshtastic Connection...[/cyan]")
            try:
                result = subprocess.run(
                    ['meshtastic', '--host', 'localhost', '--info'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    console.print("[green]✓ Meshtastic connection OK[/green]")
                    # Show first few lines
                    lines = result.stdout.split('\n')[:10]
                    for line in lines:
                        console.print(f"  [dim]{line}[/dim]")
                else:
                    console.print(f"[red]✗ Connection failed: {result.stderr}[/red]")
            except Exception as e:
                console.print(f"[red]✗ Error: {e}[/red]")
            input("\nPress Enter to continue...")
        elif choice == "7":
            console.print("\n[cyan]Testing RNS Connection...[/cyan]")
            try:
                import RNS
                console.print(f"[green]✓ RNS library loaded (v{RNS.__version__})[/green]")

                # Try to connect to shared instance
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    sock.connect(('localhost', 37428))  # Default RNS shared instance port
                    console.print("[green]✓ RNS shared instance reachable[/green]")
                except Exception:
                    console.print("[yellow]⚠ RNS shared instance not reachable on port 37428[/yellow]")
                    console.print("[dim]  Start rnsd or check if it's using a different port[/dim]")
                finally:
                    sock.close()
            except ImportError:
                console.print("[red]✗ RNS library not installed[/red]")
                console.print("[dim]  Install with: pip3 install rns[/dim]")
            except Exception as e:
                console.print(f"[red]✗ Error: {e}[/red]")
            input("\nPress Enter to continue...")


def ham_radio_menu():
    """Ham Radio tools menu - HamClock, propagation, callsign lookup"""
    console.print("\n[bold cyan]═══════════ Ham Radio Tools ═══════════[/bold cyan]\n")

    while True:
        # Check HamClock service status
        hamclock_status = "unknown"
        hamclock_port = 8080
        try:
            result = subprocess.run(['systemctl', 'is-active', 'hamclock'],
                                   capture_output=True, text=True, timeout=5)
            hamclock_status = result.stdout.strip()
        except Exception:
            pass

        console.print(f"[dim]HamClock: {hamclock_status}[/dim]\n")

        console.print("[dim cyan]-- HamClock Service --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('▶️')}  Start HamClock")
        console.print(f"  [bold]2[/bold]. {em.get('⏹️')}  Stop HamClock")
        console.print(f"  [bold]3[/bold]. {em.get('🔄')} Restart HamClock")
        console.print(f"  [bold]4[/bold]. {em.get('🌐')} Open HamClock Web UI")

        console.print("\n[dim cyan]-- Space Weather --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('☀️')}  Current Space Weather")
        console.print(f"  [bold]6[/bold]. {em.get('📡')} Solar/HF Propagation")

        console.print("\n[dim cyan]-- Callsign Tools --[/dim cyan]")
        console.print(f"  [bold]7[/bold]. {em.get('🔍')} Callsign Lookup (QRZ)")

        console.print("\n[dim cyan]-- Installation --[/dim cyan]")
        console.print(f"  [bold]8[/bold]. {em.get('📦')} Install HamClock")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            _run_service_command('hamclock', 'start')
        elif choice == "2":
            _run_service_command('hamclock', 'stop')
        elif choice == "3":
            _run_service_command('hamclock', 'restart')
        elif choice == "4":
            # Open HamClock web UI
            import socket
            hostname = socket.gethostname()
            try:
                # Get local IP
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = "localhost"
            url = f"http://{local_ip}:{hamclock_port}"
            console.print(f"\n[cyan]HamClock URL: {url}[/cyan]")
            console.print("[dim]Opening in browser (if available)...[/dim]")
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                console.print(f"[yellow]Open manually: {url}[/yellow]")
            input("\nPress Enter to continue...")
        elif choice == "5":
            _show_space_weather()
        elif choice == "6":
            _show_propagation()
        elif choice == "7":
            _callsign_lookup()
        elif choice == "8":
            _install_hamclock()


def _show_space_weather():
    """Display current space weather conditions"""
    console.print("\n[bold cyan]═══ Current Space Weather ═══[/bold cyan]\n")
    try:
        from utils.space_weather import SpaceWeatherAPI
        api = SpaceWeatherAPI()
        data = api.get_current()
        if data:
            table = Table(title="Space Weather Conditions")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")
            table.add_column("Status", style="yellow")

            table.add_row("Solar Flux Index (SFI)", str(data.sfi), _sfi_status(data.sfi))
            table.add_row("A-Index", str(data.a_index), _a_index_status(data.a_index))
            table.add_row("K-Index", str(data.k_index), _k_index_status(data.k_index))
            table.add_row("Sunspot Number", str(data.sunspots), "")
            table.add_row("X-Ray Flux", data.xray_class or "N/A", "")

            console.print(table)
        else:
            console.print("[yellow]Could not fetch space weather data[/yellow]")
    except ImportError:
        console.print("[yellow]Space weather module not available[/yellow]")
        console.print("[dim]Fetching from NOAA directly...[/dim]")
        _fetch_noaa_direct()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    input("\nPress Enter to continue...")


def _sfi_status(sfi: int) -> str:
    """Return status for Solar Flux Index"""
    if sfi >= 150:
        return "[green]Excellent[/green]"
    elif sfi >= 100:
        return "[green]Good[/green]"
    elif sfi >= 70:
        return "[yellow]Fair[/yellow]"
    else:
        return "[red]Poor[/red]"


def _a_index_status(a: int) -> str:
    """Return status for A-Index"""
    if a <= 7:
        return "[green]Quiet[/green]"
    elif a <= 15:
        return "[yellow]Unsettled[/yellow]"
    elif a <= 30:
        return "[yellow]Active[/yellow]"
    else:
        return "[red]Storm[/red]"


def _k_index_status(k: int) -> str:
    """Return status for K-Index"""
    if k <= 2:
        return "[green]Quiet[/green]"
    elif k <= 4:
        return "[yellow]Unsettled[/yellow]"
    elif k <= 5:
        return "[yellow]Minor Storm[/yellow]"
    else:
        return "[red]Major Storm[/red]"


def _fetch_noaa_direct():
    """Fetch space weather directly from NOAA"""
    import urllib.request
    try:
        # NOAA Space Weather Prediction Center
        url = "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json"
        with urllib.request.urlopen(url, timeout=10) as response:
            import json
            data = json.loads(response.read().decode())
            console.print(f"[cyan]Solar Wind Speed:[/cyan] {data.get('WindSpeed', 'N/A')} km/s")
            console.print(f"[cyan]Bt (IMF):[/cyan] {data.get('Bt', 'N/A')} nT")
            console.print(f"[cyan]Bz:[/cyan] {data.get('Bz', 'N/A')} nT")
    except Exception as e:
        console.print(f"[red]Could not fetch NOAA data: {e}[/red]")


def _show_propagation():
    """Show HF propagation predictions"""
    console.print("\n[bold cyan]═══ HF Propagation ═══[/bold cyan]\n")
    console.print("[dim]Fetching propagation data...[/dim]")

    try:
        import urllib.request
        # NOAA propagation data
        url = "https://services.swpc.noaa.gov/text/wwv.txt"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = response.read().decode()
            console.print(f"\n[cyan]WWV Propagation Report:[/cyan]\n")
            console.print(data[:2000])  # First 2000 chars
    except Exception as e:
        console.print(f"[red]Could not fetch propagation data: {e}[/red]")

    console.print("\n[dim]For detailed predictions, visit:[/dim]")
    console.print("  https://www.swpc.noaa.gov/products/hf-radio-blackout")
    console.print("  https://prop.kc2g.com/")
    input("\nPress Enter to continue...")


def _callsign_lookup():
    """Look up amateur radio callsign"""
    console.print("\n[bold cyan]═══ Callsign Lookup ═══[/bold cyan]\n")
    callsign = Prompt.ask("[cyan]Enter callsign[/cyan]").upper().strip()
    if not callsign:
        return

    console.print(f"\n[dim]Looking up {callsign}...[/dim]")

    try:
        import urllib.request
        # Use HamQTH or similar free API
        url = f"https://www.hamqth.com/dxcc_json.php?callsign={callsign}"
        with urllib.request.urlopen(url, timeout=10) as response:
            import json
            data = json.loads(response.read().decode())
            if 'dxcc' in data:
                d = data['dxcc']
                console.print(f"\n[green]Results for {callsign}:[/green]")
                console.print(f"  [cyan]Country:[/cyan] {d.get('name', 'Unknown')}")
                console.print(f"  [cyan]Continent:[/cyan] {d.get('continent', 'Unknown')}")
                console.print(f"  [cyan]CQ Zone:[/cyan] {d.get('cq', 'Unknown')}")
                console.print(f"  [cyan]ITU Zone:[/cyan] {d.get('itu', 'Unknown')}")
                console.print(f"  [cyan]DXCC:[/cyan] {d.get('adif', 'Unknown')}")
            else:
                console.print(f"[yellow]No DXCC info found for {callsign}[/yellow]")
    except Exception as e:
        console.print(f"[red]Lookup failed: {e}[/red]")

    console.print(f"\n[dim]For more info, visit: https://www.qrz.com/db/{callsign}[/dim]")
    input("\nPress Enter to continue...")


def _install_hamclock():
    """Install HamClock service"""
    console.print("\n[bold cyan]═══ Install HamClock ═══[/bold cyan]\n")
    console.print("[dim]HamClock by Clear Sky Institute[/dim]")
    console.print("[dim]https://www.clearskyinstitute.com/ham/HamClock/[/dim]\n")

    console.print("Installation options:")
    console.print("  1. Install via apt (Debian/Ubuntu)")
    console.print("  2. Install from source")
    console.print("  3. Use Docker container")
    console.print("  0. Cancel")

    choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=["0", "1", "2", "3"], default="0")

    if choice == "0":
        return
    elif choice == "1":
        console.print("\n[cyan]Installing HamClock...[/cyan]")
        # Check if hamclock-systemd repo exists
        console.print("[dim]Adding repository and installing...[/dim]")
        subprocess.run(['apt', 'update'], timeout=60)
        result = subprocess.run(['apt', 'install', '-y', 'hamclock'], timeout=300)
        if result.returncode == 0:
            console.print("[green]HamClock installed successfully[/green]")
        else:
            console.print("[yellow]Package not found. Try source install.[/yellow]")
    elif choice == "2":
        console.print("\n[cyan]For source installation, visit:[/cyan]")
        console.print("  https://github.com/pa28/hamclock-systemd")
        console.print("\n[dim]Clone and run: make && sudo make install[/dim]")
    elif choice == "3":
        console.print("\n[cyan]Docker installation:[/cyan]")
        console.print("  docker pull ghcr.io/pa28/hamclock:latest")
        console.print("  docker run -d -p 8080:8080 ghcr.io/pa28/hamclock")

    input("\nPress Enter to continue...")


def aredn_menu():
    """AREDN network tools menu"""
    console.print("\n[bold cyan]═══════════ AREDN Tools ═══════════[/bold cyan]\n")
    console.print("[dim]Amateur Radio Emergency Data Network[/dim]\n")

    while True:
        console.print("[dim cyan]-- Node Discovery --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('🔍')} Scan for AREDN Nodes")
        console.print(f"  [bold]2[/bold]. {em.get('📋')} List Known Nodes")
        console.print(f"  [bold]3[/bold]. {em.get('📊')} View Node Details")

        console.print("\n[dim cyan]-- Network --[/dim cyan]")
        console.print(f"  [bold]4[/bold]. {em.get('🌐')} Open AREDN Web UI")
        console.print(f"  [bold]5[/bold]. {em.get('📡')} Show Network Topology")

        console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
        console.print(f"  [bold]6[/bold]. {em.get('⚙️')}  Configure Scan Settings")

        console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                          choices=["0", "1", "2", "3", "4", "5", "6"],
                          default="0")

        if choice == "0":
            break
        elif choice == "1":
            _scan_aredn_nodes()
        elif choice == "2":
            _list_aredn_nodes()
        elif choice == "3":
            _view_aredn_node_details()
        elif choice == "4":
            _open_aredn_webui()
        elif choice == "5":
            _show_aredn_topology()
        elif choice == "6":
            _configure_aredn_settings()


def _scan_aredn_nodes():
    """Scan for AREDN nodes on the network"""
    console.print("\n[bold cyan]═══ Scanning for AREDN Nodes ═══[/bold cyan]\n")

    # Load settings
    settings = _load_aredn_settings()
    subnet = settings.get('scan_subnet', '10.0.0.0/24')

    console.print(f"[dim]Scanning subnet: {subnet}[/dim]")
    console.print("[dim]This may take a moment...[/dim]\n")

    try:
        from utils.aredn import AREDNScanner
        scanner = AREDNScanner()
        nodes = scanner.scan(subnet, timeout=settings.get('timeout', 3))

        if nodes:
            table = Table(title=f"AREDN Nodes Found ({len(nodes)})")
            table.add_column("Node Name", style="cyan")
            table.add_column("IP Address", style="green")
            table.add_column("Model", style="yellow")
            table.add_column("Links", style="magenta")

            for node in nodes:
                table.add_row(
                    node.name,
                    node.ip,
                    node.model or "Unknown",
                    str(len(node.links)) if hasattr(node, 'links') else "?"
                )
            console.print(table)

            # Save to known nodes
            _save_aredn_nodes(nodes)
        else:
            console.print("[yellow]No AREDN nodes found on {subnet}[/yellow]")
            console.print("[dim]Check that you're on the AREDN network (10.x.x.x)[/dim]")
    except ImportError:
        console.print("[yellow]AREDN utilities not available[/yellow]")
        console.print("[dim]Install with: pip3 install aiohttp[/dim]")
        _scan_aredn_basic(subnet)
    except Exception as e:
        console.print(f"[red]Scan failed: {e}[/red]")

    input("\nPress Enter to continue...")


def _scan_aredn_basic(subnet: str):
    """Basic AREDN scan using ping"""
    console.print("\n[dim]Falling back to basic ping scan...[/dim]")
    import ipaddress
    try:
        network = ipaddress.ip_network(subnet, strict=False)
        found = []
        # Only scan first 254 hosts
        hosts = list(network.hosts())[:254]
        for i, ip in enumerate(hosts):
            if i % 50 == 0:
                console.print(f"[dim]Progress: {i}/{len(hosts)}...[/dim]")
            try:
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', str(ip)],
                    capture_output=True, timeout=2
                )
                if result.returncode == 0:
                    found.append(str(ip))
                    console.print(f"[green]Found: {ip}[/green]")
            except Exception:
                pass
        if found:
            console.print(f"\n[green]Found {len(found)} responsive hosts[/green]")
        else:
            console.print("[yellow]No hosts responded[/yellow]")
    except Exception as e:
        console.print(f"[red]Scan error: {e}[/red]")


def _list_aredn_nodes():
    """List previously discovered AREDN nodes"""
    console.print("\n[bold cyan]═══ Known AREDN Nodes ═══[/bold cyan]\n")
    settings = _load_aredn_settings()
    nodes = settings.get('known_nodes', [])

    if not nodes:
        console.print("[yellow]No known nodes. Run a scan first.[/yellow]")
    else:
        table = Table(title=f"Known Nodes ({len(nodes)})")
        table.add_column("Name", style="cyan")
        table.add_column("IP", style="green")
        table.add_column("Last Seen", style="yellow")

        for node in nodes:
            table.add_row(
                node.get('name', 'Unknown'),
                node.get('ip', 'Unknown'),
                node.get('last_seen', 'Unknown')
            )
        console.print(table)

    input("\nPress Enter to continue...")


def _view_aredn_node_details():
    """View details of a specific AREDN node"""
    ip = Prompt.ask("[cyan]Enter node IP address[/cyan]")
    if not ip:
        return

    console.print(f"\n[dim]Fetching details for {ip}...[/dim]")

    try:
        import urllib.request
        # AREDN nodes have a standard API endpoint
        url = f"http://{ip}:8080/cgi-bin/sysinfo.json"
        with urllib.request.urlopen(url, timeout=5) as response:
            import json
            data = json.loads(response.read().decode())

            console.print(f"\n[bold green]Node: {data.get('node', 'Unknown')}[/bold green]")
            console.print(f"  [cyan]Model:[/cyan] {data.get('model', 'Unknown')}")
            console.print(f"  [cyan]Firmware:[/cyan] {data.get('firmware_version', 'Unknown')}")
            console.print(f"  [cyan]Channel:[/cyan] {data.get('channel', 'Unknown')}")
            console.print(f"  [cyan]Bandwidth:[/cyan] {data.get('chanbw', 'Unknown')} MHz")

            if 'meshrf' in data:
                rf = data['meshrf']
                console.print(f"  [cyan]SSID:[/cyan] {rf.get('ssid', 'Unknown')}")
                console.print(f"  [cyan]Frequency:[/cyan] {rf.get('freq', 'Unknown')} MHz")
    except Exception as e:
        console.print(f"[red]Could not fetch node details: {e}[/red]")
        console.print("[dim]The node may not have the API enabled[/dim]")

    input("\nPress Enter to continue...")


def _open_aredn_webui():
    """Open AREDN node web UI"""
    ip = Prompt.ask("[cyan]Enter node IP (default: localnode.local.mesh)[/cyan]",
                   default="localnode.local.mesh")
    url = f"http://{ip}:8080"
    console.print(f"\n[cyan]Opening: {url}[/cyan]")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        console.print(f"[yellow]Open manually: {url}[/yellow]")
    input("\nPress Enter to continue...")


def _show_aredn_topology():
    """Show AREDN network topology"""
    console.print("\n[bold cyan]═══ AREDN Network Topology ═══[/bold cyan]\n")
    console.print("[dim]Fetching topology data...[/dim]")

    settings = _load_aredn_settings()
    nodes = settings.get('known_nodes', [])

    if not nodes:
        console.print("[yellow]No known nodes. Run a scan first.[/yellow]")
        input("\nPress Enter to continue...")
        return

    # Build simple text-based topology
    console.print("\n[cyan]Network Topology:[/cyan]\n")
    for node in nodes:
        console.print(f"  {em.get('📡')} {node.get('name', 'Unknown')} ({node.get('ip', '?')})")
        links = node.get('links', [])
        for link in links:
            console.print(f"      └── {link.get('name', 'Unknown')} (SNR: {link.get('snr', '?')})")

    console.print("\n[dim]For graphical topology, visit your local AREDN node's mesh status page[/dim]")
    input("\nPress Enter to continue...")


def _configure_aredn_settings():
    """Configure AREDN scan settings"""
    console.print("\n[bold cyan]═══ AREDN Settings ═══[/bold cyan]\n")

    settings = _load_aredn_settings()

    current_subnet = settings.get('scan_subnet', '10.0.0.0/24')
    current_timeout = settings.get('timeout', 3)

    console.print(f"[dim]Current subnet: {current_subnet}[/dim]")
    console.print(f"[dim]Current timeout: {current_timeout}s[/dim]\n")

    new_subnet = Prompt.ask("[cyan]Scan subnet[/cyan]", default=current_subnet)
    new_timeout = Prompt.ask("[cyan]Timeout (seconds)[/cyan]", default=str(current_timeout))

    settings['scan_subnet'] = new_subnet
    settings['timeout'] = int(new_timeout)

    _save_aredn_settings(settings)
    console.print("\n[green]Settings saved[/green]")
    input("\nPress Enter to continue...")


def _load_aredn_settings() -> dict:
    """Load AREDN settings from config file"""
    import json
    config_path = get_real_user_home() / '.config' / 'meshforge' / 'aredn.json'
    defaults = {
        'scan_subnet': '10.0.0.0/24',
        'timeout': 3,
        'known_nodes': []
    }
    try:
        if config_path.exists():
            with open(config_path) as f:
                saved = json.load(f)
                defaults.update(saved)
    except Exception:
        pass
    return defaults


def _save_aredn_settings(settings: dict):
    """Save AREDN settings to config file"""
    import json
    config_path = get_real_user_home() / '.config' / 'meshforge' / 'aredn.json'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(settings, f, indent=2)


def _save_aredn_nodes(nodes):
    """Save discovered AREDN nodes"""
    import json
    from datetime import datetime
    settings = _load_aredn_settings()
    known = []
    for node in nodes:
        known.append({
            'name': node.name,
            'ip': node.ip,
            'model': getattr(node, 'model', None),
            'last_seen': datetime.now().isoformat()
        })
    settings['known_nodes'] = known
    _save_aredn_settings(settings)


def _run_service_command(service: str, action: str):
    """Run a systemctl command on a service"""
    console.print(f"\n[cyan]{action.capitalize()}ing {service}...[/cyan]")
    try:
        result = subprocess.run(
            ['systemctl', action, service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            console.print(f"[green]✓ {service} {action}ed successfully[/green]")
        else:
            console.print(f"[red]✗ Failed: {result.stderr}[/red]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    input("\nPress Enter to continue...")


def _install_meshtastic_interface():
    """Download and install Meshtastic_Interface.py for RNS"""
    import requests

    console.print("\n[bold cyan]Install Meshtastic Interface[/bold cyan]")
    console.print("[dim]RNS interface for Meshtastic LoRa transport[/dim]")
    console.print("[dim]Source: github.com/Nursedude/RNS_Over_Meshtastic_Gateway[/dim]\n")

    # Use get_real_user_home for sudo compatibility
    real_home = get_real_user_home()
    interfaces_dir = real_home / '.reticulum' / 'interfaces'
    interface_file = interfaces_dir / 'Meshtastic_Interface.py'

    # Check if already installed
    if interface_file.exists():
        console.print(f"[yellow]Meshtastic_Interface.py already exists at:[/yellow]")
        console.print(f"  {interface_file}")
        if not Confirm.ask("\n[cyan]Overwrite with latest version?[/cyan]", default=False):
            return

    # Create interfaces directory if needed
    try:
        interfaces_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Interfaces directory: {interfaces_dir}")
    except PermissionError:
        console.print(f"[red]✗ Cannot create {interfaces_dir} - permission denied[/red]")
        console.print("[dim]Try running without sudo, or check directory permissions[/dim]")
        input("\nPress Enter to continue...")
        return

    # Download from Nursedude's fork
    url = "https://raw.githubusercontent.com/Nursedude/RNS_Over_Meshtastic_Gateway/main/Meshtastic_Interface.py"
    console.print(f"\n[cyan]Downloading from {url}...[/cyan]")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        interface_file.write_text(response.text)
        console.print(f"[green]✓ Installed: {interface_file}[/green]")

        # Show config snippet
        console.print("\n[bold]Add to ~/.reticulum/config:[/bold]")
        console.print("""
[dim][[Meshtastic LoRa]]
  type = Meshtastic_Interface
  enabled = true
  # Connection method (choose one):
  # port = /dev/ttyUSB0     # Serial
  # ble_addr = AA:BB:CC:DD  # Bluetooth
  # tcp_addr = 127.0.0.1    # TCP (meshtasticd)
  tcp_addr = 127.0.0.1
  tcp_port = 4403
  data_speed = 8            # 0-8, higher = faster (Short Turbo = 8)[/dim]
""")
        console.print("[dim]Note: data_speed 8 requires SHORT_TURBO modem preset[/dim]")

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Download failed: {e}[/red]")
    except PermissionError:
        console.print(f"[red]✗ Cannot write to {interface_file} - permission denied[/red]")
    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")

    input("\nPress Enter to continue...")


def _create_rns_config_wizard():
    """Create or reconfigure ~/.reticulum/config with templates"""
    console.print("\n[bold cyan]═══════════ RNS Configuration Wizard ═══════════[/bold cyan]")
    console.print("[dim]Configure Reticulum Network Stack[/dim]\n")

    real_home = get_real_user_home()
    config_dir = real_home / '.reticulum'
    config_file = config_dir / 'config'

    # Check if config exists
    if config_file.exists():
        console.print(f"[yellow]Existing config found: {config_file}[/yellow]")
        if not Confirm.ask("Overwrite with new configuration?", default=False):
            console.print("[dim]Keeping existing config[/dim]")
            input("\nPress Enter to continue...")
            return

    # Template selection
    console.print("\n[bold]Step 1: Select Configuration Template[/bold]\n")

    templates = {
        '1': ('Local Only', 'AutoInterface for LAN discovery only'),
        '2': ('Gateway Node', 'Transport + AutoInterface + TCP Server (host)'),
        '3': ('Client + Testnet', 'AutoInterface + RNS Testnet connections'),
        '4': ('Connect to Server', 'AutoInterface + TCPClientInterface (join network)'),
        '5': ('Meshtastic Bridge', 'AutoInterface + Meshtastic_Interface'),
        '6': ('Full Gateway', 'Transport + TCP Server + Meshtastic + Client'),
        '7': ('Gateway Client', 'TCPClient + Meshtastic (NO AutoInterface) - MOC2 style'),
    }

    for key, (name, desc) in templates.items():
        console.print(f"  [bold]{key}[/bold]. {name} - [dim]{desc}[/dim]")

    template_choice = Prompt.ask("\n[cyan]Select template[/cyan]",
                                 choices=list(templates.keys()), default="1")

    # Build configuration based on template
    config = _build_rns_config(template_choice)

    # Additional options
    console.print("\n[bold]Step 2: Additional Options[/bold]\n")

    # Instance name (for multiple RNS instances)
    set_instance = Confirm.ask("Set custom instance name? (for multiple RNS instances)", default=False)
    if set_instance:
        import socket
        default_name = socket.gethostname() + " RNS"
        instance_name = Prompt.ask("  Instance name", default=default_name)
        config = config.replace('# instance_name = default', f'instance_name = {instance_name}')

    # Transport node
    if template_choice in ['2', '6']:
        console.print("[green]✓ Transport enabled (routing for other nodes)[/green]")
    else:
        enable_transport = Confirm.ask("Enable transport (route traffic for others)?", default=False)
        if enable_transport:
            config = config.replace('enable_transport = False', 'enable_transport = True')

    # TCP Server port (for hosting)
    if template_choice in ['2', '6']:
        tcp_port = Prompt.ask("TCP Server port (for incoming connections)", default="4242")
        config = config.replace('listen_port = 4242', f'listen_port = {tcp_port}')

    # TCP Client (connect to remote server)
    if template_choice in ['4', '6', '7']:
        console.print("\n[bold]TCP Client Connection (connect to remote RNS node):[/bold]")
        target_host = Prompt.ask("  Target host/IP", default="192.168.1.1")
        target_port = Prompt.ask("  Target port", default="4242")
        conn_name = Prompt.ask("  Connection name", default="Remote RNS")
        config = config.replace('target_host = remote.example.com', f'target_host = {target_host}')
        config = config.replace('target_port = 4242', f'target_port = {target_port}')
        config = config.replace('name = Remote Server', f'name = {conn_name}')

    # RNode LoRa interface (direct RNode, not Meshtastic)
    add_rnode = Confirm.ask("\nAdd RNode LoRa interface? (direct RNode hardware)", default=False)
    if add_rnode:
        config = _add_rnode_interface(config)

    # Meshtastic interface
    if template_choice in ['5', '6', '7']:
        console.print("\n[bold]Meshtastic Interface Config:[/bold]")
        console.print("  [dim]Connection method:[/dim]")
        console.print("    1. TCP (meshtasticd on localhost:4403)")
        console.print("    2. Serial (/dev/ttyUSB0)")
        console.print("    3. BLE (Bluetooth)")

        mesh_conn = Prompt.ask("  Connection type", choices=["1", "2", "3"], default="1")

        if mesh_conn == "1":
            config = config.replace('# tcp_port = 127.0.0.1:4403', 'tcp_port = 127.0.0.1:4403')
            config = config.replace('port = /dev/ttyUSB0', '# port = /dev/ttyUSB0')
        elif mesh_conn == "2":
            port = Prompt.ask("  Serial port", default="/dev/ttyUSB0")
            config = config.replace('port = /dev/ttyUSB0', f'port = {port}')
        else:
            ble_addr = Prompt.ask("  BLE device name/address", default="RNode_1234")
            config = config.replace('port = /dev/ttyUSB0', f'# port = /dev/ttyUSB0')
            config = config.replace('# ble_port = RNode_1234', f'ble_port = {ble_addr}')

        data_speed = Prompt.ask("  Data speed (0=LongFast, 8=Turbo)", default="8")
        config = config.replace('data_speed = 8', f'data_speed = {data_speed}')

    # Display preview
    console.print("\n[bold]Configuration Preview:[/bold]")
    console.print("[dim]" + "─" * 50 + "[/dim]")
    # Show first 30 lines
    preview_lines = config.split('\n')[:35]
    for line in preview_lines:
        if line.startswith('#'):
            console.print(f"[dim]{line}[/dim]")
        elif line.startswith('['):
            console.print(f"[cyan]{line}[/cyan]")
        elif '=' in line and not line.strip().startswith('#'):
            console.print(f"[green]{line}[/green]")
        else:
            console.print(line)
    console.print("[dim]... (truncated)[/dim]")
    console.print("[dim]" + "─" * 50 + "[/dim]")

    # Save config
    if Confirm.ask("\n[cyan]Save this configuration?[/cyan]", default=True):
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file.write_text(config)
            console.print(f"\n[green]✓ Config saved to {config_file}[/green]")

            # Offer to create rnsd service
            if not Path('/etc/systemd/system/rnsd.service').exists():
                if Confirm.ask("\nCreate rnsd systemd service?", default=True):
                    _create_rnsd_service()

            console.print("\n[bold yellow]Next steps:[/bold yellow]")
            console.print("  1. Start rnsd: [cyan]sudo systemctl start rnsd[/cyan]")
            console.print("  2. Check status: [cyan]rnstatus[/cyan]")
            console.print("  3. View logs: [cyan]journalctl -u rnsd -f[/cyan]")

        except PermissionError:
            console.print(f"[red]✗ Cannot write to {config_file} - permission denied[/red]")
        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")
    else:
        console.print("[yellow]Configuration cancelled[/yellow]")

    input("\nPress Enter to continue...")


def _build_rns_config(template: str) -> str:
    """Build RNS config based on template choice"""

    # Base config
    base = '''# Reticulum Network Stack Configuration
# Generated by MeshForge
# Reference: https://reticulum.network/manual/interfaces.html

[reticulum]
enable_transport = False
share_instance = Yes
shared_instance_port = 37428
# instance_name = default
panic_on_interface_error = No

[logging]
loglevel = 4

[interfaces]
'''

    # AutoInterface (all templates)
    auto_interface = '''
# Local network discovery
[[Default Interface]]
    type = AutoInterface
    enabled = Yes
'''

    # TCP Server (for gateway templates)
    tcp_server = '''
# Accept incoming RNS connections
[[TCP Server]]
    type = TCPServerInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
'''

    # Testnet connections
    testnet = '''
# RNS Testnet - Dublin
[[RNS Testnet Dublin]]
    type = TCPClientInterface
    enabled = Yes
    target_host = dublin.connect.reticulum.network
    target_port = 4965

# RNS Testnet - BetweenTheBorders
[[RNS Testnet BTB]]
    type = TCPClientInterface
    enabled = Yes
    target_host = reticulum.betweentheborders.com
    target_port = 4242
'''

    # Meshtastic interface
    meshtastic = '''
# Meshtastic LoRa Bridge
# Requires: Meshtastic_Interface.py in ~/.reticulum/interfaces/
[[Meshtastic LoRa]]
    type = Meshtastic_Interface
    enabled = Yes
    mode = gateway
    port = /dev/ttyUSB0
    # tcp_port = 127.0.0.1:4403
    # ble_port = RNode_1234
    data_speed = 8
    hop_limit = 3
'''

    # TCP Client (connect to remote RNS server)
    tcp_client = '''
# Connect to remote RNS server
[[Remote Server]]
    type = TCPClientInterface
    enabled = Yes
    target_host = remote.example.com
    target_port = 4242
    name = Remote Server
'''

    # Build based on template
    if template == '1':  # Local Only
        return base + auto_interface

    elif template == '2':  # Gateway Node (host)
        config = base.replace('enable_transport = False', 'enable_transport = True')
        return config + auto_interface + tcp_server

    elif template == '3':  # Client + Testnet
        return base + auto_interface + testnet

    elif template == '4':  # Connect to Server (TCPClientInterface)
        return base + auto_interface + tcp_client

    elif template == '5':  # Meshtastic Bridge
        return base + auto_interface + meshtastic

    elif template == '6':  # Full Gateway (everything)
        config = base.replace('enable_transport = False', 'enable_transport = True')
        return config + auto_interface + tcp_server + tcp_client + meshtastic

    elif template == '7':  # Gateway Client (MOC2 style - NO AutoInterface)
        return base + tcp_client + meshtastic

    return base + auto_interface


def _create_rnsd_service():
    """Create rnsd systemd service"""
    import pwd

    # Get the real user (not root if running with sudo)
    real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

    service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network.target

[Service]
Type=simple
User={real_user}
ExecStart=/usr/local/bin/rnsd
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''

    try:
        service_path = Path('/etc/systemd/system/rnsd.service')
        service_path.write_text(service_content)
        subprocess.run(['systemctl', 'daemon-reload'], timeout=10)
        subprocess.run(['systemctl', 'enable', 'rnsd'], timeout=10)
        console.print("[green]✓ rnsd service created and enabled[/green]")
    except PermissionError:
        console.print("[red]✗ Need sudo to create systemd service[/red]")
        console.print(f"[dim]Run: sudo tee /etc/systemd/system/rnsd.service << 'EOF'\n{service_content}EOF[/dim]")
    except Exception as e:
        console.print(f"[red]✗ Error creating service: {e}[/red]")


def _add_rnode_interface(config: str) -> str:
    """Add RNode LoRa interface configuration interactively"""
    console.print("\n[bold]RNode LoRa Interface Config:[/bold]")
    console.print("[dim]Direct RNode hardware (not Meshtastic bridge)[/dim]\n")

    # Interface name
    import socket
    default_name = socket.gethostname() + " rnode"
    iface_name = Prompt.ask("  Interface name", default=default_name)

    # Serial port
    console.print("  [dim]Common ports: /dev/ttyACM0, /dev/ttyUSB0[/dim]")
    port = Prompt.ask("  Serial port", default="/dev/ttyACM0")

    # Frequency (US 900 MHz band)
    console.print("  [dim]US frequencies: 902-928 MHz (e.g., 903625000 = 903.625 MHz)[/dim]")
    frequency = Prompt.ask("  Frequency (Hz)", default="903625000")

    # TX Power
    console.print("  [dim]TX power: typically 2-22 dBm depending on hardware[/dim]")
    txpower = Prompt.ask("  TX Power (dBm)", default="22")

    # Bandwidth
    bandwidths = {'1': '125000', '2': '250000', '3': '500000'}
    console.print("  Bandwidth: 1=125kHz, 2=250kHz, 3=500kHz")
    bw_choice = Prompt.ask("  Select bandwidth", choices=["1", "2", "3"], default="2")
    bandwidth = bandwidths[bw_choice]

    # Spreading factor
    console.print("  [dim]Spreading factor: 7-12 (lower=faster, higher=longer range)[/dim]")
    sf = Prompt.ask("  Spreading factor", default="7")

    # Coding rate
    console.print("  [dim]Coding rate: 5-8 (5=4/5, 6=4/6, 7=4/7, 8=4/8)[/dim]")
    cr = Prompt.ask("  Coding rate", default="5")

    rnode_config = f'''
# RNode LoRa Interface
[[{iface_name}]]
    type = RNodeInterface
    interface_enabled = True
    port = {port}
    frequency = {frequency}
    txpower = {txpower}
    bandwidth = {bandwidth}
    spreadingfactor = {sf}
    codingrate = {cr}
'''

    console.print(f"\n[green]✓ RNode interface '{iface_name}' configured[/green]")
    return config + rnode_config


def network_tools_menu():
    """Network tools menu (TCP/IP, ping, scanning)"""
    from tools.network_tools import NetworkTools

    tools = NetworkTools()
    tools.interactive_menu()


def rf_tools_menu():
    """RF tools menu (link budget, LoRa analysis)"""
    from tools.rf_tools import RFTools

    tools = RFTools()
    tools.interactive_menu()


def mudp_tools_menu():
    """MUDP tools menu (UDP, multicast)"""
    from tools.mudp_tools import MUDPTools

    tools = MUDPTools()
    tools.interactive_menu()


def tool_manager_menu():
    """Tool manager menu (install, update, version)"""
    from tools.tool_manager import ToolManager

    manager = ToolManager()
    manager.interactive_menu()


def hardware_config_menu():
    """Hardware configuration menu (SPI, Serial, GPIO)"""
    from config.hardware_config import HardwareConfigurator

    configurator = HardwareConfigurator()
    configurator.interactive_menu()


def device_wizard():
    """
    Industrial-class device detection and configuration wizard.
    Scans USB, SPI, TCP, and BLE for Meshtastic devices.
    """
    import socket
    from utils.device_scanner import DeviceScanner

    console.print("\n[bold cyan]═══════════ MeshForge Device Wizard ═══════════[/bold cyan]")
    console.print("[dim]Industrial-class port detection for LoRa mesh devices[/dim]\n")

    devices_found = []

    # === SCAN USB DEVICES ===
    console.print("[cyan]Scanning USB ports...[/cyan]")
    try:
        scanner = DeviceScanner()
        scan_result = scanner.scan_all()

        for port in scan_result.get('serial_ports', []):
            if port.meshtastic_compatible:
                devices_found.append({
                    'type': 'USB',
                    'port': port.device,
                    'by_id': port.by_id or '',
                    'description': port.description or f"{port.usb_vendor}:{port.usb_product}",
                    'driver': port.driver,
                })

        console.print(f"  [green]✓[/green] Found {len(scan_result.get('serial_ports', []))} serial ports")
    except Exception as e:
        console.print(f"  [yellow]⚠ USB scan error: {e}[/yellow]")

    # === SCAN SPI DEVICES ===
    console.print("[cyan]Scanning SPI/GPIO...[/cyan]")
    spi_devices = []
    for spi_path in ['/dev/spidev0.0', '/dev/spidev0.1', '/dev/spidev1.0']:
        if Path(spi_path).exists():
            spi_devices.append(spi_path)

    if spi_devices:
        devices_found.append({
            'type': 'SPI',
            'port': spi_devices[0],
            'by_id': '',
            'description': 'SPI LoRa HAT (MeshAdv, Waveshare, etc.)',
            'driver': 'spidev',
        })
        console.print(f"  [green]✓[/green] Found SPI: {', '.join(spi_devices)}")
    else:
        console.print("  [dim]No SPI devices found[/dim]")

    # === SCAN TCP (meshtasticd) ===
    console.print("[cyan]Scanning TCP (meshtasticd)...[/cyan]")
    tcp_ports = [4403, 4404]  # Primary and alternate
    for tcp_port in tcp_ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', tcp_port))
            sock.close()
            if result == 0:
                devices_found.append({
                    'type': 'TCP',
                    'port': f'127.0.0.1:{tcp_port}',
                    'by_id': '',
                    'description': f'meshtasticd daemon (port {tcp_port})',
                    'driver': 'tcp',
                })
                console.print(f"  [green]✓[/green] Found meshtasticd on port {tcp_port}")
                break
        except Exception:
            pass
    else:
        console.print("  [dim]meshtasticd not running (TCP 4403/4404)[/dim]")

    # === DISPLAY RESULTS ===
    console.print(f"\n[bold]Found {len(devices_found)} Meshtastic-compatible device(s)[/bold]\n")

    if not devices_found:
        console.print("[yellow]No devices detected.[/yellow]")
        console.print("\n[dim]Tips:[/dim]")
        console.print("  • Connect a USB LoRa radio (T-Beam, Heltec, RAK, etc.)")
        console.print("  • Enable SPI in raspi-config for HAT devices")
        console.print("  • Start meshtasticd service for TCP connection")
        input("\nPress Enter to continue...")
        return

    # Build selection table
    table = Table(title="Detected Devices", show_header=True, header_style="bold magenta")
    table.add_column("#", style="cyan", width=3)
    table.add_column("Type", style="yellow", width=6)
    table.add_column("Port", style="green")
    table.add_column("Description", style="dim")

    for i, dev in enumerate(devices_found, 1):
        table.add_row(str(i), dev['type'], dev['port'], dev['description'])

    console.print(table)

    # === SELECT DEVICE ===
    choices = [str(i) for i in range(1, len(devices_found) + 1)] + ["0"]
    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back (no configuration)")

    choice = Prompt.ask("\n[cyan]Select device to configure[/cyan]",
                       choices=choices, default="1")

    if choice == "0":
        return

    selected = devices_found[int(choice) - 1]
    console.print(f"\n[green]Selected: {selected['type']} - {selected['port']}[/green]")

    # === CONFIGURE DEVICE ===
    _configure_device_wizard(selected)


def _configure_device_wizard(device: dict):
    """Walk through complete device configuration"""
    console.print("\n[bold cyan]═══════════ Device Configuration ═══════════[/bold cyan]")
    console.print(f"[dim]Configuring: {device['type']} at {device['port']}[/dim]\n")

    config = {'device': device}

    # --- Step 1: Node Identity ---
    console.print("[bold]Step 1: Node Identity[/bold]")

    long_name = Prompt.ask(
        "  Long name (up to 40 chars)",
        default="MeshForge Node"
    )[:40]
    config['long_name'] = long_name

    # Generate short name suggestion from long name
    suggested_short = ''.join(c for c in long_name[:4].upper() if c.isalnum())
    short_name = Prompt.ask(
        "  Short name (4 chars for mesh display)",
        default=suggested_short or "MESH"
    )[:4].upper()
    config['short_name'] = short_name

    console.print(f"  [green]✓[/green] Identity: {long_name} ({short_name})")

    # --- Step 2: Region ---
    console.print("\n[bold]Step 2: Region Selection[/bold]")

    regions = {
        '1': ('US', '902-928 MHz ISM'),
        '2': ('EU_868', '863-870 MHz'),
        '3': ('CN', '470-510 MHz'),
        '4': ('JP', '920-925 MHz'),
        '5': ('ANZ', '915-928 MHz Australia/NZ'),
        '6': ('KR', '920-923 MHz Korea'),
        '7': ('TW', '920-925 MHz Taiwan'),
        '8': ('RU', '868-870 MHz Russia'),
        '9': ('IN', '865-867 MHz India'),
    }

    for key, (code, desc) in regions.items():
        console.print(f"  [bold]{key}[/bold]. {code} - {desc}")

    region_choice = Prompt.ask("  Select region", choices=list(regions.keys()), default="1")
    config['region'] = regions[region_choice][0]
    console.print(f"  [green]✓[/green] Region: {config['region']}")

    # --- Step 3: Modem Preset ---
    console.print("\n[bold]Step 3: Modem Preset[/bold]")

    presets = {
        '1': ('LONG_FAST', 'Default - Good range/speed balance'),
        '2': ('SHORT_TURBO', 'High-speed gateway (~6.8 kbps, shorter range)'),
        '3': ('LONG_SLOW', 'Maximum range, slower speed'),
        '4': ('MEDIUM_FAST', 'Balanced for urban areas'),
        '5': ('LONG_MODERATE', 'Long range with moderate speed'),
    }

    for key, (name, desc) in presets.items():
        marker = " [cyan](Recommended for gateway)[/cyan]" if name == "SHORT_TURBO" else ""
        console.print(f"  [bold]{key}[/bold]. {name} - {desc}{marker}")

    preset_choice = Prompt.ask("  Select modem preset", choices=list(presets.keys()), default="1")
    config['modem_preset'] = presets[preset_choice][0]
    console.print(f"  [green]✓[/green] Preset: {config['modem_preset']}")

    # --- Step 4: Frequency Slot ---
    console.print("\n[bold]Step 4: Frequency Slot[/bold]")
    console.print("  [dim]Different slots avoid interference between networks[/dim]")
    console.print("  [dim]Slot 0 = default, Slot 8 = common gateway slot[/dim]")

    slot = Prompt.ask("  Frequency slot (0-103 for US)", default="0")
    try:
        config['frequency_slot'] = int(slot)
    except ValueError:
        config['frequency_slot'] = 0
    console.print(f"  [green]✓[/green] Slot: {config['frequency_slot']}")

    # --- Step 5: TX Power ---
    console.print("\n[bold]Step 5: TX Power[/bold]")
    console.print("  [dim]Higher = longer range, more power consumption[/dim]")
    console.print("  [dim]Standard: 20 dBm, High-power HAT: 30 dBm (1W)[/dim]")

    tx_power = Prompt.ask("  TX Power (dBm)", default="20")
    try:
        config['tx_power'] = int(tx_power)
    except ValueError:
        config['tx_power'] = 20
    console.print(f"  [green]✓[/green] TX Power: {config['tx_power']} dBm")

    # --- Step 6: Position (Optional) ---
    console.print("\n[bold]Step 6: Position (Optional)[/bold]")

    if Confirm.ask("  Set fixed position?", default=False):
        lat = Prompt.ask("  Latitude (e.g., 19.435175)", default="0.0")
        lon = Prompt.ask("  Longitude (e.g., -155.213842)", default="0.0")
        try:
            config['latitude'] = float(lat)
            config['longitude'] = float(lon)
            console.print(f"  [green]✓[/green] Position: {config['latitude']}, {config['longitude']}")
        except ValueError:
            console.print("  [yellow]Invalid coordinates - skipping position[/yellow]")
    else:
        console.print("  [dim]Position not set (use GPS or set later)[/dim]")

    # --- Step 7: MQTT ---
    console.print("\n[bold]Step 7: MQTT Policy[/bold]")

    mqtt_enabled = Confirm.ask("  Enable MQTT uplink?", default=False)
    config['mqtt_enabled'] = mqtt_enabled
    if mqtt_enabled:
        console.print("  [green]✓[/green] MQTT enabled")
    else:
        console.print("  [dim]MQTT disabled (recommended for gateway bridging to RNS)[/dim]")

    # === DISPLAY SUMMARY ===
    console.print("\n[bold cyan]═══════════ Configuration Summary ═══════════[/bold cyan]\n")

    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Setting", style="cyan")
    summary_table.add_column("Value", style="green")

    summary_table.add_row("Device", f"{config['device']['type']} - {config['device']['port']}")
    summary_table.add_row("Long Name", config['long_name'])
    summary_table.add_row("Short Name", config['short_name'])
    summary_table.add_row("Region", config['region'])
    summary_table.add_row("Modem Preset", config['modem_preset'])
    summary_table.add_row("Frequency Slot", str(config['frequency_slot']))
    summary_table.add_row("TX Power", f"{config['tx_power']} dBm")
    if 'latitude' in config:
        summary_table.add_row("Position", f"{config['latitude']}, {config['longitude']}")
    summary_table.add_row("MQTT", "Enabled" if config['mqtt_enabled'] else "Disabled")

    console.print(summary_table)

    # === APPLY CONFIGURATION ===
    if Confirm.ask("\n[cyan]Apply this configuration?[/cyan]", default=True):
        _apply_device_config(config)
    else:
        console.print("[yellow]Configuration cancelled[/yellow]")

    input("\nPress Enter to continue...")


def _apply_device_config(config: dict):
    """Apply configuration to the device via meshtastic CLI or meshtasticd"""
    console.print("\n[cyan]Applying configuration...[/cyan]")

    device = config['device']
    commands = []

    # Build meshtastic CLI commands
    if device['type'] == 'TCP':
        base_cmd = ['meshtastic', '--host', '127.0.0.1']
    elif device['type'] == 'USB':
        port = device.get('by_id') or device['port']
        base_cmd = ['meshtastic', '--port', port]
    else:
        # SPI - use TCP to meshtasticd
        base_cmd = ['meshtastic', '--host', '127.0.0.1']

    # Set owner/identity
    commands.append(base_cmd + ['--set-owner', config['long_name']])
    commands.append(base_cmd + ['--set-owner-short', config['short_name']])

    # Set region
    commands.append(base_cmd + ['--set', 'lora.region', config['region']])

    # Set modem preset
    commands.append(base_cmd + ['--set', 'lora.modem_preset', config['modem_preset']])

    # Set channel/frequency slot
    commands.append(base_cmd + ['--set', 'lora.channel_num', str(config['frequency_slot'])])

    # Set TX power
    commands.append(base_cmd + ['--set', 'lora.tx_power', str(config['tx_power'])])

    # Set position if provided
    if 'latitude' in config and 'longitude' in config:
        commands.append(base_cmd + ['--setlat', str(config['latitude'])])
        commands.append(base_cmd + ['--setlon', str(config['longitude'])])

    # Set MQTT
    mqtt_val = 'true' if config['mqtt_enabled'] else 'false'
    commands.append(base_cmd + ['--set', 'mqtt.enabled', mqtt_val])

    # Execute commands
    success_count = 0
    for cmd in commands:
        try:
            console.print(f"  [dim]Running: {' '.join(cmd[:4])}...[/dim]")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                success_count += 1
            else:
                console.print(f"  [yellow]Warning: {result.stderr.strip()}[/yellow]")
        except subprocess.TimeoutExpired:
            console.print(f"  [yellow]Command timed out[/yellow]")
        except FileNotFoundError:
            console.print("[red]meshtastic CLI not found. Install with: pip install meshtastic[/red]")
            return
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")

    console.print(f"\n[green]✓ Applied {success_count}/{len(commands)} settings[/green]")

    # Reminder about verification
    console.print("\n[bold yellow]Important:[/bold yellow]")
    console.print("  CLI settings may not always apply reliably (upstream bug).")
    console.print("  [cyan]Verify settings in browser: http://localhost:9443[/cyan]")


def full_radio_config_menu():
    """Full radio configuration (Mesh, MQTT, Channel, Position)"""
    from config.radio_config import RadioConfig

    config = RadioConfig()
    config.interactive_menu()


def show_dashboard():
    """Show the quick status dashboard"""
    from dashboard import StatusDashboard
    dashboard = StatusDashboard()
    dashboard.interactive_dashboard()


def show_help():
    """Display help information"""
    from rich.box import ROUNDED

    help_content = """
[bold cyan]Meshtasticd Interactive Installer & Manager[/bold cyan]
[dim]A comprehensive tool for installing and managing meshtasticd on Raspberry Pi[/dim]

[bold yellow]Quick Start Guide:[/bold yellow]

  [bold]1. First-time setup:[/bold]
     • Run option [cyan]8[/cyan] (Hardware detection) to verify your LoRa hardware
     • Run option [cyan]7[/cyan] (Check dependencies) to ensure all requirements are met
     • Run option [cyan]2[/cyan] (Install) to install meshtasticd

  [bold]2. Configuration:[/bold]
     • Use option [cyan]5[/cyan] (Channel Presets) for quick, pre-configured setups
     • Use option [cyan]4[/cyan] (Configure device) for detailed configuration
     • Use option [cyan]6[/cyan] (Templates) for hardware-specific configurations

  [bold]3. Monitoring:[/bold]
     • Option [cyan]1[/cyan] shows real-time status of your meshtasticd service

[bold yellow]Keyboard Shortcuts:[/bold yellow]
  • [cyan]Ctrl+C[/cyan] - Cancel current operation
  • [cyan]Enter[/cyan] - Accept default value (shown in brackets)

[bold yellow]Common Tasks:[/bold yellow]
  • [bold]Join MtnMesh network:[/bold] Use Channel Preset → MtnMesh Community
  • [bold]Maximum range:[/bold] Use Channel Preset → Emergency/SAR or Long Range
  • [bold]Urban deployment:[/bold] Use Channel Preset → Urban High-Density

[bold yellow]Getting More Help:[/bold yellow]
  • Documentation: https://meshtastic.org/docs
  • GitHub Issues: https://github.com/Nursedude/Meshtasticd_interactive_UI/issues
"""
    console.print(Panel(help_content, title=f"[bold cyan]{em.get('❓')} Help[/bold cyan]", border_style="cyan", box=ROUNDED))
    Prompt.ask("\n[dim]Press Enter to return to menu[/dim]")


def edit_config_yaml():
    """Interactive config.yaml editor"""
    from config.yaml_editor import ConfigYamlEditor

    editor = ConfigYamlEditor()
    editor.interactive_menu()


def service_management_menu():
    """Service management menu"""
    from services.service_manager import ServiceManager

    manager = ServiceManager()
    manager.interactive_menu()


def meshtastic_cli_menu():
    """Meshtastic CLI commands menu"""
    from cli.meshtastic_cli import MeshtasticCLI

    cli = MeshtasticCLI()
    cli.interactive_menu()


def config_file_manager_menu():
    """Config file manager - select yaml from available.d, edit with nano"""
    from config.config_file_manager import ConfigFileManager

    manager = ConfigFileManager()
    manager.interactive_menu()


def configure_channel_presets():
    """Configure channels using presets"""
    console.print("\n[bold cyan]Channel Configuration with Presets[/bold cyan]\n")

    from config.channel_presets import ChannelPresetManager

    preset_manager = ChannelPresetManager()
    config = preset_manager.select_preset()

    if config:
        if Confirm.ask("\nApply this configuration?", default=True):
            preset_manager.apply_preset_to_config(config)
            console.print("\n[green]Channel configuration applied![/green]")
    else:
        console.print("\n[yellow]Configuration cancelled[/yellow]")


def manage_templates():
    """Manage configuration templates"""
    console.print("\n[bold cyan]=============== Configuration Templates ===============[/bold cyan]\n")

    console.print("[dim cyan]-- Hardware Templates --[/dim cyan]")
    console.print(f"  [bold]1[/bold]. {em.get('🔧')} MeshAdv-Pi-Hat [yellow](1W High-Power SX1262)[/yellow]")
    console.print(f"  [bold]2[/bold]. {em.get('🔧')} MeshAdv-Mini (SX1262/SX1268 HAT)")
    console.print(f"  [bold]3[/bold]. {em.get('🔧')} MeshAdv-Mini 400MHz variant")
    console.print(f"  [bold]4[/bold]. {em.get('🔧')} Waveshare SX1262")
    console.print(f"  [bold]5[/bold]. {em.get('🔧')} Adafruit RFM9x")

    console.print("\n[dim cyan]-- Network Presets --[/dim cyan]")
    console.print(f"  [bold]6[/bold]. {em.get('🏔️')}  [yellow]MtnMesh Community[/yellow] [dim](Slot 20, MediumFast)[/dim]")
    console.print(f"  [bold]7[/bold]. {em.get('🚨')} [yellow]Emergency/SAR[/yellow] [dim](Maximum Range)[/dim]")
    console.print(f"  [bold]8[/bold]. {em.get('🏙️')}  [yellow]Urban High-Speed[/yellow] [dim](Fast, Short Range)[/dim]")
    console.print(f"  [bold]9[/bold]. {em.get('📡')} [yellow]Repeater Node[/yellow] [dim](Router Mode)[/dim]")

    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to Main Menu")

    choice = Prompt.ask("\n[cyan]Select template[/cyan]", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="0")

    template_map = {
        "1": "meshadv-pi-hat.yaml",
        "2": "meshadv-mini.yaml",
        "3": "meshadv-mini-400mhz.yaml",
        "4": "waveshare-sx1262.yaml",
        "5": "adafruit-rfm9x.yaml",
        "6": "mtnmesh-community.yaml",
        "7": "emergency-sar.yaml",
        "8": "urban-highspeed.yaml",
        "9": "repeater-node.yaml"
    }

    if choice in template_map:
        apply_template(template_map[choice])


def apply_template(template_name):
    """Apply a configuration template"""
    import shutil
    from pathlib import Path

    src_dir = Path(__file__).parent.parent / 'templates' / 'available.d'
    template_path = src_dir / template_name
    dest_path = Path('/etc/meshtasticd/config.yaml')

    if not template_path.exists():
        console.print(f"[red]Template not found: {template_name}[/red]")
        return

    # Show template content
    console.print(f"\n[cyan]Template: {template_name}[/cyan]")
    console.print("[dim]Preview:[/dim]\n")

    with open(template_path, 'r') as f:
        content = f.read()
        # Show first 30 lines
        lines = content.split('\n')[:30]
        for line in lines:
            console.print(f"[dim]{line}[/dim]")
        if len(content.split('\n')) > 30:
            console.print("[dim]...[/dim]")

    if Confirm.ask(f"\nApply template to {dest_path}?", default=True):
        try:
            # Backup existing config
            if dest_path.exists():
                backup_path = dest_path.with_suffix('.yaml.bak')
                shutil.copy2(dest_path, backup_path)
                console.print(f"[dim]Backed up existing config to {backup_path}[/dim]")

            shutil.copy2(template_path, dest_path)
            console.print(f"[green]Template applied successfully![/green]")

            if Confirm.ask("\nRestart meshtasticd service?", default=True):
                result = subprocess.run(['systemctl', 'restart', 'meshtasticd'],
                                      capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    console.print("[green]Service restarted![/green]")
                else:
                    console.print(f"[red]Failed to restart service: {result.stderr}[/red]")

            # Show next steps guidance
            console.print("\n[bold cyan]═══════════ Next Steps ═══════════[/bold cyan]\n")
            console.print("[yellow]Complete your node configuration:[/yellow]\n")
            console.print("  [bold]Option 1: Web Browser[/bold]")
            console.print("    Open: [cyan]http://localhost:9443[/cyan]")
            console.print("    Set region, channel settings, and node name\n")
            console.print("  [bold]Option 2: CLI Commands[/bold]")
            console.print("    [cyan]meshtastic --host localhost --set lora.region US[/cyan]")
            console.print("    [cyan]meshtastic --host localhost --set-owner 'YourCallsign'[/cyan]")
            console.print("    [cyan]meshtastic --host localhost --info[/cyan]")
            console.print("\n[dim]See: https://meshtastic.org/docs/getting-started/initial-config/[/dim]")
            input("\nPress Enter to continue...")

        except Exception as e:
            console.print(f"[red]Failed to apply template: {e}[/red]")


def install_meshtasticd():
    """Install meshtasticd"""
    from rich.box import ROUNDED

    help_panel = Panel(
        "[cyan]Available versions:[/cyan]\n"
        "  [green]stable[/green]  - Latest stable releases (recommended)\n"
        "  [green]beta[/green]    - Latest beta releases\n"
        "  [yellow]daily[/yellow]  - Cutting-edge daily builds\n"
        "  [yellow]alpha[/yellow]  - Experimental alpha builds",
        title="[bold cyan]Installation Versions[/bold cyan]",
        border_style="cyan",
        box=ROUNDED
    )
    console.print(help_panel)

    # Ask for version preference
    version_type = Prompt.ask(
        "[cyan]Select version[/cyan]",
        choices=["stable", "beta", "daily", "alpha"],
        default="stable"
    )

    if version_type in ["daily", "alpha"]:
        console.print(f"\n[bold yellow]{em.get('⚠')} Warning:[/bold yellow] {version_type} builds may be unstable")
        if not Confirm.ask(f"Continue with {version_type} version?", default=False):
            console.print("[yellow]Installation cancelled[/yellow]")
            return

    installer = MeshtasticdInstaller()

    # Run installation (output will be streamed in real-time)
    success = installer.install(version_type=version_type)

    if success:
        success_panel = Panel(
            f"[green]meshtasticd {version_type} installation complete![/green]\n"
            f"\n[cyan]Next steps:[/cyan]\n"
            f"  1. Configure LoRa radio and channels\n"
            f"  2. Enable required modules (MQTT, Serial, etc.)\n"
            f"  3. Monitor service with Dashboard",
            title="[bold green]✓ Installation Complete[/bold green]",
            border_style="green",
            box=ROUNDED
        )
        console.print(success_panel)

        if Confirm.ask("\nWould you like to configure the device now?"):
            configure_device()
    else:
        console.print("\n[bold red]✗ Installation failed![/bold red]")
        console.print("[cyan]For help:[/cyan]")
        console.print("  • Check logs: Debug menu → View error logs")
        console.print("  • Re-run: Use the Install option again")
        console.print("  • Check connection: Ensure internet is working")


def update_meshtasticd():
    """Update meshtasticd"""
    console.print("\n[bold cyan]Updating meshtasticd[/bold cyan]\n")

    # First check for available updates
    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()

    update_info = notifier.check_for_updates(force=True)

    if update_info:
        if update_info.get('update_available'):
            console.print(f"[green]Update available![/green]")
            console.print(f"  Current: {update_info['current']}")
            console.print(f"  Latest:  {update_info['latest']}")

            if not Confirm.ask("\nProceed with update?", default=True):
                return
        else:
            console.print(f"[green]You're running the latest version ({update_info.get('current', 'Unknown')})[/green]")
            if not Confirm.ask("\nReinstall anyway?", default=False):
                return

    installer = MeshtasticdInstaller()

    with console.status("[bold green]Updating..."):
        success = installer.update()

    if success:
        console.print("\n[bold green]Update completed successfully![/bold green]")
    else:
        console.print("\n[bold red]Update failed. Check logs for details.[/bold red]")


def configure_device():
    """Configure meshtastic device"""
    console.print("\n[bold cyan]=============== Device Configuration ===============[/bold cyan]\n")

    while True:
        console.print("\n[dim cyan]-- Radio Settings --[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('📻')} Complete Radio Setup [dim](Recommended)[/dim]")
        console.print(f"  [bold]2[/bold]. {em.get('🌐')} LoRa Settings [dim](Region, Preset)[/dim]")
        console.print(f"  [bold]3[/bold]. {em.get('📢')} Channel Configuration")
        console.print(f"  [bold]4[/bold]. {em.get('⚡')} [yellow]Channel Presets[/yellow] [dim](Quick Setup)[/dim]")

        console.print("\n[dim cyan]-- Device & Modules --[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('🔌')} Module Configuration [dim](MQTT, Serial, etc.)[/dim]")
        console.print(f"  [bold]6[/bold]. {em.get('📝')} Device Settings [dim](Name, WiFi, etc.)[/dim]")

        console.print("\n[dim cyan]-- Hardware --[/dim cyan]")
        console.print(f"  [bold]7[/bold]. {em.get('🔍')} Hardware Detection")
        console.print(f"  [bold]8[/bold]. {em.get('🎛️')}  SPI HAT Configuration [dim](MeshAdv-Mini, etc.)[/dim]")

        console.print(f"\n  [bold]9[/bold]. {em.get('⬅️')}  Back to Main Menu")

        choice = Prompt.ask("\n[cyan]Select configuration option[/cyan]", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        if choice == "1":
            configure_radio_complete()
        elif choice == "2":
            configure_lora()
        elif choice == "3":
            configure_channels()
        elif choice == "4":
            configure_channel_presets()
        elif choice == "5":
            configure_modules()
        elif choice == "6":
            configure_device_settings()
        elif choice == "7":
            detect_hardware()
        elif choice == "8":
            configure_spi_hat()
        elif choice == "9":
            break


def configure_spi_hat():
    """Configure SPI HAT devices (MeshAdv-Mini, etc.)"""
    console.print("\n[bold cyan]SPI HAT Configuration[/bold cyan]\n")

    from config.spi_hats import SPIHatConfigurator

    spi_config = SPIHatConfigurator()
    config = spi_config.interactive_configure()

    if config:
        console.print("\n[green]SPI HAT configuration complete![/green]")
    else:
        console.print("\n[yellow]Configuration cancelled[/yellow]")


def configure_radio_complete():
    """Complete radio configuration with modem preset and channel slot"""
    console.print("\n[bold cyan]Complete Radio Configuration[/bold cyan]\n")

    from config.radio import RadioConfigurator

    radio_config = RadioConfigurator()
    config = radio_config.configure_radio_settings()

    # Ask to save
    if Confirm.ask("\nSave configuration to /etc/meshtasticd/config.yaml?", default=True):
        radio_config.save_configuration_yaml(config)

    console.print("\n[green]Radio configuration complete![/green]")

    # Show next steps
    console.print("\n[bold cyan]═══════════ Next Steps ═══════════[/bold cyan]\n")
    console.print("[yellow]Complete your node setup:[/yellow]\n")
    console.print("  [bold]1. Set Regional Settings (REQUIRED)[/bold]")
    console.print("    Web: [cyan]http://localhost:9443[/cyan] → Radio Config")
    console.print("    CLI: [cyan]meshtastic --host localhost --set lora.region US[/cyan]\n")
    console.print("  [bold]2. Set Node Identity[/bold]")
    console.print("    [cyan]meshtastic --host localhost --set-owner 'YourCallsign'[/cyan]\n")
    console.print("  [bold]3. Verify Connection[/bold]")
    console.print("    [cyan]meshtastic --host localhost --info[/cyan]")
    console.print("\n[dim]Docs: https://meshtastic.org/docs/getting-started/initial-config/[/dim]")


def configure_lora():
    """Configure LoRa settings"""
    console.print("\n[bold cyan]LoRa Configuration[/bold cyan]\n")

    from config.lora import LoRaConfigurator

    lora_config = LoRaConfigurator()

    # Region
    region = lora_config.configure_region()

    # Modem preset
    if Confirm.ask("\nConfigure modem preset?", default=True):
        preset_config = lora_config.configure_modem_preset()
        console.print("\n[green]LoRa settings configured![/green]")


def configure_channels():
    """Configure channels"""
    console.print("\n[bold cyan]Channel Configuration[/bold cyan]\n")

    from config.lora import LoRaConfigurator

    lora_config = LoRaConfigurator()
    channels = lora_config.configure_channels()

    console.print("\n[green]Channels configured![/green]")


def configure_modules():
    """Configure Meshtastic modules"""
    console.print("\n[bold cyan]Module Configuration[/bold cyan]\n")

    from config.modules import ModuleConfigurator

    module_config = ModuleConfigurator()
    config = module_config.interactive_module_config()

    console.print("\n[green]Module configuration complete![/green]")


def configure_device_settings():
    """Configure device settings"""
    configurator = DeviceConfigurator()
    configurator.interactive_configure()


def check_dependencies():
    """Check and fix dependencies"""
    console.print("\n[bold cyan]Checking Dependencies[/bold cyan]\n")

    from installer.dependencies import DependencyManager

    manager = DependencyManager()

    with console.status("[bold green]Checking..."):
        issues = manager.check_all()

    if issues:
        console.print("\n[bold yellow]Found issues:[/bold yellow]")
        for issue in issues:
            console.print(f"  - {issue}")

        if Confirm.ask("\nWould you like to fix these issues?"):
            with console.status("[bold green]Fixing..."):
                manager.fix_all()
            console.print("\n[bold green]Dependencies fixed![/bold green]")
    else:
        console.print("\n[bold green]All dependencies are up to date![/bold green]")


def detect_hardware():
    """Detect hardware"""
    console.print("\n[bold cyan]Hardware Detection[/bold cyan]\n")

    from config.hardware import HardwareDetector

    detector = HardwareDetector()

    with console.status("[bold green]Detecting..."):
        hardware = detector.detect_all()

    if hardware:
        table = Table(title="Detected Hardware", show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Details", style="green")

        for hw_type, details in hardware.items():
            table.add_row(hw_type, str(details))

        console.print(table)
    else:
        console.print("\n[bold yellow]No compatible hardware detected[/bold yellow]")


def debug_menu():
    """Debug and troubleshooting menu"""
    console.print("\n[bold cyan]=============== Debug & Troubleshooting ===============[/bold cyan]\n")

    console.print("[dim cyan]-- Diagnostics --[/dim cyan]")
    console.print(f"  [bold]1[/bold]. {em.get('📜')} View installation logs")
    console.print(f"  [bold]2[/bold]. {em.get('⚠️')} View error logs")
    console.print(f"  [bold]3[/bold]. {em.get('🔄')} Test meshtasticd service")
    console.print(f"  [bold]4[/bold]. {em.get('🔐')} Check permissions")

    console.print("\n[dim cyan]-- Updates & Version --[/dim cyan]")
    console.print(f"  [bold]5[/bold]. {em.get('⬆️')}  [yellow]Check for updates[/yellow]")
    console.print(f"  [bold]6[/bold]. {em.get('📋')} [yellow]Version history[/yellow]")
    console.print(f"  [bold]7[/bold]. {em.get('ℹ️')}  [yellow]Show version info[/yellow]")

    console.print("\n[dim cyan]-- Configuration --[/dim cyan]")
    console.print(f"  [bold]8[/bold]. {em.get('⚙️')}  [yellow]Show environment config[/yellow]")
    console.print(f"  [bold]9[/bold]. {em.get('🎨', '[EMJ]')}  [yellow]Emoji support status[/yellow]")

    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to main menu")

    choice = Prompt.ask("\n[cyan]Select an option[/cyan]", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="0")

    if choice == "1":
        view_logs()
    elif choice == "2":
        view_error_logs()
    elif choice == "3":
        test_service()
    elif choice == "4":
        check_permissions()
    elif choice == "5":
        check_updates_manual()
    elif choice == "6":
        show_version_history()
    elif choice == "7":
        show_version_info()
    elif choice == "8":
        show_environment_config()
    elif choice == "9":
        check_emoji_support()


def check_emoji_support():
    """Check and display emoji support status"""
    from utils import emoji as emoji_utils

    emoji_utils.setup_emoji_support(console)
    Prompt.ask("\n[dim]Press Enter to return[/dim]")


def show_environment_config():
    """Show current environment configuration"""
    console.print("\n[bold cyan]Environment Configuration[/bold cyan]\n")

    from utils.env_config import show_config_summary, validate_config
    show_config_summary()

    # Also show validation results
    validation = validate_config()
    if validation['warnings']:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in validation['warnings']:
            console.print(f"  [yellow]⚠ {warning}[/yellow]")

    if validation['errors']:
        console.print("\n[red]Errors:[/red]")
        for error in validation['errors']:
            console.print(f"  [red]✗ {error}[/red]")

    Prompt.ask("\n[dim]Press Enter to return[/dim]")


def view_logs():
    """View application logs"""
    log_file = "/var/log/meshtasticd-installer.log"
    if os.path.exists(log_file):
        console.print(f"\n[cyan]Showing last 50 lines of {log_file}:[/cyan]\n")
        try:
            result = subprocess.run(['tail', '-n', '50', log_file],
                                  capture_output=True, text=True, check=True, timeout=10)
            from rich.panel import Panel
            console.print(Panel(result.stdout, title="[cyan]Installation Log[/cyan]", border_style="cyan"))
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error reading log file: {e}[/red]")
    else:
        console.print("\n[yellow]No installation log file found[/yellow]")
        console.print("[dim]Log will be created on first installation[/dim]")


def view_error_logs():
    """View detailed error logs"""
    error_log_file = "/var/log/meshtasticd-installer-error.log"

    if os.path.exists(error_log_file):
        console.print(f"\n[red bold]Installation Error Log[/red bold]\n")

        # Check file size
        file_size = os.path.getsize(error_log_file)

        if file_size == 0:
            console.print("[green]No errors logged - installation has been successful![/green]")
            return

        try:
            # Read the entire error log
            with open(error_log_file, 'r') as f:
                error_content = f.read()

            from rich.panel import Panel
            from rich.syntax import Syntax

            # Show last 100 lines to avoid overwhelming output
            lines = error_content.split('\n')
            if len(lines) > 100:
                display_content = '\n'.join(lines[-100:])
                console.print(f"[dim]Showing last 100 lines (file has {len(lines)} total lines)[/dim]\n")
            else:
                display_content = error_content

            console.print(Panel(
                display_content,
                title="[red]Error Details[/red]",
                border_style="red",
                expand=False
            ))

            console.print(f"\n[dim]Full error log location: {error_log_file}[/dim]")
            console.print(f"[dim]File size: {file_size} bytes[/dim]")

            # Offer to clear the log
            from rich.prompt import Confirm
            if Confirm.ask("\n[yellow]Clear error log?[/yellow]", default=False):
                try:
                    with open(error_log_file, 'w') as f:
                        f.write("")
                    console.print("[green]Error log cleared[/green]")
                except Exception as e:
                    console.print(f"[red]Failed to clear log: {e}[/red]")

        except Exception as e:
            console.print(f"[red]Error reading error log file: {e}[/red]")
    else:
        console.print("\n[green]No error log found - no errors have been recorded![/green]")
        console.print("[dim]Error log will be created if installation fails[/dim]")


def test_service():
    """Test meshtasticd service"""
    console.print("\n[cyan]Testing meshtasticd service...[/cyan]\n")
    result = subprocess.run(['systemctl', 'status', 'meshtasticd'],
                          capture_output=True, text=True, timeout=15)
    console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)


def check_permissions():
    """Check GPIO/SPI permissions"""
    console.print("\n[cyan]Checking permissions...[/cyan]\n")

    from installer.dependencies import DependencyManager
    manager = DependencyManager()
    manager.check_permissions()


def check_updates_manual():
    """Manually check for updates"""
    console.print("\n[cyan]Checking for updates...[/cyan]\n")

    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()

    update_info = notifier.check_for_updates(force=True)

    if update_info:
        if update_info.get('update_available'):
            notifier.show_update_notification(update_info)
        else:
            console.print(f"[green]You're running the latest version ({update_info.get('current', 'Unknown')})[/green]")
    else:
        console.print("[yellow]Could not check for updates[/yellow]")


def show_version_history():
    """Show version history"""
    from installer.update_notifier import UpdateNotifier
    notifier = UpdateNotifier()
    notifier.get_version_history()


def show_version_info():
    """Show version information"""
    from __version__ import show_version_history, get_full_version
    console.print(f"\n[bold cyan]Installer Version: {get_full_version()}[/bold cyan]\n")
    show_version_history()


@click.command()
@click.option('--install', type=click.Choice(['stable', 'beta', 'daily', 'alpha']), help='Install meshtasticd')
@click.option('--update', is_flag=True, help='Update meshtasticd')
@click.option('--configure', is_flag=True, help='Configure device')
@click.option('--check', is_flag=True, help='Check dependencies')
@click.option('--dashboard', is_flag=True, help='Show status dashboard')
@click.option('--version', is_flag=True, help='Show version information')
@click.option('--debug', is_flag=True, help='Enable debug logging')
@click.option('--show-config', is_flag=True, help='Show current configuration')
def main(install, update, configure, check, dashboard, version, debug, show_config):
    """Meshtasticd Interactive Installer & Manager"""

    # Initialize configuration from .env file
    config_result = initialize_config()

    # Enable debug from environment if not set via CLI
    if not debug and get_config_bool('DEBUG_MODE'):
        debug = True

    # Setup logging
    setup_logger(debug=debug)

    # Show configuration if requested
    if show_config:
        from utils.env_config import show_config_summary
        show_config_summary()
        return

    # Show version and exit
    if version:
        console.print(f"Meshtasticd Interactive Installer v{get_full_version()}")
        return

    # Show dashboard
    if dashboard:
        show_dashboard()
        return

    # If no arguments, show interactive menu
    if not any([install, update, configure, check]):
        interactive_menu()
        return

    # Check root
    if not check_root():
        console.print("[bold red]Error:[/bold red] This tool requires root/sudo privileges")
        sys.exit(1)

    # Handle command line options
    if install:
        installer = MeshtasticdInstaller()
        installer.install(version_type=install)

    if update:
        installer = MeshtasticdInstaller()
        installer.update()

    if configure:
        configurator = DeviceConfigurator()
        configurator.interactive_configure()

    if check:
        from installer.dependencies import DependencyManager
        manager = DependencyManager()
        manager.check_all()


if __name__ == '__main__':
    main()
