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
from utils.safe_import import safe_import

# Module-level imports
from commands import service, hardware
COMMANDS_AVAILABLE = True

from utils.service_check import check_service, ServiceState
SERVICE_CHECK_AVAILABLE = True

_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')

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
                status_data = result.data

                # Check for misconfiguration (SPI HAT with USB placeholder)
                message = status_data.get('message', '')
                if 'WRONG CONFIG' in message:
                    return {
                        'status': 'misconfigured',
                        'running': False,
                        'color': 'red',
                        'message': 'SPI HAT needs native daemon'
                    }

                return {
                    'status': status_data.get('status', 'unknown'),
                    'running': status_data.get('running', False),
                    'color': 'green' if status_data.get('running') else 'red',
                    'message': message
                }
            except Exception as e:
                logger.error(f"Failed to get service status: {e}")
                return {'status': 'unknown', 'running': False, 'color': 'yellow', 'message': str(e)}
        else:
            # Fallback to centralized service checker or direct subprocess call
            if SERVICE_CHECK_AVAILABLE:
                try:
                    status = check_service('meshtasticd')
                    # Map ServiceState to dashboard status format
                    if status.state == ServiceState.AVAILABLE:
                        return {
                            'status': 'active',
                            'running': True,
                            'color': 'green',
                            'message': status.message
                        }
                    elif status.state == ServiceState.DEGRADED:
                        # Check if it's a SPI HAT misconfiguration
                        if 'WRONG CONFIG' in status.message:
                            return {
                                'status': 'misconfigured',
                                'running': False,
                                'color': 'red',
                                'message': 'SPI HAT needs native daemon'
                            }
                        return {
                            'status': 'degraded',
                            'running': False,
                            'color': 'yellow',
                            'message': status.message
                        }
                    elif 'USB mode' in status.message or 'placeholder' in status.message:
                        return {
                            'status': 'placeholder',
                            'running': False,
                            'color': 'yellow',
                            'message': 'USB mode (no daemon)'
                        }
                    else:
                        return {
                            'status': status.state.value,
                            'running': False,
                            'color': 'red',
                            'message': status.message
                        }
                except Exception as e:
                    logger.error(f"Failed to get service status via service_check: {e}")
                    return {'status': 'unknown', 'running': False, 'color': 'yellow', 'message': str(e)}
            else:
                # Final fallback to direct subprocess call
                import subprocess
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', 'meshtasticd'],
                        capture_output=True, text=True, timeout=5
                    )
                    status = result.stdout.strip()

                    # Check SubState to detect placeholder services
                    if status == 'active':
                        state_result = subprocess.run(
                            ['systemctl', 'show', 'meshtasticd', '--property=SubState'],
                            capture_output=True, text=True, timeout=5
                        )
                        sub_state = state_result.stdout.strip().split('=')[-1] if '=' in state_result.stdout else ''

                        # Exited = placeholder service
                        if sub_state == 'exited':
                            # Check for SPI HAT mismatch
                            spi_exists = list(Path('/dev').glob('spidev*'))
                            usb_exists = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
                            if spi_exists and not usb_exists:
                                return {
                                    'status': 'misconfigured',
                                    'running': False,
                                    'color': 'red',
                                    'message': 'SPI HAT needs native daemon'
                                }
                            return {
                                'status': 'placeholder',
                                'running': False,
                                'color': 'yellow',
                                'message': 'USB mode (no daemon)'
                            }

                    return {
                        'status': status,
                        'running': status == 'active',
                        'color': 'green' if status == 'active' else 'red',
                        'message': ''
                    }
                except Exception as e:
                    logger.error(f"Failed to get service status: {e}")
                    return {'status': 'unknown', 'running': False, 'color': 'yellow', 'message': str(e)}

    def get_installed_version(self):
        """Get installed meshtasticd version or connection mode."""
        import shutil

        # First check for native meshtasticd binary
        meshtasticd_path = shutil.which('meshtasticd')
        if meshtasticd_path:
            if COMMANDS_AVAILABLE:
                try:
                    result = service.get_version('meshtasticd')
                    if result.success:
                        return result.data.get('version', 'Native')
                except Exception as e:
                    logger.error(f"Failed to get version: {e}")
                return 'Native'
            else:
                import subprocess
                try:
                    result = subprocess.run(
                        ['meshtasticd', '--version'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        return result.stdout.strip()
                except Exception:
                    pass
                return 'Native'

        # No native daemon - check what hardware exists
        spi_devices = list(Path('/dev').glob('spidev*'))
        usb_devices = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))

        if spi_devices and not usb_devices:
            # SPI HAT but no native daemon - this is a problem
            return 'SPI (needs daemon)'

        if usb_devices:
            return f'USB ({usb_devices[0].name})'

        # Check for meshtastic Python package
        if _HAS_MESHTASTIC:
            return f'CLI {getattr(_meshtastic, "__version__", "")}'.strip()

        return 'Not installed'

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
        svc = self.get_service_status()
        status_table = Table(title=f"{em.get('📡')} Meshtasticd Service", box=ROUNDED, show_header=False)
        status_table.add_column("Property", style="cyan")
        status_table.add_column("Value", style="green")

        # Status with color based on state
        if svc['status'] == 'misconfigured':
            status_icon = em.get('🔴')
            status_str = "[red]MISCONFIGURED[/red]"
            status_style = "red"
        elif svc['running']:
            status_icon = em.get('🟢')
            status_str = "[green]RUNNING[/green]"
            status_style = "green"
        elif svc['status'] == 'placeholder':
            status_icon = em.get('🟡')
            status_str = "[yellow]USB MODE[/yellow]"
            status_style = "yellow"
        else:
            status_icon = em.get('🔴')
            status_str = "[red]STOPPED[/red]"
            status_style = "red"

        status_table.add_row(f"{status_icon} Status", status_str)
        status_table.add_row(f"{em.get('📦')} Version", self.get_installed_version())

        # Show message if there's a problem
        if svc.get('message') and 'needs' in svc.get('message', '').lower():
            status_table.add_row(f"{em.get('⚠️')} Issue", f"[red]{svc['message']}[/red]")

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
        svc = self.get_service_status()
        info = self.get_system_info()

        # Status indicator with appropriate icon
        if svc['status'] == 'misconfigured':
            status_icon = em.get('🔴')
            status_text = "[red]MISCONFIG[/red]"
        elif svc['running']:
            status_icon = em.get('🟢')
            status_text = "[green]Running[/green]"
        elif svc['status'] == 'placeholder':
            status_icon = em.get('🟡')
            status_text = "[yellow]USB[/yellow]"
        else:
            status_icon = em.get('🔴')
            status_text = "[red]Stopped[/red]"

        version = self.get_installed_version()

        return Text.from_markup(
            f"{status_icon} {status_text} | {em.get('📦')} {version} | {em.get('🌡️')} {info['cpu_temp']} | "
            f"{em.get('💾')} {info['memory']} | {em.get('💿')} {info['disk']}"
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
