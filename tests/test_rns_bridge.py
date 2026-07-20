"""
Tests for RNS-Meshtastic Bridge Service (rns_bridge.py).

Covers BridgedMessage, RNSMeshtasticBridge init/properties/state,
circuit breaker delegation, MQTT filtering, routing rules (legacy +
classifier), message bridging loops, callback systems, persistent queue
integration, and module-level headless helper functions.

All external dependencies (RNS, LXMF, meshtastic, pubsub) are mocked.

Run: python3 -m pytest tests/test_rns_bridge.py -v
"""

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue, Full, Empty
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.bridge_health import BridgeStatus, MessageOrigin


# ---------------------------------------------------------------------------
# BridgedMessage
# ---------------------------------------------------------------------------

class TestBridgedMessage:
    """Tests for BridgedMessage dataclass."""

    def _make_msg(self, **kwargs):
        from gateway.rns_bridge import BridgedMessage
        defaults = dict(
            source_network="meshtastic",
            source_id="!aabb0042",
            destination_id=None,
            content="Hello world",
        )
        defaults.update(kwargs)
        return BridgedMessage(**defaults)

    def test_defaults(self):
        msg = self._make_msg()
        assert msg.source_network == "meshtastic"
        assert msg.content == "Hello world"
        assert msg.title is None
        assert msg.is_broadcast is False
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.UNKNOWN

    def test_post_init_sets_timestamp(self):
        msg = self._make_msg()
        assert msg.timestamp is not None
        assert isinstance(msg.timestamp, datetime)

    def test_auto_timestamp_is_recent(self):
        before = datetime.now()
        msg = self._make_msg()
        after = datetime.now()
        assert before <= msg.timestamp <= after

    def test_post_init_sets_metadata(self):
        msg = self._make_msg()
        assert msg.metadata == {}

    def test_explicit_timestamp_preserved(self):
        ts = datetime(2026, 1, 1, 12, 0, 0)
        msg = self._make_msg(timestamp=ts)
        assert msg.timestamp == ts

    def test_explicit_metadata_preserved(self):
        msg = self._make_msg(metadata={"channel": 3})
        assert msg.metadata == {"channel": 3}

    def test_with_all_fields(self):
        ts = datetime(2026, 1, 9, 12, 0, 0)
        msg = self._make_msg(
            source_network="rns",
            source_id="abc123",
            destination_id="def456",
            content="Test message",
            title="Test Title",
            timestamp=ts,
            is_broadcast=True,
            metadata={"priority": "high"},
        )
        assert msg.source_network == "rns"
        assert msg.title == "Test Title"
        assert msg.timestamp == ts
        assert msg.is_broadcast is True
        assert msg.metadata == {"priority": "high"}

    def test_should_bridge_default(self):
        msg = self._make_msg()
        assert msg.should_bridge() is True

    def test_should_bridge_mqtt_filter_off(self):
        msg = self._make_msg(via_internet=True)
        assert msg.should_bridge(filter_mqtt=False) is True

    def test_should_bridge_filters_mqtt_via_internet(self):
        msg = self._make_msg(via_internet=True)
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_should_bridge_filters_mqtt_origin(self):
        msg = self._make_msg(origin=MessageOrigin.MQTT)
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_should_bridge_allows_radio_origin(self):
        msg = self._make_msg(origin=MessageOrigin.RADIO)
        assert msg.should_bridge(filter_mqtt=True) is True

    def test_should_bridge_allows_radio_not_via_internet(self):
        msg = self._make_msg(via_internet=False, origin=MessageOrigin.RADIO)
        assert msg.should_bridge(filter_mqtt=True) is True

    # Hardening C: bytes→str centralized at construction
    # LXMF delivers message.content as bytes; if a code path forgets to
    # decode, str ops (.startswith, json.dumps in requeue) crash. Issue #40
    # patched xform-side; this guard moves it to BridgedMessage so any
    # construction site is safe.

    def test_bytes_content_normalized_at_construction(self):
        msg = self._make_msg(content=b"hello from RNS")
        assert isinstance(msg.content, str)
        assert msg.content == "hello from RNS"

    def test_bytes_content_invalid_utf8_uses_replacement(self):
        msg = self._make_msg(content=b"\xff\xfe\xfdbroken")
        assert isinstance(msg.content, str)
        # errors=replace yields U+FFFD for each invalid byte; assert no crash
        # and that the trailing valid suffix survives.
        assert msg.content.endswith("broken")

    def test_bytes_title_normalized_at_construction(self):
        msg = self._make_msg(title=b"Subject Line")
        assert isinstance(msg.title, str)
        assert msg.title == "Subject Line"

    def test_none_content_normalized_to_empty_string(self):
        msg = self._make_msg(content=None)
        assert msg.content == ""

    def test_str_content_passthrough(self):
        msg = self._make_msg(content="already str")
        assert msg.content == "already str"

    def test_at_prefix_works_after_bytes_normalize(self):
        # Regression: _process_rns_to_mesh does body.startswith('@'); if
        # bytes leaked through __post_init__, that crashes with TypeError.
        msg = self._make_msg(content=b"@!aabb0042 directed downlink")
        assert msg.content.startswith("@")  # would TypeError on bytes

    def test_bridged_message_serializes_after_bytes_normalize(self):
        # Regression: _requeue_failed_message JSON-serializes content. If
        # still bytes, json.dumps raises and the message is lost twice.
        import json
        msg = self._make_msg(content=b"requeue me", metadata={"a": 1})
        json.dumps({"message": msg.content, "metadata": msg.metadata})


# ---------------------------------------------------------------------------
# Helpers for bridge construction with full mocking
# ---------------------------------------------------------------------------

def _mock_gateway_config(**overrides):
    """Create a mock GatewayConfig with sensible defaults."""
    config = MagicMock()
    config.enabled = overrides.get("enabled", True)
    config.auto_start = False
    config.bridge_mode = overrides.get("bridge_mode", "message_bridge")
    config.default_route = overrides.get("default_route", "bidirectional")
    config.routing_rules = overrides.get("routing_rules", [])
    config.log_level = "DEBUG"
    config.log_messages = True
    config.rns = MagicMock()
    config.rns.config_dir = None
    config.meshtastic = MagicMock()
    config.meshtastic.channel = 0
    config.meshtastic.connection_type = "tcp"
    config.meshtastic.host = "localhost"
    config.meshtastic.port = 4403
    # Disable dual-radio features by default in unit tests
    config.meshtastic.failover_enabled = False
    config.meshtastic.load_balancer_enabled = False
    config.meshtastic.gateway_heartbeat_enabled = False
    return config


@pytest.fixture
def bridge():
    """Create a fully-mocked RNSMeshtasticBridge for unit testing."""
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.UnifiedNodeTracker") as MockTracker, \
         patch("gateway.rns_bridge.BridgeHealthMonitor") as MockHealth, \
         patch("gateway.rns_bridge.DeliveryTracker") as MockDelivery, \
         patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", True), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry") as MockCB, \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config()
        MockConfig.load.return_value = mock_config

        mock_handler = MagicMock()
        mock_handler.is_connected = False
        MockHandler.return_value = mock_handler

        mock_reconnect = MagicMock()
        MockReconnect.for_rns.return_value = mock_reconnect

        mock_cb_registry = MagicMock()
        MockCB.return_value = mock_cb_registry

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)
        yield b
        # Teardown: stop the bridge so any loops a start()-ing test spawned are
        # halted BEFORE this fixture's `with` exits. start() launches daemon
        # loops (_rns_loop/_bridge_loop) that call check_service -> subprocess.run
        # on their interval. If left running, those threads outlive the patched
        # HAS_SERVICE_CHECK=False context here and, once it's restored, hit the
        # REAL subprocess.run — landing inside whatever LATER test happens to be
        # patching the global subprocess.run (the scattered cross-file flaky:
        # apply_config_and_restart, dialog backtitle, socket cleanup, ...).
        # stop() is a no-op when the bridge was never started.
        try:
            b.stop()
        except Exception:
            pass


@pytest.fixture
def bridge_no_cb():
    """Bridge with circuit breaker disabled."""
    with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
         patch("gateway.rns_bridge.UnifiedNodeTracker"), \
         patch("gateway.rns_bridge.BridgeHealthMonitor"), \
         patch("gateway.rns_bridge.DeliveryTracker"), \
         patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
         patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
         patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
         patch("gateway.rns_bridge.CircuitBreakerRegistry", None), \
         patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
         patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
         patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
         patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
         patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

        mock_config = _mock_gateway_config()
        MockConfig.load.return_value = mock_config
        MockHandler.return_value = MagicMock(is_connected=False)
        MockReconnect.for_rns.return_value = MagicMock()

        from gateway.rns_bridge import RNSMeshtasticBridge
        b = RNSMeshtasticBridge(config=mock_config)
        yield b
        try:
            b.stop()  # see `bridge` fixture: stop leaked subprocess-calling loops
        except Exception:
            pass


# ---------------------------------------------------------------------------
# RNSMeshtasticBridge — initial state
# ---------------------------------------------------------------------------

class TestBridgeInit:
    """Tests for bridge initialization and default state."""

    def test_not_running_initially(self, bridge):
        assert bridge.is_running is False

    def test_not_connected_initially(self, bridge):
        assert bridge.is_connected is False

    def test_stats_initialized(self, bridge):
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0
        assert bridge.stats['errors'] == 0
        assert bridge.stats['bounced'] == 0
        assert bridge.stats['start_time'] is None

    def test_queues_created(self, bridge):
        assert isinstance(bridge._mesh_to_rns_queue, Queue)
        assert isinstance(bridge._rns_to_mesh_queue, Queue)

    def test_callbacks_empty(self, bridge):
        assert bridge._message_callbacks == []
        assert bridge._status_callbacks == []

    def test_rns_state_flags(self, bridge):
        assert bridge._connected_rns is False
        assert bridge._rns_via_rnsd is False
        assert bridge._rns_init_failed_permanently is False
        assert bridge._rns_pre_initialized is False

    def test_mqtt_filter_off_by_default(self, bridge):
        assert bridge._filter_mqtt_messages is False


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestBridgeProperties:
    """Tests for bridge property methods."""

    def test_is_running_reflects_state(self, bridge):
        bridge._running = True
        assert bridge.is_running is True
        bridge._running = False
        assert bridge.is_running is False

    def test_is_connected_when_mesh_connected(self, bridge):
        bridge._mesh_handler.is_connected = True
        assert bridge.is_connected is True

    def test_is_connected_when_rns_connected(self, bridge):
        bridge._connected_rns = True
        assert bridge.is_connected is True

    def test_is_connected_neither(self, bridge):
        bridge._mesh_handler.is_connected = False
        bridge._connected_rns = False
        assert bridge.is_connected is False

    def test_bridge_status_delegates_to_health(self, bridge):
        bridge.health.get_bridge_status.return_value = BridgeStatus.HEALTHY
        assert bridge.bridge_status == BridgeStatus.HEALTHY

    def test_is_fully_healthy_delegates(self, bridge):
        bridge.health.is_bridge_fully_healthy.return_value = True
        assert bridge.is_fully_healthy is True


# ---------------------------------------------------------------------------
# Circuit breaker delegation
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Tests for circuit breaker methods."""

    def test_can_send_to_delegates(self, bridge):
        bridge._circuit_breaker.can_send.return_value = True
        assert bridge.can_send_to("!abc123") is True
        bridge._circuit_breaker.can_send.assert_called_once_with("!abc123")

    def test_can_send_to_blocked(self, bridge):
        bridge._circuit_breaker.can_send.return_value = False
        assert bridge.can_send_to("!abc123") is False

    def test_can_send_to_no_circuit_breaker(self, bridge_no_cb):
        assert bridge_no_cb.can_send_to("!abc123") is True

    def test_record_send_success(self, bridge):
        bridge.record_send_success("!abc123")
        bridge._circuit_breaker.record_success.assert_called_once_with("!abc123")

    def test_record_send_success_no_cb(self, bridge_no_cb):
        bridge_no_cb.record_send_success("!abc123")  # Should not raise

    def test_record_send_failure(self, bridge):
        bridge.record_send_failure("!abc123", "timeout")
        bridge._circuit_breaker.record_failure.assert_called_once_with("!abc123", "timeout")

    def test_record_send_failure_no_cb(self, bridge_no_cb):
        bridge_no_cb.record_send_failure("!abc123", "err")  # Should not raise

    def test_get_open_circuits(self, bridge):
        bridge._circuit_breaker.get_open_circuits.return_value = {"!abc": {}}
        result = bridge.get_open_circuits()
        assert "!abc" in result

    def test_get_open_circuits_no_cb(self, bridge_no_cb):
        assert bridge_no_cb.get_open_circuits() == {}


# ---------------------------------------------------------------------------
# MQTT filtering
# ---------------------------------------------------------------------------

class TestMQTTFiltering:
    """Tests for MQTT message filtering."""

    def test_set_filter_mqtt_enable(self, bridge):
        bridge.set_filter_mqtt(True)
        assert bridge._filter_mqtt_messages is True

    def test_set_filter_mqtt_disable(self, bridge):
        bridge.set_filter_mqtt(True)
        bridge.set_filter_mqtt(False)
        assert bridge._filter_mqtt_messages is False


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    """Tests for get_status method."""

    def test_status_when_not_running(self, bridge):
        status = bridge.get_status()
        assert status['running'] is False
        assert status['meshtastic_connected'] is False
        assert status['rns_connected'] is False
        assert status['uptime_seconds'] is None

    def test_status_when_running(self, bridge):
        bridge._running = True
        bridge.stats['start_time'] = datetime.now()
        bridge._connected_rns = True
        bridge._mesh_handler.is_connected = True

        status = bridge.get_status()
        assert status['running'] is True
        assert status['meshtastic_connected'] is True
        assert status['rns_connected'] is True
        assert status['uptime_seconds'] is not None
        assert status['uptime_seconds'] >= 0

    def test_status_contains_statistics(self, bridge):
        bridge.stats['messages_mesh_to_rns'] = 5
        status = bridge.get_status()
        assert status['statistics']['messages_mesh_to_rns'] == 5

    def test_status_contains_node_stats(self, bridge):
        bridge.node_tracker.get_stats.return_value = {"total": 10}
        status = bridge.get_status()
        assert status['node_stats'] == {"total": 10}

    def test_status_enabled_from_config(self, bridge):
        status = bridge.get_status()
        assert status['enabled'] is True

    def test_status_rns_via_rnsd(self, bridge):
        bridge._rns_via_rnsd = True
        status = bridge.get_status()
        assert status['rns_via_rnsd'] is True

    def test_status_with_uptime_calculation(self, bridge):
        bridge.stats['start_time'] = datetime.now() - timedelta(seconds=60)
        status = bridge.get_status()
        assert status['uptime_seconds'] >= 60


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    """Tests for callback registration and notification."""

    def test_register_message_callback(self, bridge):
        cb = MagicMock()
        bridge.register_message_callback(cb)
        assert cb in bridge._message_callbacks

    def test_register_status_callback(self, bridge):
        cb = MagicMock()
        bridge.register_status_callback(cb)
        assert cb in bridge._status_callbacks

    def test_notify_message_calls_callbacks(self, bridge):
        cb1 = MagicMock()
        cb2 = MagicMock()
        bridge.register_message_callback(cb1)
        bridge.register_message_callback(cb2)

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns",
            source_id="abc123",
            destination_id=None,
            content="test",
        )
        bridge._notify_message(msg)
        cb1.assert_called_once_with(msg)
        cb2.assert_called_once_with(msg)

    def test_notify_message_handles_callback_error(self, bridge):
        bad_cb = MagicMock(side_effect=RuntimeError("cb fail"))
        good_cb = MagicMock()
        bridge.register_message_callback(bad_cb)
        bridge.register_message_callback(good_cb)

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abc", destination_id=None, content="x"
        )
        bridge._notify_message(msg)
        # Good callback should still be called despite bad one failing
        good_cb.assert_called_once_with(msg)

    def test_notify_status_calls_callbacks(self, bridge):
        cb = MagicMock()
        bridge.register_status_callback(cb)
        bridge._notify_status("started")
        assert cb.call_count == 1
        assert cb.call_args[0][0] == "started"

    def test_notify_status_handles_callback_error(self, bridge):
        bad_cb = MagicMock(side_effect=RuntimeError("status cb fail"))
        good_cb = MagicMock()
        bridge.register_status_callback(bad_cb)
        bridge.register_status_callback(good_cb)
        bridge._notify_status("stopped")
        good_cb.assert_called_once()


# ---------------------------------------------------------------------------
# send_to_meshtastic
# ---------------------------------------------------------------------------

class TestSendToMeshtastic:
    """Tests for send_to_meshtastic."""

    def test_delegates_to_handler(self, bridge):
        bridge._mesh_handler.send_text.return_value = True
        result = bridge.send_to_meshtastic("Hello", "!dest", 2)
        assert result is True
        bridge._mesh_handler.send_text.assert_called_once_with("Hello", "!dest", 2)

    def test_returns_false_no_handler(self, bridge):
        bridge._mesh_handler = None
        assert bridge.send_to_meshtastic("Hello") is False


# ---------------------------------------------------------------------------
# send_to_rns
# ---------------------------------------------------------------------------

class TestSendToRNS:
    """Tests for send_to_rns."""

    def test_returns_false_not_connected(self, bridge):
        bridge._connected_rns = False
        res = bridge.send_to_rns("msg")
        assert bool(res) is False and res.reason == "not_connected"

    def test_returns_false_no_lxmf_source(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = None
        res = bridge.send_to_rns("msg")
        assert bool(res) is False and res.reason == "no_lxmf_source"

    def test_broadcast_returns_false(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock()
        # No destination hash -> broadcast
        res = bridge.send_to_rns("broadcast msg", None)
        assert bool(res) is False and res.reason == "broadcast_unsupported"


# ---------------------------------------------------------------------------
# Routing rules — legacy
# ---------------------------------------------------------------------------

class TestRoutingLegacy:
    """Tests for _should_bridge_legacy routing logic."""

    def _make_bridge_with_rules(self, rules, default_route="bidirectional", enabled=True):
        """Create bridge with specific routing rules."""
        with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
             patch("gateway.rns_bridge.UnifiedNodeTracker"), \
             patch("gateway.rns_bridge.BridgeHealthMonitor"), \
             patch("gateway.rns_bridge.DeliveryTracker"), \
             patch("gateway.rns_bridge.MeshtasticHandler") as MockHandler, \
             patch("gateway.rns_bridge.ReconnectStrategy") as MockReconnect, \
             patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
             patch("gateway.rns_bridge.CircuitBreakerRegistry", None), \
             patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", False), \
             patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
             patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
             patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
             patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

            mock_config = _mock_gateway_config(
                routing_rules=rules,
                default_route=default_route,
                enabled=enabled,
            )
            MockConfig.load.return_value = mock_config
            MockHandler.return_value = MagicMock(is_connected=False)
            MockReconnect.for_rns.return_value = MagicMock()

            from gateway.rns_bridge import RNSMeshtasticBridge
            return RNSMeshtasticBridge(config=mock_config)

    def _make_rule(self, **kwargs):
        from gateway.config import RoutingRule
        return RoutingRule(**kwargs)

    def _make_msg(self, source_network="meshtastic", source_id="!aabb0042",
                  content="hello", destination_id=None, is_broadcast=False):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network=source_network,
            source_id=source_id,
            destination_id=destination_id,
            content=content,
            is_broadcast=is_broadcast,
        )

    def test_disabled_config_blocks_all(self):
        b = self._make_bridge_with_rules([], enabled=False)
        msg = self._make_msg()
        assert b._router.should_bridge(msg) is False

    def test_no_rules_default_bidirectional(self):
        b = self._make_bridge_with_rules([], default_route="bidirectional")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True

    def test_no_rules_default_blocks(self):
        b = self._make_bridge_with_rules([], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_matching_rule_passes(self):
        rule = self._make_rule(name="all", direction="bidirectional")
        b = self._make_bridge_with_rules([rule])
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True

    def test_direction_filter_mesh_to_rns_blocks_rns_source(self):
        rule = self._make_rule(name="m2r", direction="mesh_to_rns")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_rns = self._make_msg(source_network="rns")
        assert b._router._should_bridge_legacy(msg_rns) is False

    def test_direction_filter_rns_to_mesh_blocks_mesh_source(self):
        rule = self._make_rule(name="r2m", direction="rns_to_mesh")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_mesh = self._make_msg(source_network="meshtastic")
        assert b._router._should_bridge_legacy(msg_mesh) is False

    def test_direction_filter_allows_correct_direction(self):
        rule = self._make_rule(name="m2r", direction="mesh_to_rns")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg(source_network="meshtastic")
        assert b._router._should_bridge_legacy(msg) is True

    def test_source_filter_regex(self):
        rule = self._make_rule(name="src", direction="bidirectional", source_filter="!aabb.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(source_id="!aabb0042")
        msg_no_match = self._make_msg(source_id="!ccdd0099")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_dest_filter_regex(self):
        rule = self._make_rule(name="dst", direction="bidirectional", dest_filter="!dest.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(destination_id="!dest1234")
        msg_no_match = self._make_msg(destination_id="!other")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_message_filter_regex(self):
        rule = self._make_rule(name="msg", direction="bidirectional", message_filter="URGENT.*")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg_match = self._make_msg(content="URGENT: help needed")
        msg_no_match = self._make_msg(content="casual chat")
        assert b._router._should_bridge_legacy(msg_match) is True
        assert b._router._should_bridge_legacy(msg_no_match) is False

    def test_disabled_rule_skipped(self):
        rule = self._make_rule(name="off", direction="bidirectional", enabled=False)
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_invalid_regex_skipped(self):
        rule = self._make_rule(name="bad", direction="bidirectional", source_filter="[invalid")
        b = self._make_bridge_with_rules([rule], default_route="none")
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is False

    def test_multiple_rules_first_match_wins(self):
        rule1 = self._make_rule(name="r1", direction="bidirectional", source_filter="!aabb.*")
        rule2 = self._make_rule(name="r2", direction="bidirectional")  # matches all
        b = self._make_bridge_with_rules([rule1, rule2], default_route="none")
        msg = self._make_msg(source_id="!ccdd0099")
        # rule1 doesn't match source, but rule2 matches all
        assert b._router._should_bridge_legacy(msg) is True

    def test_recompiles_when_rules_change(self):
        rule = self._make_rule(name="r1", direction="bidirectional")
        b = self._make_bridge_with_rules([rule], default_route="none")
        # First call compiles
        msg = self._make_msg()
        assert b._router._should_bridge_legacy(msg) is True
        # Add new rule and verify recompilation
        new_rule = self._make_rule(name="r2", direction="bidirectional", source_filter="!xyz.*")
        b.config.routing_rules.append(new_rule)
        msg2 = self._make_msg(source_id="!xyz9999")
        assert b._router._should_bridge_legacy(msg2) is True


# ---------------------------------------------------------------------------
# _compile_routing_rules
# ---------------------------------------------------------------------------

class TestCompileRoutingRules:
    """Tests for _compile_routing_rules."""

    def test_compiles_valid_patterns(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="test", source_filter="!aabb.*", dest_filter="", message_filter="hello")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert "test" in compiled
        assert 'source_filter' in compiled['test']
        assert compiled['test']['source_filter'] is not None

    def test_marks_invalid_patterns_none(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="bad", source_filter="[invalid")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert compiled['bad']['source_filter'] is None

    def test_empty_patterns_not_compiled(self, bridge):
        from gateway.config import RoutingRule
        rule = RoutingRule(name="empty", source_filter="", dest_filter="", message_filter="")
        bridge.config.routing_rules = [rule]
        compiled = bridge._router._compile_routing_rules()
        assert compiled['empty'] == {}


# ---------------------------------------------------------------------------
# _process_mesh_to_rns
# ---------------------------------------------------------------------------

class TestProcessMeshToRNS:
    """Tests for Mesh->RNS message processing."""

    def test_success_updates_stats(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="test msg",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['messages_mesh_to_rns'] == 1
        # Hardening D: triplet must agree with legacy counter on success
        assert bridge.stats['mesh_to_rns_attempted'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 1
        assert bridge.stats['mesh_to_rns_dropped'] == 0
        bridge.health.record_message_sent.assert_called_once_with("mesh_to_rns")

    def test_failure_broadcast_no_error(self, bridge):
        """Broadcast with no default_lxmf_destination configured logs DEBUG, no error."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = []
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="broadcast",
            is_broadcast=True,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 0
        # Hardening D: broadcast-not-delivered counts as dropped (operator
        # signal that the bridge tried but had no peer to land on),
        # without escalating to an error.
        assert bridge.stats['mesh_to_rns_attempted'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 0
        assert bridge.stats['mesh_to_rns_dropped'] == 1

    def test_failure_unicast_increments_errors(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!dest", content="unicast",
        )

        with patch.object(bridge, 'send_to_rns', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 1
        assert bridge.stats['mesh_to_rns_attempted'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 0
        assert bridge.stats['mesh_to_rns_dropped'] == 1

    def test_exception_requeues_and_tracks(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="fail",
        )

        with patch.object(bridge, 'send_to_rns', side_effect=RuntimeError("boom")), \
             patch.object(bridge, '_requeue_failed_message', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 1
        # Hardening D: exception path also marks dropped
        assert bridge.stats['mesh_to_rns_attempted'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 0
        assert bridge.stats['mesh_to_rns_dropped'] == 1
        bridge.health.record_message_failed.assert_called_once_with("mesh_to_rns", requeued=True)

    def test_body_is_clean_identity_in_title(self, bridge):
        """Mesh→RNS carries the body verbatim; identity lives in the title."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello",
        )

        captured = {}

        def capture_send(content, dest_hash=None, title=None, fields=None):
            captured["content"] = content
            captured["title"] = title
            captured["fields"] = fields
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        assert captured["content"] == "hello"
        assert captured["title"] == "!aabb0042 via Meshtastic"

    def test_title_includes_long_name_when_known(self, bridge):
        """node_tracker hit produces '<long_name> (<id>) via Meshtastic'."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        mock_node = MagicMock()
        mock_node.name = "HAT-fleet-host-3"
        mock_node.short_name = "HAT3"
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!ebfa1b11",
            destination_id=None, content="summit check",
        )

        captured = {}

        def capture_send(content, dest_hash=None, title=None, fields=None):
            captured["title"] = title
            captured["fields"] = fields
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        assert captured["title"] == "HAT-fleet-host-3 (!ebfa1b11) via Meshtastic"
        assert captured["fields"]["meshforge_from_id"] == "!ebfa1b11"
        assert captured["fields"]["meshforge_from_long"] == "HAT-fleet-host-3"
        assert captured["fields"]["meshforge_from_short"] == "HAT3"
        assert captured["fields"]["meshforge_source_network"] == "meshtastic"

    def test_fields_present_even_when_node_unknown(self, bridge):
        """Identity fallback still emits the fields dict with empty name slots."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!deadbeef",
            destination_id=None, content="orphan",
            metadata={"channel": "meshforge"},
        )

        captured = {}

        def capture_send(content, dest_hash=None, title=None, fields=None):
            captured["fields"] = fields
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        fields = captured["fields"]
        assert fields["meshforge_from_id"] == "!deadbeef"
        assert fields["meshforge_from_long"] == ""
        assert fields["meshforge_from_short"] == ""
        assert fields["meshforge_channel"] == "meshforge"

    def test_broadcast_fans_out_to_multi_recipient_default(self, bridge):
        """When default_lxmf_destination is a list, broadcast Mesh→RNS sends to each."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "522c4ac1d2f9964e03e3782ef5b0224c",
            "d1df31d352ede66eac819a577da22b75",
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello fleet", is_broadcast=True,
        )
        sent_to = []

        def capture(content, dest_hash=None, title=None, fields=None):
            sent_to.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture):
            bridge._process_mesh_to_rns(msg)

        assert len(sent_to) == 3
        assert bytes.fromhex("522c4ac1d2f9964e03e3782ef5b0224c") in sent_to
        assert bytes.fromhex("d1df31d352ede66eac819a577da22b75") in sent_to
        assert bytes.fromhex("6b1a0120941444587d7d1dc1bf6d64d7") in sent_to
        # One source message → one stats increment, regardless of fan-out count
        assert bridge.stats['messages_mesh_to_rns'] == 1

    def test_meshcore_broadcast_fans_out_to_rns(self, bridge):
        """MeshCore→RNS broadcast fans out to each default_lxmf_destination too.

        Regression: the MeshCore path (meshcore_bridge_mixin) called send_to_rns
        with no destination, so MC broadcasts always dropped even with the list
        configured — a path distinct from the Meshtastic Mesh→RNS one."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "522c4ac1d2f9964e03e3782ef5b0224c",
            "d1df31d352ede66eac819a577da22b75",
        ]
        msg = BridgedMessage(
            source_network="meshcore", source_id="!mc01",
            destination_id=None, content="mc broadcast", is_broadcast=True,
        )
        sent_to = []

        def capture(content, dest_hash=None, title=None, fields=None):
            sent_to.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture), \
             patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_meshcore_to_bridge(msg)

        assert len(sent_to) == 2
        assert bytes.fromhex("522c4ac1d2f9964e03e3782ef5b0224c") in sent_to
        assert bytes.fromhex("d1df31d352ede66eac819a577da22b75") in sent_to

    def test_broadcast_partial_failure_still_counts_as_sent(self, bridge):
        """If at least one recipient succeeds, the bridge counts as delivered."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "522c4ac1d2f9964e03e3782ef5b0224c",
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hi", is_broadcast=True,
        )
        # First send fails, second succeeds
        with patch.object(bridge, 'send_to_rns', side_effect=[False, True]):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['messages_mesh_to_rns'] == 1
        assert bridge.stats['errors'] == 0

    def test_broadcast_skips_invalid_hex_destinations(self, bridge):
        """Bad hex entries log a warning and are skipped, valid ones still send."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "not-hex-at-all",
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="ping", is_broadcast=True,
        )
        sent_to = []

        def capture(content, dest_hash=None, title=None, fields=None):
            sent_to.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture):
            bridge._process_mesh_to_rns(msg)

        assert sent_to == [bytes.fromhex("6b1a0120941444587d7d1dc1bf6d64d7")]
        assert bridge.stats['messages_mesh_to_rns'] == 1

    def test_directed_dm_short_circuits_default_list(self, bridge):
        """Directed Mesh→RNS DMs go only to the resolved destination, not the default list."""
        from gateway.rns_bridge import BridgedMessage
        # node_tracker resolves the directed dest to a known RNS hash
        resolved = bytes.fromhex("11851af8e6d95c6f4735727e513d15d8")
        bridge.node_tracker.get_node_by_mesh_id.return_value = MagicMock(
            rns_hash=resolved, name="fleet-host-1", short_name="m1",
        )
        # Default list also configured — must NOT be hit when DM resolves
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!c0ffee01", content="dm",
            is_broadcast=False,
        )
        sent_to = []

        def capture(content, dest_hash=None, title=None, fields=None):
            sent_to.append(dest_hash)
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture):
            bridge._process_mesh_to_rns(msg)

        assert sent_to == [resolved]

    def test_rns_xform_spill_dispatch_runs_full_xform(self, bridge):
        """Hardening B: persistent-queue worker calls
        _dispatch_rns_xform_spill with the spilled payload; that helper
        must reconstruct the BridgedMessage and run the full
        _process_mesh_to_rns pipeline (so spill messages get fan-out
        and stats accounting just like in-memory ones)."""
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        payload = {
            'source_id': "!aabb0042",
            'destination_id': None,
            'content': "spilled overflow",
            'title': None,
            'is_broadcast': False,
            'metadata': {"channel": 2},
        }

        sent = {}

        def capture(content, dest_hash=None, title=None, fields=None):
            sent["content"] = content
            sent["dest_hash"] = dest_hash
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture):
            assert bridge._dispatch_rns_xform_spill(payload) is True

        assert sent["content"] == "spilled overflow"
        assert sent["dest_hash"] == bytes.fromhex(
            "6b1a0120941444587d7d1dc1bf6d64d7"
        )
        assert bridge.stats['mesh_to_rns_attempted'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 1


# ---------------------------------------------------------------------------
# _process_rns_to_mesh
# ---------------------------------------------------------------------------

class TestProcessRNSToMesh:
    """Tests for RNS->Mesh message processing."""

    def test_success_updates_stats(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="from rns",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['messages_rns_to_mesh'] == 1
        # Hardening D triplet
        assert bridge.stats['rns_to_mesh_attempted'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 1
        assert bridge.stats['rns_to_mesh_dropped'] == 0
        bridge.health.record_message_sent.assert_called_once_with("rns_to_mesh")

    def test_failure_increments_errors(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="fail msg",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1
        assert bridge.stats['rns_to_mesh_attempted'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 0
        assert bridge.stats['rns_to_mesh_dropped'] == 1

    def test_exception_requeues(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="boom",
        )

        with patch.object(bridge, 'send_to_meshtastic', side_effect=RuntimeError("err")), \
             patch.object(bridge, '_requeue_failed_message', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['errors'] == 1
        # Exception path also marks dropped
        assert bridge.stats['rns_to_mesh_attempted'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 0
        assert bridge.stats['rns_to_mesh_dropped'] == 1
        bridge.health.record_message_failed.assert_called_once_with("rns_to_mesh", requeued=True)

    def test_partial_direct_failure_requeues_chunks_not_whole(self, bridge):
        """Regression: on PARTIAL direct-send failure, the FAILED chunks must be
        re-queued individually — each byte-bounded and carrying the resolved
        `destination`. Re-queuing the whole un-chunked original (old behavior)
        made the retry exceed the byte cap (_truncate_if_needed drops lines) and
        lose the DM target (broadcast to ^all). Already-sent chunks must NOT be
        re-queued."""
        from gateway.rns_bridge import BridgedMessage
        from gateway.base_handler import chunk_for_mesh
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-id"
        bridge._persistent_queue = mock_queue  # direct path still uses it to requeue
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content=_LEADERBOARD)
        expected_chunks = chunk_for_mesh(_LEADERBOARD, prefix="[RNS:abcd] ")
        assert len(expected_chunks) >= 2
        # First chunk sends; every remaining chunk fails.
        calls = {"n": 0}

        def send(chunk, destination=None, channel=0):
            calls["n"] += 1
            return calls["n"] == 1

        with patch.object(bridge, 'send_to_meshtastic', side_effect=send):
            bridge._process_rns_to_mesh(msg)

        requeued = [c.kwargs['payload'] for c in mock_queue.enqueue.call_args_list]
        # Only the chunks that FAILED were re-queued (not the already-sent one,
        # not the whole original).
        assert len(requeued) == len(expected_chunks) - 1
        for p in requeued:
            assert len(p['message'].encode('utf-8')) <= _MAX
            assert 'destination' in p  # the key queue_send routes on, not destination_id
        assert bridge.stats['rns_to_mesh_dropped'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 0

    def test_prefix_includes_rns_source(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hello",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["content"].startswith("[RNS:abcd] ")
        assert captured["destination"] is None

    def test_meshtastic_origin_prefix_uses_original_node(self, bridge):
        """Content that originated as a Meshtastic broadcast at a PEER gateway
        must be tagged with the ORIGINAL node, not the relaying gateway's RNS
        hash. (Bug: every bot reply moc re-injected showed [RNS:f68c]=moc3.)"""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns",
            source_id="dead0000beef1111cafe2222f00d3333",  # moc3 (relaying gw)
            destination_id=None,
            content="Memorial Day: chance of rain",
            metadata={"lxmf_fields": {
                "meshforge_source_network": "meshtastic",
                "meshforge_from_short": "BORG",
                "meshforge_from_id": "!a2e95ba4",
            }},
        )
        captured = {}
        with patch.object(bridge, 'send_to_meshtastic',
                          side_effect=lambda content, destination=None, channel=0: captured.update(content=content) or True):
            bridge._process_rns_to_mesh(msg)
        # Tagged with the real origin, not the relaying gateway hash
        assert captured["content"].startswith("[RNS:BORG] ")
        assert "dead" not in captured["content"]  # relaying-gw hash not leaked

    def test_meshtastic_origin_prefix_falls_back_to_from_id(self, bridge):
        """No short name → use the originating node id (sans '!'), still not
        the relaying gateway."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns",
            source_id="dead0000beef1111cafe2222f00d3333",
            destination_id=None,
            content="wx",
            metadata={"lxmf_fields": {
                "meshforge_source_network": "meshtastic",
                "meshforge_from_short": "",
                "meshforge_from_long": "",
                "meshforge_from_id": "!a2e95ba4",
            }},
        )
        captured = {}
        with patch.object(bridge, 'send_to_meshtastic',
                          side_effect=lambda content, destination=None, channel=0: captured.update(content=content) or True):
            bridge._process_rns_to_mesh(msg)
        assert captured["content"].startswith("[RNS:a2e95ba4] ")

    def test_rns_origin_prefix_keeps_source_hash(self, bridge):
        """Genuinely RNS-origin content (NomadNet etc., no meshforge_source_network)
        keeps the source RNS hash prefix — unchanged behavior."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef0123456789",
            destination_id=None, content="from nomadnet",
        )
        captured = {}
        with patch.object(bridge, 'send_to_meshtastic',
                          side_effect=lambda content, destination=None, channel=0: captured.update(content=content) or True):
            bridge._process_rns_to_mesh(msg)
        assert captured["content"].startswith("[RNS:abcd] ")

    def test_hex_prefix_targets_node(self, bridge):
        """'@!ebfa1b11 reply' is a DM to !ebfa1b11; the @token is stripped."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="@!EBFA1B11 roger copy",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["destination"] == "!ebfa1b11"
        assert captured["content"] == "[RNS:abcd] roger copy"

    def test_shortname_prefix_resolves_via_tracker(self, bridge):
        """'@HAT3 hi' asks the node_tracker to resolve the short_name."""
        from gateway.rns_bridge import BridgedMessage
        mock_node = MagicMock()
        mock_node.meshtastic_id = "!ebfa1b11"
        bridge.node_tracker.get_node_by_short_name.return_value = mock_node
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="@HAT3 meet at 7",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        bridge.node_tracker.get_node_by_short_name.assert_called_with("HAT3")
        assert captured["destination"] == "!ebfa1b11"
        assert captured["content"] == "[RNS:abcd] meet at 7"

    def test_unknown_shortname_falls_through_to_broadcast(self, bridge):
        """Unresolvable @token keeps original content and broadcasts."""
        from gateway.rns_bridge import BridgedMessage
        bridge.node_tracker.get_node_by_short_name.return_value = None
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="@ghostnode anyone home?",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["destination"] is None
        assert captured["content"] == "[RNS:abcd] @ghostnode anyone home?"

    def test_no_prefix_is_broadcast(self, bridge):
        """Plain text (no leading '@') broadcasts on the bridge channel."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="broadcast check",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["destination"] is None
        assert captured["content"] == "[RNS:abcd] broadcast check"

    def test_bytes_content_is_decoded(self, bridge):
        """LXMF delivers content as bytes; must decode before string ops."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=b"hello from bytes",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["content"] == "[RNS:abcd] hello from bytes"
        assert bridge.stats['errors'] == 0

    def test_bytes_content_with_at_prefix(self, bridge):
        """Decoding must happen before @address parsing so bytes work there too."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=b"@!EBFA1B11 bytes-directed",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["destination"] == "!ebfa1b11"
        assert captured["content"] == "[RNS:abcd] bytes-directed"

    def test_invalid_utf8_uses_replacement(self, bridge):
        """Non-UTF-8 bytes must not crash — use replacement chars."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=b"hi \xff\xfe world",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['messages_rns_to_mesh'] == 1
        assert bridge.stats['errors'] == 0

    def test_mqtt_bridge_mode_enqueues_to_meshtastic(self):
        """In mqtt_bridge mode the queue destination must be 'meshtastic'
        (HTTP send_text_direct path), NOT 'mqtt' (which publishes to a
        topic meshtasticd does not subscribe to)."""
        with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
             patch("gateway.rns_bridge.UnifiedNodeTracker"), \
             patch("gateway.rns_bridge.BridgeHealthMonitor"), \
             patch("gateway.rns_bridge.DeliveryTracker"), \
             patch("gateway.rns_bridge.MeshtasticHandler"), \
             patch("gateway.rns_bridge.MQTTBridgeHandler") as MockMQTT, \
             patch("gateway.rns_bridge.ReconnectStrategy"), \
             patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
             patch("gateway.rns_bridge.HAS_MQTT_BRIDGE", True), \
             patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True), \
             patch("gateway.rns_bridge.PersistentMessageQueue") as MockQueue, \
             patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
             patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
             patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
             patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

            config = _mock_gateway_config(bridge_mode="mqtt_bridge")
            MockConfig.load.return_value = config

            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = "msg-id-1"
            MockQueue.return_value = mock_queue

            mock_handler = MagicMock()
            MockMQTT.return_value = mock_handler

            from gateway.rns_bridge import RNSMeshtasticBridge, BridgedMessage
            b = RNSMeshtasticBridge(config=config)

            msg = BridgedMessage(
                source_network="rns", source_id="abcdef01",
                destination_id=None, content="queue-path check",
            )
            b._process_rns_to_mesh(msg)

            # Last enqueue call (register_sender may also enqueue during setup;
            # we care about the _process_rns_to_mesh one)
            rns_enqueue = [
                c for c in mock_queue.enqueue.call_args_list
                if c.kwargs.get("destination") in ("mqtt", "meshtastic")
            ]
            assert len(rns_enqueue) == 1
            assert rns_enqueue[0].kwargs["destination"] == "meshtastic"
            assert rns_enqueue[0].kwargs["payload"]["message"] == \
                "[RNS:abcd] queue-path check"


# ---------------------------------------------------------------------------
# RNS→Mesh byte-aware chunking (leaderboard "missing info" fix)
# ---------------------------------------------------------------------------

# A leaderboard-shaped reply: many short emoji lines. 446 UTF-8 bytes — well
# over the 228-byte Meshtastic cap. Before the fix this was truncated to one
# packet by _truncate_if_needed, silently dropping every line past ~line 6.
_LEADERBOARD = (
    "📊Leaderboard📊\n🪫 Low Battery: 23.0% !d38d6b0b\n🕰️ Uptime: 190d !164b3229\n"
    "🚓 Speed: 6.2 mph !6095faf9\n🪜 Tallest: 9997ft !6d9ee8c7\n🚀 Altitude: 3500ft !96393e02\n"
    "✈️ Airspeed: 120 mph node\n🥶 Coldest: 71.4°F node2\n🥵 Hottest: 92°F node3\n"
    "💨 Worst IAQ: 150 node4\n📶 Weakest RF: -120 dBm node5\n📶 Best RF: -45 dBm node6\n"
    "📊 Most Telemetry: 234 node7\n🤪 Most Emojis: 132 node8\n💬 Most Messages: 639 moc3"
)
_MAX = 228  # MAX_MESHTASTIC_MSG_LENGTH


class TestChunkForMesh:
    """Unit tests for the chunk_for_mesh primitive."""

    def test_short_message_single_chunk_unchanged(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("hello") == ["hello"]

    def test_empty_returns_empty_list(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("") == []

    def test_leaderboard_splits_into_byte_bounded_chunks(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh(_LEADERBOARD)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.encode("utf-8")) <= _MAX

    def test_no_content_lost_when_chunked(self):
        """Every original line must survive somewhere in the chunk set."""
        from gateway.base_handler import chunk_for_mesh
        joined = "\n".join(chunk_for_mesh(_LEADERBOARD))
        for line in _LEADERBOARD.split("\n"):
            assert line in joined

    def test_oversize_single_word_hard_splits_on_codepoint(self):
        """A space-less multibyte run still chunks; bytes are never lost or
        split mid-codepoint (no replacement chars introduced)."""
        from gateway.base_handler import chunk_for_mesh
        wall = "🥵" * 100  # 400 bytes, no spaces
        chunks = chunk_for_mesh(wall)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.encode("utf-8")) <= _MAX
        assert "".join(chunks) == wall  # nothing dropped, no mojibake

    def test_respects_custom_max_bytes(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh("word " * 40, max_bytes=50)
        for c in chunks:
            assert len(c.encode("utf-8")) <= 50


class TestRNSToMeshChunking:
    """The RNS→Mesh bridge must chunk oversize content, not truncate it."""

    def test_direct_path_sends_each_chunk_under_limit(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=_LEADERBOARD,
        )
        sent = []

        def capture_send(content, destination=None, channel=0):
            sent.append(content)
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert len(sent) > 1  # chunked, not one truncated packet
        for chunk in sent:
            assert len(chunk.encode("utf-8")) <= _MAX
        # No data lost: every leaderboard line lands in some chunk
        joined = "\n".join(sent)
        for line in _LEADERBOARD.split("\n"):
            assert line.lstrip("📊") in joined or line in joined
        # One source message → one delivered increment regardless of chunk count
        assert bridge.stats['messages_rns_to_mesh'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 1

    def test_prefix_on_every_chunk(self, bridge):
        """[RNS:xxxx] attribution rides EVERY chunk (2026-06-04 inversion).

        The original chunk-0-only "byte-efficient" shape was disproven live:
        untagged tail chunks bypassed is_already_bridged on every gateway —
        a dual-radio box re-bridged a peer's relayed tails back onto its
        primary RF, and tag-adding legs overflowed the byte cap on max-size
        tails. The tag is the echo-loop invariant; it rides every packet.
        """
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=_LEADERBOARD,
        )
        sent = []
        with patch.object(bridge, 'send_to_meshtastic',
                          side_effect=lambda content, destination=None, channel=0: sent.append(content) or True):
            bridge._process_rns_to_mesh(msg)

        assert len(sent) >= 2
        for chunk in sent:
            assert chunk.startswith("[RNS:abcd] ")

    def test_direct_partial_failure_counts_dropped(self, bridge):
        """If not every chunk goes out, the message is a drop, not a success."""
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=_LEADERBOARD,
        )
        # First chunk succeeds, rest fail
        results = iter([True])

        def flaky(content, destination=None, channel=0):
            try:
                return next(results)
            except StopIteration:
                return False

        with patch.object(bridge, 'send_to_meshtastic', side_effect=flaky), \
             patch.object(bridge, '_requeue_failed_message', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats['rns_to_mesh_delivered'] == 0
        assert bridge.stats['rns_to_mesh_dropped'] == 1

    def test_queue_path_enqueues_one_item_per_chunk(self):
        """In mqtt_bridge mode each chunk is its own queue item (independent
        retry), and every enqueued message is within the byte cap."""
        with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
             patch("gateway.rns_bridge.UnifiedNodeTracker"), \
             patch("gateway.rns_bridge.BridgeHealthMonitor"), \
             patch("gateway.rns_bridge.DeliveryTracker"), \
             patch("gateway.rns_bridge.MeshtasticHandler"), \
             patch("gateway.rns_bridge.MQTTBridgeHandler") as MockMQTT, \
             patch("gateway.rns_bridge.ReconnectStrategy"), \
             patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
             patch("gateway.rns_bridge.HAS_MQTT_BRIDGE", True), \
             patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True), \
             patch("gateway.rns_bridge.PersistentMessageQueue") as MockQueue, \
             patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
             patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
             patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
             patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):

            config = _mock_gateway_config(bridge_mode="mqtt_bridge")
            MockConfig.load.return_value = config
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = "msg-id"
            MockQueue.return_value = mock_queue
            MockMQTT.return_value = MagicMock()

            from gateway.rns_bridge import RNSMeshtasticBridge, BridgedMessage
            b = RNSMeshtasticBridge(config=config)
            msg = BridgedMessage(
                source_network="rns", source_id="abcdef01",
                destination_id=None, content=_LEADERBOARD,
            )
            b._process_rns_to_mesh(msg)

            mesh_enqueues = [
                c for c in mock_queue.enqueue.call_args_list
                if c.kwargs.get("destination") == "meshtastic"
            ]
            assert len(mesh_enqueues) > 1  # one per chunk
            for c in mesh_enqueues:
                payload_msg = c.kwargs["payload"]["message"]
                assert len(payload_msg.encode("utf-8")) <= _MAX
                # Dedup is left ON (default) so a re-sent forecast/command is
                # not re-broadcast onto RF — must not pass deduplicate=False.
                assert c.kwargs.get("deduplicate") is not False
            assert b.stats['messages_rns_to_mesh'] == 1

    def _queue_bridge(self, enqueue_side_effect):
        """Build an mqtt_bridge bridge whose persistent queue.enqueue uses the
        given side_effect, returning (bridge, mock_queue)."""
        with patch("gateway.rns_bridge.GatewayConfig") as MockConfig, \
             patch("gateway.rns_bridge.UnifiedNodeTracker"), \
             patch("gateway.rns_bridge.BridgeHealthMonitor"), \
             patch("gateway.rns_bridge.DeliveryTracker"), \
             patch("gateway.rns_bridge.MeshtasticHandler"), \
             patch("gateway.rns_bridge.MQTTBridgeHandler") as MockMQTT, \
             patch("gateway.rns_bridge.ReconnectStrategy"), \
             patch("gateway.rns_bridge.HAS_CIRCUIT_BREAKER", False), \
             patch("gateway.rns_bridge.HAS_MQTT_BRIDGE", True), \
             patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True), \
             patch("gateway.rns_bridge.PersistentMessageQueue") as MockQueue, \
             patch("gateway.message_routing.CLASSIFIER_AVAILABLE", False), \
             patch("gateway.rns_bridge.HAS_SERVICE_CHECK", False), \
             patch("gateway.rns_bridge.HAS_EVENT_BUS", False), \
             patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):
            config = _mock_gateway_config(bridge_mode="mqtt_bridge")
            MockConfig.load.return_value = config
            mock_queue = MagicMock()
            mock_queue.enqueue.side_effect = enqueue_side_effect
            MockQueue.return_value = mock_queue
            MockMQTT.return_value = MagicMock()
            from gateway.rns_bridge import RNSMeshtasticBridge
            return RNSMeshtasticBridge(config=config), mock_queue

    def test_deduped_chunk_does_not_abort_or_error(self):
        """A falsy enqueue (dedup suppression) must NOT abort remaining chunks
        nor count as a failure — the regression that re-truncated replies."""
        from gateway.rns_bridge import BridgedMessage
        from gateway.base_handler import chunk_for_mesh
        # First chunk "dedups" (enqueue -> None); the rest succeed.
        calls = {"n": 0}
        def side_effect(*a, **k):
            calls["n"] += 1
            return None if calls["n"] == 1 else "msg-id"
        b, mock_queue = self._queue_bridge(side_effect)
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content=_LEADERBOARD)
        expected = len(chunk_for_mesh(_LEADERBOARD, prefix="[RNS:abcd] "))
        assert expected >= 2
        b._process_rns_to_mesh(msg)
        mesh_enqueues = [c for c in mock_queue.enqueue.call_args_list
                         if c.kwargs.get("destination") == "meshtastic"]
        assert len(mesh_enqueues) == expected  # every chunk attempted, no abort
        assert b.stats['rns_to_mesh_dropped'] == 0
        assert b.stats['errors'] == 0
        assert b.stats['rns_to_mesh_delivered'] == 1

    def test_fully_deduped_message_is_not_a_failure(self):
        """Every chunk suppressed (an exact duplicate forecast/command) is the
        system working — not an error, not a drop."""
        from gateway.rns_bridge import BridgedMessage
        b, mock_queue = self._queue_bridge(lambda *a, **k: None)  # all dedup
        # A MagicMock queue reports is_recent_duplicate() truthy by default, so
        # the falsy enqueue is classified as dedup-suppression (benign).
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content="wx")
        b._process_rns_to_mesh(msg)
        assert b.stats['errors'] == 0
        assert b.stats['rns_to_mesh_dropped'] == 0
        assert b.stats['messages_rns_to_mesh'] == 1

    def test_queue_pressure_rejection_is_a_drop_not_a_delivery(self):
        """A falsy enqueue that is NOT a recent duplicate (queue full /
        unsheddable) is genuine data loss — it must count as a drop, NOT be
        booked as a delivery. Regression: pressure drops were masked as
        rns_to_mesh_delivered + record_message_sent and only debug-logged."""
        from gateway.rns_bridge import BridgedMessage
        b, mock_queue = self._queue_bridge(lambda *a, **k: None)  # all reject
        mock_queue.is_recent_duplicate.return_value = False  # not a dup → drop
        msg = BridgedMessage(source_network="rns", source_id="abcdef01",
                             destination_id=None, content="wx")
        b._process_rns_to_mesh(msg)
        assert b.stats['rns_to_mesh_delivered'] == 0
        assert b.stats['rns_to_mesh_dropped'] == 1
        assert b.stats['errors'] == 1
        assert b.stats['messages_rns_to_mesh'] == 0


# ---------------------------------------------------------------------------
# Phase-1 fluid bridge — relay-on-receive
# ---------------------------------------------------------------------------


class TestRelayOnReceive:
    """Tests for _maybe_relay_to_peers + origin-marker handling.

    Phase 1 of the fluid-bridge roadmap: a NomadNet-typed message
    arriving at one gateway in a cluster fans out to every peer
    gateway listed in rns.peer_gateway_destinations. Loop prevention
    is via the ``meshforge_relayed_by`` LXMF field set on relay
    copies; receiving gateways inspect the field and skip re-relay.
    """

    OWN_HEX = "aa" * 16  # 32 hex chars
    PEER_A = "bb" * 16
    PEER_B = "cc" * 16

    def _configure_peers(self, bridge, peers):
        """Wire peer_gateway_destinations and own LXMF hash on the mocked bridge."""
        bridge.config.rns.get_peer_gateway_destinations = MagicMock(
            return_value=list(peers)
        )
        own_hash = MagicMock()
        own_hash.hex.return_value = self.OWN_HEX
        bridge._lxmf_source = MagicMock()
        bridge._lxmf_source.hash = own_hash

    def test_original_relays_to_each_peer(self, bridge):
        """No relayed_by + 2 peers configured = 2 send_to_rns calls."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A, self.PEER_B])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hello cluster",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 2
        for call in mock_rns.call_args_list:
            kwargs = call.kwargs
            assert kwargs["title"]
            assert kwargs["fields"]["meshforge_relayed_by"] == self.OWN_HEX
            assert kwargs["fields"]["meshforge_origin_source_id"] == "abcdef01"
        sent_dests = {call.args[1] for call in mock_rns.call_args_list}
        assert sent_dests == {bytes.fromhex(self.PEER_A),
                              bytes.fromhex(self.PEER_B)}
        assert bridge.stats["relay_to_peers_attempted"] == 2
        assert bridge.stats["relay_to_peers_delivered"] == 2

    def test_relayed_message_does_not_re_relay(self, bridge):
        """meshforge_relayed_by present => skip relay (loop prevention)."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="echoed back",
            metadata={"lxmf_fields": {
                "meshforge_relayed_by": self.PEER_A,
                "meshforge_origin_source_id": "deadbeef",
            }},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 0
        assert "relay_to_peers_attempted" not in bridge.stats

    def test_relayed_uses_origin_source_for_prefix(self, bridge):
        """Relayed msg's [RNS:xxxx] must show the original NomadNet, not the relayer."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [])  # no further relay
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER_A,  # relayer's hash
            destination_id=None, content="from origin",
            metadata={"lxmf_fields": {
                "meshforge_relayed_by": self.PEER_A,
                "meshforge_origin_source_id": "deadbeef",
            }},
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert captured["content"].startswith("[RNS:dead] ")

    def test_self_hash_in_peer_list_is_skipped(self, bridge):
        """Defensive: own hash listed under peers must not relay to self."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.OWN_HEX, self.PEER_A])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 1
        assert mock_rns.call_args.args[1] == bytes.fromhex(self.PEER_A)

    def test_invalid_peer_hex_skipped_with_warning(self, bridge):
        """Non-hex / wrong-length entries must be skipped, valid ones still relay."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, ["not-a-hash", "ab", self.PEER_A])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 1

    def test_no_peers_configured_no_relay(self, bridge):
        """Empty peer list = no relay, even on original messages."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 0

    def test_local_tx_failure_does_not_relay(self, bridge):
        """Phase-1 conservative: skip relay when local R→M fails so backpressure
        signals from one preset don't multiply onto others."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 0

    def test_relay_send_failure_counts_attempted_not_delivered(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A, self.PEER_B])
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns',
                          side_effect=[True, False]):
            bridge._process_rns_to_mesh(msg)

        assert bridge.stats["relay_to_peers_attempted"] == 2
        assert bridge.stats["relay_to_peers_delivered"] == 1

    def test_relay_uses_original_body_with_at_token(self, bridge):
        """@addr DM at the originating gateway is forwarded with the @addr
        intact so a peer gateway whose preset hosts the target node can
        still resolve and DM. The local TX strips the token; the relay
        does not."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A])
        bridge.node_tracker.get_node_by_short_name.return_value = None
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="@HAT3 ping",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 1
        # First positional arg is the body forwarded to the peer.
        assert mock_rns.call_args.args[0] == "@HAT3 ping"

    def test_no_lxmf_source_skips_relay(self, bridge):
        """RNS not yet initialized — _lxmf_source None — must not crash."""
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_peer_gateway_destinations = MagicMock(
            return_value=[self.PEER_A]
        )
        bridge._lxmf_source = None
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hi",
            metadata={"lxmf_fields": {}},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 0

    def test_peer_sourced_fanout_does_not_re_relay(self, bridge):
        """Critical guard: a mesh-source M→R fan-out from a peer gateway
        arrives at this gateway via the existing default_lxmf_destination
        broadcast list. msg.source_id IS the peer's own LXMF hash. We
        must NOT relay back — that would duplicate every mesh-sourced
        message on every preset. This is independent of whether the peer
        set ``meshforge_relayed_by`` (legacy peers won't)."""
        from gateway.rns_bridge import BridgedMessage
        self._configure_peers(bridge, [self.PEER_A])
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER_A,
            destination_id=None, content="from peer M->R",
            metadata={"lxmf_fields": {
                # Peer's M→R fan-out includes Issue #39 attribution but
                # not meshforge_relayed_by (it's not a relay; it's the
                # original M→R fan-out).
                "meshforge_source_network": "meshtastic",
                "meshforge_from_id": "!aabbccdd",
            }},
        )
        with patch.object(bridge, 'send_to_meshtastic', return_value=True), \
             patch.object(bridge, 'send_to_rns', return_value=True) as mock_rns:
            bridge._process_rns_to_mesh(msg)

        assert mock_rns.call_count == 0


# ---------------------------------------------------------------------------
# content_id_view infra_hashes (dedup/identity arc STEP 6)
# ---------------------------------------------------------------------------


class TestContentIdViewInfraHashesStep6:
    """The publish hook stamps the box's KNOWN gateway/peer hashes (own LXMF
    source hash + peer_gateway_destinations) so the cross-box JOIN can tag a
    dup to a gateway recipient (e.g. MeshAnchor 58cecbd0) as infra-to-infra
    rather than human. default_lxmf_destination (which carries HUMAN inboxes)
    is deliberately NOT a source — a dup to a real operator must still page."""

    OWN = "aa" * 16
    PEER = "bb" * 16

    def test_infra_hashes_are_own_plus_peers_lowercased(self, bridge):
        bridge.config.rns.get_peer_gateway_destinations = MagicMock(
            return_value=[self.PEER.upper()])
        own_hash = MagicMock()
        own_hash.hex.return_value = self.OWN.upper()
        bridge._lxmf_source = MagicMock()
        bridge._lxmf_source.hash = own_hash
        result = bridge._content_id_view_infra_hashes()
        assert self.OWN in result    # own gateway hash, lowercased
        assert self.PEER in result   # peer gateway, lowercased

    def test_no_lxmf_source_still_returns_peers(self, bridge):
        bridge.config.rns.get_peer_gateway_destinations = MagicMock(
            return_value=[self.PEER])
        bridge._lxmf_source = None
        result = bridge._content_id_view_infra_hashes()
        assert result == [self.PEER]   # no own hash, no crash

    def test_peer_lookup_error_isolated_returns_empty(self, bridge):
        bridge.config.rns.get_peer_gateway_destinations = MagicMock(
            side_effect=TypeError("boom"))
        bridge._lxmf_source = None
        assert bridge._content_id_view_infra_hashes() == []


# ---------------------------------------------------------------------------
# _resolve_mesh_destination
# ---------------------------------------------------------------------------


class TestResolveMeshDestination:
    """Tests for the @address resolver used by _process_rns_to_mesh."""

    def test_hex_id_lowercased(self, bridge):
        assert bridge._resolve_mesh_destination("!EBFA1B11") == "!ebfa1b11"

    def test_empty_token_returns_none(self, bridge):
        assert bridge._resolve_mesh_destination("") is None

    def test_malformed_hex_falls_through(self, bridge):
        bridge.node_tracker.get_node_by_short_name.return_value = None
        assert bridge._resolve_mesh_destination("!notahexid") is None

    def test_shortname_resolved(self, bridge):
        mock_node = MagicMock()
        mock_node.meshtastic_id = "!ebfa1b11"
        bridge.node_tracker.get_node_by_short_name.return_value = mock_node
        assert bridge._resolve_mesh_destination("HAT3") == "!ebfa1b11"

    def test_shortname_with_no_meshtastic_id_returns_none(self, bridge):
        mock_node = MagicMock()
        mock_node.meshtastic_id = None
        bridge.node_tracker.get_node_by_short_name.return_value = mock_node
        assert bridge._resolve_mesh_destination("GHST") is None


# ---------------------------------------------------------------------------
# _requeue_failed_message
# ---------------------------------------------------------------------------

class TestRequeueFailedMessage:
    """Tests for _requeue_failed_message."""

    def test_no_persistent_queue_returns_false(self, bridge):
        bridge._persistent_queue = None
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="test",
        )
        assert bridge._requeue_failed_message(msg, "rns") is False

    def test_with_persistent_queue_enqueues(self, bridge):
        mock_queue = MagicMock()
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id="!dest", content="retry me",
            metadata={"channel": 1},
        )
        result = bridge._requeue_failed_message(msg, "rns")
        assert result is True
        mock_queue.enqueue.assert_called_once()

    def test_enqueue_exception_returns_false(self, bridge):
        mock_queue = MagicMock()
        mock_queue.enqueue.side_effect = RuntimeError("db error")
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="fail",
        )
        assert bridge._requeue_failed_message(msg, "rns") is False

    def test_bytes_content_serializes_as_str(self, bridge):
        """Retry payload must be JSON-serializable: decode bytes to str."""
        mock_queue = MagicMock()
        bridge._persistent_queue = mock_queue

        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abc",
            destination_id=None, content=b"retry with bytes",
        )
        assert bridge._requeue_failed_message(msg, "meshtastic") is True
        call = mock_queue.enqueue.call_args
        assert call.kwargs["payload"]["message"] == "retry with bytes"
        assert isinstance(call.kwargs["payload"]["message"], str)


# ---------------------------------------------------------------------------
# enqueue_message
# ---------------------------------------------------------------------------

class TestEnqueueMessage:
    """Tests for enqueue_message method."""

    def test_no_queue_falls_back_to_direct_meshtastic(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic")
        assert result == "direct"

    def test_no_queue_falls_back_to_direct_rns(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_rns', return_value=True):
            result = bridge.enqueue_message("hi", "dest_hash", dest_type="rns",
                                           destination_hash="aabbccdd")
        assert result == "direct"

    def test_no_queue_direct_failure_returns_none(self, bridge):
        bridge._persistent_queue = None

        with patch.object(bridge, 'send_to_meshtastic', return_value=False):
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic")
        assert result is None

    def test_with_queue_enqueues(self, bridge):
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "msg-123"
        bridge._persistent_queue = mock_queue

        with patch("gateway.rns_bridge.MessagePriority") as MockPriority:
            MockPriority.NORMAL = "normal"
            MockPriority.HIGH = "high"
            MockPriority.LOW = "low"
            MockPriority.URGENT = "urgent"
            result = bridge.enqueue_message("hi", "!dest", dest_type="meshtastic", priority="normal")

        assert result == "msg-123"
        mock_queue.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# get_queue_stats
# ---------------------------------------------------------------------------

class TestGetQueueStats:
    """Tests for get_queue_stats."""

    def test_no_queue_returns_empty(self, bridge):
        bridge._persistent_queue = None
        assert bridge.get_queue_stats() == {}

    def test_with_queue_delegates(self, bridge):
        mock_queue = MagicMock()
        mock_queue.get_stats.return_value = {"pending": 5}
        bridge._persistent_queue = mock_queue
        assert bridge.get_queue_stats() == {"pending": 5}


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------

class TestTestConnection:
    """Tests for test_connection method."""

    def test_both_disconnected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = False
        with patch.object(bridge, '_test_rns', return_value=False):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['rns']['connected'] is False

    def test_meshtastic_connected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = True
        with patch.object(bridge, '_test_rns', return_value=False):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is True
        assert result['rns']['connected'] is False

    def test_rns_connected(self, bridge):
        bridge._mesh_handler.test_connection.return_value = False
        with patch.object(bridge, '_test_rns', return_value=True):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['rns']['connected'] is True

    def test_meshtastic_error(self, bridge):
        bridge._mesh_handler.test_connection.side_effect = RuntimeError("fail")
        with patch.object(bridge, '_test_rns', return_value=True):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is False
        assert result['meshtastic']['error'] == "fail"
        assert result['rns']['connected'] is True

    def test_rns_error(self, bridge):
        bridge._mesh_handler.test_connection.return_value = True
        with patch.object(bridge, '_test_rns', side_effect=RuntimeError("rns fail")):
            result = bridge.test_connection()
        assert result['meshtastic']['connected'] is True
        assert result['rns']['connected'] is False
        assert result['rns']['error'] == "rns fail"


# ---------------------------------------------------------------------------
# on_meshtastic_receive compatibility shim
# ---------------------------------------------------------------------------

class TestOnMeshtasticReceive:
    """Tests for _on_meshtastic_receive compatibility shim."""

    def test_delegates_to_handler(self, bridge):
        packet = {"decoded": {"text": "hello"}}
        bridge._on_meshtastic_receive(packet)
        bridge._mesh_handler._on_receive.assert_called_once_with(packet)

    def test_no_handler_no_error(self, bridge):
        bridge._mesh_handler = None
        bridge._on_meshtastic_receive({"decoded": {}})  # Should not raise


# ---------------------------------------------------------------------------
# _get_rns_destination
# ---------------------------------------------------------------------------

class TestGetRNSDestination:
    """Tests for _get_rns_destination."""

    def test_returns_rns_hash_if_found(self, bridge):
        mock_node = MagicMock()
        mock_node.rns_hash = b'\xab\xcd\xef'
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        result = bridge._get_rns_destination("!aabb0042")
        assert result == b'\xab\xcd\xef'

    def test_returns_none_if_not_found(self, bridge):
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        assert bridge._get_rns_destination("!aabb0042") is None

    def test_returns_none_if_no_rns_hash(self, bridge):
        mock_node = MagicMock(spec=[])  # No rns_hash attribute
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        assert bridge._get_rns_destination("!aabb0042") is None


# ---------------------------------------------------------------------------
# Routing stats / classification
# ---------------------------------------------------------------------------

class TestRoutingStats:
    """Tests for routing stats and classification methods."""

    def test_get_routing_stats_no_classifier(self, bridge):
        bridge._router._classifier = None
        stats = bridge.get_routing_stats()
        assert 'messages_mesh_to_rns' in stats
        assert 'classifier' not in stats

    def test_get_last_classification_none(self, bridge):
        bridge._router._last_classification = None
        assert bridge.get_last_classification() is None

    def test_get_last_classification_returns_dict(self, bridge):
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"category": "bridge_rns", "confidence": 0.9}
        bridge._router._last_classification = mock_result
        result = bridge.get_last_classification()
        assert result["category"] == "bridge_rns"

    def test_fix_routing_no_classifier(self, bridge):
        bridge._router._classifier = None
        assert bridge.fix_routing("msg-1", "bridge_rns") is False

    def test_fix_routing_no_fix_registry(self, bridge):
        bridge._router._classifier = MagicMock()
        bridge._router._classifier.fix_registry = None
        assert bridge.fix_routing("msg-1", "bridge_rns") is False


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStartStop:
    """Tests for start/stop lifecycle."""

    def test_start_sets_running(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'), \
             patch("gateway._channel_resolver.apply_resolved_channel"):
            bridge.start()
        assert bridge._running is True
        assert bridge.stats['start_time'] is not None

    def test_start_when_already_running(self, bridge):
        bridge._running = True
        result = bridge.start()
        assert result is True

    def test_start_returns_true(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'), \
             patch("gateway._channel_resolver.apply_resolved_channel"):
            result = bridge.start()
        assert result is True

    def test_stop_clears_state(self, bridge):
        bridge._running = True
        bridge._mesh_handler = MagicMock()
        bridge._persistent_queue = MagicMock()

        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()

        assert bridge._running is False
        bridge._persistent_queue.stop_processing.assert_called_once()

    def test_stop_when_not_running(self, bridge):
        bridge._running = False
        bridge.stop()  # Should not raise

    def test_start_starts_node_tracker(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'), \
             patch("gateway._channel_resolver.apply_resolved_channel"):
            bridge.start()
        bridge.node_tracker.start.assert_called_once()

    def test_stop_stops_node_tracker(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        bridge.node_tracker.stop.assert_called_once()

    def test_stop_disconnects_mesh_handler(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        bridge._mesh_handler.disconnect.assert_called_once()

    def test_stop_sets_stop_event(self, bridge):
        bridge._running = True
        with patch.object(bridge, '_disconnect_rns'), \
             patch.object(bridge, '_stop_websocket_server'):
            bridge.stop()
        assert bridge._stop_event.is_set()


# ---------------------------------------------------------------------------
# RNS connection flow
# ---------------------------------------------------------------------------

class TestRNSConnectionFlow:
    """Tests for RNS connection and LXMF setup flow."""

    def test_connect_rns_import_error_is_permanent(self, bridge):
        """When RNS/LXMF modules are unavailable, ``_connect_rns`` must mark
        the init permanently failed and bail before any config access.

        Patches the module-level ``_HAS_RNS`` / ``_HAS_LXMF`` flags directly
        per CLAUDE.md Issue #29 ("Patch _HAS_* flags directly, not
        sys.modules"). The previous builtins.__import__ approach didn't
        work because safe_import() resolves those flags at module load
        time — a later __import__ hook can't flip them, so the guard at
        the top of _connect_rns never triggered and the code instead fell
        through to the main try block where the MagicMock config tripped
        a different exception path that leaves _rns_init_failed_permanently
        at False.
        """
        bridge._rns_pre_initialized = False

        with patch('gateway._rns_bridge_connection._HAS_RNS', False), \
             patch('gateway._rns_bridge_connection._HAS_LXMF', False):
            bridge._connect_rns()

        assert bridge._connected_rns is False
        assert bridge._rns_init_failed_permanently is True

    def test_disconnect_rns_clears_all_state(self, bridge):
        bridge._reticulum = MagicMock()
        bridge._lxmf_router = MagicMock()
        bridge._lxmf_source = MagicMock()
        bridge._identity = MagicMock()
        bridge._connected_rns = True

        with patch.dict('sys.modules', {'RNS': MagicMock()}):
            bridge._disconnect_rns()

        assert bridge._reticulum is None
        assert bridge._lxmf_router is None
        assert bridge._lxmf_source is None
        assert bridge._identity is None
        assert bridge._connected_rns is False

    def test_disconnect_rns_handles_no_reticulum(self, bridge):
        bridge._reticulum = None
        bridge._disconnect_rns()  # Should not raise
        assert bridge._connected_rns is False

    def test_rns_loop_logs_permanent_failure(self, bridge):
        bridge._running = True
        bridge._rns_init_failed_permanently = True

        def stop_after_wait(timeout):
            bridge._running = False
            return True

        bridge._stop_event = MagicMock()
        bridge._stop_event.wait = stop_after_wait

        # Should log warning and exit
        bridge._rns_loop()
        # Verify it ran without error (no assertion on logger needed)

    def test_suppress_signal_noop_on_main_thread(self, bridge):
        """Signal suppression is a no-op passthrough on the main thread."""
        import signal
        original = signal.signal
        with bridge._suppress_signal_in_thread():
            assert signal.signal is original

    def test_suppress_signal_replaces_in_background_thread(self, bridge):
        """Signal suppression replaces signal.signal in background threads."""
        import signal
        results = {}

        def _check():
            with bridge._suppress_signal_in_thread():
                results['replaced'] = signal.signal is not _original
                # Should return SIG_DFL instead of raising ValueError
                results['retval'] = signal.signal(signal.SIGTERM, signal.SIG_DFL)
            results['restored'] = signal.signal is _original

        _original = signal.signal
        t = threading.Thread(target=_check)
        t.start()
        t.join(timeout=5)

        assert results.get('replaced') is True
        assert results.get('retval') == signal.SIG_DFL
        assert results.get('restored') is True

    def test_connect_rns_lxmf_signal_error_handled(self, bridge):
        """LXMF signal error in background thread is suppressed by context manager."""
        import signal as _signal_mod
        bridge._rns_pre_initialized = True

        mock_rns = MagicMock()
        mock_lxmf = MagicMock()

        # Simulate LXMF.LXMRouter() calling signal.signal() internally
        def lxmf_router_init(*args, **kwargs):
            # This would fail in a real background thread without suppression
            _signal_mod.signal(_signal_mod.SIGTERM, _signal_mod.SIG_DFL)
            return MagicMock()

        mock_lxmf.LXMRouter = lxmf_router_init

        with patch('gateway._rns_bridge_connection._RNS_mod', mock_rns), \
             patch('gateway._rns_bridge_connection._LXMF_mod', mock_lxmf), \
             patch('gateway._rns_bridge_connection._HAS_RNS', True), \
             patch('gateway._rns_bridge_connection._HAS_LXMF', True), \
             patch('gateway._rns_bridge_connection.check_service', return_value=MagicMock(available=True)):
            # Run from background thread to trigger suppression
            errors = []
            def _run():
                try:
                    bridge._connect_rns()
                except Exception as e:
                    errors.append(e)

            t = threading.Thread(target=_run)
            t.start()
            t.join(timeout=5)

            assert not errors, f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Module-level headless helpers
# ---------------------------------------------------------------------------

class TestHeadlessHelpers:
    """Tests for module-level helper functions (extracted to gateway_cli.py)."""

    def test_is_gateway_running_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            assert cli.is_gateway_running() is False
        finally:
            cli._active_bridge = original

    def test_is_gateway_running_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            cli._active_bridge = mock_bridge
            assert cli.is_gateway_running() is True
        finally:
            cli._active_bridge = original

    def test_is_gateway_running_stopped_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = False
            cli._active_bridge = mock_bridge
            assert cli.is_gateway_running() is False
        finally:
            cli._active_bridge = original

    def test_get_gateway_stats_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            stats = cli.get_gateway_stats()
            assert stats['running'] is False
            assert stats['status'] == 'Not started'
        finally:
            cli._active_bridge = original

    def test_get_gateway_stats_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge._running = True
            mock_bridge._mesh_handler.is_connected = True
            mock_bridge._connected_rns = False
            mock_bridge.get_status.return_value = {
                'statistics': {
                    'messages_mesh_to_rns': 3,
                    'messages_rns_to_mesh': 1,
                    'errors': 0,
                    'bounced': 0,
                },
                'uptime_seconds': 120.0,
            }
            mock_bridge.health.get_summary.return_value = {"status": "ok"}
            mock_bridge.delivery_tracker.get_stats.return_value = {"delivered": 2}
            cli._active_bridge = mock_bridge

            stats = cli.get_gateway_stats()
            assert stats['running'] is True
            assert stats['messages_mesh_to_rns'] == 3
            assert stats['health'] == {"status": "ok"}
            assert stats['delivery'] == {"delivered": 2}
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_no_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = None
            assert cli.stop_gateway_headless() is True
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_with_bridge(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            cli._active_bridge = mock_bridge
            assert cli.stop_gateway_headless() is True
            mock_bridge.stop.assert_called_once()
            assert cli._active_bridge is None
        finally:
            cli._active_bridge = original

    def test_stop_gateway_headless_error(self):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            mock_bridge = MagicMock()
            mock_bridge.stop.side_effect = RuntimeError("stop fail")
            cli._active_bridge = mock_bridge
            assert cli.stop_gateway_headless() is False
        finally:
            cli._active_bridge = original

    def test_reexport_from_rns_bridge(self):
        """Verify backward-compatible re-export from rns_bridge."""
        import gateway.rns_bridge as mod
        assert hasattr(mod, 'start_gateway_headless')
        assert hasattr(mod, 'stop_gateway_headless')
        assert hasattr(mod, 'get_gateway_stats')
        assert hasattr(mod, 'is_gateway_running')


# ---------------------------------------------------------------------------
# Thread safety of callbacks
# ---------------------------------------------------------------------------

class TestCallbackThreadSafety:
    """Tests for thread safety of callback systems."""

    def test_concurrent_callback_registration(self, bridge):
        """Multiple threads register callbacks concurrently."""
        errors = []

        def register_callbacks():
            try:
                for _ in range(20):
                    bridge.register_message_callback(lambda msg: None)
                    bridge.register_status_callback(lambda s, d: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_callbacks) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0
        assert len(bridge._message_callbacks) == 100
        assert len(bridge._status_callbacks) == 100

    def test_concurrent_notify_and_register(self, bridge):
        """Notification while registration is happening should not crash."""
        errors = []
        from gateway.rns_bridge import BridgedMessage

        def register_loop():
            try:
                for _ in range(50):
                    bridge.register_message_callback(lambda msg: None)
            except Exception as e:
                errors.append(e)

        def notify_loop():
            try:
                msg = BridgedMessage(
                    source_network="rns", source_id="abc",
                    destination_id=None, content="x"
                )
                for _ in range(50):
                    bridge._notify_message(msg)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=register_loop)
        t2 = threading.Thread(target=notify_loop)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# _bridge_loop
# ---------------------------------------------------------------------------

class TestBridgeLoop:
    """Tests for _bridge_loop message processing."""

    def test_processes_mesh_to_rns_queue(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!abc",
            destination_id=None, content="test",
        )
        bridge._mesh_to_rns_queue.put(msg)
        bridge._running = True

        processed = []

        def mock_process(m):
            processed.append(m)
            bridge._running = False  # Stop after first iteration

        with patch.object(bridge, '_process_mesh_to_rns', side_effect=mock_process), \
             patch.object(bridge, '_process_rns_to_mesh'):
            bridge._bridge_loop()

        assert len(processed) == 1
        assert processed[0].content == "test"

    def test_processes_rns_to_mesh_queue(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abc",
            destination_id=None, content="rns msg",
        )
        bridge._rns_to_mesh_queue.put(msg)
        bridge._running = True

        processed = []

        def mock_process(m):
            processed.append(m)
            bridge._running = False

        with patch.object(bridge, '_process_mesh_to_rns'), \
             patch.object(bridge, '_process_rns_to_mesh', side_effect=mock_process):
            bridge._bridge_loop()

        assert len(processed) == 1

    def test_bridge_loop_handles_exception(self, bridge):
        """Bridge loop should not crash on exception."""
        bridge._running = True
        call_count = 0

        def failing_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("queue error")
            bridge._running = False
            raise Empty()

        with patch.object(bridge._mesh_to_rns_queue, 'get', side_effect=failing_get):
            bridge._bridge_loop()

        # Should have continued past the errors
        assert call_count >= 2


# ---------------------------------------------------------------------------
# _test_rns
# ---------------------------------------------------------------------------

class TestTestRNS:
    """Tests for _test_rns method."""

    @patch('gateway.rns_bridge._HAS_RNS', True)
    def test_returns_true_when_importable(self, bridge):
        assert bridge._test_rns() is True

    @patch('gateway.rns_bridge._HAS_RNS', False)
    def test_returns_false_when_not_importable(self, bridge):
        assert bridge._test_rns() is False


# ---------------------------------------------------------------------------
# _REGEX_INPUT_LIMIT
# ---------------------------------------------------------------------------

class TestRegexInputLimit:
    """Tests for regex input length bounding."""

    def test_limit_is_set(self):
        from gateway.message_routing import MessageRouter
        assert MessageRouter._REGEX_INPUT_LIMIT == 512


# ---------------------------------------------------------------------------
# WebSocket server integration
# ---------------------------------------------------------------------------

class TestWebSocketServer:
    """Tests for WebSocket server start/stop."""

    def test_start_websocket_handles_import_error(self, bridge):
        with patch("gateway.rns_bridge.HAS_RNS_SNIFFER", False):
            # Should not crash when websocket module not available
            bridge._start_websocket_server()

    def test_stop_websocket_when_not_started(self, bridge):
        bridge._websocket_started = False
        bridge._stop_websocket_server()  # Should not raise


# ---------------------------------------------------------------------------
# start() — refuse-loud on channel resolution error
# ---------------------------------------------------------------------------

class TestBridgeStartRefuses:
    """start() must refuse when bridge TX channel cannot be confirmed."""

    def test_start_returns_false_on_channel_resolution_error(self, bridge, caplog):
        """Per feedback_refuse_broken_configs: an unverifiable bridge channel
        must not silently fall through. Bridge refuses to start; service exits;
        operator sees the failure in journalctl. Prevents a recurrence of the
        2026-04-24 vail/Regional leak."""
        from gateway._channel_resolver import ChannelResolutionError

        with patch(
            "gateway._channel_resolver.apply_resolved_channel",
            side_effect=ChannelResolutionError(
                "Bridge channel 'meshforge' is not present on the radio's "
                "channel list. Refusing to start."
            ),
        ):
            with caplog.at_level("ERROR"):
                result = bridge.start()

        assert result is False
        assert bridge._running is False
        # No worker threads spawned
        assert getattr(bridge, "_mesh_thread", None) is None
        assert getattr(bridge, "_rns_thread", None) is None
        # Operator-visible error with fix-hint hook
        assert any("Refusing to start bridge" in rec.message for rec in caplog.records)
        assert any("meshforge" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _on_lxmf_receive — sniffer-capture branch (Issue #1162)
# ---------------------------------------------------------------------------

class TestLXMFReceiveSnifferCapture:
    """The traffic-inspection sniffer hook in _on_lxmf_receive must accept
    both bytes and str content. LXMessage.content arrives as bytes for
    binary LXMF payloads; pre-fix the .encode('utf-8') call raised
    AttributeError, dropping the capture (delivery itself was unaffected
    but observability missed the message)."""

    @staticmethod
    def _make_message(content):
        msg = MagicMock()
        msg.source_hash = b"\x3d\xfb\xdb\x5d" + b"\x00" * 12
        msg.content = content
        msg.title = None
        msg.stamp = None
        msg.fields = None
        return msg

    def _run_with_sniffer(self, bridge, message):
        sniffer = MagicMock()
        sniffer._running = True
        with patch("gateway.rns_bridge.HAS_RNS_SNIFFER", True), \
             patch("gateway.rns_bridge.get_rns_sniffer", return_value=sniffer), \
             patch("gateway.rns_bridge.UnifiedNode"), \
             patch("commands.messaging.store_incoming"):
            bridge._on_lxmf_receive(message)
        return sniffer

    def test_bytes_content_does_not_raise(self, bridge):
        """Regression for #1162: bytes content must pass through unchanged."""
        sniffer = self._run_with_sniffer(bridge, self._make_message(b"hello-bytes"))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == b"hello-bytes"
        assert captured.payload_size == len(b"hello-bytes")

    def test_str_content_is_utf8_encoded(self, bridge):
        sniffer = self._run_with_sniffer(bridge, self._make_message("hello-str-Ω"))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == "hello-str-Ω".encode("utf-8")
        assert captured.payload_size == len("hello-str-Ω".encode("utf-8"))

    def test_none_content_yields_empty_payload(self, bridge):
        sniffer = self._run_with_sniffer(bridge, self._make_message(None))
        sniffer._store_packet.assert_called_once()
        captured = sniffer._store_packet.call_args[0][0]
        assert captured.payload == b""
        assert captured.payload_size == 0


# ---------------------------------------------------------------------------
# Syn/ack callback symmetry — both RNS send paths wire LXMF delivery callbacks
# ---------------------------------------------------------------------------
#
# Behavioural counterpart to TestDeliveryCallbackSymmetry in
# test_regression_guards.py. The AST guard catches deletion of the
# `register_*_callback` calls; these tests catch deletion of the *effect*
# — i.e., that the callback, when fired by LXMF, actually records the
# CONFIRMED transition against the right msg_id.

class TestSynAckCallbackSymmetry:
    """Both RNS send paths must register the delivery-proof callbacks."""

    @pytest.fixture(autouse=True)
    def _isolate_delivery_counters(self, tmp_path, monkeypatch):
        """Point delivery_counters at a per-test SQLite file and reset."""
        from gateway import delivery_counters as _dc
        monkeypatch.setenv(
            "MESHFORGE_DELIVERY_COUNTERS_DB",
            str(tmp_path / "syn_ack.db"),
        )
        _dc._reset_singleton_for_tests()
        yield
        _dc._reset_singleton_for_tests()

    @staticmethod
    def _fake_rns_lxmf_modules():
        """Build the minimum sys.modules entries send paths import."""
        import sys
        fake_rns = MagicMock(name="RNS")
        fake_rns.Transport.has_path.return_value = True
        fake_rns.Identity.recall.return_value = MagicMock(name="dest_identity")
        fake_rns.Destination.OUT = "OUT"
        fake_rns.Destination.SINGLE = "SINGLE"
        fake_rns.Destination.return_value = MagicMock(name="destination")
        fake_lxmf = MagicMock(name="LXMF")
        fake_lxmessage = MagicMock(name="LXMessage_instance")
        fake_lxmf.LXMessage.return_value = fake_lxmessage
        return fake_rns, fake_lxmf, fake_lxmessage

    def _prime_bridge_for_send(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock(name="lxmf_source")
        bridge._lxmf_router = MagicMock(name="lxmf_router")

    def test_send_to_rns_registers_both_callbacks(self, bridge):
        """Direct send wires CONFIRMED + DROPPED proof callbacks."""
        import sys
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        self._prime_bridge_for_send(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            result = bridge.send_to_rns("hello", b"\xab" * 16)
        assert bool(result) is True and result.reason == ""
        assert fake_lxm.register_delivery_callback.called
        assert fake_lxm.register_failed_callback.called

    def test_send_to_rns_callback_records_content_id_and_recipient(self, bridge):
        """STEP 4a-ii: the RNS delivery-proof CONFIRMED event carries the
        content_id (from the meshforge_content_id field) + the recipient (the
        destination hash) — the (content_id, recipient) axis the dup detector
        keys on."""
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        self._prime_bridge_for_send(bridge)
        dest = b"\xcd" * 16
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            bridge.send_to_rns("hello", dest,
                               fields={"meshforge_content_id": "c1:live99"})
        delivered_cb = fake_lxm.register_delivery_callback.call_args[0][0]
        delivered_cb(MagicMock(name="receipt"))
        recent = _dc.get_singleton().snapshot()["recent"]
        matches = [e for e in recent if e.get("content_id") == "c1:live99"]
        assert matches, "no event carried the content_id"
        assert matches[-1]["state"] == "confirmed"
        assert matches[-1]["recipient"] == dest.hex()

    def test_queue_send_rns_registers_both_callbacks(self, bridge):
        """Retry path wires the same callbacks — was the Fork-D gap."""
        import sys
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        self._prime_bridge_for_send(bridge)
        payload = {
            "message": "hi",
            "destination_hash": b"\xab" * 16,
            "_queue_msg_id": "queue-row-7",
        }
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            result = bridge._queue_send_rns(payload)
        assert result is True
        assert fake_lxm.register_delivery_callback.called
        assert fake_lxm.register_failed_callback.called

    def test_queue_send_callback_increments_confirmed_for_queue_msg_id(
        self, bridge
    ):
        """End-to-end: the queue row's id flows from the dispatched
        payload through the LXMF callback registration to the counter.
        history_for(queue_id) joins SENT (recorded by mark_delivered
        elsewhere) with CONFIRMED (recorded here when LXMF fires the
        delivery proof callback)."""
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        self._prime_bridge_for_send(bridge)
        queue_id = "1700000000000-abcd1234"
        payload = {
            "message": "hi",
            "destination_hash": b"\xab" * 16,
            "_queue_msg_id": queue_id,
        }
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            bridge._queue_send_rns(payload)
        # Simulate LXMF firing the delivery proof
        delivered_cb = fake_lxm.register_delivery_callback.call_args[0][0]
        delivered_cb(MagicMock(name="receipt"))
        hist = [e.state for e in _dc.get_singleton().history_for(queue_id)]
        assert _dc.DeliveryState.CONFIRMED in hist

    def test_send_to_rns_callback_records_drop_on_failed_receipt(
        self, bridge
    ):
        """Direct path on_failed callback records DROPPED with the
        RNS_DELIVERY_FAILED reason — counter side of the syn/ack contract."""
        import sys
        from gateway import delivery_counters as _dc
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        self._prime_bridge_for_send(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            bridge.send_to_rns("hello", b"\xab" * 16)
        failed_cb = fake_lxm.register_failed_callback.call_args[0][0]
        receipt = MagicMock()
        receipt.failure_reason = "no_path"
        failed_cb(receipt)
        snap = _dc.get_singleton().snapshot()
        assert snap["drop_reasons"]["rns_delivery_failed"] == 1

    def test_callback_helper_tolerates_old_lxmf_without_register_api(
        self, bridge
    ):
        """If LXMF.LXMessage lacks register_delivery_callback the helper
        logs and returns without crashing. The send path still completes."""
        import sys
        fake_rns, fake_lxmf, fake_lxm = self._fake_rns_lxmf_modules()
        fake_lxm.register_delivery_callback.side_effect = AttributeError(
            "old LXMF"
        )
        self._prime_bridge_for_send(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            result = bridge.send_to_rns("hello", b"\xab" * 16)
        assert bool(result) is True


# ---------------------------------------------------------------------------
# Issue #66: application-layer ack synthesis on the bridge
# ---------------------------------------------------------------------------

class TestAckSynthesisIssue66:
    """
    _format_ack_text / _emit_ack_to_origin / _maybe_emit_ack_for_msgid /
    _sweep_overdue_acks — the rns_bridge-side wiring that turns LXMF
    delivery proofs (and timeouts) into synthetic ACK CanonicalMessages
    routed back to the origin sender.
    """

    def test_format_ack_text_delivered(self, bridge):
        text = bridge._format_ack_text("abcdef0123456789", "delivered")
        assert text == "[delivered: abcdef01]"

    def test_format_ack_text_failed(self, bridge):
        assert bridge._format_ack_text("abcdef01", "failed") == "[failed: abcdef01]"

    def test_format_ack_text_timeout(self, bridge):
        assert bridge._format_ack_text("abcdef01", "timeout") == "[timeout: abcdef01]"

    def test_format_ack_text_unknown_kind(self, bridge):
        """Unknown kinds still get a parseable form, not an exception."""
        assert bridge._format_ack_text("abcdef01", "weird") == "[weird: abcdef01]"

    def test_emit_ack_to_origin_meshtastic_dispatches_to_handler(self, bridge):
        """meshtastic origin → mesh handler's send_text gets the textual ACK."""
        mock_handler = MagicMock()
        mock_handler.send_text.return_value = True
        bridge._mesh_handler = mock_handler

        ok = bridge._emit_ack_to_origin(
            "abcdef01234567", "meshtastic", "!aabbccdd", "delivered",
        )
        assert ok is True
        mock_handler.send_text.assert_called_once_with(
            "[delivered: abcdef01]", destination="!aabbccdd", channel=0,
        )

    def test_emit_ack_to_origin_meshcore_dispatches_to_handler(self, bridge):
        """meshcore origin → meshcore handler's send_text gets the textual ACK."""
        mock_handler = MagicMock()
        mock_handler.send_text.return_value = True
        bridge._meshcore_handler = mock_handler

        ok = bridge._emit_ack_to_origin(
            "abcdef01", "meshcore", "publickey-prefix", "failed",
        )
        assert ok is True
        mock_handler.send_text.assert_called_once_with(
            "[failed: abcdef01]", destination="publickey-prefix",
        )

    def test_emit_ack_to_origin_rns_converts_hex_to_bytes(self, bridge):
        """
        rns origin → send_to_rns receives the destination_hash as bytes.

        Why pinned: ack synthesis stores origin_address as the hex string
        of the LXMF destination hash; the dispatch site must convert it
        back, not pass the string through (which would silently fail on
        an older RNS that doesn't accept hex).
        """
        with patch.object(bridge, 'send_to_rns', return_value=True) as ms:
            ok = bridge._emit_ack_to_origin(
                "abcdef01", "rns", "deadbeef00112233", "delivered",
            )
        assert ok is True
        ms.assert_called_once()
        args, kwargs = ms.call_args
        # send_to_rns(text, destination_hash=<bytes>)
        assert args[0] == "[delivered: abcdef01]"
        assert kwargs['destination_hash'] == bytes.fromhex("deadbeef00112233")

    def test_emit_ack_to_origin_rns_bad_hex_returns_false(self, bridge):
        """Bad hex doesn't crash the callback; logs a warning, returns False."""
        with patch.object(bridge, 'send_to_rns', return_value=True) as ms:
            ok = bridge._emit_ack_to_origin(
                "abcdef01", "rns", "not-hex-zzz", "delivered",
            )
        assert ok is False
        ms.assert_not_called()

    def test_emit_ack_to_origin_unknown_network_returns_false(self, bridge):
        assert bridge._emit_ack_to_origin(
            "abcdef01", "carrier-pigeon", "addr", "delivered",
        ) is False

    def test_emit_ack_to_origin_no_meshtastic_handler_returns_false(self, bridge):
        """If the origin handler is absent (handler optional), don't crash."""
        bridge._mesh_handler = None
        assert bridge._emit_ack_to_origin(
            "abcdef01", "meshtastic", "!aabbccdd", "delivered",
        ) is False

    def test_emit_ack_to_origin_handler_exception_returns_false(self, bridge):
        """Handler raises → don't bubble; ack synthesis is best-effort."""
        mock_handler = MagicMock()
        mock_handler.send_text.side_effect = RuntimeError("radio offline")
        bridge._mesh_handler = mock_handler
        assert bridge._emit_ack_to_origin(
            "abcdef01", "meshtastic", "!aabbccdd", "delivered",
        ) is False

    def test_maybe_emit_ack_no_queue_returns_false(self, bridge):
        """When persistent queue is absent, synthesis is a no-op."""
        bridge._persistent_queue = None
        assert bridge._maybe_emit_ack_for_msgid("abc", "delivered") is False

    def test_maybe_emit_ack_no_pending_record_returns_false(self, bridge):
        """When mark_acked returns None (not tracked), no synthesis."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.return_value = None
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is False
        me.assert_not_called()

    def test_maybe_emit_ack_routes_to_origin(self, bridge):
        """mark_acked returns origin → _emit_ack_to_origin called with it."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.return_value = {
            'message_id': 'abc',
            'origin_network': 'meshcore',
            'origin_address': 'pubkey-abc',
        }
        with patch.object(bridge, '_emit_ack_to_origin', return_value=True) as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is True
        me.assert_called_once_with(
            "abc",
            origin_network='meshcore',
            origin_address='pubkey-abc',
            kind='delivered',
        )

    def test_maybe_emit_ack_mark_acked_exception_does_not_raise(self, bridge):
        """A queue exception is logged + swallowed; callback can't bubble."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.mark_acked.side_effect = RuntimeError("db gone")
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            ok = bridge._maybe_emit_ack_for_msgid("abc", "delivered")
        assert ok is False
        me.assert_not_called()

    def test_sweep_overdue_emits_timeout_and_marks(self, bridge):
        """
        For each overdue record, the sweep emits a TIMEOUT ACK + calls
        mark_timeout to finalise. Counts emitted records.
        """
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.return_value = [
            {
                'message_id': 'aaa',
                'origin_network': 'meshtastic',
                'origin_address': '!aa',
                'timeout_at': '2026-05-18T10:00:00',
            },
            {
                'message_id': 'bbb',
                'origin_network': 'meshcore',
                'origin_address': 'bb',
                'timeout_at': '2026-05-18T10:00:01',
            },
        ]
        bridge._persistent_queue.mark_timeout.return_value = True
        with patch.object(bridge, '_emit_ack_to_origin', return_value=True) as me:
            count = bridge._sweep_overdue_acks()
        assert count == 2
        assert me.call_count == 2
        # Both emissions are kind='timeout'
        for call in me.call_args_list:
            assert call.kwargs['kind'] == 'timeout'
        # Both mark_timeout calls happened
        assert bridge._persistent_queue.mark_timeout.call_count == 2

    def test_sweep_skips_when_mark_timeout_loses_race(self, bridge):
        """
        If mark_timeout returns False (ack arrived between read+write),
        the sweep does NOT emit a TIMEOUT ACK — the delivered ACK
        already went out from the LXMF callback path.
        """
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.return_value = [{
            'message_id': 'aaa',
            'origin_network': 'meshtastic',
            'origin_address': '!aa',
            'timeout_at': '2026-05-18T10:00:00',
        }]
        bridge._persistent_queue.mark_timeout.return_value = False  # raced
        with patch.object(bridge, '_emit_ack_to_origin') as me:
            count = bridge._sweep_overdue_acks()
        assert count == 0
        me.assert_not_called()

    def test_sweep_no_queue_returns_zero(self, bridge):
        bridge._persistent_queue = None
        assert bridge._sweep_overdue_acks() == 0

    def test_sweep_find_overdue_exception_returns_zero(self, bridge):
        """Sweep robust to queue errors — never raises out."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.find_overdue_acks.side_effect = RuntimeError("db")
        assert bridge._sweep_overdue_acks() == 0

    def test_enqueue_message_with_ack_calls_register_pending_ack(self, bridge):
        """
        enqueue_message(ack_required=True, origin_network/_address set)
        registers a pending-ack record on the queue. Existing call sites
        that don't pass the new params get default behavior (no record).
        """
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-1"

        msg_id = bridge.enqueue_message(
            "weather pls",
            destination="!aabbccdd",
            dest_type="meshtastic",
            ack_required=True,
            ack_origin_network="meshcore",
            ack_origin_address="pubkey-abc",
        )
        assert msg_id == "msg-id-1"
        bridge._persistent_queue.register_pending_ack.assert_called_once_with(
            "msg-id-1",
            origin_network="meshcore",
            origin_address="pubkey-abc",
            timeout_seconds=300,
        )

    def test_enqueue_message_without_ack_does_not_register(self, bridge):
        """ack_required=False (default) leaves register_pending_ack untouched."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-2"

        bridge.enqueue_message(
            "ordinary message",
            destination="!aabbccdd",
            dest_type="meshtastic",
        )
        bridge._persistent_queue.register_pending_ack.assert_not_called()

    def test_enqueue_message_ack_required_without_origin_skips(self, bridge):
        """
        ack_required=True but origin fields missing → no register. Silent
        skip is the contract: caller forgot one of (network, address),
        no surprise side effects.

        Why pinned: a future refactor that swaps origin fields for a
        single dict needs to keep the "must have both" guard.
        """
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-3"

        bridge.enqueue_message(
            "weather pls", destination="!aabbccdd",
            dest_type="meshtastic", ack_required=True,
            # ack_origin_network omitted
            ack_origin_address="some-addr",
        )
        bridge._persistent_queue.register_pending_ack.assert_not_called()

    def test_enqueue_message_register_pending_ack_exception_does_not_raise(
        self, bridge,
    ):
        """A queue failure during register is logged, not raised — the
        message itself was already enqueued and shouldn't be lost."""
        bridge._persistent_queue = MagicMock()
        bridge._persistent_queue.enqueue.return_value = "msg-id-4"
        bridge._persistent_queue.register_pending_ack.side_effect = (
            RuntimeError("db")
        )

        msg_id = bridge.enqueue_message(
            "weather pls", destination="!aabbccdd",
            dest_type="meshtastic", ack_required=True,
            ack_origin_network="meshcore", ack_origin_address="abc",
        )
        # msg_id still returned — the message was enqueued successfully.
        assert msg_id == "msg-id-4"


class TestReplyRoutingMeshToRNS:
    """Theme-A step 1 — M→R emit (always) + reply-context record (gated)."""

    DEST = "6b1a0120941444587d7d1dc1bf6d64d7"
    DEST2 = "7c2b1231a52555698e8e2ed2c07e75e8"

    def _msg(self, content="hello", source_id="!aabb0042"):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="meshtastic", source_id=source_id,
            destination_id=None, content=content,
        )

    def test_reply_to_field_emitted_even_when_flag_off(self, bridge):
        """meshforge_reply_to is pure metadata — emitted regardless of gate."""
        bridge.config.rns.reply_routing_enabled = False
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        captured = {}

        def capture_send(content, dest_hash=None, title=None, fields=None):
            captured["fields"] = fields
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(self._msg())

        assert captured["fields"]["meshforge_reply_to"] == "meshtastic:!aabb0042"

    def test_records_context_when_enabled(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg())

        assert bridge._reply_context.get(self.DEST) == "meshtastic:!aabb0042"

    def test_no_record_when_flag_off(self, bridge):
        bridge.config.rns.reply_routing_enabled = False
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg())

        assert len(bridge._reply_context) == 0

    def test_magicmock_flag_reads_as_off(self, bridge):
        """Guard #3: a truthy-but-not-True flag (MagicMock / bad JSON) is OFF.

        The fixture's config.rns is a MagicMock, so reply_routing_enabled
        is a truthy auto-attribute — the strict `is True` read must treat
        it as disabled so the whole legacy suite keeps flag-off behavior.
        """
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg())

        assert len(bridge._reply_context) == 0

    def test_broadcast_fanout_records_each_peer(self, bridge):
        """The NomadNet case: fan-out to N inboxes remembers each thread."""
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [
            self.DEST, self.DEST2,
        ]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg())

        assert bridge._reply_context.get(self.DEST) == "meshtastic:!aabb0042"
        assert bridge._reply_context.get(self.DEST2) == "meshtastic:!aabb0042"

    def test_peer_gateway_destination_not_recorded(self, bridge):
        """Infrastructure threads never reply — don't remember them."""
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [
            self.DEST, self.DEST2,
        ]
        bridge.config.rns.get_peer_gateway_destinations.return_value = [self.DEST2]

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg())

        assert bridge._reply_context.get(self.DEST) == "meshtastic:!aabb0042"
        assert bridge._reply_context.get(self.DEST2) is None

    def test_already_bridged_content_not_recorded(self, bridge):
        """[RNS:-tagged content is a re-bridge, not a fresh mesh thread."""
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._msg(content="[RNS:abcd] echo"))

        assert len(bridge._reply_context) == 0

    def test_ack_synthetic_not_recorded(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(
                self._msg(content="[delivered: abcd1234]"))

        assert len(bridge._reply_context) == 0

    def test_failed_send_not_recorded(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        with patch.object(bridge, 'send_to_rns', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_mesh_to_rns(self._msg())

        assert len(bridge._reply_context) == 0


class TestReplyRoutingRNSToMesh:
    """Theme-A step 1 — R→M honor precedence:
    explicit @addr > echoed meshforge_reply_to field > memory > broadcast.
    """

    PEER = "abcd0123abcd0123abcd0123abcd0123"

    def _msg(self, content="my reply", fields=None, source_id=None):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns", source_id=source_id or self.PEER,
            destination_id=None, content=content,
            metadata={"lxmf_fields": fields} if fields else {},
        )

    def _capture(self, bridge, msg):
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)
        return captured

    def test_explicit_addr_wins_over_field_and_memory(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")
        msg = self._msg(
            content="@!11112222 hello",
            fields={"meshforge_reply_to": "meshtastic:!33334444"},
        )

        captured = self._capture(bridge, msg)

        assert captured["destination"] == "!11112222"
        assert "@!11112222" not in captured["content"]

    def test_field_honored_when_no_explicit_addr(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        msg = self._msg(fields={"meshforge_reply_to": "meshtastic:!33334444"})

        captured = self._capture(bridge, msg)

        assert captured["destination"] == "!33334444"
        assert bridge.stats['reply_routed_from_field'] == 1
        assert bridge.stats['reply_routed_from_memory'] == 0

    def test_memory_honored_when_no_field(self, bridge):
        """The stock-NomadNet case: no @addr, no echoed fields — the
        reply-context memory threads the reply back."""
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] == "!55556666"
        assert bridge.stats['reply_routed_from_memory'] == 1

    def test_field_wins_over_memory(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")
        msg = self._msg(fields={"meshforge_reply_to": "meshtastic:!33334444"})

        captured = self._capture(bridge, msg)

        assert captured["destination"] == "!33334444"

    def test_broadcast_fallback_when_nothing_resolves(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None

    def test_flag_off_ignores_field_and_memory(self, bridge):
        """Flag-off keeps legacy broadcast behavior byte-for-byte."""
        bridge.config.rns.reply_routing_enabled = False
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")
        msg = self._msg(fields={"meshforge_reply_to": "meshtastic:!33334444"})

        captured = self._capture(bridge, msg)

        assert captured["destination"] is None
        assert captured["content"].startswith("[RNS:abcd] ")
        assert bridge.stats['reply_routed_from_field'] == 0
        assert bridge.stats['reply_routed_from_memory'] == 0

    def test_bridge_origin_fanout_not_honored(self, bridge):
        """Guard #1: a PEER gateway's M→R fan-out carries meshforge_*
        markers AND a reply_to token — it is a broadcast to re-broadcast,
        never a reply to auto-direct."""
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        msg = self._msg(fields={
            "meshforge_source_network": "meshtastic",
            "meshforge_from_id": "!a2e95ba4",
            "meshforge_from_short": "BORG",
            "meshforge_reply_to": "meshtastic:!a2e95ba4",
        })

        captured = self._capture(bridge, msg)

        assert captured["destination"] is None

    def test_relayed_copy_not_honored(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")
        msg = self._msg(fields={
            "meshforge_relayed_by": "beef0000beef0000beef0000beef0000",
        })

        captured = self._capture(bridge, msg)

        assert captured["destination"] is None

    def test_peer_gateway_source_not_honored(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = [self.PEER]
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None

    def test_synthetic_ack_not_honored(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")

        captured = self._capture(
            bridge, self._msg(content="[delivered: abcd1234]"))

        assert captured["destination"] is None

    def test_unresolvable_field_falls_back_to_memory(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")
        # rns-protocol token isn't honorable on the mesh leg this step.
        msg = self._msg(fields={"meshforge_reply_to": "rns:" + "ee" * 16})

        captured = self._capture(bridge, msg)

        assert captured["destination"] == "!55556666"
        assert bridge.stats['reply_routed_from_memory'] == 1

    def test_unresolvable_everything_broadcasts(self, bridge):
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge.node_tracker.get_node_by_short_name.return_value = None
        msg = self._msg(fields={"meshforge_reply_to": "meshtastic:not-a-node"})

        captured = self._capture(bridge, msg)

        assert captured["destination"] is None


class TestReplyRoutingEndToEnd:
    """Theme-A step 1 happy path on one bridge instance: M→R records the
    thread, then a bare reply (no @addr, no fields — stock NomadNet) from
    that peer auto-directs back to the original mesh sender."""

    PEER = "6b1a0120941444587d7d1dc1bf6d64d7"

    def test_round_trip_reply_threads_back(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_lxmf_destinations.return_value = [self.PEER]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        outbound = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="anyone copy?",
        )
        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(outbound)

        reply = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="copy loud and clear",
        )
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(reply)

        assert captured["destination"] == "!aabb0042"
        assert "copy loud and clear" in captured["content"]
        assert bridge.stats['reply_routed_from_memory'] == 1


class TestCorrelationRestartReplyRouting:
    """Thread-2 — the durable reply rung. After a bridge restart the
    in-memory ReplyContextStore is empty, but the durable correlation
    store still resolves the reply back to the originating mesh node —
    the property the in-memory cache loses (the reason this slice exists)."""

    PEER = "abcd0123abcd0123abcd0123abcd0123"
    NODE = "!aabb0042"

    def _msg(self, content="my reply", fields=None):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content=content,
            metadata={"lxmf_fields": fields} if fields else {},
        )

    def _capture(self, bridge, msg):
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)
        return captured

    def _enable(self, bridge, tmp_path):
        from gateway.correlation_store import BridgeCorrelationStore
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._correlation = BridgeCorrelationStore(
            db_path=str(tmp_path / "corr.db"))
        return bridge._correlation

    def test_durable_rung_routes_after_restart(self, bridge, tmp_path):
        corr = self._enable(bridge, tmp_path)
        # M→R happened pre-restart: the durable store holds the mapping.
        corr.record(self.NODE, self.PEER, direction="m2r")
        # Post-restart shape: the in-memory cache is empty.
        bridge._reply_context.clear()

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] == self.NODE
        assert bridge.stats['reply_routed_from_memory_db'] == 1
        assert bridge.stats.get('reply_routed_from_memory', 0) == 0
        # The DB hit warmed the in-process cache for next time.
        assert bridge._reply_context.get(self.PEER) == "meshtastic:" + self.NODE

    def test_cache_hit_skips_db(self, bridge, tmp_path):
        corr = self._enable(bridge, tmp_path)
        corr.record(self.NODE, self.PEER, direction="m2r")
        # Cache already warm — the fast path wins, route is 'memory'.
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] == "!55556666"
        assert bridge.stats['reply_routed_from_memory'] == 1
        assert bridge.stats.get('reply_routed_from_memory_db', 0) == 0

    def test_flag_off_never_touches_durable_store(self, bridge, tmp_path):
        corr = self._enable(bridge, tmp_path)
        corr.record(self.NODE, self.PEER, direction="m2r")
        bridge.config.rns.reply_routing_enabled = False  # flag off
        bridge._reply_context.clear()

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None  # legacy broadcast
        assert bridge.stats.get('reply_routed_from_memory_db', 0) == 0

    def test_directed_downlink_writes_r2m_observability_row(self, bridge, tmp_path):
        corr = self._enable(bridge, tmp_path)
        # An explicit @addr downlink resolves to a node; the r2m row is
        # recorded for observability (NOT read for routing this slice).
        captured = self._capture(bridge, self._msg(content="@!11112222 hi"))

        assert captured["destination"] == "!11112222"
        r2m = [r for r in corr.list_recent() if r["direction"] == "r2m"]
        assert len(r2m) == 1
        assert r2m[0]["mesh_node"] == "!11112222"
        assert r2m[0]["rns_peer_hash"] == self.PEER


class TestIdentityReplyContactRung:
    """Theme-A step 2 — contact rung in the R→M reply chain.
    Precedence: @addr > field > memory > contact > broadcast."""

    PEER = "abcd0123abcd0123abcd0123abcd0123"

    def _enable(self, bridge, tmp_path):
        from gateway.identity_binding import IdentityBinder
        bridge.config.rns.reply_routing_enabled = True
        bridge.config.rns.cross_protocol_identity_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._identity_binder = IdentityBinder(
            db_path=str(tmp_path / "contacts.db"), throttle_sec=0.0001)
        return bridge._identity_binder

    def _msg(self, content="my reply", fields=None):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content=content,
            metadata={"lxmf_fields": fields} if fields else {},
        )

    def _capture(self, bridge, msg):
        captured = {}

        def capture_send(content, destination=None, channel=0):
            captured["content"] = content
            captured["destination"] = destination
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)
        return captured

    def test_contact_rung_resolves_reply(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "rns", self.PEER)
        binder.link("Alice", "meshtastic", "!aabb0042")

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] == "!aabb0042"
        assert bridge.stats['reply_routed_from_contact'] == 1
        assert "(reply:contact)" not in captured["content"]  # tag is log-only

    def test_memory_beats_contact(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "rns", self.PEER)
        binder.link("Alice", "meshtastic", "!aabb0042")
        bridge._reply_context.record(self.PEER, "meshtastic:!55556666")

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] == "!55556666"
        assert bridge.stats['reply_routed_from_memory'] == 1
        assert bridge.stats['reply_routed_from_contact'] == 0

    def test_explicit_addr_beats_contact(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "rns", self.PEER)
        binder.link("Alice", "meshtastic", "!aabb0042")

        captured = self._capture(bridge, self._msg(content="@!11112222 hi"))

        assert captured["destination"] == "!11112222"
        assert bridge.stats['reply_routed_from_contact'] == 0

    def test_contact_rung_needs_identity_flag(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "rns", self.PEER)
        binder.link("Alice", "meshtastic", "!aabb0042")
        bridge.config.rns.cross_protocol_identity_enabled = False

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None  # broadcast
        assert bridge.stats['reply_routed_from_contact'] == 0

    def test_contact_rung_needs_reply_routing_flag(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "rns", self.PEER)
        binder.link("Alice", "meshtastic", "!aabb0042")
        bridge.config.rns.reply_routing_enabled = False

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None

    def test_no_contact_falls_back_to_broadcast(self, bridge, tmp_path):
        self._enable(bridge, tmp_path)

        captured = self._capture(bridge, self._msg())

        assert captured["destination"] is None


class TestIdentityM2RFallback:
    """Theme-A step 2 — _get_rns_destination contact fallback."""

    RNS_HASH = "ee" * 16

    def _enable(self, bridge, tmp_path):
        from gateway.identity_binding import IdentityBinder
        bridge.config.rns.cross_protocol_identity_enabled = True
        bridge._identity_binder = IdentityBinder(
            db_path=str(tmp_path / "contacts.db"), throttle_sec=0.0001)
        return bridge._identity_binder

    def test_tracker_miss_binder_hit(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "meshtastic", "!aabb0042")
        binder.link("Alice", "rns", self.RNS_HASH)
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        dest = bridge._get_rns_destination("!aabb0042")

        assert dest == bytes.fromhex(self.RNS_HASH)
        assert bridge.stats['identity_resolved_m2r'] == 1

    def test_tracker_hit_wins_no_binder_touch(self, bridge, tmp_path):
        binder = self._enable(bridge, tmp_path)
        node = MagicMock()
        node.rns_hash = bytes.fromhex("dd" * 16)
        bridge.node_tracker.get_node_by_mesh_id.return_value = node

        dest = bridge._get_rns_destination("!aabb0042")

        assert dest == bytes.fromhex("dd" * 16)
        assert binder._table is None  # binder never opened
        assert bridge.stats['identity_resolved_m2r'] == 0

    def test_no_mapping_returns_none(self, bridge, tmp_path):
        self._enable(bridge, tmp_path)
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        assert bridge._get_rns_destination("!aabb0042") is None
        assert bridge.stats['identity_resolved_m2r'] == 0

    def test_flag_off_no_binder_touch(self, bridge):
        bridge.node_tracker.get_node_by_mesh_id.return_value = None

        assert bridge._get_rns_destination("!aabb0042") is None
        assert bridge._identity_binder._table is None

    def test_binder_survives_rns_identity_assignment(self, bridge, tmp_path):
        """Regression — caught LIVE by the 2026-06-03 radio smoke: the RNS
        connection mixin overwrites self._identity with the RNS Identity
        object on connect ('Identity' object has no attribute 'populate').
        The binder must live on its own attribute (_identity_binder) so a
        real RNS connect can never stomp it. Tests alone missed this
        because the fixture never connects RNS."""
        binder = self._enable(bridge, tmp_path)
        binder.link("Alice", "meshtastic", "!aabb0042")
        binder.link("Alice", "rns", self.RNS_HASH)
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        # What the connection mixin does on RNS connect:
        bridge._identity = MagicMock(spec=[])  # no populate/resolve attrs

        dest = bridge._get_rns_destination("!aabb0042")

        assert dest == bytes.fromhex(self.RNS_HASH)


class TestIdentityPopulation:
    """Theme-A step 2 — population gating + exclusion guards."""

    PEER = "abcd0123abcd0123abcd0123abcd0123"
    DEST = "6b1a0120941444587d7d1dc1bf6d64d7"

    def _enable(self, bridge, tmp_path):
        from gateway.identity_binding import IdentityBinder
        bridge.config.rns.cross_protocol_identity_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._identity_binder = IdentityBinder(
            db_path=str(tmp_path / "contacts.db"), throttle_sec=0.0001)
        return bridge._identity_binder

    def test_m2r_populates_mesh_sender(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        binder = self._enable(bridge, tmp_path)
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        mock_node = MagicMock()
        mock_node.name = "Alice"
        mock_node.short_name = "ALCE"
        bridge.node_tracker.get_node_by_mesh_id.return_value = mock_node
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        contact = binder.show("meshtastic", "!aabb0042")
        assert contact is not None
        assert contact.display_name == "Alice"

    def test_r2m_populates_rns_sender(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        binder = self._enable(bridge, tmp_path)
        bridge.node_tracker.get_node_by_rns_hash.return_value = None
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="hello from nomadnet",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert binder.show("rns", self.PEER) is not None

    def test_r2m_peer_gateway_not_populated(self, bridge, tmp_path):
        """D4 guard: infrastructure never becomes a contact."""
        from gateway.rns_bridge import BridgedMessage
        binder = self._enable(bridge, tmp_path)
        bridge.config.rns.get_peer_gateway_destinations.return_value = [self.PEER]
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="relayed traffic",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert binder._table is None  # populate never ran

    def test_r2m_bridge_origin_copy_not_populated(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        binder = self._enable(bridge, tmp_path)
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="fan-out copy",
            metadata={"lxmf_fields": {
                "meshforge_source_network": "meshtastic",
                "meshforge_from_id": "!a2e95ba4",
            }},
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert binder._table is None

    def test_m2r_already_bridged_content_not_populated(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        binder = self._enable(bridge, tmp_path)
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="[RNS:abcd] echo",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert binder._table is None


class TestIdentityFlagOffInertness:
    """With the fixture's MagicMock config (flag truthy-but-not-True), every
    identity leg is inert and NO DB is ever opened — the no-DB proof."""

    def test_both_paths_leave_binder_unopened(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        m2r = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello",
        )
        r2m = BridgedMessage(
            source_network="rns", source_id="ab" * 16,
            destination_id=None, content="reply",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(m2r)
        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(r2m)

        assert bridge._identity_binder._table is None
        assert bridge.stats['reply_routed_from_contact'] == 0
        assert bridge.stats['identity_resolved_m2r'] == 0


class TestSessionDmToGateway:
    """Theme-A step 3 — a Meshtastic DM to the gateway's own node routes
    privately via the sender's active session; no-session falls back to
    today's broadcast fan-out."""

    GATEWAY_ID = "!ebfa0001"
    PEER = "ab" * 16
    DEST = "6b1a0120941444587d7d1dc1bf6d64d7"

    def _enable(self, bridge, tmp_path):
        from gateway.session_store import SessionStore
        bridge.config.rns.sessions_enabled = True
        bridge.config.meshtastic.gateway_node_id = self.GATEWAY_ID
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        bridge._sessions = SessionStore(db_path=str(tmp_path / "s.db"))
        return bridge._sessions

    def _dm(self, content="my private reply", source="!aabb0042"):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="meshtastic", source_id=source,
            destination_id=self.GATEWAY_ID, content=content,
            is_broadcast=False,
        )

    def test_session_routed_single_send(self, bridge, tmp_path):
        sessions = self._enable(bridge, tmp_path)
        sessions.touch("!aabb0042", self.PEER)
        sends = []

        def capture(content, dest_hash=None, title=None, fields=None):
            sends.append((content, dest_hash, title, fields))
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture):
            bridge._process_mesh_to_rns(self._dm())

        assert len(sends) == 1  # single private send — NO fan-out
        content, dest_hash, title, fields = sends[0]
        assert dest_hash == bytes.fromhex(self.PEER)
        assert content == "my private reply"
        assert fields["meshforge_reply_to"] == "meshtastic:!aabb0042"
        assert "!aabb0042" in title
        assert bridge.stats['session_routed_m2r'] == 1
        assert bridge.stats['mesh_to_rns_delivered'] == 1

    def test_session_send_refreshes_session_and_memory(self, bridge, tmp_path):
        sessions = self._enable(bridge, tmp_path)
        bridge.config.rns.reply_routing_enabled = True
        sessions.touch("!aabb0042", self.PEER)

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(self._dm())

        rows = sessions.list_active()
        assert rows[0]["message_count"] == 2  # seeded + refreshed
        # step-1 memory kept warm for the peer's next reply
        assert bridge._reply_context.get(self.PEER) == "meshtastic:!aabb0042"

    def test_no_session_falls_back_to_broadcast(self, bridge, tmp_path):
        self._enable(bridge, tmp_path)
        sends = []

        with patch.object(bridge, 'send_to_rns',
                          side_effect=lambda *a, **k: sends.append(a) or True):
            bridge._process_mesh_to_rns(self._dm())

        assert bridge.stats['session_dm_no_session'] == 1
        assert len(sends) == 1  # the fan-out list (one configured dest)

    def test_send_failure_falls_back_to_fanout(self, bridge, tmp_path):
        sessions = self._enable(bridge, tmp_path)
        sessions.touch("!aabb0042", self.PEER)
        calls = {"n": 0}

        def first_fails(content, dest_hash=None, title=None, fields=None):
            calls["n"] += 1
            return calls["n"] > 1  # session send fails, fan-out succeeds

        with patch.object(bridge, 'send_to_rns', side_effect=first_fails):
            bridge._process_mesh_to_rns(self._dm())

        assert calls["n"] == 2  # session attempt + fan-out
        assert bridge.stats['session_routed_m2r'] == 0
        assert bridge.stats['mesh_to_rns_delivered'] == 1

    def test_flag_off_unchanged_and_db_never_opened(self, bridge):
        # Fixture MagicMock flag (truthy-not-True) → sessions OFF.
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [self.DEST]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=self.GATEWAY_ID, content="dm", is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge._sessions._initialized is False
        assert bridge.stats['session_routed_m2r'] == 0
        assert bridge.stats['session_dm_no_session'] == 0

    def test_own_id_unset_unchanged(self, bridge, tmp_path):
        sessions = self._enable(bridge, tmp_path)
        bridge.config.meshtastic.gateway_node_id = ""
        sessions.touch("!aabb0042", self.PEER)
        sends = []

        with patch.object(bridge, 'send_to_rns',
                          side_effect=lambda *a, **k: sends.append(a) or True):
            bridge._process_mesh_to_rns(self._dm())

        assert bridge.stats['session_routed_m2r'] == 0
        assert len(sends) == 1  # plain fan-out

    def test_synthetic_content_excluded(self, bridge, tmp_path):
        sessions = self._enable(bridge, tmp_path)
        sessions.touch("!aabb0042", self.PEER)

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(
                self._dm(content="[delivered: abcd1234]"))

        assert bridge.stats['session_routed_m2r'] == 0

    def test_dm_to_other_node_not_session_routed(self, bridge, tmp_path):
        """A DM to a different mesh node is NOT the gateway channel."""
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        sessions.touch("!aabb0042", self.PEER)
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!00001111", content="dm to a peer node",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['session_routed_m2r'] == 0


class TestSessionTouchPoints:
    """Theme-A step 3 — where sessions open/refresh."""

    PEER = "ab" * 16

    def _enable(self, bridge, tmp_path):
        from gateway.session_store import SessionStore
        bridge.config.rns.sessions_enabled = True
        bridge.config.rns.get_peer_gateway_destinations.return_value = []
        bridge._sessions = SessionStore(db_path=str(tmp_path / "s.db"))
        return bridge._sessions

    def test_r2m_directed_delivery_touches(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="@!aabb0042 hello there",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert sessions.lookup("!aabb0042") == self.PEER

    def test_addr_rung_peer_gateway_excluded(self, bridge, tmp_path):
        """The @addr rung resolves OUTSIDE the step-1 gated block — the
        session touch carries its own exclusion for peer-gateway sources."""
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        bridge.config.rns.get_peer_gateway_destinations.return_value = [self.PEER]
        msg = BridgedMessage(
            source_network="rns", source_id=self.PEER,
            destination_id=None, content="@!aabb0042 relayed",
        )

        with patch.object(bridge, 'send_to_meshtastic', return_value=True):
            bridge._process_rns_to_mesh(msg)

        assert sessions._initialized is False  # never touched

    def test_m2r_directed_dm_touches(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        node = MagicMock()
        node.rns_hash = bytes.fromhex("cd" * 16)
        bridge.node_tracker.get_node_by_mesh_id.return_value = node
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!00001111", content="direct to rns-mapped node",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert sessions.lookup("!aabb0042") == "cd" * 16

    def test_failed_send_no_touch(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        node = MagicMock()
        node.rns_hash = bytes.fromhex("cd" * 16)
        bridge.node_tracker.get_node_by_mesh_id.return_value = node
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id="!00001111", content="will fail",
            is_broadcast=False,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False), \
             patch.object(bridge, '_requeue_failed_message', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert sessions.lookup("!aabb0042") is None

    def test_broadcast_fanout_never_touches(self, bridge, tmp_path):
        from gateway.rns_bridge import BridgedMessage
        sessions = self._enable(bridge, tmp_path)
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="channel broadcast",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert sessions._initialized is False


class TestSessionSweep:
    """Theme-A step 3 — the ~30s maintenance sweep."""

    def test_expired_sessions_pruned(self, bridge, tmp_path, monkeypatch):
        from gateway.session_store import SessionStore
        import gateway.session_store as ss
        clock = [1000.0]
        monkeypatch.setattr(ss.time, "time", lambda: clock[0])
        bridge.config.rns.sessions_enabled = True
        bridge._sessions = SessionStore(
            db_path=str(tmp_path / "s.db"), idle_timeout_sec=60)
        bridge._sessions.touch("!aabb0042", "ab" * 16)
        clock[0] = 2000.0  # way past TTL

        assert bridge._sweep_expired_sessions() == 1
        assert bridge._sessions.active_count() == 0

    def test_sweep_gated_off_noop(self, bridge):
        # MagicMock flag → off; sweep must not open the DB.
        assert bridge._sweep_expired_sessions() == 0
        assert bridge._sessions._initialized is False

    def test_get_status_gated(self, bridge):
        # Flag off: active_sessions reported 0 without opening the DB.
        status = bridge.get_status()
        assert status['active_sessions'] == 0
        assert bridge._sessions._initialized is False


# ---------------------------------------------------------------------------
# Dual-path dedup (2026-06-04) — RecentRfTxRegistry + R→M suppression
# ---------------------------------------------------------------------------
# Two designed paths put the SAME content on the primary radio: the local
# mesh_bridge forward (true-origin downlink / tagged toradio) and a peer
# gateway's RNS relay landing back here as an [RNS:] toradio broadcast.
# Unconditional suppression of the relay loses messages (live trace: ~40% of
# relayed events arrived ONLY via RNS) — so the relay copy is suppressed
# ONLY on a registry hit, gated by rns.dual_path_dedup_enabled (default off).

class TestRecentRfTxRegistry:
    """Unit tests for the content-normalized recently-transmitted registry."""

    def _registry(self, **kw):
        from gateway.base_handler import RecentRfTxRegistry
        return RecentRfTxRegistry(**kw)

    def test_register_then_seen(self):
        r = self._registry()
        r.register("hello mesh")
        assert r.seen_within("hello mesh", 60.0) is True

    def test_unregistered_not_seen(self):
        r = self._registry()
        assert r.seen_within("never sent", 60.0) is False

    def test_window_expiry(self):
        r = self._registry()
        r.register("old content")
        key = next(iter(r._entries))
        r._entries[key] -= 61.0          # simulate 61s passing
        assert r.seen_within("old content", 60.0) is False

    def test_leading_bridge_tag_normalized(self):
        # A tagged copy matches the raw content a downlink injected.
        r = self._registry()
        r.register("Bot CMD?:ping, bbshelp")
        assert r.seen_within("[RNS:Borg serve] Bot CMD?:ping, bbshelp", 60.0)
        assert r.seen_within("[Mesh:SHORT_TURBO] Bot CMD?:ping, bbshelp", 60.0)

    def test_nested_tags_stripped(self):
        r = self._registry()
        r.register("[Mesh:LONG_FAST] [RNS:abcd] payload")
        assert r.seen_within("payload", 60.0)

    def test_whitespace_collapsed(self):
        r = self._registry()
        r.register("two  words   here")
        assert r.seen_within("two words here", 60.0)

    def test_empty_and_tag_only_never_match(self):
        r = self._registry()
        r.register("")
        r.register("[RNS:xxxx] ")
        assert r.seen_within("", 60.0) is False
        assert r.seen_within("[Mesh:ST] ", 60.0) is False

    def test_bounded_entries(self):
        r = self._registry(max_entries=3)
        for i in range(6):
            r.register(f"content {i}")
        assert len(r._entries) <= 3
        assert r.seen_within("content 5", 60.0)   # newest kept

    def test_module_singleton_accessor(self):
        import gateway.base_handler as bh
        assert bh.get_rf_tx_registry() is bh._rf_tx_registry


class TestContentIdClaim:
    """Transport-truth arc Phase 1: the content_id-keyed per-segment claim —
    a deterministic, race-free dedup / loop-guard substrate. Unlike the
    text-hash seen_within (RX-timing sensitive), claim_content_id is an atomic
    test-and-set so two intra-box egress paths racing to deliver the same
    logical message can never both win."""

    def _registry(self, **kw):
        from gateway.base_handler import RecentRfTxRegistry
        return RecentRfTxRegistry(**kw)

    def test_register_then_seen_content_id(self):
        r = self._registry()
        r.register_content_id("c1:abc")
        assert r.seen_content_id_within("c1:abc", 60.0) is True

    def test_unregistered_content_id_not_seen(self):
        r = self._registry()
        assert r.seen_content_id_within("c1:abc", 60.0) is False

    def test_content_id_window_expiry(self):
        r = self._registry()
        r.register_content_id("c1:abc")
        for k in list(r._entries):
            r._entries[k] -= 61.0            # simulate 61s passing
        assert r.seen_content_id_within("c1:abc", 60.0) is False

    def test_empty_content_id_never_seen_and_always_claims(self):
        # Unidentifiable content (no content_id) must NEVER be suppressed.
        r = self._registry()
        r.register_content_id("")            # no-op
        assert r.seen_content_id_within("", 60.0) is False
        assert r.claim_content_id("", 60.0) is True
        assert r.claim_content_id("", 60.0) is True

    def test_claim_first_wins_second_suppressed(self):
        r = self._registry()
        assert r.claim_content_id("c1:x", 60.0) is True    # first -> proceed
        assert r.claim_content_id("c1:x", 60.0) is False   # second -> suppress

    def test_claim_independent_ids_both_proceed(self):
        r = self._registry()
        assert r.claim_content_id("c1:a", 60.0) is True
        assert r.claim_content_id("c1:b", 60.0) is True

    def test_claim_expires_then_reclaimable(self):
        r = self._registry()
        assert r.claim_content_id("c1:x", 60.0) is True
        for k in list(r._entries):
            r._entries[k] -= 61.0
        assert r.claim_content_id("c1:x", 60.0) is True    # window passed

    def test_content_id_keys_dont_collide_with_text(self):
        # A content_id string and a text body equal to it must not cross-match.
        r = self._registry()
        r.register("c1:abc")                                # text key only
        assert r.seen_content_id_within("c1:abc", 60.0) is False
        r.register_content_id("c1:abc")
        assert r.seen_content_id_within("c1:abc", 60.0) is True
        assert r.seen_within("c1:abc", 60.0) is True        # text still independent

    def test_claim_is_atomic_under_concurrency(self):
        # 20 threads race claim() on ONE id: exactly one wins (the race-free
        # guarantee that the timing-sensitive text dedup could not give).
        import threading
        r = self._registry()
        results = []
        rlock = threading.Lock()

        def worker():
            got = r.claim_content_id("c1:race", 60.0)
            with rlock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1
        assert results.count(False) == 19


class TestMeshIdToNum:
    """transport-truth Phase 3: '!hex' mesh id -> numeric node, robustly."""

    def _fn(self):
        from gateway._rns_bridge_xform import MessageTransformMixin
        return MessageTransformMixin._mesh_id_to_num

    def test_parses_valid(self):
        assert self._fn()("!a2e95ba4") == 0xa2e95ba4
        assert self._fn()("A2E95BA4") == 0xa2e95ba4   # no bang, upper

    def test_rejects_malformed(self):
        f = self._fn()
        for bad in ("", "   ", "!zzzzzzzz", "!a2e95ba", "!a2e95ba44", "xyz", None):
            assert f(bad) is None


class TestTrueOriginInjectorBuild:
    """transport-truth Phase 3: the RNS bridge's DownlinkInjector build gate —
    only when the primary meshtastic leg is injection_mode='downlink' + PSK and
    the injector is usable; None (keep [RNS:label]) otherwise."""

    def test_none_when_not_downlink_mode(self, bridge):
        bridge.config.meshtastic.injection_mode = "toradio"
        assert bridge._build_true_origin_injector() is None

    def test_none_when_no_psk(self, bridge):
        bridge.config.meshtastic.injection_mode = "downlink"
        bridge.config.meshtastic.downlink_psk = ""
        assert bridge._build_true_origin_injector() is None

    def test_returns_injector_when_configured_and_usable(self, bridge):
        bridge.config.meshtastic.injection_mode = "downlink"
        bridge.config.meshtastic.downlink_psk = "AQ=="
        bridge.config.meshtastic.mqtt_broker = "localhost"
        bridge.config.meshtastic.mqtt_port = 1883
        bridge.config.meshtastic.mqtt_channel = "LongFast"
        fake = MagicMock()
        fake.usable = True
        with patch("gateway.mqtt_downlink_inject.DownlinkInjector",
                   return_value=fake) as Mk:
            got = bridge._build_true_origin_injector()
        assert got is fake
        Mk.assert_called_once()
        # cache: _get_ returns the same object without rebuilding
        bridge._true_origin_injector = fake
        assert bridge._get_true_origin_injector() is fake

    def test_none_when_injector_unusable(self, bridge):
        bridge.config.meshtastic.injection_mode = "downlink"
        bridge.config.meshtastic.downlink_psk = "AQ=="
        fake = MagicMock()
        fake.usable = False
        fake.fatal_reason = "downlink_psk must decode to 32 bytes"
        with patch("gateway.mqtt_downlink_inject.DownlinkInjector",
                   return_value=fake):
            assert bridge._build_true_origin_injector() is None


class TestTrueOriginDelivery:
    """transport-truth Phase 3: mesh-originated R→M broadcasts delivered AS the
    true origin via the downlink injector instead of [RNS:label], flag-gated,
    with a [RNS:] fallback on every failure (never untagged-unguarded)."""

    def _fresh_primary_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _mesh_bcast(self, body="the joke text", **fields):
        from gateway.rns_bridge import BridgedMessage
        f = {
            "meshforge_source_network": "meshtastic",
            "meshforge_from_id": "!a2e95ba4",
            "meshforge_from_short": "BORG",
        }
        f.update(fields)
        return BridgedMessage(
            source_network="rns",
            source_id="dead0000beef1111cafe2222f00d3333",   # relaying gw hash
            destination_id=None, content=body,
            metadata={"lxmf_fields": f},
        )

    def test_broadcast_delivered_true_origin_not_tagged(self, bridge, monkeypatch):
        reg = self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        inj = MagicMock(); inj.inject.return_value = True
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(self._mesh_bcast("the joke text"))
        # Injected AS the true origin; NO [RNS:] fallback send happened.
        inj.inject.assert_called_once()
        args, kwargs = inj.inject.call_args
        assert args[0] == "the joke text" and args[1] == 0xa2e95ba4
        assert 'sends' not in captured
        assert bridge.stats.get('rns_to_mesh_true_origin') == 1
        # content_id armed on the primary registry for the M→R loop guard.
        from gateway.base_handler import loop_guard_content_id
        cid = loop_guard_content_id("meshtastic:!a2e95ba4", "the joke text")
        assert reg.seen_content_id_within(cid, 120) is True
        # EVIDENCE-TIER PIN (review 2026-07-01): cid ONLY, never the raw
        # text. The text namespace means "on RF" proven by RF RX or a
        # radio-API-confirmed send; inject()'s truth is broker-publish-only,
        # so it must stay in the cid namespace where check sites can tag the
        # suppression with the *_cid_only loss-exposure witness.
        assert reg.seen_within("the joke text", 120) is False

    def test_flag_off_keeps_rns_tag(self, bridge, monkeypatch):
        self._fresh_primary_registry(monkeypatch)
        # flag OFF (default MagicMock reads False); injector must not be consulted
        inj = MagicMock(); inj.inject.return_value = True
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(self._mesh_bcast())
        inj.inject.assert_not_called()
        assert captured['sends'][0].startswith("[RNS:BORG] ")

    def test_rns_origin_keeps_tag_even_with_flag_on(self, bridge, monkeypatch):
        self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(   # genuine RNS content: no meshforge_source_network
            source_network="rns", source_id="abcdef0123456789",
            destination_id=None, content="from nomadnet")
        inj = MagicMock(); inj.inject.return_value = True
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(msg)
        inj.inject.assert_not_called()
        assert captured['sends'][0].startswith("[RNS:abcd] ")

    def test_inject_failure_falls_back_to_tag(self, bridge, monkeypatch):
        reg = self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        inj = MagicMock(); inj.inject.return_value = False   # publish failed
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(self._mesh_bcast("wx"))
        inj.inject.assert_called_once()
        assert captured['sends'][0].startswith("[RNS:BORG] ")   # tag fallback
        # content_id NOT registered on failure (delivered tagged, tag-guarded).
        from gateway.base_handler import loop_guard_content_id
        cid = loop_guard_content_id("meshtastic:!a2e95ba4", "wx")
        assert reg.seen_content_id_within(cid, 120) is False
        assert bridge.stats.get('rns_to_mesh_true_origin', 0) == 0

    def test_origin_unrecoverable_falls_back(self, bridge, monkeypatch):
        self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        inj = MagicMock(); inj.inject.return_value = True
        captured = {}
        # no meshforge_from_id -> origin_num None -> fall back before inject
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(
                self._mesh_bcast("x", meshforge_from_id=""))
        inj.inject.assert_not_called()
        assert captured['sends'][0].startswith("[RNS:")

    def test_injector_none_keeps_tag(self, bridge, monkeypatch):
        self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=None), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append(content) or True):
            bridge._process_rns_to_mesh(self._mesh_bcast())
        assert captured['sends'][0].startswith("[RNS:BORG] ")

    def test_mesh_origin_at_token_stays_broadcast(self, bridge, monkeypatch):
        """A MESH-originated relayed broadcast whose body starts with a
        resolvable '@<addr>' must STAY a broadcast (review 2026-07-01): the
        @-directed downlink (#39) is for RNS-authored originals only — the
        DM conversion rewrote the mesh audience AND bypassed every
        broadcast-gated dedup/true-origin path (the resolved-DM leg neither
        checks nor registers either RF-TX namespace). So the @-parse is
        skipped, destination stays None, and true-origin delivery runs on
        the FULL body, token included."""
        self._fresh_primary_registry(monkeypatch)
        bridge.config.rns.true_origin_downlink_enabled = True
        inj = MagicMock(); inj.inject.return_value = True
        captured = {}
        with patch.object(bridge, '_get_true_origin_injector', return_value=inj), \
                patch.object(bridge, '_resolve_mesh_destination',
                             return_value='!ebfa1b11') as resolve, \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append((content, destination)) or True):
            bridge._process_rns_to_mesh(
                self._mesh_bcast("@!ebfa1b11 roger"))
        # @-resolution never consulted for mesh-originated content...
        resolve.assert_not_called()
        # ...and the message delivered true-origin as a broadcast, untruncated.
        inj.inject.assert_called_once()
        args, _ = inj.inject.call_args
        assert args[0] == "@!ebfa1b11 roger"
        assert 'sends' not in captured

    def test_rns_authored_at_token_still_resolves_dm(self, bridge, monkeypatch):
        """Regression pin for #39: an RNS-AUTHORED original (no
        meshforge_source_network) keeps the @-directed downlink — resolution
        runs and the send is a DM with the token stripped."""
        from gateway.rns_bridge import BridgedMessage
        self._fresh_primary_registry(monkeypatch)
        msg = BridgedMessage(
            source_network="rns",
            source_id="dead0000beef1111cafe2222f00d3333",
            destination_id=None, content="@!ebfa1b11 roger",
            metadata={},
        )
        captured = {}
        with patch.object(bridge, '_resolve_mesh_destination',
                          return_value='!ebfa1b11'), \
                patch.object(bridge, 'send_to_meshtastic',
                             side_effect=lambda content, destination=None, channel=0:
                                 captured.setdefault('sends', []).append((content, destination)) or True):
            bridge._process_rns_to_mesh(msg)
        assert captured.get('sends'), "DM send must happen"
        content, destination = captured['sends'][0]
        assert destination == '!ebfa1b11'
        assert 'roger' in content and '@!ebfa1b11' not in content


class TestLoopGuardContentId:
    """Transport-truth arc Phase 2: the channel-agnostic loop-guard content_id.

    The loop guard is INTRA-box (register at true-origin delivery, recognize on
    re-RX) and must compute the SAME id regardless of ingress mode — but the
    modes disagree on how they see the channel (topic NAME vs box-local numeric
    slot, #77). So the loop-guard id deliberately drops the channel axis and
    keys on ORIGIN + CONTENT only."""

    def test_deterministic(self):
        from gateway.base_handler import loop_guard_content_id
        a = loop_guard_content_id("meshtastic:!a2e95ba4", "borg joke reply")
        b = loop_guard_content_id("meshtastic:!a2e95ba4", "borg joke reply")
        assert a == b and a.startswith("c1:")

    def test_channel_agnostic(self):
        # By construction the loop-guard id takes no channel — it equals the
        # channel="" content_id and is INVARIANT to any channel a caller might
        # otherwise have supplied. This is what lets mqtt_bridge (channel name)
        # and mesh_bridge/TCP (numeric slot) agree on one box.
        from gateway.base_handler import loop_guard_content_id
        from gateway.canonical_message import compute_content_id
        lg = loop_guard_content_id("meshtastic:!a2e95ba4", "hello")
        assert lg == compute_content_id("meshtastic:!a2e95ba4", "hello", "")
        # Differs from a channel-scoped id — proving the channel really is out.
        assert lg != compute_content_id(
            "meshtastic:!a2e95ba4", "hello", "longfast")

    def test_origin_keyed(self):
        # The axis a plain text-registry lookup lacks: identical text from two
        # DIFFERENT senders must NOT collide (else the guard would drop a
        # different node's genuine message).
        from gateway.base_handler import loop_guard_content_id
        borg = loop_guard_content_id("meshtastic:!a2e95ba4", "wx")
        alice = loop_guard_content_id("meshtastic:!11112222", "wx")
        assert borg != alice

    def test_empty_content_unidentifiable(self):
        # No text -> '' -> the guard treats it as unidentifiable and never
        # suppresses (honest_failure_modes #1).
        from gateway.base_handler import loop_guard_content_id
        assert loop_guard_content_id("meshtastic:!a2e95ba4", "") == ""
        assert loop_guard_content_id("meshtastic:!a2e95ba4", "   ") == ""

    def test_tag_stripped_and_whitespace_normalized(self):
        # A tagged delivery form and the raw re-RX form normalize to one id,
        # and whitespace runs collapse — so register/check round-trip even if
        # one side carries a leading bridge tag.
        from gateway.base_handler import loop_guard_content_id
        raw = loop_guard_content_id("meshtastic:!a2e95ba4", "hello world")
        tagged = loop_guard_content_id(
            "meshtastic:!a2e95ba4", "[RNS:borg]  hello   world")
        assert raw == tagged

    def test_round_trip_delivery_to_rerx(self):
        # The real invariant: the id registered at true-origin DELIVERY
        # (origin=BORG, body) equals the id computed at re-RX (from_id=BORG,
        # untagged text). This is what makes the loop guard recognize the echo.
        from gateway.base_handler import loop_guard_content_id
        delivered = loop_guard_content_id(
            "meshtastic:!a2e95ba4", "the joke text")
        from_id = "!a2e95ba4"
        re_rx = loop_guard_content_id(f"meshtastic:{from_id}", "the joke text")
        assert delivered == re_rx


class TestMeshOriginContentId:
    """Review 2026-07-01: mesh_origin_content_id is THE shared derivation of
    the Phase-4 dedup / loop-guard key — every register and check site routes
    through it so the byte-equality invariant can never drift per-site (the
    recipe was previously hand-rolled at 6 sites in 3 variants)."""

    def test_equals_hand_rolled_recipe(self):
        from gateway.base_handler import (
            loop_guard_content_id, mesh_origin_content_id,
        )
        assert (mesh_origin_content_id("!a2e95ba4", "wx")
                == loop_guard_content_id("meshtastic:!a2e95ba4", "wx"))

    def test_origin_stripped(self):
        # One site stripped its origin, one used it raw — the helper makes
        # the normalization uniform so register/check can't diverge.
        from gateway.base_handler import mesh_origin_content_id
        assert (mesh_origin_content_id("  !a2e95ba4 ", "wx")
                == mesh_origin_content_id("!a2e95ba4", "wx"))

    def test_no_origin_yields_empty(self):
        from gateway.base_handler import mesh_origin_content_id
        assert mesh_origin_content_id(None, "wx") == ""
        assert mesh_origin_content_id("", "wx") == ""
        assert mesh_origin_content_id("   ", "wx") == ""

    def test_placeholder_sentinels_yield_empty(self):
        # 'unknown' (M→R uplink / mqtt ingress fallback) and 'None' (a raw
        # f-string over packet.get('fromId')) are NOT identities — sharing
        # one key bucket would let byte-identical text from two different
        # unidentified senders cross-suppress.
        from gateway.base_handler import mesh_origin_content_id
        assert mesh_origin_content_id("unknown", "wx") == ""
        assert mesh_origin_content_id("None", "wx") == ""

    def test_sentinel_constant_shared_with_producers(self):
        # The producers mint UNKNOWN_ORIGIN and the filter refuses it — one
        # constant, so a respelled producer sentinel can't slip past the
        # filter and collapse unidentified senders onto one key bucket
        # (honest_failure_modes #5; review 2026-07-01 second pass).
        from gateway.base_handler import UNKNOWN_ORIGIN, mesh_origin_content_id
        assert mesh_origin_content_id(UNKNOWN_ORIGIN, "wx") == ""

    def test_non_str_origin_degrades_never_raises(self):
        """Review 2026-07-01 second pass: meshforge_from_id is a PEER-
        CONTROLLED msgpack wire field — a truthy non-str must degrade to
        'no origin', never AttributeError into a caller's broad except
        (which converted every message from that peer into an error+requeue
        path)."""
        from gateway.base_handler import mesh_origin_content_id
        assert mesh_origin_content_id(12345, "wx") == ""
        assert mesh_origin_content_id(3.5, "wx") == ""
        assert mesh_origin_content_id(True, "wx") == ""
        assert mesh_origin_content_id(["!a2e95ba4"], "wx") == ""

    def test_bytes_origin_decodes_to_str_equal_key(self):
        """A bytes origin (the one honest non-str shape msgpack produces)
        must derive the SAME key as its str twin — pre-guard it silently
        minted a repr-keyed id ('meshtastic:b\\'!…\\'') that matched
        nothing."""
        from gateway.base_handler import mesh_origin_content_id
        assert (mesh_origin_content_id(b"!a2e95ba4", "wx")
                == mesh_origin_content_id("!a2e95ba4", "wx"))
        # Undecodable bytes are not an identity.
        assert mesh_origin_content_id(b"\xff\xfe", "wx") == ""


class TestSeenNamespaceWithin:
    """Review 2026-07-01: the shared two-namespace egress gate. Text checked
    first, so 'cid' means the ONLY suppression evidence is a content_id
    registration — the callers' loss-exposure witness."""

    def _reg(self):
        from gateway.base_handler import RecentRfTxRegistry
        return RecentRfTxRegistry()

    def test_no_hit(self):
        assert self._reg().seen_namespace_within("t", "c1:ab", 60) == ''

    def test_text_hit_wins(self):
        reg = self._reg()
        reg.register("hello")
        from gateway.base_handler import mesh_origin_content_id
        cid = mesh_origin_content_id("!a2e95ba4", "hello")
        reg.register_content_id(cid)
        assert reg.seen_namespace_within("hello", cid, 60) == 'text'

    def test_cid_only_hit(self):
        reg = self._reg()
        from gateway.base_handler import mesh_origin_content_id
        cid = mesh_origin_content_id("!a2e95ba4", "hello")
        reg.register_content_id(cid)
        assert reg.seen_namespace_within("hello", cid, 60) == 'cid'

    def test_empty_cid_never_matches(self):
        reg = self._reg()
        reg.register_content_id("")   # no-op
        assert reg.seen_namespace_within("nothing here", "", 60) == ''


class TestDualPathFlagStrictRead:
    """Review 2026-07-01 second pass: the strict is-True discipline of
    dual_path_dedup_enabled() pinned DIRECTLY (previously only transitive
    via one MagicMock bridge test + the unpinned assumption every site keeps
    delegating). Suppression is the loss direction, so a truthy-non-True
    config value (hand-edited "false" string, 1, a mock shim) arming it
    silently would be the dangerous failure."""

    def _cfg(self, value):
        from types import SimpleNamespace
        return SimpleNamespace(rns=SimpleNamespace(
            dual_path_dedup_enabled=value))

    def test_true_reads_on(self):
        from gateway.base_handler import dual_path_dedup_enabled
        assert dual_path_dedup_enabled(self._cfg(True)) is True

    def test_malformed_values_read_off(self):
        from gateway.base_handler import dual_path_dedup_enabled
        for malformed in ("false", "true", 1, 0, None, [], MagicMock()):
            assert dual_path_dedup_enabled(self._cfg(malformed)) is False, \
                f"{malformed!r} must read OFF (strict is-True)"

    def test_attribute_less_config_reads_off(self):
        from types import SimpleNamespace
        from gateway.base_handler import dual_path_dedup_enabled
        assert dual_path_dedup_enabled(SimpleNamespace()) is False
        assert dual_path_dedup_enabled(MagicMock()) is False


class TestWindowClampedToRegistryTtl:
    """Review 2026-07-01 (honest_failure_modes #5): the registry prunes
    entries past RF_TX_REGISTRY_MAX_AGE_S on every write, so any configured
    window above the TTL was silently defeated — non-deterministically, only
    on a busy gateway. The window helpers clamp to the shared constant."""

    def _cfg(self, **rns_values):
        from types import SimpleNamespace
        return SimpleNamespace(rns=SimpleNamespace(**rns_values))

    def test_dual_path_window_clamped(self):
        from gateway.base_handler import (
            RF_TX_REGISTRY_MAX_AGE_S, dual_path_dedup_window_s,
        )
        cfg = self._cfg(dual_path_dedup_window_sec=900)
        assert dual_path_dedup_window_s(cfg) == RF_TX_REGISTRY_MAX_AGE_S

    def test_loop_guard_window_clamped(self):
        from gateway.base_handler import (
            RF_TX_REGISTRY_MAX_AGE_S, true_origin_loop_guard_window_s,
        )
        cfg = self._cfg(true_origin_loop_guard_window_sec=900)
        assert true_origin_loop_guard_window_s(cfg) == RF_TX_REGISTRY_MAX_AGE_S

    def test_defaults_unaffected(self):
        from gateway.base_handler import (
            dual_path_dedup_window_s, true_origin_loop_guard_window_s,
        )
        from types import SimpleNamespace
        cfg = SimpleNamespace(rns=SimpleNamespace())
        assert dual_path_dedup_window_s(cfg) == 60.0
        assert true_origin_loop_guard_window_s(cfg) == 120.0

    def test_registry_default_uses_shared_constant(self):
        from gateway.base_handler import (
            RF_TX_REGISTRY_MAX_AGE_S, RecentRfTxRegistry,
        )
        assert RecentRfTxRegistry()._max_age_s == RF_TX_REGISTRY_MAX_AGE_S

    def test_nan_window_degrades_to_default(self):
        """Review 2026-07-01 second pass: NaN passes float() coercion and
        poisons min() AND every registry comparison as always-False — dedup
        silently OFF while the flag reads on. The bound must catch it."""
        from gateway.base_handler import (
            dual_path_dedup_window_s, true_origin_loop_guard_window_s,
        )
        cfg = self._cfg(dual_path_dedup_window_sec=float('nan'))
        assert dual_path_dedup_window_s(cfg) == 60.0
        cfg = self._cfg(true_origin_loop_guard_window_sec='nan')
        assert true_origin_loop_guard_window_s(cfg) == 120.0

    def test_negative_window_degrades_to_default(self):
        from gateway.base_handler import dual_path_dedup_window_s
        cfg = self._cfg(dual_path_dedup_window_sec=-30)
        assert dual_path_dedup_window_s(cfg) == 60.0

    def test_clamp_leaves_a_warning_witness(self, caplog):
        """The bound adjusts operator config silently otherwise — every
        swallow gets a witness (honest_failure_modes #9). Once per key."""
        import logging
        import gateway.base_handler as bh
        bh._window_warned.discard('dual_path_dedup_window_sec')
        cfg = self._cfg(dual_path_dedup_window_sec=900)
        with caplog.at_level(logging.WARNING, logger='gateway.base_handler'):
            assert bh.dual_path_dedup_window_s(cfg) == bh.RF_TX_REGISTRY_MAX_AGE_S
            first = [r for r in caplog.records if 'exceeds' in r.message]
            assert len(first) == 1
            # Second call: latched, no spam on the hot path.
            bh.dual_path_dedup_window_s(cfg)
            again = [r for r in caplog.records if 'exceeds' in r.message]
            assert len(again) == 1


class TestIsBridgeLoop:
    """Transport-truth arc Phase 2: is_bridge_loop — the augmented M→R loop
    guard. Signal 1 (leading bridge tag) is always active; signal 2 (a
    content_id delivered onto this mesh via a true-origin downlink) is consulted
    only when a registry + positive window + non-empty content_id are given."""

    def _registry(self):
        from gateway.base_handler import RecentRfTxRegistry
        return RecentRfTxRegistry()

    def test_tagged_always_loops_regardless_of_cid(self):
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        # Tag guard fires with no registry at all, and even with a fresh one.
        assert is_bridge_loop("[RNS:x] hi") is True
        assert is_bridge_loop("[RNS:x] hi", "c1:unregistered",
                              registry=r, cid_window_s=120) is True

    def test_untagged_no_registry_never_loops(self):
        from gateway.base_handler import is_bridge_loop
        assert is_bridge_loop("plain content") is False
        assert is_bridge_loop("plain content", "c1:abc") is False

    def test_untagged_registered_cid_loops(self):
        # The recognition mechanism: a content_id registered as delivered is
        # recognized on re-RX and the untagged content is dropped.
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        r.register_content_id("c1:abc")
        assert is_bridge_loop("plain content", "c1:abc",
                              registry=r, cid_window_s=120) is True

    def test_untagged_unregistered_cid_does_not_loop(self):
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        r.register_content_id("c1:other")
        assert is_bridge_loop("plain content", "c1:abc",
                              registry=r, cid_window_s=120) is False

    def test_empty_cid_never_loops_even_when_registry_present(self):
        # Unidentifiable content is never suppressed (honest_failure_modes #1).
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        r.register_content_id("")   # no-op
        assert is_bridge_loop("plain content", "",
                              registry=r, cid_window_s=120) is False

    def test_zero_window_disables_cid_branch(self):
        # cid_window_s == 0 (or the caller passing no window) means the flag is
        # off — the content_id branch is inert even with a registered id.
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        r.register_content_id("c1:abc")
        assert is_bridge_loop("plain content", "c1:abc",
                              registry=r, cid_window_s=0) is False

    def test_expired_cid_does_not_loop(self):
        from gateway.base_handler import is_bridge_loop
        r = self._registry()
        r.register_content_id("c1:abc")
        for k in list(r._entries):
            r._entries[k] -= 200.0   # older than the 120s window
        assert is_bridge_loop("plain content", "c1:abc",
                              registry=r, cid_window_s=120) is False

    def test_flag_off_equivalent_to_is_already_bridged(self):
        # With loop_cid='' (what the ingress passes when the flag is off),
        # is_bridge_loop reduces EXACTLY to is_already_bridged for both tagged
        # and untagged content — proving flag-off is a no-op vs today.
        from gateway.base_handler import is_bridge_loop, is_already_bridged
        r = self._registry()
        for text in ("[RNS:x] hi", "[MC:p4] yo", "plain", "", "  spaced  "):
            assert (
                is_bridge_loop(text, "", registry=r, cid_window_s=120)
                == is_already_bridged(text)
            )


class TestTrueOriginFlagReaders:
    """Strict `is True` reads of the transport-truth flags (default off), the
    same MagicMock-safe discipline as the dual_path / reply-routing gates."""

    def _cfg(self, **rns_kw):
        from types import SimpleNamespace
        cfg = SimpleNamespace(rns=SimpleNamespace(**rns_kw))
        return cfg

    def test_enabled_default_false(self):
        from gateway.base_handler import true_origin_downlink_enabled
        assert true_origin_downlink_enabled(self._cfg()) is False

    def test_enabled_true_only_on_literal_true(self):
        from gateway.base_handler import true_origin_downlink_enabled
        assert true_origin_downlink_enabled(
            self._cfg(true_origin_downlink_enabled=True)) is True
        # truthy-but-not-True reads OFF (a MagicMock attr, or 1/"yes")
        assert true_origin_downlink_enabled(
            self._cfg(true_origin_downlink_enabled=1)) is False
        assert true_origin_downlink_enabled(MagicMock()) is False

    def test_window_default_120(self):
        from gateway.base_handler import true_origin_loop_guard_window_s
        assert true_origin_loop_guard_window_s(self._cfg()) == 120.0

    def test_window_reads_config_value(self):
        from gateway.base_handler import true_origin_loop_guard_window_s
        assert true_origin_loop_guard_window_s(
            self._cfg(true_origin_loop_guard_window_sec=45)) == 45.0

    def test_window_malformed_falls_back(self):
        # A genuinely malformed value (float() raises ValueError/TypeError)
        # falls back to the 120s default — same guard as dual_path_dedup_window_s.
        # (A MagicMock config is NOT this case: it has __float__ -> 1.0, so it
        # reads 1.0 exactly like the existing helper; but the FLAG reads OFF for
        # a MagicMock, so that window is never consulted anyway.)
        from gateway.base_handler import true_origin_loop_guard_window_s
        assert true_origin_loop_guard_window_s(
            self._cfg(true_origin_loop_guard_window_sec="oops")) == 120.0
        assert true_origin_loop_guard_window_s(
            self._cfg(true_origin_loop_guard_window_sec=None)) == 120.0


class TestDualPathDedup:
    """R→M broadcast suppression on registry hit (gated, default off)."""

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _msg(self, content):
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content=content,
        )

    def test_suppressed_on_registry_hit(self, bridge, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("dup content from mesh_bridge")        # path A went out
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("dup content from mesh_bridge"))

        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 1
        assert bridge.stats['rns_to_mesh_delivered'] == 0
        assert bridge.stats['rns_to_mesh_dropped'] == 0     # suppression ≠ drop

    def test_no_hit_delivers_normally(self, bridge, monkeypatch):
        """The fallback value: RF-missed content (no registration) still goes out."""
        self._fresh_registry(monkeypatch)                    # empty registry
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("rf missed this one"))

        send.assert_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 0
        assert bridge.stats['rns_to_mesh_delivered'] == 1

    def test_flag_off_hit_still_delivers(self, bridge, monkeypatch):
        """Default-off: a registry hit without the flag changes nothing."""
        reg = self._fresh_registry(monkeypatch)
        reg.register("dup content")
        # bridge fixture config.rns is a MagicMock — dual_path_dedup_enabled
        # reads truthy-but-not-True, which the strict gate treats as OFF.

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("dup content"))

        send.assert_called()
        assert bridge.stats['rns_to_mesh_delivered'] == 1

    def test_directed_dm_never_suppressed(self, bridge, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("hello there")
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, '_resolve_mesh_destination',
                          return_value="!b03bb70c"), \
             patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._msg("@b03bb70c hello there"))

        send.assert_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 0

    def _mesh_origin_msg(self, content, from_id="!a2e95ba4"):
        """A mesh-originated R→M broadcast carrying its true origin — the shape
        the true-origin downlink + Phase-4 content_id dedup operate on."""
        from gateway.rns_bridge import BridgedMessage
        return BridgedMessage(
            source_network="rns",
            source_id="dead0000beef1111cafe2222f00d3333",   # relaying gw hash
            destination_id=None, content=content,
            metadata={"lxmf_fields": {
                "meshforge_source_network": "meshtastic",
                "meshforge_from_id": from_id,
                "meshforge_from_short": "BORG",
            }},
        )

    def test_suppressed_on_content_id_hit(self, bridge, monkeypatch):
        """Phase 4 (the live 0743 dup): mesh_bridge delivered this content
        UNTAGGED as its true origin and registered ONLY the content_id (never
        the raw text). A peer's RNS relay of the same logical message arriving
        R→M must suppress via the content_id namespace — the text-only check is
        blind to it. RED pre-Phase-4."""
        reg = self._fresh_registry(monkeypatch)
        from gateway.base_handler import loop_guard_content_id
        cid = loop_guard_content_id("meshtastic:!a2e95ba4", "borg reply text")
        reg.register_content_id(cid)                       # only the id, no text
        assert not reg.seen_within("borg reply text", 60.0)
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send, \
             patch.object(bridge, '_get_true_origin_injector',
                          return_value=MagicMock()):
            bridge._process_rns_to_mesh(self._mesh_origin_msg("borg reply text"))

        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 1

    def test_cid_only_suppression_bumps_witness(self, bridge, monkeypatch):
        """Review 2026-07-01: a suppression whose only evidence is the cid
        namespace (broker-publish-only true-origin delivery) leaves its own
        witness stat — the loss-exposure meter for the stale-PSK class."""
        reg = self._fresh_registry(monkeypatch)
        from gateway.base_handler import mesh_origin_content_id
        reg.register_content_id(
            mesh_origin_content_id("!a2e95ba4", "cid witness text"))
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(
                self._mesh_origin_msg("cid witness text"))

        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 1
        assert bridge.stats['rns_to_mesh_dual_path_suppressed_cid_only'] == 1

    def test_text_suppression_no_cid_witness(self, bridge, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("text witness text")
        bridge.config.rns.dual_path_dedup_enabled = True
        bridge.config.rns.dual_path_dedup_window_sec = 60

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(
                self._mesh_origin_msg("text witness text"))

        send.assert_not_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 1
        assert bridge.stats.get(
            'rns_to_mesh_dual_path_suppressed_cid_only', 0) == 0

    def test_queued_payload_carries_content_id(self, bridge, monkeypatch):
        """Review 2026-07-01: every queued [RNS:label] chunk payload carries
        the logical message's content_id so the dispatch-time re-check can
        recognize a true-origin (cid-only) delivery of the same message —
        the tagged chunk's text can never match it, and chunked text can't
        match the full body either way."""
        self._fresh_registry(monkeypatch)
        from gateway.base_handler import mesh_origin_content_id
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        bridge._persistent_queue = mock_queue
        bridge.config.bridge_mode = "mqtt_bridge"
        bridge.config.meshtastic.channel = 2

        with patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True):
            bridge._process_rns_to_mesh(
                self._mesh_origin_msg("payload cid text"))

        assert mock_queue.enqueue.called
        payload = mock_queue.enqueue.call_args.kwargs['payload']
        assert payload['content_id'] == mesh_origin_content_id(
            "!a2e95ba4", "payload cid text")

    def test_queued_payload_empty_cid_for_rns_origin(self, bridge, monkeypatch):
        """Genuine RNS-origin content has no mesh origin → cid '' (never
        registered, never matched — honest_failure_modes #1)."""
        self._fresh_registry(monkeypatch)
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        bridge._persistent_queue = mock_queue
        bridge.config.bridge_mode = "mqtt_bridge"
        bridge.config.meshtastic.channel = 2

        with patch("gateway.rns_bridge.HAS_PERSISTENT_QUEUE", True):
            bridge._process_rns_to_mesh(self._msg("pure rns text"))

        assert mock_queue.enqueue.called
        payload = mock_queue.enqueue.call_args.kwargs['payload']
        assert payload['content_id'] == ""

    def test_requeue_failed_chunks_carries_content_id(self, bridge):
        """Review 2026-07-01 second pass: the partial-failure retry leg
        dropped the cid, leaving the dispatch re-check's cid namespace inert
        exactly when a true-origin delivery (cid-only registration) of the
        same logical message could race the retry."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        bridge._persistent_queue = mock_queue
        bridge.config.meshtastic.channel = 2

        assert bridge._requeue_failed_chunks(
            ["chunk one"], None, "meshtastic", content_id="c1:abc") is True
        payload = mock_queue.enqueue.call_args.kwargs['payload']
        assert payload['content_id'] == "c1:abc"

    def test_requeue_failed_message_recomputes_content_id(self, bridge):
        """The subsystem-flap requeue recomputes the cid from the message's
        own lxmf_fields (it can be called from except handlers where the
        enqueue-time cid was never computed)."""
        from gateway.base_handler import mesh_origin_content_id
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        bridge._persistent_queue = mock_queue

        msg = self._mesh_origin_msg("flap retry text")
        assert bridge._requeue_failed_message(msg, "meshtastic") is True
        payload = mock_queue.enqueue.call_args.kwargs['payload']
        assert payload['content_id'] == mesh_origin_content_id(
            "!a2e95ba4", "flap retry text")

    def test_requeue_failed_message_rns_origin_empty_cid(self, bridge):
        """No mesh origin in metadata → cid '' — never a shared bucket."""
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = True
        bridge._persistent_queue = mock_queue

        assert bridge._requeue_failed_message(
            self._msg("plain rns retry"), "meshtastic") is True
        payload = mock_queue.enqueue.call_args.kwargs['payload']
        assert payload['content_id'] == ""

    def test_witness_counters_pre_seeded(self, bridge):
        """Absent-vs-zero (honest_failure_modes #9, review 2026-07-01 second
        pass): the loss-exposure witnesses — including the dispatch-time
        pair the handlers bump into this shared stats dict — must exist at 0
        from construction."""
        assert bridge.stats['rns_to_mesh_dual_path_suppressed_cid_only'] == 0
        assert bridge.stats['dispatch_dedup_suppressed'] == 0
        assert bridge.stats['dispatch_dedup_suppressed_cid_only'] == 0

    def test_content_id_hit_flag_off_delivers(self, bridge, monkeypatch):
        """Default-off: a content_id hit without the flag changes nothing."""
        reg = self._fresh_registry(monkeypatch)
        from gateway.base_handler import loop_guard_content_id
        reg.register_content_id(
            loop_guard_content_id("meshtastic:!a2e95ba4", "off text"))
        # bridge fixture config.rns is a MagicMock → strict is-True gate = OFF.

        with patch.object(bridge, 'send_to_meshtastic', return_value=True) as send:
            bridge._process_rns_to_mesh(self._mesh_origin_msg("off text"))

        send.assert_called()
        assert bridge.stats['rns_to_mesh_dual_path_suppressed'] == 0


class TestChunkForMeshPrefix:
    """Tag-every-chunk (2026-06-04): the bridge tag is the echo-loop
    invariant and must ride EVERY chunk — untagged tail chunks bypassed
    is_already_bridged on every gateway and got re-bridged onto RF."""

    def test_every_chunk_carries_prefix(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh(_LEADERBOARD, prefix="[RNS:abcd] ")
        assert len(chunks) >= 2
        assert all(c.startswith("[RNS:abcd] ") for c in chunks)

    def test_budget_includes_prefix(self):
        from gateway.base_handler import chunk_for_mesh
        chunks = chunk_for_mesh(_LEADERBOARD, max_bytes=100, prefix="[RNS:abcd] ")
        assert all(len(c.encode("utf-8")) <= 100 for c in chunks)

    def test_single_fit_returns_prefixed_message(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh("short", prefix="[RNS:abcd] ") == ["[RNS:abcd] short"]

    def test_content_reassembles_without_loss(self):
        from gateway.base_handler import chunk_for_mesh
        prefix = "[RNS:abcd] "
        chunks = chunk_for_mesh(_LEADERBOARD, max_bytes=80, prefix=prefix)
        stripped = [c[len(prefix):] for c in chunks]
        # Newline/word boundaries collapse to whitespace — compare tokens.
        assert " ".join(" ".join(stripped).split()) == " ".join(_LEADERBOARD.split())

    def test_no_prefix_behavior_unchanged(self):
        from gateway.base_handler import chunk_for_mesh
        assert chunk_for_mesh(_LEADERBOARD) == chunk_for_mesh(_LEADERBOARD, prefix="")

    def test_absurd_prefix_falls_back_untagged(self):
        from gateway.base_handler import chunk_for_mesh
        huge = "[RNS:" + "x" * 300 + "] "
        chunks = chunk_for_mesh(_LEADERBOARD, prefix=huge)
        assert chunks == chunk_for_mesh(_LEADERBOARD)

    def test_tagged_tail_chunks_trip_loop_guard(self):
        """The point of the change: every chunk is now is_already_bridged."""
        from gateway.base_handler import chunk_for_mesh, is_already_bridged
        chunks = chunk_for_mesh(_LEADERBOARD, prefix="[RNS:abcd] ")
        assert all(is_already_bridged(c) for c in chunks)


# ---------------------------------------------------------------------------
# Issue #74: circuit breaker actually wired into the send paths
# ---------------------------------------------------------------------------

class TestCircuitBreakerWiringIssue74:
    """The breaker was write-only before #74: can_send_to /
    record_send_success / record_send_failure had ZERO callers, so a
    tripped circuit never stopped the next send and organic failures
    never fed the threshold-OPEN transition. These tests pin the gate
    and the outcome recording in both send paths, plus the
    reset-on-reconnect."""

    @staticmethod
    def _fake_rns_lxmf_modules():
        import sys  # noqa: F401  (parallel to TestLxmfCallback helper)
        fake_rns = MagicMock(name="RNS")
        fake_rns.Transport.has_path.return_value = True
        fake_rns.Identity.recall.return_value = MagicMock(name="dest_identity")
        fake_rns.Destination.OUT = "OUT"
        fake_rns.Destination.SINGLE = "SINGLE"
        fake_rns.Destination.return_value = MagicMock(name="destination")
        fake_lxmf = MagicMock(name="LXMF")
        fake_lxmf.LXMessage.return_value = MagicMock(name="LXMessage_instance")
        return fake_rns, fake_lxmf

    def _prime(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock(name="lxmf_source")
        bridge._lxmf_router = MagicMock(name="lxmf_router")

    def test_send_to_rns_blocked_when_circuit_open(self, bridge):
        """Open circuit -> send returns False WITHOUT touching any RNS RPC."""
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        bridge._circuit_breaker.can_send.return_value = False
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            result = bridge.send_to_rns("hello", b"\xab" * 16)
        assert bool(result) is False and result.reason == "circuit_open"
        fake_rns.Transport.has_path.assert_not_called()
        fake_rns.Transport.request_path.assert_not_called()

    def test_send_to_rns_records_success(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bool(bridge.send_to_rns("hello", b"\xab" * 16)) is True
        bridge._circuit_breaker.record_success.assert_called_once_with(
            (b"\xab" * 16).hex()[:8]
        )

    def test_send_to_rns_records_failure_on_exception(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_lxmf.LXMessage.side_effect = RuntimeError("ctor boom")
        self._prime(bridge)
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            res = bridge.send_to_rns("hello", b"\xab" * 16)
        # row 2: a real crash is now NAMED send_error, not a bare False
        assert bool(res) is False and res.reason == "send_error"
        assert "ctor boom" in res.detail
        dest, err = bridge._circuit_breaker.record_failure.call_args[0]
        assert dest == (b"\xab" * 16).hex()[:8]
        assert "ctor boom" in err

    def test_send_to_rns_records_failure_on_no_path(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_rns.Transport.has_path.return_value = False
        self._prime(bridge)
        # Short-circuit the 50-iteration path-wait loop.
        bridge._stop_event.set()
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            res = bridge.send_to_rns("hello", b"\xab" * 16)
        assert bool(res) is False and res.reason == "no_path"
        bridge._stop_event.clear()
        bridge._circuit_breaker.record_failure.assert_called_once_with(
            (b"\xab" * 16).hex()[:8], "no path"
        )

    def test_queue_send_rns_raises_retriable_when_circuit_open(self, bridge):
        """Queue path RAISES with a retriable-pattern message so
        RetryPolicy classifies it transient (backoff + retry after the
        recovery window) instead of the unknown-error one-shot retry a
        bare `return False` would get."""
        self._prime(bridge)
        bridge._circuit_breaker.can_send.return_value = False
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            bridge._queue_send_rns(payload)

    def test_queue_send_rns_records_success(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        self._prime(bridge)
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge._queue_send_rns(payload) is True
        bridge._circuit_breaker.record_success.assert_called_once_with(
            (b"\xab" * 16).hex()[:8]
        )

    def test_queue_send_rns_records_failure_on_exception(self, bridge):
        import sys
        fake_rns, fake_lxmf = self._fake_rns_lxmf_modules()
        fake_lxmf.LXMessage.side_effect = RuntimeError("queue boom")
        self._prime(bridge)
        payload = {"message": "hi", "destination_hash": b"\xab" * 16}
        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            assert bridge._queue_send_rns(payload) is False
        dest, err = bridge._circuit_breaker.record_failure.call_args[0]
        assert dest == (b"\xab" * 16).hex()[:8]
        assert "queue boom" in err

    def test_reconnect_resets_circuit_breaker(self, bridge):
        """A fresh RNS transport invalidates stale per-destination OPEN
        state — reconnect success calls reset_all()."""
        bridge._running = True
        bridge._connected_rns = False
        bridge._rns_init_failed_permanently = False
        bridge._rns_reconnect.should_retry.return_value = True
        bridge._rns_reconnect.attempts = 0
        bridge._circuit_breaker.reset_all.return_value = 2

        def _fake_connect():
            bridge._connected_rns = True
        bridge._connect_rns = _fake_connect

        def _stop_loop():
            bridge._running = False
        bridge._maybe_start_meshtastic_broadcast = _stop_loop

        bridge._rns_loop()
        bridge._circuit_breaker.reset_all.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #74: trip_open failure inside the wedge hook is VISIBLE
# ---------------------------------------------------------------------------

class TestTripOpenFailureVisibleIssue74:
    """The composite _on_wedge isolates a trip_open exception so the
    default publish+counter+abort still run — but it must log it, not
    swallow it: with the abort suppressed (env backstop / tests) a
    broken trip_open would otherwise be undetectable."""

    def test_trip_open_failure_logged_and_publish_still_runs(
        self, bridge, caplog, monkeypatch
    ):
        import logging
        import sys
        from gateway import bounded_rpc
        from gateway.bounded_rpc import WedgeTimeout

        monkeypatch.setenv("MESHFORGE_BOUNDED_RPC_NO_EXIT", "1")
        bounded_rpc._reset_counters_for_tests()

        fake_rns = MagicMock(name="RNS")
        fake_rns.Transport.has_path.side_effect = WedgeTimeout("synthetic")
        fake_lxmf = MagicMock(name="LXMF")
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock()
        bridge._lxmf_router = MagicMock()
        bridge._circuit_breaker.trip_open.side_effect = RuntimeError(
            "breaker boom"
        )

        with patch.dict(sys.modules, {"RNS": fake_rns, "LXMF": fake_lxmf}):
            with caplog.at_level(logging.ERROR):
                result = bridge.send_to_rns("hello", b"\xab" * 16)

        assert bool(result) is False
        assert any(
            "trip_open failed" in r.getMessage() for r in caplog.records
        ), "broken trip_open must be logged, not silently swallowed"
        # The default hook still ran: wedge counter bumped.
        assert bounded_rpc.get_wedge_counters().get("rnsd.has_path") == 1
        bounded_rpc._reset_counters_for_tests()


class TestMeshOracleRnsWiring:
    """Phase-2 wiring of the read-only mesh oracle into the LXMF/RNS RX path."""

    def test_rns_oracle_default_off(self, bridge, monkeypatch):
        monkeypatch.delenv("MESHFORGE_ORACLE_ENABLED", raising=False)
        assert bridge._build_rns_oracle_responder() is None

    def test_build_rns_oracle_responder_on(self, bridge, monkeypatch):
        monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHFORGE_ORACLE_RNS_ALLOWLIST", "*")
        assert bridge._build_rns_oracle_responder() is not None

    def test_rns_leg_send_fn_forwards_the_reason_not_a_bare_bool(
        self, bridge, monkeypatch
    ):
        """Structural-dark row 2, the wiring half: the RNS leg's send_fn must
        hand the responder the RnsSendResult ITSELF, so a real send crash is
        audited as send_error instead of falling into the benign bucket. A
        `bool(...)` here would still deliver and still log — and would silently
        re-open the blind spot — so pin the composed path, not just the type.
        """
        from gateway.bridge_send_mixin import RnsSendResult

        monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHFORGE_ORACLE_RNS_ALLOWLIST", "*")
        records = []
        monkeypatch.setattr(
            bridge, "send_to_rns",
            lambda *a, **k: RnsSendResult(False, "send_error", "rnsd wedged"),
            raising=False,
        )
        responder = bridge._build_rns_oracle_responder()
        assert responder is not None
        monkeypatch.setattr(responder, "_log_fn", records.append)

        assert responder.handle("ab" * 16, "status")   # consumed
        assert records[-1]["delivered"] is False
        assert records[-1]["reason"] == "send_error: rnsd wedged"
        assert records[-1]["transport"] == "rns"

    def test_rns_leg_send_fn_names_a_benign_no_path(self, bridge, monkeypatch):
        """The other side of the same wire: benign reasons are RECORDED (so the
        ambiguity measure can shrink) but must not read as send_error."""
        from gateway.bridge_send_mixin import RnsSendResult

        monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHFORGE_ORACLE_RNS_ALLOWLIST", "*")
        records = []
        monkeypatch.setattr(bridge, "send_to_rns",
                            lambda *a, **k: RnsSendResult(False, "no_path"),
                            raising=False)
        responder = bridge._build_rns_oracle_responder()
        monkeypatch.setattr(responder, "_log_fn", records.append)

        assert responder.handle("cd" * 16, "status")
        assert records[-1]["reason"] == "no_path"

    def test_lxmf_query_routed_and_consumed(self, bridge):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        bridge._oracle_rns = MagicMock()
        bridge._oracle_rns.handle.return_value = "dude-AI@x: fleet:?"
        h = bytes.fromhex("aabbccddeeff00112233445566778899")
        msg = SimpleNamespace(source_hash=h, content="status", title=None,
                              stamp=None, fields=None)
        bridge._on_lxmf_receive(msg)
        bridge._oracle_rns.handle.assert_called_once_with(h.hex(), "status", 0)
        assert bridge._rns_to_mesh_queue.empty()  # consumed, not bridged onward


class TestContentIdCarryRnsLeg:
    """STEP 2b-i (dedup/identity arc, measure-only): the logical content_id
    is CARRIED across the RNS leg — stamped into the M→R meshforge_content_id
    LXMF field, and read back (or minted) at _on_lxmf_receive."""

    def test_m2r_fields_carry_content_id(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        bridge.config.rns.get_lxmf_destinations.return_value = [
            "6b1a0120941444587d7d1dc1bf6d64d7",
        ]
        bridge.node_tracker.get_node_by_mesh_id.return_value = None
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!deadbeef",
            destination_id=None, content="hello fleet",
            content_id="c1:abc123", metadata={"channel": "meshforge"})
        captured = {}

        def capture_send(content, dest_hash=None, title=None, fields=None):
            captured["fields"] = fields
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)
        assert captured["fields"]["meshforge_content_id"] == "c1:abc123"

    def test_on_lxmf_mints_content_id_when_field_absent(self, bridge):
        from types import SimpleNamespace
        from gateway.canonical_message import compute_content_id
        bridge._oracle_rns = None
        h = bytes.fromhex("aabbccddeeff00112233445566778899")
        msg = SimpleNamespace(source_hash=h, content="sun", title=None,
                              stamp=None, fields=None)
        captured = {}
        with patch.object(bridge, '_notify_message',
                          side_effect=lambda m: captured.setdefault('m', m)), \
             patch("commands.messaging.store_incoming"):
            bridge._on_lxmf_receive(msg)
        expected = compute_content_id("rns:" + h.hex(), "sun", "")
        assert captured['m'].content_id == expected != ""

    def test_on_lxmf_carries_content_id_when_field_present(self, bridge):
        # A sibling gateway already minted the id on the M→R hop; the RNS
        # ingress must CARRY it (not re-mint), so the two legs share one id.
        from types import SimpleNamespace
        bridge._oracle_rns = None
        h = bytes.fromhex("aabbccddeeff00112233445566778899")
        msg = SimpleNamespace(source_hash=h, content="sun", title=None,
                              stamp=None,
                              fields={"meshforge_content_id": "c1:carried99"})
        captured = {}
        with patch.object(bridge, '_notify_message',
                          side_effect=lambda m: captured.setdefault('m', m)), \
             patch("commands.messaging.store_incoming"):
            bridge._on_lxmf_receive(msg)
        assert captured['m'].content_id == "c1:carried99"

    def test_spill_requeue_reconstructs_content_id(self, bridge):
        # A queue-full spill persists content_id (mqtt handler) and the requeue
        # worker reconstructs it, so the overflow copy stays correlatable.
        captured = {}
        with patch.object(bridge, '_process_mesh_to_rns',
                          side_effect=lambda m: captured.setdefault('m', m)):
            bridge._dispatch_rns_xform_spill({
                'source_id': '!a2e95ba4', 'content': 'hi',
                'content_id': 'c1:spilled', 'is_broadcast': True})
        assert captured['m'].content_id == 'c1:spilled'


class TestContentIdViewPublishHookStep4c:
    """STEP 4c producer hook: the gateway loop throttle-publishes its
    content_id_view to the state file the cross-box JOIN ssh-collects.
    Exception-isolated — a publish failure must never touch the bridge."""

    def _fake(self):
        import types
        ns = types.SimpleNamespace()
        # STEP 6: the publish hook gathers infra_hashes off self before
        # writing — model that dependency so the throttle/isolation tests
        # exercise the real publish path, not an AttributeError swallow.
        ns._content_id_view_infra_hashes = lambda: []
        return ns

    def test_publishes_first_then_throttles_then_republishes(self, monkeypatch):
        import types
        from gateway import rns_bridge
        calls = []
        monkeypatch.setattr(rns_bridge._dc, "write_content_id_view_state",
                            lambda **kw: (calls.append(kw), True)[1])
        clock = {"t": 1000.0}
        monkeypatch.setattr(rns_bridge, "time",
                            types.SimpleNamespace(monotonic=lambda: clock["t"]))
        fake = self._fake()
        meth = rns_bridge.RNSMeshtasticBridge._maybe_publish_content_id_view
        meth(fake)                     # first → publish
        assert len(calls) == 1
        meth(fake)                     # immediate → throttled
        assert len(calls) == 1
        clock["t"] += rns_bridge._CONTENT_ID_VIEW_WRITE_INTERVAL_S + 1
        meth(fake)                     # after interval → publish
        assert len(calls) == 2

    def test_never_raises_when_writer_errors(self, monkeypatch):
        import types
        from gateway import rns_bridge

        def boom(**kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(rns_bridge._dc,
                            "write_content_id_view_state", boom)
        monkeypatch.setattr(rns_bridge, "time",
                            types.SimpleNamespace(monotonic=lambda: 5.0))
        # Must not propagate — bridge stability over a publish failure.
        rns_bridge.RNSMeshtasticBridge._maybe_publish_content_id_view(self._fake())


class TestRnsConfigdirGuardDegrades:
    """QA re-review 2026-07-05: ensure_rns_client_configdir now RAISES on a
    compromised /tmp (foreign-owned dir / 'config' symlink). The gateway
    main-thread pre-init must DEGRADE (Meshtastic leg keeps serving), not
    let the raise abort start() — a local user pre-creating the dir must
    not DoS the bridge."""

    def test_configdir_raise_degrades_not_aborts(self, bridge):
        bridge.config.rns.config_dir = None  # force the canonical-dir path
        with patch("gateway._rns_bridge_connection.ReticulumPaths."
                   "ensure_rns_client_configdir",
                   side_effect=RuntimeError("foreign-owned /tmp dir")):
            # must NOT raise out of the pre-init — the security guard's
            # RuntimeError has to degrade like a wedged rnsd, not crash start()
            bridge._init_rns_main_thread()
        assert bridge._rns_pre_initialized is False  # degraded, not crashed
