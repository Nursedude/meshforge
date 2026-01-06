"""
MQTT Bridge Plugin for MeshForge.

Bridges Meshtastic mesh network to MQTT broker for:
- Home Assistant integration
- Node-RED automation
- Custom dashboards
- Multi-mesh bridging

Usage:
    manager = PluginManager()
    manager.register(MQTTBridgePlugin)
    manager.activate("mqtt-bridge")

Configuration (~/.config/meshforge/plugins/mqtt_bridge.json):
{
    "broker": "localhost",
    "port": 1883,
    "username": "",
    "password": "",
    "topic_prefix": "meshtastic",
    "publish_nodes": true,
    "publish_messages": true,
    "subscribe_commands": true
}
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

from utils.plugins import (
    IntegrationPlugin,
    PluginMetadata,
    PluginType,
)

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

logger = logging.getLogger(__name__)


class MQTTBridgePlugin(IntegrationPlugin):
    """MQTT integration plugin for MeshForge."""

    def __init__(self):
        self._connected = False
        self._client = None
        self._config = self._load_config()

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="mqtt-bridge",
            version="0.1.0",
            description="MQTT integration for Home Assistant and Node-RED",
            author="MeshForge Community",
            plugin_type=PluginType.INTEGRATION,
            dependencies=["paho-mqtt"],
            homepage="https://github.com/Nursedude/meshforge",
        )

    def _load_config(self) -> Dict[str, Any]:
        """Load plugin configuration."""
        config_path = get_real_user_home() / ".config" / "meshforge" / "plugins" / "mqtt_bridge.json"
        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception as e:
                logger.error(f"Failed to load MQTT config: {e}")

        return {
            "broker": "localhost",
            "port": 1883,
            "username": "",
            "password": "",
            "topic_prefix": "meshtastic",
            "publish_nodes": True,
            "publish_messages": True,
            "subscribe_commands": True,
        }

    def activate(self) -> None:
        """Activate the MQTT bridge."""
        logger.info("MQTT Bridge plugin activated")
        # Auto-connect if configured
        if self._config.get("auto_connect", False):
            self.connect()

    def deactivate(self) -> None:
        """Deactivate the MQTT bridge."""
        self.disconnect()
        logger.info("MQTT Bridge plugin deactivated")

    def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client()

            # Set credentials if provided
            username = self._config.get("username")
            password = self._config.get("password")
            if username:
                self._client.username_pw_set(username, password)

            # Connect
            broker = self._config.get("broker", "localhost")
            port = self._config.get("port", 1883)

            self._client.connect(broker, port, 60)
            self._client.loop_start()
            self._connected = True

            logger.info(f"Connected to MQTT broker at {broker}:{port}")
            return True

        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        logger.info("Disconnected from MQTT broker")

    def is_connected(self) -> bool:
        """Check connection status."""
        return self._connected

    def send(self, data: Dict[str, Any]) -> bool:
        """Publish data to MQTT."""
        if not self._connected or not self._client:
            return False

        try:
            topic = data.get("topic", f"{self._config['topic_prefix']}/message")
            payload = json.dumps(data.get("payload", data))
            self._client.publish(topic, payload)
            return True
        except Exception as e:
            logger.error(f"MQTT publish failed: {e}")
            return False

    def on_message(self, message: Dict[str, Any]) -> None:
        """Handle mesh message - publish to MQTT."""
        if not self._config.get("publish_messages", True):
            return

        if self._connected:
            self.send({
                "topic": f"{self._config['topic_prefix']}/messages",
                "payload": message,
            })

    def on_node_update(self, node: Dict[str, Any]) -> None:
        """Handle node update - publish to MQTT."""
        if not self._config.get("publish_nodes", True):
            return

        if self._connected:
            node_id = node.get("id", "unknown")
            self.send({
                "topic": f"{self._config['topic_prefix']}/nodes/{node_id}",
                "payload": node,
            })
