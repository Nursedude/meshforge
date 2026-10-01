"""
Gateway Commands

Provides unified interface for RNS-Meshtastic gateway operations.
Used by both TUI and CLI interfaces.

The gateway is a cornerstone of MeshForge - bridging Meshtastic
and Reticulum (RNS) mesh networks.
"""

import logging
from typing import Optional, Dict, Any

from .base import CommandResult
from utils.safe_import import safe_import

# gateway.rns_bridge is imported inside the functions that
# use them: at module level they pull the full RNS/LXMF/meshtastic stack
# (~400 ms) into every TUI handler that imports commands.gateway.

logger = logging.getLogger(__name__)

_HAS_BRIDGE = True

# Module-level bridge instance (singleton pattern)
_bridge_instance = None


def set_bridge(bridge):
    """Register an external bridge instance (e.g., from TUI)."""
    global _bridge_instance
    _bridge_instance = bridge
    logger.info("Gateway bridge registered from external source")


def _get_bridge():
    """Get or create the bridge instance."""
    global _bridge_instance

    if _bridge_instance is not None:
        return _bridge_instance

    if not _HAS_BRIDGE:
        logger.warning("Gateway bridge not available: gateway.rns_bridge not installed")
        return None

    try:
        from gateway.rns_bridge import RNSMeshtasticBridge
    except ImportError as e:
        # Deferred import (TUI-startup perf): a broken bridge stack must
        # still read as cleanly unavailable, not a mid-call traceback.
        logger.warning("Gateway bridge not available: %s", e)
        return None
    _bridge_instance = RNSMeshtasticBridge()
    return _bridge_instance


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


def check_health() -> CommandResult:
    """
    Comprehensive health check for gateway bridge.

    Checks:
    - Connection status (Meshtastic, RNS)
    - Message flow (recent activity)
    - Queue status (pending messages)
    - Error rates
    - Configuration validity

    Returns:
        CommandResult with health status and recommendations
    """
    import socket
    from datetime import datetime, timedelta

    health_status = {
        'overall': 'unknown',
        'checks': {},
        'warnings': [],
        'errors': [],
        'recommendations': [],
    }

    # Check 1: Gateway bridge availability
    bridge = _get_bridge()
    if not bridge:
        health_status['checks']['bridge_available'] = False
        health_status['errors'].append("Gateway bridge not instantiated")
        health_status['recommendations'].append("Start the gateway bridge")
        health_status['overall'] = 'critical'
        return CommandResult(
            success=False,
            message="Gateway bridge not available",
            data=health_status
        )

    health_status['checks']['bridge_available'] = True

    # Check 2: Gateway running
    try:
        status = bridge.get_status()
        is_running = status.get('running', False)
        health_status['checks']['bridge_running'] = is_running

        if not is_running:
            health_status['errors'].append("Gateway bridge is not running")
            health_status['recommendations'].append("Start the gateway: gateway.start()")
    except Exception as e:
        health_status['checks']['bridge_running'] = False
        health_status['errors'].append(f"Cannot get bridge status: {e}")

    # Check 3: Meshtastic connection
    mesh_connected = status.get('meshtastic_connected', False)
    health_status['checks']['meshtastic_connected'] = mesh_connected
    if not mesh_connected:
        health_status['errors'].append("Not connected to Meshtastic")
        health_status['recommendations'].append(
            "Check meshtasticd: sudo systemctl status meshtasticd"
        )

    # Check 4: RNS connection
    rns_connected = status.get('rns_connected', False)
    health_status['checks']['rns_connected'] = rns_connected
    if not rns_connected:
        health_status['warnings'].append("Not connected to RNS (may be expected if rnsd running)")

    # Check 5: Port accessibility
    try:
        from gateway.config import GatewayConfig
        config = GatewayConfig.load()
        host = config.meshtastic.host
        port = config.meshtastic.port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            port_open = result == 0

        health_status['checks']['meshtasticd_port_open'] = port_open
        if not port_open:
            health_status['errors'].append(f"meshtasticd port {port} not accessible")
            health_status['recommendations'].append(
                f"Start meshtasticd or check host:port ({host}:{port})"
            )
    except Exception as e:
        health_status['checks']['meshtasticd_port_open'] = False
        health_status['warnings'].append(f"Port check failed: {e}")

    # Check 6: Message statistics
    stats = status.get('statistics', {})
    mesh_to_rns = stats.get('messages_mesh_to_rns', 0)
    rns_to_mesh = stats.get('messages_rns_to_mesh', 0)
    errors = stats.get('errors', 0)
    total_messages = mesh_to_rns + rns_to_mesh

    health_status['checks']['message_flow'] = {
        'mesh_to_rns': mesh_to_rns,
        'rns_to_mesh': rns_to_mesh,
        'total': total_messages,
        'errors': errors,
    }

    # Check error rate
    if total_messages > 0:
        error_rate = errors / total_messages
        health_status['checks']['error_rate'] = error_rate
        if error_rate > 0.1:  # >10% errors
            health_status['warnings'].append(
                f"High error rate: {error_rate:.1%} ({errors}/{total_messages})"
            )
    else:
        health_status['checks']['error_rate'] = 0.0
        if is_running:
            health_status['warnings'].append("No messages bridged yet")

    # Check 7: Uptime
    uptime = status.get('uptime_seconds')
    if uptime:
        health_status['checks']['uptime_seconds'] = uptime
        if uptime < 60:
            health_status['warnings'].append(f"Gateway recently started ({uptime:.0f}s ago)")

    # Check 8: Configuration validity
    try:
        from gateway.config import GatewayConfig
        config = GatewayConfig.load()
        is_valid, validation_errors = config.validate()
        health_status['checks']['config_valid'] = is_valid

        for err in validation_errors:
            if err.severity == "error":
                health_status['errors'].append(f"Config: {err.message}")
            elif err.severity == "warning":
                health_status['warnings'].append(f"Config: {err.message}")
    except Exception as e:
        health_status['checks']['config_valid'] = False
        health_status['warnings'].append(f"Config validation failed: {e}")

    # Determine overall health
    if health_status['errors']:
        health_status['overall'] = 'unhealthy'
    elif health_status['warnings']:
        health_status['overall'] = 'degraded'
    elif is_running and mesh_connected:
        health_status['overall'] = 'healthy'
    else:
        health_status['overall'] = 'unknown'

    # Build summary message
    check_count = len([v for v in health_status['checks'].values() if v is True or v])
    total_checks = len(health_status['checks'])
    error_count = len(health_status['errors'])
    warning_count = len(health_status['warnings'])

    message = f"Health: {health_status['overall'].upper()} ({check_count}/{total_checks} checks pass"
    if error_count:
        message += f", {error_count} errors"
    if warning_count:
        message += f", {warning_count} warnings"
    message += ")"

    return CommandResult(
        success=health_status['overall'] in ('healthy', 'degraded'),
        message=message,
        data=health_status
    )


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
    # Note: Use BaseException to catch pyo3 PanicException (not an Exception subclass)
    # from RNS's cryptography library when cffi backend is missing
    try:
        _, _HAS_RNS = safe_import('RNS')
        if _HAS_RNS:
            checks['rns_package'] = True
        else:
            issues.append("RNS package not installed (pipx install rns)")
    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        issues.append(f"RNS package error: {e}")

    try:
        _, _HAS_LXMF = safe_import('LXMF')
        if _HAS_LXMF:
            checks['lxmf_package'] = True
        else:
            issues.append("LXMF package not installed (pip install lxmf)")
    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        issues.append(f"LXMF package error: {e}")

    try:
        _, _HAS_MESHTASTIC = safe_import('meshtastic')
        if _HAS_MESHTASTIC:
            checks['meshtastic_package'] = True
        else:
            issues.append("meshtastic package not installed (pip install meshtastic)")
    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise
    except BaseException as e:
        issues.append(f"meshtastic package error: {e}")

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
    return _HAS_BRIDGE
