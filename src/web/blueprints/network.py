"""
Network Blueprint - Network diagnostics

Handles network port and connection diagnostics.
"""

from flask import Blueprint, jsonify, request
import subprocess
import os
import re
import struct
import socket

network_bp = Blueprint('network', __name__)

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
    """Convert hex address from /proc/net to dotted IP format (little endian)."""
    try:
        addr_int = int(hex_addr, 16)
        # Convert from network byte order (big endian) as stored
        return socket.inet_ntoa(struct.pack('<I', addr_int))
    except Exception:
        return '0.0.0.0'


def parse_proc_net_line(line: str) -> dict:
    """Parse a single line from /proc/net/{tcp,udp}.

    Returns dict with connection info or None if invalid/header line.
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
    """Parse /proc/net/{tcp,udp} for connection info."""
    connections = []
    proc_file = f'/proc/net/{protocol}'

    if not os.path.exists(proc_file):
        return connections

    try:
        with open(proc_file, 'r') as f:
            lines = f.readlines()[1:]  # Skip header

        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue

            local_addr = parts[1]
            remote_addr = parts[2]
            state = parts[3]

            # Parse hex addresses
            local_ip, local_port = local_addr.split(':')
            local_port = int(local_port, 16)

            connections.append({
                'local_port': local_port,
                'state': state,
                'raw': line.strip()[:80]
            })

    except Exception:
        pass

    return connections


@network_bp.route('/network/udp')
def api_network_udp():
    """Get UDP port information."""
    connections = parse_proc_net('udp')
    return jsonify({'udp': connections})


@network_bp.route('/network/tcp')
def api_network_tcp():
    """Get TCP port information."""
    connections = parse_proc_net('tcp')
    return jsonify({'tcp': connections})


@network_bp.route('/network/rns-ports')
def api_network_rns_ports():
    """Get RNS-related port information."""
    rns_ports = {
        'standard': [
            {'port': 37428, 'name': 'RNS AutoInterface', 'protocol': 'UDP'},
            {'port': 4242, 'name': 'RNS TCP Server', 'protocol': 'TCP'},
            {'port': 4965, 'name': 'RNS Testnet', 'protocol': 'TCP'},
        ],
        'active': []
    }

    # Check which ports are in use
    for port_info in rns_ports['standard']:
        try:
            import socket
            sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM if port_info['protocol'] == 'UDP' else socket.SOCK_STREAM
            )
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port_info['port']))
            sock.close()

            if result == 0:
                rns_ports['active'].append(port_info['port'])
        except Exception:
            pass

    return jsonify(rns_ports)


@network_bp.route('/network/meshtastic-ports')
def api_network_meshtastic_ports():
    """Get Meshtastic-related port information."""
    meshtastic_ports = {
        'standard': [
            {'port': 4403, 'name': 'meshtasticd TCP', 'protocol': 'TCP'},
            {'port': 8080, 'name': 'Web Interface', 'protocol': 'TCP'},
        ],
        'active': []
    }

    # Check which ports are in use
    for port_info in meshtastic_ports['standard']:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port_info['port']))
            sock.close()

            if result == 0:
                meshtastic_ports['active'].append(port_info['port'])
        except Exception:
            pass

    return jsonify(meshtastic_ports)


@network_bp.route('/network/full-diagnostics')
def api_network_full_diagnostics():
    """Get complete network diagnostics."""
    diagnostics = {
        'tcp': parse_proc_net('tcp'),
        'udp': parse_proc_net('udp'),
        'interfaces': [],
        'listening_ports': []
    }

    # Get network interfaces
    try:
        result = subprocess.run(
            ['ip', '-j', 'addr'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json
            diagnostics['interfaces'] = json.loads(result.stdout)
    except Exception:
        pass

    # Get listening ports
    try:
        result = subprocess.run(
            ['ss', '-tulnp'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n')[1:]:
                if line.strip():
                    diagnostics['listening_ports'].append(line.strip())
    except Exception:
        pass

    return jsonify(diagnostics)
