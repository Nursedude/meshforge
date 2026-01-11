"""
Tests for RNS Over Meshtastic transport layer.

Run: python3 -m pytest tests/test_gateway_transport.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

from src.gateway.config import RNSOverMeshtasticConfig
from src.gateway.rns_transport import (
    Fragment,
    PendingPacket,
    TransportStats,
    RNSMeshtasticTransport,
    RNSMeshtasticInterface,
    create_rns_transport,
    MAX_FRAGMENT_SIZE,
    PAYLOAD_PER_FRAGMENT,
    FRAGMENT_HEADER_SIZE,
)


class TestRNSOverMeshtasticConfig:
    """Tests for RNSOverMeshtasticConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = RNSOverMeshtasticConfig()

        assert config.enabled is False
        assert config.connection_type == "tcp"
        assert config.device_path == "localhost:4403"
        assert config.data_speed == 8
        assert config.hop_limit == 3
        assert config.fragment_timeout_sec == 30
        assert config.max_pending_fragments == 100
        assert config.enable_stats is True
        assert config.stats_interval_sec == 60
        assert config.packet_loss_threshold == 0.1
        assert config.latency_threshold_ms == 5000

    def test_custom_values(self):
        """Test custom configuration values."""
        config = RNSOverMeshtasticConfig(
            enabled=True,
            connection_type="serial",
            device_path="/dev/ttyUSB0",
            data_speed=4,
            hop_limit=5,
            fragment_timeout_sec=60,
        )

        assert config.enabled is True
        assert config.connection_type == "serial"
        assert config.device_path == "/dev/ttyUSB0"
        assert config.data_speed == 4
        assert config.hop_limit == 5
        assert config.fragment_timeout_sec == 60

    def test_get_throughput_estimate_short_turbo(self):
        """Test throughput estimate for SHORT_TURBO preset."""
        config = RNSOverMeshtasticConfig(data_speed=8)
        throughput = config.get_throughput_estimate()

        assert throughput['name'] == 'SHORT_TURBO'
        assert throughput['bps'] == 500
        assert throughput['range'] == 'short'
        assert throughput['delay'] == 0.4

    def test_get_throughput_estimate_long_fast(self):
        """Test throughput estimate for LONG_FAST preset."""
        config = RNSOverMeshtasticConfig(data_speed=0)
        throughput = config.get_throughput_estimate()

        assert throughput['name'] == 'LONG_FAST'
        assert throughput['bps'] == 50
        assert throughput['range'] == 'maximum'

    def test_get_throughput_estimate_all_presets(self):
        """Test all speed presets return valid data."""
        for speed in range(9):
            config = RNSOverMeshtasticConfig(data_speed=speed)
            throughput = config.get_throughput_estimate()

            assert 'name' in throughput
            assert 'bps' in throughput
            assert 'range' in throughput
            assert 'delay' in throughput
            assert throughput['bps'] > 0

    def test_get_throughput_estimate_invalid_speed(self):
        """Test throughput estimate falls back for invalid speed."""
        config = RNSOverMeshtasticConfig(data_speed=99)
        throughput = config.get_throughput_estimate()

        # Should return SHORT_TURBO as default
        assert throughput['name'] == 'SHORT_TURBO'


class TestFragment:
    """Tests for Fragment dataclass."""

    def test_create_fragment(self):
        """Test creating a fragment."""
        frag = Fragment(
            packet_id=b'\x00\x01\x02\x03',
            sequence=0,
            total=5,
            payload=b'test data'
        )

        assert frag.packet_id == b'\x00\x01\x02\x03'
        assert frag.sequence == 0
        assert frag.total == 5
        assert frag.payload == b'test data'
        assert isinstance(frag.timestamp, datetime)

    def test_to_bytes(self):
        """Test fragment serialization."""
        frag = Fragment(
            packet_id=b'\xde\xad\xbe\xef',
            sequence=2,
            total=10,
            payload=b'hello'
        )

        data = frag.to_bytes()

        assert data[:4] == b'\xde\xad\xbe\xef'  # packet_id
        assert data[4] == 2   # sequence
        assert data[5] == 10  # total
        assert data[6:] == b'hello'  # payload

    def test_from_bytes(self):
        """Test fragment deserialization."""
        data = b'\xca\xfe\xba\xbe' + bytes([3, 7]) + b'payload data'

        frag = Fragment.from_bytes(data)

        assert frag.packet_id == b'\xca\xfe\xba\xbe'
        assert frag.sequence == 3
        assert frag.total == 7
        assert frag.payload == b'payload data'

    def test_from_bytes_too_short(self):
        """Test fragment deserialization fails for short data."""
        with pytest.raises(ValueError, match="too short"):
            Fragment.from_bytes(b'\x00\x01\x02')

    def test_round_trip(self):
        """Test serialization round-trip."""
        original = Fragment(
            packet_id=b'\x12\x34\x56\x78',
            sequence=5,
            total=20,
            payload=b'round trip test'
        )

        data = original.to_bytes()
        restored = Fragment.from_bytes(data)

        assert restored.packet_id == original.packet_id
        assert restored.sequence == original.sequence
        assert restored.total == original.total
        assert restored.payload == original.payload


class TestPendingPacket:
    """Tests for PendingPacket dataclass."""

    def test_create_pending_packet(self):
        """Test creating a pending packet."""
        pending = PendingPacket(
            packet_id=b'\x00\x01\x02\x03',
            total_fragments=5
        )

        assert pending.packet_id == b'\x00\x01\x02\x03'
        assert pending.total_fragments == 5
        assert len(pending.fragments) == 0
        assert pending.is_complete is False

    def test_add_fragment(self):
        """Test adding fragments."""
        pending = PendingPacket(
            packet_id=b'\x00\x00\x00\x00',
            total_fragments=3
        )

        pending.add_fragment(0, b'part1')
        pending.add_fragment(1, b'part2')

        assert len(pending.fragments) == 2
        assert pending.is_complete is False

        pending.add_fragment(2, b'part3')
        assert pending.is_complete is True

    def test_reassemble(self):
        """Test packet reassembly."""
        pending = PendingPacket(
            packet_id=b'\x00\x00\x00\x00',
            total_fragments=3
        )

        pending.add_fragment(0, b'AAA')
        pending.add_fragment(1, b'BBB')
        pending.add_fragment(2, b'CCC')

        result = pending.reassemble()
        assert result == b'AAABBBCCC'

    def test_reassemble_out_of_order(self):
        """Test reassembly with out-of-order fragments."""
        pending = PendingPacket(
            packet_id=b'\x00\x00\x00\x00',
            total_fragments=3
        )

        # Add out of order
        pending.add_fragment(2, b'CCC')
        pending.add_fragment(0, b'AAA')
        pending.add_fragment(1, b'BBB')

        result = pending.reassemble()
        assert result == b'AAABBBCCC'

    def test_reassemble_incomplete_raises(self):
        """Test reassembly fails for incomplete packet."""
        pending = PendingPacket(
            packet_id=b'\x00\x00\x00\x00',
            total_fragments=3
        )

        pending.add_fragment(0, b'part1')

        with pytest.raises(ValueError, match="incomplete"):
            pending.reassemble()


class TestTransportStats:
    """Tests for TransportStats dataclass."""

    def test_defaults(self):
        """Test default statistics values."""
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

    def test_packet_loss_rate_zero(self):
        """Test packet loss rate with no data."""
        stats = TransportStats()
        assert stats.packet_loss_rate == 0.0

    def test_packet_loss_rate_calculation(self):
        """Test packet loss rate calculation."""
        stats = TransportStats()
        stats.reassembly_successes = 90
        stats.reassembly_timeouts = 10

        assert stats.packet_loss_rate == 0.1  # 10%

    def test_avg_latency_no_samples(self):
        """Test average latency with no samples."""
        stats = TransportStats()
        assert stats.avg_latency_ms == 0.0

    def test_avg_latency_calculation(self):
        """Test average latency calculation."""
        stats = TransportStats()
        stats.record_latency(100.0)
        stats.record_latency(200.0)
        stats.record_latency(300.0)

        assert stats.avg_latency_ms == 200.0

    def test_latency_sample_limit(self):
        """Test latency samples are limited to 100."""
        stats = TransportStats()

        for i in range(150):
            stats.record_latency(float(i))

        assert len(stats.latency_samples) == 100
        # Should keep the last 100
        assert stats.latency_samples[0] == 50.0

    def test_uptime_seconds(self):
        """Test uptime calculation."""
        stats = TransportStats()
        stats.start_time = datetime.now() - timedelta(seconds=60)

        assert 59 <= stats.uptime_seconds <= 61

    def test_to_dict(self):
        """Test conversion to dictionary."""
        stats = TransportStats()
        stats.packets_sent = 10
        stats.packets_received = 5
        stats.start_time = datetime.now()

        data = stats.to_dict()

        assert data['packets_sent'] == 10
        assert data['packets_received'] == 5
        assert 'packet_loss_rate' in data
        assert 'avg_latency_ms' in data
        assert 'uptime_seconds' in data


class TestRNSMeshtasticTransport:
    """Tests for RNSMeshtasticTransport class."""

    def test_create_transport(self):
        """Test creating transport instance."""
        config = RNSOverMeshtasticConfig(data_speed=6)
        transport = RNSMeshtasticTransport(config)

        assert transport.config.data_speed == 6
        assert transport.is_running is False
        assert transport.is_connected is False

    def test_create_transport_default_config(self):
        """Test creating transport with default config."""
        transport = RNSMeshtasticTransport()

        assert transport.config.data_speed == 8
        assert transport.config.connection_type == "tcp"

    def test_get_status_not_running(self):
        """Test status when not running."""
        transport = RNSMeshtasticTransport()
        status = transport.get_status()

        assert status['running'] is False
        assert status['connected'] is False
        assert status['connection_type'] == 'tcp'

    def test_generate_packet_id(self):
        """Test packet ID generation is deterministic."""
        transport = RNSMeshtasticTransport()

        packet = b'test packet data'
        id1 = transport._generate_packet_id(packet)
        id2 = transport._generate_packet_id(packet)

        assert id1 == id2
        assert len(id1) == 4

    def test_generate_packet_id_different_packets(self):
        """Test different packets get different IDs."""
        transport = RNSMeshtasticTransport()

        id1 = transport._generate_packet_id(b'packet 1')
        id2 = transport._generate_packet_id(b'packet 2')

        assert id1 != id2

    def test_fragment_small_packet(self):
        """Test fragmenting a small packet."""
        transport = RNSMeshtasticTransport()

        packet = b'small'
        fragments = transport._fragment_packet(packet)

        assert len(fragments) == 1
        assert fragments[0].total == 1
        assert fragments[0].sequence == 0
        assert fragments[0].payload == packet

    def test_fragment_large_packet(self):
        """Test fragmenting a large packet."""
        transport = RNSMeshtasticTransport()

        # Create packet larger than one fragment
        packet = b'X' * (PAYLOAD_PER_FRAGMENT * 3 + 50)
        fragments = transport._fragment_packet(packet)

        assert len(fragments) == 4
        for i, frag in enumerate(fragments):
            assert frag.sequence == i
            assert frag.total == 4

        # Verify all fragments have same packet_id
        ids = [f.packet_id for f in fragments]
        assert all(id == ids[0] for id in ids)

    def test_fragment_reassemble_round_trip(self):
        """Test fragmenting and reassembling a packet."""
        transport = RNSMeshtasticTransport()

        original_packet = b'This is a test packet that will be fragmented and reassembled.'
        fragments = transport._fragment_packet(original_packet)

        # Simulate reassembly
        pending = PendingPacket(
            packet_id=fragments[0].packet_id,
            total_fragments=len(fragments)
        )
        for frag in fragments:
            pending.add_fragment(frag.sequence, frag.payload)

        reassembled = pending.reassemble()
        assert reassembled == original_packet


class TestRNSMeshtasticInterface:
    """Tests for RNSMeshtasticInterface adapter."""

    def test_create_interface(self):
        """Test creating interface adapter."""
        transport = RNSMeshtasticTransport()
        interface = RNSMeshtasticInterface(transport)

        assert interface.transport == transport
        assert interface.name == "Meshtastic"
        assert interface.online is False

    def test_send_delegates_to_transport(self):
        """Test send delegates to transport."""
        transport = MagicMock()
        transport.send_packet.return_value = True
        interface = RNSMeshtasticInterface(transport)

        result = interface.send(b'test')

        transport.send_packet.assert_called_once_with(b'test')
        assert result is True


class TestCreateRnsTransport:
    """Tests for factory function."""

    def test_create_with_config(self):
        """Test factory with config."""
        config = RNSOverMeshtasticConfig(data_speed=5)
        transport = create_rns_transport(config)

        assert isinstance(transport, RNSMeshtasticTransport)
        assert transport.config.data_speed == 5

    def test_create_default(self):
        """Test factory with defaults."""
        transport = create_rns_transport()

        assert isinstance(transport, RNSMeshtasticTransport)
        assert transport.config.data_speed == 8


class TestConstants:
    """Tests for module constants."""

    def test_max_fragment_size(self):
        """Test max fragment size is reasonable."""
        assert MAX_FRAGMENT_SIZE == 200

    def test_header_size(self):
        """Test header size."""
        assert FRAGMENT_HEADER_SIZE == 6  # 4 bytes ID + 1 seq + 1 total

    def test_payload_per_fragment(self):
        """Test payload size calculation."""
        assert PAYLOAD_PER_FRAGMENT == MAX_FRAGMENT_SIZE - FRAGMENT_HEADER_SIZE
        assert PAYLOAD_PER_FRAGMENT == 194
