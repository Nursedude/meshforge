"""
Transport-Aware Chunking for X1 Tactical Messages.

Splits X1 messages into transport-sized chunks and reassembles
them with out-of-order support and deduplication.

Each transport has different payload limits:
    Meshtastic: 228 bytes (237 total - ~9 header overhead)
    MeshCore:   150 bytes (184 total - ~34 header)
    RNS/LXMF:  500 bytes
    SMS:        160 characters
    QR:        2953 bytes (QR v40 binary capacity)

Usage:
    from tactical.chunker import chunk, Reassembler

    # Split a long X1 message for Meshtastic
    chunks = chunk(x1_string, transport='meshtastic')

    # Reassemble on receive side
    reassembler = Reassembler()
    for chunk_str in chunks:
        result = reassembler.ingest(chunk_str)
        if result is not None:
            print(f"Complete message: {result}")
"""

import logging
import re
import time
import threading
from typing import Dict, List, Optional, Set

from tactical.x1_codec import X1_PREFIX, _X1_PATTERN, decode, encode, get_chunk_info
from tactical.models import TacticalMessage

logger = logging.getLogger(__name__)

# Transport payload limits (bytes available for X1 string)
TRANSPORT_LIMITS: Dict[str, int] = {
    'meshtastic': 228,
    'meshcore': 150,
    'rns': 500,
    'sms': 160,
    'qr': 2953,
}

# Maximum chunks per message (prevent resource exhaustion)
MAX_CHUNKS = 32

# Default reassembly timeout (seconds)
DEFAULT_TIMEOUT = 120


def chunk(x1_string: str, transport: str) -> List[str]:
    """Split an X1 message into transport-sized chunks.

    If the message fits within the transport limit, returns a single-element
    list with the original message (P/N updated to 1/1).

    If the message is too large, splits the payload across multiple chunks,
    each with the same header but different P/N values.

    Args:
        x1_string: Complete X1 wire format string.
        transport: Transport name ('meshtastic', 'meshcore', 'rns', 'sms', 'qr').

    Returns:
        List of X1 chunk strings, each within transport limit.

    Raises:
        ValueError: If transport is unknown or message can't be chunked.
    """
    limit = TRANSPORT_LIMITS.get(transport)
    if limit is None:
        raise ValueError(
            f"Unknown transport '{transport}'. "
            f"Known: {', '.join(TRANSPORT_LIMITS.keys())}"
        )

    # If message fits, return as-is (ensure P/N is 1/1)
    if len(x1_string.encode('utf-8')) <= limit:
        return [_set_chunk_info(x1_string, 1, 1)]

    # Parse the X1 string to extract components
    match = _X1_PATTERN.match(x1_string)
    if not match:
        raise ValueError(f"Invalid X1 format: {x1_string[:50]}...")

    template_id, mode, msg_id, _, _, payload = match.groups()

    # Calculate header overhead: "X1.<T>.<M>.<ID>.<P>/<N>."
    # P and N can be up to 2 digits each for MAX_CHUNKS=32
    header_template = f"{X1_PREFIX}.{template_id}.{mode}.{msg_id}."
    # Max chunk indicator size: "32/32."
    max_chunk_indicator = f"{MAX_CHUNKS}/{MAX_CHUNKS}."
    header_overhead = len((header_template + max_chunk_indicator).encode('utf-8'))

    # Available space for payload per chunk
    payload_space = limit - header_overhead
    if payload_space <= 0:
        raise ValueError(
            f"Transport '{transport}' limit ({limit}B) too small for X1 header"
        )

    # Split payload into chunks
    payload_bytes = payload.encode('utf-8')
    chunks = []
    offset = 0
    while offset < len(payload_bytes):
        chunk_payload = payload_bytes[offset:offset + payload_space].decode(
            'utf-8', errors='ignore'
        )
        chunks.append(chunk_payload)
        offset += payload_space

    total = len(chunks)
    if total > MAX_CHUNKS:
        raise ValueError(
            f"Message requires {total} chunks (max {MAX_CHUNKS}) "
            f"for transport '{transport}'"
        )

    # Build chunk strings
    result = []
    for i, chunk_payload in enumerate(chunks, start=1):
        chunk_str = f"{header_template}{i}/{total}.{chunk_payload}"
        result.append(chunk_str)

    return result


class Reassembler:
    """Reassemble chunked X1 messages with timeout and dedup.

    Thread-safe. Maintains pending chunks keyed by message ID.
    Completed message IDs are tracked for deduplication.

    Args:
        timeout_seconds: How long to wait for all chunks before discarding.
        max_completed: Maximum completed IDs to track for dedup.
    """

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        max_completed: int = 1000,
    ):
        self._timeout = timeout_seconds
        self._max_completed = max_completed
        self._lock = threading.Lock()

        # msg_id -> {part_num: payload_str, '_meta': {total, first_seen}}
        self._pending: Dict[str, Dict] = {}

        # Completed message IDs for deduplication
        self._completed: Set[str] = set()
        self._completed_order: List[str] = []  # Track insertion order for eviction

    def ingest(self, x1_chunk: str) -> Optional[TacticalMessage]:
        """Ingest a chunk. Returns complete TacticalMessage when all parts received.

        Args:
            x1_chunk: X1 wire format string (possibly a chunk).

        Returns:
            Complete TacticalMessage if all chunks received, None otherwise.
        """
        info = get_chunk_info(x1_chunk)
        if info is None:
            logger.debug("Ignoring non-X1 message in reassembler")
            return None

        msg_id = info['msg_id']
        part = info['part']
        total = info['total']

        with self._lock:
            # Dedup: skip already-completed messages
            if msg_id in self._completed:
                logger.debug(f"Dedup: message {msg_id} already completed")
                return None

            # Single-chunk message: decode immediately
            if total == 1:
                self._mark_completed(msg_id)
                return decode(x1_chunk)

            # Multi-chunk: store this chunk
            if msg_id not in self._pending:
                self._pending[msg_id] = {
                    '_meta': {'total': total, 'first_seen': time.time()}
                }

            entry = self._pending[msg_id]

            # Validate total consistency
            if entry['_meta']['total'] != total:
                logger.warning(
                    f"Chunk total mismatch for {msg_id}: "
                    f"expected {entry['_meta']['total']}, got {total}"
                )
                return None

            # Store chunk (extract payload from X1 string)
            match = _X1_PATTERN.match(x1_chunk)
            if match:
                entry[part] = match.group(6)  # payload portion

            # Check if all chunks received
            received = {k for k in entry if k != '_meta'}
            if len(received) == total:
                # Reassemble: sort by part number, concatenate payloads
                meta = entry['_meta']
                header_match = _X1_PATTERN.match(x1_chunk)
                if not header_match:
                    return None

                template_id, mode, _, _, _, _ = header_match.groups()

                # Reconstruct payload from ordered chunks
                full_payload = ''
                for i in range(1, total + 1):
                    if i not in entry:
                        logger.warning(f"Missing chunk {i}/{total} for {msg_id}")
                        return None
                    full_payload += entry[i]

                # Build complete X1 string
                complete_x1 = (
                    f"{X1_PREFIX}.{template_id}.{mode}.{msg_id}"
                    f".1/1.{full_payload}"
                )

                # Cleanup and mark complete
                del self._pending[msg_id]
                self._mark_completed(msg_id)

                try:
                    return decode(complete_x1)
                except ValueError as e:
                    logger.error(f"Failed to decode reassembled message {msg_id}: {e}")
                    return None

        return None

    def cleanup_expired(self) -> int:
        """Remove expired pending messages. Returns count of expired entries."""
        now = time.time()
        expired = []

        with self._lock:
            for msg_id, entry in self._pending.items():
                meta = entry.get('_meta', {})
                first_seen = meta.get('first_seen', 0)
                if now - first_seen > self._timeout:
                    expired.append(msg_id)

            for msg_id in expired:
                received = len({k for k in self._pending[msg_id] if k != '_meta'})
                total = self._pending[msg_id].get('_meta', {}).get('total', '?')
                logger.info(
                    f"Expired incomplete message {msg_id}: "
                    f"{received}/{total} chunks received"
                )
                del self._pending[msg_id]

        return len(expired)

    @property
    def pending_count(self) -> int:
        """Number of messages waiting for more chunks."""
        with self._lock:
            return len(self._pending)

    def _mark_completed(self, msg_id: str) -> None:
        """Mark message ID as completed for dedup. Evicts oldest if over limit."""
        self._completed.add(msg_id)
        self._completed_order.append(msg_id)

        # Evict oldest if over limit
        while len(self._completed) > self._max_completed:
            oldest = self._completed_order.pop(0)
            self._completed.discard(oldest)


def _set_chunk_info(x1_string: str, part: int, total: int) -> str:
    """Update the P/N chunk info in an X1 string."""
    match = _X1_PATTERN.match(x1_string)
    if not match:
        return x1_string

    template_id, mode, msg_id, _, _, payload = match.groups()
    return f"{X1_PREFIX}.{template_id}.{mode}.{msg_id}.{part}/{total}.{payload}"
