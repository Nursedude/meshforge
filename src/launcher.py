#!/usr/bin/env python3
"""
Meshtasticd Manager - Launcher Wizard

This wizard helps users select the appropriate interface for their setup.
It detects the environment and recommends the best option.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


# Colors for terminal output
class Colors:
    CYAN = '\033[0;36m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    NC = '\033[0m'  # No Color


def print_banner():
    """Print the welcome banner"""
    print(f"""{Colors.CYAN}
╔═══════════════════════════════════════════════════════════════╗
║     Meshtasticd Interactive Manager - v3.0.0                  ║
║     For Raspberry Pi OS & Linux                               ║
╠═══════════════════════════════════════════════════════════════╣
║     Choose your interface to get started                      ║
╚═══════════════════════════════════════════════════════════════╝
{Colors.NC}""")


def detect_environment():
    """Detect the current environment and capabilities"""
    env = {
        'has_display': False,
        'display_type': None,
        'is_ssh': False,
        'has_gtk': False,
        'has_textual': False,
        'is_root': os.geteuid() == 0,
        'terminal': os.environ.get('TERM', 'unknown'),
    }

    # Check for display
    display = os.environ.get('DISPLAY')
    wayland = os.environ.get('WAYLAND_DISPLAY')
    if display or wayland:
        env['has_display'] = True
        env['display_type'] = 'Wayland' if wayland else 'X11'

    # Check for SSH
    if os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'):
        env['is_ssh'] = True

    # Check for GTK4
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk, Adw
        env['has_gtk'] = True
    except (ImportError, ValueError):
        pass

    # Check for Textual
    try:
        import textual
        env['has_textual'] = True
    except ImportError:
        pass

    return env


def print_environment_info(env):
    """Print detected environment information"""
    print(f"\n{Colors.DIM}Environment Detection:{Colors.NC}")

    if env['has_display']:
        print(f"  {Colors.GREEN}✓{Colors.NC} Display detected ({env['display_type']})")
    else:
        print(f"  {Colors.YELLOW}○{Colors.NC} No display detected")

    if env['is_ssh']:
        print(f"  {Colors.YELLOW}○{Colors.NC} Running via SSH")
    else:
        print(f"  {Colors.GREEN}✓{Colors.NC} Local session")

    if env['has_gtk']:
        print(f"  {Colors.GREEN}✓{Colors.NC} GTK4/libadwaita available")
    else:
        print(f"  {Colors.YELLOW}○{Colors.NC} GTK4 not available")

    if env['has_textual']:
        print(f"  {Colors.GREEN}✓{Colors.NC} Textual TUI available")
    else:
        print(f"  {Colors.YELLOW}○{Colors.NC} Textual not installed")

    print()


def get_recommendation(env):
    """Get the recommended interface based on environment"""
    if env['has_display'] and env['has_gtk'] and not env['is_ssh']:
        return '1'  # GTK4 GUI
    elif env['has_textual']:
        return '2'  # Textual TUI
    else:
        return '3'  # Rich CLI


def print_menu(env, recommended):
    """Print the interface selection menu"""
    print(f"{Colors.BOLD}Select Interface:{Colors.NC}\n")

    # Option 1: GTK4 GUI
    gtk_status = ""
    if not env['has_display']:
        gtk_status = f" {Colors.DIM}(no display){Colors.NC}"
    elif not env['has_gtk']:
        gtk_status = f" {Colors.YELLOW}(requires installation){Colors.NC}"
    elif env['is_ssh']:
        gtk_status = f" {Colors.YELLOW}(may not work over SSH){Colors.NC}"

    rec1 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '1' else ""
    print(f"  {Colors.BOLD}1{Colors.NC}. {Colors.CYAN}GTK4 Graphical Interface{Colors.NC}{gtk_status}{rec1}")
    print(f"     {Colors.DIM}Modern desktop UI with libadwaita design{Colors.NC}")
    print(f"     {Colors.DIM}Best for: Pi with monitor, VNC, Raspberry Pi Connect desktop{Colors.NC}")
    print()

    # Option 2: Textual TUI
    tui_status = ""
    if not env['has_textual']:
        tui_status = f" {Colors.YELLOW}(requires installation){Colors.NC}"

    rec2 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '2' else ""
    print(f"  {Colors.BOLD}2{Colors.NC}. {Colors.CYAN}Textual TUI (Terminal Interface){Colors.NC}{tui_status}{rec2}")
    print(f"     {Colors.DIM}Full-featured terminal UI with mouse support{Colors.NC}")
    print(f"     {Colors.DIM}Best for: SSH, headless, Raspberry Pi Connect terminal{Colors.NC}")
    print()

    # Option 3: Rich CLI
    rec3 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '3' else ""
    print(f"  {Colors.BOLD}3{Colors.NC}. {Colors.CYAN}Rich CLI (Original Interface){Colors.NC}{rec3}")
    print(f"     {Colors.DIM}Text-based menu interface{Colors.NC}")
    print(f"     {Colors.DIM}Best for: Basic terminals, fallback, minimal environments{Colors.NC}")
    print()

    # Install options
    print(f"  {Colors.BOLD}i{Colors.NC}. {Colors.YELLOW}Install missing dependencies{Colors.NC}")
    print(f"     {Colors.DIM}Install GTK4 or Textual if needed{Colors.NC}")
    print()

    print(f"  {Colors.BOLD}q{Colors.NC}. {Colors.DIM}Quit{Colors.NC}")
    print()


def install_dependencies():
    """Interactive dependency installation"""
    print(f"\n{Colors.BOLD}Install Dependencies:{Colors.NC}\n")

    print(f"  {Colors.BOLD}1{Colors.NC}. Install GTK4/libadwaita (for graphical interface)")
    print(f"     {Colors.DIM}sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1{Colors.NC}")
    print()

    print(f"  {Colors.BOLD}2{Colors.NC}. Install Textual (for terminal UI)")
    print(f"     {Colors.DIM}sudo pip install --break-system-packages textual{Colors.NC}")
    print()

    print(f"  {Colors.BOLD}3{Colors.NC}. Install both")
    print()

    print(f"  {Colors.BOLD}0{Colors.NC}. Back")
    print()

    try:
        choice = input(f"{Colors.CYAN}Select option [0]: {Colors.NC}").strip() or "0"
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if choice == "0":
        return

    if choice in ["1", "3"]:
        print(f"\n{Colors.CYAN}Installing GTK4 dependencies...{Colors.NC}")
        try:
            subprocess.run([
                'sudo', 'apt', 'install', '-y',
                'python3-gi', 'python3-gi-cairo',
                'gir1.2-gtk-4.0', 'libadwaita-1-0', 'gir1.2-adw-1'
            ], check=True)
            print(f"{Colors.GREEN}GTK4 dependencies installed!{Colors.NC}")
        except subprocess.CalledProcessError as e:
            print(f"{Colors.RED}Failed to install GTK4 dependencies: {e}{Colors.NC}")

    if choice in ["2", "3"]:
        print(f"\n{Colors.CYAN}Installing Textual...{Colors.NC}")
        try:
            subprocess.run([
                'sudo', 'pip', 'install', '--break-system-packages', 'textual'
            ], check=True)
            print(f"{Colors.GREEN}Textual installed!{Colors.NC}")
        except subprocess.CalledProcessError as e:
            print(f"{Colors.RED}Failed to install Textual: {e}{Colors.NC}")

    print(f"\n{Colors.GREEN}Installation complete! Returning to menu...{Colors.NC}")
    input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")


def launch_interface(choice):
    """Launch the selected interface"""
    src_dir = Path(__file__).parent

    if choice == "1":
        # GTK4 GUI
        print(f"\n{Colors.GREEN}Launching GTK4 Graphical Interface...{Colors.NC}\n")
        os.execv(sys.executable, [sys.executable, str(src_dir / 'main_gtk.py')])

    elif choice == "2":
        # Textual TUI
        print(f"\n{Colors.GREEN}Launching Textual TUI...{Colors.NC}\n")
        os.execv(sys.executable, [sys.executable, str(src_dir / 'main_tui.py')])

    elif choice == "3":
        # Rich CLI
        print(f"\n{Colors.GREEN}Launching Rich CLI...{Colors.NC}\n")
        os.execv(sys.executable, [sys.executable, str(src_dir / 'main.py')])


def main():
    """Main entry point"""
    # Check root
    if os.geteuid() != 0:
        print(f"\n{Colors.RED}Error: This application requires root/sudo privileges{Colors.NC}")
        print(f"Please run with: {Colors.CYAN}sudo python3 src/launcher.py{Colors.NC}")
        sys.exit(1)

    while True:
        # Clear screen
        os.system('clear' if os.name == 'posix' else 'cls')

        # Print banner and info
        print_banner()

        # Detect environment
        env = detect_environment()
        print_environment_info(env)

        # Get recommendation
        recommended = get_recommendation(env)

        # Print menu
        print_menu(env, recommended)

        # Get user choice
        try:
            choice = input(f"{Colors.CYAN}Select option [{recommended}]: {Colors.NC}").strip() or recommended
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.YELLOW}Goodbye!{Colors.NC}")
            sys.exit(0)

        if choice.lower() == 'q':
            print(f"\n{Colors.YELLOW}Goodbye!{Colors.NC}")
            sys.exit(0)

        elif choice == 'i':
            install_dependencies()

        elif choice in ['1', '2', '3']:
            # Validate choice
            if choice == '1' and not env['has_display']:
                print(f"\n{Colors.YELLOW}Warning: No display detected. GTK4 requires a display.{Colors.NC}")
                try:
                    confirm = input(f"Continue anyway? [y/N]: ").strip().lower()
                    if confirm not in ['y', 'yes']:
                        continue
                except (KeyboardInterrupt, EOFError):
                    continue

            if choice == '1' and not env['has_gtk']:
                print(f"\n{Colors.YELLOW}GTK4 is not installed. Would you like to install it?{Colors.NC}")
                try:
                    confirm = input(f"Install GTK4 dependencies? [Y/n]: ").strip().lower()
                    if confirm in ['', 'y', 'yes']:
                        install_dependencies()
                        continue
                except (KeyboardInterrupt, EOFError):
                    continue

            if choice == '2' and not env['has_textual']:
                print(f"\n{Colors.YELLOW}Textual is not installed. Would you like to install it?{Colors.NC}")
                try:
                    confirm = input(f"Install Textual? [Y/n]: ").strip().lower()
                    if confirm in ['', 'y', 'yes']:
                        subprocess.run([
                            'sudo', 'pip', 'install', '--break-system-packages', 'textual'
                        ], check=True)
                        print(f"{Colors.GREEN}Textual installed!{Colors.NC}")
                except (KeyboardInterrupt, EOFError):
                    continue
                except subprocess.CalledProcessError:
                    print(f"{Colors.RED}Failed to install Textual{Colors.NC}")
                    input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")
                    continue

            # Launch the interface
            launch_interface(choice)

        else:
            print(f"\n{Colors.RED}Invalid option. Please try again.{Colors.NC}")
            input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")


if __name__ == '__main__':
    main()
