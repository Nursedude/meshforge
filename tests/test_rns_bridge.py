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
         patch("gateway.rns_bridge.CLASSIFIER_AVAILABLE", False), \
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
         patch("gateway.rns_bridge.CLASSIFIER_AVAILABLE", False), \
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
        assert bridge.send_to_rns("msg") is False

    def test_returns_false_no_lxmf_source(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = None
        assert bridge.send_to_rns("msg") is False

    def test_broadcast_returns_false(self, bridge):
        bridge._connected_rns = True
        bridge._lxmf_source = MagicMock()
        # No destination hash -> broadcast
        assert bridge.send_to_rns("broadcast msg", None) is False


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
             patch("gateway.rns_bridge.CLASSIFIER_AVAILABLE", False), \
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
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="test msg",
        )

        with patch.object(bridge, 'send_to_rns', return_value=True):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['messages_mesh_to_rns'] == 1
        bridge.health.record_message_sent.assert_called_once_with("mesh_to_rns")

    def test_failure_broadcast_no_error(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="broadcast",
            is_broadcast=True,
        )

        with patch.object(bridge, 'send_to_rns', return_value=False):
            bridge._process_mesh_to_rns(msg)

        assert bridge.stats['errors'] == 0

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
        bridge.health.record_message_failed.assert_called_once_with("mesh_to_rns", requeued=True)

    def test_prefix_includes_source_id(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="meshtastic", source_id="!aabb0042",
            destination_id=None, content="hello",
        )
        sent_content = None

        def capture_send(content, dest_hash=None):
            nonlocal sent_content
            sent_content = content
            return True

        with patch.object(bridge, 'send_to_rns', side_effect=capture_send):
            bridge._process_mesh_to_rns(msg)

        assert sent_content.startswith("[Mesh:0042] ")


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
        bridge.health.record_message_failed.assert_called_once_with("rns_to_mesh", requeued=True)

    def test_prefix_includes_rns_source(self, bridge):
        from gateway.rns_bridge import BridgedMessage
        msg = BridgedMessage(
            source_network="rns", source_id="abcdef01",
            destination_id=None, content="hello",
        )
        sent_content = None

        def capture_send(content, destination=None, channel=0):
            nonlocal sent_content
            sent_content = content
            return True

        with patch.object(bridge, 'send_to_meshtastic', side_effect=capture_send):
            bridge._process_rns_to_mesh(msg)

        assert sent_content.startswith("[RNS:abcd] ")


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
             patch.object(bridge, '_init_rns_main_thread'):
            bridge.start()
        assert bridge._running is True
        assert bridge.stats['start_time'] is not None

    def test_start_when_already_running(self, bridge):
        bridge._running = True
        result = bridge.start()
        assert result is True

    def test_start_returns_true(self, bridge):
        with patch.object(bridge, '_start_websocket_server'), \
             patch.object(bridge, '_init_rns_main_thread'):
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
             patch.object(bridge, '_init_rns_main_thread'):
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
        bridge._rns_pre_initialized = False
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ('RNS', 'LXMF'):
                raise ImportError("No module")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            saved_rns = sys.modules.pop('RNS', None)
            saved_lxmf = sys.modules.pop('LXMF', None)
            try:
                bridge._connect_rns()
            finally:
                if saved_rns:
                    sys.modules['RNS'] = saved_rns
                if saved_lxmf:
                    sys.modules['LXMF'] = saved_lxmf

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

        with patch('gateway.rns_bridge._RNS_mod', mock_rns), \
             patch('gateway.rns_bridge._LXMF_mod', mock_lxmf), \
             patch('gateway.rns_bridge._HAS_RNS', True), \
             patch('gateway.rns_bridge._HAS_LXMF', True):
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
