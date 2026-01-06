#!/usr/bin/env python3
"""
Meshtasticd Manager - GTK4 GUI Entry Point

This is the graphical interface for systems with a display.
For headless/SSH access, use main_tui.py instead.

Usage:
    sudo python3 src/main_gtk.py           # Run in foreground
    sudo python3 src/main_gtk.py &         # Run in background (shell)
    sudo python3 src/main_gtk.py --daemon  # Run detached (returns terminal)

Daemon Control:
    python3 src/main_gtk.py --status       # Check if daemon is running
    python3 src/main_gtk.py --stop         # Stop running daemon
"""

import os
import sys
import shutil
import subprocess
import argparse
import signal
import threading
import logging
import warnings
from pathlib import Path

# PID file for daemon management
PID_FILE = Path('/tmp/meshtasticd-manager.pid')


def setup_error_suppression():
    """
    Suppress noisy errors from the meshtastic library.
    These occur when the connection drops and heartbeat threads crash.
    """
    # Suppress threading exceptions from meshtastic heartbeat threads
    original_excepthook = threading.excepthook

    def quiet_threading_excepthook(args):
        # Suppress BrokenPipeError and OSError from meshtastic
        if args.exc_type in (BrokenPipeError, OSError, ConnectionResetError):
            exc_str = str(args.exc_value)
            # Only suppress known meshtastic errors
            if any(x in exc_str for x in ['Broken pipe', 'Connection reset', 'Errno 32', 'Errno 104']):
                # Silently ignore - these are expected when connection drops
                return
        # Let other exceptions through to the original handler
        original_excepthook(args)

    threading.excepthook = quiet_threading_excepthook

    # Suppress meshtastic library logging noise
    for logger_name in ['meshtastic', 'meshtastic.mesh_interface', 'meshtastic.stream_interface',
                        'meshtastic.tcp_interface', 'meshtastic.serial_interface']:
        logging.getLogger(logger_name).setLevel(logging.ERROR)

    # Suppress warnings
    warnings.filterwarnings('ignore', category=DeprecationWarning, module='meshtastic')
    warnings.filterwarnings('ignore', category=RuntimeWarning, module='meshtastic')

    # Suppress "Connection lost" prints by patching the interface
    # (the library prints these directly to stdout)
    _setup_output_filter()


def _setup_output_filter():
    """
    Filter noisy stdout/stderr messages from meshtastic library.
    The library prints directly to stdout without using logging.
    """
    import io

    class FilteredWriter:
        """Wrapper that filters out noisy meshtastic messages"""

        SUPPRESS_PATTERNS = [
            'Connection lost',
            'Connection failed',
            'Unexpected OSError',
            'terminating meshtastic reader',
            'BrokenPipeError',
            'Connection reset by peer',
            'Errno 104',
            'Errno 32',
        ]

        def __init__(self, original):
            self._original = original
            self._encoding = getattr(original, 'encoding', 'utf-8')

        def write(self, text):
            # Filter out noisy meshtastic messages
            if text and isinstance(text, str):
                for pattern in self.SUPPRESS_PATTERNS:
                    if pattern in text:
                        return len(text)  # Pretend we wrote it
            return self._original.write(text)

        def flush(self):
            return self._original.flush()

        def __getattr__(self, name):
            return getattr(self._original, name)

    # Only filter if stdout/stderr are regular streams
    if hasattr(sys.stdout, 'write') and not isinstance(sys.stdout, FilteredWriter):
        sys.stdout = FilteredWriter(sys.stdout)
    if hasattr(sys.stderr, 'write') and not isinstance(sys.stderr, FilteredWriter):
        sys.stderr = FilteredWriter(sys.stderr)


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
    from utils.system import require_root
    require_root(
        exit_on_fail=True,
        message="This application requires root/sudo privileges.\n"
                "Please run with:\n"
                "  sudo python3 src/main_gtk.py"
    )


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


def save_pid():
    """Save current PID to file"""
    PID_FILE.write_text(str(os.getpid()))


def get_daemon_pid():
    """Get running daemon PID if exists"""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # Check if process is running
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError):
            # Process not running, clean up stale PID file
            try:
                PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                # Can't delete root-owned file, but that's okay
                pass
        except PermissionError:
            # Can't signal process (different user), but it exists
            return pid
    return None


def daemon_status():
    """Check daemon status"""
    pid = get_daemon_pid()
    if pid:
        print(f"Meshtasticd Manager is running (PID: {pid})")
        print()
        print("The GTK window should be visible on your desktop.")
        print("If you can't find it:")
        print("  - Check other workspaces/virtual desktops")
        print("  - Look for minimized windows")
        print("  - Check log: cat /tmp/meshtasticd-manager.log")
        print()
        print("To restart the app:")
        print(f"  sudo python3 {os.path.abspath(__file__)} --stop")
        print(f"  sudo python3 {os.path.abspath(__file__)} --daemon")
        return True
    else:
        print("Meshtasticd Manager is not running")
        print()
        print("To start:")
        print(f"  sudo python3 {os.path.abspath(__file__)} --daemon")
        return False


def stop_daemon():
    """Stop running daemon"""
    pid = get_daemon_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to Meshtasticd Manager (PID: {pid})")
            # Wait a moment and check if stopped
            import time
            time.sleep(1)
            try:
                os.kill(pid, 0)
                # Still running, try SIGKILL
                os.kill(pid, signal.SIGKILL)
                print("Process killed with SIGKILL")
            except ProcessLookupError:
                pass
            try:
                PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                print("Note: Could not remove PID file (run with sudo to clean up)")
            print("Daemon stopped")
            return True
        except ProcessLookupError:
            print("Process already stopped")
            try:
                PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                print("Note: Could not remove stale PID file (run with sudo to clean up)")
            return True
        except PermissionError:
            print("Permission denied. Try with sudo:")
            print(f"  sudo python3 {os.path.abspath(__file__)} --stop")
            return False
    else:
        print("Daemon is not running")
        return True


def daemonize():
    """
    Start process in background using subprocess (avoids fork() warning).
    This is safer than fork() in multi-threaded environments.
    """
    # Check if already running
    existing_pid = get_daemon_pid()
    if existing_pid:
        print(f"Meshtasticd Manager already running (PID: {existing_pid})")
        print("Use --stop to stop it first, or --status to check")
        sys.exit(1)

    # Get the current script path
    script_path = os.path.abspath(__file__)

    # Use subprocess to spawn a new process instead of fork()
    # This avoids the multi-threaded fork() warning
    env = os.environ.copy()
    env['MESHTASTICD_DAEMON'] = '1'  # Mark as daemon

    # Start new process detached from terminal
    proc = subprocess.Popen(
        [sys.executable, script_path],
        stdin=subprocess.DEVNULL,
        stdout=open('/tmp/meshtasticd-manager.log', 'a'),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env
    )

    print(f"Meshtasticd Manager started in background (PID: {proc.pid})")
    print(f"Log: /tmp/meshtasticd-manager.log")
    print(f"To stop: sudo python3 {script_path} --stop")
    print(f"To check status: python3 {script_path} --status")
    sys.exit(0)


def main():
    """Main entry point"""
    # Parse arguments first
    parser = argparse.ArgumentParser(
        description='Meshtasticd Manager - GTK4 GUI',
        epilog='Daemon control: --status to check, --stop to stop'
    )
    parser.add_argument('--daemon', '-d', action='store_true',
                        help='Run in background (detach from terminal)')
    parser.add_argument('--status', action='store_true',
                        help='Check if daemon is running')
    parser.add_argument('--stop', action='store_true',
                        help='Stop running daemon')
    args, remaining = parser.parse_known_args()

    # Handle daemon control commands (don't need root)
    if args.status:
        sys.exit(0 if daemon_status() else 1)

    if args.stop:
        sys.exit(0 if stop_daemon() else 1)

    # Check if we're running as daemon subprocess
    is_daemon_subprocess = os.environ.get('MESHTASTICD_DAEMON') == '1'

    # Check prerequisites
    check_root()
    check_display()
    check_gtk()
    check_meshtastic_cli()

    # Daemonize if requested (spawns new process and exits)
    if args.daemon:
        daemonize()

    # Save PID if running as daemon
    if is_daemon_subprocess:
        save_pid()

    # Suppress GTK accessibility bus warning if a11y service not available
    # This prevents: "Unable to acquire the address of the accessibility bus"
    if 'GTK_A11Y' not in os.environ:
        os.environ['GTK_A11Y'] = 'none'

    # Suppress noisy meshtastic library errors (connection drops, heartbeat failures)
    setup_error_suppression()

    # Add src to path
    src_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, src_dir)

    # Initialize configuration
    from utils.env_config import initialize_config
    initialize_config()

    # Initialize comprehensive logging system
    try:
        from utils.logging_utils import setup_logging
        # Enable debug logging if running in foreground (not daemon)
        log_level = logging.INFO if is_daemon_subprocess else logging.DEBUG
        setup_logging(log_level=log_level, log_to_file=True, log_to_console=True)
    except ImportError:
        # Fallback to basic logging if logging_utils not available
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'
        )

    # Launch GTK application
    from gtk_ui.app import MeshtasticdApp
    app = MeshtasticdApp()
    return app.run(remaining or sys.argv[:1])


if __name__ == '__main__':
    sys.exit(main())
