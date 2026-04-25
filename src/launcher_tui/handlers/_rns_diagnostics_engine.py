"""RNS diagnostic runner functions.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
Functions take handler for TUI interaction (same pattern as _rns_repair.py).
"""

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen
from utils.service_check import (
    check_process_running, check_rns_shared_instance,
    get_rns_shared_instance_info, get_udp_port_owner,
    start_service, stop_service,
)
from commands.rns import (
    create_identities, check_connectivity, get_status,
)
from utils.config_drift import detect_rnsd_config_drift

logger = logging.getLogger(__name__)


def run_rns_diagnostics(handler):
    """Run comprehensive RNS diagnostics.

    Args:
        handler: RNSDiagnosticsHandler instance for TUI interaction.
    """
    clear_screen()
    print("=== RNS Diagnostics ===\n")

    # Collect issues and warnings throughout diagnostics
    issues = []
    warnings = []

    # 1. Service status
    print("[1/6] Checking rnsd service...")
    status = get_status()
    status_data = status.data or {}
    running = status_data.get('rnsd_running', False)
    service_state = status_data.get('service_state', '')
    print(f"  rnsd: {'RUNNING' if running else 'NOT RUNNING'}")
    if status_data.get('rnsd_pid'):
        print(f"  PID: {status_data['rnsd_pid']}")
    if service_state:
        print(f"  State: {service_state}")

    # Check rnsd.service file for misplaced directives
    service_file = Path('/etc/systemd/system/rnsd.service')
    if service_file.exists():
        try:
            svc_content = service_file.read_text()
            svc_section = None
            for svc_line in svc_content.splitlines():
                svc_stripped = svc_line.strip()
                if svc_stripped.startswith('[') and svc_stripped.endswith(']'):
                    svc_section = svc_stripped
                elif svc_section == '[Service]' and (
                    'StartLimitIntervalSec' in svc_stripped
                    or 'StartLimitBurst' in svc_stripped
                ):
                    print(f"  Service file: has misplaced directives in [Service]")
                    warnings.append(
                        "rnsd.service: StartLimitIntervalSec in [Service] "
                        "(should be [Unit]) — run Repair to fix"
                    )
                    break
            else:
                print(f"  Service file: OK")
        except (OSError, PermissionError):
            print("  Service file: could not read (check permissions)")

    # Detect LXMF app conflict (common cause of rnsd crash-loops)
    conflicting_app = handler._check_lxmf_app_conflict()
    if conflicting_app:
        print(f"  {conflicting_app}: RUNNING (port conflict!)")
        # Show port 37428 owner for clarity
        try:
            owner = get_udp_port_owner(37428)
            if owner:
                proc_name, pid = owner
                print(f"  Port 37428 owner: {proc_name} (PID {pid})")
        except Exception:
            pass
    if service_state == 'failed' or (not running and conflicting_app):
        print("")
        if conflicting_app:
            print("  WARNING: NomadNet is holding the RNS shared "
                  "instance port.")
            print("  rnsd cannot bind port 37428 while NomadNet "
                  "is running.")
            print("  Fix: stop NomadNet first, or disable rnsd "
                  "and let NomadNet")
            print("  serve as the shared instance.")
            # Show NomadNet log tail for context
            nn_logfile = (get_real_user_home()
                          / '.nomadnetwork' / 'logfile')
            if nn_logfile.exists():
                try:
                    import collections
                    with open(nn_logfile, 'r') as f:
                        last_lines = list(
                            collections.deque(f, maxlen=5)
                        )
                    if last_lines:
                        print("\n  Recent NomadNet log entries:")
                        for line in last_lines:
                            print(f"    {line[:100]}")
                except (OSError, PermissionError):
                    print("    (cannot read NomadNet logfile)")
        elif service_state == 'failed':
            print("  WARNING: rnsd has crashed. Check logs:")
            print("    sudo journalctl -u rnsd -n 30")

    # 2. Config check
    print("\n[2/6] Checking configuration...")
    config_dir = ReticulumPaths.get_config_dir()
    config_exists = status_data.get('config_exists', False)
    print(f"  Config: {'found' if config_exists else 'MISSING'}")
    if config_exists:
        iface_count = status_data.get('interface_count', 0)
        print(f"  Interfaces: {iface_count}")

    # 2b. Check for config shadowing and problematic settings
    try:
        from utils.config_drift import detect_config_shadowing
        shadow = detect_config_shadowing()
        if shadow.shadowed:
            print(f"  WARNING: Config shadowing detected!")
            print(f"    Active:  {shadow.active_path}")
            print(f"    Ignored: {shadow.ignored_path}")
            if shadow.differences:
                print("    Differences:")
                for diff in shadow.differences:
                    print(f"      - {diff}")
            warnings.append(
                f"Config shadowing: {shadow.ignored_path} is silently "
                f"ignored because {shadow.active_path} takes precedence"
            )
    except Exception as e:
        logger.debug("Config shadowing check failed: %s", e)

    # 2c. Check for shared_instance_type = tcp (broken on Linux/epoll)
    try:
        config_path = config_dir / 'config' if config_exists else None
        if config_path and config_path.is_file():
            cfg_text = config_path.read_text()
            in_ret = False
            for line in cfg_text.split('\n'):
                s = line.strip()
                if s.startswith('#'):
                    continue
                if s == '[reticulum]':
                    in_ret = True
                    continue
                if s.startswith('[') and in_ret:
                    break
                if (in_ret and 'shared_instance_type' in s
                        and '=' in s):
                    _, val = s.split('=', 1)
                    if val.strip().lower() == 'tcp':
                        print("  WARNING: shared_instance_type = tcp")
                        print("    This causes silent shared instance "
                              "failures on Linux with epoll.")
                        print("    Fix: Remove this setting to use "
                              "domain sockets (default, recommended).")
                        warnings.append(
                            "shared_instance_type = tcp causes silent "
                            "failures on Linux with epoll"
                        )
                    break
    except (OSError, PermissionError) as e:
        logger.debug("shared_instance_type check failed: %s", e)

    # 3. Identity check
    print("\n[3/6] Checking identity...")
    identity_exists = status_data.get('identity_exists', False)
    print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
    rns_identity = config_dir / 'identity'
    print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

    # 4. Full connectivity check
    print("\n[4/6] Running connectivity check...")
    conn = check_connectivity()
    conn_data = conn.data or {}
    print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
    if conn_data.get('rns_version'):
        print(f"  RNS version: {conn_data['rns_version']}")
    print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
    print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

    # Merge issues and warnings from connectivity check
    issues.extend(conn_data.get('issues', []))
    warnings.extend(conn_data.get('warnings', []))

    # 5. Interface dependencies
    print("\n[5/6] Checking interface dependencies...")
    try:
        blocking = handler._find_blocking_interfaces()
        if blocking:
            for iface_name, reason, fix in blocking:
                print(f"  ! [{iface_name}] {reason}")
                print(f"    Fix: {fix}")
                issues.append(f"Blocking interface: {iface_name}")
        else:
            print("  All enabled interfaces have their dependencies met")
    except Exception as e:
        logger.debug("Interface dependency check failed: %s", e)
        print(f"  Could not check: {e}")

    # Check if shared instance is actually reachable.
    # RNS uses abstract Unix domain sockets on Linux (\0rns/default),
    # NOT UDP port 37428. check_rns_shared_instance() checks both.
    instance_ok = False
    instance_name = ReticulumPaths.get_configured_instance_name()
    try:
        si_info = get_rns_shared_instance_info(instance_name)
        instance_ok = si_info['available']
        if running and not instance_ok:
            # rnsd may still be initializing — wait before declaring failure
            print("  rnsd running but shared instance not yet available...")
            print("  Waiting for rnsd to finish initializing...")
            instance_ok = handler._wait_for_rns_shared_instance(max_wait=10)
            if instance_ok:
                si_info = get_rns_shared_instance_info(instance_name)
                print(f"  Shared instance: available (slow startup)")
                print(f"    Method: {si_info['detail']}")
            else:
                print("  ! rnsd running but shared instance NOT "
                      "available after 10s wait")
                print(f"    {si_info['detail']}")
                if si_info.get('diagnostic'):
                    print(f"    Diagnostic: {si_info['diagnostic']}")
                # Show who owns port 37428 (if anyone, for TCP/UDP mode)
                try:
                    owner = get_udp_port_owner(37428)
                    if owner:
                        proc_name, pid = owner
                        print(f"    Port 37428 held by: {proc_name} "
                              f"(PID {pid})")
                except Exception:
                    pass
                # Check if share_instance is enabled in config
                share_ok = conn_data.get('share_instance', None)
                if share_ok is False:
                    print("    Cause: share_instance is not "
                          "enabled in [reticulum] config")
                    print("    Fix: Add 'share_instance = Yes' "
                          "to [reticulum] section,")
                    print("         then restart: sudo systemctl "
                          "restart rnsd")
                    issues.append(
                        "share_instance not enabled — gateway "
                        "cannot connect to rnsd")
                else:
                    # Check for config drift as potential root cause
                    try:
                        drift = detect_rnsd_config_drift()
                        if drift.drifted:
                            print(f"    Config drift: gateway reads {drift.gateway_config_dir}")
                            print(f"                  rnsd reads    {drift.rnsd_config_dir}")
                            print(f"    Fix: {drift.fix_hint}")
                            issues.append(
                                "Config drift — rnsd and gateway "
                                "use different config paths")
                    except Exception as e:
                        logger.debug("Config drift check failed: %s", e)
                    # Surface recent journal errors (unfiltered)
                    try:
                        r = subprocess.run(
                            ['journalctl', '-u', 'rnsd', '-n', '10',
                             '--no-pager', '-q', '--no-hostname'],
                            capture_output=True, text=True, timeout=10
                        )
                        if r.stdout and r.stdout.strip():
                            print("    Recent rnsd log:")
                            for line in r.stdout.strip().splitlines()[-5:]:
                                print(f"      {line.strip()[:100]}")
                    except (subprocess.SubprocessError, OSError):
                        pass
                    warnings.append(
                        "rnsd active but shared instance "
                        "not available")
        elif running and instance_ok:
            print(f"  Shared instance: available ({si_info['method']})")
    except Exception as e:
        logger.debug("Port check failed: %s", e)

    # 6. Interface TX/RX health
    print("\n[6/6] Checking interface traffic...")
    try:
        iface_health = check_rns_interface_health()
        if iface_health:
            rx_only_found = False
            for name, tx, rx, healthy in iface_health:
                if healthy:
                    print(f"  {name}: ↑{tx} ↓{rx}")
                else:
                    print(f"  {name}: RX-ONLY (↑{tx} ↓{rx})")
                    issues.append(
                        f"Interface {name} is RX-only (no TX)")
                    rx_only_found = True
            if rx_only_found:
                print("\n  RX-only interfaces = link "
                      "establishment failing.")
                print("  Common cause: shared instance port "
                      "37428 not bound.")
        else:
            # Provide specific reason instead of generic message
            rnstatus_path = shutil.which('rnstatus')
            if not rnstatus_path:
                print("  rnstatus not installed — install RNS tools: pip install rns")
            elif running and not instance_ok:
                print("  rnstatus available but cannot connect (shared instance not available)")
            else:
                print("  Could not retrieve interface traffic from rnstatus")
    except Exception as e:
        logger.debug("Interface health check failed: %s", e)
        print(f"  Could not check: {e}")

    # Summary
    if issues:
        print(f"\n--- Issues Found ({len(issues)}) ---")
        for issue in issues:
            print(f"  ! {issue}")
    if warnings:
        print(f"\n--- Warnings ({len(warnings)}) ---")
        for warning in warnings:
            print(f"  ~ {warning}")

    if not issues and not warnings:
        print("\n--- All checks passed ---")
    elif not issues:
        print("\n--- Connectivity OK (with warnings) ---")

    # Offer inline repair if shared instance is not available
    if running and not instance_ok:
        print("\n--- Quick Fix ---")
        if handler.ctx.dialog.yesno(
            "Repair RNS",
            "RNS shared instance is not available.\n\n"
            "Run the RNS repair wizard now?\n"
            "This will validate config, check dependencies,\n"
            "and restart rnsd.\n\n"
            "Repair now?"
        ):
            clear_screen()
            handler._repair_rns_shared_instance()
            handler.ctx.wait_for_enter()
            return

    # Offer to create missing identities
    if not identity_exists or not rns_identity.exists():
        print("\n--- Identity Setup ---")
        if handler.ctx.dialog.yesno(
            "Create Identities",
            "One or more RNS identities are missing.\n\n"
            "Create them now?\n\n"
            "  • RNS identity: used by rnsd for network presence\n"
            "  • Gateway identity: used by MeshForge bridge"
        ):
            try:
                result = create_identities()
                if result.success:
                    print(f"  ✓ {result.message}")
                    created = (result.data or {}).get('created', [])
                    if 'rns' in created:
                        print(f"    RNS identity: {result.data['rns_identity']}")
                    if 'gateway' in created:
                        print(f"    Gateway identity: {result.data['gateway_identity']}")
                else:
                    print(f"  ✗ {result.message}")
            except Exception as e:
                print(f"  ✗ Identity creation failed: {e}")

    # RNS tool availability
    print("\n--- RNS Tool Availability ---")
    for tool in ['rnsd', 'rnstatus', 'rnpath', 'rnprobe', 'rnid', 'rncp', 'rnx']:
        path = shutil.which(tool)
        if path:
            print(f"  {tool}: {path}")
        else:
            print(f"  {tool}: not found")

    handler.ctx.wait_for_enter()


def diagnose_rns_connectivity(handler, error_output: str):
    """Show targeted diagnostics when rnsd is running but tools can't connect.

    Instead of guessing, check for auth errors (actionable) then fall
    through to showing the actual rnsd journal log.

    Args:
        handler: RNSDiagnosticsHandler instance for TUI interaction.
        error_output: Error output from the failed RNS tool.
    """
    lower = error_output.lower()
    print("rnsd is running but RNS tools cannot connect.\n")

    # Auth errors are actionable — detect and show specific fix
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
    rnsd_user = handler._get_rnsd_user()
    sudo_user = os.environ.get('SUDO_USER', '')
    if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
        print(
            f"Cause: rnsd is running as root, but you are "
            f"'{sudo_user}'\n"
            f"       Different users = different RNS identities "
            f"= auth failure\n"
        )
        nomadnet_installed = bool(shutil.which('nomadnet'))
        menu_items = [
            ("fix", f"Fix rnsd to run as {sudo_user} (recommended)"),
        ]
        if nomadnet_installed:
            menu_items.append(
                ("nomadnet",
                 "Stop rnsd — let NomadNet manage RNS"),
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
            if handler._fix_rnsd_user(sudo_user):
                print("\nRetry: RNS > Status from the menu.")
            return
        elif choice == "nomadnet":
            app_name = "NomadNet"
            handler.ctx.dialog.infobox(
                "Stopping rnsd",
                "Stopping rnsd service...",
            )
            stop_service('rnsd')
            subprocess.run(
                ['pkill', '-f', 'rnsd'],
                capture_output=True, timeout=5,
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


def check_rns_interface_health():
    """Run rnstatus and parse per-interface TX/RX counters.

    Returns a list of (interface_name, tx_str, rx_str, is_healthy)
    tuples. An interface is unhealthy if it has RX but zero TX
    (link establishment / SYN/ACK failing).

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
            [rnstatus_path],
            capture_output=True, text=True, timeout=15
        )
        combined = (result.stdout or '') + (result.stderr or '')
        if ('no shared' in combined.lower()
                or 'could not' in combined.lower()):
            return []
    except (subprocess.SubprocessError, FileNotFoundError,
            OSError):
        return []

    interfaces = []
    current_iface = None
    tx_cache = {}

    for line in combined.splitlines():
        # Interface header: InterfaceType[DisplayName]
        iface_match = re.match(r'\s*(\w+)\[(.+?)\]', line)
        if iface_match:
            current_iface = (
                f"{iface_match.group(1)}"
                f"[{iface_match.group(2)}]"
            )
            continue

        # TX line: ↑NNN B  NNN bps
        tx_match = re.search(
            r'↑\s*([\d,.]+)\s*(\w+)', line
        )
        if tx_match and current_iface:
            tx_cache[current_iface] = (
                tx_match.group(1).replace(',', ''),
                tx_match.group(2),
            )

        # RX line: ↓NNN B  NNN bps
        rx_match = re.search(
            r'↓\s*([\d,.]+)\s*(\w+)', line
        )
        if rx_match and current_iface:
            rx_val = rx_match.group(1).replace(',', '')
            rx_unit = rx_match.group(2)
            tx_info = tx_cache.get(
                current_iface, ('0', 'B')
            )
            tx_str = f"{tx_info[0]} {tx_info[1]}"
            rx_str = f"{rx_val} {rx_unit}"

            try:
                tx_bytes = float(tx_info[0])
                rx_bytes = float(rx_val)
            except ValueError:
                tx_bytes = rx_bytes = 0

            # Healthy if TX > 0, or both are 0 (just started)
            healthy = not (rx_bytes > 0 and tx_bytes == 0)
            interfaces.append(
                (current_iface, tx_str, rx_str, healthy)
            )
            current_iface = None

    return interfaces


def diagnose_rns_port_conflict(handler):
    """Diagnose and offer to fix RNS port conflicts from the TUI.

    Args:
        handler: RNSDiagnosticsHandler instance for TUI interaction.
    """
    try:
        # Check LXMF apps — most common cause of port conflicts
        conflicting_app = handler._check_lxmf_app_conflict()
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
                    ['pkill', '-f', app_lower],
                    capture_output=True, timeout=5
                )
                time.sleep(1)

                print("Starting rnsd...")
                start_service('rnsd')
                time.sleep(2)

                print(f"Done. Startup order: rnsd -> {conflicting_app} -> MeshForge\n")
            return

        rnsd_running = check_process_running('rnsd')

        if rnsd_running:
            # Get PID for diagnostic message
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
