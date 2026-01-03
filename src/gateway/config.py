"""
Gateway Configuration Management
Handles persistent configuration for RNS-Meshtastic bridge
"""

import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


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

    # Network configurations
    meshtastic: MeshtasticConfig = field(default_factory=MeshtasticConfig)
    rns: RNSConfig = field(default_factory=RNSConfig)

    # Routing
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
        config_dir = Path.home() / ".config" / "meshforge"
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

            # Reconstruct nested dataclasses
            config = cls(
                enabled=data.get('enabled', False),
                auto_start=data.get('auto_start', False),
                meshtastic=MeshtasticConfig(**data.get('meshtastic', {})),
                rns=RNSConfig(**data.get('rns', {})),
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
            # Convert to dict with nested dataclasses
            data = {
                'enabled': self.enabled,
                'auto_start': self.auto_start,
                'meshtastic': asdict(self.meshtastic),
                'rns': asdict(self.rns),
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
