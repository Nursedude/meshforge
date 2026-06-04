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


class TestEchoLoopInvariant:
    """Cross-gateway echo amplification guard (cf. 1a34699; live on moc
    2026-06-03): bridge-tagged content NEVER crosses mesh_bridge, and our
    own forwards carry a [Mesh: tag so every gateway refuses them too."""

    @staticmethod
    def _packet(text, channel=2):
        return {
            'fromId': '!b03bb70c',
            'toId': '!ffffffff',
            'channel': channel,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': text},
        }

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_rns_tagged_dropped_from_secondary(self, mock_home, tmp_path, mock_config):
        """The exact moc loop shape: a peer gateway's [RNS:...] injection
        heard on the secondary RF segment must not cross back."""
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)
        bridge._process_receive(
            self._packet("[RNS:hawaii_gaz] [SHORT_TURBO] st-lf test"),
            "secondary", "primary", bridge._secondary_to_primary,
        )

        assert bridge._secondary_to_primary.get_queue_depth() == 0
        assert bridge.stats['already_bridged_dropped'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_mesh_tagged_dropped_from_primary(self, mock_home, tmp_path, mock_config):
        """Our own ST->LF forward republished via MQTT must not ping-pong
        back to the secondary."""
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)
        bridge._process_receive(
            self._packet("[Mesh:SHORT_TURBO] hello"),
            "primary", "secondary", bridge._primary_to_secondary,
        )

        assert bridge._primary_to_secondary.get_queue_depth() == 0
        assert bridge.stats['already_bridged_dropped'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_raw_text_still_crosses(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=mock_config)
        bridge._process_receive(
            self._packet("plain user chatter"),
            "secondary", "primary", bridge._secondary_to_primary,
        )

        assert bridge._secondary_to_primary.get_queue_depth() == 1
        assert bridge.stats['already_bridged_dropped'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_default_prefix_marks_forward_as_bridged(self, mock_home, tmp_path):
        """Round-trip closure: with the DEFAULT prefix, a forwarded message
        is itself is_already_bridged — so this bridge (and every gateway)
        drops it on any re-RX. This is the structural loop-breaker."""
        mock_home.return_value = tmp_path
        from gateway.config import GatewayConfig, MeshtasticBridgeConfig
        from gateway.mesh_bridge import MeshtasticPresetBridge, BridgedMeshMessage
        from gateway.base_handler import is_already_bridged

        config = GatewayConfig()
        config.mesh_bridge = MeshtasticBridgeConfig(enabled=True)
        assert config.mesh_bridge.prefix_format.startswith("[Mesh:")

        bridge = MeshtasticPresetBridge(config=config)
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

        sent_text = iface.sendText.call_args[0][0]
        assert sent_text.startswith("[Mesh:SHORT_TURBO] ")
        assert is_already_bridged(sent_text)


class TestDownlinkInjectionWiring:
    """mesh_bridge primary-leg true-origin downlink injection (default off)."""

    def _bridge(self, tmp_path, mock_config, injection_mode=None, psk="QQ=="):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        if injection_mode:
            mock_config.mesh_bridge.primary.injection_mode = injection_mode
            mock_config.mesh_bridge.primary.downlink_psk = psk
            mock_config.mesh_bridge.primary.mqtt_channel = "meshforge"
        return MeshtasticPresetBridge(config=mock_config)

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_no_injector_by_default(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        bridge = self._bridge(tmp_path, mock_config)
        assert bridge._primary_downlink is None

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_downlink_mode_without_psk_falls_back(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        mock_config.mesh_bridge.primary.injection_mode = "downlink"
        mock_config.mesh_bridge.primary.downlink_psk = ""
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=mock_config)
        assert bridge._primary_downlink is None

    def test_node_id_to_num(self):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        f = MeshtasticPresetBridge._node_id_to_num
        assert f("!ddfb8065") == 0xDDFB8065
        assert f("0xddfb8065") == 0xDDFB8065
        assert f(0xDDFB8065) == 0xDDFB8065
        assert f(None) is None
        assert f("not-hex") is None

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_broadcast_uses_downlink_when_injector_present(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        bridge = self._bridge(tmp_path, mock_config)
        injector = MagicMock()
        injector.inject.return_value = True
        bridge._primary_downlink = injector
        iface = MagicMock()

        from gateway.mesh_bridge import BridgedMeshMessage
        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!ddfb8065",
            destination_id="!ffffffff", content="hi", channel=2, is_broadcast=True,
        )
        assert bridge._forward_message(msg, iface, True, "primary") is True
        injector.inject.assert_called_once()
        # RAW content injected (no [Mesh:...] tag — attribution names the sender)
        assert injector.inject.call_args[0][0] == "hi"
        # origin passed as the true source node number
        assert injector.inject.call_args[0][1] == 0xDDFB8065
        # toradio path NOT used when downlink succeeds
        assert not iface.sendText.called
        assert bridge.stats['downlink_injected'] == 1

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_downlink_strips_tag_but_fallback_keeps_it(self, mock_home, tmp_path, mock_config):
        """Downlink injects raw text (clean display); when downlink FAILS the
        toradio fallback still carries the [Mesh: loop-guard tag."""
        mock_home.return_value = tmp_path
        mock_config.mesh_bridge.add_prefix = True
        mock_config.mesh_bridge.prefix_format = "[Mesh:{source_preset}] "
        bridge = self._bridge(tmp_path, mock_config)
        injector = MagicMock()
        injector.inject.return_value = False  # force fallback
        bridge._primary_downlink = injector
        iface = MagicMock()

        from gateway.mesh_bridge import BridgedMeshMessage
        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!ddfb8065",
            destination_id="!ffffffff", content="hi", channel=2, is_broadcast=True,
        )
        bridge._forward_message(msg, iface, True, "primary")
        # downlink was attempted with RAW text
        assert injector.inject.call_args[0][0] == "hi"
        # fallback toradio carried the TAGGED text (loop guard intact)
        assert iface.sendText.call_args[0][0] == "[Mesh:SHORT_TURBO] hi"

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_downlink_failure_falls_back_to_sendtext(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        bridge = self._bridge(tmp_path, mock_config)
        injector = MagicMock()
        injector.inject.return_value = False  # broker down, etc.
        bridge._primary_downlink = injector
        iface = MagicMock()

        from gateway.mesh_bridge import BridgedMeshMessage
        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!ddfb8065",
            destination_id="!ffffffff", content="hi", channel=2, is_broadcast=True,
        )
        assert bridge._forward_message(msg, iface, True, "primary") is True
        # message NOT dropped — toradio sendText carried it
        assert iface.sendText.called
        assert bridge.stats['downlink_injected'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_secondary_leg_never_downlinks(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        bridge = self._bridge(tmp_path, mock_config)
        bridge._primary_downlink = MagicMock()  # exists, but dest is secondary
        iface = MagicMock()

        from gateway.mesh_bridge import BridgedMeshMessage
        msg = BridgedMeshMessage(
            source_preset="LONG_FAST", source_id="!32962f10",
            destination_id="!ffffffff", content="hi", channel=2, is_broadcast=True,
        )
        bridge._forward_message(msg, iface, True, "secondary")
        assert not bridge._primary_downlink.inject.called
        assert iface.sendText.called

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_dm_does_not_downlink(self, mock_home, tmp_path, mock_config):
        """Downlink is a channel inject (broadcast); DMs keep the toradio path."""
        mock_home.return_value = tmp_path
        bridge = self._bridge(tmp_path, mock_config)
        bridge._primary_downlink = MagicMock()
        iface = MagicMock()

        from gateway.mesh_bridge import BridgedMeshMessage
        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!ddfb8065",
            destination_id="!32962f10", content="dm", channel=2, is_broadcast=False,
        )
        bridge._forward_message(msg, iface, True, "primary")
        assert not bridge._primary_downlink.inject.called
        assert iface.sendText.called


class TestBroadcastDetectionShapes:
    """meshtastic emits broadcast destination in several shapes; all must be
    detected as broadcast or forwards go out as DMs and skip the
    broadcast-only downlink path (moc canary regression, 2026-06-03)."""

    @patch('gateway.mesh_bridge.get_real_user_home')
    def _bcast(self, to_field, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=mock_config)
        pkt = {
            'fromId': '!ddfb8065', 'channel': 0,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': 'hi'},
        }
        pkt.update(to_field)
        bridge._process_receive(pkt, "secondary", "primary",
                                bridge._secondary_to_primary)
        msgs = bridge._secondary_to_primary.get_pending()
        assert len(msgs) == 1
        from gateway.mesh_bridge import BridgedMeshMessage
        return BridgedMeshMessage.from_payload(msgs[0].payload)

    def test_caret_all_is_broadcast(self, tmp_path, mock_config):
        msg = self._bcast({'toId': '^all'}, tmp_path=tmp_path, mock_config=mock_config)
        assert msg.is_broadcast is True

    def test_hex_ffffffff_is_broadcast(self, tmp_path, mock_config):
        msg = self._bcast({'toId': '!ffffffff'}, tmp_path=tmp_path, mock_config=mock_config)
        assert msg.is_broadcast is True

    def test_numeric_to_is_broadcast(self, tmp_path, mock_config):
        msg = self._bcast({'to': 0xFFFFFFFF}, tmp_path=tmp_path, mock_config=mock_config)
        assert msg.is_broadcast is True

    def test_real_dm_is_not_broadcast(self, tmp_path, mock_config):
        msg = self._bcast({'toId': '!32962f10', 'to': 0x32962f10},
                          tmp_path=tmp_path, mock_config=mock_config)
        assert msg.is_broadcast is False


class TestNodeInfoInjection:
    """Teach the primary radio a bridged origin's NAME (once) so :9443 shows
    'moc2: ...' not '!ddfb8065: ...'."""

    @patch('gateway.mesh_bridge.get_real_user_home')
    def _bridge_with_injector_and_nodedb(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=mock_config)
        injector = MagicMock()
        injector.inject.return_value = True
        injector.inject_nodeinfo.return_value = True
        bridge._primary_downlink = injector
        # secondary (serial) interface carries the origin's NodeInfo
        iface = MagicMock()
        iface.nodes = {"!ddfb8065": {"user": {
            "longName": "meshforge moc2", "shortName": "moc2", "hwModel": "PORTDUINO"}}}
        bridge._secondary_interface = iface
        return bridge, injector

    def _bcast_msg(self):
        from gateway.mesh_bridge import BridgedMeshMessage
        return BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!ddfb8065",
            destination_id="!ffffffff", content="hi", channel=2, is_broadcast=True,
        )

    def test_nodeinfo_injected_before_text(self, tmp_path, mock_config):
        bridge, injector = self._bridge_with_injector_and_nodedb(
            tmp_path=tmp_path, mock_config=mock_config)
        bridge._forward_message(self._bcast_msg(), MagicMock(), True, "primary")
        injector.inject_nodeinfo.assert_called_once()
        args = injector.inject_nodeinfo.call_args
        assert args[0][0] == 0xDDFB8065
        assert args[0][1] == "meshforge moc2"
        assert args[0][2] == "moc2"

    def test_nodeinfo_only_sent_once_per_origin(self, tmp_path, mock_config):
        bridge, injector = self._bridge_with_injector_and_nodedb(
            tmp_path=tmp_path, mock_config=mock_config)
        for _ in range(3):
            bridge._forward_message(self._bcast_msg(), MagicMock(), True, "primary")
        assert injector.inject_nodeinfo.call_count == 1
        assert injector.inject.call_count == 3  # text every time

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_no_nodeinfo_when_name_unknown_retries_later(self, mock_home, tmp_path, mock_config):
        mock_home.return_value = tmp_path
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=mock_config)
        injector = MagicMock()
        injector.inject.return_value = True
        bridge._primary_downlink = injector
        bridge._secondary_interface = MagicMock(nodes={})  # name not learned yet
        bridge._forward_message(self._bcast_msg(), MagicMock(), True, "primary")
        # text still flows; nodeinfo skipped and NOT marked sent (retry later)
        assert not injector.inject_nodeinfo.called
        assert 0xDDFB8065 not in bridge._nodeinfo_sent


class TestDualPathDedupRegistration:
    """Successful primary-leg broadcast forwards register their content in
    the process-wide RecentRfTxRegistry so the rns_bridge R→M path can
    suppress the peer-relay duplicate of the same content (dual-path dedup,
    2026-06-04). DMs and failed forwards must not register."""

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _bridge(self, mock_home, tmp_path, mock_config):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        mock_home.return_value = tmp_path
        return MeshtasticPresetBridge(config=mock_config)

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_primary_broadcast_registers_content(self, mock_home, tmp_path,
                                                 mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        from gateway.mesh_bridge import BridgedMeshMessage
        bridge = self._bridge(mock_home, tmp_path, mock_config)
        bridge._primary_interface = MagicMock()
        bridge._primary_connected = True

        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!b03bb70c",
            destination_id="!ffffffff", content="hello LF",
            channel=2, is_broadcast=True,
        )
        assert bridge._send_to_primary(msg.to_payload()) is True
        # Raw content registered — and the tagged copy matches it too.
        assert reg.seen_within("hello LF", 60.0)
        assert reg.seen_within("[RNS:xxxx] hello LF", 60.0)

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_primary_dm_does_not_register(self, mock_home, tmp_path,
                                          mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        from gateway.mesh_bridge import BridgedMeshMessage
        bridge = self._bridge(mock_home, tmp_path, mock_config)
        bridge._primary_interface = MagicMock()
        bridge._primary_connected = True

        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!b03bb70c",
            destination_id="!32962f10", content="private dm",
            channel=2, is_broadcast=False,
        )
        assert bridge._send_to_primary(msg.to_payload()) is True
        assert not reg.seen_within("private dm", 60.0)

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_failed_forward_does_not_register(self, mock_home, tmp_path,
                                              mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        from gateway.mesh_bridge import BridgedMeshMessage
        bridge = self._bridge(mock_home, tmp_path, mock_config)
        bridge._primary_interface = None          # not connected → forward fails
        bridge._primary_connected = False

        msg = BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!b03bb70c",
            destination_id="!ffffffff", content="never went out",
            channel=2, is_broadcast=True,
        )
        assert bridge._send_to_primary(msg.to_payload()) is False
        assert not reg.seen_within("never went out", 60.0)


class TestSymmetricDualPathSuppression:
    """mesh_bridge's primary forward suppresses when the rns_bridge relay
    copy already went out (it registered on TX) — closes the race direction
    the one-way check missed (live 2026-06-04: RNS beat serial RX by
    ~300ms and the duplicate sailed through). Gated, default off."""

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _bridge(self, mock_home, tmp_path, mock_config, dedup=None):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        mock_home.return_value = tmp_path
        if dedup is not None:
            mock_config.rns.dual_path_dedup_enabled = dedup
            mock_config.rns.dual_path_dedup_window_sec = 60
        b = MeshtasticPresetBridge(config=mock_config)
        b._primary_interface = MagicMock()
        b._primary_connected = True
        return b

    def _payload(self, content, broadcast=True):
        from gateway.mesh_bridge import BridgedMeshMessage
        return BridgedMeshMessage(
            source_preset="SHORT_TURBO", source_id="!b03bb70c",
            destination_id="!ffffffff" if broadcast else "!32962f10",
            content=content, channel=2, is_broadcast=broadcast,
        ).to_payload()

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_suppressed_when_rns_copy_already_out(self, mock_home, tmp_path,
                                                  mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("[RNS:abcd] race content")     # rns_bridge TX'd first
        bridge = self._bridge(mock_home, tmp_path, mock_config, dedup=True)

        assert bridge._send_to_primary(self._payload("race content")) is True
        bridge._primary_interface.sendText.assert_not_called()
        assert bridge.stats['dual_path_suppressed'] == 1
        assert bridge.stats['messages_secondary_to_primary'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_no_hit_forwards_normally(self, mock_home, tmp_path,
                                      mock_config, monkeypatch):
        self._fresh_registry(monkeypatch)
        bridge = self._bridge(mock_home, tmp_path, mock_config, dedup=True)

        assert bridge._send_to_primary(self._payload("fresh content")) is True
        bridge._primary_interface.sendText.assert_called_once()
        assert bridge.stats['dual_path_suppressed'] == 0

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_flag_off_hit_still_forwards(self, mock_home, tmp_path,
                                         mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("dup content")
        # mock_config.rns is a MagicMock — strict is-True gate reads OFF.
        bridge = self._bridge(mock_home, tmp_path, mock_config)

        assert bridge._send_to_primary(self._payload("dup content")) is True
        bridge._primary_interface.sendText.assert_called_once()

    @patch('gateway.mesh_bridge.get_real_user_home')
    def test_dm_never_suppressed(self, mock_home, tmp_path,
                                 mock_config, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        reg.register("private words")
        bridge = self._bridge(mock_home, tmp_path, mock_config, dedup=True)

        assert bridge._send_to_primary(
            self._payload("private words", broadcast=False)) is True
        bridge._primary_interface.sendText.assert_called_once()
        assert bridge.stats['dual_path_suppressed'] == 0
