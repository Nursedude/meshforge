"""
Tests for gateway.mqtt_bridge_handler.MQTTBridgeHandler.

Focus: publish_to_mqtt's payload shape, specifically the directed-downlink
path where payload['destination'] is a !xxxxxxxx Meshtastic node id and
should surface as a numeric 'to' field in the outbound MQTT JSON.

Run: python3 -m pytest tests/test_mqtt_bridge_handler.py -v
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.mqtt_bridge_handler import MQTTBridgeHandler


def _make_handler(region: str = "") -> MQTTBridgeHandler:
    """Construct a handler wired up only enough for publish_to_mqtt."""
    config = MagicMock()
    config.mqtt_bridge.root_topic = "msh"
    config.mqtt_bridge.region = region
    config.mqtt_bridge.channel = "meshforge"
    handler = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    handler.config = config
    handler._connected = True
    handler._client = MagicMock()
    handler._client.publish.return_value = MagicMock(rc=0)
    handler._mqtt_lock = MagicMock()
    handler._mqtt_lock.__enter__ = lambda _self: None
    handler._mqtt_lock.__exit__ = lambda _self, *a: False
    return handler


def _published_body(handler: MQTTBridgeHandler) -> dict:
    """Extract the JSON body from the handler's last .publish call."""
    args, _ = handler._client.publish.call_args
    return json.loads(args[1])


class TestPublishToMqttDirectedReply:
    """publish_to_mqtt threads payload['destination'] into an MQTT 'to' field."""

    def test_no_destination_omits_to_field(self):
        handler = _make_handler()
        assert handler.publish_to_mqtt(
            {"message": "hello", "channel": 2, "source_id": "abc"}
        ) is True

        body = _published_body(handler)
        assert "to" not in body
        assert body["payload"]["text"] == "hello"

    def test_hex_destination_converts_to_numeric_to(self):
        handler = _make_handler()
        assert handler.publish_to_mqtt(
            {
                "message": "roger",
                "channel": 2,
                "source_id": "abc",
                "destination": "!ebfa1b11",
            }
        ) is True

        body = _published_body(handler)
        assert body["to"] == int("ebfa1b11", 16)

    def test_null_destination_omits_to_field(self):
        """A None destination is treated as broadcast; no 'to' leaks in."""
        handler = _make_handler()
        assert handler.publish_to_mqtt(
            {
                "message": "broadcast",
                "channel": 2,
                "source_id": "abc",
                "destination": None,
            }
        ) is True

        body = _published_body(handler)
        assert "to" not in body

    def test_malformed_destination_falls_back_to_broadcast(self):
        """Bad hex is logged and the message still publishes as broadcast."""
        handler = _make_handler()
        assert handler.publish_to_mqtt(
            {
                "message": "keepgoing",
                "channel": 2,
                "source_id": "abc",
                "destination": "!notvalidhex",
            }
        ) is True

        body = _published_body(handler)
        assert "to" not in body
        assert body["payload"]["text"] == "keepgoing"

    def test_empty_message_rejected(self):
        handler = _make_handler()
        assert handler.publish_to_mqtt({"message": "", "channel": 0}) is False
        handler._client.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Self-echo filter — _bridge_text_message must drop the gateway's own TX
# rebroadcast that meshtasticd republishes via MQTT (otherwise it loops back
# to RNS as a fresh Mesh→RNS message).
# ---------------------------------------------------------------------------


def _make_bridge_handler(own_id: str = "") -> MQTTBridgeHandler:
    """Construct a handler with just enough wiring for _bridge_text_message."""
    config = MagicMock()
    config.meshtastic.gateway_node_id = own_id
    handler = MQTTBridgeHandler.__new__(MQTTBridgeHandler)
    handler.config = config
    handler._message_queue = MagicMock()
    handler._stats_lock = MagicMock()
    handler._stats_lock.__enter__ = lambda _self: None
    handler._stats_lock.__exit__ = lambda _self, *a: False
    handler.stats = {"errors": 0}
    handler._should_bridge = None
    handler._message_callback = None
    handler._is_duplicate = MagicMock(return_value=False)
    return handler


class TestSelfEchoFilter:
    """gateway_node_id, when set, drops self-echoed inbound MQTT JSON."""

    def test_self_sender_dropped(self):
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "sender": "!ebfa1b11",
                "to": 0xFFFFFFFF,
                "payload": {"text": "echo"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        handler._message_queue.put_nowait.assert_not_called()

    def test_other_sender_passes_through(self):
        handler = _make_bridge_handler(own_id="!ebfa1b11")
        handler._bridge_text_message(
            {
                "sender": "!aabb0042",
                "to": 0xFFFFFFFF,
                "payload": {"text": "real msg"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!aabb0042",
        )
        handler._message_queue.put_nowait.assert_called_once()

    def test_filter_disabled_when_unset(self):
        """gateway_node_id='' (default) means no filter; everything passes."""
        handler = _make_bridge_handler(own_id="")
        handler._bridge_text_message(
            {
                "sender": "!ebfa1b11",
                "to": 0xFFFFFFFF,
                "payload": {"text": "would-be-echo"},
                "channel": 2,
            },
            topic="msh/US/2/json/meshforge/!ebfa1b11",
        )
        # No filter configured → message goes through (preserves legacy behavior)
        handler._message_queue.put_nowait.assert_called_once()
