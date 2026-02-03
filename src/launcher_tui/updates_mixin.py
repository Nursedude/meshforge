"""
Updates Mixin - One-click software update management for MeshForge TUI.

Provides:
- Version checking for all mesh components
- One-click update execution
- Update status display
"""

import subprocess
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Import version checker
try:
    from updates.version_checker import check_all_versions, VersionInfo
    _HAS_VERSION_CHECKER = True
except ImportError:
    _HAS_VERSION_CHECKER = False
    VersionInfo = None

# Import service check for restart after updates
try:
    from utils.service_check import apply_config_and_restart
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False


class UpdatesMixin:
    """One-click software update management."""

    def _updates_menu(self):
        """Main updates menu - check and apply software updates."""
        if not _HAS_VERSION_CHECKER:
            self.dialog.msgbox(
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

            choice = self.dialog.menu(
                "Software Updates",
                "Check and apply software updates:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "check":
                self._check_updates()
            elif choice == "update-all":
                self._update_all()
            elif choice == "meshforge":
                self._update_meshforge()
            elif choice == "meshtasticd":
                self._update_meshtasticd()
            elif choice == "cli":
                self._update_cli()
            elif choice == "firmware":
                self._firmware_info()

    def _check_updates(self) -> Optional[Dict[str, Any]]:
        """Check for available updates and display results."""
        self.dialog.infobox("Checking for Updates", "Querying version information...")

        try:
            versions = check_all_versions()
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to check versions:\n{e}")
            return None

        # Build status report
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

        self.dialog.msgbox(
            "Version Status",
            "\n".join(lines),
            width=60,
            height=20
        )

        return versions

    def _update_all(self):
        """Update all components that have updates available."""
        self.dialog.infobox("Checking Updates", "Checking which components need updates...")

        try:
            versions = check_all_versions()
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to check versions:\n{e}")
            return

        updates_needed = []
        for key, info in versions.items():
            if info.update_available and info.update_command:
                # Skip firmware (manual process) and meshforge (handled separately)
                if key not in ('firmware', 'meshforge'):
                    updates_needed.append((key, info))

        if not updates_needed:
            self.dialog.msgbox(
                "No Updates",
                "All components are up to date!\n\n"
                "No automatic updates available."
            )
            return

        # Confirm update
        update_list = "\n".join([f"  - {info.name}" for _, info in updates_needed])
        if not self.dialog.yesno(
            "Confirm Updates",
            f"The following components will be updated:\n\n{update_list}\n\n"
            "This may take a few minutes. Continue?"
        ):
            return

        # Execute updates
        results = []
        for key, info in updates_needed:
            self.dialog.infobox(
                f"Updating {info.name}",
                f"Running: {info.update_command}\n\nPlease wait..."
            )

            success, msg = self._run_update_command(key, info.update_command)
            results.append((info.name, success, msg))

        # Show results
        lines = ["UPDATE RESULTS", "=" * 40, ""]
        for name, success, msg in results:
            status = "SUCCESS" if success else "FAILED"
            lines.append(f"{name}: {status}")
            if not success and msg:
                lines.append(f"  Error: {msg[:60]}...")
            lines.append("")

        self.dialog.msgbox("Update Complete", "\n".join(lines), width=60)

    def _update_meshtasticd(self):
        """Update meshtasticd package."""
        try:
            versions = check_all_versions()
            info = versions.get('meshtasticd')
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.dialog.msgbox("Error", "Could not get meshtasticd version info.")
            return

        if not info.update_available:
            self.dialog.msgbox(
                "No Update",
                f"meshtasticd is already at the latest version.\n\n"
                f"Installed: {info.installed}\n"
                f"Latest: {info.latest}"
            )
            return

        if not self.dialog.yesno(
            "Update meshtasticd",
            f"Update meshtasticd from {info.installed} to {info.latest}?\n\n"
            f"Command: {info.update_command}\n\n"
            "Note: The meshtasticd service will be restarted after the update."
        ):
            return

        self.dialog.infobox("Updating meshtasticd", "Running apt update and upgrade...\n\nThis may take a while...")

        success, msg = self._run_update_command('meshtasticd', info.update_command)

        if success:
            # Restart the service
            if _HAS_SERVICE_CHECK:
                self.dialog.infobox("Restarting", "Restarting meshtasticd service...")
                apply_config_and_restart('meshtasticd')

            self.dialog.msgbox(
                "Update Complete",
                "meshtasticd has been updated successfully!\n\n"
                "The service has been restarted."
            )
        else:
            self.dialog.msgbox(
                "Update Failed",
                f"Failed to update meshtasticd.\n\n{msg}"
            )

    def _update_cli(self):
        """Update Meshtastic CLI."""
        try:
            versions = check_all_versions()
            info = versions.get('cli')
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.dialog.msgbox("Error", "Could not get CLI version info.")
            return

        if not info.installed:
            # Not installed - offer to install
            if self.dialog.yesno(
                "Install Meshtastic CLI",
                "Meshtastic CLI is not installed.\n\n"
                f"Install command: {info.install_command}\n\n"
                "Install now?"
            ):
                self.dialog.infobox("Installing", "Installing Meshtastic CLI via pipx...")
                success, msg = self._run_update_command('cli', info.install_command)
                if success:
                    self.dialog.msgbox("Installed", "Meshtastic CLI installed successfully!")
                else:
                    self.dialog.msgbox("Failed", f"Installation failed:\n{msg}")
            return

        if not info.update_available:
            self.dialog.msgbox(
                "No Update",
                f"Meshtastic CLI is already at the latest version.\n\n"
                f"Installed: {info.installed}\n"
                f"Latest: {info.latest}"
            )
            return

        if not self.dialog.yesno(
            "Update Meshtastic CLI",
            f"Update CLI from {info.installed} to {info.latest}?\n\n"
            f"Command: {info.update_command}"
        ):
            return

        self.dialog.infobox("Updating CLI", "Running pipx upgrade...")
        success, msg = self._run_update_command('cli', info.update_command)

        if success:
            self.dialog.msgbox("Update Complete", "Meshtastic CLI updated successfully!")
        else:
            self.dialog.msgbox("Update Failed", f"Failed to update CLI.\n\n{msg}")

    def _firmware_info(self):
        """Show firmware update information."""
        try:
            versions = check_all_versions()
            info = versions.get('firmware')
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to check version:\n{e}")
            return

        if not info:
            self.dialog.msgbox("Error", "Could not get firmware version info.")
            return

        installed = info.installed or "Unknown (connect radio)"
        latest = info.latest or "Unknown"
        update_needed = " [UPDATE AVAILABLE]" if info.update_available else ""

        self.dialog.msgbox(
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

        # Get MeshForge install directory
        meshforge_dir = Path(__file__).parent.parent.parent  # src/launcher_tui -> src -> meshforge root

        # Check if it's a git repo
        git_dir = meshforge_dir / '.git'
        if not git_dir.exists():
            self.dialog.msgbox(
                "Not a Git Repository",
                "MeshForge is not installed via git.\n\n"
                "To update, re-run the installer:\n\n"
                "curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo bash"
            )
            return

        # Confirm update
        if not self.dialog.yesno(
            "Update MeshForge",
            "This will:\n\n"
            "1. Pull latest code from GitHub (git pull)\n"
            "2. Install/update Python dependencies\n\n"
            "Continue?"
        ):
            return

        # Step 1: Git pull
        self.dialog.infobox("Updating MeshForge", "Step 1/2: Pulling latest code from GitHub...")

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
                self.dialog.msgbox(
                    "Git Pull Failed",
                    f"Failed to pull updates:\n\n{git_output[:500]}"
                )
                return

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Git pull timed out after 60 seconds.")
            return
        except Exception as e:
            self.dialog.msgbox("Error", f"Git pull failed: {e}")
            return

        # Step 2: Install dependencies
        self.dialog.infobox("Updating MeshForge", "Step 2/2: Installing Python dependencies...")

        requirements_file = meshforge_dir / 'requirements.txt'
        if not requirements_file.exists():
            self.dialog.msgbox("Error", "requirements.txt not found!")
            return

        # Determine pip command based on venv or system install
        venv_pip = meshforge_dir / 'venv' / 'bin' / 'pip'
        no_venv_marker = meshforge_dir / '.no-venv'

        try:
            if venv_pip.exists() and not no_venv_marker.exists():
                # Use venv pip
                pip_cmd = [str(venv_pip), 'install', '-r', str(requirements_file)]
            else:
                # Use system pip with --break-system-packages if needed
                pip_cmd = ['pip3', 'install', '--break-system-packages', '-r', str(requirements_file)]

            result = subprocess.run(
                pip_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            pip_output = result.stdout + result.stderr

            if result.returncode != 0:
                self.dialog.msgbox(
                    "Pip Install Failed",
                    f"Failed to install dependencies:\n\n{pip_output[:500]}"
                )
                return

        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Error", "Pip install timed out after 5 minutes.")
            return
        except Exception as e:
            self.dialog.msgbox("Error", f"Pip install failed: {e}")
            return

        # Success!
        self.dialog.msgbox(
            "Update Complete",
            "MeshForge has been updated!\n\n"
            f"Git: {git_output.strip()[:200]}\n\n"
            "Please restart MeshForge to apply changes.\n\n"
            "Run: sudo meshforge"
        )

    def _run_update_command(self, component: str, command: str) -> Tuple[bool, str]:
        """Execute an update command safely.

        Args:
            component: Name of the component being updated
            command: Shell command to execute

        Returns:
            Tuple of (success, message)
        """
        try:
            # Split command properly for subprocess
            # Note: We use shell=True here because update commands often have
            # pipes and special characters. This is safe because the commands
            # are hardcoded in version_checker.py, not user input.
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
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
