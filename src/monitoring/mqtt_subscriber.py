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
from utils.safe_import import safe_import
from utils.timeouts import (
    MQTT_RECONNECT_INITIAL,
    MQTT_RECONNECT_MAX,
    MQTT_LOCAL_RECONNECT_INITIAL,
    MQTT_LOCAL_RECONNECT_MAX,
)
from gateway.circuit_breaker import create_service_registry as _create_cb_registry

# Per-broker circuit breaker — prevents hammering a downed MQTT broker
_mqtt_circuit = _create_cb_registry("mqtt_subscriber", failure_threshold=5, recovery_timeout=60.0)

# Module-level safe imports
_mqtt, _HAS_PAHO_MQTT = safe_import('paho.mqtt.client')

logger = logging.getLogger(__name__)

# Default Meshtastic MQTT settings (from centralized mqtt_defaults)
from utils.mqtt_defaults import (
    MESHTASTIC_PUBLIC_BROKER as DEFAULT_BROKER,
    MESHTASTIC_PUBLIC_PORT as DEFAULT_PORT_TLS,
    MESHTASTIC_PUBLIC_PORT_PLAIN as DEFAULT_PORT,
    MESHTASTIC_PUBLIC_ROOT_TOPIC as DEFAULT_ROOT_TOPIC,
    MESHTASTIC_PUBLIC_CHANNEL as DEFAULT_CHANNEL,
    MESHTASTIC_PUBLIC_KEY as DEFAULT_KEY,
)

# Local broker defaults (for meshtasticd → mosquitto → MeshForge architecture)
LOCAL_BROKER = "localhost"
LOCAL_PORT = 1883
LOCAL_ROOT_TOPIC = "msh/2/e"  # No region prefix for local meshtasticd publishing

# Robustness limits
MAX_PAYLOAD_BYTES = 65536  # 64 KB max per MQTT message
MAX_NODES = 10000  # Maximum tracked nodes before pruning
STALE_NODE_HOURS = 72  # Remove nodes not seen for 72 hours

# Mesh congestion thresholds (from Meshtastic ROUTER_LATE documentation)
# See: https://meshtastic.org/blog/demystifying-router-late/
CHUTIL_WARNING_THRESHOLD = 25.0   # Channel utilization warning at 25%
CHUTIL_CRITICAL_THRESHOLD = 40.0  # Channel utilization critical at 40%
AIRUTILTX_WARNING_THRESHOLD = 7.0  # TX airtime warning at 7-8%
AIRUTILTX_CRITICAL_THRESHOLD = 10.0  # TX airtime critical at 10%

# Mesh size tracking
MESH_SIZE_WINDOW_HOURS = 24  # Track unique nodes seen in last 24 hours

# Shared data types and validation ranges live in a leaf module so they can be
# imported by _mqtt_message_decoder without forming a cycle. Re-exported here
# so existing `from monitoring.mqtt_subscriber import MQTTNode, ...` callers
# keep working unchanged.
from monitoring._mqtt_types import (
    MQTTMessage,
    MQTTNode,
    VALID_LAT_RANGE,
    VALID_LON_RANGE,
    VALID_RSSI_RANGE,
    VALID_SNR_RANGE,
)
from monitoring._mqtt_message_decoder import MQTTMessageDecoderMixin


class MQTTNodelessSubscriber(MQTTMessageDecoderMixin):
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
        self._atexit_registered = False  # register the exit handler only once

        # Data storage
        self._nodes: Dict[str, MQTTNode] = {}
        self._messages: deque = deque(maxlen=1000)  # Last 1000 messages
        self._nodes_lock = threading.RLock()  # RLock: methods that hold lock may call others that also acquire it
        self._messages_lock = threading.Lock()

        # Callbacks
        self._node_callbacks: List[Callable[[MQTTNode], None]] = []
        self._message_callbacks: List[Callable[[MQTTMessage], None]] = []
        # Raw decoded-json packet observers: cb(topic, data) per uplinked
        # packet of ANY type (message callbacks fire only for text; node
        # callbacks carry neither the topic nor per-packet RF fields).
        # Kilo K1 edge capture rides this. Fires on the paho network
        # thread — observers must be fast and must not touch this client.
        self._packet_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []

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
            "processing_errors": 0,   # witnessed _on_message swallows
            "nodes_rejected": 0,      # implausible node keys refused
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
            "nodes_with_health_metrics": 0,  # Nodes with health sensors (HR, SpO2)
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
            "reconnect_delay": MQTT_RECONNECT_INITIAL,
            "max_reconnect_delay": MQTT_RECONNECT_MAX,
        }

    def save_config(self) -> bool:
        """Save current configuration to file.

        The config may carry broker credentials — written 0600 (a plain
        write_text left the password world-readable on multi-user boxes).
        """
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "mqtt_nodeless.json"

        try:
            config_path.write_text(json.dumps(self._config, indent=2))
            config_path.chmod(0o600)
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
        if not _HAS_PAHO_MQTT:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return False

        mqtt = _mqtt

        # Tear down any prior client before creating a new one. Without this,
        # every reconnect cycle orphaned the previous paho Client — loop_start()
        # keeps its network thread + broker socket alive, so file descriptors to
        # the broker climbed until the process hit its open-files limit
        # ([Errno 24]), wedging the HTTP server (it could no longer accept()).
        # Observed in the wild: 298 leaked ESTAB sockets to localhost:1883.
        if self._client is not None:
            self._disconnect()

        try:
            # Create client - compatible with paho-mqtt v1.x and v2.x.
            # Random suffix: two processes minting an id in the same second
            # collide on the broker and kick each other off.
            client_id = (f"meshforge_nodeless_{int(time.time())}"
                         f"_{random.randint(0, 0xFFFF):04x}")
            if hasattr(mqtt, 'CallbackAPIVersion'):
                # paho-mqtt v2.x requires callback_api_version
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                    client_id=client_id,
                    protocol=mqtt.MQTTv311
                )
            else:
                # paho-mqtt v1.x
                self._client = mqtt.Client(
                    client_id=client_id,
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

            # Advisory pre-flight for localhost brokers (Issue #3)
            broker = self._config.get("broker", DEFAULT_BROKER)
            port = self._config.get("port", DEFAULT_PORT_TLS)
            if broker in ('localhost', '127.0.0.1', '::1'):
                try:
                    from utils.service_check import check_service
                    broker_status = check_service('mosquitto')
                    if not broker_status.available:
                        logger.warning("mosquitto pre-flight: %s (attempting connection anyway)", broker_status.message)
                except ImportError:
                    pass

            # Connect with timeout to prevent hanging
            connect_timeout = self._config.get("connect_timeout", 10)  # 10 second default

            logger.info(f"Connecting to MQTT broker {broker}:{port}")

            # Use connect_async for non-blocking connection
            self._client.connect_async(broker, port, keepalive=60)
            self._client.loop_start()

            # Register atexit handler for clean shutdown — once per instance,
            # not on every reconnect (each _connect() used to stack a duplicate).
            if not self._atexit_registered:
                import atexit
                atexit.register(self._atexit_cleanup)
                self._atexit_registered = True

            # Wait for connection with timeout
            start_time = time.time()
            while not self._connected and (time.time() - start_time) < connect_timeout:
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

            if not self._connected:
                logger.warning(f"Connection to {broker}:{port} timed out after {connect_timeout}s")
                # Connection will continue trying in background due to loop_start()

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
        """Disconnect from MQTT broker with timeout."""
        client = self._client
        if client:
            try:
                # Disconnect first (tells broker we're leaving)
                try:
                    client.disconnect()
                except Exception as e:
                    logger.debug(f"MQTT disconnect error: {e}")

                # loop_stop() can hang in some edge cases, use timeout thread
                def stop_loop():
                    try:
                        # paho-mqtt v2.x removed the force parameter
                        client.loop_stop()
                    except Exception as e:
                        logger.debug(f"MQTT loop_stop error: {e}")

                stop_thread = threading.Thread(target=stop_loop, daemon=True)
                stop_thread.start()
                stop_thread.join(timeout=3.0)

                if stop_thread.is_alive():
                    logger.warning("MQTT loop_stop timed out, abandoning thread")
            except Exception as e:
                logger.debug(f"Disconnect cleanup: {e}")
            self._client = None
        self._connected = False

    def _atexit_cleanup(self) -> None:
        """Cleanup handler called on process exit."""
        if self._client:
            try:
                self._stop_event.set()
                self._client.disconnect()
                self._client.loop_stop()
            except Exception as e:
                logger.debug(f"MQTT atexit cleanup error: {e}")
            self._client = None

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        if rc == 0:
            self._connected = True
            with self._stats_lock:
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
        broker = self._config.get("broker", DEFAULT_BROKER)
        port = self._config.get("port", DEFAULT_PORT_TLS)
        cb_dest = f"{broker}:{port}"

        while not self._stop_event.is_set():
            # Circuit breaker — don't hammer a broker that's known-down
            if not _mqtt_circuit.can_send(cb_dest):
                logger.debug(f"Circuit open for {cb_dest}, waiting for recovery window")
                self._stop_event.wait(delay)
                if self._stop_event.is_set():
                    break
                continue

            # Add jitter (0-25% of delay) to prevent thundering herd
            jitter = random.uniform(0, delay * 0.25)
            wait_time = delay + jitter
            logger.debug(f"Reconnecting in {wait_time:.1f}s...")
            self._stop_event.wait(wait_time)

            if self._stop_event.is_set():
                break

            with self._stats_lock:
                self._stats["reconnect_attempts"] += 1

            if self._connect():
                _mqtt_circuit.record_success(cb_dest)
                logger.info("Reconnection successful")
                break

            _mqtt_circuit.record_failure(cb_dest, "reconnect_failed")
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
            # Witnessed swallow: a repeating handler bug used to be 100%
            # invisible (debug-level only) while messages_received kept
            # climbing — processing looked alive while every packet died.
            with self._stats_lock:
                self._stats["processing_errors"] = \
                    self._stats.get("processing_errors", 0) + 1
            now_ts = time.time()
            if now_ts - getattr(self, "_last_processing_error_warn", 0) > 60:
                self._last_processing_error_warn = now_ts
                logger.warning(
                    f"MQTT message processing error (witnessed in stats as "
                    f"processing_errors; further errors this minute at "
                    f"debug): {e}")
            else:
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

    # Message decoding methods provided by MQTTMessageDecoderMixin:
    # _handle_json_message, _handle_encrypted_message, _ensure_node,
    # _try_merge_relay_node, _discover_relay_node, _match_relay_to_full_node,
    # _safe_float, _safe_int, _update_node_from_json, _handle_nodeinfo,
    # _handle_position, _handle_telemetry, _handle_text_message

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

    def add_packet_callback(
            self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register a raw decoded-json packet observer: cb(topic, data).

        Fires once per successfully-decoded json uplink packet (any type),
        before type-specific handling, on the paho network thread. The
        topic carries the uplinking gateway's identity as its suffix.
        """
        self._packet_callbacks.append(callback)

    def remove_packet_callback(
            self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Detach a packet observer registered via add_packet_callback.

        Register/remove must pair, or a shared long-lived subscriber
        accumulates one dead observer per bounded window (each parsing
        every packet forever). Removing an unregistered callback is a
        no-op — detach paths run in ``finally`` blocks and must not raise.
        The decoder iterates a snapshot (``list(...)``), so removal from
        another thread mid-dispatch is safe.
        """
        try:
            self._packet_callbacks.remove(callback)
        except ValueError:
            pass

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
        "reconnect_delay": MQTT_LOCAL_RECONNECT_INITIAL,
        "max_reconnect_delay": MQTT_LOCAL_RECONNECT_MAX,
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
        "reconnect_delay": MQTT_RECONNECT_INITIAL,
        "max_reconnect_delay": MQTT_RECONNECT_MAX,
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
