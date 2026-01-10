"""
Web Utilities - Pure Python utilities for web blueprints

These functions don't depend on Flask and can be tested independently.
"""

import struct
import socket
import re
import os

# TCP state mapping (hex to name)
TCP_STATES = {
    '01': 'ESTABLISHED',
    '02': 'SYN_SENT',
    '03': 'SYN_RECV',
    '04': 'FIN_WAIT1',
    '05': 'FIN_WAIT2',
    '06': 'TIME_WAIT',
    '07': 'CLOSE',
    '08': 'CLOSE_WAIT',
    '09': 'LAST_ACK',
    '0A': 'LISTEN',
    '0B': 'CLOSING',
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
    try:
        addr_int = int(hex_addr, 16)
        # Convert from network byte order (big endian) as stored
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
