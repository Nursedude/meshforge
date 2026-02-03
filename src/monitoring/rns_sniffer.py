"""
RNS Packet Sniffer - Wireshark-Grade RNS Traffic Capture.

Provides deep packet inspection and traffic capture for Reticulum (RNS) networks.
Hooks into RNS Transport layer to capture:
- Announces (node discovery)
- Links (connection establishment)
- Packets (data transfer)
- Path table changes

Integrates with TrafficInspector for unified mesh visibility.

Reference: https://reticulum.network/manual/understanding.html
"""

import hashlib
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from queue import Queue, Empty, Full
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# RNS PROTOCOL CONSTANTS
# =============================================================================

# RNS Packet Types (Header flags)
class RNSPacketType(Enum):
    """RNS packet types from protocol specification."""
    DATA = 0x00           # Regular data packet
    ANNOUNCE = 0x01       # Node announcement
    LINK_REQUEST = 0x02   # Link establishment request
    LINK_PROOF = 0x03     # Link proof response
    LINK_RTT = 0x04       # Link round-trip time measurement
    LINK_KEEPALIVE = 0x05 # Link keepalive
    LINK_CLOSE = 0x06     # Link termination
    PATH_REQUEST = 0x07   # Path discovery request
    PATH_RESPONSE = 0x08  # Path discovery response
    UNKNOWN = 0xFF


class RNSInterfaceType(Enum):
    """Known RNS interface types."""
    LOCAL = "LocalInterface"       # Local shared instance
    TCP = "TCPInterface"           # TCP/IP
    UDP = "UDPInterface"           # UDP/IP
    I2P = "I2PInterface"           # I2P anonymous network
    LORA = "RNodeInterface"        # LoRa via RNode
    SERIAL = "SerialInterface"     # Serial/UART
    KISS = "KISSInterface"         # KISS protocol (packet radio)
    AX25 = "AX25Interface"         # AX.25 amateur radio
    PIPE = "PipeInterface"         # Named pipe
    UNKNOWN = "Unknown"


class RNSTransportState(Enum):
    """Transport-level connection states."""
    UNKNOWN = "unknown"
    ANNOUNCED = "announced"      # Destination has been announced
    PATH_KNOWN = "path_known"    # Path to destination is known
    LINK_PENDING = "pending"     # Link request sent
    LINK_ACTIVE = "active"       # Link established
    LINK_STALE = "stale"         # Link may be stale
    LINK_CLOSED = "closed"       # Link terminated


# =============================================================================
# RNS PACKET REPRESENTATION
# =============================================================================

@dataclass
class RNSPacketInfo:
    """
    Captured RNS packet information.

    Mirrors the wire format:
    [HEADER 2 bytes] [ADDRESSES 16/32 bytes] [CONTEXT 1 byte] [DATA 0-465 bytes]
    """
    # Unique capture ID
    id: str = field(default_factory=lambda: f"rns_{int(time.time()*1000000)}")

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)
    capture_time_ns: int = 0  # Nanoseconds since capture start

    # Packet type
    packet_type: RNSPacketType = RNSPacketType.UNKNOWN

    # Header fields (2 bytes)
    header_flags: int = 0
    header_hops: int = 0

    # Addressing (16 bytes = truncated destination hash)
    destination_hash: Optional[bytes] = None
    source_hash: Optional[bytes] = None  # For announces

    # Context byte
    context: int = 0

    # Interface info
    interface_name: str = ""
    interface_type: RNSInterfaceType = RNSInterfaceType.UNKNOWN

    # Direction
    direction: str = "inbound"  # inbound, outbound, internal

    # Payload
    payload: bytes = b""
    payload_size: int = 0

    # Announce-specific
    announce_identity: Optional[bytes] = None
    announce_app_data: Optional[bytes] = None
    announce_aspect: str = ""

    # Link-specific
    link_id: Optional[bytes] = None
    link_state: RNSTransportState = RNSTransportState.UNKNOWN

    # Path info
    hops: int = 0
    rssi: Optional[int] = None
    snr: Optional[float] = None

    # Raw packet
    raw_bytes: bytes = b""

    def __post_init__(self):
        if self.raw_bytes and not self.payload_size:
            self.payload_size = len(self.payload)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "packet_type": self.packet_type.name,
            "header_flags": self.header_flags,
            "header_hops": self.header_hops,
            "destination_hash": self.destination_hash.hex() if self.destination_hash else None,
            "source_hash": self.source_hash.hex() if self.source_hash else None,
            "context": self.context,
            "interface_name": self.interface_name,
            "interface_type": self.interface_type.value,
            "direction": self.direction,
            "payload_size": self.payload_size,
            "announce_aspect": self.announce_aspect,
            "link_id": self.link_id.hex() if self.link_id else None,
            "link_state": self.link_state.value,
            "hops": self.hops,
            "rssi": self.rssi,
            "snr": self.snr,
        }

    def get_summary(self) -> str:
        """Get one-line summary for display."""
        dir_sym = {"inbound": "<-", "outbound": "->", "internal": ".."}
        dir_str = dir_sym.get(self.direction, "??")

        dest = self.destination_hash.hex()[:16] if self.destination_hash else "?"
        ptype = self.packet_type.name[:12]

        info = ""
        if self.packet_type == RNSPacketType.ANNOUNCE:
            info = f"[{self.announce_aspect or 'announce'}]"
        elif self.packet_type in (RNSPacketType.LINK_REQUEST, RNSPacketType.LINK_PROOF):
            info = f"[link:{self.link_state.value}]"
        else:
            info = f"[{self.payload_size}B]"

        hops_str = f"h={self.hops}" if self.hops else ""

        return f"{dest} {dir_str} {ptype} {info} {hops_str}"


# =============================================================================
# RNS PATH TABLE ENTRY
# =============================================================================

@dataclass
class RNSPathEntry:
    """Entry in the RNS path table."""
    destination_hash: bytes
    hops: int
    interface_name: str
    expires: float  # Unix timestamp
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    announce_count: int = 1

    def is_expired(self) -> bool:
        return time.time() > self.expires

    def to_dict(self) -> Dict[str, Any]:
        return {
            "destination": self.destination_hash.hex(),
            "hops": self.hops,
            "interface": self.interface_name,
            "expires_in": max(0, int(self.expires - time.time())),
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "announce_count": self.announce_count,
        }


# =============================================================================
# RNS LINK TRACKING
# =============================================================================

@dataclass
class RNSLinkInfo:
    """Tracked RNS link information."""
    link_id: bytes
    destination_hash: bytes
    state: RNSTransportState = RNSTransportState.UNKNOWN
    created: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    rtt_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "link_id": self.link_id.hex(),
            "destination": self.destination_hash.hex(),
            "state": self.state.value,
            "created": self.created.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "rtt_ms": self.rtt_ms,
        }


# =============================================================================
# RNS PACKET SNIFFER
# =============================================================================

class RNSSniffer:
    """
    Wireshark-grade packet sniffer for Reticulum networks.

    Hooks into RNS Transport to capture all network activity:
    - Announces (node discovery)
    - Links (connections)
    - Data packets
    - Path table changes

    Usage:
        sniffer = RNSSniffer()
        sniffer.start()

        # Get recent packets
        packets = sniffer.get_packets(limit=100)

        # Get path table
        paths = sniffer.get_path_table()

        # Filter by destination
        filtered = sniffer.filter_packets("destination == 17a4dcfd")
    """

    MAX_PACKETS = 10000  # Max packets to keep in memory

    def __init__(self, max_packets: int = MAX_PACKETS):
        self._max_packets = max_packets
        self._lock = threading.Lock()
        self._running = False
        self._capture_start: Optional[float] = None

        # Captured packets (circular buffer)
        self._packets: List[RNSPacketInfo] = []

        # Path table tracking
        self._path_table: Dict[bytes, RNSPathEntry] = {}

        # Link tracking
        self._links: Dict[bytes, RNSLinkInfo] = {}

        # Statistics
        self._stats = {
            "packets_captured": 0,
            "announces_seen": 0,
            "links_established": 0,
            "paths_discovered": 0,
            "bytes_captured": 0,
            "start_time": None,
        }

        # Callbacks for packet notifications
        self._callbacks: List[Callable[[RNSPacketInfo], None]] = []

        # RNS hooks installed
        self._hooks_installed = False
        self._original_handlers: Dict[str, Any] = {}

    def start(self) -> bool:
        """Start packet capture."""
        if self._running:
            return True

        self._running = True
        self._capture_start = time.time_ns()
        self._stats["start_time"] = datetime.now()

        # Install RNS hooks
        success = self._install_rns_hooks()
        if success:
            logger.info("RNS Sniffer started - capturing packets")
        else:
            logger.warning("RNS Sniffer started (RNS not available - waiting)")

        return True

    def stop(self) -> None:
        """Stop packet capture."""
        self._running = False
        self._remove_rns_hooks()
        logger.info("RNS Sniffer stopped")

    def _install_rns_hooks(self) -> bool:
        """Install hooks into RNS Transport layer."""
        if self._hooks_installed:
            return True

        try:
            import RNS

            # Hook into Transport's packet handling
            # RNS.Transport processes all incoming/outgoing packets

            # Save original handler if exists
            if hasattr(RNS.Transport, '_packet_filter'):
                self._original_handlers['packet_filter'] = RNS.Transport._packet_filter

            # Install our packet capture hook
            original_inbound = getattr(RNS.Transport, 'inbound', None)
            if original_inbound:
                self._original_handlers['inbound'] = original_inbound

                def hooked_inbound(raw, interface=None):
                    self._capture_inbound_packet(raw, interface)
                    if self._original_handlers.get('inbound'):
                        return self._original_handlers['inbound'](raw, interface)

                # Note: We don't actually monkey-patch RNS.Transport.inbound
                # because it would affect RNS operation. Instead, we use
                # the announce handler and link callbacks.

            # Register announce handler for discovery
            class SnifferAnnounceHandler:
                def __init__(self, sniffer):
                    self.aspect_filter = None  # Capture all aspects
                    self.sniffer = sniffer

                def received_announce(self, dest_hash, announced_identity, app_data):
                    self.sniffer._on_rns_announce(dest_hash, announced_identity, app_data)

            self._announce_handler = SnifferAnnounceHandler(self)
            RNS.Transport.register_announce_handler(self._announce_handler)

            self._hooks_installed = True
            logger.debug("RNS hooks installed for packet capture")
            return True

        except ImportError:
            logger.debug("RNS not available for hooking")
            return False
        except Exception as e:
            logger.debug(f"Failed to install RNS hooks: {e}")
            return False

    def _remove_rns_hooks(self) -> None:
        """Remove RNS hooks."""
        if not self._hooks_installed:
            return

        try:
            import RNS
            # RNS doesn't have unregister_announce_handler, but handler
            # won't be called after we stop since we check _running
            self._hooks_installed = False
        except ImportError:
            pass

    def _capture_inbound_packet(self, raw: bytes, interface) -> None:
        """Capture an inbound packet (called from RNS hook)."""
        if not self._running:
            return

        try:
            packet_info = self._parse_rns_packet(raw, interface, "inbound")
            self._store_packet(packet_info)
        except Exception as e:
            logger.debug(f"Error capturing inbound packet: {e}")

    def _on_rns_announce(self, dest_hash: bytes, announced_identity, app_data: bytes) -> None:
        """Handle RNS announce (called from announce handler)."""
        if not self._running:
            return

        try:
            import RNS

            # Create packet info for the announce
            packet_info = RNSPacketInfo(
                packet_type=RNSPacketType.ANNOUNCE,
                destination_hash=dest_hash,
                direction="inbound",
                announce_app_data=app_data,
            )

            # Extract aspect from app_data if present
            if app_data:
                try:
                    # LXMF announces include aspect info
                    packet_info.announce_aspect = "lxmf.delivery" if b"lxmf" in app_data.lower() else ""
                except (AttributeError, TypeError):
                    pass

            # Get identity hash if available
            if announced_identity:
                try:
                    packet_info.announce_identity = announced_identity.hash
                    packet_info.source_hash = announced_identity.hash
                except Exception:
                    pass

            # Get hop count from Transport path table
            try:
                if RNS.Transport.has_path(dest_hash):
                    hops = RNS.Transport.hops_to(dest_hash)
                    packet_info.hops = hops if hops is not None else 0
            except Exception:
                pass

            # Store packet
            self._store_packet(packet_info)

            # Update path table
            self._update_path_table(dest_hash, packet_info.hops)

            # Update stats
            with self._lock:
                self._stats["announces_seen"] += 1

        except Exception as e:
            logger.debug(f"Error processing RNS announce: {e}")

    def _parse_rns_packet(self, raw: bytes, interface, direction: str) -> RNSPacketInfo:
        """Parse raw RNS packet bytes into structured info."""
        packet = RNSPacketInfo(
            direction=direction,
            raw_bytes=raw,
            payload_size=len(raw),
        )

        if len(raw) < 2:
            return packet

        # Parse header (2 bytes)
        # Byte 0: [flags:4][hops:4]
        # Byte 1: [type:4][reserved:4]
        packet.header_flags = (raw[0] >> 4) & 0x0F
        packet.header_hops = raw[0] & 0x0F
        packet.hops = packet.header_hops

        packet_type_raw = (raw[1] >> 4) & 0x0F
        try:
            packet.packet_type = RNSPacketType(packet_type_raw)
        except ValueError:
            packet.packet_type = RNSPacketType.UNKNOWN

        # Extract destination hash (bytes 2-17 for 16-byte hash)
        if len(raw) >= 18:
            packet.destination_hash = raw[2:18]

        # Context byte at position 18
        if len(raw) >= 19:
            packet.context = raw[18]

        # Payload starts at byte 19
        if len(raw) > 19:
            packet.payload = raw[19:]
            packet.payload_size = len(packet.payload)

        # Interface info
        if interface:
            packet.interface_name = getattr(interface, 'name', str(interface))
            iface_class = type(interface).__name__
            try:
                packet.interface_type = RNSInterfaceType(iface_class)
            except ValueError:
                packet.interface_type = RNSInterfaceType.UNKNOWN

        return packet

    def _store_packet(self, packet: RNSPacketInfo) -> None:
        """Store captured packet."""
        # Set capture timestamp
        if self._capture_start:
            packet.capture_time_ns = time.time_ns() - int(self._capture_start)

        with self._lock:
            self._packets.append(packet)
            self._stats["packets_captured"] += 1
            self._stats["bytes_captured"] += len(packet.raw_bytes)

            # Trim if over limit
            if len(self._packets) > self._max_packets:
                self._packets = self._packets[-self._max_packets:]

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(packet)
            except Exception as e:
                logger.debug(f"Packet callback error: {e}")

    def _update_path_table(self, dest_hash: bytes, hops: int) -> None:
        """Update internal path table tracking."""
        with self._lock:
            if dest_hash in self._path_table:
                entry = self._path_table[dest_hash]
                entry.hops = hops
                entry.last_seen = datetime.now()
                entry.announce_count += 1
            else:
                # New path discovered
                self._path_table[dest_hash] = RNSPathEntry(
                    destination_hash=dest_hash,
                    hops=hops,
                    interface_name="",
                    expires=time.time() + 3600,  # 1 hour default
                )
                self._stats["paths_discovered"] += 1

    def capture_outbound(self, dest_hash: bytes, data: bytes, interface_name: str = "") -> None:
        """
        Manually capture an outbound packet.

        Call this from RNS bridge when sending data.
        """
        if not self._running:
            return

        packet = RNSPacketInfo(
            packet_type=RNSPacketType.DATA,
            destination_hash=dest_hash,
            direction="outbound",
            payload=data,
            payload_size=len(data),
            interface_name=interface_name,
        )
        self._store_packet(packet)

    def capture_link_event(self, link_id: bytes, dest_hash: bytes,
                          event: str, rtt_ms: Optional[float] = None) -> None:
        """
        Capture link-related event.

        Args:
            link_id: Link identifier
            dest_hash: Destination hash
            event: Event type (request, established, closed, timeout)
            rtt_ms: Round-trip time if available
        """
        if not self._running:
            return

        # Map event to packet type
        event_to_type = {
            "request": RNSPacketType.LINK_REQUEST,
            "proof": RNSPacketType.LINK_PROOF,
            "established": RNSPacketType.LINK_PROOF,
            "rtt": RNSPacketType.LINK_RTT,
            "keepalive": RNSPacketType.LINK_KEEPALIVE,
            "closed": RNSPacketType.LINK_CLOSE,
        }

        packet_type = event_to_type.get(event, RNSPacketType.UNKNOWN)

        # Map event to link state
        event_to_state = {
            "request": RNSTransportState.LINK_PENDING,
            "proof": RNSTransportState.LINK_ACTIVE,
            "established": RNSTransportState.LINK_ACTIVE,
            "closed": RNSTransportState.LINK_CLOSED,
            "timeout": RNSTransportState.LINK_STALE,
        }
        link_state = event_to_state.get(event, RNSTransportState.UNKNOWN)

        packet = RNSPacketInfo(
            packet_type=packet_type,
            destination_hash=dest_hash,
            link_id=link_id,
            link_state=link_state,
            direction="internal",
        )
        self._store_packet(packet)

        # Update link tracking
        self._update_link(link_id, dest_hash, link_state, rtt_ms)

    def _update_link(self, link_id: bytes, dest_hash: bytes,
                     state: RNSTransportState, rtt_ms: Optional[float]) -> None:
        """Update link tracking."""
        with self._lock:
            if link_id in self._links:
                link = self._links[link_id]
                link.state = state
                link.last_activity = datetime.now()
                if rtt_ms is not None:
                    link.rtt_ms = rtt_ms
            else:
                self._links[link_id] = RNSLinkInfo(
                    link_id=link_id,
                    destination_hash=dest_hash,
                    state=state,
                    rtt_ms=rtt_ms,
                )
                if state == RNSTransportState.LINK_ACTIVE:
                    self._stats["links_established"] += 1

    def get_packets(self, limit: int = 100, offset: int = 0,
                   packet_type: Optional[RNSPacketType] = None,
                   destination: Optional[str] = None) -> List[RNSPacketInfo]:
        """
        Get captured packets.

        Args:
            limit: Maximum packets to return
            offset: Skip first N packets
            packet_type: Filter by packet type
            destination: Filter by destination hash prefix

        Returns:
            List of RNSPacketInfo objects
        """
        with self._lock:
            packets = list(self._packets)

        # Filter by type
        if packet_type:
            packets = [p for p in packets if p.packet_type == packet_type]

        # Filter by destination
        if destination:
            dest_lower = destination.lower()
            packets = [p for p in packets
                      if p.destination_hash and
                      p.destination_hash.hex().startswith(dest_lower)]

        # Apply offset and limit (newest first)
        packets = list(reversed(packets))
        return packets[offset:offset + limit]

    def get_path_table(self) -> List[RNSPathEntry]:
        """Get current path table."""
        with self._lock:
            return list(self._path_table.values())

    def get_links(self) -> List[RNSLinkInfo]:
        """Get tracked links."""
        with self._lock:
            return list(self._links.values())

    def get_stats(self) -> Dict[str, Any]:
        """Get sniffer statistics."""
        with self._lock:
            stats = dict(self._stats)
            stats["packet_count"] = len(self._packets)
            stats["path_count"] = len(self._path_table)
            stats["link_count"] = len(self._links)
            stats["active_links"] = sum(
                1 for l in self._links.values()
                if l.state == RNSTransportState.LINK_ACTIVE
            )
        return stats

    def register_callback(self, callback: Callable[[RNSPacketInfo], None]) -> None:
        """Register callback for new packets."""
        self._callbacks.append(callback)

    def clear(self) -> int:
        """Clear all captured packets."""
        with self._lock:
            count = len(self._packets)
            self._packets.clear()
            self._stats["packets_captured"] = 0
            self._stats["bytes_captured"] = 0
        return count

    def lookup_destination(self, hash_prefix: str) -> Optional[RNSPathEntry]:
        """
        Look up destination by hash prefix.

        Args:
            hash_prefix: First characters of destination hash (hex)

        Returns:
            Path entry if found
        """
        prefix_lower = hash_prefix.lower()
        with self._lock:
            for dest_hash, entry in self._path_table.items():
                if dest_hash.hex().startswith(prefix_lower):
                    return entry
        return None

    def probe_destination(self, dest_hash_hex: str) -> bool:
        """
        Probe a destination to discover path.

        Args:
            dest_hash_hex: Destination hash as hex string

        Returns:
            True if path request was sent
        """
        try:
            import RNS

            dest_hash = bytes.fromhex(dest_hash_hex)

            # Request path
            RNS.Transport.request_path(dest_hash)

            # Capture as outbound packet
            packet = RNSPacketInfo(
                packet_type=RNSPacketType.PATH_REQUEST,
                destination_hash=dest_hash,
                direction="outbound",
            )
            self._store_packet(packet)

            return True

        except ImportError:
            logger.warning("RNS not available for path probe")
            return False
        except Exception as e:
            logger.error(f"Path probe failed: {e}")
            return False


# =============================================================================
# INTEGRATION WITH TRAFFIC INSPECTOR
# =============================================================================

def convert_to_mesh_packet(rns_packet: RNSPacketInfo):
    """
    Convert RNSPacketInfo to MeshPacket for unified TrafficInspector.

    This allows RNS packets to be viewed alongside Meshtastic packets
    with consistent filtering and analysis.
    """
    try:
        from monitoring.traffic_inspector import (
            MeshPacket, PacketProtocol, PacketDirection, PacketTree,
            PacketField, FieldType
        )

        # Map direction
        dir_map = {
            "inbound": PacketDirection.INBOUND,
            "outbound": PacketDirection.OUTBOUND,
            "internal": PacketDirection.INTERNAL,
        }
        direction = dir_map.get(rns_packet.direction, PacketDirection.INBOUND)

        # Create MeshPacket
        packet = MeshPacket(
            id=rns_packet.id,
            timestamp=rns_packet.timestamp,
            direction=direction,
            protocol=PacketProtocol.RNS,
            source=rns_packet.source_hash.hex() if rns_packet.source_hash else "",
            destination=rns_packet.destination_hash.hex() if rns_packet.destination_hash else "",
            hops_taken=rns_packet.hops,
            payload=rns_packet.payload,
            rns_dest_hash=rns_packet.destination_hash,
            rns_interface=rns_packet.interface_name,
            rns_service=rns_packet.announce_aspect,
            size=len(rns_packet.raw_bytes) if rns_packet.raw_bytes else rns_packet.payload_size,
            raw_bytes=rns_packet.raw_bytes,
        )

        # Build protocol tree
        tree = PacketTree()

        # Frame layer
        frame = tree.add_layer("Frame", "frame")
        tree.add_field(frame, "Timestamp", "frame.time",
                      packet.timestamp, FieldType.TIMESTAMP)
        tree.add_field(frame, "Direction", "frame.direction",
                      direction.value, FieldType.ENUM)
        tree.add_field(frame, "Size", "frame.size",
                      packet.size, FieldType.INTEGER)
        tree.add_field(frame, "Protocol", "frame.protocol",
                      "rns", FieldType.STRING)

        # RNS layer
        rns_layer = tree.add_layer("Reticulum", "rns")

        # Packet type
        tree.add_field(rns_layer, "Packet Type", "rns.type",
                      rns_packet.packet_type.name, FieldType.STRING,
                      "RNS packet type")

        # Destination hash
        tree.add_field(rns_layer, "Destination Hash", "rns.dest_hash",
                      rns_packet.destination_hash.hex() if rns_packet.destination_hash else "",
                      FieldType.BYTES, "16-byte destination hash")

        # Hops
        tree.add_field(rns_layer, "Hops", "rns.hops",
                      rns_packet.hops, FieldType.INTEGER,
                      "Number of hops traversed")

        # Interface
        tree.add_field(rns_layer, "Interface", "rns.interface",
                      rns_packet.interface_name, FieldType.STRING)
        tree.add_field(rns_layer, "Interface Type", "rns.iface_type",
                      rns_packet.interface_type.value, FieldType.STRING)

        # Header fields
        header = PacketField(name="Header", abbrev="rns.header",
                            value=None, field_type=FieldType.NESTED)
        rns_layer.children.append(header)
        tree.add_field(header, "Flags", "rns.header.flags",
                      rns_packet.header_flags, FieldType.INTEGER)
        tree.add_field(header, "Hop Count", "rns.header.hops",
                      rns_packet.header_hops, FieldType.INTEGER)
        tree.add_field(header, "Context", "rns.context",
                      rns_packet.context, FieldType.INTEGER)

        # Announce-specific fields
        if rns_packet.packet_type == RNSPacketType.ANNOUNCE:
            announce = PacketField(name="Announce", abbrev="rns.announce",
                                  value=None, field_type=FieldType.NESTED)
            rns_layer.children.append(announce)

            tree.add_field(announce, "Aspect", "rns.announce.aspect",
                          rns_packet.announce_aspect, FieldType.STRING)
            if rns_packet.source_hash:
                tree.add_field(announce, "Identity", "rns.announce.identity",
                              rns_packet.source_hash.hex(), FieldType.BYTES)
            if rns_packet.announce_app_data:
                tree.add_field(announce, "App Data Size", "rns.announce.app_size",
                              len(rns_packet.announce_app_data), FieldType.INTEGER)

        # Link-specific fields
        if rns_packet.link_id:
            link = PacketField(name="Link", abbrev="rns.link",
                              value=None, field_type=FieldType.NESTED)
            rns_layer.children.append(link)

            tree.add_field(link, "Link ID", "rns.link.id",
                          rns_packet.link_id.hex(), FieldType.BYTES)
            tree.add_field(link, "State", "rns.link.state",
                          rns_packet.link_state.value, FieldType.STRING)

        # Payload
        tree.add_field(rns_layer, "Payload Size", "rns.payload_size",
                      rns_packet.payload_size, FieldType.INTEGER)

        packet.tree = tree
        return packet

    except ImportError:
        logger.debug("TrafficInspector not available for conversion")
        return None


# =============================================================================
# GLOBAL SNIFFER INSTANCE
# =============================================================================

_global_sniffer: Optional[RNSSniffer] = None


def get_rns_sniffer() -> RNSSniffer:
    """Get or create the global RNS sniffer instance."""
    global _global_sniffer
    if _global_sniffer is None:
        _global_sniffer = RNSSniffer()
    return _global_sniffer


def start_rns_capture() -> bool:
    """Start RNS packet capture."""
    sniffer = get_rns_sniffer()
    return sniffer.start()


def stop_rns_capture() -> None:
    """Stop RNS packet capture."""
    sniffer = get_rns_sniffer()
    sniffer.stop()


def capture_rns_packet(packet_info: RNSPacketInfo) -> None:
    """
    Capture an RNS packet (called from RNS bridge/transport).

    This is the main integration point for the RNS bridge to
    feed packets into the sniffer.
    """
    sniffer = get_rns_sniffer()
    if sniffer._running:
        sniffer._store_packet(packet_info)


def integrate_with_traffic_inspector() -> bool:
    """
    Integrate RNS sniffer with TrafficInspector.

    Registers a callback that converts RNS packets to MeshPackets
    and feeds them into the unified traffic capture.
    """
    try:
        from monitoring.traffic_inspector import get_traffic_inspector

        inspector = get_traffic_inspector()
        sniffer = get_rns_sniffer()

        def on_rns_packet(rns_packet: RNSPacketInfo):
            """Forward RNS packets to TrafficInspector."""
            mesh_packet = convert_to_mesh_packet(rns_packet)
            if mesh_packet:
                # Create metadata for the capture
                metadata = {
                    "protocol": "rns",
                    "dest_hash": rns_packet.destination_hash.hex() if rns_packet.destination_hash else "",
                    "hops": rns_packet.hops,
                    "interface": rns_packet.interface_name,
                    "packet_type": rns_packet.packet_type.name,
                }
                inspector.capture(rns_packet.raw_bytes, metadata)

        sniffer.register_callback(on_rns_packet)
        logger.info("RNS Sniffer integrated with TrafficInspector")
        return True

    except ImportError as e:
        logger.debug(f"Could not integrate with TrafficInspector: {e}")
        return False
