"""
Web Utilities - Pure Python utilities for web blueprints

These functions don't depend on Flask and can be tested independently.

Note: Network parsing functions are now in utils.network_diag.
This module re-exports them for backwards compatibility.
"""

import struct
import socket
import re
import os

# Import shared network diagnostics
try:
    from utils.network_diag import (
        TCP_STATES,
        hex_to_ip as _hex_to_ip,
        parse_proc_net as _parse_proc_net,
        get_socket_to_process,
        check_port_open,
    )
    _HAS_NETWORK_DIAG = True
except ImportError:
    _HAS_NETWORK_DIAG = False
    # Fallback TCP states
    TCP_STATES = {
        '01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
        '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
        '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
        '0A': 'LISTEN', '0B': 'CLOSING',
    }

# Valid service actions
VALID_ACTIONS = {'start', 'stop', 'restart', 'status'}

# Services that can be controlled
ALLOWED_SERVICES = {'meshtasticd', 'rnsd'}


def hex_to_ip(hex_addr: str) -> str:
    """Convert hex address from /proc/net to dotted IP format (little endian).

    Args:
        hex_addr: 8-character hex string like '0100007F'

    Returns:
        Dotted IP string like '127.0.0.1'
    """
    if _HAS_NETWORK_DIAG:
        return _hex_to_ip(hex_addr)
    try:
        addr_int = int(hex_addr, 16)
        return socket.inet_ntoa(struct.pack('<I', addr_int))
    except Exception:
        return '0.0.0.0'


def parse_proc_net_line(line: str) -> dict:
    """Parse a single line from /proc/net/{tcp,udp}.

    Args:
        line: A line from /proc/net/tcp or /proc/net/udp

    Returns:
        Dict with connection info or None if invalid/header line
    """
    line = line.strip()
    if not line or line.startswith('sl') or 'local_address' in line:
        return None

    parts = line.split()
    if len(parts) < 10:
        return None

    try:
        local_addr = parts[1]
        remote_addr = parts[2]
        state_hex = parts[3]

        local_ip_hex, local_port_hex = local_addr.split(':')
        local_ip = hex_to_ip(local_ip_hex)
        local_port = int(local_port_hex, 16)

        remote_ip_hex, remote_port_hex = remote_addr.split(':')
        remote_ip = hex_to_ip(remote_ip_hex)
        remote_port = int(remote_port_hex, 16)

        state = TCP_STATES.get(state_hex, state_hex)

        return {
            'local_ip': local_ip,
            'local_port': local_port,
            'remote_ip': remote_ip,
            'remote_port': remote_port,
            'state': state,
            'state_hex': state_hex,
        }
    except Exception:
        return None


def parse_proc_net(protocol: str) -> list:
    """Parse /proc/net/{tcp,udp} for connection info.

    Args:
        protocol: 'tcp' or 'udp'

    Returns:
        List of connection dicts
    """
    if _HAS_NETWORK_DIAG:
        # Use shared module - it returns dicts with 'ip'/'port' keys
        # Transform to match legacy format with 'local_ip'/'local_port'
        results = []
        for conn in _parse_proc_net(protocol):
            results.append({
                'local_ip': conn.get('ip', '0.0.0.0'),
                'local_port': conn.get('port', 0),
                'remote_ip': '0.0.0.0',  # Not in shared module output
                'remote_port': 0,
                'state': conn.get('state', ''),
                'state_hex': '',
            })
        return results

    # Fallback implementation
    connections = []
    proc_file = f'/proc/net/{protocol}'

    if not os.path.exists(proc_file):
        return connections

    try:
        with open(proc_file, 'r') as f:
            lines = f.readlines()[1:]  # Skip header

        for line in lines:
            parsed = parse_proc_net_line(line)
            if parsed:
                connections.append(parsed)

    except Exception:
        pass

    return connections


def validate_config_name(config_name: str) -> bool:
    """Validate config file name to prevent path traversal.

    Args:
        config_name: Config filename to validate

    Returns:
        True if valid, False otherwise
    """
    if not config_name:
        return False
    # Only allow alphanumeric, dash, underscore, and .yaml/.yml extension
    pattern = r'^[a-zA-Z0-9_-]+\.ya?ml$'
    return bool(re.match(pattern, config_name))
