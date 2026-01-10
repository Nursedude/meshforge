"""Quick Status Dashboard for Meshtasticd - Simplified Working Version

Uses the unified commands layer for service status checks.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED, HEAVY
import logging

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import emoji as em

# Import unified commands layer
try:
    from commands import service, hardware
    COMMANDS_AVAILABLE = True
except ImportError:
    COMMANDS_AVAILABLE = False

console = Console()
logger = logging.getLogger(__name__)


class StatusDashboard:
    """Quick Status Dashboard showing system and mesh network status"""

    def __init__(self):
        self.config_path = Path('/etc/meshtasticd/config.yaml')
        self.log_path = Path('/var/log/meshtasticd.log')

    def get_service_status(self):
        """Get meshtasticd service status using commands layer"""
        if COMMANDS_AVAILABLE:
            try:
                result = service.check_status('meshtasticd')
                return {
                    'status': result.data.get('status', 'unknown'),
                    'running': result.data.get('running', False),
                    'color': 'green' if result.data.get('running') else 'red'
                }
            except Exception as e:
                logger.error(f"Failed to get service status: {e}")
                return {'status': 'unknown', 'running': False, 'color': 'yellow'}
        else:
            # Fallback to direct subprocess call
            import subprocess
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

    def get_installed_version(self):
        """Get installed meshtasticd version using commands layer"""
        if COMMANDS_AVAILABLE:
            try:
                result = service.get_version('meshtasticd')
                if result.success:
                    return result.data.get('version', 'Unknown')
            except Exception as e:
                logger.error(f"Failed to get version: {e}")
            return 'Unknown'
        else:
            # Fallback to direct subprocess call
            import subprocess
            try:
                result = subprocess.run(
                    ['meshtasticd', '--version'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception as e:
                logger.error(f"Failed to get version: {e}")
            return 'Unknown'

    def get_system_info(self):
        """Get system information - CPU temp, memory, disk"""
        info = {}

        # CPU temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
                info['cpu_temp'] = f'{temp:.1f}°C'
        except (FileNotFoundError, ValueError, PermissionError):
            info['cpu_temp'] = 'N/A'

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
                else:
                    info['memory'] = 'N/A'
        except (FileNotFoundError, ValueError, KeyError, PermissionError):
            info['memory'] = 'N/A'

        # Disk usage
        try:
            statvfs = os.statvfs('/')
            total = statvfs.f_blocks * statvfs.f_frsize
            free = statvfs.f_bavail * statvfs.f_frsize
            used = total - free
            if total > 0:
                usage_pct = (used / total) * 100
                info['disk'] = f'{usage_pct:.1f}%'
            else:
                info['disk'] = 'N/A'
        except (OSError, ZeroDivisionError):
            info['disk'] = 'N/A'

        return info

    def show_dashboard(self):
        """Display the complete status dashboard - SIMPLE VERSION"""
        console.clear()

        # Service Status Table
        service = self.get_service_status()
        status_table = Table(title=f"{em.get('📡')} Meshtasticd Service", box=ROUNDED, show_header=False)
        status_table.add_column("Property", style="cyan")
        status_table.add_column("Value", style="green")

        status_icon = em.get('🟢') if service['running'] else em.get('🔴')
        status_str = "RUNNING" if service['running'] else "STOPPED"
        status_table.add_row(f"{status_icon} Status", status_str)
        status_table.add_row(f"{em.get('📦')} Version", self.get_installed_version())

        console.print(status_table)
        console.print()

        # System Info Table
        info = self.get_system_info()
        system_table = Table(title=f"{em.get('⚙️')} System Health", box=ROUNDED, show_header=False)
        system_table.add_column("Property", style="cyan")
        system_table.add_column("Value", style="green")

        system_table.add_row(f"{em.get('🌡️')} CPU Temp", info['cpu_temp'])
        system_table.add_row(f"{em.get('💾')} Memory", info['memory'])
        system_table.add_row(f"{em.get('💿')} Disk", info['disk'])

        console.print(system_table)
        console.print()

        # Quick Status Line
        console.print(self.get_quick_status_line())

    def get_quick_status_line(self):
        """Get a single line status for menu display"""
        service = self.get_service_status()
        info = self.get_system_info()

        status_icon = em.get('🟢') if service['running'] else em.get('🔴')
        version = self.get_installed_version()

        return Text(
            f"{status_icon} Service | {em.get('📦')} {version} | {em.get('🌡️')} {info['cpu_temp']} | "
            f"{em.get('💾')} {info['memory']} | {em.get('💿')} {info['disk']}",
            style="dim"
        )

    def interactive_dashboard(self):
        """Interactive dashboard loop"""
        while True:
            try:
                self.show_dashboard()

                console.print("\n[bold cyan]Dashboard Options[/bold cyan]")
                console.print(f"  [bold]1[/bold]. {em.get('🔄')} Refresh")
                console.print(f"  [bold]2[/bold]. {em.get('⬅️')} Back to Menu")

                from rich.prompt import Prompt
                choice = Prompt.ask("[cyan]Select[/cyan]", choices=["1", "2"], default="1")

                if choice == "2":
                    break
                # else refresh loop continues

            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                break


if __name__ == '__main__':
    dashboard = StatusDashboard()
    dashboard.interactive_dashboard()
