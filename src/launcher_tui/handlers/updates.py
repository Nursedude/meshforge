"""
Updates Handler — One-click software update management.

Converted from updates_mixin.py as part of the mixin-to-registry migration.
"""

import subprocess
import logging
from typing import Dict, Any, Optional, Tuple

from handler_protocol import BaseHandler
from utils.safe_import import safe_import
from utils.pip_install import pip_install
from utils.requirements_floor import read_floor
from updates.meshtasticd_apt import (
    apt_update,
    disable_repo_lines,
    get_meshtasticd_apt_state,
    run_meshtasticd_upgrade,
)

logger = logging.getLogger(__name__)

_check_all_versions, _VersionInfo, _HAS_VERSION_CHECKER = safe_import(
    'updates.version_checker', 'check_all_versions', 'VersionInfo'
)

_apply_config_and_restart, daemon_reload, _sudo_cmd, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'apply_config_and_restart', 'daemon_reload', '_sudo_cmd'
)


class UpdatesHandler(BaseHandler):
    """TUI handler for software updates."""

    handler_id = "updates"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("updates", "Software Updates    One-click updates", None),
        ]

    def execute(self, action):
        if action == "updates":
            self._updates_menu()

    def _updates_menu(self):
        """Main updates menu."""
        if not _HAS_VERSION_CHECKER:
            self.ctx.dialog.msgbox(
                "Updates Unavailable",
                "Version checker module not found.\n\n"
                "Make sure updates/version_checker.py exists."
            )
            return

        while True:
            choices = [
                ("check", "Check for Updates"),
                ("update-all", "Update All Components"),
                ("meshforge", "Update MeshForge"),
                ("meshtasticd", "Update meshtasticd"),
                ("cli", "Update Meshtastic CLI"),
                ("meshtastic-lib", "Update Meshtastic Library"),
                ("firmware", "Update Node Firmware (Info)"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Software Updates",
                "Check and apply software updates:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "check": ("Check Updates", self._check_updates),
                "update-all": ("Update All", self._update_all),
                "meshforge": ("Update MeshForge", self._update_meshforge),
                "meshtasticd": ("Update meshtasticd", self._update_meshtasticd),
                "cli": ("Update CLI", self._update_cli),
                "meshtastic-lib": ("Update Meshtastic Lib", self._update_meshtastic_lib),
                "firmware": ("Firmware Info", self._firmware_info),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _check_updates(self) -> Optional[Dict[str, Any]]:
        """Check for available updates."""
        self.ctx.dialog.infobox("Checking for Updates", "Querying version information...")

        try:
            versions = _check_all_versions()
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check versions:\n{e}")
            return None

        lines = ["SOFTWARE UPDATE STATUS", "=" * 40, ""]

        updates_available = []
        for key, info in versions.items():
            status = ""
            if info.update_available:
                status = " [UPDATE AVAILABLE]"
                updates_available.append(key)

            installed = info.installed or "Not installed"

            lines.append(f"{info.name}:")
            lines.append(f"  Installed: {installed}")
            # Floor-gated components (meshtastic lib/cli) are judged against the
            # REVIEWED fleet baseline, not PyPI-latest — label it as such so the
            # operator isn't told "behind" when the box is at the baseline and
            # PyPI simply moved (the 2026-06-17 phantom-update class).
            floor = getattr(info, "fleet_floor", None)
            if floor:
                lines.append(f"  Fleet floor: {floor}{status}")
                pypi = getattr(info, "pypi_latest", None)
                if pypi and pypi != floor:
                    lines.append(f"  (PyPI {pypi} — reviewed bump only, not auto)")
            else:
                latest = info.latest or "Unknown"
                lines.append(f"  Latest:    {latest}{status}")
            if getattr(info, "notes", None):
                lines.append(f"  Note:      {info.notes}")
            if info.error:
                lines.append(f"  Error:     {info.error}")
            lines.append("")

        if updates_available:
            lines.append("=" * 40)
            lines.append(f"{len(updates_available)} update(s) available!")
            lines.append("Use 'Update All' to install updates.")
        else:
            lines.append("=" * 40)
            lines.append("All components are up to date!")

        self.ctx.dialog.msgbox(
            "Version Status",
            "\n".join(lines),
            width=60,
            height=20
        )

        return versions

    def _update_all(self):
        """Update all components that have updates available."""
        self.ctx.dialog.infobox("Checking Updates", "Checking which components need updates...")

        try:
            versions = _check_all_versions()
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check versions:\n{e}")
            return

        updates_needed = []
        for key, info in versions.items():
            if info.update_available and info.update_command:
                if key not in ('firmware', 'meshforge'):
                    updates_needed.append((key, info))

        if not updates_needed:
            self.ctx.dialog.msgbox(
                "No Updates",
                "All components are up to date!\n\n"
                "No automatic updates available."
            )
            return

        update_list = "\n".join([f"  - {info.name}" for _, info in updates_needed])
        if not self.ctx.dialog.yesno(
            "Confirm Updates",
            f"The following components will be updated:\n\n{update_list}\n\n"
            "This may take a few minutes. Continue?"
        ):
            return

        results = []
        for key, info in updates_needed:
            self.ctx.dialog.infobox(
                f"Updating {info.name}",
                f"Running: {info.update_command}\n\nPlease wait..."
            )

            # The meshtastic library must go through the pip helper, not the
            # raw update_command: the helper adds --break-system-packages (PEP
            # 668 / externally-managed-environment on Debian/RPi) and does the
            # rnsd dual-install (#24). The raw 'pip3 install --upgrade
            # meshtastic' string handles neither — it failed Update All with
            # "externally-managed-environment" while the standalone updater
            # (which already uses the helper) worked.
            if key == 'meshtastic_lib':
                success, msg = self._pip_install_meshtastic(upgrade=True)
            elif key == 'cli':
                # pipx upgrade must run in the pipx that owns the resolved CLI,
                # not the sudo'd (root) context — read/write split, #24 sibling.
                success, msg = self._pipx_upgrade_cli()
            elif key == 'meshtasticd':
                # apt path with the re-derived success gate (a pinned/held
                # package never reaches here — update_available is False).
                success, msg = run_meshtasticd_upgrade()
                if success and _HAS_SERVICE_CHECK:
                    restart_ok, restart_msg = _apply_config_and_restart('meshtasticd')
                    if not restart_ok:
                        success = False
                        msg = (f"package updated ({msg}) but the service "
                               f"restart FAILED: {restart_msg} — check "
                               "meshtasticd in Service Control")
            else:
                success, msg = self._run_update_command(key, info.update_command)
            results.append((info.name, success, msg))

        lines = ["UPDATE RESULTS", "=" * 40, ""]
        for name, success, msg in results:
            status = "SUCCESS" if success else "FAILED"
            lines.append(f"{name}: {status}")
            if not success and msg:
                lines.append(f"  Error: {msg[:200]}")
            lines.append("")

        self.ctx.dialog.msgbox("Update Complete", "\n".join(lines), width=60)

    def _update_meshtasticd(self):
        """Update meshtasticd via the apt state machine (2026-07-10 audit).

        Reads the truth apt acts on (candidate, hold, dry-run installability)
        instead of GitHub tags, walks the operator through the real blockers
        (deliberate pin → explicit unhold; mismatched OBS repo → guided
        repair), and only ever claims success from a re-derived version.
        """
        self.ctx.dialog.infobox("meshtasticd", "Reading apt package state...")
        state = get_meshtasticd_apt_state(simulate=True)

        if not state.apt_available:
            self.ctx.dialog.msgbox(
                "apt Unavailable",
                "This system does not use apt — meshtasticd updates must be\n"
                "managed by your platform's package manager."
            )
            return
        if state.error and not (state.installed or state.candidate):
            self.ctx.dialog.msgbox(
                "Error", f"Could not read meshtasticd apt state:\n\n{state.error}")
            return
        if not state.installed:
            self.ctx.dialog.msgbox(
                "Not Installed",
                "meshtasticd is not installed via apt on this box.\n\n"
                "Install it from the meshtasticd Service menu or the\n"
                "official Meshtastic apt repository."
            )
            return

        # Gate on the dpkg-derived comparison, not string inequality — a
        # candidate that merely DIFFERS (e.g. a manually installed .deb newer
        # than any repo) is not an update.
        if not state.update_available:
            self.ctx.dialog.msgbox(
                "No Update",
                "meshtasticd is already at the newest version apt can\n"
                f"install on this box.\n\n"
                f"Installed:     {state.installed}\n"
                f"apt candidate: {state.candidate or 'unknown'}"
                + ("\n\nNote: package is on apt hold (deliberate pin)."
                   if state.held else "")
            )
            return

        # Deliberate pin: require an explicit, default-no decision to lift it.
        unhold = False
        if state.held:
            if not self.ctx.dialog.yesno(
                "meshtasticd is PINNED",
                f"meshtasticd is on apt hold at {state.installed} — on this\n"
                "fleet a hold is a DELIBERATE pin (canary/baseline roll).\n\n"
                f"apt candidate: {state.candidate}\n\n"
                "Unhold, update to the candidate, then RE-APPLY the hold at\n"
                "the new version?",
                default_no=True,
            ):
                return
            unhold = True

        # Dry-run said the candidate cannot install (the mismatched-repo
        # class): show apt's real error and offer the guided repair.
        if state.candidate_installable is False:
            if not self._repair_broken_candidate(state):
                return
            # Re-derive after the repair — never proceed on the old state.
            self.ctx.dialog.infobox("meshtasticd", "Re-checking apt state after repair...")
            state = get_meshtasticd_apt_state(simulate=True)
            if state.candidate_installable is False or not state.candidate:
                self.ctx.dialog.msgbox(
                    "Still Blocked",
                    "The candidate is still not installable after the repair:\n\n"
                    f"{(state.blocking_detail or 'unknown blocker')[:500]}"
                )
                return

        if not self.ctx.dialog.yesno(
            "Update meshtasticd",
            f"Update meshtasticd {state.installed} -> {state.candidate}?\n\n"
            "Runs: apt-get update, then\n"
            "apt-get install --only-upgrade -y meshtasticd\n"
            + ("(hold will be lifted for the update and re-applied)\n"
               if unhold else "")
            + "\nNote: The meshtasticd service will be restarted after the update."
        ):
            return

        self.ctx.dialog.infobox(
            "Updating meshtasticd",
            "Running apt-get update + only-upgrade...\n\nThis may take a while...")

        success, msg = run_meshtasticd_upgrade(unhold=unhold, rehold=unhold)

        if success:
            if _HAS_SERVICE_CHECK:
                self.ctx.dialog.infobox("Restarting", "Restarting meshtasticd service...")
                # Honest-signal: gate the "restarted" claim on the real result.
                restart_ok, restart_msg = _apply_config_and_restart('meshtasticd')
                self.ctx.report_action(
                    restart_ok,
                    "Update Complete",
                    f"{msg}\n\n"
                    "The service has been restarted.",
                    "Updated — Restart FAILED",
                    f"{msg}\n\n"
                    "...but meshtasticd did NOT restart cleanly:\n"
                    f"{restart_msg}\n\n"
                    "The radio daemon may be down or running the old version.\n"
                    "Check meshtasticd in Service Control.",
                )
            else:
                # No service-control available — do not imply a restart happened.
                self.ctx.dialog.msgbox(
                    "Update Applied",
                    "The meshtasticd package was updated.\n\n"
                    "Service control is unavailable in this environment, so "
                    "meshtasticd was NOT restarted automatically. Restart it "
                    "from the Service Management menu to load the new version."
                )
        else:
            self.ctx.dialog.msgbox(
                "Update Failed",
                f"Failed to update meshtasticd.\n\n{msg}"
            )

    def _repair_broken_candidate(self, state) -> bool:
        """Guided fix for an unsatisfiable apt candidate.

        The known class: a distro-mismatched Meshtastic OBS repo (e.g. a
        ``Debian_Testing`` line on a Debian 13 box) publishes the SAME version
        string built against a newer libc; apt binds the candidate to the
        uninstallable stanza and every upgrade dies with "held broken
        packages". Repair = comment out the mismatched line(s) (backup kept)
        and refresh apt, IN-APP (MF018). Returns True when a repair was
        applied and the caller should re-derive state.
        """
        blocker = (state.blocking_detail or 'unknown dependency problem')[:400]

        if not state.mismatched_repos:
            self.ctx.dialog.msgbox(
                "Candidate Not Installable",
                f"apt cannot install {state.candidate}:\n\n{blocker}\n\n"
                "No distro-mismatched Meshtastic repo was found, so this\n"
                "needs manual investigation:\n"
                "  apt-cache policy meshtasticd\n"
                "  ls /etc/apt/sources.list.d/"
            )
            return False

        repo_list = "\n".join(f"  {r.path}:{r.lineno}" for r in state.mismatched_repos)
        if not self.ctx.dialog.yesno(
            "Mismatched Repo Detected",
            f"apt cannot install {state.candidate}:\n\n{blocker[:200]}\n\n"
            "Likely cause — Meshtastic repo(s) for the WRONG distro release\n"
            "(their builds need newer system libraries than this box has):\n\n"
            f"{repo_list}\n\n"
            "Disable these line(s) (a .meshforge-bak backup is kept) and\n"
            "refresh apt?"
        ):
            return False

        self.ctx.dialog.infobox("Repairing", "Disabling mismatched repo line(s)...")
        ok, detail = disable_repo_lines(state.mismatched_repos)
        if not ok:
            self.ctx.dialog.msgbox(
                "Repair Failed", f"Could not disable the repo line(s):\n\n{detail}")
            return False

        self.ctx.dialog.infobox("Repairing", "Refreshing apt package lists...")
        ok, detail = apt_update()
        if not ok:
            self.ctx.dialog.msgbox(
                "apt Refresh Problem",
                "Repo line(s) disabled, but the apt refresh reported:\n\n"
                f"{detail[:400]}\n\n"
                "Continuing with the freshest lists available."
            )
        return True

    def _update_cli(self):
        """Update Meshtastic CLI."""
        try:
            versions = _check_all_versions()
            info = versions.get('cli')
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.ctx.dialog.msgbox("Error", "Could not get CLI version info.")
            return

        if not info.installed:
            if self.ctx.dialog.yesno(
                "Install Meshtastic CLI",
                "Meshtastic CLI is not installed.\n\n"
                "It will be installed with pipx, pinned to the fleet\n"
                f"baseline ({info.fleet_floor or 'requirements/core.txt'}), "
                "in the operator's pipx home.\n\n"
                "Install now?"
            ):
                self.ctx.dialog.infobox("Installing", "Installing Meshtastic CLI via pipx...")
                success, msg = self._pipx_upgrade_cli()
                if success:
                    self.ctx.dialog.msgbox("Installed", "Meshtastic CLI installed successfully!")
                else:
                    self.ctx.dialog.msgbox("Failed", f"Installation failed:\n{msg}")
            return

        # Ownership check BEFORE any version verdict: a pip --user script
        # shadowing the pipx shim means pipx upgrades a venv that never runs —
        # every future update silently no-ops (the read/write split at the
        # binary level, found live 2026-07-10).
        from utils.cli import diagnose_meshtastic_cli
        diag = diagnose_meshtastic_cli()
        if diag['kind'] == 'pip-script':
            if self.ctx.dialog.yesno(
                "CLI Install Fragmented",
                f"The meshtastic CLI at\n  {diag['path']}\n"
                "is a pip console script, NOT the pipx-managed shim — so\n"
                "pipx upgrades land in a venv this script never executes,\n"
                "and CLI updates silently do nothing.\n\n"
                "Repair now? (pipx install --force at the fleet baseline —\n"
                "makes pipx the single owner of the CLI)"
            ):
                self.ctx.dialog.infobox("Repairing CLI", "Running pipx install --force...")
                success, msg = self._pipx_upgrade_cli()
                if success:
                    self.ctx.dialog.msgbox(
                        "Repaired",
                        "The CLI is now pipx-owned at the fleet baseline.")
                else:
                    self.ctx.dialog.msgbox("Repair Failed", f"{msg}")
            return

        if not info.update_available:
            self.ctx.dialog.msgbox(
                "No Update",
                f"Meshtastic CLI is at the fleet baseline.\n\n"
                f"Installed: {info.installed}\n"
                f"Baseline:  {info.latest}"
            )
            return

        if not self.ctx.dialog.yesno(
            "Update Meshtastic CLI",
            f"Update CLI from {info.installed} to the fleet baseline "
            f"{info.latest}?\n\n"
            "Runs: pipx install --force meshtastic==<baseline>\n"
            "(in the pipx that owns the running CLI)"
        ):
            return

        self.ctx.dialog.infobox("Updating CLI", "Running pipx install --force...")
        success, msg = self._pipx_upgrade_cli()

        if success:
            self.ctx.dialog.msgbox("Update Complete", "Meshtastic CLI updated successfully!")
        else:
            self.ctx.dialog.msgbox("Update Failed", f"Failed to update CLI.\n\n{msg}")

    def _update_meshtastic_lib(self):
        """Update Meshtastic Python library (protobuf definitions)."""
        try:
            versions = _check_all_versions()
            info = versions.get('meshtastic_lib')
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.ctx.dialog.msgbox("Error", "Could not get library version info.")
            return

        if not info.installed:
            if self.ctx.dialog.yesno(
                "Install Meshtastic Library",
                "Meshtastic Python library is not installed.\n\n"
                "This is required for the protobuf gateway client.\n\n"
                "Install now?"
            ):
                self.ctx.dialog.infobox(
                    "Installing",
                    "Installing Meshtastic Python library..."
                )
                success, msg = self._pip_install_meshtastic()
                if success:
                    self.ctx.dialog.msgbox(
                        "Installed",
                        "Meshtastic library installed successfully!"
                    )
                else:
                    self.ctx.dialog.msgbox("Failed", f"Installation failed:\n{msg}")
            return

        if not info.update_available:
            self.ctx.dialog.msgbox(
                "No Update",
                f"Meshtastic library is at the latest version.\n\n"
                f"Installed: {info.installed}\n"
                f"Latest: {info.latest}"
            )
            return

        # Check for rnsd dual-install scenario
        from pathlib import Path
        install_note = ""
        rnsd_interface = Path('/etc/reticulum/interfaces/Meshtastic_Interface.py')
        if rnsd_interface.exists():
            install_note = (
                "\n\nNote: Meshtastic_Interface.py detected.\n"
                "Will also install system-wide for rnsd compatibility."
            )

        if not self.ctx.dialog.yesno(
            "Update Meshtastic Library",
            f"Update library from {info.installed} to {info.latest}?\n\n"
            f"This updates the protobuf definitions used by\n"
            f"the gateway bridge.{install_note}"
        ):
            return

        self.ctx.dialog.infobox(
            "Updating Library",
            "Upgrading Meshtastic Python library..."
        )
        success, msg = self._pip_install_meshtastic(upgrade=True)

        if success:
            self.ctx.dialog.msgbox(
                "Update Complete",
                "Meshtastic library updated successfully!\n\n"
                "New protobuf definitions are now available.\n"
                "Restart the gateway bridge to use them."
            )
        else:
            self.ctx.dialog.msgbox(
                "Update Failed",
                f"Failed to update library.\n\n{msg}"
            )

    def _pipx_upgrade_cli(self) -> Tuple[bool, str]:
        """Install/upgrade/repair the meshtastic CLI: floor-pinned, owner-aware.

        Two disciplines meet here (feedback_version_env_rigor):

        1. TARGET the reviewed fleet baseline, never PyPI-latest. The checker
           judges the CLI against requirements/core.txt's floor, so the writer
           must land exactly there — a bare `pipx upgrade` overshoots the
           reviewed pin the moment PyPI moves. No readable floor → refuse
           loudly rather than blind-upgrade.
        2. Run pipx as the OWNER of what actually runs. Under sudo, a bare
           pipx call writes ROOT's pipx home while the resolved CLI lives in
           the operator's — the upgrade no-ops forever (the read/write split).

        `pipx install --force meshtastic==<floor>` also REPAIRS the
        fragmented-shim case (a pip --user script shadowing the pipx shim):
        pipx rewrites ~/.local/bin/meshtastic to point at its own venv,
        making pipx the single owner again.
        """
        import os
        import pwd
        from utils.cli import find_meshtastic_cli

        floor = read_floor('meshtastic')
        if not floor:
            return False, (
                "fleet baseline (requirements/core.txt) unreadable — "
                "refusing a blind upgrade past the reviewed pin"
            )

        cli_path = find_meshtastic_cli()
        if cli_path:
            try:
                owner_uid = os.stat(cli_path).st_uid
                owner_name = pwd.getpwuid(owner_uid).pw_name
            except (OSError, KeyError) as e:
                return False, f"could not resolve CLI owner ({cli_path}): {e}"
        else:
            # Fresh install: no binary to own yet — target the OPERATOR's
            # pipx home (SUDO_USER under sudo), never root's by accident.
            from utils.paths import get_real_username
            owner_name = get_real_username()
            try:
                owner_uid = pwd.getpwnam(owner_name).pw_uid
            except KeyError as e:
                return False, f"could not resolve user {owner_name}: {e}"

        base = ['pipx', 'install', '--force', f'meshtastic=={floor}']
        if owner_uid == os.geteuid():
            cmd = base
        else:
            # Drop to the owning user so pipx uses THEIR pipx home
            # (~/.local/share/pipx) rather than the current (root) one.
            cmd = ['sudo', '-u', owner_name, '-H'] + base

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                logger.info("Installed meshtastic==%s CLI in %s's pipx", floor, owner_name)
                return True, result.stdout
            return False, result.stderr or result.stdout or f"Exit code: {result.returncode}"
        except subprocess.TimeoutExpired:
            return False, "pipx install timed out after 5 minutes"
        except Exception as e:
            return False, str(e)

    def _pip_install_meshtastic(self, upgrade: bool = False) -> Tuple[bool, str]:
        """Install or upgrade the meshtastic Python library.

        Routes through the ONE hardened pip helper (`utils.pip_install`): it
        resolves venv-vs-system via the SAME SSOT the version reader uses
        (`get_meshforge_venv_dir`, so read and write target one interpreter —
        the read/write split, feedback_version_env_rigor), ensures pip exists,
        checks the return code, and verifies meshtastic actually IMPORTS
        afterward. The Issue-#24 dual-install for rnsd's root Python is a second
        verified call; its failure is surfaced honestly, never swallowed.
        """
        from pathlib import Path

        # Target the reviewed fleet baseline, not PyPI-latest: the checker
        # flags "update available" only when BELOW the floor, so the writer
        # must land exactly ON it — a bare `--upgrade meshtastic` overshoots
        # the reviewed pin. Refuse a blind upgrade when the floor is
        # unreadable; a bare bootstrap INSTALL (nothing present yet) may
        # still proceed unpinned.
        floor = read_floor('meshtastic')
        if floor:
            spec = f'meshtastic=={floor}'
        elif upgrade:
            return False, (
                "fleet baseline (requirements/core.txt) unreadable — "
                "refusing a blind upgrade past the reviewed pin"
            )
        else:
            spec = 'meshtastic'

        # Primary install into MeshForge's interpreter (venv if present, else
        # system pip with --break-system-packages auto-applied).
        primary = pip_install([spec], upgrade=upgrade,
                              verify_import='meshtastic', timeout=120)
        if not primary.ok:
            return False, primary.detail

        # Dual-install for rnsd (Issue #24): rnsd runs as root and needs
        # meshtastic in root's system Python, where pipx/--user installs don't
        # reach. verify_import='meshtastic' with sudo runs the root-import check.
        rnsd_interface = Path('/etc/reticulum/interfaces/Meshtastic_Interface.py')
        if rnsd_interface.exists():
            self.ctx.dialog.infobox(
                "System Install",
                "Also installing system-wide for rnsd..."
            )
            rnsd_result = pip_install(
                [spec], python='python3', sudo=True, break_system=True,
                ignore_installed=True, upgrade=upgrade,
                verify_import='meshtastic', timeout=120,
            )
            if not rnsd_result.ok:
                # subprocess.run does NOT raise on nonzero exit, and "installed"
                # is not "importable" — both traps are closed by the helper, so
                # a real rnsd-copy failure reaches here instead of passing as
                # full success (Issue #24 defeated).
                logger.warning(
                    "rnsd system-wide meshtastic install failed: %s", rnsd_result.detail
                )
                self.ctx.dialog.msgbox(
                    "rnsd Install Incomplete",
                    "The Meshtastic library updated for MeshForge, but the\n"
                    "system-wide copy that rnsd needs did NOT install.\n\n"
                    "Impact: the RNS gateway bridge may not pick up the new\n"
                    "protobuf definitions until this lands (Issue #24).\n\n"
                    "Fix: re-run this update from the Updates menu. If it\n"
                    "keeps failing, the error below shows why:\n\n"
                    f"{rnsd_result.detail[:400]}"
                )

        return True, primary.stdout or primary.detail

    def _firmware_info(self):
        """Show firmware update information."""
        try:
            versions = _check_all_versions()
            info = versions.get('firmware')
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.ctx.dialog.msgbox("Error", "Could not get firmware version info.")
            return

        installed = info.installed or "Unknown (connect radio)"
        latest = info.latest or "Unknown"
        update_needed = " [UPDATE AVAILABLE]" if info.update_available else ""

        self.ctx.dialog.msgbox(
            "Node Firmware",
            f"NODE FIRMWARE STATUS{update_needed}\n"
            f"{'=' * 40}\n\n"
            f"Installed: {installed}\n"
            f"Latest:    {latest}\n\n"
            f"{'=' * 40}\n"
            "FIRMWARE UPDATE OPTIONS:\n\n"
            "1. Web Flasher (recommended):\n"
            "   https://flasher.meshtastic.org\n\n"
            "2. Meshtastic Flasher (desktop app):\n"
            "   pip install meshtastic-flasher\n\n"
            "3. meshtastic CLI:\n"
            "   meshtastic --flash\n\n"
            "Note: Backup your node config before updating!\n"
            "Use: meshtastic --export-config > backup.yaml",
            width=60,
            height=22
        )

    def _update_meshforge(self):
        """Update MeshForge itself (git pull + pip install)."""
        from pathlib import Path
        from utils.paths import get_real_user_home

        meshforge_dir = Path(__file__).parent.parent.parent.parent

        git_dir = meshforge_dir / '.git'
        if not git_dir.exists():
            self.ctx.dialog.msgbox(
                "Not a Git Repository",
                "MeshForge is not installed via git.\n\n"
                "To update, re-run the installer:\n\n"
                "curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo bash"
            )
            return

        if not self.ctx.dialog.yesno(
            "Update MeshForge",
            "This will:\n\n"
            "1. Pull latest code from GitHub (git pull)\n"
            "2. Install/update Python dependencies\n"
            "3. Update systemd service files\n\n"
            "Continue?"
        ):
            return

        self.ctx.dialog.infobox("Updating MeshForge", "Step 1/3: Pulling latest code from GitHub...")

        try:
            result = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                cwd=str(meshforge_dir),
                capture_output=True,
                text=True,
                timeout=60
            )
            git_output = result.stdout + result.stderr

            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Git Pull Failed",
                    f"Failed to pull updates:\n\n{git_output[:500]}"
                )
                return

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Git pull timed out after 60 seconds.")
            return
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Git pull failed: {e}")
            return

        self.ctx.dialog.infobox("Updating MeshForge", "Step 2/3: Installing Python dependencies...")

        requirements_file = meshforge_dir / 'requirements.txt'
        if not requirements_file.exists():
            self.ctx.dialog.msgbox("Error", "requirements.txt not found!")
            return

        # Route through the hardened helper: SSOT venv-vs-system resolution
        # (`get_meshforge_venv_dir`) + ensure_pip + return-code check, replacing
        # the hand-rolled venv gate that bypassed the SSOT.
        result = pip_install([], requirements_file=requirements_file, timeout=300)
        if not result.ok:
            self.ctx.dialog.msgbox(
                "Pip Install Failed",
                f"Failed to install dependencies:\n\n{result.detail[:500]}"
            )
            return

        self.ctx.dialog.infobox("Updating MeshForge", "Step 3/3: Updating service files...")

        svc_msgs = []
        try:
            svc_src = meshforge_dir / 'scripts' / 'meshforge.service'
            svc_dst = Path('/etc/systemd/system/meshforge.service')
            if svc_src.exists() and svc_dst.exists():
                import shutil
                shutil.copy2(str(svc_src), str(svc_dst))
                svc_msgs.append("meshforge.service")

            user_svc_dir = get_real_user_home() / '.config' / 'systemd' / 'user'
            templates_dir = meshforge_dir / 'templates' / 'systemd'
            if templates_dir.exists():
                user_svc_dir.mkdir(parents=True, exist_ok=True)
                for tmpl in templates_dir.glob('*-user.service'):
                    svc_name = tmpl.name.replace('-user.service', '.service')
                    dst = user_svc_dir / svc_name
                    import shutil
                    shutil.copy2(str(tmpl), str(dst))
                    svc_msgs.append(svc_name)

            if svc_msgs:
                daemon_reload()
        except (OSError, PermissionError) as e:
            svc_msgs.append(f"(warning: {e})")
        except Exception as e:
            # Surface unexpected service-step failures in the completion dialog
            # instead of "Update Complete" implying the step ran clean (S7).
            svc_msgs.append(f"(service update error: {e})")

        svc_info = ""
        if svc_msgs:
            svc_info = f"\nServices updated: {', '.join(svc_msgs)}\n"

        self.ctx.dialog.msgbox(
            "Update Complete",
            "MeshForge has been updated!\n\n"
            f"Git: {git_output.strip()[:200]}\n"
            f"{svc_info}\n"
            "Please restart MeshForge to apply changes.\n\n"
            "Run: meshforge"
        )

    def _run_update_command(self, component: str, command: str) -> Tuple[bool, str]:
        """Execute an update command safely."""
        try:
            import shlex
            if '|' in command or '>' in command or '&&' in command:
                cmd_args = ['bash', '-c', command]
            else:
                cmd_args = shlex.split(command)
            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode == 0:
                logger.info(f"Updated {component} successfully")
                return True, result.stdout

            error_msg = result.stderr or result.stdout or f"Exit code: {result.returncode}"
            logger.error(f"Failed to update {component}: {error_msg}")
            return False, error_msg

        except subprocess.TimeoutExpired:
            logger.error(f"Update timeout for {component}")
            return False, "Update timed out after 5 minutes"
        except Exception as e:
            logger.error(f"Update error for {component}: {e}")
            return False, str(e)
