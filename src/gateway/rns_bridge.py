"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks

MeshCore bridge processing extracted to meshcore_bridge_mixin.py.
"""

import signal as _signal_mod
import threading
import time
import logging
import subprocess
from contextlib import contextmanager
from queue import Queue, Empty, Full
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from .config import GatewayConfig
from .node_tracker import UnifiedNodeTracker, UnifiedNode
from .reconnect import ReconnectStrategy
from .bridge_health import (
    BridgeHealthMonitor, DeliveryTracker, classify_error,
    BridgeStatus, SubsystemState, MessageOrigin
)
from utils.safe_import import safe_import

# MQTT bridge handler (zero-interference, recommended)
MQTTBridgeHandler, HAS_MQTT_BRIDGE = safe_import(
    '.mqtt_bridge_handler', 'MQTTBridgeHandler', package=__package__
)

# TCP-based handler (legacy, requires meshtastic Python library)
MeshtasticHandler, HAS_MESHTASTIC_LIB = safe_import(
    '.meshtastic_handler', 'MeshtasticHandler', package=__package__
)

# MeshCore handler (companion radio via meshcore_py)
MeshCoreHandler, HAS_MESHCORE = safe_import(
    '.meshcore_handler', 'MeshCoreHandler', package=__package__
)

# Import circuit breaker for destination-level failure handling
CircuitBreakerRegistry, HAS_CIRCUIT_BREAKER = safe_import(
    '.circuit_breaker', 'CircuitBreakerRegistry', package=__package__
)

# AREDN topology overlay (optional - visibility only)
AREDNTopologyOverlay, HAS_AREDN_TOPOLOGY = safe_import(
    '.aredn_topology', 'AREDNTopologyOverlay', package=__package__
)

# Import persistent message queue for reliable delivery
PersistentMessageQueue, MessagePriority, HAS_PERSISTENT_QUEUE = safe_import(
    '.message_queue', 'PersistentMessageQueue', 'MessagePriority', package=__package__
)

from .message_routing import MessageRouter, CLASSIFIER_AVAILABLE
from .meshcore_bridge_mixin import MeshCoreBridgeMixin

logger = logging.getLogger(__name__)

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
import os
from utils.paths import get_real_user_home, ReticulumPaths

# Import service checker for pre-flight checks (Issue #3)
from utils.service_check import check_service, ServiceState
HAS_SERVICE_CHECK = True

# Import event bus for RX message notifications (Issue #17 Phase 3)
from utils.event_bus import emit_message, emit_tactical
HAS_EVENT_BUS = True

# RNS sniffer is optional monitoring — not required for message bridging
try:
    from monitoring.rns_sniffer import (
        get_rns_sniffer, RNSPacketInfo, RNSPacketType,
        start_rns_capture, integrate_with_traffic_inspector
    )
    HAS_RNS_SNIFFER = True
except ImportError:
    HAS_RNS_SNIFFER = False

# Import RNS and LXMF modules (optional - for mesh bridge)
_RNS_mod, _HAS_RNS = safe_import('RNS')
_LXMF_mod, _HAS_LXMF = safe_import('LXMF')

# Import config drift detection
from utils.config_drift import detect_rnsd_config_drift, get_rnsd_effective_config_dir

# WebSocket is optional — used for web UI push, not core bridging
try:
    from utils.websocket_server import (
        start_websocket_server, is_websocket_available, stop_websocket_server
    )
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


@dataclass
class BridgedMessage:
    """Represents a message being bridged between networks"""
    source_network: str  # "meshtastic" or "rns"
    source_id: str
    destination_id: Optional[str]
    content: str
    title: Optional[str] = None
    timestamp: datetime = None
    is_broadcast: bool = False
    metadata: dict = None
    origin: MessageOrigin = MessageOrigin.UNKNOWN
    via_internet: bool = False  # True if message came through MQTT/internet

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.metadata is None:
            self.metadata = {}

    def should_bridge(self, filter_mqtt: bool = False) -> bool:
        """
        Check if this message should be bridged.

        Args:
            filter_mqtt: If True, drop MQTT-originated messages.
                        Useful for pure radio mesh networks.

        Returns:
            True if message should be bridged to other network.
        """
        if filter_mqtt and self.via_internet:
            return False
        if filter_mqtt and self.origin == MessageOrigin.MQTT:
            return False
        return True


class RNSMeshtasticBridge(MeshCoreBridgeMixin):
    """
    Main gateway bridge between RNS, Meshtastic, and MeshCore networks.

    Supports multiple modes:
    1. RNS Over Meshtastic - Uses Meshtastic as RNS transport layer
    2. Message Bridge - Translates messages between separate networks
    3. MeshCore Bridge - Bridges MeshCore companion radio with other protocols
    4. Tri-Bridge - All three protocols (Meshtastic + MeshCore + RNS)

    MeshCore bridge processing methods inherited from MeshCoreBridgeMixin.
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.node_tracker = UnifiedNodeTracker()

        # State
        self._running = False
        self._websocket_started = False
        self._connected_rns = False
        self._rns_via_rnsd = False  # True when rnsd handles RNS (bridge defers)
        self._rns_init_failed_permanently = False  # True if RNS can't be initialized from this thread
        self._rns_pre_initialized = False  # True if RNS was initialized from main thread

        # Reconnection strategy for RNS (Meshtastic reconnect is in handler)
        self._rns_reconnect = ReconnectStrategy.for_rns()
        self._stop_event = threading.Event()

        # Health monitoring
        self.health = BridgeHealthMonitor()

        # LXMF delivery confirmation tracking
        self.delivery_tracker = DeliveryTracker()

        # Message queues (bounded to prevent memory exhaustion)
        self._mesh_to_rns_queue = Queue(maxsize=1000)
        self._rns_to_mesh_queue = Queue(maxsize=1000)
        # MeshCore queues for 3-way routing
        self._meshcore_to_bridge_queue = Queue(maxsize=1000)
        self._bridge_to_meshcore_queue = Queue(maxsize=1000)

        # Threads
        self._mesh_thread = None
        self._rns_thread = None
        self._bridge_thread = None
        self._meshcore_thread = None

        # Callbacks (protected by _callbacks_lock for thread-safe registration)
        self._message_callbacks = []
        self._status_callbacks = []
        self._callbacks_lock = threading.Lock()

        # Thread-safe stats updates
        self._stats_lock = threading.Lock()

        # RNS components (lazy loaded)
        self._reticulum = None
        self._lxmf_router = None
        self._identity = None
        self._lxmf_source = None

        # Meshtastic handler (encapsulates connection and message handling)
        self._mesh_handler: Optional[MeshtasticHandler] = None

        # MeshCore handler (companion radio integration)
        self._meshcore_handler = None

        # Statistics
        self.stats = {
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
            'start_time': None,
        }

        # Persistent message queue for reliable delivery
        # Note: Meshtastic sender registered after handler init below
        self._persistent_queue = None
        if HAS_PERSISTENT_QUEUE:
            try:
                self._persistent_queue = PersistentMessageQueue()
                # RNS sender registered here, Meshtastic sender after handler init
                self._persistent_queue.register_sender(
                    "rns", self._queue_send_rns
                )
                logger.info("Persistent message queue initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize persistent queue: {e}")

        # Message routing and classification (extracted to message_routing.py)
        self._router = MessageRouter(self.config, self.stats, self._stats_lock)

        # Circuit breaker for destination-level failure handling
        self._circuit_breaker = None
        if HAS_CIRCUIT_BREAKER:
            self._circuit_breaker = CircuitBreakerRegistry(
                failure_threshold=5,
                recovery_timeout=60.0,
            )
            logger.info("Circuit breaker initialized for destination tracking")

        # AREDN topology overlay (visibility only)
        self._aredn_overlay = None
        self._aredn_thread = None
        aredn_cfg = getattr(self.config, 'aredn_backhaul', None)
        if HAS_AREDN_TOPOLOGY and aredn_cfg and aredn_cfg.enabled:
            try:
                self._aredn_overlay = AREDNTopologyOverlay(
                    router_ip=aredn_cfg.router_ip,
                    auto_detect=aredn_cfg.auto_detect,
                    poll_interval_sec=aredn_cfg.poll_interval_sec,
                )
                logger.info("AREDN topology overlay initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize AREDN overlay: {e}")

        # MQTT filtering configuration
        self._filter_mqtt_messages = False  # Set True to drop MQTT-originated messages

        # Initialize Meshtastic handler based on bridge mode
        # ── MeshCore handler (initialize FIRST when meshcore_primary) ──
        # In meshcore_primary mode, MeshCore is the primary radio and
        # Meshtastic is optional. In other modes, MeshCore is secondary.
        meshcore_config = getattr(self.config, 'meshcore', None)
        is_meshcore_primary = self.config.bridge_mode == "meshcore_primary"

        if is_meshcore_primary:
            # MeshCore-primary: initialize MeshCore handler first
            if HAS_MESHCORE and meshcore_config:
                logger.info("MeshCore-primary mode: initializing MeshCore handler")
                # Force-enable meshcore config in meshcore_primary mode
                meshcore_config.enabled = True
                self._meshcore_handler = MeshCoreHandler(
                    config=self.config,
                    node_tracker=self.node_tracker,
                    health=self.health,
                    stop_event=self._stop_event,
                    stats=self.stats,
                    stats_lock=self._stats_lock,
                    message_queue=self._meshcore_to_bridge_queue,
                    message_callback=self._notify_message,
                    status_callback=lambda status: self._notify_status(status),
                    should_bridge=self._router.should_bridge,
                )
                if self._persistent_queue:
                    self._persistent_queue.register_sender(
                        "meshcore", self._meshcore_handler.queue_send
                    )
                self.health.set_subsystem_enabled("meshcore", True)
                logger.info("MeshCore handler initialized (primary radio)")
            else:
                raise ImportError(
                    "MeshCore-primary mode requires meshcore_py library. "
                    "Install with: pip install meshcore"
                )

        # ── Meshtastic handler ──
        # In meshcore_primary mode, Meshtastic is OPTIONAL (graceful degradation).
        # In all other modes, Meshtastic is required.
        meshtastic_required = not is_meshcore_primary
        meshtastic_available = False

        if self.config.bridge_mode == "mqtt_bridge" and HAS_MQTT_BRIDGE:
            logger.info("Using MQTT bridge handler (zero-interference mode)")
            self._mesh_handler = MQTTBridgeHandler(
                config=self.config,
                node_tracker=self.node_tracker,
                health=self.health,
                stop_event=self._stop_event,
                stats=self.stats,
                stats_lock=self._stats_lock,
                message_queue=self._mesh_to_rns_queue,
                message_callback=self._notify_message,
                status_callback=lambda status: self._notify_status(status),
                should_bridge=self._router.should_bridge,
            )
            meshtastic_available = True
        elif is_meshcore_primary and (HAS_MQTT_BRIDGE or HAS_MESHTASTIC_LIB):
            # In meshcore_primary mode, try to set up Meshtastic as optional bridge
            if HAS_MQTT_BRIDGE:
                logger.info("MeshCore-primary: Meshtastic available via MQTT (optional bridge)")
                self._mesh_handler = MQTTBridgeHandler(
                    config=self.config,
                    node_tracker=self.node_tracker,
                    health=self.health,
                    stop_event=self._stop_event,
                    stats=self.stats,
                    stats_lock=self._stats_lock,
                    message_queue=self._mesh_to_rns_queue,
                    message_callback=self._notify_message,
                    status_callback=lambda status: self._notify_status(status),
                    should_bridge=self._router.should_bridge,
                )
                meshtastic_available = True
            elif HAS_MESHTASTIC_LIB:
                logger.info("MeshCore-primary: Meshtastic available via TCP (optional bridge)")
                self._mesh_handler = MeshtasticHandler(
                    config=self.config,
                    node_tracker=self.node_tracker,
                    health=self.health,
                    stop_event=self._stop_event,
                    stats=self.stats,
                    stats_lock=self._stats_lock,
                    message_queue=self._mesh_to_rns_queue,
                    message_callback=self._notify_message,
                    status_callback=lambda status: self._notify_status(status),
                    should_bridge=self._router.should_bridge,
                )
                meshtastic_available = True
        elif HAS_MESHTASTIC_LIB:
            if self.config.bridge_mode == "mqtt_bridge" and not HAS_MQTT_BRIDGE:
                logger.warning("MQTT bridge requested but paho-mqtt not available, "
                             "falling back to TCP handler")
            logger.info("Using TCP Meshtastic handler (legacy mode)")
            self._mesh_handler = MeshtasticHandler(
                config=self.config,
                node_tracker=self.node_tracker,
                health=self.health,
                stop_event=self._stop_event,
                stats=self.stats,
                stats_lock=self._stats_lock,
                message_queue=self._mesh_to_rns_queue,
                message_callback=self._notify_message,
                status_callback=lambda status: self._notify_status(status),
                should_bridge=self._router.should_bridge,
            )
            meshtastic_available = True
        elif meshtastic_required:
            raise ImportError(
                "No Meshtastic handler available. Install paho-mqtt for MQTT bridge "
                "(recommended) or meshtastic Python library for legacy TCP bridge."
            )

        if is_meshcore_primary and not meshtastic_available:
            logger.info("MeshCore-primary: running without Meshtastic (MeshCore + RNS only)")

        # Register Meshtastic sender now that handler exists
        if self._mesh_handler and self._persistent_queue:
            self._persistent_queue.register_sender(
                "meshtastic", self._mesh_handler.queue_send
            )

        # ── MeshCore handler (secondary mode — when NOT meshcore_primary) ──
        if not is_meshcore_primary:
            if HAS_MESHCORE and meshcore_config and meshcore_config.enabled:
                logger.info("Initializing MeshCore handler (secondary)")
                self._meshcore_handler = MeshCoreHandler(
                    config=self.config,
                    node_tracker=self.node_tracker,
                    health=self.health,
                    stop_event=self._stop_event,
                    stats=self.stats,
                    stats_lock=self._stats_lock,
                    message_queue=self._meshcore_to_bridge_queue,
                    message_callback=self._notify_message,
                    status_callback=lambda status: self._notify_status(status),
                    should_bridge=self._router.should_bridge,
                )
                if self._persistent_queue:
                    self._persistent_queue.register_sender(
                        "meshcore", self._meshcore_handler.queue_send
                    )
                self.health.set_subsystem_enabled("meshcore", True)
                logger.info("MeshCore handler initialized")
            else:
                if meshcore_config and meshcore_config.enabled and not HAS_MESHCORE:
                    logger.warning("MeshCore enabled in config but meshcore_handler not available")
                self.health.set_subsystem_enabled("meshcore", False)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return (self._mesh_handler and self._mesh_handler.is_connected) or self._connected_rns

    @property
    def bridge_status(self) -> BridgeStatus:
        """Get current bridge operational status."""
        return self.health.get_bridge_status()

    # =========================================================================
    # Subsystem State Management (Phase 2: Circuit Breakers)
    # =========================================================================

    def _update_subsystem_state(self, subsystem: str, state: SubsystemState) -> None:
        """Update a subsystem's state and emit an event if it changed.

        Args:
            subsystem: "meshtastic" or "rns"
            state: New SubsystemState value
        """
        old_state = self.health.set_subsystem_state(subsystem, state)
        if old_state != state:
            # Emit event for StatusBar and other listeners
            if HAS_EVENT_BUS:
                try:
                    from utils.event_bus import emit_service_status
                    emit_service_status(
                        f"bridge_{subsystem}",
                        available=(state == SubsystemState.HEALTHY),
                        message=f"{subsystem}: {state.value}",
                    )
                except Exception as e:
                    logger.debug(f"Failed to emit subsystem state event: {e}")

    def get_subsystem_state(self, subsystem: str) -> SubsystemState:
        """Get the current state of a bridge subsystem.

        Args:
            subsystem: "meshtastic", "rns", or "meshcore"

        Returns:
            Current SubsystemState.
        """
        return self.health.get_subsystem_state(subsystem)

    @property
    def is_fully_healthy(self) -> bool:
        """Check if bridge is fully operational (both networks up)."""
        return self.health.is_bridge_fully_healthy()

    def can_send_to(self, destination: str) -> bool:
        """
        Check if we can send to a destination (circuit breaker check).

        Args:
            destination: Target node/identity ID

        Returns:
            True if sending is allowed, False if circuit is open
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.can_send(destination)

    def record_send_success(self, destination: str) -> None:
        """Record successful send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success(destination)

    def record_send_failure(self, destination: str, error: str = "") -> None:
        """Record failed send to destination (for circuit breaker)."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure(destination, error)

    def get_open_circuits(self) -> Dict[str, Any]:
        """Get destinations with open circuits (currently blocked)."""
        if self._circuit_breaker is None:
            return {}
        return self._circuit_breaker.get_open_circuits()

    def set_filter_mqtt(self, enabled: bool) -> None:
        """
        Enable/disable MQTT message filtering.

        When enabled, messages that originated from MQTT/internet
        will not be bridged to the other network.

        Args:
            enabled: True to filter MQTT messages
        """
        self._filter_mqtt_messages = enabled
        logger.info(f"MQTT message filtering {'enabled' if enabled else 'disabled'}")

    def start(self) -> bool:
        """Start the gateway bridge"""
        if self._running:
            logger.warning("Bridge already running")
            return True

        # Issue #3: Pre-flight service check
        if HAS_SERVICE_CHECK:
            meshtasticd_status = check_service('meshtasticd')
            if not meshtasticd_status.available:
                logger.warning(f"meshtasticd not available: {meshtasticd_status.message}")
                logger.warning(f"Fix: {meshtasticd_status.fix_hint}")
                # Continue anyway - gateway can start in degraded mode
            else:
                logger.info("Pre-flight check: meshtasticd is running")

        logger.info("Starting RNS-Meshtastic bridge...")
        self._running = True
        self.stats['start_time'] = datetime.now()

        # Start WebSocket server for real-time message broadcast to web UI
        self._start_websocket_server()

        # Start node tracker
        self.node_tracker.start()

        # Pre-initialize RNS from main thread (signal handlers require it)
        # Must happen before spawning _rns_loop background thread
        self._init_rns_main_thread()

        # Start network threads
        if self.config.enabled:
            is_meshcore_primary = self.config.bridge_mode == "meshcore_primary"

            # In meshcore_primary mode, start MeshCore FIRST (it's the primary radio)
            if is_meshcore_primary and self._meshcore_handler:
                self._meshcore_thread = threading.Thread(
                    target=self._meshcore_loop,
                    daemon=True,
                    name="MeshCoreBridge"
                )
                self._meshcore_thread.start()
                logger.info("MeshCore handler thread started (primary radio)")

            # Start RNS (backhaul — always started when enabled)
            self._rns_thread = threading.Thread(
                target=self._rns_loop,
                daemon=True,
                name="RNSBridge"
            )
            self._rns_thread.start()

            # Start Meshtastic (optional in meshcore_primary mode)
            if self._mesh_handler:
                self._mesh_thread = threading.Thread(
                    target=self._meshtastic_loop,
                    daemon=True,
                    name="MeshtasticBridge"
                )
                self._mesh_thread.start()
            elif is_meshcore_primary:
                logger.info("MeshCore-primary: Meshtastic handler not available, "
                          "running MeshCore + RNS only")

            self._bridge_thread = threading.Thread(
                target=self._bridge_loop,
                daemon=True,
                name="MessageBridge"
            )
            self._bridge_thread.start()

        # Start MeshCore handler thread (secondary mode — non-meshcore_primary)
        if self._meshcore_handler and self.config.bridge_mode != "meshcore_primary":
            self._meshcore_thread = threading.Thread(
                target=self._meshcore_loop,
                daemon=True,
                name="MeshCoreBridge"
            )
            self._meshcore_thread.start()
            logger.info("MeshCore handler thread started (secondary)")

        # Start persistent queue processing
        if self._persistent_queue:
            self._persistent_queue.start_processing(interval=2.0)
            logger.info("Persistent message queue processing started")

        # Start RNS packet sniffer for Wireshark-grade traffic visibility
        if HAS_RNS_SNIFFER:
            try:
                start_rns_capture()
                integrate_with_traffic_inspector()
                logger.info("RNS packet sniffer started for traffic capture")
            except Exception as e:
                logger.warning(f"Could not start RNS sniffer: {e}")

        # Start AREDN topology polling (visibility only)
        if self._aredn_overlay:
            self._aredn_thread = threading.Thread(
                target=self._aredn_poll_loop,
                daemon=True,
                name="AREDNTopology",
            )
            self._aredn_thread.start()
            logger.info("AREDN topology polling started")

        logger.info("Bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the gateway bridge"""
        if not self._running:
            return

        logger.info("Stopping bridge...")
        self._running = False
        self._stop_event.set()  # Wake any sleeping reconnect waits

        # Stop persistent queue processing
        if self._persistent_queue:
            self._persistent_queue.stop_processing()

        # Stop node tracker
        self.node_tracker.stop()

        # Close connections
        if self._mesh_handler:
            self._mesh_handler.disconnect()
        if self._meshcore_handler:
            self._meshcore_handler.disconnect()
        self._disconnect_rns()

        # Wait for threads
        for thread in [self._mesh_thread, self._rns_thread,
                        self._bridge_thread, self._meshcore_thread,
                        self._aredn_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop WebSocket server
        self._stop_websocket_server()

        # Stop RNS sniffer
        if HAS_RNS_SNIFFER:
            try:
                from monitoring.rns_sniffer import stop_rns_capture
                stop_rns_capture()
            except Exception as e:
                logger.debug(f"RNS sniffer stop error: {e}")

        logger.info("Bridge stopped")
        self._notify_status("stopped")

    def _aredn_poll_loop(self):
        """Periodically scan AREDN topology and update node reachability."""
        overlay = self._aredn_overlay
        aredn_cfg = getattr(self.config, 'aredn_backhaul', None)
        try:
            interval = int(aredn_cfg.poll_interval_sec) if aredn_cfg else 60
        except (TypeError, ValueError):
            interval = 60

        while self._running:
            try:
                links = overlay.discover_backhaul_links()
                if links:
                    # Build reachability map for all tracked nodes
                    nodes = self.node_tracker.get_all_nodes()
                    reach_map = {}
                    for node in nodes:
                        reach = overlay.get_reachability(node.id)
                        reach_map[node.id] = reach
                    self.node_tracker.update_aredn_topology(reach_map)
                    logger.debug(f"AREDN scan: {len(links)} links, "
                                 f"{len(overlay.get_remote_gateways())} remote gateways")
            except Exception as e:
                logger.warning(f"AREDN poll error: {e}")

            if self._stop_event.wait(interval):
                break

    def get_status(self) -> dict:
        """Get current bridge status including subsystem states."""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        mesh_connected = self._mesh_handler.is_connected if self._mesh_handler else False
        meshcore_connected = (
            self._meshcore_handler.is_connected if self._meshcore_handler else False
        )
        meshcore_config = getattr(self.config, 'meshcore', None)
        return {
            'running': self._running,
            'enabled': self.config.enabled,
            'meshtastic_connected': mesh_connected,
            'rns_connected': self._connected_rns,
            'rns_via_rnsd': self._rns_via_rnsd,
            'meshcore_connected': meshcore_connected,
            'meshcore_enabled': bool(meshcore_config and meshcore_config.enabled),
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'node_stats': self.node_tracker.get_stats(),
            'subsystems': self.health.get_subsystem_states(),
            'bridge_status': self.bridge_status.value,
        }

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send a message to Meshtastic network."""
        if not self._mesh_handler:
            logger.warning("Meshtastic handler not initialized")
            return False
        return self._mesh_handler.send_text(message, destination, channel)

    def send_to_rns(self, message: str, destination_hash: bytes = None) -> bool:
        """Send a message to RNS network via LXMF"""
        if not self._connected_rns:
            logger.warning("Not connected to RNS")
            return False

        if self._lxmf_source is None:
            logger.warning("LXMF source not initialized (partial RNS init)")
            return False

        try:
            import RNS
            import LXMF

            if destination_hash:
                # Direct message
                if not RNS.Transport.has_path(destination_hash):
                    RNS.Transport.request_path(destination_hash)
                    # Wait briefly for path (interruptible on shutdown)
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        if self._stop_event.wait(0.1):
                            break

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    return False

                dest_identity = RNS.Identity.recall(destination_hash)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                # Would need group destination or propagation
                logger.warning("Broadcast to RNS requires propagation node")
                return False

            lxm = LXMF.LXMessage(
                destination,
                self._lxmf_source,
                message,
                "MeshForge Gateway"
            )

            # Track delivery confirmation
            msg_id = f"lxmf-{int(time.time() * 1000)}"
            self.delivery_tracker.track_message(
                msg_id, destination_hash, message[:50]
            )

            # Register LXMF delivery/failure callbacks
            def on_delivered(receipt):
                self.delivery_tracker.confirm_delivery(msg_id)

            def on_failed(receipt):
                reason = "delivery_failed"
                if hasattr(receipt, 'failure_reason'):
                    reason = str(receipt.failure_reason)
                self.delivery_tracker.confirm_failure(msg_id, reason)

            try:
                lxm.register_delivery_callback(on_delivered)
                lxm.register_failed_callback(on_failed)
            except (AttributeError, TypeError):
                # LXMF version may not support callbacks
                logger.debug("LXMF callbacks not available, skipping delivery tracking")

            self._lxmf_router.handle_outbound(lxm)
            return True

        except Exception as e:
            logger.error(f"Failed to send to RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

    # send_to_meshcore() inherited from MeshCoreBridgeMixin

    def _queue_send_rns(self, payload: Dict) -> bool:
        """Send handler for persistent queue - RNS destination."""
        message = payload.get('message', '')
        destination_hash = payload.get('destination_hash')

        if not self._connected_rns:
            return False

        try:
            import RNS
            import LXMF

            if not destination_hash:
                return False

            if isinstance(destination_hash, str):
                destination_hash = bytes.fromhex(destination_hash)

            if not RNS.Transport.has_path(destination_hash):
                RNS.Transport.request_path(destination_hash)
                for _ in range(30):
                    if RNS.Transport.has_path(destination_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            if not RNS.Transport.has_path(destination_hash):
                return False

            dest_identity = RNS.Identity.recall(destination_hash)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            lxm = LXMF.LXMessage(destination, self._lxmf_source, message, "MeshForge Gateway")
            self._lxmf_router.handle_outbound(lxm)
            return True

        except Exception as e:
            logger.error(f"Queue send to RNS failed: {e}")
            return False

    def enqueue_message(self, message: str, destination: str, dest_type: str = "meshtastic",
                        priority: str = "normal", **kwargs) -> Optional[str]:
        """
        Enqueue a message for reliable delivery.

        Args:
            message: Message content
            destination: Destination ID/hash
            dest_type: "meshtastic" or "rns"
            priority: "low", "normal", "high", or "urgent"
            **kwargs: Additional parameters (channel, etc.)

        Returns:
            Message ID if enqueued, None if queue unavailable
        """
        if not self._persistent_queue:
            # Fall back to direct send
            if dest_type == "meshtastic":
                return "direct" if self.send_to_meshtastic(message, destination, kwargs.get('channel', 0)) else None
            else:
                dest_hash = kwargs.get('destination_hash')
                if isinstance(dest_hash, str):
                    dest_hash = bytes.fromhex(dest_hash)
                return "direct" if self.send_to_rns(message, dest_hash) else None

        # Map priority string to enum
        priority_map = {
            "low": MessagePriority.LOW,
            "normal": MessagePriority.NORMAL,
            "high": MessagePriority.HIGH,
            "urgent": MessagePriority.URGENT,
        }
        msg_priority = priority_map.get(priority, MessagePriority.NORMAL)

        payload = {
            'message': message,
            'destination': destination,
            **kwargs
        }

        return self._persistent_queue.enqueue(
            payload=payload,
            destination=dest_type,
            priority=msg_priority
        )

    def get_queue_stats(self) -> Dict:
        """Get persistent queue statistics."""
        if self._persistent_queue:
            return self._persistent_queue.get_stats()
        return {}

    def _on_meshtastic_receive(self, packet: dict) -> None:
        """Handle incoming Meshtastic packet (compatibility shim).

        Delegates to MeshtasticHandler._on_receive. Kept for backward
        compatibility with integration tests and external callers.
        """
        if self._mesh_handler:
            self._mesh_handler._on_receive(packet)

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages"""
        with self._callbacks_lock:
            self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes"""
        with self._callbacks_lock:
            self._status_callbacks.append(callback)

    def test_connection(self) -> dict:
        """Test connectivity to all configured networks"""
        results = {
            'meshtastic': {'connected': False, 'error': None},
            'rns': {'connected': False, 'error': None},
        }

        # Test Meshtastic
        try:
            if self._mesh_handler and self._mesh_handler.test_connection():
                results['meshtastic']['connected'] = True
        except Exception as e:
            results['meshtastic']['error'] = str(e)

        # Test RNS
        try:
            if self._test_rns():
                results['rns']['connected'] = True
        except Exception as e:
            results['rns']['error'] = str(e)

        # Test MeshCore (if enabled)
        if self._meshcore_handler:
            results['meshcore'] = {'connected': False, 'error': None}
            try:
                if self._meshcore_handler.is_connected:
                    results['meshcore']['connected'] = True
            except Exception as e:
                results['meshcore']['error'] = str(e)

        return results

    # ========================================
    # Private Methods
    # ========================================

    def _meshtastic_loop(self):
        """Main loop for Meshtastic connection - delegates to handler.

        In meshcore_primary mode, this may not be started if no Meshtastic
        handler is available (graceful degradation).
        """
        if self._mesh_handler:
            self._mesh_handler.run_loop()
        else:
            logger.debug("Meshtastic loop skipped: no handler available")

    # _meshcore_loop() inherited from MeshCoreBridgeMixin

    def _rns_loop(self):
        """Main loop for RNS connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Respects permanent failure flag for non-retriable errors.
        Manages the RNS subsystem state independently of Meshtastic.
        """
        _logged_permanent_failure = False
        while self._running:
            try:
                # Don't retry if RNS init failed permanently (e.g., library not installed)
                if self._rns_init_failed_permanently:
                    self._update_subsystem_state("rns", SubsystemState.DISABLED)
                    if not _logged_permanent_failure:
                        logger.warning("RNS initialization failed permanently - "
                                      "bridge will not attempt reconnection. "
                                      "Check RNS/LXMF installation and logs above.")
                        _logged_permanent_failure = True
                    self._stop_event.wait(30)
                    continue

                if not self._connected_rns:
                    self._update_subsystem_state("rns", SubsystemState.DISCONNECTED)
                    if not self._rns_reconnect.should_retry():
                        logger.warning("RNS reconnection: max attempts reached, resetting")
                        self._rns_reconnect.reset()
                        self._stop_event.wait(self._rns_reconnect.config.max_delay)
                        continue

                    logger.info(f"Attempting RNS connection "
                               f"(attempt {self._rns_reconnect.attempts + 1})...")
                    self.health.record_connection_event("rns", "retry")
                    self._connect_rns()

                    if self._connected_rns:
                        self._rns_reconnect.record_success()
                        self.health.record_connection_event("rns", "connected")
                        self._update_subsystem_state("rns", SubsystemState.HEALTHY)
                        logger.info("RNS connection established")
                    else:
                        self._rns_reconnect.record_failure()
                        self._rns_reconnect.wait(self._stop_event)
                        continue

                if self._connected_rns:
                    # RNS handles its own event loop
                    self._stop_event.wait(1)

            except Exception as e:
                category = self.health.record_error("rns", e)
                logger.error(f"RNS loop error ({category}): {e}")
                self._connected_rns = False
                self.health.record_connection_event("rns", "error", str(e))

                if category == "permanent":
                    logger.error("RNS permanent error detected, stopping retries")
                    self._rns_init_failed_permanently = True
                    self._update_subsystem_state("rns", SubsystemState.DISABLED)
                else:
                    self._update_subsystem_state("rns", SubsystemState.DISCONNECTED)
                    self._rns_reconnect.record_failure()
                    self._rns_reconnect.wait(self._stop_event)

    def _bridge_loop(self):
        """Main loop for message bridging.

        Phase 2 (Circuit Breakers): Each subsystem operates independently.
        When a destination is down, messages are queued to the persistent
        queue instead of being dropped. The bridge drains queued messages
        when the destination comes back up.
        """
        loop_count = 0
        while self._running:
            try:
                # Sync subsystem states from connection status
                self._sync_subsystem_states()

                # Process Meshtastic → RNS queue
                try:
                    msg = self._mesh_to_rns_queue.get(timeout=0.1)
                    rns_state = self.health.get_subsystem_state("rns")
                    if rns_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        # RNS is down — queue for later delivery
                        requeued = self._requeue_failed_message(msg, "rns")
                        if requeued:
                            self.health.record_message_queued_degraded()
                            logger.debug("Mesh→RNS: RNS subsystem down, message queued")
                    else:
                        self._process_mesh_to_rns(msg)
                    # Also route Meshtastic → MeshCore if handler active
                    if self._meshcore_handler:
                        try:
                            self._bridge_to_meshcore_queue.put_nowait(msg)
                        except Full:
                            logger.debug("→MeshCore queue full, dropping Mesh→MC message")
                except Empty:
                    pass

                # Process RNS → Meshtastic queue
                try:
                    msg = self._rns_to_mesh_queue.get(timeout=0.1)
                    mesh_state = self.health.get_subsystem_state("meshtastic")
                    if mesh_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        # Meshtastic is down — queue for later delivery
                        requeued = self._requeue_failed_message(msg, "meshtastic")
                        if requeued:
                            self.health.record_message_queued_degraded()
                            logger.debug("RNS→Mesh: Meshtastic subsystem down, message queued")
                    else:
                        self._process_rns_to_mesh(msg)
                    # Also route RNS → MeshCore if handler active
                    if self._meshcore_handler:
                        try:
                            self._bridge_to_meshcore_queue.put_nowait(msg)
                        except Full:
                            logger.debug("→MeshCore queue full, dropping RNS→MC message")
                except Empty:
                    pass

                # Process MeshCore → Bridge queue (route to Meshtastic and/or RNS)
                try:
                    msg = self._meshcore_to_bridge_queue.get_nowait()
                    self._process_meshcore_to_bridge(msg)
                except Empty:
                    pass

                # Process Bridge → MeshCore queue (messages from other networks)
                try:
                    msg = self._bridge_to_meshcore_queue.get_nowait()
                    mc_state = self.health.get_subsystem_state("meshcore")
                    if mc_state in (SubsystemState.DISCONNECTED, SubsystemState.DISABLED):
                        requeued = self._requeue_failed_message(msg, "meshcore")
                        if requeued:
                            self.health.record_message_queued_degraded()
                            logger.debug("→MeshCore: subsystem down, message queued")
                    else:
                        self._process_bridge_to_meshcore(msg)
                except Empty:
                    pass

                # Periodically check delivery timeouts (~every 30s)
                loop_count += 1
                if loop_count % 150 == 0:
                    self.delivery_tracker.check_timeouts()
                    # Drain persistent queue when subsystems are back
                    self._drain_persistent_queue()
                    # Clean expired bounced messages from routing classifier
                    self._cleanup_bounced_messages()

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                self._stop_event.wait(1)

    def _sync_subsystem_states(self) -> None:
        """Synchronize subsystem states from connection status.

        Called each bridge loop iteration. Both handlers manage their own
        reconnection, so we observe connection states and update accordingly.
        The RNS subsystem state is also updated in _rns_loop, but we sync
        here too so the bridge loop has accurate state even when _rns_loop
        is not running (e.g., in tests).
        """
        # Meshtastic
        if not self._mesh_handler:
            self._update_subsystem_state("meshtastic", SubsystemState.DISABLED)
        elif self._mesh_handler.is_connected:
            self._update_subsystem_state("meshtastic", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshtastic", SubsystemState.DISCONNECTED)

        # RNS (also managed by _rns_loop, but kept in sync here)
        if self._rns_init_failed_permanently:
            self._update_subsystem_state("rns", SubsystemState.DISABLED)
        elif self._connected_rns:
            self._update_subsystem_state("rns", SubsystemState.HEALTHY)
        # Note: don't overwrite DISCONNECTED here — _rns_loop handles transitions

        # MeshCore
        if not self._meshcore_handler:
            self._update_subsystem_state("meshcore", SubsystemState.DISABLED)
        elif self._meshcore_handler.is_connected:
            self._update_subsystem_state("meshcore", SubsystemState.HEALTHY)
        else:
            self._update_subsystem_state("meshcore", SubsystemState.DISCONNECTED)

    def _drain_persistent_queue(self) -> None:
        """Process pending messages from the persistent queue.

        Called periodically from _bridge_loop when subsystems are healthy.
        Only drains messages destined for currently-connected subsystems.
        """
        if not self._persistent_queue:
            return
        try:
            self._persistent_queue.process_once(batch_size=5)
        except Exception as e:
            logger.warning(f"Persistent queue drain error: {e}")

    def _cleanup_bounced_messages(self) -> None:
        """Clean expired bounced messages from the routing classifier queue.

        Prevents unbounded queue growth when low-confidence messages
        are never manually reviewed. Called periodically from _bridge_loop.
        """
        if not hasattr(self, '_router') or not self._router:
            return
        try:
            cleared = self._router.clear_expired_bounced(max_age_seconds=3600.0)
            if cleared:
                logger.debug(f"Cleared {cleared} expired bounced messages")
        except Exception as e:
            logger.debug(f"Bouncer cleanup skipped: {e}")

    @staticmethod
    @contextmanager
    def _suppress_signal_in_thread():
        """Suppress signal.signal() calls when not in the main thread.

        LXMF.LXMRouter() and RNS.Reticulum() internally register signal
        handlers for graceful shutdown. When called from a background
        thread, signal.signal() raises ValueError. This context manager
        temporarily replaces signal.signal with a safe wrapper that
        returns SIG_DFL instead of raising.

        On the main thread, this is a no-op passthrough.
        """
        if threading.current_thread() is threading.main_thread():
            yield
            return

        original = _signal_mod.signal

        def _safe_signal(signalnum, handler):
            # Cannot register signal handlers from non-main thread.
            # Return default disposition; bridge has its own shutdown logic.
            return _signal_mod.SIG_DFL

        _signal_mod.signal = _safe_signal
        try:
            yield
        finally:
            _signal_mod.signal = original

    def _init_rns_main_thread(self):
        """Pre-initialize RNS from the main thread.

        RNS.Reticulum() registers signal handlers that only work in the
        main thread. If we defer to the background _rns_loop thread,
        initialization fails with 'signal only works in main thread'.

        When rnsd is running, we connect as a client to its shared instance.

        POLICY: Diagnose, don't fix. This method NEVER restarts services
        or modifies configs. It logs issues and lets the user fix them.
        """
        import threading as _threading
        if _threading.current_thread() is not _threading.main_thread():
            logger.warning("RNS pre-init skipped (not main thread)")
            return

        if not _HAS_RNS:
            logger.info("RNS not installed, will be handled in _connect_rns")
            return

        RNS = _RNS_mod

        # Ensure /etc/reticulum/storage subdirs exist before RNS init.
        # RNS requires ratchets/, resources/, cache/announces/.
        # Create dirs if missing but NEVER restart services.
        if os.geteuid() == 0:
            if not ReticulumPaths.ensure_system_dirs():
                logger.warning("Could not create /etc/reticulum directories "
                             "(filesystem may be read-only)")

        # Detect rnsd process
        try:
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()
        except ImportError:
            rns_pids = []

        # Determine config directory: explicit config > rnsd's actual path > default
        config_dir = self.config.rns.config_dir or None
        if config_dir:
            logger.info(f"Using explicit RNS config dir: {config_dir}")
        else:
            # Check for config drift between gateway and rnsd
            try:
                drift = detect_rnsd_config_drift()
                if drift.drifted:
                    logger.warning("Config drift: %s", drift.message)
                    config_dir = str(drift.rnsd_config_dir)
                    logger.info("Using rnsd's config dir: %s", config_dir)
            except Exception as e:
                logger.debug("Config drift check skipped: %s", e)

        try:
            if rns_pids:
                logger.info(f"rnsd detected (PID: {rns_pids[0]}), "
                           "connecting as shared instance client")
                self._rns_via_rnsd = True

            self._reticulum = RNS.Reticulum(configdir=config_dir)
            self._rns_pre_initialized = True
            logger.info("RNS pre-initialized from main thread")
        except Exception as e:
            err_msg = str(e).lower()
            if "reinitialise" in err_msg or "already running" in err_msg:
                self._rns_pre_initialized = True
                logger.info("RNS already initialized, bridge will use existing instance")
            elif hasattr(e, 'errno') and getattr(e, 'errno', None) == 98:
                logger.warning(f"RNS port conflict: {e} (will retry in background)")
            else:
                logger.warning(f"RNS pre-init failed: {e}")
                try:
                    from utils.gateway_diagnostic import diagnose_rnsd_connection
                    diagnose_rnsd_connection(rns_pids, error=e)
                except Exception:
                    pass  # diagnostic failure should never block init

    def _connect_rns(self):
        """Initialize RNS and LXMF.

        If RNS was pre-initialized from the main thread (via _init_rns_main_thread),
        skips Reticulum initialization and proceeds directly to LXMF setup.
        Otherwise falls back to initialization here (background thread).

        POLICY: Diagnose, don't fix. Never restart services or modify configs.
        """
        if not (_HAS_RNS and _HAS_LXMF):
            logger.warning("RNS/LXMF library not installed - bridge cannot connect")
            self._connected_rns = False
            self._rns_init_failed_permanently = True
            return

        # Pre-flight: verify rnsd is available (advisory, not blocking)
        rnsd_status = check_service('rnsd')
        if not rnsd_status.available:
            logger.warning("rnsd not available: %s", rnsd_status.message)
            if rnsd_status.fix_hint:
                logger.info("Fix: %s", rnsd_status.fix_hint)
            # Continue anyway — RNS can init standalone without rnsd

        RNS = _RNS_mod
        LXMF = _LXMF_mod

        # Both RNS.Reticulum() and LXMF.LXMRouter() register signal
        # handlers internally. When _connect_rns is called from the
        # background _rns_loop thread, signal.signal() raises ValueError.
        # Suppress signal registration for the entire init sequence.
        with self._suppress_signal_in_thread():
            try:
                if self._rns_pre_initialized:
                    logger.info("RNS pre-initialized, proceeding to LXMF setup")
                else:
                    # Fallback: init RNS from background thread.
                    # Works when rnsd is running (client mode, no signal handlers).
                    config_dir = self.config.rns.config_dir or None
                    if not config_dir:
                        try:
                            effective = get_rnsd_effective_config_dir()
                            config_dir = str(effective)
                        except Exception:
                            pass  # Use RNS default resolution

                    try:
                        self._reticulum = RNS.Reticulum(configdir=config_dir)
                    except Exception as e:
                        err_msg = str(e).lower()
                        if "reinitialise" in err_msg or "already running" in err_msg:
                            logger.info("RNS already initialized, proceeding to LXMF")
                        elif "signal only works in main thread" in err_msg:
                            logger.warning("RNS needs main thread init (no rnsd running?)")
                            self._rns_init_failed_permanently = True
                            self._connected_rns = False
                            return
                        elif hasattr(e, 'errno') and getattr(e, 'errno', None) == 98:
                            logger.warning(f"RNS port conflict: {e} (will retry)")
                            self._connected_rns = False
                            return
                        else:
                            raise

                # Set up LXMF messaging on top of the RNS instance
                self._setup_lxmf(RNS, LXMF)

            except Exception as e:
                logger.error(f"Failed to connect to RNS: {e}")
                try:
                    from utils.gateway_diagnostic import (
                        diagnose_rnsd_connection, find_rns_processes
                    )
                    diagnose_rnsd_connection(find_rns_processes(), error=e)
                except Exception:
                    pass  # diagnostic failure should never block bridge
                self._connected_rns = False

    def _setup_lxmf(self, RNS, LXMF):
        """Set up LXMF identity, router, and announce handler.

        Called after RNS is initialized (either pre-init or fallback).
        Separated from _connect_rns to keep the method focused and
        allow LXMF setup to be retried independently.
        """
        # Create or load identity
        identity_path = get_real_user_home() / ".config" / "meshforge" / "gateway_identity"
        if identity_path.exists():
            self._identity = RNS.Identity.from_file(str(identity_path))
        else:
            self._identity = RNS.Identity()
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            self._identity.to_file(str(identity_path))

        # Create LXMF router
        storage_path = get_real_user_home() / ".config" / "meshforge" / "lxmf_storage"
        storage_path.mkdir(parents=True, exist_ok=True)
        self._lxmf_router = LXMF.LXMRouter(storagepath=str(storage_path))

        # Register delivery callback
        self._lxmf_router.register_delivery_callback(self._on_lxmf_receive)

        # Create source identity
        self._lxmf_source = self._lxmf_router.register_delivery_identity(
            self._identity,
            display_name="MeshForge Gateway"
        )

        # Announce presence
        self._lxmf_router.announce(self._lxmf_source.hash)

        # Register announce handler for node discovery
        class AnnounceHandler:
            def __init__(self, bridge):
                self.aspect_filter = "lxmf.delivery"
                self.bridge = bridge

            def received_announce(self, dest_hash, announced_identity, app_data):
                self.bridge._on_rns_announce(dest_hash, announced_identity, app_data)

        RNS.Transport.register_announce_handler(AnnounceHandler(self))

        self._connected_rns = True
        logger.info("Connected to RNS (LXMF ready)")
        self._notify_status("rns_connected")

    def _disconnect_rns(self):
        """Disconnect from RNS and release ports"""
        # Properly shut down RNS to release ports
        if self._reticulum:
            try:
                import RNS
                # RNS.Transport.exithandler() closes all interfaces and releases ports
                RNS.Transport.exithandler()
                logger.debug("RNS Transport shut down")
            except Exception as e:
                logger.debug(f"Error shutting down RNS Transport: {e}")

        self._lxmf_router = None
        self._lxmf_source = None
        self._identity = None
        self._reticulum = None
        self._connected_rns = False

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        try:
            # Update node info
            source_hash = message.source_hash
            node = UnifiedNode.from_rns(source_hash)
            self.node_tracker.add_node(node)

            # Capture LXMF message for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        # Encode message content as payload
                        content_bytes = message.content.encode('utf-8') if message.content else b''
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.DATA,
                            source_hash=source_hash,
                            direction="inbound",
                            payload=content_bytes,
                            payload_size=len(content_bytes),
                            announce_aspect="lxmf.delivery",
                        )
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer LXMF capture error: {e}")

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=message.content,
                title=message.title,
                metadata={
                    'lxmf_stamp': message.stamp,
                }
            )

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages
                content = message.content
                if message.title:
                    content = f"[{message.title}] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # Queue for bridging if enabled (non-blocking to prevent deadlock)
            if self._router.should_bridge(msg):
                try:
                    self._rns_to_mesh_queue.put_nowait(msg)
                except Full:
                    logger.warning("RNS→Mesh queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            # Capture announce packet for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    import RNS
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.ANNOUNCE,
                            destination_hash=dest_hash,
                            direction="inbound",
                            announce_app_data=app_data,
                            announce_aspect="lxmf.delivery",
                        )
                        # Get identity hash if available
                        if announced_identity:
                            try:
                                packet_info.source_hash = announced_identity.hash
                                packet_info.announce_identity = announced_identity.hash
                            except Exception:
                                pass
                        # Get hop count
                        try:
                            if RNS.Transport.has_path(dest_hash):
                                hops = RNS.Transport.hops_to(dest_hash)
                                packet_info.hops = hops if hops is not None else 0
                        except Exception:
                            pass
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer capture error: {e}")

            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    # Routing delegated to MessageRouter (see gateway/message_routing.py)

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing classifier statistics."""
        return self._router.get_routing_stats()

    def get_last_classification(self) -> Optional[Dict]:
        """Get the last classification result for debugging."""
        return self._router.get_last_classification()

    def fix_routing(self, msg_id: str, correct_category: str) -> bool:
        """Record a user correction for routing decisions."""
        return self._router.fix_routing(msg_id, correct_category)

    def _process_mesh_to_rns(self, msg: BridgedMessage):
        """Process message from Meshtastic to RNS.

        On send failure for non-broadcast messages, attempts to persist
        to the persistent queue for later retry.
        """
        try:
            prefix = f"[Mesh:{msg.source_id[-4:]}] " if msg.source_id else "[Mesh] "
            content = prefix + msg.content

            destination_hash = None
            if msg.destination_id and not msg.is_broadcast:
                destination_hash = self._get_rns_destination(msg.destination_id)

            if self.send_to_rns(content, destination_hash):
                logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_mesh_to_rns'] += 1
                self.health.record_message_sent("mesh_to_rns")
            else:
                if msg.is_broadcast:
                    logger.debug(f"Mesh→RNS broadcast not sent (no propagation node): {content[:30]}...")
                else:
                    logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                    with self._stats_lock:
                        self.stats['errors'] += 1
                    requeued = self._requeue_failed_message(msg, "rns")
                    self.health.record_message_failed("mesh_to_rns", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("rns", e)
            self._requeue_failed_message(msg, "rns")
            self.health.record_message_failed("mesh_to_rns", requeued=True)

    def _get_rns_destination(self, meshtastic_id: str) -> bytes:
        """Look up RNS destination hash for a Meshtastic node ID"""
        # Check node tracker for known mappings
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        return None

    def _requeue_failed_message(self, msg, destination: str) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send (BridgedMessage or CanonicalMessage).
            destination: Target network ("meshtastic", "rns", or "meshcore").

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
            source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
            self._persistent_queue.enqueue(
                payload={
                    'message': msg.content,
                    'source_id': source_id,
                    'destination_id': dest_id or "",
                    'metadata': msg.metadata or {},
                },
                destination=destination,
                priority=MessagePriority.HIGH,
            )
            logger.debug(f"Failed message re-queued to persistent storage ({destination})")
            return True
        except Exception as e:
            logger.error(f"Failed to persist message for retry: {e}")
            return False

    def _process_rns_to_mesh(self, msg: BridgedMessage):
        """Process message from RNS to Meshtastic.

        On send failure, persists to persistent queue for later retry.
        """
        try:
            prefix = f"[RNS:{msg.source_id[:4]}] "
            content = prefix + msg.content

            if self.send_to_meshtastic(content, channel=self.config.meshtastic.channel):
                logger.info(f"Bridge RNS→Mesh: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                self.health.record_message_sent("rns_to_mesh")
            else:
                logger.warning("Failed to bridge RNS→Mesh")
                with self._stats_lock:
                    self.stats['errors'] += 1
                requeued = self._requeue_failed_message(msg, "meshtastic")
                self.health.record_message_failed("rns_to_mesh", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            self.health.record_error("meshtastic", e)
            self._requeue_failed_message(msg, "meshtastic")
            self.health.record_message_failed("rns_to_mesh", requeued=True)

    # _process_meshcore_to_bridge() and _process_bridge_to_meshcore()
    # inherited from MeshCoreBridgeMixin — see meshcore_bridge_mixin.py

    def _test_rns(self) -> bool:
        """Test RNS availability"""
        return _HAS_RNS

    def _notify_message(self, msg):
        """Notify message callbacks and emit to event bus (thread-safe snapshot).

        Handles both BridgedMessage and CanonicalMessage objects.

        Issue #17 Phase 3: Emit messages to event bus so UI panels can subscribe
        and display RX messages without being directly coupled to the bridge.
        """
        with self._callbacks_lock:
            callbacks = list(self._message_callbacks)
        for callback in callbacks:
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

        # Emit to event bus for UI panels (Issue #17 Phase 3)
        if HAS_EVENT_BUS and emit_message:
            try:
                # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
                node_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '') or ''
                dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', None)
                title = getattr(msg, 'title', None) or (msg.metadata.get('title') if msg.metadata else None)
                emit_message(
                    direction='rx',
                    content=msg.content,
                    node_id=node_id,
                    node_name="",  # Could be enhanced with node lookup
                    channel=msg.metadata.get('channel', 0) if msg.metadata else 0,
                    network=msg.source_network,
                    raw_data={
                        'destination_id': dest_id,
                        'is_broadcast': msg.is_broadcast,
                        'title': title,
                        'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
                        'metadata': msg.metadata
                    }
                )
            except Exception as e:
                logger.warning(f"Event bus emit failed: {e}")

        # Auto-ingest tactical messages (X1 format) to timeline + event bus
        msg_type = getattr(msg, 'message_type', None)
        if msg_type is not None and hasattr(msg_type, 'value') and msg_type.value == 'tactical':
            try:
                from tactical.x1_codec import decode as x1_decode, is_x1
                if is_x1(msg.content):
                    tac_msg = x1_decode(msg.content)
                    emit_tactical(
                        tactical_type=tac_msg.tactical_type.name,
                        message_id=tac_msg.id,
                        sender_id=tac_msg.sender_id,
                        content=tac_msg.content,
                        encryption_mode=tac_msg.encryption_mode.value,
                    )
            except Exception as e:
                logger.warning(f"Tactical auto-ingest failed: {e}")

    def _start_websocket_server(self):
        """Start WebSocket server for real-time message broadcast to web UI."""
        if not HAS_WEBSOCKET:
            return
        try:
            if is_websocket_available():
                if start_websocket_server(port=5001):
                    logger.info("WebSocket server started on port 5001")
                    self._websocket_started = True
                else:
                    logger.debug("WebSocket server failed to start")
            else:
                logger.debug("WebSocket not available (websockets library not installed)")
        except Exception as e:
            logger.debug(f"Could not start WebSocket server: {e}")

    def _stop_websocket_server(self):
        """Stop WebSocket server."""
        if getattr(self, '_websocket_started', False):
            try:
                stop_websocket_server()
                logger.info("WebSocket server stopped")
            except Exception as e:
                logger.debug(f"Error stopping WebSocket server: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks (thread-safe snapshot)"""
        with self._callbacks_lock:
            callbacks = list(self._status_callbacks)
        for callback in callbacks:
            try:
                callback(status, self.get_status())
            except Exception as e:
                logger.error(f"Status callback error: {e}")


# === Module-level helper functions for CLI/headless operation ===
# Extracted to gateway_cli.py; re-exported here for backward compatibility.
from .gateway_cli import (  # noqa: F401, E402
    start_gateway_headless,
    stop_gateway_headless,
    get_gateway_stats,
    is_gateway_running,
)
