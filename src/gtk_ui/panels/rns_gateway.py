"""
RNS Gateway Utilities

Gateway control and status checking utilities.
Extracted from rns.py for maintainability.
"""

import os
import subprocess
import logging
import socket
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GatewayStatus:
    """Gateway status information."""
    running: bool
    pid: Optional[int] = None
    port: Optional[int] = None
    message: str = ""
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


def check_gateway_process() -> GatewayStatus:
    """Check if the RNS-Meshtastic gateway is running.

    Returns:
        GatewayStatus with running state and details
    """
    try:
        # Check for gateway process
        result = subprocess.run(
            ['pgrep', '-f', 'rns_meshtastic_gateway'],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0:
            pid = int(result.stdout.strip().split('\n')[0])
            return GatewayStatus(
                running=True,
                pid=pid,
                message="Gateway running",
                details={'source': 'pgrep'}
            )
    except Exception as e:
        logger.debug(f"Gateway pgrep check failed: {e}")

    # Check alternative process names
    for process_name in ['gateway.py', 'RNS_Over_Meshtastic']:
        try:
            result = subprocess.run(
                ['pgrep', '-f', process_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                pid = int(result.stdout.strip().split('\n')[0])
                return GatewayStatus(
                    running=True,
                    pid=pid,
                    message=f"Gateway running ({process_name})",
                    details={'source': 'pgrep', 'pattern': process_name}
                )
        except Exception:
            pass

    return GatewayStatus(running=False, message="Gateway not running")


def check_gateway_port(port: int = 4403) -> bool:
    """Check if the gateway port is accessible.

    Args:
        port: TCP port to check (default: 4403 for meshtasticd)

    Returns:
        True if port is open
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def get_gateway_install_path() -> Optional[Path]:
    """Get the path to the RNS-Meshtastic gateway installation.

    Returns:
        Path to gateway directory or None if not found
    """
    # Check common installation locations
    possible_paths = [
        Path.home() / 'RNS_Over_Meshtastic_Gateway',
        Path('/opt/RNS_Over_Meshtastic_Gateway'),
        Path('/usr/local/share/rns_meshtastic_gateway'),
    ]

    # Also check SUDO_USER home
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        possible_paths.insert(0, Path(f'/home/{sudo_user}/RNS_Over_Meshtastic_Gateway'))

    for path in possible_paths:
        if path.exists():
            return path

    return None


def start_gateway(config_path: Optional[str] = None) -> tuple[bool, str]:
    """Start the RNS-Meshtastic gateway.

    Args:
        config_path: Optional path to gateway config

    Returns:
        Tuple of (success, message)
    """
    gateway_path = get_gateway_install_path()
    if not gateway_path:
        return False, "Gateway not installed"

    gateway_script = gateway_path / 'rns_meshtastic_gateway.py'
    if not gateway_script.exists():
        gateway_script = gateway_path / 'gateway.py'

    if not gateway_script.exists():
        return False, "Gateway script not found"

    try:
        # Start in background
        cmd = ['python3', str(gateway_script)]
        if config_path:
            cmd.extend(['--config', config_path])

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        return True, "Gateway started"

    except Exception as e:
        logger.error(f"Failed to start gateway: {e}")
        return False, str(e)


def stop_gateway() -> tuple[bool, str]:
    """Stop the RNS-Meshtastic gateway.

    Returns:
        Tuple of (success, message)
    """
    try:
        # Try graceful stop first
        result = subprocess.run(
            ['pkill', '-f', 'rns_meshtastic_gateway'],
            capture_output=True, timeout=5
        )

        if result.returncode != 0:
            # Try alternative patterns
            for pattern in ['gateway.py', 'RNS_Over_Meshtastic']:
                subprocess.run(
                    ['pkill', '-f', pattern],
                    capture_output=True, timeout=5
                )

        return True, "Gateway stopped"

    except Exception as e:
        logger.error(f"Failed to stop gateway: {e}")
        return False, str(e)
