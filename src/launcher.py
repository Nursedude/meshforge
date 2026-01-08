#!/usr/bin/env python3
"""
Meshtasticd Manager - Launcher Wizard

This wizard helps users select the appropriate interface for their setup.
It detects the environment and recommends the best option.
User preferences are saved for future launches.
"""

import os
import sys
import shutil
import subprocess
import json
from pathlib import Path

# Import version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "3.0.3"

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Config file location
CONFIG_DIR = get_real_user_home() / '.config' / 'meshtasticd-installer'
CONFIG_FILE = CONFIG_DIR / 'preferences.json'


# Colors for terminal output
class Colors:
    CYAN = '\033[0;36m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    RED = '\033[0;31m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    NC = '\033[0m'  # No Color


def load_preferences():
    """Load saved user preferences"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_preferences(prefs):
    """Save user preferences"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
    except IOError:
        pass


def check_first_run() -> bool:
    """Check if this is a first run (no setup marker exists)"""
    marker = get_real_user_home() / ".meshforge" / ".setup_complete"
    return not marker.exists()


def run_setup_wizard():
    """Run the interactive setup wizard"""
    print(f"\n{Colors.CYAN}{'='*60}")
    print("  MeshForge First-Run Setup")
    print(f"{'='*60}{Colors.NC}\n")

    print("This appears to be your first time running MeshForge.")
    print("The setup wizard will detect installed services and guide")
    print("you through initial configuration.\n")

    try:
        response = input(f"Run setup wizard now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = 'n'

    if response != 'n':
        try:
            from setup_wizard import SetupWizard
            wizard = SetupWizard(interactive=True)
            wizard.run_interactive_setup()
            wizard.mark_setup_complete()
        except ImportError:
            # Fallback: try to import from different location
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "setup_wizard",
                    Path(__file__).parent / "setup_wizard.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                wizard = module.SetupWizard(interactive=True)
                wizard.run_interactive_setup()
                wizard.mark_setup_complete()
            except Exception as e:
                print(f"{Colors.YELLOW}Setup wizard not available: {e}{Colors.NC}")
                print("Continuing to main launcher...\n")
                # Mark as complete to avoid asking again
                marker = get_real_user_home() / ".meshforge" / ".setup_complete"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("skipped")
    else:
        print(f"\n{Colors.DIM}Skipping setup. Run 'meshforge --setup' anytime.{Colors.NC}\n")
        # Mark as complete
        marker = get_real_user_home() / ".meshforge" / ".setup_complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("skipped")


def print_banner():
    """Print the welcome banner"""
    print(f"""{Colors.CYAN}
╔═══════════════════════════════════════════════════════════════╗
║     Meshtasticd Interactive Manager - v{__version__:<21}║
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


def print_menu(env, recommended, saved_pref=None):
    """Print the interface selection menu"""
    print(f"{Colors.BOLD}Select Interface:{Colors.NC}\n")

    # Show saved preference if any
    if saved_pref:
        pref_names = {'1': 'GTK4 GUI', '2': 'Textual TUI', '3': 'Rich CLI'}
        print(f"  {Colors.DIM}Saved preference: {pref_names.get(saved_pref, saved_pref)}{Colors.NC}\n")

    # Option 1: GTK4 GUI
    gtk_status = ""
    if not env['has_display']:
        gtk_status = f" {Colors.DIM}(no display){Colors.NC}"
    elif not env['has_gtk']:
        gtk_status = f" {Colors.YELLOW}(requires installation){Colors.NC}"
    elif env['is_ssh']:
        gtk_status = f" {Colors.YELLOW}(may not work over SSH){Colors.NC}"

    rec1 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '1' else ""
    saved1 = f" {Colors.CYAN}[saved]{Colors.NC}" if saved_pref == '1' else ""
    print(f"  {Colors.BOLD}1{Colors.NC}. {Colors.CYAN}GTK4 Graphical Interface{Colors.NC}{gtk_status}{rec1}{saved1}")
    print(f"     {Colors.DIM}Modern desktop UI with libadwaita design{Colors.NC}")
    print(f"     {Colors.DIM}Best for: Pi with monitor, VNC, Raspberry Pi Connect desktop{Colors.NC}")
    print()

    # Option 2: Textual TUI
    tui_status = ""
    if not env['has_textual']:
        tui_status = f" {Colors.YELLOW}(requires installation){Colors.NC}"

    rec2 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '2' else ""
    saved2 = f" {Colors.CYAN}[saved]{Colors.NC}" if saved_pref == '2' else ""
    print(f"  {Colors.BOLD}2{Colors.NC}. {Colors.CYAN}Textual TUI (Terminal Interface){Colors.NC}{tui_status}{rec2}{saved2}")
    print(f"     {Colors.DIM}Full-featured terminal UI with mouse support{Colors.NC}")
    print(f"     {Colors.DIM}Best for: SSH, headless, Raspberry Pi Connect terminal{Colors.NC}")
    print()

    # Option 3: Rich CLI
    rec3 = f" {Colors.GREEN}← Recommended{Colors.NC}" if recommended == '3' else ""
    saved3 = f" {Colors.CYAN}[saved]{Colors.NC}" if saved_pref == '3' else ""
    print(f"  {Colors.BOLD}3{Colors.NC}. {Colors.CYAN}Rich CLI (Original Interface){Colors.NC}{rec3}{saved3}")
    print(f"     {Colors.DIM}Text-based menu interface{Colors.NC}")
    print(f"     {Colors.DIM}Best for: Basic terminals, fallback, minimal environments{Colors.NC}")
    print()

    # Install options
    print(f"  {Colors.BOLD}i{Colors.NC}. {Colors.YELLOW}Install missing dependencies{Colors.NC}")
    print(f"     {Colors.DIM}Install GTK4 or Textual if needed{Colors.NC}")
    print()

    # Preference options
    if saved_pref:
        print(f"  {Colors.BOLD}c{Colors.NC}. {Colors.DIM}Clear saved preference{Colors.NC}")
    print(f"  {Colors.BOLD}s{Colors.NC}. {Colors.DIM}Save preference after selecting{Colors.NC}")
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
    print(f"     {Colors.DIM}sudo pip install --break-system-packages --ignore-installed textual{Colors.NC}")
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
            ], check=True, timeout=300)
            print(f"{Colors.GREEN}GTK4 dependencies installed!{Colors.NC}")
        except subprocess.CalledProcessError as e:
            print(f"{Colors.RED}Failed to install GTK4 dependencies: {e}{Colors.NC}")
        except subprocess.TimeoutExpired:
            print(f"{Colors.RED}Installation timed out (5 min limit){Colors.NC}")

    if choice in ["2", "3"]:
        print(f"\n{Colors.CYAN}Installing Textual...{Colors.NC}")
        try:
            subprocess.run([
                'sudo', 'pip', 'install', '--break-system-packages', '--ignore-installed', 'textual'
            ], check=True, timeout=180)
            print(f"{Colors.GREEN}Textual installed!{Colors.NC}")
        except subprocess.CalledProcessError as e:
            print(f"{Colors.RED}Failed to install Textual: {e}{Colors.NC}")
        except subprocess.TimeoutExpired:
            print(f"{Colors.RED}Installation timed out (3 min limit){Colors.NC}")

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

    # Check for first run - offer setup wizard
    if '--setup' in sys.argv or check_first_run():
        run_setup_wizard()

    # Load saved preferences
    prefs = load_preferences()
    saved_interface = prefs.get('interface')
    auto_launch = prefs.get('auto_launch', False)
    save_next = False

    # Auto-launch saved preference if set
    if auto_launch and saved_interface in ['1', '2', '3']:
        env = detect_environment()
        # Verify dependencies are still available
        can_launch = True
        if saved_interface == '1' and not (env['has_display'] and env['has_gtk']):
            can_launch = False
        if saved_interface == '2' and not env['has_textual']:
            can_launch = False

        if can_launch:
            print(f"{Colors.GREEN}Auto-launching saved preference...{Colors.NC}")
            print(f"{Colors.DIM}(Run with --wizard to change preference){Colors.NC}")
            import time
            time.sleep(1)
            launch_interface(saved_interface)
        else:
            print(f"{Colors.YELLOW}Saved interface not available, showing wizard...{Colors.NC}")

    # Check for --wizard flag to force wizard
    if '--wizard' in sys.argv:
        prefs['auto_launch'] = False
        save_preferences(prefs)
        auto_launch = False

    while True:
        # Clear screen (using subprocess for security)
        import subprocess
        subprocess.run(['clear'] if os.name == 'posix' else ['cls'], shell=False, check=False, timeout=5)

        # Print banner and info
        print_banner()

        # Detect environment
        env = detect_environment()
        print_environment_info(env)

        # Get recommendation
        recommended = get_recommendation(env)

        # Default to saved preference if available
        default_choice = saved_interface if saved_interface else recommended

        # Print menu
        print_menu(env, recommended, saved_interface)

        # Get user choice
        try:
            choice = input(f"{Colors.CYAN}Select option [{default_choice}]: {Colors.NC}").strip() or default_choice
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n{Colors.YELLOW}A Hui Hou!{Colors.NC}")
            sys.exit(0)

        if choice.lower() == 'q':
            print(f"\n{Colors.YELLOW}A Hui Hou!{Colors.NC}")
            sys.exit(0)

        elif choice.lower() == 'c':
            # Clear saved preference
            prefs.pop('interface', None)
            prefs.pop('auto_launch', None)
            save_preferences(prefs)
            saved_interface = None
            print(f"\n{Colors.GREEN}Preference cleared!{Colors.NC}")
            input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")

        elif choice.lower() == 's':
            # Enable save mode for next selection
            save_next = True
            print(f"\n{Colors.CYAN}Select an interface to save as your default...{Colors.NC}")
            input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")

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
                            'sudo', 'pip', 'install', '--break-system-packages', '--ignore-installed', 'textual'
                        ], check=True)
                        print(f"{Colors.GREEN}Textual installed!{Colors.NC}")
                except (KeyboardInterrupt, EOFError):
                    continue
                except subprocess.CalledProcessError:
                    print(f"{Colors.RED}Failed to install Textual{Colors.NC}")
                    input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")
                    continue

            # Save preference if requested
            if save_next:
                prefs['interface'] = choice
                try:
                    confirm = input(f"\n{Colors.CYAN}Auto-launch this interface next time? [Y/n]: {Colors.NC}").strip().lower()
                    prefs['auto_launch'] = confirm in ['', 'y', 'yes']
                except (KeyboardInterrupt, EOFError):
                    prefs['auto_launch'] = False

                save_preferences(prefs)
                pref_names = {'1': 'GTK4 GUI', '2': 'Textual TUI', '3': 'Rich CLI'}
                print(f"{Colors.GREEN}Saved {pref_names.get(choice)} as default!{Colors.NC}")
                if prefs['auto_launch']:
                    print(f"{Colors.DIM}Use --wizard flag to change preference{Colors.NC}")
                save_next = False

            # Launch the interface
            launch_interface(choice)

        else:
            print(f"\n{Colors.RED}Invalid option. Please try again.{Colors.NC}")
            input(f"{Colors.DIM}Press Enter to continue...{Colors.NC}")


if __name__ == '__main__':
    main()
