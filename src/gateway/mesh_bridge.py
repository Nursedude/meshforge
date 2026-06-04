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

from .base_handler import (
    get_rf_tx_registry, get_secondary_rf_registry, is_already_bridged,
)
from .config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
from .message_queue import PersistentMessageQueue, MessagePriority, RetryPolicy
from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Import meshtastic library (optional - graceful fallback)
_meshtastic, _HAS_MESHTASTIC = safe_import('meshtastic')
_meshtastic_tcp, _HAS_MESHTASTIC_TCP = safe_import('meshtastic.tcp_interface')
_meshtastic_serial, _HAS_MESHTASTIC_SERIAL = safe_import('meshtastic.serial_interface')
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

        # Advisory pre-flight for localhost brokers (Issue #3)
        broker = self._config.mqtt_broker
        if broker in ('localhost', '127.0.0.1', '::1'):
            try:
                from utils.service_check import check_service
                broker_status = check_service('mosquitto')
                if not broker_status.available:
                    logger.warning("mosquitto pre-flight: %s (attempting connection anyway)", broker_status.message)
            except ImportError:
                pass

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

            # Subscribe to BOTH topic shapes — with and without the region
            # segment. meshtasticd 2.7.x publishes region-less topics
            # (msh/2/json/...); older builds include the region
            # (msh/US/2/json/...). Same dual-shape fix as Issue #34 in
            # mqtt_bridge_handler; per-message dedup absorbs any overlap.
            json_topics = [f"{root}/2/json/{cfg.mqtt_channel}/#"]
            proto_topics = [f"{root}/2/e/{cfg.mqtt_channel}/#"]
            if cfg.mqtt_region:
                json_topics.append(
                    f"{root}/{cfg.mqtt_region}/2/json/{cfg.mqtt_channel}/#")
                proto_topics.append(
                    f"{root}/{cfg.mqtt_region}/2/e/{cfg.mqtt_channel}/#")

            for topic in json_topics:
                client.subscribe(topic)
                logger.info(f"[{self._name}] Subscribed to: {topic}")

            # Also subscribe to encrypted topics for node discovery
            for topic in proto_topics:
                client.subscribe(topic)
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
            logger.warning(f"[{self._name}] Failed to parse MQTT JSON: {e}")
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
            logger.warning(f"[{self._name}] Stateless TX failed: {e}")

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
                    logger.warning(f"[{self._name}] Protobuf client connect failed")
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

        # Strong references to serial pubsub listeners. pypubsub holds
        # listeners WEAKLY — a nested on_receive closure with no other
        # reference is garbage-collected right after connect and the
        # callback never fires (serial RX silently dead).
        self._serial_rx_keepalive: Dict[str, Callable] = {}

        # Statistics
        self._stats_lock = threading.Lock()
        self.stats = {
            'messages_primary_to_secondary': 0,
            'messages_secondary_to_primary': 0,
            'duplicates_suppressed': 0,
            'channel_filtered': 0,
            'already_bridged_dropped': 0,
            'downlink_injected': 0,
            # Symmetric dual-path dedup (gated): primary forwards suppressed
            # because the rns_bridge's relay copy already went out on the
            # primary radio (it won the race this time).
            'dual_path_suppressed': 0,
            'errors': 0,
            'start_time': None,
        }

        # Optional true-origin downlink injector for the primary (meshtasticd)
        # leg — makes ST→LF traffic show as the real source node on :9443
        # instead of self-TX. Default off; built only when primary leg is
        # configured injection_mode="downlink" with a PSK. Secondary is a USB
        # serial radio with no meshtasticd, so downlink doesn't apply there.
        self._primary_downlink = self._build_downlink_injector(
            self.bridge_config.primary)
        # Origins we've already taught the primary radio about (NodeInfo
        # downlink) so it renders the friendly name, not bare hex. Injected
        # once per origin before its first text downlink.
        self._nodeinfo_sent = set()

    def _build_downlink_injector(self, leg: MeshtasticConfig):
        """Construct a DownlinkInjector for a leg, or None if not enabled."""
        if (leg.injection_mode or "toradio").lower() != "downlink":
            return None
        if not leg.downlink_psk:
            logger.warning(
                "mesh_bridge %s injection_mode=downlink but no downlink_psk — "
                "falling back to toradio", leg.name)
            return None
        try:
            from .mqtt_downlink_inject import DownlinkInjector
            injector = DownlinkInjector(
                broker=leg.mqtt_broker,
                port=leg.mqtt_port,
                channel_name=leg.mqtt_channel,
                psk_b64=leg.downlink_psk,
                root_topic="msh",
            )
            if not injector.usable:
                logger.warning(
                    "mesh_bridge %s downlink injector unusable: %s — "
                    "falling back to toradio", leg.name, injector.fatal_reason)
                return None
            logger.info(
                "mesh_bridge %s: true-origin downlink injection ENABLED "
                "(channel=%s)", leg.name, leg.mqtt_channel)
            return injector
        except Exception as e:
            logger.warning(
                "mesh_bridge %s downlink injector init failed: %s — "
                "falling back to toradio", leg.name, e)
            return None

    def _lookup_node_user(self, node_id_str: str):
        """Return {'long','short','hw'} for a node id from an interface nodedb.

        Bridged origins are heard on the source (serial) interface, whose
        meshtastic nodedb carries the NodeInfo. The MQTT interface has no
        nodedb, so this safely returns None there.
        """
        if not node_id_str:
            return None
        for iface in (self._secondary_interface, self._primary_interface):
            nodes = getattr(iface, "nodes", None)
            if not nodes:
                continue
            node = nodes.get(node_id_str)
            user = (node or {}).get("user") if isinstance(node, dict) else None
            if user and (user.get("longName") or user.get("shortName")):
                return {
                    "long": user.get("longName") or user.get("shortName") or node_id_str,
                    "short": user.get("shortName") or node_id_str[-4:],
                    "hw": user.get("hwModel"),
                }
        return None

    def _ensure_nodeinfo(self, origin: int, source_id: str) -> None:
        """Teach the primary radio this origin's NAME once (NodeInfo downlink)
        so :9443 shows 'moc2: ...' not '!ddfb8065: ...'. Best-effort: if the
        name isn't known yet we skip (and retry on a later message)."""
        if origin in self._nodeinfo_sent or self._primary_downlink is None:
            return
        user = self._lookup_node_user(source_id)
        if not user:
            return  # name not learned yet; don't mark sent — retry next time
        if self._primary_downlink.inject_nodeinfo(
            origin, user["long"], user["short"], hw_model=user["hw"],
        ):
            self._nodeinfo_sent.add(origin)
            logger.info(
                "Taught primary radio NodeInfo for !%08x (%s)",
                origin, user["long"])

    @staticmethod
    def _node_id_to_num(node_id) -> Optional[int]:
        """'!ddfb8065' / '0xddfb8065' / int → node number, or None."""
        if node_id is None:
            return None
        if isinstance(node_id, int):
            return node_id
        s = str(node_id).strip().lstrip("!")
        if s.lower().startswith("0x"):
            s = s[2:]
        try:
            return int(s, 16)
        except ValueError:
            return None

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
        if self._primary_downlink is not None:
            self._primary_downlink.close()

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
        Connect to a Meshtastic interface using TCP, MQTT, or direct serial.

        Dispatch order:
          1. connection_type="serial"  → SerialInterface to a USB Meshtastic
             device (no meshtasticd involved; used when the secondary radio
             is a USB-attached Heltec/etc. with no dedicated meshtasticd
             instance).
          2. connection_type="mqtt" OR use_mqtt=True → MQTTMeshInterface
             (zero-interference; preferred when meshtasticd is running
             and mosquitto is available).
          3. Fallback → TCPInterface (legacy; contends with :9443).
        """
        ctype = (config.connection_type or "").lower()
        if ctype == "serial":
            return self._connect_serial(config, name, callback)
        if ctype == "mqtt" or config.use_mqtt:
            return self._connect_mqtt(config, name, callback)
        return self._connect_tcp(config, name, callback)

    def _connect_serial(self, config: MeshtasticConfig, name: str,
                        callback: Callable) -> tuple:
        """Connect directly to a USB Meshtastic device via SerialInterface.

        Used for dual-radio gateways where the secondary radio is a USB
        Meshtastic device (e.g. Heltec V3) with no second meshtasticd
        instance. meshtasticd 2.7.x has no USB-relay mode, so this is the
        supported path for that topology.
        """
        if not _HAS_MESHTASTIC or not _HAS_MESHTASTIC_SERIAL or not _HAS_PUBSUB:
            logger.error(
                "meshtastic library or serial_interface not importable; "
                "install with: pip install meshtastic"
            )
            return None, False
        device = config.serial_device or None
        try:
            logger.info(
                f"Connecting to {name} via serial ({device or 'auto-detect'})"
            )
            if device:
                interface = _meshtastic_serial.SerialInterface(devPath=device)
            else:
                interface = _meshtastic_serial.SerialInterface()

            our_interface = interface

            def on_receive(packet, interface):
                # pubsub "meshtastic.receive" is global — filter to packets
                # from THIS serial interface (another meshtastic interface
                # in the same process would otherwise leak in).
                if interface is not our_interface:
                    return
                callback(packet)

            # pypubsub holds listeners weakly — retain a strong reference
            # or the closure is garbage-collected and RX silently dies.
            self._serial_rx_keepalive[name] = on_receive
            _pub.subscribe(on_receive, "meshtastic.receive")
            logger.info(
                f"Connected to {name} via serial "
                f"({config.preset or 'unknown preset'})"
            )
            self._notify_status(f"{name}_connected")
            return interface, True
        except Exception as e:
            logger.error(f"Failed to connect to {name} via serial: {e}")
            return None, False

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
            from utils.service_check import check_service

            # Advisory pre-flight: warn if meshtasticd not detected (Issue #3)
            status = check_service('meshtasticd')
            if not status.available:
                logger.warning("meshtasticd pre-flight: %s (attempting connection anyway)", status.message)

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

            # Channel allow-list: only bridge text on allow-listed channel
            # indexes (empty list = all). Serial RX hears every channel of
            # its radio; without this, a secondary's ch0 text would be
            # re-TXed on the primary's ch0 (often a public channel).
            allowed = self.bridge_config.channels
            channel = packet.get('channel', 0)
            if allowed and channel not in allowed:
                with self._stats_lock:
                    self.stats['channel_filtered'] += 1
                logger.debug(
                    f"Channel filter: dropped ch{channel} text from {source} "
                    f"(allow-list: {allowed})"
                )
                return

            from_id = packet.get('fromId', '')
            to_id = packet.get('toId', '')

            # Broadcast detection must cover every shape meshtastic emits:
            #   - serial/TCP lib maps 0xFFFFFFFF -> '^all' (BROADCAST_ADDR)
            #   - MQTT json path here formats it as '!ffffffff'
            #   - the raw numeric `to` field (0xFFFFFFFF, or absent = 0)
            # Getting this wrong sends broadcasts as DMs and skips the
            # broadcast-only downlink-injection path (moc canary, 2026-06-03).
            to_num = packet.get('to')
            is_broadcast = (
                to_id in ('^all', '!ffffffff')
                or to_num == 0xFFFFFFFF
            )

            payload = decoded.get('payload', b'')
            if isinstance(payload, bytes):
                text = payload.decode('utf-8', errors='ignore')
            else:
                text = str(payload)

            # Seen-on-RF registration (dual-path dedup, cross-BOX direction):
            # a broadcast heard on this leg IS on that radio's mesh, whoever
            # TX'd it — including a peer box's radio on the same RF segment.
            # Scope matters: register into the RECEIVING leg's registry only
            # (a primary-RX entry must never suppress a primary→secondary
            # forward of the same content). Deliberately BEFORE the
            # already-bridged drop below — tagged content is refused for
            # re-bridging but is still on the mesh, and recording it is what
            # lets the inject-side checks kill the other path's copy (the
            # moc3 ×4 bot-reply shape, 2026-06-04). After the channel
            # allow-list, so filtered channels don't poison the registry.
            if is_broadcast and text:
                (get_rf_tx_registry() if source == "primary"
                 else get_secondary_rf_registry()).register(text)

            # ECHO-LOOP INVARIANT (cf. 1a34699): content already carrying a
            # bridge tag has crossed SOME bridge — it never crosses another.
            # Live failure shape (moc, 2026-06-03): ST text -> mesh_bridge ->
            # HAT -> M->R fan-out -> peer gateway (moc3, also on the ST RF
            # segment) re-injected it tagged [RNS:...] -> our serial RX heard
            # it -> re-forwarded with a fresh prefix -> dedup never matched
            # the mutated content -> infinite prefix-growing amplification.
            # Our own forwards carry a [Mesh: prefix (BRIDGE_TAG_PREFIXES),
            # so every gateway — including this one — refuses them on re-RX.
            if is_already_bridged(text):
                with self._stats_lock:
                    self.stats['already_bridged_dropped'] += 1
                logger.debug(
                    f"Already-bridged content dropped from {source}: "
                    f"{text[:50]}..."
                )
                return

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
                is_broadcast=is_broadcast,
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

        # Seen-on-RF dedup, secondary scope (gated, default off): when this
        # content is already on the SECONDARY mesh — e.g. a peer gateway's
        # RNS relay injected it there and our serial RX heard it — suppress
        # the forward instead of double-delivering (the other order of the
        # moc3 ×4 bot-reply pair, 2026-06-04). Checks the secondary registry
        # ONLY: consulting the primary one here would suppress every
        # primary→secondary forward of content just heard on primary.
        if (msg.is_broadcast and self._dual_path_dedup_on()
                and get_secondary_rf_registry().seen_within(
                    msg.content, self._dual_path_dedup_window())):
            with self._stats_lock:
                self.stats['dual_path_suppressed_secondary'] = (
                    self.stats.get('dual_path_suppressed_secondary', 0) + 1)
            logger.info(
                f"mesh_bridge secondary forward suppressed (seen-on-RF "
                f"dedup — already on secondary mesh): {msg.content[:50]}...")
            return True

        success = self._forward_message(
            msg, self._secondary_interface,
            self._secondary_connected, "secondary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_primary_to_secondary'] += 1
            # Mirror of _send_to_primary's registration, secondary scope.
            if msg.is_broadcast:
                get_secondary_rf_registry().register(msg.content)
        return success

    def _dual_path_dedup_on(self) -> bool:
        """Strict read of rns.dual_path_dedup_enabled (default False).

        Same ``is True`` discipline as the rns_bridge gates — MagicMock test
        configs and malformed values read as OFF.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'dual_path_dedup_enabled', False) is True

    def _dual_path_dedup_window(self) -> float:
        rns_cfg = getattr(self.config, 'rns', None)
        try:
            return float(getattr(rns_cfg, 'dual_path_dedup_window_sec', 60))
        except (TypeError, ValueError):
            return 60.0

    def _send_to_primary(self, payload: Dict) -> bool:
        """Send callback for persistent queue — forward to primary."""
        msg = BridgedMeshMessage.from_payload(payload)

        # Symmetric dual-path dedup (gated, default off): when the
        # rns_bridge's relay copy of this same content WON the race and
        # already went out on the primary radio (it registered on TX),
        # suppress our forward instead of double-delivering. The original
        # one-directional check (rns_bridge consults mesh_bridge's
        # registrations) only caught the races mesh_bridge won — live
        # 2026-06-04 the RNS relay beat the serial RX by ~300ms and the
        # duplicate sailed through. Returning True marks the queue item
        # delivered (the content IS on the radio — just via the other path).
        if (msg.is_broadcast and self._dual_path_dedup_on()
                and get_rf_tx_registry().seen_within(
                    msg.content, self._dual_path_dedup_window())):
            with self._stats_lock:
                self.stats['dual_path_suppressed'] += 1
            logger.info(
                f"mesh_bridge forward suppressed (dual-path dedup — already "
                f"on RF via rns_bridge): {msg.content[:50]}...")
            return True

        success = self._forward_message(
            msg, self._primary_interface,
            self._primary_connected, "primary"
        )
        if success:
            with self._stats_lock:
                self.stats['messages_secondary_to_primary'] += 1
            # Dual-path dedup: record what just went onto the primary radio
            # so the rns_bridge's R→M path (the peer-relay copy of this same
            # content, arriving seconds later via RNS) can suppress its
            # duplicate — gated on rns.dual_path_dedup_enabled at the CHECK
            # side; registering is unconditional and cheap. Broadcasts only:
            # DMs never dual-path.
            if msg.is_broadcast:
                get_rf_tx_registry().register(msg.content)
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

            # True-origin downlink injection (primary/meshtasticd leg only).
            # Makes the forward show as the REAL source node on :9443 instead
            # of self-TX. Only for broadcasts (downlink is a channel inject);
            # any failure falls through to the toradio sendText path below so
            # a message is never dropped.
            #
            # The downlink path injects the RAW message (no [Mesh:...] prefix):
            # the true-origin attribution already names the sender, so the tag
            # is just clutter ("moc2: [Mesh:SHORT_TURBO:8065] hi" → "moc2: hi").
            # The prefix exists only as the cross-gateway echo-loop guard for
            # the toradio path (where re-heard json could re-bridge). Downlink
            # is loop-safe by construction: meshtasticd does NOT re-uplink a
            # via_mqtt packet to json and the gateway can't decode the raw -e-
            # protobuf, so the injected packet never re-enters the bridge
            # (proven step-0 + moc canary 2026-06-03). The toradio FALLBACK
            # below still sends the tagged `content`, so loop safety is intact
            # whenever downlink is unavailable.
            if (dest_name == "primary" and self._primary_downlink is not None
                    and msg.is_broadcast):
                origin = self._node_id_to_num(msg.source_id)
                if origin is not None:
                    # Teach the radio the origin's name first, so its text
                    # renders as "moc2: ..." instead of bare hex.
                    self._ensure_nodeinfo(origin, msg.source_id)
                if origin is not None and self._primary_downlink.inject(
                    msg.content, origin, hop_limit=3,
                ):
                    with self._stats_lock:
                        self.stats['downlink_injected'] += 1
                    logger.info(
                        f"Bridged {msg.source_preset} -> {dest_name} "
                        f"(downlink as !{origin:08x}): {msg.content[:50]}...")
                    return True
                logger.debug(
                    "Downlink inject unavailable for %s — toradio fallback",
                    dest_name)

            # Send to interface (works for TCP, MQTT, and serial interfaces).
            # Broadcasts OMIT destinationId so each interface uses its own
            # broadcast default — meshtastic's SerialInterface/TCPInterface
            # hard-exit the process (our_exit) on destinationId=None, while
            # their default is BROADCAST_ADDR ('^all').
            if msg.is_broadcast:
                interface.sendText(content, channelIndex=msg.channel)
            else:
                interface.sendText(
                    content,
                    destinationId=msg.destination_id,
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
