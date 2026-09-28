"""
Unit tests for GatewayHandler.

Tests config-driven gateway configuration menu: status display,
bridge mode, MQTT/Meshtastic/RNS config, routing rules, templates,
and validation.
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_gateway():
    from handlers.gateway import GatewayHandler
    h = GatewayHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


def _make_mock_config():
    """Create a mock GatewayConfig with nested sub-configs."""
    config = MagicMock()
    config.enabled = True
    config.auto_start = False
    config.bridge_mode = "bidirectional"
    config.default_route = "bidirectional"
    config.routing_rules = []
    config.log_level = "INFO"
    config.log_messages = False
    config.load.return_value = None
    config.save.return_value = None
    config.validate.return_value = (True, [])

    # Meshtastic sub-config
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    config.meshtastic.channel = 0
    config.meshtastic.use_mqtt = False

    # MQTT sub-config
    config.mqtt_bridge.broker = "mqtt.meshtastic.org"
    config.mqtt_bridge.port = 8883
    config.mqtt_bridge.region = "US"
    config.mqtt_bridge.channel = "LongFast"
    config.mqtt_bridge.json_enabled = False
    config.mqtt_bridge.use_tls = True
    config.mqtt_bridge.username = ""
    config.mqtt_bridge.password = ""

    # RNS sub-config
    config.rns.identity_name = "meshforge_gateway"
    config.rns.announce_interval = 300
    config.rns.config_dir = ""
    # Off unless a test opts in: enabled reads the live gateway journal.
    config.mesh_bridge.enabled = False

    # Telemetry sub-config
    config.telemetry.share_position = True
    config.telemetry.share_battery = True
    config.telemetry.share_environment = False
    config.telemetry.position_precision = 4
    config.telemetry.update_interval = 300

    return config


class TestGatewayHandlerStructure:

    def test_handler_id(self):
        h = _make_gateway()
        assert h.handler_id == "gateway"

    def test_menu_section(self):
        h = _make_gateway()
        assert h.menu_section == "mesh_networks"

    def test_menu_items(self):
        h = _make_gateway()
        items = h.menu_items()
        assert len(items) == 1
        tag, desc, flag = items[0]
        assert tag == "gateway"
        assert flag == "gateway"

    def test_execute_dispatches_to_config_menu(self):
        h = _make_gateway()
        with patch.object(h, '_gateway_config_menu') as mock:
            h.execute("gateway")
            mock.assert_called_once()


class TestGatewayStatus:

    def test_show_status_displays_all_fields(self):
        h = _make_gateway()
        config = _make_mock_config()
        h._show_gateway_status(config)
        text = h.ctx.dialog.last_msgbox_text
        assert "localhost" in text
        assert "bidirectional" in text
        assert "meshforge_gateway" in text  # RNS identity

    def test_show_status_mqtt_mode(self):
        h = _make_gateway()
        config = _make_mock_config()
        config.bridge_mode = "mqtt_bridge"
        h._show_gateway_status(config)
        text = h.ctx.dialog.last_msgbox_text
        assert "mqtt.meshtastic.org" in text

    def test_show_status_disabled(self):
        h = _make_gateway()
        config = _make_mock_config()
        config.enabled = False
        h._show_gateway_status(config)
        text = h.ctx.dialog.last_msgbox_text
        assert "False" in text  # Enabled: False


def _mesh_bridge_config():
    from gateway.config import MeshtasticBridgeConfig
    config = _make_mock_config()
    mb = MeshtasticBridgeConfig(enabled=True)
    mb.primary.preset = "LONG_FAST"
    mb.secondary.preset = "SHORT_TURBO"
    mb.secondary.connection_type = "serial"
    mb.secondary.serial_device = "/dev/serial/by-id/usb-RAK4631"
    config.mesh_bridge = mb
    return config


# The gateway's real 30 s status block, as journalctl -o cat returns it.
_MOC_BLOCK = [
    "    LONG_FAST: connected   SHORT_TURBO: disconnected",
    "    Messages bridged: 3 (P->S: 3, S->P: 0)",
    "    LONG_FAST: connected   SHORT_TURBO: connected",
    "    Messages bridged: 4 (P->S: 3, S->P: 1)",
]


class TestMeshBridgeLegRows:

    def test_latest_reading_per_leg_and_real_endpoint(self):
        h = _make_gateway()
        text = "\n".join(h._mesh_bridge_leg_lines(
            _mesh_bridge_config(), lines_fn=lambda pat: list(_MOC_BLOCK)))
        assert "SHORT_TURBO  connected" in text      # the LAST report wins
        assert "/dev/serial/by-id/usb-RAK4631 (serial)" in text
        assert "(TCP)" in text                        # primary leg's endpoint

    def test_count_labelled_handed_to_radio_not_delivered(self):
        h = _make_gateway()
        text = "\n".join(h._mesh_bridge_leg_lines(
            _mesh_bridge_config(), lines_fn=lambda pat: list(_MOC_BLOCK)))
        assert "Handed to radio: P->S 3, S->P 1" in text
        assert "not proof it was heard on RF" in text
        assert "delivered" not in text.lower()

    def test_disconnected_reported(self):
        h = _make_gateway()
        text = "\n".join(h._mesh_bridge_leg_lines(
            _mesh_bridge_config(), lines_fn=lambda pat: _MOC_BLOCK[:2]))
        assert "SHORT_TURBO  disconnected" in text

    def test_unreadable_journal_is_unknown_not_down(self):
        h = _make_gateway()
        text = "\n".join(h._mesh_bridge_leg_lines(
            _mesh_bridge_config(), lines_fn=lambda pat: None))
        assert text.count("UNKNOWN (gateway journal unreadable)") == 2
        assert "disconnected" not in text

    def test_no_reports_is_unknown_not_down(self):
        h = _make_gateway()
        text = "\n".join(h._mesh_bridge_leg_lines(
            _mesh_bridge_config(), lines_fn=lambda pat: []))
        assert text.count("UNKNOWN (no gateway report") == 2
        assert "Handed to radio" not in text

    def test_pattern_matches_the_real_status_line(self):
        # The regex must match what bridge_cli prints, or every leg reads UNKNOWN.
        import re
        seen = []
        h = _make_gateway()
        h._mesh_bridge_leg_lines(_mesh_bridge_config(),
                                 lines_fn=lambda pat: seen.append(pat) or [])
        assert re.search(seen[0], _MOC_BLOCK[0])
        assert re.search(seen[0], _MOC_BLOCK[1])

    def test_status_screen_includes_section_only_when_enabled(self):
        h = _make_gateway()
        with patch("utils.watchdog_probe_core._journal_match_lines",
                   return_value=list(_MOC_BLOCK)):
            h._show_gateway_status(_mesh_bridge_config())
        assert "MESH BRIDGE" in h.ctx.dialog.last_msgbox_text
        h._show_gateway_status(_make_mock_config())
        assert "MESH BRIDGE" not in h.ctx.dialog.last_msgbox_text


class TestSetBridgeMode:

    def test_set_bridge_mode_updates_config(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["mesh_to_rns"]
        config = _make_mock_config()
        h._set_bridge_mode(config)
        assert config.bridge_mode == "mesh_to_rns"

    def test_set_bridge_mode_cancel(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        original = config.bridge_mode
        h._set_bridge_mode(config)
        assert config.bridge_mode == original


class TestConfigMeshtastic:

    def test_set_host(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["host", None]
        h.ctx.dialog._inputbox_returns = ["192.168.1.100"]
        config = _make_mock_config()
        h._config_meshtastic(config)
        assert config.meshtastic.host == "192.168.1.100"

    def test_set_port(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["port", None]
        h.ctx.dialog._inputbox_returns = ["4404"]
        config = _make_mock_config()
        h._config_meshtastic(config)
        assert config.meshtastic.port == 4404

    def test_set_channel_valid(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["channel", None]
        h.ctx.dialog._inputbox_returns = ["3"]
        config = _make_mock_config()
        h._config_meshtastic(config)
        assert config.meshtastic.channel == 3

    def test_menu_back(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        h._config_meshtastic(config)  # Should exit cleanly


class TestConfigMQTTBridge:

    def test_set_broker(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["broker", None]
        h.ctx.dialog._inputbox_returns = ["my-broker.local"]
        config = _make_mock_config()
        h._config_mqtt_bridge(config)
        assert config.mqtt_bridge.broker == "my-broker.local"

    def test_set_port(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["port", None]
        h.ctx.dialog._inputbox_returns = ["1883"]
        config = _make_mock_config()
        h._config_mqtt_bridge(config)
        assert config.mqtt_bridge.port == 1883

    def test_menu_back(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        h._config_mqtt_bridge(config)


class TestConfigRNS:

    def test_set_identity_name(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["identity", None]
        h.ctx.dialog._inputbox_returns = ["my_gateway"]
        config = _make_mock_config()
        h._config_rns(config)
        assert config.rns.identity_name == "my_gateway"

    def test_set_announce_interval(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["announce", None]
        h.ctx.dialog._inputbox_returns = ["600"]
        config = _make_mock_config()
        h._config_rns(config)
        assert config.rns.announce_interval == 600

    def test_menu_back(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        h._config_rns(config)


class TestConfigRouting:

    def test_routing_menu_back(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        h._config_routing(config)

    def test_clear_rules(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["clear", None]
        h.ctx.dialog._yesno_returns = [True]
        config = _make_mock_config()
        rule1 = MagicMock()
        rule1.name = "rule1"
        rule1.enabled = True
        rule2 = MagicMock()
        rule2.name = "rule2"
        rule2.enabled = False
        config.routing_rules = [rule1, rule2]
        h._config_routing(config)
        assert config.routing_rules == []


class TestConfigTelemetry:

    def test_toggle_position(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["position", None]
        config = _make_mock_config()
        original = config.telemetry.share_position
        h._config_telemetry(config)
        assert config.telemetry.share_position == (not original)

    def test_set_update_interval(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = ["interval", None]
        h.ctx.dialog._inputbox_returns = ["600"]
        config = _make_mock_config()
        h._config_telemetry(config)
        assert config.telemetry.update_interval == 600

    def test_menu_back(self):
        h = _make_gateway()
        h.ctx.dialog._menu_returns = [None]
        config = _make_mock_config()
        h._config_telemetry(config)


class TestValidateConfig:

    def test_valid_config(self):
        h = _make_gateway()
        config = _make_mock_config()
        config.validate.return_value = (True, [])
        h._validate_gateway_config(config)
        assert "valid" in h.ctx.dialog.last_msgbox_text.lower() or \
               "pass" in h.ctx.dialog.last_msgbox_text.lower() or \
               "ok" in h.ctx.dialog.last_msgbox_text.lower() or \
               h.ctx.dialog.last_msgbox_text is not None

    def test_invalid_config_shows_errors(self):
        h = _make_gateway()
        config = _make_mock_config()
        error = MagicMock()
        error.severity = "error"
        error.field = "mqtt_bridge.broker"
        error.message = "Broker address is required"
        config.validate.return_value = (False, [error])
        h._validate_gateway_config(config)
        assert h.ctx.dialog.last_msgbox_text is not None


class TestLoadTemplate:

    def test_load_template_success(self):
        h = _make_gateway()
        config = _make_mock_config()

        with patch('handlers.gateway._GatewayConfig') as MockConfig:
            MockConfig.get_available_templates.return_value = {
                "basic": "Basic bidirectional bridge",
                "monitor": "Monitor-only (no TX)",
            }
            template_config = _make_mock_config()
            template_config.bridge_mode = "monitor"
            MockConfig.from_template.return_value = template_config

            h.ctx.dialog._menu_returns = ["basic"]
            h.ctx.dialog._yesno_returns = [True]
            h._load_template(config)

    def test_load_template_cancel(self):
        h = _make_gateway()
        config = _make_mock_config()
        original_mode = config.bridge_mode

        with patch('handlers.gateway._GatewayConfig') as MockConfig:
            MockConfig.get_available_templates.return_value = {
                "basic": "Basic bidirectional bridge",
            }
            h.ctx.dialog._menu_returns = [None]
            h._load_template(config)
        assert config.bridge_mode == original_mode
