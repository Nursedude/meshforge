#!/usr/bin/env python3
"""
Meshtasticd Manager - Textual TUI Entry Point

This is the terminal-based interface for SSH and headless systems.
Works over Raspberry Pi Connect, VNC terminal, or any SSH session.

For systems with a display, you can also use main_gtk.py for a
full graphical interface.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def check_root():
    """Check for root privileges"""
    if os.geteuid() != 0:
        print("=" * 60)
        print("ERROR: Root privileges required")
        print("=" * 60)
        print()
        print("This application requires root/sudo privileges.")
        print("Please run with:")
        print("  sudo python3 src/main_tui.py")
        print("=" * 60)
        sys.exit(1)


def check_textual():
    """Check if Textual is available and offer to install"""
    try:
        import textual
        return True
    except ImportError:
        print("=" * 60)
        print("Textual TUI framework not installed")
        print("=" * 60)
        print()
        print("Install options:")
        print()
        print("  Option 1 - With sudo (recommended for Raspberry Pi):")
        print("    sudo pip install --break-system-packages --ignore-installed textual")
        print()
        print("  Option 2 - Without sudo:")
        print("    pip install --break-system-packages --ignore-installed textual")
        print()
        print("  Option 3 - In a virtual environment:")
        print("    python3 -m venv venv && source venv/bin/activate")
        print("    pip install textual")
        print()

        # Offer to install
        try:
            response = input("Install now with sudo? [y/n] (y): ").strip().lower()
            if response in ('', 'y', 'yes'):
                print("\nInstalling textual...")
                result = subprocess.run(
                    ['sudo', 'pip', 'install', '--break-system-packages', '--ignore-installed', 'textual'],
                    capture_output=False
                )
                if result.returncode == 0:
                    print("\nTextual installed successfully! Restarting...")
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                else:
                    print("\nInstallation failed. Please install manually.")
                    sys.exit(1)
            else:
                print("\nAlternatively, use the Rich-based CLI:")
                print("  sudo python3 src/main.py")
                sys.exit(1)
        except (KeyboardInterrupt, EOFError):
            print("\n\nCancelled. Use the Rich-based CLI instead:")
            print("  sudo python3 src/main.py")
            sys.exit(1)


def check_meshtastic_cli():
    """Check if meshtastic CLI is installed"""
    # Use centralized CLI finder
    try:
        from utils.cli import find_meshtastic_cli
        if find_meshtastic_cli() is not None:
            return True
    except ImportError:
        # Fallback if utils not available
        if shutil.which('meshtastic'):
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
            return False  # Continue without CLI
        else:
            # Offer to install
            response = input("Install CLI now with pipx? [y/n] (y): ").strip().lower()
            if response in ('', 'y', 'yes'):
                print("\nInstalling pipx...")
                subprocess.run(['sudo', 'apt', 'install', '-y', 'pipx'], capture_output=False)
                print("\nInstalling meshtastic CLI...")
                subprocess.run(['pipx', 'install', 'meshtastic[cli]'], capture_output=False)
                subprocess.run(['pipx', 'ensurepath'], capture_output=False)
                print("\nCLI installed! You may need to restart your shell or run:")
                print("  source ~/.bashrc")
                return True
            else:
                return False
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return False


def main():
    """Main entry point"""
    check_root()
    check_textual()
    check_meshtastic_cli()

    # Add src to path
    src_dir = Path(__file__).parent
    sys.path.insert(0, str(src_dir))

    # Initialize configuration
    from utils.env_config import initialize_config
    initialize_config()

    # Launch TUI
    from tui.app import MeshtasticdTUI
    app = MeshtasticdTUI()
    app.run()


if __name__ == '__main__':
    main()
