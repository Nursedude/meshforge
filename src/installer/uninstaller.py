"""Meshtasticd uninstaller module"""

import os
import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel

from utils.system import run_command, is_service_running
from utils.logger import log

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

console = Console()


class MeshtasticdUninstaller:
    """Handles meshtasticd uninstallation"""

    def __init__(self):
        self.config_dir = Path('/etc/meshtasticd')
        self.installer_dir = Path('/opt/meshtasticd-installer')
        self.user_config_dir = get_real_user_home() / '.config' / 'meshtasticd-installer'
        self.log_files = [
            Path('/var/log/meshtasticd-installer.log'),
            Path('/var/log/meshtasticd-installer-error.log')
        ]
        self.symlinks = [
            Path('/usr/local/bin/meshtasticd-installer'),
            Path('/usr/local/bin/meshtasticd-cli')
        ]

    def uninstall(self, interactive=True):
        """
        Uninstall meshtasticd and related components

        Args:
            interactive: If True, prompt for each component. If False, remove all.

        Returns:
            bool: True if uninstall completed successfully
        """
        console.print("\n[bold cyan]═══════════════ Meshtasticd Uninstaller ═══════════════[/bold cyan]\n")

        console.print("[yellow]This will uninstall meshtasticd and its components.[/yellow]")
        console.print("[dim]You can choose which components to remove.[/dim]\n")

        # Check what's installed
        components = self._detect_installed_components()

        if not any(components.values()):
            console.print("[yellow]Nothing appears to be installed.[/yellow]")
            return True

        # Show what's installed
        console.print("[bold]Installed Components:[/bold]")
        if components['service']:
            console.print("  [green]✓[/green] meshtasticd service")
        if components['package']:
            console.print("  [green]✓[/green] meshtasticd package")
        if components['config']:
            console.print("  [green]✓[/green] Configuration files (/etc/meshtasticd)")
        if components['installer']:
            console.print("  [green]✓[/green] Installer (/opt/meshtasticd-installer)")
        if components['symlinks']:
            console.print("  [green]✓[/green] Command symlinks")
        if components['user_config']:
            console.print("  [green]✓[/green] User preferences (~/.config/meshtasticd-installer)")
        console.print()

        if interactive:
            return self._interactive_uninstall(components)
        else:
            return self._full_uninstall(components)

    def _detect_installed_components(self):
        """Detect which components are installed"""
        components = {
            'service': False,
            'package': False,
            'config': False,
            'installer': False,
            'symlinks': False,
            'user_config': False,
            'logs': False
        }

        # Check service
        result = run_command('systemctl list-unit-files meshtasticd.service')
        if result['success'] and 'meshtasticd.service' in result['stdout']:
            components['service'] = True

        # Check package
        result = run_command('dpkg -l meshtasticd')
        if result['success'] and 'meshtasticd' in result['stdout']:
            components['package'] = True

        # Check config directory
        if self.config_dir.exists():
            components['config'] = True

        # Check installer directory
        if self.installer_dir.exists():
            components['installer'] = True

        # Check symlinks
        if any(s.exists() for s in self.symlinks):
            components['symlinks'] = True

        # Check user config
        if self.user_config_dir.exists():
            components['user_config'] = True

        # Check logs
        if any(f.exists() for f in self.log_files):
            components['logs'] = True

        return components

    def _interactive_uninstall(self, components):
        """Interactive uninstall with prompts for each component"""
        success = True

        # Step 1: Stop and disable service
        if components['service']:
            if Confirm.ask("\n[cyan]Stop and disable meshtasticd service?[/cyan]", default=True):
                if not self._stop_service():
                    success = False

        # Step 2: Remove package
        if components['package']:
            if Confirm.ask("\n[cyan]Remove meshtasticd package?[/cyan]", default=True):
                if not self._remove_package():
                    success = False

        # Step 3: Remove configuration
        if components['config']:
            console.print("\n[yellow]Configuration files contain your device settings.[/yellow]")
            if Confirm.ask("[cyan]Remove configuration files (/etc/meshtasticd)?[/cyan]", default=False):
                if not self._remove_config():
                    success = False
            else:
                console.print("[dim]Configuration files preserved.[/dim]")

        # Step 4: Remove symlinks
        if components['symlinks']:
            if Confirm.ask("\n[cyan]Remove command symlinks (meshtasticd-installer, meshtasticd-cli)?[/cyan]", default=True):
                if not self._remove_symlinks():
                    success = False

        # Step 5: Remove installer
        if components['installer']:
            console.print("\n[yellow]Removing the installer will delete this program.[/yellow]")
            if Confirm.ask("[cyan]Remove installer (/opt/meshtasticd-installer)?[/cyan]", default=False):
                if not self._remove_installer():
                    success = False
            else:
                console.print("[dim]Installer preserved.[/dim]")

        # Step 6: Remove user preferences
        if components['user_config']:
            if Confirm.ask("\n[cyan]Remove user preferences?[/cyan]", default=True):
                if not self._remove_user_config():
                    success = False

        # Step 7: Remove logs
        if components['logs']:
            if Confirm.ask("\n[cyan]Remove log files?[/cyan]", default=True):
                if not self._remove_logs():
                    success = False

        # Summary
        console.print("\n" + "=" * 50)
        if success:
            console.print("[bold green]Uninstallation completed![/bold green]")
        else:
            console.print("[bold yellow]Uninstallation completed with some warnings.[/bold yellow]")

        return success

    def _full_uninstall(self, components):
        """Full uninstall without prompts"""
        success = True

        if components['service']:
            if not self._stop_service():
                success = False

        if components['package']:
            if not self._remove_package():
                success = False

        if components['config']:
            if not self._remove_config():
                success = False

        if components['symlinks']:
            if not self._remove_symlinks():
                success = False

        if components['user_config']:
            if not self._remove_user_config():
                success = False

        if components['logs']:
            if not self._remove_logs():
                success = False

        # Don't remove installer in non-interactive mode (safety)

        return success

    def _stop_service(self):
        """Stop and disable meshtasticd service"""
        console.print("\n[cyan]Stopping meshtasticd service...[/cyan]")
        log("Stopping meshtasticd service")

        # Stop service
        result = run_command('systemctl stop meshtasticd')
        if result['success']:
            console.print("  [green]✓[/green] Service stopped")
        else:
            console.print("  [yellow]⚠[/yellow] Could not stop service (may not be running)")

        # Disable service
        result = run_command('systemctl disable meshtasticd')
        if result['success']:
            console.print("  [green]✓[/green] Service disabled")
        else:
            console.print("  [yellow]⚠[/yellow] Could not disable service")

        return True

    def _remove_package(self):
        """Remove meshtasticd package"""
        console.print("\n[cyan]Removing meshtasticd package...[/cyan]")
        log("Removing meshtasticd package")

        # Remove package
        result = run_command('apt-get remove -y meshtasticd', stream_output=True)
        if result['success']:
            console.print("  [green]✓[/green] Package removed")

            # Ask about purge
            if Confirm.ask("  [dim]Also remove package configuration (purge)?[/dim]", default=True):
                result = run_command('apt-get purge -y meshtasticd')
                if result['success']:
                    console.print("  [green]✓[/green] Package purged")
            return True
        else:
            console.print("  [red]✗[/red] Failed to remove package")
            console.print(f"  [dim]{result.get('stderr', '')}[/dim]")
            return False

    def _remove_config(self):
        """Remove configuration files"""
        console.print("\n[cyan]Removing configuration files...[/cyan]")
        log(f"Removing configuration directory: {self.config_dir}")

        try:
            if self.config_dir.exists():
                # Backup first
                backup_dir = Path('/tmp/meshtasticd-config-backup')
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                shutil.copytree(self.config_dir, backup_dir)
                console.print(f"  [dim]Backup created: {backup_dir}[/dim]")

                # Remove
                shutil.rmtree(self.config_dir)
                console.print("  [green]✓[/green] Configuration removed")
                return True
            else:
                console.print("  [dim]Configuration directory not found[/dim]")
                return True
        except Exception as e:
            console.print(f"  [red]✗[/red] Error: {e}")
            log(f"Error removing config: {e}", 'error')
            return False

    def _remove_symlinks(self):
        """Remove command symlinks"""
        console.print("\n[cyan]Removing command symlinks...[/cyan]")
        log("Removing command symlinks")

        success = True
        for symlink in self.symlinks:
            try:
                if symlink.exists() or symlink.is_symlink():
                    symlink.unlink()
                    console.print(f"  [green]✓[/green] Removed: {symlink}")
                else:
                    console.print(f"  [dim]Not found: {symlink}[/dim]")
            except Exception as e:
                console.print(f"  [red]✗[/red] Error removing {symlink}: {e}")
                success = False

        return success

    def _remove_installer(self):
        """Remove installer directory"""
        console.print("\n[cyan]Removing installer...[/cyan]")
        log(f"Removing installer directory: {self.installer_dir}")

        try:
            if self.installer_dir.exists():
                shutil.rmtree(self.installer_dir)
                console.print("  [green]✓[/green] Installer removed")
                console.print("\n[yellow]Note: This program will exit after completion.[/yellow]")
                return True
            else:
                console.print("  [dim]Installer directory not found[/dim]")
                return True
        except Exception as e:
            console.print(f"  [red]✗[/red] Error: {e}")
            log(f"Error removing installer: {e}", 'error')
            return False

    def _remove_user_config(self):
        """Remove user configuration/preferences"""
        console.print("\n[cyan]Removing user preferences...[/cyan]")
        log(f"Removing user config: {self.user_config_dir}")

        try:
            if self.user_config_dir.exists():
                shutil.rmtree(self.user_config_dir)
                console.print("  [green]✓[/green] User preferences removed")
                return True
            else:
                console.print("  [dim]User preferences not found[/dim]")
                return True
        except Exception as e:
            console.print(f"  [red]✗[/red] Error: {e}")
            return False

    def _remove_logs(self):
        """Remove log files"""
        console.print("\n[cyan]Removing log files...[/cyan]")
        log("Removing log files")

        success = True
        for log_file in self.log_files:
            try:
                if log_file.exists():
                    log_file.unlink()
                    console.print(f"  [green]✓[/green] Removed: {log_file}")
            except Exception as e:
                console.print(f"  [red]✗[/red] Error removing {log_file}: {e}")
                success = False

        return success


def uninstall_interactive():
    """Run interactive uninstall"""
    uninstaller = MeshtasticdUninstaller()
    return uninstaller.uninstall(interactive=True)


def uninstall_full():
    """Run full uninstall (for scripting)"""
    uninstaller = MeshtasticdUninstaller()
    return uninstaller.uninstall(interactive=False)
