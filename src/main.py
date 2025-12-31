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
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.system import check_root, get_system_info
from utils.logger import setup_logger, log
from utils import emoji as em
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
╔═══════════════════════════════════════════════════════════╗
║   {mesh} Meshtasticd Interactive Installer & Manager          ║
║   For Raspberry Pi OS                          v{__version__}   ║
╠═══════════════════════════════════════════════════════════╣
║   {ant} Install • Configure • Monitor • Update               ║
╚═══════════════════════════════════════════════════════════╝
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

        console.print("\n[bold cyan]═══════════════════ Main Menu ═══════════════════[/bold cyan]")

        # Status & Monitoring Section
        console.print("\n[dim cyan]── Status & Monitoring ──[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('📊')} [green]Quick Status Dashboard[/green]")
        console.print(f"  [bold]2[/bold]. {em.get('🔧')} [green]Service Management[/green] [dim](Start/Stop/Logs)[/dim]")

        # Installation Section
        console.print("\n[dim cyan]── Installation ──[/dim cyan]")
        console.print(f"  [bold]3[/bold]. {em.get('📦')} Install meshtasticd")
        console.print(f"  [bold]4[/bold]. {em.get('⬆️')}  Update meshtasticd")

        # Configuration Section
        console.print("\n[dim cyan]── Configuration ──[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('⚙️')}  Configure device")
        console.print(f"  [bold]6[/bold]. {em.get('📻')} [yellow]Channel Presets[/yellow] [dim](Quick Setup)[/dim]")
        console.print(f"  [bold]7[/bold]. {em.get('📋')} Configuration Templates")
        console.print(f"  [bold]8[/bold]. {em.get('📁')} [green]Config File Manager[/green] [dim](Select YAML + nano)[/dim]")

        # Meshtastic CLI Section
        console.print("\n[dim cyan]── Meshtastic CLI ──[/dim cyan]")
        console.print(f"  [bold]c[/bold]. {em.get('💻')} [yellow]Meshtastic CLI Commands[/yellow]")

        # System Section
        console.print("\n[dim cyan]── System ──[/dim cyan]")
        console.print(f"  [bold]9[/bold]. {em.get('🔍')} Check dependencies")
        console.print(f"  [bold]h[/bold]. {em.get('🔌')} Hardware detection")
        console.print(f"  [bold]d[/bold]. {em.get('🐛')} Debug & troubleshooting")

        console.print(f"\n  [bold]q[/bold]. {em.get('🚪')} Exit")
        console.print(f"  [bold]?[/bold]. {em.get('❓')} Help")

        choice = Prompt.ask("\n[cyan]Select an option[/cyan]", choices=["q", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "h", "d", "?"], default="1")

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
        elif choice == "9":
            check_dependencies()
        elif choice == "h":
            detect_hardware()
        elif choice == "d":
            debug_menu()
        elif choice == "?":
            show_help()
        elif choice == "q":
            console.print(f"\n[green]{em.get('🤙')} A Hui Hou! Happy meshing![/green]")
            sys.exit(0)


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
    console.print("\n[bold cyan]═══════════════ Configuration Templates ═══════════════[/bold cyan]\n")

    console.print("[dim cyan]── Hardware Templates ──[/dim cyan]")
    console.print(f"  [bold]1[/bold]. {em.get('🔧')} MeshAdv-Mini (SX1262/SX1268 HAT)")
    console.print(f"  [bold]2[/bold]. {em.get('🔧')} MeshAdv-Mini 400MHz variant")
    console.print(f"  [bold]3[/bold]. {em.get('🔧')} Waveshare SX1262")
    console.print(f"  [bold]4[/bold]. {em.get('🔧')} Adafruit RFM9x")

    console.print("\n[dim cyan]── Network Presets ──[/dim cyan]")
    console.print(f"  [bold]5[/bold]. {em.get('🏔️')}  [yellow]MtnMesh Community[/yellow] [dim](Slot 20, MediumFast)[/dim]")
    console.print(f"  [bold]6[/bold]. {em.get('🚨')} [yellow]Emergency/SAR[/yellow] [dim](Maximum Range)[/dim]")
    console.print(f"  [bold]7[/bold]. {em.get('🏙️')}  [yellow]Urban High-Speed[/yellow] [dim](Fast, Short Range)[/dim]")
    console.print(f"  [bold]8[/bold]. {em.get('📡')} [yellow]Repeater Node[/yellow] [dim](Router Mode)[/dim]")

    console.print(f"\n  [bold]9[/bold]. {em.get('⬅️')}  Back to Main Menu")

    choice = Prompt.ask("\n[cyan]Select template[/cyan]", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="9")

    template_map = {
        "1": "meshadv-mini.yaml",
        "2": "meshadv-mini-400mhz.yaml",
        "3": "waveshare-sx1262.yaml",
        "4": "adafruit-rfm9x.yaml",
        "5": "mtnmesh-community.yaml",
        "6": "emergency-sar.yaml",
        "7": "urban-highspeed.yaml",
        "8": "repeater-node.yaml"
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
                                      capture_output=True, text=True)
                if result.returncode == 0:
                    console.print("[green]Service restarted![/green]")
                else:
                    console.print(f"[red]Failed to restart service: {result.stderr}[/red]")

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
    console.print("\n[bold cyan]═══════════════ Device Configuration ═══════════════[/bold cyan]\n")

    while True:
        console.print("\n[dim cyan]── Radio Settings ──[/dim cyan]")
        console.print(f"  [bold]1[/bold]. {em.get('📻')} Complete Radio Setup [dim](Recommended)[/dim]")
        console.print(f"  [bold]2[/bold]. {em.get('🌐')} LoRa Settings [dim](Region, Preset)[/dim]")
        console.print(f"  [bold]3[/bold]. {em.get('📢')} Channel Configuration")
        console.print(f"  [bold]4[/bold]. {em.get('⚡')} [yellow]Channel Presets[/yellow] [dim](Quick Setup)[/dim]")

        console.print("\n[dim cyan]── Device & Modules ──[/dim cyan]")
        console.print(f"  [bold]5[/bold]. {em.get('🔌')} Module Configuration [dim](MQTT, Serial, etc.)[/dim]")
        console.print(f"  [bold]6[/bold]. {em.get('📝')} Device Settings [dim](Name, WiFi, etc.)[/dim]")

        console.print("\n[dim cyan]── Hardware ──[/dim cyan]")
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
    console.print("\n[bold cyan]═══════════════ Debug & Troubleshooting ═══════════════[/bold cyan]\n")

    console.print("[dim cyan]── Diagnostics ──[/dim cyan]")
    console.print(f"  [bold]1[/bold]. {em.get('📜')} View installation logs")
    console.print(f"  [bold]2[/bold]. {em.get('⚠️')} View error logs")
    console.print(f"  [bold]3[/bold]. {em.get('🔄')} Test meshtasticd service")
    console.print(f"  [bold]4[/bold]. {em.get('🔐')} Check permissions")

    console.print("\n[dim cyan]── Updates & Version ──[/dim cyan]")
    console.print(f"  [bold]5[/bold]. {em.get('⬆️')}  [yellow]Check for updates[/yellow]")
    console.print(f"  [bold]6[/bold]. {em.get('📋')} [yellow]Version history[/yellow]")
    console.print(f"  [bold]7[/bold]. {em.get('ℹ️')}  [yellow]Show version info[/yellow]")

    console.print("\n[dim cyan]── Configuration ──[/dim cyan]")
    console.print(f"  [bold]8[/bold]. {em.get('⚙️')}  [yellow]Show environment config[/yellow]")

    console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')}  Back to main menu")

    choice = Prompt.ask("\n[cyan]Select an option[/cyan]", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"], default="0")

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
                                  capture_output=True, text=True, check=True)
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
                          capture_output=True, text=True)
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
