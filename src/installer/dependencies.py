"""Dependency management and deprecation fixes"""

import subprocess
from rich.console import Console
from rich.table import Table

from utils.system import run_command, check_package_installed
from utils.logger import log, log_command

console = Console()


class DependencyManager:
    """Manages system and Python dependencies"""

    # Known deprecated packages and their replacements
    DEPRECATED_PACKAGES = {
        'python-dev': 'python3-dev',
        'python-pip': 'python3-pip',
        'python-setuptools': 'python3-setuptools',
    }

    # Required system packages
    REQUIRED_PACKAGES = [
        'python3',
        'python3-pip',
        'python3-dev',
        'git',
        'curl',
        'build-essential',
        'libssl-dev',
        'libffi-dev',
    ]

    # Required Python packages
    REQUIRED_PYTHON_PACKAGES = [
        'meshtastic',
        'click',
        'rich',
        'pyyaml',
        'requests',
        'packaging',
        'psutil',
        'distro',
        'python-dotenv',
    ]

    def __init__(self):
        self.issues = []

    def check_all(self):
        """Check all dependencies"""
        self.issues = []

        self.check_system_packages()
        self.check_python_packages()
        self.check_deprecated_packages()
        self.check_permissions()

        return self.issues

    def check_system_packages(self):
        """Check required system packages"""
        log("Checking system packages")

        for package in self.REQUIRED_PACKAGES:
            if not check_package_installed(package):
                self.issues.append(f"Missing system package: {package}")

    def check_python_packages(self):
        """Check required Python packages"""
        log("Checking Python packages")

        result = run_command('pip3 list --format=json')

        if result['success']:
            import json
            try:
                installed = json.loads(result['stdout'])
                installed_names = [pkg['name'].lower() for pkg in installed]

                for package in self.REQUIRED_PYTHON_PACKAGES:
                    if package.lower() not in installed_names:
                        self.issues.append(f"Missing Python package: {package}")
            except json.JSONDecodeError:
                self.issues.append("Could not parse pip list output")

    def check_deprecated_packages(self):
        """Check for deprecated packages"""
        log("Checking for deprecated packages")

        for old_pkg, new_pkg in self.DEPRECATED_PACKAGES.items():
            if check_package_installed(old_pkg):
                self.issues.append(f"Deprecated package installed: {old_pkg} (use {new_pkg} instead)")

    def check_permissions(self):
        """Check GPIO/SPI permissions"""
        log("Checking GPIO/SPI permissions")

        # Check if user is in required groups
        result = run_command('groups')
        if result['success']:
            groups = result['stdout'].strip().split()

            required_groups = ['gpio', 'spi', 'i2c', 'dialout']
            for group in required_groups:
                if group not in groups:
                    self.issues.append(f"User not in {group} group")

        # Check if SPI is enabled
        import os
        config_files = ['/boot/config.txt', '/boot/firmware/config.txt']

        spi_enabled = False
        for config_file in config_files:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    content = f.read()
                    if 'dtparam=spi=on' in content:
                        spi_enabled = True
                        break

        if not spi_enabled:
            self.issues.append("SPI not enabled in config.txt")

    def fix_all(self):
        """Fix all dependency issues"""
        log("Fixing dependency issues")

        self.fix_system_packages()
        self.fix_python_packages()
        self.fix_deprecated_packages()

        console.print("\n[bold green]Dependency fixes completed![/bold green]")

    def fix_system_packages(self):
        """Install missing system packages"""
        console.print("\n[cyan]Installing missing system packages...[/cyan]")

        missing = [pkg for pkg in self.REQUIRED_PACKAGES if not check_package_installed(pkg)]

        if missing:
            packages_str = ' '.join(missing)
            result = run_command(f'apt-get install -y {packages_str}')
            log_command(f'apt-get install {packages_str}', result)

            if result['success']:
                console.print(f"[green]Installed: {packages_str}[/green]")
            else:
                console.print(f"[red]Failed to install: {packages_str}[/red]")

    def fix_python_packages(self):
        """Install missing Python packages"""
        console.print("\n[cyan]Installing missing Python packages...[/cyan]")

        result = run_command('pip3 install --upgrade --break-system-packages meshtastic click rich pyyaml requests packaging psutil distro python-dotenv')
        log_command('pip3 install packages', result)

        if result['success']:
            console.print("[green]Python packages installed/updated[/green]")
        else:
            console.print("[red]Failed to install Python packages[/red]")

    def fix_deprecated_packages(self):
        """Remove deprecated packages and install replacements"""
        console.print("\n[cyan]Fixing deprecated packages...[/cyan]")

        for old_pkg, new_pkg in self.DEPRECATED_PACKAGES.items():
            if check_package_installed(old_pkg):
                # Remove old package
                result = run_command(f'apt-get remove -y {old_pkg}')
                log_command(f'apt-get remove {old_pkg}', result)

                if result['success']:
                    console.print(f"[green]Removed deprecated package: {old_pkg}[/green]")

                # Install new package
                result = run_command(f'apt-get install -y {new_pkg}')
                log_command(f'apt-get install {new_pkg}', result)

                if result['success']:
                    console.print(f"[green]Installed replacement: {new_pkg}[/green]")

    def show_dependency_table(self):
        """Display dependency status table"""
        table = Table(title="Dependency Status", show_header=True, header_style="bold magenta")
        table.add_column("Package", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Type", style="yellow")

        # System packages
        for package in self.REQUIRED_PACKAGES:
            status = "✓ Installed" if check_package_installed(package) else "✗ Missing"
            table.add_row(package, status, "System")

        # Python packages
        result = run_command('pip3 list --format=json')
        if result['success']:
            import json
            try:
                installed = json.loads(result['stdout'])
                installed_names = [pkg['name'].lower() for pkg in installed]

                for package in self.REQUIRED_PYTHON_PACKAGES:
                    status = "✓ Installed" if package.lower() in installed_names else "✗ Missing"
                    table.add_row(package, status, "Python")
            except json.JSONDecodeError:
                pass

        console.print(table)
