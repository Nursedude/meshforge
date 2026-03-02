"""
RNS Diagnostics Handler — RNS health checks, tool execution, and port diagnostics.

Repair and interface management are in:
- _rns_repair.py (repair wizard, service file validation)
- _rns_interface_mgr.py (blocking detection, interface disabling)
- _rns_diag_tools.py (CLI tool execution, connectivity diagnosis, conflict checks)
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen
from utils.service_check import (
    get_rns_shared_instance_info,
    _sudo_cmd, get_udp_port_owner,
)
from commands.rns import (
    create_identities, check_connectivity, get_status,
)
from utils.config_drift import detect_rnsd_config_drift

# Error patterns indicating RNS shared instance connectivity failure.
# Used in _run_rns_tool() for both initial detection and retry validation.
_RNS_SHARED_ERRORS = (
    "no shared", "could not connect", "could not get",
    "shared instance", "authenticationerror", "digest",
)


class RNSDiagnosticsHandler(BaseHandler):
    """TUI handler for RNS diagnostics, repair, and tool execution."""

    handler_id = "rns_diagnostics"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("diag", "RNS Diagnostics", None),
            ("repair", "Repair RNS", None),
            ("drift", "Config Drift Check", None),
        ]

    def execute(self, action):
        dispatch = {
            "diag": self._rns_diagnostics,
            "repair": self._rns_repair_menu,
            "drift": self._rns_config_drift_check,
        }
        method = dispatch.get(action)
        if method:
            method()

    # ------------------------------------------------------------------
    # Diagnostics methods (from rns_diagnostics_mixin.py)
    # ------------------------------------------------------------------

    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics."""
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
        conflicting_app = self._check_lxmf_app_conflict()
        if conflicting_app:
            print(f"  {conflicting_app}: RUNNING (port conflict!)")
            # Show port 37428 owner for clarity
            try:
                from utils.service_check import get_udp_port_owner
                owner = get_udp_port_owner(37428)
                if owner:
                    proc_name, pid = owner
                    print(f"  Port 37428 owner: {proc_name} (PID {pid})")
            except ImportError:
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
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/6] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
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
            blocking = self._find_blocking_interfaces()
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
        try:
            si_info = get_rns_shared_instance_info()
            instance_ok = si_info['available']
            if running and not instance_ok:
                # rnsd may still be initializing — wait before declaring failure
                print("  rnsd running but shared instance not yet available...")
                print("  Waiting for rnsd to finish initializing...")
                instance_ok = self._wait_for_rns_shared_instance(max_wait=10)
                if instance_ok:
                    si_info = get_rns_shared_instance_info()
                    print(f"  Shared instance: available (slow startup)")
                    print(f"    Method: {si_info['detail']}")
                else:
                    print("  ! rnsd running but shared instance NOT "
                          "available after 10s wait")
                    print(f"    {si_info['detail']}")
                    # Show who owns port 37428 (if anyone, for TCP/UDP mode)
                    try:
                        from utils.service_check import get_udp_port_owner
                        owner = get_udp_port_owner(37428)
                        if owner:
                            proc_name, pid = owner
                            print(f"    Port 37428 held by: {proc_name} "
                                  f"(PID {pid})")
                    except ImportError:
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
            iface_health = self._check_rns_interface_health()
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
            if self.ctx.dialog.yesno(
                "Repair RNS",
                "RNS shared instance is not available.\n\n"
                "Run the RNS repair wizard now?\n"
                "This will validate config, check dependencies,\n"
                "and restart rnsd.\n\n"
                "Repair now?"
            ):
                clear_screen()
                self._repair_rns_shared_instance()
                self.ctx.wait_for_enter()
                return

        # Offer to create missing identities
        if not identity_exists or not rns_identity.exists():
            print("\n--- Identity Setup ---")
            if self.ctx.dialog.yesno(
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

        self.ctx.wait_for_enter()

    def _rns_config_drift_check(self):
        """Check for config drift between gateway and rnsd."""
        clear_screen()
        print("=== RNS Config Drift Check ===\n")
        print("Comparing gateway config path vs rnsd actual path...\n")

        result = detect_rnsd_config_drift()

        # Display result
        severity_colors = {
            'info': '\033[0;34m',     # blue
            'warning': '\033[0;33m',  # yellow
            'error': '\033[0;31m',    # red
        }
        color = severity_colors.get(result.severity, '')
        reset = '\033[0m'

        if result.drifted:
            print(f"  {color}CONFIG DRIFT DETECTED{reset}\n")
            print(f"  Gateway resolves to: {result.gateway_config_dir}")
            print(f"  rnsd actually uses:   {result.rnsd_config_dir}")
            print(f"  Detection method:     {result.detection_method}")
            if result.rnsd_pid:
                print(f"  rnsd PID:             {result.rnsd_pid}")
            print(f"\n  {color}Fix:{reset} {result.fix_hint}")

            # Offer to fix now if possible
            if result.can_auto_fix:
                print()
                self._offer_drift_fix(result)
            else:
                print()
                self.ctx.wait_for_enter()
        else:
            print(f"  \033[0;32mNo drift detected\033[0m\n")
            print(f"  {result.message}")
            if result.gateway_config_dir:
                print(f"  Config directory: {result.gateway_config_dir}")
            if result.rnsd_pid:
                print(f"  rnsd PID: {result.rnsd_pid}")
            print(f"  Detection method: {result.detection_method}")

            print()
            self.ctx.wait_for_enter()

    def _offer_drift_fix(self, drift_result):
        """Offer to fix config drift — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import offer_drift_fix
        offer_drift_fix(self, drift_result)

    # Known RNS external interface plugins and their pip package dependencies.
    # Key: plugin filename in /etc/reticulum/interfaces/
    # Value: list of (import_name, pip_package) tuples
    _INTERFACE_DEPS = {
        'Meshtastic_Interface.py': [('meshtastic', 'meshtastic')],
    }

    def _ensure_rnsd_dependencies(self):
        """Check that rnsd's Python can import packages required by enabled interfaces.

        Scans /etc/reticulum/interfaces/ for known plugin files, checks if their
        Python dependencies are importable by rnsd's interpreter, and offers to
        install missing packages system-wide via pip.

        Common failure: meshtastic installed via pipx (isolated venv, CLI only)
        but Meshtastic_Interface.py needs it importable by system Python.
        """
        interfaces_dir = Path('/etc/reticulum/interfaces')
        if not interfaces_dir.is_dir():
            print("  No external interfaces directory")
            return

        # Determine rnsd's Python interpreter from its shebang.
        # Check multiple locations in priority order:
        # 1. ExecStart from the service file (the actual binary systemd uses)
        # 2. Venv rnsd (has all dependencies)
        # 3. System rnsd (PATH or /usr/local/bin)
        rnsd_path = None

        # Try ExecStart from the service file first — most accurate
        service_file = Path('/etc/systemd/system/rnsd.service')
        if service_file.exists():
            try:
                svc_content = service_file.read_text()
                exec_match = re.search(r'ExecStart\s*=\s*(.+)', svc_content)
                if exec_match:
                    # Extract just the binary path, stripping args like --service
                    candidate = Path(exec_match.group(1).strip().split()[0])
                    if candidate.exists():
                        rnsd_path = candidate
            except (OSError, PermissionError):
                pass

        # Fallback: venv path
        if rnsd_path is None:
            venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')
            if venv_rnsd.exists():
                rnsd_path = venv_rnsd

        # Fallback: system path
        if rnsd_path is None:
            sys_rnsd = Path('/usr/local/bin/rnsd')
            if sys_rnsd.exists():
                rnsd_path = sys_rnsd
            else:
                rnsd_which = shutil.which('rnsd')
                if rnsd_which:
                    rnsd_path = Path(rnsd_which)
                else:
                    print("  rnsd not found — skipping dependency check")
                    return

        # Read shebang to find which Python rnsd uses
        try:
            first_line = rnsd_path.read_text().split('\n', 1)[0]
            if first_line.startswith('#!'):
                rnsd_python = first_line[2:].strip().split()[0]
            else:
                rnsd_python = 'python3'
        except (OSError, PermissionError):
            rnsd_python = 'python3'

        # Check each known plugin
        missing = []
        for plugin_file, deps in self._INTERFACE_DEPS.items():
            if not (interfaces_dir / plugin_file).exists():
                continue
            for import_name, pip_name in deps:
                try:
                    result = subprocess.run(
                        [rnsd_python, '-c', f'import {import_name}'],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0:
                        missing.append((plugin_file, import_name, pip_name))
                        print(f"  {plugin_file} needs '{import_name}' — NOT installed")
                    else:
                        print(f"  {plugin_file} needs '{import_name}' — OK")
                except (subprocess.SubprocessError, OSError):
                    missing.append((plugin_file, import_name, pip_name))
                    print(f"  {plugin_file} needs '{import_name}' — check failed")

        if not missing:
            print("  All interface dependencies met")
            return

        # Offer to install missing packages
        pkg_list = ', '.join(pip_name for _, _, pip_name in missing)
        if self.ctx.dialog.yesno(
            "Install Missing Packages",
            f"rnsd's Python ({rnsd_python}) is missing packages\n"
            f"required by external interface plugins:\n\n"
            + '\n'.join(
                f"  {plugin}: {imp} (pip: {pip})"
                for plugin, imp, pip in missing
            )
            + f"\n\nInstall system-wide with:\n"
            f"  sudo {rnsd_python} -m pip install {pkg_list}\n\n"
            f"Without these, rnsd will crash on startup.\n\n"
            f"Install now?"
        ):
            for _, _, pip_name in missing:
                print(f"  Installing {pip_name}...")
                try:
                    install_cmd = [rnsd_python, '-m', 'pip', 'install',
                                    '--break-system-packages', pip_name,
                                    'cryptography>=45.0.7,<47', 'pyopenssl>=25.3.0']
                    base_cmd = _sudo_cmd(install_cmd)
                    result = subprocess.run(
                        base_cmd,
                        capture_output=True, text=True, timeout=120
                    )
                    if result.returncode == 0:
                        print(f"  {pip_name}: installed")
                    else:
                        # Detect Debian-managed package conflict:
                        # pip says "installed by debian/apt" when it refuses
                        # to overwrite an apt-owned package.
                        err_text = (result.stderr or result.stdout or '').lower()
                        if 'installed by' in err_text or 'externally-managed' in err_text:
                            print(f"  {pip_name}: Debian package conflict, retrying with --ignore-installed...")
                            retry_cmd = _sudo_cmd([rnsd_python, '-m', 'pip', 'install',
                                         '--break-system-packages', '--ignore-installed', pip_name,
                                         'cryptography>=45.0.7,<47', 'pyopenssl>=25.3.0'])
                            retry = subprocess.run(
                                retry_cmd,
                                capture_output=True, text=True, timeout=120
                            )
                            if retry.returncode == 0:
                                print(f"  {pip_name}: installed (bypassed Debian package)")
                            else:
                                err_lines = (retry.stderr or retry.stdout or '').strip().split('\n')
                                print(f"  {pip_name}: FAILED (even with --ignore-installed)")
                                if err_lines:
                                    print(f"    {err_lines[-1]}")
                        else:
                            err_lines = (result.stderr or result.stdout or '').strip().split('\n')
                            print(f"  {pip_name}: FAILED")
                            if err_lines:
                                print(f"    {err_lines[-1]}")
                except subprocess.TimeoutExpired:
                    print(f"  {pip_name}: timed out (network issue?)")
                except (subprocess.SubprocessError, OSError) as e:
                    print(f"  {pip_name}: error — {e}")
        else:
            print(f"  Skipped. Install manually: sudo {rnsd_python} -m pip install {pkg_list}")
            print(f"  Without these packages, rnsd will crash on startup.")

    # ------------------------------------------------------------------
    # Thin wrappers — delegate to _rns_diag_tools module
    # (extracted for file size compliance, CLAUDE.md #6)
    # ------------------------------------------------------------------

    def _run_rns_tool(self, cmd: list, tool_name: str):
        """Run an RNS CLI tool — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import run_rns_tool
        run_rns_tool(self, cmd, tool_name)

    def _wait_for_rns_shared_instance(self, max_wait: int = 10) -> bool:
        """Wait for rnsd shared instance — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import wait_for_rns_shared_instance
        return wait_for_rns_shared_instance(max_wait)

    # Keep old name as alias for any callers during transition
    _wait_for_rns_port = _wait_for_rns_shared_instance

    def _get_rnsd_user(self) -> Optional[str]:
        """Get the OS user running rnsd — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import get_rnsd_user
        return get_rnsd_user()

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Fix rnsd user mismatch — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import fix_rnsd_user
        return fix_rnsd_user(self, target_user)

    def _diagnose_rns_connectivity(self, error_output: str):
        """Diagnose RNS connectivity — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import diagnose_rns_connectivity
        diagnose_rns_connectivity(self, error_output)

    def _check_nomadnet_conflict(self) -> bool:
        """Check NomadNet port conflict — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import check_nomadnet_conflict
        return check_nomadnet_conflict()

    def _check_lxmf_app_conflict(self) -> Optional[str]:
        """Check LXMF app port conflict — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import check_lxmf_app_conflict
        return check_lxmf_app_conflict()

    def _check_rns_interface_health(self):
        """Check interface TX/RX health — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import check_rns_interface_health
        return check_rns_interface_health()

    def _diagnose_rns_port_conflict(self):
        """Diagnose RNS port conflict — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import diagnose_rns_port_conflict
        diagnose_rns_port_conflict(self)

    def _get_config_handler(self):
        """Get the RNS config handler — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import get_config_handler
        return get_config_handler(self)

    def _check_meshchat_installed(self) -> bool:
        """Check if MeshChat is installed — delegates to _rns_diag_tools."""
        from ._rns_diag_tools import check_meshchat_installed
        return check_meshchat_installed()

    # ------------------------------------------------------------------
    # Repair methods (from rns_menu_mixin.py)
    # ------------------------------------------------------------------

    def _rns_repair_menu(self):
        """RNS Repair Wizard — delegates to _rns_repair module."""
        if not self.ctx.dialog.yesno(
            "RNS Repair Wizard",
            "This will attempt to fix RNS shared instance issues.\n\n"
            "What it does:\n"
            "  1. Ensures /etc/reticulum/ dirs exist & deploys config if missing\n"
            "  2. Validates rnsd.service file (fixes ExecStart & directives)\n"
            "  3. Checks rnsd Python dependencies (meshtastic, etc.)\n"
            "  4. Clears stale auth tokens & restarts rnsd\n"
            "  5. Verifies port 37428 is listening\n\n"
            "Your existing RNS config will NOT be overwritten.\n\n"
            "Run diagnostics first? Use RNS > Diagnostics.\n\n"
            "Proceed with repair?",
        ):
            return

        clear_screen()
        from ._rns_repair import repair_rns_shared_instance
        repair_rns_shared_instance(self)
        self.ctx.wait_for_enter()

    def _validate_rnsd_service_file(self) -> bool:
        """Validate and fix rnsd.service — delegates to _rns_repair module."""
        from ._rns_repair import validate_rnsd_service_file
        return validate_rnsd_service_file()

    def _find_blocking_interfaces(self) -> list:
        """Check for blocking RNS interfaces — delegates to _rns_interface_mgr."""
        from ._rns_interface_mgr import find_blocking_interfaces
        return find_blocking_interfaces()

    def _disable_interfaces_in_config(self, interface_names: list) -> list:
        """Disable interfaces in config — delegates to _rns_interface_mgr."""
        from ._rns_interface_mgr import disable_interfaces_in_config
        return disable_interfaces_in_config(interface_names)

