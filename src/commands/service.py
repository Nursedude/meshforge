"""
Service Commands

Provides unified interface for system service operations.
Used by both GTK and CLI interfaces.
"""

import subprocess
import logging
from typing import Optional, List
from pathlib import Path

from .base import CommandResult

logger = logging.getLogger(__name__)


# Known services configuration
KNOWN_SERVICES = {
    'meshtasticd': {
        'port': 4403,
        'description': 'Meshtastic daemon',
        'start_cmd': 'sudo systemctl start meshtasticd',
        'stop_cmd': 'sudo systemctl stop meshtasticd',
    },
    'rnsd': {
        'port': None,
        'description': 'Reticulum Network Stack daemon',
        'start_cmd': 'rnsd',  # or sudo systemctl start rnsd
        'stop_cmd': 'sudo systemctl stop rnsd',
    },
    'hamclock': {
        'port': 8080,
        'description': 'HamClock space weather display',
        'start_cmd': 'sudo systemctl start hamclock',
        'stop_cmd': 'sudo systemctl stop hamclock',
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

    Args:
        name: Service name
        port: Optional port to check

    Returns:
        CommandResult with status information
    """
    config = KNOWN_SERVICES.get(name, {})
    check_port = port or config.get('port')
    description = config.get('description', name)

    is_running = False
    is_enabled = False
    status_detail = "Unknown"

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

    # Check if enabled
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

    # Check port if applicable
    port_open = False
    if check_port:
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex(('localhost', check_port))
            sock.close()
            port_open = result == 0
        except (socket.error, OSError):
            pass

    if not is_running:
        status_detail = "Stopped"

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
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'start', name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return CommandResult.ok(f"Service {name} started")
        else:
            return CommandResult.fail(
                f"Failed to start {name}",
                error=result.stderr or result.stdout
            )
    except subprocess.TimeoutExpired:
        return CommandResult.fail(f"Timeout starting {name}")
    except FileNotFoundError:
        return CommandResult.fail("systemctl not available")
    except Exception as e:
        return CommandResult.fail(f"Error starting {name}: {e}")


def stop(name: str) -> CommandResult:
    """
    Stop a service.

    Args:
        name: Service name
    """
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'stop', name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return CommandResult.ok(f"Service {name} stopped")
        else:
            return CommandResult.fail(
                f"Failed to stop {name}",
                error=result.stderr or result.stdout
            )
    except subprocess.TimeoutExpired:
        return CommandResult.fail(f"Timeout stopping {name}")
    except FileNotFoundError:
        return CommandResult.fail("systemctl not available")
    except Exception as e:
        return CommandResult.fail(f"Error stopping {name}: {e}")


def restart(name: str) -> CommandResult:
    """
    Restart a service.

    Args:
        name: Service name
    """
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return CommandResult.ok(f"Service {name} restarted")
        else:
            return CommandResult.fail(
                f"Failed to restart {name}",
                error=result.stderr or result.stdout
            )
    except subprocess.TimeoutExpired:
        return CommandResult.fail(f"Timeout restarting {name}")
    except Exception as e:
        return CommandResult.fail(f"Error restarting {name}: {e}")


def enable(name: str) -> CommandResult:
    """Enable a service to start on boot."""
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'enable', name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return CommandResult.ok(f"Service {name} enabled")
        else:
            return CommandResult.fail(
                f"Failed to enable {name}",
                error=result.stderr
            )
    except Exception as e:
        return CommandResult.fail(f"Error enabling {name}: {e}")


def disable(name: str) -> CommandResult:
    """Disable a service from starting on boot."""
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'disable', name],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return CommandResult.ok(f"Service {name} disabled")
        else:
            return CommandResult.fail(
                f"Failed to disable {name}",
                error=result.stderr
            )
    except Exception as e:
        return CommandResult.fail(f"Error disabling {name}: {e}")


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
    Get version of a binary.

    Args:
        binary: Binary name (e.g., 'meshtasticd', 'rnsd')
    """
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
