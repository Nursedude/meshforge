"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks
"""

import re
import threading
import time
import logging
import subprocess
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
    BridgeStatus, MessageOrigin
)

# Import circuit breaker for destination-level failure handling
try:
    from .circuit_breaker import CircuitBreakerRegistry
    HAS_CIRCUIT_BREAKER = True
except ImportError:
    HAS_CIRCUIT_BREAKER = False
    CircuitBreakerRegistry = None

# Import persistent message queue for reliable delivery
try:
    from .message_queue import PersistentMessageQueue, MessagePriority
    HAS_PERSISTENT_QUEUE = True
except ImportError:
    HAS_PERSISTENT_QUEUE = False
    PersistentMessageQueue = None
    MessagePriority = None

# Import routing classifier with confidence scoring
try:
    from utils.classifier import (
        RoutingClassifier, RoutingCategory,
        create_routing_system, ClassificationResult
    )
    CLASSIFIER_AVAILABLE = True
except ImportError:
    CLASSIFIER_AVAILABLE = False

logger = logging.getLogger(__name__)

# Import centralized path utility
# get_real_user_home: for MeshForge-specific files (identity, LXMF storage)
# ReticulumPaths: for RNS config paths (mirrors RNS's own resolution)
import os
try:
    from utils.paths import get_real_user_home, ReticulumPaths
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')

    class ReticulumPaths:
        @classmethod
        def get_config_dir(cls) -> Path:
            if Path('/etc/reticulum/config').is_file():
                return Path('/etc/reticulum')
            home = get_real_user_home()
            xdg = home / '.config' / 'reticulum'
            if (xdg / 'config').is_file():
                return xdg
            return home / '.reticulum'

        @classmethod
        def get_config_file(cls) -> Path:
            return cls.get_config_dir() / 'config'

# Import service checker for pre-flight checks (Issue #3)
try:
    from utils.service_check import check_service, ServiceState
    HAS_SERVICE_CHECK = True
except ImportError:
    HAS_SERVICE_CHECK = False
    check_service = None
    ServiceState = None

# Import event bus for RX message notifications (Issue #17 Phase 3)
try:
    from utils.event_bus import emit_message
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    emit_message = None

# Import RNS sniffer for Wireshark-grade packet capture
try:
    from monitoring.rns_sniffer import (
        get_rns_sniffer, RNSPacketInfo, RNSPacketType,
        start_rns_capture, integrate_with_traffic_inspector
    )
    HAS_RNS_SNIFFER = True
except ImportError:
    HAS_RNS_SNIFFER = False
    get_rns_sniffer = None
    RNSPacketInfo = None
    RNSPacketType = None


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


class RNSMeshtasticBridge:
    """
    Main gateway bridge between RNS and Meshtastic networks.

    Supports two modes:
    1. RNS Over Meshtastic - Uses Meshtastic as RNS transport layer
    2. Message Bridge - Translates messages between separate networks
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.node_tracker = UnifiedNodeTracker()

        # State
        self._running = False
        self._websocket_started = False
        self._connected_mesh = False
        self._connected_rns = False
        self._rns_via_rnsd = False  # True when rnsd handles RNS (bridge defers)
        self._rns_init_failed_permanently = False  # True if RNS can't be initialized from this thread
        self._rns_pre_initialized = False  # True if RNS was initialized from main thread

        # Reconnection strategies (exponential backoff with jitter)
        self._mesh_reconnect = ReconnectStrategy.for_meshtastic()
        self._rns_reconnect = ReconnectStrategy.for_rns()
        self._stop_event = threading.Event()

        # Health monitoring
        self.health = BridgeHealthMonitor()

        # LXMF delivery confirmation tracking
        self.delivery_tracker = DeliveryTracker()

        # Message queues (bounded to prevent memory exhaustion)
        self._mesh_to_rns_queue = Queue(maxsize=1000)
        self._rns_to_mesh_queue = Queue(maxsize=1000)

        # Threads
        self._mesh_thread = None
        self._rns_thread = None
        self._bridge_thread = None

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

        # Meshtastic interface and connection manager
        self._mesh_interface = None
        self._conn_manager = None
        self._pubsub_handler = None

        # Statistics
        self.stats = {
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
            'start_time': None,
        }

        # Persistent message queue for reliable delivery
        self._persistent_queue = None
        if HAS_PERSISTENT_QUEUE:
            try:
                self._persistent_queue = PersistentMessageQueue()
                # Register send handlers for each destination
                self._persistent_queue.register_sender(
                    "meshtastic", self._queue_send_meshtastic
                )
                self._persistent_queue.register_sender(
                    "rns", self._queue_send_rns
                )
                logger.info("Persistent message queue initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize persistent queue: {e}")

        # Pre-compile routing rule regexes to avoid re-compilation per message
        # and catch invalid patterns at startup rather than at runtime
        self._compiled_rules = self._compile_routing_rules()

        # Routing classifier with confidence scoring
        self._classifier = None
        self._last_classification: Optional[ClassificationResult] = None
        if CLASSIFIER_AVAILABLE:
            fixes_path = get_real_user_home() / '.config' / 'meshforge' / 'routing_fixes.json'
            rules = [
                {
                    'name': rule.name,
                    'enabled': rule.enabled,
                    'direction': rule.direction,
                    'source_filter': rule.source_filter,
                    'dest_filter': rule.dest_filter,
                    'message_filter': rule.message_filter,
                    'priority': rule.priority
                }
                for rule in self.config.routing_rules
            ]
            self._classifier = create_routing_system(
                rules=rules,
                bounce_threshold=0.3,
                fixes_path=fixes_path
            )
            logger.info("Routing classifier initialized with confidence scoring")

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

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected_mesh or self._connected_rns

    @property
    def bridge_status(self) -> BridgeStatus:
        """Get current bridge operational status."""
        return self.health.get_bridge_status()

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
                logger.debug(f"Could not start RNS sniffer: {e}")

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
        self._disconnect_meshtastic()
        self._disconnect_rns()

        # Wait for threads
        for thread in [self._mesh_thread, self._rns_thread, self._bridge_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        # Stop WebSocket server
        self._stop_websocket_server()

        # Stop RNS sniffer
        if HAS_RNS_SNIFFER:
            try:
                from monitoring.rns_sniffer import stop_rns_capture
                stop_rns_capture()
            except Exception:
                pass

        logger.info("Bridge stopped")
        self._notify_status("stopped")

    def get_status(self) -> dict:
        """Get current bridge status"""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        return {
            'running': self._running,
            'enabled': self.config.enabled,
            'meshtastic_connected': self._connected_mesh,
            'rns_connected': self._connected_rns,
            'rns_via_rnsd': self._rns_via_rnsd,
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'node_stats': self.node_tracker.get_stats(),
        }

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send a message to Meshtastic network"""
        if not self._connected_mesh:
            logger.warning("Not connected to Meshtastic")
            return False

        try:
            if self._mesh_interface:
                # For broadcasts, use ^all instead of None
                dest = destination if destination else "^all"
                logger.info(f"Sending to Meshtastic: dest={dest}, ch={channel}, msg={message[:50]}")
                self._mesh_interface.sendText(
                    message,
                    destinationId=dest,
                    channelIndex=channel
                )
                return True
            else:
                # Fallback to CLI
                return self._send_via_cli(message, destination, channel)
        except Exception as e:
            logger.error(f"Failed to send to Meshtastic: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
            return False

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

    def _queue_send_meshtastic(self, payload: Dict) -> bool:
        """Send handler for persistent queue - Meshtastic destination."""
        message = payload.get('message', '')
        destination = payload.get('destination')
        channel = payload.get('channel', 0)

        if not self._connected_mesh:
            return False

        try:
            if self._mesh_interface:
                dest = destination if destination else "^all"
                self._mesh_interface.sendText(message, destinationId=dest, channelIndex=channel)
                return True
            return False
        except Exception as e:
            logger.error(f"Queue send to Meshtastic failed: {e}")
            return False

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

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages"""
        with self._callbacks_lock:
            self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes"""
        with self._callbacks_lock:
            self._status_callbacks.append(callback)

    def test_connection(self) -> dict:
        """Test connectivity to both networks"""
        results = {
            'meshtastic': {'connected': False, 'error': None},
            'rns': {'connected': False, 'error': None},
        }

        # Test Meshtastic
        try:
            if self._test_meshtastic():
                results['meshtastic']['connected'] = True
        except Exception as e:
            results['meshtastic']['error'] = str(e)

        # Test RNS
        try:
            if self._test_rns():
                results['rns']['connected'] = True
        except Exception as e:
            results['rns']['error'] = str(e)

        return results

    # ========================================
    # Private Methods
    # ========================================

    def _meshtastic_loop(self):
        """Main loop for Meshtastic connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Records events to BridgeHealthMonitor for metrics.
        """
        while self._running:
            try:
                if not self._connected_mesh:
                    if not self._mesh_reconnect.should_retry():
                        logger.warning("Meshtastic reconnection: max attempts reached, resetting")
                        self._mesh_reconnect.reset()
                        self._stop_event.wait(self._mesh_reconnect.config.max_delay)
                        continue

                    logger.info(f"Attempting Meshtastic connection "
                               f"(attempt {self._mesh_reconnect.attempts + 1})...")
                    self.health.record_connection_event("meshtastic", "retry")
                    self._connect_meshtastic()

                    if self._connected_mesh:
                        self._mesh_reconnect.record_success()
                        self.health.record_connection_event("meshtastic", "connected")
                        logger.info("Meshtastic connection established")
                    else:
                        self._mesh_reconnect.record_failure()
                        self._mesh_reconnect.wait(self._stop_event)
                        continue

                if self._connected_mesh:
                    self._poll_meshtastic()

                self._stop_event.wait(1)

            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                category = self.health.record_error("meshtastic", e)
                logger.warning(f"Meshtastic connection error ({category}): {e}")
                self._handle_connection_lost()
                self.health.record_connection_event("meshtastic", "disconnected", str(e))
                self._mesh_reconnect.record_failure()
                self._mesh_reconnect.wait(self._stop_event)
            except Exception as e:
                category = self.health.record_error("meshtastic", e)
                logger.error(f"Meshtastic loop error ({category}): {e}")
                self._connected_mesh = False
                self.health.record_connection_event("meshtastic", "error", str(e))
                self._mesh_reconnect.record_failure()
                self._mesh_reconnect.wait(self._stop_event)

    def _rns_loop(self):
        """Main loop for RNS connection with auto-reconnect.

        Uses ReconnectStrategy for exponential backoff with jitter.
        Respects permanent failure flag for non-retriable errors.
        """
        while self._running:
            try:
                # Don't retry if RNS init failed permanently (e.g., signal handler issue)
                if self._rns_init_failed_permanently:
                    self._stop_event.wait(30)
                    continue

                if not self._connected_rns:
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
                else:
                    self._rns_reconnect.record_failure()
                    self._rns_reconnect.wait(self._stop_event)

    def _bridge_loop(self):
        """Main loop for message bridging"""
        loop_count = 0
        while self._running:
            try:
                # Process Meshtastic → RNS queue
                try:
                    msg = self._mesh_to_rns_queue.get(timeout=0.1)
                    self._process_mesh_to_rns(msg)
                except Empty:
                    pass

                # Process RNS → Meshtastic queue
                try:
                    msg = self._rns_to_mesh_queue.get(timeout=0.1)
                    self._process_rns_to_mesh(msg)
                except Empty:
                    pass

                # Periodically check delivery timeouts (~every 30s)
                loop_count += 1
                if loop_count % 150 == 0:
                    self.delivery_tracker.check_timeouts()

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                self._stop_event.wait(1)

    def _connect_meshtastic(self):
        """Connect to Meshtastic via TCP using singleton connection manager"""
        try:
            from pubsub import pub
            from utils.meshtastic_connection import get_connection_manager

            host = self.config.meshtastic.host
            port = self.config.meshtastic.port

            logger.info(f"Connecting to Meshtastic at {host}:{port}")

            # Use singleton connection manager to prevent connection conflicts
            # meshtasticd only allows ONE TCP client - this ensures we share
            self._conn_manager = get_connection_manager(host, port)

            # Acquire persistent connection (stays open for message receiving)
            if not self._conn_manager.acquire_persistent(owner="gateway_bridge"):
                logger.error("Could not acquire persistent Meshtastic connection")
                self._connected_mesh = False
                return

            # Get the interface for operations
            self._mesh_interface = self._conn_manager.get_interface()

            if self._mesh_interface is None:
                logger.error("Failed to get Meshtastic interface from connection manager")
                self._connected_mesh = False
                return

            # Subscribe to messages (store reference for proper unsubscribe)
            def on_receive(packet, interface):
                self._on_meshtastic_receive(packet)

            self._pubsub_handler = on_receive
            pub.subscribe(self._pubsub_handler, "meshtastic.receive")

            # Get initial node list
            self._update_meshtastic_nodes()

            self._connected_mesh = True
            logger.info("Connected to Meshtastic via connection manager")
            self._notify_status("meshtastic_connected")

        except ImportError as e:
            logger.warning(f"Meshtastic/connection manager not available: {e}, using CLI fallback")
            self._connected_mesh = self._test_meshtastic_cli()
        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._connected_mesh = False

    def _disconnect_meshtastic(self):
        """Disconnect from Meshtastic via connection manager"""
        # Release persistent connection through the manager
        if hasattr(self, '_conn_manager') and self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing persistent connection: {e}")
        self._mesh_interface = None
        self._connected_mesh = False

    def _init_rns_main_thread(self):
        """Pre-initialize RNS from the main thread.

        RNS.Reticulum() registers signal handlers that only work in the
        main thread. If we defer to the background _rns_loop thread,
        initialization fails with 'signal only works in main thread'.

        When rnsd is running, we connect as a client to its shared instance.
        RNS's own config resolution finds the config at:
          /etc/reticulum/ -> ~/.config/reticulum/ -> ~/.reticulum/
        When running as root (via sudo or systemd), ~ = /root/.
        """
        import threading as _threading
        if _threading.current_thread() is not _threading.main_thread():
            logger.warning("RNS pre-init skipped (not main thread)")
            return

        try:
            import RNS
        except ImportError:
            logger.info("RNS not installed, will be handled in _connect_rns")
            return

        from utils.gateway_diagnostic import find_rns_processes
        rns_pids = find_rns_processes()

        # Determine config directory: explicit config > RNS default resolution
        config_dir = self.config.rns.config_dir or None
        if config_dir:
            logger.info(f"Using explicit RNS config dir: {config_dir}")
        else:
            # Let RNS resolve its own config path (matches rnsd's resolution)
            rns_config = ReticulumPaths.get_config_file()
            logger.info(f"RNS config path: {rns_config} "
                       f"(exists: {rns_config.exists()})")

        try:
            if rns_pids:
                # rnsd running - RNS will auto-connect to shared instance
                # as long as the config has share_instance = Yes
                logger.info(f"rnsd detected (PID: {rns_pids[0]}), "
                           "initializing RNS as shared instance client")
                self._rns_via_rnsd = True

            # Initialize RNS - let it use its own config resolution
            # When rnsd is running with share_instance=Yes, RNS auto-connects
            # to the shared instance via LocalInterface (domain socket/TCP 37428)
            self._reticulum = RNS.Reticulum(configdir=config_dir)

            self._rns_pre_initialized = True
            logger.info("RNS pre-initialized from main thread")
        except OSError as e:
            if hasattr(e, 'errno') and e.errno == 98:
                logger.warning(f"RNS port conflict during pre-init: {e}")
                # Will be retried in _connect_rns() background thread
            elif "reinitialise" in str(e).lower() or "already running" in str(e).lower():
                # RNS singleton already exists (node_tracker initialized it)
                self._rns_pre_initialized = True
                logger.info("RNS already initialized (node tracker), "
                           "bridge will use existing instance")
            else:
                logger.warning(f"RNS pre-init failed: {e}")
        except Exception as e:
            err_msg = str(e).lower()
            if "reinitialise" in err_msg or "already running" in err_msg:
                self._rns_pre_initialized = True
                logger.info("RNS already initialized (node tracker), "
                           "bridge will use existing instance")
            else:
                logger.warning(f"RNS pre-init failed: {e}")

    def _connect_rns(self):
        """Initialize RNS and LXMF.

        If RNS was pre-initialized from the main thread (via _init_rns_main_thread),
        skips Reticulum initialization and proceeds directly to LXMF setup.
        Otherwise falls back to initialization here (may fail in background thread
        due to signal handler requirements).
        """
        try:
            import RNS
            import LXMF

            # If RNS was pre-initialized from main thread, skip to LXMF setup
            if self._rns_pre_initialized:
                logger.info("RNS pre-initialized, proceeding to LXMF setup")
            else:
                # RNS was NOT pre-initialized - try here (fallback path)
                # This may fail with 'signal only works in main thread'
                from utils.gateway_diagnostic import find_rns_processes
                rns_pids = find_rns_processes()

                if rns_pids:
                    logger.info(f"rnsd detected (PID: {rns_pids[0]}), deferring RNS to rnsd")
                    logger.info("Bridge RNS features deferred - rnsd handles transport")
                    self._reticulum = None
                    self._connected_rns = False
                    self._rns_via_rnsd = True
                    self._rns_init_failed_permanently = True
                    return
                else:
                    config_dir = self.config.rns.config_dir or None
                    try:
                        self._reticulum = RNS.Reticulum(configdir=config_dir)
                    except OSError as e:
                        if hasattr(e, 'errno') and e.errno == 98:
                            from utils.gateway_diagnostic import handle_address_in_use_error
                            diag = handle_address_in_use_error(e, logger)

                            self._reticulum = None
                            self._connected_rns = False

                            if diag['rns_pids']:
                                logger.info(f"rnsd now detected (PID: {diag['rns_pids'][0]}), deferring to rnsd")
                                self._rns_via_rnsd = True
                                self._rns_init_failed_permanently = True
                            else:
                                logger.warning("RNS port in use by unknown process (stale socket?)")
                                logger.info("Will retry after backoff - port may become available")
                            return
                        else:
                            raise
                    except Exception as e:
                        if "reinitialise" in str(e).lower() or "already running" in str(e).lower():
                            logger.info("RNS already running in this process, using shared instance")
                            # RNS singleton is active - proceed to LXMF setup
                        else:
                            raise

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
            logger.info("Connected to RNS")
            self._notify_status("rns_connected")

        except ImportError:
            logger.warning("RNS library not installed")
            self._connected_rns = False
            self._rns_init_failed_permanently = True  # Don't retry
        except Exception as e:
            error_msg = str(e).lower()
            if "signal only works in main thread" in error_msg:
                # RNS must be initialized from main thread - don't retry from background thread
                logger.warning("RNS must be initialized from main thread (run rnsd separately)")
                self._rns_init_failed_permanently = True  # Don't retry
            elif "reinitialise" in error_msg or "already running" in error_msg:
                # RNS singleton already exists - don't retry
                logger.info("RNS already initialized elsewhere, skipping gateway RNS init")
                self._rns_init_failed_permanently = True  # Don't retry
            else:
                logger.error(f"Failed to initialize RNS: {e}")
            self._connected_rns = False

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

    def _on_meshtastic_receive(self, packet: dict):
        """Handle incoming Meshtastic message"""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Update node info
            from_id = packet.get('fromId')
            if from_id:
                node = UnifiedNode.from_meshtastic({
                    'num': int(from_id[1:], 16) if from_id.startswith('!') else 0,
                    'snr': packet.get('rxSnr'),
                    'hopsAway': packet.get('hopStart', 0) - packet.get('hopLimit', 0),
                })
                self.node_tracker.add_node(node)

            # Extract relay node (Meshtastic 2.6+)
            # relayNode is the last byte of the node ID that relayed this packet
            relay_node = packet.get('relayNode')
            if relay_node and relay_node > 0:
                self._discover_relay_node(relay_node, from_id, packet)

            # Handle text messages
            if portnum == 'TEXT_MESSAGE_APP':
                payload = decoded.get('payload', b'')
                if isinstance(payload, bytes):
                    text = payload.decode('utf-8', errors='ignore')
                else:
                    text = str(payload)

                msg = BridgedMessage(
                    source_network="meshtastic",
                    source_id=from_id,
                    destination_id=packet.get('toId'),
                    content=text,
                    is_broadcast=packet.get('toId') == '!ffffffff',
                    metadata={
                        'channel': packet.get('channel', 0),
                        'snr': packet.get('rxSnr'),
                    }
                )

                # Store incoming message for UI/history
                try:
                    from commands import messaging
                    to_id = packet.get('toId')
                    # Convert broadcast marker to None
                    if to_id == '!ffffffff' or to_id == '^all':
                        to_id = None
                    messaging.store_incoming(
                        from_id=from_id,
                        content=text,
                        network="meshtastic",
                        to_id=to_id,
                        channel=packet.get('channel', 0),
                        snr=packet.get('rxSnr'),
                        rssi=packet.get('rxRssi'),
                    )
                except Exception as e:
                    logger.debug(f"Could not store incoming message: {e}")

                # Broadcast to WebSocket for real-time web UI updates
                try:
                    from utils.websocket_server import broadcast_message
                    broadcast_message({
                        'from_id': from_id,
                        'to_id': to_id,
                        'content': text,
                        'channel': packet.get('channel', 0),
                        'snr': packet.get('rxSnr'),
                        'rssi': packet.get('rxRssi'),
                        'timestamp': datetime.now().isoformat(),
                        'is_broadcast': to_id is None,
                    })
                except ImportError:
                    pass  # WebSocket server not available
                except Exception as e:
                    logger.debug(f"Could not broadcast to WebSocket: {e}")

                # Queue for bridging if enabled (non-blocking to prevent deadlock)
                if self._should_bridge(msg):
                    try:
                        self._mesh_to_rns_queue.put_nowait(msg)
                    except Full:
                        logger.warning("Mesh→RNS queue full, dropping message")
                        with self._stats_lock:
                            self.stats['errors'] += 1

                # Notify callbacks
                self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing Meshtastic message: {e}")

    def _discover_relay_node(self, relay_byte: int, from_id: str, packet: dict):
        """
        Discover relay node from Meshtastic 2.6+ relayNode field.

        The relayNode field only contains the last byte of the relay node's ID.
        We try to match it against known nodes or create a placeholder.
        """
        try:
            if relay_byte <= 0 or relay_byte > 255:
                return

            # Try to find existing node matching this last byte
            for node in self.node_tracker.get_meshtastic_nodes():
                if node.meshtastic_id:
                    try:
                        node_num = int(node.meshtastic_id[1:], 16)
                        if (node_num & 0xFF) == relay_byte:
                            # Found the relay node - update topology
                            if self._network_topology and from_id:
                                self._network_topology.add_edge(
                                    source_id=node.id,
                                    dest_id=from_id,
                                    hops=1,  # Direct relay relationship
                                    snr=packet.get('rxSnr'),
                                    rssi=packet.get('rxRssi'),
                                )
                            logger.debug(f"Relay path: {node.meshtastic_id} -> {from_id}")
                            return
                    except (ValueError, TypeError):
                        continue

            # No match - create partial relay node for tracking
            partial_id = f"!????{relay_byte:02x}"
            node = UnifiedNode(
                id=partial_id,
                name=f"Relay-{relay_byte:02x}",
                network="meshtastic",
                meshtastic_id=partial_id,
            )
            self.node_tracker.add_node(node)

            # Add topology edge from unknown relay to sender
            if self._network_topology and from_id:
                self._network_topology.add_edge(
                    source_id=partial_id,
                    dest_id=from_id,
                    hops=1,
                    snr=packet.get('rxSnr'),
                    rssi=packet.get('rxRssi'),
                )

            logger.info(f"Discovered relay node via packet routing: {partial_id}")

        except Exception as e:
            logger.debug(f"Error discovering relay node: {e}")

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
            if self._should_bridge(msg):
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

    def _should_bridge(self, msg: BridgedMessage) -> bool:
        """
        Check if message should be bridged based on routing rules.

        Uses confidence-scored classifier when available:
        - High confidence (>0.7): Route immediately
        - Low confidence (<0.3): Bounce to queue for review
        - Medium confidence: Route with logging
        """
        if not self.config.enabled:
            return False

        # Use classifier if available
        if self._classifier:
            return self._classify_message(msg)

        # Fallback to legacy logic
        return self._should_bridge_legacy(msg)

    def _classify_message(self, msg: BridgedMessage) -> bool:
        """Classify message using confidence-scored routing."""
        msg_id = f"{msg.source_network}:{msg.source_id}:{msg.timestamp.isoformat()}"

        result = self._classifier.classify(msg_id, {
            'source_network': msg.source_network,
            'source_id': msg.source_id,
            'destination_id': msg.destination_id,
            'content': msg.content,
            'is_broadcast': msg.is_broadcast,
            'metadata': msg.metadata
        })

        self._last_classification = result

        # Handle bounced messages
        if result.bounced:
            with self._stats_lock:
                self.stats['bounced'] += 1
            logger.info(
                f"Message bounced (confidence {result.confidence:.2f}): "
                f"{msg.source_id[:8]}... -> {result.bounce_reason}"
            )
            # Bounced messages go to queue category, don't bridge immediately
            return result.category == RoutingCategory.QUEUE.value

        # Log classification decision
        if result.confidence < 0.7:
            logger.debug(
                f"Routing decision (confidence {result.confidence:.2f}): "
                f"{result.category} - {result.reason}"
            )

        # Determine if we should bridge based on category
        if result.category == RoutingCategory.DROP.value:
            return False
        elif result.category in (RoutingCategory.BRIDGE_RNS.value, RoutingCategory.BRIDGE_MESH.value):
            return True
        elif result.category == RoutingCategory.QUEUE.value:
            # Queued items need manual review
            return False

        return False

    def _compile_routing_rules(self) -> dict:
        """Pre-compile regex patterns from routing rules at init time.

        Returns a dict mapping rule name to compiled filter patterns.
        Invalid patterns are logged and skipped — the rule will never match.
        """
        compiled = {}
        for rule in self.config.routing_rules:
            filters = {}
            for field in ('source_filter', 'dest_filter', 'message_filter'):
                pattern = getattr(rule, field, '')
                if pattern:
                    try:
                        filters[field] = re.compile(pattern)
                    except re.error as e:
                        logger.warning(
                            f"Invalid regex in rule '{rule.name}' "
                            f"field '{field}': {e} — rule will be skipped"
                        )
                        filters[field] = None  # Mark as broken
            compiled[rule.name] = filters
        return compiled

    # Maximum input length for regex matching to bound execution time
    _REGEX_INPUT_LIMIT = 512

    def _should_bridge_legacy(self, msg: BridgedMessage) -> bool:
        """Legacy routing logic (fallback when classifier unavailable)."""
        # Re-compile if routing rules changed since last compile
        current_names = {r.name for r in self.config.routing_rules}
        if current_names != set(self._compiled_rules.keys()):
            self._compiled_rules = self._compile_routing_rules()

        for rule in self.config.routing_rules:
            if not rule.enabled:
                continue

            # Check direction
            if msg.source_network == "meshtastic" and rule.direction == "rns_to_mesh":
                continue
            if msg.source_network == "rns" and rule.direction == "mesh_to_rns":
                continue

            # Get pre-compiled filters for this rule
            filters = self._compiled_rules.get(rule.name, {})

            # Skip rule entirely if any of its patterns failed to compile
            if any(v is None for v in filters.values()):
                continue

            # Apply pre-compiled regex filters with bounded input
            # Source filter
            if rule.source_filter:
                compiled = filters.get('source_filter')
                if not compiled or not msg.source_id:
                    continue
                if not compiled.search(msg.source_id[:self._REGEX_INPUT_LIMIT]):
                    continue

            # Destination filter
            if rule.dest_filter:
                compiled = filters.get('dest_filter')
                if not compiled:
                    continue
                dest = (msg.destination_id or "")[:self._REGEX_INPUT_LIMIT]
                if not compiled.search(dest):
                    continue

            # Message content filter
            if rule.message_filter:
                compiled = filters.get('message_filter')
                if not compiled or not msg.content:
                    continue
                if not compiled.search(msg.content[:self._REGEX_INPUT_LIMIT]):
                    continue

            # All filters passed - this rule matches
            return True

        return self.config.default_route in ("bidirectional", f"{msg.source_network}_to_*")

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing classifier statistics."""
        stats = dict(self.stats)
        if self._classifier:
            classifier_stats = self._classifier.get_stats()
            stats['classifier'] = classifier_stats
            stats['bouncer_queue'] = len(self._classifier.bouncer.get_queue())
        return stats

    def get_last_classification(self) -> Optional[Dict]:
        """Get the last classification result for debugging."""
        if self._last_classification:
            return self._last_classification.to_dict()
        return None

    def fix_routing(self, msg_id: str, correct_category: str) -> bool:
        """
        Record a user correction for routing decisions.

        This is the 'fix button' - allows users to correct mistakes
        and improve the system over time.
        """
        if not self._classifier or not self._classifier.fix_registry:
            return False

        # Create a dummy result for the fix
        result = ClassificationResult(
            input_id=msg_id,
            category="unknown",
            confidence=0.5
        )
        self._classifier.fix_registry.add_fix(result, correct_category)
        logger.info(f"Routing fix recorded: {msg_id} -> {correct_category}")
        return True

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

    def _requeue_failed_message(self, msg: BridgedMessage, destination: str) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send.
            destination: Target network ("meshtastic" or "rns").

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            self._persistent_queue.enqueue(
                payload={
                    'message': msg.content,
                    'source_id': msg.source_id,
                    'destination_id': msg.destination_id or "",
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

    def _update_meshtastic_nodes(self):
        """Update node tracker with Meshtastic nodes"""
        if not self._mesh_interface:
            return

        try:
            my_info = self._mesh_interface.getMyNodeInfo()
            my_id = my_info.get('num', 0)

            for node_id, node_data in self._mesh_interface.nodes.items():
                is_local = node_data.get('num') == my_id
                node = UnifiedNode.from_meshtastic(node_data, is_local=is_local)
                self.node_tracker.add_node(node)

        except Exception as e:
            logger.error(f"Error updating Meshtastic nodes: {e}")

    def _poll_meshtastic(self):
        """Poll Meshtastic for health check and updates"""
        # Check connection health - detect dropped connections early
        if self._mesh_interface:
            try:
                # Check if interface is still connected
                if hasattr(self._mesh_interface, 'isConnected'):
                    if not self._mesh_interface.isConnected:
                        logger.warning("Meshtastic connection lost (isConnected=False)")
                        self._handle_connection_lost()
                        return
                # Also check if we can access basic properties (catches broken pipes)
                if hasattr(self._mesh_interface, 'nodes'):
                    _ = len(self._mesh_interface.nodes)  # Triggers exception if dead
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"Meshtastic connection lost: {e}")
                self._handle_connection_lost()
                return
            except Exception as e:
                logger.debug(f"Meshtastic health check error: {e}")

    def _handle_connection_lost(self):
        """Handle lost meshtastic connection - cleanup and prepare for reconnect"""
        logger.info("Handling lost Meshtastic connection...")
        self._connected_mesh = False

        # Release the persistent connection properly
        if hasattr(self, '_conn_manager') and self._conn_manager:
            try:
                self._conn_manager.release_persistent()
            except Exception as e:
                logger.debug(f"Error releasing connection after loss: {e}")

        # Unsubscribe from pub/sub to avoid stale callbacks
        try:
            from pubsub import pub
            if self._pubsub_handler:
                pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                self._pubsub_handler = None
        except Exception:
            pass

        self._mesh_interface = None
        self._notify_status("meshtastic_disconnected")

        # Wait for cooldown before reconnect attempt
        try:
            from utils.meshtastic_connection import wait_for_cooldown
            wait_for_cooldown()
        except ImportError:
            self._stop_event.wait(2)  # Interruptible fallback cooldown

    def _test_meshtastic(self) -> bool:
        """Test Meshtastic connection"""
        sock = None
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((
                self.config.meshtastic.host,
                self.config.meshtastic.port
            ))
            return result == 0
        except (OSError, socket.error, socket.timeout) as e:
            logger.debug(f"Meshtastic connection test failed: {e}")
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception as e:
                    logger.debug(f"Socket close during cleanup: {e}")

    def _test_meshtastic_cli(self) -> bool:
        """Test Meshtastic CLI availability"""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if not cli_path:
                logger.debug("Meshtastic CLI not found")
                return False

            result = subprocess.run(
                [cli_path, '--info'],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"Meshtastic CLI test failed: {e}")
            return False

    def _test_rns(self) -> bool:
        """Test RNS availability"""
        try:
            import RNS
            return True
        except ImportError:
            return False

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send via Meshtastic CLI as fallback"""
        try:
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli() or 'meshtastic'
            cmd = [cli_path, '--host', self.config.meshtastic.host, '--sendtext', message]
            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def _notify_message(self, msg: BridgedMessage):
        """Notify message callbacks and emit to event bus (thread-safe snapshot).

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
                emit_message(
                    direction='rx',
                    content=msg.content,
                    node_id=msg.source_id or "",
                    node_name="",  # Could be enhanced with node lookup
                    channel=msg.metadata.get('channel', 0) if msg.metadata else 0,
                    network=msg.source_network,
                    raw_data={
                        'destination_id': msg.destination_id,
                        'is_broadcast': msg.is_broadcast,
                        'title': msg.title,
                        'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
                        'metadata': msg.metadata
                    }
                )
            except Exception as e:
                logger.debug(f"Event bus emit failed: {e}")

    def _start_websocket_server(self):
        """Start WebSocket server for real-time message broadcast to web UI."""
        try:
            from utils.websocket_server import start_websocket_server, is_websocket_available
            if is_websocket_available():
                if start_websocket_server(port=5001):
                    logger.info("WebSocket server started on port 5001")
                    self._websocket_started = True
                else:
                    logger.debug("WebSocket server failed to start")
            else:
                logger.debug("WebSocket not available (websockets library not installed)")
        except ImportError:
            logger.debug("WebSocket server module not available")
        except Exception as e:
            logger.debug(f"Could not start WebSocket server: {e}")

    def _stop_websocket_server(self):
        """Stop WebSocket server."""
        if getattr(self, '_websocket_started', False):
            try:
                from utils.websocket_server import stop_websocket_server
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

_active_bridge: Optional[RNSMeshtasticBridge] = None


def start_gateway_headless() -> bool:
    """
    Start the gateway bridge in headless mode (for CLI use).

    Returns True if started successfully, False otherwise.
    """
    global _active_bridge

    if _active_bridge is not None and _active_bridge._running:
        logger.warning("Gateway bridge is already running")
        print("Gateway bridge is already running")
        return True

    try:
        _active_bridge = RNSMeshtasticBridge()
        success = _active_bridge.start()

        if success:
            print("Gateway bridge started successfully")
            print(f"  Meshtastic: {'Connected' if _active_bridge._connected_mesh else 'Disconnected'}")
            print(f"  RNS: {'Connected' if _active_bridge._connected_rns else 'Disconnected'}")
        else:
            print("Gateway bridge failed to start - check logs")

        return success
    except Exception as e:
        logger.error(f"Failed to start gateway: {e}")
        print(f"Failed to start gateway: {e}")
        return False


def stop_gateway_headless() -> bool:
    """
    Stop the gateway bridge (for CLI use).

    Returns True if stopped successfully.
    """
    global _active_bridge

    if _active_bridge is None:
        print("No active gateway bridge to stop")
        return True

    try:
        _active_bridge.stop()
        _active_bridge = None
        print("Gateway bridge stopped")
        return True
    except Exception as e:
        logger.error(f"Error stopping gateway: {e}")
        print(f"Error stopping gateway: {e}")
        return False


def get_gateway_stats() -> dict:
    """
    Get current gateway statistics (for CLI use).

    Returns dict with bridge status and statistics.
    """
    global _active_bridge

    if _active_bridge is None:
        return {
            'running': False,
            'status': 'Not started',
            'meshtastic_connected': False,
            'rns_connected': False,
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
        }

    try:
        status = _active_bridge.get_status()
        stats = status.get('statistics', {})
        result = {
            'running': _active_bridge._running,
            'status': 'Running' if _active_bridge._running else 'Stopped',
            'meshtastic_connected': _active_bridge._connected_mesh,
            'rns_connected': _active_bridge._connected_rns,
            'messages_mesh_to_rns': stats.get('messages_mesh_to_rns', 0),
            'messages_rns_to_mesh': stats.get('messages_rns_to_mesh', 0),
            'errors': stats.get('errors', 0),
            'bounced': stats.get('bounced', 0),
            'uptime_seconds': status.get('uptime_seconds'),
        }
        # Include health metrics if available
        if hasattr(_active_bridge, 'health'):
            result['health'] = _active_bridge.health.get_summary()
        # Include delivery confirmation stats
        if hasattr(_active_bridge, 'delivery_tracker'):
            result['delivery'] = _active_bridge.delivery_tracker.get_stats()
        return result
    except Exception as e:
        logger.error(f"Error getting gateway stats: {e}")
        return {
            'running': False,
            'status': f'Error: {e}',
            'meshtastic_connected': False,
            'rns_connected': False,
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'bounced': 0,
        }


def is_gateway_running() -> bool:
    """Check if gateway bridge is currently running."""
    global _active_bridge
    return _active_bridge is not None and _active_bridge._running
