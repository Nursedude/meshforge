"""
RNS Diagnostics Handler — RNS health checks, tool execution, and port diagnostics.

Repair and interface management are in:
- _rns_repair.py (repair wizard, service file validation)
- _rns_interface_mgr.py (blocking detection, interface disabling)
- _rns_diagnostics_engine.py (diagnostic runners, interface health, port conflict)
"""

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen
from utils.service_check import (
    apply_config_and_restart, check_process_running, check_service,
    check_udp_port,
    check_rns_shared_instance, get_rns_shared_instance_info,
    start_service, stop_service, _sudo_cmd, _sudo_write,
    daemon_reload, enable_service, get_udp_port_owner,
)
from commands.rns import (
    get_identity_path, create_identities, list_known_destinations,
    check_connectivity, get_status,
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

    # Known RNS external interface plugins and their pip package dependencies.
    _INTERFACE_DEPS = {
        'Meshtastic_Interface.py': [('meshtastic', 'meshtastic')],
    }

    # ------------------------------------------------------------------
    # Diagnostics methods (from rns_diagnostics_mixin.py)
    # ------------------------------------------------------------------


    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics — delegates to engine module."""
        from ._rns_diagnostics_engine import run_rns_diagnostics
        run_rns_diagnostics(self)

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
        """Offer to fix config drift by migrating to /etc/reticulum/.

        Orchestrates existing primitives:
        1. Migrate config to /etc/reticulum/config (via _migrate_rns_config_to_etc)
        2. Ensure /etc/reticulum/storage/ dirs exist (via ReticulumPaths)
        3. Clear stale auth tokens from all locations
        4. Validate rnsd.service (ExecStart path, systemd directives)
        5. Verify rnsd Python dependencies (install missing packages)
        6. Restart rnsd
        7. Wait for port 37428 and verify drift is resolved
        """
        etc_config = Path('/etc/reticulum/config')

        # Determine the source config to migrate — prefer rnsd's (actively in use)
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
            self.ctx.wait_for_enter()
            return

        # Build the dialog message
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
                f"  4. Restart rnsd\n\n"
                f"Fix now?"
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
                f"  4. Restart rnsd\n\n"
                f"Fix now?"
            )

        if not self.ctx.dialog.yesno("Fix Config Drift", dialog_text):
            self.ctx.wait_for_enter()
            return

        # === Execute the fix ===
        print("\n--- Fixing Config Drift ---\n")

        # Step 1: Migrate config to /etc/reticulum/config
        print("[1/7] Migrating config to /etc/reticulum/...")
        if etc_config.exists():
            print(f"  /etc/reticulum/config already exists — keeping it")
            # Rename competing configs to .migrated so RNS resolution
            # finds /etc/reticulum/config first
            for candidate in source_candidates:
                if candidate.is_file() and not str(candidate).startswith('/etc/'):
                    try:
                        backup = candidate.with_suffix('.migrated')
                        candidate.rename(backup)
                        print(f"  Renamed: {candidate} -> {backup.name}")
                    except (OSError, PermissionError) as e:
                        print(f"  Warning: Could not rename {candidate}: {e}")
        else:
            if self._get_config_handler()._migrate_rns_config_to_etc(source):
                print(f"  Migrated: {source} -> /etc/reticulum/config")
            else:
                print("  Migration failed. Aborting.")
                self.ctx.wait_for_enter()
                return

        # Step 2: Ensure system directories exist with correct permissions
        print("\n[2/7] Ensuring /etc/reticulum/ directory structure...")
        if ReticulumPaths.ensure_system_dirs():
            print("  Directories OK (storage, ratchets, cache, interfaces)")
        else:
            print("  Warning: Could not create all directories (need sudo?)")

        # Step 3: Clear stale auth tokens from all locations
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

        # Step 4: Validate rnsd.service file (ExecStart path, directives)
        print("\n[4/7] Validating rnsd.service...")
        service_fixed = self._validate_rnsd_service_file()
        if not service_fixed:
            print("  Service file: OK")

        # Step 5: Verify rnsd Python dependencies
        print("\n[5/7] Checking rnsd Python dependencies...")
        self._ensure_rnsd_dependencies()

        # Step 6: Restart rnsd
        print("\n[6/7] Restarting rnsd...")
        success, msg = stop_service('rnsd')
        if not success:
            print(f"  Warning stopping rnsd: {msg}")
        time.sleep(1)

        # Reset failed state in case rnsd was in a crash loop
        try:
            subprocess.run(
                _sudo_cmd(['systemctl', 'reset-failed', 'rnsd']),
                capture_output=True, timeout=5
            )
        except (subprocess.SubprocessError, OSError):
            pass

        success, msg = start_service('rnsd')
        if success:
            print("  rnsd started")
        else:
            print(f"  Warning: {msg}")

        # Step 7: Wait for shared instance and verify
        print("\n[7/7] Verifying fix...")
        print("  Waiting for shared instance...")
        instance_ready = self._wait_for_rns_shared_instance(max_wait=15)

        if instance_ready:
            si_info = get_rns_shared_instance_info(
                ReticulumPaths.get_configured_instance_name()
            )
            print(f"  Shared instance: available ({si_info['detail']})")

            # Re-run drift detection to confirm fix
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
        self.ctx.wait_for_enter()

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

    def _run_rns_tool(self, cmd: list, tool_name: str):
        """Run an RNS CLI tool with address-in-use error detection.

        Captures both stdout and stderr to detect specific error patterns.
        RNS logs errors to stdout in some configurations, so both streams
        must be checked for the 'Address already in use' pattern.

        Args:
            cmd: Command and arguments to run
            tool_name: Display name for error messages (e.g., "rnpath")
        """
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            # RNS tools may log errors to stdout or stderr depending on config
            combined = (result.stdout or "") + (result.stderr or "")

            # Check for error patterns BEFORE returncode — rnstatus returns
            # exit code 0 even when the shared instance is unreachable.
            lower_combined = combined.lower()
            has_shared_error = any(p in lower_combined for p in _RNS_SHARED_ERRORS)

            if "address already in use" in lower_combined:
                # Suppress noisy traceback, show actionable diagnostics
                print("\nError: RNS port conflict (Address already in use)")
                print("Another process is bound to the RNS AutoInterface port.\n")
                self._diagnose_rns_port_conflict()
            elif has_shared_error:
                # RNS shared instance issue — DIAGNOSE, don't auto-fix.
                # Auto-fix was the #1 source of regressions (see persistent_issues.md).
                # Policy: show what's wrong, let the user decide what to do.
                # Don't dump raw stdout — the diagnostic path below shows actionable info.
                print(f"\nRNS connectivity issue detected.")

                # Check if rnsd is actually running
                rnsd_running = False
                try:
                    # MF008: service state via check_service(), not raw systemctl.
                    rnsd_running = check_service('rnsd').available
                except (subprocess.SubprocessError, OSError):
                    pass

                if not rnsd_running:
                    # rnsd is NOT running — offer to start it (with user consent)
                    print("rnsd is not running.\n")
                    if self.ctx.dialog.yesno(
                        "rnsd Not Running",
                        f"{tool_name} failed because rnsd is not running.\n\n"
                        "Start rnsd now?\n\n"
                        "If rnsd won't start, use RNS > Diagnostics to investigate.",
                    ):
                        try:
                            start_service('rnsd')
                            print("Starting rnsd...")
                            if self._wait_for_rns_shared_instance(max_wait=10):
                                print(f"rnsd started. Retrying {tool_name}...\n")
                                # rnsd creates the socket before fully
                                # initializing. Retry with stabilization
                                # delays to handle the startup race.
                                retry_combined = ""
                                for attempt in range(3):
                                    if attempt > 0:
                                        threading.Event().wait(2)  # MF010: bounded retry, no daemon
                                    retry_result = subprocess.run(
                                        cmd, capture_output=True,
                                        text=True, timeout=15,
                                    )
                                    retry_combined = (
                                        (retry_result.stdout or "")
                                        + (retry_result.stderr or "")
                                    )
                                    retry_has_error = any(
                                        p in retry_combined.lower()
                                        for p in _RNS_SHARED_ERRORS
                                    )
                                    if (retry_result.returncode == 0
                                            and retry_result.stdout
                                            and not retry_has_error):
                                        print(retry_result.stdout, end='')
                                        break
                                else:
                                    self._diagnose_rns_connectivity(
                                        retry_combined
                                    )
                            else:
                                print("rnsd started but shared instance not available.")
                                print("Check: sudo journalctl -u rnsd -n 20")
                                print("Or run: RNS > Diagnostics from the menu.")
                        except (subprocess.SubprocessError, OSError) as e:
                            print(f"Failed to start rnsd: {e}")
                    else:
                        print("To start rnsd manually: sudo systemctl start rnsd")
                else:
                    # rnsd IS running but tools can't connect.
                    # Most common cause: rnsd still initializing (crypto, interfaces).
                    # Wait for shared instance before showing diagnostics.
                    print("rnsd is running — waiting for shared instance...")
                    port_ready = self._wait_for_rns_shared_instance()
                    if port_ready:
                        # Shared instance socket detected — but rnsd may still
                        # be initializing (crypto, interfaces). Retry with
                        # stabilization delays to handle the startup race.
                        print(f"Shared instance ready. Retrying {tool_name}...\n")
                        retry_combined = ""
                        for attempt in range(3):
                            if attempt > 0:
                                threading.Event().wait(2)  # MF010: bounded retry, no daemon
                            retry_result = subprocess.run(
                                cmd, capture_output=True, text=True, timeout=15
                            )
                            retry_combined = (
                                (retry_result.stdout or "")
                                + (retry_result.stderr or "")
                            )
                            retry_has_error = any(
                                p in retry_combined.lower()
                                for p in _RNS_SHARED_ERRORS
                            )
                            if (retry_result.returncode == 0
                                    and retry_result.stdout
                                    and not retry_has_error):
                                print(retry_result.stdout, end='')
                                break
                        else:
                            # All retries exhausted — show diagnostics
                            self._diagnose_rns_connectivity(retry_combined)
                    else:
                        # Port never came up — show diagnostics
                        self._diagnose_rns_connectivity(combined)
            elif result.returncode == 0:
                # Clean success — no error patterns detected
                if result.stdout:
                    print(result.stdout, end='')
            else:
                # Other error - DON'T auto-fix, just show output
                # RNS tools may return non-zero for benign reasons (empty table, no paths)
                if result.stdout:
                    print(result.stdout, end='')
                if result.stderr and result.stderr.strip():
                    # Show concise error hint — not raw tracebacks
                    stderr_lower = result.stderr.lower()
                    if "error" in stderr_lower or "traceback" in stderr_lower or "exception" in stderr_lower:
                        print(f"\n{tool_name} reported an error. Check: sudo journalctl -u rnsd -n 10")
        except FileNotFoundError:
            print(f"\n{tool_name} not found. Is RNS installed?")
            print("Install: pipx install rns")
        except subprocess.TimeoutExpired:
            print(f"\n{tool_name} timed out. RNS may be unresponsive.")
            print("Try restarting rnsd: sudo systemctl restart rnsd")

    def _wait_for_rns_shared_instance(self, max_wait: int = 10) -> bool:
        """Wait for rnsd shared instance to become available.

        Checks abstract Unix domain socket (Linux default), TCP, and UDP
        with 1-second intervals. Returns True if shared instance becomes
        reachable, False if timeout expires.
        """
        for i in range(max_wait):
            if check_rns_shared_instance():
                return True
            time.sleep(1)
        return False

    # Keep old name as alias for any callers during transition
    _wait_for_rns_port = _wait_for_rns_shared_instance

    def _get_rnsd_user(self) -> Optional[str]:
        """Get the OS user running the rnsd process, or None if not running."""
        try:
            result = subprocess.run(
                ['ps', '-o', 'user=', '-C', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return None
            # Take first line only — multiple rnsd processes may exist
            lines = result.stdout.strip().splitlines()
            return lines[0].strip() if lines else None
        except (subprocess.SubprocessError, OSError):
            return None

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Configure rnsd systemd service to run as the specified user.

        Creates a systemd override to set User= directive, then restarts rnsd.
        This is the proper fix for the identity mismatch problem where rnsd
        runs as root but user tools expect a different RNS identity.
        """
        # Validate username to prevent systemd directive injection
        if not re.match(r'^[a-z_][a-z0-9_.-]{0,31}$', target_user):
            self.ctx.dialog.msgbox(
                "Invalid Username",
                f"'{target_user}' is not a valid Linux username.",
            )
            return False

        override_dir = Path('/etc/systemd/system/rnsd.service.d')
        override_file = override_dir / 'user.conf'

        self.ctx.dialog.infobox(
            "Configuring rnsd",
            f"Setting rnsd to run as {target_user}...",
        )

        try:
            # Create override directory
            override_dir.mkdir(parents=True, exist_ok=True)

            # Write override config
            override_content = (
                f"[Service]\n"
                f"User={target_user}\n"
                f"Group={target_user}\n"
            )
            override_file.write_text(override_content)

            # mf.4 / Issue #73: flipping rnsd to a non-root user leaves
            # /etc/reticulum root-owned -> RNS.log() fails on every write (self-
            # deadlock pre-fork-mf.4, lost logs after). Repair to the canonical
            # layout BEFORE the restart via the SSOT apply-path (D3: no bespoke
            # copy — one definition in utils.rns_alignment, shared with
            # fleet_foundation + the installer + the provisioner).
            try:
                from utils.rns_alignment import apply_logfile_perms
                apply_logfile_perms(target_user)
            except Exception as e:
                logger.warning("rnsd logfile perms fix failed (non-fatal): %s", e)

            # Reload systemd and restart rnsd
            stop_service('rnsd')
            subprocess.run(
                ['pkill', '-f', 'rnsd'],
                capture_output=True, timeout=5,
            )
            threading.Event().wait(1)  # MF010: service restart stabilization
            # Honest-signal: capture the restart result rather than discarding it.
            restart_ok, restart_msg = apply_config_and_restart('rnsd')
            threading.Event().wait(2)  # MF010: service restart stabilization

            # Verify it's running as the right user now
            new_user = self._get_rnsd_user()

            if restart_ok and new_user == target_user:
                self.ctx.dialog.msgbox(
                    "rnsd Fixed",
                    f"rnsd is now running as {target_user}.\n\n"
                    f"Override created: {override_file}\n\n"
                    "RNS tools and NomadNet can now connect via RPC.",
                )
                return True
            else:
                # Surface a restart failure explicitly, not only the user check —
                # rnsd left stopped must not read as "fixed".
                detail = (
                    f"rnsd did NOT restart cleanly: {restart_msg}"
                    if not restart_ok else
                    f"rnsd is running as '{new_user}' (expected '{target_user}')."
                )
                self.ctx.dialog.msgbox(
                    "Fix May Have Failed",
                    f"{detail}\n\n"
                    f"Check: systemctl status rnsd\n"
                    f"       cat {override_file}",
                )
                return True  # Let them try anyway

        except PermissionError:
            self.ctx.dialog.msgbox(
                "Permission Denied",
                f"Cannot write to {override_dir}\n\n"
                "MeshForge needs to run with sudo to fix this.",
            )
            return False
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Configuration Failed",
                f"Could not configure rnsd: {e}\n\n"
                "Manual fix:\n"
                f"  sudo systemctl edit rnsd\n"
                f"  Add: [Service]\n"
                f"       User={target_user}",
            )
            return False

    def _diagnose_rns_connectivity(self, error_output: str):
        """Show targeted diagnostics — delegates to engine module."""
        from ._rns_diagnostics_engine import diagnose_rns_connectivity
        diagnose_rns_connectivity(self, error_output)

    def _check_nomadnet_conflict(self) -> bool:
        """Check if NomadNet is running and holding the shared instance port.

        NomadNet creates its own Reticulum() instance and becomes the shared
        instance on port 37428. If rnsd is also configured with
        share_instance = Yes, they fight over the port causing crash loops.

        Returns True if NomadNet conflict detected.
        """
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def _check_lxmf_app_conflict(self) -> Optional[str]:
        """Check if an LXMF app (NomadNet) holds port 37428.

        NomadNet can create its own RNS shared instance,
        which conflicts with rnsd if both try to bind port 37428.

        Returns the app name if conflict detected, None otherwise.
        """
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "NomadNet"
        except (subprocess.SubprocessError, OSError):
            pass

        return None

    def _check_rns_interface_health(self):
        """Run rnstatus and parse per-interface TX/RX counters — delegates to engine."""
        from ._rns_diagnostics_engine import check_rns_interface_health
        return check_rns_interface_health()

    def _diagnose_rns_port_conflict(self):
        """Diagnose and offer to fix RNS port conflicts — delegates to engine."""
        from ._rns_diagnostics_engine import diagnose_rns_port_conflict
        diagnose_rns_port_conflict(self)



    # ------------------------------------------------------------------
    # Cross-handler helpers
    # ------------------------------------------------------------------

    def _get_config_handler(self):
        """Get the RNS config handler for cross-handler calls."""
        if self.ctx.registry:
            return self.ctx.registry.get_handler("rns_config")
        return None

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

