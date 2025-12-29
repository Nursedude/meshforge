#!/usr/bin/env python3
"""
Meshtasticd Interactive Installer & Manager
Main entry point for the application
"""

import os
import sys
import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.system import check_root, get_system_info
from utils.logger import setup_logger, log
from installer.meshtasticd import MeshtasticdInstaller
from config.device import DeviceConfigurator

console = Console()

BANNER = """
╔═══════════════════════════════════════════════════════════╗
║   Meshtasticd Interactive Installer & Manager             ║
║   For Raspberry Pi OS                                     ║
╚═══════════════════════════════════════════════════════════╝
"""


def show_banner():
    """Display application banner"""
    console.print(BANNER, style="bold cyan")


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


def interactive_menu():
    """Show interactive menu and handle user choices"""
    show_banner()

    # Check if running as root
    if not check_root():
        console.print("[bold red]Error:[/bold red] This tool requires root/sudo privileges")
        console.print("Please run with: [cyan]sudo python3 src/main.py[/cyan]")
        sys.exit(1)

    show_system_info()

    while True:
        console.print("\n[bold cyan]Main Menu:[/bold cyan]")
        console.print("1. Install meshtasticd")
        console.print("2. Update meshtasticd")
        console.print("3. Configure device")
        console.print("4. Check dependencies")
        console.print("5. Hardware detection")
        console.print("6. Debug & troubleshooting")
        console.print("7. Exit")

        choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "5", "6", "7"], default="1")

        if choice == "1":
            install_meshtasticd()
        elif choice == "2":
            update_meshtasticd()
        elif choice == "3":
            configure_device()
        elif choice == "4":
            check_dependencies()
        elif choice == "5":
            detect_hardware()
        elif choice == "6":
            debug_menu()
        elif choice == "7":
            console.print("\n[green]Goodbye![/green]")
            sys.exit(0)


def install_meshtasticd():
    """Install meshtasticd"""
    console.print("\n[bold cyan]Installing meshtasticd[/bold cyan]\n")

    # Ask for version preference
    version_type = Prompt.ask(
        "Select version",
        choices=["stable", "beta"],
        default="stable"
    )

    installer = MeshtasticdInstaller()

    with console.status("[bold green]Installing..."):
        success = installer.install(version_type=version_type)

    if success:
        console.print("\n[bold green]Installation completed successfully![/bold green]")

        if Confirm.ask("\nWould you like to configure the device now?"):
            configure_device()
    else:
        console.print("\n[bold red]Installation failed. Check logs for details.[/bold red]")


def update_meshtasticd():
    """Update meshtasticd"""
    console.print("\n[bold cyan]Updating meshtasticd[/bold cyan]\n")

    installer = MeshtasticdInstaller()

    with console.status("[bold green]Updating..."):
        success = installer.update()

    if success:
        console.print("\n[bold green]Update completed successfully![/bold green]")
    else:
        console.print("\n[bold red]Update failed. Check logs for details.[/bold red]")


def configure_device():
    """Configure meshtastic device"""
    console.print("\n[bold cyan]Device Configuration[/bold cyan]\n")

    while True:
        console.print("\n[cyan]Configuration Options:[/cyan]")
        console.print("1. Complete Radio Setup (Modem Preset + Channel Slot)")
        console.print("2. LoRa Settings (Region, Preset)")
        console.print("3. Channel Configuration")
        console.print("4. Module Configuration (MQTT, Serial, etc.)")
        console.print("5. Device Settings (Name, WiFi, etc.)")
        console.print("6. Hardware Detection")
        console.print("7. SPI HAT Configuration (MeshAdv-Mini, etc.)")
        console.print("8. Back to Main Menu")

        choice = Prompt.ask("\nSelect configuration option", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="1")

        if choice == "1":
            configure_radio_complete()
        elif choice == "2":
            configure_lora()
        elif choice == "3":
            configure_channels()
        elif choice == "4":
            configure_modules()
        elif choice == "5":
            configure_device_settings()
        elif choice == "6":
            detect_hardware()
        elif choice == "7":
            configure_spi_hat()
        elif choice == "8":
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
    console.print("\n[bold cyan]Debug & Troubleshooting[/bold cyan]\n")
    console.print("1. View logs")
    console.print("2. Test meshtasticd service")
    console.print("3. Check permissions")
    console.print("4. Back to main menu")

    choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4"], default="4")

    if choice == "1":
        view_logs()
    elif choice == "2":
        test_service()
    elif choice == "3":
        check_permissions()


def view_logs():
    """View application logs"""
    log_file = "/var/log/meshtasticd-installer.log"
    if os.path.exists(log_file):
        console.print(f"\n[cyan]Showing last 50 lines of {log_file}:[/cyan]\n")
        os.system(f"tail -n 50 {log_file}")
    else:
        console.print("\n[yellow]No log file found[/yellow]")


def test_service():
    """Test meshtasticd service"""
    console.print("\n[cyan]Testing meshtasticd service...[/cyan]\n")
    os.system("systemctl status meshtasticd")


def check_permissions():
    """Check GPIO/SPI permissions"""
    console.print("\n[cyan]Checking permissions...[/cyan]\n")

    from installer.dependencies import DependencyManager
    manager = DependencyManager()
    manager.check_permissions()


@click.command()
@click.option('--install', type=click.Choice(['stable', 'beta']), help='Install meshtasticd')
@click.option('--update', is_flag=True, help='Update meshtasticd')
@click.option('--configure', is_flag=True, help='Configure device')
@click.option('--check', is_flag=True, help='Check dependencies')
@click.option('--debug', is_flag=True, help='Enable debug logging')
def main(install, update, configure, check, debug):
    """Meshtasticd Interactive Installer & Manager"""

    # Setup logging
    setup_logger(debug=debug)

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
