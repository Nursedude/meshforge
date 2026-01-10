"""
Gateway Blueprint - Meshtastic ↔ RNS Bridge Control

Provides API endpoints for:
- Gateway status and control (start/stop)
- Bridge statistics (messages, nodes)
- Configuration management
- Diagnostic checks
"""

import os
import json
import socket
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from flask import Blueprint, jsonify, request

# Import meshtastic connection utilities
try:
    from utils.meshtastic_connection import (
        MESHTASTIC_CONNECTION_LOCK,
        wait_for_cooldown,
        safe_close_interface
    )
    HAS_MESHTASTIC_LOCK = True
except ImportError:
    HAS_MESHTASTIC_LOCK = False
    MESHTASTIC_CONNECTION_LOCK = None
    def wait_for_cooldown():
        import time
        time.sleep(1.0)
    def safe_close_interface(iface):
        if iface:
            try:
                iface.close()
            except Exception:
                pass

gateway_bp = Blueprint('gateway', __name__)

# Gateway state (in-memory, bridge runs in separate process/thread)
_gateway_state = {
    'running': False,
    'meshtastic_connected': False,
    'rns_connected': False,
    'stats': {
        'mesh_to_rns': 0,
        'rns_to_mesh': 0,
        'total_nodes': 0,
        'errors': 0,
        'last_activity': None
    }
}
_gateway_lock = threading.Lock()

# Bridge instance (if running in-process)
_bridge_instance = None


def get_config_path():
    """Get gateway config path"""
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        home = Path(f'/home/{sudo_user}')
    else:
        home = Path.home()
    return home / '.config' / 'meshforge' / 'gateway.json'


def load_gateway_config():
    """Load gateway configuration"""
    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            pass

    # Default config
    return {
        'enabled': False,
        'meshtastic': {
            'host': 'localhost',
            'port': 4403,
            'channel': 0
        },
        'rns': {
            'config_dir': None,
            'identity_name': 'gateway'
        },
        'routing': {
            'mode': 'bridge'
        }
    }


def save_gateway_config(config):
    """Save gateway configuration"""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)


def check_meshtasticd():
    """Check if meshtasticd is reachable"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        result = sock.connect_ex(('localhost', 4403))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_rnsd():
    """Check if rnsd is running"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'rnsd'],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def get_tracked_nodes():
    """Get nodes from the node tracker cache"""
    nodes = {'meshtastic': [], 'rns': [], 'total': 0}

    # Try to read from cache file
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        home = Path(f'/home/{sudo_user}')
    else:
        home = Path.home()

    cache_file = home / '.config' / 'meshforge' / 'node_cache.json'

    if cache_file.exists():
        try:
            with open(cache_file) as f:
                data = json.load(f)
                nodes['meshtastic'] = data.get('meshtastic', [])
                nodes['rns'] = data.get('rns', [])
                nodes['total'] = len(nodes['meshtastic']) + len(nodes['rns'])
        except Exception:
            pass

    return nodes


@gateway_bp.route('/gateway/status')
def gateway_status():
    """Get gateway status and statistics"""
    with _gateway_lock:
        state = _gateway_state.copy()

    # Check live service status
    meshtastic_ok = check_meshtasticd()
    rns_ok = check_rnsd()

    # Get config
    config = load_gateway_config()

    # Get tracked nodes count
    nodes = get_tracked_nodes()

    return jsonify({
        'running': state['running'],
        'enabled': config.get('enabled', False),
        'services': {
            'meshtasticd': meshtastic_ok,
            'rnsd': rns_ok
        },
        'connections': {
            'meshtastic': state['meshtastic_connected'],
            'rns': state['rns_connected']
        },
        'stats': {
            'mesh_to_rns': state['stats']['mesh_to_rns'],
            'rns_to_mesh': state['stats']['rns_to_mesh'],
            'meshtastic_nodes': len(nodes['meshtastic']),
            'rns_nodes': len(nodes['rns']),
            'total_nodes': nodes['total'],
            'errors': state['stats']['errors'],
            'last_activity': state['stats']['last_activity']
        },
        'config': {
            'meshtastic_host': config.get('meshtastic', {}).get('host', 'localhost'),
            'meshtastic_port': config.get('meshtastic', {}).get('port', 4403),
            'routing_mode': config.get('routing', {}).get('mode', 'bridge')
        }
    })


@gateway_bp.route('/gateway/start', methods=['POST'])
def gateway_start():
    """Start the gateway bridge"""
    global _bridge_instance

    # Check prerequisites
    if not check_meshtasticd():
        return jsonify({
            'success': False,
            'error': 'meshtasticd not running on port 4403'
        }), 400

    if not check_rnsd():
        return jsonify({
            'success': False,
            'error': 'rnsd not running (start with: rnsd)'
        }), 400

    try:
        # Try to import and start the bridge
        from gateway.rns_bridge import RNSMeshtasticBridge

        config = load_gateway_config()

        with _gateway_lock:
            if _gateway_state['running']:
                return jsonify({'success': True, 'message': 'Gateway already running'})

            _bridge_instance = RNSMeshtasticBridge(
                meshtastic_host=config.get('meshtastic', {}).get('host', 'localhost'),
                meshtastic_port=config.get('meshtastic', {}).get('port', 4403)
            )

            if _bridge_instance.start():
                _gateway_state['running'] = True
                _gateway_state['meshtastic_connected'] = True
                _gateway_state['rns_connected'] = True
                return jsonify({'success': True, 'message': 'Gateway started'})
            else:
                return jsonify({'success': False, 'error': 'Failed to start bridge'}), 500

    except ImportError:
        return jsonify({
            'success': False,
            'error': 'Gateway module not available'
        }), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@gateway_bp.route('/gateway/stop', methods=['POST'])
def gateway_stop():
    """Stop the gateway bridge"""
    global _bridge_instance

    with _gateway_lock:
        if not _gateway_state['running']:
            return jsonify({'success': True, 'message': 'Gateway not running'})

        try:
            if _bridge_instance:
                _bridge_instance.stop()
                _bridge_instance = None

            _gateway_state['running'] = False
            _gateway_state['meshtastic_connected'] = False
            _gateway_state['rns_connected'] = False

            return jsonify({'success': True, 'message': 'Gateway stopped'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500


@gateway_bp.route('/gateway/config', methods=['GET'])
def gateway_config_get():
    """Get gateway configuration"""
    config = load_gateway_config()
    return jsonify(config)


@gateway_bp.route('/gateway/config', methods=['POST'])
def gateway_config_set():
    """Update gateway configuration"""
    try:
        new_config = request.get_json()
        if not new_config:
            return jsonify({'error': 'No config provided'}), 400

        # Merge with existing config
        config = load_gateway_config()

        if 'enabled' in new_config:
            config['enabled'] = bool(new_config['enabled'])
        if 'meshtastic' in new_config:
            config['meshtastic'].update(new_config['meshtastic'])
        if 'rns' in new_config:
            config['rns'].update(new_config['rns'])
        if 'routing' in new_config:
            config['routing'].update(new_config['routing'])

        save_gateway_config(config)
        return jsonify({'success': True, 'config': config})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@gateway_bp.route('/gateway/nodes')
def gateway_nodes():
    """Get all tracked nodes from both networks"""
    nodes = get_tracked_nodes()

    # Format nodes for display
    all_nodes = []

    for node in nodes.get('meshtastic', []):
        all_nodes.append({
            'network': 'meshtastic',
            'id': node.get('id', ''),
            'name': node.get('name', 'Unknown'),
            'short': node.get('short', ''),
            'last_seen': node.get('last_seen'),
            'position': node.get('position')
        })

    for node in nodes.get('rns', []):
        all_nodes.append({
            'network': 'rns',
            'id': node.get('hash', '')[:16],
            'name': node.get('name', 'RNS Node'),
            'short': 'RNS',
            'last_seen': node.get('last_seen'),
            'position': node.get('position')
        })

    return jsonify({
        'nodes': all_nodes,
        'meshtastic_count': len(nodes.get('meshtastic', [])),
        'rns_count': len(nodes.get('rns', [])),
        'total': len(all_nodes)
    })


@gateway_bp.route('/gateway/diagnostic', methods=['POST'])
def gateway_diagnostic():
    """Run gateway diagnostic checks"""
    results = []

    # Check Python version
    import sys
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    results.append({
        'check': 'Python Version',
        'status': 'PASS' if sys.version_info >= (3, 9) else 'WARN',
        'message': py_version
    })

    # Check meshtasticd
    mesh_ok = check_meshtasticd()
    results.append({
        'check': 'meshtasticd (TCP 4403)',
        'status': 'PASS' if mesh_ok else 'FAIL',
        'message': 'Connected' if mesh_ok else 'Not running - start with: sudo systemctl start meshtasticd'
    })

    # Check rnsd
    rns_ok = check_rnsd()
    results.append({
        'check': 'rnsd',
        'status': 'PASS' if rns_ok else 'FAIL',
        'message': 'Running' if rns_ok else 'Not running - start with: rnsd'
    })

    # Check RNS library
    try:
        import RNS
        results.append({
            'check': 'RNS Library',
            'status': 'PASS',
            'message': f'Version {RNS.__version__}'
        })
    except ImportError:
        results.append({
            'check': 'RNS Library',
            'status': 'FAIL',
            'message': 'Not installed - pip install rns'
        })

    # Check meshtastic library
    try:
        import meshtastic
        ver = getattr(meshtastic, '__version__', 'unknown')
        results.append({
            'check': 'Meshtastic Library',
            'status': 'PASS',
            'message': f'Version {ver}'
        })
    except ImportError:
        results.append({
            'check': 'Meshtastic Library',
            'status': 'FAIL',
            'message': 'Not installed - pip install meshtastic'
        })

    # Check gateway config
    config_path = get_config_path()
    if config_path.exists():
        results.append({
            'check': 'Gateway Config',
            'status': 'PASS',
            'message': str(config_path)
        })
    else:
        results.append({
            'check': 'Gateway Config',
            'status': 'WARN',
            'message': 'Not found - will use defaults'
        })

    # Check RNS config
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        home = Path(f'/home/{sudo_user}')
    else:
        home = Path.home()

    rns_config = home / '.reticulum' / 'config'
    if rns_config.exists():
        results.append({
            'check': 'RNS Config',
            'status': 'PASS',
            'message': str(rns_config)
        })
    else:
        results.append({
            'check': 'RNS Config',
            'status': 'WARN',
            'message': 'Not found - run rnsd once to create'
        })

    # Overall status
    failed = sum(1 for r in results if r['status'] == 'FAIL')
    warned = sum(1 for r in results if r['status'] == 'WARN')

    return jsonify({
        'results': results,
        'summary': {
            'passed': len(results) - failed - warned,
            'failed': failed,
            'warnings': warned,
            'ready': failed == 0
        }
    })


@gateway_bp.route('/gateway/test', methods=['POST'])
def gateway_test():
    """Test gateway connections"""
    results = {
        'meshtastic': {'success': False, 'message': ''},
        'rns': {'success': False, 'message': ''}
    }

    # Test meshtasticd connection
    if check_meshtasticd():
        # Acquire global lock - meshtasticd only supports one TCP connection
        lock_acquired = False
        if HAS_MESHTASTIC_LOCK and MESHTASTIC_CONNECTION_LOCK:
            lock_acquired = MESHTASTIC_CONNECTION_LOCK.acquire(timeout=5.0)
            if lock_acquired:
                wait_for_cooldown()
        else:
            lock_acquired = True

        if not lock_acquired:
            results['meshtastic'] = {
                'success': False,
                'message': 'Connection lock busy (another operation in progress)'
            }
        else:
            iface = None
            try:
                import meshtastic.tcp_interface
                iface = meshtastic.tcp_interface.TCPInterface(hostname='localhost')
                node_count = len(iface.nodes) if hasattr(iface, 'nodes') and iface.nodes else 0

                results['meshtastic'] = {
                    'success': True,
                    'message': f'Connected - {node_count} nodes'
                }
            except Exception as e:
                results['meshtastic'] = {
                    'success': False,
                    'message': str(e)
                }
            finally:
                if iface:
                    safe_close_interface(iface)
                if HAS_MESHTASTIC_LOCK and MESHTASTIC_CONNECTION_LOCK:
                    try:
                        MESHTASTIC_CONNECTION_LOCK.release()
                    except RuntimeError:
                        pass
    else:
        results['meshtastic'] = {
            'success': False,
            'message': 'meshtasticd not running'
        }

    # Test RNS
    if check_rnsd():
        try:
            import RNS
            # Just check if we can import and RNS is configured
            results['rns'] = {
                'success': True,
                'message': 'rnsd running'
            }
        except ImportError:
            results['rns'] = {
                'success': False,
                'message': 'RNS library not installed'
            }
    else:
        results['rns'] = {
            'success': False,
            'message': 'rnsd not running'
        }

    return jsonify(results)
