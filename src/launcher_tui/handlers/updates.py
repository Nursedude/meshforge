"""
Updates Handler — One-click software update management.

Converted from updates_mixin.py as part of the mixin-to-registry migration.
"""

import subprocess
import logging
from typing import Dict, Any, Optional, Tuple

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

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
            latest = info.latest or "Unknown"

            lines.append(f"{info.name}:")
            lines.append(f"  Installed: {installed}")
            lines.append(f"  Latest:    {latest}{status}")
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

            success, msg = self._run_update_command(key, info.update_command)
            results.append((info.name, success, msg))

        lines = ["UPDATE RESULTS", "=" * 40, ""]
        for name, success, msg in results:
            status = "SUCCESS" if success else "FAILED"
            lines.append(f"{name}: {status}")
            if not success and msg:
                lines.append(f"  Error: {msg[:60]}...")
            lines.append("")

        self.ctx.dialog.msgbox("Update Complete", "\n".join(lines), width=60)

    def _update_meshtasticd(self):
        """Update meshtasticd package."""
        try:
            versions = _check_all_versions()
            info = versions.get('meshtasticd')
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.ctx.dialog.msgbox("Error", "Could not get meshtasticd version info.")
            return

        if not info.update_available:
            self.ctx.dialog.msgbox(
                "No Update",
                f"meshtasticd is already at the latest version.\n\n"
                f"Installed: {info.installed}\n"
                f"Latest: {info.latest}"
            )
            return

        if not self.ctx.dialog.yesno(
            "Update meshtasticd",
            f"Update meshtasticd from {info.installed} to {info.latest}?\n\n"
            f"Command: {info.update_command}\n\n"
            "Note: The meshtasticd service will be restarted after the update."
        ):
            return

        self.ctx.dialog.infobox("Updating meshtasticd", "Running apt update and upgrade...\n\nThis may take a while...")

        success, msg = self._run_update_command('meshtasticd', info.update_command)

        if success:
            if _HAS_SERVICE_CHECK:
                self.ctx.dialog.infobox("Restarting", "Restarting meshtasticd service...")
                _apply_config_and_restart('meshtasticd')

            self.ctx.dialog.msgbox(
                "Update Complete",
                "meshtasticd has been updated successfully!\n\n"
                "The service has been restarted."
            )
        else:
            self.ctx.dialog.msgbox(
                "Update Failed",
                f"Failed to update meshtasticd.\n\n{msg}"
            )

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
                f"Install command: {info.install_command}\n\n"
                "Install now?"
            ):
                self.ctx.dialog.infobox("Installing", "Installing Meshtastic CLI via pipx...")
                success, msg = self._run_update_command('cli', info.install_command)
                if success:
                    self.ctx.dialog.msgbox("Installed", "Meshtastic CLI installed successfully!")
                else:
                    self.ctx.dialog.msgbox("Failed", f"Installation failed:\n{msg}")
            return

        if not info.update_available:
            self.ctx.dialog.msgbox(
                "No Update",
                f"Meshtastic CLI is already at the latest version.\n\n"
                f"Installed: {info.installed}\n"
                f"Latest: {info.latest}"
            )
            return

        if not self.ctx.dialog.yesno(
            "Update Meshtastic CLI",
            f"Update CLI from {info.installed} to {info.latest}?\n\n"
            f"Command: {info.update_command}"
        ):
            return

        self.ctx.dialog.infobox("Updating CLI", "Running pipx upgrade...")
        success, msg = self._run_update_command('cli', info.update_command)

        if success:
            self.ctx.dialog.msgbox("Update Complete", "Meshtastic CLI updated successfully!")
        else:
            self.ctx.dialog.msgbox("Update Failed", f"Failed to update CLI.\n\n{msg}")

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

        venv_pip = meshforge_dir / 'venv' / 'bin' / 'pip'
        no_venv_marker = meshforge_dir / '.no-venv'

        try:
            if venv_pip.exists() and not no_venv_marker.exists():
                pip_cmd = [str(venv_pip), 'install', '-r', str(requirements_file)]
            else:
                pip_cmd = ['pip3', 'install', '--break-system-packages', '-r', str(requirements_file)]

            result = subprocess.run(
                pip_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            pip_output = result.stdout + result.stderr

            if result.returncode != 0:
                self.ctx.dialog.msgbox(
                    "Pip Install Failed",
                    f"Failed to install dependencies:\n\n{pip_output[:500]}"
                )
                return

        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Error", "Pip install timed out after 5 minutes.")
            return
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Pip install failed: {e}")
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
        except Exception:
            pass

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
