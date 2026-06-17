"""RNS prerequisite checks and NomadNet config validation.

Validates RNS/rnsd availability before launching NomadNet using
the pure-logic readiness gate in _nomadnet_prelaunch.py.
Also validates NomadNet config for required sections.

Extracted from nomadnet.py for file size compliance (CLAUDE.md #6).
Simplified from 711 lines to ~180 lines — repair logic moved to
_rns_repair.py and rns_diagnostics handler.
"""

import logging
import os
import subprocess
from pathlib import Path

from utils.paths import get_real_user_home

from utils.safe_import import safe_import

get_rns_shared_instance_info, _ = safe_import(
    'utils.service_check', 'get_rns_shared_instance_info'
)

from handlers._nomadnet_prelaunch import check_rns_readiness

logger = logging.getLogger(__name__)


class NomadNetRNSChecksMixin:
    """Mixin providing RNS prerequisite checks for NomadNet.

    Expects the host class to provide:
        self.ctx.dialog   — DialogBackend for TUI dialogs
        self._get_rnsd_user() -> Optional[str]
        self._get_rns_diagnostics_handler() -> handler or None
        self._get_nomadnet_config_path() -> Optional[Path]
    """

    def _get_nomadnet_venv_python(self, nn_path: str) -> str:
        """Derive NomadNet's pipx venv Python path from the binary.

        NomadNet installed via pipx lives in a venv like:
          ~/.local/pipx/venvs/nomadnet/bin/nomadnet
        The Python interpreter is at:
          ~/.local/pipx/venvs/nomadnet/bin/python3

        Returns the path string, or None if not found.
        """
        try:
            nn_resolved = Path(nn_path).resolve()
            venv_bin = nn_resolved.parent
            candidate = venv_bin / 'python3'
            if candidate.exists():
                return str(candidate)
            # Try python (no version suffix)
            candidate = venv_bin / 'python'
            if candidate.exists():
                return str(candidate)
        except (OSError, ValueError) as e:
            logger.debug("Cannot resolve NomadNet venv Python: %s", e)
        return None

    def _check_rns_for_nomadnet(self, nn_path: str = None) -> bool:
        """Check that RNS/rnsd is available for NomadNet launch.

        Uses the pure-logic readiness gate from _nomadnet_prelaunch.py.
        When blocked, offers diagnostics redirect instead of inline repair.
        Also runs rnsd-user-match and Meshtastic-interface checks so every
        launch path (textui / daemon / interactive) inherits them.

        Args:
            nn_path: Path to NomadNet binary (unused, kept for API compat).

        Returns True if OK to proceed, False if user cancelled.
        """
        # 0. User-mismatch warning (only fires when rnsd/nomadnet users differ)
        if not self._check_rnsd_user_match():
            return False

        # 0b. Meshtastic RNS-interface pre-launch probe
        if not self._check_mesh_iface_before_launch():
            return False

        # 1. Gather state (read-only, no mutations)
        rnsd_user = self._get_rnsd_user()
        # The shared instance socket is @rns/<instance_name>; resolve the name
        # rnsd actually uses (canonical, sudo/active-config-aware) instead of
        # hardcoding 'default' — a non-default box was probed on the wrong
        # socket → false health verdict (#72 class). Falls back to 'default'.
        from utils.paths import ReticulumPaths
        rns_instance_name = ReticulumPaths.get_configured_instance_name()
        shared_info = {}
        if get_rns_shared_instance_info:
            try:
                shared_info = get_rns_shared_instance_info(rns_instance_name) or {}
            except Exception as e:
                logger.debug("Shared instance info check failed: %s", e)

        sudo_user = os.environ.get('SUDO_USER')

        # 1b. Probe rnsd health via shared instance data exchange
        # Note: rnstatus uses RPC which may fail even when rnsd is healthy
        # (e.g., enable_transport=False). Instead, we verify the shared
        # instance socket accepts connections and responds to data.
        rnsd_healthy = None
        if rnsd_user and shared_info.get('available', False):
            # Canonical instance-aware, #68-bounded connect probe
            # (utils.rns_init) — replaces the old hardcoded default-instance
            # socket so a box on a non-default instance_name isn't reported
            # falsely unhealthy, and a wedged-connect is distinguished from a
            # refusal in the logs.
            from utils.rns_init import _probe_shared_instance_connect
            rnsd_healthy = _probe_shared_instance_connect(rns_instance_name, 2.0)

        # 2. Pure decision
        result = check_rns_readiness(
            rnsd_running=bool(rnsd_user),
            shared_instance_available=shared_info.get('available', False),
            rnsd_healthy=rnsd_healthy,
            rnsd_user=rnsd_user,
            launch_user=sudo_user,
        )

        # 3. Handle degraded rnsd (running but unhealthy)
        if result.can_launch and result.rnsd_healthy is False:
            return self._handle_degraded_rnsd(result)

        # 4. Show warning if applicable (but still allow launch)
        if result.can_launch:
            if result.warning:
                self.ctx.dialog.msgbox("RNS Warning", result.warning)
            return True

        # 4. Blocked — single dialog, no repair, just redirect
        choice = self.ctx.dialog.menu(
            "RNS Not Ready",
            f"{result.reason}\n\n{result.suggestion}",
            [
                ("diagnostics", "Open RNS Diagnostics (recommended)"),
                ("continue", "Launch anyway"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "diagnostics":
            diag = self._get_rns_diagnostics_handler()
            if diag:
                try:
                    diag._rns_diagnostics()
                except Exception as e:
                    logger.warning("RNS diagnostics failed: %s", e)
            else:
                self.ctx.dialog.msgbox(
                    "Not Available",
                    "RNS Diagnostics handler not found.\n\n"
                    "Start rnsd from Service Control, then re-run diagnostics.",
                )
            return False  # Return to NomadNet menu after diagnostics

        return choice == "continue"

    def _handle_degraded_rnsd(self, result) -> bool:
        """Handle degraded rnsd — offer restart, diagnostics, or continue.

        Called when rnsd is running and shared instance is up but rnstatus
        failed, indicating internal rnsd crash (e.g., RNS 1.1.3 race).

        Returns True if OK to proceed with NomadNet launch.
        """
        choice = self.ctx.dialog.menu(
            "RNS Degraded",
            result.warning,
            [
                ("restart", "Restart rnsd (recommended)"),
                ("diagnostics", "Open RNS Diagnostics"),
                ("continue", "Launch anyway"),
                ("cancel", "Cancel"),
            ],
        )

        if choice == "restart":
            from handlers._rns_repair import restart_rnsd
            self.ctx.dialog.infobox("Restarting", "Restarting rnsd...")
            if restart_rnsd():
                self.ctx.dialog.msgbox(
                    "rnsd Restarted",
                    "rnsd restarted successfully.\n"
                    "Shared instance is available.",
                )
                return True
            self.ctx.dialog.msgbox(
                "Restart Failed",
                "rnsd restart did not fully recover.\n\n"
                "Use RNS Diagnostics for a full repair.",
            )
            return False

        if choice == "diagnostics":
            diag = self._get_rns_diagnostics_handler()
            if diag:
                try:
                    diag._rns_diagnostics()
                except Exception as e:
                    logger.warning("RNS diagnostics failed: %s", e)
            return False

        return choice == "continue"

    def _validate_nomadnet_config(self) -> bool:
        """Validate and repair NomadNet config if needed.

        NomadNet requires a [textui] section when running in text UI mode.
        If the config exists but lacks this section (e.g., old config from
        before [textui] was required), NomadNet will crash with KeyError.

        This function checks for and adds a minimal [textui] section if missing.

        Returns:
            True to proceed with launch, False if user cancelled.
        """
        config_path = self._get_nomadnet_config_path()
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

        if not self.ctx.dialog.yesno(
            "Config Repair Needed",
            f"Your NomadNet config is missing the [textui] section\n"
            f"required for text UI mode.\n\n"
            f"Config: {config_path}\n\n"
            f"Add a default [textui] section now?",
        ):
            return self.ctx.dialog.yesno(
                "Proceed Anyway?",
                "Without [textui], NomadNet will crash.\n\n"
                "Continue anyway?",
            )

        # Add minimal [textui] section (identical string, compact literal)
        textui_section = (
            "\n\n[textui]\n# Text UI configuration added by MeshForge\n"
            "intro_time = 1\ntheme = dark\ncolormode = 256\n"
            "glyphs = unicode\nmouse_enabled = yes\nhide_guide = no\n"
        )
        try:
            # Append [textui] section to config
            with open(config_path, 'a') as f:
                f.write(textui_section)
            logger.info(f"Added [textui] section to {config_path}")

            # Fix ownership if running via sudo; warn on chown failure so we
            # don't claim the UI works when the config is left root-owned and
            # unreadable to NomadNet (#74 / MF018 false instruction).
            sudo_user = os.environ.get('SUDO_USER')
            chown_ok = True
            if sudo_user and sudo_user != 'root':
                chown_ok = subprocess.run(
                    ['chown', f'{sudo_user}:{sudo_user}', str(config_path)],
                    capture_output=True, timeout=10
                ).returncode == 0
            warn = "" if chown_ok else (
                f"\n\nWARNING: ownership not restored to {sudo_user}; config may be "
                f"root-owned/unreadable. Fix: sudo chown {sudo_user}:{sudo_user} {config_path}")
            self.ctx.dialog.msgbox(
                "Config Updated",
                f"Added [textui] section to config.\n\n"
                f"NomadNet text UI should now work." + warn,
            )
            return True
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox(
                "Config Update Failed",
                f"Could not update config:\n  {config_path}\n\n"
                f"Error: {e}\n\n"
                f"Add [textui] section manually or delete config\n"
                f"and let NomadNet recreate it.",
            )
            return False

    # _check_rnsd_user_match, _check_mesh_iface_before_launch,
    # _mesh_iface_subtitle_state, _meshtastic_blockers
    #   -> provided by NomadNetIfaceChecksMixin (_nomadnet_iface_checks.py)
