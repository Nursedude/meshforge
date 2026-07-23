"""
Cross-Gateway Failover via MQTT Heartbeat.

Enables two MeshForge gateways to coordinate failover using a shared
MQTT broker. Each gateway publishes periodic heartbeats; the peer monitors
them and promotes itself to active when the other goes silent.

Architecture:
    Gateway A (primary)  ──┐
                           ├── MQTT Broker ──> Heartbeat coordination
    Gateway B (secondary) ─┘

Protocol:
    Publish:   meshforge/gateway/{gateway_id}/heartbeat   (every 15s)
    Subscribe: meshforge/gateway/+/heartbeat              (all peers)
    LWT:       meshforge/gateway/{gateway_id}/status → "offline"

    Payload: {
        "id": "gw-rpi4-01",
        "role": "primary",
        "state": "active",
        "uptime": 3600,
        "health_score": 85,
        "failover_state": "primary_active",
        "timestamp": 1709683200
    }

Failover Logic:
    - Configured roles: primary or secondary (no election — prevents split-brain)
    - Secondary monitors primary heartbeats
    - missed_heartbeats_threshold consecutive misses → secondary promotes to active
    - MQTT Last Will and Testament (LWT) provides fast failure detection
    - When primary recovers (heartbeats resume), secondary demotes back to standby

Requires:
    - paho-mqtt library (pip install paho-mqtt)
    - Shared MQTT broker reachable by both gateways

Usage:
    from gateway.gateway_heartbeat import GatewayHeartbeat, HeartbeatConfig

    config = HeartbeatConfig(
        enabled=True,
        mqtt_broker="192.168.1.100",
        role="secondary",
    )
    hb = GatewayHeartbeat(config=config)
    hb.start()

    # Check peer status
    print(hb.get_status())  # {'peer_alive': True, 'peer_health': 85, ...}

    hb.stop()
"""

import json
import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from utils.safe_import import safe_import

_mqtt_client_mod, _HAS_PAHO = safe_import('paho.mqtt.client')

# EventBus for broadcasting gateway state changes
try:
    from utils.event_bus import emit_service_status
    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

# Health scorer for heartbeat payload
try:
    from utils.health_score import get_health_scorer
    _HAS_HEALTH_SCORER = True
except ImportError:
    _HAS_HEALTH_SCORER = False

# SharedHealthState for persistent state
try:
    from utils.shared_health_state import get_shared_health_state
    _HAS_SHARED_STATE = True
except ImportError:
    _HAS_SHARED_STATE = False

logger = logging.getLogger(__name__)


class GatewayRole(Enum):
    """Gateway role in the failover pair."""
    PRIMARY = "primary"
    SECONDARY = "secondary"


class GatewayState(Enum):
    """Current operational state of this gateway."""
    ACTIVE = "active"        # Currently handling TX
    STANDBY = "standby"      # Monitoring, not handling TX
    PROMOTING = "promoting"  # Peer down, transitioning to active
    DEMOTING = "demoting"    # Peer recovered, transitioning to standby
    DISABLED = "disabled"    # Heartbeat not enabled


@dataclass
class HeartbeatConfig:
    """Configuration for cross-gateway MQTT heartbeat."""
    enabled: bool = False

    # MQTT connection
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "meshforge/gateway"

    # Heartbeat timing
    heartbeat_interval: float = 15.0        # Seconds between heartbeats
    missed_heartbeats_threshold: int = 4    # Misses before declaring peer dead

    # Gateway identity
    gateway_id: str = ""                    # Auto-generated from hostname if empty
    role: str = "primary"                   # "primary" or "secondary"

    # MQTT connection resilience
    mqtt_keepalive: int = 60                # MQTT keepalive interval
    mqtt_reconnect_delay: float = 5.0       # Seconds before reconnect attempt


@dataclass
class PeerInfo:
    """Tracked state of a peer gateway."""
    gateway_id: str = ""
    role: str = ""
    state: str = ""
    uptime: float = 0.0
    health_score: float = 0.0
    failover_state: str = ""
    last_heartbeat: float = 0.0          # wall-clock (time.time) — for display only
    last_heartbeat_mono: float = 0.0     # monotonic anchor — drives liveness/failover
    missed_count: int = 0
    alive: bool = False


@dataclass
class HeartbeatEvent:
    """Record of a heartbeat-related event."""
    timestamp: datetime
    event_type: str   # "peer_down", "peer_recovered", "promoted", "demoted"
    detail: str


class GatewayHeartbeat:
    """
    MQTT-based heartbeat for cross-gateway failover coordination.

    Uses MQTT Last Will and Testament (LWT) for instant failure detection
    and periodic heartbeats for health monitoring. Role is configured
    (not elected) to prevent split-brain scenarios.
    """

    def __init__(
        self,
        config: Optional[HeartbeatConfig] = None,
        on_peer_down: Optional[Callable[[], None]] = None,
        on_peer_recovered: Optional[Callable[[], None]] = None,
        failover_manager: Optional[Any] = None,
    ):
        self._config = config or HeartbeatConfig()
        self._on_peer_down = on_peer_down
        self._on_peer_recovered = on_peer_recovered
        self._failover_manager = failover_manager

        # Auto-generate gateway ID from hostname if not set
        if not self._config.gateway_id:
            self._config.gateway_id = f"gw-{platform.node().split('.')[0]}"

        # Role
        self._role = GatewayRole(self._config.role)

        # State
        self._state = GatewayState.DISABLED
        if self._config.enabled:
            self._state = (GatewayState.ACTIVE if self._role == GatewayRole.PRIMARY
                          else GatewayState.STANDBY)
        self._state_lock = threading.Lock()

        # Peer tracking
        self._peers: Dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()

        # Event history
        self._events: List[HeartbeatEvent] = []
        self._max_events = 50

        # MQTT
        self._mqtt_client = None
        self._mqtt_connected = False
        self._start_time = time.time()
        self._last_mqtt_connect: float = 0.0

        # Guard against duplicate peer-down thread spawns
        self._pending_peer_down: set = set()
        self._pending_peer_down_lock = threading.Lock()

        # MQTT alert deduplication — suppress same (service:event) within 60s
        self._alert_dedup: Dict[str, float] = {}
        self._alert_dedup_window: float = 60.0

        # Threads
        self._stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._check_thread: Optional[threading.Thread] = None

    @property
    def state(self) -> GatewayState:
        """Current gateway state."""
        with self._state_lock:
            return self._state

    @property
    def gateway_id(self) -> str:
        """This gateway's unique ID."""
        return self._config.gateway_id

    @property
    def role(self) -> GatewayRole:
        """This gateway's configured role."""
        return self._role

    @property
    def is_active(self) -> bool:
        """Whether this gateway is currently the active TX handler."""
        with self._state_lock:
            return self._state == GatewayState.ACTIVE

    def start(self) -> None:
        """Start MQTT connection, heartbeat publishing, and peer monitoring."""
        if not self._config.enabled:
            logger.info("Gateway heartbeat disabled by configuration")
            return

        if not _HAS_PAHO:
            logger.warning("Gateway heartbeat requires paho-mqtt — disabled")
            self._state = GatewayState.DISABLED
            return

        self._stop_event.clear()
        self._connect_mqtt()
        self._start_threads()

        logger.info(
            "Gateway heartbeat started: id=%s, role=%s, broker=%s:%d, interval=%.0fs",
            self._config.gateway_id, self._role.value,
            self._config.mqtt_broker, self._config.mqtt_port,
            self._config.heartbeat_interval,
        )

    def stop(self) -> None:
        """Stop heartbeat and disconnect MQTT."""
        self._stop_event.set()

        # Publish offline status before disconnecting
        if self._mqtt_client and self._mqtt_connected:
            try:
                status_topic = (f"{self._config.mqtt_topic_prefix}/"
                               f"{self._config.gateway_id}/status")
                self._mqtt_client.publish(status_topic, "offline", retain=True)
            except Exception as e:
                logger.debug("Failed to publish offline status: %s", e)

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None
        if self._check_thread:
            self._check_thread.join(timeout=5)
            self._check_thread = None

        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
                self._mqtt_client.loop_stop()
            except Exception as e:
                logger.debug("MQTT disconnect error during stop: %s", e)
            self._mqtt_client = None

        logger.info("Gateway heartbeat stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive heartbeat status for TUI."""
        with self._state_lock:
            state = self._state

        with self._peers_lock:
            peers = {
                pid: {
                    'role': p.role,
                    'state': p.state,
                    'alive': p.alive,
                    'health_score': p.health_score,
                    'uptime': p.uptime,
                    'last_heartbeat': p.last_heartbeat,
                    'missed_count': p.missed_count,
                }
                for pid, p in self._peers.items()
            }

        return {
            'enabled': self._config.enabled,
            'gateway_id': self._config.gateway_id,
            'role': self._role.value,
            'state': state.value,
            'mqtt_connected': self._mqtt_connected,
            'peers': peers,
            'events': [
                {
                    'timestamp': e.timestamp.isoformat(),
                    'type': e.event_type,
                    'detail': e.detail,
                }
                for e in self._events[-10:]  # Last 10 events
            ],
        }

    # ── MQTT alerting ─────────────────────────────────────────────────

    def publish_alert(
        self,
        severity: str,
        service: str,
        event: str,
        reason: str,
    ) -> None:
        """Publish an alert to MQTT for remote NOC visibility.

        Alerts are deduplicated: the same (service, event) pair is suppressed
        for ``_alert_dedup_window`` seconds (default 60s).

        Args:
            severity: "critical", "warning", or "info"
            service: Service name (e.g., "radio_failover")
            event: Event type (e.g., "secondary_active", "down", "up")
            reason: Human-readable explanation
        """
        if not self._mqtt_connected or not self._mqtt_client:
            return

        # Dedup — suppress same (service, event) within window
        key = f"{service}:{event}"
        now = time.time()
        if key in self._alert_dedup and now - self._alert_dedup[key] < self._alert_dedup_window:
            return
        self._alert_dedup[key] = now

        topic = (f"{self._config.mqtt_topic_prefix}/"
                f"{self._config.gateway_id}/alerts")
        payload = json.dumps({
            'severity': severity,
            'service': service,
            'event': event,
            'reason': reason,
            'gateway_id': self._config.gateway_id,
            'timestamp': now,
        })
        try:
            self._mqtt_client.publish(topic, payload, qos=1)
        except Exception as e:
            logger.debug("Alert publish error: %s", e)

    def _classify_alert_severity(self, available: bool, message: str) -> str:
        """Classify alert severity from service event context.

        Returns:
            "critical", "warning", or "info"
        """
        if not available:
            return "critical"
        msg_lower = message.lower()
        if any(k in msg_lower for k in ("recovery", "pending", "rate limit", "saturated")):
            return "warning"
        return "info"

    # ── MQTT connection ────────────────────────────────────────────────

    def _connect_mqtt(self) -> None:
        """Connect to MQTT broker with LWT configured."""
        if not _HAS_PAHO:
            return

        client_id = f"meshforge-{self._config.gateway_id}"
        self._mqtt_client = _mqtt_client_mod.Client(client_id=client_id)

        # Authentication
        if self._config.mqtt_username:
            self._mqtt_client.username_pw_set(
                self._config.mqtt_username,
                self._config.mqtt_password,
            )

        # Last Will and Testament — published automatically if we disconnect unexpectedly
        lwt_topic = (f"{self._config.mqtt_topic_prefix}/"
                    f"{self._config.gateway_id}/status")
        self._mqtt_client.will_set(lwt_topic, payload="offline", qos=1, retain=True)

        # Callbacks
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self._mqtt_client.on_message = self._on_mqtt_message

        try:
            self._mqtt_client.connect(
                self._config.mqtt_broker,
                self._config.mqtt_port,
                keepalive=self._config.mqtt_keepalive,
            )
            self._mqtt_client.loop_start()
        except Exception as e:
            logger.error("MQTT connect failed: %s", e)

    def _on_mqtt_connect(self, client, userdata, flags, rc) -> None:
        """Handle MQTT connection established."""
        if rc == 0:
            self._mqtt_connected = True
            self._last_mqtt_connect = time.monotonic()
            logger.info("Gateway heartbeat MQTT connected to %s:%d",
                       self._config.mqtt_broker, self._config.mqtt_port)

            # Subscribe to all peer heartbeats
            topic = f"{self._config.mqtt_topic_prefix}/+/heartbeat"
            client.subscribe(topic, qos=1)

            # Subscribe to peer status (LWT)
            status_topic = f"{self._config.mqtt_topic_prefix}/+/status"
            client.subscribe(status_topic, qos=1)

            # Publish online status
            my_status_topic = (f"{self._config.mqtt_topic_prefix}/"
                              f"{self._config.gateway_id}/status")
            client.publish(my_status_topic, "online", retain=True)
        else:
            logger.error("Gateway heartbeat MQTT connect failed with rc=%d", rc)

    def _on_mqtt_disconnect(self, client, userdata, rc) -> None:
        """Handle MQTT disconnection."""
        self._mqtt_connected = False
        if rc != 0:
            logger.warning("Gateway heartbeat MQTT disconnected unexpectedly (rc=%d)", rc)

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        """Handle incoming MQTT messages (peer heartbeats and status)."""
        try:
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3:
                return

            peer_id = topic_parts[-2]

            # Ignore our own messages
            if peer_id == self._config.gateway_id:
                return

            if topic_parts[-1] == 'heartbeat':
                self._handle_peer_heartbeat(peer_id, msg.payload)
            elif topic_parts[-1] == 'status':
                self._handle_peer_status(peer_id, msg.payload)
        except Exception as e:
            logger.debug("Heartbeat message parse error: %s", e)

    def _handle_peer_heartbeat(self, peer_id: str, payload: bytes) -> None:
        """Process a peer's heartbeat message."""
        try:
            data = json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        with self._peers_lock:
            if peer_id not in self._peers:
                self._peers[peer_id] = PeerInfo(gateway_id=peer_id)
                logger.info("Discovered peer gateway: %s (role=%s)",
                           peer_id, data.get('role', 'unknown'))

            peer = self._peers[peer_id]
            was_dead = not peer.alive

            peer.role = data.get('role', '')
            peer.state = data.get('state', '')
            peer.uptime = data.get('uptime', 0)
            peer.health_score = data.get('health_score', 0)
            peer.failover_state = data.get('failover_state', '')
            peer.last_heartbeat = time.time()          # display
            peer.last_heartbeat_mono = time.monotonic()  # liveness decision
            peer.missed_count = 0
            peer.alive = True

        # Peer recovered from being down
        if was_dead:
            self._handle_peer_recovered(peer_id)

    def _handle_peer_status(self, peer_id: str, payload: bytes) -> None:
        """Process a peer's LWT status message."""
        status = payload.decode('utf-8', errors='replace').strip()

        if status == "offline":
            logger.warning("Received LWT offline for peer %s", peer_id)
            with self._peers_lock:
                if peer_id in self._peers:
                    self._peers[peer_id].alive = False
            self._handle_peer_down(peer_id)

    # ── Peer lifecycle ─────────────────────────────────────────────────

    def _handle_peer_down(self, peer_id: str) -> None:
        """Handle a peer gateway going down."""
        event = HeartbeatEvent(
            timestamp=datetime.now(),
            event_type="peer_down",
            detail=f"Peer {peer_id} is down",
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        logger.warning("GATEWAY HEARTBEAT: peer %s is DOWN", peer_id)

        # Read peer role outside state lock to avoid lock ordering issues
        with self._peers_lock:
            peer = self._peers.get(peer_id)
            peer_role = peer.role if peer else None

        # If we're secondary and primary went down, promote to active
        if self._role == GatewayRole.SECONDARY and peer_role == GatewayRole.PRIMARY.value:
            with self._state_lock:
                if self._state == GatewayState.STANDBY:
                    self._state = GatewayState.ACTIVE
                    self._record_promotion()

        if self._on_peer_down:
            try:
                self._on_peer_down()
            except Exception as e:
                logger.error("Peer down callback error: %s", e)

        # Emit event
        if _HAS_EVENT_BUS:
            try:
                emit_service_status(
                    "gateway_heartbeat",
                    False,
                    f"Peer {peer_id} is down",
                )
            except Exception as e:
                logger.debug("EventBus emit failed (peer_down): %s", e)

    def _handle_peer_recovered(self, peer_id: str) -> None:
        """Handle a peer gateway recovering."""
        event = HeartbeatEvent(
            timestamp=datetime.now(),
            event_type="peer_recovered",
            detail=f"Peer {peer_id} recovered",
        )
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        logger.info("GATEWAY HEARTBEAT: peer %s RECOVERED", peer_id)

        # Read peer role outside state lock to avoid lock ordering issues
        with self._peers_lock:
            peer = self._peers.get(peer_id)
            peer_role = peer.role if peer else None

        # If we're secondary and were promoted, demote back to standby
        if self._role == GatewayRole.SECONDARY and peer_role == GatewayRole.PRIMARY.value:
            with self._state_lock:
                if self._state == GatewayState.ACTIVE:
                    self._state = GatewayState.STANDBY
                    self._record_demotion()

        if self._on_peer_recovered:
            try:
                self._on_peer_recovered()
            except Exception as e:
                logger.error("Peer recovered callback error: %s", e)

        if _HAS_EVENT_BUS:
            try:
                emit_service_status(
                    "gateway_heartbeat",
                    True,
                    f"Peer {peer_id} recovered",
                )
            except Exception as e:
                logger.debug("EventBus emit failed (peer_recovered): %s", e)

    def _record_promotion(self) -> None:
        """Record promotion event (state already set by caller under lock)."""
        event = HeartbeatEvent(
            timestamp=datetime.now(),
            event_type="promoted",
            detail="Secondary promoted to active — primary is down",
        )
        self._events.append(event)

        logger.warning(
            "GATEWAY HEARTBEAT: %s PROMOTED to active",
            self._config.gateway_id,
        )

        # Persist
        if _HAS_SHARED_STATE:
            try:
                shs = get_shared_health_state()
                shs.update_service(
                    "gateway_heartbeat",
                    state="active",
                    reason="Secondary promoted — primary down",
                )
            except Exception as e:
                logger.warning("Failed to persist promotion to shared state: %s", e)

    def _record_demotion(self) -> None:
        """Record demotion event (state already set by caller under lock)."""
        event = HeartbeatEvent(
            timestamp=datetime.now(),
            event_type="demoted",
            detail="Secondary demoted to standby — primary recovered",
        )
        self._events.append(event)

        logger.info(
            "GATEWAY HEARTBEAT: %s DEMOTED to standby",
            self._config.gateway_id,
        )

        if _HAS_SHARED_STATE:
            try:
                shs = get_shared_health_state()
                shs.update_service(
                    "gateway_heartbeat",
                    state="standby",
                    reason="Secondary demoted — primary recovered",
                )
            except Exception as e:
                logger.warning("Failed to persist demotion to shared state: %s", e)

    # ── Background threads ─────────────────────────────────────────────

    def _start_threads(self) -> None:
        """Start heartbeat publisher and peer checker threads."""
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="gateway-heartbeat-pub",
            daemon=True,
        )
        self._heartbeat_thread.start()

        self._check_thread = threading.Thread(
            target=self._check_loop,
            name="gateway-heartbeat-check",
            daemon=True,
        )
        self._check_thread.start()

    def _heartbeat_loop(self) -> None:
        """Publish heartbeats at regular intervals."""
        while not self._stop_event.is_set():
            if self._mqtt_connected and self._mqtt_client:
                try:
                    self._publish_heartbeat()
                except Exception as e:
                    logger.debug("Heartbeat publish error: %s", e)

            self._stop_event.wait(timeout=self._config.heartbeat_interval)

    def _publish_heartbeat(self) -> None:
        """Publish a single heartbeat message."""
        with self._state_lock:
            state = self._state

        # Build payload
        payload = {
            'id': self._config.gateway_id,
            'role': self._role.value,
            'state': state.value,
            'uptime': time.time() - self._start_time,
            'timestamp': time.time(),
        }

        # Include failover state if FailoverManager available
        if self._failover_manager is not None:
            try:
                payload['failover_state'] = self._failover_manager.state.value
            except (AttributeError, ValueError) as e:
                logger.debug("Could not read failover state: %s", e)

        # Include health score if available
        if _HAS_HEALTH_SCORER:
            try:
                scorer = get_health_scorer()
                snapshot = scorer.get_snapshot()
                payload['health_score'] = snapshot.overall_score
            except (AttributeError, RuntimeError) as e:
                logger.debug("Could not read health score: %s", e)
                payload['health_score'] = 0

        topic = (f"{self._config.mqtt_topic_prefix}/"
                f"{self._config.gateway_id}/heartbeat")
        self._mqtt_client.publish(
            topic,
            json.dumps(payload),
            qos=1,
        )

    def _check_loop(self) -> None:
        """Check for missed peer heartbeats."""
        while not self._stop_event.is_set():
            self._check_peers()
            self._stop_event.wait(timeout=self._config.heartbeat_interval)

    def _check_peers(self) -> None:
        """Check all tracked peers for missed heartbeats.

        Liveness aging is measured on time.monotonic(), never wall-clock: on the
        RTC-less Pi fleet an NTP forward step would otherwise inflate `elapsed`
        past the timeout and forge a peer-down → a SECONDARY promotes to ACTIVE
        while the PRIMARY is still alive = the split-brain this module's role
        model exists to prevent. A backward step forged the inverse (a dead peer
        reads alive forever → missed failover). Monotonic never steps, so both
        vanish. Wall-clock `last_heartbeat` is kept for the TUI display only.
        """
        now_mono = time.monotonic()

        # Grace period after MQTT reconnect — skip checks while catching up.
        # `_last_mqtt_connect` == 0.0 means "never connected" (no grace yet).
        if self._last_mqtt_connect and (
            now_mono - self._last_mqtt_connect < self._config.heartbeat_interval * 2
        ):
            return

        timeout = self._config.heartbeat_interval * self._config.missed_heartbeats_threshold

        with self._peers_lock:
            for peer_id, peer in self._peers.items():
                if not peer.alive:
                    continue

                # An alive peer always has a monotonic stamp (set in the same
                # block that sets alive=True). Guard ONLY the unset default
                # sentinel (exactly 0.0) rather than let it forge a huge elapsed
                # → false down. NOT `<= 0`: time.monotonic() has an arbitrary
                # origin, so a real stamp minus an offset (or a fresh-boot host
                # where monotonic() is small) can be negative — that's a
                # legitimately-old peer, and `elapsed` still ages it correctly.
                if peer.last_heartbeat_mono == 0.0:
                    continue

                elapsed = now_mono - peer.last_heartbeat_mono
                if elapsed > self._config.heartbeat_interval:
                    peer.missed_count = int(elapsed / self._config.heartbeat_interval)

                if elapsed > timeout:
                    peer.alive = False
                    logger.warning(
                        "Peer %s missed %d heartbeats (%.0fs) — declaring down",
                        peer_id, peer.missed_count, elapsed,
                    )
                    # Guard against duplicate peer-down threads
                    with self._pending_peer_down_lock:
                        if peer_id in self._pending_peer_down:
                            continue
                        self._pending_peer_down.add(peer_id)
                    threading.Thread(
                        target=self._handle_peer_down_safe,
                        args=(peer_id,),
                        daemon=True,
                    ).start()

    def _handle_peer_down_safe(self, peer_id: str) -> None:
        """Wrapper for _handle_peer_down that cleans up pending tracking."""
        try:
            self._handle_peer_down(peer_id)
        finally:
            with self._pending_peer_down_lock:
                self._pending_peer_down.discard(peer_id)
