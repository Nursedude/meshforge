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
from typing import Optional

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
            'mesh_to_rns_attempted': 0,
            'mesh_to_rns_delivered': 0,
            'mesh_to_rns_dropped': 0,
            'rns_to_mesh_attempted': 0,
            'rns_to_mesh_delivered': 0,
            'rns_to_mesh_dropped': 0,
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
            'mesh_to_rns_attempted': stats.get('mesh_to_rns_attempted', 0),
            'mesh_to_rns_delivered': stats.get('mesh_to_rns_delivered', 0),
            'mesh_to_rns_dropped': stats.get('mesh_to_rns_dropped', 0),
            'rns_to_mesh_attempted': stats.get('rns_to_mesh_attempted', 0),
            'rns_to_mesh_delivered': stats.get('rns_to_mesh_delivered', 0),
            'rns_to_mesh_dropped': stats.get('rns_to_mesh_dropped', 0),
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
