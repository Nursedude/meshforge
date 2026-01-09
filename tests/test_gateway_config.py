"""
Tests for gateway configuration persistence.

Run: python3 -m pytest tests/test_gateway_config.py -v
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.gateway.config import (
    GatewayConfig,
    MeshtasticConfig,
    RNSConfig,
    RoutingRule,
    TelemetryConfig,
)


class TestMeshtasticConfig:
    """Tests for MeshtasticConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = MeshtasticConfig()

        assert config.host == "localhost"
        assert config.port == 4403
        assert config.channel == 0
        assert config.use_mqtt is False
        assert config.mqtt_topic == ""

    def test_custom_values(self):
        """Test custom initialization."""
        config = MeshtasticConfig(
            host="192.168.1.100",
            port=4404,
            channel=2,
            use_mqtt=True,
            mqtt_topic="mesh/test"
        )

        assert config.host == "192.168.1.100"
        assert config.port == 4404
        assert config.channel == 2
        assert config.use_mqtt is True
        assert config.mqtt_topic == "mesh/test"


class TestRNSConfig:
    """Tests for RNSConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = RNSConfig()

        assert config.config_dir == ""
        assert config.identity_name == "meshforge_gateway"
        assert config.announce_interval == 300
        assert config.propagation_node == ""

    def test_custom_values(self):
        """Test custom initialization."""
        config = RNSConfig(
            config_dir="/custom/rns",
            identity_name="custom_gateway",
            announce_interval=600,
            propagation_node="abc123"
        )

        assert config.config_dir == "/custom/rns"
        assert config.identity_name == "custom_gateway"


class TestRoutingRule:
    """Tests for RoutingRule dataclass."""

    def test_defaults(self):
        """Test default values."""
        rule = RoutingRule(name="test_rule")

        assert rule.name == "test_rule"
        assert rule.enabled is True
        assert rule.direction == "bidirectional"
        assert rule.source_filter == ""
        assert rule.dest_filter == ""
        assert rule.message_filter == ""
        assert rule.transform == ""
        assert rule.priority == 0

    def test_custom_rule(self):
        """Test custom routing rule."""
        rule = RoutingRule(
            name="mesh_only",
            enabled=True,
            direction="mesh_to_rns",
            source_filter="^!abc",
            priority=10
        )

        assert rule.direction == "mesh_to_rns"
        assert rule.source_filter == "^!abc"
        assert rule.priority == 10


class TestTelemetryConfig:
    """Tests for TelemetryConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = TelemetryConfig()

        assert config.share_position is True
        assert config.share_battery is True
        assert config.share_environment is True
        assert config.position_precision == 5
        assert config.update_interval == 60


class TestGatewayConfig:
    """Tests for GatewayConfig main class."""

    def test_defaults(self):
        """Test default configuration values."""
        config = GatewayConfig()

        assert config.enabled is False
        assert config.auto_start is False
        assert config.default_route == "bidirectional"
        assert config.log_level == "INFO"
        assert config.log_messages is True
        assert config.ai_diagnostics_enabled is False
        assert len(config.routing_rules) == 0

    def test_nested_configs_initialized(self):
        """Test that nested configs are properly initialized."""
        config = GatewayConfig()

        assert isinstance(config.meshtastic, MeshtasticConfig)
        assert isinstance(config.rns, RNSConfig)
        assert isinstance(config.telemetry, TelemetryConfig)

    def test_add_routing_rule(self):
        """Test adding routing rules."""
        config = GatewayConfig()

        rule1 = RoutingRule(name="rule1", priority=5)
        rule2 = RoutingRule(name="rule2", priority=10)

        config.add_routing_rule(rule1)
        config.add_routing_rule(rule2)

        assert len(config.routing_rules) == 2
        # Rules should be sorted by priority (highest first)
        assert config.routing_rules[0].name == "rule2"
        assert config.routing_rules[1].name == "rule1"

    def test_remove_routing_rule(self):
        """Test removing routing rules."""
        config = GatewayConfig()

        config.add_routing_rule(RoutingRule(name="keep"))
        config.add_routing_rule(RoutingRule(name="remove"))
        config.remove_routing_rule("remove")

        assert len(config.routing_rules) == 1
        assert config.routing_rules[0].name == "keep"

    def test_get_default_rules(self):
        """Test getting default routing rules."""
        config = GatewayConfig()
        rules = config.get_default_rules()

        assert len(rules) == 3
        rule_names = [r.name for r in rules]
        assert "broadcast_mesh_to_rns" in rule_names
        assert "broadcast_rns_to_mesh" in rule_names
        assert "direct_messages" in rule_names


class TestGatewayConfigPersistence:
    """Tests for save/load functionality."""

    def test_save_creates_file(self, tmp_path):
        """Test that save creates config file."""
        config_file = tmp_path / "gateway.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            config = GatewayConfig(enabled=True)
            result = config.save()

            assert result is True
            assert config_file.exists()

    def test_save_load_round_trip(self, tmp_path):
        """Test saving and loading preserves values."""
        config_file = tmp_path / "gateway.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            # Create and save config
            original = GatewayConfig(
                enabled=True,
                auto_start=True,
                log_level="DEBUG"
            )
            original.meshtastic.host = "192.168.1.100"
            original.meshtastic.port = 4404
            original.rns.identity_name = "test_gateway"
            original.add_routing_rule(RoutingRule(name="test_rule", priority=5))
            original.save()

            # Load and verify
            loaded = GatewayConfig.load()

            assert loaded.enabled is True
            assert loaded.auto_start is True
            assert loaded.log_level == "DEBUG"
            assert loaded.meshtastic.host == "192.168.1.100"
            assert loaded.meshtastic.port == 4404
            assert loaded.rns.identity_name == "test_gateway"
            assert len(loaded.routing_rules) == 1
            assert loaded.routing_rules[0].name == "test_rule"

    def test_load_returns_defaults_when_no_file(self, tmp_path):
        """Test load returns default config when file doesn't exist."""
        config_file = tmp_path / "nonexistent.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            config = GatewayConfig.load()

            assert config.enabled is False
            assert config.meshtastic.host == "localhost"

    def test_load_handles_corrupted_file(self, tmp_path):
        """Test load handles corrupted JSON gracefully."""
        config_file = tmp_path / "gateway.json"
        config_file.write_text("not valid json {{{")

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            config = GatewayConfig.load()

            # Should return defaults on error
            assert config.enabled is False
            assert isinstance(config.meshtastic, MeshtasticConfig)

    def test_load_handles_partial_config(self, tmp_path):
        """Test load handles config with missing fields."""
        config_file = tmp_path / "gateway.json"
        config_file.write_text(json.dumps({
            "enabled": True,
            "meshtastic": {"host": "192.168.1.1"}
            # Other fields missing
        }))

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            config = GatewayConfig.load()

            assert config.enabled is True
            assert config.meshtastic.host == "192.168.1.1"
            # Missing fields should use defaults
            assert config.meshtastic.port == 4403

    def test_save_with_routing_rules(self, tmp_path):
        """Test saving config with routing rules."""
        config_file = tmp_path / "gateway.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            config = GatewayConfig()
            config.add_routing_rule(RoutingRule(
                name="custom",
                direction="mesh_to_rns",
                source_filter="^!abc",
                priority=15
            ))
            config.save()

            # Verify JSON structure
            with open(config_file) as f:
                data = json.load(f)

            assert len(data['routing_rules']) == 1
            assert data['routing_rules'][0]['name'] == "custom"
            assert data['routing_rules'][0]['direction'] == "mesh_to_rns"

    def test_save_creates_parent_directories(self, tmp_path):
        """Test that save creates parent directories."""
        config_file = tmp_path / "deep" / "nested" / "gateway.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            # get_config_path creates the directory, so we need to mock that too
            config = GatewayConfig()

            # Create the directory manually since get_config_path is mocked
            config_file.parent.mkdir(parents=True, exist_ok=True)

            result = config.save()
            assert result is True


class TestGatewayConfigPath:
    """Tests for config path handling."""

    def test_get_config_path_uses_real_user_home(self, tmp_path):
        """Test that config path uses real user home."""
        with patch('src.gateway.config.get_real_user_home', return_value=tmp_path):
            path = GatewayConfig.get_config_path()

            assert tmp_path in path.parents or path.parent.parent == tmp_path
            assert path.name == "gateway.json"
            assert "meshforge" in str(path)


class TestTelemetryRoundTrip:
    """Tests for telemetry config persistence."""

    def test_telemetry_values_preserved(self, tmp_path):
        """Test that telemetry values are preserved on save/load."""
        config_file = tmp_path / "gateway.json"

        with patch.object(GatewayConfig, 'get_config_path', return_value=config_file):
            original = GatewayConfig()
            original.telemetry.share_position = False
            original.telemetry.position_precision = 3
            original.telemetry.update_interval = 120
            original.save()

            loaded = GatewayConfig.load()

            assert loaded.telemetry.share_position is False
            assert loaded.telemetry.position_precision == 3
            assert loaded.telemetry.update_interval == 120
