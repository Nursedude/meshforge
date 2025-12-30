"""Meshtasticd installer module"""

import os
import subprocess
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel

from utils.system import (
    get_system_info,
    get_os_type,
    run_command,
    check_internet_connection,
    is_service_running,
    enable_service
)
from utils.logger import log, log_command, log_exception

console = Console()

# Log file location
INSTALL_LOG = Path('/var/log/meshtasticd-installer.log')
ERROR_LOG = Path('/var/log/meshtasticd-installer-error.log')


class MeshtasticdInstaller:
    """Handles meshtasticd installation and updates"""

    def __init__(self):
        self.system_info = get_system_info()
        self.os_type = get_os_type()
        self.scripts_dir = Path(__file__).parent.parent.parent / 'scripts'
        self.templates_dir = Path(__file__).parent.parent.parent / 'templates'

    def _log_error(self, error_msg, stderr='', stdout=''):
        """Log detailed error information to error log file"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(ERROR_LOG, 'a') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Error: {error_msg}\n")
                if stdout:
                    f.write(f"\nStdout:\n{stdout}\n")
                if stderr:
                    f.write(f"\nStderr:\n{stderr}\n")
                f.write(f"{'='*80}\n")
        except Exception as e:
            log(f"Failed to write error log: {e}", 'error')

    def _detect_error_type(self, stderr, stdout):
        """Detect common error patterns and provide helpful suggestions"""
        combined_output = f"{stderr}\n{stdout}".lower()

        suggestions = []

        # Packaging/pip errors
        if 'packaging' in combined_output and 'uninstall' in combined_output:
            suggestions.append("Python packaging conflict detected. Try:")
            suggestions.append("  sudo apt-get remove --purge python3-packaging")
            suggestions.append("  sudo python3 -m pip install --upgrade --force-reinstall packaging")

        # Disk space errors
        if 'no space left' in combined_output or 'disk full' in combined_output:
            suggestions.append("Insufficient disk space. Free up space with:")
            suggestions.append("  sudo apt-get clean")
            suggestions.append("  sudo apt-get autoremove")

        # Network/repository errors
        if 'unable to fetch' in combined_output or 'failed to fetch' in combined_output:
            suggestions.append("Repository access failed. Try:")
            suggestions.append("  sudo apt-get update")
            suggestions.append("  Check your internet connection")

        # Permission errors
        if 'permission denied' in combined_output:
            suggestions.append("Permission error. Make sure you're running with sudo")

        # GPG key errors
        if 'gpg' in combined_output or 'key' in combined_output:
            suggestions.append("GPG key issue. The repository key may need updating.")

        return suggestions

    def check_prerequisites(self):
        """Check if system meets prerequisites"""
        issues = []

        # Check if Raspberry Pi
        if not self.system_info['is_pi']:
            issues.append("This tool is designed for Raspberry Pi OS")

        # Check internet connection
        if not check_internet_connection():
            issues.append("No internet connection detected")

        # Check available disk space (need at least 100MB)
        from utils.system import get_disk_space
        disk_space = get_disk_space()
        if disk_space < 100:
            issues.append(f"Low disk space: {disk_space}MB available (need 100MB minimum)")

        # Check if supported architecture
        if self.os_type == 'unknown':
            issues.append(f"Unsupported architecture: {self.system_info['arch']}")

        return issues

    def install(self, version_type='stable'):
        """Install meshtasticd"""
        # Validate version_type to prevent shell injection
        VALID_VERSION_TYPES = ['stable', 'beta', 'daily', 'alpha']
        if version_type not in VALID_VERSION_TYPES:
            console.print(f"[bold red]Invalid version type: {version_type}[/bold red]")
            log(f"Invalid version type attempted: {version_type}", 'error')
            return False

        log(f"Starting meshtasticd installation (version: {version_type})")
        console.print(f"\n[cyan]Installing meshtasticd ({version_type} version)...[/cyan]")

        # Check prerequisites
        issues = self.check_prerequisites()
        if issues:
            console.print("\n[bold red]Cannot proceed with installation:[/bold red]")
            for issue in issues:
                console.print(f"  - {issue}")
            log(f"Installation aborted due to prerequisites: {issues}", 'error')
            return False

        # Display system info
        console.print(f"\n[cyan]System: {self.system_info['os']} ({self.os_type})[/cyan]")
        console.print(f"[cyan]Architecture: {self.system_info['arch']} ({self.system_info['bits']}-bit)[/cyan]")

        # Update package lists
        console.print("\n[cyan]Updating package lists...[/cyan]")
        result = run_command('apt-get update')
        log_command('apt-get update', result)

        if not result['success']:
            console.print("[bold red]Failed to update package lists[/bold red]")
            return False

        # Run appropriate installation script
        if self.os_type == 'armhf':
            return self._install_armhf(version_type)
        elif self.os_type == 'arm64':
            return self._install_arm64(version_type)
        else:
            console.print(f"[bold red]Unsupported OS type: {self.os_type}[/bold red]")
            return False

    def _install_armhf(self, version_type):
        """Install on 32-bit Raspberry Pi OS"""
        log("Installing on armhf (32-bit)")
        console.print("\n[cyan]Installing for 32-bit Raspberry Pi OS...[/cyan]")
        console.print("[dim]Streaming installation output...[/dim]\n")

        script_path = self.scripts_dir / 'install_armhf.sh'

        if not script_path.exists():
            error_msg = f"Installation script not found: {script_path}"
            console.print(f"[bold red]{error_msg}[/bold red]")
            self._log_error(error_msg)
            return False

        # Make script executable
        os.chmod(script_path, 0o755)

        # Run installation script with streaming output for better user feedback
        console.print("[dim]Running installation script (this may take a few minutes)...[/dim]\n")
        result = run_command(['bash', str(script_path), version_type], stream_output=True)
        log_command(f'bash {script_path}', result)

        if result['success']:
            console.print("\n[bold green]✓ Installation completed successfully![/bold green]")

            # Setup permissions
            self._setup_permissions()

            # Install configuration templates
            self.install_config_templates()

            # Enable and start service
            if self._setup_service():
                console.print("[bold green]✓ Service enabled and started[/bold green]")
            else:
                console.print("[bold yellow]⚠ Service setup had issues (check logs)[/bold yellow]")

            return True
        else:
            # Log detailed error information
            error_msg = "Installation script failed"
            self._log_error(error_msg, result.get('stderr', ''), result.get('stdout', ''))

            # Detect and display error type with suggestions
            suggestions = self._detect_error_type(result.get('stderr', ''), result.get('stdout', ''))

            console.print("\n[bold red]✗ Installation failed![/bold red]")

            if result['stderr']:
                console.print(Panel(
                    result['stderr'][-500:],  # Show last 500 chars of error
                    title="[red]Error Output[/red]",
                    border_style="red"
                ))

            if suggestions:
                console.print("\n[bold yellow]Troubleshooting Suggestions:[/bold yellow]")
                for suggestion in suggestions:
                    console.print(f"  {suggestion}")

            console.print(f"\n[dim]Full error log saved to: {ERROR_LOG}[/dim]")
            console.print(f"[dim]Installation log: {INSTALL_LOG}[/dim]")

            return False

    def _install_arm64(self, version_type):
        """Install on 64-bit Raspberry Pi OS"""
        log("Installing on arm64 (64-bit)")
        console.print("\n[cyan]Installing for 64-bit Raspberry Pi OS...[/cyan]")
        console.print("[dim]Streaming installation output...[/dim]\n")

        script_path = self.scripts_dir / 'install_arm64.sh'

        if not script_path.exists():
            error_msg = f"Installation script not found: {script_path}"
            console.print(f"[bold red]{error_msg}[/bold red]")
            self._log_error(error_msg)
            return False

        # Make script executable
        os.chmod(script_path, 0o755)

        # Run installation script with streaming output for better user feedback
        console.print("[dim]Running installation script (this may take a few minutes)...[/dim]\n")
        result = run_command(['bash', str(script_path), version_type], stream_output=True)
        log_command(f'bash {script_path}', result)

        if result['success']:
            console.print("\n[bold green]✓ Installation completed successfully![/bold green]")

            # Setup permissions
            self._setup_permissions()

            # Install configuration templates
            self.install_config_templates()

            # Enable and start service
            if self._setup_service():
                console.print("[bold green]✓ Service enabled and started[/bold green]")
            else:
                console.print("[bold yellow]⚠ Service setup had issues (check logs)[/bold yellow]")

            return True
        else:
            # Log detailed error information
            error_msg = "Installation script failed"
            self._log_error(error_msg, result.get('stderr', ''), result.get('stdout', ''))

            # Detect and display error type with suggestions
            suggestions = self._detect_error_type(result.get('stderr', ''), result.get('stdout', ''))

            console.print("\n[bold red]✗ Installation failed![/bold red]")

            if result['stderr']:
                console.print(Panel(
                    result['stderr'][-500:],  # Show last 500 chars of error
                    title="[red]Error Output[/red]",
                    border_style="red"
                ))

            if suggestions:
                console.print("\n[bold yellow]Troubleshooting Suggestions:[/bold yellow]")
                for suggestion in suggestions:
                    console.print(f"  {suggestion}")

            console.print(f"\n[dim]Full error log saved to: {ERROR_LOG}[/dim]")
            console.print(f"[dim]Installation log: {INSTALL_LOG}[/dim]")

            return False

    def _setup_permissions(self):
        """Setup GPIO/SPI permissions"""
        log("Setting up GPIO/SPI permissions")
        console.print("\n[cyan]Setting up GPIO/SPI permissions...[/cyan]")

        script_path = self.scripts_dir / 'setup_permissions.sh'

        if script_path.exists():
            os.chmod(script_path, 0o755)
            result = run_command(f'bash {script_path}', shell=True)
            log_command('setup_permissions.sh', result)
            return result['success']

        return False

    def _setup_service(self):
        """Enable and start meshtasticd service"""
        log("Setting up meshtasticd service")
        console.print("\n[cyan]Setting up meshtasticd service...[/cyan]")

        return enable_service('meshtasticd')

    def update(self):
        """Update meshtasticd"""
        log("Starting meshtasticd update")
        console.print("\n[cyan]Updating meshtasticd...[/cyan]")

        # Check if meshtasticd is installed
        result = run_command('which meshtasticd')
        if not result['success']:
            console.print("[bold red]meshtasticd is not installed[/bold red]")
            return False

        # Update package lists
        console.print("\n[cyan]Updating package lists...[/cyan]")
        result = run_command('apt-get update')
        log_command('apt-get update', result)

        # Upgrade meshtasticd
        console.print("\n[cyan]Upgrading meshtasticd...[/cyan]")
        result = run_command('apt-get install --only-upgrade meshtasticd -y')
        log_command('apt-get upgrade meshtasticd', result)

        if result['success']:
            console.print("\n[bold green]Update completed successfully![/bold green]")

            # Restart service
            from utils.system import restart_service
            if restart_service('meshtasticd'):
                console.print("[bold green]Service restarted[/bold green]")

            return True
        else:
            console.print("\n[bold red]Update failed![/bold red]")
            return False

    def get_installed_version(self):
        """Get currently installed version"""
        result = run_command('meshtasticd --version')
        if result['success']:
            return result['stdout'].strip()
        return None

    def is_installed(self):
        """Check if meshtasticd is installed"""
        result = run_command('which meshtasticd')
        return result['success']

    def install_config_templates(self):
        """Install configuration templates to /etc/meshtasticd"""
        import shutil

        log("Installing configuration templates")
        console.print("\n[cyan]Installing configuration templates...[/cyan]")

        # Define target directories
        config_base = Path('/etc/meshtasticd')
        available_d = config_base / 'available.d'
        config_d = config_base / 'config.d'

        # Create directories if they don't exist
        for dir_path in [config_base, available_d, config_d]:
            dir_path.mkdir(parents=True, exist_ok=True)
            log(f"Created directory: {dir_path}")

        # Copy templates from available.d
        src_available = self.templates_dir / 'available.d'
        if src_available.exists():
            for template_file in src_available.glob('*.yaml'):
                dest_file = available_d / template_file.name
                try:
                    shutil.copy2(template_file, dest_file)
                    console.print(f"  [green]Installed: {dest_file}[/green]")
                    log(f"Copied template: {template_file} -> {dest_file}")
                except Exception as e:
                    console.print(f"  [yellow]Warning: Could not copy {template_file.name}: {e}[/yellow]")
                    log(f"Failed to copy template {template_file}: {e}", 'warning')

        # Copy main config.yaml template if it doesn't exist
        main_config = config_base / 'config.yaml'
        src_config = self.templates_dir / 'config.yaml'
        if not main_config.exists() and src_config.exists():
            try:
                shutil.copy2(src_config, main_config)
                console.print(f"  [green]Installed: {main_config}[/green]")
                log(f"Installed main config: {main_config}")
            except Exception as e:
                console.print(f"  [yellow]Warning: Could not copy config.yaml: {e}[/yellow]")
                log(f"Failed to copy config.yaml: {e}", 'warning')
        elif main_config.exists():
            console.print(f"  [dim]Skipped (exists): {main_config}[/dim]")

        console.print("[green]Configuration templates installed![/green]")
        return True
