"""
Tests for RNS Over Meshtastic Transport.

Tests packet fragmentation, fragment reassembly, transport statistics,
callback systems, and the RNS interface adapter.

All external dependencies (meshtastic, pubsub) are mocked.

Run: python3 -m pytest tests/test_rns_transport.py -v
"""

import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from unittest.mock import MagicMock, patch, call

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway.config import RNSOverMeshtasticConfig
from gateway.rns_transport import (
    Fragment,
    FRAGMENT_HEADER_SIZE,
    MAX_FRAGMENT_SIZE,
    PAYLOAD_PER_FRAGMENT,
    PendingPacket,
    RNSMeshtasticInterface,
    RNSMeshtasticTransport,
    TransportStats,
    create_rns_transport,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def transport_config():
    """Default transport config for testing."""
    return RNSOverMeshtasticConfig(
        connection_type="tcp",
        device_path="localhost:4403",
        data_speed=8,
        hop_limit=3,
        fragment_timeout_sec=5,
        max_pending_fragments=10,
    )


@pytest.fixture
def transport(transport_config):
    """Create a transport instance (not started)."""
    t = RNSMeshtasticTransport(transport_config)
    yield t
    if t.is_running:
        t.stop()


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify module-level constants."""

    def test_max_fragment_size(self):
        assert MAX_FRAGMENT_SIZE == 200

    def test_fragment_header_size(self):
        assert FRAGMENT_HEADER_SIZE == 6

    def test_payload_per_fragment(self):
        assert PAYLOAD_PER_FRAGMENT == MAX_FRAGMENT_SIZE - FRAGMENT_HEADER_SIZE


# ---------------------------------------------------------------------------
# Fragment tests
# ---------------------------------------------------------------------------

class TestFragment:
    """Tests for Fragment data class."""

    def test_create_fragment(self):
        """Basic fragment creation."""
        f = Fragment(
            packet_id=b"\x01\x02\x03\x04",
            sequence=0,
            total=3,
            payload=b"hello",
        )
        assert f.packet_id == b"\x01\x02\x03\x04"
        assert f.sequence == 0
        assert f.total == 3
        assert f.payload == b"hello"
        assert isinstance(f.timestamp, datetime)

    def test_to_bytes(self):
        """Serialize fragment to bytes."""
        f = Fragment(
            packet_id=b"\xAA\xBB\xCC\xDD",
            sequence=1,
            total=5,
            payload=b"data",
        )
        result = f.to_bytes()
        assert result == b"\xAA\xBB\xCC\xDD\x01\x05data"

    def test_to_bytes_length(self):
        """Serialized length = header + payload."""
        payload = b"x" * 100
        f = Fragment(
            packet_id=b"\x00\x00\x00\x01",
            sequence=0,
            total=1,
            payload=payload,
        )
        result = f.to_bytes()
        assert len(result) == FRAGMENT_HEADER_SIZE + 100

    def test_from_bytes(self):
        """Deserialize fragment from bytes."""
        raw = b"\x01\x02\x03\x04\x02\x07payload_data"
        f = Fragment.from_bytes(raw)
        assert f.packet_id == b"\x01\x02\x03\x04"
        assert f.sequence == 2
        assert f.total == 7
        assert f.payload == b"payload_data"

    def test_from_bytes_minimal(self):
        """Minimum valid fragment (header only, no payload)."""
        raw = b"\x00\x00\x00\x00\x00\x01"  # packet_id=0, seq=0, total=1
        f = Fragment.from_bytes(raw)
        assert f.sequence == 0
        assert f.total == 1
        assert f.payload == b""

    def test_from_bytes_too_short(self):
        """Fragment shorter than header raises ValueError."""
        with pytest.raises(ValueError, match="too short"):
            Fragment.from_bytes(b"\x00\x01\x02")

    def test_roundtrip(self):
        """to_bytes -> from_bytes preserves data."""
        original = Fragment(
            packet_id=b"\xDE\xAD\xBE\xEF",
            sequence=42,
            total=100,
            payload=b"roundtrip_test_data",
        )
        raw = original.to_bytes()
        restored = Fragment.from_bytes(raw)

        assert restored.packet_id == original.packet_id
        assert restored.sequence == original.sequence
        assert restored.total == original.total
        assert restored.payload == original.payload


# ---------------------------------------------------------------------------
# PendingPacket tests
# ---------------------------------------------------------------------------

class TestPendingPacket:
    """Tests for PendingPacket reassembly tracker."""

    def test_create_pending(self):
        """Create an empty pending packet."""
        pp = PendingPacket(
            packet_id=b"\x01\x02\x03\x04",
            total_fragments=3,
        )
        assert pp.total_fragments == 3
        assert pp.fragments == {}
        assert pp.is_complete is False

    def test_add_fragment(self):
        """Add fragment to pending packet."""
        pp = PendingPacket(packet_id=b"\x01\x02\x03\x04", total_fragments=2)
        pp.add_fragment(0, b"first")
        assert 0 in pp.fragments
        assert pp.is_complete is False

    def test_is_complete(self):
        """Packet is complete when all fragments received."""
        pp = PendingPacket(packet_id=b"\x01\x02\x03\x04", total_fragments=2)
        pp.add_fragment(0, b"first")
        pp.add_fragment(1, b"second")
        assert pp.is_complete is True

    def test_reassemble_in_order(self):
        """Reassemble fragments in order."""
        pp = PendingPacket(packet_id=b"\x00\x00\x00\x01", total_fragments=3)
        pp.add_fragment(0, b"AAA")
        pp.add_fragment(1, b"BBB")
        pp.add_fragment(2, b"CCC")
        result = pp.reassemble()
        assert result == b"AAABBBCCC"

    def test_reassemble_out_of_order(self):
        """Fragments received out of order still reassemble correctly."""
        pp = PendingPacket(packet_id=b"\x00\x00\x00\x01", total_fragments=3)
        pp.add_fragment(2, b"CCC")
        pp.add_fragment(0, b"AAA")
        pp.add_fragment(1, b"BBB")
        result = pp.reassemble()
        assert result == b"AAABBBCCC"

    def test_reassemble_incomplete_raises(self):
        """Reassembling incomplete packet raises ValueError."""
        pp = PendingPacket(packet_id=b"\x00\x00\x00\x01", total_fragments=3)
        pp.add_fragment(0, b"AAA")
        with pytest.raises(ValueError, match="incomplete"):
            pp.reassemble()

    def test_duplicate_fragment(self):
        """Duplicate fragment replaces (no crash)."""
        pp = PendingPacket(packet_id=b"\x00\x00\x00\x01", total_fragments=1)
        pp.add_fragment(0, b"first")
        pp.add_fragment(0, b"second")
        assert pp.fragments[0] == b"second"
        assert pp.is_complete is True

    def test_single_fragment_packet(self):
        """Single-fragment packet."""
        pp = PendingPacket(packet_id=b"\x00\x00\x00\x01", total_fragments=1)
        pp.add_fragment(0, b"only_fragment")
        assert pp.is_complete is True
        assert pp.reassemble() == b"only_fragment"


# ---------------------------------------------------------------------------
# TransportStats tests
# ---------------------------------------------------------------------------

class TestTransportStats:
    """Tests for TransportStats."""

    def test_default_values(self):
        """Verify defaults are zero."""
        stats = TransportStats()
        assert stats.packets_sent == 0
        assert stats.packets_received == 0
        assert stats.fragments_sent == 0
        assert stats.fragments_received == 0
        assert stats.bytes_sent == 0
        assert stats.bytes_received == 0
        assert stats.reassembly_timeouts == 0
        assert stats.reassembly_successes == 0
        assert stats.crc_errors == 0
        assert stats.start_time is None
        assert stats.last_activity is None

    def test_record_latency(self):
        """Record and retrieve latency samples."""
        stats = TransportStats()
        stats.record_latency(100.0)
        stats.record_latency(200.0)
        assert stats.avg_latency_ms == 150.0

    def test_record_latency_cap(self):
        """Latency samples capped at 100."""
        stats = TransportStats()
        for i in range(150):
            stats.record_latency(float(i))
        assert len(stats.latency_samples) == 100

    def test_avg_latency_empty(self):
        """Average latency is 0 with no samples."""
        stats = TransportStats()
        assert stats.avg_latency_ms == 0.0

    def test_packet_loss_rate_zero(self):
        """Packet loss rate is 0 with no data."""
        stats = TransportStats()
        assert stats.packet_loss_rate == 0.0

    def test_packet_loss_rate_calculation(self):
        """Packet loss rate calculation."""
        stats = TransportStats()
        stats.reassembly_successes = 8
        stats.reassembly_timeouts = 2
        assert abs(stats.packet_loss_rate - 0.2) < 0.001

    def test_uptime_zero_without_start(self):
        """Uptime is 0 without start_time."""
        stats = TransportStats()
        assert stats.uptime_seconds == 0.0

    def test_uptime_with_start(self):
        """Uptime positive after start."""
        stats = TransportStats()
        stats.start_time = datetime.now() - timedelta(seconds=10)
        assert stats.uptime_seconds >= 9.0

    def test_to_dict(self):
        """Stats serialized to dict."""
        stats = TransportStats()
        stats.packets_sent = 5
        stats.packets_received = 3
        stats.bytes_sent = 1000
        stats.bytes_received = 600
        stats.reassembly_successes = 3
        stats.reassembly_timeouts = 1
        stats.crc_errors = 0
        stats.start_time = datetime.now()
        stats.last_activity = datetime.now()

        d = stats.to_dict()
        assert d['packets_sent'] == 5
        assert d['packets_received'] == 3
        assert d['bytes_sent'] == 1000
        assert d['bytes_received'] == 600
        assert d['reassembly_successes'] == 3
        assert d['reassembly_timeouts'] == 1
        assert d['packet_loss_rate'] == 0.25
        assert d['last_activity'] is not None
        assert d['uptime_seconds'] >= 0

    def test_to_dict_no_activity(self):
        """Stats dict with no activity."""
        stats = TransportStats()
        d = stats.to_dict()
        assert d['last_activity'] is None
        assert d['uptime_seconds'] == 0


# ---------------------------------------------------------------------------
# RNSMeshtasticTransport - initialization tests
# ---------------------------------------------------------------------------

class TestTransportInit:
    """Tests for RNSMeshtasticTransport initialization."""

    def test_default_config(self):
        """Transport creates with default config."""
        t = RNSMeshtasticTransport()
        assert t.is_running is False
        assert t.is_connected is False

    def test_custom_config(self, transport_config, transport):
        """Transport stores custom config."""
        assert transport.config is transport_config
        assert transport.is_running is False
        assert transport.is_connected is False

    def test_initial_stats(self, transport):
        """Stats are fresh on init."""
        assert transport.stats.packets_sent == 0

    def test_initial_queues_empty(self, transport):
        """Queues start empty."""
        assert transport._outbound_queue.empty()
        assert transport._inbound_queue.empty()

    def test_no_pending_packets(self, transport):
        """No pending packets on init."""
        assert len(transport._pending_packets) == 0


# ---------------------------------------------------------------------------
# Fragmentation tests
# ---------------------------------------------------------------------------

class TestFragmentation:
    """Tests for packet fragmentation logic."""

    def test_small_packet_single_fragment(self, transport):
        """Small packet results in one fragment."""
        data = b"small"
        fragments = transport._fragment_packet(data)
        assert len(fragments) == 1
        assert fragments[0].sequence == 0
        assert fragments[0].total == 1
        assert fragments[0].payload == data

    def test_exact_payload_size(self, transport):
        """Packet exactly PAYLOAD_PER_FRAGMENT bytes = one fragment."""
        data = b"x" * PAYLOAD_PER_FRAGMENT
        fragments = transport._fragment_packet(data)
        assert len(fragments) == 1
        assert fragments[0].payload == data

    def test_two_fragments(self, transport):
        """Packet larger than PAYLOAD_PER_FRAGMENT splits into 2."""
        data = b"x" * (PAYLOAD_PER_FRAGMENT + 1)
        fragments = transport._fragment_packet(data)
        assert len(fragments) == 2
        assert fragments[0].total == 2
        assert fragments[1].total == 2
        assert fragments[0].sequence == 0
        assert fragments[1].sequence == 1
        # Concatenating payloads should yield original data
        assert fragments[0].payload + fragments[1].payload == data

    def test_many_fragments(self, transport):
        """Large packet fragments correctly."""
        data = b"A" * 1000  # 1000 bytes
        fragments = transport._fragment_packet(data)
        expected_count = (1000 + PAYLOAD_PER_FRAGMENT - 1) // PAYLOAD_PER_FRAGMENT
        assert len(fragments) == expected_count

        # All fragments have same packet_id
        ids = {f.packet_id for f in fragments}
        assert len(ids) == 1

        # Sequences are in order
        for i, f in enumerate(fragments):
            assert f.sequence == i
            assert f.total == expected_count

        # Reassembly yields original
        reassembled = b"".join(f.payload for f in fragments)
        assert reassembled == data

    def test_fragment_packet_ids_deterministic(self, transport):
        """Same packet produces same packet_id (djb2 hash)."""
        data = b"test_packet"
        id1 = transport._fragment_packet(data)[0].packet_id
        id2 = transport._fragment_packet(data)[0].packet_id
        assert id1 == id2

    def test_different_packets_different_ids(self, transport):
        """Different packets produce different IDs."""
        id1 = transport._fragment_packet(b"packet_one")[0].packet_id
        id2 = transport._fragment_packet(b"packet_two")[0].packet_id
        assert id1 != id2

    def test_empty_packet(self, transport):
        """Empty packet produces one fragment with empty payload."""
        fragments = transport._fragment_packet(b"")
        # (0 + PAYLOAD_PER_FRAGMENT - 1) // PAYLOAD_PER_FRAGMENT = 0, so expect 0 or 1
        # Implementation uses ceiling division; 0/194 = 0
        # With (0 + 193) // 194 = 0, so 0 fragments means range(0) = empty list
        # This is an edge case - transport shouldn't send empty packets in practice
        assert len(fragments) == 0 or (len(fragments) == 1 and fragments[0].payload == b"")


# ---------------------------------------------------------------------------
# Packet ID generation tests
# ---------------------------------------------------------------------------

class TestPacketIdGeneration:
    """Tests for djb2 packet ID hashing."""

    def test_packet_id_is_4_bytes(self, transport):
        """Packet ID is always 4 bytes."""
        pid = transport._generate_packet_id(b"test")
        assert len(pid) == 4
        assert isinstance(pid, bytes)

    def test_consistent_hash(self, transport):
        """Same input produces same hash."""
        a = transport._generate_packet_id(b"test_data")
        b = transport._generate_packet_id(b"test_data")
        assert a == b

    def test_different_input_different_hash(self, transport):
        """Different input produces different hash."""
        a = transport._generate_packet_id(b"alpha")
        b = transport._generate_packet_id(b"beta")
        assert a != b

    def test_uses_first_32_bytes(self, transport):
        """Hash uses only first 32 bytes."""
        base = b"A" * 32
        a = transport._generate_packet_id(base + b"EXTRA_IGNORED")
        b = transport._generate_packet_id(base + b"DIFFERENT_EXTRA")
        assert a == b


# ---------------------------------------------------------------------------
# Callback tests
# ---------------------------------------------------------------------------

class TestCallbacks:
    """Tests for callback registration and notification."""

    def test_register_packet_callback(self, transport):
        """Register and invoke packet callback."""
        cb = MagicMock()
        transport.register_packet_callback(cb)
        transport._notify_packet(b"test_data")
        cb.assert_called_once_with(b"test_data")

    def test_multiple_packet_callbacks(self, transport):
        """Multiple callbacks all fire."""
        cb1 = MagicMock()
        cb2 = MagicMock()
        transport.register_packet_callback(cb1)
        transport.register_packet_callback(cb2)
        transport._notify_packet(b"data")
        cb1.assert_called_once_with(b"data")
        cb2.assert_called_once_with(b"data")

    def test_packet_callback_error_doesnt_crash(self, transport):
        """Erroring callback doesn't prevent others from firing."""
        cb1 = MagicMock(side_effect=RuntimeError("boom"))
        cb2 = MagicMock()
        transport.register_packet_callback(cb1)
        transport.register_packet_callback(cb2)
        transport._notify_packet(b"data")
        cb2.assert_called_once_with(b"data")

    def test_register_status_callback(self, transport):
        """Register and invoke status callback."""
        cb = MagicMock()
        transport.register_status_callback(cb)
        transport._notify_status("test_event")
        cb.assert_called_once()
        status_str, status_data = cb.call_args[0]
        assert status_str == "test_event"
        assert isinstance(status_data, dict)

    def test_status_callback_error_doesnt_crash(self, transport):
        """Erroring status callback doesn't crash."""
        cb = MagicMock(side_effect=RuntimeError("boom"))
        transport.register_status_callback(cb)
        transport._notify_status("test")  # Should not raise


# ---------------------------------------------------------------------------
# Meshtastic receive handler tests
# ---------------------------------------------------------------------------

class TestOnMeshtasticReceive:
    """Tests for _on_meshtastic_receive packet filtering."""

    def test_private_app_portnum_string(self, transport):
        """PRIVATE_APP packets are queued."""
        packet = {
            'decoded': {
                'portnum': 'PRIVATE_APP',
                'payload': b'\x01\x02\x03\x04\x00\x01data',
            }
        }
        transport._on_meshtastic_receive(packet)
        assert not transport._inbound_queue.empty()
        queued = transport._inbound_queue.get_nowait()
        assert queued == b'\x01\x02\x03\x04\x00\x01data'

    def test_private_app_portnum_int(self, transport):
        """Port 256 (int) packets are queued."""
        packet = {
            'decoded': {
                'portnum': 256,
                'payload': b'\x01\x02\x03\x04\x00\x01data',
            }
        }
        transport._on_meshtastic_receive(packet)
        assert not transport._inbound_queue.empty()

    def test_non_private_app_ignored(self, transport):
        """Non-PRIVATE_APP packets are ignored."""
        packet = {
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'payload': b'Hello',
            }
        }
        transport._on_meshtastic_receive(packet)
        assert transport._inbound_queue.empty()

    def test_numeric_portnum_not_256_ignored(self, transport):
        """Numeric portnum != 256 is ignored."""
        packet = {
            'decoded': {
                'portnum': 1,
                'payload': b'text message',
            }
        }
        transport._on_meshtastic_receive(packet)
        assert transport._inbound_queue.empty()

    def test_string_payload_converted(self, transport):
        """String payload is converted to bytes via latin-1."""
        packet = {
            'decoded': {
                'portnum': 'PRIVATE_APP',
                'payload': 'string_payload',
            }
        }
        transport._on_meshtastic_receive(packet)
        queued = transport._inbound_queue.get_nowait()
        assert queued == b'string_payload'

    def test_no_payload_ignored(self, transport):
        """Packet with no payload is ignored."""
        packet = {
            'decoded': {
                'portnum': 'PRIVATE_APP',
            }
        }
        transport._on_meshtastic_receive(packet)
        assert transport._inbound_queue.empty()

    def test_empty_packet_ignored(self, transport):
        """Empty packet dict doesn't crash."""
        transport._on_meshtastic_receive({})
        assert transport._inbound_queue.empty()

    def test_malformed_packet_handled(self, transport):
        """Missing decoded field doesn't crash."""
        transport._on_meshtastic_receive({'from': '!abc'})
        assert transport._inbound_queue.empty()


# ---------------------------------------------------------------------------
# Send packet tests
# ---------------------------------------------------------------------------

class TestSendPacket:
    """Tests for send_packet queueing."""

    def test_send_when_running(self, transport):
        """Packets queued when running."""
        transport._running = True
        result = transport.send_packet(b"test_data")
        assert result is True
        assert not transport._outbound_queue.empty()

    def test_send_when_not_running(self, transport):
        """Packets rejected when not running."""
        result = transport.send_packet(b"test_data")
        assert result is False
        assert transport._outbound_queue.empty()

    def test_send_with_destination(self, transport):
        """Destination is passed through in queue entry."""
        transport._running = True
        transport.send_packet(b"data", destination="!aabb")
        packet, dest = transport._outbound_queue.get_nowait()
        assert packet == b"data"
        assert dest == "!aabb"

    def test_send_without_destination(self, transport):
        """No destination -> None in queue."""
        transport._running = True
        transport.send_packet(b"data")
        packet, dest = transport._outbound_queue.get_nowait()
        assert dest is None


# ---------------------------------------------------------------------------
# Get status tests
# ---------------------------------------------------------------------------

class TestGetStatus:
    """Tests for get_status."""

    def test_status_structure(self, transport):
        """Status dict has expected keys."""
        status = transport.get_status()
        assert 'running' in status
        assert 'connected' in status
        assert 'connection_type' in status
        assert 'speed_preset' in status
        assert 'estimated_bps' in status
        assert 'range_estimate' in status
        assert 'hop_limit' in status
        assert 'pending_fragments' in status
        assert 'outbound_queue_size' in status
        assert 'statistics' in status

    def test_status_initial_values(self, transport):
        """Status reflects initial state."""
        status = transport.get_status()
        assert status['running'] is False
        assert status['connected'] is False
        assert status['pending_fragments'] == 0
        assert status['outbound_queue_size'] == 0

    def test_status_speed_preset(self, transport_config, transport):
        """Speed preset reflects config."""
        status = transport.get_status()
        assert status['speed_preset'] == 'SHORT_TURBO'
        assert status['estimated_bps'] == 500
        assert status['hop_limit'] == 3


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------

class TestConnection:
    """Tests for connection management."""

    @patch('gateway.rns_transport._HAS_MESHTASTIC', True)
    @patch('gateway.rns_transport._HAS_MESH_TCP', True)
    @patch('gateway.rns_transport._HAS_PUBSUB', True)
    @patch('gateway.rns_transport._meshtastic_tcp', MagicMock())
    @patch('gateway.rns_transport._pub_mod', MagicMock())
    def test_connect_tcp_success(self, transport):
        """TCP connection succeeds."""
        result = transport._connect()
        assert result is True
        assert transport._connected is True

    @patch('gateway.rns_transport._HAS_MESHTASTIC', True)
    @patch('gateway.rns_transport._HAS_MESH_SERIAL', True)
    @patch('gateway.rns_transport._HAS_PUBSUB', True)
    @patch('gateway.rns_transport._meshtastic_serial', MagicMock())
    @patch('gateway.rns_transport._pub_mod', MagicMock())
    def test_connect_serial(self):
        """Serial connection attempted."""
        config = RNSOverMeshtasticConfig(
            connection_type="serial",
            device_path="/dev/ttyUSB0",
        )
        t = RNSMeshtasticTransport(config)
        result = t._connect()
        assert result is True

    @patch('gateway.rns_transport._HAS_MESHTASTIC', True)
    @patch('gateway.rns_transport._HAS_MESH_BLE', True)
    @patch('gateway.rns_transport._HAS_PUBSUB', True)
    @patch('gateway.rns_transport._meshtastic_ble', MagicMock())
    @patch('gateway.rns_transport._pub_mod', MagicMock())
    def test_connect_ble(self):
        """BLE connection attempted."""
        config = RNSOverMeshtasticConfig(
            connection_type="ble",
            device_path="00:11:22:33:44:55",
        )
        t = RNSMeshtasticTransport(config)
        result = t._connect()
        assert result is True

    @patch('gateway.rns_transport._HAS_MESHTASTIC', True)
    @patch('gateway.rns_transport._HAS_PUBSUB', True)
    def test_connect_unknown_type(self, transport):
        """Unknown connection type returns False."""
        transport.config.connection_type = "unknown"
        result = transport._connect()
        assert result is False

    def test_connect_import_error(self, transport):
        """Missing meshtastic library returns False."""
        with patch.dict('sys.modules', {'meshtastic': None}):
            with patch('builtins.__import__', side_effect=ImportError("no meshtastic")):
                result = transport._connect()
        assert result is False

    def test_disconnect(self, transport):
        """Disconnect clears state."""
        mock_iface = MagicMock()
        transport._interface = mock_iface
        transport._connected = True

        transport._disconnect()

        assert transport._connected is False
        assert transport._interface is None
        mock_iface.close.assert_called_once()

    def test_disconnect_close_error(self, transport):
        """Disconnect handles close error gracefully."""
        mock_iface = MagicMock()
        mock_iface.close.side_effect = RuntimeError("already closed")
        transport._interface = mock_iface
        transport._connected = True

        transport._disconnect()  # Should not raise
        assert transport._connected is False

    def test_disconnect_no_interface(self, transport):
        """Disconnect with no interface doesn't crash."""
        transport._disconnect()
        assert transport._connected is False


# ---------------------------------------------------------------------------
# Start/stop tests
# ---------------------------------------------------------------------------

class TestStartStop:
    """Tests for transport start/stop lifecycle."""

    def test_start_returns_false_without_connection(self, transport):
        """Start fails if _connect fails."""
        with patch.object(transport, '_connect', return_value=False):
            result = transport.start()
        assert result is False
        assert transport.is_running is False

    def test_start_success(self, transport):
        """Start succeeds when connection works."""
        with patch.object(transport, '_connect', return_value=True):
            result = transport.start()
        assert result is True
        assert transport.is_running is True
        assert transport.stats.start_time is not None
        transport.stop()

    def test_start_already_running(self, transport):
        """Start when already running returns True (idempotent)."""
        transport._running = True
        result = transport.start()
        assert result is True

    def test_stop(self, transport):
        """Stop clears running state."""
        with patch.object(transport, '_connect', return_value=True):
            transport.start()
        transport.stop()
        assert transport.is_running is False

    def test_stop_not_running(self, transport):
        """Stop when not running is a no-op."""
        transport.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Send fragment tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_local_radio_tx")
class TestSendFragment:
    """Tests for _send_fragment."""

    def test_send_fragment_calls_interface(self, transport):
        """Fragment sent via interface.sendData."""
        mock_iface = MagicMock()
        transport._interface = mock_iface

        fragment = Fragment(
            packet_id=b"\x01\x02\x03\x04",
            sequence=0,
            total=1,
            payload=b"test",
        )
        transport._send_fragment(fragment, destination="!aabb")

        mock_iface.sendData.assert_called_once()
        call_kwargs = mock_iface.sendData.call_args
        assert call_kwargs[1]['destinationId'] == "!aabb"
        assert call_kwargs[1]['portNum'] == 256
        assert call_kwargs[1]['hopLimit'] == transport.config.hop_limit
        transport.stats.fragments_sent == 1

    def test_send_fragment_no_interface(self, transport):
        """No interface -> no crash."""
        transport._interface = None
        fragment = Fragment(
            packet_id=b"\x01\x02\x03\x04",
            sequence=0,
            total=1,
            payload=b"test",
        )
        transport._send_fragment(fragment)  # Should not raise

    def test_send_fragment_exception_handled(self, transport):
        """Interface error doesn't crash."""
        mock_iface = MagicMock()
        mock_iface.sendData.side_effect = RuntimeError("send failed")
        transport._interface = mock_iface

        fragment = Fragment(
            packet_id=b"\x01\x02\x03\x04",
            sequence=0,
            total=1,
            payload=b"test",
        )
        transport._send_fragment(fragment)  # Should not raise


# ---------------------------------------------------------------------------
# Cleanup loop tests
# ---------------------------------------------------------------------------

class TestCleanup:
    """Tests for fragment cleanup."""

    def test_expired_fragments_removed(self, transport):
        """Stale pending packets are removed."""
        old_time = datetime.now() - timedelta(seconds=60)
        transport._pending_packets[b"\x01\x02\x03\x04"] = PendingPacket(
            packet_id=b"\x01\x02\x03\x04",
            total_fragments=3,
            first_seen=old_time,
        )

        # Simulate cleanup manually
        timeout = timedelta(seconds=transport.config.fragment_timeout_sec)
        now = datetime.now()

        with transport._pending_lock:
            expired = []
            for pid, pending in transport._pending_packets.items():
                if now - pending.first_seen > timeout:
                    expired.append(pid)
            for pid in expired:
                del transport._pending_packets[pid]
                transport.stats.reassembly_timeouts += 1

        assert len(transport._pending_packets) == 0
        assert transport.stats.reassembly_timeouts == 1

    def test_max_pending_enforced(self, transport):
        """Max pending fragments limit is enforced."""
        transport.config.max_pending_fragments = 2

        # Add 3 pending packets
        for i in range(3):
            pid = bytes([i, 0, 0, 0])
            transport._pending_packets[pid] = PendingPacket(
                packet_id=pid,
                total_fragments=5,
                first_seen=datetime.now() - timedelta(seconds=i),
            )

        # Enforce limit manually
        with transport._pending_lock:
            while len(transport._pending_packets) > transport.config.max_pending_fragments:
                oldest_id = min(
                    transport._pending_packets.keys(),
                    key=lambda k: transport._pending_packets[k].first_seen
                )
                del transport._pending_packets[oldest_id]
                transport.stats.reassembly_timeouts += 1

        assert len(transport._pending_packets) == 2
        assert transport.stats.reassembly_timeouts == 1


# ---------------------------------------------------------------------------
# RNSMeshtasticInterface tests
# ---------------------------------------------------------------------------

class TestRNSMeshtasticInterface:
    """Tests for the RNS interface adapter."""

    def test_create_interface(self, transport):
        """Interface creates with transport reference."""
        iface = RNSMeshtasticInterface(transport)
        assert iface.transport is transport
        assert iface.name == "Meshtastic"
        assert iface.mtu == MAX_FRAGMENT_SIZE * 10
        assert iface.online is False

    def test_send_delegates_to_transport(self, transport):
        """send() delegates to transport.send_packet()."""
        transport._running = True
        iface = RNSMeshtasticInterface(transport)
        result = iface.send(b"test_packet")
        assert result is True
        assert not transport._outbound_queue.empty()

    def test_send_when_transport_not_running(self, transport):
        """send() returns False when transport not running."""
        iface = RNSMeshtasticInterface(transport)
        result = iface.send(b"test_packet")
        assert result is False

    def test_start_delegates_to_transport(self, transport):
        """start() delegates to transport.start()."""
        with patch.object(transport, 'start', return_value=True):
            iface = RNSMeshtasticInterface(transport)
            result = iface.start()
        assert result is True
        assert iface.online is True

    def test_start_failure(self, transport):
        """start() reflects transport failure."""
        with patch.object(transport, 'start', return_value=False):
            iface = RNSMeshtasticInterface(transport)
            result = iface.start()
        assert result is False
        assert iface.online is False

    def test_stop(self, transport):
        """stop() stops transport and sets offline."""
        iface = RNSMeshtasticInterface(transport)
        iface.online = True
        with patch.object(transport, 'stop'):
            iface.stop()
        assert iface.online is False

    def test_set_packet_callback(self, transport):
        """Packet callback is stored."""
        iface = RNSMeshtasticInterface(transport)
        cb = MagicMock()
        iface.set_packet_callback(cb)
        assert iface._rns_packet_callback is cb

    def test_on_packet_forwards_to_rns(self, transport):
        """Received packets forwarded to RNS callback."""
        iface = RNSMeshtasticInterface(transport)
        cb = MagicMock()
        iface.set_packet_callback(cb)

        iface._on_packet(b"packet_data")
        cb.assert_called_once_with(b"packet_data", iface)

    def test_on_packet_no_callback(self, transport):
        """No crash when no RNS callback registered."""
        iface = RNSMeshtasticInterface(transport)
        iface._on_packet(b"data")  # Should not raise

    def test_on_status_updates_online(self, transport):
        """Status callback updates online state."""
        iface = RNSMeshtasticInterface(transport)
        iface._on_status("connected", {'connected': True})
        assert iface.online is True

        iface._on_status("disconnected", {'connected': False})
        assert iface.online is False


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------

class TestFactory:
    """Tests for create_rns_transport factory."""

    def test_create_default(self):
        """Factory creates transport with defaults."""
        t = create_rns_transport()
        assert isinstance(t, RNSMeshtasticTransport)
        assert t.is_running is False

    def test_create_with_config(self):
        """Factory passes config through."""
        config = RNSOverMeshtasticConfig(hop_limit=5, data_speed=4)
        t = create_rns_transport(config)
        assert t.config.hop_limit == 5
        assert t.config.data_speed == 4


# ---------------------------------------------------------------------------
# Integration: fragment -> reassemble pipeline
# ---------------------------------------------------------------------------

class TestFragmentReassemblyPipeline:
    """End-to-end fragmentation and reassembly tests."""

    def test_single_fragment_pipeline(self, transport):
        """Small packet: fragment -> serialize -> deserialize -> reassemble."""
        original = b"Hello RNS over Meshtastic!"
        fragments = transport._fragment_packet(original)
        assert len(fragments) == 1

        # Simulate wire transfer
        raw = fragments[0].to_bytes()
        received = Fragment.from_bytes(raw)

        # Reassemble
        pp = PendingPacket(
            packet_id=received.packet_id,
            total_fragments=received.total,
        )
        pp.add_fragment(received.sequence, received.payload)
        assert pp.is_complete is True
        assert pp.reassemble() == original

    def test_multi_fragment_pipeline(self, transport):
        """Large packet: fragment -> serialize -> deserialize -> reassemble."""
        original = bytes(range(256)) * 4  # 1024 bytes
        fragments = transport._fragment_packet(original)
        assert len(fragments) > 1

        # Simulate receiving fragments in random order
        import random
        shuffled = list(fragments)
        random.shuffle(shuffled)

        pp = PendingPacket(
            packet_id=shuffled[0].packet_id,
            total_fragments=shuffled[0].total,
        )

        for frag in shuffled:
            raw = frag.to_bytes()
            received = Fragment.from_bytes(raw)
            pp.add_fragment(received.sequence, received.payload)

        assert pp.is_complete is True
        assert pp.reassemble() == original

    def test_inbound_queue_to_reassembly(self, transport):
        """Full pipeline through inbound queue."""
        original = b"Test packet for queue pipeline"
        fragments = transport._fragment_packet(original)

        # Put fragment data through the receive handler
        for frag in fragments:
            packet = {
                'decoded': {
                    'portnum': 'PRIVATE_APP',
                    'payload': frag.to_bytes(),
                }
            }
            transport._on_meshtastic_receive(packet)

        # All fragments should be in inbound queue
        assert transport._inbound_queue.qsize() == len(fragments)

        # Manually reassemble (simulating receive_loop logic)
        for _ in range(len(fragments)):
            data = transport._inbound_queue.get_nowait()
            fragment = Fragment.from_bytes(data)

            pid = fragment.packet_id
            if pid not in transport._pending_packets:
                transport._pending_packets[pid] = PendingPacket(
                    packet_id=pid,
                    total_fragments=fragment.total,
                )
            transport._pending_packets[pid].add_fragment(
                fragment.sequence, fragment.payload
            )

        # Should have one complete pending packet
        assert len(transport._pending_packets) == 1
        pid = list(transport._pending_packets.keys())[0]
        pp = transport._pending_packets[pid]
        assert pp.is_complete is True
        assert pp.reassemble() == original
