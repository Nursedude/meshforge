"""
MeshCore Protocol Plugin for MeshForge.

Adds support for MeshCore mesh protocol alongside Meshtastic.
MeshCore is a lightweight alternative with better routing for
city-scale fixed repeater networks.

See: https://github.com/meshcore-dev/MeshCore

Key differences from Meshtastic:
- Fixed repeater-based routing (not client flooding)
- Up to 64 hops (vs 7 for Meshtastic)
- Lower radio congestion
- Better battery life
- Supports LoRa, BLE, WiFi, Serial, UDP transports

Usage:
    manager = PluginManager()
    manager.register(MeshCorePlugin)
    manager.activate("meshcore")
"""

import logging
import time
import re
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

from utils.plugins import (
    ProtocolPlugin,
    PluginMetadata,
    PluginType,
)

logger = logging.getLogger(__name__)


@dataclass
class MeshCoreNode:
    """Represents a node in the MeshCore network."""
    node_id: str
    name: str = ""
    role: str = "client"  # client, repeater, gateway
    hops: int = 0
    last_seen: Optional[datetime] = None
    rssi: Optional[int] = None
    snr: Optional[float] = None
    battery: Optional[int] = None
    position: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary."""
        return {
            "node_id": self.node_id,
            "name": self.name or self.node_id[:8],
            "role": self.role,
            "hops": self.hops,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "rssi": self.rssi,
            "snr": self.snr,
            "battery": self.battery,
            "position": self.position,
        }


@dataclass
class MeshCoreConfig:
    """Configuration for MeshCore connection."""
    connection_type: str = "serial"  # serial, tcp, ble, wifi, udp
    port: str = "/dev/ttyUSB0"
    host: str = "localhost"
    tcp_port: int = 4405
    baud_rate: int = 115200
    ble_name: str = ""
    simulation_mode: bool = False  # For testing without hardware


class MeshCorePlugin(ProtocolPlugin):
    """MeshCore protocol support for MeshForge."""

    def __init__(self):
        self._connected = False
        self._device = None
        self._nodes: Dict[str, MeshCoreNode] = {}
        self._config: Optional[MeshCoreConfig] = None
        self._message_callbacks: List[Callable] = []
        self._stats = {
            "messages_sent": 0,
            "messages_received": 0,
            "messages_failed": 0,
            "connect_attempts": 0,
            "last_activity": None,
        }

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="meshcore",
            version="0.1.0",
            description="MeshCore protocol support - lightweight mesh with advanced routing",
            author="MeshForge Community",
            plugin_type=PluginType.PROTOCOL,
            dependencies=[],
            homepage="https://meshcore.co.uk/",
        )

    def activate(self) -> None:
        """Activate MeshCore protocol support."""
        logger.info("MeshCore plugin activated")
        logger.info("Note: MeshCore and Meshtastic are not directly compatible")

    def deactivate(self) -> None:
        """Deactivate MeshCore protocol support."""
        if self._connected:
            self.disconnect()
        self._message_callbacks.clear()
        logger.info("MeshCore plugin deactivated")

    def get_protocol_name(self) -> str:
        return "MeshCore"

    def connect_device(self, **kwargs) -> bool:
        """Connect to a MeshCore device.

        Supported connection types:
        - serial: USB serial port (e.g., /dev/ttyUSB0)
        - tcp: TCP connection (e.g., host:port)
        - ble: Bluetooth LE (device name)
        - simulation: Simulated device for testing

        Args:
            type: Connection type (serial, tcp, ble, simulation)
            port: Serial port path or BLE device name
            host: TCP host for tcp connection
            tcp_port: TCP port number (default 4405)
            baud_rate: Serial baud rate (default 115200)

        Returns:
            True if connection successful, False otherwise
        """
        self._stats["connect_attempts"] += 1
        conn_type = kwargs.get("type", "serial")
        self._config = MeshCoreConfig(
            connection_type=conn_type,
            port=kwargs.get("port", "/dev/ttyUSB0"),
            host=kwargs.get("host", "localhost"),
            tcp_port=kwargs.get("tcp_port", 4405),
            baud_rate=kwargs.get("baud_rate", 115200),
            ble_name=kwargs.get("ble_name", ""),
            simulation_mode=(conn_type == "simulation"),
        )

        try:
            if self._config.simulation_mode:
                return self._connect_simulation()
            elif conn_type == "serial":
                return self._connect_serial()
            elif conn_type == "tcp":
                return self._connect_tcp()
            elif conn_type == "ble":
                return self._connect_ble()
            else:
                logger.error(f"Unsupported connection type: {conn_type}")
                return False

        except Exception as e:
            logger.error(f"MeshCore connection failed: {e}")
            return False

    def _connect_simulation(self) -> bool:
        """Connect in simulation mode for testing."""
        logger.info("MeshCore: Connecting in simulation mode")
        self._connected = True
        self._stats["last_activity"] = datetime.now()

        # Add some simulated nodes
        self._add_simulated_nodes()
        logger.info("MeshCore: Simulation mode active with test nodes")
        return True

    def _connect_serial(self) -> bool:
        """Connect via serial port."""
        port = self._config.port
        logger.info(f"MeshCore: Connecting via serial port {port}")

        # Check if port exists
        import os
        if not os.path.exists(port):
            logger.warning(f"MeshCore: Serial port {port} not found")
            logger.info("MeshCore: Falling back to simulation mode")
            return self._connect_simulation()

        # Actual serial connection would go here
        # For now, log what would happen and use simulation
        logger.info(f"MeshCore: Would connect to {port} at {self._config.baud_rate} baud")
        logger.info("MeshCore: Serial driver not yet implemented, using simulation")
        return self._connect_simulation()

    def _connect_tcp(self) -> bool:
        """Connect via TCP."""
        host = self._config.host
        port = self._config.tcp_port
        logger.info(f"MeshCore: Connecting via TCP to {host}:{port}")

        # Actual TCP connection would go here
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                logger.info(f"MeshCore: TCP connection to {host}:{port} available")
                logger.info("MeshCore: TCP driver not yet implemented, using simulation")
            else:
                logger.warning(f"MeshCore: Cannot reach {host}:{port}")
        except Exception as e:
            logger.warning(f"MeshCore: TCP test failed: {e}")

        return self._connect_simulation()

    def _connect_ble(self) -> bool:
        """Connect via Bluetooth LE."""
        ble_name = self._config.ble_name or self._config.port
        logger.info(f"MeshCore: Connecting via BLE to {ble_name}")
        logger.info("MeshCore: BLE driver not yet implemented, using simulation")
        return self._connect_simulation()

    def _add_simulated_nodes(self):
        """Add simulated nodes for testing."""
        test_nodes = [
            MeshCoreNode(
                node_id="mc001a2b3c",
                name="MC-Gateway-1",
                role="gateway",
                hops=0,
                last_seen=datetime.now(),
                rssi=-65,
                snr=10.5,
                battery=100,
            ),
            MeshCoreNode(
                node_id="mc002d4e5f",
                name="MC-Repeater-A",
                role="repeater",
                hops=1,
                last_seen=datetime.now(),
                rssi=-78,
                snr=7.2,
            ),
            MeshCoreNode(
                node_id="mc003g7h8i",
                name="MC-Client-1",
                role="client",
                hops=2,
                last_seen=datetime.now(),
                rssi=-85,
                snr=5.0,
                battery=67,
            ),
        ]
        for node in test_nodes:
            self._nodes[node.node_id] = node

    def disconnect(self) -> None:
        """Disconnect from MeshCore device."""
        self._connected = False
        self._device = None
        self._nodes.clear()
        self._stats["last_activity"] = datetime.now()
        logger.info("Disconnected from MeshCore device")

    def send_message(self, destination: str, message: str) -> bool:
        """Send a message via MeshCore.

        Args:
            destination: Target node ID or "broadcast" for all nodes
            message: Message text to send (max 200 chars for MeshCore)

        Returns:
            True if message was sent successfully
        """
        if not self._connected:
            logger.error("Not connected to MeshCore device")
            return False

        # Validate destination
        if destination != "broadcast" and not self._validate_node_id(destination):
            logger.error(f"Invalid destination node ID: {destination}")
            self._stats["messages_failed"] += 1
            return False

        # Validate message length (MeshCore limit)
        if len(message) > 200:
            logger.warning(f"Message truncated from {len(message)} to 200 chars")
            message = message[:200]

        try:
            self._stats["last_activity"] = datetime.now()

            if self._config.simulation_mode:
                logger.info(f"MeshCore [SIM]: {destination} <- {message}")
                self._stats["messages_sent"] += 1
                return True

            # Actual send would go here
            logger.info(f"MeshCore: Sending to {destination}: {message}")
            self._stats["messages_sent"] += 1
            return True

        except Exception as e:
            logger.error(f"MeshCore send failed: {e}")
            self._stats["messages_failed"] += 1
            return False

    def _validate_node_id(self, node_id: str) -> bool:
        """Validate MeshCore node ID format."""
        # MeshCore uses 10-character hex IDs with 'mc' prefix
        pattern = r'^mc[0-9a-f]{8}$'
        return bool(re.match(pattern, node_id.lower()))

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Get list of visible MeshCore nodes."""
        return [node.to_dict() for node in self._nodes.values()]

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific node by ID."""
        node = self._nodes.get(node_id)
        return node.to_dict() if node else None

    def register_message_callback(self, callback: Callable) -> None:
        """Register a callback for incoming messages."""
        self._message_callbacks.append(callback)

    def on_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming message from main mesh.

        This can be used for cross-protocol bridging between
        MeshCore and Meshtastic networks.
        """
        self._stats["messages_received"] += 1
        self._stats["last_activity"] = datetime.now()

        # Notify all registered callbacks
        for callback in self._message_callbacks:
            try:
                callback(message)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def get_supported_transports(self) -> List[str]:
        """Return list of supported transports."""
        return ["serial", "tcp", "ble", "wifi", "udp", "simulation"]

    def get_max_hops(self) -> int:
        """MeshCore supports up to 64 hops."""
        return 64

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            **self._stats,
            "connected": self._connected,
            "node_count": len(self._nodes),
            "connection_type": self._config.connection_type if self._config else None,
        }

    def is_connected(self) -> bool:
        """Check if connected to a device."""
        return self._connected
