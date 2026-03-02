"""NomadNet lifecycle operations — status, launch, install, logs, config view/edit.

Extracted from nomadnet.py for file size compliance (CLAUDE.md #6).
Functions receive the NomadNetHandler instance for access to ctx and utility methods.

Operations in this module:
  show_nomadnet_status   — Comprehensive status display
  view_nomadnet_logs     — Log viewer with filter options
  launch_nomadnet_daemon — Start NomadNet in background daemon mode
  stop_nomadnet          — Stop running NomadNet process(es)
  uninstall_nomadnet     — Stop NomadNet and leave it disabled
  view_nomadnet_config   — View NomadNet configuration
  edit_nomadnet_config   — Edit NomadNet config with available editor
  install_nomadnet       — Install NomadNet via pipx
  setup_nomadnet_shared_instance — Post-install message
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path

from backend import clear_screen
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# Import centralized service checking
check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running'
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

def show_nomadnet_status(handler):
    """Show comprehensive NomadNet status."""
    clear_screen()
    print("=== NomadNet Status ===\n")

    # Installation
    nn_path = shutil.which('nomadnet')
    if not nn_path:
        # Check user local bin (pipx / pip install --user)
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'nomadnet'
        if candidate.exists():
            nn_path = str(candidate)

    if nn_path:
        print(f"  Installed: {nn_path}")
        # Get version
        try:
            result = subprocess.run(
                [nn_path, '--version'],
                capture_output=True, text=True, timeout=10
            )
            version = result.stdout.strip() or result.stderr.strip()
            if version:
                print(f"  Version:   {version}")
        except Exception as e:
            logger.debug(f"NomadNet version check failed: {e}")
    else:
        print("  NOT INSTALLED")
        print("  Install:   pipx install nomadnet")
        print("             (installs rns + lxmf automatically)")

    # Process
    print()
    running = handler._is_nomadnet_running()
    if running:
        print("  Process:   RUNNING")
        try:
            result = subprocess.run(
                ['pgrep', '-fa', 'bin/nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if 'pgrep' not in line:
                        print(f"             {line.strip()}")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("NomadNet process check failed: %s", e)
    else:
        print("  Process:   not running")

    # Config file
    print()
    config_path = handler._get_nomadnet_config_path()
    if config_path and config_path.exists():
        print(f"  Config:    {config_path}")
        try:
            content = config_path.read_text()
            # Parse key settings
            for line in content.split('\n'):
                stripped = line.strip()
                if stripped.startswith('#') or not stripped:
                    continue
                if any(k in stripped.lower() for k in [
                    'user_interface', 'enable_node', 'enable_client',
                    'announce_at_start', 'node_name', 'display_name',
                ]):
                    print(f"             {stripped}")
        except PermissionError:
            print(f"             (permission denied)")
    else:
        print(f"  Config:    not found")
        print(f"  Expected:  ~/.nomadnetwork/config")
        print(f"             (created on first run)")

    # RNS shared instance check
    print()
    print("--- RNS Connectivity ---")
    try:
        if _HAS_SERVICE_CHECK:
            rnsd_running = check_process_running('rnsd')
        else:
            # Fallback to direct pgrep call
            result = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            rnsd_running = result.returncode == 0

        if rnsd_running:
            print("  rnsd:      RUNNING (shared instance available)")
        else:
            print("  rnsd:      NOT running")
            print("  WARNING:   NomadNet needs rnsd or share_instance=Yes")
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("rnsd status check failed: %s", e)
        print("  rnsd:      (check failed)")

    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Log viewer
# ------------------------------------------------------------------

def view_nomadnet_logs(handler):
    """View NomadNet logfile (works in daemon and textui mode).

    NomadNet writes to ~/.nomadnetwork/logfile independently of
    stdout/stderr, so this works regardless of launch mode.
    """
    import collections

    user_home = get_real_user_home()
    logfile = user_home / '.nomadnetwork' / 'logfile'

    if not logfile.exists():
        handler.ctx.dialog.msgbox(
            "No Logs",
            "NomadNet logfile not found yet.\n\n"
            f"Expected at: {logfile}\n\n"
            "Logs are created when NomadNet runs.",
        )
        return

    clear_screen()

    # Offer view options
    choices = [
        ("last50", "Last 50 lines"),
        ("last200", "Last 200 lines"),
        ("errors", "Errors only (last 200 lines)"),
        ("follow", "Follow live (Ctrl+C to stop)"),
        ("back", "Back"),
    ]

    choice = handler.ctx.dialog.menu(
        "NomadNet Logs",
        f"Logfile: {logfile}",
        choices,
    )

    if choice is None or choice == "back":
        return

    if choice == "follow":
        clear_screen()
        print(f"=== NomadNet log — {logfile} "
              f"(Ctrl+C to stop) ===\n")
        try:
            subprocess.run(
                ['tail', '-f', '-n', '30', str(logfile)],
                timeout=None
            )
        except KeyboardInterrupt:
            pass
        return

    # Read the logfile tail
    if choice == "last200":
        maxlines = 200
    else:
        maxlines = 50  # last50 and errors both read 200

    clear_screen()

    try:
        with open(logfile, 'r') as f:
            lines = list(collections.deque(
                f, maxlen=max(maxlines, 200)
            ))

        if choice == "errors":
            error_patterns = [
                'Error', 'Exception', 'CRITICAL',
                'WARNING', 'AuthenticationError',
                'PermissionError', 'Traceback',
            ]
            lines = [
                line for line in lines
                if any(p in line for p in error_patterns)
            ]
            print(f"=== NomadNet errors "
                  f"({len(lines)} found) ===\n")
        else:
            lines = lines[-maxlines:]
            print(f"=== NomadNet log (last "
                  f"{len(lines)} lines) ===\n")

        if lines:
            for line in lines:
                print(line.rstrip())
        else:
            print("  (no matching lines)")

    except PermissionError:
        print(f"Cannot read {logfile} — permission denied")
    except OSError as e:
        print(f"Error reading logfile: {e}")

    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Launch daemon
# ------------------------------------------------------------------

def launch_nomadnet_daemon(handler):
    """Start NomadNet in daemon mode (background, no UI).

    When running via sudo, launches as the real user so NomadNet
    uses their config (~/.nomadnetwork) instead of root's.
    """
    nn_path = handler._find_nomadnet_binary()
    if not nn_path:
        return

    if handler._is_nomadnet_running():
        handler.ctx.dialog.msgbox("Already Running", "NomadNet is already running.")
        return

    # LXMF exclusivity: stop MeshChat if running (one at a time)
    if not handler._ensure_lxmf_exclusive("nomadnet"):
        return

    # Fix ownership of user directories if they were created by root
    if not handler._fix_user_directory_ownership():
        return

    if not handler._check_rns_for_nomadnet():
        return

    if not handler.ctx.dialog.yesno(
        "Start NomadNet Daemon",
        "Start NomadNet in daemon mode (background)?\n\n"
        "This will:\n"
        "  - Announce your node on the RNS network\n"
        "  - Accept and propagate LXMF messages\n"
        "  - Serve node pages (if enabled in config)\n\n"
        "NomadNet will run until stopped.",
    ):
        return

    handler.ctx.dialog.infobox("Starting", "Starting NomadNet daemon...")

    # Check if we need to use a specific RNS config path
    rns_config_path = handler._get_rns_config_for_user()

    # Build command - run as real user if we're under sudo
    # This ensures NomadNet uses ~/.nomadnetwork/config, not /root/.nomadnetwork/config
    sudo_user = os.environ.get('SUDO_USER')

    # Build base args with optional --rnsconfig
    nn_args = ['--daemon']
    if rns_config_path:
        nn_args = ['--rnsconfig', rns_config_path, '--daemon']

    if sudo_user and sudo_user != 'root':
        # Run as real user with -H to set HOME correctly
        # Using -H instead of -i avoids running shell profiles which can interfere
        cmd = ['sudo', '-H', '-u', sudo_user, nn_path] + nn_args
    else:
        cmd = [nn_path] + nn_args

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait briefly and verify
        time.sleep(3)

        if handler._is_nomadnet_running():
            handler.ctx.dialog.msgbox(
                "Daemon Started",
                "NomadNet daemon is running in the background.\n\n"
                "Your node is now announcing on the RNS network.\n"
                "Use 'Stop NomadNet' to shut it down.",
            )
        else:
            handler.ctx.dialog.msgbox(
                "Start Failed",
                "NomadNet daemon failed to start.\n\n"
                "Check logs: ~/.nomadnetwork/logfile\n"
                "Or run manually: nomadnet --daemon --console",
            )
    except FileNotFoundError:
        handler.ctx.dialog.msgbox("Error", f"NomadNet binary not found at: {nn_path}")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to start NomadNet daemon:\n{e}")


# ------------------------------------------------------------------
# Stop
# ------------------------------------------------------------------

def stop_nomadnet(handler):
    """Stop running NomadNet process(es)."""
    if not handler._is_nomadnet_running():
        handler.ctx.dialog.msgbox("Not Running", "NomadNet is not currently running.")
        return

    if not handler.ctx.dialog.yesno(
        "Stop NomadNet",
        "Stop all running NomadNet processes?",
    ):
        return

    try:
        subprocess.run(
            ['pkill', '-f', 'bin/nomadnet'],
            capture_output=True, timeout=10
        )

        time.sleep(2)

        if handler._is_nomadnet_running():
            # Force kill
            subprocess.run(
                ['pkill', '-9', '-f', 'bin/nomadnet'],
                capture_output=True, timeout=10
            )
            time.sleep(1)

        if not handler._is_nomadnet_running():
            handler.ctx.dialog.msgbox("Stopped", "NomadNet has been stopped.")
        else:
            handler.ctx.dialog.msgbox("Warning", "NomadNet may still be running.\nTry: sudo pkill -9 -f nomadnet")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to stop NomadNet:\n{e}")


# ------------------------------------------------------------------
# Uninstall (stop + disable)
# ------------------------------------------------------------------

def uninstall_nomadnet(handler):
    """Stop NomadNet and leave it disabled.

    Does not remove files -- just stops the process and shows how
    to reinstall later if desired.
    """
    if not handler.ctx.dialog.yesno(
        "Disable NomadNet",
        "Stop NomadNet and disable it?\n\n"
        "This will:\n"
        "  - Stop NomadNet if running\n"
        "  - Leave files in place\n\n"
        "Reinstall later with: pipx install nomadnet\n\n"
        "Disable now?",
    ):
        return

    clear_screen()
    print("=== Disabling NomadNet ===\n")

    # Stop running processes
    if handler._is_nomadnet_running():
        print("Stopping NomadNet...")
        try:
            subprocess.run(
                ['pkill', '-f', 'bin/nomadnet'],
                capture_output=True, timeout=10,
            )
            time.sleep(2)
        except (subprocess.SubprocessError, OSError):
            pass

        if handler._is_nomadnet_running():
            try:
                subprocess.run(
                    ['pkill', '-9', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10,
                )
                time.sleep(1)
            except (subprocess.SubprocessError, OSError):
                pass

    if handler._is_nomadnet_running():
        print("NomadNet may still be running.")
        print("Try: sudo pkill -9 -f nomadnet")
    else:
        print("NomadNet stopped.")

    user_home = get_real_user_home()
    print(f"\nConfig remains at: {user_home}/.nomadnetwork/")
    print("Reinstall: pipx install nomadnet")

    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Config management
# ------------------------------------------------------------------

def view_nomadnet_config(handler):
    """View NomadNet configuration."""
    clear_screen()
    print("=== NomadNet Configuration ===\n")

    config_path = handler._get_nomadnet_config_path()
    if config_path and config_path.exists():
        print(f"Config: {config_path}\n")
        try:
            content = config_path.read_text()
            print(content)

            # Highlight key connectivity settings
            print("\n--- Connectivity Notes ---")
            content_lower = content.lower()
            if 'enable_client = yes' in content_lower:
                print("  Client:    ENABLED (can send/receive messages)")
            elif 'enable_client = no' in content_lower:
                print("  Client:    DISABLED")
            if 'enable_node = yes' in content_lower:
                print("  Node:      ENABLED (serving pages, propagation)")
            elif 'enable_node = no' in content_lower:
                print("  Node:      DISABLED (not serving)")
            if 'announce_at_start = yes' in content_lower:
                print("  Announce:  YES (visible to other nodes)")
            if 'user_interface = text' in content_lower:
                print("  UI mode:   text (interactive TUI with browser)")
        except PermissionError:
            print(f"Permission denied reading {config_path}")
    else:
        print("No NomadNet config found.\n")
        print("Config is created on first run of NomadNet.")
        print("Expected locations (checked in order):")
        print("  1. /etc/nomadnetwork/config")
        user_home = get_real_user_home()
        print(f"  2. {user_home}/.config/nomadnetwork/config")
        print(f"  3. {user_home}/.nomadnetwork/config")
        print("\nRun 'Launch Text UI' to create the default config.")

    handler.ctx.wait_for_enter()


def edit_nomadnet_config(handler):
    """Edit NomadNet config with available editor."""
    config_path = handler._get_nomadnet_config_path()

    if not config_path or not config_path.exists():
        if handler.ctx.dialog.yesno(
            "No Config Found",
            "NomadNet config doesn't exist yet.\n\n"
            "It is created automatically on first run.\n"
            "Launch NomadNet once to generate it?\n\n"
            "(It will create the config and exit.)",
        ):
            nn_path = handler._find_nomadnet_binary()
            if nn_path:
                handler.ctx.dialog.infobox("Generating Config", "Running NomadNet briefly to generate config...")
                try:
                    # Check if we need to use a specific RNS config path
                    rns_config_path = handler._get_rns_config_for_user()

                    # Build command - run as real user if we're under sudo
                    # This ensures config is created with correct ownership
                    sudo_user = os.environ.get('SUDO_USER')

                    # Build base args with optional --rnsconfig
                    nn_args = ['--daemon']
                    if rns_config_path:
                        nn_args = ['--rnsconfig', rns_config_path, '--daemon']

                    if sudo_user and sudo_user != 'root':
                        # Using -H instead of -i to set HOME without shell profiles
                        cmd = ['sudo', '-H', '-u', sudo_user, nn_path] + nn_args
                    else:
                        cmd = [nn_path] + nn_args

                    # Run daemon briefly, then kill to generate config
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(5)
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()

                    config_path = handler._get_nomadnet_config_path()
                    if config_path and config_path.exists():
                        handler.ctx.dialog.msgbox(
                            "Config Generated",
                            f"Config created at:\n  {config_path}\n\n"
                            f"Opening editor...",
                        )
                    else:
                        handler.ctx.dialog.msgbox(
                            "Config Not Found",
                            "NomadNet ran but config was not generated.\n"
                            "Check: ~/.nomadnetwork/config",
                        )
                        return
                except FileNotFoundError:
                    handler.ctx.dialog.msgbox("Error", f"NomadNet not found at: {nn_path}")
                    return
                except Exception as e:
                    handler.ctx.dialog.msgbox("Error", f"Failed to generate config:\n{e}")
                    return
        else:
            return

    if not config_path or not config_path.exists():
        return

    # Find editor
    editor = None
    for cmd in ['nano', 'vim', 'vi']:
        if shutil.which(cmd):
            editor = cmd
            break

    if not editor:
        handler.ctx.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
        return

    subprocess.run([editor, str(config_path)], timeout=None)


# ------------------------------------------------------------------
# Install
# ------------------------------------------------------------------

def install_nomadnet(handler):
    """Install NomadNet via pipx (isolated environment)."""
    if handler._is_nomadnet_installed():
        handler.ctx.dialog.msgbox("Already Installed", "NomadNet is already installed.")
        return

    if not handler.ctx.dialog.yesno(
        "Install NomadNet",
        "Install NomadNet RNS client?\n\n"
        "This will run:\n"
        "  pipx install nomadnet\n\n"
        "NomadNet pulls in RNS and LXMF automatically.\n\n"
        "It provides:\n"
        "  - Text UI with micron page browser\n"
        "  - LXMF encrypted messaging\n"
        "  - Node hosting and page serving\n"
        "  - Network announcement/discovery\n\n"
        "Source: github.com/markqvist/NomadNet\n\n"
        "Install now?",
    ):
        return

    clear_screen()
    print("=== Installing NomadNet ===\n")

    # Determine if we should install as a different user (when running via sudo)
    sudo_user = os.environ.get('SUDO_USER')
    run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

    try:
        # Ensure pipx is available (this needs root for apt)
        if not shutil.which('pipx'):
            print("Installing pipx...\n")
            result = subprocess.run(
                ['apt-get', 'install', '-y', 'pipx'],
                timeout=60
            )
            if result.returncode != 0:
                print("\nFailed to install pipx.")
                print("Try manually: sudo apt install pipx")
                handler.ctx.wait_for_enter()
                return

        # Build pipx commands - run as real user if we're under sudo
        def run_pipx_cmd(args, timeout_sec=300):
            """Run pipx command, as real user if running via sudo."""
            if run_as_user:
                # Run as real user with login shell (-i) to set HOME correctly
                cmd = ['sudo', '-i', '-u', run_as_user] + args
            else:
                cmd = args
            return subprocess.run(cmd, timeout=timeout_sec)

        # Ensure pipx bin dir is in PATH for this session
        print("Ensuring pipx paths...\n")
        run_pipx_cmd(['pipx', 'ensurepath'], timeout_sec=15)

        # Add common pipx bin dirs to current process PATH
        for bindir in [
            get_real_user_home() / '.local' / 'bin',
            Path('/root/.local/bin'),
            Path('/usr/local/bin'),
        ]:
            if bindir.is_dir() and str(bindir) not in os.environ.get('PATH', ''):
                os.environ['PATH'] = f"{bindir}:{os.environ.get('PATH', '')}"

        # Install nomadnet via pipx (live output)
        if run_as_user:
            print(f"\nInstalling NomadNet via pipx (as {run_as_user})...\n")
        else:
            print("\nInstalling NomadNet via pipx...\n")
        result = run_pipx_cmd(['pipx', 'install', 'nomadnet'])

        if result.returncode == 0:
            print("\nInstallation complete.")
            if handler._is_nomadnet_installed():
                nn_path = shutil.which('nomadnet')
                if nn_path:
                    print(f"NomadNet installed at: {nn_path}")
                else:
                    # Check user's local bin
                    user_bin = get_real_user_home() / '.local' / 'bin' / 'nomadnet'
                    if user_bin.exists():
                        print(f"NomadNet installed at: {user_bin}")

                # Configure NomadNet for shared instance mode (use rnsd)
                setup_nomadnet_shared_instance(run_as_user)
            else:
                print("\nnomadnet not found in PATH.")
                print("You may need to log out and back in,")
                print("or run: eval \"$(pipx ensurepath)\"")
        else:
            print(f"\nInstallation failed (exit code {result.returncode}).")
            print("Try manually: pipx install nomadnet")
    except FileNotFoundError:
        print("pipx not found.")
        print("Try: sudo apt install pipx && pipx install nomadnet")
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled.")
    except subprocess.TimeoutExpired:
        print("\nInstallation timed out. Check your internet connection.")
        print("Try manually: pipx install nomadnet")
    except Exception as e:
        print(f"\nInstallation error: {e}")
        print("Try manually: pipx install nomadnet")

    try:
        handler.ctx.wait_for_enter()
    except (EOFError, KeyboardInterrupt):
        pass


def setup_nomadnet_shared_instance(run_as_user: str = None):
    """Post-install message for NomadNet.

    NomadNet creates its own complete default config on first run.
    We don't create configs - let NomadNet use its defaults.
    """
    user_home = get_real_user_home()
    config_file = user_home / '.nomadnetwork' / 'config'

    if config_file.exists():
        print(f"\nNomadNet config exists: {config_file}")
    else:
        print("\nNomadNet will create its default config on first run.")

    print("\nNomadNet uses the shared RNS instance from rnsd by default.")
    print("Config location: ~/.nomadnetwork/config")
