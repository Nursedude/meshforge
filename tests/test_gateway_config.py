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
    MQTTBridgeConfig,
    MeshCoreConfig,
    RNSConfig,
    RoutingRule,
    TelemetryConfig,
    validate_log_level,
    validate_channel,
    validate_baud_rate,
    validate_position_precision,
    validate_update_interval,
    validate_hostname_config,
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

    def test_get_lxmf_destinations_empty_string(self):
        """Default empty string yields empty list."""
        assert RNSConfig().get_lxmf_destinations() == []

    def test_get_lxmf_destinations_legacy_string(self):
        """Single-string default (legacy) becomes a one-element list."""
        config = RNSConfig(default_lxmf_destination="6b1a0120941444587d7d1dc1bf6d64d7")
        assert config.get_lxmf_destinations() == ["6b1a0120941444587d7d1dc1bf6d64d7"]

    def test_get_lxmf_destinations_list(self):
        """List form returns the list verbatim."""
        config = RNSConfig(default_lxmf_destination=[
            "522c4ac1d2f9964e03e3782ef5b0224c",
            "d1df31d352ede66eac819a577da22b75",
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ])
        result = config.get_lxmf_destinations()
        assert len(result) == 3
        assert "522c4ac1d2f9964e03e3782ef5b0224c" in result

    def test_get_lxmf_destinations_filters_empty_and_non_strings(self):
        """Empty strings and non-string entries are dropped."""
        config = RNSConfig(default_lxmf_destination=[
            "6b1a0120941444587d7d1dc1bf6d64d7",
            "",
            None,
            123,
            "d1df31d352ede66eac819a577da22b75",
        ])
        assert config.get_lxmf_destinations() == [
            "6b1a0120941444587d7d1dc1bf6d64d7",
            "d1df31d352ede66eac819a577da22b75",
        ]

    def test_get_lxmf_destinations_unknown_type_returns_empty(self):
        """Unknown types (int, dict) return empty list rather than crashing."""
        config = RNSConfig(default_lxmf_destination=42)
        assert config.get_lxmf_destinations() == []
        config2 = RNSConfig(default_lxmf_destination={"a": 1})
        assert config2.get_lxmf_destinations() == []


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


class TestStaleHttpPortMigration:
    """Issue #62-pattern migration: saved http_port=443 is the stale
    default that shipped in rendered fleet configs while meshtasticd's
    web API lives on 9443. load() must rewrite it; any other saved
    value is operator intent and must survive.

    Live failure shape (moc, 2026-06-03): every send_text_direct to
    localhost:443 was connection-refused, the circuit breaker flapped
    open all day, and all TX rode the legacy session fallback.
    """

    def _write_and_load(self, tmp_path, data):
        config_file = tmp_path / "gateway.json"
        config_file.write_text(json.dumps(data))
        with patch.object(GatewayConfig, 'get_config_path',
                          return_value=config_file):
            return GatewayConfig.load()

    def test_fresh_default_is_9443(self):
        assert MeshtasticConfig().http_port == 9443

    def test_saved_443_migrates_to_9443(self, tmp_path):
        loaded = self._write_and_load(
            tmp_path, {"meshtastic": {"http_port": 443}})
        assert loaded.meshtastic.http_port == 9443

    def test_saved_custom_port_preserved(self, tmp_path):
        loaded = self._write_and_load(
            tmp_path, {"meshtastic": {"http_port": 8443}})
        assert loaded.meshtastic.http_port == 8443

    def test_saved_9443_untouched(self, tmp_path):
        loaded = self._write_and_load(
            tmp_path, {"meshtastic": {"http_port": 9443}})
        assert loaded.meshtastic.http_port == 9443

    def test_mesh_bridge_radios_migrate(self, tmp_path):
        """The moc3 shape: 443 baked into mesh_bridge primary AND
        secondary radio entries, not just the top-level meshtastic."""
        loaded = self._write_and_load(tmp_path, {
            "meshtastic": {"http_port": 443},
            "mesh_bridge": {
                "enabled": True,
                "primary": {"port": 4403, "http_port": 443},
                "secondary": {"port": 4404, "http_port": 443},
            },
        })
        assert loaded.meshtastic.http_port == 9443
        assert loaded.mesh_bridge.primary.http_port == 9443
        assert loaded.mesh_bridge.secondary.http_port == 9443

    def test_migration_does_not_mutate_caller_dict(self):
        data = {"http_port": 443}
        out = GatewayConfig._migrate_stale_http_port(data)
        assert out["http_port"] == 9443
        assert data["http_port"] == 443


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


class TestConfigValidation:
    """Tests for GatewayConfig.validate() schema validation.

    Config drift detection is mocked out because it checks for running
    rnsd processes and RNS config files that don't exist in CI/test.
    """

    @pytest.fixture(autouse=True)
    def _no_config_drift(self):
        """Disable config drift detection for validation tests."""
        with patch('src.gateway.config._HAS_CONFIG_DRIFT', False):
            yield

    def test_default_config_validates(self):
        """Default config should pass validation."""
        config = GatewayConfig()
        is_valid, errors = config.validate()
        # Filter only severity="error" items
        error_items = [e for e in errors if e.severity == "error"]
        assert is_valid is True, f"Default config invalid: {[str(e) for e in error_items]}"

    def test_invalid_bridge_mode(self):
        """Invalid bridge_mode should return error."""
        config = GatewayConfig(bridge_mode="nonexistent_mode")
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("bridge_mode" in e.field for e in errors)

    def test_invalid_meshtastic_port(self):
        """Out-of-range meshtastic port should return error."""
        config = GatewayConfig()
        config.meshtastic.port = 0
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("meshtastic.port" in e.field for e in errors)

    def test_invalid_meshtastic_port_too_high(self):
        """Port above 65535 should return error."""
        config = GatewayConfig()
        config.meshtastic.port = 99999
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("meshtastic.port" in e.field for e in errors)

    def test_invalid_channel(self):
        """Channel 8 should return error (valid range 0-7)."""
        config = GatewayConfig()
        config.meshtastic.channel = 8
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("meshtastic.channel" in e.field for e in errors)

    def test_valid_channel_range(self):
        """Channels 0-7 should all pass."""
        for ch in range(8):
            config = GatewayConfig()
            config.meshtastic.channel = ch
            is_valid, errors = config.validate()
            error_items = [e for e in errors if e.severity == "error"]
            assert is_valid is True, f"Channel {ch} failed: {error_items}"

    def test_invalid_log_level(self):
        """Invalid log level should return error."""
        config = GatewayConfig(log_level="VERBOSE")
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("log_level" in e.field for e in errors)

    def test_valid_log_levels(self):
        """All standard Python log levels should pass."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = GatewayConfig(log_level=level)
            is_valid, errors = config.validate()
            level_errors = [e for e in errors if "log_level" in e.field]
            assert not level_errors, f"Log level {level} failed: {level_errors}"

    def test_invalid_position_precision(self):
        """Position precision out of range should return error."""
        config = GatewayConfig()
        config.telemetry.position_precision = 11
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("position_precision" in e.field for e in errors)

    def test_valid_position_precision(self):
        """Position precision 0-10 should pass."""
        config = GatewayConfig()
        config.telemetry.position_precision = 5
        is_valid, errors = config.validate()
        precision_errors = [e for e in errors
                           if "position_precision" in e.field and e.severity == "error"]
        assert not precision_errors

    def test_update_interval_too_short_warning(self):
        """Very short update interval should give warning (not error)."""
        config = GatewayConfig()
        config.telemetry.update_interval = 5
        is_valid, errors = config.validate()
        # Warnings don't make config invalid
        assert is_valid is True
        warnings = [e for e in errors
                    if "update_interval" in e.field and e.severity == "warning"]
        assert len(warnings) == 1

    def test_mqtt_port_validated_in_mqtt_mode(self):
        """MQTT bridge port should be validated when in mqtt_bridge mode."""
        config = GatewayConfig(bridge_mode="mqtt_bridge")
        config.mqtt_bridge.port = 0
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("mqtt_bridge.port" in e.field for e in errors)

    def test_mqtt_broker_empty_warning(self):
        """Empty MQTT broker in mqtt_bridge mode should give warning."""
        config = GatewayConfig(bridge_mode="mqtt_bridge")
        config.mqtt_bridge.broker = ""
        is_valid, errors = config.validate()
        broker_issues = [e for e in errors if "mqtt_bridge.broker" in e.field]
        assert len(broker_issues) >= 1

    def test_meshcore_tcp_port_validated(self):
        """MeshCore TCP port should be validated in meshcore_bridge mode."""
        config = GatewayConfig(bridge_mode="meshcore_bridge")
        config.meshcore.connection_type = "tcp"
        config.meshcore.tcp_port = 0
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("meshcore.tcp_port" in e.field for e in errors)

    def test_meshcore_baud_rate_warning(self):
        """Non-standard baud rate should give warning."""
        config = GatewayConfig(bridge_mode="meshcore_bridge")
        config.meshcore.connection_type = "serial"
        config.meshcore.baud_rate = 12345
        is_valid, errors = config.validate()
        # Warning, not error — config is still valid
        assert is_valid is True
        baud_warnings = [e for e in errors
                         if "baud_rate" in e.field and e.severity == "warning"]
        assert len(baud_warnings) == 1

    def test_meshtastic_mqtt_port_validated_when_enabled(self):
        """Meshtastic MQTT port should be validated when use_mqtt=True."""
        config = GatewayConfig()
        config.meshtastic.use_mqtt = True
        config.meshtastic.mqtt_port = 99999
        is_valid, errors = config.validate()
        assert is_valid is False
        assert any("meshtastic.mqtt_port" in e.field for e in errors)

    def test_warnings_dont_fail_validation(self):
        """Config with only warnings should still be valid."""
        config = GatewayConfig()
        config.telemetry.update_interval = 5  # Warning: too short
        is_valid, errors = config.validate()
        assert is_valid is True
        assert any(e.severity == "warning" for e in errors)

    # --- Standalone validator function tests ---

    def test_validate_log_level_accepts_valid(self):
        assert validate_log_level("INFO", "test") is None
        assert validate_log_level("debug", "test") is None  # Case-insensitive

    def test_validate_log_level_rejects_invalid(self):
        err = validate_log_level("VERBOSE", "test")
        assert err is not None
        assert err.severity == "error"

    def test_validate_channel_accepts_valid(self):
        assert validate_channel(0, "test") is None
        assert validate_channel(7, "test") is None

    def test_validate_channel_rejects_invalid(self):
        assert validate_channel(-1, "test") is not None
        assert validate_channel(8, "test") is not None

    def test_validate_baud_rate_accepts_standard(self):
        assert validate_baud_rate(115200, "test") is None
        assert validate_baud_rate(9600, "test") is None

    def test_validate_baud_rate_warns_nonstandard(self):
        err = validate_baud_rate(12345, "test")
        assert err is not None
        assert err.severity == "warning"

    def test_validate_position_precision_bounds(self):
        assert validate_position_precision(0, "test") is None
        assert validate_position_precision(10, "test") is None
        assert validate_position_precision(11, "test") is not None
        assert validate_position_precision(-1, "test") is not None

    def test_validate_update_interval_bounds(self):
        assert validate_update_interval(60, "test") is None
        err = validate_update_interval(5, "test")
        assert err is not None and err.severity == "warning"
        err = validate_update_interval(100000, "test")
        assert err is not None and err.severity == "warning"

    def test_validate_hostname_accepts_valid(self):
        assert validate_hostname_config("localhost", "test") is None
        assert validate_hostname_config("192.168.1.1", "test") is None
        assert validate_hostname_config("mesh.local", "test") is None

    def test_validate_hostname_warns_empty(self):
        err = validate_hostname_config("", "test")
        assert err is not None
        assert err.severity == "warning"

    def test_validate_hostname_rejects_invalid(self):
        err = validate_hostname_config("-invalid", "test")
        assert err is not None
        assert err.severity == "error"


class TestMeshBridgeChannelAllowList:
    """mesh_bridge.channels — optional channel-index allow-list.
    Empty/absent = bridge all (backward compatible); malformed entries
    must be rejected loudly (validate + bridge_cli startup refusal)."""

    def _write_and_load(self, tmp_path, data):
        config_file = tmp_path / "gateway.json"
        config_file.write_text(json.dumps(data))
        with patch.object(GatewayConfig, 'get_config_path',
                          return_value=config_file):
            return GatewayConfig.load()

    def test_default_is_empty_list(self):
        from gateway.config import MeshtasticBridgeConfig
        assert MeshtasticBridgeConfig().channels == []

    def test_load_parses_channels(self, tmp_path):
        loaded = self._write_and_load(tmp_path, {
            "mesh_bridge": {"enabled": True, "channels": [2, 3]},
        })
        assert loaded.mesh_bridge.channels == [2, 3]

    def test_load_absent_channels_is_empty(self, tmp_path):
        loaded = self._write_and_load(tmp_path, {
            "mesh_bridge": {"enabled": True},
        })
        assert loaded.mesh_bridge.channels == []

    def test_save_load_round_trip(self, tmp_path):
        config_file = tmp_path / "gateway.json"
        with patch.object(GatewayConfig, 'get_config_path',
                          return_value=config_file):
            config = GatewayConfig()
            config.mesh_bridge.enabled = True
            config.mesh_bridge.channels = [2]
            assert config.save() is True
            loaded = GatewayConfig.load()
        assert loaded.mesh_bridge.channels == [2]

    def test_validate_accepts_valid_channels(self):
        config = GatewayConfig()
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = [0, 2, 7]
        _, all_errors = config.validate()
        errors = [e for e in all_errors
                  if "channels" in e.field and e.severity == "error"]
        assert errors == []

    def test_validate_rejects_out_of_range(self):
        config = GatewayConfig()
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = [8]
        _, all_errors = config.validate()
        errors = [e for e in all_errors if "channels" in e.field]
        assert len(errors) == 1
        assert "out of range" in errors[0].message

    def test_validate_rejects_non_int_entries(self):
        config = GatewayConfig()
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = ["2", True, -1]
        _, all_errors = config.validate()
        errors = [e for e in all_errors if "channels" in e.field]
        # "2" (str), True (bool), -1 (range) — all three rejected
        assert len(errors) == 3

    def test_validate_applies_when_enabled_without_mode(self):
        """Composable model: mesh_bridge.enabled=true alongside
        bridge_mode=mqtt_bridge must still be validated."""
        config = GatewayConfig(bridge_mode="mqtt_bridge")
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = [99]
        _, all_errors = config.validate()
        errors = [e for e in all_errors if "channels" in e.field]
        assert len(errors) == 1

    def test_bridge_cli_refuses_malformed_channels(self):
        """Startup refusal path: the systemd entrypoint must refuse-loud
        on a malformed allow-list rather than guess."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        config = GatewayConfig(bridge_mode="mqtt_bridge")
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = ["meshforge"]
        bridges = resolve_bridges(config)
        errs = validate_bridge_conflicts(config, bridges)
        assert any("mesh_bridge.channels" in e for e in errs)

    def test_bridge_cli_accepts_valid_channels(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from gateway.bridge_cli import resolve_bridges, validate_bridge_conflicts

        config = GatewayConfig(bridge_mode="mqtt_bridge")
        config.mesh_bridge.enabled = True
        config.mesh_bridge.channels = [2]
        bridges = resolve_bridges(config)
        errs = validate_bridge_conflicts(config, bridges)
        assert not any("channels" in e for e in errs)
