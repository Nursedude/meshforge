"""
Meshtastic-to-Meshtastic Preset Bridge

Bridges two Meshtastic networks with different LoRa presets,
enabling communication between e.g., LONG_FAST and SHORT_TURBO meshes.

Connection modes:
- TCP (legacy): Persistent TCP connection via meshtastic library.
  Blocks meshtasticd web client (port 4403 single-client limit).
- MQTT (recommended): MQTT subscription for RX, HTTP protobuf for TX.
  Zero interference — web client on :9443 works uninterrupted.

Persistence:
- SQLite-backed message queues survive restarts (via PersistentMessageQueue).

Requirements:
- Two Meshtastic radios (one per preset)
- Two meshtasticd instances on different ports
- MeshForge gateway configured with bridge_mode="mesh_bridge"
- For MQTT mode: mosquitto + meshtasticd mqtt.enabled=true
"""

import json
import re
import threading
import time
import logging
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Callable, Any, List

from .config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
from .message_queue import PersistentMessageQueue, MessagePriority, RetryPolicy
from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Import meshtastic library (optional - graceful fallback)
_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')
_meshtastic_tcp, _HAS_MESHTASTIC_TCP = safe_import('meshtastic.tcp_interface')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')

# Optional MQTT client (for MQTT mode)
_mqtt_mod, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

# Optional protobuf client (for MQTT mode TX)
_get_protobuf_client, _HAS_PROTOBUF_CLIENT = safe_import(
    '.meshtastic_protobuf_client', 'get_protobuf_client', package='gateway',
)
_ProtobufTransportConfig, _HAS_PROTOBUF_CONFIG = safe_import(
    '.meshtastic_protobuf_ops', 'ProtobufTransportConfig', package='gateway',
)


@dataclass
class BridgedMeshMessage:
    """Message being bridged between Meshtastic presets."""
    source_preset: str  # "longfast" or "shortturbo"
    source_id: str      # Node ID (!xxxxxxxx)
    destination_id: Optional[str]
    content: str
    channel: int = 0
    is_broadcast: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Generate key for duplicate detection."""
        content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
        return f"{self.source_id}:{content_hash}"

    def to_payload(self) -> Dict[str, Any]:
        """Serialize to dict for persistent queue storage."""
        return {
            "source_preset": self.source_preset,
            "source_id": self.source_id,
            "destination_id": self.destination_id,
            "content": self.content,
            "channel": self.channel,
            "is_broadcast": self.is_broadcast,
            "timestamp": self.timestamp.isoformat(),
            "from": self.source_id,
            "to": self.destination_id or "broadcast",
            "text": self.content,
            "type": "text",
            "metadata": self.metadata,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> 'BridgedMeshMessage':
        """Deserialize from persistent queue payload."""
        ts = payload.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = datetime.now()
        else:
            ts = datetime.now()

        return cls(
            source_preset=payload.get("source_preset", ""),
            source_id=payload.get("source_id", ""),
            destination_id=payload.get("destination_id"),
            content=payload.get("content", ""),
            channel=payload.get("channel", 0),
            is_broadcast=payload.get("is_broadcast", False),
            timestamp=ts,
            metadata=payload.get("metadata", {}),
        )


class MQTTMeshInterface:
    """
    MQTT-based Meshtastic interface (zero-interference alternative to TCP).

    RX: Subscribes to meshtasticd MQTT topics (JSON mode).
    TX: Sends via HTTP protobuf to /api/v1/toradio.

    Implements sendText() compatible with meshtastic TCPInterface,
    so the bridge can use either connection mode transparently.
    """

    def __init__(self, config: MeshtasticConfig, name: str,
                 message_callback: Callable, stop_event: threading.Event):
        self._config = config
        self._name = name
        self._message_callback = message_callback
        self._stop_event = stop_event
        self._client = None
        self._connected = False
        self._mqtt_lock = threading.Lock()
        self._recent_ids: Dict[str, float] = {}
        self._dedup_window = 60

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Connect to MQTT broker and subscribe to meshtasticd topics."""
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed for MQTT bridge mode")
            return False

        mqtt = _mqtt_mod
        try:
            client_id = f"meshforge-presetbridge-{self._name}-{int(time.time()) % 10000}"
            self._client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            self._client.connect(
                self._config.mqtt_broker,
                self._config.mqtt_port,
                keepalive=60,
            )
            self._client.loop_start()

            # Wait for connection
            for _ in range(50):
                if self._connected:
                    return True
                if self._stop_event.wait(0.1):
                    return False

            if not self._connected:
                logger.warning(f"MQTT connection timed out for {self._name}")
            return self._connected

        except Exception as e:
            logger.error(f"Failed to connect MQTT for {self._name}: {e}")
            return False

    def _on_connect(self, client, userdata, flags, rc):
        """Subscribe to meshtasticd MQTT topics on connect."""
        if rc == 0:
            self._connected = True
            cfg = self._config
            root = "msh"

            # Subscribe to JSON topics
            json_topic = f"{root}/{cfg.mqtt_region}/2/json/{cfg.mqtt_channel}/#"
            client.subscribe(json_topic)
            logger.info(f"[{self._name}] Subscribed to: {json_topic}")

            # Also subscribe to encrypted topic for node discovery
            proto_topic = f"{root}/{cfg.mqtt_region}/2/e/{cfg.mqtt_channel}/#"
            client.subscribe(proto_topic)
        else:
            logger.error(f"[{self._name}] MQTT connect failed (rc={rc})")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        was_connected = self._connected
        self._connected = False
        if was_connected and rc != 0:
            logger.warning(f"[{self._name}] MQTT disconnected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message — parse and forward to bridge."""
        try:
            if "/json/" not in msg.topic:
                return  # Skip protobuf messages for now

            data = json.loads(msg.payload.decode('utf-8', errors='ignore'))
            msg_type = data.get('type', '')
            msg_id = str(data.get('id', ''))

            # Dedup
            if msg_id and self._is_duplicate(msg_id):
                return

            if msg_type != 'text':
                return

            sender = data.get('sender', '')
            to_num = data.get('to', 0)
            payload = data.get('payload', {})
            text = payload.get('text', '') if isinstance(payload, dict) else str(payload)

            if not text:
                return

            to_id = f"!{to_num:08x}" if to_num else None
            is_broadcast = to_num == 0xFFFFFFFF

            # Build a packet dict compatible with _process_receive
            packet = {
                'fromId': sender,
                'toId': to_id or '!ffffffff',
                'channel': data.get('channel', 0),
                'rxSnr': data.get('snr'),
                'rxRssi': data.get('rssi'),
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'payload': text,
                },
            }

            self._message_callback(packet)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"[{self._name}] Failed to parse MQTT JSON: {e}")
        except Exception as e:
            logger.error(f"[{self._name}] Error processing MQTT message: {e}")

    def sendText(self, text: str, destinationId: str = None,
                 channelIndex: int = 0) -> bool:
        """
        Send text message via HTTP protobuf (compatible with TCPInterface API).

        Primary: Stateless direct POST to /api/v1/toradio — NEVER reads
        from /api/v1/fromradio, so the web client at :9443 is never
        starved of delivery ACK packets.

        Fallback: Session-based protobuf client (legacy).
        """
        dest_num = None
        if destinationId:
            dest_num = self._node_id_to_num(destinationId)

        # Primary: stateless direct send — zero fromradio contention
        try:
            from .meshtastic_protobuf_client import send_text_direct
            if send_text_direct(
                text=text,
                host=self._config.host,
                port=self._config.http_port,
                destination=dest_num,
                channel_index=channelIndex,
            ):
                return True
        except Exception as e:
            logger.debug(f"[{self._name}] Stateless TX failed: {e}")

        # Fallback: session-based send (reads fromradio during connect)
        if not _HAS_PROTOBUF_CLIENT or not _HAS_PROTOBUF_CONFIG:
            logger.warning(f"[{self._name}] Protobuf client unavailable for TX")
            return False

        try:
            get_client = _get_protobuf_client
            cfg = _ProtobufTransportConfig(
                host=self._config.host,
                port=self._config.http_port,
            )
            client = get_client(cfg)

            if not client.is_connected:
                if not client.connect():
                    logger.debug(f"[{self._name}] Protobuf client connect failed")
                    return False

            return client.send_text(
                text=text,
                destination=dest_num,
                channel_index=channelIndex,
            )
        except Exception as e:
            logger.error(f"[{self._name}] Session-based TX failed: {e}")
            return False

    @staticmethod
    def _node_id_to_num(node_id: str) -> Optional[int]:
        """Convert Meshtastic node ID string to numeric form."""
        if not node_id:
            return None
        try:
            cleaned = node_id.lstrip('!')
            return int(cleaned, 16)
        except ValueError:
            try:
                return int(node_id)
            except ValueError:
                return None

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if message ID was seen recently."""
        now = time.time()
        with self._mqtt_lock:
            if msg_id in self._recent_ids:
                return True
            self._recent_ids[msg_id] = now
            # Cleanup old entries
            expired = [k for k, v in self._recent_ids.items()
                       if now - v > self._dedup_window]
            for k in expired:
                del self._recent_ids[k]
        return False

    def close(self):
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"[{self._name}] Error disconnecting MQTT: {e}")
        self._connected = False


class MeshtasticPresetBridge:
    """
    Bridges messages between two Meshtastic networks with different presets.

    Supports both TCP (legacy) and MQTT (recommended) connection modes.
    Uses persistent SQLite queues for crash-resilient message delivery.

    Typical use case: LONG_FAST rural mesh <-> SHORT_TURBO local mesh
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.bridge_config = self.config.mesh_bridge

        # State
        self._running = False
        self._stop_event = threading.Event()
        self._primary_connected = False
        self._secondary_connected = False

        # Interfaces (TCPInterface or MQTTMeshInterface)
        self._primary_interface = None
        self._secondary_interface = None

        # Persistent message queues (SQLite-backed)
        queue_dir = get_real_user_home() / ".config" / "meshforge" / "mesh_bridge_queues"
        queue_dir.mkdir(parents=True, exist_ok=True)

        retry_policy = RetryPolicy.for_meshtastic()
        self._primary_to_secondary = PersistentMessageQueue(
            db_path=str(queue_dir / "p2s.db"),
            retry_policy=retry_policy,
        )
        self._secondary_to_primary = PersistentMessageQueue(
            db_path=str(queue_dir / "s2p.db"),
            retry_policy=retry_policy,
        )

        # Register sender callbacks for queue processing
        self._primary_to_secondary.register_sender(
            "secondary", self._send_to_secondary
        )
        self._secondary_to_primary.register_sender(
            "primary", self._send_to_primary
        )

        # Duplicate suppression
        self._seen_messages: Dict[str, datetime] = {}
        self._seen_lock = threading.Lock()

        # Threads
        self._primary_thread = None
        self._secondary_thread = None
        self._bridge_thread = None
        self._cleanup_thread = None

        # Callbacks
        self._message_callbacks: List[Callable] = []
        self._status_callbacks: List[Callable] = []

        # Statistics
        self._stats_lock = threading.Lock()
        self.stats = {
            'messages_primary_to_secondary': 0,
            'messages_secondary_to_primary': 0,
            'duplicates_suppressed': 0,
            'errors': 0,
            'start_time': None,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._primary_connected or self._secondary_connected

    def start(self) -> bool:
        """Start the mesh bridge."""
        if self._running:
            logger.warning("Mesh bridge already running")
            return True

        if not self.bridge_config.enabled:
            logger.warning("Mesh bridge not enabled in config")
            return False

        pri = self.bridge_config.primary
        sec = self.bridge_config.secondary
        pri_mode = "MQTT" if pri.use_mqtt else "TCP"
        sec_mode = "MQTT" if sec.use_mqtt else "TCP"

        logger.info("Starting Meshtastic preset bridge...")
        logger.info(f"  Primary: {pri.preset} @ {pri.host}:{pri.port} ({pri_mode})")
        logger.info(f"  Secondary: {sec.preset} @ {sec.host}:{sec.port} ({sec_mode})")

        self._running = True
        self._stop_event.clear()
        self.stats['start_time'] = datetime.now()

        # Start connection threads
        self._primary_thread = threading.Thread(
            target=self._primary_loop,
            daemon=True,
            name="MeshBridge-Primary"
        )
        self._primary_thread.start()

        self._secondary_thread = threading.Thread(
            target=self._secondary_loop,
            daemon=True,
            name="MeshBridge-Secondary"
        )
        self._secondary_thread.start()

        # Start bridge thread (processes persistent queues)
        self._bridge_thread = threading.Thread(
            target=self._bridge_loop,
            daemon=True,
            name="MeshBridge-Forward"
        )
        self._bridge_thread.start()

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="MeshBridge-Cleanup"
        )
        self._cleanup_thread.start()

        logger.info("Mesh bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the mesh bridge."""
        if not self._running:
            return

        logger.info("Stopping mesh bridge...")
        self._running = False
        self._stop_event.set()

        # Disconnect interfaces
        self._disconnect_primary()
        self._disconnect_secondary()

        # Wait for threads
        for thread in [self._primary_thread, self._secondary_thread,
                       self._bridge_thread, self._cleanup_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop queue processing if running
        try:
            self._primary_to_secondary.stop_processing()
        except Exception as e:
            logger.debug(f"Error stopping p2s queue: {e}")
        try:
            self._secondary_to_primary.stop_processing()
        except Exception as e:
            logger.debug(f"Error stopping s2p queue: {e}")

        logger.info("Mesh bridge stopped")
        self._notify_status("stopped")

    def get_status(self) -> dict:
        """Get current bridge status."""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        pri = self.bridge_config.primary
        sec = self.bridge_config.secondary

        return {
            'running': self._running,
            'enabled': self.bridge_config.enabled,
            'primary': {
                'connected': self._primary_connected,
                'preset': pri.preset,
                'host': pri.host,
                'port': pri.port,
                'mode': 'mqtt' if pri.use_mqtt else 'tcp',
            },
            'secondary': {
                'connected': self._secondary_connected,
                'preset': sec.preset,
                'host': sec.host,
                'port': sec.port,
                'mode': 'mqtt' if sec.use_mqtt else 'tcp',
            },
            'direction': self.bridge_config.direction,
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'queue': {
                'p2s_pending': self._primary_to_secondary.get_queue_depth(),
                's2p_pending': self._secondary_to_primary.get_queue_depth(),
            },
        }

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages."""
        self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes."""
        self._status_callbacks.append(callback)

    # ========================================
    # Connection Loops
    # ========================================

    def _primary_loop(self):
        """Main loop for primary Meshtastic connection."""
        while self._running:
            try:
                if not self._primary_connected:
                    self._connect_primary()

                if self._stop_event.wait(1):
                    break

            except Exception as e:
                logger.error(f"Primary loop error: {e}")
                self._primary_connected = False
                if self._stop_event.wait(5):
                    break

    def _secondary_loop(self):
        """Main loop for secondary Meshtastic connection."""
        while self._running:
            try:
                if not self._secondary_connected:
                    self._connect_secondary()

                if self._stop_event.wait(1):
                    break

            except Exception as e:
                logger.error(f"Secondary loop error: {e}")
                self._secondary_connected = False
                if self._stop_event.wait(5):
                    break

    def _bridge_loop(self):
        """Process persistent queues and forward messages."""
        while self._running:
            try:
                processed = 0

                # Process primary -> secondary queue
                if self.bridge_config.direction in ("bidirectional", "primary_to_secondary"):
                    processed += self._primary_to_secondary.process_once(batch_size=5)

                # Process secondary -> primary queue
                if self.bridge_config.direction in ("bidirectional", "secondary_to_primary"):
                    processed += self._secondary_to_primary.process_once(batch_size=5)

                # If no messages processed, wait a bit
                if processed == 0:
                    if self._stop_event.wait(0.2):
                        break
                else:
                    if self._stop_event.wait(0.05):
                        break

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                if self._stop_event.wait(1):
                    break

    def _cleanup_loop(self):
        """Cleanup stale dedup entries."""
        while self._running:
            try:
                if self._stop_event.wait(10):
                    break

                cutoff = datetime.now() - timedelta(
                    seconds=self.bridge_config.dedup_window_sec
                )

                with self._seen_lock:
                    expired = [k for k, v in self._seen_messages.items() if v < cutoff]
                    for key in expired:
                        del self._seen_messages[key]

            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    # ========================================
    # Connection Management
    # ========================================

    def _connect_primary(self):
        """Connect to primary Meshtastic interface."""
        self._primary_interface, self._primary_connected = self._connect_interface(
            self.bridge_config.primary,
            "primary",
            self._on_primary_receive
        )

    def _connect_secondary(self):
        """Connect to secondary Meshtastic interface."""
        self._secondary_interface, self._secondary_connected = self._connect_interface(
            self.bridge_config.secondary,
            "secondary",
            self._on_secondary_receive
        )

    def _connect_interface(self, config: MeshtasticConfig, name: str,
                           callback: Callable) -> tuple:
        """
        Connect to a Meshtastic interface using TCP or MQTT mode.

        When config.use_mqtt is True, uses MQTTMeshInterface (zero-interference).
        Otherwise falls back to TCP (legacy, blocks web client).
        """
        if config.use_mqtt:
            return self._connect_mqtt(config, name, callback)
        return self._connect_tcp(config, name, callback)

    def _connect_mqtt(self, config: MeshtasticConfig, name: str,
                      callback: Callable) -> tuple:
        """Connect via MQTT (zero-interference mode)."""
        try:
            logger.info(
                f"Connecting to {name} via MQTT "
                f"({config.mqtt_broker}:{config.mqtt_port})"
            )

            interface = MQTTMeshInterface(
                config=config,
                name=name,
                message_callback=callback,
                stop_event=self._stop_event,
            )

            if interface.connect():
                logger.info(f"Connected to {name} via MQTT ({config.preset})")
                self._notify_status(f"{name}_connected")
                return interface, True

            logger.warning(f"Failed to connect {name} via MQTT")
            return None, False

        except Exception as e:
            logger.error(f"Failed to connect {name} via MQTT: {e}")
            return None, False

    def _connect_tcp(self, config: MeshtasticConfig, name: str,
                     callback: Callable) -> tuple:
        """Connect via TCP (legacy mode — contends with web client at :9443).

        WARNING: Direct TCPInterface creation competes for meshtasticd's single
        TCP slot (Issue #17). Prefer MQTT mode or HTTP send_text_direct() for TX.
        This method acquires the global connection lock to prevent thrashing.
        """
        if not _HAS_MESHTASTIC or not _HAS_MESHTASTIC_TCP or not _HAS_PUBSUB:
            logger.error("Meshtastic library not installed")
            return None, False

        try:
            logger.warning(
                f"TCP legacy mode for {name} — this contends with :9443 web client. "
                "Consider switching to MQTT mode (see mesh_bridge config)."
            )

            from utils.meshtastic_connection import (
                MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
            )

            # Acquire global lock to prevent connection thrashing
            if not MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10):
                logger.error("TCP connection lock busy — another component owns it")
                return None, False

            try:
                wait_for_cooldown()
                interface = _meshtastic_tcp.TCPInterface(hostname=config.host)

                def on_receive(packet, interface):
                    callback(packet)

                _pub.subscribe(on_receive, "meshtastic.receive")

                logger.info(f"Connected to {name} via TCP ({config.preset})")
                self._notify_status(f"{name}_connected")
                # Note: lock stays held — released on disconnect
                return interface, True
            except Exception:
                MESHTASTIC_CONNECTION_LOCK.release()
                raise

        except Exception as e:
            logger.error(f"Failed to connect to {name} via TCP: {e}")
            return None, False

    def _disconnect_primary(self):
        """Disconnect primary interface."""
        if self._primary_interface:
            try:
                self._primary_interface.close()
            except Exception as e:
                logger.debug(f"Error closing primary: {e}")
            self._primary_interface = None
        self._primary_connected = False

    def _disconnect_secondary(self):
        """Disconnect secondary interface."""
        if self._secondary_interface:
            try:
                self._secondary_interface.close()
            except Exception as e:
                logger.debug(f"Error closing secondary: {e}")
            self._secondary_interface = None
        self._secondary_connected = False

    # ========================================
    # Message Processing
    # ========================================

    def _on_primary_receive(self, packet: dict):
        """Handle message from primary interface."""
        self._process_receive(packet, "primary", "secondary",
                              self._primary_to_secondary)

    def _on_secondary_receive(self, packet: dict):
        """Handle message from secondary interface."""
        self._process_receive(packet, "secondary", "primary",
                              self._secondary_to_primary)

    def _process_receive(self, packet: dict, source: str, dest: str,
                         queue: PersistentMessageQueue):
        """Process received message and enqueue for forwarding."""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Only bridge text messages
            if portnum != 'TEXT_MESSAGE_APP':
                return

            from_id = packet.get('fromId', '')
            to_id = packet.get('toId', '')

            payload = decoded.get('payload', b'')
            if isinstance(payload, bytes):
                text = payload.decode('utf-8', errors='ignore')
            else:
                text = str(payload)

            # Skip if message matches exclude filter
            if self.bridge_config.exclude_filter:
                try:
                    if re.search(self.bridge_config.exclude_filter, text):
                        return
                except re.error:
                    pass

            # Skip if message filter set and doesn't match
            if self.bridge_config.message_filter:
                try:
                    if not re.search(self.bridge_config.message_filter, text):
                        return
                except re.error:
                    pass

            preset = (self.bridge_config.primary.preset if source == "primary"
                      else self.bridge_config.secondary.preset)

            msg = BridgedMeshMessage(
                source_preset=preset,
                source_id=from_id,
                destination_id=to_id,
                content=text,
                channel=packet.get('channel', 0),
                is_broadcast=to_id == '!ffffffff',
                metadata={
                    'snr': packet.get('rxSnr'),
                    'rssi': packet.get('rxRssi'),
                    'source_interface': source,
                }
            )

            # Check for duplicate
            if self._is_duplicate(msg):
                with self._stats_lock:
                    self.stats['duplicates_suppressed'] += 1
                return

            # Enqueue in persistent queue
            priority = (MessagePriority.HIGH if not msg.is_broadcast
                        else MessagePriority.NORMAL)
            msg_id = queue.enqueue(
                payload=msg.to_payload(),
                destination=dest,
                priority=priority,
            )

            if msg_id is None:
                logger.debug(f"Message from {source} deduplicated by queue")
                return

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing message from {source}: {e}")

    def _is_duplicate(self, msg: BridgedMeshMessage) -> bool:
        """Check if message was recently seen (loop prevention)."""
        key = msg.dedup_key

        with self._seen_lock:
            if key in self._seen_messages:
                return True

            self._seen_messages[key] = datetime.now()
            return False

    # ========================================
    # Queue Send Callbacks
    # ========================================

    def _send_to_secondary(self, payload: Dict) -> bool:
        """Send callback for persistent queue — forward to secondary."""
        msg = BridgedMeshMessage.from_payload(payload)
        success = self._forward_message(
            msg, self._secondary_interface,
            self._secondary_connected, "secondary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_primary_to_secondary'] += 1
        return success

    def _send_to_primary(self, payload: Dict) -> bool:
        """Send callback for persistent queue — forward to primary."""
        msg = BridgedMeshMessage.from_payload(payload)
        success = self._forward_message(
            msg, self._primary_interface,
            self._primary_connected, "primary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_secondary_to_primary'] += 1
        return success

    def _forward_message(self, msg: BridgedMeshMessage, interface,
                         connected: bool, dest_name: str) -> bool:
        """Forward a message to the specified interface."""
        if not connected or not interface:
            logger.warning(f"Cannot forward to {dest_name}: not connected")
            return False

        try:
            # Format message with prefix if enabled
            content = msg.content
            if self.bridge_config.add_prefix:
                prefix = self.bridge_config.prefix_format.format(
                    source_preset=msg.source_preset,
                    source_id=msg.source_id[-4:] if msg.source_id else "????",
                )
                content = prefix + content

            # Send to interface (works for both TCP and MQTT interfaces)
            interface.sendText(
                content,
                destinationId=msg.destination_id if not msg.is_broadcast else None,
                channelIndex=msg.channel
            )

            logger.info(f"Bridged {msg.source_preset} -> {dest_name}: {content[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to forward to {dest_name}: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

    # ========================================
    # Callbacks
    # ========================================

    def _notify_message(self, msg: BridgedMeshMessage):
        """Notify message callbacks."""
        for callback in list(self._message_callbacks):
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks."""
        status_data = self.get_status()
        for callback in list(self._status_callbacks):
            try:
                callback(status, status_data)
            except Exception as e:
                logger.error(f"Status callback error: {e}")


def create_mesh_bridge(config: Optional[GatewayConfig] = None) -> MeshtasticPresetBridge:
    """
    Create a Meshtastic preset bridge instance.

    Args:
        config: Optional gateway config, loads from file if not provided

    Returns:
        Configured bridge instance (not started)
    """
    return MeshtasticPresetBridge(config)
