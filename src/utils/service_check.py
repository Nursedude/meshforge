"""
Service Availability Utilities for MeshForge

Provides standardized service checking before connecting to external services.
Use these instead of assuming services are running.

Usage:
    from utils.service_check import check_port, check_service, ServiceStatus

    # Quick port check
    if check_port(4403):
        connect_to_meshtasticd()
    else:
        show_error("meshtasticd not running on port 4403")

    # Full service check with actionable feedback
    status = check_service('meshtasticd', port=4403)
    if not status.available:
        show_error(status.message)
        show_fix(status.fix_hint)
"""

import socket
import subprocess
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ServiceState(Enum):
    """Service availability states."""
    AVAILABLE = "available"
    PORT_CLOSED = "port_closed"
    NOT_RUNNING = "not_running"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass
class ServiceStatus:
    """Result of a service availability check."""
    name: str
    available: bool
    state: ServiceState
    message: str
    fix_hint: str = ""
    port: Optional[int] = None

    def __bool__(self) -> bool:
        return self.available


# Known services and their configurations
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': 4403,
        'systemd_name': 'meshtasticd',
        'description': 'Meshtastic daemon',
        'fix_hint': 'Start with: sudo systemctl start meshtasticd',
    },
    'rnsd': {
        'port': None,  # Uses socket, not TCP
        'systemd_name': 'rnsd',
        'description': 'Reticulum Network Stack daemon',
        'fix_hint': 'Start with: rnsd or sudo systemctl start rnsd',
    },
    'hamclock': {
        'port': 8080,
        'systemd_name': 'hamclock',
        'description': 'HamClock space weather display',
        'fix_hint': 'Start with: sudo systemctl start hamclock',
    },
    'mosquitto': {
        'port': 1883,
        'systemd_name': 'mosquitto',
        'description': 'MQTT broker',
        'fix_hint': 'Start with: sudo systemctl start mosquitto',
    },
}


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
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.error, OSError) as e:
        logger.debug(f"Port check failed for {host}:{port}: {e}")
        return False


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

    Args:
        name: Service name (e.g., 'meshtasticd', 'hamclock')
        port: Override port to check (uses known default if not specified)
        host: Host to check (default localhost)

    Returns:
        ServiceStatus with availability info and fix hints
    """
    # Get known service config
    config = KNOWN_SERVICES.get(name, {})
    check_port_num = port or config.get('port')
    systemd_name = config.get('systemd_name', name)
    description = config.get('description', name)
    fix_hint = config.get('fix_hint', f'Start {name} service')

    # Check port if applicable
    if check_port_num:
        if check_port(check_port_num, host):
            return ServiceStatus(
                name=name,
                available=True,
                state=ServiceState.AVAILABLE,
                message=f"{description} is running on port {check_port_num}",
                port=check_port_num
            )

    # Check systemd service
    is_running, is_enabled = check_systemd_service(systemd_name)

    if is_running:
        # Service running but port not open (maybe wrong port?)
        if check_port_num:
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.PORT_CLOSED,
                message=f"{description} is running but port {check_port_num} is not responding",
                fix_hint=f"Check if {name} is configured for port {check_port_num}",
                port=check_port_num
            )
        else:
            return ServiceStatus(
                name=name,
                available=True,
                state=ServiceState.AVAILABLE,
                message=f"{description} is running"
            )

    if is_enabled:
        return ServiceStatus(
            name=name,
            available=False,
            state=ServiceState.NOT_RUNNING,
            message=f"{description} is enabled but not running",
            fix_hint=fix_hint,
            port=check_port_num
        )

    # Check if service exists at all
    try:
        result = subprocess.run(
            ['systemctl', 'status', systemd_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if 'could not be found' in result.stderr.lower():
            return ServiceStatus(
                name=name,
                available=False,
                state=ServiceState.NOT_INSTALLED,
                message=f"{description} is not installed",
                fix_hint=f"Install {name} first",
                port=check_port_num
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return ServiceStatus(
        name=name,
        available=False,
        state=ServiceState.NOT_RUNNING,
        message=f"{description} is not running",
        fix_hint=fix_hint,
        port=check_port_num
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
