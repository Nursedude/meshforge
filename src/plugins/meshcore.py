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
from typing import Dict, Any, List, Optional

from utils.plugins import (
    ProtocolPlugin,
    PluginMetadata,
    PluginType,
)

logger = logging.getLogger(__name__)


class MeshCorePlugin(ProtocolPlugin):
    """MeshCore protocol support for MeshForge."""

    def __init__(self):
        self._connected = False
        self._device = None
        self._nodes: List[Dict[str, Any]] = []

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
        logger.info("MeshCore plugin deactivated")

    def get_protocol_name(self) -> str:
        return "MeshCore"

    def connect_device(self, **kwargs) -> bool:
        """Connect to a MeshCore device.

        Supported connection types:
        - serial: USB serial port (e.g., /dev/ttyUSB0)
        - tcp: TCP connection (e.g., host:port)
        - ble: Bluetooth LE (device name)
        """
        conn_type = kwargs.get("type", "serial")
        port = kwargs.get("port", "/dev/ttyUSB0")

        try:
            # TODO: Implement actual MeshCore connection
            # This is a stub for the plugin architecture

            logger.info(f"Connecting to MeshCore device via {conn_type}: {port}")

            # Placeholder for actual connection
            self._connected = True
            return True

        except Exception as e:
            logger.error(f"MeshCore connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MeshCore device."""
        self._connected = False
        self._device = None
        logger.info("Disconnected from MeshCore device")

    def send_message(self, destination: str, message: str) -> bool:
        """Send a message via MeshCore."""
        if not self._connected:
            logger.error("Not connected to MeshCore device")
            return False

        try:
            # TODO: Implement actual message sending
            logger.info(f"MeshCore: Sending to {destination}: {message}")
            return True
        except Exception as e:
            logger.error(f"MeshCore send failed: {e}")
            return False

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Get list of visible MeshCore nodes."""
        return self._nodes

    def on_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming message from main mesh."""
        # Could be used for cross-protocol bridging
        pass

    def get_supported_transports(self) -> List[str]:
        """Return list of supported transports."""
        return ["serial", "tcp", "ble", "wifi", "udp"]

    def get_max_hops(self) -> int:
        """MeshCore supports up to 64 hops."""
        return 64
