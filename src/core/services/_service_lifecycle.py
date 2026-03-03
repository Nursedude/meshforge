"""
Service Lifecycle Management — Start, stop, restart, enable, disable systemd services.

Extracted from service_check.py to keep files under 1,500 lines.
These are the "mutation" operations for systemd services. The "query"
operations (check_service, check_port, etc.) remain in service_check.py.

All functions use _sudo_cmd() from service_check.py for privilege escalation.
"""

import logging
import socket
import subprocess
import time
from typing import Tuple

from core.services.service_check import _sudo_cmd

logger = logging.getLogger(__name__)


def apply_config_and_restart(service_name: str = 'meshtasticd', timeout: int = 30) -> Tuple[bool, str]:
    """
    Reload systemd daemon and restart a service.

    This is the standard pattern after modifying service configuration files.
    Always runs daemon-reload before restart to pick up changes.

    Args:
        service_name: Name of the systemd service to restart (default: meshtasticd)
        timeout: Timeout in seconds for each command (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import apply_config_and_restart

        # After modifying /etc/meshtasticd/config.yaml:
        success, msg = apply_config_and_restart('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        # Step 1: Reload systemd daemon to pick up any service file changes
        reload_cmd = subprocess.run(
            _sudo_cmd(['systemctl', 'daemon-reload']),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if reload_cmd.returncode != 0:
            error_msg = reload_cmd.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Restart the service
        restart = subprocess.run(
            _sudo_cmd(['systemctl', 'restart', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if restart.returncode != 0:
            error_msg = restart.stderr.strip() or f"restart {service_name} failed"
            logger.error(f"restart {service_name} failed: {error_msg}")
            return False, f"restart {service_name} failed: {error_msg}"

        logger.info(f"Successfully restarted {service_name}")

        # Wait for TCP port readiness (meshtasticd binds 4403 on startup)
        if service_name == 'meshtasticd':
            tcp_ready = _wait_for_tcp_ready(4403, max_wait=15)
            if tcp_ready:
                return True, f"{service_name} restarted and accepting connections"
            else:
                logger.warning("meshtasticd restarted but TCP:4403 not ready within 15s")
                return True, f"{service_name} restarted (TCP port not yet ready)"

        return True, f"{service_name} restarted successfully"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while restarting {service_name}")
        return False, f"Timeout while restarting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error restarting {service_name}: {e}")
        return False, f"Error: {e}"


def _wait_for_tcp_ready(port: int, host: str = 'localhost', max_wait: int = 15) -> bool:
    """Poll a TCP port until it accepts connections.

    Used after service restart to ensure the daemon is fully initialized
    and accepting client connections before returning.

    Args:
        port: TCP port number to check
        host: Host to connect to (default: localhost)
        max_wait: Maximum seconds to wait (default: 15)

    Returns:
        True if port became ready, False if timeout
    """
    for _attempt in range(max_wait):
        try:
            with socket.create_connection((host, port), timeout=1):
                logger.debug("TCP port %d ready", port)
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(1)
    return False


def daemon_reload(timeout: int = 30) -> Tuple[bool, str]:
    """
    Reload the systemd daemon to pick up service file changes.

    Use this after creating or modifying service unit files.
    For most cases, prefer enable_service() or apply_config_and_restart()
    which include daemon-reload automatically.

    Args:
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import daemon_reload

        # After creating a new service file:
        success, msg = daemon_reload()
        if not success:
            show_error(msg)
    """
    try:
        result = subprocess.run(
            _sudo_cmd(['systemctl', 'daemon-reload']),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        logger.debug("systemctl daemon-reload succeeded")
        return True, "daemon-reload succeeded"

    except subprocess.TimeoutExpired:
        logger.error("Timeout during daemon-reload")
        return False, "Timeout during daemon-reload"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error during daemon-reload: {e}")
        return False, f"Error: {e}"


def enable_service(service_name: str, start: bool = False, timeout: int = 30) -> Tuple[bool, str]:
    """
    Enable a systemd service to start at boot.

    Automatically runs daemon-reload before enabling to ensure service
    file changes are picked up.

    Args:
        service_name: Name of the systemd service to enable
        start: If True, also start the service immediately (default: False)
        timeout: Timeout in seconds for each command (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import enable_service

        # After creating a service file:
        success, msg = enable_service('rnsd')
        if not success:
            show_error(msg)

        # Enable and start immediately:
        success, msg = enable_service('meshtasticd', start=True)
    """
    try:
        # Step 1: Reload systemd daemon to pick up service file changes
        reload_result = subprocess.run(
            _sudo_cmd(['systemctl', 'daemon-reload']),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if reload_result.returncode != 0:
            error_msg = reload_result.stderr.strip() or "daemon-reload failed"
            logger.error(f"daemon-reload failed: {error_msg}")
            return False, f"daemon-reload failed: {error_msg}"

        # Step 2: Enable the service
        enable_result = subprocess.run(
            _sudo_cmd(['systemctl', 'enable', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if enable_result.returncode != 0:
            error_msg = enable_result.stderr.strip() or f"enable {service_name} failed"
            logger.error(f"enable {service_name} failed: {error_msg}")
            return False, f"enable {service_name} failed: {error_msg}"

        # Step 3: Optionally start the service
        if start:
            start_result = subprocess.run(
                _sudo_cmd(['systemctl', 'start', service_name]),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if start_result.returncode != 0:
                error_msg = start_result.stderr.strip() or f"start {service_name} failed"
                logger.error(f"start {service_name} failed: {error_msg}")
                return False, f"Enabled but start failed: {error_msg}"

            logger.info(f"Successfully enabled and started {service_name}")
            return True, f"{service_name} enabled and started"

        logger.info(f"Successfully enabled {service_name}")
        return True, f"{service_name} enabled"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while enabling {service_name}")
        return False, f"Timeout while enabling {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error enabling {service_name}: {e}")
        return False, f"Error: {e}"


def disable_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Disable a systemd service from starting at boot.

    Args:
        service_name: Name of the systemd service to disable
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import disable_service

        success, msg = disable_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        result = subprocess.run(
            _sudo_cmd(['systemctl', 'disable', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"disable {service_name} failed"
            logger.error(f"disable {service_name} failed: {error_msg}")
            return False, f"disable {service_name} failed: {error_msg}"

        logger.info(f"Successfully disabled {service_name}")
        return True, f"{service_name} disabled"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while disabling {service_name}")
        return False, f"Timeout while disabling {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error disabling {service_name}: {e}")
        return False, f"Error: {e}"


def start_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Start a systemd service.

    Args:
        service_name: Name of the systemd service to start
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import start_service

        success, msg = start_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        result = subprocess.run(
            _sudo_cmd(['systemctl', 'start', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"start {service_name} failed"
            logger.error(f"start {service_name} failed: {error_msg}")
            return False, f"start {service_name} failed: {error_msg}"

        logger.info(f"Successfully started {service_name}")
        return True, f"{service_name} started"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while starting {service_name}")
        return False, f"Timeout while starting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error starting {service_name}: {e}")
        return False, f"Error: {e}"


def stop_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Stop a systemd service.

    Args:
        service_name: Name of the systemd service to stop
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import stop_service

        success, msg = stop_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        result = subprocess.run(
            _sudo_cmd(['systemctl', 'stop', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"stop {service_name} failed"
            logger.error(f"stop {service_name} failed: {error_msg}")
            return False, f"stop {service_name} failed: {error_msg}"

        logger.info(f"Successfully stopped {service_name}")
        return True, f"{service_name} stopped"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while stopping {service_name}")
        return False, f"Timeout while stopping {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error stopping {service_name}: {e}")
        return False, f"Error: {e}"


def restart_service(service_name: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Restart a systemd service.

    For a simple restart without daemon-reload. If you've modified service
    unit files or config that requires a reload, use apply_config_and_restart()
    instead.

    Args:
        service_name: Name of the systemd service to restart
        timeout: Timeout in seconds (default: 30)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import restart_service

        success, msg = restart_service('meshtasticd')
        if not success:
            show_error(msg)
    """
    try:
        result = subprocess.run(
            _sudo_cmd(['systemctl', 'restart', service_name]),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"restart {service_name} failed"
            logger.error(f"restart {service_name} failed: {error_msg}")
            return False, f"restart {service_name} failed: {error_msg}"

        logger.info(f"Successfully restarted {service_name}")
        return True, f"{service_name} restarted"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while restarting {service_name}")
        return False, f"Timeout while restarting {service_name}"
    except FileNotFoundError:
        logger.error("systemctl not found")
        return False, "systemctl not found - is this a systemd system?"
    except Exception as e:
        logger.error(f"Error restarting {service_name}: {e}")
        return False, f"Error: {e}"
