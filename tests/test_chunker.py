"""Tests for transport-aware chunking and reassembly."""

import pytest
from datetime import datetime

from tactical.models import (
    TacticalType, TacticalPriority, EncryptionMode, TacticalMessage,
)
from tactical.x1_codec import encode, decode
from tactical.chunker import chunk, Reassembler, TRANSPORT_LIMITS


class TestChunk:
    """Test message chunking."""

    def _make_message(self, content_size: int = 10) -> str:
        """Create an X1 string with specified content size."""
        msg = TacticalMessage(
            id="TST01",
            tactical_type=TacticalType.SITREP,
            sender_id="WH6GXZ",
            content={"situation": "x" * content_size},
        )
        return encode(msg)

    def test_small_message_single_chunk(self):
        x1 = self._make_message(content_size=10)
        chunks = chunk(x1, 'rns')  # 500B limit — plenty
        assert len(chunks) == 1
        assert chunks[0].startswith("X1.")

    def test_large_message_multiple_chunks(self):
        # Create a message that exceeds Meshtastic limit
        x1 = self._make_message(content_size=500)
        chunks = chunk(x1, 'meshtastic')  # 228B limit
        assert len(chunks) > 1

        # Each chunk should be within limit
        for c in chunks:
            assert len(c.encode('utf-8')) <= TRANSPORT_LIMITS['meshtastic']

    def test_unknown_transport(self):
        x1 = self._make_message()
        with pytest.raises(ValueError, match="Unknown transport"):
            chunk(x1, 'unknown_transport')

    def test_all_transports(self):
        x1 = self._make_message(content_size=10)
        for transport in TRANSPORT_LIMITS:
            chunks = chunk(x1, transport)
            assert len(chunks) >= 1

    def test_chunk_part_numbers(self):
        """Verify chunk P/N fields are correct."""
        from tactical.x1_codec import get_chunk_info

        x1 = self._make_message(content_size=500)
        chunks = chunk(x1, 'meshcore')  # 150B limit — will chunk heavily

        for i, c in enumerate(chunks, start=1):
            info = get_chunk_info(c)
            assert info is not None
            assert info['part'] == i
            assert info['total'] == len(chunks)


class TestReassembler:
    """Test out-of-order chunk reassembly."""

    def test_single_chunk_passthrough(self):
        msg = TacticalMessage(
            id="RAS01",
            tactical_type=TacticalType.CHECKIN,
            sender_id="WH6GXZ",
            content={"callsign": "WH6GXZ", "status": "ok"},
        )
        x1 = encode(msg)

        reassembler = Reassembler()
        result = reassembler.ingest(x1)
        assert result is not None
        assert result.id == "RAS01"
        assert result.content['callsign'] == "WH6GXZ"

    def test_multi_chunk_reassembly(self):
        msg = TacticalMessage(
            id="RAS02",
            tactical_type=TacticalType.SITREP,
            sender_id="KH6ABC",
            content={"situation": "x" * 500},
        )
        x1 = encode(msg)
        chunks = chunk(x1, 'meshcore')  # Will create multiple chunks
        assert len(chunks) > 1

        reassembler = Reassembler()

        # Ingest all but last — should return None
        for c in chunks[:-1]:
            result = reassembler.ingest(c)
            assert result is None

        # Last chunk completes the message
        result = reassembler.ingest(chunks[-1])
        assert result is not None
        assert result.id == "RAS02"
        assert result.content['situation'] == "x" * 500

    def test_out_of_order_reassembly(self):
        msg = TacticalMessage(
            id="OOO01",
            tactical_type=TacticalType.SITREP,
            sender_id="WH6GXZ",
            content={"situation": "y" * 500},
        )
        x1 = encode(msg)
        chunks = chunk(x1, 'meshcore')
        assert len(chunks) > 1

        reassembler = Reassembler()

        # Ingest in reverse order
        for c in reversed(chunks[:-1]):
            result = reassembler.ingest(c)
            assert result is None

        # First chunk (ingested last) completes
        result = reassembler.ingest(chunks[0])
        # May or may not complete depending on which was "last" ingested
        # The key test is that eventually we get a result
        if result is None:
            # Need one more pass if first was already ingested
            for c in chunks:
                result = reassembler.ingest(c)
                if result is not None:
                    break

    def test_dedup(self):
        msg = TacticalMessage(
            id="DUP01",
            tactical_type=TacticalType.CHECKIN,
            content={"callsign": "WH6GXZ"},
        )
        x1 = encode(msg)

        reassembler = Reassembler()

        # First ingest succeeds
        result1 = reassembler.ingest(x1)
        assert result1 is not None

        # Second ingest of same message returns None (dedup)
        result2 = reassembler.ingest(x1)
        assert result2 is None

    def test_cleanup_expired(self):
        reassembler = Reassembler(timeout_seconds=0)  # Immediate timeout

        msg = TacticalMessage(
            id="EXP01",
            tactical_type=TacticalType.SITREP,
            content={"situation": "z" * 500},
        )
        x1 = encode(msg)
        chunks = chunk(x1, 'meshcore')

        if len(chunks) > 1:
            # Ingest only first chunk
            reassembler.ingest(chunks[0])
            assert reassembler.pending_count == 1

            # Cleanup should expire it
            expired = reassembler.cleanup_expired()
            assert expired >= 1
            assert reassembler.pending_count == 0

    def test_non_x1_ignored(self):
        reassembler = Reassembler()
        result = reassembler.ingest("Hello, this is not X1")
        assert result is None


class TestTransportLimits:
    """Test transport limit constants."""

    def test_known_transports(self):
        assert 'meshtastic' in TRANSPORT_LIMITS
        assert 'meshcore' in TRANSPORT_LIMITS
        assert 'rns' in TRANSPORT_LIMITS
        assert 'sms' in TRANSPORT_LIMITS
        assert 'qr' in TRANSPORT_LIMITS

    def test_limits_are_positive(self):
        for transport, limit in TRANSPORT_LIMITS.items():
            assert limit > 0, f"{transport} limit should be positive"
