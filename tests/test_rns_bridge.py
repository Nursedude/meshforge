"""
Tests for RNS-Meshtastic bridge service.

Run: python3 -m pytest tests/test_rns_bridge.py -v
"""

import pytest
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
from queue import Queue

from src.gateway.rns_bridge import (
    BridgedMessage,
    RNSMeshtasticBridge,
)
from src.gateway.config import GatewayConfig


class TestBridgedMessage:
    """Tests for BridgedMessage dataclass."""

    def test_defaults(self):
        """Test default message values."""
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="!abcd1234",
            destination_id=None,
            content="Hello"
        )

        assert msg.source_network == "meshtastic"
        assert msg.source_id == "!abcd1234"
        assert msg.content == "Hello"
        assert msg.title is None
        assert msg.is_broadcast is False
        assert msg.timestamp is not None
        assert msg.metadata == {}

    def test_with_all_fields(self):
        """Test message with all fields."""
        ts = datetime(2026, 1, 9, 12, 0, 0)
        msg = BridgedMessage(
            source_network="rns",
            source_id="abc123",
            destination_id="def456",
            content="Test message",
            title="Test Title",
            timestamp=ts,
            is_broadcast=True,
            metadata={"priority": "high"}
        )

        assert msg.source_network == "rns"
        assert msg.title == "Test Title"
        assert msg.timestamp == ts
        assert msg.is_broadcast is True
        assert msg.metadata == {"priority": "high"}

    def test_auto_timestamp(self):
        """Test automatic timestamp on creation."""
        before = datetime.now()
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="test",
            destination_id=None,
            content="Test"
        )
        after = datetime.now()

        assert before <= msg.timestamp <= after

    def test_auto_metadata(self):
        """Test automatic empty metadata dict."""
        msg = BridgedMessage(
            source_network="meshtastic",
            source_id="test",
            destination_id=None,
            content="Test"
        )

        # Should be a new dict, not None
        assert msg.metadata is not None
        assert isinstance(msg.metadata, dict)


class TestRNSMeshtasticBridge:
    """Tests for RNSMeshtasticBridge class."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for testing."""
        config = GatewayConfig()
        config.enabled = True
        return config

    @pytest.fixture
    def bridge(self, mock_config):
        """Create a bridge instance with mocked dependencies."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            with patch.object(GatewayConfig, 'load', return_value=mock_config):
                bridge = RNSMeshtasticBridge(config=mock_config)
                yield bridge
                # Cleanup
                if bridge._running:
                    bridge._running = False

    def test_init(self, bridge):
        """Test bridge initialization."""
        assert bridge._running is False
        assert bridge._connected_mesh is False
        assert bridge._connected_rns is False
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0

    def test_is_running_property(self, bridge):
        """Test is_running property."""
        assert bridge.is_running is False

        bridge._running = True
        assert bridge.is_running is True

    def test_is_connected_property(self, bridge):
        """Test is_connected property."""
        assert bridge.is_connected is False

        bridge._connected_mesh = True
        assert bridge.is_connected is True

        bridge._connected_mesh = False
        bridge._connected_rns = True
        assert bridge.is_connected is True

    def test_get_status(self, bridge):
        """Test get_status returns correct structure."""
        status = bridge.get_status()

        assert 'running' in status
        assert 'enabled' in status
        assert 'meshtastic_connected' in status
        assert 'rns_connected' in status
        assert 'statistics' in status
        assert 'node_stats' in status

    def test_get_status_with_uptime(self, bridge):
        """Test get_status calculates uptime."""
        bridge.stats['start_time'] = datetime.now() - timedelta(seconds=60)

        status = bridge.get_status()

        assert status['uptime_seconds'] is not None
        assert status['uptime_seconds'] >= 60

    def test_register_message_callback(self, bridge):
        """Test registering message callbacks."""
        callback = MagicMock()

        bridge.register_message_callback(callback)

        assert callback in bridge._message_callbacks

    def test_register_status_callback(self, bridge):
        """Test registering status callbacks."""
        callback = MagicMock()

        bridge.register_status_callback(callback)

        assert callback in bridge._status_callbacks

    def test_send_to_meshtastic_not_connected(self, bridge):
        """Test send fails when not connected."""
        bridge._connected_mesh = False

        result = bridge.send_to_meshtastic("Test message")

        assert result is False

    def test_send_to_rns_not_connected(self, bridge):
        """Test send fails when not connected."""
        bridge._connected_rns = False

        result = bridge.send_to_rns("Test message")

        assert result is False

    def test_send_to_meshtastic_with_interface(self, bridge):
        """Test send uses interface when available."""
        bridge._connected_mesh = True
        mock_interface = MagicMock()
        bridge._mesh_interface = mock_interface

        result = bridge.send_to_meshtastic("Hello", destination="!12345678", channel=1)

        assert result is True
        mock_interface.sendText.assert_called_once_with(
            "Hello",
            destinationId="!12345678",
            channelIndex=1
        )

    def test_send_to_meshtastic_error_handling(self, bridge):
        """Test send handles errors gracefully."""
        bridge._connected_mesh = True
        mock_interface = MagicMock()
        mock_interface.sendText.side_effect = Exception("Send failed")
        bridge._mesh_interface = mock_interface

        result = bridge.send_to_meshtastic("Hello")

        assert result is False
        assert bridge.stats['errors'] == 1

    def test_test_connection_structure(self, bridge):
        """Test test_connection returns correct structure."""
        with patch.object(bridge, '_test_meshtastic', return_value=False):
            with patch.object(bridge, '_test_rns', return_value=False):
                result = bridge.test_connection()

        assert 'meshtastic' in result
        assert 'rns' in result
        assert 'connected' in result['meshtastic']
        assert 'error' in result['meshtastic']

    def test_stop_when_not_running(self, bridge):
        """Test stop does nothing when not running."""
        bridge._running = False

        # Should not raise
        bridge.stop()

    def test_message_queues_initialized(self, bridge):
        """Test message queues are initialized."""
        assert isinstance(bridge._mesh_to_rns_queue, Queue)
        assert isinstance(bridge._rns_to_mesh_queue, Queue)

    def test_stats_initialization(self, bridge):
        """Test statistics are properly initialized."""
        assert bridge.stats['messages_mesh_to_rns'] == 0
        assert bridge.stats['messages_rns_to_mesh'] == 0
        assert bridge.stats['errors'] == 0
        assert bridge.stats['start_time'] is None


class TestBridgeStartStop:
    """Tests for bridge start/stop lifecycle."""

    def test_start_sets_running(self):
        """Test start sets running flag."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker') as mock_tracker:
            mock_tracker_instance = MagicMock()
            mock_tracker.return_value = mock_tracker_instance

            config = GatewayConfig()
            config.enabled = False  # Disable to avoid thread spawning

            bridge = RNSMeshtasticBridge(config=config)
            result = bridge.start()

            assert result is True
            assert bridge._running is True
            assert bridge.stats['start_time'] is not None
            mock_tracker_instance.start.assert_called_once()

            bridge._running = False  # Cleanup

    def test_start_when_already_running(self):
        """Test start returns True if already running."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            config.enabled = False

            bridge = RNSMeshtasticBridge(config=config)
            bridge._running = True

            result = bridge.start()

            assert result is True

    def test_stop_clears_running(self):
        """Test stop clears running flag."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker') as mock_tracker:
            mock_tracker_instance = MagicMock()
            mock_tracker.return_value = mock_tracker_instance

            config = GatewayConfig()
            config.enabled = False

            bridge = RNSMeshtasticBridge(config=config)
            bridge._running = True

            bridge.stop()

            assert bridge._running is False
            mock_tracker_instance.stop.assert_called_once()


class TestBridgeCallbacks:
    """Tests for callback notification."""

    def test_notify_status_calls_callbacks(self):
        """Test _notify_status calls all registered callbacks."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            callback1 = MagicMock()
            callback2 = MagicMock()
            bridge.register_status_callback(callback1)
            bridge.register_status_callback(callback2)

            bridge._notify_status("test_event")

            # Callbacks receive event and status dict
            assert callback1.call_count == 1
            assert callback2.call_count == 1
            assert callback1.call_args[0][0] == "test_event"
            assert callback2.call_args[0][0] == "test_event"

    def test_notify_message_calls_callbacks(self):
        """Test _notify_message calls all registered callbacks."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            callback = MagicMock()
            bridge.register_message_callback(callback)

            msg = BridgedMessage(
                source_network="meshtastic",
                source_id="test",
                destination_id=None,
                content="Hello"
            )
            bridge._notify_message(msg)

            callback.assert_called_once_with(msg)

    def test_callback_error_handling(self):
        """Test callbacks don't break on error."""
        with patch('src.gateway.rns_bridge.UnifiedNodeTracker'):
            config = GatewayConfig()
            bridge = RNSMeshtasticBridge(config=config)

            bad_callback = MagicMock(side_effect=Exception("Callback error"))
            good_callback = MagicMock()

            bridge.register_status_callback(bad_callback)
            bridge.register_status_callback(good_callback)

            # Should not raise
            bridge._notify_status("test")

            # Good callback should still be called
            good_callback.assert_called_once()
