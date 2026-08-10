"""
Tests for MeshtasticHandler - Meshtastic connection and message processing.

Tests the handler's connection logic, message receiving, node tracking,
send operations, relay discovery, and CLI fallback.

All dependencies are mocked — no live device or network needed.

Run: python3 -m pytest tests/test_meshtastic_handler.py -v
"""

import sys
import threading
import time
from pathlib import Path
from queue import Queue, Full
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.meshtastic_handler import MeshtasticHandler
from gateway.config import GatewayConfig, MeshtasticConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway_config():
    """Create a GatewayConfig with test defaults."""
    config = GatewayConfig()
    config.meshtastic = MeshtasticConfig(host="127.0.0.1", port=4403)
    return config


@pytest.fixture
def mock_node_tracker():
    """Mock UnifiedNodeTracker."""
    tracker = MagicMock()
    tracker.get_meshtastic_nodes.return_value = []
    tracker.add_node = MagicMock()
    return tracker


@pytest.fixture
def mock_health():
    """Mock BridgeHealthMonitor."""
    health = MagicMock()
    health.record_connection_event = MagicMock()
    health.record_error = MagicMock(return_value="transient")
    health.record_message_received = MagicMock()
    return health


@pytest.fixture
def stop_event():
    """Threading stop event."""
    return threading.Event()


@pytest.fixture
def message_queue():
    """Queue for mesh→RNS messages."""
    return Queue(maxsize=100)


@pytest.fixture
def handler(gateway_config, mock_node_tracker, mock_health, stop_event, message_queue):
    """Create a MeshtasticHandler with all mocked dependencies."""
    stats = {'errors': 0, 'messages_received': 0, 'messages_sent': 0}
    stats_lock = threading.Lock()

    h = MeshtasticHandler(
        config=gateway_config,
        node_tracker=mock_node_tracker,
        health=mock_health,
        stop_event=stop_event,
        stats=stats,
        stats_lock=stats_lock,
        message_queue=message_queue,
    )
    yield h
    # Ensure disconnected after test
    h.disconnect()


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------

class TestHandlerInit:
    """Tests for MeshtasticHandler initialization."""

    def test_initial_state(self, handler):
        """Handler starts disconnected with no interface."""
        assert handler.is_connected is False
        assert handler.interface is None

    def test_config_stored(self, handler, gateway_config):
        """Handler stores config reference."""
        assert handler.config is gateway_config
        assert handler.config.meshtastic.host == "127.0.0.1"
        assert handler.config.meshtastic.port == 4403

    def test_set_network_topology(self, handler):
        """Setting network topology stores reference."""
        mock_topo = MagicMock()
        handler.set_network_topology(mock_topo)
        assert handler._network_topology is mock_topo


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

class TestConnect:
    """Tests for connection establishment."""

    @patch('gateway.meshtastic_handler._HAS_PUBSUB', True)
    @patch('gateway.meshtastic_handler.check_service')
    @patch('gateway.meshtastic_handler.get_connection_manager')
    @patch('gateway.meshtastic_handler.pub', new_callable=MagicMock)
    def test_connect_success(self, mock_pub, mock_get_cm, mock_check_service, handler):
        """Successful TCP connection via connection manager."""
        # Pre-flight check passes
        mock_status = MagicMock()
        mock_status.available = True
        mock_check_service.return_value = mock_status

        mock_cm = MagicMock()
        mock_cm.acquire_persistent.return_value = True
        mock_iface = MagicMock()
        mock_iface.nodes = {}
        mock_iface.getMyNodeInfo.return_value = {'num': 0xAABB0001}
        mock_cm.get_interface.return_value = mock_iface
        mock_get_cm.return_value = mock_cm

        result = handler.connect()

        assert result is True
        assert handler.is_connected is True

    @patch('gateway.meshtastic_handler._HAS_PUBSUB', True)
    @patch('gateway.meshtastic_handler.check_service')
    @patch('gateway.meshtastic_handler.get_connection_manager')
    def test_connect_acquire_fails(self, mock_get_cm, mock_check_service, handler):
        """Connection fails when persistent acquire returns False."""
        mock_status = MagicMock()
        mock_status.available = True
        mock_check_service.return_value = mock_status

        mock_cm = MagicMock()
        mock_cm.acquire_persistent.return_value = False
        mock_get_cm.return_value = mock_cm

        result = handler.connect()

        assert result is False
        assert handler.is_connected is False

    @patch('gateway.meshtastic_handler._HAS_PUBSUB', True)
    @patch('gateway.meshtastic_handler.check_service')
    @patch('gateway.meshtastic_handler.get_connection_manager')
    def test_connect_interface_none(self, mock_get_cm, mock_check_service, handler):
        """Connection fails when interface is None."""
        mock_status = MagicMock()
        mock_status.available = True
        mock_check_service.return_value = mock_status

        mock_cm = MagicMock()
        mock_cm.acquire_persistent.return_value = True
        mock_cm.get_interface.return_value = None
        mock_get_cm.return_value = mock_cm

        result = handler.connect()

        assert result is False
        assert handler.is_connected is False

    @patch('gateway.meshtastic_handler._HAS_PUBSUB', False)
    def test_connect_import_error_cli_fallback(self, handler):
        """Falls back to CLI when pubsub not available."""
        with patch.object(handler, '_test_cli', return_value=True):
            result = handler.connect()

        # Should fall back to CLI
        assert handler.is_connected is True

    @patch('gateway.meshtastic_handler._HAS_PUBSUB', True)
    @patch('gateway.meshtastic_handler.check_service')
    @patch('gateway.meshtastic_handler.get_connection_manager')
    def test_connect_general_exception(self, mock_get_cm, mock_check_service, handler):
        """General exception during connect returns False."""
        mock_status = MagicMock()
        mock_status.available = True
        mock_check_service.return_value = mock_status

        mock_get_cm.side_effect = RuntimeError("boom")
        result = handler.connect()

        assert result is False
        assert handler.is_connected is False


# ---------------------------------------------------------------------------
# Disconnect tests
# ---------------------------------------------------------------------------

class TestDisconnect:
    """Tests for disconnection."""

    def test_disconnect_clears_state(self, handler):
        """Disconnect clears connection state."""
        handler._connected = True
        handler._interface = MagicMock()
        handler._conn_manager = MagicMock()

        handler.disconnect()

        assert handler.is_connected is False
        assert handler.interface is None

    def test_disconnect_releases_persistent(self, handler):
        """Disconnect releases persistent connection."""
        mock_cm = MagicMock()
        handler._conn_manager = mock_cm
        handler._connected = True

        handler.disconnect()

        mock_cm.release_persistent.assert_called_once()

    def test_disconnect_unsubscribes_pubsub(self, handler):
        """Disconnect unsubscribes from pubsub."""
        mock_handler = MagicMock()
        handler._pubsub_handler = mock_handler
        handler._connected = True

        with patch.dict('sys.modules', {
            'pubsub': MagicMock(),
            'pubsub.pub': MagicMock(),
        }):
            handler.disconnect()

        assert handler._pubsub_handler is None

    def test_disconnect_handles_errors_gracefully(self, handler):
        """Disconnect doesn't crash on errors."""
        mock_cm = MagicMock()
        mock_cm.release_persistent.side_effect = RuntimeError("already closed")
        handler._conn_manager = mock_cm
        handler._connected = True

        # Should not raise
        handler.disconnect()
        assert handler.is_connected is False


# ---------------------------------------------------------------------------
# Send tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestSendText:
    """Tests for text message sending."""

    def test_send_text_success(self, handler):
        """Successful send via interface."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        result = handler.send_text("Hello mesh!", destination="!aabb0001", channel=0)

        assert result is True
        mock_iface.sendText.assert_called_once_with(
            "Hello mesh!",
            destinationId="!aabb0001",
            channelIndex=0,
            wantAck=False,
        )

    def test_send_text_broadcast(self, handler):
        """Broadcast when no destination specified."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        result = handler.send_text("Hello everyone!")

        assert result is True
        mock_iface.sendText.assert_called_once_with(
            "Hello everyone!",
            destinationId="^all",
            channelIndex=0,
            wantAck=False,
        )

    def test_send_text_not_connected(self, handler):
        """Send returns False when not connected."""
        result = handler.send_text("Hello!")
        assert result is False

    def test_send_text_exception(self, handler):
        """Send returns False on exception and increments error count."""
        handler._connected = True
        mock_iface = MagicMock()
        mock_iface.sendText.side_effect = RuntimeError("send failed")
        handler._interface = mock_iface

        result = handler.send_text("Hello!")

        assert result is False
        assert handler.stats['errors'] == 1

    def test_send_text_cli_fallback(self, handler):
        """Falls back to CLI when no interface."""
        handler._connected = True
        handler._interface = None

        with patch.object(handler, '_send_via_cli', return_value=True) as mock_cli:
            result = handler.send_text("Hello!", destination="!aabb")
            assert result is True
            mock_cli.assert_called_once_with("Hello!", "!aabb", 0)

    def test_send_text_truncates_long_message(self, handler):
        """Messages exceeding 228 bytes are truncated with '...' marker."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        long_msg = "A" * 300  # 300 bytes, exceeds 228
        result = handler.send_text(long_msg)

        assert result is True
        sent_msg = mock_iface.sendText.call_args[0][0]
        assert len(sent_msg.encode('utf-8')) <= 228
        assert sent_msg.endswith('...')

    def test_send_text_exact_boundary_not_truncated(self, handler):
        """Messages exactly at 228 bytes pass through unmodified."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        exact_msg = "B" * 228
        result = handler.send_text(exact_msg)

        assert result is True
        sent_msg = mock_iface.sendText.call_args[0][0]
        assert sent_msg == exact_msg

    def test_send_text_unicode_truncation_safe(self, handler):
        """UTF-8 multi-byte chars are not split during truncation."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        # Each emoji is 4 bytes. 60 emojis = 240 bytes > 228
        unicode_msg = "\U0001F600" * 60
        result = handler.send_text(unicode_msg)

        assert result is True
        sent_msg = mock_iface.sendText.call_args[0][0]
        sent_msg.encode('utf-8')  # Must be valid UTF-8 (no split chars)
        assert len(sent_msg.encode('utf-8')) <= 228


# ---------------------------------------------------------------------------
# Queue send tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestQueueSend:
    """Tests for persistent queue send handler."""

    def test_queue_send_success(self, handler):
        """Queue send dispatches message correctly."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        payload = {'message': 'Test msg', 'destination': '!aabb', 'channel': 1}
        result = handler.queue_send(payload)

        assert result is True
        mock_iface.sendText.assert_called_once_with(
            "Test msg", destinationId="!aabb", channelIndex=1, wantAck=False,
        )

    def test_queue_send_broadcast(self, handler):
        """Queue send broadcasts when no destination."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        payload = {'message': 'Broadcast'}
        result = handler.queue_send(payload)

        assert result is True
        mock_iface.sendText.assert_called_once_with(
            "Broadcast", destinationId="^all", channelIndex=0, wantAck=False,
        )

    def test_queue_send_not_connected(self, handler):
        """Queue send returns False when disconnected."""
        result = handler.queue_send({'message': 'test'})
        assert result is False

    def test_queue_send_no_interface(self, handler):
        """Queue send returns False with no interface."""
        handler._connected = True
        handler._interface = None

        result = handler.queue_send({'message': 'test'})
        assert result is False

    def test_queue_send_exception(self, handler):
        """Queue send returns False on exception."""
        handler._connected = True
        mock_iface = MagicMock()
        mock_iface.sendText.side_effect = RuntimeError("boom")
        handler._interface = mock_iface

        result = handler.queue_send({'message': 'test'})
        assert result is False

    def test_queue_send_truncates_long_message(self, handler):
        """Queue send also truncates long messages."""
        handler._connected = True
        mock_iface = MagicMock()
        handler._interface = mock_iface

        payload = {'message': 'C' * 300, 'destination': '!aabb', 'channel': 0}
        result = handler.queue_send(payload)

        assert result is True
        sent_msg = mock_iface.sendText.call_args[0][0]
        assert len(sent_msg.encode('utf-8')) <= 228


# ---------------------------------------------------------------------------
# Test connection tests
# ---------------------------------------------------------------------------

class TestTestConnection:
    """Tests for TCP connection test."""

    @patch('socket.socket')
    def test_connection_test_success(self, mock_socket_cls, handler):
        """Successful TCP connection test."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        result = handler.test_connection()

        assert result is True
        mock_sock.close.assert_called_once()

    @patch('socket.socket')
    def test_connection_test_refused(self, mock_socket_cls, handler):
        """Failed TCP connection test (refused)."""
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111  # ECONNREFUSED
        mock_socket_cls.return_value = mock_sock

        result = handler.test_connection()

        assert result is False

    @patch('socket.socket')
    def test_connection_test_exception(self, mock_socket_cls, handler):
        """TCP connection test handles exceptions."""
        mock_socket_cls.side_effect = OSError("No network")

        result = handler.test_connection()
        assert result is False


# ---------------------------------------------------------------------------
# Message receiving tests
# ---------------------------------------------------------------------------

class TestOnReceive:
    """Tests for incoming message handling."""

    def test_on_receive_text_message(self, handler, mock_node_tracker, message_queue):
        """Text messages are queued for bridging."""
        handler._connected = True

        packet = {
            'fromId': '!aabb0001',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Hello from mesh!',
            },
            'rxSnr': 8.5,
            'rxRssi': -80,
            'channel': 0,
            'hopStart': 3,
            'hopLimit': 2,
        }

        with patch.dict('sys.modules', {
            'commands': MagicMock(),
            'commands.messaging': MagicMock(),
        }):
            handler._on_receive(packet)

        # Node should be added to tracker
        mock_node_tracker.add_node.assert_called()

        # Message should be in the queue
        assert not message_queue.empty()
        msg = message_queue.get_nowait()
        assert msg.content == "Hello from mesh!"
        assert msg.source_id == '!aabb0001'
        assert msg.is_broadcast is True

    def test_on_receive_non_text_portnum(self, handler, mock_node_tracker, message_queue):
        """Non-text messages don't get queued for bridging."""
        packet = {
            'fromId': '!aabb0001',
            'decoded': {
                'portnum': 'POSITION_APP',
            },
            'hopStart': 3,
            'hopLimit': 3,
        }

        handler._on_receive(packet)

        # Node should still be tracked
        mock_node_tracker.add_node.assert_called()
        # But no message in queue
        assert message_queue.empty()

    def test_on_receive_with_relay_node(self, handler, mock_node_tracker):
        """Relay node discovery via Meshtastic 2.6+ field."""
        handler._connected = True
        mock_topo = MagicMock()
        handler.set_network_topology(mock_topo)

        packet = {
            'fromId': '!aabb0001',
            'decoded': {'portnum': 'POSITION_APP'},
            'relayNode': 0x42,
            'rxSnr': 5.0,
            'rxRssi': -90,
            'hopStart': 3,
            'hopLimit': 2,
        }

        handler._on_receive(packet)

        # Relay node should be discovered and added
        assert mock_node_tracker.add_node.call_count >= 1

    def test_on_receive_exception_handled(self, handler):
        """Exceptions during receive don't crash."""
        # Malformed packet
        packet = {}
        handler._on_receive(packet)  # Should not raise

    def test_on_receive_routing_filter(self, handler, message_queue):
        """Messages blocked by routing rules are not queued."""
        handler._connected = True
        handler._should_bridge = MagicMock(return_value=False)

        packet = {
            'fromId': '!aabb0001',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Blocked message',
            },
            'hopStart': 3,
            'hopLimit': 3,
        }

        with patch.dict('sys.modules', {
            'commands': MagicMock(),
            'commands.messaging': MagicMock(),
        }):
            handler._on_receive(packet)

        # Message should NOT be in the queue
        assert message_queue.empty()

    def test_on_receive_queue_full(self, handler, mock_node_tracker):
        """Full queue drops message gracefully."""
        small_queue = Queue(maxsize=1)
        small_queue.put("filler")  # Fill the queue
        handler._message_queue = small_queue
        handler._connected = True

        packet = {
            'fromId': '!aabb0001',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Overflow message',
            },
            'hopStart': 3,
            'hopLimit': 3,
        }

        with patch.dict('sys.modules', {
            'commands': MagicMock(),
            'commands.messaging': MagicMock(),
        }):
            handler._on_receive(packet)  # Should not raise

        assert handler.stats['errors'] == 1

    def test_on_receive_message_callback(self, handler, message_queue):
        """Message callback is invoked for text messages."""
        handler._connected = True
        callback = MagicMock()
        handler._message_callback = callback

        packet = {
            'fromId': '!aabb0001',
            'toId': '!ccdd0002',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Direct message',
            },
            'hopStart': 3,
            'hopLimit': 3,
        }

        with patch.dict('sys.modules', {
            'commands': MagicMock(),
            'commands.messaging': MagicMock(),
        }):
            handler._on_receive(packet)

        callback.assert_called_once()
        msg = callback.call_args[0][0]
        assert msg.content == "Direct message"
        assert msg.is_broadcast is False

    def test_on_receive_string_payload(self, handler, message_queue):
        """String payload (non-bytes) is handled correctly."""
        handler._connected = True

        packet = {
            'fromId': '!aabb0001',
            'toId': '!ffffffff',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': 'Already a string',
            },
            'hopStart': 3,
            'hopLimit': 3,
        }

        with patch.dict('sys.modules', {
            'commands': MagicMock(),
            'commands.messaging': MagicMock(),
        }):
            handler._on_receive(packet)

        msg = message_queue.get_nowait()
        assert msg.content == "Already a string"


# ---------------------------------------------------------------------------
# Relay node discovery tests
# ---------------------------------------------------------------------------

class TestDiscoverRelayNode:
    """Tests for relay node discovery (Meshtastic 2.6+)."""

    def test_discover_known_relay(self, handler, mock_node_tracker):
        """Matches relay byte to known node and updates topology."""
        mock_topo = MagicMock()
        handler.set_network_topology(mock_topo)

        # Create a known node whose last byte is 0x42
        from gateway.node_tracker import UnifiedNode
        mock_node = MagicMock()
        mock_node.meshtastic_id = "!aabb0042"
        mock_node.id = "node-0042"
        mock_node_tracker.get_meshtastic_nodes.return_value = [mock_node]

        packet = {'rxSnr': 8.0, 'rxRssi': -75}
        handler._discover_relay_node(0x42, "!ccdd0001", packet)

        mock_topo.add_edge.assert_called_once()

    def test_discover_unknown_relay(self, handler, mock_node_tracker):
        """Unknown relay byte creates placeholder node."""
        mock_node_tracker.get_meshtastic_nodes.return_value = []

        handler._discover_relay_node(0xFF, "!ccdd0001", {})

        # A partial node should be added
        calls = mock_node_tracker.add_node.call_args_list
        assert len(calls) >= 1
        added_node = calls[-1][0][0]
        assert added_node.id == "!????ff"

    def test_discover_relay_invalid_byte(self, handler, mock_node_tracker):
        """Invalid relay byte (0 or >255) is ignored."""
        handler._discover_relay_node(0, "!ccdd0001", {})
        handler._discover_relay_node(256, "!ccdd0001", {})
        # No nodes should be added for relay discovery (only from _on_receive)
        # The add_node calls should be 0 since we're calling _discover_relay_node directly
        mock_node_tracker.add_node.assert_not_called()

    def test_discover_relay_exception_handled(self, handler, mock_node_tracker):
        """Exceptions during relay discovery don't crash."""
        mock_node_tracker.get_meshtastic_nodes.side_effect = RuntimeError("boom")
        handler._discover_relay_node(0x42, "!ccdd0001", {})  # Should not raise


# ---------------------------------------------------------------------------
# Poll and connection health tests
# ---------------------------------------------------------------------------

class TestPoll:
    """Tests for health polling."""

    def test_poll_interface_connected(self, handler):
        """Polling passes when interface is connected."""
        mock_iface = MagicMock()
        mock_iface.isConnected = True
        mock_iface.nodes = {}
        handler._interface = mock_iface
        handler._connected = True

        handler._poll()  # Should not change state
        assert handler._connected is True

    def test_poll_interface_disconnected(self, handler):
        """Polling detects disconnected interface."""
        mock_iface = MagicMock()
        mock_iface.isConnected = False
        handler._interface = mock_iface
        handler._connected = True

        with patch.object(handler, '_handle_connection_lost'):
            handler._poll()
            handler._handle_connection_lost.assert_called_once()

    def test_poll_broken_pipe(self, handler):
        """Polling handles broken pipe exceptions."""
        mock_iface = MagicMock()
        type(mock_iface).nodes = PropertyMock(side_effect=BrokenPipeError)
        mock_iface.isConnected = True
        handler._interface = mock_iface
        handler._connected = True

        with patch.object(handler, '_handle_connection_lost'):
            handler._poll()
            handler._handle_connection_lost.assert_called_once()

    def test_poll_no_interface(self, handler):
        """Polling does nothing when no interface."""
        handler._interface = None
        handler._poll()  # Should not raise


# ---------------------------------------------------------------------------
# Connection lost handling tests
# ---------------------------------------------------------------------------

class TestHandleConnectionLost:
    """Tests for connection loss handling."""

    def test_handle_connection_lost_clears_state(self, handler):
        """Connection loss resets handler state."""
        handler._connected = True
        handler._interface = MagicMock()
        mock_cm = MagicMock()
        handler._conn_manager = mock_cm

        with patch.dict('sys.modules', {
            'pubsub': MagicMock(),
            'pubsub.pub': MagicMock(),
            'utils.meshtastic_connection': MagicMock(),
        }):
            handler._handle_connection_lost()

        assert handler._connected is False
        assert handler._interface is None
        mock_cm.release_persistent.assert_called_once()

    def test_handle_connection_lost_notifies_status(self, handler):
        """Connection loss triggers status callback."""
        handler._connected = True
        callback = MagicMock()
        handler._status_callback = callback

        with patch.dict('sys.modules', {
            'pubsub': MagicMock(),
            'pubsub.pub': MagicMock(),
            'utils.meshtastic_connection': MagicMock(),
        }):
            handler._handle_connection_lost()

        callback.assert_called_with("meshtastic_disconnected")


# ---------------------------------------------------------------------------
# CLI fallback tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestCLIFallback:
    """Tests for Meshtastic CLI fallback."""

    @patch('subprocess.run')
    def test_send_via_cli_success(self, mock_run, handler):
        """CLI send with destination."""
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict('sys.modules', {
            'utils.cli': MagicMock(find_meshtastic_cli=MagicMock(return_value='/usr/bin/meshtastic')),
        }):
            result = handler._send_via_cli("Hello!", "!aabb", 1)

        assert result is True
        cmd = mock_run.call_args[0][0]
        assert '--sendtext' in cmd
        assert 'Hello!' in cmd
        assert '--dest' in cmd
        assert '--ch-index' in cmd

    @patch('subprocess.run')
    def test_send_via_cli_failure(self, mock_run, handler):
        """CLI send returns False on failure."""
        mock_run.return_value = MagicMock(returncode=1)

        with patch.dict('sys.modules', {
            'utils.cli': MagicMock(find_meshtastic_cli=MagicMock(return_value='meshtastic')),
        }):
            result = handler._send_via_cli("Hello!")

        assert result is False

    @patch('subprocess.run')
    def test_send_via_cli_exception(self, mock_run, handler):
        """CLI send handles exceptions."""
        mock_run.side_effect = FileNotFoundError("no such file")

        with patch.dict('sys.modules', {
            'utils.cli': MagicMock(find_meshtastic_cli=MagicMock(return_value='meshtastic')),
        }):
            result = handler._send_via_cli("Hello!")

        assert result is False

    @patch('subprocess.run')
    def test_test_cli_success(self, mock_run, handler):
        """CLI availability test success."""
        mock_run.return_value = MagicMock(returncode=0)

        with patch.dict('sys.modules', {
            'utils.cli': MagicMock(find_meshtastic_cli=MagicMock(return_value='/usr/bin/meshtastic')),
        }):
            result = handler._test_cli()

        assert result is True

    def test_test_cli_not_found(self, handler):
        """CLI test returns False when CLI not found."""
        with patch.dict('sys.modules', {
            'utils.cli': MagicMock(find_meshtastic_cli=MagicMock(return_value=None)),
        }):
            result = handler._test_cli()

        assert result is False


# ---------------------------------------------------------------------------
# Node update tests
# ---------------------------------------------------------------------------

class TestUpdateNodes:
    """Tests for node tracker updates."""

    def test_update_nodes_from_interface(self, handler, mock_node_tracker):
        """Nodes from interface are added to tracker."""
        mock_iface = MagicMock()
        mock_iface.getMyNodeInfo.return_value = {'num': 0xAABB0001}
        mock_iface.nodes = {
            '!aabb0001': {'num': 0xAABB0001, 'user': {'longName': 'Local'}},
            '!ccdd0002': {'num': 0xCCDD0002, 'user': {'longName': 'Remote'}},
        }
        handler._interface = mock_iface

        handler._update_nodes()

        assert mock_node_tracker.add_node.call_count == 2

    def test_update_nodes_no_interface(self, handler, mock_node_tracker):
        """Update does nothing without interface."""
        handler._interface = None
        handler._update_nodes()
        mock_node_tracker.add_node.assert_not_called()

    def test_update_nodes_exception(self, handler, mock_node_tracker):
        """Update handles exceptions gracefully."""
        mock_iface = MagicMock()
        mock_iface.getMyNodeInfo.side_effect = RuntimeError("boom")
        handler._interface = mock_iface

        handler._update_nodes()  # Should not raise


# ---------------------------------------------------------------------------
# Status notification tests
# ---------------------------------------------------------------------------

class TestNotifyStatus:
    """Tests for status callback notification."""

    def test_notify_status_calls_callback(self, handler):
        """Status callback is invoked."""
        callback = MagicMock()
        handler._status_callback = callback

        handler._notify_status("meshtastic_connected")

        callback.assert_called_once_with("meshtastic_connected")

    def test_notify_status_no_callback(self, handler):
        """No error when callback is None."""
        handler._status_callback = None
        handler._notify_status("meshtastic_connected")  # Should not raise

    def test_notify_status_callback_error(self, handler):
        """Callback errors don't crash handler."""
        callback = MagicMock(side_effect=RuntimeError("boom"))
        handler._status_callback = callback

        handler._notify_status("meshtastic_connected")  # Should not raise


# ---------------------------------------------------------------------------
# Run loop tests
# ---------------------------------------------------------------------------

class TestRunLoop:
    """Tests for the main connection loop."""

    def test_run_loop_stops_on_event(self, handler, stop_event):
        """Run loop exits when stop event is set."""
        stop_event.set()
        handler.run_loop()  # Should return immediately

    def test_run_loop_connects_and_polls(self, handler, stop_event, mock_health):
        """Run loop attempts connection then polls."""
        call_count = [0]

        def mock_connect():
            handler._connected = True
            return True

        def mock_poll():
            call_count[0] += 1
            if call_count[0] >= 2:
                stop_event.set()

        with patch.object(handler, 'connect', side_effect=mock_connect):
            with patch.object(handler, '_poll', side_effect=mock_poll):
                handler.run_loop()

        assert call_count[0] >= 1
        mock_health.record_connection_event.assert_any_call("meshtastic", "connected")

    def test_run_loop_handles_connection_error(self, handler, stop_event, mock_health):
        """Run loop handles connection errors with backoff."""
        attempt = [0]

        def mock_connect():
            attempt[0] += 1
            if attempt[0] >= 2:
                stop_event.set()
            raise ConnectionResetError("connection reset")

        with patch.object(handler, 'connect', side_effect=mock_connect):
            handler.run_loop()

        mock_health.record_error.assert_called()


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestDualPathDedupRegistration:
    """Broadcast TX through the handler registers in the process-wide
    RecentRfTxRegistry (dual-path dedup, 2026-06-04) — so mesh_bridge can
    suppress its forward when the rns_bridge relay copy won the race.
    DMs and failed sends must not register."""

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _connected(self, handler):
        handler._connected = True
        handler._interface = MagicMock()
        handler._interface.sendText.return_value = True
        return handler

    def test_send_text_broadcast_registers(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        assert h.send_text("broadcast content", destination=None, channel=2)
        assert reg.seen_within("broadcast content", 60.0)

    def test_send_text_dm_does_not_register(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        assert h.send_text("dm content", destination="!b03bb70c", channel=2)
        assert not reg.seen_within("dm content", 60.0)

    def test_queue_send_broadcast_registers(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        assert h.queue_send({"message": "[RNS:abcd] queued bc",
                             "destination": None, "channel": 2})
        # Tag-normalized: the raw downlink content matches the tagged send.
        assert reg.seen_within("queued bc", 60.0)

    def test_failed_send_does_not_register(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        h._interface.sendText.return_value = False
        assert not h.send_text("never sent", destination=None, channel=2)
        assert not reg.seen_within("never sent", 60.0)


@pytest.mark.usefixtures("allow_local_radio_tx")
class TestDispatchTimeDedupRecheck:
    """queue_send re-checks the registry at DISPATCH time (gated).

    The enqueue-side check in _rns_bridge_xform races mesh_bridge's RF-TX
    registration: live 2026-06-04, the LAN-fast RNS relay copy of a bot
    reply was checked ~250ms BEFORE the serial leg registered its downlink,
    missed, queued, and went out 3s later as a visible duplicate — on
    EVERY bot event, not the rare race we'd accepted. By dispatch time
    (≥min_spacing_s) the registry is settled, so this re-check closes the
    race deterministically. Suppress-only-on-hit; flag-gated; DMs exempt.
    """

    def _fresh_registry(self, monkeypatch):
        import gateway.base_handler as bh
        fresh = bh.RecentRfTxRegistry()
        monkeypatch.setattr(bh, "_rf_tx_registry", fresh)
        return fresh

    def _connected(self, handler):
        handler._connected = True
        handler._interface = MagicMock()
        handler._interface.sendText.return_value = True
        return handler

    def test_suppresses_broadcast_on_registry_hit(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        h.config.rns.dual_path_dedup_enabled = True
        reg.register("Tonight: Occasional rain shwrs.")  # mesh_bridge downlink
        # The tagged relay copy of the same content must die at dispatch.
        assert h.queue_send({"message": "[RNS:Borg serve] Tonight: Occasional rain shwrs.",
                             "destination": None, "channel": 2}) is True
        h._interface.sendText.assert_not_called()
        assert h.stats.get('dispatch_dedup_suppressed') == 1

    def test_no_suppression_when_flag_off(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        h.config.rns.dual_path_dedup_enabled = False
        reg.register("Tonight: Occasional rain shwrs.")
        assert h.queue_send({"message": "[RNS:Borg serve] Tonight: Occasional rain shwrs.",
                             "destination": None, "channel": 2}) is True
        h._interface.sendText.assert_called_once()

    def test_dm_never_suppressed(self, handler, monkeypatch):
        reg = self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        h.config.rns.dual_path_dedup_enabled = True
        reg.register("private reply")
        assert h.queue_send({"message": "private reply",
                             "destination": "!b03bb70c", "channel": 2}) is True
        h._interface.sendText.assert_called_once()

    def test_no_hit_sends_normally(self, handler, monkeypatch):
        self._fresh_registry(monkeypatch)
        h = self._connected(handler)
        h.config.rns.dual_path_dedup_enabled = True
        assert h.queue_send({"message": "fresh content",
                             "destination": None, "channel": 2}) is True
        h._interface.sendText.assert_called_once()

    def test_suppresses_on_payload_content_id_hit(self, handler, monkeypatch):
        """Review 2026-07-01: a true-origin delivery registers ONLY its
        content_id — the payload carries the cid from enqueue time so this
        re-check recognizes it, tagging the cid-only witness."""
        reg = self._fresh_registry(monkeypatch)
        from gateway.base_handler import mesh_origin_content_id
        cid = mesh_origin_content_id("!a2e95ba4", "borg reply text")
        reg.register_content_id(cid)             # cid only — no text entry
        h = self._connected(handler)
        h.config.rns.dual_path_dedup_enabled = True
        assert h.queue_send({"message": "[RNS:BORG] borg reply text",
                             "destination": None, "channel": 2,
                             "content_id": cid}) is True
        h._interface.sendText.assert_not_called()
        assert h.stats.get('dispatch_dedup_suppressed') == 1
        assert h.stats.get('dispatch_dedup_suppressed_cid_only') == 1


# ---------------------------------------------------------------------------
# Thread-2 step 4 — Meshtastic ROUTING_APP ACK consumption (#74)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestMeshAckConsumption:
    """The honest-delivery rung: a wantAck DM's ROUTING_APP ACK/NAK becomes
    a real CONFIRMED / DROPPED delivery_counters transition, so Meshtastic
    joins the #74 confirmable set."""

    def _connected(self, handler, packet_id=0xABCD1234):
        handler._connected = True
        iface = MagicMock()
        iface.sendText.return_value = MagicMock(id=packet_id)
        handler._interface = iface
        return handler

    def _rec(self, monkeypatch):
        """Patch delivery_counters.record; return the capturing mock."""
        from unittest.mock import MagicMock as _MM
        rec = _MM()
        monkeypatch.setattr('gateway.delivery_counters.record', rec)
        return rec

    # --- send side: wantAck + registration -------------------------------

    def test_dm_sets_wantack_and_registers_when_enabled(self, handler, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._connected(handler, packet_id=0x11112222)
        h.config.rns.meshtastic_ack_consumption_enabled = True

        assert h.send_text("hi", destination="!aabb0001", channel=0) is True
        # wantAck requested for the DM
        _, kwargs = h._interface.sendText.call_args
        assert kwargs["wantAck"] is True
        # packet_id registered → resolvable as the synthesized id
        resolved = h.ack_tracker.resolve(0x11112222)
        assert resolved == ("mesh-11112222", "meshtastic")
        # direct path records its own SENT (no queue lifecycle)
        rec.assert_any_call(DeliveryState.SENT,
                            msg_id="mesh-11112222", protocol="meshtastic")

    def test_broadcast_no_wantack_no_register(self, handler, monkeypatch):
        self._rec(monkeypatch)
        h = self._connected(handler)
        h.config.rns.meshtastic_ack_consumption_enabled = True

        assert h.send_text("all hands") is True   # broadcast
        _, kwargs = h._interface.sendText.call_args
        assert kwargs["wantAck"] is False
        assert h.ack_tracker.pending_count() == 0

    def test_dm_disabled_flag_no_register(self, handler, monkeypatch):
        self._rec(monkeypatch)
        h = self._connected(handler, packet_id=0x33334444)
        h.config.rns.meshtastic_ack_consumption_enabled = False

        assert h.send_text("hi", destination="!aabb0001") is True
        _, kwargs = h._interface.sendText.call_args
        assert kwargs["wantAck"] is False
        assert h.ack_tracker.resolve(0x33334444) is None

    def test_queue_send_dm_registers_with_queue_msg_id(self, handler, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._connected(handler, packet_id=0x55556666)
        h.config.rns.meshtastic_ack_consumption_enabled = True

        ok = h.queue_send({"message": "reply", "destination": "!b03bb70c",
                           "channel": 2, "_queue_msg_id": "q-123"})
        assert ok is True
        # registered against the queue's id (so CONFIRMED joins QUEUED→SENT)
        assert h.ack_tracker.resolve(0x55556666) == ("q-123", "meshtastic")
        # queue owns SENT — queue_send must NOT double-record it
        for c in rec.call_args_list:
            assert c.args[:1] != (DeliveryState.SENT,) or \
                c.kwargs.get("msg_id") != "q-123"

    # --- receive side: ROUTING_APP → CONFIRMED / DROPPED -----------------

    def test_positive_ack_records_confirmed(self, handler, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._connected(handler)
        h.config.rns.meshtastic_ack_consumption_enabled = True
        h.ack_tracker.register(0xAA01, "msg-7")

        h._handle_routing_ack({"portnum": "ROUTING_APP",
                               "requestId": 0xAA01, "routing": {}})

        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-7", protocol="meshtastic")
        assert h.stats.get("mesh_ack_confirmed") == 1

    def test_nak_records_dropped_with_reason(self, handler, monkeypatch):
        from gateway.delivery_counters import DeliveryState, DropReason
        rec = self._rec(monkeypatch)
        h = self._connected(handler)
        h.config.rns.meshtastic_ack_consumption_enabled = True
        h.ack_tracker.register(0xBB02, "msg-9")

        h._handle_routing_ack({"portnum": "ROUTING_APP", "requestId": 0xBB02,
                               "routing": {"errorReason": "MAX_RETRANSMIT"}})

        assert rec.call_count == 1
        args, kwargs = rec.call_args
        assert args[0] == DeliveryState.DROPPED
        assert kwargs["msg_id"] == "msg-9"
        assert kwargs["protocol"] == "meshtastic"
        assert kwargs["drop_reason"] == DropReason.RETRIES_EXHAUSTED
        assert kwargs["note"] == "meshtastic_nak:MAX_RETRANSMIT"
        assert h.stats.get("mesh_ack_failed") == 1

    def test_unmatched_ack_is_noop(self, handler, monkeypatch):
        rec = self._rec(monkeypatch)
        h = self._connected(handler)
        h.config.rns.meshtastic_ack_consumption_enabled = True
        # nothing registered for this request_id
        h._handle_routing_ack({"portnum": "ROUTING_APP",
                               "requestId": 0xDEAD, "routing": {}})
        rec.assert_not_called()

    def test_on_receive_dispatches_routing_app(self, handler, monkeypatch):
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._connected(handler)
        h.config.rns.meshtastic_ack_consumption_enabled = True
        h.ack_tracker.register(0xCAFE, "msg-x")

        h._on_receive({"decoded": {"portnum": "ROUTING_APP",
                                   "requestId": 0xCAFE, "routing": {}}})
        rec.assert_called_once_with(DeliveryState.CONFIRMED,
                                    msg_id="msg-x", protocol="meshtastic")

    def test_end_to_end_send_then_ack_same_msg_id(self, handler, monkeypatch):
        """The full rung: queue-send a DM, then its ACK arrives via
        _on_receive → CONFIRMED on the SAME _queue_msg_id."""
        from gateway.delivery_counters import DeliveryState
        rec = self._rec(monkeypatch)
        h = self._connected(handler, packet_id=0x0BADF00D)
        h.config.rns.meshtastic_ack_consumption_enabled = True

        h.queue_send({"message": "ping", "destination": "!b03bb70c",
                      "channel": 2, "_queue_msg_id": "q-777"})
        h._on_receive({"decoded": {"portnum": "ROUTING_APP",
                                   "requestId": 0x0BADF00D, "routing": {}}})

        rec.assert_called_with(DeliveryState.CONFIRMED,
                              msg_id="q-777", protocol="meshtastic")
        assert h.ack_tracker.resolve(0x0BADF00D) is None  # consumed once


class TestMeshOracleWiring:
    """Phase-1 wiring of the read-only mesh oracle into the text-RX path."""

    def test_build_responder_default_off(self, handler, monkeypatch):
        monkeypatch.delenv("MESHFORGE_ORACLE_ENABLED", raising=False)
        assert handler._build_oracle_responder() is None

    def test_build_responder_enabled(self, handler, monkeypatch):
        monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
        monkeypatch.setenv("MESHFORGE_ORACLE_ALLOWLIST", "*")
        assert handler._build_oracle_responder() is not None

    def test_query_routed_to_oracle_and_consumed(self, handler):
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = "dude-AI@x: fleet:?"
        packet = {"toId": "!ffffffff", "channel": 0, "rxSnr": -5, "rxRssi": -90}
        handler._handle_text_message(packet, {"payload": b"status"}, "!a1b2c3d4")
        handler._oracle.handle.assert_called_once_with("!a1b2c3d4", "status", 0)
        assert handler._message_queue.empty()  # consumed, NOT bridged onward

    def test_non_query_passes_through_to_bridge(self, handler):
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = None  # not a query
        with patch("commands.messaging.store_incoming"):
            handler._handle_text_message(
                {"toId": "!ffffffff", "channel": 0},
                {"payload": b"hello world"}, "!a1b2c3d4")
        assert not handler._message_queue.empty()  # passed through to the bridge

    def test_oracle_none_does_not_break_rx(self, handler):
        handler._oracle = None  # default-off: hook is a no-op
        with patch("commands.messaging.store_incoming"):
            handler._handle_text_message(
                {"toId": "!ffffffff", "channel": 0},
                {"payload": b"hello"}, "!a1b2c3d4")
        assert not handler._message_queue.empty()

    # -- channel whitelist (MESHFORGE_ORACLE_CHANNELS name -> local index) --
    def test_resolve_oracle_channels_empty(self, handler):
        assert handler._resolve_oracle_channels("") == set()
        assert handler._resolve_oracle_channels("  ,  ") == set()

    def test_resolve_oracle_channels_resolves_names(self, handler, monkeypatch):
        calls = {"meshforge": (2, "resolved"), "anchor": (3, "resolved")}
        monkeypatch.setattr(
            "gateway._channel_resolver.resolve_tx_channel_index",
            lambda name, fallback: calls[name])
        assert handler._resolve_oracle_channels("meshforge, anchor") == {2, 3}

    def test_resolve_oracle_channels_skips_unresolved(self, handler, monkeypatch):
        # not_found / unreachable contribute nothing (never silently index 0)
        monkeypatch.setattr(
            "gateway._channel_resolver.resolve_tx_channel_index",
            lambda name, fallback: (fallback, "not_found"))
        assert handler._resolve_oracle_channels("ghost") == set()

    def test_build_responder_with_channels(self, handler, monkeypatch):
        monkeypatch.setenv("MESHFORGE_ORACLE_ENABLED", "1")
        monkeypatch.delenv("MESHFORGE_ORACLE_ALLOWLIST", raising=False)
        monkeypatch.setenv("MESHFORGE_ORACLE_CHANNELS", "meshforge")
        monkeypatch.setattr(
            "gateway._channel_resolver.resolve_tx_channel_index",
            lambda name, fallback: (2, "resolved"))
        responder = handler._build_oracle_responder()
        assert responder is not None
        assert responder._allowed_channels == {2}
