"""
Gateway CLI helpers — headless operation of the RNS-Meshtastic bridge.

Extracted from rns_bridge.py to keep that file under the 1,500 line threshold.
These functions manage a module-level singleton bridge for CLI/headless use.

Usage:
    from gateway.gateway_cli import start_gateway_headless, stop_gateway_headless

    start_gateway_headless()
    stats = get_gateway_stats()
    stop_gateway_headless()
"""

import logging
import os
import socket
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_active_bridge = None


def start_gateway_headless() -> bool:
    """
    Start the gateway bridge in headless mode (for CLI use).

    Returns True if started successfully, False otherwise.
    """
    global _active_bridge

    if _active_bridge is not None and _active_bridge._running:
        logger.warning("Gateway bridge is already running")
        print("Gateway bridge is already running")
        return True

    try:
        from .rns_bridge import RNSMeshtasticBridge

        _active_bridge = RNSMeshtasticBridge()
        success = _active_bridge.start()

        if success:
            print("Gateway bridge started successfully")
            mesh_ok = _active_bridge._mesh_handler.is_connected if _active_bridge._mesh_handler else False
            print(f"  Meshtastic: {'Connected' if mesh_ok else 'Disconnected'}")
            print(f"  RNS: {'Connected' if _active_bridge._connected_rns else 'Disconnected'}")
        else:
            print("Gateway bridge failed to start - check logs")

        return success
    except Exception as e:
        logger.error(f"Failed to start gateway: {e}")
        print(f"Failed to start gateway: {e}")
        return False


def stop_gateway_headless() -> bool:
    """
    Stop the gateway bridge (for CLI use).

    Returns True if stopped successfully.
    """
    global _active_bridge

    if _active_bridge is None:
        print("No active gateway bridge to stop")
        return True

    try:
        _active_bridge.stop()
        _active_bridge = None
        print("Gateway bridge stopped")
        return True
    except Exception as e:
        logger.error(f"Error stopping gateway: {e}")
        print(f"Error stopping gateway: {e}")
        return False


def get_gateway_stats() -> dict:
    """
    Get current gateway statistics (for CLI use).

    Returns dict with bridge status and statistics.
    """
    global _active_bridge

    if _active_bridge is None:
        return {
            'running': False,
            'status': 'Not started',
            'meshtastic_connected': False,
            'rns_connected': False,
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
        }

    try:
        status = _active_bridge.get_status()
        stats = status.get('statistics', {})
        result = {
            'running': _active_bridge._running,
            'status': 'Running' if _active_bridge._running else 'Stopped',
            'meshtastic_connected': _active_bridge._mesh_handler.is_connected if _active_bridge._mesh_handler else False,
            'rns_connected': _active_bridge._connected_rns,
            'messages_mesh_to_rns': stats.get('messages_mesh_to_rns', 0),
            'messages_rns_to_mesh': stats.get('messages_rns_to_mesh', 0),
            'errors': stats.get('errors', 0),
            'bounced': stats.get('bounced', 0),
            'uptime_seconds': status.get('uptime_seconds'),
        }
        # Include health metrics if available
        if hasattr(_active_bridge, 'health'):
            result['health'] = _active_bridge.health.get_summary()
        # Include delivery confirmation stats
        if hasattr(_active_bridge, 'delivery_tracker'):
            result['delivery'] = _active_bridge.delivery_tracker.get_stats()
        return result
    except Exception as e:
        logger.error(f"Error getting gateway stats: {e}")
        return {
            'running': False,
            'status': f'Error: {e}',
            'meshtastic_connected': False,
            'rns_connected': False,
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
        }


def is_gateway_running() -> bool:
    """Check if gateway bridge is currently running."""
    global _active_bridge
    return _active_bridge is not None and _active_bridge._running


def test_meshcore_connection() -> Dict:
    """
    Test MeshCore device availability without starting the bridge.

    Reads connection settings from gateway config and probes the device.

    Returns:
        Dict with 'reachable' (bool), 'connection_type', 'detail', 'error'
    """
    result = {
        'reachable': False,
        'connection_type': 'unknown',
        'detail': '',
        'error': None,
    }

    try:
        from .config import GatewayConfig
        config = GatewayConfig.load()
    except Exception as e:
        result['error'] = f"Config load failed: {e}"
        return result

    mc = getattr(config, 'meshcore', None)
    if mc is None or not mc.enabled:
        result['error'] = "MeshCore not configured or disabled"
        return result

    result['connection_type'] = mc.connection_type

    if mc.connection_type == "serial":
        device = mc.device_path
        if os.path.exists(device):
            result['reachable'] = True
            result['detail'] = f"Serial device present: {device}"
        else:
            result['error'] = f"Serial device not found: {device}"

    elif mc.connection_type == "tcp":
        host = mc.tcp_host or "localhost"
        port = mc.tcp_port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            conn_result = sock.connect_ex((host, port))
            sock.close()
            if conn_result == 0:
                result['reachable'] = True
                result['detail'] = f"TCP connection OK: {host}:{port}"
            else:
                result['error'] = f"TCP connection refused: {host}:{port}"
        except Exception as e:
            result['error'] = f"TCP error: {e}"

    elif mc.connection_type == "ble":
        result['error'] = "BLE transport not yet supported"

    else:
        result['error'] = f"Unknown connection type: {mc.connection_type}"

    return result


def send_meshcore_message(text: str, destination: str = None) -> Dict:
    """
    Send a text message via MeshCore through the running bridge.

    Args:
        text: Message text to send
        destination: Optional destination node ID (broadcast if None)

    Returns:
        Dict with 'sent' (bool), 'error' (str or None)
    """
    global _active_bridge

    if _active_bridge is None or not _active_bridge._running:
        return {'sent': False, 'error': "Gateway bridge not running"}

    mc_handler = getattr(_active_bridge, '_meshcore_handler', None)
    if mc_handler is None:
        return {'sent': False, 'error': "MeshCore handler not active"}

    try:
        ok = mc_handler.send_text(text, destination=destination)
        if ok:
            return {'sent': True, 'error': None}
        else:
            return {'sent': False, 'error': "send_text returned False"}
    except Exception as e:
        logger.error(f"Error sending MeshCore message: {e}")
        return {'sent': False, 'error': str(e)}


def get_meshcore_status() -> Dict:
    """
    Get detailed MeshCore subsystem status from the running bridge.

    Returns:
        Dict with connection state, device info, node count, and metrics.
    """
    global _active_bridge

    result = {
        'active': False,
        'connected': False,
        'device': None,
        'nodes_discovered': 0,
        'rx': 0,
        'tx': 0,
    }

    if _active_bridge is None or not _active_bridge._running:
        return result

    mc_handler = getattr(_active_bridge, '_meshcore_handler', None)
    if mc_handler is None:
        return result

    result['active'] = True

    try:
        result['connected'] = getattr(mc_handler, 'is_connected', False)
        result['device'] = getattr(mc_handler, '_device_path', None) or \
            getattr(mc_handler, '_tcp_host', None)
    except Exception:
        pass

    # Pull stats from gateway stats
    try:
        stats = get_gateway_stats()
        inner = stats.get('statistics', stats)
        result['rx'] = inner.get('meshcore_rx', 0)
        result['tx'] = inner.get('meshcore_tx', 0)
        result['nodes_discovered'] = inner.get('meshcore_nodes', 0)
    except Exception:
        pass

    return result
