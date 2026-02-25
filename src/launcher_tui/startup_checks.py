"""
MeshForge Startup Checks - Environment Detection & Conflict Resolution

Detects the runtime environment at startup:
- Service states (meshtasticd, rnsd, etc.)
- Port usage and conflicts
- Hardware (SPI, USB serial devices)
- First-run status

This module enables "no-dependencies" startup where MeshForge can
launch and display status even if services aren't running.

Usage:
    from startup_checks import StartupChecker, EnvironmentState

    checker = StartupChecker()
    env = checker.check_all()

    if env.conflicts:
        # Handle port conflicts
        for conflict in env.conflicts:
            print(f"Port {conflict.port} used by {conflict.process}")
"""

import os
import re
import socket
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

# Service check utilities — first-party, always available (direct import per Issue #5)
from utils.service_check import (
    check_service, ServiceState, ServiceStatus,
    check_udp_port, check_rns_shared_instance,
)

from utils import ports
MESHTASTICD_PORT = ports.MESHTASTICD_PORT
MESHTASTICD_WEB_PORT = ports.MESHTASTICD_WEB_PORT
RNS_SHARED_INSTANCE_PORT = ports.RNS_SHARED_INSTANCE_PORT
RNS_TCP_SERVER_PORT = ports.RNS_TCP_SERVER_PORT
MQTT_PORT = ports.MQTT_PORT

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home

from utils.paths import ReticulumPaths


class ServiceRunState(Enum):
    """Simple service state for display."""
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ServiceInfo:
    """Information about a service."""
    name: str
    state: ServiceRunState
    enabled_at_boot: bool = False
    pid: Optional[int] = None
    port: Optional[int] = None
    port_open: bool = False
    detection_method: str = ""


@dataclass
class PortConflict:
    """Information about a port conflict."""
    port: int
    expected_service: str
    actual_process: str
    actual_pid: int
    resolution_options: List[str] = field(default_factory=list)


@dataclass
class HardwareInfo:
    """Detected hardware information."""
    spi_available: bool = False
    spi_devices: List[str] = field(default_factory=list)
    i2c_available: bool = False
    i2c_devices: List[str] = field(default_factory=list)
    usb_serial_devices: List[Dict[str, str]] = field(default_factory=list)
    gpio_available: bool = False


@dataclass
class EnvironmentState:
    """Complete environment state at startup."""
    # Services
    services: Dict[str, ServiceInfo] = field(default_factory=dict)

    # Port conflicts
    conflicts: List[PortConflict] = field(default_factory=list)

    # Hardware
    hardware: HardwareInfo = field(default_factory=HardwareInfo)

    # System info
    is_root: bool = False
    has_display: bool = False
    display_type: Optional[str] = None
    is_ssh: bool = False

    # First run
    is_first_run: bool = False
    config_exists: bool = False

    # System-level warnings (udev bugs, packaging issues, etc.)
    system_warnings: List[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        """Check if there are any port conflicts."""
        return len(self.conflicts) > 0

    @property
    def all_services_running(self) -> bool:
        """Check if all expected services are running and functional.

        A service with state RUNNING but port not bound (zombie) is
        not considered fully running.
        """
        return all(
            s.state == ServiceRunState.RUNNING and (not s.port or s.port_open)
            for s in self.services.values()
        )

    def get_status_line(self, plain: bool = False) -> str:
        """Generate a one-line status summary for display.

        Args:
            plain: If True, use text indicators instead of ANSI color codes.
                   Use plain=True for whiptail/dialog menus which don't render ANSI.
        """
        parts = []
        for name, info in self.services.items():
            if info.state == ServiceRunState.RUNNING:
                if plain:
                    parts.append(f"{name}: UP")
                else:
                    parts.append(f"{name} \033[32m●\033[0m")
            elif info.state == ServiceRunState.FAILED:
                if plain:
                    parts.append(f"{name}: FAIL")
                else:
                    parts.append(f"{name} \033[31m●\033[0m")
            else:
                if plain:
                    parts.append(f"{name}: --")
                else:
                    parts.append(f"{name} \033[2m○\033[0m")

        status = "  ".join(parts)

        if self.conflicts:
            if plain:
                status += f"  ! {len(self.conflicts)} conflict(s)"
            else:
                status += f"  \033[33m⚠ {len(self.conflicts)} conflict(s)\033[0m"

        return status

    def get_alerts(self) -> List[str]:
        """Get list of alert messages to display."""
        alerts = []

        for conflict in self.conflicts:
            alerts.append(
                f"Port {conflict.port} conflict: {conflict.actual_process} "
                f"(PID {conflict.actual_pid}) blocking {conflict.expected_service}"
            )

        for name, info in self.services.items():
            if info.state == ServiceRunState.FAILED:
                alerts.append(f"Service {name} has FAILED - check logs")
            # Note: We intentionally don't alert on "running but not enabled at boot"
            # since service is working - boot-enable is a user preference, not an issue

        return alerts


class StartupChecker:
    """Performs environment checks at MeshForge startup."""

    # Services to check with their expected ports
    SERVICES_TO_CHECK = {
        'meshtasticd': {'port': MESHTASTICD_PORT, 'port_type': 'tcp', 'systemd': True},
        'rnsd': {'port': RNS_SHARED_INSTANCE_PORT, 'port_type': 'unix_socket', 'systemd': True},
    }

    # Ports that MeshForge needs
    REQUIRED_PORTS = {
        MESHTASTICD_PORT: 'meshtasticd',
        RNS_SHARED_INSTANCE_PORT: 'rnsd',
    }

    def __init__(self):
        self._cache: Optional[EnvironmentState] = None

    def check_all(self, use_cache: bool = False) -> EnvironmentState:
        """
        Run all environment checks.

        Args:
            use_cache: If True, return cached result if available

        Returns:
            EnvironmentState with complete environment info
        """
        if use_cache and self._cache is not None:
            return self._cache

        env = EnvironmentState()

        # System info
        env.is_root = os.geteuid() == 0
        env.has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
        env.display_type = 'Wayland' if os.environ.get('WAYLAND_DISPLAY') else (
            'X11' if os.environ.get('DISPLAY') else None
        )
        env.is_ssh = bool(os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'))

        # Ensure RNS storage directories exist (self-healing)
        # Prevents rnsd PermissionError on /etc/reticulum/storage/ratchets
        # Runs as root or non-root — gracefully degrades if no permissions
        self._heal_rns_storage_dirs()

        # Check services
        env.services = self._check_services()

        # Check for port conflicts
        env.conflicts = self._check_port_conflicts(env.services)

        # Check hardware
        env.hardware = self._check_hardware()

        # Check first run status
        env.is_first_run, env.config_exists = self._check_first_run()

        # Detect system-level issues (udev packaging bugs, etc.)
        alsa_warning = self._check_alsa_udev()
        if alsa_warning:
            env.system_warnings.append(alsa_warning)

        self._cache = env
        return env

    def invalidate_cache(self):
        """Clear the cached environment state."""
        self._cache = None

    def _heal_rns_storage_dirs(self):
        """Ensure RNS storage directories exist with correct permissions.

        RNS requires several subdirectories under /etc/reticulum/storage/:
        - ratchets/ (Identity.persist_job key ratcheting)
        - resources/ (Reticulum.__init__ resource storage)
        - cache/announces/ (Transport announce caching)

        This method ONLY creates missing directories and fixes permissions.
        It never restarts rnsd — that's rnsd's job via systemd.  If rnsd is
        crashing due to missing dirs, the health monitor will report it and
        the user can restart from the service menu.

        Previous versions auto-restarted rnsd here, which caused the #1
        "gotcha": running sudo meshforge would restart rnsd under root
        context, regenerating shared_instance auth tokens and breaking
        RNS connectivity for the normal user.
        """
        if not ReticulumPaths.ensure_system_dirs():
            logger.debug("Could not create /etc/reticulum directories")
            return

        logger.debug("RNS storage directories verified")

    @staticmethod
    def _has_permission_issues(dir_path: Path) -> bool:
        """Check if any files inside dir_path are not writable by non-root users.

        Returns True if there are files that could cause PermissionError
        for rnsd Transport jobs.

        Note: Cannot use os.access(os.W_OK) here because MeshForge runs
        as root (sudo), and root always passes access checks regardless
        of actual file mode bits.  Instead, inspect the mode directly:
        files need 0o666 (world-writable) and directories need 0o777.
        """
        import stat

        try:
            if not dir_path.is_dir():
                return False

            # Check directory itself — needs world-writable for rnsd
            dir_mode = dir_path.stat().st_mode
            if not (dir_mode & stat.S_IWOTH):
                return True

            for entry in dir_path.iterdir():
                try:
                    mode = entry.stat().st_mode
                    if entry.is_file() and not (mode & stat.S_IWOTH):
                        return True
                    elif entry.is_dir() and not (mode & stat.S_IWOTH):
                        return True
                except (PermissionError, OSError):
                    return True
        except (PermissionError, OSError):
            return True
        return False

    def _check_services(self) -> Dict[str, ServiceInfo]:
        """Check status of all known services."""
        services = {}

        for name, config in self.SERVICES_TO_CHECK.items():
            info = ServiceInfo(name=name, state=ServiceRunState.UNKNOWN)
            info.port = config.get('port')

            if config.get('systemd'):
                # Check via systemctl
                state, enabled, pid = self._check_systemd_service(name)
                info.state = state
                info.enabled_at_boot = enabled
                info.pid = pid
                info.detection_method = 'systemctl'
            else:
                # Check via process/port
                state, pid = self._check_process_service(name, config)
                info.state = state
                info.pid = pid
                info.detection_method = 'process'

            # Also check if port is actually open
            if info.port:
                port_type = config.get('port_type', 'tcp')
                info.port_open = self._check_port(info.port, port_type)

            services[name] = info

        return services

    def _check_systemd_service(self, name: str) -> Tuple[ServiceRunState, bool, Optional[int]]:
        """Check a systemd service status."""
        state = ServiceRunState.UNKNOWN
        enabled = False
        pid = None

        try:
            # Check if active
            result = subprocess.run(
                ['systemctl', 'is-active', name],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()

            if status == 'active':
                state = ServiceRunState.RUNNING
            elif status == 'failed':
                state = ServiceRunState.FAILED
            elif status in ('inactive', 'dead'):
                state = ServiceRunState.STOPPED
            else:
                state = ServiceRunState.UNKNOWN

            # Check if enabled
            result = subprocess.run(
                ['systemctl', 'is-enabled', name],
                capture_output=True, text=True, timeout=5
            )
            enabled = result.returncode == 0

            # Get PID if running
            if state == ServiceRunState.RUNNING:
                result = subprocess.run(
                    ['systemctl', 'show', '-p', 'MainPID', name],
                    capture_output=True, text=True, timeout=5
                )
                match = re.search(r'MainPID=(\d+)', result.stdout)
                if match:
                    pid = int(match.group(1))

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Error checking systemd service {name}: {e}")

        return state, enabled, pid

    def _check_process_service(self, name: str, config: dict) -> Tuple[ServiceRunState, Optional[int]]:
        """Check a non-systemd service via process list."""
        state = ServiceRunState.STOPPED
        pid = None

        try:
            # Try pgrep first
            result = subprocess.run(
                ['pgrep', '-x', name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                pid = int(pids[0])
                state = ServiceRunState.RUNNING

            # If pgrep didn't find it, check the port
            if state == ServiceRunState.STOPPED and config.get('port'):
                port_type = config.get('port_type', 'tcp')
                if self._check_port(config['port'], port_type):
                    # Port is open but pgrep didn't find the process
                    # Service might be running under different name
                    state = ServiceRunState.RUNNING

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Error checking process service {name}: {e}")

        return state, pid

    def _check_port(self, port: int, port_type: str = 'tcp') -> bool:
        """Check if a port is open.

        For RNS (unix_socket), uses check_rns_shared_instance() which checks
        abstract domain sockets (Linux default) and falls back to TCP/UDP.
        For UDP ports, uses centralized check_udp_port().
        """
        try:
            if port_type == 'unix_socket':
                return check_rns_shared_instance()
            if port_type == 'udp':
                return check_udp_port(port)

            sock_type = socket.SOCK_STREAM if port_type == 'tcp' else socket.SOCK_DGRAM
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.settimeout(1)
                if port_type == 'tcp':
                    result = sock.connect_ex(('127.0.0.1', port))
                    return result == 0
                else:
                    # Fallback UDP bind test (unreliable with SO_REUSEADDR)
                    try:
                        sock.bind(('127.0.0.1', port))
                        return False
                    except OSError:
                        return True
        except OSError as e:
            logger.debug("Port check for %d failed: %s", port, e)
            return False

    def _check_port_conflicts(self, services: Dict[str, ServiceInfo]) -> List[PortConflict]:
        """Check for port conflicts."""
        conflicts = []

        for port, expected_service in self.REQUIRED_PORTS.items():
            # Skip if the expected service is running and owns the port
            service_info = services.get(expected_service)
            if service_info and service_info.state == ServiceRunState.RUNNING:
                continue

            # Check if something else is using the port
            process_info = self._get_port_owner(port)
            if process_info:
                process_name, pid = process_info

                # Skip if it's the expected service (might be detected differently)
                if process_name == expected_service:
                    continue

                conflict = PortConflict(
                    port=port,
                    expected_service=expected_service,
                    actual_process=process_name,
                    actual_pid=pid,
                    resolution_options=[
                        f"Stop {process_name}: sudo kill {pid}",
                        f"Configure {expected_service} to use different port",
                        "Continue anyway (may cause errors)",
                    ]
                )
                conflicts.append(conflict)

        return conflicts

    def _get_port_owner(self, port: int) -> Optional[Tuple[str, int]]:
        """Get the process using a port."""
        try:
            # Use ss command to find port owner
            result = subprocess.run(
                ['ss', '-tlnp', f'sport = :{port}'],
                capture_output=True, text=True, timeout=5
            )

            # Parse ss output
            for line in result.stdout.splitlines():
                if f':{port}' in line:
                    # Extract PID and process name
                    # Format: ... users:(("process",pid=12345,fd=3))
                    match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
                    if match:
                        return match.group(1), int(match.group(2))

            # Try UDP if TCP didn't find anything
            result = subprocess.run(
                ['ss', '-ulnp', f'sport = :{port}'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f':{port}' in line:
                    match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
                    if match:
                        return match.group(1), int(match.group(2))

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Error getting port owner for {port}: {e}")

        return None

    def _check_hardware(self) -> HardwareInfo:
        """Detect available hardware."""
        hw = HardwareInfo()

        # Check SPI
        hw.spi_available = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        hw.spi_devices = [
            str(p) for p in Path('/dev').glob('spidev*')
        ]

        # Check I2C
        hw.i2c_available = Path('/dev/i2c-1').exists() or Path('/dev/i2c-0').exists()
        hw.i2c_devices = [
            str(p) for p in Path('/dev').glob('i2c-*')
        ]

        # Check GPIO
        hw.gpio_available = Path('/dev/gpiomem').exists() or Path('/sys/class/gpio').exists()

        # Find USB serial devices (potential Meshtastic devices)
        hw.usb_serial_devices = self._find_usb_serial_devices()

        return hw

    def _find_usb_serial_devices(self) -> List[Dict[str, str]]:
        """Find USB serial devices that might be Meshtastic radios."""
        devices = []

        # Common device paths
        for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
            for path in Path('/dev').glob(pattern.replace('/dev/', '')):
                device = {'path': str(path), 'name': 'Unknown'}

                # Try to get device info from udevadm
                try:
                    result = subprocess.run(
                        ['udevadm', 'info', '--query=property', str(path)],
                        capture_output=True, text=True, timeout=5
                    )
                    props = {}
                    for line in result.stdout.splitlines():
                        if '=' in line:
                            key, value = line.split('=', 1)
                            props[key] = value

                    # Build device name from properties
                    vendor = props.get('ID_VENDOR', '')
                    model = props.get('ID_MODEL', '')
                    if vendor or model:
                        device['name'] = f"{vendor} {model}".strip()

                    # Check if this is likely a Meshtastic device
                    device['likely_meshtastic'] = any(
                        kw in (vendor + model).lower()
                        for kw in ['meshtastic', 't-beam', 'heltec', 'rak', 'lilygo', 'cp210', 'ch340']
                    )

                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("USB device detection for %s failed: %s", path, e)

                devices.append(device)

        return devices

    def _check_first_run(self) -> Tuple[bool, bool]:
        """
        Check if this is first run.

        Returns:
            Tuple of (is_first_run, config_exists)
        """
        config_dir = get_real_user_home() / ".config" / "meshforge"
        flag_file = config_dir / ".meshforge_setup_complete"
        settings_file = config_dir / "settings.json"

        is_first_run = not flag_file.exists()
        config_exists = settings_file.exists()

        return is_first_run, config_exists

    def _check_alsa_udev(self) -> Optional[str]:
        """Detect broken ALSA udev rules (RPi OS packaging bug).

        Returns a warning string if broken, None if OK or not applicable.
        """
        override = Path("/etc/udev/rules.d/90-alsa-restore.rules")
        pkg_rules = Path("/usr/lib/udev/rules.d/90-alsa-restore.rules")

        if override.exists() or not pkg_rules.exists():
            return None

        try:
            content = pkg_rules.read_text()
            gotos = set(re.findall(r'GOTO="([^"]+)"', content))
            labels = set(re.findall(r'LABEL="([^"]+)"', content))
            missing = gotos - labels
            if missing:
                return (
                    f"ALSA udev rules have broken GOTO labels: {missing} "
                    f"(fix: sudo python3 -c "
                    f"\"from utils.udev_fix import fix_broken_udev_rules; "
                    f"fix_broken_udev_rules()\")"
                )
        except OSError:
            pass
        return None


def resolve_conflict(conflict: PortConflict, action: str = 'stop') -> bool:
    """
    Attempt to resolve a port conflict.

    Args:
        conflict: The conflict to resolve
        action: 'stop' to kill the process, 'skip' to ignore

    Returns:
        True if resolved successfully
    """
    if action == 'stop':
        try:
            subprocess.run(
                ['kill', str(conflict.actual_pid)],
                capture_output=True, timeout=5
            )
            # Wait a moment and verify
            import time
            time.sleep(1)

            # Check if process is gone
            result = subprocess.run(
                ['ps', '-p', str(conflict.actual_pid)],
                capture_output=True, timeout=5
            )
            return result.returncode != 0  # Process is gone

        except Exception as e:
            logger.error(f"Failed to stop process {conflict.actual_pid}: {e}")
            return False

    elif action == 'skip':
        return True  # User chose to continue anyway

    return False
