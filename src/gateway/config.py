"""
Gateway Configuration Management
Handles persistent configuration for RNS-Meshtastic bridge
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback: Get real user's home directory, even when running with sudo."""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


@dataclass
class MeshtasticConfig:
    """Meshtastic connection configuration"""
    host: str = "localhost"
    port: int = 4403
    channel: int = 0  # Primary channel for gateway messages
    use_mqtt: bool = False
    mqtt_topic: str = ""


@dataclass
class RNSConfig:
    """Reticulum Network Stack configuration"""
    config_dir: str = ""  # Empty = default ~/.reticulum
    identity_name: str = "meshforge_gateway"
    announce_interval: int = 300  # seconds
    propagation_node: str = ""  # Optional propagation node address


@dataclass
class RNSOverMeshtasticConfig:
    """
    RNS Over Meshtastic transport configuration.

    When enabled, RNS uses Meshtastic as a network transport layer,
    allowing RNS packets to traverse LoRa mesh networks.

    Based on: https://github.com/landandair/RNS_Over_Meshtastic
    """
    enabled: bool = False

    # Connection type: "serial", "tcp", "ble"
    connection_type: str = "tcp"

    # Device path based on connection type:
    # - serial: /dev/ttyUSB0, /dev/ttyACM0
    # - tcp: localhost:4403 (meshtasticd)
    # - ble: device_name or MAC address
    device_path: str = "localhost:4403"

    # LoRa speed preset (0-8, maps to Meshtastic modem presets)
    # 8 = SHORT_TURBO (fastest, ~500 B/s, shortest range)
    # 6 = SHORT_FAST (~300 B/s)
    # 5 = SHORT_SLOW (~150 B/s)
    # 4 = MEDIUM_FAST (~100 B/s)
    # 0 = LONG_FAST (slowest, ~50 B/s, longest range)
    data_speed: int = 8  # Default: SHORT_TURBO for RNS

    # Mesh hop limit (1-7)
    hop_limit: int = 3

    # Packet handling
    fragment_timeout_sec: int = 30  # Discard incomplete after timeout
    max_pending_fragments: int = 100  # Prevent memory exhaustion

    # Monitoring
    enable_stats: bool = True
    stats_interval_sec: int = 60

    # Performance thresholds for alerts
    packet_loss_threshold: float = 0.1  # Alert if >10% loss
    latency_threshold_ms: int = 5000  # Alert if >5s roundtrip

    def get_throughput_estimate(self) -> dict:
        """Estimate throughput based on speed preset."""
        speed_info = {
            8: {'name': 'SHORT_TURBO', 'delay': 0.4, 'bps': 500, 'range': 'short'},
            7: {'name': 'SHORT_FAST+', 'delay': 0.5, 'bps': 400, 'range': 'short'},
            6: {'name': 'SHORT_FAST', 'delay': 1.0, 'bps': 300, 'range': 'medium'},
            5: {'name': 'SHORT_SLOW', 'delay': 3.0, 'bps': 150, 'range': 'medium-long'},
            4: {'name': 'MEDIUM_FAST', 'delay': 4.0, 'bps': 100, 'range': 'long'},
            3: {'name': 'MEDIUM_SLOW', 'delay': 5.0, 'bps': 80, 'range': 'long'},
            2: {'name': 'LONG_MODERATE', 'delay': 6.0, 'bps': 60, 'range': 'very long'},
            1: {'name': 'LONG_SLOW', 'delay': 7.0, 'bps': 55, 'range': 'very long'},
            0: {'name': 'LONG_FAST', 'delay': 8.0, 'bps': 50, 'range': 'maximum'},
        }
        return speed_info.get(self.data_speed, speed_info[8])


@dataclass
class RoutingRule:
    """Message routing rule between networks"""
    name: str
    enabled: bool = True
    direction: str = "bidirectional"  # "rns_to_mesh", "mesh_to_rns", "bidirectional"
    source_filter: str = ""  # Regex for source address filtering
    dest_filter: str = ""  # Regex for destination filtering
    message_filter: str = ""  # Regex for message content filtering
    transform: str = ""  # Optional message transformation
    priority: int = 0


@dataclass
class TelemetryConfig:
    """Telemetry sharing configuration"""
    share_position: bool = True
    share_battery: bool = True
    share_environment: bool = True
    position_precision: int = 5  # Decimal places for lat/lon
    update_interval: int = 60  # seconds


@dataclass
class GatewayConfig:
    """Complete gateway configuration"""
    enabled: bool = False
    auto_start: bool = False

    # Bridge mode: "message_bridge" or "rns_transport"
    # - message_bridge: Translates messages between RNS/LXMF and Meshtastic
    # - rns_transport: RNS uses Meshtastic as network transport layer
    bridge_mode: str = "message_bridge"

    # Network configurations
    meshtastic: MeshtasticConfig = field(default_factory=MeshtasticConfig)
    rns: RNSConfig = field(default_factory=RNSConfig)

    # RNS Over Meshtastic transport (used when bridge_mode="rns_transport")
    rns_transport: RNSOverMeshtasticConfig = field(default_factory=RNSOverMeshtasticConfig)

    # Routing (used when bridge_mode="message_bridge")
    routing_rules: List[RoutingRule] = field(default_factory=list)
    default_route: str = "bidirectional"

    # Telemetry
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

    # Logging
    log_level: str = "INFO"
    log_messages: bool = True

    # AI Diagnostics
    ai_diagnostics_enabled: bool = False
    snr_analysis: bool = True
    anomaly_detection: bool = False

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the configuration file path"""
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "gateway.json"

    @classmethod
    def load(cls) -> 'GatewayConfig':
        """Load configuration from file"""
        config_path = cls.get_config_path()

        if not config_path.exists():
            logger.info(f"No gateway config found, using defaults")
            return cls()

        try:
            with open(config_path, 'r') as f:
                data = json.load(f)

            # Handle RNSOverMeshtasticConfig separately (has method, can't use **)
            rns_transport_data = data.get('rns_transport', {})
            rns_transport = RNSOverMeshtasticConfig(
                enabled=rns_transport_data.get('enabled', False),
                connection_type=rns_transport_data.get('connection_type', 'tcp'),
                device_path=rns_transport_data.get('device_path', 'localhost:4403'),
                data_speed=rns_transport_data.get('data_speed', 8),
                hop_limit=rns_transport_data.get('hop_limit', 3),
                fragment_timeout_sec=rns_transport_data.get('fragment_timeout_sec', 30),
                max_pending_fragments=rns_transport_data.get('max_pending_fragments', 100),
                enable_stats=rns_transport_data.get('enable_stats', True),
                stats_interval_sec=rns_transport_data.get('stats_interval_sec', 60),
                packet_loss_threshold=rns_transport_data.get('packet_loss_threshold', 0.1),
                latency_threshold_ms=rns_transport_data.get('latency_threshold_ms', 5000),
            )

            # Reconstruct nested dataclasses
            config = cls(
                enabled=data.get('enabled', False),
                auto_start=data.get('auto_start', False),
                bridge_mode=data.get('bridge_mode', 'message_bridge'),
                meshtastic=MeshtasticConfig(**data.get('meshtastic', {})),
                rns=RNSConfig(**data.get('rns', {})),
                rns_transport=rns_transport,
                routing_rules=[RoutingRule(**r) for r in data.get('routing_rules', [])],
                default_route=data.get('default_route', 'bidirectional'),
                telemetry=TelemetryConfig(**data.get('telemetry', {})),
                log_level=data.get('log_level', 'INFO'),
                log_messages=data.get('log_messages', True),
                ai_diagnostics_enabled=data.get('ai_diagnostics_enabled', False),
                snr_analysis=data.get('snr_analysis', True),
                anomaly_detection=data.get('anomaly_detection', False),
            )

            logger.info(f"Loaded gateway config from {config_path}")
            return config

        except Exception as e:
            logger.error(f"Failed to load gateway config: {e}")
            return cls()

    def save(self) -> bool:
        """Save configuration to file"""
        config_path = self.get_config_path()

        try:
            # Convert RNSOverMeshtasticConfig manually (has method that shouldn't be serialized)
            rns_transport_data = {
                'enabled': self.rns_transport.enabled,
                'connection_type': self.rns_transport.connection_type,
                'device_path': self.rns_transport.device_path,
                'data_speed': self.rns_transport.data_speed,
                'hop_limit': self.rns_transport.hop_limit,
                'fragment_timeout_sec': self.rns_transport.fragment_timeout_sec,
                'max_pending_fragments': self.rns_transport.max_pending_fragments,
                'enable_stats': self.rns_transport.enable_stats,
                'stats_interval_sec': self.rns_transport.stats_interval_sec,
                'packet_loss_threshold': self.rns_transport.packet_loss_threshold,
                'latency_threshold_ms': self.rns_transport.latency_threshold_ms,
            }

            # Convert to dict with nested dataclasses
            data = {
                'enabled': self.enabled,
                'auto_start': self.auto_start,
                'bridge_mode': self.bridge_mode,
                'meshtastic': asdict(self.meshtastic),
                'rns': asdict(self.rns),
                'rns_transport': rns_transport_data,
                'routing_rules': [asdict(r) for r in self.routing_rules],
                'default_route': self.default_route,
                'telemetry': asdict(self.telemetry),
                'log_level': self.log_level,
                'log_messages': self.log_messages,
                'ai_diagnostics_enabled': self.ai_diagnostics_enabled,
                'snr_analysis': self.snr_analysis,
                'anomaly_detection': self.anomaly_detection,
            }

            with open(config_path, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved gateway config to {config_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save gateway config: {e}")
            return False

    def add_routing_rule(self, rule: RoutingRule):
        """Add a routing rule"""
        self.routing_rules.append(rule)
        self.routing_rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_routing_rule(self, name: str):
        """Remove a routing rule by name"""
        self.routing_rules = [r for r in self.routing_rules if r.name != name]

    def get_default_rules(self) -> List[RoutingRule]:
        """Get default routing rules"""
        return [
            RoutingRule(
                name="broadcast_mesh_to_rns",
                direction="mesh_to_rns",
                source_filter="",
                dest_filter="^!ffffffff$",  # Broadcast address
                message_filter="",
                priority=10,
            ),
            RoutingRule(
                name="broadcast_rns_to_mesh",
                direction="rns_to_mesh",
                source_filter="",
                dest_filter="",
                message_filter="",
                priority=10,
            ),
            RoutingRule(
                name="direct_messages",
                direction="bidirectional",
                source_filter="",
                dest_filter="^!(?!ffffffff)",  # Non-broadcast
                message_filter="",
                priority=5,
            ),
        ]
