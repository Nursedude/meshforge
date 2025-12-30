#!/usr/bin/env python3
"""
Meshtasticd Manager - GTK4 GUI Entry Point

This is the graphical interface for systems with a display.
For headless/SSH access, use main_tui.py instead.
"""

import os
import sys
import shutil
import subprocess


def check_display():
    """Check if a display is available"""
    display = os.environ.get('DISPLAY')
    wayland = os.environ.get('WAYLAND_DISPLAY')

    if not display and not wayland:
        print("=" * 60)
        print("ERROR: No display detected")
        print("=" * 60)
        print()
        print("This GTK4 interface requires a display.")
        print()
        print("Options:")
        print("  1. Use the TUI (Text UI) for SSH/headless access:")
        print("     sudo python3 src/main_tui.py")
        print()
        print("  2. Use the original Rich terminal UI:")
        print("     sudo python3 src/main.py")
        print()
        print("  3. Connect via Raspberry Pi Connect or VNC")
        print("     for remote desktop access, then run this again.")
        print()
        print("  4. Set DISPLAY environment variable if using X11 forwarding:")
        print("     export DISPLAY=:0")
        print("     sudo -E python3 src/main_gtk.py")
        print("=" * 60)
        sys.exit(1)

    return True


def check_gtk():
    """Check if GTK4 and libadwaita are available"""
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk, Adw
        return True
    except (ImportError, ValueError) as e:
        print("=" * 60)
        print("ERROR: GTK4/libadwaita not available")
        print("=" * 60)
        print()
        print(f"Error: {e}")
        print()
        print("Install GTK4 dependencies with:")
        print("  sudo apt install python3-gi python3-gi-cairo")
        print("  sudo apt install gir1.2-gtk-4.0 libadwaita-1-0")
        print("  sudo apt install gir1.2-adw-1")
        print()
        print("Or use the TUI (Text UI) instead:")
        print("  sudo python3 src/main_tui.py")
        print("=" * 60)
        sys.exit(1)


def check_root():
    """Check for root privileges"""
    if os.geteuid() != 0:
        print("=" * 60)
        print("ERROR: Root privileges required")
        print("=" * 60)
        print()
        print("This application requires root/sudo privileges.")
        print("Please run with:")
        print("  sudo python3 src/main_gtk.py")
        print("=" * 60)
        sys.exit(1)


def check_meshtastic_cli():
    """Check if meshtastic CLI is installed"""
    cli_paths = [
        'meshtastic',
        '/root/.local/bin/meshtastic',
        '/home/pi/.local/bin/meshtastic',
        os.path.expanduser('~/.local/bin/meshtastic'),
    ]

    for path in cli_paths:
        if shutil.which(path):
            return True

    # CLI not found - warn user
    print("=" * 60)
    print("WARNING: Meshtastic CLI not found")
    print("=" * 60)
    print()
    print("The meshtastic CLI is recommended for full functionality.")
    print()
    print("Install with:")
    print("  sudo apt install pipx")
    print("  pipx install 'meshtastic[cli]'")
    print("  pipx ensurepath")
    print()
    print("Or with pip:")
    print("  sudo pip install --break-system-packages meshtastic")
    print()

    try:
        response = input("Continue without CLI? [y/n] (y): ").strip().lower()
        if response in ('', 'y', 'yes'):
            return False
        else:
            response = input("Install CLI now with pipx? [y/n] (y): ").strip().lower()
            if response in ('', 'y', 'yes'):
                print("\nInstalling pipx...")
                subprocess.run(['sudo', 'apt', 'install', '-y', 'pipx'], capture_output=False)
                print("\nInstalling meshtastic CLI...")
                subprocess.run(['pipx', 'install', 'meshtastic[cli]'], capture_output=False)
                subprocess.run(['pipx', 'ensurepath'], capture_output=False)
                print("\nCLI installed!")
                return True
            else:
                return False
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return False


def main():
    """Main entry point"""
    # Check prerequisites
    check_root()
    check_display()
    check_gtk()
    check_meshtastic_cli()

    # Add src to path
    src_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, src_dir)

    # Initialize configuration
    from utils.env_config import initialize_config
    initialize_config()

    # Launch GTK application
    from gtk_ui.app import MeshtasticdApp
    app = MeshtasticdApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
