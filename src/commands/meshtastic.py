"""
Meshtastic Commands

Provides unified interface for Meshtastic operations.
Used by both GTK and CLI interfaces.
"""

import subprocess
import logging
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from .base import CommandResult
from utils.paths import get_real_user_home
from utils.message_listener import diagnose_pubsub

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

    # Check common locations - use get_real_user_home() not Path.home()
    common_paths = [
        '/usr/local/bin/meshtastic',
        '/usr/bin/meshtastic',
        str(get_real_user_home() / '.local' / 'bin' / 'meshtastic'),
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


def _run_command(args: List[str], timeout: int = 60, auto_detect: bool = True) -> CommandResult:
    """
    Run a meshtastic CLI command.

    Args:
        args: Command arguments (without 'meshtastic' prefix)
        timeout: Command timeout in seconds
        auto_detect: If True, retry with auto-detection on connection failure

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
            error_text = result.stderr or result.stdout

            # Check for connection refused - try auto-detect
            if auto_detect and 'Connection refused' in error_text:
                logger.info("Connection refused, attempting auto-detect...")
                detect_result = auto_detect_connection()
                if detect_result.success:
                    # Retry with new connection
                    return _run_command(args, timeout, auto_detect=False)

                # Provide helpful message for connection failure
                return CommandResult.fail(
                    message="Cannot connect to Meshtastic device.\n\n"
                            "For USB radios: Ensure device is connected\n"
                            "For HAT radios: Start meshtasticd service\n"
                            "  sudo systemctl start meshtasticd",
                    error="connection_refused",
                    fix_hint="Check device connection or start meshtasticd"
                )

            return CommandResult.fail(
                message=f"Command failed: {error_text}",
                error=error_text,
                raw=result.stdout
            )

    except KeyboardInterrupt:
        return CommandResult.fail(
            "Command aborted by user",
            error="interrupted"
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


def auto_detect_connection() -> CommandResult:
    """
    Auto-detect the best connection method.

    Checks in order:
    1. TCP port 4403 (meshtasticd daemon)
    2. USB serial devices (/dev/ttyUSB*, /dev/ttyACM*)

    Returns:
        CommandResult with data={'type': ..., 'value': ...} on success
    """
    import socket

    # Check if meshtasticd is running on port 4403
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        result = sock.connect_ex(('127.0.0.1', 4403))
        sock.close()
        if result == 0:
            set_connection("localhost", "127.0.0.1")
            return CommandResult.ok(
                "Using TCP connection to meshtasticd (port 4403)",
                data={'type': 'localhost', 'value': '127.0.0.1', 'method': 'tcp'}
            )
    except Exception as e:
        logger.debug(f"TCP connection check to meshtasticd failed: {e}")

    # Check for USB serial devices
    usb_paths = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
    if usb_paths:
        # Sort to get consistent ordering
        usb_paths.sort()
        usb_port = str(usb_paths[0])
        set_connection("serial", usb_port)
        return CommandResult.ok(
            f"Using USB serial connection ({usb_port})",
            data={'type': 'serial', 'value': usb_port, 'method': 'usb'}
        )

    # No connection found
    return CommandResult.fail(
        "No Meshtastic device found.\n\n"
        "For USB radios: Connect device to USB port\n"
        "For HAT radios: Start meshtasticd service\n"
        "  sudo systemctl start meshtasticd",
        error="no_device"
    )


def ensure_connection() -> CommandResult:
    """
    Ensure we have a valid connection, auto-detecting if needed.

    Call this before running commands to ensure proper connection.
    """
    # If already connected to something other than default, test it
    if _connection_type != "localhost" or _connection_value != "localhost":
        test_result = test_connection()
        if test_result.success:
            return CommandResult.ok(
                f"Connected via {_connection_type}: {_connection_value}",
                data={'type': _connection_type, 'value': _connection_value}
            )

    # Auto-detect
    return auto_detect_connection()


# Information Commands

def get_node_info() -> CommandResult:
    """Get local node information."""
    return _run_command(["--info"])


def list_nodes() -> CommandResult:
    """List all known nodes in the mesh."""
    return _run_command(["--nodes"])


def get_nodes() -> CommandResult:
    """
    Get all known nodes in the mesh with structured data.

    Returns:
        CommandResult with data={'nodes': [{'id': ..., 'name': ..., ...}]}
    """
    result = list_nodes()
    if not result.success:
        return result

    # Parse the raw output into structured node data
    nodes = []
    raw = result.raw or ''

    try:
        # Parse meshtastic --nodes output
        # Format varies but typically includes table with node info
        lines = raw.strip().split('\n')

        for line in lines:
            # Skip header lines and empty lines
            if not line.strip() or line.startswith('─') or line.startswith('|'):
                continue
            if 'User' in line and 'ID' in line:
                continue  # Header row

            # Try to parse node entries
            # Typical format: !ba4bf9d0  NodeName  ...
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith('!'):
                node = {
                    'id': parts[0],
                    'name': parts[1] if len(parts) > 1 else 'Unknown',
                    'snr': 'N/A',
                    'last_heard': 'N/A'
                }

                # Try to extract SNR if present
                for i, part in enumerate(parts):
                    if 'snr' in part.lower() or (i > 1 and part.replace('-', '').replace('.', '').isdigit()):
                        try:
                            node['snr'] = f"{float(part):.1f} dB"
                        except ValueError:
                            pass

                nodes.append(node)

        return CommandResult.ok(
            f"Found {len(nodes)} nodes",
            data={'nodes': nodes},
            raw=raw
        )
    except Exception as e:
        logger.warning(f"Failed to parse nodes: {e}")
        # Return raw data on parse failure
        return CommandResult.ok(
            "Nodes retrieved (unparsed)",
            data={'nodes': [], 'raw': raw},
            raw=raw
        )


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

# Recommended hop limits based on message type and network conditions
# Per Meshtastic docs: https://meshtastic.org/docs/overview/mesh-algo/
HOP_LIMIT_DM_INITIAL = 7       # First DM uses flood to discover path
HOP_LIMIT_DM_ESTABLISHED = 3   # Subsequent DMs use discovered next-hop
HOP_LIMIT_BROADCAST = 3        # Public channel broadcasts
HOP_LIMIT_EMERGENCY = 7        # Emergency/SAR messages


def send_message(
    text: str,
    dest: Optional[str] = None,
    channel_index: int = 0,
    ack: bool = False,
    hop_limit: Optional[int] = None,
    want_ack: bool = True
) -> CommandResult:
    """
    Send a text message with configurable routing.

    Args:
        text: Message text
        dest: Destination node ID (None for broadcast)
        channel_index: Channel to use (0 for DM, 1+ for public channels)
        ack: Request delivery acknowledgment (blocks until ack received)
        hop_limit: Override hop limit (1-7). None = use smart defaults:
                   - DM (dest specified): 7 for discovery, 3 once path known
                   - Broadcast: 3
        want_ack: Request ack without blocking (recipient sends ack)

    Returns:
        CommandResult with send status

    Note on routing (from Meshtastic mesh-algo):
        - DMs use next-hop routing: first message floods to find path,
          subsequent messages use shortest discovered path
        - Higher hop_limit helps initial path discovery but creates traffic
        - Lower hop_limit reduces congestion once path is established
    """
    # Build command args
    args = ["--ch-index", str(channel_index)]

    # Smart hop limit defaults based on message type
    if hop_limit is not None:
        # User specified - validate and use
        if not 1 <= hop_limit <= 7:
            return CommandResult.fail(f"hop_limit must be 1-7, got {hop_limit}")
    else:
        # Smart defaults based on destination
        if dest and dest != '!ffffffff':
            # DM - use higher hop for better path discovery
            hop_limit = HOP_LIMIT_DM_INITIAL
        else:
            # Broadcast - lower hop to reduce congestion
            hop_limit = HOP_LIMIT_BROADCAST

    logger.debug(f"Sending with intended hop_limit={hop_limit} (device setting applies)")

    # Try HTTP protobuf first — no CLI subprocess, no TCP contention,
    # no fromradio reads. This prevents "waiting for delivery" hangs on
    # the meshtasticd web client at :9443.
    try:
        from gateway.meshtastic_protobuf_client import send_text_direct

        dest_num = None
        if dest:
            try:
                cleaned = dest.lstrip('!')
                dest_num = int(cleaned, 16)
            except ValueError:
                try:
                    dest_num = int(dest)
                except ValueError:
                    dest_num = None

        if send_text_direct(
            text=text,
            destination=dest_num,
            channel_index=channel_index,
            want_ack=want_ack,
            hop_limit=hop_limit,
        ):
            is_dm = dest and dest != '!ffffffff'
            routing_note = (
                'DM: uses next-hop routing (flood then direct)' if is_dm
                else 'Broadcast: flooded to mesh'
            )
            return CommandResult.ok(
                message="Message sent via HTTP protobuf",
                data={
                    'hop_limit_intended': hop_limit,
                    'destination': dest,
                    'channel': channel_index,
                    'routing_note': routing_note,
                    'tx_method': 'http_protobuf',
                },
            )
    except Exception as e:
        logger.debug(f"HTTP protobuf TX unavailable, falling back to CLI: {e}")

    # Fallback: meshtastic CLI (creates TCP connection to 4403)
    # Add destination for DMs
    if dest:
        args.extend(["--dest", dest])

    # Add message
    args.extend(["--sendtext", text])

    # Acknowledgment options
    if ack:
        # --ack blocks until ack received (can timeout)
        args.append("--ack")
    elif want_ack and dest:
        # For DMs, request ack but don't block
        # Note: meshtastic CLI --ack is blocking, so we skip for non-blocking
        pass

    result = _run_command(args)

    # Add routing info to result
    if result.success:
        result.data = result.data or {}
        result.data['hop_limit_intended'] = hop_limit
        result.data['destination'] = dest
        result.data['channel'] = channel_index
        result.data['routing_note'] = (
            'DM: uses next-hop routing (flood then direct)' if dest
            else 'Broadcast: flooded to mesh'
        )
        result.data['tx_method'] = 'cli'

    return result


def send_dm(
    text: str,
    dest: str,
    ack: bool = False,
    high_reliability: bool = False
) -> CommandResult:
    """
    Send a direct message with optimized settings.

    Args:
        text: Message text
        dest: Destination node ID (required)
        ack: Block until delivery confirmed
        high_reliability: Use max hops for difficult paths

    Returns:
        CommandResult with send status
    """
    if not dest or dest == '!ffffffff':
        return CommandResult.fail("DM requires a specific destination")

    hop_limit = HOP_LIMIT_EMERGENCY if high_reliability else HOP_LIMIT_DM_INITIAL

    return send_message(
        text=text,
        dest=dest,
        channel_index=0,  # DMs use channel 0
        ack=ack,
        hop_limit=hop_limit,
        want_ack=True
    )


def send_broadcast(
    text: str,
    channel_index: int = 1,
    hop_limit: int = HOP_LIMIT_BROADCAST
) -> CommandResult:
    """
    Send a broadcast message to a channel.

    Args:
        text: Message text
        channel_index: Channel number (1+ for public channels)
        hop_limit: Hop limit (default 3 to reduce congestion)

    Returns:
        CommandResult with send status
    """
    return send_message(
        text=text,
        dest=None,  # Broadcast
        channel_index=channel_index,
        ack=False,
        hop_limit=hop_limit,
        want_ack=False
    )


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
    Set node owner name (long name).

    Args:
        name: Owner name (max 40 chars)
        dest: Remote node ID (None for local node)
    """
    args = ["--set-owner", name]
    if dest:
        args = ["--dest", dest] + args
    return _run_command(args)


def set_owner_short(name: str, dest: Optional[str] = None) -> CommandResult:
    """
    Set node owner short name (4 characters).

    Args:
        name: Short name (max 4 chars, will be uppercase)
        dest: Remote node ID (None for local node)
    """
    # Ensure short name is max 4 chars and uppercase
    short_name = name[:4].upper()
    args = ["--set-owner-short", short_name]
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


# Hop Limit / Routing Configuration

def get_hop_limit() -> CommandResult:
    """
    Get the current device hop limit setting.

    Returns:
        CommandResult with data={'hop_limit': int}
    """
    result = _run_command(["--get", "lora.hop_limit"])
    if not result.success:
        return result

    # Parse output like "lora.hop_limit: 3"
    try:
        raw = result.raw or ''
        for line in raw.split('\n'):
            if 'hop_limit' in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    hop_limit = int(parts[-1].strip())
                    return CommandResult.ok(
                        f"Device hop limit: {hop_limit}",
                        data={'hop_limit': hop_limit}
                    )
    except (ValueError, IndexError) as e:
        logger.debug(f"Could not parse hop_limit: {e}")

    return CommandResult.ok("Hop limit retrieved", data={'hop_limit': 3, 'raw': result.raw})


def set_hop_limit(hop_limit: int) -> CommandResult:
    """
    Set the device hop limit.

    Args:
        hop_limit: Hop limit value (1-7)
            - 1-2: Short range, low traffic (urban dense)
            - 3-4: Medium range, balanced (default)
            - 5-7: Long range, high traffic (rural/SAR)

    Returns:
        CommandResult with status
    """
    if not 1 <= hop_limit <= 7:
        return CommandResult.fail(f"hop_limit must be 1-7, got {hop_limit}")

    result = _run_command(["--set", "lora.hop_limit", str(hop_limit)])
    if result.success:
        result.message = f"Device hop limit set to {hop_limit}"
        result.data = {'hop_limit': hop_limit}
    return result


def get_device_role() -> CommandResult:
    """
    Get the current device role.

    Returns:
        CommandResult with data={'role': str, 'description': str}
    """
    result = _run_command(["--get", "device.role"])
    if not result.success:
        return result

    # Parse and add description
    roles = {
        'CLIENT': 'Standard client, rebroadcasts messages',
        'CLIENT_MUTE': 'Silent client, no rebroadcast (saves battery)',
        'ROUTER': 'Infrastructure router, always rebroadcasts, hop_limit=7',
        'ROUTER_CLIENT': 'Router + local functions',
        'REPEATER': 'Dedicated repeater, minimal processing',
        'TRACKER': 'Location tracking device',
        'SENSOR': 'Sensor reporting device',
        'TAK': 'ATAK/TAK integration mode',
        'CLIENT_HIDDEN': 'Hidden from node list',
        'LOST_AND_FOUND': 'Recovery mode',
        'TAK_TRACKER': 'TAK + tracking',
    }

    try:
        raw = result.raw or ''
        for line in raw.split('\n'):
            if 'role' in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    role = parts[-1].strip().upper()
                    return CommandResult.ok(
                        f"Device role: {role}",
                        data={
                            'role': role,
                            'description': roles.get(role, 'Unknown role'),
                        }
                    )
    except Exception as e:
        logger.debug(f"Could not parse role: {e}")

    return CommandResult.ok("Role retrieved", data={'role': 'UNKNOWN', 'raw': result.raw})


def set_device_role(role: str) -> CommandResult:
    """
    Set the device role.

    Args:
        role: One of CLIENT, CLIENT_MUTE, ROUTER, ROUTER_CLIENT, REPEATER, etc.

    Returns:
        CommandResult with status

    Note: Role affects routing behavior:
        - CLIENT: Normal rebroadcast
        - CLIENT_MUTE: No rebroadcast (good for listeners)
        - ROUTER: Always rebroadcast, uses hop_limit=7
    """
    valid_roles = [
        'CLIENT', 'CLIENT_MUTE', 'ROUTER', 'ROUTER_CLIENT',
        'REPEATER', 'TRACKER', 'SENSOR', 'TAK', 'CLIENT_HIDDEN',
        'LOST_AND_FOUND', 'TAK_TRACKER'
    ]

    role = role.upper()
    if role not in valid_roles:
        return CommandResult.fail(
            f"Invalid role '{role}'. Valid: {', '.join(valid_roles)}"
        )

    return _run_command(["--set", "device.role", role])


def diagnose_messaging() -> CommandResult:
    """
    Diagnose messaging configuration and connection.

    Returns:
        CommandResult with comprehensive diagnostic data
    """
    diagnostics = {
        'connection': {},
        'device': {},
        'routing': {},
        'pubsub': {},
        'recommendations': [],
    }

    # Connection test
    conn_result = test_connection()
    diagnostics['connection']['status'] = 'ok' if conn_result.success else 'error'
    diagnostics['connection']['error'] = conn_result.error if not conn_result.success else None

    if not conn_result.success:
        diagnostics['recommendations'].append(
            "Cannot connect to meshtastic. Check: systemctl status meshtasticd"
        )
        return CommandResult.ok("Diagnostics complete (connection failed)", data=diagnostics)

    # Get device settings
    hop_result = get_hop_limit()
    if hop_result.success and hop_result.data:
        diagnostics['device']['hop_limit'] = hop_result.data.get('hop_limit')

    role_result = get_device_role()
    if role_result.success and role_result.data:
        diagnostics['device']['role'] = role_result.data.get('role')
        diagnostics['device']['role_description'] = role_result.data.get('description')

    # Analyze routing configuration
    hop_limit = diagnostics['device'].get('hop_limit', 3)
    role = diagnostics['device'].get('role', 'CLIENT')

    if role == 'CLIENT_MUTE':
        diagnostics['routing']['rebroadcast'] = False
        diagnostics['routing']['note'] = 'CLIENT_MUTE: Messages received but not rebroadcast'
    elif role == 'ROUTER':
        diagnostics['routing']['rebroadcast'] = True
        diagnostics['routing']['note'] = 'ROUTER: Always rebroadcasts, uses max hops'
    else:
        diagnostics['routing']['rebroadcast'] = True
        diagnostics['routing']['note'] = f'Standard routing with hop_limit={hop_limit}'

    # Recommendations
    if hop_limit < 3 and role not in ('ROUTER', 'ROUTER_CLIENT'):
        diagnostics['recommendations'].append(
            f"Low hop_limit ({hop_limit}) may limit message reach. Consider: --set lora.hop_limit 5"
        )

    if role == 'CLIENT_MUTE':
        diagnostics['recommendations'].append(
            "CLIENT_MUTE role prevents message rebroadcast. "
            "Good for listening, but won't help mesh. Consider CLIENT role."
        )

    # Check pubsub (for RX)
    diagnostics['pubsub'] = diagnose_pubsub()

    return CommandResult.ok(
        "Messaging diagnostics complete",
        data=diagnostics
    )


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
    except KeyboardInterrupt:
        return CommandResult.fail("BLE scan aborted by user", error="interrupted")
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
