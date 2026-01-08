"""
Tool Manager - Version checking and upgrades for network tools

Manages installation status, version checking, and upgrades for:
- mudp (Meshtastic UDP)
- meshtastic (Python CLI)
- net-tools, iproute2 (system tools)
"""

import subprocess
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm

console = Console()


@dataclass
class ToolInfo:
    """Information about an installable tool"""
    name: str
    package: str
    install_method: str  # pip, pipx, apt
    description: str
    version: Optional[str] = None
    installed: bool = False
    update_available: bool = False
    latest_version: Optional[str] = None


class ToolManager:
    """Manages network tool installation and upgrades"""

    # Tool definitions
    TOOLS = {
        'mudp': ToolInfo(
            name='MUDP',
            package='mudp',
            install_method='pip',
            description='UDP-based Meshtastic packet broadcasting'
        ),
        'meshtastic': ToolInfo(
            name='Meshtastic CLI',
            package='meshtastic',
            install_method='pipx',
            description='Official Meshtastic Python CLI'
        ),
        'net-tools': ToolInfo(
            name='net-tools',
            package='net-tools',
            install_method='apt',
            description='Network utilities (ifconfig, netstat, arp)'
        ),
        'iproute2': ToolInfo(
            name='iproute2',
            package='iproute2',
            install_method='apt',
            description='Modern network utilities (ip, ss)'
        ),
        'nmap': ToolInfo(
            name='nmap',
            package='nmap',
            install_method='apt',
            description='Network scanner and security tool'
        ),
        'tcpdump': ToolInfo(
            name='tcpdump',
            package='tcpdump',
            install_method='apt',
            description='Network packet analyzer'
        ),
        'socat': ToolInfo(
            name='socat',
            package='socat',
            install_method='apt',
            description='Multipurpose relay (TCP/UDP/Serial)'
        )
    }

    def __init__(self):
        self._return_to_main = False
        self._refresh_tool_status()

    def _refresh_tool_status(self):
        """Refresh installation status for all tools"""
        for key, tool in self.TOOLS.items():
            if tool.install_method == 'pip':
                tool.installed, tool.version = self._check_pip_package(tool.package)
            elif tool.install_method == 'pipx':
                tool.installed, tool.version = self._check_pipx_package(tool.package)
            elif tool.install_method == 'apt':
                tool.installed, tool.version = self._check_apt_package(tool.package)

    def _check_pip_package(self, package: str) -> Tuple[bool, Optional[str]]:
        """Check if a pip package is installed"""
        try:
            result = subprocess.run(
                ['pip', 'show', package],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Version:'):
                        return True, line.split(':', 1)[1].strip()
                return True, None
            return False, None
        except Exception:
            return False, None

    def _check_pipx_package(self, package: str) -> Tuple[bool, Optional[str]]:
        """Check if a pipx package is installed"""
        try:
            result = subprocess.run(
                ['pipx', 'list', '--json'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                venvs = data.get('venvs', {})
                if package in venvs:
                    version = venvs[package].get('metadata', {}).get('main_package', {}).get('package_version')
                    return True, version
            return False, None
        except Exception:
            # Fallback: check if command exists
            try:
                result = subprocess.run(
                    ['which', package],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0, None
            except Exception:
                return False, None

    def _check_apt_package(self, package: str) -> Tuple[bool, Optional[str]]:
        """Check if an apt package is installed"""
        try:
            result = subprocess.run(
                ['dpkg', '-s', package],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Version:'):
                        return True, line.split(':', 1)[1].strip()
                return True, None
            return False, None
        except Exception:
            return False, None

    def _get_pypi_latest(self, package: str) -> Optional[str]:
        """Get latest version from PyPI"""
        try:
            result = subprocess.run(
                ['pip', 'index', 'versions', package],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                # Parse output like "mudp (0.1.5)"
                match = re.search(rf'{package}\s+\(([^)]+)\)', result.stdout)
                if match:
                    return match.group(1)
            return None
        except Exception:
            return None

    def interactive_menu(self):
        """Main tool manager menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            self._refresh_tool_status()

            console.print("\n[bold cyan]═══════════ Tool Manager ═══════════[/bold cyan]\n")

            # Show tool status table
            table = Table(title="Installed Tools", show_header=True)
            table.add_column("Tool", style="cyan")
            table.add_column("Status", justify="center")
            table.add_column("Version", justify="center")
            table.add_column("Description")

            for key, tool in self.TOOLS.items():
                status = "[green]Installed[/green]" if tool.installed else "[red]Not Installed[/red]"
                version = tool.version or "-"
                table.add_row(tool.name, status, version, tool.description)

            console.print(table)
            console.print()

            console.print("[dim cyan]── Actions ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Install/Update MUDP")
            console.print("  [bold]2[/bold]. Install/Update Meshtastic CLI")
            console.print("  [bold]3[/bold]. Install Network Tools (apt)")
            console.print("  [bold]4[/bold]. Check for Updates")
            console.print("  [bold]5[/bold]. Install All Missing Tools")
            console.print()
            console.print("  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")
            console.print()

            choice = Prompt.ask("Select option", default="0")

            if choice == "0":
                return
            elif choice.lower() == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self._install_mudp()
            elif choice == "2":
                self._install_meshtastic()
            elif choice == "3":
                self._install_apt_tools()
            elif choice == "4":
                self._check_updates()
            elif choice == "5":
                self._install_all_missing()

    def _install_mudp(self):
        """Install or update MUDP"""
        console.print("\n[cyan]Installing/Updating MUDP...[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Installing mudp via pip...", total=None)

            try:
                result = subprocess.run(
                    ['pip', 'install', '--upgrade', '--break-system-packages', '--ignore-installed', 'mudp'],
                    capture_output=True, text=True, timeout=120
                )

                if result.returncode == 0:
                    console.print("[green]MUDP installed/updated successfully![/green]")
                else:
                    console.print(f"[red]Installation failed:[/red] {result.stderr}")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _install_meshtastic(self):
        """Install or update Meshtastic CLI via pipx"""
        console.print("\n[cyan]Installing/Updating Meshtastic CLI...[/cyan]")

        # Check if pipx is installed
        pipx_check = subprocess.run(['which', 'pipx'], capture_output=True, timeout=5)
        if pipx_check.returncode != 0:
            console.print("[yellow]pipx not found, installing...[/yellow]")
            subprocess.run(['sudo', 'apt', 'install', '-y', 'pipx'], check=False, timeout=180)
            subprocess.run(['pipx', 'ensurepath'], check=False, timeout=30)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Installing meshtastic via pipx...", total=None)

            try:
                # Try upgrade first, then install
                result = subprocess.run(
                    ['pipx', 'upgrade', 'meshtastic'],
                    capture_output=True, text=True, timeout=120
                )

                if result.returncode != 0:
                    result = subprocess.run(
                        ['pipx', 'install', 'meshtastic[cli]', '--force'],
                        capture_output=True, text=True, timeout=120
                    )

                if result.returncode == 0:
                    console.print("[green]Meshtastic CLI installed/updated successfully![/green]")
                else:
                    console.print(f"[yellow]Note:[/yellow] {result.stderr}")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        input("\nPress Enter to continue...")

    def _install_apt_tools(self):
        """Install network tools via apt"""
        apt_tools = ['net-tools', 'iproute2', 'nmap', 'tcpdump', 'socat']
        missing = []

        for pkg in apt_tools:
            tool = self.TOOLS.get(pkg)
            if tool and not tool.installed:
                missing.append(pkg)

        if not missing:
            console.print("[green]All network tools are already installed![/green]")
            input("\nPress Enter to continue...")
            return

        console.print(f"\n[cyan]Installing: {', '.join(missing)}[/cyan]")

        if Confirm.ask("Continue with installation?", default=True):
            try:
                subprocess.run(
                    ['sudo', 'apt', 'install', '-y'] + missing,
                    check=True, timeout=300
                )
                console.print("[green]Network tools installed successfully![/green]")
            except subprocess.CalledProcessError as e:
                console.print(f"[red]Installation failed: {e}[/red]")
            except subprocess.TimeoutExpired:
                console.print("[red]Installation timed out[/red]")

        input("\nPress Enter to continue...")

    def _check_updates(self):
        """Check for available updates"""
        console.print("\n[cyan]Checking for updates...[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Checking PyPI...", total=None)

            updates = []

            # Check pip packages
            for key in ['mudp']:
                tool = self.TOOLS[key]
                if tool.installed:
                    latest = self._get_pypi_latest(tool.package)
                    if latest and tool.version and latest != tool.version:
                        updates.append((tool.name, tool.version, latest))

        if updates:
            console.print("\n[yellow]Updates available:[/yellow]")
            table = Table(show_header=True)
            table.add_column("Tool")
            table.add_column("Current")
            table.add_column("Latest")

            for name, current, latest in updates:
                table.add_row(name, current, latest)

            console.print(table)
        else:
            console.print("[green]All tools are up to date![/green]")

        input("\nPress Enter to continue...")

    def _install_all_missing(self):
        """Install all missing tools"""
        missing_pip = []
        missing_pipx = []
        missing_apt = []

        for key, tool in self.TOOLS.items():
            if not tool.installed:
                if tool.install_method == 'pip':
                    missing_pip.append(tool.package)
                elif tool.install_method == 'pipx':
                    missing_pipx.append(tool.package)
                elif tool.install_method == 'apt':
                    missing_apt.append(tool.package)

        if not any([missing_pip, missing_pipx, missing_apt]):
            console.print("[green]All tools are already installed![/green]")
            input("\nPress Enter to continue...")
            return

        console.print("\n[cyan]Missing tools to install:[/cyan]")
        if missing_pip:
            console.print(f"  pip: {', '.join(missing_pip)}")
        if missing_pipx:
            console.print(f"  pipx: {', '.join(missing_pipx)}")
        if missing_apt:
            console.print(f"  apt: {', '.join(missing_apt)}")

        if not Confirm.ask("\nInstall all missing tools?", default=True):
            return

        # Install apt packages
        if missing_apt:
            console.print("\n[cyan]Installing apt packages...[/cyan]")
            try:
                subprocess.run(['sudo', 'apt', 'install', '-y'] + missing_apt, check=True, timeout=300)
            except subprocess.CalledProcessError:
                console.print("[red]Some apt packages failed to install[/red]")
            except subprocess.TimeoutExpired:
                console.print("[red]APT installation timed out[/red]")

        # Install pip packages
        for pkg in missing_pip:
            console.print(f"\n[cyan]Installing {pkg}...[/cyan]")
            subprocess.run(
                ['pip', 'install', '--break-system-packages', '--ignore-installed', pkg],
                check=False, timeout=120
            )

        # Install pipx packages
        for pkg in missing_pipx:
            console.print(f"\n[cyan]Installing {pkg}...[/cyan]")
            subprocess.run(['pipx', 'install', f'{pkg}[cli]', '--force'], check=False, timeout=120)

        console.print("\n[green]Installation complete![/green]")
        input("\nPress Enter to continue...")

    def is_tool_installed(self, tool_name: str) -> bool:
        """Check if a specific tool is installed"""
        tool = self.TOOLS.get(tool_name)
        if tool:
            self._refresh_tool_status()
            return tool.installed
        return False

    def get_tool_version(self, tool_name: str) -> Optional[str]:
        """Get version of a specific tool"""
        tool = self.TOOLS.get(tool_name)
        if tool and tool.installed:
            return tool.version
        return None
