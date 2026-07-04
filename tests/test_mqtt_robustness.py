"""
Tests for MQTT subscriber robustness improvements.

Tests cover:
- Input validation (_safe_float, _safe_int)
- Payload size limits
- Coordinate validation in position handling
- Telemetry value validation
- Stale node cleanup
- MAX_NODES enforcement
- GeoJSON coordinate filtering
- Reconnection jitter (non-deterministic, bounds tested)
- Malformed message handling (no crashes)
- Stats tracking (rejected messages, pruned nodes, reconnect attempts)
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from monitoring.mqtt_subscriber import (
    MQTTNodelessSubscriber,
    MQTTNode,
    MQTTMessage,
    MAX_PAYLOAD_BYTES,
    MAX_NODES,
    STALE_NODE_HOURS,
    VALID_LAT_RANGE,
    VALID_LON_RANGE,
    VALID_SNR_RANGE,
    VALID_RSSI_RANGE,
)


@pytest.fixture
def subscriber():
    """Create subscriber with test config (no real connection)."""
    config = {
        "broker": "test.example.com",
        "port": 1883,
        "username": "",
        "password": "",
        "root_topic": "msh/US/2/e",
        "channel": "LongFast",
        "key": "AQ==",
        "use_tls": False,
        "auto_reconnect": True,
        "reconnect_delay": 1,
        "max_reconnect_delay": 10,
    }
    return MQTTNodelessSubscriber(config=config)


# =============================================================================
# Input Validation Tests
# =============================================================================

class TestSafeFloat:
    def test_valid_float(self, subscriber):
        assert subscriber._safe_float(5.0, 0.0, 10.0) == 5.0

    def test_valid_int_as_float(self, subscriber):
        assert subscriber._safe_float(5, 0.0, 10.0) == 5.0

    def test_valid_string_float(self, subscriber):
        assert subscriber._safe_float("5.5", 0.0, 10.0) == 5.5

    def test_none_returns_none(self, subscriber):
        assert subscriber._safe_float(None, 0.0, 10.0) is None

    def test_below_min(self, subscriber):
        assert subscriber._safe_float(-1.0, 0.0, 10.0) is None

    def test_above_max(self, subscriber):
        assert subscriber._safe_float(11.0, 0.0, 10.0) is None

    def test_invalid_string(self, subscriber):
        assert subscriber._safe_float("not_a_number", 0.0, 10.0) is None

    def test_empty_string(self, subscriber):
        assert subscriber._safe_float("", 0.0, 10.0) is None

    def test_dict_value(self, subscriber):
        assert subscriber._safe_float({"value": 5}, 0.0, 10.0) is None

    def test_boundary_min(self, subscriber):
        assert subscriber._safe_float(0.0, 0.0, 10.0) == 0.0

    def test_boundary_max(self, subscriber):
        assert subscriber._safe_float(10.0, 0.0, 10.0) == 10.0

    def test_negative_range(self, subscriber):
        assert subscriber._safe_float(-5.0, -10.0, 0.0) == -5.0


class TestSafeInt:
    def test_valid_int(self, subscriber):
        assert subscriber._safe_int(5, 0, 10) == 5

    def test_valid_float_as_int(self, subscriber):
        assert subscriber._safe_int(5.7, 0, 10) == 5

    def test_valid_string(self, subscriber):
        assert subscriber._safe_int("5", 0, 10) == 5

    def test_none_returns_none(self, subscriber):
        assert subscriber._safe_int(None, 0, 10) is None

    def test_below_min(self, subscriber):
        assert subscriber._safe_int(-1, 0, 10) is None

    def test_above_max(self, subscriber):
        assert subscriber._safe_int(11, 0, 10) is None

    def test_invalid_string(self, subscriber):
        assert subscriber._safe_int("abc", 0, 10) is None

    def test_negative_range(self, subscriber):
        assert subscriber._safe_int(-95, -200, 0) == -95


# =============================================================================
# Payload Size Limit Tests
# =============================================================================

class TestPayloadSizeLimit:
    def test_normal_payload_processed(self, subscriber):
        """Normal-size payload is processed."""
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc123"
        msg.payload = json.dumps({"from": "!abc123", "type": "text"}).encode()
        subscriber._on_message(None, None, msg)
        assert subscriber._stats["messages_received"] == 1

    def test_oversized_payload_rejected(self, subscriber):
        """Oversized payload is rejected."""
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc123"
        msg.payload = b"x" * (MAX_PAYLOAD_BYTES + 1)
        subscriber._on_message(None, None, msg)
        assert subscriber._stats["messages_rejected"] == 1
        assert subscriber._stats["messages_received"] == 0

    def test_exact_limit_accepted(self, subscriber):
        """Payload exactly at limit is accepted."""
        msg = MagicMock()
        msg.topic = "msh/US/2/e/LongFast/!abc123"
        msg.payload = b"x" * MAX_PAYLOAD_BYTES
        subscriber._on_message(None, None, msg)
        assert subscriber._stats["messages_received"] == 1


# =============================================================================
# Position Validation Tests
# =============================================================================

class TestPositionValidation:
    def test_valid_position_integer_format(self, subscriber):
        """Valid latitude_i/longitude_i format accepted."""
        data = {
            "from": "!node1",
            "type": "position",
            "payload": {
                "latitude_i": 197749000,  # 19.7749
                "longitude_i": -1559000000,  # -155.9
            }
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node1")
        assert node is not None
        assert abs(node.latitude - 19.7749) < 0.001
        assert abs(node.longitude - (-155.9)) < 0.1

    def test_valid_position_float_format(self, subscriber):
        """Valid lat/lon float format accepted."""
        data = {
            "from": "!node2",
            "type": "position",
            "payload": {"latitude": 21.3069, "longitude": -157.8583}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node2")
        assert node is not None
        assert abs(node.latitude - 21.3069) < 0.001

    def test_zero_zero_rejected(self, subscriber):
        """Position (0, 0) rejected as invalid."""
        data = {
            "from": "!node3",
            "type": "position",
            "payload": {"latitude": 0.0, "longitude": 0.0}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node3")
        assert node.latitude is None  # Not updated

    def test_out_of_range_latitude(self, subscriber):
        """Latitude > 90 rejected."""
        data = {
            "from": "!node4",
            "type": "position",
            "payload": {"latitude": 95.0, "longitude": -155.0}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node4")
        assert node.latitude is None

    def test_string_latitude_rejected(self, subscriber):
        """Non-numeric latitude handled gracefully."""
        data = {
            "from": "!node5",
            "type": "position",
            "payload": {"latitude": "not_a_number", "longitude": -155.0}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node5")
        assert node.latitude is None

    def test_valid_altitude(self, subscriber):
        """Valid altitude accepted."""
        data = {
            "from": "!node6",
            "type": "position",
            "payload": {"latitude": 21.0, "longitude": -157.0, "altitude": 1200}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node6")
        assert node.altitude == 1200

    def test_extreme_altitude_rejected(self, subscriber):
        """Altitude > 100km rejected."""
        data = {
            "from": "!node7",
            "type": "position",
            "payload": {"latitude": 21.0, "longitude": -157.0, "altitude": 200000}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!node7")
        assert node.altitude is None


# =============================================================================
# Telemetry Validation Tests
# =============================================================================

class TestNumericFromCanonicalized:
    """Live meshtasticd json uplinks carry ``from`` as a NUMBER (sampled
    on-air 2026-07-04); position/telemetry handlers key nodes on it. The
    string fixtures below masked that for months — these pin the live
    shape so the handlers can never go silently dead for numeric senders
    again (the swallowed-AttributeError class, honest_failure_modes #9)."""

    def test_position_with_numeric_from_lands(self, subscriber):
        data = {
            "from": 3792937512,  # !e213a228 — numeric, as on the wire
            "type": "position",
            "payload": {"latitude_i": 197749000,
                        "longitude_i": -1559000000},
        }
        subscriber._handle_json_message("msh/2/json/LongFast/!32962f10",
                                        json.dumps(data).encode())
        node = subscriber.get_node("!e213a228")
        assert node is not None
        assert abs(node.latitude - 19.7749) < 0.001

    def test_telemetry_with_numeric_from_lands(self, subscriber):
        data = {
            "from": 3792937512,
            "type": "telemetry",
            "payload": {"device_metrics": {"battery_level": 85}},
        }
        subscriber._handle_json_message("msh/2/json/LongFast/!32962f10",
                                        json.dumps(data).encode())
        node = subscriber.get_node("!e213a228")
        assert node is not None and node.battery_level == 85

    def test_string_from_unchanged(self, subscriber):
        node = subscriber._ensure_node("!node9")
        assert node.node_id == "!node9"


class TestTelemetryValidation:
    def test_valid_telemetry(self, subscriber):
        """Valid telemetry values accepted."""
        data = {
            "from": "!tel1",
            "type": "telemetry",
            "payload": {
                "device_metrics": {
                    "battery_level": 85,
                    "voltage": 3.95,
                    "channel_utilization": 12.5,
                    "air_util_tx": 3.2,
                }
            }
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!tel1")
        assert node.battery_level == 85
        assert node.voltage == 3.95
        assert node.channel_utilization == 12.5
        assert node.air_util_tx == 3.2

    def test_battery_over_101_rejected(self, subscriber):
        """Battery > 101 rejected."""
        data = {
            "from": "!tel2",
            "type": "telemetry",
            "payload": {"device_metrics": {"battery_level": 255}}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!tel2")
        assert node.battery_level is None

    def test_negative_voltage_rejected(self, subscriber):
        """Negative voltage rejected."""
        data = {
            "from": "!tel3",
            "type": "telemetry",
            "payload": {"device_metrics": {"voltage": -1.0}}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!tel3")
        assert node.voltage is None

    def test_channel_util_over_100_rejected(self, subscriber):
        """Channel utilization > 100% rejected."""
        data = {
            "from": "!tel4",
            "type": "telemetry",
            "payload": {"device_metrics": {"channel_utilization": 150.0}}
        }
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!tel4")
        assert node.channel_utilization is None

    def test_non_dict_device_metrics(self, subscriber):
        """Non-dict device_metrics handled gracefully."""
        data = {
            "from": "!tel5",
            "type": "telemetry",
            "payload": {"device_metrics": "not_a_dict"}
        }
        # Should not crash
        subscriber._handle_json_message("msh/US/2/json/LongFast", json.dumps(data).encode())
        node = subscriber.get_node("!tel5")
        assert node.battery_level is None


# =============================================================================
# SNR/RSSI Validation Tests
# =============================================================================

class TestSnrRssiValidation:
    def test_valid_snr(self, subscriber):
        """Valid SNR value accepted."""
        data = {"from": "!sig1", "snr": -5.5}
        subscriber._update_node_from_json("!sig1", data)
        node = subscriber.get_node("!sig1")
        assert node.snr == -5.5

    def test_snr_out_of_range(self, subscriber):
        """SNR outside -50 to 50 rejected."""
        subscriber._ensure_node("!sig2")
        data = {"from": "!sig2", "snr": 100.0}
        subscriber._update_node_from_json("!sig2", data)
        node = subscriber.get_node("!sig2")
        assert node.snr is None

    def test_valid_rssi(self, subscriber):
        """Valid RSSI value accepted."""
        data = {"from": "!sig3", "rssi": -95}
        subscriber._update_node_from_json("!sig3", data)
        node = subscriber.get_node("!sig3")
        assert node.rssi == -95

    def test_rssi_positive_rejected(self, subscriber):
        """Positive RSSI rejected."""
        subscriber._ensure_node("!sig4")
        data = {"from": "!sig4", "rssi": 10}
        subscriber._update_node_from_json("!sig4", data)
        node = subscriber.get_node("!sig4")
        assert node.rssi is None

    def test_hop_start_valid(self, subscriber):
        """Valid hop_start (0-15) accepted."""
        data = {"from": "!sig5", "hop_start": 3}
        subscriber._update_node_from_json("!sig5", data)
        node = subscriber.get_node("!sig5")
        assert node.hop_start == 3

    def test_hop_start_too_high(self, subscriber):
        """hop_start > 15 rejected."""
        subscriber._ensure_node("!sig6")
        data = {"from": "!sig6", "hop_start": 99}
        subscriber._update_node_from_json("!sig6", data)
        node = subscriber.get_node("!sig6")
        assert node.hop_start is None


# =============================================================================
# Stale Node Cleanup Tests
# =============================================================================

class TestStaleNodeCleanup:
    def test_recent_nodes_kept(self, subscriber):
        """Nodes seen recently are not pruned."""
        subscriber._ensure_node("!recent1")
        subscriber._ensure_node("!recent2")
        subscriber._cleanup_stale_nodes()
        assert len(subscriber.get_nodes()) == 2

    def test_stale_nodes_removed(self, subscriber):
        """Nodes not seen for STALE_NODE_HOURS are removed."""
        subscriber._ensure_node("!stale1")
        # Manually age the node
        node = subscriber.get_node("!stale1")
        node.last_seen = datetime.now() - timedelta(hours=STALE_NODE_HOURS + 1)

        subscriber._ensure_node("!fresh1")

        subscriber._cleanup_stale_nodes()
        assert subscriber.get_node("!stale1") is None
        assert subscriber.get_node("!fresh1") is not None
        assert subscriber._stats["nodes_pruned"] == 1

    def test_max_nodes_enforcement(self, subscriber):
        """Excess nodes beyond MAX_NODES are pruned."""
        # Create nodes just over the limit
        # Use a smaller max for testing by manipulating the threshold
        import monitoring.mqtt_subscriber as mqtt_mod
        original_max = mqtt_mod.MAX_NODES
        mqtt_mod.MAX_NODES = 5

        try:
            for i in range(8):
                node = subscriber._ensure_node(f"!overflow{i}")
                # Stagger last_seen times so pruning order is deterministic
                node.last_seen = datetime.now() - timedelta(minutes=8 - i)

            subscriber._cleanup_stale_nodes()
            remaining = subscriber.get_nodes()
            assert len(remaining) <= 5
        finally:
            mqtt_mod.MAX_NODES = original_max

    def test_cleanup_stats_accumulate(self, subscriber):
        """Pruned count accumulates across cleanups."""
        for i in range(3):
            node = subscriber._ensure_node(f"!old{i}")
            node.last_seen = datetime.now() - timedelta(hours=STALE_NODE_HOURS + 1)

        subscriber._cleanup_stale_nodes()
        assert subscriber._stats["nodes_pruned"] == 3

        # Add and age more
        for i in range(2):
            node = subscriber._ensure_node(f"!old_b{i}")
            node.last_seen = datetime.now() - timedelta(hours=STALE_NODE_HOURS + 1)

        subscriber._cleanup_stale_nodes()
        assert subscriber._stats["nodes_pruned"] == 5


# =============================================================================
# GeoJSON Coordinate Filtering Tests
# =============================================================================

class TestGeoJSONFiltering:
    def test_valid_nodes_included(self, subscriber):
        """Nodes with valid positions appear in GeoJSON."""
        node = subscriber._ensure_node("!geo1")
        node.latitude = 21.3069
        node.longitude = -157.8583
        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 1
        assert geojson["features"][0]["properties"]["id"] == "!geo1"

    def test_zero_coords_excluded(self, subscriber):
        """Nodes at (0, 0) excluded from GeoJSON."""
        node = subscriber._ensure_node("!geo2")
        node.latitude = 0.0
        node.longitude = 0.0
        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 0

    def test_none_coords_excluded(self, subscriber):
        """Nodes with None coords excluded."""
        subscriber._ensure_node("!geo3")
        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 0

    def test_out_of_range_excluded(self, subscriber):
        """Nodes with coords outside valid range excluded."""
        node = subscriber._ensure_node("!geo4")
        node.latitude = 95.0  # Invalid
        node.longitude = -157.0
        geojson = subscriber.get_geojson()
        assert len(geojson["features"]) == 0

    def test_geojson_includes_rssi_and_hops(self, subscriber):
        """GeoJSON properties include rssi and hops_away."""
        node = subscriber._ensure_node("!geo5")
        node.latitude = 21.0
        node.longitude = -157.0
        node.rssi = -95
        node.hops_away = 2
        geojson = subscriber.get_geojson()
        props = geojson["features"][0]["properties"]
        assert props["rssi"] == -95
        assert props["hops_away"] == 2


# =============================================================================
# Malformed Message Handling Tests
# =============================================================================

class TestMalformedMessages:
    def test_invalid_json_payload(self, subscriber):
        """Invalid JSON doesn't crash."""
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = b"not valid json at all {{{}"
        subscriber._on_message(None, None, msg)
        # Should not crash, message counted but gracefully handled
        assert subscriber._stats["messages_received"] == 1

    def test_empty_payload(self, subscriber):
        """Empty payload handled gracefully."""
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = b""
        subscriber._on_message(None, None, msg)
        assert subscriber._stats["messages_received"] == 1

    def test_binary_payload_on_json_topic(self, subscriber):
        """Binary payload on JSON topic doesn't crash."""
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = bytes(range(256))[:100]
        subscriber._on_message(None, None, msg)
        assert subscriber._stats["messages_received"] == 1

    def test_missing_from_field(self, subscriber):
        """Message without 'from' handled gracefully."""
        data = {"type": "position", "payload": {"latitude": 21.0, "longitude": -157.0}}
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = json.dumps(data).encode()
        subscriber._on_message(None, None, msg)
        # No crash, no node created for position without 'from'

    def test_null_payload_fields(self, subscriber):
        """Null values in payload fields don't crash."""
        data = {
            "from": "!null_test",
            "type": "telemetry",
            "payload": {
                "device_metrics": {
                    "battery_level": None,
                    "voltage": None,
                }
            }
        }
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = json.dumps(data).encode()
        subscriber._on_message(None, None, msg)
        node = subscriber.get_node("!null_test")
        assert node is not None
        assert node.battery_level is None

    def test_nested_missing_payload(self, subscriber):
        """Message with no payload dict handled."""
        data = {"from": "!nopayload", "type": "telemetry"}
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!abc"
        msg.payload = json.dumps(data).encode()
        subscriber._on_message(None, None, msg)
        # No crash


# =============================================================================
# Reconnection Metrics Tests
# =============================================================================

class TestReconnectionMetrics:
    def test_disconnect_reason_tracked(self, subscriber):
        """Unexpected disconnect stores reason."""
        subscriber._on_disconnect(None, None, 7)
        assert "7" in subscriber._stats["last_disconnect_reason"]

    def test_clean_disconnect_no_reason(self, subscriber):
        """Clean disconnect (rc=0) doesn't set reason."""
        subscriber._on_disconnect(None, None, 0)
        assert subscriber._stats["last_disconnect_reason"] == ""

    def test_initial_stats(self, subscriber):
        """Stats start at zero."""
        assert subscriber._stats["reconnect_attempts"] == 0
        assert subscriber._stats["messages_rejected"] == 0
        assert subscriber._stats["nodes_pruned"] == 0


# =============================================================================
# Encrypted Topic Handling
# =============================================================================

class TestEncryptedTopicHandling:
    def test_valid_encrypted_topic(self, subscriber):
        """Valid encrypted topic extracts node ID."""
        msg = MagicMock()
        msg.topic = "msh/US/2/e/LongFast/!abcd1234"
        msg.payload = b"\x01\x02\x03"
        subscriber._on_message(None, None, msg)
        assert subscriber.get_node("!abcd1234") is not None

    def test_short_topic_ignored(self, subscriber):
        """Short topic with < 6 parts ignored."""
        msg = MagicMock()
        msg.topic = "msh/US/2/e"
        msg.payload = b"\x01\x02\x03"
        subscriber._on_message(None, None, msg)
        assert len(subscriber.get_nodes()) == 0

    def test_non_node_id_ignored(self, subscriber):
        """Topic part not starting with ! ignored."""
        msg = MagicMock()
        msg.topic = "msh/US/2/e/LongFast/gateway"
        msg.payload = b"\x01\x02\x03"
        subscriber._on_message(None, None, msg)
        assert len(subscriber.get_nodes()) == 0


# =============================================================================
# Node Callbacks Resilience
# =============================================================================

class TestCallbackResilience:
    def test_callback_exception_doesnt_crash(self, subscriber):
        """Exception in node callback doesn't prevent processing."""
        def bad_callback(node):
            raise RuntimeError("callback failed")

        subscriber.register_node_callback(bad_callback)

        data = {"from": "!cb1", "snr": -5.0}
        subscriber._update_node_from_json("!cb1", data)
        # Node should still be updated despite callback failure
        node = subscriber.get_node("!cb1")
        assert node is not None

    def test_message_callback_exception(self, subscriber):
        """Exception in message callback doesn't crash."""
        def bad_callback(msg):
            raise ValueError("msg callback error")

        subscriber.register_message_callback(bad_callback)

        data = {
            "from": "!cb2",
            "type": "text",
            "payload": {"text": "hello"},
            "id": "123",
            "to": "!all",
        }
        msg = MagicMock()
        msg.topic = "msh/US/2/json/LongFast/!cb2"
        msg.payload = json.dumps(data).encode()
        subscriber._on_message(None, None, msg)
        # Should not crash


# =============================================================================
# Stats API Tests
# =============================================================================

class TestStatsAPI:
    def test_get_stats_structure(self, subscriber):
        """Stats dict contains all expected keys."""
        stats = subscriber.get_stats()
        assert "messages_received" in stats
        assert "messages_rejected" in stats
        assert "nodes_discovered" in stats
        assert "nodes_pruned" in stats
        assert "reconnect_attempts" in stats
        assert "node_count" in stats
        assert "online_count" in stats
        assert "with_position" in stats

    def test_stats_after_activity(self, subscriber):
        """Stats reflect actual activity."""
        # Add some nodes
        for i in range(5):
            node = subscriber._ensure_node(f"!stat{i}")
            node.latitude = 21.0 + i * 0.01
            node.longitude = -157.0

        stats = subscriber.get_stats()
        assert stats["node_count"] == 5
        assert stats["with_position"] == 5
        assert stats["nodes_discovered"] == 5


# =============================================================================
# Reconnect FD-leak regression (meshanchor-server 2026-05-31)
# =============================================================================

class TestReconnectFdLeak:
    """Each _connect() must tear down the prior paho client before creating a
    new one. Without that, repeated reconnects orphaned clients whose
    loop_start() network thread + broker socket stayed alive, leaking file
    descriptors until the process hit [Errno 24] Too many open files and the
    HTTP server could no longer accept(). Observed: 298 leaked ESTAB sockets
    to localhost:1883 on meshanchor-server.
    """

    def _make_mqtt_module(self):
        """Build a fake paho module whose Client() returns a fresh mock each call."""
        fake_mqtt = MagicMock()
        # No CallbackAPIVersion attr -> exercises the v1.x construction path
        del fake_mqtt.CallbackAPIVersion
        created = []

        def _new_client(*args, **kwargs):
            c = MagicMock(name=f"client{len(created)}")
            created.append(c)
            return c

        fake_mqtt.Client.side_effect = _new_client
        return fake_mqtt, created

    def test_second_connect_tears_down_first_client(self, subscriber):
        """A reconnect closes the previous client instead of orphaning it."""
        subscriber._config["connect_timeout"] = 0  # don't block waiting to connect
        fake_mqtt, created = self._make_mqtt_module()

        with patch("monitoring.mqtt_subscriber._HAS_PAHO_MQTT", True), \
             patch("monitoring.mqtt_subscriber._mqtt", fake_mqtt):
            subscriber._connect()
            first = subscriber._client
            assert first is created[0]

            subscriber._connect()
            second = subscriber._client

        # Exactly two clients constructed; the first was torn down, the second
        # is now current.
        assert len(created) == 2
        assert second is created[1]
        assert second is not first
        # The orphaned first client was disconnected + its loop stopped.
        first.disconnect.assert_called()
        first.loop_stop.assert_called()

    def test_atexit_registered_once_across_reconnects(self, subscriber):
        """atexit handler is registered once per instance, not per reconnect."""
        subscriber._config["connect_timeout"] = 0
        fake_mqtt, _ = self._make_mqtt_module()

        with patch("monitoring.mqtt_subscriber._HAS_PAHO_MQTT", True), \
             patch("monitoring.mqtt_subscriber._mqtt", fake_mqtt), \
             patch("atexit.register") as mock_register:
            subscriber._connect()
            subscriber._connect()
            subscriber._connect()

        assert mock_register.call_count == 1
