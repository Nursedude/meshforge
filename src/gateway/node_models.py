"""
Data models for unified node tracking.

Extracted from node_tracker.py to reduce file size per MeshForge guidelines.
Contains all dataclasses and type definitions for node representation.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, TYPE_CHECKING

logger = logging.getLogger(__name__)

# Import node state machine (optional - graceful fallback)
try:
    from .node_state import (
        NodeState, NodeStateMachine, NodeStateConfig,
        StateTransition, get_default_state_config
    )
    NODE_STATE_AVAILABLE = True
except ImportError:
    NODE_STATE_AVAILABLE = False
    NodeState = None  # type: ignore
    NodeStateMachine = None  # type: ignore

# Import RNS service registry (optional - graceful fallback)
try:
    from .rns_services import (
        RNSServiceType, ServiceInfo, AnnounceEvent,
        get_service_registry, RNSServiceRegistry
    )
    RNS_SERVICES_AVAILABLE = True
except ImportError:
    RNS_SERVICES_AVAILABLE = False
    RNSServiceType = None  # type: ignore
    ServiceInfo = None  # type: ignore


@dataclass
class Position:
    """Geographic position"""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    precision: int = 5  # decimal places
    timestamp: Optional[datetime] = None

    def is_valid(self) -> bool:
        """Check if position is valid"""
        return (self.latitude != 0.0 or self.longitude != 0.0) and \
               -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180

    def to_dict(self) -> dict:
        return {
            "latitude": round(self.latitude, self.precision),
            "longitude": round(self.longitude, self.precision),
            "altitude": self.altitude,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


class PKIKeyState(Enum):
    """
    PKI key verification state for Meshtastic 2.5+ nodes.

    Meshtastic uses TOFU (Trust On First Use) model with Curve25519 keys.
    """
    UNKNOWN = "unknown"           # No key seen yet
    TRUSTED = "trusted"           # TOFU - first key accepted
    CHANGED = "changed"           # Key changed (warning - potential MITM!)
    VERIFIED = "verified"         # Manually verified out-of-band
    LEGACY = "legacy"             # Pre-2.5 node, no PKI support


@dataclass
class PKIStatus:
    """
    PKI encryption status for a Meshtastic node.

    Tracks public key state for direct message encryption.
    See: https://meshtastic.org/docs/overview/encryption/
    """
    state: PKIKeyState = PKIKeyState.UNKNOWN
    public_key: Optional[bytes] = None       # 32-byte Curve25519 public key
    public_key_hex: Optional[str] = None     # Hex string for display
    first_seen: Optional[datetime] = None    # When key was first seen
    last_changed: Optional[datetime] = None  # When key last changed (if CHANGED)
    is_admin_trusted: bool = False           # Is in admin_key list

    def key_fingerprint(self) -> str:
        """
        6-character fingerprint for visual verification.

        Allows out-of-band key verification (e.g., compare over phone).
        """
        if not self.public_key:
            return "------"
        h = hashlib.sha256(self.public_key).hexdigest()
        return h[:6].upper()

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "public_key_hex": self.public_key_hex,
            "fingerprint": self.key_fingerprint(),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_changed": self.last_changed.isoformat() if self.last_changed else None,
            "is_admin_trusted": self.is_admin_trusted
        }

    @classmethod
    def from_public_key(cls, public_key: bytes, is_admin: bool = False) -> "PKIStatus":
        """Create PKIStatus from a public key (TOFU)."""
        return cls(
            state=PKIKeyState.TRUSTED,
            public_key=public_key,
            public_key_hex=public_key.hex() if public_key else None,
            first_seen=datetime.now(),
            is_admin_trusted=is_admin
        )


@dataclass
class AirQualityMetrics:
    """Air quality sensor data (e.g., PMSA003I, SCD4X)"""
    pm10_standard: Optional[int] = None   # PM1.0 standard (µg/m³)
    pm25_standard: Optional[int] = None   # PM2.5 standard (µg/m³)
    pm100_standard: Optional[int] = None  # PM10 standard (µg/m³)
    pm10_environmental: Optional[int] = None
    pm25_environmental: Optional[int] = None
    pm100_environmental: Optional[int] = None
    co2: Optional[int] = None             # CO2 in ppm (SCD4X)
    iaq: Optional[int] = None             # Indoor Air Quality index
    gas_resistance: Optional[float] = None  # BME680 gas sensor
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "pm10_standard": self.pm10_standard,
            "pm25_standard": self.pm25_standard,
            "pm100_standard": self.pm100_standard,
            "pm10_environmental": self.pm10_environmental,
            "pm25_environmental": self.pm25_environmental,
            "pm100_environmental": self.pm100_environmental,
            "co2": self.co2,
            "iaq": self.iaq,
            "gas_resistance": self.gas_resistance,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}

    def has_data(self) -> bool:
        """Check if any air quality data is present"""
        return any([self.pm25_standard, self.co2, self.iaq, self.gas_resistance])


@dataclass
class HealthMetrics:
    """Health sensor data (heart rate, SpO2, temperature)"""
    heart_rate: Optional[int] = None      # BPM
    spo2: Optional[int] = None            # Blood oxygen saturation %
    body_temperature: Optional[float] = None  # Celsius
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "heart_rate": self.heart_rate,
            "spo2": self.spo2,
            "body_temperature": self.body_temperature,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}


@dataclass
class DetectionSensor:
    """Detection sensor state (motion, reed switch, etc.)"""
    name: str = ""                        # Sensor name (e.g., "Motion", "Door")
    triggered: bool = False               # Current state
    gpio_pin: Optional[int] = None        # Monitored GPIO pin
    triggered_high: bool = True           # Whether HIGH means triggered
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0                # Number of triggers since reset

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "triggered": self.triggered,
            "gpio_pin": self.gpio_pin,
            "triggered_high": self.triggered_high,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "trigger_count": self.trigger_count
        }


@dataclass
class SignalSample:
    """A single signal quality measurement with timestamp."""
    timestamp: datetime
    value: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "value": self.value
        }


@dataclass
class Telemetry:
    """
    Complete node telemetry data.

    Supports all Meshtastic telemetry types:
    - Device metrics (battery, voltage, channel utilization, airtime)
    - Environment metrics (temperature, humidity, pressure from BME280/BME680)
    - Air quality metrics (PM2.5, CO2 from PMSA003I, SCD4X)
    - Health metrics (heart rate, SpO2)
    - Detection sensors (motion, door sensors)

    Reference: https://meshtastic.org/docs/configuration/module/telemetry/
    """
    # Device Metrics
    battery_level: Optional[int] = None   # 0-100%
    voltage: Optional[float] = None       # Battery voltage
    channel_utilization: Optional[float] = None  # 0-100% (how busy the channel is)
    air_util_tx: Optional[float] = None   # TX airtime utilization %
    uptime: Optional[int] = None          # Uptime in seconds

    # Environment Metrics (BME280, BME680, BMP280, etc.)
    temperature: Optional[float] = None   # Celsius
    humidity: Optional[float] = None      # 0-100%
    pressure: Optional[float] = None      # hPa (barometric pressure)
    gas_resistance: Optional[float] = None  # Ohms (BME680 VOC sensor)

    # Air Quality (PMSA003I, SCD4X)
    air_quality: Optional[AirQualityMetrics] = None

    # Health Metrics
    health: Optional[HealthMetrics] = None

    # Detection Sensors
    detection_sensors: List[DetectionSensor] = field(default_factory=list)

    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        result = {k: v for k, v in {
            "battery_level": self.battery_level,
            "voltage": self.voltage,
            "channel_utilization": self.channel_utilization,
            "air_util_tx": self.air_util_tx,
            "uptime": self.uptime,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "pressure": self.pressure,
            "gas_resistance": self.gas_resistance,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }.items() if v is not None}

        if self.air_quality and self.air_quality.has_data():
            result["air_quality"] = self.air_quality.to_dict()

        if self.health:
            result["health"] = self.health.to_dict()

        if self.detection_sensors:
            result["detection_sensors"] = [s.to_dict() for s in self.detection_sensors]

        return result

    def has_environment_sensors(self) -> bool:
        """Check if node has environment sensors"""
        return any([self.temperature, self.humidity, self.pressure, self.gas_resistance])

    def has_air_quality_sensors(self) -> bool:
        """Check if node has air quality sensors"""
        return self.air_quality is not None and self.air_quality.has_data()

    def has_detection_sensors(self) -> bool:
        """Check if node has detection sensors"""
        return len(self.detection_sensors) > 0

    def get_sensor_summary(self) -> str:
        """Get a summary of available sensor data"""
        parts = []
        if self.battery_level is not None:
            parts.append(f"🔋{self.battery_level}%")
        if self.temperature is not None:
            parts.append(f"🌡️{self.temperature:.1f}°C")
        if self.humidity is not None:
            parts.append(f"💧{self.humidity:.0f}%")
        if self.air_quality and self.air_quality.pm25_standard:
            parts.append(f"AQI:{self.air_quality.pm25_standard}")
        if self.detection_sensors:
            triggered = sum(1 for s in self.detection_sensors if s.triggered)
            parts.append(f"📡{triggered}/{len(self.detection_sensors)}")
        return " ".join(parts) if parts else "No data"


@dataclass
class UnifiedNode:
    """Represents a node from either RNS or Meshtastic network"""
    # Core identity
    id: str  # Unified identifier (network prefix + hash/id)
    network: str  # "meshtastic", "rns", or "both"
    name: str = ""
    short_name: str = ""

    # Position and telemetry
    position: Position = field(default_factory=Position)
    telemetry: Telemetry = field(default_factory=Telemetry)

    # Network-specific identifiers
    meshtastic_id: Optional[str] = None  # !abcd1234
    rns_hash: Optional[bytes] = None  # 16-byte destination hash

    # Radio metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    hops: Optional[int] = None

    # Signal quality history (for trending)
    snr_history: List[SignalSample] = field(default_factory=list)
    rssi_history: List[SignalSample] = field(default_factory=list)

    # Configuration for signal history
    MAX_SIGNAL_SAMPLES: int = field(default=100, repr=False)

    # Status
    is_online: bool = False
    is_gateway: bool = False
    is_local: bool = False  # Is this our own node
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None

    # Relay tracking (Meshtastic 2.6+)
    discovered_via_relay: bool = False  # Node discovered by seeing it relay packets
    relay_node: Optional[int] = None  # Last byte of node that relayed to us
    next_hop: Optional[int] = None  # Last byte of expected next-hop for our packets

    # State machine for granular status tracking
    # Initialized in __post_init__ if available
    _state_machine: Optional[Any] = field(default=None, repr=False)

    # Hardware info
    hardware_model: Optional[str] = None
    firmware_version: Optional[str] = None
    role: Optional[str] = None

    # RNS service info (enhanced tracking)
    service_type: Optional[str] = None  # RNS service type (LXMF_DELIVERY, NOMAD_PAGE, etc.)
    service_aspect: Optional[str] = None  # Raw aspect filter (lxmf.delivery, nomadnetwork.node, etc.)
    service_capabilities: List[str] = field(default_factory=list)  # Service capabilities

    # PKI status (Meshtastic 2.5+)
    pki_status: PKIStatus = field(default_factory=PKIStatus)

    # Favorites (BaseUI 2.7+)
    is_favorite: bool = False  # Marked as favorite in BaseUI
    favorite_updated: Optional[datetime] = None  # When favorite status last changed

    def __post_init__(self):
        if self.first_seen is None:
            self.first_seen = datetime.now()
        # Initialize state machine if available
        if NODE_STATE_AVAILABLE and self._state_machine is None:
            from .node_state import NodeStateMachine, NodeState
            initial = NodeState.DISCOVERED if self.is_online else NodeState.STALE_CACHE
            self._state_machine = NodeStateMachine(initial_state=initial)

    def update_seen(self):
        """Update last seen timestamp and state machine"""
        self.last_seen = datetime.now()
        self.is_online = True
        # Update state machine with current signal values
        if self._state_machine is not None:
            self._state_machine.record_response(snr=self.snr, rssi=self.rssi)

    @property
    def state(self) -> Optional['NodeState']:
        """Get current node state (granular status)."""
        if self._state_machine is not None:
            return self._state_machine.state
        # Fallback: derive from is_online
        if NODE_STATE_AVAILABLE:
            from .node_state import NodeState
            return NodeState.ONLINE if self.is_online else NodeState.OFFLINE
        return None

    @property
    def state_name(self) -> str:
        """Get human-readable state name."""
        if self._state_machine is not None:
            return self._state_machine.state.display_name
        return "Online" if self.is_online else "Offline"

    @property
    def state_icon(self) -> str:
        """Get state icon for display."""
        if self._state_machine is not None:
            return self._state_machine.state.icon
        return "+" if self.is_online else "-"

    def check_timeout(self) -> bool:
        """Check for timeout and update state. Returns True if state changed."""
        if self._state_machine is not None:
            old_state = self._state_machine.state
            self._state_machine.check_timeout(self.last_seen)
            # Sync is_online with state machine
            self.is_online = self._state_machine.state.is_active()
            return old_state != self._state_machine.state
        return False

    def get_state_history(self, count: int = 10) -> List[dict]:
        """Get recent state transitions for debugging."""
        if self._state_machine is not None:
            return [t.to_dict() for t in self._state_machine.get_transitions(count)]
        return []

    def record_signal_quality(self, snr: Optional[float] = None, rssi: Optional[int] = None):
        """Record signal quality measurements with timestamp for trending.

        Args:
            snr: Signal-to-Noise Ratio in dB (can be negative)
            rssi: Received Signal Strength Indicator in dBm (typically negative)
        """
        now = datetime.now()

        if snr is not None:
            self.snr = snr
            self.snr_history.append(SignalSample(timestamp=now, value=float(snr)))
            # Trim to max size
            if len(self.snr_history) > self.MAX_SIGNAL_SAMPLES:
                self.snr_history = self.snr_history[-self.MAX_SIGNAL_SAMPLES:]

        if rssi is not None:
            self.rssi = rssi
            self.rssi_history.append(SignalSample(timestamp=now, value=float(rssi)))
            # Trim to max size
            if len(self.rssi_history) > self.MAX_SIGNAL_SAMPLES:
                self.rssi_history = self.rssi_history[-self.MAX_SIGNAL_SAMPLES:]

    def update_pki_status(self, public_key: bytes, is_admin: bool = False) -> bool:
        """
        Update PKI status with a new public key.

        Implements TOFU (Trust On First Use) model:
        - First key seen: Trusted automatically
        - Same key seen again: No change
        - Different key seen: Mark as CHANGED (warning!)

        Args:
            public_key: 32-byte Curve25519 public key
            is_admin: Whether this key is in admin_key list

        Returns:
            True if key was new or changed, False if unchanged
        """
        if not public_key or len(public_key) != 32:
            return False

        now = datetime.now()

        if self.pki_status.state == PKIKeyState.UNKNOWN:
            # First key - TOFU
            self.pki_status = PKIStatus(
                state=PKIKeyState.TRUSTED,
                public_key=public_key,
                public_key_hex=public_key.hex(),
                first_seen=now,
                is_admin_trusted=is_admin
            )
            logger.info(f"PKI: First key for {self.id}, fingerprint: {self.pki_status.key_fingerprint()}")
            return True

        elif self.pki_status.public_key != public_key:
            # Key changed - WARNING!
            old_fingerprint = self.pki_status.key_fingerprint()
            self.pki_status.state = PKIKeyState.CHANGED
            self.pki_status.last_changed = now
            # Note: Keep old key until manually verified
            new_fp = hashlib.sha256(public_key).hexdigest()[:6].upper()
            logger.warning(
                f"PKI KEY CHANGED for {self.id}! "
                f"Old: {old_fingerprint}, New: {new_fp} - Potential MITM!"
            )
            return True

        # Key unchanged
        return False

    def verify_pki_key(self):
        """Manually mark the current key as verified (out-of-band verification)."""
        if self.pki_status.state in (PKIKeyState.TRUSTED, PKIKeyState.CHANGED):
            self.pki_status.state = PKIKeyState.VERIFIED
            logger.info(f"PKI: Key verified for {self.id}")

    @property
    def snr_trend(self) -> str:
        """Calculate SNR trend from history.

        Compares average of recent samples (last 5) to older samples (previous 5).
        Requires at least 5 samples for meaningful trend.

        Returns:
            "improving": SNR increasing (better signal)
            "degrading": SNR decreasing (worse signal)
            "stable": SNR relatively constant
            "unknown": Not enough data
        """
        return self._calculate_trend(self.snr_history)

    @property
    def rssi_trend(self) -> str:
        """Calculate RSSI trend from history.

        Compares average of recent samples to older samples.
        Higher RSSI (closer to 0) = better signal.

        Returns:
            "improving": RSSI increasing (better signal)
            "degrading": RSSI decreasing (worse signal)
            "stable": RSSI relatively constant
            "unknown": Not enough data
        """
        return self._calculate_trend(self.rssi_history)

    def _calculate_trend(self, history: List[SignalSample], threshold: float = 2.0) -> str:
        """Calculate trend from signal history.

        Args:
            history: List of SignalSample objects
            threshold: Minimum delta to be considered improving/degrading (dB)

        Returns:
            Trend string: "improving", "degrading", "stable", or "unknown"
        """
        if len(history) < 5:
            return "unknown"

        # Get recent samples (last 5) and older samples (previous 5)
        recent = [s.value for s in history[-5:]]
        older_start = max(0, len(history) - 10)
        older_end = len(history) - 5
        older = [s.value for s in history[older_start:older_end]]

        if not older:
            return "unknown"

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        delta = recent_avg - older_avg

        # Higher SNR/RSSI = better signal
        if delta > threshold:
            return "improving"
        elif delta < -threshold:
            return "degrading"
        return "stable"

    def get_signal_stats(self) -> dict:
        """Get signal quality statistics.

        Returns:
            Dict with min, max, avg, current, and trend for SNR and RSSI
        """
        stats = {}

        if self.snr_history:
            snr_values = [s.value for s in self.snr_history]
            stats['snr'] = {
                'current': self.snr,
                'min': min(snr_values),
                'max': max(snr_values),
                'avg': sum(snr_values) / len(snr_values),
                'samples': len(snr_values),
                'trend': self.snr_trend
            }

        if self.rssi_history:
            rssi_values = [s.value for s in self.rssi_history]
            stats['rssi'] = {
                'current': self.rssi,
                'min': min(rssi_values),
                'max': max(rssi_values),
                'avg': sum(rssi_values) / len(rssi_values),
                'samples': len(rssi_values),
                'trend': self.rssi_trend
            }

        return stats

    def get_age_string(self) -> str:
        """Get human-readable time since last seen"""
        if not self.last_seen:
            return "Never"

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

    def to_dict(self, include_signal_history: bool = False) -> dict:
        """Convert to dictionary for JSON serialization.

        Args:
            include_signal_history: If True, include full signal history arrays.
                                   Default False to reduce payload size.
        """
        result = {
            "id": self.id,
            "network": self.network,
            "name": self.name,
            "short_name": self.short_name,
            "position": self.position.to_dict() if self.position.is_valid() else None,
            "telemetry": self.telemetry.to_dict(),
            "meshtastic_id": self.meshtastic_id,
            "rns_hash": self.rns_hash.hex() if self.rns_hash else None,
            "snr": self.snr,
            "rssi": self.rssi,
            "snr_trend": self.snr_trend if self.snr_history else None,
            "rssi_trend": self.rssi_trend if self.rssi_history else None,
            "hops": self.hops,
            "is_online": self.is_online,
            "is_gateway": self.is_gateway,
            "is_local": self.is_local,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "last_seen_ago": self.get_age_string(),
            "hardware_model": self.hardware_model,
            "firmware_version": self.firmware_version,
            "role": self.role,
            # RNS service info
            "service_type": self.service_type,
            "service_aspect": self.service_aspect,
            "service_capabilities": self.service_capabilities if self.service_capabilities else None,
            # PKI status (Meshtastic 2.5+)
            "pki_status": self.pki_status.to_dict() if self.pki_status.state != PKIKeyState.UNKNOWN else None,
            # Favorites (BaseUI 2.7+)
            "is_favorite": self.is_favorite,
            "favorite_updated": self.favorite_updated.isoformat() if self.favorite_updated else None,
            # Granular state
            "state": self.state.name if self.state else None,
            "state_display": self.state_name,
            "state_icon": self.state_icon,
        }

        # Optionally include full signal history (for detailed views/caching)
        if include_signal_history:
            if self.snr_history:
                result["snr_history"] = [s.to_dict() for s in self.snr_history]
            if self.rssi_history:
                result["rssi_history"] = [s.to_dict() for s in self.rssi_history]
            # Include state machine data for cache persistence
            if self._state_machine is not None:
                result["state_machine"] = self._state_machine.to_dict()

        return result

    @classmethod
    def from_meshtastic(cls, mesh_node: dict, is_local: bool = False) -> 'UnifiedNode':
        """Create from Meshtastic node data"""
        node_id = mesh_node.get('num', 0)
        user = mesh_node.get('user', {})
        position = mesh_node.get('position', {})
        metrics = mesh_node.get('deviceMetrics', {})

        meshtastic_id = f"!{node_id:08x}"

        node = cls(
            id=f"mesh_{meshtastic_id}",
            network="meshtastic",
            name=user.get('longName', meshtastic_id),
            short_name=user.get('shortName', ''),
            meshtastic_id=meshtastic_id,
            is_local=is_local,
            hardware_model=user.get('hwModel'),
            role=user.get('role'),
        )

        # Position
        if position:
            node.position = Position(
                latitude=position.get('latitude', 0) or 0,
                longitude=position.get('longitude', 0) or 0,
                altitude=position.get('altitude', 0) or 0,
                timestamp=datetime.now()
            )

        # Telemetry
        if metrics:
            node.telemetry = Telemetry(
                battery_level=metrics.get('batteryLevel'),
                voltage=metrics.get('voltage'),
                uptime=metrics.get('uptimeSeconds'),
                timestamp=datetime.now()
            )

        # Radio metrics
        node.snr = mesh_node.get('snr')
        node.rssi = mesh_node.get('rssi') or mesh_node.get('rxRssi')
        node.hops = mesh_node.get('hopsAway')
        node.last_seen = datetime.now()
        node.is_online = True

        # Relay tracking (Meshtastic 2.6+)
        relay_node = mesh_node.get('relayNode')
        if relay_node and relay_node > 0:
            node.relay_node = relay_node
        next_hop = mesh_node.get('nextHop')
        if next_hop and next_hop > 0:
            node.next_hop = next_hop

        # PKI status (Meshtastic 2.5+)
        # Extract public_key from user dict if available
        public_key = user.get('publicKey')
        if public_key:
            # Handle different formats: bytes, base64 string
            key_bytes = None
            if isinstance(public_key, bytes) and len(public_key) == 32:
                key_bytes = public_key
            elif isinstance(public_key, str):
                try:
                    import base64
                    decoded = base64.b64decode(public_key)
                    if len(decoded) == 32:
                        key_bytes = decoded
                except Exception:
                    pass

            if key_bytes:
                # Check if this is an admin key
                is_admin = False
                admin_keys = mesh_node.get('adminKey', [])
                if admin_keys and key_bytes.hex() in [k if isinstance(k, str) else k.hex() if isinstance(k, bytes) else '' for k in admin_keys]:
                    is_admin = True

                node.update_pki_status(key_bytes, is_admin=is_admin)

        # Update state machine with live data
        if node._state_machine is not None:
            node._state_machine.record_response(snr=node.snr)

        # Favorites (BaseUI 2.7+)
        if mesh_node.get('isFavorite', False):
            node.is_favorite = True
            node.favorite_updated = datetime.now()

        return node

    @classmethod
    def from_rns(cls, rns_hash: bytes, name: str = "", app_data: bytes = None,
                 service_info: Any = None, aspect: str = None) -> 'UnifiedNode':
        """Create from RNS announce/discovery data.

        Parses announce app_data which may contain:
        - Display name (first portion of app_data)
        - Telemetry data including position (msgpack encoded, if present)

        Supports multiple RNS service types:
        - LXMF (Sideband, NomadNet messaging)
        - Nomad Network pages
        - Generic services

        Args:
            rns_hash: 16-byte destination hash
            name: Optional display name override
            app_data: Raw announce app_data bytes
            service_info: Optional ServiceInfo from RNS service registry
            aspect: Optional aspect filter string (e.g., "lxmf.delivery")
        """
        hash_hex = rns_hash.hex()

        node = cls(
            id=f"rns_{hash_hex[:16]}",
            network="rns",
            name=name or hash_hex[:8],
            short_name=hash_hex[:4].upper(),
            rns_hash=rns_hash,
        )

        # Use service registry if available and service_info provided
        if RNS_SERVICES_AVAILABLE and service_info is not None:
            # Extract data from ServiceInfo
            if service_info.display_name:
                node.name = service_info.display_name
            if service_info.latitude is not None and service_info.longitude is not None:
                lat, lon = service_info.latitude, service_info.longitude
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    node.position = Position(
                        latitude=lat,
                        longitude=lon,
                        altitude=service_info.altitude or 0.0,
                        timestamp=datetime.now()
                    )
                    logger.debug(f"RNS node {hash_hex[:8]} has position: {lat:.4f}, {lon:.4f}")
            if service_info.battery is not None:
                node.telemetry.battery_level = service_info.battery

            # Store service info
            node.service_type = service_info.service_type.name if hasattr(service_info.service_type, 'name') else str(service_info.service_type)
            node.service_aspect = service_info.aspect
            node.service_capabilities = list(service_info.capabilities)

        elif app_data and len(app_data) > 0:
            # Fallback to legacy parsing if service registry not available
            parsed = cls._parse_lxmf_app_data(app_data)
            if parsed:
                if parsed.get("display_name"):
                    node.name = parsed["display_name"]
                if parsed.get("latitude") is not None and parsed.get("longitude") is not None:
                    lat = parsed["latitude"]
                    lon = parsed["longitude"]
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        node.position = Position(
                            latitude=lat,
                            longitude=lon,
                            altitude=parsed.get("altitude", 0.0),
                            timestamp=datetime.now()
                        )
                        logger.debug(f"RNS node {hash_hex[:8]} has position: {lat:.4f}, {lon:.4f}")

        # Store aspect even without full service info
        if aspect and not node.service_aspect:
            node.service_aspect = aspect

        node.last_seen = datetime.now()
        return node

    @classmethod
    def _parse_lxmf_app_data(cls, app_data: bytes) -> dict:
        """Parse LXMF announce app_data to extract name and telemetry.

        LXMF app_data format (Sideband/NomadNet):
        - Display name as UTF-8 string (variable length)
        - Optional msgpack-encoded telemetry dict after the name

        Returns dict with: display_name, latitude, longitude, altitude, etc.
        """
        result = {}
        msgpack_start = -1

        try:
            # First, find where msgpack telemetry starts (if present)
            # Scan for msgpack dict marker (fixmap: 0x80-0x8f, map16: 0xde, map32: 0xdf)
            for i in range(len(app_data)):
                byte = app_data[i]
                if byte >= 0x80 and byte <= 0x8f:  # fixmap (up to 15 entries)
                    msgpack_start = i
                    break
                elif byte == 0xde or byte == 0xdf:  # map16 or map32
                    msgpack_start = i
                    break

            # Extract display name from bytes BEFORE msgpack (or entire data if no msgpack)
            name_bytes = app_data[:msgpack_start] if msgpack_start > 0 else app_data
            if len(name_bytes) > 0 and len(name_bytes) < 128:
                try:
                    decoded = name_bytes.decode('utf-8', errors='ignore').strip('\x00').strip()
                    if decoded and len(decoded) >= 2:
                        # Filter to printable characters only
                        clean_name = ''.join(c for c in decoded if c.isprintable())
                        if clean_name:
                            result["display_name"] = clean_name[:64]
                except UnicodeDecodeError:
                    pass

            # Parse msgpack telemetry if found
            if msgpack_start >= 0:
                try:
                    import msgpack
                    telemetry = msgpack.unpackb(app_data[msgpack_start:], raw=False, strict_map_key=False)
                    if isinstance(telemetry, dict):
                        cls._extract_telemetry(telemetry, result)
                except ImportError:
                    # msgpack not installed - skip telemetry parsing
                    pass
                except Exception:
                    # Invalid msgpack data - ignore
                    pass

        except Exception as e:
            logger.debug(f"Error parsing LXMF app_data: {e}")

        return result

    @classmethod
    def _extract_telemetry(cls, telemetry: dict, result: dict):
        """Extract position and other telemetry from parsed msgpack dict.

        Sideband telemetry keys (from Sideband source):
        - 'latitude' or 'lat': GPS latitude
        - 'longitude' or 'lon' or 'lng': GPS longitude
        - 'altitude' or 'alt': GPS altitude
        - 'speed': Speed in km/h
        - 'heading': Compass heading
        - 'accuracy': GPS accuracy in meters
        """
        # Position extraction with multiple key formats
        lat = telemetry.get('latitude') or telemetry.get('lat')
        lon = telemetry.get('longitude') or telemetry.get('lon') or telemetry.get('lng')
        alt = telemetry.get('altitude') or telemetry.get('alt') or 0.0

        if lat is not None and lon is not None:
            try:
                result['latitude'] = float(lat)
                result['longitude'] = float(lon)
                result['altitude'] = float(alt) if alt else 0.0
            except (TypeError, ValueError):
                pass

        # Other telemetry fields
        if 'speed' in telemetry:
            try:
                result['speed'] = float(telemetry['speed'])
            except (TypeError, ValueError):
                pass

        if 'battery' in telemetry:
            try:
                result['battery'] = int(telemetry['battery'])
            except (TypeError, ValueError):
                pass
