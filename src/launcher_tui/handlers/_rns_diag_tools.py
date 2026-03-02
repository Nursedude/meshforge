"""RNS diagnostic tools — CLI execution, connectivity diagnosis, conflict checks.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
Functions take ``handler`` as first parameter; access TUI via handler.ctx.
Internal cross-calls use module-level functions directly.
"""

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from utils.paths import get_real_user_home
from utils.service_check import (
    check_process_running, check_rns_shared_instance,
    start_service, stop_service, _sudo_cmd,
)

logger = logging.getLogger(__name__)

# Error patterns indicating RNS shared instance connectivity failure.
_RNS_SHARED_ERRORS = (
    "no shared", "could not connect", "could not get",
    "shared instance", "authenticationerror", "digest",
)


def run_rns_tool(handler, cmd: list, tool_name: str):
    """Run an RNS CLI tool with error detection and retry logic."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        combined = (result.stdout or "") + (result.stderr or "")
        lower_combined = combined.lower()
        has_shared_error = any(p in lower_combined for p in _RNS_SHARED_ERRORS)

        if "address already in use" in lower_combined:
            print("\nError: RNS port conflict (Address already in use)")
            print("Another process is bound to the RNS AutoInterface port.\n")
            diagnose_rns_port_conflict(handler)
        elif has_shared_error:
            # RNS shared instance issue — DIAGNOSE, don't auto-fix.
            print(f"\nRNS connectivity issue detected.")
            rnsd_running = False
            try:
                r = subprocess.run(
                    ['systemctl', 'is-active', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = r.stdout.strip() == 'active'
            except (subprocess.SubprocessError, OSError):
                pass

            if not rnsd_running:
                print("rnsd is not running.\n")
                if handler.ctx.dialog.yesno(
                    "rnsd Not Running",
                    f"{tool_name} failed because rnsd is not running.\n\n"
                    "Start rnsd now?\n\n"
                    "If rnsd won't start, use RNS > Diagnostics to investigate.",
                ):
                    try:
                        start_service('rnsd')
                        print("Starting rnsd...")
                        if wait_for_rns_shared_instance(max_wait=10):
                            print(f"rnsd started. Retrying {tool_name}...\n")
                            retry_combined = _retry_rns_tool(cmd)
                            if retry_combined is None:
                                pass  # Success — already printed
                            else:
                                diagnose_rns_connectivity(handler, retry_combined)
                        else:
                            print("rnsd started but shared instance not available.")
                            print("Check: sudo journalctl -u rnsd -n 20")
                            print("Or run: RNS > Diagnostics from the menu.")
                    except (subprocess.SubprocessError, OSError) as e:
                        print(f"Failed to start rnsd: {e}")
                else:
                    print("To start rnsd manually: sudo systemctl start rnsd")
            else:
                # rnsd IS running but tools can't connect — wait for init
                print("rnsd is running — waiting for shared instance...")
                port_ready = wait_for_rns_shared_instance()
                if port_ready:
                    print(f"Shared instance ready. Retrying {tool_name}...\n")
                    retry_combined = _retry_rns_tool(cmd)
                    if retry_combined is None:
                        pass  # Success
                    else:
                        diagnose_rns_connectivity(handler, retry_combined)
                else:
                    diagnose_rns_connectivity(handler, combined)
        elif result.returncode == 0:
            if result.stdout:
                print(result.stdout, end='')
        else:
            if result.stdout:
                print(result.stdout, end='')
            if result.stderr and result.stderr.strip():
                stderr_lower = result.stderr.lower()
                if "error" in stderr_lower or "traceback" in stderr_lower or "exception" in stderr_lower:
                    print(f"\n{tool_name} reported an error. Check: sudo journalctl -u rnsd -n 10")
    except FileNotFoundError:
        print(f"\n{tool_name} not found. Is RNS installed?")
        print("Install: pipx install rns")
    except subprocess.TimeoutExpired:
        print(f"\n{tool_name} timed out. RNS may be unresponsive.")
        print("Try restarting rnsd: sudo systemctl restart rnsd")


def _retry_rns_tool(cmd: list, attempts: int = 3) -> Optional[str]:
    """Retry an RNS CLI tool with stabilization delays.

    Returns None on success (output already printed), or the combined
    output string on failure for diagnostic use.
    """
    retry_combined = ""
    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(2)
        retry_result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        retry_combined = (
            (retry_result.stdout or "") + (retry_result.stderr or "")
        )
        retry_has_error = any(
            p in retry_combined.lower() for p in _RNS_SHARED_ERRORS
        )
        if (retry_result.returncode == 0
                and retry_result.stdout
                and not retry_has_error):
            print(retry_result.stdout, end='')
            return None  # Success
    return retry_combined  # All retries failed


def wait_for_rns_shared_instance(max_wait: int = 10) -> bool:
    """Wait for rnsd shared instance with 1-second polling."""
    for _ in range(max_wait):
        if check_rns_shared_instance():
            return True
        time.sleep(1)
    return False


def get_rnsd_user() -> Optional[str]:
    """Get the OS user running the rnsd process, or None if not running."""
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
    """Configure rnsd systemd service to run as the specified user."""
    if not re.match(r'^[a-z_][a-z0-9_.-]{0,31}$', target_user):
        handler.ctx.dialog.msgbox(
            "Invalid Username",
            f"'{target_user}' is not a valid Linux username.",
        )
        return False

    override_dir = Path('/etc/systemd/system/rnsd.service.d')
    override_file = override_dir / 'user.conf'

    handler.ctx.dialog.infobox(
        "Configuring rnsd",
        f"Setting rnsd to run as {target_user}...",
    )

    try:
        override_dir.mkdir(parents=True, exist_ok=True)
        override_content = (
            f"[Service]\nUser={target_user}\nGroup={target_user}\n"
        )
        override_file.write_text(override_content)

        stop_service('rnsd')
        subprocess.run(
            ['pkill', '-f', 'rnsd'], capture_output=True, timeout=5,
        )
        time.sleep(1)
        from utils.service_check import apply_config_and_restart
        apply_config_and_restart('rnsd')
        time.sleep(2)

        new_user = get_rnsd_user()
        if new_user == target_user:
            handler.ctx.dialog.msgbox(
                "rnsd Fixed",
                f"rnsd is now running as {target_user}.\n\n"
                f"Override created: {override_file}\n\n"
                "RNS tools and NomadNet can now connect via RPC.",
            )
            return True
        else:
            handler.ctx.dialog.msgbox(
                "Fix May Have Failed",
                f"rnsd is running as '{new_user}' "
                f"(expected '{target_user}').\n\n"
                f"Check: systemctl status rnsd\n"
                f"       cat {override_file}",
            )
            return True  # Let them try anyway

    except PermissionError:
        handler.ctx.dialog.msgbox(
            "Permission Denied",
            f"Cannot write to {override_dir}\n\n"
            "MeshForge needs to run with sudo to fix this.",
        )
        return False
    except Exception as e:
        handler.ctx.dialog.msgbox(
            "Configuration Failed",
            f"Could not configure rnsd: {e}\n\n"
            "Manual fix:\n"
            f"  sudo systemctl edit rnsd\n"
            f"  Add: [Service]\n"
            f"       User={target_user}",
        )
        return False


def diagnose_rns_connectivity(handler, error_output: str):
    """Show targeted diagnostics when rnsd is running but tools can't connect."""
    lower = error_output.lower()
    print("rnsd is running but RNS tools cannot connect.\n")

    # Auth errors are actionable
    if "authenticationerror" in lower or "digest" in lower:
        print("Cause: RPC authentication mismatch (stale auth tokens)")
        print("Fix:   Clear auth tokens and restart rnsd:\n")
        print("  sudo systemctl stop rnsd")
        print("  sudo rm -f /etc/reticulum/storage/shared_instance_*")
        print("  sudo rm -f /root/.reticulum/storage/shared_instance_*")
        user_home = get_real_user_home()
        print(f"  rm -f {user_home}/.reticulum/storage/shared_instance_*")
        print("  sudo systemctl start rnsd")
        return

    # Check if rnsd is running as root (causes identity/auth mismatch)
    rnsd_user = get_rnsd_user()
    sudo_user = os.environ.get('SUDO_USER', '')
    if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
        print(
            f"Cause: rnsd is running as root, but you are "
            f"'{sudo_user}'\n"
            f"       Different users = different RNS identities "
            f"= auth failure\n"
        )
        nomadnet_installed = bool(shutil.which('nomadnet'))
        meshchat_installed = check_meshchat_installed()
        menu_items = [
            ("fix", f"Fix rnsd to run as {sudo_user} (recommended)"),
        ]
        if nomadnet_installed:
            menu_items.append(
                ("nomadnet", "Stop rnsd — let NomadNet manage RNS"),
            )
        if meshchat_installed:
            menu_items.append(
                ("meshchat", "Stop rnsd — let MeshChat manage RNS"),
            )
        menu_items.append(("skip", "Skip (show diagnostics)"))

        choice = handler.ctx.dialog.menu(
            "rnsd Running as Root",
            "rnsd is running as root, but RNS tools run as\n"
            f"'{sudo_user}'. Different users = different RNS\n"
            "identities = RPC authentication failure.\n\n"
            "How do you want to fix this?",
            menu_items,
        )
        if choice == "fix":
            if fix_rnsd_user(handler, sudo_user):
                print("\nRetry: RNS > Status from the menu.")
            return
        elif choice in ("nomadnet", "meshchat"):
            app_name = "NomadNet" if choice == "nomadnet" else "MeshChat"
            handler.ctx.dialog.infobox(
                "Stopping rnsd", "Stopping rnsd service...",
            )
            stop_service('rnsd')
            subprocess.run(
                ['pkill', '-f', 'rnsd'], capture_output=True, timeout=5,
            )
            time.sleep(1)
            handler.ctx.dialog.msgbox(
                "rnsd Stopped",
                f"rnsd has been stopped.\n\n"
                f"{app_name} will start its own RNS instance.\n"
                f"The gateway bridge will connect to {app_name}'s\n"
                f"shared instance as a client.\n\n"
                f"Note: RNS is only available while {app_name} runs.",
            )
            return
        elif choice is None:
            return  # User cancelled
        # "skip" falls through to existing diagnostics

    # Check for blocking interfaces (most common root cause)
    blocking = handler._find_blocking_interfaces()
    if blocking:
        print("Cause: rnsd is stuck initializing a blocking interface.\n")
        for iface_name, reason, fix in blocking:
            print(f"  [{iface_name}] {reason}")
            print(f"  Fix: {fix}\n")
        print("Options:")
        print("  1. Start the missing dependency (see Fix above)")
        print("  2. Disable the interface in /etc/reticulum/config")
        print("     (change 'enabled = yes' to 'enabled = no')")
        print("  3. sudo systemctl restart rnsd (after fixing)")
        return

    # No specific cause detected — show actual rnsd log
    print("Showing recent rnsd log:\n")
    try:
        r = subprocess.run(
            ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        if r.stdout and r.stdout.strip():
            for line in r.stdout.strip().split('\n'):
                print(f"  {line}")
        else:
            print("  (no log output)")
    except (subprocess.SubprocessError, OSError):
        print("  (could not read journal)")
    print("\nTo restart: sudo systemctl restart rnsd")


def check_nomadnet_conflict() -> bool:
    """Check if NomadNet is running and holding the shared instance port."""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'nomadnet'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def check_lxmf_app_conflict() -> Optional[str]:
    """Check if an LXMF app (NomadNet or MeshChat) holds port 37428.

    Returns the app name if conflict detected, None otherwise.
    """
    for app_name, pattern in [("NomadNet", "nomadnet"), ("MeshChat", "meshchat")]:
        try:
            result = subprocess.run(
                ['pgrep', '-f', pattern],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return app_name
        except (subprocess.SubprocessError, OSError):
            pass
    return None


def check_rns_interface_health():
    """Run rnstatus and parse per-interface TX/RX counters.

    Returns list of (interface_name, tx_str, rx_str, is_healthy) tuples.
    Returns empty list if rnstatus is unavailable or fails.
    """
    rnstatus_path = shutil.which('rnstatus')
    if not rnstatus_path:
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'rnstatus'
        if candidate.exists():
            rnstatus_path = str(candidate)
    if not rnstatus_path:
        return []

    try:
        result = subprocess.run(
            [rnstatus_path], capture_output=True, text=True, timeout=15
        )
        combined = (result.stdout or '') + (result.stderr or '')
        if ('no shared' in combined.lower()
                or 'could not' in combined.lower()):
            return []
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []

    interfaces = []
    current_iface = None
    tx_cache = {}

    for line in combined.splitlines():
        iface_match = re.match(r'\s*(\w+)\[(.+?)\]', line)
        if iface_match:
            current_iface = f"{iface_match.group(1)}[{iface_match.group(2)}]"
            continue

        tx_match = re.search(r'↑\s*([\d,.]+)\s*(\w+)', line)
        if tx_match and current_iface:
            tx_cache[current_iface] = (
                tx_match.group(1).replace(',', ''), tx_match.group(2),
            )

        rx_match = re.search(r'↓\s*([\d,.]+)\s*(\w+)', line)
        if rx_match and current_iface:
            rx_val = rx_match.group(1).replace(',', '')
            rx_unit = rx_match.group(2)
            tx_info = tx_cache.get(current_iface, ('0', 'B'))
            tx_str = f"{tx_info[0]} {tx_info[1]}"
            rx_str = f"{rx_val} {rx_unit}"
            try:
                tx_bytes = float(tx_info[0])
                rx_bytes = float(rx_val)
            except ValueError:
                tx_bytes = rx_bytes = 0
            healthy = not (rx_bytes > 0 and tx_bytes == 0)
            interfaces.append((current_iface, tx_str, rx_str, healthy))
            current_iface = None

    return interfaces


def diagnose_rns_port_conflict(handler):
    """Diagnose and offer to fix RNS port conflicts from the TUI."""
    try:
        conflicting_app = check_lxmf_app_conflict()
        if conflicting_app:
            app_lower = conflicting_app.lower()
            print(f"CAUSE: {conflicting_app} is running and owns port 37428.")
            print(f"rnsd can't start because {conflicting_app} has the port.\n")
            if handler.ctx.dialog.yesno(
                "Fix Port Conflict",
                f"{conflicting_app} is holding port 37428.\n\n"
                f"MeshForge can fix this:\n"
                f"  1. Stop {conflicting_app}\n"
                f"  2. Start rnsd (becomes shared instance)\n"
                f"  3. Restart {conflicting_app} (connects as client)\n\n"
                f"Fix now?"
            ):
                print(f"Stopping {conflicting_app}...")
                subprocess.run(
                    ['pkill', '-f', app_lower], capture_output=True, timeout=5
                )
                time.sleep(1)
                print("Starting rnsd...")
                start_service('rnsd')
                time.sleep(2)
                print(f"Done. Startup order: rnsd -> {conflicting_app} -> MeshForge\n")
            return

        rnsd_running = check_process_running('rnsd')
        if rnsd_running:
            try:
                pid_result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                pid = pid_result.stdout.strip().split('\n')[0] if pid_result.stdout else 'unknown'
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("rnsd PID lookup failed: %s", e)
                pid = 'unknown'
            print(f"rnsd is running (PID: {pid}) but may need a restart:")
            print("  sudo systemctl restart rnsd")
        else:
            print("No rnsd found. A stale process may be holding the port.")
            print("  Find it:    sudo lsof -i UDP:37428")
            print("  Kill stale: pkill -f rnsd")
            print("  Or wait ~30s for the socket to timeout")
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("RNS port conflict diagnosis failed: %s", e)
        print("  Try: sudo systemctl restart rnsd")


def get_config_handler(handler):
    """Get the RNS config handler for cross-handler calls."""
    if handler.ctx.registry:
        return handler.ctx.registry.get_handler("rns_config")
    return None


def check_meshchat_installed() -> bool:
    """Check if MeshChat is installed."""
    try:
        result = subprocess.run(
            ['which', 'meshchat'], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def offer_drift_fix(handler, drift_result):
    """Offer to fix config drift by migrating to /etc/reticulum/."""
    from utils.paths import ReticulumPaths
    from utils.service_check import (
        get_rns_shared_instance_info, start_service as _start,
        stop_service as _stop, _sudo_cmd as _scmd,
    )
    from utils.config_drift import detect_rnsd_config_drift

    etc_config = Path('/etc/reticulum/config')
    source_candidates = []
    if drift_result.rnsd_config_dir:
        source_candidates.append(drift_result.rnsd_config_dir / 'config')
    if drift_result.gateway_config_dir:
        source_candidates.append(drift_result.gateway_config_dir / 'config')

    source = None
    for candidate in source_candidates:
        if candidate.is_file():
            source = candidate
            break

    if source is None:
        print("  Cannot find a source config file to migrate.")
        handler.ctx.wait_for_enter()
        return

    if etc_config.exists():
        dialog_text = (
            f"Config drift: gateway and rnsd use different paths.\n\n"
            f"  Gateway: {drift_result.gateway_config_dir}\n"
            f"  rnsd:    {drift_result.rnsd_config_dir}\n\n"
            f"/etc/reticulum/config already exists.\n\n"
            f"MeshForge will:\n"
            f"  1. Keep existing /etc/reticulum/config\n"
            f"  2. Rename old config(s) to .migrated\n"
            f"  3. Clear stale auth tokens\n"
            f"  4. Restart rnsd\n\nFix now?"
        )
    else:
        dialog_text = (
            f"Config drift: gateway and rnsd use different paths.\n\n"
            f"  Gateway: {drift_result.gateway_config_dir}\n"
            f"  rnsd:    {drift_result.rnsd_config_dir}\n\n"
            f"MeshForge will:\n"
            f"  1. Migrate {source} to /etc/reticulum/config\n"
            f"  2. Rename old config to .migrated\n"
            f"  3. Clear stale auth tokens\n"
            f"  4. Restart rnsd\n\nFix now?"
        )

    if not handler.ctx.dialog.yesno("Fix Config Drift", dialog_text):
        handler.ctx.wait_for_enter()
        return

    print("\n--- Fixing Config Drift ---\n")

    # Step 1: Migrate config
    print("[1/7] Migrating config to /etc/reticulum/...")
    if etc_config.exists():
        print(f"  /etc/reticulum/config already exists — keeping it")
        for candidate in source_candidates:
            if candidate.is_file() and not str(candidate).startswith('/etc/'):
                try:
                    backup = candidate.with_suffix('.migrated')
                    candidate.rename(backup)
                    print(f"  Renamed: {candidate} -> {backup.name}")
                except (OSError, PermissionError) as e:
                    print(f"  Warning: Could not rename {candidate}: {e}")
    else:
        if handler._get_config_handler()._migrate_rns_config_to_etc(source):
            print(f"  Migrated: {source} -> /etc/reticulum/config")
        else:
            print("  Migration failed. Aborting.")
            handler.ctx.wait_for_enter()
            return

    # Step 2: Ensure dirs
    print("\n[2/7] Ensuring /etc/reticulum/ directory structure...")
    if ReticulumPaths.ensure_system_dirs():
        print("  Directories OK (storage, ratchets, cache, interfaces)")
    else:
        print("  Warning: Could not create all directories (need sudo?)")

    # Step 3: Clear stale auth tokens
    print("\n[3/7] Clearing stale auth tokens...")
    user_home = get_real_user_home()
    storage_dirs = [
        Path('/etc/reticulum/storage'),
        Path('/root/.reticulum/storage'),
        user_home / '.reticulum' / 'storage',
        user_home / '.config' / 'reticulum' / 'storage',
    ]
    files_cleared = 0
    for storage_dir in storage_dirs:
        if storage_dir.exists():
            for auth_file in storage_dir.glob('shared_instance_*'):
                try:
                    auth_file.unlink()
                    files_cleared += 1
                    print(f"  Removed: {auth_file}")
                except (OSError, PermissionError) as e:
                    print(f"  Warning: Could not remove {auth_file}: {e}")
    if files_cleared == 0:
        print("  No stale auth files found")

    # Step 4: Validate service file
    print("\n[4/7] Validating rnsd.service...")
    if not handler._validate_rnsd_service_file():
        print("  Service file: OK")

    # Step 5: Check dependencies
    print("\n[5/7] Checking rnsd Python dependencies...")
    handler._ensure_rnsd_dependencies()

    # Step 6: Restart rnsd
    print("\n[6/7] Restarting rnsd...")
    success, msg = _stop('rnsd')
    if not success:
        print(f"  Warning stopping rnsd: {msg}")
    time.sleep(1)
    try:
        subprocess.run(
            _scmd(['systemctl', 'reset-failed', 'rnsd']),
            capture_output=True, timeout=5
        )
    except (subprocess.SubprocessError, OSError):
        pass
    success, msg = _start('rnsd')
    if success:
        print("  rnsd started")
    else:
        print(f"  Warning: {msg}")

    # Step 7: Verify
    print("\n[7/7] Verifying fix...")
    print("  Waiting for shared instance...")
    instance_ready = wait_for_rns_shared_instance(max_wait=15)
    if instance_ready:
        si_info = get_rns_shared_instance_info()
        print(f"  Shared instance: available ({si_info['detail']})")
        verify = detect_rnsd_config_drift()
        if not verify.drifted:
            print(f"\n  \033[0;32mDrift resolved!\033[0m Config aligned at: "
                  f"{verify.gateway_config_dir}")
        else:
            print(f"\n  \033[0;33mDrift may persist.\033[0m")
            print(f"  Gateway: {verify.gateway_config_dir}")
            print(f"  rnsd:    {verify.rnsd_config_dir}")
            print("  You may need to restart MeshForge for path resolution to update.")
    else:
        print("  Shared instance not available after 15s.")
        print("  rnsd may be slow to initialize or may have crashed.")
        print("  Check: sudo journalctl -u rnsd -n 20")

    print()
    handler.ctx.wait_for_enter()
