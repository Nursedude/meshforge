"""
Service Commands

Provides unified interface for system service operations.
Used by both GTK and CLI interfaces.

Security: Service names are validated against a whitelist to prevent
arbitrary systemctl commands. Binary names for version checks are also
restricted to known safe binaries.
"""

import subprocess
import logging
import re
from typing import Optional, List
from pathlib import Path

from .base import CommandResult
from utils.service_check import (
    check_service, start_service, stop_service, restart_service,
    enable_service, disable_service
)

# Expose for tests that check module attributes
HAS_SERVICE_CHECK = True

logger = logging.getLogger(__name__)


# Security: Regex for validating service names (alphanumeric, underscore, hyphen, @, .)
_VALID_SERVICE_NAME = re.compile(r'^[a-zA-Z0-9_\-@.]+$')

# Security: Whitelist of allowed binaries for get_version()
ALLOWED_BINARIES = {'meshtasticd', 'rnsd', 'mosquitto', 'meshtastic'}


def _validate_service_name(name: str) -> bool:
    """Validate service name contains only safe characters.

    Security: Prevents command injection via malformed service names.
    """
    if not name or len(name) > 256:
        return False
    return bool(_VALID_SERVICE_NAME.match(name))


# Known services configuration
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': 4403,
        'description': 'Meshtastic daemon',
        'start_cmd': 'sudo systemctl start meshtasticd',
        'stop_cmd': 'sudo systemctl stop meshtasticd',
    },
    'rnsd': {
        'port': 37428,  # UDP port - use centralized check_service for proper detection
        'port_type': 'udp',  # Flag to indicate UDP port check needed
        'description': 'Reticulum Network Stack daemon',
        'start_cmd': 'rnsd',  # or sudo systemctl start rnsd
        'stop_cmd': 'sudo systemctl stop rnsd',
    },
    'mosquitto': {
        'port': 1883,
        'description': 'MQTT broker',
        'start_cmd': 'sudo systemctl start mosquitto',
        'stop_cmd': 'sudo systemctl stop mosquitto',
    },
}


def check_status(name: str, port: Optional[int] = None) -> CommandResult:
    """
    Check service status.

    Uses centralized service_check.check_service() for services with specialized
    detection methods (e.g., rnsd with UDP port, meshtasticd with TCP).

    Args:
        name: Service name
        port: Optional port to check

    Returns:
        CommandResult with status information
    """
    # Security: Validate service name
    if not _validate_service_name(name):
        return CommandResult.fail(f"Invalid service name: {name}")

    config = KNOWN_SERVICES.get(name, {})
    check_port = port or config.get('port')
    description = config.get('description', name)

    is_running = False
    is_enabled = False
    status_detail = "Unknown"
    port_open = False

    # Use centralized service checker for consistent detection (SINGLE SOURCE OF TRUTH)
    # This handles UDP/TCP port checks, pgrep, and systemd properly
    if name in ('rnsd', 'meshtasticd'):
        service_status = check_service(name)
        is_running = service_status.available
        status_detail = service_status.message or ("Running" if is_running else "Stopped")
        port_open = is_running  # If service responds, port is effectively "open"
        logger.debug(f"[SERVICE] {name} status via check_service: {service_status.state}")
    else:
        # Fallback for services without specialized detection
        # Check systemd status
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', name],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout.strip() == 'active':
                is_running = True
                status_detail = "Running (systemd)"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Check if running as process
        if not is_running:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    is_running = True
                    status_detail = "Running (process)"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Check port if applicable (TCP only for non-specialized services)
        if check_port and config.get('port_type') != 'udp':
            import socket
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                result = sock.connect_ex(('localhost', check_port))
                port_open = result == 0
            except (socket.error, OSError):
                pass
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:  # Ignore errors during cleanup
                        pass

        if not is_running:
            status_detail = "Stopped"

    # Check if enabled (always check systemd for this)
    try:
        result = subprocess.run(
            ['systemctl', 'is-enabled', name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_enabled = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return CommandResult(
        success=is_running,
        message=f"{description}: {status_detail}",
        data={
            'name': name,
            'running': is_running,
            'enabled': is_enabled,
            'port': check_port,
            'port_open': port_open,
            'status': status_detail,
            'description': description,
            'start_cmd': config.get('start_cmd', f'sudo systemctl start {name}'),
            'stop_cmd': config.get('stop_cmd', f'sudo systemctl stop {name}'),
        }
    )


def start(name: str) -> CommandResult:
    """
    Start a service.

    Args:
        name: Service name

    Returns:
        CommandResult indicating success/failure
    """
    success, msg = start_service(name)
    if success:
        return CommandResult.ok(f"Service {name} started")
    return CommandResult.fail(f"Failed to start {name}", error=msg)


def stop(name: str) -> CommandResult:
    """
    Stop a service.

    Args:
        name: Service name
    """
    success, msg = stop_service(name)
    if success:
        return CommandResult.ok(f"Service {name} stopped")
    return CommandResult.fail(f"Failed to stop {name}", error=msg)


def restart(name: str) -> CommandResult:
    """
    Restart a service.

    Args:
        name: Service name
    """
    success, msg = restart_service(name)
    if success:
        return CommandResult.ok(f"Service {name} restarted")
    return CommandResult.fail(f"Failed to restart {name}", error=msg)


def enable(name: str) -> CommandResult:
    """Enable a service to start on boot."""
    success, msg = enable_service(name)
    if success:
        return CommandResult.ok(f"Service {name} enabled")
    return CommandResult.fail(f"Failed to enable {name}", error=msg)


def disable(name: str) -> CommandResult:
    """Disable a service from starting on boot."""
    success, msg = disable_service(name)
    if success:
        return CommandResult.ok(f"Service {name} disabled")
    return CommandResult.fail(f"Failed to disable {name}", error=msg)


def get_logs(name: str, lines: int = 50, follow: bool = False) -> CommandResult:
    """
    Get service logs from journalctl.

    Args:
        name: Service name
        lines: Number of lines to retrieve
        follow: Whether to follow (not implemented for non-interactive)
    """
    try:
        result = subprocess.run(
            ['journalctl', '-u', name, '-n', str(lines), '--no-pager'],
            capture_output=True,
            text=True,
            timeout=10
        )
        logs = result.stdout if result.stdout else "No logs available"
        return CommandResult.ok(
            f"Retrieved {lines} log lines",
            data={'logs': logs, 'lines': lines},
            raw=logs
        )
    except subprocess.TimeoutExpired:
        return CommandResult.fail("Timeout retrieving logs")
    except FileNotFoundError:
        return CommandResult.fail("journalctl not available")
    except Exception as e:
        return CommandResult.fail(f"Error retrieving logs: {e}")


def get_full_status(name: str) -> CommandResult:
    """
    Get full systemctl status output.

    Args:
        name: Service name
    """
    try:
        result = subprocess.run(
            ['systemctl', 'status', name, '--no-pager'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return CommandResult.ok(
            "Status retrieved",
            data={'status': result.stdout},
            raw=result.stdout
        )
    except Exception as e:
        return CommandResult.fail(f"Error getting status: {e}")


def list_all() -> CommandResult:
    """List all known services and their status."""
    results = {}
    for name in KNOWN_SERVICES:
        status = check_status(name)
        results[name] = {
            'running': status.data.get('running', False),
            'enabled': status.data.get('enabled', False),
            'description': status.data.get('description', name),
        }

    running_count = sum(1 for s in results.values() if s['running'])
    return CommandResult.ok(
        f"{running_count}/{len(results)} services running",
        data={'services': results}
    )


def is_installed(name: str) -> bool:
    """Check if a service unit file exists."""
    try:
        result = subprocess.run(
            ['systemctl', 'cat', name],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def get_version(binary: str) -> CommandResult:
    """
    Get version of a known binary.

    Args:
        binary: Binary name (must be in ALLOWED_BINARIES whitelist)

    Security: Only binaries in ALLOWED_BINARIES can be executed.
    """
    # Security: Whitelist check to prevent arbitrary binary execution
    if binary not in ALLOWED_BINARIES:
        return CommandResult.fail(
            f"Unknown binary: {binary}",
            error="Binary not in allowed list"
        )

    version_flags = ['--version', '-v', '-V', 'version']

    for flag in version_flags:
        try:
            result = subprocess.run(
                [binary, flag],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return CommandResult.ok(
                    result.stdout.strip(),
                    data={'version': result.stdout.strip()}
                )
        except FileNotFoundError:
            return CommandResult.not_available(
                f"{binary} not installed",
                fix_hint=f"Install {binary}"
            )
        except Exception:
            continue

    return CommandResult.fail(f"Could not determine {binary} version")
