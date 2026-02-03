"""
MQTT to WebSocket Bridge for MeshForge.

Connects the MQTT subscriber to the WebSocket server, enabling web UI
to receive mesh data without the Gateway Bridge running.

Architecture:
    MQTT Subscriber ─┬─> TUI display
                     └─> WebSocket Bridge ──> WebSocket:5001 ──> Web UI

Usage:
    from monitoring.mqtt_subscriber import create_local_subscriber
    from utils.mqtt_websocket_bridge import MQTTWebSocketBridge

    # Create MQTT subscriber
    subscriber = create_local_subscriber()
    subscriber.start()

    # Enable WebSocket broadcast
    bridge = MQTTWebSocketBridge(subscriber)
    bridge.start()

    # ... later ...
    bridge.stop()
"""

import logging
from datetime import datetime
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies and optional deps
MQTTNodelessSubscriber = None
MessageWebSocketServer = None


def _import_dependencies():
    """Lazy import of dependencies."""
    global MQTTNodelessSubscriber, MessageWebSocketServer

    if MQTTNodelessSubscriber is None:
        try:
            from monitoring.mqtt_subscriber import MQTTNodelessSubscriber as _Sub
            MQTTNodelessSubscriber = _Sub
        except ImportError:
            logger.warning("MQTT subscriber not available")

    if MessageWebSocketServer is None:
        try:
            from utils.websocket_server import MessageWebSocketServer as _WS
            MessageWebSocketServer = _WS
        except ImportError:
            logger.warning("WebSocket server not available")


class MQTTWebSocketBridge:
    """
    Bridges MQTT subscriber data to WebSocket for web UI clients.

    This allows the web UI to display mesh data when using MQTT monitoring
    instead of the full Gateway Bridge (which uses TCP:4403).
    """

    def __init__(
        self,
        subscriber: Any,
        websocket_port: int = 5001,
        broadcast_messages: bool = True,
        broadcast_nodes: bool = True,
    ):
        """
        Initialize the MQTT to WebSocket bridge.

        Args:
            subscriber: MQTTNodelessSubscriber instance
            websocket_port: WebSocket server port (default: 5001)
            broadcast_messages: Whether to broadcast text messages
            broadcast_nodes: Whether to broadcast node updates
        """
        _import_dependencies()

        self._subscriber = subscriber
        self._ws_port = websocket_port
        self._ws_server: Optional[Any] = None
        self._running = False

        self._broadcast_messages = broadcast_messages
        self._broadcast_nodes = broadcast_nodes

        # Stats
        self._messages_bridged = 0
        self._nodes_bridged = 0

    def start(self) -> bool:
        """
        Start the WebSocket server and register callbacks.

        Returns:
            True if started successfully, False otherwise
        """
        if self._running:
            logger.warning("Bridge already running")
            return True

        if MessageWebSocketServer is None:
            logger.error("WebSocket server not available - install websockets")
            return False

        try:
            # Create and start WebSocket server
            self._ws_server = MessageWebSocketServer(port=self._ws_port)
            if not self._ws_server.start():
                logger.error("Failed to start WebSocket server")
                return False

            # Register callbacks with MQTT subscriber
            if self._broadcast_messages:
                self._subscriber.register_message_callback(self._on_mqtt_message)
            if self._broadcast_nodes:
                self._subscriber.register_node_callback(self._on_mqtt_node)

            self._running = True
            logger.info(f"MQTT→WebSocket bridge started on ws://0.0.0.0:{self._ws_port}")
            return True

        except Exception as e:
            logger.error(f"Failed to start bridge: {e}")
            return False

    def stop(self):
        """Stop the WebSocket server."""
        if not self._running:
            return

        self._running = False

        if self._ws_server:
            self._ws_server.stop()
            self._ws_server = None

        logger.info("MQTT→WebSocket bridge stopped")

    def _on_mqtt_message(self, message: Any):
        """Handle MQTT text message - broadcast to WebSocket."""
        if not self._running or not self._ws_server:
            return

        try:
            # Format message for WebSocket clients
            ws_message = {
                "type": "mesh_message",
                "source": "mqtt",
                "data": {
                    "from_id": message.from_id,
                    "to_id": message.to_id,
                    "text": message.text,
                    "channel": message.channel,
                    "timestamp": message.timestamp.isoformat(),
                    "snr": message.snr,
                    "rssi": message.rssi,
                    "hop_start": message.hop_start,
                }
            }

            self._ws_server.broadcast(ws_message)
            self._messages_bridged += 1

        except Exception as e:
            logger.debug(f"Message bridge error: {e}")

    def _on_mqtt_node(self, node: Any):
        """Handle MQTT node update - broadcast to WebSocket."""
        if not self._running or not self._ws_server:
            return

        try:
            # Format node update for WebSocket clients
            ws_message = {
                "type": "node_update",
                "source": "mqtt",
                "data": {
                    "node_id": node.node_id,
                    "name": node.long_name or node.short_name or node.node_id,
                    "latitude": node.latitude,
                    "longitude": node.longitude,
                    "altitude": node.altitude,
                    "battery_level": node.battery_level,
                    "snr": node.snr,
                    "rssi": node.rssi,
                    "hardware_model": node.hardware_model,
                    "role": node.role,
                    "is_online": node.is_online(),
                    "last_seen": node.get_age_string(),
                    "via_mqtt": True,
                }
            }

            self._ws_server.broadcast(ws_message)
            self._nodes_bridged += 1

        except Exception as e:
            logger.debug(f"Node bridge error: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get bridge statistics."""
        ws_stats = self._ws_server.stats if self._ws_server else None

        return {
            "running": self._running,
            "messages_bridged": self._messages_bridged,
            "nodes_bridged": self._nodes_bridged,
            "websocket_port": self._ws_port,
            "websocket_clients": ws_stats.connected_clients if ws_stats else 0,
            "websocket_total_connections": ws_stats.total_connections if ws_stats else 0,
        }

    @property
    def is_running(self) -> bool:
        """Check if bridge is running."""
        return self._running

    @property
    def connected_clients(self) -> int:
        """Get number of connected WebSocket clients."""
        if self._ws_server:
            return self._ws_server.stats.connected_clients
        return 0


# Singleton instance
_bridge: Optional[MQTTWebSocketBridge] = None


def get_mqtt_websocket_bridge(
    subscriber: Any,
    port: int = 5001
) -> MQTTWebSocketBridge:
    """Get or create the global MQTT→WebSocket bridge."""
    global _bridge
    if _bridge is None:
        _bridge = MQTTWebSocketBridge(subscriber, websocket_port=port)
    return _bridge


def start_mqtt_websocket_bridge(subscriber: Any, port: int = 5001) -> bool:
    """Start the global MQTT→WebSocket bridge."""
    bridge = get_mqtt_websocket_bridge(subscriber, port)
    return bridge.start()


def stop_mqtt_websocket_bridge():
    """Stop the global MQTT→WebSocket bridge."""
    global _bridge
    if _bridge:
        _bridge.stop()
        _bridge = None


def is_bridge_available() -> bool:
    """Check if MQTT→WebSocket bridge can be started."""
    _import_dependencies()
    return MQTTNodelessSubscriber is not None and MessageWebSocketServer is not None
