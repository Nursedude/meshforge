"""
Unified Node Tracker for RNS and Meshtastic Networks
Tracks nodes from both networks with position and telemetry data.

Enhanced with:
- Multi-service RNS announce parsing (LXMF, Nomad, generic)
- Network topology graph with edge tracking
- Path table change monitoring
"""

import threading
import time
import logging
import os
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Callable, Any, TYPE_CHECKING
from pathlib import Path
import json
import hashlib

logger = logging.getLogger(__name__)

# Import node state machine
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

# Import RNS service registry and topology (optional - graceful fallback)
try:
    from .rns_services import (
        RNSServiceType, ServiceInfo, AnnounceEvent,
        get_service_registry, RNSServiceRegistry
    )
    from .network_topology import (
        NetworkTopology, get_network_topology, TopologyEvent
    )
    RNS_SERVICES_AVAILABLE = True
except ImportError:
    RNS_SERVICES_AVAILABLE = False
    RNSServiceType = None  # type: ignore
    ServiceInfo = None  # type: ignore

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
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


class UnifiedNodeTracker:
    """
    Tracks nodes from both RNS and Meshtastic networks.
    Provides unified view for map display and monitoring.

    Enhanced features:
    - Multi-service RNS announce parsing via RNSServiceRegistry
    - Network topology graph via NetworkTopology
    - Path table change monitoring with event logging
    """

    OFFLINE_THRESHOLD = 3600  # 1 hour
    MAX_NODES = 10000  # Prevent unbounded memory growth

    @classmethod
    def get_cache_file(cls) -> Path:
        """Get the cache file path (evaluated at runtime, not import time)"""
        return get_real_user_home() / ".config" / "meshforge" / "node_cache.json"

    def __init__(self):
        self._nodes: Dict[str, UnifiedNode] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable] = []
        self._running = False
        self._stop_event = threading.Event()
        self._cleanup_thread = None
        self._rns_thread = None
        self._reticulum = None
        self._rns_connected = False

        # Enhanced RNS service tracking
        self._service_registry: Optional[RNSServiceRegistry] = None
        self._network_topology: Optional[NetworkTopology] = None
        if RNS_SERVICES_AVAILABLE:
            self._service_registry = get_service_registry()
            self._network_topology = get_network_topology()
            # Register for topology events
            self._network_topology.register_callback(self._on_topology_event)
            logger.debug("Enhanced RNS service tracking enabled")

        # Load cached nodes
        self._load_cache()

    def start(self):
        """Start the node tracker"""
        self._running = True
        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        # Start network topology tracking (includes path table monitor)
        if self._network_topology:
            self._network_topology.start()

        # Initialize RNS in the main thread to avoid signal handler issues
        # RNS.Reticulum() sets up signal handlers which only work in main thread
        self._init_rns_main_thread()

        logger.info("Node tracker started")

    def _init_rns_main_thread(self):
        """Initialize RNS from main thread, then start background listener.

        IMPORTANT: MeshForge operates as a CLIENT ONLY - it connects to existing
        rnsd/NomadNet instances but never creates its own RNS instance that would
        bind interfaces and conflict with NomadNet or other RNS services.

        NOTE: RNS.Reticulum() uses signal handlers which ONLY work in the main
        thread. If called from a background thread, it will fail with:
        "signal only works in main thread of the main interpreter"
        """
        # Check if we're in the main thread - RNS signal handlers require it
        import threading as _threading
        current = _threading.current_thread()
        main = _threading.main_thread()
        is_main = current is main
        logger.info(f"Thread check: current={current.name}, main={main.name}, is_main={is_main}")

        if not is_main:
            logger.warning("RNS initialization must be in main thread - skipping node discovery")
            logger.info("RNS node discovery disabled (call start() from main thread to enable)")
            self._rns_connected = False
            return

        try:
            import RNS
            logger.info("Checking for existing RNS service...")

            # Check if rnsd is already running
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()

            if not rns_pids:
                # No rnsd running - DO NOT initialize our own RNS instance
                # This would bind AutoInterface port and block NomadNet from starting
                logger.info("No rnsd detected - skipping RNS node discovery")
                logger.info("To enable RNS features, start rnsd first: sudo systemctl start rnsd")
                logger.info("MeshForge will operate without RNS node tracking")
                self._rns_connected = False
                return

            # rnsd is running - connect to existing instance as CLIENT ONLY
            logger.info(f"rnsd detected (PID: {rns_pids[0]}), connecting as client...")
            try:
                # Create a client-only config to avoid interface conflicts
                # This prevents RNS from trying to bind ports that rnsd already owns
                import tempfile
                client_config_dir = Path(tempfile.gettempdir()) / "meshforge_rns_client"
                client_config_dir.mkdir(exist_ok=True)
                client_config_file = client_config_dir / "config"

                # Write minimal client-only config (no interfaces, just shared transport)
                client_config_file.write_text("""# MeshForge RNS Client Config (auto-generated)
# This config connects to existing rnsd without creating interfaces

[reticulum]
share_instance = Yes
shared_instance_port = 37428
instance_control_port = 37429
""")

                # Connect using client-only config
                self._reticulum = RNS.Reticulum(configdir=str(client_config_dir))
                self._rns_connected = True
                logger.info("Connected to existing rnsd instance")

                # Register announce handlers for node discovery
                # We register handlers for specific aspects to get accurate service typing,
                # plus a catch-all handler for unknown service types

                class AspectAnnounceHandler:
                    """Announce handler that passes aspect info to tracker"""
                    def __init__(self, tracker, aspect: str = None):
                        self.tracker = tracker
                        self.aspect_filter = aspect  # None = catch all

                    def received_announce(self, destination_hash, announced_identity, app_data):
                        try:
                            self.tracker._on_rns_announce(
                                destination_hash, announced_identity, app_data,
                                aspect=self.aspect_filter
                            )
                        except Exception as e:
                            logger.error(f"Error handling RNS announce: {e}")

                # Register handlers for known service aspects
                known_aspects = [
                    "lxmf.delivery",       # LXMF messaging (Sideband, NomadNet)
                    "lxmf.propagation",    # LXMF propagation nodes
                    "nomadnetwork.node",   # Nomad Network pages
                ]

                for aspect in known_aspects:
                    RNS.Transport.register_announce_handler(AspectAnnounceHandler(self, aspect))
                    logger.debug(f"Registered announce handler for aspect: {aspect}")

                # Also register a catch-all handler for unknown services
                RNS.Transport.register_announce_handler(AspectAnnounceHandler(self, None))
                logger.info(f"Registered {len(known_aspects) + 1} announce handlers with rnsd")

                # Load known destinations from rnsd (may be empty initially)
                self._load_known_rns_destinations(RNS)

                # Store RNS module reference for background loop
                self._rns_module = RNS

                # Start background loop (will re-check path_table periodically)
                self._rns_thread = threading.Thread(target=self._rns_loop, daemon=True)
                self._rns_thread.start()

                # Schedule delayed re-check after 5 seconds for sync'd data
                def delayed_check():
                    import time
                    time.sleep(5)
                    if self._running and self._rns_connected:
                        logger.debug("Running delayed RNS destination check...")
                        self._load_known_rns_destinations(RNS)

                threading.Thread(target=delayed_check, daemon=True).start()

            except Exception as e:
                logger.warning(f"Could not connect to rnsd: {e}")
                logger.info("RNS nodes may not appear on map - ensure rnsd is running properly")
                self._rns_connected = False

        except ImportError:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pipx install rns")
            logger.info("  2. Start rnsd: sudo systemctl start rnsd")
            logger.info("  3. Restart MeshForge")
        except Exception as e:
            logger.warning(f"Failed to initialize RNS discovery: {e}")
            self._rns_connected = False

    def _rns_loop(self):
        """Background loop for RNS - periodically check for new destinations.

        When connected as a shared instance client, the path_table may not
        be populated immediately. This loop periodically checks for new
        destinations that rnsd has discovered.
        """
        import time
        import RNS

        check_interval = 30  # Check every 30 seconds
        last_check = 0

        while self._running:
            if self._stop_event.wait(1):
                break

            # Periodic check for new RNS destinations
            current_time = time.time()
            if current_time - last_check >= check_interval:
                last_check = current_time
                try:
                    # Re-check path_table for newly discovered routes
                    new_count = 0
                    if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                        for dest_hash, path_data in RNS.Transport.path_table.items():
                            try:
                                if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                                    node_id = f"rns_{dest_hash.hex()[:16]}"
                                    if node_id not in self._nodes:
                                        hops = 0
                                        if isinstance(path_data, tuple) and len(path_data) > 1:
                                            hops = path_data[1]
                                        node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                        self.add_node(node)
                                        new_count += 1
                                        logger.debug(f"Discovered RNS destination: {dest_hash.hex()[:8]} ({hops} hops)")
                            except Exception as e:
                                logger.debug(f"Error processing path_table entry: {e}")

                    if new_count > 0:
                        logger.info(f"Discovered {new_count} new RNS destinations from path_table")

                except Exception as e:
                    logger.debug(f"Error checking path_table: {e}")

    def stop(self, timeout: float = 5.0):
        """Stop the node tracker and wait for threads to finish

        Args:
            timeout: Seconds to wait for each thread to finish
        """
        logger.info("Stopping node tracker...")
        self._running = False
        self._stop_event.set()

        # Stop network topology tracker
        if self._network_topology:
            self._network_topology.stop(timeout)

        # Wait for cleanup thread to finish
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=timeout)
            if self._cleanup_thread.is_alive():
                logger.warning("Cleanup thread did not stop in time")

        # Wait for RNS thread to finish
        if hasattr(self, '_rns_thread') and self._rns_thread and self._rns_thread.is_alive():
            self._rns_thread.join(timeout=timeout)
            if self._rns_thread.is_alive():
                logger.warning("RNS thread did not stop in time")

        self._save_cache()
        logger.info("Node tracker stopped")

    def add_node(self, node: UnifiedNode):
        """Add or update a node"""
        is_new = False
        with self._lock:
            existing = self._nodes.get(node.id)
            if existing:
                # Merge data
                self._merge_node(existing, node)
            else:
                # Evict oldest offline nodes if at capacity
                if len(self._nodes) >= self.MAX_NODES:
                    self._evict_stale_nodes()
                self._nodes[node.id] = node
                is_new = True
                logger.debug(f"Added new node: {node.id} ({node.name})")

            self._notify_callbacks("update", node)

        # Add topology edge for Meshtastic nodes (outside lock to avoid deadlock)
        # This ensures Meshtastic nodes appear in the D3.js topology graph
        if self._network_topology and node.network in ("meshtastic", "both"):
            try:
                self._network_topology.add_edge(
                    source_id="local",
                    dest_id=node.id,
                    hops=node.hops or 0,
                    snr=node.snr,
                    rssi=node.rssi,
                )
            except Exception as e:
                logger.debug(f"Could not add topology edge for {node.id}: {e}")

    def _evict_stale_nodes(self):
        """Evict oldest offline nodes to stay within MAX_NODES. Called under _lock."""
        offline = [
            (nid, n) for nid, n in self._nodes.items()
            if not n.is_online
        ]
        if not offline:
            # All online — evict oldest by last_seen
            offline = list(self._nodes.items())

        # Sort by last_seen ascending (oldest first)
        offline.sort(key=lambda x: x[1].last_seen or datetime.min)

        # Evict 10% to avoid frequent evictions
        evict_count = max(1, len(self._nodes) // 10)
        for nid, _ in offline[:evict_count]:
            del self._nodes[nid]

        if evict_count > 0:
            logger.info(f"Evicted {evict_count} stale nodes (capacity: {self.MAX_NODES})")

    def remove_node(self, node_id: str):
        """Remove a node"""
        with self._lock:
            if node_id in self._nodes:
                node = self._nodes.pop(node_id)
                self._notify_callbacks("remove", node)
                logger.debug(f"Removed node: {node_id}")

    def get_node(self, node_id: str) -> Optional[UnifiedNode]:
        """Get a node by ID"""
        with self._lock:
            return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[UnifiedNode]:
        """Get all tracked nodes"""
        with self._lock:
            return list(self._nodes.values())

    def get_meshtastic_nodes(self) -> List[UnifiedNode]:
        """Get only Meshtastic nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("meshtastic", "both")]

    def get_rns_nodes(self) -> List[UnifiedNode]:
        """Get only RNS nodes"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.network in ("rns", "both")]

    def get_node_by_mesh_id(self, meshtastic_id: str) -> Optional[UnifiedNode]:
        """Get a node by its Meshtastic ID (e.g., !abcd1234)"""
        with self._lock:
            for node in self._nodes.values():
                if node.meshtastic_id == meshtastic_id:
                    return node
            return None

    def get_node_by_rns_hash(self, rns_hash: bytes) -> Optional[UnifiedNode]:
        """Get a node by its RNS destination hash"""
        with self._lock:
            for node in self._nodes.values():
                if node.rns_hash == rns_hash:
                    return node
            return None

    def get_nodes_with_position(self) -> List[UnifiedNode]:
        """Get nodes that have valid positions"""
        with self._lock:
            return [n for n in self._nodes.values()
                    if n.position and n.position.is_valid()]

    def get_online_nodes(self) -> List[UnifiedNode]:
        """Get online nodes only"""
        with self._lock:
            return [n for n in self._nodes.values() if n.is_online]

    def get_stats(self) -> dict:
        """Get tracker statistics"""
        with self._lock:
            nodes = list(self._nodes.values())
            return {
                "total": len(nodes),
                "meshtastic": sum(1 for n in nodes if n.network in ("meshtastic", "both")),
                "rns": sum(1 for n in nodes if n.network in ("rns", "both")),
                "online": sum(1 for n in nodes if n.is_online),
                "with_position": sum(1 for n in nodes if n.position and n.position.is_valid()),
                "gateways": sum(1 for n in nodes if n.is_gateway),
            }

    def register_callback(self, callback: Callable):
        """Register a callback for node updates"""
        with self._lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister a callback"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _merge_node(self, existing: UnifiedNode, new: UnifiedNode):
        """Merge new node data into existing node"""
        # Update network type if we see it on both
        if existing.network != new.network:
            existing.network = "both"

        # Update identifiers
        if new.meshtastic_id:
            existing.meshtastic_id = new.meshtastic_id
        if new.rns_hash:
            existing.rns_hash = new.rns_hash

        # Update name if we have a better one
        if new.name and (not existing.name or existing.name.startswith("!")):
            existing.name = new.name
        if new.short_name:
            existing.short_name = new.short_name

        # Update position if newer
        if new.position.is_valid():
            existing.position = new.position

        # Update telemetry if newer
        if new.telemetry.timestamp:
            existing.telemetry = new.telemetry

        # Update metrics with signal quality trending
        if new.snr is not None or new.rssi is not None:
            existing.record_signal_quality(snr=new.snr, rssi=new.rssi)
        if new.hops is not None:
            existing.hops = new.hops

        # Update hardware info
        if new.hardware_model:
            existing.hardware_model = new.hardware_model
        if new.firmware_version:
            existing.firmware_version = new.firmware_version
        if new.role:
            existing.role = new.role

        # Update status
        existing.is_gateway = existing.is_gateway or new.is_gateway
        existing.update_seen()

    def _notify_callbacks(self, event: str, node: UnifiedNode):
        """Notify registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(event, node)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _cleanup_loop(self):
        """Periodically check node timeouts and save cache"""
        while self._running:
            if self._stop_event.wait(60):
                break

            with self._lock:
                now = datetime.now()
                for node in self._nodes.values():
                    # Use state machine for timeout checking if available
                    if node._state_machine is not None:
                        node.check_timeout()
                    elif node.last_seen:
                        # Fallback to simple threshold check
                        age = (now - node.last_seen).total_seconds()
                        if age > self.OFFLINE_THRESHOLD:
                            node.is_online = False

            # Save cache every 5 minutes
            self._save_cache()

    def _load_cache(self):
        """Load node cache from file"""
        cache_file = self.get_cache_file()
        if not cache_file.exists():
            return

        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)

            for node_data in data.get('nodes', []):
                node = UnifiedNode(
                    id=node_data['id'],
                    network=node_data['network'],
                    name=node_data.get('name', ''),
                    short_name=node_data.get('short_name', ''),
                    meshtastic_id=node_data.get('meshtastic_id'),
                    rns_hash=bytes.fromhex(node_data['rns_hash']) if node_data.get('rns_hash') else None,
                    hardware_model=node_data.get('hardware_model'),
                    role=node_data.get('role'),
                    is_online=False,  # Assume offline until we hear from them
                )
                # Restore last_seen from cache
                if node_data.get('last_seen'):
                    try:
                        node.last_seen = datetime.fromisoformat(node_data['last_seen'])
                    except (ValueError, TypeError):
                        pass
                # Restore position from cache
                pos_data = node_data.get('position')
                if pos_data and isinstance(pos_data, dict):
                    node.position = Position(
                        latitude=pos_data.get('latitude', 0.0),
                        longitude=pos_data.get('longitude', 0.0),
                        altitude=pos_data.get('altitude', 0.0),
                    )
                # Restore signal history from cache
                snr_history = node_data.get('snr_history', [])
                for sample in snr_history:
                    try:
                        ts = datetime.fromisoformat(sample['timestamp'])
                        node.snr_history.append(SignalSample(timestamp=ts, value=sample['value']))
                    except (KeyError, ValueError, TypeError):
                        pass
                rssi_history = node_data.get('rssi_history', [])
                for sample in rssi_history:
                    try:
                        ts = datetime.fromisoformat(sample['timestamp'])
                        node.rssi_history.append(SignalSample(timestamp=ts, value=sample['value']))
                    except (KeyError, ValueError, TypeError):
                        pass
                # Restore current SNR/RSSI values
                if node_data.get('snr') is not None:
                    node.snr = node_data['snr']
                if node_data.get('rssi') is not None:
                    node.rssi = node_data['rssi']
                # Restore state machine from cache if available
                if NODE_STATE_AVAILABLE and node_data.get('state_machine'):
                    try:
                        from .node_state import NodeStateMachine
                        node._state_machine = NodeStateMachine.from_dict(node_data['state_machine'])
                    except Exception as e:
                        logger.debug(f"Could not restore state machine: {e}")
                # Restore favorites from cache (BaseUI 2.7+)
                node.is_favorite = node_data.get('is_favorite', False)
                if node_data.get('favorite_updated'):
                    try:
                        node.favorite_updated = datetime.fromisoformat(node_data['favorite_updated'])
                    except (ValueError, TypeError):
                        pass
                self._nodes[node.id] = node

            logger.info(f"Loaded {len(self._nodes)} nodes from cache")

        except Exception as e:
            logger.warning(f"Failed to load node cache: {e}")

    def _save_cache(self):
        """Save node cache to file"""
        try:
            cache_file = self.get_cache_file()
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                # Include signal history in cache for persistence
                nodes_data = [n.to_dict(include_signal_history=True) for n in self._nodes.values()]

            cache_data = {
                'version': 1,
                'saved_at': datetime.now().isoformat(),
                'nodes': nodes_data
            }

            from utils.paths import atomic_write_text
            atomic_write_text(cache_file, json.dumps(cache_data, indent=2))

            # Also save to /tmp for web API access (cross-process sharing)
            try:
                tmp_path = '/tmp/meshforge_rns_nodes.json'
                if os.path.islink(tmp_path):
                    logger.warning(f"Refusing to write to symlink: {tmp_path}")
                else:
                    fd = os.open(
                        tmp_path,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                        0o644
                    )
                    with os.fdopen(fd, 'w') as f:
                        json.dump(cache_data, f)
            except Exception as e:
                logger.debug(f"Could not save web API cache: {e}")

        except Exception as e:
            logger.warning(f"Failed to save node cache: {e}")

    def to_geojson(self) -> dict:
        """Export nodes as GeoJSON for map display"""
        features = []

        for node in self.get_nodes_with_position():
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        node.position.longitude,
                        node.position.latitude
                    ]
                },
                "properties": {
                    "id": node.id,
                    "name": node.name,
                    "network": node.network,
                    "is_online": node.is_online,
                    "is_local": node.is_local,
                    "is_gateway": node.is_gateway,
                    "snr": node.snr,
                    "battery": node.telemetry.battery_level,
                    "last_seen": node.get_age_string(),
                }
            }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features
        }

    def _load_known_rns_destinations(self, RNS):
        """Load known destinations from RNS path table and identity cache.

        Priority order (most complete first):
        1. RNS.Transport.path_table - complete routing table from rnsd
        2. RNS.Identity.known_destinations - cached identities
        3. RNS.Transport.destinations - local destinations only (fallback)
        """
        try:
            known_count = 0

            # PRIMARY: Check path_table - contains ALL destinations rnsd knows about
            # This is the complete routing table, updated in real-time
            if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
                for dest_hash, path_data in RNS.Transport.path_table.items():
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                # Extract hop count from path tuple if available
                                hops = 0
                                if isinstance(path_data, tuple) and len(path_data) > 1:
                                    hops = path_data[1]

                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                # Store hop count for later use
                                if hasattr(node, 'hops'):
                                    node.hops = hops
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from path_table: {dest_hash.hex()[:8]} ({hops} hops)")
                    except Exception as e:
                        logger.debug(f"Error loading from path_table: {e}")

            # SECONDARY: Check identity known destinations (for any missed in path_table)
            if hasattr(RNS.Identity, 'known_destinations') and RNS.Identity.known_destinations:
                known_dests = RNS.Identity.known_destinations
                # Handle both dict (hash->identity) and list (hashes) formats
                if isinstance(known_dests, dict):
                    dest_hashes = known_dests.keys()
                else:
                    dest_hashes = known_dests

                for dest_hash in dest_hashes:
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            node_id = f"rns_{dest_hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                                logger.debug(f"Loaded from known_destinations: {dest_hash.hex()[:8]}")
                    except Exception as e:
                        logger.debug(f"Error loading known identity: {e}")

            # TERTIARY: Check Transport.destinations (local only - least useful)
            if hasattr(RNS.Transport, 'destinations') and RNS.Transport.destinations:
                destinations = RNS.Transport.destinations
                if isinstance(destinations, dict):
                    dest_items = destinations.values()
                elif isinstance(destinations, list):
                    dest_items = destinations
                else:
                    dest_items = []

                for dest in dest_items:
                    try:
                        if hasattr(dest, 'hash'):
                            node_id = f"rns_{dest.hash.hex()[:16]}"
                            if node_id not in self._nodes:
                                node = UnifiedNode.from_rns(dest.hash, name="", app_data=None)
                                self.add_node(node)
                                known_count += 1
                    except Exception as e:
                        logger.debug(f"Error loading destination: {e}")

            if known_count > 0:
                logger.info(f"Loaded {known_count} known RNS destinations")
            else:
                logger.debug("No known RNS destinations found (path_table may be empty)")

        except Exception as e:
            logger.debug(f"Could not load known RNS destinations: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data, aspect: str = None):
        """Handle RNS announce for node discovery.

        Uses the RNS service registry (if available) for multi-service parsing,
        or falls back to legacy LXMF-only parsing.

        Args:
            dest_hash: 16-byte destination hash
            announced_identity: RNS Identity object
            app_data: Raw announce app_data bytes
            aspect: Optional aspect filter from announce handler
        """
        try:
            hash_short = dest_hash.hex()[:8]
            service_info = None
            display_name = ""

            # Use service registry for enhanced parsing if available
            if self._service_registry and RNS_SERVICES_AVAILABLE:
                event = self._service_registry.parse_announce(
                    dest_hash, announced_identity, app_data, aspect
                )
                service_info = event.service_info
                display_name = event.raw_name

                service_type_name = service_info.service_type.name if service_info else "UNKNOWN"
                logger.debug(f"Parsed announce {hash_short}: type={service_type_name}, name={display_name or 'unnamed'}")
            else:
                # Legacy fallback: simple UTF-8 decode
                if app_data:
                    try:
                        display_name = app_data.decode('utf-8', errors='ignore').strip()
                        display_name = ''.join(c for c in display_name if c.isprintable())
                    except Exception as e:
                        logger.debug(f"Could not decode RNS display name: {e}")

            # Create node from announce with service info
            node = UnifiedNode.from_rns(
                dest_hash,
                name=display_name,
                app_data=app_data,
                service_info=service_info,
                aspect=aspect
            )
            self.add_node(node)

            # Update topology edge
            if self._network_topology:
                node_id = f"rns_{dest_hash.hex()[:16]}"
                self._network_topology.add_edge(
                    source_id="local",
                    dest_id=node_id,
                    dest_hash=dest_hash,
                    hops=node.hops or 0,
                )

            service_desc = f"[{node.service_type}]" if node.service_type else ""
            logger.info(f"Discovered RNS node: {hash_short} ({display_name or 'unnamed'}) {service_desc}")

        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    def _on_topology_event(self, event: 'TopologyEvent'):
        """Handle topology change events.

        Updates node information when path table changes are detected.
        """
        if not RNS_SERVICES_AVAILABLE or event.dest_hash is None:
            return

        try:
            node_id = f"rns_{event.dest_hash.hex()[:16]}"

            with self._lock:
                node = self._nodes.get(node_id)
                if node:
                    # Update hop count from topology event
                    if event.new_value is not None and isinstance(event.new_value, int):
                        node.hops = event.new_value
                        node.update_seen()
                        logger.debug(f"Updated node {node_id[:12]} hops: {event.new_value}")

        except Exception as e:
            logger.debug(f"Error handling topology event: {e}")

    # --- Topology API methods ---

    def get_topology_stats(self) -> Optional[Dict[str, Any]]:
        """Get network topology statistics.

        Returns:
            Dict with node_count, edge_count, avg_hops, etc. or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.get_topology_stats()
        return None

    def get_topology(self) -> Optional[Dict[str, Any]]:
        """Get full network topology as dictionary.

        Returns:
            Dict with nodes, edges, and stats or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.to_dict()
        return None

    def trace_path(self, dest_hash: bytes) -> Optional[Dict[str, Any]]:
        """Trace path to a destination through the network.

        Args:
            dest_hash: 16-byte destination hash

        Returns:
            Dict with path info or None if unavailable
        """
        if self._network_topology:
            return self._network_topology.trace_path(dest_hash)
        return None

    def get_recent_topology_events(self, count: int = 50) -> List[Dict[str, Any]]:
        """Get recent topology change events.

        Args:
            count: Maximum number of events to return

        Returns:
            List of event dicts
        """
        if self._network_topology:
            return self._network_topology.get_recent_events(count)
        return []

    def get_service_stats(self) -> Optional[Dict[str, int]]:
        """Get counts of discovered services by type.

        Returns:
            Dict mapping service type names to counts or None if unavailable
        """
        if self._service_registry:
            return self._service_registry.get_stats()
        return None
