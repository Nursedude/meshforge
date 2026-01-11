"""
Gateway Commands

Provides unified interface for RNS-Meshtastic gateway operations.
Used by both GTK and CLI interfaces.

The gateway is a cornerstone of MeshForge - bridging Meshtastic
and Reticulum (RNS) mesh networks.
"""

import logging
from typing import Optional, Dict, Any

from .base import CommandResult

logger = logging.getLogger(__name__)

# Module-level bridge instance (singleton pattern)
_bridge_instance = None


def _get_bridge():
    """Get or create the bridge instance."""
    global _bridge_instance

    if _bridge_instance is not None:
        return _bridge_instance

    try:
        from gateway.rns_bridge import RNSMeshtasticBridge
        _bridge_instance = RNSMeshtasticBridge()
        return _bridge_instance
    except ImportError as e:
        logger.warning(f"Gateway bridge not available: {e}")
        return None


def get_status() -> CommandResult:
    """
    Get current gateway bridge status.

    Returns:
        CommandResult with gateway status information
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available(
            "Gateway bridge not available",
            fix_hint="Ensure gateway module is installed"
        )

    try:
        status = bridge.get_status()

        # Build summary message
        mesh_status = "connected" if status.get('meshtastic_connected') else "disconnected"
        rns_status = "connected" if status.get('rns_connected') else "disconnected"

        if status.get('running'):
            message = f"Running - Mesh: {mesh_status}, RNS: {rns_status}"
        else:
            message = "Gateway not running"

        return CommandResult(
            success=status.get('running', False),
            message=message,
            data=status
        )
    except Exception as e:
        return CommandResult.fail(f"Error getting status: {e}")


def start() -> CommandResult:
    """
    Start the gateway bridge.

    Returns:
        CommandResult indicating success/failure
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available(
            "Gateway bridge not available",
            fix_hint="Ensure gateway module is installed"
        )

    try:
        # Check if already running
        status = bridge.get_status()
        if status.get('running'):
            return CommandResult.warn(
                "Gateway already running",
                data=status
            )

        # Start the bridge
        success = bridge.start()

        if success:
            return CommandResult.ok(
                "Gateway started",
                data=bridge.get_status()
            )
        else:
            return CommandResult.fail("Failed to start gateway")

    except Exception as e:
        return CommandResult.fail(f"Error starting gateway: {e}")


def stop() -> CommandResult:
    """
    Stop the gateway bridge.

    Returns:
        CommandResult indicating success/failure
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available(
            "Gateway bridge not available"
        )

    try:
        status = bridge.get_status()
        if not status.get('running'):
            return CommandResult.warn("Gateway not running")

        bridge.stop()
        return CommandResult.ok("Gateway stopped")

    except Exception as e:
        return CommandResult.fail(f"Error stopping gateway: {e}")


def restart() -> CommandResult:
    """Restart the gateway bridge."""
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available("Gateway bridge not available")

    try:
        bridge.stop()
        import time
        time.sleep(1)
        success = bridge.start()

        if success:
            return CommandResult.ok(
                "Gateway restarted",
                data=bridge.get_status()
            )
        else:
            return CommandResult.fail("Failed to restart gateway")

    except Exception as e:
        return CommandResult.fail(f"Error restarting gateway: {e}")


def test_connection() -> CommandResult:
    """
    Test connectivity to both Meshtastic and RNS networks.

    Returns:
        CommandResult with connection test results
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available(
            "Gateway bridge not available",
            fix_hint="Ensure gateway module is installed"
        )

    try:
        results = bridge.test_connection()

        mesh_ok = results.get('meshtastic', {}).get('connected', False)
        rns_ok = results.get('rns', {}).get('connected', False)

        if mesh_ok and rns_ok:
            message = "Both networks connected"
            success = True
        elif mesh_ok:
            message = "Meshtastic connected, RNS disconnected"
            success = False
        elif rns_ok:
            message = "RNS connected, Meshtastic disconnected"
            success = False
        else:
            message = "Both networks disconnected"
            success = False

        return CommandResult(
            success=success,
            message=message,
            data=results
        )

    except Exception as e:
        return CommandResult.fail(f"Connection test error: {e}")


def send_to_meshtastic(
    message: str,
    destination: Optional[str] = None,
    channel: int = 0
) -> CommandResult:
    """
    Send a message to Meshtastic network via gateway.

    Args:
        message: Message text
        destination: Destination node ID (None for broadcast)
        channel: Channel index
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available("Gateway bridge not available")

    status = bridge.get_status()
    if not status.get('running'):
        return CommandResult.fail("Gateway not running")

    if not status.get('meshtastic_connected'):
        return CommandResult.fail("Not connected to Meshtastic")

    try:
        success = bridge.send_to_meshtastic(message, destination, channel)
        if success:
            dest_str = destination or "broadcast"
            return CommandResult.ok(f"Message sent to Meshtastic ({dest_str})")
        else:
            return CommandResult.fail("Failed to send message")
    except Exception as e:
        return CommandResult.fail(f"Send error: {e}")


def send_to_rns(
    message: str,
    destination_hash: Optional[bytes] = None
) -> CommandResult:
    """
    Send a message to RNS network via LXMF.

    Args:
        message: Message text
        destination_hash: Destination identity hash (None for broadcast)
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available("Gateway bridge not available")

    status = bridge.get_status()
    if not status.get('running'):
        return CommandResult.fail("Gateway not running")

    if not status.get('rns_connected'):
        return CommandResult.fail("Not connected to RNS")

    try:
        success = bridge.send_to_rns(message, destination_hash)
        if success:
            return CommandResult.ok("Message sent to RNS")
        else:
            return CommandResult.fail("Failed to send message")
    except Exception as e:
        return CommandResult.fail(f"Send error: {e}")


def get_nodes() -> CommandResult:
    """
    Get all tracked nodes from both networks.

    Returns:
        CommandResult with unified node list
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available("Gateway bridge not available")

    try:
        stats = bridge.node_tracker.get_stats()
        nodes = bridge.node_tracker.get_all_nodes()

        return CommandResult.ok(
            f"Found {len(nodes)} nodes",
            data={
                'nodes': [n.__dict__ for n in nodes],
                'stats': stats,
                'count': len(nodes)
            }
        )
    except Exception as e:
        return CommandResult.fail(f"Error getting nodes: {e}")


def get_statistics() -> CommandResult:
    """
    Get gateway bridge statistics.

    Returns:
        CommandResult with message counts and performance stats
    """
    bridge = _get_bridge()
    if not bridge:
        return CommandResult.not_available("Gateway bridge not available")

    try:
        status = bridge.get_status()
        stats = status.get('statistics', {})

        # Calculate total bridged messages
        mesh_to_rns = stats.get('messages_mesh_to_rns', 0)
        rns_to_mesh = stats.get('messages_rns_to_mesh', 0)
        total_bridged = mesh_to_rns + rns_to_mesh

        return CommandResult.ok(
            f"Messages bridged: {total_bridged} (M→R: {mesh_to_rns}, R→M: {rns_to_mesh})",
            data={
                'statistics': stats,
                'total_bridged': total_bridged,
                'mesh_to_rns': mesh_to_rns,
                'rns_to_mesh': rns_to_mesh,
                'node_stats': status.get('node_stats', {}),
                'uptime_seconds': status.get('uptime_seconds')
            }
        )
    except Exception as e:
        return CommandResult.fail(f"Error getting statistics: {e}")


def get_config() -> CommandResult:
    """
    Get current gateway configuration.

    Returns:
        CommandResult with configuration details
    """
    try:
        from gateway.config import GatewayConfig
        config = GatewayConfig.load()

        return CommandResult.ok(
            "Configuration loaded",
            data={
                'enabled': config.enabled,
                'auto_start': config.auto_start,
                'meshtastic_host': config.meshtastic.host,
                'meshtastic_port': config.meshtastic.port,
                'meshtastic_channel': config.meshtastic.channel,
                'rns_config_dir': config.rns.config_dir,
                'rns_identity_name': config.rns.identity_name,
                'default_route': config.default_route,
                'routing_rules_count': len(config.routing_rules),
                'config_path': str(GatewayConfig.get_config_path()),
            }
        )
    except Exception as e:
        return CommandResult.fail(f"Error loading config: {e}")


def check_prerequisites() -> CommandResult:
    """
    Check if gateway prerequisites are met.

    Checks:
    - meshtasticd running and accessible
    - rnsd running
    - Required Python packages installed

    Returns:
        CommandResult with prerequisite status
    """
    from . import service

    checks = {
        'meshtasticd': False,
        'rnsd': False,
        'rns_package': False,
        'lxmf_package': False,
        'meshtastic_package': False,
    }
    issues = []

    # Check services
    mesh_status = service.check_status('meshtasticd')
    if mesh_status.success:
        checks['meshtasticd'] = True
    else:
        issues.append(f"meshtasticd: {mesh_status.message}")

    rns_status = service.check_status('rnsd')
    if rns_status.success:
        checks['rnsd'] = True
    else:
        issues.append(f"rnsd: {rns_status.message}")

    # Check packages
    try:
        import RNS
        checks['rns_package'] = True
    except ImportError:
        issues.append("RNS package not installed (pip install rns)")

    try:
        import LXMF
        checks['lxmf_package'] = True
    except ImportError:
        issues.append("LXMF package not installed (pip install lxmf)")

    try:
        import meshtastic
        checks['meshtastic_package'] = True
    except ImportError:
        issues.append("meshtastic package not installed (pip install meshtastic)")

    all_good = all(checks.values())

    if all_good:
        return CommandResult.ok(
            "All prerequisites met",
            data={'checks': checks}
        )
    else:
        return CommandResult.fail(
            f"{len(issues)} prerequisite(s) missing",
            data={'checks': checks, 'issues': issues}
        )


def is_available() -> bool:
    """Check if gateway functionality is available."""
    try:
        from gateway.rns_bridge import RNSMeshtasticBridge
        return True
    except ImportError:
        return False
