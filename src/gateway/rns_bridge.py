"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks

MeshCore bridge processing extracted to meshcore_bridge_mixin.py.
RNS/LXMF connection lifecycle extracted to _rns_bridge_connection.py.
"""

import threading
import time
import logging
from queue import Queue, Empty, Full
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from .config import GatewayConfig
from .node_tracker import UnifiedNodeTracker, UnifiedNode
from .reconnect import ReconnectStrategy
from .bridge_health import (
    BridgeHealthMonitor, DeliveryTracker,
    BridgeStatus, SubsystemState, MessageOrigin
)
from utils.boundary_timing import call_boundary
from gateway.bounded_rpc import bounded_call, default_on_wedge
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

# TX load balancer and failover manager for dual-radio gateways
RadioLoadBalancer, LoadBalancerConfig, HAS_LOAD_BALANCER = safe_import(
    '.radio_failover', 'RadioLoadBalancer', 'LoadBalancerConfig', package=__package__
)
FailoverManager, FailoverConfig, HAS_FAILOVER = safe_import(
    '.radio_failover', 'FailoverManager', 'FailoverConfig', package=__package__
)

# Cross-gateway failover via MQTT heartbeat
GatewayHeartbeat, HeartbeatConfig, HAS_HEARTBEAT = safe_import(
    '.gateway_heartbeat', 'GatewayHeartbeat', 'HeartbeatConfig', package=__package__
)

# Import persistent message queue for reliable delivery
PersistentMessageQueue, MessagePriority, HAS_PERSISTENT_QUEUE = safe_import(
    '.message_queue', 'PersistentMessageQueue', 'MessagePriority', package=__package__
)

from .message_routing import MessageRouter
from .meshcore_bridge_mixin import MeshCoreBridgeMixin
from ._rns_bridge_connection import RNSConnectionMixin
from ._rns_bridge_xform import MessageTransformMixin
from ._rns_bridge_aux import BridgeAuxMixin

logger = logging.getLogger(__name__)

# Centralized path utility — used by RNSConnectionMixin in _rns_bridge_connection.py
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)

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

# Config drift detection used by RNSConnectionMixin in _rns_bridge_connection.py

# WebSocket lifecycle moved to BridgeAuxMixin in _rns_bridge_aux.py.


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
        # Normalize bytes→str at construction so downstream code (xform,
        # requeue→JSON, str ops like .startswith('@')) cannot trip on
        # LXMF's bytes payload. Issue #40 fix was xform-local; centralizing
        # here closes the requeue double-crash window for any future code
        # path that builds a BridgedMessage directly from an LXMF callback.
        if isinstance(self.content, bytes):
            self.content = self.content.decode("utf-8", errors="replace")
        elif self.content is None:
            self.content = ""
        elif not isinstance(self.content, str):
            self.content = str(self.content)
        if isinstance(self.title, bytes):
            self.title = self.title.decode("utf-8", errors="replace")

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


class RNSMeshtasticBridge(
    RNSConnectionMixin,
    MeshCoreBridgeMixin,
    MessageTransformMixin,
    BridgeAuxMixin,
):
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
        # Hardening D: the historical messages_* counters are kept (consumed
        # by launcher.py, gateway_cli.py, bridge_cli.py, commands/gateway.py)
        # and remain wire-equivalent to *_delivered. The new attempted/
        # delivered/dropped triplets are surfaced in status output so
        # operators can see "tried 100, radio accepted 95, lost 5" instead
        # of the single legacy success count which masked dropped paths
        # entirely. Delivered semantics:
        #   M→R delivered = LXMF send_to_rns returned True (link established
        #     and handed to LXMF Router; closer to "delivered" than HTTP).
        #   R→M delivered = persistent queue accepted enqueue OR direct
        #     send_text_direct() POST returned True (radio accepted packet,
        #     not LoRa-ack confirmed — wiring true acks is deferred).
        self.stats = {
            'messages_mesh_to_rns': 0,    # legacy alias for mesh_to_rns_delivered
            'messages_rns_to_mesh': 0,    # legacy alias for rns_to_mesh_delivered
            'mesh_to_rns_attempted': 0,
            'mesh_to_rns_delivered': 0,
            'mesh_to_rns_dropped': 0,
            'rns_to_mesh_attempted': 0,
            'rns_to_mesh_delivered': 0,
            'rns_to_mesh_dropped': 0,
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

        # MQTT filtering configuration
        self._filter_mqtt_messages = False  # Set True to drop MQTT-originated messages

        # Initialize Meshtastic handler based on bridge mode
        # MQTT bridge (recommended): zero interference with web client
        # TCP bridge (legacy): holds persistent connection, blocks web client

        # Initialize failover manager for dual-radio (crash detection + watchdog)
        self._failover_manager = None
        if HAS_FAILOVER and getattr(self.config.meshtastic, 'failover_enabled', False):
            fo_config = FailoverConfig(
                enabled=True,
                utilization_threshold=getattr(
                    self.config.meshtastic, 'failover_utilization_threshold', 25.0
                ),
                utilization_duration=getattr(
                    self.config.meshtastic, 'failover_utilization_duration', 30
                ),
                recovery_threshold=getattr(
                    self.config.meshtastic, 'failover_recovery_threshold', 15.0
                ),
                recovery_duration=getattr(
                    self.config.meshtastic, 'failover_recovery_duration', 60
                ),
                health_poll_interval=getattr(
                    self.config.meshtastic, 'failover_health_poll_interval', 5.0
                ),
                watchdog_enabled=getattr(
                    self.config.meshtastic, 'failover_watchdog_enabled', True
                ),
                restart_after_failures=getattr(
                    self.config.meshtastic, 'failover_restart_after_failures', 5
                ),
                max_restarts_per_hour=getattr(
                    self.config.meshtastic, 'failover_max_restarts_per_hour', 3
                ),
                restart_cooldown=getattr(
                    self.config.meshtastic, 'failover_restart_cooldown', 60
                ),
                primary_service=getattr(
                    self.config.meshtastic, 'failover_primary_service', 'meshtasticd'
                ),
                secondary_service=getattr(
                    self.config.meshtastic, 'failover_secondary_service', 'meshtasticd-alt'
                ),
            )
            self._failover_manager = FailoverManager(fo_config)
            self._failover_manager.start()
            logger.info("Radio failover manager enabled (watchdog=%s)",
                       "on" if fo_config.watchdog_enabled else "off")

        # Initialize TX load balancer if configured for dual-radio
        # Pass failover_manager so LB defers to failover state
        self._load_balancer = None
        if HAS_LOAD_BALANCER and getattr(self.config.meshtastic, 'load_balancer_enabled', False):
            lb_config = LoadBalancerConfig(
                enabled=True,
                tx_threshold=getattr(self.config.meshtastic, 'load_balancer_tx_threshold', 10.0),
                tx_max=getattr(self.config.meshtastic, 'load_balancer_tx_max', 20.0),
                health_poll_interval=getattr(
                    self.config.meshtastic, 'load_balancer_health_poll_interval', 5.0
                ),
                recovery_margin=getattr(
                    self.config.meshtastic, 'load_balancer_recovery_margin', 2.0
                ),
            )
            self._load_balancer = RadioLoadBalancer(
                lb_config,
                failover_manager=self._failover_manager,
            )
            self._load_balancer.start()
            logger.info("TX load balancer enabled (failover_aware=%s)",
                       "yes" if self._failover_manager else "no")

        # Initialize cross-gateway heartbeat if configured
        self._heartbeat = None
        if HAS_HEARTBEAT and getattr(self.config.meshtastic, 'gateway_heartbeat_enabled', False):
            hb_config = HeartbeatConfig(
                enabled=True,
                mqtt_broker=getattr(self.config.meshtastic, 'gateway_heartbeat_broker', 'localhost'),
                mqtt_port=getattr(self.config.meshtastic, 'gateway_heartbeat_port', 1883),
                heartbeat_interval=getattr(
                    self.config.meshtastic, 'gateway_heartbeat_interval', 15.0
                ),
                missed_heartbeats_threshold=getattr(
                    self.config.meshtastic, 'gateway_heartbeat_missed_threshold', 4
                ),
                role=getattr(self.config.meshtastic, 'gateway_role', 'primary'),
                gateway_id=getattr(self.config.meshtastic, 'gateway_id', ''),
            )
            self._heartbeat = GatewayHeartbeat(config=hb_config)
            self._heartbeat.start()
            logger.info("Cross-gateway heartbeat enabled (role=%s)", hb_config.role)

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
                load_balancer=self._load_balancer,
                persistent_queue=self._persistent_queue,
            )
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
        else:
            raise ImportError(
                "No Meshtastic handler available. Install paho-mqtt for MQTT bridge "
                "(recommended) or meshtastic Python library for legacy TCP bridge."
            )

        # Register Meshtastic sender now that handler exists.
        # NOTE: Issue #40 (2026-04-21) routed R→M through send_text_direct()
        # via destination="meshtastic"; the historical destination="mqtt"
        # path (publish_to_mqtt) was deleted as Hardening E.
        if self._persistent_queue:
            self._persistent_queue.register_sender(
                "meshtastic", self._mesh_handler.queue_send
            )
            # Hardening B: M→R in-memory overflow spills here under
            # destination="rns_xform"; the worker re-runs the message
            # through _process_mesh_to_rns so it gets fan-out to all
            # configured LXMF destinations and proper stats accounting.
            self._persistent_queue.register_sender(
                "rns_xform", self._dispatch_rns_xform_spill
            )

        # Initialize MeshCore handler if configured and available
        meshcore_config = getattr(self.config, 'meshcore', None)
        if HAS_MESHCORE and meshcore_config and meshcore_config.enabled:
            logger.info("Initializing MeshCore handler")
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
            # Register MeshCore sender with persistent queue
            if self._persistent_queue:
                self._persistent_queue.register_sender(
                    "meshcore", self._meshcore_handler.queue_send
                )
            # Tell health monitor that MeshCore is enabled
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

        # Issue #42: reconcile TX channel index with mqtt_bridge.channel name.
        # In mqtt_bridge mode, RX arrives via MQTT topic .../<name>/# and TX
        # uses a numeric index. If the two disagree, RNS→Mesh lands on the
        # wrong channel. Resolve once at startup from the radio's own channel
        # list. Refuse-loud (ChannelResolutionError) when a bridge name is
        # configured but cannot be confirmed — see Issue #46.
        from ._channel_resolver import apply_resolved_channel, ChannelResolutionError
        try:
            apply_resolved_channel(self.config)
        except ChannelResolutionError as e:
            logger.error(f"Refusing to start bridge: {e}")
            return False
        except Exception as e:
            logger.warning(f"TX channel resolution failed: {e}")

        # Hardening F: refuse-loud on rpc_key drift between rnsd and the
        # gateway's RNS client config. Issue #41 was field-discovered after
        # weeks of silent inbound-AuthError loss; catching the misalignment
        # at startup turns it into a 5-second failure instead of a 5-week
        # mystery. Pure config-file read; no service-level side effects.
        try:
            from utils.rns_alignment import check_gateway_rpc_key_alignment
            drift = check_gateway_rpc_key_alignment()
            if drift:
                logger.error(f"Refusing to start bridge: {drift}")
                return False
        except Exception as e:
            # Don't let the preflight itself crash bridge startup — a
            # missing helper or unreadable file should warn, not refuse.
            logger.warning(f"rpc_key preflight check failed (continuing): {e}")

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
            self._mesh_thread = threading.Thread(
                target=self._meshtastic_loop,
                daemon=True,
                name="MeshtasticBridge"
            )
            self._mesh_thread.start()

            self._rns_thread = threading.Thread(
                target=self._rns_loop,
                daemon=True,
                name="RNSBridge"
            )
            self._rns_thread.start()

            self._bridge_thread = threading.Thread(
                target=self._bridge_loop,
                daemon=True,
                name="MessageBridge"
            )
            self._bridge_thread.start()

        # Start MeshCore handler thread if initialized
        if self._meshcore_handler:
            self._meshcore_thread = threading.Thread(
                target=self._meshcore_loop,
                daemon=True,
                name="MeshCoreBridge"
            )
            self._meshcore_thread.start()
            logger.info("MeshCore handler thread started")

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

        # Hardening A: bridge channel deployment diagnostic. Watches the
        # MQTT handler's _last_uplink_at and logs a one-shot WARN if no
        # uplink ever arrives within the configured window — the silent
        # symptom shape behind moc3's 8h frozen-stats stall (channel
        # exists on the radio but fleet clients are publishing on a
        # different channel).
        self._channel_diagnostic_thread = threading.Thread(
            target=self._channel_diagnostic_loop,
            daemon=True,
            name="ChannelDiagnostic",
        )
        self._channel_diagnostic_thread.start()

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
                        self._bridge_thread, self._meshcore_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop WebSocket server
        self._stop_websocket_server()

        # Stop TX load balancer
        if self._load_balancer:
            self._load_balancer.stop()

        # Stop failover manager
        if self._failover_manager:
            self._failover_manager.stop()

        # Stop cross-gateway heartbeat
        if self._heartbeat:
            self._heartbeat.stop()

        # Stop RNS sniffer
        if HAS_RNS_SNIFFER:
            try:
                from monitoring.rns_sniffer import stop_rns_capture
                stop_rns_capture()
            except Exception as e:
                logger.debug(f"RNS sniffer stop error: {e}")

        logger.info("Bridge stopped")
        self._notify_status("stopped")

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

    def send_to_rns(
        self,
        message: str,
        destination_hash: bytes = None,
        title: str = None,
        fields: dict = None,
    ) -> bool:
        """Send a message to RNS network via LXMF.

        Optional ``title`` and ``fields`` let the caller carry structured
        sender identity (Mesh→RNS path uses this to surface the originating
        Meshtastic node's long_name + full id). When omitted, falls back
        to the legacy gateway-branded title with no extra fields — keeps
        the persistent-queue retry call site behavior unchanged.
        """
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
                hash_short = destination_hash.hex()[:8]
                # On-wedge composite: trip the circuit breaker for this
                # destination THEN run the default publish-+-counter hook.
                # The watchdog calls `os._exit(2)` after we return, so the
                # trip_open effect is process-local + brief — but it
                # still produces an observable side effect during the
                # abort window that tests with `exit_on_wedge=False`
                # rely on.
                def _on_wedge(label, target, timeout_s,
                              _hash=hash_short):
                    try:
                        if self._circuit_breaker is not None:
                            self._circuit_breaker.trip_open(
                                _hash, f"wedge:{label}"
                            )
                    except Exception:
                        pass
                    default_on_wedge(label, target, timeout_s)
                if not bounded_call("rnsd.has_path",
                                    RNS.Transport.has_path, destination_hash,
                                    target=hash_short,
                                    timeout_s=3.0,
                                    on_wedge=_on_wedge):
                    bounded_call("rnsd.request_path",
                                 RNS.Transport.request_path, destination_hash,
                                 target=hash_short,
                                 timeout_s=5.0,
                                 on_wedge=_on_wedge)
                    # Wait briefly for path (interruptible on shutdown)
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        if self._stop_event.wait(0.1):
                            break

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    return False

                dest_identity = bounded_call("rnsd.identity_recall",
                                             RNS.Identity.recall,
                                             destination_hash,
                                             target=hash_short,
                                             timeout_s=3.0,
                                             on_wedge=_on_wedge)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                logger.info(
                    "Broadcast to RNS dropped: set rns.default_lxmf_destination "
                    "in gateway config to route broadcasts to one (string) or "
                    "many (list) LXMF peer(s)"
                )
                return False

            lxm = bounded_call(
                "rnsd.lxmessage_ctor",
                LXMF.LXMessage,
                destination,
                self._lxmf_source,
                message,
                title or "MeshForge Gateway",
                fields=fields,
                target=hash_short,
                timeout_s=5.0,
                on_wedge=_on_wedge,
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

            bounded_call("rnsd.handle_outbound",
                         self._lxmf_router.handle_outbound, lxm,
                         target=hash_short,
                         timeout_s=15.0,
                         on_wedge=_on_wedge)
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
            hash_short = destination_hash.hex()[:8]

            def _on_wedge(label, target, timeout_s, _hash=hash_short):
                try:
                    if self._circuit_breaker is not None:
                        self._circuit_breaker.trip_open(
                            _hash, f"wedge:{label}"
                        )
                except Exception:
                    pass
                default_on_wedge(label, target, timeout_s)

            if not bounded_call("rnsd.has_path",
                                RNS.Transport.has_path, destination_hash,
                                target=hash_short,
                                timeout_s=3.0,
                                on_wedge=_on_wedge):
                bounded_call("rnsd.request_path",
                             RNS.Transport.request_path, destination_hash,
                             target=hash_short,
                             timeout_s=5.0,
                             on_wedge=_on_wedge)
                for _ in range(30):
                    if RNS.Transport.has_path(destination_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            if not RNS.Transport.has_path(destination_hash):
                return False

            dest_identity = bounded_call("rnsd.identity_recall",
                                         RNS.Identity.recall, destination_hash,
                                         target=hash_short,
                                         timeout_s=3.0,
                                         on_wedge=_on_wedge)
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT,
                RNS.Destination.SINGLE, "lxmf", "delivery"
            )

            lxm = bounded_call(
                "rnsd.lxmessage_ctor",
                LXMF.LXMessage,
                destination, self._lxmf_source, message, "MeshForge Gateway",
                target=hash_short, timeout_s=5.0, on_wedge=_on_wedge,
            )
            bounded_call("rnsd.handle_outbound",
                         self._lxmf_router.handle_outbound, lxm,
                         target=hash_short,
                         timeout_s=15.0,
                         on_wedge=_on_wedge)
            return True

        except Exception as e:
            logger.error(f"Queue send to RNS failed: {e}")
            return False

    def _dispatch_rns_xform_spill(self, payload: Dict) -> bool:
        """Persistent-queue sender for Hardening B's M→R spill.

        Reconstructs a BridgedMessage from the spilled payload and runs it
        through the standard _process_mesh_to_rns pipeline so it gets
        proper fan-out + attempted/delivered/dropped accounting. Returns
        True so the queue marks the row delivered after dispatch (the
        underlying xform will re-spill or count drops on its own failure
        paths — we don't re-loop overflow back into rns_xform).
        """
        try:
            metadata = payload.get('metadata') or {}
            msg = BridgedMessage(
                source_network="meshtastic",
                source_id=payload.get('source_id', '') or '',
                destination_id=payload.get('destination_id') or None,
                content=payload.get('content', '') or '',
                title=payload.get('title'),
                is_broadcast=bool(payload.get('is_broadcast', False)),
                metadata=dict(metadata),
            )
            self._process_mesh_to_rns(msg)
            return True
        except Exception as e:
            logger.error(f"rns_xform spill dispatch failed: {e}")
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
        """Main loop for Meshtastic connection - delegates to handler."""
        if self._mesh_handler:
            self._mesh_handler.run_loop()

    # _channel_diagnostic_loop() and _should_emit_channel_stall_warning()
    # inherited from BridgeAuxMixin (Hardening A).
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
                    # Periodic LXMF re-announce so late-joining RNS clients can
                    # discover this gateway. First announce fires in _setup_lxmf.
                    announce_interval = max(60, int(self.config.rns.announce_interval))
                    last = getattr(self, "_last_lxmf_announce", None)
                    if last is not None and (time.monotonic() - last) >= announce_interval:
                        try:
                            self._lxmf_router.announce(self._lxmf_source.hash)
                            self._last_lxmf_announce = time.monotonic()
                            logger.info("LXMF re-announce sent (dest=%s)",
                                        self._lxmf_source.hash.hex())
                        except Exception as e:
                            logger.warning("LXMF re-announce failed: %s", e)

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

    # RNS connection lifecycle methods provided by RNSConnectionMixin:
    # _suppress_signal_in_thread, _init_rns_main_thread, _connect_rns,
    # _setup_lxmf, _disconnect_rns

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
                        # LXMessage.content can be either bytes (binary LXMF
                        # payload) or str — encode only when we got text.
                        # (Issue #1162: 'bytes'.encode() raised, dropping the
                        # capture; delivery itself was unaffected.)
                        raw_content = message.content or b''
                        content_bytes = (
                            raw_content.encode('utf-8')
                            if isinstance(raw_content, str)
                            else raw_content
                        )
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

            # Pass through LXMF fields so the xform layer can inspect
            # meshforge_* namespace (Issue #39 attribution + relay-on-receive
            # origin marker). LXMF.fields is dict-or-None; normalize to dict.
            lxmf_fields = getattr(message, 'fields', None) or {}

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=message.content,
                title=message.title,
                metadata={
                    'lxmf_stamp': message.stamp,
                    'lxmf_fields': lxmf_fields,
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

    # _process_mesh_to_rns / _get_rns_destination / _requeue_failed_message
    # / _resolve_mesh_destination / _process_rns_to_mesh inherited from
    # MessageTransformMixin — see _rns_bridge_xform.py
    # _process_meshcore_to_bridge / _process_bridge_to_meshcore inherited
    # from MeshCoreBridgeMixin — see meshcore_bridge_mixin.py

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

    # _start_websocket_server / _stop_websocket_server inherited from
    # BridgeAuxMixin (web UI broadcast lifecycle).

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
