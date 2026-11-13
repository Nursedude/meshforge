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


def _downlink_config(mesh=True, rns=True):
    config = _mesh_bridge_config()
    config.mesh_bridge.primary.injection_mode = "downlink" if mesh else "toradio"
    config.meshtastic.injection_mode = "downlink" if rns else "toradio"
    config.rns_bridge_enabled = rns
    return config


_DOWNLINK_BLOCK = [
    "    Downlink[mesh-LONG_FAST]: session up · lost-sessions 0 · <=0 possibly lost · QoS 0, window 12.5 s · ok 4 · no-connack 0",
    "    Downlink[mesh-LONG_FAST]: session up · lost-sessions 1 · <=3 possibly lost · QoS 0, window 12.5 s · ok 5 · no-connack 0",
    "Downlink[rns]: idle — enabled, no downlink attempted since start",
]


class TestDownlinkRows:
    """#25 counters in the Gateway Status pane: the writer's own line, read
    with the writer's regex; UNKNOWN when it can't be seen; silent when the
    box isn't configured for downlink."""

    def test_latest_line_per_injector(self):
        h = _make_gateway()
        text = "\n".join(h._downlink_lines(_downlink_config(),
                                           lines_fn=lambda pat: list(_DOWNLINK_BLOCK)))
        assert "mesh-LONG_FAST:" in text
        assert "lost-sessions 1" in text and "<=3 possibly lost" in text   # the LAST line wins
        assert "ok 5" in text
        assert "  rns:\n    idle" in text
        assert "upper bound" in text

    def test_configured_but_no_line_is_unknown_not_zero(self):
        h = _make_gateway()
        text = "\n".join(h._downlink_lines(_downlink_config(),
                                           lines_fn=lambda pat: []))
        assert text.count("UNKNOWN (no gateway report in 2min)") == 2
        assert "ok 0" not in text

    def test_unreadable_journal_is_unknown(self):
        h = _make_gateway()
        text = "\n".join(h._downlink_lines(_downlink_config(),
                                           lines_fn=lambda pat: None))
        assert text.count("UNKNOWN (gateway journal unreadable)") == 2

    def test_not_configured_shows_nothing(self):
        h = _make_gateway()
        assert h._downlink_lines(_downlink_config(mesh=False, rns=False),
                                 lines_fn=lambda pat: list(_DOWNLINK_BLOCK)) == []

    def test_reader_uses_the_writers_regex(self):
        # One constant for writer and reader (honest_failure_modes #5).
        from gateway.mqtt_downlink_inject import DOWNLINK_LINE_RE, format_downlink_line
        h = _make_gateway()
        seen = {}
        line = format_downlink_line({"label": "mesh-LONG_FAST", "counts": {"ok": 1}, "connected": True})
        h._downlink_lines(_downlink_config(rns=False),
                          lines_fn=lambda pat: seen.setdefault("pat", pat) and [line])
        assert seen["pat"] == DOWNLINK_LINE_RE.pattern



class TestDownlinkRowsRound1:

    def test_pane_includes_the_section_and_scrolls(self):
        # F2 + F8 M18: the section is wired into Gateway Status, and the pane
        # is a scrolling textbox (the 19-row msgbox cut it off).
        h = _make_gateway()
        cfg = _downlink_config()
        with patch.object(type(h), "_downlink_lines",
                          lambda self, config, lines_fn=None: ["TRUE-ORIGIN DOWNLINK (MQTT, QoS 0):"]):
            h._show_gateway_status(cfg)
        kind, (title, text), kw = h.ctx.dialog.calls[-1]
        assert kind == "textbox" and "TRUE-ORIGIN DOWNLINK" in text

    def test_rows_fit_the_pane_with_the_witness_first(self):
        from gateway.mqtt_downlink_inject import format_downlink_line
        h = _make_gateway()
        line = format_downlink_line({"label": "mesh-LONG_FAST", "counts": {"ok": 412, "not_written": 3},
                                     "connected": True, "sessions_lost": 2, "possibly_lost": 7,
                                     "window_s": 12.5})
        out = h._downlink_lines(_downlink_config(rns=False), lines_fn=lambda pat: [line])
        assert all(len(r) <= 60 for r in out), [r for r in out if len(r) > 60]
        body = [r for r in out if r.startswith("    ")]
        assert "lost-sessions 2" in body[0] and "<=7 possibly lost" in body[0]

    def test_no_rns_row_when_the_rns_bridge_does_not_run(self):
        # Suspected (r1): meshtastic downlink set but rns_bridge_enabled off
        # -> no rns row (it would read UNKNOWN forever).
        h = _make_gateway()
        cfg = _downlink_config()
        cfg.rns_bridge_enabled = False
        text = "\n".join(h._downlink_lines(cfg, lines_fn=lambda pat: []))
        assert "rns:" not in text and "mesh-LONG_FAST:" in text

    def test_mesh_configured_without_injector_reads_unavailable(self):
        # F8 M14: the mesh bridge's "configured, no injector" -> unavailable.
        from gateway.mqtt_downlink_inject import downlink_status
        assert downlink_status("mesh-LONG_FAST", None, configured=True, built=True) == \
            {"label": "mesh-LONG_FAST", "unavailable": True}

    def test_odd_preset_round_trips_writer_to_pane(self):
        # F6 (TUI half): a free-form preset reaches the pane as the SAME
        # sanitized label the writer printed — not UNKNOWN.
        from gateway.mqtt_downlink_inject import downlink_label, format_downlink_line
        h = _make_gateway()
        cfg = _downlink_config(rns=False)
        cfg.mesh_bridge.primary.preset = "A: B [x]"
        line = format_downlink_line({"label": "mesh-" + downlink_label("A: B [x]"),
                                     "counts": {"ok": 1}, "connected": True})
        text = "\n".join(h._downlink_lines(cfg, lines_fn=lambda pat: [line]))
        assert "UNKNOWN" not in text and "session up" in text


class TestDownlinkRowsRound2:

    def test_rows_fit_the_textbox_text_area_and_drop_no_field(self):
        # F4: a 60-wide textbox shows 56 text columns; F7 m18b: wrapping must
        # keep EVERY field.
        from gateway.mqtt_downlink_inject import DOWNLINK_OUTCOMES, format_downlink_line
        h = _make_gateway()
        st = {"label": "mesh-LONG_FAST", "counts": {k: 123456 for k in DOWNLINK_OUTCOMES},
              "connected": True, "sessions_lost": 12, "possibly_lost": 345, "window_s": 12.5}
        line = format_downlink_line(st)
        body = line.split("]: ", 1)[1]
        out = h._downlink_lines(_downlink_config(rns=False), lines_fn=lambda pat: [line])
        assert all(len(r) <= 56 for r in out), [r for r in out if len(r) > 56]
        rows = [r.strip() for r in out if r.startswith("    ") and "upper bound" not in r
                and "radio can say" not in r]
        assert " · ".join(rows) == body

    def test_rns_row_follows_the_writers_truthiness(self):
        # F5: bridge_cli starts the RNS bridge on a TRUTHY rns_bridge_enabled
        # (1, "true"); the reader must agree.
        h = _make_gateway()
        for v in (1, "true", True):
            cfg = _downlink_config()
            cfg.rns_bridge_enabled = v
            text = "\n".join(h._downlink_lines(cfg, lines_fn=lambda pat: []))
            assert "  rns:" in text, v
