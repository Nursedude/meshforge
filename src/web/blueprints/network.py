"""
Network Blueprint - Network diagnostics

Handles network port and connection diagnostics.
"""

from flask import Blueprint, jsonify, request
import subprocess
import socket

# Import utilities from web.utils (allows testing without Flask)
from web.utils import (
    TCP_STATES,
    hex_to_ip,
    parse_proc_net_line,
    parse_proc_net,
)

network_bp = Blueprint('network', __name__)


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
