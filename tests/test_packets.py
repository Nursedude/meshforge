"""
Tests for packet fragmentation and reassembly utilities.

Run: python3 -m pytest tests/test_packets.py -v
"""

import pytest
import struct
from src.utils.packets import (
    PacketHandler,
    FragmentAssembler,
    FragmentStats,
    calc_index,
)


class TestPacketHandler:
    """Tests for PacketHandler fragmentation and reassembly."""

    def test_single_small_packet(self):
        """Test packet smaller than max_payload - no fragmentation needed."""
        data = b"Hello, mesh!"
        handler = PacketHandler(data=data, index=0, max_payload=200)

        assert handler.fragment_count == 1
        assert handler.data_size == len(data)

    def test_fragmentation_creates_correct_count(self):
        """Test that large data is split into correct number of fragments."""
        # 500 bytes should split into 3 fragments at 200 byte max
        data = b"X" * 500
        handler = PacketHandler(data=data, index=0, max_payload=200)

        assert handler.fragment_count >= 2
        # Each fragment should be under max_payload + metadata (2 bytes)
        for key in handler.get_keys():
            assert len(handler[key]) <= 202  # 200 + 2 metadata bytes

    def test_metadata_format(self):
        """Test that metadata is correctly embedded in fragments."""
        data = b"Test data for metadata check"
        packet_index = 42
        handler = PacketHandler(data=data, index=packet_index, max_payload=200)

        fragment = handler[1]  # Get first fragment
        # Extract metadata
        idx, pos = struct.unpack('Bb', fragment[:2])

        assert idx == packet_index
        assert pos == 1 or pos == -1  # First fragment, may be only fragment

    def test_last_fragment_has_negative_position(self):
        """Test that last fragment has negative position marker."""
        data = b"X" * 500  # Will create multiple fragments
        handler = PacketHandler(data=data, index=0, max_payload=200)

        keys = handler.get_keys()
        # Find the last fragment (has negative key)
        negative_keys = [k for k in keys if k < 0]
        assert len(negative_keys) == 1, "Should have exactly one negative key (last fragment)"

    def test_reassembly_complete(self):
        """Test successful reassembly when all fragments are present."""
        original_data = b"This is a test message for fragmentation and reassembly!"

        # Fragment
        sender = PacketHandler(data=original_data, index=5, max_payload=20)

        # Reassemble
        receiver = PacketHandler()
        result = None
        for key in sender.get_keys():
            fragment = sender[key]
            result = receiver.process_packet(fragment)

        assert result is not None
        assert result == original_data

    def test_reassembly_large_packet(self):
        """Test reassembly of a larger packet (~1KB)."""
        original_data = bytes(range(256)) * 4  # 1024 bytes

        sender = PacketHandler(data=original_data, index=0, max_payload=200)
        receiver = PacketHandler()

        result = None
        for key in sender.get_keys():
            result = receiver.process_packet(sender[key])

        assert result == original_data

    def test_reassembly_out_of_order(self):
        """Test reassembly when fragments arrive out of order."""
        original_data = b"A" * 100 + b"B" * 100 + b"C" * 100  # 300 bytes

        sender = PacketHandler(data=original_data, index=0, max_payload=100)
        receiver = PacketHandler()

        # Send fragments in reverse order (except last must be last for completion signal)
        keys = sender.get_keys()
        positive_keys = sorted([k for k in keys if k > 0])
        negative_key = [k for k in keys if k < 0][0]

        # Send middle fragments first, then last
        result = None
        for key in reversed(positive_keys[:-1]):
            result = receiver.process_packet(sender[key])
            assert result is None  # Should not complete yet

        # Send remaining positive key
        if positive_keys:
            result = receiver.process_packet(sender[positive_keys[-1]])

        # Send last fragment (with negative position)
        result = receiver.process_packet(sender[negative_key])

        assert result == original_data

    def test_reassembly_missing_fragment(self):
        """Test that reassembly fails when a fragment is missing."""
        original_data = b"X" * 500

        sender = PacketHandler(data=original_data, index=0, max_payload=100)
        receiver = PacketHandler()

        keys = sender.get_keys()
        # Skip the second fragment
        for key in keys:
            if abs(key) == 2:
                continue
            receiver.process_packet(sender[key])

        # Find the last fragment and try to assemble
        # _check_data should return False due to missing fragment
        assert receiver._check_data() is False

    def test_get_next_iterator(self):
        """Test the get_next iterator pattern."""
        data = b"Test iterator pattern data"
        handler = PacketHandler(data=data, index=0, max_payload=10)

        fragments = []
        while not handler.is_done():
            frag = handler.get_next()
            if frag:
                fragments.append(frag)

        assert len(fragments) == handler.fragment_count

    def test_index_tracking(self):
        """Test that packet index is properly tracked."""
        for index in [0, 127, 255]:
            handler = PacketHandler(data=b"test", index=index, max_payload=200)
            fragment = handler[1]
            extracted_index, _ = struct.unpack('Bb', fragment[:2])
            assert extracted_index == index

    def test_properties(self):
        """Test fragment_count, total_size, and data_size properties."""
        data = b"A" * 150
        handler = PacketHandler(data=data, index=0, max_payload=50)

        assert handler.fragment_count > 0
        assert handler.total_size > len(data)  # Includes metadata
        assert handler.data_size == len(data)

    def test_getitem_negative_key(self):
        """Test __getitem__ with negative keys (last fragment)."""
        data = b"X" * 100
        handler = PacketHandler(data=data, index=0, max_payload=40)

        keys = handler.get_keys()
        negative_key = [k for k in keys if k < 0][0]

        # Should be able to access via both positive and negative
        assert handler[negative_key] is not None
        assert handler[abs(negative_key)] is not None

    def test_empty_getitem(self):
        """Test __getitem__ returns None for non-existent keys."""
        handler = PacketHandler(data=b"test", index=0, max_payload=200)
        assert handler[999] is None


class TestCalcIndex:
    """Tests for calc_index function."""

    def test_increment(self):
        """Test basic increment."""
        assert calc_index(0) == 1
        assert calc_index(100) == 101

    def test_wrap_at_256(self):
        """Test wrap-around at 256."""
        assert calc_index(255) == 0
        assert calc_index(254) == 255

    def test_cycle(self):
        """Test full cycle returns to 0."""
        index = 0
        for _ in range(256):
            index = calc_index(index)
        assert index == 0


class TestFragmentStats:
    """Tests for FragmentStats dataclass."""

    def test_defaults(self):
        """Test default values are zero."""
        stats = FragmentStats()
        assert stats.packets_sent == 0
        assert stats.packets_received == 0
        assert stats.fragments_sent == 0
        assert stats.fragments_received == 0
        assert stats.reassembly_failures == 0
        assert stats.bytes_sent == 0
        assert stats.bytes_received == 0

    def test_custom_values(self):
        """Test custom initialization."""
        stats = FragmentStats(packets_sent=10, bytes_received=1000)
        assert stats.packets_sent == 10
        assert stats.bytes_received == 1000


class TestFragmentAssembler:
    """Tests for FragmentAssembler multi-sender handling."""

    def test_single_sender_complete(self):
        """Test reassembly from a single sender."""
        assembler = FragmentAssembler()
        original_data = b"Hello from sender A!"

        sender = PacketHandler(data=original_data, index=0, max_payload=10)
        result = None

        for key in sender.get_keys():
            result = assembler.add_fragment("sender_a", sender[key])

        assert result == original_data
        assert assembler.stats.packets_received == 1

    def test_multiple_senders_concurrent(self):
        """Test concurrent reassembly from multiple senders."""
        assembler = FragmentAssembler()

        data_a = b"Message from sender A"
        data_b = b"Message from sender B"

        sender_a = PacketHandler(data=data_a, index=0, max_payload=10)
        sender_b = PacketHandler(data=data_b, index=0, max_payload=10)

        keys_a = sender_a.get_keys()
        keys_b = sender_b.get_keys()

        # Interleave fragments from both senders
        results = {}
        max_len = max(len(keys_a), len(keys_b))

        for i in range(max_len):
            if i < len(keys_a):
                result = assembler.add_fragment("a", sender_a[keys_a[i]])
                if result:
                    results["a"] = result
            if i < len(keys_b):
                result = assembler.add_fragment("b", sender_b[keys_b[i]])
                if result:
                    results["b"] = result

        assert results.get("a") == data_a
        assert results.get("b") == data_b

    def test_lru_eviction(self):
        """Test LRU eviction when max_senders is exceeded."""
        assembler = FragmentAssembler(max_senders=2)

        # Add fragments from 3 different senders (exceeds limit)
        data = b"X" * 50
        for i in range(3):
            sender = PacketHandler(data=data, index=0, max_payload=20)
            # Send only first fragment (incomplete)
            first_key = [k for k in sender.get_keys() if k > 0][0]
            assembler.add_fragment(f"sender_{i}", sender[first_key])

        # Only 2 senders should remain (oldest evicted)
        assert len(assembler.handlers) == 2
        assert "sender_0" not in assembler.handlers  # First sender evicted

    def test_stats_tracking(self):
        """Test that stats are properly updated."""
        assembler = FragmentAssembler()
        data = b"Stats test data"

        sender = PacketHandler(data=data, index=0, max_payload=10)

        for key in sender.get_keys():
            assembler.add_fragment("test", sender[key])

        assert assembler.stats.fragments_received > 0
        assert assembler.stats.packets_received == 1
        assert assembler.stats.bytes_received == len(data)

    def test_clear_sender(self):
        """Test clearing a specific sender's fragments."""
        assembler = FragmentAssembler()
        data = b"X" * 100

        sender = PacketHandler(data=data, index=0, max_payload=30)
        # Send partial fragments
        keys = sender.get_keys()
        assembler.add_fragment("test", sender[keys[0]])

        assert "test" in assembler.handlers
        assembler.clear_sender("test")
        assert "test" not in assembler.handlers

    def test_clear_all(self):
        """Test clearing all pending fragments."""
        assembler = FragmentAssembler()

        for i in range(3):
            data = b"X" * 50
            sender = PacketHandler(data=data, index=0, max_payload=20)
            first_key = [k for k in sender.get_keys() if k > 0][0]
            assembler.add_fragment(f"sender_{i}", sender[first_key])

        assert len(assembler.handlers) > 0
        assembler.clear_all()
        assert len(assembler.handlers) == 0

    def test_multiple_packets_same_sender(self):
        """Test handling multiple packets from same sender."""
        assembler = FragmentAssembler()

        # Send two complete messages from same sender with different indices
        for idx in [0, 1]:
            data = f"Message {idx}".encode()
            sender = PacketHandler(data=data, index=idx, max_payload=10)

            result = None
            for key in sender.get_keys():
                result = assembler.add_fragment("sender", sender[key])

            assert result == data


class TestRoundTrip:
    """Integration tests for complete send/receive cycles."""

    def test_various_sizes(self):
        """Test fragmentation/reassembly at various data sizes."""
        test_sizes = [1, 10, 50, 100, 199, 200, 201, 500, 1000, 2000]

        for size in test_sizes:
            original = bytes(range(256)) * (size // 256 + 1)
            original = original[:size]

            sender = PacketHandler(data=original, index=0, max_payload=200)
            receiver = PacketHandler()

            result = None
            for key in sender.get_keys():
                result = receiver.process_packet(sender[key])

            assert result == original, f"Failed for size {size}"

    def test_binary_data_preservation(self):
        """Test that binary data (including null bytes) is preserved."""
        original = bytes(range(256))  # All possible byte values

        sender = PacketHandler(data=original, index=0, max_payload=50)
        receiver = PacketHandler()

        result = None
        for key in sender.get_keys():
            result = receiver.process_packet(sender[key])

        assert result == original

    def test_max_index_values(self):
        """Test with boundary index values."""
        data = b"Test boundary indices"

        for index in [0, 1, 127, 128, 254, 255]:
            sender = PacketHandler(data=data, index=index, max_payload=10)
            receiver = PacketHandler()

            result = None
            for key in sender.get_keys():
                result = receiver.process_packet(sender[key])

            assert result == data, f"Failed for index {index}"
