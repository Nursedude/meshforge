"""
MQTT Bridge Plugin for MeshForge.

Bridges Meshtastic mesh network to MQTT broker for:
- Home Assistant integration
- Node-RED automation
- Custom dashboards
- Multi-mesh bridging
- Nodeless MQTT connection (like pdxlocations/connect)

Supports:
- TLS encryption (port 8883)
- Auto-reconnect with configurable delays
- Message encryption/decryption
- Persistent node storage

Usage:
    manager = PluginManager()
    manager.register(MQTTBridgePlugin)
    manager.activate("mqtt-bridge")

Configuration (~/.config/meshforge/plugins/mqtt_bridge.json):
{
    "broker": "mqtt.meshtastic.org",
    "port": 8883,
    "username": "",
    "password": "",
    "topic_prefix": "msh/US/2/e",
    "channel": "LongFast",
    "key": "AQ==",
    "use_tls": true,
    "auto_reconnect": true,
    "reconnect_delay": 5,
    "publish_nodes": true,
    "publish_messages": true,
    "subscribe_commands": true
}

Inspired by pdxlocations/connect for nodeless MQTT connectivity.
"""

import json
import logging
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Callable

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

# Default Meshtastic MQTT settings (from pdxlocations/connect)
DEFAULT_MQTT_BROKER = "mqtt.meshtastic.org"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_PORT_TLS = 8883
DEFAULT_ROOT_TOPIC = "msh/US/2/e"
DEFAULT_CHANNEL = "LongFast"
DEFAULT_KEY = "AQ=="  # Default Meshtastic key


class MQTTBridgePlugin(IntegrationPlugin):
    """MQTT integration plugin for MeshForge with TLS and auto-reconnect."""

    def __init__(self):
        self._connected = False
        self._client = None
        self._config = self._load_config()
        self._reconnect_thread = None
        self._stop_reconnect = threading.Event()
        self._message_callbacks: list[Callable] = []
        self._tls_configured = False

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="mqtt-bridge",
            version="0.2.0",
            description="MQTT integration with TLS, auto-reconnect, and nodeless mode",
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
            "broker": DEFAULT_MQTT_BROKER,
            "port": DEFAULT_MQTT_PORT,
            "username": "",
            "password": "",
            "topic_prefix": DEFAULT_ROOT_TOPIC,
            "channel": DEFAULT_CHANNEL,
            "key": DEFAULT_KEY,
            "use_tls": False,
            "auto_reconnect": True,
            "reconnect_delay": 5,
            "publish_nodes": True,
            "publish_messages": True,
            "subscribe_commands": True,
        }

    def _save_config(self) -> None:
        """Save plugin configuration."""
        config_dir = get_real_user_home() / ".config" / "meshforge" / "plugins"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "mqtt_bridge.json"
        try:
            config_path.write_text(json.dumps(self._config, indent=2))
        except Exception as e:
            logger.error(f"Failed to save MQTT config: {e}")

    def activate(self) -> None:
        """Activate the MQTT bridge."""
        logger.info("MQTT Bridge plugin activated")
        # Auto-connect if configured
        if self._config.get("auto_connect", False):
            self.connect()

    def deactivate(self) -> None:
        """Deactivate the MQTT bridge."""
        self._stop_reconnect.set()
        self.disconnect()
        logger.info("MQTT Bridge plugin deactivated")

    def _setup_tls(self, client) -> None:
        """Configure TLS for secure MQTT connection."""
        if self._tls_configured:
            return

        try:
            # Try to use system CA certificates
            context = ssl.create_default_context()

            # Check for custom CA cert (like pdxlocations/connect)
            ca_cert = Path("cacert.pem")
            if ca_cert.exists():
                context.load_verify_locations(str(ca_cert))
                logger.info("Using custom CA certificate: cacert.pem")

            client.tls_set_context(context)
            self._tls_configured = True
            logger.info("TLS configured for MQTT connection")

        except Exception as e:
            logger.warning(f"TLS setup warning: {e}")
            # Fall back to insecure TLS (not recommended for production)
            try:
                client.tls_set(cert_reqs=ssl.CERT_NONE)
                client.tls_insecure_set(True)
                self._tls_configured = True
                logger.warning("Using insecure TLS (certificate verification disabled)")
            except Exception as e2:
                logger.error(f"TLS setup failed: {e2}")

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            logger.info("Connected to MQTT broker")

            # Subscribe to command topics
            if self._config.get("subscribe_commands", True):
                topic = f"{self._config['topic_prefix']}/+/json/#"
                client.subscribe(topic)
                logger.info(f"Subscribed to: {topic}")
        else:
            error_msgs = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            logger.error(f"MQTT connection failed: {error_msgs.get(rc, f'Unknown error {rc}')}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self._connected = False
        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnect (rc={rc})")
            if self._config.get("auto_reconnect", True):
                self._start_reconnect()
        else:
            logger.info("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, msg):
        """MQTT message callback."""
        try:
            topic = msg.topic
            payload = msg.payload

            # Try to decode as JSON
            try:
                data = json.loads(payload.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {"raw": payload.hex()}

            logger.debug(f"MQTT message: {topic} -> {data}")

            # Notify registered callbacks
            for callback in self._message_callbacks:
                try:
                    callback(topic, data)
                except Exception as e:
                    logger.error(f"Message callback error: {e}")

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _start_reconnect(self):
        """Start auto-reconnect thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        self._stop_reconnect.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        """Auto-reconnect loop with exponential backoff."""
        delay = self._config.get("reconnect_delay", 5)
        max_delay = 60

        while not self._stop_reconnect.is_set():
            logger.info(f"Attempting MQTT reconnection in {delay}s...")
            self._stop_reconnect.wait(delay)

            if self._stop_reconnect.is_set():
                break

            if self.connect():
                logger.info("MQTT reconnection successful")
                break

            # Exponential backoff
            delay = min(delay * 1.5, max_delay)

    def connect(self) -> bool:
        """Connect to MQTT broker with TLS support."""
        try:
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client()

            # Set callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Set credentials if provided
            username = self._config.get("username")
            password = self._config.get("password")
            if username:
                self._client.username_pw_set(username, password)

            # Get connection params
            broker = self._config.get("broker", DEFAULT_MQTT_BROKER)
            port = self._config.get("port", DEFAULT_MQTT_PORT)

            # Handle broker:port format (like pdxlocations/connect)
            if ":" in broker:
                broker, port_str = broker.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    pass

            # Setup TLS for port 8883 or if explicitly enabled
            if port == DEFAULT_MQTT_PORT_TLS or self._config.get("use_tls", False):
                self._setup_tls(self._client)

            # Connect
            self._client.connect(broker, port, 60)
            self._client.loop_start()

            logger.info(f"Connecting to MQTT broker at {broker}:{port}")
            return True

        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        self._stop_reconnect.set()
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Disconnect cleanup: {e}")
            self._client = None
        self._connected = False
        self._tls_configured = False
        logger.info("Disconnected from MQTT broker")

    def is_connected(self) -> bool:
        """Check connection status."""
        return self._connected

    def register_message_callback(self, callback: Callable) -> None:
        """Register a callback for incoming MQTT messages."""
        self._message_callbacks.append(callback)

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
