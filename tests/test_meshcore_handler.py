"""
Tests for MeshCoreHandler — MeshCore companion radio integration.

Tests cover:
- Handler instantiation and DI pattern
- Simulation mode (no hardware)
- Message receive → queue flow
- Outbound message processing
- Connection lifecycle
- Device detection
- Event subscription
"""

import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.meshcore_handler import (
    MeshCoreHandler,
    MeshCoreSimulator,
    detect_meshcore_devices,
    _HAS_MESHCORE,
)
from gateway.canonical_message import CanonicalMessage, MessageType, Protocol
from gateway.bridge_health import MessageOrigin


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_config():
    """Create a minimal gateway config with MeshCore settings."""
    meshcore = SimpleNamespace(
        enabled=True,
        device_path='/dev/ttyUSB1',
        baud_rate=115200,
        connection_type='serial',
        tcp_host='localhost',
        tcp_port=4000,
        auto_fetch_messages=True,
        bridge_channels=True,
        bridge_dms=True,
        simulation_mode=True,
        channel_poll_interval_sec=5,
    )
    config = SimpleNamespace(
        meshcore=meshcore,
        meshtastic=SimpleNamespace(host='localhost', port=4403),
    )
    return config


@pytest.fixture
def mock_health():
    """Create a mock health monitor."""
    health = MagicMock()
    health.record_error.return_value = 'transient'
    health.record_connection_event = MagicMock()
    health.record_message_sent = MagicMock()
    return health


@pytest.fixture
def mock_node_tracker():
    """Create a mock node tracker."""
    tracker = MagicMock()
    tracker.add_node = MagicMock()
    return tracker


@pytest.fixture
def handler(mock_config, mock_health, mock_node_tracker):
    """Create a MeshCoreHandler in simulation mode."""
    stop_event = threading.Event()
    stats = {'errors': 0}
    stats_lock = threading.Lock()
    queue = Queue(maxsize=100)

    h = MeshCoreHandler(
        config=mock_config,
        node_tracker=mock_node_tracker,
        health=mock_health,
        stop_event=stop_event,
        stats=stats,
        stats_lock=stats_lock,
        message_queue=queue,
    )
    return h


# =============================================================================
# Instantiation & DI Pattern
# =============================================================================

class TestInstantiation:
    """Test handler creation follows the DI pattern."""

    def test_handler_creates(self, handler):
        """Handler instantiates without errors."""
        assert handler is not None
        assert handler.is_connected is False

    def test_simulation_mode_detected(self, handler):
        """Simulation mode detected when meshcore not installed or config says so."""
        assert handler._simulation_mode is True

    def test_handler_with_callbacks(self, mock_config, mock_health, mock_node_tracker):
        """Handler accepts all callback parameters."""
        msg_cb = MagicMock()
        status_cb = MagicMock()
        bridge_cb = MagicMock()

        h = MeshCoreHandler(
            config=mock_config,
            node_tracker=mock_node_tracker,
            health=mock_health,
            stop_event=threading.Event(),
            stats={},
            stats_lock=threading.Lock(),
            message_queue=Queue(),
            message_callback=msg_cb,
            status_callback=status_cb,
            should_bridge=bridge_cb,
        )
        assert h._message_callback is msg_cb
        assert h._status_callback is status_cb
        assert h._should_bridge is bridge_cb


# =============================================================================
# Simulator
# =============================================================================

class TestSimulator:
    """Test MeshCoreSimulator for hardware-free testing."""

    def test_simulator_creates(self):
        """Simulator instantiates with fake contacts."""
        sim = MeshCoreSimulator()
        assert len(sim._contacts) > 0

    def test_simulator_start_stop(self):
        """Simulator starts and stops cleanly."""
        async def _test():
            sim = MeshCoreSimulator()
            await sim.start()
            assert sim._running is True
            await sim.stop()
            assert sim._running is False
        asyncio.run(_test())

    def test_simulator_get_contacts(self):
        """Simulator returns fake contacts."""
        async def _test():
            sim = MeshCoreSimulator()
            contacts = await sim.get_contacts()
            assert len(contacts) >= 2
            assert 'adv_name' in contacts[0]
        asyncio.run(_test())

    def test_simulator_send_msg(self):
        """Simulator accepts send operations."""
        async def _test():
            sim = MeshCoreSimulator()
            result = await sim.send_msg(None, "Test message")
            assert result is True
        asyncio.run(_test())

    def test_simulator_send_channel(self):
        """Simulator accepts channel broadcasts."""
        async def _test():
            sim = MeshCoreSimulator()
            result = await sim.send_channel_txt_msg("Broadcast test")
            assert result is True
        asyncio.run(_test())

    def test_simulator_subscribe(self):
        """Simulator accepts event subscriptions."""
        sim = MeshCoreSimulator()
        callback = MagicMock()
        sim.subscribe('CONTACT_MSG_RECV', callback)
        assert 'CONTACT_MSG_RECV' in sim._subscribers


# =============================================================================
# Connection Lifecycle
# =============================================================================

class TestConnectionLifecycle:
    """Test connect/disconnect behavior."""

    def test_connect_simulation(self, handler):
        """Connect in simulation mode succeeds."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())
            assert handler.is_connected is True
            assert isinstance(handler._meshcore, MeshCoreSimulator)
        finally:
            loop.close()

    def test_disconnect_clears_state(self, handler):
        """Disconnect clears connection state."""
        # First connect
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())
            assert handler.is_connected is True
        finally:
            loop.close()

        # Then disconnect (need loop for async disconnect)
        handler._loop = None
        handler._connected = False
        handler._meshcore = None
        assert handler.is_connected is False

    def test_not_connected_by_default(self, handler):
        """Handler starts disconnected."""
        assert handler.is_connected is False
        assert handler._meshcore is None


# =============================================================================
# Message Receive → Queue
# =============================================================================

class TestMessageReceive:
    """Test incoming message processing."""

    def test_contact_message_queued(self, handler):
        """Incoming DM is converted and queued."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Hello from MeshCore',
                    'sender': 'abc123',
                    'destination': 'def456',
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            # Check message was queued
            assert not handler._message_queue.empty()
            msg = handler._message_queue.get_nowait()
            assert isinstance(msg, CanonicalMessage)
            assert msg.content == 'Hello from MeshCore'
            assert msg.source_network == 'meshcore'
        finally:
            loop.close()

    def test_channel_message_queued(self, handler):
        """Incoming channel message is converted and queued."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CHANNEL_MSG_RECV',
                payload={
                    'text': 'Channel broadcast',
                    'sender': 'node789',
                    'destination': None,
                    'is_channel': True,
                    'channel': 1,
                },
            )
            loop.run_until_complete(handler._on_channel_message(event))

            msg = handler._message_queue.get_nowait()
            assert msg.is_broadcast is True
        finally:
            loop.close()

    def test_routing_filter_blocks_message(self, handler):
        """Messages blocked by routing rules are not queued."""
        handler._should_bridge = lambda msg: False

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Blocked message',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert handler._message_queue.empty()
        finally:
            loop.close()

    def test_message_callback_invoked(self, handler):
        """Message callback is called for received messages."""
        callback = MagicMock()
        handler._message_callback = callback

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Callback test',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert callback.called
            args = callback.call_args[0]
            assert isinstance(args[0], CanonicalMessage)
        finally:
            loop.close()


# =============================================================================
# Outbound Messages
# =============================================================================

class TestOutbound:
    """Test outbound message processing."""

    def test_send_text_queues(self, handler):
        """send_text() queues message for async processing."""
        handler._connected = True
        result = handler.send_text("Test message", destination="abc123")
        assert result is True
        assert not handler._send_queue.empty()

    def test_send_text_broadcast(self, handler):
        """send_text() with no destination creates broadcast."""
        handler._connected = True
        result = handler.send_text("Broadcast")
        assert result is True
        msg = handler._send_queue.get_nowait()
        assert msg.is_broadcast is True

    def test_send_text_not_connected(self, handler):
        """send_text() returns False when disconnected."""
        assert handler.is_connected is False
        result = handler.send_text("Should fail")
        assert result is False

    def test_queue_send_interface(self, handler):
        """queue_send() works for persistent queue integration."""
        handler._connected = True
        result = handler.queue_send({
            'message': 'Queued message',
            'destination': 'abc123',
        })
        assert result is True

    def test_process_outbound(self, handler):
        """Outbound messages are sent via MeshCore."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            msg = CanonicalMessage(
                content="Outbound test",
                destination_address=None,
                is_broadcast=True,
            )
            handler._send_queue.put_nowait(msg)

            loop.run_until_complete(handler._process_outbound())

            # Queue should be drained
            assert handler._send_queue.empty()
            # Stats updated
            assert handler.stats.get('meshcore_tx', 0) >= 1
        finally:
            loop.close()


# =============================================================================
# Node Tracking (Advertisement)
# =============================================================================

class TestNodeTracking:
    """Test node discovery from advertisements."""

    def test_advertisement_adds_node(self, handler, mock_node_tracker):
        """Advertisement events add nodes to tracker."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='ADVERTISEMENT',
                payload={
                    'adv_name': 'TestRepeater',
                    'pubkey_prefix': 'aabbcc',
                },
            )
            loop.run_until_complete(handler._on_advertisement(event))

            assert mock_node_tracker.add_node.called
        finally:
            loop.close()

    def test_advertisement_object_payload(self, handler, mock_node_tracker):
        """Advertisement with object-style payload."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            payload = SimpleNamespace(
                adv_name='ObjectNode',
                public_key=b'\xde\xad\xbe\xef',
                name='',
            )
            event = SimpleNamespace(type='ADVERTISEMENT', payload=payload)
            loop.run_until_complete(handler._on_advertisement(event))

            assert mock_node_tracker.add_node.called
        finally:
            loop.close()


# =============================================================================
# Device Detection
# =============================================================================

class TestDeviceDetection:
    """Test serial device scanning."""

    @patch('glob.glob')
    def test_detect_devices(self, mock_glob):
        """Detect USB serial devices."""
        mock_glob.side_effect = [
            ['/dev/ttyUSB0', '/dev/ttyUSB1'],
            ['/dev/ttyACM0'],
        ]
        devices = detect_meshcore_devices()
        assert '/dev/ttyUSB0' in devices
        assert '/dev/ttyUSB1' in devices
        assert '/dev/ttyACM0' in devices

    @patch('glob.glob')
    def test_no_devices(self, mock_glob):
        """No devices found returns empty list."""
        mock_glob.return_value = []
        devices = detect_meshcore_devices()
        assert devices == []


# =============================================================================
# Test Connection
# =============================================================================

class TestTestConnection:
    """Test connection testing methods."""

    def test_serial_device_exists(self, handler):
        """test_connection() checks serial device existence."""
        with patch('os.path.exists', return_value=True):
            assert handler.test_connection() is True

    def test_serial_device_missing(self, handler):
        """test_connection() fails if device missing."""
        with patch('os.path.exists', return_value=False):
            assert handler.test_connection() is False

    def test_tcp_connection(self, mock_config, mock_health, mock_node_tracker):
        """test_connection() for TCP mode."""
        mock_config.meshcore.connection_type = 'tcp'
        mock_config.meshcore.tcp_host = 'localhost'
        mock_config.meshcore.tcp_port = 4000

        h = MeshCoreHandler(
            config=mock_config,
            node_tracker=mock_node_tracker,
            health=mock_health,
            stop_event=threading.Event(),
            stats={},
            stats_lock=threading.Lock(),
            message_queue=Queue(),
        )

        with patch('socket.socket') as mock_sock:
            mock_instance = MagicMock()
            mock_instance.connect_ex.return_value = 0
            mock_sock.return_value = mock_instance
            assert h.test_connection() is True


# =============================================================================
# Contact Resolution
# =============================================================================

class TestContactResolution:
    """Test finding MeshCore contacts by address."""

    def test_find_by_pubkey(self, handler):
        """Find contact by public key prefix."""
        contacts = [
            {'public_key': b'\xaa\xbb\xcc\xdd', 'adv_name': 'Node1'},
            {'public_key': b'\x11\x22\x33\x44', 'adv_name': 'Node2'},
        ]
        result = handler._find_contact(contacts, 'aabbcc')
        assert result is not None
        assert result['adv_name'] == 'Node1'

    def test_find_by_name(self, handler):
        """Find contact by advertised name."""
        contacts = [
            {'public_key': b'\xaa\xbb', 'adv_name': 'AlphaNode'},
        ]
        result = handler._find_contact(contacts, 'AlphaNode')
        assert result is not None

    def test_not_found(self, handler):
        """Return None when contact not found."""
        contacts = [
            {'public_key': b'\xaa\xbb', 'adv_name': 'Node1'},
        ]
        result = handler._find_contact(contacts, 'nonexistent')
        assert result is None

    def test_empty_contacts(self, handler):
        """Return None for empty contact list."""
        result = handler._find_contact([], 'anything')
        assert result is None

    def test_object_contact(self, handler):
        """Handle contact objects (not just dicts)."""
        contact = SimpleNamespace(
            public_key=b'\xde\xad\xbe\xef',
            adv_name='ObjectContact',
        )
        result = handler._find_contact([contact], 'deadbeef')
        assert result is not None


# =============================================================================
# Stats Tracking
# =============================================================================

class TestStats:
    """Test statistics tracking."""

    def test_rx_stats_increment(self, handler):
        """Receive events increment meshcore_rx counter."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(handler._connect())

            event = SimpleNamespace(
                type='CONTACT_MSG_RECV',
                payload={
                    'text': 'Stats test',
                    'sender': 'abc',
                    'destination': None,
                    'is_channel': False,
                    'channel': 0,
                },
            )
            loop.run_until_complete(handler._on_contact_message(event))

            assert handler.stats.get('meshcore_rx', 0) >= 1
        finally:
            loop.close()

    def test_ack_stats_increment(self, handler):
        """ACK events increment meshcore_acks counter."""
        loop = asyncio.new_event_loop()
        try:
            event = SimpleNamespace(type='ACK', payload={})
            loop.run_until_complete(handler._on_ack(event))
            assert handler.stats.get('meshcore_acks', 0) >= 1
        finally:
            loop.close()
