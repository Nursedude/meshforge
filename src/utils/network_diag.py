"""
Network Diagnostics Utilities

Pure Python functions for network diagnostics.
Used by both GTK UI and Web UI.

Functions:
- parse_proc_net: Parse /proc/net/{tcp,udp} for connection info
- get_socket_to_process: Map socket inodes to process names
- check_port: Check if a TCP/UDP port is open
"""

import os
import socket
from pathlib import Path
from typing import Dict, List, Optional

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


def hex_to_ip(hex_addr: str) -> str:
    """Convert hex address from /proc/net to dotted IP format (little endian).

    Args:
        hex_addr: 8-character hex string like '0100007F'

    Returns:
        Dotted IP string like '127.0.0.1'
    """
    try:
        ip_int = int(hex_addr, 16)
        ip_bytes = [
            (ip_int >> 0) & 0xFF,
            (ip_int >> 8) & 0xFF,
            (ip_int >> 16) & 0xFF,
            (ip_int >> 24) & 0xFF,
        ]
        return '.'.join(str(b) for b in ip_bytes)
    except (ValueError, TypeError):
        return '0.0.0.0'


def hex_to_ipv6(hex_addr: str) -> str:
    """Convert hex address from /proc/net to IPv6 format.

    Args:
        hex_addr: 32-character hex string

    Returns:
        IPv6 address string
    """
    try:
        if len(hex_addr) != 32:
            return hex_addr

        # Split into 8 groups of 4 hex chars
        groups = []
        for i in range(0, 32, 8):
            # Each 8-char segment is little-endian 32-bit
            segment = hex_addr[i:i+8]
            # Reverse byte order within each 32-bit word
            reversed_seg = segment[6:8] + segment[4:6] + segment[2:4] + segment[0:2]
            groups.append(reversed_seg[0:4])
            groups.append(reversed_seg[4:8])
        return ':'.join(groups).lower()
    except (ValueError, TypeError):
        return hex_addr


def parse_proc_net(protocol: str) -> List[Dict]:
    """Parse /proc/net/{tcp,udp} for connection info.

    Args:
        protocol: 'tcp', 'udp', 'tcp6', or 'udp6'

    Returns:
        List of connection dicts with keys: ip, port, state, inode
    """
    results = []
    proc_file = f'/proc/net/{protocol}'

    if not os.path.exists(proc_file):
        return results

    is_v6 = protocol.endswith('6')
    hex_converter = hex_to_ipv6 if is_v6 else hex_to_ip

    try:
        with open(proc_file, 'r') as f:
            lines = f.readlines()[1:]  # Skip header

        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue

            local_addr = parts[1]
            state_hex = parts[3]
            inode = parts[9]

            try:
                addr_parts = local_addr.split(':')
                ip_str = hex_converter(addr_parts[0])
                port = int(addr_parts[1], 16)
                state = TCP_STATES.get(state_hex.upper(), state_hex)

                results.append({
                    'ip': ip_str,
                    'port': port,
                    'state': state,
                    'inode': inode
                })
            except (ValueError, IndexError):
                continue

    except (FileNotFoundError, PermissionError):
        pass

    return results


def get_socket_to_process() -> Dict[str, str]:
    """Map socket inodes to process names.

    Returns:
        Dict mapping inode string to "process_name (PID pid)"
    """
    inode_map = {}

    try:
        for pid_dir in Path('/proc').iterdir():
            if not pid_dir.name.isdigit():
                continue

            pid = pid_dir.name
            try:
                # Get process name
                comm_file = pid_dir / 'comm'
                if comm_file.exists():
                    proc_name = comm_file.read_text().strip()
                else:
                    proc_name = "unknown"

                # Check fd directory for sockets
                fd_dir = pid_dir / 'fd'
                if fd_dir.exists():
                    for fd_link in fd_dir.iterdir():
                        try:
                            target_str = str(fd_link.readlink())
                            if target_str.startswith('socket:['):
                                inode = target_str[8:-1]  # Extract from socket:[12345]
                                inode_map[inode] = f"{proc_name} (PID {pid})"
                        except (OSError, PermissionError):
                            continue
            except (OSError, PermissionError):
                continue
    except Exception:
        pass

    return inode_map


def get_listening_ports(protocol: str = 'tcp') -> List[Dict]:
    """Get all listening ports for a protocol.

    Args:
        protocol: 'tcp' or 'udp'

    Returns:
        List of dicts with ip, port, process info
    """
    connections = parse_proc_net(protocol)
    # For TCP, filter to LISTEN state; UDP is stateless
    if protocol.startswith('tcp'):
        connections = [c for c in connections if c['state'] == 'LISTEN']

    # Enrich with process info
    inode_map = get_socket_to_process()
    for conn in connections:
        conn['process'] = inode_map.get(conn['inode'], 'unknown')

    return connections


def check_port_open(port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
    """Check if a TCP port is open.

    Args:
        port: Port number
        host: Hostname (default localhost)
        timeout: Connection timeout in seconds

    Returns:
        True if port is open
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.error, OSError):
        return False


def get_all_connections() -> Dict[str, List[Dict]]:
    """Get all network connections organized by protocol.

    Returns:
        Dict with keys: tcp, udp, tcp6, udp6
    """
    inode_map = get_socket_to_process()

    result = {}
    for protocol in ['tcp', 'udp', 'tcp6', 'udp6']:
        connections = parse_proc_net(protocol)
        for conn in connections:
            conn['process'] = inode_map.get(conn['inode'], 'unknown')
        result[protocol] = connections

    return result


def find_process_on_port(port: int, protocol: str = 'tcp') -> Optional[str]:
    """Find which process is using a specific port.

    Args:
        port: Port number to check
        protocol: 'tcp' or 'udp'

    Returns:
        Process info string or None if not found
    """
    connections = parse_proc_net(protocol)
    inode_map = get_socket_to_process()

    for conn in connections:
        if conn['port'] == port:
            return inode_map.get(conn['inode'], 'unknown')

    return None
