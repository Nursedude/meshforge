"""RNS diagnostic runner functions.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
Functions take handler for TUI interaction (same pattern as _rns_repair.py).
"""

import logging
import os
from dataclasses import dataclass, field
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
from utils.config_drift import detect_rnsd_config_drift

logger = logging.getLogger(__name__)


def summary_lines(issues, warnings, unobserved) -> list:
    """The diagnostics verdict. A leg that could not be checked is UNKNOWN —
    it must never be summarised as "passed" or "OK" (TUI audit finding 5)."""
    out = []
    if issues:
        out.append(f"\n--- Issues Found ({len(issues)}) ---")
        out += [f"  ! {i}" for i in issues]
    if warnings:
        out.append(f"\n--- Warnings ({len(warnings)}) ---")
        out += [f"  ~ {w}" for w in warnings]
    if unobserved:
        out.append(f"\n--- NOT CHECKED ({len(unobserved)}) — UNKNOWN, not passed ---")
        out += [f"  ? {u}" for u in unobserved]
    if not issues and not warnings and not unobserved:
        out.append("\n--- All checks passed ---")
    elif not issues and not unobserved:
        out.append("\n--- Connectivity OK (with warnings) ---")
    return out


def run_rns_diagnostics(handler):
    """Run comprehensive RNS diagnostics.

    Args:
        handler: RNSDiagnosticsHandler instance for TUI interaction.
    """
    # Deferred: commands.rns imports the RNS library at module level (~400 ms,
    # hundreds of modules) — the cost belongs to this menu, not TUI startup.
    from commands.rns import create_identities, check_connectivity, get_status

    clear_screen()
    print("=== RNS Diagnostics ===\n")

    # Collect issues and warnings throughout diagnostics
    issues = []
    warnings = []
    # Legs that could NOT be checked. Never folded into "passed": before
    # this (TUI audit finding 5) a blind rnstatus leg printed a note and the
    # summary still said "All checks passed".
    unobserved = []

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
        print(f"  {conflicting_app}: OWNS the RNS shared instance (should be rnsd)")
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
            # On Linux the shared instance is the abstract socket
            # @rns/<instance>, not UDP 37428 (that is the TCP/UDP-mode port).
            print(f"  WARNING: {conflicting_app} is holding the RNS "
                  "shared instance.")
            print("  rnsd cannot take @rns/"
                  f"{ReticulumPaths.get_configured_instance_name()} "
                  f"while {conflicting_app} is running.")
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
            print("  WARNING: rnsd has crashed.")
            print("    View rnsd logs in-app (RNS > Diagnostics).")

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
    from commands.rns import identity_exposure, rnsd_identity_path
    rns_identity = rnsd_identity_path(config_dir)
    print(f"  rnsd identity: {'found' if rns_identity.exists() else 'not yet created by rnsd'}"
          f" ({rns_identity})")
    exposure = identity_exposure(rns_identity)
    if exposure:
        print(f"  ⚠ {exposure}")
        warnings.append(f"rnsd identity {exposure}")

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
                    print("         then restart rnsd in-app: "
                          "RNS > Diagnostics > Repair")
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
                    from ._service_ops_common import journal_tail_text
                    text = journal_tail_text('rnsd', lines=10, quiet=True,
                                             no_hostname=True, empty_text="")
                    if text:
                        print("    Recent rnsd log:")
                        for line in text.splitlines()[-5:]:
                            print(f"      {line.strip()[:100]}")
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
        from utils.rns_status_parser import _find_rnstatus_binary, read_interface_traffic
        # The reader's own lookup (PATH + ~/.local/bin), not shutil.which —
        # two lookups disagreed on a pip --user install (Fable r3 nit).
        leg = interface_traffic_leg(
            read_interface_traffic(), running=running, instance_ok=instance_ok,
            rnstatus_present=_find_rnstatus_binary() is not None)
        apply_traffic_leg(leg, issues, warnings, unobserved)
    except Exception as e:
        logger.debug("Interface health check failed: %s", e)
        print(f"  Could not check: {e}")
        unobserved.append(f"interface traffic ({type(e).__name__})")

    # Summary
    for line in summary_lines(issues, warnings, unobserved):
        print(line)

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
            handler._rns_repair_menu()
            handler.ctx.wait_for_enter()
            return

    # Offer to create the GATEWAY identity only — rnsd creates its own
    # transport identity on first start (MeshForge used to "create" one at a
    # path RNS never reads, 2026-09-25).
    if not identity_exists:
        print("\n--- Identity Setup ---")
        if handler.ctx.dialog.yesno(
            "Create Gateway Identity",
            "The MeshForge gateway identity is missing.\n\n"
            "Create it now? (rnsd's own identity is created by rnsd itself.)"
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
        print("Cause: RPC authentication mismatch (stale auth tokens).")
        print("The guided RNS Repair clears the stale tokens and restarts rnsd.\n")
        if handler.ctx.dialog.yesno(
            "Repair RNS now?",
            "Stale RPC auth tokens detected.\n\n"
            "Run the guided RNS Repair in-app? It stops rnsd, clears the\n"
            "stale shared-instance auth files, and restarts rnsd — no shell\n"
            "needed.",
        ):
            from ._rns_repair import repair_rns_shared_instance
            repair_rns_shared_instance(handler)
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
                ['pkill', '-x', 'rnsd'],
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
        print("Fix this in-app:")
        print("  - Address each blocking interface's dependency (Fix above).")
        print("  - Or disable a blocking interface in RNS > Manage Interfaces.")
        print("  - Then restart rnsd from Service Control.")
        return

    # No specific cause detected — show actual rnsd log
    print("Showing recent rnsd log:\n")
    from ._service_ops_common import journal_tail_text
    for line in journal_tail_text('rnsd', lines=15,
                                  empty_text="(no log output)").splitlines():
        print(f"  {line}")
    print("\nRestart rnsd from Service Control to clear this.")


SHARED_INSTANCE = "Shared Instance"


def _leg_interfaces(status):
    """Interfaces leg [6/6] is ABOUT, in listed order — Shared Instance
    INCLUDED (Fable r3: its filtered rx is a silent drop like any other).

    No filter here: the PARSER alone decides what an interface is (a header
    whose block holds a Status line). The old `if i.type_name` filter, kept
    for the retired header regex's bogus usage-text matches, dropped a real
    fully-read "[nested][High Datarate]" and the leg said "All checks
    passed" (Fable r6 IMPORTANT — the same class one layer up).
    """
    return list(status.interfaces)


def health_rows(traffic):
    """:func:`leg_rows` minus Shared Instance — the RX-only health contract.

    Shared Instance rx-without-tx is not an RX-only link fault (r1 #5) and
    the pre-2026-11-21 reader never listed it (4-tuple callers).
    """
    return [(i, row) for i, row in leg_rows(traffic)
            if i.type_name != SHARED_INSTANCE]


def leg_rows(traffic):
    """[(RNSInterface, (name, tx_str, rx_str, is_healthy))] for EVERY leg
    interface whose two counters were READ, in listed order.

    Order, not a name lookup: two interfaces may share a full_name (`-a`
    lists LocalInterface three times) and must not collapse (Fable r2 #5).
    An unread counter is never a 0 B reading (r1 #2, r2 #6); what is left
    out is reported by :func:`blind_spots`, never silently dropped (r2 #1).
    """
    from utils.rns_status_parser import format_bytes
    return [
        (i, (i.full_name, format_bytes(i.tx), format_bytes(i.rx), i.is_healthy))
        for i in _trusted_interfaces(traffic.status)
        if i.rx.observed and i.tx.observed
    ]


def _trusted_interfaces(status):
    """Leg interfaces from a listing that can be trusted at all: none when
    the run timed out or printed an rnstatus error line (no shared instance
    / could not connect). A bare non-zero exit alone keeps what was listed
    — :func:`accepted_blind_reason` names it a partial listing. No re-parse
    (Fable r4 nit 5: each re-parse re-logged the whole dump)."""
    from utils.rns_status_parser import error_line
    if status.timed_out or error_line(status):
        return []
    return _leg_interfaces(status)


def shown_interfaces(traffic):
    """Every trusted interface with ANY reading — an accepted counter or a
    Filtered ↓ (Fable r4 nit 1: a filtered 5.10 KB on an interface whose
    Traffic was unread was observed and dropped). An unread counter prints
    UNKNOWN on its row, and is a blind spot, never 0 B."""
    return [i for i in _trusted_interfaces(traffic.status)
            if i.tx.observed or i.rx.observed or i.rx_filtered is not None]


def check_rns_interface_health(traffic=None):
    """Per-interface TX/RX counters from the ONE shared rnstatus reader.

    Returns a list of (interface_name, tx_str, rx_str, is_healthy) tuples
    (contract of the pre-2026-11-21 hand-rolled reader, 4 callers): empty
    when rnstatus timed out or cannot reach the shared instance — callers
    treat that as unobserved, never healthy. See :func:`health_rows`.
    """
    return interface_health(traffic)[0]


def interface_health(traffic=None, *, accepted_only=False):
    """(rows, why_blind) — :func:`check_rns_interface_health`'s rows AND why
    the reading is not complete (None = every listed interface read).

    An empty list alone cannot tell "nothing there" from "could not see"
    (r5 nit: the NomadNet subtitle said DOWN on a timed-out rnstatus).
    ``accepted_only`` = one plain rnstatus run, no -F: for callers that never
    show filtered rx (a menu subtitle ran two rnstatus per render on 1.3.9).
    """
    from utils.rns_status_parser import (
        FilterState, InterfaceTraffic, _find_rnstatus_binary, read_interface_traffic,
        run_rnstatus,
    )
    if traffic is None:
        traffic = (InterfaceTraffic(run_rnstatus(), FilterState.UNKNOWN, "not read")
                   if accepted_only else read_interface_traffic())
    st = traffic.status
    # A reading that RAN (printed, timed out, or was timed) proves the binary.
    ran = bool(st.raw_output) or st.timed_out or st.duration_s is not None
    why = accepted_blind_reason(
        traffic, running=False, instance_ok=False,
        rnstatus_present=ran or _find_rnstatus_binary() is not None)
    return [row for _, row in health_rows(traffic)], why


def accepted_blind_reason(traffic, *, running, instance_ok, rnstatus_present):
    """Why accepted traffic was (partly) not seen — None when every listed
    interface was read. Causes in order, most specific first (Fable r3: a
    timeout read "cannot connect", an error text "returned nothing"):
    not installed > the READER's gap for its view (timed out / error line /
    partial listing / failed run — Fable r4: judged once, for -F and the
    plain re-read alike) > nothing listed: the TUI probe's "cannot connect"
    / "listed no interfaces" > unread interfaces.
    """
    status = traffic.status
    if not rnstatus_present:
        return "rnstatus not installed"
    gap = traffic.accepted_gap
    if gap:
        return gap
    unread = [i.full_name for i in _trusted_interfaces(status)
              if not (i.tx.observed and i.rx.observed)]
    if not shown_interfaces(traffic) and not unread:
        if running and not instance_ok:
            return "rnstatus cannot connect"
        return "rnstatus listed no interfaces"
    if unread:
        return (f"{len(unread)} interface(s) listed without readable counters: "
                f"{', '.join(unread)}")
    return None


def blind_spots(traffic, *, running, instance_ok, rnstatus_present):
    """EVERYTHING leg [6/6] could not see — the ONE producer of its
    unobserved entries (Fable r2: blindness kept escaping through whichever
    branch printed; one function now owns it, pinned by a state table).

      * accepted traffic: no rows at all (why: not installed / cannot
        connect / timed out / nothing listed), or listed interfaces whose
        counters were not read;
      * filtered rx: the -F reading was UNKNOWN, or COUNTED but a shown
        interface printed no Filtered line.
    NO_FILTER is NOT blindness: the CLI said it has no -F (an explained
    absence, shown on every row).
    """
    from utils.rns_status_parser import FilterState
    spots = []
    reason = accepted_blind_reason(traffic, running=running, instance_ok=instance_ok,
                                   rnstatus_present=rnstatus_present)
    if reason:
        spots.append(f"interface traffic ({reason})")
    if traffic.filter_state is FilterState.UNKNOWN:
        spots.append(f"filtered rx ({traffic.filter_reason})")
    elif traffic.filter_state is FilterState.COUNTED:
        missing = [i.full_name for i in shown_interfaces(traffic) if i.rx_filtered is None]
        if missing:
            spots.append(f"filtered rx ({len(missing)} interface(s) printed "
                         f"no Filtered line: {', '.join(missing)})")
    return spots


@dataclass
class TrafficLeg:
    """What diagnostics leg [6/6] says: lines to print + summary entries."""
    lines: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unobserved: list = field(default_factory=list)


def interface_traffic_leg(traffic, *, running, instance_ok, rnstatus_present):
    """Leg [6/6]: per-interface TX/RX + rx accepted vs FILTERED. Pure.

    Filtered rx is the only witness of a silent drop (rns >= 1.6 drops
    unstamped announces under enforce_stamps with no log line). unobserved
    comes ONLY from :func:`blind_spots`; filtered > 0 is a WARNING, never an
    issue (observe before alarm).
    """
    from utils.rns_status_parser import FilterState, filtered_rx_verdict, format_bytes
    leg = TrafficLeg()
    rows = shown_interfaces(traffic)
    dropping = 0
    rx_only_found = False
    for iface in rows:
        name = iface.full_name
        tx = format_bytes(iface.tx) if iface.tx.observed else "UNKNOWN"
        rx = format_bytes(iface.rx) if iface.rx.observed else "UNKNOWN"
        # RX-only needs BOTH counters read; Shared Instance rx-without-tx is
        # not an RX-only link fault (r1 #5).
        rx_only = (iface.tx.observed and iface.rx.observed and not iface.is_healthy
                   and iface.type_name != SHARED_INSTANCE)
        if not rx_only:
            leg.lines.append(f"  {name}: ↑{tx} ↓{rx}")
        else:
            leg.lines.append(f"  {name}: RX-ONLY (↑{tx} ↓{rx})")
            leg.issues.append(f"Interface {name} is RX-only (no TX)")
            rx_only_found = True
        state, text = filtered_rx_verdict(iface, traffic.filter_state)
        leg.lines.append(f"      {text}")
        if state is FilterState.COUNTED and iface.rx_filtered.bytes_total > 0:
            dropping += 1
    if rx_only_found:
        leg.lines.append("\n  RX-only interfaces = link establishment failing.")
        leg.lines.append("  Common cause: shared instance port 37428 not bound.")
    leg.unobserved = blind_spots(traffic, running=running, instance_ok=instance_ok,
                                 rnstatus_present=rnstatus_present)
    if not rows:
        reason = accepted_blind_reason(traffic, running=running, instance_ok=instance_ok,
                                       rnstatus_present=rnstatus_present)
        leg.lines.append(f"  Could not read: {reason}")
        if not rnstatus_present:
            leg.lines.append("  Reinstall MeshForge to restore rnstatus (Settings > Updates).")
    if dropping:
        leg.warnings.append(
            f"{dropping} interface(s) filtered inbound traffic — dropped "
            "by this RNS without a log line (see [6/6])")
    return leg


def apply_traffic_leg(leg, issues, warnings, unobserved, emit=print):
    """Print leg [6/6] and fold ALL of its entries into the summary lists."""
    for line in leg.lines:
        emit(line)
    issues.extend(leg.issues)
    warnings.extend(leg.warnings)
    unobserved.extend(leg.unobserved)


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
                start_ok, start_msg = start_service('rnsd')
                if not start_ok:
                    print(f"FAILED to start rnsd: {start_msg}")
                    print("Re-run Diagnose RNS once the port is free.")
                    return

                # "Done" only after rnsd actually claims the shared instance —
                # a started-but-not-listening rnsd has NOT resolved the conflict
                # (honest-signal #74-#77: verify the port, don't assume).
                if handler._wait_for_rns_shared_instance(max_wait=10):
                    print(f"Done. Startup order: rnsd -> {conflicting_app} -> MeshForge\n")
                else:
                    print("rnsd started but its shared instance is NOT available "
                          "after 10s.")
                    print(f"The port may still be held — re-run Diagnose RNS or "
                          f"verify {conflicting_app} fully stopped.")
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
            print(f"rnsd is running (PID: {pid}) but may need a restart.")
            print("  Restart rnsd from Service Control.")
        else:
            print("No rnsd found. A stale process may be holding the port.")
            print("  Restart rnsd from Service Control to clear it,")
            print("  or wait ~30s for the socket to time out.")
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("RNS port conflict diagnosis failed: %s", e)
        print("  Restart rnsd from Service Control.")
