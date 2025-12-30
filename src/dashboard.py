"""Quick Status Dashboard for Meshtasticd"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.columns import Columns
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich.prompt import Prompt, Confirm
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.tree import Tree
from rich.box import ROUNDED, DOUBLE, HEAVY
import time
import logging

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import emoji as em

console = Console()
logger = logging.getLogger(__name__)


class StatusDashboard:
    """Quick Status Dashboard showing system and mesh network status"""

    def __init__(self):
        self.config_path = Path('/etc/meshtasticd/config.yaml')
        self.log_path = Path('/var/log/meshtasticd.log')

    def get_service_status(self):
        """Get meshtasticd service status"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            return {
                'status': status,
                'running': status == 'active',
                'color': 'green' if status == 'active' else 'red'
            }
        except Exception as e:
            logger.error(f"Failed to get service status: {e}")
            return {'status': 'unknown', 'running': False, 'color': 'yellow'}

    def get_service_uptime(self):
        """Get service uptime"""
        try:
            result = subprocess.run(
                ['systemctl', 'show', 'meshtasticd', '--property=ActiveEnterTimestamp'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                timestamp_line = result.stdout.strip()
                if '=' in timestamp_line:
                    timestamp_str = timestamp_line.split('=')[1]
                    if timestamp_str:
                        return timestamp_str
            return 'N/A'
        except Exception as e:
            logger.error(f"Failed to get service uptime: {e}")
            return 'N/A'

    def get_installed_version(self):
        """Get installed meshtasticd version"""
        try:
            result = subprocess.run(
                ['meshtasticd', '--version'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return 'Not installed'
        except FileNotFoundError:
            return 'Not installed'
        except Exception as e:
            logger.error(f"Failed to get installed version: {e}")
            return 'Unknown'

    def get_system_info(self):
        """Get system information"""
        info = {}

        # CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
                info['cpu_temp'] = f'{temp:.1f}°C'
                info['temp_status'] = 'normal' if temp < 70 else ('warning' if temp < 80 else 'critical')
        except Exception as e:
            logger.debug(f"Failed to get CPU temperature: {e}")
            info['cpu_temp'] = 'N/A'
            info['temp_status'] = 'unknown'

        # Memory usage
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = {}
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().split()[0]
                        meminfo[key] = int(value)
                total = meminfo.get('MemTotal', 0)
                available = meminfo.get('MemAvailable', 0)
                used = total - available
                if total > 0:
                    usage_pct = (used / total) * 100
                    info['memory'] = f'{usage_pct:.1f}%'
                    info['memory_status'] = 'normal' if usage_pct < 80 else 'warning'
                    info['memory_used_mb'] = used / 1024  # Store for progress bar
                    info['memory_total_mb'] = total / 1024
                else:
                    info['memory'] = 'N/A'
                    info['memory_status'] = 'unknown'
        except Exception as e:
            logger.debug(f"Failed to get memory usage: {e}")
            info['memory'] = 'N/A'
            info['memory_status'] = 'unknown'

        # Disk usage
        try:
            statvfs = os.statvfs('/')
            total = statvfs.f_blocks * statvfs.f_frsize
            free = statvfs.f_bavail * statvfs.f_frsize
            used = total - free
            if total > 0:
                usage_pct = (used / total) * 100
                info['disk'] = f'{usage_pct:.1f}%'
                info['disk_status'] = 'normal' if usage_pct < 90 else 'warning'
                info['disk_used_gb'] = used / (1024**3)  # Store for progress bar
                info['disk_total_gb'] = total / (1024**3)
            else:
                info['disk'] = 'N/A'
                info['disk_status'] = 'unknown'
        except Exception as e:
            logger.debug(f"Failed to get disk usage: {e}")
            info['disk'] = 'N/A'
            info['disk_status'] = 'unknown'

        # System uptime
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
                days = int(uptime_seconds // 86400)
                hours = int((uptime_seconds % 86400) // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                if days > 0:
                    info['uptime'] = f'{days}d {hours}h {minutes}m'
                elif hours > 0:
                    info['uptime'] = f'{hours}h {minutes}m'
                else:
                    info['uptime'] = f'{minutes}m'
        except Exception as e:
            logger.debug(f"Failed to get system uptime: {e}")
            info['uptime'] = 'N/A'

        return info

    def get_network_info(self):
        """Get network information"""
        info = {}

        # Get IP addresses
        try:
            result = subprocess.run(
                ['hostname', '-I'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                ips = result.stdout.strip().split()
                info['ip'] = ips[0] if ips else 'No IP'
            else:
                info['ip'] = 'N/A'
        except Exception as e:
            logger.debug(f"Failed to get IP address: {e}")
            info['ip'] = 'N/A'

        # Check internet connectivity
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
                capture_output=True, timeout=5
            )
            info['internet'] = result.returncode == 0
        except Exception as e:
            logger.debug(f"Failed to check internet connectivity: {e}")
            info['internet'] = False

        return info

    def get_config_status(self):
        """Check configuration status"""
        status = {
            'config_exists': self.config_path.exists(),
            'config_path': str(self.config_path),
            'active_template': None
        }

        # Check for active templates in config.d
        config_d = Path('/etc/meshtasticd/config.d')
        if config_d.exists():
            templates = list(config_d.glob('*.yaml'))
            if templates:
                status['active_template'] = templates[0].name

        return status

    def get_recent_logs(self, lines=5):
        """Get recent log entries"""
        logs = []
        log_files = [
            Path('/var/log/meshtasticd.log'),
            Path('/var/log/syslog')
        ]

        for log_file in log_files:
            if log_file.exists():
                try:
                    result = subprocess.run(
                        ['tail', '-n', str(lines), str(log_file)],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if 'meshtasticd' in line.lower():
                                logs.append(line[:100])
                except Exception as e:
                    logger.debug(f"Failed to read log file {log_file}: {e}")
                    pass
                if logs:
                    break

        return logs[-lines:] if logs else ['No recent logs available']

    def create_status_panel(self):
        """Create the main status panel with enhanced visuals"""
        service = self.get_service_status()
        version = self.get_installed_version()
        uptime = self.get_service_uptime()

        # Service status with icon and box
        if service['running']:
            status_text = Text(f"{em.get('✓')} RUNNING", style="bold green on dark_green")
            status_icon = em.get('🟢')
        else:
            status_text = Text(f"{em.get('✗')} STOPPED", style="bold red on dark_red")
            status_icon = em.get('🔴')

        content = Text()
        content.append(f"{status_icon} Status:  ")
        content.append(status_text)
        content.append(f"\n\n{em.get('📦')} Version: ", style="bold")
        content.append(f"{version}")
        content.append(f"\n\n{em.get('⏰', '[TIME]')} Started: ", style="bold")
        content.append(f"{uptime}")

        return Panel(content, title=f"[bold cyan]{em.get('📡')} Meshtasticd Service[/bold cyan]", border_style="cyan", box=ROUNDED)

    def create_system_panel(self):
        """Create system information panel with progress bars"""
        info = self.get_system_info()

        # Format temperature with color
        temp_color = 'green' if info['temp_status'] == 'normal' else ('yellow' if info['temp_status'] == 'warning' else 'red')

        # Create rich content with progress bars
        content = Text()
        content.append(f"{em.get('🌡️')}  CPU Temp: ")
        content.append(info['cpu_temp'], style=f"bold {temp_color}")
        content.append("\n")

        # Memory progress bar
        if info.get('memory_used_mb') and info.get('memory_total_mb'):
            mem_pct = (info['memory_used_mb'] / info['memory_total_mb']) * 100
            mem_color = 'green' if mem_pct < 80 else ('yellow' if mem_pct < 90 else 'red')
            content.append(f"\n{em.get('💾')} Memory:   ")
            content.append(info['memory'], style=f"bold {mem_color}")
            content.append(f" ({info['memory_used_mb']:.0f}/{info['memory_total_mb']:.0f} MB)\n")
            content.append(self._create_progress_bar(mem_pct, mem_color))
        else:
            content.append(f"\n{em.get('💾')} Memory:   {info['memory']}")

        # Disk progress bar
        if info.get('disk_used_gb') and info.get('disk_total_gb'):
            disk_pct = (info['disk_used_gb'] / info['disk_total_gb']) * 100
            disk_color = 'green' if disk_pct < 90 else ('yellow' if disk_pct < 95 else 'red')
            content.append(f"\n\n{em.get('💿', '[DISK]')} Disk:     ")
            content.append(info['disk'], style=f"bold {disk_color}")
            content.append(f" ({info['disk_used_gb']:.1f}/{info['disk_total_gb']:.1f} GB)\n")
            content.append(self._create_progress_bar(disk_pct, disk_color))
        else:
            content.append(f"\n\n{em.get('💿', '[DISK]')} Disk:     {info['disk']}")

        content.append(f"\n\n{em.get('⏱️', '[TIME]')}  Uptime:   {info['uptime']}")

        return Panel(content, title=f"[bold magenta]{em.get('⚙️')}  System Health[/bold magenta]", border_style="magenta", box=ROUNDED)

    def _create_progress_bar(self, percentage, color):
        """Create a text-based progress bar"""
        width = 30
        filled = int((percentage / 100) * width)
        bar = '█' * filled + '░' * (width - filled)
        return Text(f"   [{bar}]", style=color)

    def create_network_panel(self):
        """Create network information panel with enhanced visuals"""
        info = self.get_network_info()

        if info['internet']:
            internet_status = Text(f"{em.get('✓')} Connected", style="bold green on dark_green")
            internet_icon = em.get('🌐')
        else:
            internet_status = Text(f"{em.get('✗')} Offline", style="bold red on dark_red")
            internet_icon = em.get('📡')

        content = Text()
        content.append(f"{em.get('🔗', '[NET]')} IP Address: ", style="bold")
        content.append(f"{info['ip']}\n\n")
        content.append(f"{internet_icon} Internet:  ")
        content.append(internet_status)

        return Panel(content, title=f"[bold yellow]{em.get('🌍', '[WRLD]')} Network Status[/bold yellow]", border_style="yellow", box=ROUNDED)

    def create_config_panel(self):
        """Create configuration status panel with enhanced visuals"""
        status = self.get_config_status()

        if status['config_exists']:
            config_status = Text(f"{em.get('✓')} Found", style="bold green on dark_green")
            config_icon = em.get('📝')
        else:
            config_status = Text(f"{em.get('✗')} Missing", style="bold red on dark_red")
            config_icon = em.get('⚠')

        content = Text()
        content.append(f"{config_icon} Config:   ")
        content.append(config_status)
        content.append(f"\n\n{em.get('📂', '[DIR]')} Path: ", style="bold")
        content.append(f"\n   {status['config_path']}", style="dim")
        if status['active_template']:
            content.append(f"\n\n{em.get('📄', '[FILE]')} Template: ", style="bold")
            content.append(f"{status['active_template']}")

        return Panel(content, title=f"[bold blue]{em.get('⚙️')}  Configuration[/bold blue]", border_style="blue", box=ROUNDED)

    def show_dashboard(self):
        """Display the complete status dashboard with rich visuals"""
        console.clear()

        # Enhanced header with box
        header = Panel(
            Text("MESHTASTICD QUICK STATUS DASHBOARD", style="bold cyan", justify="center"),
            box=DOUBLE,
            border_style="bright_cyan",
            padding=(0, 2)
        )
        console.print(header)
        console.print()

        # Create panels
        service_panel = self.create_status_panel()
        system_panel = self.create_system_panel()
        network_panel = self.create_network_panel()
        config_panel = self.create_config_panel()

        # Display in two columns
        console.print(Columns([service_panel, system_panel], equal=True, expand=True))
        console.print()
        console.print(Columns([network_panel, config_panel], equal=True, expand=True))

        # Recent logs section with enhanced styling
        console.print()
        logs_header = Panel(
            Text(f"{em.get('📋')} Recent Activity", style="bold cyan"),
            box=ROUNDED,
            border_style="cyan",
            padding=(0, 1)
        )
        console.print(logs_header)

        logs = self.get_recent_logs(3)
        for i, log in enumerate(logs, 1):
            console.print(f"  [dim cyan]{i}.[/dim cyan] [dim]{log}[/dim]")

        # Quick actions with enhanced formatting
        console.print()
        actions_panel = Panel(
            f"[bold cyan]1[/bold cyan] {em.get('🔄')} Refresh  │  "
            f"[bold cyan]2[/bold cyan] {em.get('📜')} View Full Logs  │  "
            f"[bold cyan]3[/bold cyan] {em.get('🔁', '[RST]')} Restart Service  │  "
            f"[bold cyan]4[/bold cyan] {em.get('⬆️')}  Check Updates  │  "
            f"[bold cyan]5[/bold cyan] {em.get('⬅️')}  Back",
            title=f"[bold yellow]{em.get('⚡')} Quick Actions[/bold yellow]",
            box=ROUNDED,
            border_style="yellow"
        )
        console.print(actions_panel)

    def interactive_dashboard(self):
        """Interactive dashboard with auto-refresh option"""
        while True:
            self.show_dashboard()

            console.print()
            choice = Prompt.ask(
                "Select action",
                choices=["1", "2", "3", "4", "5"],
                default="5"
            )

            if choice == "1":
                continue  # Refresh
            elif choice == "2":
                self.show_full_logs()
            elif choice == "3":
                self.restart_service()
            elif choice == "4":
                self.check_updates()
            elif choice == "5":
                break

    def show_full_logs(self):
        """Show full logs with enhanced formatting"""
        console.clear()
        header = Panel(
            Text("📜 RECENT MESHTASTICD LOGS (LAST 30 LINES)", style="bold cyan", justify="center"),
            box=DOUBLE,
            border_style="bright_cyan"
        )
        console.print(header)
        console.print()

        logs = self.get_recent_logs(30)
        log_table = Table(show_header=False, box=ROUNDED, border_style="dim")
        log_table.add_column("#", style="dim cyan", width=4)
        log_table.add_column("Log Entry", style="dim")

        for i, log in enumerate(logs, 1):
            log_table.add_row(str(i), log)

        console.print(log_table)
        console.print()
        Prompt.ask("\n[bold yellow]Press Enter to continue[/bold yellow]")

    def restart_service(self):
        """Restart meshtasticd service"""
        if Confirm.ask("\n[yellow]Restart meshtasticd service?[/yellow]", default=False):
            console.print("[cyan]Restarting service...[/cyan]")
            try:
                result = subprocess.run(
                    ['systemctl', 'restart', 'meshtasticd'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    console.print(f"[green]{em.get('✓')} Service restarted successfully![/green]")
                else:
                    console.print(f"[red]{em.get('✗')} Failed to restart: {result.stderr}[/red]")
            except Exception as e:
                logger.error(f"Failed to restart service: {e}")
                console.print(f"[red]{em.get('✗')} Error: {str(e)}[/red]")
            time.sleep(2)

    def check_updates(self):
        """Check for available updates with enhanced visuals"""
        console.print(f"\n[cyan]{em.get('🔍')} Checking for updates...[/cyan]")

        from installer.version import VersionManager
        vm = VersionManager()

        update_info = vm.check_for_updates()

        if update_info:
            if update_info.get('update_available'):
                update_panel = Panel(
                    f"[bold]Current:[/bold] {update_info['current']}\n"
                    f"[bold]Latest:[/bold]  [green]{update_info['latest']}[/green]\n\n"
                    f"[yellow]{em.get('⬆️')}  An update is available![/yellow]",
                    title=f"[bold green]{em.get('🎉', '[!]')} Update Available[/bold green]",
                    border_style="green",
                    box=ROUNDED
                )
                console.print(update_panel)
            else:
                success_panel = Panel(
                    f"[bold]Version:[/bold] {update_info['current']}\n\n"
                    f"[green]{em.get('✓')} You're running the latest version![/green]",
                    title=f"[bold cyan]{em.get('✨', '[*]')} Up to Date[/bold cyan]",
                    border_style="cyan",
                    box=ROUNDED
                )
                console.print(success_panel)
        else:
            error_panel = Panel(
                f"[yellow]{em.get('⚠')}  Could not check for updates\n"
                "Please check your internet connection[/yellow]",
                title="[bold red]Update Check Failed[/bold red]",
                border_style="red",
                box=ROUNDED
            )
            console.print(error_panel)

        Prompt.ask("\n[bold yellow]Press Enter to continue[/bold yellow]")

    def get_quick_status_line(self):
        """Get a single-line status summary for the main menu with enhanced visuals"""
        service = self.get_service_status()
        version = self.get_installed_version()
        info = self.get_system_info()

        if service['running']:
            status = f"[green]{em.get('🟢')} Running[/green]"
        else:
            status = f"[red]{em.get('🔴')} Stopped[/red]"

        # Color code temperature
        temp_color = 'green' if info['temp_status'] == 'normal' else ('yellow' if info['temp_status'] == 'warning' else 'red')

        return f"{status} │ {em.get('📦')} {version} │ {em.get('🌡️')}  [{temp_color}]{info['cpu_temp']}[/{temp_color}] │ {em.get('💾')} {info['memory']}"
