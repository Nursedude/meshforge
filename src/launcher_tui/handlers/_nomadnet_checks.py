"""NomadNet pre-flight checks — RNS readiness, ownership repair, config validation.

Extracted from nomadnet.py for file size compliance (CLAUDE.md #6).
Functions take the NomadNetHandler instance for TUI interaction via handler.ctx.

Internal call graph:
  check_rns_for_nomadnet -> get_rnsd_user, wait_for_rns_port,
                            find_blocking_interfaces_via_handler, fix_rnsd_user
  diagnose_nomadnet_error -> get_rnsd_user
  validate_nomadnet_config -> handler._get_nomadnet_config_path()
  fix_user_directory_ownership -> handler.ctx.dialog
"""

import os
import stat
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from utils.paths import ReticulumPaths, get_real_user_home
from utils.safe_import import safe_import

# Import centralized service checking
check_process_running, stop_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'stop_service'
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Cross-handler delegation (RNS diagnostics handler)
# ------------------------------------------------------------------

def _get_rns_diagnostics_handler(handler):
    """Get the RNS diagnostics handler from the registry."""
    if handler.ctx.registry:
        return handler.ctx.registry.get_handler("rns_diagnostics")
    return None


def get_rnsd_user(handler) -> Optional[str]:
    """Get the OS user running the rnsd process, or None if not running.

    Delegates to RNSDiagnosticsHandler when available, falls back to
    direct process check.
    """
    diag = _get_rns_diagnostics_handler(handler)
    if diag:
        return diag._get_rnsd_user()
    # Fallback: direct ps check
    try:
        result = subprocess.run(
            ['ps', '-o', 'user=', '-C', 'rnsd'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        return lines[0].strip() if lines else None
    except (subprocess.SubprocessError, OSError):
        return None


def fix_rnsd_user(handler, target_user: str) -> bool:
    """Configure rnsd systemd service to run as the specified user.

    Delegates to RNSDiagnosticsHandler.
    """
    diag = _get_rns_diagnostics_handler(handler)
    if diag:
        return diag._fix_rnsd_user(target_user)
    handler.ctx.dialog.msgbox(
        "Not Available",
        "RNS diagnostics handler not available.\n\n"
        "Cannot reconfigure rnsd user automatically.",
    )
    return False


def wait_for_rns_port(handler, max_wait: int = 10) -> bool:
    """Wait for rnsd shared instance to become available.

    Delegates to RNSDiagnosticsHandler when available.
    """
    diag = _get_rns_diagnostics_handler(handler)
    if diag:
        return diag._wait_for_rns_port(max_wait=max_wait)
    # Fallback: simple socket check
    import socket
    for _ in range(max_wait):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', 37428))
            s.close()
            if result == 0:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def find_blocking_interfaces_via_handler(handler) -> list:
    """Check if enabled RNS interfaces have missing dependencies.

    Delegates to RNSDiagnosticsHandler when available.
    """
    diag = _get_rns_diagnostics_handler(handler)
    if diag:
        return diag._find_blocking_interfaces()
    return []


# ------------------------------------------------------------------
# RNS config path detection
# ------------------------------------------------------------------

def get_rns_config_for_user() -> str:
    """Get RNS config directory path appropriate for the current user.

    Returns the EXPLICIT config dir that NomadNet should use via
    --rnsconfig. This MUST match the config that rnsd is using to
    prevent config drift (different identities, stale auth tokens).

    Strategy:
    1. If /etc/reticulum/config exists AND storage is writable -> use it
    2. If storage is NOT writable -> FIX permissions (we run as root)
    3. Never fall back to ~/.reticulum -- that creates config drift

    IMPORTANT: Always return an explicit path. Never return None to
    let RNS use its own resolution, because user-context resolution
    may pick ~/.reticulum instead of /etc/reticulum, causing auth
    mismatches with rnsd.

    Returns:
        Path string to pass to --rnsconfig.
    """
    etc_rns = Path('/etc/reticulum')
    etc_config = etc_rns / 'config'

    # If system config exists, always use it -- fix permissions if needed
    if etc_config.is_file():
        storage_dir = etc_rns / 'storage'
        try:
            if storage_dir.exists():
                mode = storage_dir.stat().st_mode
                if not (mode & stat.S_IWOTH):
                    # Fix permissions -- we're root (sudo), we can do this.
                    # This prevents NomadNet from falling back to ~/.reticulum
                    # which would cause config drift with rnsd.
                    logger.info(
                        f"/etc/reticulum/storage mode {oct(mode)} missing "
                        f"world-writable bit, fixing to 0o777"
                    )
                    old_umask = os.umask(0)
                    try:
                        storage_dir.chmod(0o777)
                    finally:
                        os.umask(old_umask)
                    # Also fix file permissions inside storage
                    ReticulumPaths._fix_storage_file_permissions()
            else:
                # Create storage dir with correct permissions
                old_umask = os.umask(0)
                try:
                    storage_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
                finally:
                    os.umask(old_umask)
        except (OSError, PermissionError) as e:
            logger.warning(f"Could not fix /etc/reticulum/storage: {e}")

        return str(etc_rns)

    # No system config -- use default resolution
    # (ReticulumPaths.get_config_dir will find XDG or ~/.reticulum)
    config_dir = ReticulumPaths.get_config_dir()
    return str(config_dir)


# ------------------------------------------------------------------
# Ownership fix for user directories
# ------------------------------------------------------------------

def fix_user_directory_ownership(handler) -> bool:
    """Fix ownership of user directories if they were created by root.

    When MeshForge runs with sudo, any user-space applications (NomadNet,
    rnstatus, etc.) that were previously launched as root may have created
    ~/.reticulum or ~/.nomadnetwork with root ownership.

    This function detects and fixes that situation so the real user can
    access their own directories.

    Returns:
        True if directories are accessible (or were fixed successfully).
        False if fix failed and user declined to proceed.
    """
    sudo_user = os.environ.get('SUDO_USER')
    if not sudo_user or sudo_user == 'root':
        # Not running via sudo, nothing to fix
        return True

    user_home = get_real_user_home()
    if not user_home.exists():
        return True

    # Directories that should belong to the user, not root
    user_dirs = [
        user_home / '.reticulum',
        user_home / '.nomadnetwork',
        user_home / '.config' / 'nomadnetwork',
    ]

    dirs_to_fix = []
    for dir_path in user_dirs:
        if dir_path.exists():
            try:
                stat_info = dir_path.stat()
                # Check if owned by root (uid 0)
                if stat_info.st_uid == 0:
                    dirs_to_fix.append(dir_path)
            except (OSError, PermissionError):
                # Can't stat, might still be a problem
                dirs_to_fix.append(dir_path)

    if not dirs_to_fix:
        return True

    # Found directories with wrong ownership - offer to fix
    dir_list = '\n'.join(f'  {d}' for d in dirs_to_fix)
    if not handler.ctx.dialog.yesno(
        "Fix Directory Ownership",
        f"The following directories are owned by root,\n"
        f"which prevents NomadNet from accessing them:\n\n"
        f"{dir_list}\n\n"
        f"This happened because NomadNet or rnsd was\n"
        f"previously run as root.\n\n"
        f"Fix ownership to user '{sudo_user}'?",
    ):
        # User declined - warn but allow proceeding
        return handler.ctx.dialog.yesno(
            "Proceed Anyway?",
            "Ownership was not fixed.\n\n"
            "NomadNet may fail with 'Permission denied' errors.\n\n"
            "Continue anyway?",
        )

    # Fix ownership recursively
    handler.ctx.dialog.infobox("Fixing Ownership", f"Changing ownership to {sudo_user}...")

    for dir_path in dirs_to_fix:
        try:
            # chown -R user:user dir_path
            subprocess.run(
                ['chown', '-R', f'{sudo_user}:{sudo_user}', str(dir_path)],
                capture_output=True, timeout=30
            )
            logger.info(f"Fixed ownership of {dir_path} to {sudo_user}")
        except Exception as e:
            logger.warning(f"Failed to fix ownership of {dir_path}: {e}")
            handler.ctx.dialog.msgbox(
                "Ownership Fix Failed",
                f"Could not fix ownership of:\n  {dir_path}\n\n"
                f"Error: {e}\n\n"
                f"Try manually:\n  sudo chown -R {sudo_user}:{sudo_user} {dir_path}",
            )
            return False

    return True


# ------------------------------------------------------------------
# Error diagnostics
# ------------------------------------------------------------------

def diagnose_nomadnet_error(handler, returncode: int, sudo_user: str = None):
    """Analyze NomadNet failure and provide helpful diagnostics."""
    print(f"NomadNet exited with error code {returncode}")

    # Try to read the log file for clues
    user_home = get_real_user_home()
    logfile = user_home / '.nomadnetwork' / 'logfile'

    error_hints = []
    if logfile.exists():
        try:
            import collections
            with open(logfile, 'r') as f:
                last_lines = list(
                    collections.deque(f, maxlen=20)
                )

            # Look for known error patterns
            for line in last_lines:
                if 'AuthenticationError' in line or 'digest sent was rejected' in line:
                    error_hints.append("RPC authentication failed between NomadNet and rnsd")
                    # Check if rnsd is running as root
                    rnsd_user = get_rnsd_user(handler)
                    if rnsd_user == 'root':
                        error_hints.append("rnsd is running as root - identities don't match")
                        error_hints.append("Fix: sudo systemctl stop rnsd")
                        error_hints.append("     Then run rnsd as your user, or reconfigure")
                    elif rnsd_user and rnsd_user != sudo_user:
                        error_hints.append(f"rnsd runs as '{rnsd_user}', you are '{sudo_user}'")
                    else:
                        error_hints.append("Check that rnsd uses the same ~/.reticulum/ identity")
                    break
                elif 'KeyError' in line and 'textui' in line.lower():
                    error_hints.append("Config missing [textui] section")
                    error_hints.append("Delete ~/.nomadnetwork/config and restart")
                    break
                elif 'PermissionError' in line or 'Permission denied' in line:
                    if '/etc/reticulum' in line:
                        error_hints.append("Cannot write to /etc/reticulum/ (system config)")
                        error_hints.append("This happens when rnsd was run as root first")
                        error_hints.append("Fix: sudo rm -rf /etc/reticulum")
                        error_hints.append("     (or sudo chown -R $USER /etc/reticulum)")
                    else:
                        error_hints.append("Permission denied accessing files")
                        error_hints.append(f"Check ownership: ls -la ~/.nomadnetwork/")
                    break
                elif 'meshtastic' in line.lower() and (
                    'critical' in line.lower() or 'requires' in line.lower()
                    or 'no module' in line.lower() or 'modulenotfounderror' in line.lower()
                ):
                    error_hints.append("rnsd cannot load the meshtastic module")
                    error_hints.append("The Meshtastic_Interface.py plugin requires meshtastic")
                    error_hints.append(
                        "Fix: sudo pip3 install --break-system-packages "
                        "--ignore-installed meshtastic"
                    )
                    error_hints.append("Then: sudo systemctl restart rnsd")
                    break
                elif 'ModuleNotFoundError' in line or 'ImportError' in line:
                    error_hints.append("Missing Python dependencies")
                    error_hints.append("Try: pipx reinstall nomadnet")
                    break
        except (OSError, PermissionError):
            pass

    # If no NomadNet-specific error found, check rnsd journal for clues.
    # NomadNet fails when rnsd is down due to meshtastic module issue.
    if not error_hints:
        try:
            journal_r = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '20', '--no-pager', '-q'],
                capture_output=True, text=True, timeout=5
            )
            journal_text = journal_r.stdout.lower()
            if 'meshtastic' in journal_text and (
                'critical' in journal_text or 'module' in journal_text
            ):
                error_hints.append("rnsd crashed because the meshtastic module is missing")
                error_hints.append("NomadNet depends on rnsd for network access")
                error_hints.append(
                    "Fix: sudo pip3 install --break-system-packages "
                    "--ignore-installed meshtastic"
                )
                error_hints.append("Then: sudo systemctl restart rnsd")
            elif 'status=255' in journal_text or 'exception' in journal_text:
                error_hints.append("rnsd is crashing (exit code 255)")
                error_hints.append("Check: sudo journalctl -u rnsd -n 30")
        except (subprocess.SubprocessError, OSError):
            pass

    if error_hints:
        print("\nDiagnosis:")
        for hint in error_hints:
            print(f"  - {hint}")
    else:
        print("\nCheck logs for details:")
        print(f"  cat {logfile}")
        print("  journalctl --user -u nomadnet -n 50")


# ------------------------------------------------------------------
# RNS pre-flight check (LARGEST function)
# ------------------------------------------------------------------

def check_rns_for_nomadnet(handler) -> bool:
    """Check that RNS/rnsd is available and properly configured.

    Checks:
    1. Is /etc/reticulum blocking user access?
    2. Is rnsd running?
    3. Is rnsd running as root? (causes RPC auth failures with user NomadNet)

    Returns True if OK to proceed, False if user cancelled.
    """
    sudo_user = os.environ.get('SUDO_USER')

    # Check for /etc/reticulum permission issues first.
    # IMPORTANT: MeshForge runs as root (sudo) but NomadNet launches as
    # the real user. Check permissions for the REAL USER, not root.
    etc_rns = Path('/etc/reticulum')
    if etc_rns.exists():
        storage_dir = etc_rns / 'storage'
        can_write = False
        try:
            if storage_dir.exists():
                if sudo_user and sudo_user != 'root':
                    # Running via sudo -- check mode bits for real user
                    mode = storage_dir.stat().st_mode
                    can_write = bool(mode & stat.S_IWOTH)
                else:
                    # Not running via sudo -- direct write test is valid
                    test_file = storage_dir / '.write_test'
                    try:
                        test_file.touch()
                        test_file.unlink()
                        can_write = True
                    except (OSError, PermissionError):
                        pass
            else:
                try:
                    storage_dir.mkdir(parents=True, exist_ok=True)
                    can_write = True
                except (OSError, PermissionError):
                    pass
        except (OSError, ValueError) as e:
            logger.debug("RNS storage dir check failed: %s", e)

        if not can_write:
            # /etc/reticulum storage not writable -- fix it immediately.
            # We're running as root (sudo), so we can fix permissions.
            # NEVER fall back to ~/.reticulum -- that creates config drift
            # (different identity/auth tokens than rnsd -> auth failures).
            target_user = sudo_user if sudo_user and sudo_user != 'root' else 'current user'
            logger.info(
                f"/etc/reticulum/storage not writable by {target_user}, "
                "fixing permissions to 0o777"
            )
            try:
                old_umask = os.umask(0)
                try:
                    storage_dir.chmod(0o777)
                    # Also fix subdirectories and files
                    ReticulumPaths._fix_storage_file_permissions()
                finally:
                    os.umask(old_umask)
                handler.ctx.dialog.msgbox(
                    "Storage Permissions Fixed",
                    f"/etc/reticulum/storage/ permissions have been fixed.\n\n"
                    f"NomadNet will use the system config (same as rnsd).",
                )
            except (OSError, PermissionError) as e:
                handler.ctx.dialog.msgbox(
                    "Permission Fix Failed",
                    f"Could not fix /etc/reticulum/storage permissions:\n"
                    f"  {e}\n\n"
                    f"Try manually:\n"
                    f"  sudo chmod 777 /etc/reticulum/storage"
                )
                return False

    # Check if rnsd is running and get its user
    rnsd_user = get_rnsd_user(handler)

    if not rnsd_user:
        # rnsd not running -- warn but allow proceeding
        return handler.ctx.dialog.yesno(
            "rnsd Not Running",
            "The RNS daemon (rnsd) is not running.\n\n"
            "NomadNet can start its own RNS instance,\n"
            "but for Meshtastic bridging you should run rnsd\n"
            "with share_instance = Yes in the Reticulum config.\n\n"
            "Continue anyway?",
        )

    # rnsd is running -- wait for it to bind port 37428.
    # rnsd initializes crypto and interfaces BEFORE binding the shared
    # instance port, so we give it time before declaring failure.
    handler.ctx.dialog.infobox(
        "Checking rnsd",
        "Verifying rnsd shared instance (port 37428)...",
    )
    port_listening = wait_for_rns_port(handler, max_wait=10)

    if not port_listening:
        # rnsd running but not listening -- check for blocking interfaces
        blocking = []
        try:
            blocking = find_blocking_interfaces_via_handler(handler)
        except Exception as e:
            logger.debug("Blocking interface check failed: %s", e)

        if blocking:
            lines = ["rnsd is running but NOT listening on port 37428.\n"]
            lines.append("Cause: an enabled interface is blocking startup:\n")
            for iface_name, reason, fix in blocking:
                lines.append(f"  [{iface_name}] {reason}")
                lines.append(f"  Fix: {fix}\n")
            lines.append("NomadNet will fail to connect until this is resolved.")
            return handler.ctx.dialog.yesno(
                "rnsd Not Ready",
                "\n".join(lines) + "\n\nContinue anyway?",
            )
        else:
            # No blocking interfaces found -- may still be initializing
            return handler.ctx.dialog.yesno(
                "rnsd Not Ready",
                "rnsd is running but not yet listening on port 37428.\n\n"
                "It may still be initializing (crypto, interfaces).\n"
                "NomadNet may fail to connect.\n\n"
                "Continue anyway?",
            )

    # rnsd is running and listening - check for user mismatches
    current_uid = os.getuid()
    we_are_root = current_uid == 0

    if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
        # Case 1: rnsd as root, NomadNet wants to run as user
        choice = handler.ctx.dialog.menu(
            "rnsd Running as Root",
            "rnsd is running as root, but NomadNet needs to\n"
            "run as your user for RPC authentication.\n\n"
            "Different users = different RNS identities = auth failure.\n\n"
            "How do you want to fix this?",
            [
                ("fix", f"Fix rnsd to run as {sudo_user} (recommended)"),
                ("stop", "Stop rnsd (NomadNet will use its own RNS)"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "fix":
            return fix_rnsd_user(handler, sudo_user)
        elif choice == "stop":
            # Just stop rnsd
            handler.ctx.dialog.infobox("Stopping rnsd", "Stopping rnsd service...")
            try:
                stop_service('rnsd')
                subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True, timeout=5)
                time.sleep(1)
                handler.ctx.dialog.msgbox(
                    "rnsd Stopped",
                    "rnsd has been stopped.\n\n"
                    "NomadNet will start its own RNS instance.",
                )
                return True
            except Exception as e:
                handler.ctx.dialog.msgbox("Stop Failed", f"Could not stop rnsd: {e}")
                return False
        else:
            return False  # User cancelled

    elif we_are_root and rnsd_user and rnsd_user != 'root' and not sudo_user:
        # Case 2: We're root but SUDO_USER not set, rnsd runs as user
        # This is a fresh install issue - NomadNet would run as root
        # Store the rnsd user so we can run NomadNet as that user
        choice = handler.ctx.dialog.menu(
            "User Mismatch Detected",
            f"rnsd is running as '{rnsd_user}', but SUDO_USER is not set.\n\n"
            f"NomadNet would run as root, causing RPC auth failure.\n\n"
            f"Different users = different RNS identities = auth failure.\n\n"
            f"How do you want to proceed?",
            [
                ("run_as_user", f"Run NomadNet as '{rnsd_user}' (recommended)"),
                ("stop", "Stop rnsd (NomadNet will use its own RNS)"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "run_as_user":
            # Set SUDO_USER temporarily so _launch_nomadnet_textui uses it
            os.environ['SUDO_USER'] = rnsd_user
            handler.ctx.dialog.msgbox(
                "User Set",
                f"NomadNet will run as '{rnsd_user}'.\n\n"
                f"This matches the user running rnsd.",
            )
            return True
        elif choice == "stop":
            handler.ctx.dialog.infobox("Stopping rnsd", "Stopping rnsd service...")
            try:
                stop_service('rnsd')
                subprocess.run(['pkill', '-f', 'rnsd'], capture_output=True, timeout=5)
                time.sleep(1)
                handler.ctx.dialog.msgbox(
                    "rnsd Stopped",
                    "rnsd has been stopped.\n\n"
                    "NomadNet will start its own RNS instance.",
                )
                return True
            except Exception as e:
                handler.ctx.dialog.msgbox("Stop Failed", f"Could not stop rnsd: {e}")
                return False
        else:
            return False  # User cancelled

    # rnsd running as correct user (or no sudo context)
    return True


# ------------------------------------------------------------------
# Config validation
# ------------------------------------------------------------------

def validate_nomadnet_config(handler) -> bool:
    """Validate and repair NomadNet config if needed.

    NomadNet requires a [textui] section when running in text UI mode.
    If the config exists but lacks this section (e.g., old config from
    before [textui] was required), NomadNet will crash with KeyError.

    This function checks for and adds a minimal [textui] section if missing.

    Returns:
        True to proceed with launch, False if user cancelled.
    """
    config_path = handler._get_nomadnet_config_path()
    if not config_path or not config_path.exists():
        # No config yet - NomadNet will create default on first run
        return True

    try:
        content = config_path.read_text()
    except (OSError, PermissionError) as e:
        logger.warning(f"Cannot read NomadNet config: {e}")
        return True  # Let NomadNet handle the error

    # Check if [textui] section exists (case-insensitive)
    if '[textui]' in content.lower():
        return True

    # Missing [textui] section - need to add it
    logger.info(f"NomadNet config missing [textui] section: {config_path}")

    if not handler.ctx.dialog.yesno(
        "Config Repair Needed",
        f"Your NomadNet config is missing the [textui] section\n"
        f"required for text UI mode.\n\n"
        f"Config: {config_path}\n\n"
        f"Add a default [textui] section now?",
    ):
        return handler.ctx.dialog.yesno(
            "Proceed Anyway?",
            "Without [textui], NomadNet will crash.\n\n"
            "Continue anyway?",
        )

    # Add minimal [textui] section
    textui_section = """

[textui]
# Text UI configuration added by MeshForge
intro_time = 1
theme = dark
colormode = 256
glyphs = unicode
mouse_enabled = yes
hide_guide = no
"""
    try:
        # Append [textui] section to config
        with open(config_path, 'a') as f:
            f.write(textui_section)
        logger.info(f"Added [textui] section to {config_path}")

        # Fix ownership if running via sudo
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            subprocess.run(
                ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                capture_output=True, timeout=10
            )

        handler.ctx.dialog.msgbox(
            "Config Updated",
            f"Added [textui] section to config.\n\n"
            f"NomadNet text UI should now work.",
        )
        return True
    except (OSError, PermissionError) as e:
        handler.ctx.dialog.msgbox(
            "Config Update Failed",
            f"Could not update config:\n  {config_path}\n\n"
            f"Error: {e}\n\n"
            f"Add [textui] section manually or delete config\n"
            f"and let NomadNet recreate it.",
        )
        return False
