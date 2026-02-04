"""
MQTT Nodeless Subscriber for MeshForge.

Enables mesh monitoring WITHOUT local Meshtastic hardware by connecting
to the public MQTT broker (mqtt.meshtastic.org) or a private broker.

This is the "nodeless" mode - observe the mesh from anywhere with internet.

Based on: pdxlocations/connect approach
See: https://github.com/pdxlocations/connect

Usage:
    subscriber = MQTTNodelessSubscriber()
    subscriber.start()

    # Get discovered nodes
    nodes = subscriber.get_nodes()

    # Get recent messages
    messages = subscriber.get_messages(limit=100)
"""

import json
import logging
import random
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from collections import deque

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Default Meshtastic MQTT settings
DEFAULT_BROKER = "mqtt.meshtastic.org"
DEFAULT_PORT_TLS = 8883
DEFAULT_PORT = 1883
DEFAULT_ROOT_TOPIC = "msh/US/2/e"
DEFAULT_CHANNEL = "LongFast"
DEFAULT_KEY = "AQ=="  # Default Meshtastic encryption key

# Local broker defaults (for meshtasticd → mosquitto → MeshForge architecture)
LOCAL_BROKER = "localhost"
LOCAL_PORT = 1883
LOCAL_ROOT_TOPIC = "msh/2/e"  # No region prefix for local meshtasticd publishing

# Robustness limits
MAX_PAYLOAD_BYTES = 65536  # 64 KB max per MQTT message
MAX_NODES = 10000  # Maximum tracked nodes before pruning
STALE_NODE_HOURS = 72  # Remove nodes not seen for 72 hours
VALID_LAT_RANGE = (-90.0, 90.0)
VALID_LON_RANGE = (-180.0, 180.0)
VALID_SNR_RANGE = (-50.0, 50.0)  # dB
VALID_RSSI_RANGE = (-200, 0)  # dBm

# Mesh congestion thresholds (from Meshtastic ROUTER_LATE documentation)
# See: https://meshtastic.org/blog/demystifying-router-late/
CHUTIL_WARNING_THRESHOLD = 25.0   # Channel utilization warning at 25%
CHUTIL_CRITICAL_THRESHOLD = 40.0  # Channel utilization critical at 40%
AIRUTILTX_WARNING_THRESHOLD = 7.0  # TX airtime warning at 7-8%
AIRUTILTX_CRITICAL_THRESHOLD = 10.0  # TX airtime critical at 10%

# Mesh size tracking
MESH_SIZE_WINDOW_HOURS = 24  # Track unique nodes seen in last 24 hours


@dataclass
class MQTTNode:
    """Node discovered via MQTT."""
    node_id: str
    long_name: str = ""
    short_name: str = ""
    hardware_model: str = ""
    role: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    battery_level: Optional[int] = None
    voltage: Optional[float] = None
    channel_utilization: Optional[float] = None
    air_util_tx: Optional[float] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
    last_seen: datetime = field(default_factory=datetime.now)
    via_mqtt: bool = True
    hop_start: Optional[int] = None
    hops_away: Optional[int] = None
    # Relay tracking (Meshtastic 2.6+)
    relay_node: Optional[int] = None  # Last byte of relay node ID
    next_hop: Optional[int] = None    # Last byte of expected next-hop node
    discovered_via_relay: bool = False  # Node discovered by seeing it relay packets
    # Environment metrics (BME280, BME680, BMP280)
    temperature: Optional[float] = None  # Celsius
    humidity: Optional[float] = None     # 0-100%
    pressure: Optional[float] = None     # hPa (barometric)
    gas_resistance: Optional[float] = None  # Ohms (BME680 VOC)
    # Air quality metrics (PMSA003I, SCD4X)
    pm25_standard: Optional[int] = None   # PM2.5 standard µg/m³
    pm25_environmental: Optional[int] = None  # PM2.5 environmental µg/m³
    pm10_standard: Optional[int] = None   # PM10 standard µg/m³
    pm10_environmental: Optional[int] = None  # PM10 environmental µg/m³
    co2: Optional[int] = None             # CO2 ppm (SCD4X)
    iaq: Optional[int] = None             # Indoor Air Quality index

    def is_online(self, threshold_minutes: int = 15) -> bool:
        """Check if node was seen recently."""
        delta = datetime.now() - self.last_seen
        return delta.total_seconds() < threshold_minutes * 60

    def get_age_string(self) -> str:
        """Get human-readable age string."""
        delta = datetime.now() - self.last_seen
        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"


@dataclass
class MQTTMessage:
    """Message received via MQTT."""
    message_id: str
    from_id: str
    to_id: str
    text: str
    channel: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    hop_start: Optional[int] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None


class MQTTNodelessSubscriber:
    """
    MQTT subscriber for nodeless Meshtastic monitoring.

    Connects to MQTT broker and passively monitors mesh traffic
    without requiring local hardware.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the MQTT subscriber.

        Args:
            config: Optional configuration dict. If not provided,
                   loads from ~/.config/meshforge/mqtt_nodeless.json
        """
        self._config = config or self._load_config()
        self._client = None
        self._connected = False
        self._stop_event = threading.Event()
        self._reconnect_thread = None

        # Data storage
        self._nodes: Dict[str, MQTTNode] = {}
        self._messages: deque = deque(maxlen=1000)  # Last 1000 messages
        self._nodes_lock = threading.Lock()
        self._messages_lock = threading.Lock()

        # Callbacks
        self._node_callbacks: List[Callable[[MQTTNode], None]] = []
        self._message_callbacks: List[Callable[[MQTTMessage], None]] = []

        # Map cache persistence
        self._last_cache_write: float = 0
        self._cache_interval: int = 30  # Write cache every 30 seconds max

        # Stale node cleanup
        self._last_cleanup: float = 0
        self._cleanup_interval: int = 600  # Check every 10 minutes

        # Stats
        self._stats_lock = threading.Lock()
        self._stats = {
            "messages_received": 0,
            "messages_rejected": 0,
            "nodes_discovered": 0,
            "nodes_discovered_via_relay": 0,  # Nodes found through relay_node field
            "relay_nodes_merged": 0,  # Partial relay nodes matched to full IDs
            "nodes_pruned": 0,
            "connect_time": None,
            "last_message_time": None,
            "reconnect_attempts": 0,
            "last_disconnect_reason": "",
            # Mesh health tracking (Meshtastic 2.7+)
            "nodes_chutil_warning": 0,    # Nodes with ChUtil > 25%
            "nodes_chutil_critical": 0,   # Nodes with ChUtil > 40%
            "nodes_airutiltx_warning": 0, # Nodes with AirUtilTX > 7%
            "nodes_airutiltx_critical": 0, # Nodes with AirUtilTX > 10%
            "nodes_with_env_metrics": 0,  # Nodes with environment sensors
            "nodes_with_aq_metrics": 0,   # Nodes with air quality sensors
        }

        # Mesh size tracking - unique node IDs seen with timestamps
        self._mesh_size_history: Dict[str, datetime] = {}

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults."""
        config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"

        if config_path.exists():
            try:
                return json.loads(config_path.read_text())
            except Exception as e:
                logger.error(f"Failed to load MQTT nodeless config: {e}")

        # Return defaults
        return {
            "broker": DEFAULT_BROKER,
            "port": DEFAULT_PORT_TLS,
            "username": "",
            "password": "",
            "root_topic": DEFAULT_ROOT_TOPIC,
            "channel": DEFAULT_CHANNEL,
            "key": DEFAULT_KEY,
            "use_tls": True,
            "regions": ["US"],  # Subscribe to these regions
            "auto_reconnect": True,
            "reconnect_delay": 5,
            "max_reconnect_delay": 60,
        }

    def save_config(self) -> bool:
        """Save current configuration to file."""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "mqtt_nodeless.json"

        try:
            config_path.write_text(json.dumps(self._config, indent=2))
            return True
        except Exception as e:
            logger.error(f"Failed to save MQTT nodeless config: {e}")
            return False

    def start(self) -> bool:
        """Start the MQTT subscriber."""
        if self._connected:
            return True

        self._stop_event.clear()
        return self._connect()

    def stop(self) -> None:
        """Stop the MQTT subscriber."""
        self._stop_event.set()
        self._disconnect()

    def _connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False

        try:
            # Create client
            self._client = mqtt.Client(
                client_id=f"meshforge_nodeless_{int(time.time())}",
                protocol=mqtt.MQTTv311
            )

            # Set callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Set credentials if provided
            username = self._config.get("username")
            password = self._config.get("password")
            if username:
                self._client.username_pw_set(username, password)

            # Configure TLS
            if self._config.get("use_tls", True):
                self._setup_tls()

            # Connect
            broker = self._config.get("broker", DEFAULT_BROKER)
            port = self._config.get("port", DEFAULT_PORT_TLS)

            logger.info(f"Connecting to MQTT broker {broker}:{port}")
            self._client.connect(broker, port, keepalive=60)
            self._client.loop_start()

            return True

        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def _setup_tls(self) -> None:
        """Configure TLS for MQTT connection."""
        if not self._client:
            return

        try:
            context = ssl.create_default_context()
            self._client.tls_set_context(context)
            logger.debug("TLS configured for MQTT")
        except Exception as e:
            logger.warning(f"TLS context setup warning: {e}")
            # Only allow insecure TLS if explicitly configured
            if self._config.get("tls_insecure", False):
                try:
                    self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                    self._client.tls_insecure_set(True)
                    logger.warning("Using insecure TLS (user-configured tls_insecure=true)")
                except Exception as e2:
                    logger.error(f"TLS insecure fallback failed: {e2}")
            else:
                logger.error(
                    f"TLS setup failed: {e}. "
                    "Set tls_insecure=true in config to bypass certificate verification."
                )

    def _disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.debug(f"Disconnect cleanup: {e}")
            self._client = None
        self._connected = False

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            self._stats["connect_time"] = datetime.now()
            logger.info("Connected to MQTT broker (nodeless mode)")

            # Subscribe to topics based on configured regions
            self._subscribe_topics()
        else:
            error_msgs = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            logger.error(f"MQTT connect failed: {error_msgs.get(rc, f'Error {rc}')}")

    def _subscribe_topics(self) -> None:
        """Subscribe to Meshtastic MQTT topics."""
        if not self._client:
            return

        root = self._config.get("root_topic", DEFAULT_ROOT_TOPIC)
        channel = self._config.get("channel", DEFAULT_CHANNEL)

        # Subscribe to JSON-formatted messages (easier to parse)
        # Topic format: msh/{region}/2/json/{channel}/#
        topic = f"{root.rsplit('/', 1)[0]}/json/{channel}/#"
        self._client.subscribe(topic)
        logger.info(f"Subscribed to: {topic}")

        # Also subscribe to encrypted topic for node discovery
        # Topic format: msh/{region}/2/e/{channel}/#
        enc_topic = f"{root}/{channel}/#"
        self._client.subscribe(enc_topic)
        logger.info(f"Subscribed to: {enc_topic}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        self._connected = False

        if rc != 0:
            reason = f"unexpected_rc_{rc}"
            self._stats["last_disconnect_reason"] = reason
            logger.warning(f"Unexpected MQTT disconnect (rc={rc})")
            if self._config.get("auto_reconnect", True) and not self._stop_event.is_set():
                self._start_reconnect()
        else:
            logger.info("Disconnected from MQTT broker")

    def _start_reconnect(self) -> None:
        """Start reconnection thread."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        """Reconnection loop with exponential backoff and jitter."""
        delay = self._config.get("reconnect_delay", 5)
        max_delay = self._config.get("max_reconnect_delay", 60)

        while not self._stop_event.is_set():
            # Add jitter (0-25% of delay) to prevent thundering herd
            jitter = random.uniform(0, delay * 0.25)
            wait_time = delay + jitter
            logger.info(f"Reconnecting in {wait_time:.1f}s...")
            self._stop_event.wait(wait_time)

            if self._stop_event.is_set():
                break

            with self._stats_lock:
                self._stats["reconnect_attempts"] += 1

            if self._connect():
                logger.info("Reconnection successful")
                break

            delay = min(delay * 1.5, max_delay)

    def _on_message(self, client, userdata, msg):
        """MQTT message callback."""
        try:
            topic = msg.topic
            payload = msg.payload

            # Payload size defense
            if len(payload) > MAX_PAYLOAD_BYTES:
                with self._stats_lock:
                    self._stats["messages_rejected"] += 1
                # Log topic structure for debugging (strip node ID for privacy)
                # Topic format: msh/{region}/2/e/{channel}/!nodeId
                topic_parts = topic.split('/')
                safe_topic = '/'.join(topic_parts[:-1]) + '/...' if len(topic_parts) > 2 else topic
                logger.warning(
                    f"Rejected oversized MQTT payload: {len(payload)} bytes "
                    f"(max: {MAX_PAYLOAD_BYTES}), topic pattern: {safe_topic}"
                )
                return

            with self._stats_lock:
                self._stats["messages_received"] += 1
                self._stats["last_message_time"] = datetime.now()

            # Try to decode JSON payload
            if "/json/" in topic:
                self._handle_json_message(topic, payload)
            else:
                # Encrypted payload - just track node existence
                self._handle_encrypted_message(topic, payload)

            # Periodically persist node data for map service
            now = time.time()
            if now - self._last_cache_write >= self._cache_interval:
                self._persist_map_cache()
                self._last_cache_write = now

            # Periodically clean stale nodes
            if now - self._last_cleanup >= self._cleanup_interval:
                self._cleanup_stale_nodes()
                self._last_cleanup = now

        except Exception as e:
            logger.debug(f"Error processing MQTT message: {e}")

    def _persist_map_cache(self):
        """Write current node GeoJSON to disk for map data service.

        The MapDataCollector reads this file to populate the live map
        without needing a direct reference to this subscriber instance.
        """
        try:
            cache_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / "mqtt_nodes.json"

            geojson = self.get_geojson()
            if geojson.get("features"):
                from utils.paths import atomic_write_text
                atomic_write_text(cache_file, json.dumps(geojson))
                logger.debug(f"Map cache: wrote {len(geojson['features'])} nodes")
        except Exception as e:
            logger.debug(f"Map cache write error: {e}")

    def _handle_json_message(self, topic: str, payload: bytes) -> None:
        """Handle JSON-formatted Meshtastic message."""
        try:
            data = json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # Extract node info from sender
        sender = data.get("sender") or data.get("from")
        if sender:
            self._update_node_from_json(sender, data)

        # Handle specific message types
        msg_type = data.get("type", "")

        if msg_type == "nodeinfo":
            self._handle_nodeinfo(data)
        elif msg_type == "position":
            self._handle_position(data)
        elif msg_type == "telemetry":
            self._handle_telemetry(data)
        elif msg_type == "text":
            self._handle_text_message(data)

    def _handle_encrypted_message(self, topic: str, payload: bytes) -> None:
        """Handle encrypted message - just track node existence."""
        # Topic format: msh/US/2/e/LongFast/!abcd1234
        parts = topic.split("/")
        if len(parts) >= 6:
            node_id = parts[-1]
            if node_id.startswith("!"):
                self._ensure_node(node_id)

    def _ensure_node(self, node_id: str) -> MQTTNode:
        """Ensure a node exists in our tracking."""
        with self._nodes_lock:
            if node_id not in self._nodes:
                self._nodes[node_id] = MQTTNode(node_id=node_id)
                with self._stats_lock:
                    self._stats["nodes_discovered"] += 1

                # Check if this node matches a previously discovered relay node
                if node_id.startswith("!") and not node_id.startswith("!????"):
                    self._try_merge_relay_node(node_id)
            else:
                self._nodes[node_id].last_seen = datetime.now()
            return self._nodes[node_id]

    def _try_merge_relay_node(self, full_node_id: str) -> None:
        """
        Check if this full node ID matches a partial relay node and merge them.
        Called under _nodes_lock.
        """
        try:
            full_num = int(full_node_id[1:], 16)
            last_byte = full_num & 0xFF
            partial_id = f"!????{last_byte:02x}"

            if partial_id in self._nodes:
                # Found matching partial relay node - merge
                partial_node = self._nodes[partial_id]
                full_node = self._nodes[full_node_id]

                # Transfer relay discovery flag
                full_node.discovered_via_relay = True

                # Remove partial entry
                del self._nodes[partial_id]

                with self._stats_lock:
                    self._stats["relay_nodes_merged"] += 1

                logger.info(
                    f"Merged relay node {partial_id} -> {full_node_id} "
                    f"({partial_node.long_name} -> {full_node.long_name or 'unknown'})"
                )
        except (ValueError, TypeError, KeyError):
            pass

    def _discover_relay_node(self, relay_byte: int, data: Dict) -> Optional[MQTTNode]:
        """
        Discover a relay node from its last byte (Meshtastic 2.6+ relay_node field).

        The relay_node field only contains the last byte of the node ID. We try to:
        1. Match it against existing nodes
        2. If no match, create a partial entry that can be filled in later

        This allows us to discover nodes that relay packets but never send their
        own telemetry/position, which is common for managed flood routing.
        """
        if relay_byte <= 0 or relay_byte > 255:
            return None

        # Try to find an existing node matching this last byte
        with self._nodes_lock:
            for node_id, node in self._nodes.items():
                if node_id.startswith("!"):
                    try:
                        # Extract last byte of node ID
                        node_num = int(node_id[1:], 16)
                        if (node_num & 0xFF) == relay_byte:
                            # Found matching node - update last_seen
                            node.last_seen = datetime.now()
                            logger.debug(f"Relay activity from known node: {node_id}")
                            return node
                    except (ValueError, TypeError):
                        continue

            # No match found - create a placeholder node with partial ID
            # Format: !????xxxx where xxxx is the hex of the last byte
            partial_id = f"!????{relay_byte:02x}"

            if partial_id not in self._nodes:
                # Create new node discovered via relay
                relay_node = MQTTNode(
                    node_id=partial_id,
                    discovered_via_relay=True,
                    long_name=f"Relay-{relay_byte:02x}",
                    short_name=f"R{relay_byte:02x}",
                )
                self._nodes[partial_id] = relay_node
                with self._stats_lock:
                    self._stats["nodes_discovered"] += 1
                    self._stats["nodes_discovered_via_relay"] += 1
                logger.info(f"Discovered relay node via packet routing: {partial_id}")
                return relay_node
            else:
                # Update existing partial node
                self._nodes[partial_id].last_seen = datetime.now()
                return self._nodes[partial_id]

    def _match_relay_to_full_node(self, partial_id: str, full_node_id: str) -> bool:
        """
        Match a partial relay node ID to a full node ID when we learn the full ID.

        When a node that was discovered via relay sends its own telemetry,
        we can merge the partial entry with the full node info.
        """
        if not partial_id.startswith("!????"):
            return False

        try:
            relay_byte = int(partial_id[-2:], 16)
            full_num = int(full_node_id[1:], 16)
            if (full_num & 0xFF) == relay_byte:
                # Match! Merge the nodes
                with self._nodes_lock:
                    if partial_id in self._nodes and full_node_id in self._nodes:
                        # Copy relay discovery flag to full node
                        self._nodes[full_node_id].discovered_via_relay = True
                        # Remove partial entry
                        del self._nodes[partial_id]
                        logger.info(f"Merged relay node {partial_id} -> {full_node_id}")
                        return True
        except (ValueError, TypeError):
            pass
        return False

    def _safe_float(self, value: Any, min_val: float, max_val: float) -> Optional[float]:
        """Safely extract and validate a float value within range."""
        if value is None:
            return None
        try:
            f = float(value)
            if min_val <= f <= max_val:
                return f
        except (TypeError, ValueError):
            pass
        return None

    def _safe_int(self, value: Any, min_val: int, max_val: int) -> Optional[int]:
        """Safely extract and validate an int value within range."""
        if value is None:
            return None
        try:
            i = int(value)
            if min_val <= i <= max_val:
                return i
        except (TypeError, ValueError):
            pass
        return None

    def _update_node_from_json(self, node_id: str, data: Dict) -> None:
        """Update node info from JSON message with input validation."""
        node = self._ensure_node(node_id)

        # Validate and update fields
        if "snr" in data:
            snr = self._safe_float(data["snr"], *VALID_SNR_RANGE)
            if snr is not None:
                node.snr = snr
        if "rssi" in data:
            rssi = self._safe_int(data["rssi"], *VALID_RSSI_RANGE)
            if rssi is not None:
                node.rssi = rssi
        if "hop_start" in data:
            hop = self._safe_int(data["hop_start"], 0, 15)
            if hop is not None:
                node.hop_start = hop
        if "hops_away" in data:
            hops = self._safe_int(data["hops_away"], 0, 15)
            if hops is not None:
                node.hops_away = hops

        # Extract relay node info (Meshtastic 2.6+)
        # relay_node is the last byte of the node ID that relayed this packet
        if "relay_node" in data or "relayNode" in data:
            relay = self._safe_int(
                data.get("relay_node") or data.get("relayNode"), 0, 255
            )
            if relay is not None and relay > 0:
                node.relay_node = relay
                # Discover the relay node if we haven't seen it
                self._discover_relay_node(relay, data)

        # Extract next_hop info (Meshtastic 2.6+)
        if "next_hop" in data or "nextHop" in data:
            next_hop = self._safe_int(
                data.get("next_hop") or data.get("nextHop"), 0, 255
            )
            if next_hop is not None and next_hop > 0:
                node.next_hop = next_hop

        # Notify callbacks (snapshot for thread-safe iteration)
        for callback in list(self._node_callbacks):
            try:
                callback(node)
            except Exception as e:
                logger.debug(f"Node callback error: {e}")

    def _handle_nodeinfo(self, data: Dict) -> None:
        """Handle nodeinfo message."""
        payload = data.get("payload", {})
        node_id = payload.get("id") or data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)
        node.long_name = payload.get("longname", node.long_name)
        node.short_name = payload.get("shortname", node.short_name)
        node.hardware_model = payload.get("hardware", node.hardware_model)
        node.role = payload.get("role", node.role)

    def _handle_position(self, data: Dict) -> None:
        """Handle position message with coordinate validation."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)

        # Position may be in different formats
        lat = None
        lon = None

        if "latitude_i" in payload:
            lat = self._safe_float(payload.get("latitude_i"), -900000000, 900000000)
            lon = self._safe_float(payload.get("longitude_i"), -1800000000, 1800000000)
            if lat is not None:
                lat = lat / 1e7
            if lon is not None:
                lon = lon / 1e7
        elif "latitude" in payload:
            lat = self._safe_float(payload.get("latitude"), *VALID_LAT_RANGE)
            lon = self._safe_float(payload.get("longitude"), *VALID_LON_RANGE)

        # Only update if both lat/lon are valid and non-zero
        if lat is not None and lon is not None and (lat != 0.0 or lon != 0.0):
            if VALID_LAT_RANGE[0] <= lat <= VALID_LAT_RANGE[1] and \
               VALID_LON_RANGE[0] <= lon <= VALID_LON_RANGE[1]:
                node.latitude = lat
                node.longitude = lon

        if "altitude" in payload:
            alt = self._safe_float(payload.get("altitude"), -500, 100000)
            if alt is not None:
                node.altitude = alt

    def _handle_telemetry(self, data: Dict) -> None:
        """Handle telemetry message with value validation."""
        payload = data.get("payload", {})
        node_id = data.get("from")
        if not node_id:
            return

        node = self._ensure_node(node_id)

        # Device metrics
        device = payload.get("device_metrics", {})
        if isinstance(device, dict) and device:
            battery = self._safe_int(device.get("battery_level"), 0, 101)
            if battery is not None:
                node.battery_level = battery

            voltage = self._safe_float(device.get("voltage"), 0.0, 10.0)
            if voltage is not None:
                node.voltage = voltage

            ch_util = self._safe_float(device.get("channel_utilization"), 0.0, 100.0)
            if ch_util is not None:
                node.channel_utilization = ch_util

            air_util = self._safe_float(device.get("air_util_tx"), 0.0, 100.0)
            if air_util is not None:
                node.air_util_tx = air_util

        # Environment metrics (BME280, BME680, BMP280, etc.)
        env = payload.get("environment_metrics", {})
        if isinstance(env, dict) and env:
            temp = self._safe_float(env.get("temperature"), -50.0, 100.0)
            if temp is not None:
                node.temperature = temp

            humidity = self._safe_float(env.get("relative_humidity"), 0.0, 100.0)
            if humidity is not None:
                node.humidity = humidity

            pressure = self._safe_float(env.get("barometric_pressure"), 300.0, 1200.0)
            if pressure is not None:
                node.pressure = pressure

            gas = self._safe_float(env.get("gas_resistance"), 0.0, 1000000.0)
            if gas is not None:
                node.gas_resistance = gas

        # Air quality metrics (PMSA003I, SCD4X)
        aq = payload.get("air_quality_metrics", {})
        if isinstance(aq, dict) and aq:
            pm25_std = self._safe_int(aq.get("pm25_standard"), 0, 1000)
            if pm25_std is not None:
                node.pm25_standard = pm25_std

            pm25_env = self._safe_int(aq.get("pm25_environmental"), 0, 1000)
            if pm25_env is not None:
                node.pm25_environmental = pm25_env

            pm10_std = self._safe_int(aq.get("pm10_standard"), 0, 1000)
            if pm10_std is not None:
                node.pm10_standard = pm10_std

            pm10_env = self._safe_int(aq.get("pm10_environmental"), 0, 1000)
            if pm10_env is not None:
                node.pm10_environmental = pm10_env

            co2 = self._safe_int(aq.get("co2"), 0, 10000)
            if co2 is not None:
                node.co2 = co2

            iaq = self._safe_int(aq.get("iaq"), 0, 500)
            if iaq is not None:
                node.iaq = iaq

    def _handle_text_message(self, data: Dict) -> None:
        """Handle text message."""
        payload = data.get("payload", {})
        text = payload.get("text") or data.get("text", "")
        if not text:
            return

        msg = MQTTMessage(
            message_id=str(data.get("id", time.time())),
            from_id=data.get("from", ""),
            to_id=data.get("to", ""),
            text=text,
            channel=data.get("channel", 0),
            snr=data.get("snr"),
            rssi=data.get("rssi"),
            hop_start=data.get("hop_start"),
        )

        with self._messages_lock:
            self._messages.append(msg)

        # Notify callbacks (snapshot for thread-safe iteration)
        for callback in list(self._message_callbacks):
            try:
                callback(msg)
            except Exception as e:
                logger.debug(f"Message callback error: {e}")

    def _cleanup_stale_nodes(self) -> None:
        """Remove nodes not seen for STALE_NODE_HOURS.

        Prevents unbounded memory growth when monitoring long-running
        sessions on busy networks.
        """
        cutoff = datetime.now()
        stale_ids = []

        with self._nodes_lock:
            for node_id, node in self._nodes.items():
                delta = cutoff - node.last_seen
                if delta.total_seconds() > STALE_NODE_HOURS * 3600:
                    stale_ids.append(node_id)

            # Also enforce MAX_NODES: if over limit, prune oldest
            if len(self._nodes) > MAX_NODES:
                # Sort by last_seen, prune oldest
                sorted_nodes = sorted(
                    self._nodes.items(),
                    key=lambda x: x[1].last_seen
                )
                excess = len(self._nodes) - MAX_NODES
                for node_id, _ in sorted_nodes[:excess]:
                    if node_id not in stale_ids:
                        stale_ids.append(node_id)

            for node_id in stale_ids:
                del self._nodes[node_id]

        if stale_ids:
            with self._stats_lock:
                self._stats["nodes_pruned"] += len(stale_ids)
            logger.debug(f"Pruned {len(stale_ids)} stale nodes")

    # Public API

    def is_connected(self) -> bool:
        """Check if connected to MQTT broker."""
        return self._connected

    def get_nodes(self) -> List[MQTTNode]:
        """Get all discovered nodes."""
        with self._nodes_lock:
            return list(self._nodes.values())

    def get_node(self, node_id: str) -> Optional[MQTTNode]:
        """Get a specific node by ID."""
        with self._nodes_lock:
            return self._nodes.get(node_id)

    def get_online_nodes(self, threshold_minutes: int = 15) -> List[MQTTNode]:
        """Get nodes seen within threshold."""
        with self._nodes_lock:
            return [n for n in self._nodes.values() if n.is_online(threshold_minutes)]

    def get_nodes_with_position(self) -> List[MQTTNode]:
        """Get nodes that have position data."""
        with self._nodes_lock:
            return [n for n in self._nodes.values()
                    if n.latitude is not None and n.longitude is not None]

    def get_relay_discovered_nodes(self) -> List[MQTTNode]:
        """Get nodes discovered via relay_node field (no direct telemetry yet)."""
        with self._nodes_lock:
            return [n for n in self._nodes.values() if n.discovered_via_relay]

    def get_partial_relay_nodes(self) -> List[MQTTNode]:
        """Get nodes with partial IDs (discovered via relay, not yet identified)."""
        with self._nodes_lock:
            return [n for n in self._nodes.values()
                    if n.node_id.startswith("!????")]

    def get_congested_nodes(self, warning_only: bool = False) -> List[MQTTNode]:
        """Get nodes with high channel utilization or TX airtime.

        Based on ROUTER_LATE thresholds from Meshtastic documentation:
        - ChUtil > 25% = warning, > 40% = critical
        - AirUtilTX > 7% = warning, > 10% = critical

        Args:
            warning_only: If True, only return nodes at warning level.
                          If False, return both warning and critical.

        Returns:
            List of MQTTNode objects with congestion issues.
        """
        with self._nodes_lock:
            congested = []
            for node in self._nodes.values():
                if node.channel_utilization is not None:
                    if warning_only:
                        if CHUTIL_WARNING_THRESHOLD <= node.channel_utilization < CHUTIL_CRITICAL_THRESHOLD:
                            congested.append(node)
                    elif node.channel_utilization >= CHUTIL_WARNING_THRESHOLD:
                        congested.append(node)
                        continue

                if node.air_util_tx is not None:
                    if warning_only:
                        if AIRUTILTX_WARNING_THRESHOLD <= node.air_util_tx < AIRUTILTX_CRITICAL_THRESHOLD:
                            if node not in congested:
                                congested.append(node)
                    elif node.air_util_tx >= AIRUTILTX_WARNING_THRESHOLD:
                        if node not in congested:
                            congested.append(node)

            return congested

    def get_nodes_with_environment_metrics(self) -> List[MQTTNode]:
        """Get nodes that have environment sensor data (temp, humidity, pressure)."""
        with self._nodes_lock:
            return [n for n in self._nodes.values()
                    if n.temperature is not None or n.humidity is not None or n.pressure is not None]

    def get_nodes_with_air_quality(self) -> List[MQTTNode]:
        """Get nodes that have air quality sensor data (PM2.5, CO2)."""
        with self._nodes_lock:
            return [n for n in self._nodes.values()
                    if n.pm25_standard is not None or n.co2 is not None]

    def get_mesh_health(self) -> Dict[str, Any]:
        """Get mesh health summary based on congestion metrics.

        Returns dict with:
        - status: "healthy", "warning", or "critical"
        - chutil_avg: Average channel utilization across online nodes
        - airutiltx_avg: Average TX airtime across online nodes
        - congested_nodes: Count of nodes with congestion issues
        - recommendations: List of suggested actions
        """
        online = self.get_online_nodes()
        if not online:
            return {
                "status": "unknown",
                "chutil_avg": None,
                "airutiltx_avg": None,
                "congested_nodes": 0,
                "recommendations": ["No online nodes to assess mesh health"]
            }

        # Calculate averages
        chutil_values = [n.channel_utilization for n in online if n.channel_utilization is not None]
        airutiltx_values = [n.air_util_tx for n in online if n.air_util_tx is not None]

        chutil_avg = sum(chutil_values) / len(chutil_values) if chutil_values else None
        airutiltx_avg = sum(airutiltx_values) / len(airutiltx_values) if airutiltx_values else None

        congested = self.get_congested_nodes()
        recommendations = []

        # Determine status and recommendations
        status = "healthy"

        if chutil_avg is not None and chutil_avg >= CHUTIL_CRITICAL_THRESHOLD:
            status = "critical"
            recommendations.append(f"Channel utilization at {chutil_avg:.1f}% - mesh is congested")
            recommendations.append("Consider reducing traffic or switching to slower modulation")
        elif chutil_avg is not None and chutil_avg >= CHUTIL_WARNING_THRESHOLD:
            status = "warning"
            recommendations.append(f"Channel utilization at {chutil_avg:.1f}% - approaching congestion")

        if airutiltx_avg is not None and airutiltx_avg >= AIRUTILTX_CRITICAL_THRESHOLD:
            status = "critical"
            recommendations.append(f"TX airtime at {airutiltx_avg:.1f}% - nodes transmitting too much")
            recommendations.append("Review ROUTER_LATE nodes and consider CLIENT role instead")
        elif airutiltx_avg is not None and airutiltx_avg >= AIRUTILTX_WARNING_THRESHOLD:
            if status != "critical":
                status = "warning"
            recommendations.append(f"TX airtime at {airutiltx_avg:.1f}% - monitor closely")

        if not recommendations:
            recommendations.append("Mesh operating within normal parameters")

        return {
            "status": status,
            "chutil_avg": round(chutil_avg, 1) if chutil_avg else None,
            "airutiltx_avg": round(airutiltx_avg, 1) if airutiltx_avg else None,
            "congested_nodes": len(congested),
            "nodes_with_metrics": len(chutil_values),
            "recommendations": recommendations
        }

    def get_mesh_size(self) -> Dict[str, int]:
        """Get mesh size statistics.

        Returns:
            Dict with:
            - total_nodes: Total unique nodes discovered
            - nodes_24h: Nodes seen in last 24 hours
            - nodes_online: Nodes seen in last 15 minutes
        """
        now = datetime.now()
        cutoff_24h = now.timestamp() - (MESH_SIZE_WINDOW_HOURS * 3600)

        with self._nodes_lock:
            nodes_24h = sum(
                1 for node in self._nodes.values()
                if node.last_seen.timestamp() >= cutoff_24h
            )

            return {
                "total_nodes": len(self._nodes),
                "nodes_24h": nodes_24h,
                "nodes_online": len(self.get_online_nodes()),
            }

    def get_messages(self, limit: int = 100) -> List[MQTTMessage]:
        """Get recent messages."""
        with self._messages_lock:
            messages = list(self._messages)
            return messages[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get subscriber statistics including mesh health metrics."""
        mesh_health = self.get_mesh_health()
        mesh_size = self.get_mesh_size()

        return {
            **self._stats,
            "node_count": len(self._nodes),
            "online_count": len(self.get_online_nodes()),
            "with_position": len(self.get_nodes_with_position()),
            "relay_discovered": len(self.get_relay_discovered_nodes()),
            "partial_relay_nodes": len(self.get_partial_relay_nodes()),
            "message_count": len(self._messages),
            # Mesh health (Meshtastic 2.7+)
            "mesh_health_status": mesh_health["status"],
            "mesh_chutil_avg": mesh_health["chutil_avg"],
            "mesh_airutiltx_avg": mesh_health["airutiltx_avg"],
            "congested_nodes": mesh_health["congested_nodes"],
            # Extended telemetry stats
            "nodes_with_env_metrics": len(self.get_nodes_with_environment_metrics()),
            "nodes_with_aq_metrics": len(self.get_nodes_with_air_quality()),
            # Mesh size
            "mesh_size_24h": mesh_size["nodes_24h"],
        }

    def register_node_callback(self, callback: Callable[[MQTTNode], None]) -> None:
        """Register callback for node updates."""
        self._node_callbacks.append(callback)

    def register_message_callback(self, callback: Callable[[MQTTMessage], None]) -> None:
        """Register callback for new messages."""
        self._message_callbacks.append(callback)

    def get_geojson(self) -> Dict:
        """Get nodes as GeoJSON FeatureCollection for mapping.

        Only includes nodes with valid, non-zero coordinates within
        the valid lat/lon range.
        """
        features = []

        for node in self.get_nodes_with_position():
            # Validate coordinates: non-None, non-zero, within range
            if (node.latitude is not None and node.longitude is not None and
                    (node.latitude != 0.0 or node.longitude != 0.0) and
                    VALID_LAT_RANGE[0] <= node.latitude <= VALID_LAT_RANGE[1] and
                    VALID_LON_RANGE[0] <= node.longitude <= VALID_LON_RANGE[1]):
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [node.longitude, node.latitude]
                    },
                    "properties": {
                        "id": node.node_id,
                        "name": node.long_name or node.short_name or node.node_id,
                        "network": "meshtastic",
                        "is_online": node.is_online(),
                        "via_mqtt": True,
                        "snr": node.snr,
                        "rssi": node.rssi,
                        "battery": node.battery_level,
                        "last_seen": node.get_age_string(),
                        "hardware": node.hardware_model,
                        "role": node.role,
                        "hops_away": node.hops_away,
                        # Relay tracking (Meshtastic 2.6+)
                        "discovered_via_relay": node.discovered_via_relay,
                        "relay_node": node.relay_node,
                        "next_hop": node.next_hop,
                        # Congestion metrics (Meshtastic 2.7+)
                        "channel_utilization": node.channel_utilization,
                        "air_util_tx": node.air_util_tx,
                        "is_congested": (
                            (node.channel_utilization is not None and node.channel_utilization >= CHUTIL_WARNING_THRESHOLD) or
                            (node.air_util_tx is not None and node.air_util_tx >= AIRUTILTX_WARNING_THRESHOLD)
                        ),
                        # Environment metrics
                        "temperature": node.temperature,
                        "humidity": node.humidity,
                        "pressure": node.pressure,
                        # Air quality metrics
                        "pm25": node.pm25_standard,
                        "co2": node.co2,
                        "iaq": node.iaq,
                    }
                }
                features.append(feature)

        return {"type": "FeatureCollection", "features": features}


# Factory functions for common configurations

def create_local_subscriber(
    broker: str = LOCAL_BROKER,
    port: int = LOCAL_PORT,
    root_topic: str = LOCAL_ROOT_TOPIC,
    channel: str = DEFAULT_CHANNEL,
) -> MQTTNodelessSubscriber:
    """
    Create an MQTT subscriber configured for a local broker (e.g., mosquitto).

    This is the recommended setup for multi-consumer architecture where
    meshtasticd publishes to a local broker.

    Args:
        broker: Local MQTT broker hostname (default: localhost)
        port: MQTT port (default: 1883, non-TLS)
        root_topic: Meshtastic root topic (default: msh/US/2/e)
        channel: Meshtastic channel (default: LongFast)

    Returns:
        MQTTNodelessSubscriber configured for local broker

    Example:
        subscriber = create_local_subscriber()
        subscriber.register_message_callback(my_handler)
        subscriber.start()
    """
    config = {
        "broker": broker,
        "port": port,
        "username": "",
        "password": "",
        "root_topic": root_topic,
        "channel": channel,
        "key": DEFAULT_KEY,
        "use_tls": False,  # Local brokers typically don't use TLS
        "regions": ["US"],
        "auto_reconnect": True,
        "reconnect_delay": 2,  # Faster reconnect for local
        "max_reconnect_delay": 30,
    }
    return MQTTNodelessSubscriber(config=config)


def create_public_subscriber(
    region: str = "US",
    channel: str = DEFAULT_CHANNEL,
) -> MQTTNodelessSubscriber:
    """
    Create an MQTT subscriber configured for the public Meshtastic broker.

    This is the "nodeless" mode - observe mesh networks without local hardware.

    Args:
        region: Region code (US, EU_868, etc.)
        channel: Meshtastic channel (default: LongFast)

    Returns:
        MQTTNodelessSubscriber configured for mqtt.meshtastic.org
    """
    config = {
        "broker": DEFAULT_BROKER,
        "port": DEFAULT_PORT_TLS,
        "username": "",
        "password": "",
        "root_topic": f"msh/{region}/2/e",
        "channel": channel,
        "key": DEFAULT_KEY,
        "use_tls": True,
        "regions": [region],
        "auto_reconnect": True,
        "reconnect_delay": 5,
        "max_reconnect_delay": 60,
    }
    return MQTTNodelessSubscriber(config=config)


# Singleton instance management

_local_subscriber: Optional[MQTTNodelessSubscriber] = None


def get_local_subscriber() -> MQTTNodelessSubscriber:
    """
    Get or create the global local MQTT subscriber.

    Returns a singleton instance configured for local broker (localhost:1883).
    """
    global _local_subscriber
    if _local_subscriber is None:
        _local_subscriber = create_local_subscriber()
    return _local_subscriber


def start_local_subscriber() -> bool:
    """
    Start the local MQTT subscriber.

    Returns:
        True if started successfully
    """
    subscriber = get_local_subscriber()
    return subscriber.start()


def stop_local_subscriber():
    """Stop the local MQTT subscriber."""
    global _local_subscriber
    if _local_subscriber:
        _local_subscriber.stop()
        _local_subscriber = None
