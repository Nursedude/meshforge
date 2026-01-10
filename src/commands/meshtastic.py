"""
Meshtastic Commands

Provides unified interface for Meshtastic operations.
Used by both GTK and CLI interfaces.
"""

import subprocess
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from .base import CommandResult

logger = logging.getLogger(__name__)

# Connection settings (module-level state)
_connection_type = "localhost"  # localhost, serial, ble
_connection_value = "localhost"


@dataclass
class ConnectionConfig:
    """Meshtastic connection configuration."""
    type: str  # "localhost", "serial", "ble"
    value: str  # hostname/IP, serial port, or BLE address


def _find_cli() -> Optional[str]:
    """Find the meshtastic CLI executable."""
    try:
        result = subprocess.run(
            ['which', 'meshtastic'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Check common locations
    common_paths = [
        '/usr/local/bin/meshtastic',
        '/usr/bin/meshtastic',
        str(Path.home() / '.local' / 'bin' / 'meshtastic'),
    ]
    for path in common_paths:
        if Path(path).exists():
            return path

    return None


def _get_connection_args() -> List[str]:
    """Get connection arguments based on current settings."""
    if _connection_type == "localhost":
        return ["--host", _connection_value]
    elif _connection_type == "serial":
        return ["--port", _connection_value]
    elif _connection_type == "ble":
        return []  # BLE requires special handling
    return ["--host", "localhost"]


def _run_command(args: List[str], timeout: int = 60) -> CommandResult:
    """
    Run a meshtastic CLI command.

    Args:
        args: Command arguments (without 'meshtastic' prefix)
        timeout: Command timeout in seconds

    Returns:
        CommandResult with output
    """
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    full_args = [cli_path] + _get_connection_args() + args
    cmd_str = ' '.join(full_args)
    logger.debug(f"Running: {cmd_str}")

    try:
        result = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode == 0:
            return CommandResult.ok(
                message="Command completed",
                data={'stdout': result.stdout, 'stderr': result.stderr},
                raw=result.stdout
            )
        else:
            return CommandResult.fail(
                message=f"Command failed: {result.stderr or result.stdout}",
                error=result.stderr or result.stdout,
                raw=result.stdout
            )

    except subprocess.TimeoutExpired:
        return CommandResult.fail(
            f"Command timed out after {timeout}s",
            error="timeout"
        )
    except FileNotFoundError:
        return CommandResult.not_available(
            "Meshtastic CLI not found",
            fix_hint="pip install meshtastic"
        )
    except Exception as e:
        return CommandResult.fail(
            f"Command error: {str(e)}",
            error=str(e)
        )


# Connection Management

def set_connection(conn_type: str, value: str) -> CommandResult:
    """
    Set the connection type and value.

    Args:
        conn_type: "localhost", "serial", or "ble"
        value: hostname/IP, serial port, or BLE address
    """
    global _connection_type, _connection_value

    if conn_type not in ("localhost", "serial", "ble"):
        return CommandResult.fail(f"Invalid connection type: {conn_type}")

    _connection_type = conn_type
    _connection_value = value

    return CommandResult.ok(
        f"Connection set to {conn_type}: {value}",
        data={'type': conn_type, 'value': value}
    )


def get_connection() -> ConnectionConfig:
    """Get current connection configuration."""
    return ConnectionConfig(type=_connection_type, value=_connection_value)


def test_connection() -> CommandResult:
    """Test the current connection by requesting node info."""
    result = get_node_info()
    if result.success:
        return CommandResult.ok(
            "Connection successful",
            data=result.data
        )
    return CommandResult.fail(
        "Connection failed",
        error=result.error
    )


# Information Commands

def get_node_info() -> CommandResult:
    """Get local node information."""
    return _run_command(["--info"])


def list_nodes() -> CommandResult:
    """List all known nodes in the mesh."""
    return _run_command(["--nodes"])


def get_settings(setting: str = "all") -> CommandResult:
    """
    Get node settings.

    Args:
        setting: Specific setting or "all" for all settings
    """
    return _run_command(["--get", setting])


def get_channel_info(channel_index: int = 0) -> CommandResult:
    """Get channel information."""
    return _run_command(["--ch-index", str(channel_index), "--info"])


# Location Commands

def set_position(lat: float, lon: float, alt: float = 0) -> CommandResult:
    """
    Set node position.

    Args:
        lat: Latitude in decimal degrees
        lon: Longitude in decimal degrees
        alt: Altitude in meters
    """
    return _run_command([
        "--setlat", str(lat),
        "--setlon", str(lon),
        "--setalt", str(alt)
    ])


def request_position(dest: str, channel_index: int = 0) -> CommandResult:
    """
    Request position from a remote node.

    Args:
        dest: Destination node ID (e.g., !ba4bf9d0)
        channel_index: Channel to use
    """
    return _run_command([
        "--request-position",
        "--dest", dest,
        "--ch-index", str(channel_index)
    ])


# Messaging Commands

def send_message(
    text: str,
    dest: Optional[str] = None,
    channel_index: int = 0,
    ack: bool = False
) -> CommandResult:
    """
    Send a text message.

    Args:
        text: Message text
        dest: Destination node ID (None for broadcast)
        channel_index: Channel to use
        ack: Request acknowledgment
    """
    args = ["--ch-index", str(channel_index), "--sendtext", text]
    if dest:
        args.extend(["--dest", dest])
    if ack:
        args.append("--ack")

    return _run_command(args)


def request_telemetry(dest: str) -> CommandResult:
    """Request telemetry from a remote node."""
    return _run_command(["--request-telemetry", "--dest", dest])


def traceroute(dest: str) -> CommandResult:
    """Perform traceroute to a node."""
    return _run_command(["--traceroute", dest], timeout=120)


# Network Configuration

def configure_wifi(ssid: str, password: str, enable: bool = True) -> CommandResult:
    """
    Configure WiFi settings.

    Args:
        ssid: WiFi network name
        password: WiFi password
        enable: Enable WiFi after configuration
    """
    return _run_command([
        "--set", "network.wifi_ssid", ssid,
        "--set", "network.wifi_psk", password,
        "--set", "network.wifi_enabled", "1" if enable else "0"
    ])


def set_channel_name(channel_index: int, name: str) -> CommandResult:
    """Set channel name."""
    return _run_command([
        "--ch-index", str(channel_index),
        "--ch-set", "name", name,
        "--info"
    ])


def set_channel_psk(channel_index: int, psk: str) -> CommandResult:
    """
    Set channel PSK (encryption key).

    Args:
        channel_index: Channel index
        psk: PSK value (hex, "random", or "none")
    """
    return _run_command([
        "--ch-index", str(channel_index),
        "--ch-set", "psk", psk,
        "--info"
    ])


# Node Control

def set_owner(name: str, dest: Optional[str] = None) -> CommandResult:
    """
    Set node owner name.

    Args:
        name: Owner name
        dest: Remote node ID (None for local node)
    """
    args = ["--set-owner", name]
    if dest:
        args = ["--dest", dest] + args
    return _run_command(args)


def reboot() -> CommandResult:
    """Reboot the node."""
    return _run_command(["--reboot"])


def shutdown() -> CommandResult:
    """Shutdown the node."""
    return _run_command(["--shutdown"])


def factory_reset() -> CommandResult:
    """Factory reset the node (dangerous!)."""
    return _run_command(["--factory-reset"])


def reset_nodedb() -> CommandResult:
    """Reset the node database."""
    return _run_command(["--reset-nodedb"])


# Bluetooth

def ble_scan() -> CommandResult:
    """Scan for Bluetooth devices."""
    # BLE scan doesn't use connection args
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    try:
        result = subprocess.run(
            [cli_path, "--ble-scan"],
            capture_output=True,
            text=True,
            timeout=60
        )
        return CommandResult.ok(
            "BLE scan complete",
            data={'devices': result.stdout},
            raw=result.stdout
        )
    except Exception as e:
        return CommandResult.fail(f"BLE scan failed: {e}")


# Utility

def is_available() -> bool:
    """Check if meshtastic CLI is available."""
    return _find_cli() is not None


def get_cli_path() -> Optional[str]:
    """Get the path to the meshtastic CLI."""
    return _find_cli()


def get_cli_help() -> CommandResult:
    """Get meshtastic CLI help."""
    cli_path = _find_cli()
    if not cli_path:
        return CommandResult.not_available(
            "Meshtastic CLI not installed",
            fix_hint="pip install meshtastic"
        )

    try:
        result = subprocess.run(
            [cli_path, "-h"],
            capture_output=True,
            text=True,
            timeout=15
        )
        return CommandResult.ok(
            "Help retrieved",
            raw=result.stdout
        )
    except Exception as e:
        return CommandResult.fail(f"Failed to get help: {e}")
