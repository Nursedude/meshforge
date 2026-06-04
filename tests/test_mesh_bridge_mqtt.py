"""
Tests for mesh_bridge.py: MQTT mode and persistent queue integration.

Tests Features #1 (MQTT mode) and #4 (persistent SQLite queue).
"""

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


@pytest.fixture
def tmp_queue_dir(tmp_path):
    """Temporary directory for queue databases."""
    return tmp_path / "mesh_bridge_queues"


@pytest.fixture
def mock_config(tmp_queue_dir):
    """Create a mock GatewayConfig for testing."""
    from gateway.config import (
        GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig,
    )

    config = GatewayConfig()
    config.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(
            host="localhost",
            port=4403,
            preset="LONG_FAST",
            name="longfast",
            use_mqtt=False,
        ),
        secondary=MeshtasticConfig(
            host="localhost",
            port=4404,
            preset="SHORT_TURBO",
            name="shortturbo",
            use_mqtt=False,
        ),
        direction="bidirectional",
        dedup_window_sec=60,
        add_prefix=True,
        prefix_format="[{source_preset}] ",
    )
    return config


@pytest.fixture
def mqtt_config(tmp_queue_dir):
    """Create a mock config with MQTT mode enabled."""
    from gateway.config import (
        GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig,
    )

    config = GatewayConfig()
    config.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(
            host="localhost",
            port=4403,
            preset="LONG_FAST",
            name="longfast",
            use_mqtt=True,
            mqtt_broker="localhost",
            mqtt_port=1883,
            mqtt_channel="LongFast",
            mqtt_region="US",
            http_port=9443,
        ),
        secondary=MeshtasticConfig(
            host="localhost",
            port=4404,
            preset="SHORT_TURBO",
            name="shortturbo",
            use_mqtt=True,
            mqtt_broker="localhost",
            mqtt_port=1883,
            mqtt_channel="ShortTurbo",
            mqtt_region="US",
            http_port=9444,
        ),
    )
    return config


class TestBridgedMeshMessage:
    """Tests for BridgedMeshMessage serialization."""

    def test_to_payload(self):
        from gateway.mesh_bridge import BridgedMeshMessage

        msg = BridgedMeshMessage(
            source_preset="LONG_FAST",
            source_id="!aabb1234",
            destination_id="!ccdd5678",
            content="Hello from LONG_FAST",
            channel=0,
            is_broadcast=False,
        )

        payload = msg.to_payload()
        assert payload["source_preset"] == "LONG_FAST"
        assert payload["source_id"] == "!aabb1234"
        assert payload["content"] == "Hello from LONG_FAST"
        assert payload["text"] == "Hello from LONG_FAST"
        assert payload["from"] == "!aabb1234"
        assert payload["to"] == "!ccdd5678"

    def test_from_payload_roundtrip(self):
        from gateway.mesh_bridge import BridgedMeshMessage

        original = BridgedMeshMessage(
            source_preset="SHORT_TURBO",
            source_id="!11223344",
            destination_id=None,
            content="Broadcast test",
            channel=1,
            is_broadcast=True,
        )

        payload = original.to_payload()
        restored = BridgedMeshMessage.from_payload(payload)

        assert restored.source_preset == original.source_preset
        assert restored.source_id == original.source_id
        assert restored.content == original.content
        assert restored.channel == original.channel
        assert restored.is_broadcast == original.is_broadcast

    def test_dedup_key(self):
        from gateway.mesh_bridge import BridgedMeshMessage

        msg1 = BridgedMeshMessage(
            source_preset="LONG_FAST",
            source_id="!aabb1234",
            destination_id=None,
            content="Hello",
        )
        msg2 = BridgedMeshMessage(
            source_preset="LONG_FAST",
            source_id="!aabb1234",
            destination_id=None,
            content="Hello",
        )
        # Same content = same dedup key
        assert msg1.dedup_key == msg2.dedup_key

        msg3 = BridgedMeshMessage(
            source_preset="LONG_FAST",
            source_id="!aabb1234",
            destination_id=None,
            content="Different",
        )
        # Different content = different dedup key
        assert msg1.dedup_key != msg3.dedup_key


class TestMQTTMeshInterface:
    """Tests for MQTTMeshInterface."""

    @patch('gateway.mesh_bridge._HAS_PAHO_MQTT', False)
    def test_connect_without_paho(self):
        from gateway.mesh_bridge import MQTTMeshInterface
        from gateway.config import MeshtasticConfig

        config = MeshtasticConfig(
            use_mqtt=True, mqtt_broker="localhost", mqtt_port=1883
        )
        iface = MQTTMeshInterface(
            config=config,
            name="test",
            message_callback=lambda p: None,
            stop_event=threading.Event(),
        )

        assert iface.connect() is False

    def test_node_id_to_num(self):
        from gateway.mesh_bridge import MQTTMeshInterface

        assert MQTTMeshInterface._node_id_to_num("!aabbccdd") == 0xaabbccdd
        assert MQTTMeshInterface._node_id_to_num("!00000001") == 1
        assert MQTTMeshInterface._node_id_to_num("") is None
        assert MQTTMeshInterface._node_id_to_num(None) is None

    def test_dedup(self):
        from gateway.mesh_bridge import MQTTMeshInterface
        from gateway.config import MeshtasticConfig

        config = MeshtasticConfig(use_mqtt=True)
        iface = MQTTMeshInterface(
            config=config,
            name="test",
            message_callback=lambda p: None,
            stop_event=threading.Event(),
        )

        # First time: not duplicate
        assert iface._is_duplicate("msg-001") is False
        # Second time: duplicate
        assert iface._is_duplicate("msg-001") is True
        # Different ID: not duplicate
        assert iface._is_duplicate("msg-002") is False


class TestMeshtasticPresetBridgePersistence:
    """Tests for persistent queue integration."""

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_bridge_creates_queues(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        queue_dir = tmp_path / ".config" / "meshforge" / "mesh_bridge_queues"
        assert queue_dir.exists()
        assert (queue_dir / "p2s.db").exists()
        assert (queue_dir / "s2p.db").exists()

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_process_receive_enqueues(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        packet = {
            'fromId': '!aabb1234',
            'toId': '!ffffffff',
            'channel': 0,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'Test message',
            },
        }

        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        # Check message was enqueued
        depth = bridge._primary_to_secondary.get_queue_depth()
        assert depth == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_duplicate_suppression(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        packet = {
            'fromId': '!aabb1234',
            'toId': '!ffffffff',
            'channel': 0,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'Duplicate test',
            },
        }

        # First receive
        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )
        # Second receive (same content from same sender)
        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        # Only one message should be queued (second was deduplicated)
        depth = bridge._primary_to_secondary.get_queue_depth()
        assert depth == 1
        assert bridge.stats['duplicates_suppressed'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_exclude_filter(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.exclude_filter = r"^SPAM"
        bridge = MeshtasticPresetBridge(config=mock_config)

        packet = {
            'fromId': '!1234',
            'toId': '!ffffffff',
            'channel': 0,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'SPAM message here',
            },
        }

        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        # Message should be filtered out
        assert bridge._primary_to_secondary.get_queue_depth() == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_message_filter(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.message_filter = r"IMPORTANT"
        bridge = MeshtasticPresetBridge(config=mock_config)

        # This message doesn't match the filter
        packet = {
            'fromId': '!1234',
            'toId': '!ffffffff',
            'channel': 0,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'Regular message',
            },
        }

        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        assert bridge._primary_to_secondary.get_queue_depth() == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_non_text_messages_ignored(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        packet = {
            'fromId': '!1234',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'POSITION_APP',
                'payload': b'\x00\x01',
            },
        }

        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        assert bridge._primary_to_secondary.get_queue_depth() == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_dm_gets_high_priority(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        packet = {
            'fromId': '!aabb1234',
            'toId': '!ccdd5678',  # Non-broadcast = DM
            'channel': 0,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'Private message',
            },
        }

        bridge._process_receive(
            packet, "primary", "secondary",
            bridge._primary_to_secondary,
        )

        messages = bridge._primary_to_secondary.get_pending()
        assert len(messages) == 1
        # DMs get HIGH priority
        from gateway.message_queue import MessagePriority
        assert messages[0].priority == MessagePriority.HIGH

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_get_status_includes_queue(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)

        status = bridge.get_status()
        assert 'queue' in status
        assert 'p2s_pending' in status['queue']
        assert 's2p_pending' in status['queue']

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_get_status_includes_mode(self, mock_home, tmp_path, mqtt_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mqtt_config)

        status = bridge.get_status()
        assert status['primary']['mode'] == 'mqtt'
        assert status['secondary']['mode'] == 'mqtt'


class TestChannelAllowList:
    """mesh_bridge.channels — channel-index allow-list (both directions,
    all connection types). Empty = bridge everything (backward compat)."""

    @staticmethod
    def _packet(channel=None, text="ST chat"):
        pkt = {
            'fromId': '!b03bb70c',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': text,
            },
        }
        if channel is not None:
            pkt['channel'] = channel
        return pkt

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_disallowed_channel_dropped_and_counted(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.channels = [2]
        bridge = MeshtasticPresetBridge(config=mock_config)

        # ch0 text from the secondary must NOT reach the primary's ch0
        bridge._process_receive(
            self._packet(channel=0), "secondary", "primary",
            bridge._secondary_to_primary,
        )

        assert bridge._secondary_to_primary.get_queue_depth() == 0
        assert bridge.stats['channel_filtered'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_allowed_channel_passes(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.channels = [2]
        bridge = MeshtasticPresetBridge(config=mock_config)

        bridge._process_receive(
            self._packet(channel=2), "secondary", "primary",
            bridge._secondary_to_primary,
        )

        assert bridge._secondary_to_primary.get_queue_depth() == 1
        assert bridge.stats['channel_filtered'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_empty_list_bridges_all_channels(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        assert mock_config.mesh_bridge.channels == []  # dataclass default
        bridge = MeshtasticPresetBridge(config=mock_config)

        for ch, text in [(0, "ch0 text"), (5, "ch5 text")]:
            bridge._process_receive(
                self._packet(channel=ch, text=text), "primary", "secondary",
                bridge._primary_to_secondary,
            )

        assert bridge._primary_to_secondary.get_queue_depth() == 2
        assert bridge.stats['channel_filtered'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_missing_channel_key_defaults_to_zero(self, mock_home, tmp_path, mock_config):
        """Protobuf omits default-0 fields: a packet with no 'channel' key
        is channel 0 and must be filtered when 0 is not allow-listed."""
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.channels = [2]
        bridge = MeshtasticPresetBridge(config=mock_config)

        bridge._process_receive(
            self._packet(channel=None), "secondary", "primary",
            bridge._secondary_to_primary,
        )

        assert bridge._secondary_to_primary.get_queue_depth() == 0
        assert bridge.stats['channel_filtered'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_filter_applies_in_both_directions(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        mock_config.mesh_bridge.channels = [2]
        bridge = MeshtasticPresetBridge(config=mock_config)

        bridge._process_receive(
            self._packet(channel=1, text="LF ch1"), "primary", "secondary",
            bridge._primary_to_secondary,
        )

        assert bridge._primary_to_secondary.get_queue_depth() == 0
        assert bridge.stats['channel_filtered'] == 1


class TestMQTTTopicShapes:
    """Issue #34 mirror for MQTTMeshInterface: meshtasticd 2.7.x publishes
    region-less topics (msh/2/json/...); older builds include the region.
    _on_connect must subscribe to BOTH shapes."""

    @staticmethod
    def _iface(region):
        from gateway.mesh_bridge import MQTTMeshInterface
        from gateway.config import MeshtasticConfig

        config = MeshtasticConfig(
            use_mqtt=True, mqtt_channel="meshforge", mqtt_region=region,
        )
        return MQTTMeshInterface(
            config=config,
            name="test",
            message_callback=lambda p: None,
            stop_event=threading.Event(),
        )

    def test_subscribes_both_shapes_when_region_set(self):
        iface = self._iface(region="US")
        client = MagicMock()

        iface._on_connect(client, None, None, 0)

        topics = [c.args[0] for c in client.subscribe.call_args_list]
        assert "msh/2/json/meshforge/#" in topics          # region-less (2.7.x)
        assert "msh/US/2/json/meshforge/#" in topics       # region-full (older)
        assert "msh/2/e/meshforge/#" in topics
        assert "msh/US/2/e/meshforge/#" in topics

    def test_region_less_only_when_region_empty(self):
        iface = self._iface(region="")
        client = MagicMock()

        iface._on_connect(client, None, None, 0)

        topics = [c.args[0] for c in client.subscribe.call_args_list]
        assert "msh/2/json/meshforge/#" in topics
        # No malformed double-slash topic from an empty region segment
        assert not any("//" in t for t in topics)

    def test_no_subscribe_on_failed_connect(self):
        iface = self._iface(region="US")
        client = MagicMock()

        iface._on_connect(client, None, None, 5)  # rc != 0

        assert not client.subscribe.called
        assert iface._connected is False


class TestForwardBroadcastOmitsDestination:
    """meshtastic's SerialInterface/TCPInterface hard-exit the process
    (our_exit) when sendText gets destinationId=None — broadcasts must
    OMIT the kwarg so the interface's own BROADCAST_ADDR default applies."""

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_broadcast_forward_omits_destination_id(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge, BridgedMeshMessage

        bridge = MeshtasticPresetBridge(config=mock_config)
        iface = MagicMock()

        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO",
            source_id="!b03bb70c",
            destination_id="!ffffffff",
            content="hello LF",
            channel=2,
            is_broadcast=True,
        )
        assert bridge._forward_message(msg, iface, True, "primary") is True

        _, kwargs = iface.sendText.call_args
        assert 'destinationId' not in kwargs
        assert kwargs['channelIndex'] == 2

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_dm_forward_keeps_destination_id(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge, BridgedMeshMessage

        bridge = MeshtasticPresetBridge(config=mock_config)
        iface = MagicMock()

        msg = BridgedMeshMessage(
            source_preset="LONG_FAST",
            source_id="!32962f10",
            destination_id="!b03bb70c",
            content="dm",
            channel=2,
            is_broadcast=False,
        )
        assert bridge._forward_message(msg, iface, True, "secondary") is True

        _, kwargs = iface.sendText.call_args
        assert kwargs['destinationId'] == "!b03bb70c"
