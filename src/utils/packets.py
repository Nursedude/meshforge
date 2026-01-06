"""
Packet handling utilities for mesh network fragmentation and reassembly.

Version: 0.4.3-beta
Updated: 2026-01-06

Adapted from RNS_Over_Meshtastic_Gateway's PacketHandler class.
Original author: Mark Qvist (MIT License)
MeshForge integration: Dude AI (Claude)

This module provides utilities for handling packet fragmentation when
bridging between networks with different MTU sizes (e.g., RNS 564 bytes
to Meshtastic 200 bytes).
"""

import struct
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class FragmentStats:
    """Statistics for fragment handling"""
    packets_sent: int = 0
    packets_received: int = 0
    fragments_sent: int = 0
    fragments_received: int = 0
    reassembly_failures: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0


class PacketHandler:
    """
    Handles packet fragmentation and reassembly for mesh network bridging.

    Splits large packets into smaller fragments with metadata for reassembly.
    Designed for RNS over Meshtastic where 564-byte packets must be split
    into 200-byte fragments.

    Adapted from RNS_Over_Meshtastic_Gateway (MIT License).

    Example usage:
        # Sending - split large packet
        handler = PacketHandler(data=large_packet, index=0)
        for key in handler.get_keys():
            fragment = handler[key]
            send_to_mesh(fragment)

        # Receiving - reassemble fragments
        handler = PacketHandler()
        for fragment in incoming_fragments:
            complete_data = handler.process_packet(fragment)
            if complete_data:
                process_complete_packet(complete_data)
    """

    # Metadata format: 1 byte index (0-255), 1 signed byte position
    # Positive position = more fragments coming
    # Negative position = last fragment (signals end)
    struct_format = 'Bb'  # unsigned char, signed char

    def __init__(
        self,
        data: Optional[bytes] = None,
        index: Optional[int] = None,
        max_payload: int = 200,
        destination_id: Optional[str] = None
    ):
        """
        Initialize PacketHandler.

        Args:
            data: Data to fragment (for sending). If None, handler is for receiving.
            index: Packet index (0-255) for tracking multiple concurrent packets.
            max_payload: Maximum fragment size in bytes (default 200 for Meshtastic).
            destination_id: Optional destination identifier for routing.
        """
        self.max_payload = max_payload
        self.index = index
        self.data_dict: Dict[int, bytes] = {}
        self.loop_pos = 1
        self.done = False
        self.destination_id = destination_id

        if data:  # Sending mode - split data into fragments
            self._split_data(data)

    def _split_data(self, data: bytes) -> None:
        """Split data into even chunks and add metadata."""
        data_len = len(data)

        # Calculate optimal fragment size
        num_packets = (data_len // self.max_payload) + 1
        packet_size = (data_len // num_packets) + 1

        # Create fragments
        data_list = []
        for i in range(0, data_len, packet_size):
            data_list.append(data[i:i + packet_size])

        # Add metadata to each fragment
        for i, packet in enumerate(data_list):
            pos = i + 1
            # Negative position indicates last fragment
            if pos == len(data_list):
                pos = -pos
            meta_data = struct.pack(self.struct_format, self.index, pos)
            self.data_dict[pos] = meta_data + packet

    def get_next(self) -> Optional[bytes]:
        """Get next fragment to send (iterator pattern)."""
        ret = self[self.loop_pos]
        if max(self.data_dict.keys()) < self.loop_pos:
            self.loop_pos = 1
            self.done = True
        self.loop_pos += 1
        return ret

    def is_done(self) -> bool:
        """Return True if all fragments have been retrieved via get_next()."""
        return self.done

    def __getitem__(self, i: int) -> Optional[bytes]:
        """Get fragment at index (handles positive/negative positions)."""
        if i in self.data_dict:
            return self.data_dict[i]
        elif -i in self.data_dict:
            return self.data_dict[-i]
        return None

    def process_packet(self, packet: bytes) -> Optional[bytes]:
        """
        Process incoming fragment and attempt reassembly.

        Args:
            packet: Incoming fragment with metadata

        Returns:
            Complete reassembled data if all fragments received, else None
        """
        new_index, pos = self._get_metadata(packet)
        self.index = new_index
        self.data_dict[abs(pos)] = packet

        # Negative position indicates last fragment
        if pos < 0:
            return self._assemble_data()
        return None

    def _check_data(self) -> bool:
        """Verify all expected fragments are present."""
        expected = 1
        for key in sorted(self.data_dict.keys()):
            if key != expected:
                return False
            expected += 1
        return True

    def get_keys(self) -> List[int]:
        """Get all fragment position keys."""
        return list(self.data_dict.keys())

    def _assemble_data(self) -> Optional[bytes]:
        """Reassemble fragments into complete data."""
        if self._check_data():
            data = b''
            metadata_size = struct.calcsize(self.struct_format)
            for key in sorted(self.data_dict.keys()):
                # Strip metadata from each fragment
                data += self.data_dict[key][metadata_size:]
            return data
        return None

    def _get_metadata(self, packet: bytes) -> Tuple[int, int]:
        """Extract index and position from fragment metadata."""
        size = struct.calcsize(self.struct_format)
        meta_data = packet[:size]
        new_index, pos = struct.unpack(self.struct_format, meta_data)
        return new_index, pos

    @property
    def fragment_count(self) -> int:
        """Number of fragments in this packet."""
        return len(self.data_dict)

    @property
    def total_size(self) -> int:
        """Total size of all fragments including metadata."""
        return sum(len(f) for f in self.data_dict.values())

    @property
    def data_size(self) -> int:
        """Size of actual data (excluding metadata)."""
        metadata_size = struct.calcsize(self.struct_format)
        return sum(len(f) - metadata_size for f in self.data_dict.values())


def calc_index(current_index: int) -> int:
    """Calculate next packet index (wraps at 256)."""
    return (current_index + 1) % 256


class FragmentAssembler:
    """
    Manages fragment reassembly for multiple concurrent senders.

    Tracks fragments from different sources and handles reassembly
    when all fragments for a packet are received.

    Example usage:
        assembler = FragmentAssembler()

        def on_fragment(sender_id: str, fragment: bytes):
            complete = assembler.add_fragment(sender_id, fragment)
            if complete:
                process_message(complete)
    """

    def __init__(self, max_senders: int = 20, timeout_seconds: float = 30.0):
        """
        Initialize FragmentAssembler.

        Args:
            max_senders: Maximum concurrent senders to track (LRU eviction)
            timeout_seconds: Timeout for incomplete packets
        """
        self.max_senders = max_senders
        self.timeout = timeout_seconds
        self.handlers: Dict[str, Dict[int, PacketHandler]] = {}
        self.stats = FragmentStats()

    def add_fragment(
        self,
        sender_id: str,
        fragment: bytes
    ) -> Optional[bytes]:
        """
        Add fragment from sender and attempt reassembly.

        Args:
            sender_id: Unique identifier for the sender
            fragment: Fragment data with metadata

        Returns:
            Complete data if all fragments received, else None
        """
        # Initialize sender tracking if needed
        if sender_id not in self.handlers:
            if len(self.handlers) >= self.max_senders:
                # LRU eviction - remove oldest
                oldest = next(iter(self.handlers))
                del self.handlers[oldest]
            self.handlers[sender_id] = {}

        # Extract packet index from fragment
        handler = PacketHandler()
        packet_index, _ = handler._get_metadata(fragment)

        # Get or create handler for this packet
        if packet_index not in self.handlers[sender_id]:
            self.handlers[sender_id][packet_index] = PacketHandler()

        packet_handler = self.handlers[sender_id][packet_index]
        self.stats.fragments_received += 1

        # Process fragment
        complete_data = packet_handler.process_packet(fragment)

        if complete_data:
            # Cleanup completed packet
            del self.handlers[sender_id][packet_index]
            self.stats.packets_received += 1
            self.stats.bytes_received += len(complete_data)
            return complete_data

        return None

    def clear_sender(self, sender_id: str) -> None:
        """Clear all pending fragments from a sender."""
        if sender_id in self.handlers:
            del self.handlers[sender_id]

    def clear_all(self) -> None:
        """Clear all pending fragments."""
        self.handlers.clear()
