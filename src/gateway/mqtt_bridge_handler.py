"""
MQTT Bridge Handler for RNS Gateway.

Replaces TCP-based MeshtasticHandler with zero-interference approach.

RX: Receives mesh traffic via MQTT subscription (no TCP connection needed).
TX: Sends to mesh via HTTP protobuf (/api/v1/toradio), CLI as fallback.

Architecture:
    RX: Meshtastic mesh -> meshtasticd -> MQTT broker -> MQTTBridgeHandler
    TX: MQTTBridgeHandler -> HTTP protobuf -> meshtasticd -> Meshtastic mesh
        (fallback: CLI subprocess -> meshtasticd TCP -> Meshtastic mesh)

Zero interference:
    - RX via MQTT: no TCP connection to meshtasticd
    - TX via HTTP protobuf: uses /api/v1/toradio (same as web client)
    - Web client on :9443 works uninterrupted
    - Multiple monitoring tools can coexist

Requires:
    - mosquitto (or any MQTT broker) running locally
    - meshtasticd configured with mqtt.enabled=true, mqtt.json_enabled=true
    - paho-mqtt (pip install paho-mqtt)
    - meshtastic Python package (for protobuf TX; CLI used as fallback)

Usage:
    handler = MQTTBridgeHandler(config, node_tracker, health, ...)
    handler.run_loop()  # Blocks, runs in thread
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from queue import Full
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Optional MQTT client
_mqtt_mod, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

# Optional protobuf client
_get_protobuf_client, _HAS_PROTOBUF_CLIENT = safe_import(
    '.meshtastic_protobuf_client', 'get_protobuf_client', package='gateway',
)

# Sudo-safe home directory — first-party, always available (MF001)
from utils.paths import get_real_user_home as _get_real_user_home_fn
from utils.service_check import check_service as _check_service

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker


class MQTTBridgeHandler:
    """
    MQTT-based Meshtastic handler for the gateway bridge.

    Subscribes to meshtasticd's MQTT topics to receive mesh traffic.
    Uses meshtastic CLI for sending messages (transient, no interference).

    This replaces the TCP-based MeshtasticHandler that held a persistent
    connection to port 4403, blocking the web client.

    Args:
        config: Gateway configuration object
        node_tracker: Unified node tracker instance
        health: Bridge health monitor instance
        stop_event: Threading event for graceful shutdown
        stats: Shared statistics dictionary
        stats_lock: Lock for thread-safe stats updates
        message_queue: Queue for messages to be bridged to RNS
        message_callback: Callback for received messages
        status_callback: Callback for status changes
        should_bridge: Callback to check routing rules
    """

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._mesh_to_rns_queue = message_queue

        # Callbacks
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge

        # MQTT client
        self._client = None
        self._connected = False
        self._mqtt_lock = threading.Lock()

        # Meshtastic CLI path (cached)
        self._cli_path: Optional[str] = None

        # Deduplication: track recent message IDs to avoid loops
        self._recent_ids: Dict[str, float] = {}
        self._dedup_window = 60  # seconds

    @property
    def is_connected(self) -> bool:
        """Check if connected to MQTT broker."""
        return self._connected

    def run_loop(self) -> None:
        """
        Main loop: connect to MQTT and process messages.

        Blocks until stop_event is set. Handles reconnection automatically.
        """
        while not self._stop_event.is_set():
            try:
                if not self._connected:
                    logger.info("Connecting to MQTT broker for gateway bridge...")
                    self._connect()

                    if self._connected:
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("MQTT bridge handler connected")
                        self._notify_status("meshtastic_connected")
                    else:
                        self.health.record_connection_event("meshtastic", "retry")
                        self._stop_event.wait(5)
                        continue

                # MQTT client has its own event loop via loop_start()
                # We just need to stay alive and do periodic maintenance
                self._cleanup_dedup()
                self._stop_event.wait(1)

            except Exception as e:
                self.health.record_error("meshtastic", e)
                logger.error(f"MQTT bridge loop error: {e}")
                self._connected = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._stop_event.wait(5)

    def _connect(self) -> bool:
        """Connect to MQTT broker and subscribe to meshtasticd topics."""
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed. Install with: pip install paho-mqtt")
            return False

        # Pre-flight: verify MQTT broker is running
        mqtt_cfg = self.config.mqtt_bridge
        if mqtt_cfg.broker in ('localhost', '127.0.0.1', '::1'):
            broker_status = _check_service('mosquitto')
            if not broker_status.available:
                logger.warning("mosquitto service check: %s (attempting connection anyway)",
                               broker_status.message)
                if broker_status.fix_hint:
                    logger.info("Fix: %s", broker_status.fix_hint)
                # Continue — mosquitto may be running outside systemd

        mqtt = _mqtt_mod

        try:
            # Create MQTT client
            client_id = f"meshforge-gateway-{int(time.time()) % 10000}"
            self._client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )

            # Auth if configured
            if mqtt_cfg.username:
                self._client.username_pw_set(mqtt_cfg.username, mqtt_cfg.password)

            # TLS if configured
            if mqtt_cfg.use_tls:
                self._client.tls_set()

            # Callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Connect
            self._client.connect(
                mqtt_cfg.broker,
                mqtt_cfg.port,
                keepalive=60,
            )

            # Start background thread for MQTT event loop
            self._client.loop_start()

            # Wait briefly for connection
            for _ in range(50):
                if self._connected:
                    return True
                if self._stop_event.wait(0.1):
                    return False

            if not self._connected:
                logger.warning("MQTT connection timed out")
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._connected = False
            return False

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connect callback - subscribe to meshtasticd topics."""
        if rc == 0:
            self._connected = True
            mqtt_cfg = self.config.mqtt_bridge

            # Subscribe to JSON topics (human-readable, recommended)
            # Topic format: msh/{REGION}/2/json/{CHANNEL}/{NODE_ID}
            if mqtt_cfg.json_enabled:
                json_topic = f"{mqtt_cfg.root_topic}/{mqtt_cfg.region}/2/json/{mqtt_cfg.channel}/#"
                client.subscribe(json_topic)
                logger.info(f"Subscribed to JSON topic: {json_topic}")

            # Also subscribe to protobuf topics for completeness
            # Topic format: msh/{REGION}/2/e/{CHANNEL}/{NODE_ID}
            proto_topic = f"{mqtt_cfg.root_topic}/{mqtt_cfg.region}/2/e/{mqtt_cfg.channel}/#"
            client.subscribe(proto_topic)
            logger.info(f"Subscribed to protobuf topic: {proto_topic}")

            logger.info(f"MQTT bridge connected to {mqtt_cfg.broker}:{mqtt_cfg.port}")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnect callback."""
        was_connected = self._connected
        self._connected = False
        if was_connected:
            if rc == 0:
                logger.info("MQTT bridge disconnected cleanly")
            else:
                logger.warning(f"MQTT bridge disconnected unexpectedly (rc={rc})")
                self.health.record_connection_event("meshtastic", "disconnected", f"rc={rc}")
            self._notify_status("meshtastic_disconnected")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message from meshtasticd."""
        try:
            topic = msg.topic
            payload = msg.payload

            # Determine if JSON or protobuf based on topic
            if "/json/" in topic:
                self._handle_json_message(topic, payload)
            else:
                # Protobuf messages need decoding - skip for now,
                # JSON mode is the recommended path
                self._handle_protobuf_message(topic, payload)

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """
        Handle JSON-encoded message from meshtasticd MQTT.

        JSON messages have this structure:
        {
            "channel": 0,
            "from": 1234567890,
            "id": 12345678,
            "payload": {"text": "Hello"},
            "sender": "!abcd1234",
            "timestamp": 1234567890,
            "to": 4294967295,
            "type": "text"
        }
        """
        try:
            data = json.loads(payload.decode('utf-8', errors='ignore'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to parse MQTT JSON: {e}")
            return

        msg_type = data.get('type', '')
        sender = data.get('sender', '')
        msg_id = str(data.get('id', ''))

        # Dedup check
        if msg_id and self._is_duplicate(msg_id):
            return

        # Update node tracking
        from_num = data.get('from', 0)
        if from_num:
            self._update_node_from_mqtt(data)

        # Handle text messages for bridging
        if msg_type == 'text':
            self._bridge_text_message(data, topic)

        # Handle telemetry for node tracking
        elif msg_type == 'telemetry':
            self._update_telemetry(data)

        # Handle position for maps
        elif msg_type == 'position':
            self._update_position(data)

        # Handle nodeinfo for discovery
        elif msg_type == 'nodeinfo':
            self._update_nodeinfo(data)

    def _handle_protobuf_message(self, topic: str, payload: bytes) -> None:
        """
        Handle protobuf-encoded ServiceEnvelope from meshtasticd MQTT.

        For now, we prefer JSON mode. Protobuf is more complex and requires
        the meshtastic protobuf definitions. This is a placeholder for
        future enhancement.
        """
        # Log that we received a protobuf message but prefer JSON
        logger.debug(f"Protobuf message on {topic} ({len(payload)} bytes) - "
                     "use json_enabled=true for full parsing")

    def _bridge_text_message(self, data: dict, topic: str) -> None:
        """Bridge a text message from Meshtastic to RNS."""
        from .rns_bridge import BridgedMessage
        from .bridge_health import MessageOrigin

        sender = data.get('sender', '')
        to_num = data.get('to', 0)
        payload = data.get('payload', {})
        text = payload.get('text', '') if isinstance(payload, dict) else str(payload)
        channel = data.get('channel', 0)

        if not text:
            return

        # Determine destination
        to_id = f"!{to_num:08x}" if to_num else None
        is_broadcast = to_num == 0xFFFFFFFF

        msg = BridgedMessage(
            source_network="meshtastic",
            source_id=sender,
            destination_id=to_id,
            content=text,
            is_broadcast=is_broadcast,
            origin=MessageOrigin.MQTT,
            via_internet=False,  # Local MQTT, not internet relay
            metadata={
                'channel': channel,
                'mqtt_topic': topic,
                'msg_id': data.get('id'),
                'timestamp': data.get('timestamp'),
            },
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            dest = None if is_broadcast else to_id
            messaging.store_incoming(
                from_id=sender,
                content=text,
                network="meshtastic",
                to_id=dest,
                channel=channel,
            )
        except Exception as e:
            logger.debug(f"Could not store incoming message: {e}")

        # Queue for bridging if routing rules allow
        if self._mesh_to_rns_queue is not None:
            if self._should_bridge and not self._should_bridge(msg):
                logger.debug(f"Message from {sender} blocked by routing rules")
            else:
                try:
                    self._mesh_to_rns_queue.put_nowait(msg)
                except Full:
                    logger.warning("Mesh->RNS queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

        # Notify callback
        if self._message_callback:
            try:
                self._message_callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

        # Emit to event bus for TUI live feed (Issue #17 Phase 3)
        try:
            from utils.event_bus import emit_message
            emit_message(
                direction='rx',
                content=text,
                node_id=sender,
                channel=channel,
                network='meshtastic',
                raw_data={
                    'to_id': to_id,
                    'is_broadcast': is_broadcast,
                    'mqtt_topic': topic,
                    'msg_id': data.get('id'),
                    'timestamp': data.get('timestamp'),
                }
            )
        except Exception as e:
            logger.debug(f"Event bus emit failed: {e}")

    def _update_node_from_mqtt(self, data: dict) -> None:
        """Update node tracker from MQTT message data."""
        try:
            from .node_tracker import UnifiedNode

            from_num = data.get('from', 0)
            sender = data.get('sender', f"!{from_num:08x}")

            node = UnifiedNode(
                id=sender,
                name=sender,
                network="meshtastic",
                meshtastic_id=sender,
            )
            self.node_tracker.add_node(node)
        except Exception as e:
            logger.debug(f"Error updating node from MQTT: {e}")

    def _update_telemetry(self, data: dict) -> None:
        """Update node with telemetry data from MQTT."""
        try:
            sender = data.get('sender', '')
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not sender:
                return

            # Device metrics
            device = payload.get('device_metrics', {})
            if device:
                logger.debug(f"Telemetry from {sender}: "
                            f"battery={device.get('battery_level')}%, "
                            f"chUtil={device.get('channel_utilization')}%")

            # Environment metrics
            env = payload.get('environment_metrics', {})
            if env:
                logger.debug(f"Environment from {sender}: "
                            f"temp={env.get('temperature')}C, "
                            f"humidity={env.get('relative_humidity')}%")
        except Exception as e:
            logger.debug(f"Error processing telemetry: {e}")

    def _update_position(self, data: dict) -> None:
        """Update node position from MQTT for maps."""
        try:
            sender = data.get('sender', '')
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not sender:
                return

            lat = payload.get('latitude_i', 0) / 1e7 if payload.get('latitude_i') else None
            lon = payload.get('longitude_i', 0) / 1e7 if payload.get('longitude_i') else None
            alt = payload.get('altitude')

            if lat and lon:
                logger.debug(f"Position from {sender}: {lat:.6f}, {lon:.6f}")
                # Node tracker update with position would go here
        except Exception as e:
            logger.debug(f"Error processing position: {e}")

    def _update_nodeinfo(self, data: dict) -> None:
        """Update node info from MQTT."""
        try:
            from .node_tracker import UnifiedNode

            sender = data.get('sender', '')
            payload = data.get('payload', {})
            if not isinstance(payload, dict) or not sender:
                return

            long_name = payload.get('longname', '')
            short_name = payload.get('shortname', '')
            hw_model = payload.get('hardware', '')

            node = UnifiedNode(
                id=sender,
                name=long_name or short_name or sender,
                network="meshtastic",
                meshtastic_id=sender,
            )
            self.node_tracker.add_node(node)
            logger.debug(f"NodeInfo from {sender}: {long_name} ({short_name})")
        except Exception as e:
            logger.debug(f"Error processing nodeinfo: {e}")

    def send_text(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """
        Send a text message to Meshtastic network.

        Primary: HTTP protobuf via /api/v1/toradio (no TCP, no subprocess).
        Fallback: meshtastic CLI (transient subprocess).

        Args:
            message: Text content to send
            destination: Destination node ID (None for broadcast)
            channel: Channel index to send on

        Returns:
            True if message sent successfully, False otherwise.
        """
        # Try HTTP protobuf first (preferred — no TCP contention, no subprocess)
        if self._send_via_http_protobuf(message, destination, channel):
            return True

        # Fall back to CLI
        logger.debug("HTTP protobuf TX unavailable, falling back to CLI")
        return self._send_via_cli(message, destination, channel)

    def _send_via_http_protobuf(
        self, message: str, destination: str = None, channel: int = 0
    ) -> bool:
        """Send text via HTTP protobuf transport (preferred TX path).

        Primary: Stateless direct POST to /api/v1/toradio — NEVER reads
        from /api/v1/fromradio, so the web client at :9443 is never
        starved of delivery ACK packets.

        Fallback: Session-based protobuf client (legacy, only if direct
        send fails).
        """
        # Convert hex node ID string to int (e.g. "!aabbccdd" -> 0xaabbccdd)
        dest_num = None
        if destination:
            dest_num = self._node_id_to_num(destination)

        # Primary: stateless direct send — zero fromradio contention
        try:
            from .meshtastic_protobuf_client import send_text_direct
            host = self.config.meshtastic.host
            http_port = getattr(self.config.meshtastic, 'http_port', 9443) or 9443
            if send_text_direct(text=message, host=host, port=http_port,
                                destination=dest_num, channel_index=channel):
                return True
        except Exception as e:
            logger.debug(f"Stateless HTTP protobuf TX failed: {e}")

        # Fallback: session-based send (reads fromradio during connect)
        if not _HAS_PROTOBUF_CLIENT:
            return False

        get_protobuf_client = _get_protobuf_client

        try:
            client = get_protobuf_client()

            if not client.is_connected:
                if not client.connect():
                    logger.debug("Protobuf client failed to connect for TX")
                    return False

            return client.send_text(
                text=message,
                destination=dest_num,
                channel_index=channel,
            )
        except Exception as e:
            logger.debug(f"Session-based HTTP protobuf TX failed: {e}")
            return False

    @staticmethod
    def _node_id_to_num(node_id: str) -> Optional[int]:
        """Convert a Meshtastic node ID string to numeric form.

        Args:
            node_id: Node ID like "!aabbccdd" or "0xaabbccdd" or decimal string

        Returns:
            Integer node number, or None if unparseable
        """
        if not node_id:
            return None
        try:
            cleaned = node_id.lstrip('!')
            return int(cleaned, 16)
        except ValueError:
            try:
                return int(node_id)
            except ValueError:
                logger.warning(f"Cannot parse node ID: {node_id}")
                return None

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send text via meshtastic CLI (fallback TX path).

        Spawns a transient CLI process that connects via TCP, sends, exits.
        Works but slower and uses the TCP slot briefly.
        """
        cli = self._find_cli()
        if not cli:
            logger.error("meshtastic CLI not found. Install with: pip install meshtastic")
            return False

        try:
            host = self.config.meshtastic.host
            cmd = [cli, '--host', host, '--sendtext', message]

            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Sent to Meshtastic via CLI: {message[:50]}...")
                return True
            else:
                logger.warning(f"CLI send failed (rc={result.returncode}): {result.stderr[:200]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("meshtastic CLI timed out")
            return False
        except FileNotFoundError:
            logger.error(f"meshtastic CLI not found at: {cli}")
            self._cli_path = None  # Reset cache
            return False
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def queue_send(self, payload: Dict) -> bool:
        """
        Send handler for persistent queue - Meshtastic destination.

        Args:
            payload: Dictionary with 'message', 'destination', 'channel' keys

        Returns:
            True if sent successfully, False otherwise.
        """
        message = payload.get('message', '')
        destination = payload.get('destination')
        channel = payload.get('channel', 0)
        return self.send_text(message, destination, channel)

    def test_connection(self) -> bool:
        """Test MQTT broker connectivity."""
        import socket
        mqtt_cfg = self.config.mqtt_bridge
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((mqtt_cfg.broker, mqtt_cfg.port))
            return result == 0
        except (OSError, Exception) as e:
            logger.debug(f"MQTT broker connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting MQTT: {e}")
        self._connected = False

    def _find_cli(self) -> Optional[str]:
        """Find meshtastic CLI binary (cached)."""
        if self._cli_path:
            return self._cli_path

        import shutil
        path = shutil.which('meshtastic')
        if path:
            self._cli_path = path
            return path

        # Check common locations
        for candidate in [
            '/usr/local/bin/meshtastic',
            '/usr/bin/meshtastic',
            str(self._get_user_bin() / 'meshtastic'),
        ]:
            if self._path_exists(candidate):
                self._cli_path = candidate
                return candidate

        return None

    def _get_user_bin(self):
        """Get user's local bin directory."""
        return _get_real_user_home_fn() / '.local' / 'bin'

    @staticmethod
    def _path_exists(path: str) -> bool:
        """Check if a file exists at path."""
        import os
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if message ID was seen recently (dedup)."""
        now = time.time()
        with self._mqtt_lock:
            if msg_id in self._recent_ids:
                return True
            self._recent_ids[msg_id] = now
        return False

    def _cleanup_dedup(self) -> None:
        """Remove expired entries from dedup cache."""
        now = time.time()
        with self._mqtt_lock:
            expired = [
                k for k, v in self._recent_ids.items()
                if now - v > self._dedup_window
            ]
            for k in expired:
                del self._recent_ids[k]

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")
