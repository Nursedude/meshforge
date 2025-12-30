"""Dependency management and deprecation fixes"""

import subprocess
import shutil
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

    # Required system packages (includes pipx for isolated meshtastic CLI)
    REQUIRED_PACKAGES = [
        'python3',
        'python3-pip',
        'python3-dev',
        'python3-venv',
        'pipx',
        'git',
        'curl',
        'build-essential',
        'libssl-dev',
        'libffi-dev',
    ]

    # Required Python packages for the installer UI (not meshtastic - that uses pipx)
    REQUIRED_PYTHON_PACKAGES = [
        'click',
        'rich',
        'pyyaml',
        'requests',
        'psutil',
        'distro',
        'python-dotenv',
    ]

    # Packages that should be installed via pipx (isolated environments)
    PIPX_PACKAGES = [
        'meshtastic',
    ]

    def __init__(self):
        self.issues = []

    def check_all(self):
        """Check all dependencies"""
        self.issues = []

        self.check_system_packages()
        self.check_python_packages()
        self.check_pipx_packages()
        self.check_deprecated_packages()
        self.check_permissions()

        return self.issues

    def check_pipx_packages(self):
        """Check packages that should be installed via pipx"""
        log("Checking pipx packages")

        # First check if pipx is available
        if not shutil.which('pipx'):
            self.issues.append("pipx not installed (required for meshtastic CLI)")
            return

        result = run_command('pipx list')

        if result['success']:
            output = result['stdout'].lower()
            for package in self.PIPX_PACKAGES:
                if package.lower() not in output:
                    self.issues.append(f"Missing pipx package: {package} (install with: pipx install {package})")

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
        self.fix_pipx_packages()
        self.fix_deprecated_packages()

        console.print("\n[bold green]Dependency fixes completed![/bold green]")

    def fix_pipx_packages(self):
        """Install missing pipx packages"""
        console.print("\n[cyan]Installing pipx packages (isolated environments)...[/cyan]")

        # Ensure pipx is available
        if not shutil.which('pipx'):
            console.print("[yellow]pipx not found, installing...[/yellow]")
            result = run_command('apt-get install -y pipx')
            log_command('apt-get install pipx', result)
            if not result['success']:
                console.print("[red]Failed to install pipx[/red]")
                return

        # Ensure pipx path
        run_command('pipx ensurepath')

        for package in self.PIPX_PACKAGES:
            console.print(f"  Installing {package} via pipx...")
            # Check if already installed
            check_result = run_command('pipx list')
            if check_result['success'] and package.lower() in check_result['stdout'].lower():
                console.print(f"  [green]{package} already installed[/green]")
                # Upgrade instead
                result = run_command(f'pipx upgrade {package}')
                log_command(f'pipx upgrade {package}', result)
            else:
                # Install with [cli] extras for meshtastic
                pkg_spec = f'"{package}[cli]"' if package == 'meshtastic' else package
                result = run_command(f'pipx install {pkg_spec} --force')
                log_command(f'pipx install {pkg_spec}', result)

                if result['success']:
                    console.print(f"  [green]✓ {package} installed[/green]")
                else:
                    console.print(f"  [red]✗ Failed to install {package}[/red]")
                    # Try without extras
                    if '[cli]' in pkg_spec:
                        console.print(f"  [yellow]Retrying without extras...[/yellow]")
                        result = run_command(f'pipx install {package} --force')
                        if result['success']:
                            console.print(f"  [green]✓ {package} installed[/green]")

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
        """Install missing Python packages for the installer UI"""
        console.print("\n[cyan]Installing missing Python packages...[/cyan]")

        # Install UI dependencies via pip (meshtastic is installed via pipx separately)
        packages = ' '.join(self.REQUIRED_PYTHON_PACKAGES)
        result = run_command(f'pip3 install --upgrade --break-system-packages {packages}')
        log_command('pip3 install packages', result)

        if result['success']:
            console.print("[green]Python packages installed/updated[/green]")
        else:
            console.print("[yellow]Some packages may have failed, checking individually...[/yellow]")
            # Try installing each package individually
            for pkg in self.REQUIRED_PYTHON_PACKAGES:
                result = run_command(f'pip3 install --break-system-packages {pkg}')
                if result['success']:
                    console.print(f"  [green]✓ {pkg}[/green]")
                else:
                    console.print(f"  [yellow]⚠ {pkg} (may be system package)[/yellow]")

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

        # Pipx packages (isolated environments)
        pipx_result = run_command('pipx list')
        if pipx_result['success']:
            pipx_output = pipx_result['stdout'].lower()
            for package in self.PIPX_PACKAGES:
                status = "✓ Installed (pipx)" if package.lower() in pipx_output else "✗ Missing"
                table.add_row(package, status, "Pipx (isolated)")
        else:
            for package in self.PIPX_PACKAGES:
                table.add_row(package, "⚠ pipx not available", "Pipx (isolated)")

        console.print(table)
