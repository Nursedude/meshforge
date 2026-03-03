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

Split into three files for size compliance (CLAUDE.md #6):
  nomadnet.py          — This file: menu dispatch, launch orchestrator, small helpers
  _nomadnet_checks.py  — RNS readiness, ownership repair, config validation, diagnostics
  _nomadnet_ops.py     — Status, logs, daemon, stop, install, uninstall, config view/edit
"""

import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional

from handler_protocol import BaseHandler, PRIVILEGE_ADMIN
from backend import clear_screen

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

# Import centralized service checking
check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running'
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home

# LXMF exclusivity — shared utility for MeshChat/NomadNet conflict prevention
from handlers._lxmf_utils import ensure_lxmf_exclusive


class NomadNetHandler(BaseHandler):
    """TUI handler for NomadNet client management."""

    handler_id = "nomadnet"
    menu_section = "mesh_networks"
    privilege_level = PRIVILEGE_ADMIN

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

    def _ensure_lxmf_exclusive(self, starting_app: str) -> bool:
        """Ensure only one LXMF app runs at a time.

        NomadNet doesn't have is_meshchat_running_fn, so it uses the
        default pgrep fallback in the utility.
        """
        return ensure_lxmf_exclusive(self.ctx.dialog, starting_app)

    # ------------------------------------------------------------------
    # Cross-handler helpers (delegate to rns_diagnostics handler)
    # ------------------------------------------------------------------

    def _get_rns_diagnostics_handler(self):
        """Get the RNS diagnostics handler from the registry."""
        if self.ctx.registry:
            return self.ctx.registry.get_handler("rns_diagnostics")
        return None

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _nomadnet_menu(self):
        """NomadNet RNS client -- install, configure, launch."""
        def _nomadnet_choices():
            running = self._is_nomadnet_running()
            installed = self._is_nomadnet_installed()
            items = [("status", "NomadNet Status")]
            if installed:
                if running:
                    items.append(("stop", "Stop NomadNet"))
                else:
                    items.append(("textui", "Launch Text UI (interactive)"))
                    items.append(("daemon", "Start as Daemon (background)"))
                items.append(("logs", "View NomadNet Logs"))
                items.append(("config", "View NomadNet Config"))
                items.append(("edit", "Edit NomadNet Config"))
                items.append(("uninstall", "Disable NomadNet"))
            else:
                items.append(("install", "Install NomadNet"))
            return items

        dispatch = {
            "status": ("NomadNet Status", self._nomadnet_status),
            "textui": ("Launch NomadNet TUI", self._launch_nomadnet_textui),
            "daemon": ("Start NomadNet Daemon", self._launch_nomadnet_daemon),
            "stop": ("Stop NomadNet", self._stop_nomadnet),
            "logs": ("View NomadNet Logs", self._view_nomadnet_logs),
            "config": ("View NomadNet Config", self._view_nomadnet_config),
            "edit": ("Edit NomadNet Config", self._edit_nomadnet_config),
            "install": ("Install NomadNet", self._install_nomadnet),
            "uninstall": ("Disable NomadNet", self._uninstall_nomadnet),
        }
        self.run_menu_loop(
            "NomadNet Client", "RNS client with page browser & LXMF messaging:",
            _nomadnet_choices, dispatch,
        )

    # ------------------------------------------------------------------
    # Launch text UI (core orchestrator — stays in main file)
    # ------------------------------------------------------------------

    def _launch_nomadnet_textui(self):
        """Launch NomadNet in interactive text UI mode.

        This takes over the terminal (like running nomadnet directly).
        The user returns to MeshForge when they exit NomadNet.

        When running via sudo, launches as the real user so NomadNet
        uses their config (~/.nomadnetwork) instead of root's.
        """
        nn_path = self._find_nomadnet_binary()
        if not nn_path:
            return

        # LXMF exclusivity: stop MeshChat if running (one at a time)
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
        if not self._check_rns_for_nomadnet():
            return

        # Check if we need to use a specific RNS config path
        # This handles the case where /etc/reticulum exists but isn't writable
        rns_config_path = self._get_rns_config_for_user()

        # Clear screen before launching
        clear_screen()
        print("=== Launching NomadNet ===")
        if rns_config_path:
            print(f"Using RNS config: {rns_config_path}")
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

            if sudo_user and sudo_user != 'root':
                # Run as real user using 'sudo -u' with explicit PATH
                # The -H sets HOME correctly, we pass PATH for pipx binaries
                user_home = get_real_user_home()
                user_path = f"{user_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
                result = subprocess.run(
                    ['sudo', '-u', sudo_user, '-H',
                     f'PATH={user_path}', nn_path] + nn_args,
                    timeout=None
                )
            else:
                # Not running via sudo, run directly
                result = subprocess.run([nn_path] + nn_args, timeout=None)

            # After NomadNet exits, show status and wait for user
            print()
            if result.returncode != 0:
                self._diagnose_nomadnet_error(result.returncode, sudo_user)
            else:
                print("NomadNet exited normally.")
            print("\nPress Enter to return to MeshForge...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        except KeyboardInterrupt:
            print("\n\nAborted.")
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
    # Small helpers (used by menu dispatch and launch orchestrator)
    # ------------------------------------------------------------------

    def _is_nomadnet_installed(self) -> bool:
        """Check if NomadNet is installed."""
        if shutil.which('nomadnet'):
            return True
        # Check user local bin
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'nomadnet'
        return candidate.exists()

    def _is_nomadnet_running(self) -> bool:
        """Check if NomadNet process is running.

        Uses centralized service_check module when available, with fallback
        to direct pgrep for custom filtering.
        """
        # Try unified check first (faster and standardized)
        if _HAS_SERVICE_CHECK:
            if check_process_running('nomadnet'):
                return True

        # Fallback to direct pgrep with custom filtering
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'bin/nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            # Filter out false positives (our own grep, etc.)
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip() and pid.strip() != str(os.getpid()):
                        return True
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("NomadNet running check failed: %s", e)
            return False

    def _find_nomadnet_binary(self) -> str:
        """Find NomadNet binary path, or show error and return None."""
        nn_path = shutil.which('nomadnet')
        if not nn_path:
            user_home = get_real_user_home()
            candidate = user_home / '.local' / 'bin' / 'nomadnet'
            if candidate.exists():
                nn_path = str(candidate)

        if not nn_path:
            self.ctx.dialog.msgbox(
                "Not Installed",
                "NomadNet is not installed.\n\n"
                "Install with: pipx install nomadnet\n"
                "Or use the Install option from this menu.",
            )
            return None
        return nn_path

    def _get_nomadnet_config_path(self):
        """Find the NomadNet config file.

        Mirrors NomadNet's own resolution order:
          /etc/nomadnetwork/config  ->
          ~/.config/nomadnetwork/config  ->
          ~/.nomadnetwork/config
        """
        user_home = get_real_user_home()

        candidates = [
            Path('/etc/nomadnetwork/config'),
            user_home / '.config' / 'nomadnetwork' / 'config',
            user_home / '.nomadnetwork' / 'config',
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Return the default path (even if it doesn't exist yet)
        return user_home / '.nomadnetwork' / 'config'

    # ------------------------------------------------------------------
    # Delegates to _nomadnet_checks.py
    # ------------------------------------------------------------------

    def _get_rnsd_user(self) -> Optional[str]:
        """Get the OS user running rnsd — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import get_rnsd_user
        return get_rnsd_user(self)

    def _fix_rnsd_user(self, target_user: str) -> bool:
        """Configure rnsd to run as target_user — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import fix_rnsd_user
        return fix_rnsd_user(self, target_user)

    def _wait_for_rns_port(self, max_wait: int = 10) -> bool:
        """Wait for rnsd shared instance — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import wait_for_rns_port
        return wait_for_rns_port(self, max_wait=max_wait)

    def _find_blocking_interfaces(self) -> list:
        """Check for blocking RNS interfaces — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import find_blocking_interfaces_via_handler
        return find_blocking_interfaces_via_handler(self)

    def _get_rns_config_for_user(self) -> str:
        """Get RNS config dir path — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import get_rns_config_for_user
        return get_rns_config_for_user()

    def _fix_user_directory_ownership(self) -> bool:
        """Fix ownership of user directories — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import fix_user_directory_ownership
        return fix_user_directory_ownership(self)

    def _check_rns_for_nomadnet(self) -> bool:
        """Check RNS/rnsd availability — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import check_rns_for_nomadnet
        return check_rns_for_nomadnet(self)

    def _validate_nomadnet_config(self) -> bool:
        """Validate and repair NomadNet config — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import validate_nomadnet_config
        return validate_nomadnet_config(self)

    def _diagnose_nomadnet_error(self, returncode: int, sudo_user: str = None):
        """Analyze NomadNet failure — delegates to _nomadnet_checks."""
        from ._nomadnet_checks import diagnose_nomadnet_error
        diagnose_nomadnet_error(self, returncode, sudo_user)

    # ------------------------------------------------------------------
    # Delegates to _nomadnet_ops.py
    # ------------------------------------------------------------------

    def _nomadnet_status(self):
        """Show comprehensive NomadNet status — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import show_nomadnet_status
        show_nomadnet_status(self)

    def _launch_nomadnet_daemon(self):
        """Start NomadNet daemon — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import launch_nomadnet_daemon
        launch_nomadnet_daemon(self)

    def _stop_nomadnet(self):
        """Stop NomadNet — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import stop_nomadnet
        stop_nomadnet(self)

    def _uninstall_nomadnet(self):
        """Disable NomadNet — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import uninstall_nomadnet
        uninstall_nomadnet(self)

    def _view_nomadnet_logs(self):
        """View NomadNet logs — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import view_nomadnet_logs
        view_nomadnet_logs(self)

    def _view_nomadnet_config(self):
        """View NomadNet config — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import view_nomadnet_config
        view_nomadnet_config(self)

    def _edit_nomadnet_config(self):
        """Edit NomadNet config — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import edit_nomadnet_config
        edit_nomadnet_config(self)

    def _install_nomadnet(self):
        """Install NomadNet — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import install_nomadnet
        install_nomadnet(self)

    def _setup_nomadnet_shared_instance(self, run_as_user: str = None):
        """Post-install message — delegates to _nomadnet_ops."""
        from ._nomadnet_ops import setup_nomadnet_shared_instance
        setup_nomadnet_shared_instance(run_as_user)
