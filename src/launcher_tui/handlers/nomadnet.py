"""
NomadNet Handler — NomadNet client installation, configuration, and management.

Provides TUI handlers to install, configure, launch, and manage
NomadNet -- the primary RNS client application used for verifying
Meshtastic <> Reticulum connectivity.

NomadNet runs its own text-UI with a built-in micron page browser
for browsing content hosted on RNS nodes.  It can also run in daemon
mode to serve pages and propagate LXMF messages.

Config directory resolution (mirrors NomadNet upstream):
  /etc/nomadnetwork  ->  ~/.config/nomadnetwork  ->  ~/.nomadnetwork

Requires:  pipx install nomadnet   (pulls in rns + lxmf automatically)

Converted from nomadnet_client_mixin.py as part of the mixin-to-registry migration (Batch 8).
"""

import os
import shutil
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler
from backend import clear_screen

logger = logging.getLogger(__name__)

from utils.paths import ReticulumPaths

from utils.safe_import import safe_import

# Import centralized service checking (start/stop/apply moved to _nomadnet_install_utils)
check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running'
)
get_rns_shared_instance_info, _ = safe_import(
    'utils.service_check', 'get_rns_shared_instance_info'
)
check_systemd_service_fn, _ = safe_import(
    'utils.service_check', 'check_systemd_service'
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home

# LXMF exclusivity — prevent concurrent LXMF apps on port 37428
from handlers._lxmf_utils import ensure_lxmf_exclusive

# RNS prerequisite checks extracted for file size compliance (CLAUDE.md #6)
from handlers._nomadnet_rns_checks import NomadNetRNSChecksMixin

# User-match + Meshtastic-iface checks extracted from rns_checks to keep
# it under the 300-line regression-guard cap.
from handlers._nomadnet_iface_checks import NomadNetIfaceChecksMixin

# Install/upgrade utilities extracted for file size compliance (CLAUDE.md #6)
from handlers._nomadnet_install_utils import NomadNetInstallUtilsMixin

# Per-identity submenus extracted for file size compliance (CLAUDE.md #6)
from handlers._nomadnet_submenus import NomadNetSubmenusMixin

# Log viewer + config IO + launch-error diagnosis extracted for file size (#6)
from handlers._nomadnet_io_ops import NomadNetIOOpsMixin

# Issue #45 — tmux-wrapped systemd user unit as a first-class concern.
from handlers._nomadnet_service_ops import NomadNetServiceOpsMixin

# Issue #45 — inline toggles for the common config knobs.
from handlers._nomadnet_config_ops import NomadNetConfigOpsMixin


class NomadNetHandler(NomadNetSubmenusMixin, NomadNetIOOpsMixin,
                      NomadNetServiceOpsMixin, NomadNetConfigOpsMixin,
                      NomadNetIfaceChecksMixin,
                      NomadNetInstallUtilsMixin, NomadNetRNSChecksMixin,
                      BaseHandler):
    """TUI handler for NomadNet client management."""

    handler_id = "nomadnet"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("nomadnet", "NomadNet Client     RNS messaging", "rns"),
        ]

    def execute(self, action):
        if action == "nomadnet":
            self._nomadnet_menu()

    # ------------------------------------------------------------------
    # LXMF exclusivity — imported from shared utility
    # ------------------------------------------------------------------

    def _ensure_lxmf_exclusive(self, starting_app: str,
                               config_dir: str = None) -> bool:
        """Ensure no other LXMF client is using the same config_dir.

        Pass None to check the default config dir; pass an explicit
        path when launching with ``--config``.
        """
        return ensure_lxmf_exclusive(
            self.ctx.dialog, starting_app, config_dir=config_dir,
        )

    # ------------------------------------------------------------------
    # Cross-handler helpers (delegate to rns_diagnostics handler)
    # ------------------------------------------------------------------

    def _get_rns_diagnostics_handler(self):
        """Get the RNS diagnostics handler from the registry."""
        if self.ctx.registry:
            return self.ctx.registry.get_handler("rns_diagnostics")
        return None

    def _show_canonical_installer_msg(self) -> None:
        """Tell the operator how to repair a non-canonical NomadNet install.

        Surfaced when ``_get_wrapper_command`` refuses because the pipx
        venv python isn't where the canonical layout expects (Issue #46).
        """
        self.ctx.dialog.msgbox(
            "NomadNet not canonically installed",
            "NomadNet's pipx venv layout is missing or non-canonical, so\n"
            "MeshForge refuses to launch it (Issue #46 wrapper-bypass guard).\n\n"
            "Repair with the canonical installer:\n"
            "  bash /opt/meshforge/scripts/install_nomadnet.sh\n\n"
            "Or via TUI:\n"
            "  NomadNet > Service Control > Reinstall NomadNet (idempotent)",
        )

    def _get_rnsd_user(self) -> Optional[str]:
        """Get the OS user running the rnsd process, or None if not running.

        Delegates to RNSDiagnosticsHandler when available, falls back to
        direct process check.
        """
        diag = self._get_rns_diagnostics_handler()
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

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Configure rnsd systemd service to run as the specified user.

        Delegates to RNSDiagnosticsHandler.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._fix_rnsd_user(target_user)
        self.ctx.dialog.msgbox(
            "Not Available",
            "RNS diagnostics handler not available.\n\n"
            "Cannot reconfigure rnsd user automatically.",
        )
        return False

    def _wait_for_rns_port(self, max_wait: int = 10) -> bool:
        """Wait for rnsd shared instance to become available.

        Delegates to RNSDiagnosticsHandler when available.
        """
        diag = self._get_rns_diagnostics_handler()
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

    def _find_blocking_interfaces(self) -> list:
        """Check if enabled RNS interfaces have missing dependencies.

        Delegates to RNSDiagnosticsHandler when available.
        """
        diag = self._get_rns_diagnostics_handler()
        if diag:
            return diag._find_blocking_interfaces()
        return []

    # ------------------------------------------------------------------
    # RNS config path detection
    # ------------------------------------------------------------------

    def _get_rns_config_for_user(self) -> str:
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
        import stat

        # Reset each call; set if the storage perms-fix below fails so the
        # launch surface can warn the operator instead of it vanishing (S7).
        self._rns_storage_prep_warning = None

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
                # A failed perms-fix means NomadNet may not write or may drift
                # to ~/.reticulum (config drift with rnsd). Stash it so the
                # launch surface surfaces it — don't let it vanish into a
                # debug-invisible log (S7, #74-#77).
                self._rns_storage_prep_warning = str(e)
                logger.warning(f"Could not fix /etc/reticulum/storage: {e}")

            return str(etc_rns)

        # No system config -- use default resolution
        # (ReticulumPaths.get_config_dir will find XDG or ~/.reticulum)
        config_dir = ReticulumPaths.get_config_dir()
        return str(config_dir)

    # ------------------------------------------------------------------
    # share_instance pre-flight check
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Ownership fix for user directories
    # ------------------------------------------------------------------

    def _fix_user_directory_ownership(self) -> bool:
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
        if not self.ctx.dialog.yesno(
            "Fix Directory Ownership",
            f"The following directories are owned by root,\n"
            f"which prevents NomadNet from accessing them:\n\n"
            f"{dir_list}\n\n"
            f"This happened because NomadNet or rnsd was\n"
            f"previously run as root.\n\n"
            f"Fix ownership to user '{sudo_user}'?",
        ):
            # User declined - warn but allow proceeding
            return self.ctx.dialog.yesno(
                "Proceed Anyway?",
                "Ownership was not fixed.\n\n"
                "NomadNet may fail with 'Permission denied' errors.\n\n"
                "Continue anyway?",
            )

        # Fix ownership recursively
        self.ctx.dialog.infobox("Fixing Ownership", f"Changing ownership to {sudo_user}...")

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
                self.ctx.dialog.msgbox(
                    "Ownership Fix Failed",
                    f"Could not fix ownership of:\n  {dir_path}\n\n"
                    f"Error: {e}\n\n"
                    f"Relaunch MeshForge in Admin mode (sudo) to fix ownership.",
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _nomadnet_menu(self):
        """NomadNet top-level menu — service-first, with legacy paths under Advanced.

        Issue #45: the tmux-wrapped systemd user unit
        (``nomadnet-user.service``) is the canonical NomadNet on every
        fleet box. The menu surfaces Attach / Service Control as the
        hot path; Default Identity / Interactive Client / raw launch
        live under Advanced for back-compat.
        """
        while True:
            installed = self._is_nomadnet_installed()

            if not installed:
                choice = self.ctx.dialog.menu(
                    "NomadNet Client",
                    "NomadNet is NOT INSTALLED.\n"
                    "RNS client with page browser & LXMF messaging.",
                    [
                        ("status", "NomadNet Status (global overview)"),
                        ("install", "Install NomadNet (pipx)"),
                        ("back", "Back"),
                    ],
                )
                if choice is None or choice == "back":
                    break
                dispatch = {
                    "status": ("NomadNet Status", self._nomadnet_status),
                    "install": ("Install NomadNet", self._install_nomadnet),
                }
                entry = dispatch.get(choice)
                if entry:
                    self.ctx.safe_call(*entry)
                continue

            service_state = self._nomadnet_service_state()
            service_line = self._service_state_line(service_state)
            mesh_line = self._mesh_iface_subtitle_state()
            subtitle = service_line + (f"\n{mesh_line}" if mesh_line else "")

            choices: list = [
                ("status", "Status          overview, service, interfaces"),
            ]
            if service_state["tmux_session"]:
                choices.append(
                    ("attach", "Attach tmux     enter live NomadNet TUI"),
                )
            choices.append(
                ("service", "Service Control start / stop / install unit"),
            )
            choices.append(
                ("logs", "Logs            journal / tmux / logfile / rnsd"),
            )
            choices.append(
                ("config", "Configuration   toggles / propagation node"),
            )
            choices.append(
                ("advanced", "Advanced        default / interactive / reset"),
            )
            choices.append(("uninstall", "Disable NomadNet"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                "NomadNet Client",
                f"RNS client with page browser & LXMF messaging:\n\n"
                f"{subtitle}",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("NomadNet Status", self._nomadnet_status),
                "attach": ("Attach tmux", self._attach_tmux_session),
                "service": ("Service Control", self._service_control_menu),
                "logs": ("Logs", self._unified_logs_menu),
                "config": ("Configuration", self._config_menu),
                "advanced": ("Advanced", self._advanced_menu),
                "uninstall": ("Disable NomadNet", self._uninstall_nomadnet),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _nomadnet_status(self):
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
        running = self._is_nomadnet_running()
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

        # Service (Issue #45): tmux-wrapped systemd user unit state
        print()
        print("--- Service State ---")
        self._print_service_state_block()

        # Config file
        print()
        config_path = self._get_nomadnet_config_path()
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

        # RNS shared instance check — verify BOTH process AND shared instance
        print()
        print("--- RNS Connectivity ---")
        rnsd_running = False
        shared_available = False
        shared_detail = ''
        try:
            if _HAS_SERVICE_CHECK:
                rnsd_running = check_process_running('rnsd')
                if get_rns_shared_instance_info:
                    instance_name = ReticulumPaths.get_configured_instance_name()
                    si_info = get_rns_shared_instance_info(instance_name)
                    shared_available = (si_info or {}).get(
                        'available', False
                    )
                    shared_detail = (si_info or {}).get('detail', '')
            else:
                # Fallback to direct pgrep call (exact match only)
                result = subprocess.run(
                    ['pgrep', '-x', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0

            if rnsd_running and shared_available:
                print(f"  rnsd:      RUNNING (shared instance: "
                      f"{shared_detail})")
            elif rnsd_running:
                print("  rnsd:      RUNNING (shared instance "
                      "NOT available)")
                print("  WARNING:   rnsd may be hung or "
                      "interfaces blocking startup")
            else:
                print("  rnsd:      NOT running")
                # Show actionable fix hint from systemd state
                if _HAS_SERVICE_CHECK and check_systemd_service_fn:
                    try:
                        _, is_enabled = check_systemd_service_fn('rnsd')
                        if not is_enabled:
                            print("  Fix:       sudo systemctl "
                                  "enable --now rnsd")
                        else:
                            print("  Fix:       sudo systemctl "
                                  "start rnsd")
                    except Exception as e:
                        logger.debug(
                            "systemd check for rnsd failed: %s", e
                        )
                print("  WARNING:   NomadNet needs rnsd or "
                      "share_instance=Yes")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("rnsd status check failed: %s", e)
            print("  rnsd:      (check failed)")

        # Show RNS interface status when shared instance is available
        has_issues = False
        if rnsd_running and shared_available:
            try:
                from utils.rns_status_parser import (
                    run_rnstatus, InterfaceStatus, parse_rnstatus,
                )
                status = run_rnstatus()

                # If rnstatus failed (e.g. auth mismatch when running
                # as root), retry as the real user
                if status and status.parse_error and not status.interfaces:
                    sudo_user = os.environ.get('SUDO_USER')
                    if sudo_user and sudo_user != 'root':
                        try:
                            import shutil as _shutil
                            rnstatus_bin = _shutil.which('rnstatus')
                            if not rnstatus_bin:
                                _candidate = (
                                    get_real_user_home() / '.local'
                                    / 'bin' / 'rnstatus'
                                )
                                if _candidate.exists():
                                    rnstatus_bin = str(_candidate)
                            if rnstatus_bin:
                                proc = subprocess.run(
                                    ['sudo', '-u', sudo_user, '-H',
                                     rnstatus_bin],
                                    capture_output=True, text=True,
                                    timeout=15,
                                )
                                combined = (
                                    (proc.stdout or "")
                                    + (proc.stderr or "")
                                )
                                retry = parse_rnstatus(combined)
                                if retry.interfaces:
                                    status = retry
                        except (subprocess.SubprocessError, OSError) as e:
                            logger.debug(
                                "rnstatus retry as %s failed: %s",
                                sudo_user, e,
                            )

                if status and status.interfaces:
                    print()
                    print("--- RNS Interfaces ---")
                    has_down = False
                    has_rx_only = False
                    has_zero_traffic = False
                    for iface in status.interfaces:
                        if iface.status == InterfaceStatus.UP:
                            icon = "\033[0;32mUP\033[0m"
                        elif iface.status == InterfaceStatus.DOWN:
                            icon = "\033[0;31mDOWN\033[0m"
                            has_down = True
                        else:
                            icon = "?"
                        # Build traffic info
                        traffic = ""
                        if iface.tx.bytes_total > 0 or iface.rx.bytes_total > 0:
                            traffic = (
                                f"  \u2191{iface.tx.bytes_total:.0f} "
                                f"{iface.tx.bytes_unit}  "
                                f"\u2193{iface.rx.bytes_total:.0f} "
                                f"{iface.rx.bytes_unit}"
                            )
                        # Flag anomalies
                        flags = ""
                        if iface.is_rx_only:
                            flags = "  \033[0;33m[RX-ONLY]\033[0m"
                            has_rx_only = True
                        elif (iface.is_zero_traffic
                              and iface.status == InterfaceStatus.UP):
                            flags = "  \033[0;33m[no traffic]\033[0m"
                            has_zero_traffic = True
                        print(f"  {iface.display_name:<40} "
                              f"{icon}{traffic}{flags}")

                    # Connectivity summary
                    total = len(status.interfaces)
                    connected = len([
                        i for i in status.interfaces
                        if i.tx.bytes_total > 0 or i.rx.bytes_total > 0
                    ])
                    isolated = len(status.zero_traffic_interfaces)
                    down_count = len([
                        i for i in status.interfaces
                        if i.status == InterfaceStatus.DOWN
                    ])
                    print()
                    print(f"  Summary: {total} interfaces, "
                          f"{connected} with traffic, "
                          f"{isolated} zero-traffic, "
                          f"{down_count} down")

                    # Always check for blocking interfaces — an
                    # interface can be UP in rnstatus but its
                    # dependency may be flaky or unreachable
                    try:
                        from handlers._rns_interface_mgr import (
                            find_blocking_interfaces,
                        )
                        blocking = find_blocking_interfaces()
                        if blocking:
                            has_issues = True
                            print()
                            print("--- Blocking Interfaces ---")
                            for name, reason, fix in blocking:
                                print(f"  \033[0;33m[{name}]\033[0m "
                                      f"{reason}")
                                print(f"    Fix: {fix}")
                                logger.warning(
                                    "RNS blocking interface [%s]: "
                                    "%s (fix: %s)",
                                    name, reason, fix,
                                )
                    except Exception as e:
                        logger.debug(
                            "Blocking interface check failed: %s", e
                        )

                    # Warn about RX-only interfaces
                    if has_rx_only:
                        has_issues = True
                        rx_only = [
                            i for i in status.interfaces if i.is_rx_only
                        ]
                        print()
                        print(
                            f"  \033[0;33mWARNING: {len(rx_only)} "
                            f"interface(s) receiving only — link "
                            f"establishment may be failing\033[0m"
                        )
                        for iface in rx_only:
                            logger.warning(
                                "RNS interface %s is RX-only "
                                "(link establishment failing)",
                                iface.display_name,
                            )

                    # Warn about zero-traffic UP interfaces
                    if has_zero_traffic:
                        has_issues = True
                        zero = status.zero_traffic_interfaces
                        print()
                        print(
                            f"  \033[0;33mWARNING: {len(zero)} "
                            f"interface(s) UP but no traffic — "
                            f"no peers announcing on these "
                            f"interfaces\033[0m"
                        )
                        for iface in zero:
                            logger.warning(
                                "RNS interface %s is UP but has "
                                "zero traffic (no peers/announces)",
                                iface.display_name,
                            )

                elif status and status.parse_error:
                    has_issues = True
                    print(f"\n  rnstatus: {status.parse_error}")
                    logger.warning(
                        "rnstatus failed in NomadNet status: %s",
                        status.parse_error,
                    )
            except Exception as e:
                logger.debug("Interface status check failed: %s", e)

        # When shared instance is unavailable, still check for blocking
        # interfaces — gives actionable diagnostics even when rnsd is down
        if not shared_available:
            try:
                from handlers._rns_interface_mgr import (
                    find_blocking_interfaces,
                )
                blocking = find_blocking_interfaces()
                if blocking:
                    has_issues = True
                    print()
                    print("--- Blocking Interfaces ---")
                    for name, reason, fix in blocking:
                        print(f"  \033[0;33m[{name}]\033[0m "
                              f"{reason}")
                        print(f"    Fix: {fix}")
                        logger.warning(
                            "RNS blocking interface [%s]: "
                            "%s (fix: %s)",
                            name, reason, fix,
                        )
                    if not rnsd_running:
                        print()
                        print("  NOTE: Fix these before starting "
                              "rnsd — they will block startup.")
                elif not rnsd_running:
                    print()
                    print("  No interface dependency issues "
                          "detected — rnsd should start cleanly.")
            except Exception as e:
                logger.debug(
                    "Blocking interface check failed: %s", e
                )

        # Check rnsd loglevel — suggest increasing if interfaces
        # have issues and loglevel is too low for troubleshooting
        if has_issues:
            try:
                rns_config = ReticulumPaths.get_config_file()
                if rns_config.exists():
                    import re as _re
                    content = rns_config.read_text()
                    m = _re.search(
                        r'^\s*loglevel\s*=\s*(\d+)',
                        content, _re.MULTILINE,
                    )
                    current_level = int(m.group(1)) if m else 4
                    if current_level < 6:
                        print()
                        print(
                            "  \033[0;36mTIP: Set loglevel = 6 in "
                            f"{rns_config}\033[0m"
                        )
                        print(
                            "       and restart rnsd to see why "
                            "interfaces aren't connecting."
                        )
                        print(
                            "       View via: NomadNet > Logs > "
                            "rnsd journal"
                        )
            except Exception as e:
                logger.debug("loglevel check failed: %s", e)

        # Show recent NomadNet logfile errors inline
        nn_logfile = get_real_user_home() / '.nomadnetwork' / 'logfile'
        if nn_logfile.exists():
            try:
                import collections
                with open(nn_logfile, 'r') as f:
                    recent = list(collections.deque(f, maxlen=200))
                error_patterns = [
                    'Error', 'Exception', 'CRITICAL',
                    'WARNING', 'AuthenticationError',
                    'ConnectionRefused', 'Traceback',
                ]
                errors = [
                    line.rstrip() for line in recent
                    if any(p in line for p in error_patterns)
                ]
                if errors:
                    print()
                    print("--- Recent NomadNet Errors ---")
                    for line in errors[-5:]:
                        print(f"  {line}")
                    if len(errors) > 5:
                        print(f"  ... ({len(errors) - 5} more — "
                              f"see Logs > Errors)")
            except (OSError, PermissionError):
                pass

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Launch text UI
    # ------------------------------------------------------------------

    def _launch_nomadnet_textui(self):
        """Launch NomadNet in interactive text UI mode.

        This takes over the terminal (like running nomadnet directly).
        The user returns to MeshForge when they exit NomadNet.

        When running via sudo, launches as the real user so NomadNet
        uses their config (~/.nomadnetwork) instead of root's.
        """
        # Issue #45: refuse to launch a raw TUI when the tmux-wrapped
        # systemd user unit owns the default identity. Spawning a
        # second nomadnet on the same config dir breaks LXMF / the
        # tmux session, and the right answer is Attach, not Launch.
        if not self._warn_if_service_active(
            "Launch Text UI (Advanced)",
            "The NomadNet user service is currently active.\n\n"
            "Use NomadNet > Attach tmux session to interact with the\n"
            "running instance, or stop the service from Service Control\n"
            "before launching a raw TUI.",
        ):
            return

        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        # LXMF exclusivity: prevent concurrent LXMF apps
        if not self._ensure_lxmf_exclusive("nomadnet"):
            return

        # Fix ownership of user directories if they were created by root
        # This is a common issue when MeshForge runs with sudo
        if not self._fix_user_directory_ownership():
            return

        # Validate and repair config if needed (e.g., missing [textui] section)
        if not self._validate_nomadnet_config():
            return

        # Check if rnsd is running (NomadNet needs RNS)
        if not self._check_rns_for_nomadnet(nn_path=nn_path):
            return

        # Check if we need to use a specific RNS config path
        # This handles the case where /etc/reticulum exists but isn't writable
        rns_config_path = self._get_rns_config_for_user()

        # Clear screen before launching
        clear_screen()
        print("=== Launching NomadNet ===")
        if rns_config_path:
            print(f"Using RNS config: {rns_config_path}")
        prep_warning = getattr(self, '_rns_storage_prep_warning', None)
        if prep_warning:
            # Surface the storage perms-fix failure swallowed in
            # _get_rns_config_for_user (S7) — the operator needs to know
            # NomadNet may fail to write or drift to ~/.reticulum.
            print(f"⚠ Storage perms not fixed: {prep_warning}")
            print("  NomadNet may fail to write or drift to ~/.reticulum.")
        print("Exit NomadNet (Ctrl+Q) to return to MeshForge.\n")

        # When running via sudo, we must run NomadNet as the real user.
        # Just setting HOME is not enough - RPC authentication between
        # NomadNet and rnsd requires matching UIDs.
        sudo_user = os.environ.get('SUDO_USER')

        try:
            # Build base command with optional --rnsconfig
            nn_args = ['--textui']
            if rns_config_path:
                nn_args = ['--rnsconfig', rns_config_path, '--textui']

            # Build command — use wrapper to patch RPC if possible
            cmd = self._get_wrapper_command(nn_path, nn_args)
            if cmd is None:
                self._show_canonical_installer_msg()
                return

            if sudo_user and sudo_user != 'root':
                # Run as real user using 'sudo -u' with explicit PATH
                # The -H sets HOME correctly, we pass PATH for pipx binaries
                user_home = get_real_user_home()
                user_path = f"{user_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H',
                     f'PATH={user_path}'] + cmd,
                    stderr=subprocess.PIPE, text=True,
                    timeout=None
                )
            else:
                # Not running via sudo, run directly
                result = subprocess.run(
                    cmd, stderr=subprocess.PIPE, text=True,
                    timeout=None
                )

            # After NomadNet exits, show status and wait for user
            print()
            if result.returncode != 0:
                self._show_launch_error(result.returncode, result.stderr)
            else:
                print("NomadNet exited normally.")
            print("\nPress Enter to return to MeshForge...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except KeyboardInterrupt:
            print("\n\nAborted.")
            print("\nReturning to MeshForge...")
        except FileNotFoundError:
            print(f"\nError: NomadNet binary not found at: {nn_path}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except Exception as e:
            print(f"\nFailed to launch NomadNet: {e}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    # ------------------------------------------------------------------
    # Launch interactive client with separate identity
    # ------------------------------------------------------------------

    # Sibling to the default ~/.nomadnetwork config dir. Different name =
    # different identity file = different lxmf.delivery hash. Attaches to
    # the same rnsd shared instance.
    INTERACTIVE_CONFIG_REL = ".nomadnetwork-interactive"

    def _interactive_config_dir(self) -> Path:
        return get_real_user_home() / self.INTERACTIVE_CONFIG_REL

    def _launch_nomadnet_interactive(self):
        """Launch a second NomadNet TUI with its own identity.

        Uses ``--config ~/.nomadnetwork-interactive`` so it does NOT
        collide with a running daemon or default-config textui. Safe to
        run alongside the meshforge-gateway daemon — it attaches to the
        same rnsd shared instance but owns a distinct LXMF identity.
        """
        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        config_dir = self._interactive_config_dir()

        # Exclusivity check keyed on THIS config dir — no false-positives
        # from rnsd or from the daemon using the default config.
        if not self._ensure_lxmf_exclusive("nomadnet", str(config_dir)):
            return

        # Create config dir as the real user so NomadNet can write to it.
        if not self._ensure_interactive_config_dir(config_dir):
            return

        if not self._check_rns_for_nomadnet(nn_path=nn_path):
            return

        rns_config_path = self._get_rns_config_for_user()

        clear_screen()
        print("=== Launching NomadNet (Interactive Client) ===")
        print(f"Config dir: {config_dir}")
        print(f"Identity:   {config_dir}/storage/identity")
        if rns_config_path:
            print(f"RNS config: {rns_config_path}")
        print()
        print("Exit NomadNet (Ctrl+Q) to return to MeshForge.")
        print("After exit, MeshForge will show your interactive hash")
        print("so you can share it with peers to receive DMs.\n")

        sudo_user = os.environ.get('SUDO_USER')

        try:
            nn_args = ['--config', str(config_dir), '--textui']
            if rns_config_path:
                nn_args = ['--rnsconfig', rns_config_path] + nn_args

            cmd = self._get_wrapper_command(nn_path, nn_args)
            if cmd is None:
                self._show_canonical_installer_msg()
                return

            if sudo_user and sudo_user != 'root':
                user_home = get_real_user_home()
                user_path = f"{user_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H',
                     f'PATH={user_path}'] + cmd,
                    stderr=subprocess.PIPE, text=True,
                    timeout=None,
                )
            else:
                result = subprocess.run(
                    cmd, stderr=subprocess.PIPE, text=True,
                    timeout=None,
                )

            print()
            if result.returncode != 0:
                self._show_launch_error(result.returncode, result.stderr)
            else:
                print("NomadNet (interactive) exited normally.")
            self._print_interactive_hash(config_dir)
            print("\nPress Enter to return to MeshForge...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except KeyboardInterrupt:
            print("\n\nAborted.\nReturning to MeshForge...")
        except FileNotFoundError:
            print(f"\nError: NomadNet binary not found at: {nn_path}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except Exception as e:
            print(f"\nFailed to launch NomadNet (interactive): {e}")
            print("\nPress Enter to continue...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    def _ensure_interactive_config_dir(self, config_dir: Path) -> bool:
        """Create the interactive config dir and seed its config from default.

        Behavior:
          1. mkdir the dir if missing (ownership fixed via chown below).
          2. If ``<config_dir>/config`` is missing AND the default NomadNet
             config (``~/.nomadnetwork/config``) exists, copy the default's
             content into place so node-hosting settings ([nodeserver],
             [propagation], [textui] etc.) carry over. We copy content,
             not the file, because the identity lives in a separate
             ``storage/identity`` file — only the config text is shared.
          3. Never overwrite an existing interactive config (Issue #22/#31).
          4. chown -R the whole tree to the real user when running via sudo.
        """
        try:
            newly_created = not config_dir.exists()
            if newly_created:
                config_dir.mkdir(parents=True, exist_ok=True)

            interactive_cfg = config_dir / "config"
            seeded = False
            if not interactive_cfg.exists():
                default_cfg = get_real_user_home() / ".nomadnetwork" / "config"
                if default_cfg.exists():
                    try:
                        interactive_cfg.write_text(default_cfg.read_text())
                        seeded = True
                        logger.info(
                            "Seeded interactive config from default: %s -> %s",
                            default_cfg, interactive_cfg,
                        )
                    except (OSError, PermissionError) as e:
                        logger.warning(
                            "Could not seed interactive config from %s: %s",
                            default_cfg, e,
                        )
                else:
                    logger.info(
                        "No default config at %s — interactive config will "
                        "be generated blank by NomadNet on first launch",
                        default_cfg,
                    )

            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                subprocess.run(
                    ['chown', '-R', f'{sudo_user}:{sudo_user}',
                     str(config_dir)],
                    capture_output=True, timeout=15,
                )

            if seeded:
                self.ctx.dialog.msgbox(
                    "Config Seeded",
                    f"Seeded interactive config from your default NomadNet\n"
                    f"config. Node-hosting settings ([nodeserver],\n"
                    f"[propagation]) carry over.\n\n"
                    f"New:       {interactive_cfg}\n"
                    f"Seeded from: {get_real_user_home()}/.nomadnetwork/config\n\n"
                    f"Use 'Edit Config' in the Interactive Client submenu\n"
                    f"to customize before or after launch.",
                )
            return True
        except (OSError, subprocess.SubprocessError) as e:
            self.ctx.dialog.msgbox(
                "Config Dir Error",
                f"Could not prepare interactive config dir:\n\n"
                f"  {config_dir}\n\n"
                f"Error: {e}",
            )
            return False

    def _print_interactive_hash(self, config_dir: Path) -> None:
        """Print the lxmf.delivery hash of the interactive identity, if available."""
        identity = config_dir / "storage" / "identity"
        if not identity.exists():
            print(f"\n  (No identity found yet at {identity};")
            print(f"   it's created on first NomadNet start.)")
            return
        try:
            result = subprocess.run(
                ['rnid', '-i', str(identity), '-H', 'lxmf.delivery'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                print("\n--- Your interactive LXMF hash ---")
                print(result.stdout.rstrip())
                print("Share this hash with peers so they can DM you.")
            else:
                print(f"\n  (rnid exit {result.returncode})")
                if result.stderr:
                    print(result.stderr.rstrip())
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            print(f"\n  (Could not read hash via rnid: {e})")

    # _show_launch_error, _view_nomadnet_logs[_for], _show_log_options —
    # see NomadNetIOOpsMixin

    # ------------------------------------------------------------------
    # Launch daemon
    # ------------------------------------------------------------------

    def _launch_nomadnet_daemon(self):
        """Start NomadNet in daemon mode (background, no UI).

        When running via sudo, launches as the real user so NomadNet
        uses their config (~/.nomadnetwork) instead of root's.
        """
        # Issue #45: the canonical NomadNet is the tmux-wrapped systemd
        # user unit. Starting a bare --daemon alongside it double-binds
        # the LXMF identity and causes the exclusivity lock to flap.
        if not self._warn_if_service_active(
            "Start Daemon (Advanced)",
            "The NomadNet user service is already active.\n\n"
            "Running a second nomadnet --daemon against the same\n"
            "identity causes exclusivity conflicts. Use Service\n"
            "Control instead, or stop the service first.",
        ):
            return

        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        if self._is_nomadnet_running():
            self.ctx.dialog.msgbox("Already Running", "NomadNet is already running.")
            return

        # LXMF exclusivity: prevent concurrent LXMF apps
        if not self._ensure_lxmf_exclusive("nomadnet"):
            return

        # Fix ownership of user directories if they were created by root
        if not self._fix_user_directory_ownership():
            return

        if not self._check_rns_for_nomadnet(nn_path=nn_path):
            return

        # Get RNS config path (must match rnsd to prevent config drift)
        rns_config_path = self._get_rns_config_for_user()

        if not self.ctx.dialog.yesno(
            "Start NomadNet Daemon",
            "Start NomadNet in daemon mode (background)?\n\n"
            "This will:\n"
            "  - Announce your node on the RNS network\n"
            "  - Accept and propagate LXMF messages\n"
            "  - Serve node pages (if enabled in config)\n\n"
            "NomadNet will run until stopped.",
        ):
            return

        self.ctx.dialog.infobox("Starting", "Starting NomadNet daemon...")

        # Build command - run as real user if we're under sudo
        # This ensures NomadNet uses ~/.nomadnetwork/config, not /root/.nomadnetwork/config
        sudo_user = os.environ.get('SUDO_USER')

        # Build base args with optional --rnsconfig
        nn_args = ['--daemon']
        if rns_config_path:
            nn_args = ['--rnsconfig', rns_config_path, '--daemon']

        # Build command — use wrapper to patch RPC if possible
        base_cmd = self._get_wrapper_command(nn_path, nn_args)
        if base_cmd is None:
            self._show_canonical_installer_msg()
            return

        if sudo_user and sudo_user != 'root':
            # Run as real user with -H to set HOME correctly
            # Using -H instead of -i avoids running shell profiles which can interfere
            cmd = ['sudo', '-H', '-u', sudo_user] + base_cmd
        else:
            cmd = base_cmd

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            # Wait briefly and verify
            time.sleep(3)

            if self._is_nomadnet_running():
                self.ctx.dialog.msgbox(
                    "Daemon Started",
                    "NomadNet daemon is running in the background.\n\n"
                    "Your node is now announcing on the RNS network.\n"
                    "Use 'Stop NomadNet' to shut it down.",
                )
            else:
                # Read stderr from the failed process
                stderr_out = ""
                try:
                    stderr_bytes = proc.stderr.read(4096) if proc.stderr else b""
                    stderr_out = stderr_bytes.decode('utf-8', errors='replace').strip()
                except (OSError, ValueError):
                    pass

                if stderr_out and ('ConnectionRefusedError' in stderr_out
                                   or 'Errno 111' in stderr_out):
                    self.ctx.dialog.msgbox(
                        "Start Failed — Connection Refused",
                        "NomadNet daemon crashed: ConnectionRefusedError.\n\n"
                        "Use RNS Diagnostics to check rnsd status.\n\n"
                        "Quick fix: sudo systemctl restart rnsd\n"
                        "Then wait 20s and re-launch NomadNet.",
                    )
                elif stderr_out:
                    # Show first few lines of stderr
                    lines = stderr_out.splitlines()[:5]
                    detail = "\n".join(lines)
                    self.ctx.dialog.msgbox(
                        "Start Failed",
                        f"NomadNet daemon failed to start.\n\n"
                        f"Error output:\n{detail}\n\n"
                        f"Retry from the NomadNet menu after addressing the error.",
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "Start Failed",
                        "NomadNet daemon failed to start.\n\n"
                        "Check logs: ~/.nomadnetwork/logfile\n"
                        "Retry from the NomadNet menu, or view logs in-app.",
                    )
        except FileNotFoundError:
            self.ctx.dialog.msgbox("Error", f"NomadNet binary not found at: {nn_path}")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to start NomadNet daemon:\n{e}")

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def _stop_nomadnet(self, config_dir: "Optional[Path]" = None):
        """Stop running NomadNet process(es).

        When ``config_dir`` is given, only processes whose ``--config``
        argv (or default config dir) matches are killed — this scopes the
        stop to a single identity (default or interactive). Without
        ``config_dir`` the original behavior is preserved: kill every
        nomadnet process on the box.
        """
        from handlers._lxmf_utils import find_competing_clients

        if config_dir is not None:
            pids = [
                int(pid) for (pid, _client, _cfg)
                in find_competing_clients(str(config_dir))
                if pid.isdigit()
            ]
            if not pids:
                self.ctx.dialog.msgbox(
                    "Not Running",
                    f"No NomadNet process found for this identity:\n\n"
                    f"  {config_dir}",
                )
                return
            if not self.ctx.dialog.yesno(
                "Stop NomadNet",
                f"Stop NomadNet for identity:\n\n"
                f"  {config_dir}\n\n"
                f"PIDs: {', '.join(str(p) for p in pids)}",
            ):
                return
            logger.info(
                "Stopping NomadNet for config_dir=%s PIDs=%s",
                config_dir, pids,
            )
            self._kill_nomadnet_pids(pids)
            # Confirm via /proc — _is_nomadnet_running() is not
            # identity-scoped, so use find_competing_clients again.
            remaining = find_competing_clients(str(config_dir))
            if remaining:
                self.ctx.dialog.msgbox(
                    "Warning",
                    f"NomadNet may still be running for {config_dir}.\n"
                    f"Try: sudo kill -9 {' '.join(p for p, _, _ in remaining)}",
                )
            else:
                self.ctx.dialog.msgbox(
                    "Stopped",
                    f"NomadNet stopped for identity:\n  {config_dir}",
                )
            return

        # Global stop — original behavior, guarded against fighting
        # the tmux-wrapped systemd user service (Issue #45). If the
        # service is active and supervising the process, pkill will
        # just trigger Restart=on-failure, leaving us in a confused
        # "we reported Stopped but it's still running" state.
        svc = self._nomadnet_service_state()
        if svc["active"]:
            self.ctx.dialog.msgbox(
                "Managed by systemd",
                "The NomadNet user service is active and supervising\n"
                "the running process. pkill would fight its\n"
                "Restart=on-failure loop.\n\n"
                "Use:  Service Control > Stop service\n"
                "(systemctl --user stop nomadnet)",
            )
            return

        if not self._is_nomadnet_running():
            self.ctx.dialog.msgbox("Not Running", "NomadNet is not currently running.")
            return

        if not self.ctx.dialog.yesno(
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

            if self._is_nomadnet_running():
                # Force kill
                subprocess.run(
                    ['pkill', '-9', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10
                )
                time.sleep(1)

            if not self._is_nomadnet_running():
                self.ctx.dialog.msgbox("Stopped", "NomadNet has been stopped.")
            else:
                self.ctx.dialog.msgbox("Warning", "NomadNet may still be running.\nRelaunch in Admin mode to force-stop it.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop NomadNet:\n{e}")

    def _kill_nomadnet_pids(self, pids):
        """SIGTERM then SIGKILL a list of NomadNet PIDs."""
        import signal
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError) as e:
                logger.debug("SIGTERM %s failed: %s", pid, e)
        time.sleep(2)
        for pid in pids:
            try:
                os.kill(pid, 0)  # probe — raises if gone
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as e:
                logger.debug("SIGKILL %s failed: %s", pid, e)
        time.sleep(1)

    # ------------------------------------------------------------------
    # Uninstall (stop + disable)
    # ------------------------------------------------------------------

    # _uninstall_nomadnet provided by NomadNetInstallUtilsMixin

    # ------------------------------------------------------------------
    # Config management
    # ------------------------------------------------------------------

    # _view_nomadnet_config, _edit_nomadnet_config[_for], _open_editor —
    # see NomadNetIOOpsMixin

    # ------------------------------------------------------------------
    # Propagation node configuration
    # ------------------------------------------------------------------

    # _configure_propagation_node provided by NomadNetConfigOpsMixin

    # Install/upgrade utilities provided by NomadNetInstallUtilsMixin:
    #   _install_nomadnet, _run_canonical_installer, _find_pipx,
    #   _upgrade_nomadnet, _get_rns_version_info, _is_nomadnet_installed,
    #   _is_nomadnet_running, _find_nomadnet_binary,
    #   _get_nomadnet_config_path, _create_nomadnet_wrapper,
    #   _get_wrapper_command

    # RNS prerequisite checks provided by NomadNetRNSChecksMixin:
    #   _check_rns_for_nomadnet, _validate_nomadnet_config
