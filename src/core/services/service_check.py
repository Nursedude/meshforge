"""
Service Availability Utilities for MeshForge

Provides standardized service checking before connecting to external services.
Use these instead of assuming services are running.

ARCHITECTURE (Issue #17 redesign, Issue #20 completion):
    - For systemd services: Trust systemctl ONLY (single source of truth)
    - Port/process checks kept for utilities but NOT used in check_service()
    - "Unknown" state is better than wrong state from conflicting methods
    - Active services always trusted (no port fallback for transitional states)

Usage:
    from utils.service_check import check_port, check_service, ServiceStatus
    from utils.ports import MESHTASTICD_PORT

    # Quick port check (utility function)
    if check_port(MESHTASTICD_PORT):
        connect_to_meshtasticd()

    # Full service check - trusts systemctl for systemd services
    status = check_service('meshtasticd')
    if not status.available:
        show_error(status.message)
        show_fix(status.fix_hint)
"""

import os
import re
import socket
import subprocess
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from enum import Enum

from utils.ports import MESHTASTICD_PORT, MQTT_PORT, RNS_SHARED_INSTANCE_PORT

logger = logging.getLogger(__name__)


def _sudo_cmd(cmd: List[str]) -> List[str]:
    """Prefix a command with 'sudo' when MeshForge is not running as root.

    Allows MeshForge to run as a normal user and only elevate for
    specific operations (systemctl, iptables, etc.).  When already root,
    returns the command unchanged.

    Args:
        cmd: Command and arguments, e.g. ['systemctl', 'restart', 'rnsd']

    Returns:
        The command, possibly prefixed with ['sudo'].
    """
    if os.geteuid() != 0:
        return ['sudo'] + cmd
    return cmd

# Public API - these are the functions/classes intended for external use
__all__ = [
    # Main entry points
    'check_service',        # Primary status checker (SINGLE SOURCE OF TRUTH)
    'require_service',      # Check with exception on failure
    'check_port',           # TCP port check (utility)
    'check_udp_port',       # UDP port check (utility)
    'check_rns_shared_instance',  # RNS shared instance check (domain socket + TCP + UDP)
    'get_rns_shared_instance_info',  # RNS shared instance diagnostics
    'get_udp_port_owner',   # UDP port owner lookup (process name + PID)
    'check_process_running', # Process check via pgrep (utility)
    'check_systemd_service', # Systemd status check
    # Service management
    'daemon_reload',             # Reload systemd daemon
    'enable_service',            # Enable service at boot
    'disable_service',           # Disable service at boot
    'start_service',             # Start a systemd service
    'stop_service',              # Stop a systemd service
    'restart_service',           # Restart a systemd service
    'apply_config_and_restart',  # Reload daemon + restart service
    # Port lockdown (MeshForge owns the browser)
    'lock_port_external',        # Block external access to a port
    'unlock_port_external',      # Restore external access to a port
    'check_port_locked',         # Check if port is locked to localhost
    'persist_iptables',          # Save iptables rules to survive reboot
    # Privilege elevation & file I/O
    '_sudo_cmd',            # Prefix command with sudo when not root
    '_sudo_write',          # Write file content with privilege elevation
    # Data classes
    'ServiceStatus',        # Return type from check_service
    'ServiceState',         # Status enum (AVAILABLE, DEGRADED, FAILED, etc.)
    # Configuration
    'KNOWN_SERVICES',       # Service configuration dict
    # MeshCore
    'check_meshcore_device',  # Check if MeshCore companion radio is connected
]


class ServiceState(Enum):
    """Service availability states."""
    AVAILABLE = "available"
    DEGRADED = "degraded"       # Running but with issues
    FAILED = "failed"           # Service crashed or failed to start
    NOT_RUNNING = "not_running"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"         # Cannot determine state


@dataclass
class ServiceStatus:
    """Result of a service availability check."""
    name: str
    available: bool
    state: ServiceState
    message: str
    fix_hint: str = ""
    port: Optional[int] = None
    # Additional context (Phase 2: separate service state from detection)
    detection_method: str = ""  # How was this determined

    def __bool__(self) -> bool:
        return self.available


# Known services and their configurations
# Port numbers imported from utils.ports for centralization
# NOTE: is_systemd=True means we ONLY trust systemctl for status
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': MESHTASTICD_PORT,
        'systemd_name': 'meshtasticd',
        'is_systemd': True,  # Trust systemctl only
        'description': 'Meshtastic daemon',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd',
    },
    'rnsd': {
        'port': RNS_SHARED_INSTANCE_PORT,
        'port_type': 'unix_socket',  # RNS uses abstract domain sockets on Linux
        'systemd_name': 'rnsd',
        'is_systemd': True,  # rnsd runs as systemd service (install_noc.sh creates unit)
        'description': 'Reticulum Network Stack daemon',
        'fix_hint': 'Start with: sudo systemctl start rnsd',
    },
    'mosquitto': {
        'port': MQTT_PORT,
        'systemd_name': 'mosquitto',
        'is_systemd': True,
        'description': 'MQTT broker',
        'fix_hint': 'Start with: sudo systemctl start mosquitto',
    },
    'nomadnet': {
        'port': None,  # NomadNet uses RNS shared instance, no dedicated port
        'systemd_name': 'nomadnet',
        'is_systemd': False,  # NomadNet is a user-space app, NOT a systemd service
        'description': 'NomadNet mesh messaging client',
        'fix_hint': 'Start with: nomadnetwork (run as user, not root)',
    },
    'meshcore': {
        'port': None,  # MeshCore has no daemon — MeshForge connects directly
        'systemd_name': None,
        'is_systemd': False,  # No daemon process; managed by MeshForge gateway
        'description': 'MeshCore companion radio (managed by MeshForge)',
        'fix_hint': 'Connect MeshCore companion radio via USB. Install: pip install meshcore',
    },
}


def _detect_radio_hardware() -> dict:
    """
    Detect what Meshtastic radio hardware is present.

    Returns:
        dict with:
            has_spi: bool - SPI devices present (/dev/spidev*)
            has_usb: bool - USB serial devices present (/dev/ttyUSB*, /dev/ttyACM*)
            spi_devices: list - SPI device paths
            usb_devices: list - USB device paths
            usb_device: str - First USB device (for fix hints)
            hardware_type: str - 'spi', 'usb', 'both', or 'none'
    """
    from pathlib import Path

    result = {
        'has_spi': False,
        'has_usb': False,
        'spi_devices': [],
        'usb_devices': [],
        'usb_device': '/dev/ttyUSB0',
        'hardware_type': 'none'
    }

    # Check SPI devices
    spi_devices = list(Path('/dev').glob('spidev*'))
    if spi_devices:
        result['has_spi'] = True
        result['spi_devices'] = [str(d) for d in spi_devices]

    # Check USB serial devices
    usb_devices = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
    if usb_devices:
        result['has_usb'] = True
        result['usb_devices'] = [str(d) for d in usb_devices]
        result['usb_device'] = str(usb_devices[0])

    # Determine hardware type
    if result['has_spi'] and result['has_usb']:
        result['hardware_type'] = 'both'
    elif result['has_spi']:
        result['hardware_type'] = 'spi'
    elif result['has_usb']:
        result['hardware_type'] = 'usb'

    return result


# =============================================================================
# UTILITY FUNCTIONS
# These are kept for direct use but NOT used by check_service() for systemd
# services (Issue #17: avoid conflicting detection methods)
# =============================================================================


def check_meshcore_device(device_path: str = "") -> bool:
    """Check if a MeshCore companion radio device is available.

    MeshCore has no daemon — this checks whether the serial device exists.
    Does NOT verify the device is running MeshCore firmware.

    Args:
        device_path: Specific device to check (e.g., '/dev/ttyUSB0').
                     If empty, scans for any USB serial device.

    Returns:
        True if device path exists or any USB serial device is found.
    """
    from pathlib import Path

    if device_path and device_path != "auto":
        return Path(device_path).exists()

    # Scan for any USB serial device
    usb_devices = list(Path("/dev").glob("ttyUSB*")) + \
                  list(Path("/dev").glob("ttyACM*"))
    return len(usb_devices) > 0


def check_port(port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
    """
    Check if a TCP port is accepting connections.

    Args:
        port: TCP port number
        host: Hostname to check (default localhost)
        timeout: Connection timeout in seconds

    Returns:
        True if port is open, False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        return result == 0
    except (socket.error, OSError) as e:
        logger.debug(f"Port check failed for {host}:{port}: {e}")
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass  # Socket close errors are non-critical


def check_udp_port(port: int, host: str = '127.0.0.1', timeout: float = 2.0) -> bool:
    """
    Check if a UDP port is in use.

    Primary method: read /proc/net/udp + /proc/net/udp6 (kernel socket table).
    This is reliable even when the service sets SO_REUSEADDR/SO_REUSEPORT,
    which causes bind-test false negatives.

    Fallback chain: ss → lsof → bind test.

    Args:
        port: UDP port number
        host: Host address to check (default 127.0.0.1)
        timeout: Socket timeout in seconds

    Returns:
        True if port appears to be in use (service running), False otherwise
    """
    # Primary: read /proc/net/udp directly (always available on Linux,
    # no external tool required). Port is stored as hex in column 1
    # (local_address) in the format ADDR:PORT_HEX.
    hex_port = f'{port:04X}'
    for proc_path in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            with open(proc_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        # local_address is "ADDR:PORT_HEX"
                        local = parts[1]
                        if local.endswith(':' + hex_port):
                            return True
        except (OSError, IOError):
            continue

    # Fallback 1: ss (not always installed — e.g., minimal containers)
    try:
        result = subprocess.run(
            ['ss', '-uln'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = str(port)
            for line in result.stdout.split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    local_addr = parts[4]
                    if local_addr.endswith(':' + port_str):
                        return True
            return False
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback 2: lsof (commonly available)
    try:
        result = subprocess.run(
            ['lsof', '-i', f'UDP:{port}', '-nP'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Any output means something has the port open
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:  # Header + at least one entry
                return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback 3: bind test (unreliable with SO_REUSEADDR, last resort)
    hosts_to_check = [host]
    if host == '127.0.0.1':
        hosts_to_check.append('0.0.0.0')

    for check_host in hosts_to_check:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.bind((check_host, port))
            sock.close()
            continue
        except OSError as e:
            if e.errno in (98, 48, 10048):  # EADDRINUSE
                return True
            logger.debug(f"UDP port check error for {check_host}:{port}: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    return False


def get_udp_port_owner(port: int) -> Optional[Tuple[str, int]]:
    """Get the process name and PID that owns a UDP port.

    Primary: ``ss -ulnp``. Fallback: ``/proc/net/udp`` inode scan.

    Args:
        port: UDP port number to check.

    Returns:
        Tuple of ``(process_name, pid)`` if found, ``None`` otherwise.
    """
    # Primary: ss -ulnp shows process info for UDP listeners
    try:
        result = subprocess.run(
            ['ss', '-ulnp', 'sport', '=', f':{port}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse users:(("process",pid=NNN,fd=N)) pattern
            m = re.search(
                r'users:\(\("([^"]+)",pid=(\d+)',
                result.stdout
            )
            if m:
                return (m.group(1), int(m.group(2)))
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback: find inode in /proc/net/udp, then scan /proc/*/fd
    hex_port = f'{port:04X}'
    target_inode = None
    for proc_path in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            with open(proc_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 10:
                        local = parts[1]
                        if local.endswith(':' + hex_port):
                            target_inode = parts[9]
                            break
            if target_inode:
                break
        except (OSError, IOError):
            continue

    if not target_inode:
        return None

    # Scan /proc/*/fd for the inode
    proc_dir = Path('/proc')
    for pid_dir in proc_dir.iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / 'fd'
        try:
            for fd in fd_dir.iterdir():
                try:
                    link = os.readlink(str(fd))
                    if f'socket:[{target_inode}]' in link:
                        comm_path = pid_dir / 'comm'
                        name = comm_path.read_text().strip()
                        return (name, int(pid_dir.name))
                except (OSError, ValueError):
                    continue
        except (OSError, PermissionError):
            continue

    return None


def check_rns_shared_instance(instance_name: str = 'default',
                               port: int = 37428) -> bool:
    """Check if the RNS shared instance is available.

    Uses passive detection (reads /proc files) to avoid disrupting the
    shared instance.  Safe to call in tight poll loops.

    Checks in priority order:
        1. ``/proc/net/unix`` for abstract domain socket (Linux default)
        2. TCP port via ``check_port()`` (fallback)
        3. UDP port via ``check_udp_port()`` (legacy)

    Args:
        instance_name: RNS instance name (default: ``'default'``).
        port: Shared instance port for TCP/UDP fallback (default: 37428).

    Returns:
        True if the shared instance is detected via any method.
    """
    info = get_rns_shared_instance_info(instance_name, port)
    return info['available']


def _check_proc_net_unix(socket_name: str) -> bool:
    """Check if an abstract Unix domain socket exists via /proc/net/unix.

    Passive check — reads a proc file, never connects to the service.
    Abstract sockets appear in /proc/net/unix with ``@`` prefix.

    Args:
        socket_name: Socket name WITHOUT the null byte or ``@`` prefix.
                     e.g. ``'rns/default'`` to match ``@rns/default``.

    Returns:
        True if the socket is listed in /proc/net/unix.
    """
    target = f'@{socket_name}'
    try:
        with open('/proc/net/unix', 'r') as f:
            for line in f:
                if target in line:
                    return True
    except OSError:
        pass
    return False


def get_rns_shared_instance_info(instance_name: str = 'default',
                                  port: int = 37428) -> dict:
    """Get detailed shared instance connectivity info for diagnostics.

    Returns a dict with keys:
        - ``available`` (bool): Whether shared instance is reachable.
        - ``method`` (str): Detection method that succeeded
          (``'unix_socket'``, ``'tcp'``, ``'udp'``, or ``'none'``).
        - ``detail`` (str): Human-readable connection detail.

    Args:
        instance_name: RNS instance name (default: ``'default'``).
        port: Shared instance port for TCP/UDP fallback (default: 37428).
    """
    # 1. Passive check: scan /proc/net/unix for the abstract domain socket.
    # RNS creates @rns/{instance_name} (LocalInterface data transport).
    # This mirrors how check_udp_port() reads /proc/net/udp — no connection
    # to the service, zero side effects, safe to call in tight poll loops.
    socket_name = f'rns/{instance_name}'
    if _check_proc_net_unix(socket_name):
        return {
            'available': True,
            'method': 'unix_socket',
            'detail': f'@rns/{instance_name} (abstract domain socket)',
        }

    # 2. TCP port (used when shared_instance_type = tcp in RNS config)
    if check_port(port):
        return {
            'available': True,
            'method': 'tcp',
            'detail': f'127.0.0.1:{port} (TCP)',
        }

    # 3. UDP port (legacy fallback)
    if check_udp_port(port):
        return {
            'available': True,
            'method': 'udp',
            'detail': f'127.0.0.1:{port} (UDP)',
        }

    return {
        'available': False,
        'method': 'none',
        'detail': (f'No shared instance found '
                   f'(checked @rns/{instance_name}, '
                   f'TCP:{port}, UDP:{port})'),
    }


def check_process_running(process_name: str) -> bool:
    """
    Check if a process is running by name.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd')

    Returns:
        True if process is running, False otherwise
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],  # -x = exact match
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Also check with -f but use word boundaries to avoid partial matches
        # e.g., match "rnsd" but not "myrnsd_wrapper"
        result = subprocess.run(
            ['pgrep', '-f', f'(^|/)({process_name})(\\s|$)'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Fallback: Check via ps for python-based services (e.g., python3 -m rnsd)
        if process_name in ('rnsd', 'nomadnet', 'meshchat'):
            result = subprocess.run(
                ['pgrep', '-f', f'python.*{process_name}'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and result.stdout.strip()

        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def check_process_with_pid(process_name: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a process is running and return its PID.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd', 'meshtasticd')

    Returns:
        Tuple of (is_running, pid) where pid is the first matching PID or None

    Example:
        >>> running, pid = check_process_with_pid('rnsd')
        >>> if running:
        ...     print(f"rnsd is running (PID: {pid})")
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split('\n')[0]
            return True, pid

        # Also check with -f for processes run via interpreters
        result = subprocess.run(
            ['pgrep', '-f', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Filter out pgrep itself and get first real PID
            pids = [p for p in result.stdout.strip().split('\n') if p]
            if pids:
                return True, pids[0]

        return False, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None


def check_systemd_service(service_name: str) -> Tuple[bool, bool]:
    """
    Check if a systemd service is running and enabled.

    Args:
        service_name: Name of the systemd service

    Returns:
        Tuple of (is_running, is_enabled)
    """
    is_running = False
    is_enabled = False

    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_running = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            ['systemctl', 'is-enabled', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_enabled = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return is_running, is_enabled


def check_service(name: str, port: Optional[int] = None, host: str = 'localhost') -> ServiceStatus:
    """
    Check if a service is available and provide actionable feedback.

    SIMPLIFIED ARCHITECTURE (Issue #17):
        - For systemd services: ONLY trust systemctl (single source of truth)
        - No conflicting fallback methods (port check, pgrep)
        - "Unknown" is better than wrong state

    Args:
        name: Service name (e.g., 'meshtasticd', 'rnsd', 'mosquitto')
        port: Override port to check (uses known default if not specified)
        host: Host to check (default localhost)

    Returns:
        ServiceStatus with availability info and fix hints

    API Contract:
        - ALWAYS returns a ServiceStatus (never None)
        - ServiceStatus.available: bool indicating if service is ready
        - ServiceStatus.state: ServiceState enum (AVAILABLE, NOT_RUNNING, etc.)
        - ServiceStatus.detection_method: How status was determined
        - Known services: meshtasticd, rnsd, mosquitto, nomadnet
    """
    config = KNOWN_SERVICES.get(name, {})
    check_port_num = port or config.get('port')
    systemd_name = config.get('systemd_name', name)
    description = config.get('description', name)
    fix_hint = config.get('fix_hint', f'Start {name} service')
    is_systemd = config.get('is_systemd', True)  # Default to systemd

    # =========================================================================
    # SYSTEMD SERVICES: Trust systemctl ONLY
    # =========================================================================
    if is_systemd:
        try:
            # Single source of truth: systemctl is-active
            result = subprocess.run(
                ['systemctl', 'is-active', systemd_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_active = result.returncode == 0
            status_text = result.stdout.strip()  # "active", "inactive", "failed"

            # For daemon services, also check the actual state (running vs exited)
            # "active (exited)" means it ran once and exited - NOT a running daemon
            sub_state = ""
            if is_active:
                state_result = subprocess.run(
                    ['systemctl', 'show', systemd_name, '--property=SubState'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                # Output is like "SubState=running" or "SubState=exited"
                if '=' in state_result.stdout:
                    sub_state = state_result.stdout.strip().split('=')[1]

            # Check for placeholder services (active but exited = not a real daemon)
            if is_active and sub_state == "exited":
                # This is a placeholder or oneshot that ran and exited
                hardware = _detect_radio_hardware()

                # Check if the real binary exists — stale placeholder if so
                has_binary = False
                try:
                    bin_result = subprocess.run(
                        ['which', 'meshtasticd'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    has_binary = bin_result.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

                if has_binary:
                    # Real binary exists but service is a placeholder — stale
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.DEGRADED,
                        message=f"{description}: stale placeholder — meshtasticd binary available",
                        fix_hint="Restart NOC to auto-fix, or run: sudo bash scripts/install_noc.sh",
                        port=check_port_num,
                        detection_method="systemctl (exited) + binary exists"
                    )
                elif hardware['has_spi'] and not hardware['has_usb']:
                    # SPI HAT detected but placeholder service - MISMATCH!
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.DEGRADED,
                        message=f"{description}: WRONG CONFIG - SPI HAT needs native daemon",
                        fix_hint="Run: sudo bash scripts/install_noc.sh (or install meshtasticd)",
                        port=check_port_num,
                        detection_method="systemctl (exited) + hardware mismatch"
                    )
                elif hardware['has_usb']:
                    # USB radio, no native binary — placeholder is expected
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.NOT_RUNNING,
                        message=f"{description}: USB mode (no daemon needed)",
                        fix_hint=f"Use: meshtastic --port {hardware.get('usb_device', '/dev/ttyUSB0')} --info",
                        port=check_port_num,
                        detection_method="systemctl (exited)"
                    )
                else:
                    # No hardware detected
                    return ServiceStatus(
                        name=name,
                        available=False,
                        state=ServiceState.NOT_RUNNING,
                        message=f"{description}: placeholder (no hardware detected)",
                        fix_hint="Connect a Meshtastic device via USB or configure SPI HAT",
                        port=check_port_num,
                        detection_method="systemctl (exited)"
                    )

            if is_active and sub_state == "running":
                return ServiceStatus(
                    name=name,
                    available=True,
                    state=ServiceState.AVAILABLE,
                    message=f"{description} is running",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            if is_active:
                # Active but sub-state not "running" or "exited"
                # (e.g., "start", "auto-restart", "reload", or empty)
                # Trust systemctl — port fallback here caused flakiness (Issue #20)
                return ServiceStatus(
                    name=name,
                    available=True,
                    state=ServiceState.AVAILABLE,
                    message=f"{description} is active ({sub_state or 'transitioning'})",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Not active - check if it exists
            if status_text == "inactive":
                # Service exists but not running
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.NOT_RUNNING,
                    message=f"{description} is not running",
                    fix_hint=fix_hint,
                    port=check_port_num,
                    detection_method="systemctl"
                )

            if status_text == "failed":
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.FAILED,
                    message=f"{description} has failed",
                    fix_hint=f"Check logs: journalctl -u {systemd_name}",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Check if service unit exists
            check_result = subprocess.run(
                ['systemctl', 'list-unit-files', f'{systemd_name}.service'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if systemd_name not in check_result.stdout:
                return ServiceStatus(
                    name=name,
                    available=False,
                    state=ServiceState.NOT_INSTALLED,
                    message=f"{description} is not installed",
                    fix_hint=f"Install {name} first",
                    port=check_port_num,
                    detection_method="systemctl"
                )

            # Generic not running
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.NOT_RUNNING,
                message=f"{description} is not running",
                fix_hint=fix_hint,
                port=check_port_num,
                detection_method="systemctl"
            )

        except FileNotFoundError:
            # systemctl not available (non-systemd system)
            logger.warning(f"systemctl not found - cannot check {name}")
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: cannot determine status (no systemctl)",
                fix_hint="Check manually or use port check",
                port=check_port_num,
                detection_method="none"
            )
        except subprocess.TimeoutExpired:
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: status check timed out",
                fix_hint="System may be overloaded",
                port=check_port_num,
                detection_method="systemctl-timeout"
            )
        except Exception as e:
            logger.error(f"Service check failed for {name}: {e}")
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.UNKNOWN,
                message=f"{description}: check failed ({e})",
                port=check_port_num,
                detection_method="error"
            )

    # =========================================================================
    # NON-SYSTEMD SERVICES: Fall back to port/process check
    # Race condition fix: Process may start before binding port, so check
    # process FIRST, then port. Also add retry for startup race condition.
    # =========================================================================
    port_type = config.get('port_type', 'tcp')

    # Check process FIRST (more reliable during startup)
    # This helps with the race condition where process starts but hasn't
    # bound to port yet (e.g., rnsd shows PID but port check fails)
    if check_process_running(systemd_name):
        return ServiceStatus(
            name=name,
            available=True,
            state=ServiceState.AVAILABLE,
            message=f"{description} is running (process detected)",
            port=check_port_num,
            detection_method="process"
        )

    # Fall back to port check
    if check_port_num:
        if port_type == 'udp':
            port_open = check_udp_port(check_port_num, host)
        else:
            port_open = check_port(check_port_num, host)

        if port_open:
            return ServiceStatus(
                name=name,
                available=True,
                state=ServiceState.AVAILABLE,
                message=f"{description} is running (port {check_port_num})",
                port=check_port_num,
                detection_method="port"
            )

    return ServiceStatus(
        name=name,
        available=False,
        state=ServiceState.NOT_RUNNING,
        message=f"{description} is not running",
        fix_hint=fix_hint,
        port=check_port_num,
        detection_method="port+process"
    )


def require_service(name: str, port: Optional[int] = None) -> ServiceStatus:
    """
    Check service and log warning if not available.

    Convenience wrapper around check_service that logs warnings.

    Args:
        name: Service name
        port: Optional port override

    Returns:
        ServiceStatus
    """
    status = check_service(name, port)
    if not status.available:
        logger.warning(f"{status.message}. {status.fix_hint}")
    return status


# ─────────────────────────────────────────────────────────────────────
# Service lifecycle (extracted to _service_lifecycle.py)
# ─────────────────────────────────────────────────────────────────────
from core.services._service_lifecycle import (  # noqa: E402 — re-export
    apply_config_and_restart,
    _wait_for_tcp_ready,
    daemon_reload,
    enable_service,
    disable_service,
    start_service,
    stop_service,
    restart_service,
)


# ─────────────────────────────────────────────────────────────────────
# Port lockdown + sudo write (extracted to _iptables_utils.py)
# ─────────────────────────────────────────────────────────────────────
from core.services._iptables_utils import (  # noqa: E402 — re-export
    _sudo_write,
    lock_port_external,
    unlock_port_external,
    check_port_locked,
    persist_iptables,
)
