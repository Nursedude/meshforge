"""
MeshCore Protocol Plugin for MeshForge.

Thin plugin wrapper that delegates to gateway.meshcore_handler.MeshCoreHandler
for actual connection management, message handling, and node tracking.

The handler owns the async event loop, reconnection logic, health monitoring,
and CanonicalMessage serialization. This plugin adapts that to the PluginManager
interface (activate/deactivate/send_message/get_nodes).

See: https://meshcore.co.uk/
Requires: pip install meshcore (Python 3.10+)

Usage:
    manager = PluginManager()
    manager.register(MeshCorePlugin)
    manager.activate("meshcore")
"""

import logging
import threading
from typing import Dict, Any, List, Optional, Callable

from utils.plugins import (
    ProtocolPlugin,
    PluginMetadata,
    PluginType,
)
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Gateway handler — first-party but may not be importable in all contexts
_MeshCoreHandler, _HAS_HANDLER = safe_import(
    'gateway.meshcore_handler', 'MeshCoreHandler'
)
_detect_devices, _HAS_DETECT = safe_import(
    'gateway.meshcore_handler', 'detect_meshcore_devices'
)
from gateway.config import GatewayConfig
from gateway.config import MeshCoreConfig
_BridgeHealthMonitor, _HAS_HEALTH = safe_import(
    'gateway.bridge_health', 'BridgeHealthMonitor'
)
_UnifiedNodeTracker, _HAS_TRACKER = safe_import(
    'gateway.node_tracker', 'UnifiedNodeTracker'
)


class MeshCorePlugin(ProtocolPlugin):
    """MeshCore protocol support for MeshForge.

    Wraps gateway.meshcore_handler.MeshCoreHandler in the PluginManager
    interface. The handler manages the actual async connection; this plugin
    manages lifecycle (activate/deactivate) and provides a synchronous API.
    """

    def __init__(self):
        self._handler = None
        self._handler_thread = None
        self._stop_event = threading.Event()
        self._message_callbacks: List[Callable] = []

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="meshcore",
            version="0.2.0",
            description="MeshCore protocol support - lightweight mesh with advanced routing",
            author="MeshForge Community",
            plugin_type=PluginType.PROTOCOL,
            dependencies=["meshcore"],
            homepage="https://meshcore.co.uk/",
        )

    def activate(self) -> None:
        """Activate MeshCore protocol support."""
        logger.info("MeshCore plugin activated")
        if not _HAS_HANDLER:
            logger.warning(
                "gateway.meshcore_handler not available — "
                "plugin will operate in limited mode"
            )

    def deactivate(self) -> None:
        """Deactivate MeshCore protocol support."""
        self.disconnect()
        self._message_callbacks.clear()
        logger.info("MeshCore plugin deactivated")

    def get_protocol_name(self) -> str:
        return "MeshCore"

    def connect_device(self, **kwargs) -> bool:
        """Connect to a MeshCore device via the gateway handler.

        Args:
            type: Connection type (serial, tcp, ble, simulation)
            port: Serial port path (default /dev/ttyUSB1)
            host: TCP host
            tcp_port: TCP port (default 4000)
            baud_rate: Serial baud rate (default 115200)
            simulation_mode: Force simulation mode

        Returns:
            True if handler started successfully.
        """
        if not _HAS_HANDLER:
            logger.error("MeshCore handler or config not available")
            return False

        if self._handler and self.is_connected():
            logger.info("Already connected to MeshCore")
            return True

        try:
            # Build gateway config from kwargs
            conn_type = kwargs.get("type", "serial")
            mc_config = MeshCoreConfig(
                enabled=True,
                device_path=kwargs.get("port", "/dev/ttyUSB1"),
                baud_rate=kwargs.get("baud_rate", 115200),
                connection_type=conn_type,
                tcp_host=kwargs.get("host", "localhost"),
                tcp_port=kwargs.get("tcp_port", 4000),
                simulation_mode=(
                    conn_type == "simulation"
                    or kwargs.get("simulation_mode", False)
                ),
            )

            gw_config = GatewayConfig()
            gw_config.meshcore = mc_config

            # Create minimal supporting objects
            stats = {}
            stats_lock = threading.Lock()
            self._stop_event.clear()

            # Build handler with optional dependencies
            handler_kwargs = {
                'config': gw_config,
                'stop_event': self._stop_event,
                'stats': stats,
                'stats_lock': stats_lock,
                'message_queue': None,
                'message_callback': self._dispatch_message,
            }

            # node_tracker and health are optional for standalone plugin use
            if _HAS_TRACKER:
                handler_kwargs['node_tracker'] = _UnifiedNodeTracker()
            else:
                handler_kwargs['node_tracker'] = _StubTracker()

            if _HAS_HEALTH:
                handler_kwargs['health'] = _BridgeHealthMonitor()
            else:
                handler_kwargs['health'] = _StubHealth()

            self._handler = _MeshCoreHandler(**handler_kwargs)

            # Start handler in background thread
            self._handler_thread = threading.Thread(
                target=self._handler.run_loop,
                daemon=True,
                name="meshcore-plugin",
            )
            self._handler_thread.start()

            # Wait briefly for connection
            import time
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if self._handler.is_connected:
                    logger.info("MeshCore plugin connected")
                    return True
                time.sleep(0.1)

            logger.warning("MeshCore connection timeout — handler running in background")
            return True  # Handler may still connect via reconnect

        except Exception as e:
            logger.error(f"MeshCore plugin connect failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MeshCore device."""
        self._stop_event.set()
        if self._handler:
            try:
                self._handler.disconnect()
            except Exception as e:
                logger.debug(f"Error during disconnect: {e}")
            self._handler = None
        if self._handler_thread:
            self._handler_thread.join(timeout=5)
            self._handler_thread = None
        logger.info("MeshCore plugin disconnected")

    def send_message(self, destination: str, message: str) -> bool:
        """Send a message via MeshCore.

        Args:
            destination: Target node ID or "broadcast"
            message: Message text (max ~160 chars for MeshCore)

        Returns:
            True if queued successfully.
        """
        if not self._handler or not self._handler.is_connected:
            logger.error("Not connected to MeshCore device")
            return False

        dest = None if destination == "broadcast" else destination
        return self._handler.send_text(message, destination=dest)

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Get list of visible MeshCore nodes from the node tracker."""
        if not self._handler:
            return []

        tracker = getattr(self._handler, 'node_tracker', None)
        if not tracker:
            return []

        try:
            # Get nodes filtered to meshcore network
            if hasattr(tracker, 'get_nodes_by_network'):
                return [n.to_dict() for n in tracker.get_nodes_by_network('meshcore')]
            elif hasattr(tracker, 'get_all_nodes'):
                return [
                    n.to_dict() for n in tracker.get_all_nodes()
                    if getattr(n, 'network', '') == 'meshcore'
                ]
            return []
        except Exception as e:
            logger.error(f"Error getting nodes: {e}")
            return []

    def register_message_callback(self, callback: Callable) -> None:
        """Register a callback for incoming messages."""
        self._message_callbacks.append(callback)

    def on_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming message from main mesh (cross-protocol bridging)."""
        self._dispatch_message(message)

    def _dispatch_message(self, message) -> None:
        """Dispatch message to all registered callbacks."""
        for callback in self._message_callbacks:
            try:
                callback(message)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics from the handler."""
        if not self._handler:
            return {"connected": False, "node_count": 0}

        return {
            "connected": self._handler.is_connected,
            "node_count": len(self.get_nodes()),
            **getattr(self._handler, 'stats', {}),
        }

    def is_connected(self) -> bool:
        """Check if connected to a device."""
        return bool(self._handler and self._handler.is_connected)

    def get_supported_transports(self) -> List[str]:
        """Return list of supported transports."""
        return ["serial", "tcp", "ble", "simulation"]

    @staticmethod
    def detect_devices() -> List[str]:
        """Scan for MeshCore companion radio serial devices."""
        if _HAS_DETECT:
            return _detect_devices()
        return []

    def test_connection(self) -> bool:
        """Test if MeshCore device is reachable."""
        if not self._handler:
            return False
        return self._handler.test_connection()


class _StubTracker:
    """Minimal stub for UnifiedNodeTracker when not available."""

    def add_node(self, node):
        pass

    def get_all_nodes(self):
        return []

    def get_nodes_by_network(self, network):
        return []


class _StubHealth:
    """Minimal stub for BridgeHealthMonitor when not available."""

    def record_connection_event(self, *args, **kwargs):
        pass

    def record_error(self, *args, **kwargs):
        return "unknown"

    def record_message_sent(self, *args, **kwargs):
        pass

    def record_message_failed(self, *args, **kwargs):
        pass

    def set_subsystem_enabled(self, *args, **kwargs):
        pass

    def get_subsystem_state(self, *args, **kwargs):
        return None
