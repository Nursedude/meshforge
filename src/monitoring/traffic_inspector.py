"""
Traffic Inspector - Wireshark-Grade Traffic Visibility for Mesh Networks.

Provides deep packet inspection, path tracing, and traffic analysis for
both Meshtastic and Reticulum (RNS) mesh networks.

Key Components:
- MeshPacket: Unified packet representation
- PacketDissector: Protocol-aware packet parser
- PacketTree: Hierarchical packet detail display (like Wireshark's protocol tree)
- PathTrace: Hop-by-hop message tracking through the mesh
- TrafficCapture: Real-time packet capture and storage
- DisplayFilter: Field-based filtering (e.g., "mesh.hops > 2")

Usage:
    from monitoring.traffic_inspector import TrafficInspector

    inspector = TrafficInspector()
    inspector.start_capture()

    # Get recent packets
    packets = inspector.get_packets(limit=100)

    # Apply filter
    filtered = inspector.filter("mesh.from == '!abc123' and mesh.hops <= 3")

    # Trace message path
    trace = inspector.trace_message(message_id)

Reference: Inspired by Wireshark's dissector architecture
https://www.wireshark.org/docs/wsdg_html_chunked/ChDissectAdd.html
"""

import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

# Import centralized path utility for sudo compatibility
import os

try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not in Python path."""
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        return Path('/root')

logger = logging.getLogger(__name__)


# Traffic logging configuration
TRAFFIC_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB max log size
TRAFFIC_LOG_BACKUP_COUNT = 3


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class PacketDirection(Enum):
    """Direction of packet flow."""
    INBOUND = "inbound"      # Received from mesh
    OUTBOUND = "outbound"    # Sent to mesh
    INTERNAL = "internal"    # Internal processing
    RELAYED = "relayed"      # Forwarded through us


class PacketProtocol(Enum):
    """Packet protocol type."""
    MESHTASTIC = "meshtastic"
    RNS = "rns"
    BRIDGED = "bridged"      # Cross-protocol (Meshtastic <-> RNS)
    UNKNOWN = "unknown"


class FieldType(Enum):
    """Field data types for display and filtering."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    BYTES = "bytes"
    TIMESTAMP = "timestamp"
    ENUM = "enum"
    NESTED = "nested"        # Contains child fields


class HopState(Enum):
    """State of a message at a hop."""
    RECEIVED = "received"    # Received at this node
    DECODED = "decoded"      # Successfully decoded
    QUEUED = "queued"        # Queued for relay
    RELAYED = "relayed"      # Forwarded to next hop
    DELIVERED = "delivered"  # Final destination
    DROPPED = "dropped"      # Dropped (TTL, duplicate, etc.)
    FAILED = "failed"        # Relay failed


# Meshtastic port numbers (from protobufs)
MESHTASTIC_PORTS = {
    0: "UNKNOWN",
    1: "TEXT_MESSAGE",
    3: "REMOTE_HARDWARE",
    4: "POSITION",
    5: "NODEINFO",
    6: "ROUTING",
    7: "ADMIN",
    8: "TEXT_MESSAGE_COMPRESSED",
    32: "REPLY",
    33: "IP_TUNNEL",
    34: "PAXCOUNTER",
    64: "SERIAL",
    65: "STORE_FORWARD",
    66: "RANGE_TEST",
    67: "TELEMETRY",
    68: "ZPS",
    69: "SIMULATOR",
    70: "TRACEROUTE",
    71: "NEIGHBORINFO",
    72: "ATAK",
    73: "MAP_REPORT",
    256: "PRIVATE",
    257: "ATAK_FORWARDER",
}


# =============================================================================
# PACKET FIELD REPRESENTATION
# =============================================================================

@dataclass
class PacketField:
    """
    A single field in a packet, similar to Wireshark's proto_item.

    Fields have:
    - name: Display name (e.g., "Source Node")
    - abbrev: Abbreviated name for filtering (e.g., "mesh.from")
    - value: The field value
    - field_type: Data type for display/filtering
    - raw_bytes: Original bytes if available
    - children: Nested fields for hierarchical display
    """
    name: str
    abbrev: str
    value: Any
    field_type: FieldType = FieldType.STRING
    raw_bytes: Optional[bytes] = None
    offset: int = 0
    length: int = 0
    children: List['PacketField'] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "abbrev": self.abbrev,
            "value": self._serialize_value(),
            "type": self.field_type.value,
            "description": self.description,
        }
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        if self.raw_bytes:
            result["raw"] = self.raw_bytes.hex()
        return result

    def _serialize_value(self) -> Any:
        """Serialize value for JSON."""
        if isinstance(self.value, bytes):
            return self.value.hex()
        if isinstance(self.value, datetime):
            return self.value.isoformat()
        if isinstance(self.value, Enum):
            return self.value.value
        return self.value

    def get_display_value(self) -> str:
        """Get human-readable display value."""
        if self.value is None:
            return "<none>"
        if isinstance(self.value, bytes):
            if len(self.value) <= 8:
                return self.value.hex()
            return f"{self.value[:8].hex()}... ({len(self.value)} bytes)"
        if isinstance(self.value, bool):
            return "True" if self.value else "False"
        if isinstance(self.value, float):
            return f"{self.value:.4f}"
        if isinstance(self.value, datetime):
            return self.value.strftime("%Y-%m-%d %H:%M:%S")
        return str(self.value)

    def matches_filter(self, operator: str, compare_value: Any) -> bool:
        """Check if field matches a filter expression."""
        try:
            if self.field_type == FieldType.INTEGER:
                val = int(self.value) if self.value is not None else 0
                cmp = int(compare_value)
            elif self.field_type == FieldType.FLOAT:
                val = float(self.value) if self.value is not None else 0.0
                cmp = float(compare_value)
            elif self.field_type == FieldType.BOOLEAN:
                val = bool(self.value)
                cmp = compare_value.lower() in ('true', '1', 'yes')
            else:
                val = str(self.value) if self.value is not None else ""
                cmp = str(compare_value)

            if operator == "==":
                return val == cmp
            elif operator == "!=":
                return val != cmp
            elif operator == ">":
                return val > cmp
            elif operator == ">=":
                return val >= cmp
            elif operator == "<":
                return val < cmp
            elif operator == "<=":
                return val <= cmp
            elif operator == "contains":
                return cmp.lower() in str(val).lower()
            elif operator == "matches":
                return bool(re.search(cmp, str(val)))

        except (ValueError, TypeError):
            return False

        return False


# =============================================================================
# PACKET TREE (HIERARCHICAL DISPLAY)
# =============================================================================

class PacketTree:
    """
    Hierarchical packet detail display, similar to Wireshark's protocol tree.

    Organizes packet fields into a tree structure:
    - Frame (timing, direction, size)
      - Protocol Layer (Meshtastic/RNS)
        - Header Fields
        - Payload Fields
        - Metrics (SNR, RSSI)
    """

    def __init__(self):
        self.root_fields: List[PacketField] = []
        self._field_index: Dict[str, PacketField] = {}  # abbrev -> field

    def add_layer(self, layer_name: str, abbrev_prefix: str) -> PacketField:
        """Add a protocol layer to the tree."""
        layer = PacketField(
            name=layer_name,
            abbrev=abbrev_prefix,
            value=None,
            field_type=FieldType.NESTED,
        )
        self.root_fields.append(layer)
        return layer

    def add_field(self, parent: PacketField, name: str, abbrev: str,
                  value: Any, field_type: FieldType = FieldType.STRING,
                  description: str = "") -> PacketField:
        """Add a field to a parent layer."""
        field_obj = PacketField(
            name=name,
            abbrev=abbrev,
            value=value,
            field_type=field_type,
            description=description,
        )
        parent.children.append(field_obj)
        self._field_index[abbrev] = field_obj
        return field_obj

    def get_field(self, abbrev: str) -> Optional[PacketField]:
        """Get field by abbreviated name."""
        return self._field_index.get(abbrev)

    def get_all_fields(self) -> Dict[str, PacketField]:
        """Get all fields indexed by abbreviation."""
        return dict(self._field_index)

    def to_dict(self) -> List[Dict[str, Any]]:
        """Convert tree to dictionary for serialization."""
        return [f.to_dict() for f in self.root_fields]

    def format_ascii(self, indent: int = 2) -> str:
        """Format tree as ASCII for terminal display."""
        lines = []

        def format_field(f: PacketField, level: int):
            prefix = " " * (level * indent)
            if f.children:
                lines.append(f"{prefix}[+] {f.name}")
                for child in f.children:
                    format_field(child, level + 1)
            else:
                display_val = f.get_display_value()
                lines.append(f"{prefix}{f.name}: {display_val}")

        for root in self.root_fields:
            format_field(root, 0)

        return "\n".join(lines)


# =============================================================================
# MESH PACKET (UNIFIED REPRESENTATION)
# =============================================================================

@dataclass
class MeshPacket:
    """
    Unified packet representation for both Meshtastic and RNS.

    Like Wireshark's frame_data, this captures all packet metadata
    and enables cross-protocol analysis.
    """
    # Unique identifier (microseconds + random for uniqueness)
    id: str = field(default_factory=lambda: f"pkt_{int(time.time()*1000000)}_{random.randint(0, 9999):04d}")

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)
    capture_time_us: int = 0  # Microseconds since capture start

    # Direction and protocol
    direction: PacketDirection = PacketDirection.INBOUND
    protocol: PacketProtocol = PacketProtocol.UNKNOWN

    # Addressing
    source: str = ""          # Source node ID
    destination: str = ""     # Destination node ID (or "broadcast")
    channel: int = 0          # Channel number (Meshtastic)

    # Routing
    hop_limit: int = 3        # Maximum hops allowed
    hop_start: int = 3        # Original hop limit
    hops_taken: int = 0       # Hops traversed so far
    via_mqtt: bool = False    # Received via MQTT
    want_ack: bool = False    # ACK requested

    # Payload
    portnum: int = 0          # Meshtastic port number
    port_name: str = ""       # Human-readable port name
    payload: bytes = b""      # Raw payload
    decoded_payload: Optional[Dict[str, Any]] = None  # Decoded content

    # Metrics
    snr: Optional[float] = None
    rssi: Optional[int] = None
    channel_utilization: Optional[float] = None
    air_util_tx: Optional[float] = None

    # RNS-specific
    rns_dest_hash: Optional[bytes] = None
    rns_interface: str = ""
    rns_service: str = ""

    # Path tracing
    path_trace: List['HopInfo'] = field(default_factory=list)

    # Raw data
    raw_bytes: bytes = b""
    size: int = 0

    # Protocol tree (populated by dissector)
    tree: Optional[PacketTree] = None

    def __post_init__(self):
        """Initialize derived fields."""
        if not self.port_name and self.portnum:
            self.port_name = MESHTASTIC_PORTS.get(self.portnum, f"PORT_{self.portnum}")
        if not self.size and self.raw_bytes:
            self.size = len(self.raw_bytes)
        self.hops_taken = self.hop_start - self.hop_limit

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction.value,
            "protocol": self.protocol.value,
            "source": self.source,
            "destination": self.destination,
            "channel": self.channel,
            "hop_limit": self.hop_limit,
            "hop_start": self.hop_start,
            "hops_taken": self.hops_taken,
            "via_mqtt": self.via_mqtt,
            "want_ack": self.want_ack,
            "portnum": self.portnum,
            "port_name": self.port_name,
            "payload_size": len(self.payload),
            "decoded_payload": self.decoded_payload,
            "snr": self.snr,
            "rssi": self.rssi,
            "rns_dest_hash": self.rns_dest_hash.hex() if self.rns_dest_hash else None,
            "rns_interface": self.rns_interface,
            "size": self.size,
            "tree": self.tree.to_dict() if self.tree else None,
            "path_trace": [h.to_dict() for h in self.path_trace],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MeshPacket':
        """Create packet from dictionary."""
        return cls(
            id=data.get("id", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(),
            direction=PacketDirection(data.get("direction", "inbound")),
            protocol=PacketProtocol(data.get("protocol", "unknown")),
            source=data.get("source", ""),
            destination=data.get("destination", ""),
            channel=data.get("channel", 0),
            hop_limit=data.get("hop_limit", 3),
            hop_start=data.get("hop_start", 3),
            via_mqtt=data.get("via_mqtt", False),
            want_ack=data.get("want_ack", False),
            portnum=data.get("portnum", 0),
            port_name=data.get("port_name", ""),
            decoded_payload=data.get("decoded_payload"),
            snr=data.get("snr"),
            rssi=data.get("rssi"),
            rns_dest_hash=bytes.fromhex(data["rns_dest_hash"]) if data.get("rns_dest_hash") else None,
            rns_interface=data.get("rns_interface", ""),
            size=data.get("size", 0),
        )

    def get_summary(self) -> str:
        """Get one-line summary for list display."""
        dir_sym = {"inbound": "<-", "outbound": "->", "relayed": "<>", "internal": ".."}
        dir_str = dir_sym.get(self.direction.value, "??")

        src = self.source[:12] if self.source else "?"
        dst = self.destination[:12] if self.destination else "broadcast"

        if self.protocol == PacketProtocol.MESHTASTIC:
            info = f"[{self.port_name}]"
        elif self.protocol == PacketProtocol.RNS:
            info = f"[RNS:{self.rns_service or 'announce'}]"
        else:
            info = "[?]"

        hops = f"h={self.hops_taken}" if self.hops_taken else ""
        metrics = ""
        if self.snr is not None:
            metrics = f" SNR:{self.snr:.1f}"
        if self.rssi is not None:
            metrics += f" RSSI:{self.rssi}"

        return f"{src} {dir_str} {dst} {info} {hops}{metrics}"


# =============================================================================
# HOP INFO (PATH TRACING)
# =============================================================================

@dataclass
class HopInfo:
    """
    Information about a single hop in a message's path.

    Captures timing, node info, and metrics at each relay point.
    """
    hop_number: int
    node_id: str
    node_name: str = ""
    state: HopState = HopState.RECEIVED
    timestamp: datetime = field(default_factory=datetime.now)

    # Position (if known)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Metrics at this hop
    snr: Optional[float] = None
    rssi: Optional[int] = None

    # Timing
    latency_ms: Optional[float] = None  # Time since previous hop

    # Additional details
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hop": self.hop_number,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "state": self.state.value,
            "timestamp": self.timestamp.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "snr": self.snr,
            "rssi": self.rssi,
            "latency_ms": self.latency_ms,
            "details": self.details,
        }


# =============================================================================
# PACKET DISSECTOR (PROTOCOL PARSER)
# =============================================================================

class PacketDissector(ABC):
    """
    Base class for protocol dissectors.

    Each protocol (Meshtastic, RNS) has its own dissector that parses
    raw packets into structured MeshPacket objects with full PacketTree.
    """

    @abstractmethod
    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this dissector can handle the data."""
        pass

    @abstractmethod
    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse raw data into a MeshPacket with protocol tree."""
        pass

    def _add_frame_layer(self, tree: PacketTree, packet: MeshPacket) -> PacketField:
        """Add frame-level fields common to all protocols."""
        frame = tree.add_layer("Frame", "frame")
        tree.add_field(frame, "Timestamp", "frame.time",
                       packet.timestamp, FieldType.TIMESTAMP)
        tree.add_field(frame, "Direction", "frame.direction",
                       packet.direction.value, FieldType.ENUM)
        tree.add_field(frame, "Size", "frame.size",
                       packet.size, FieldType.INTEGER,
                       "Total packet size in bytes")
        tree.add_field(frame, "Protocol", "frame.protocol",
                       packet.protocol.value, FieldType.ENUM)
        return frame


class MeshtasticDissector(PacketDissector):
    """
    Dissector for Meshtastic packets.

    Parses Meshtastic protocol packets including:
    - Header (from, to, channel, hop_limit)
    - Payload (portnum, decoded content)
    - Metrics (SNR, RSSI)
    """

    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this looks like a Meshtastic packet."""
        # Check for meshtastic indicators in metadata
        if metadata.get("protocol") == "meshtastic":
            return True
        if "from" in metadata and "to" in metadata:
            return True
        return False

    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse Meshtastic packet into MeshPacket."""
        packet = MeshPacket(
            protocol=PacketProtocol.MESHTASTIC,
            raw_bytes=data,
            size=len(data) if data else 0,
        )

        # Extract from metadata (typically from meshtastic-python callbacks)
        packet.source = str(metadata.get("from", metadata.get("fromId", "")))
        packet.destination = str(metadata.get("to", metadata.get("toId", "broadcast")))
        packet.channel = int(metadata.get("channel", 0))
        packet.hop_limit = int(metadata.get("hopLimit", 3))
        packet.hop_start = int(metadata.get("hopStart", 3))
        packet.hops_taken = packet.hop_start - packet.hop_limit  # Recalculate after setting
        packet.via_mqtt = bool(metadata.get("viaMqtt", False))
        packet.want_ack = bool(metadata.get("wantAck", False))

        # Payload info
        packet.portnum = int(metadata.get("portnum", metadata.get("decoded", {}).get("portnum", 0)))
        packet.port_name = MESHTASTIC_PORTS.get(packet.portnum, f"PORT_{packet.portnum}")

        # Decoded payload
        if "decoded" in metadata:
            decoded = metadata["decoded"]
            packet.decoded_payload = decoded
            if "payload" in decoded:
                try:
                    packet.payload = decoded["payload"] if isinstance(decoded["payload"], bytes) else decoded["payload"].encode()
                except (AttributeError, UnicodeError):
                    pass

        # Metrics
        packet.snr = metadata.get("snr", metadata.get("rxSnr"))
        packet.rssi = metadata.get("rssi", metadata.get("rxRssi"))
        if "channelUtilization" in metadata:
            packet.channel_utilization = metadata["channelUtilization"]
        if "airUtilTx" in metadata:
            packet.air_util_tx = metadata["airUtilTx"]

        # Direction
        if metadata.get("direction") == "outbound":
            packet.direction = PacketDirection.OUTBOUND
        elif metadata.get("relayed"):
            packet.direction = PacketDirection.RELAYED
        else:
            packet.direction = PacketDirection.INBOUND

        # Build protocol tree
        packet.tree = self._build_tree(packet, metadata)

        return packet

    def _build_tree(self, packet: MeshPacket, metadata: Dict[str, Any]) -> PacketTree:
        """Build hierarchical protocol tree."""
        tree = PacketTree()

        # Frame layer
        self._add_frame_layer(tree, packet)

        # Meshtastic layer
        mesh = tree.add_layer("Meshtastic", "mesh")

        # Addressing
        tree.add_field(mesh, "Source", "mesh.from", packet.source, FieldType.STRING,
                       "Source node ID")
        tree.add_field(mesh, "Destination", "mesh.to", packet.destination, FieldType.STRING,
                       "Destination node ID (^all for broadcast)")
        tree.add_field(mesh, "Channel", "mesh.channel", packet.channel, FieldType.INTEGER,
                       "Channel index (0-7)")

        # Routing
        routing = PacketField(name="Routing", abbrev="mesh.routing",
                              value=None, field_type=FieldType.NESTED)
        mesh.children.append(routing)

        tree.add_field(routing, "Hop Limit", "mesh.hop_limit", packet.hop_limit, FieldType.INTEGER,
                       "Remaining hops allowed")
        tree.add_field(routing, "Hop Start", "mesh.hop_start", packet.hop_start, FieldType.INTEGER,
                       "Original hop limit when sent")
        tree.add_field(routing, "Hops Taken", "mesh.hops", packet.hops_taken, FieldType.INTEGER,
                       "Number of hops traversed")
        tree.add_field(routing, "Via MQTT", "mesh.mqtt", packet.via_mqtt, FieldType.BOOLEAN,
                       "Received via MQTT broker")
        tree.add_field(routing, "Want ACK", "mesh.ack", packet.want_ack, FieldType.BOOLEAN,
                       "Acknowledgement requested")

        # Payload
        payload_field = PacketField(name="Payload", abbrev="mesh.payload",
                                    value=None, field_type=FieldType.NESTED)
        mesh.children.append(payload_field)

        tree.add_field(payload_field, "Port Number", "mesh.portnum", packet.portnum, FieldType.INTEGER,
                       "Meshtastic application port")
        tree.add_field(payload_field, "Port Name", "mesh.port", packet.port_name, FieldType.STRING,
                       "Application type")
        tree.add_field(payload_field, "Size", "mesh.payload_size", len(packet.payload), FieldType.INTEGER,
                       "Payload size in bytes")

        # Decoded content based on port
        if packet.decoded_payload:
            self._add_decoded_fields(tree, payload_field, packet)

        # Metrics
        if packet.snr is not None or packet.rssi is not None:
            metrics = PacketField(name="Radio Metrics", abbrev="mesh.radio",
                                  value=None, field_type=FieldType.NESTED)
            mesh.children.append(metrics)

            if packet.snr is not None:
                tree.add_field(metrics, "SNR", "mesh.snr", packet.snr, FieldType.FLOAT,
                               "Signal-to-Noise Ratio (dB)")
            if packet.rssi is not None:
                tree.add_field(metrics, "RSSI", "mesh.rssi", packet.rssi, FieldType.INTEGER,
                               "Received Signal Strength (dBm)")
            if packet.channel_utilization is not None:
                tree.add_field(metrics, "Channel Util", "mesh.chan_util",
                               packet.channel_utilization, FieldType.FLOAT,
                               "Channel utilization percentage")
            if packet.air_util_tx is not None:
                tree.add_field(metrics, "TX Air Util", "mesh.air_util",
                               packet.air_util_tx, FieldType.FLOAT,
                               "Transmit airtime utilization")

        return tree

    def _add_decoded_fields(self, tree: PacketTree, parent: PacketField,
                            packet: MeshPacket) -> None:
        """Add decoded payload fields based on port type."""
        decoded = packet.decoded_payload

        if packet.portnum == 1:  # TEXT_MESSAGE
            text = decoded.get("text", decoded.get("payload", ""))
            tree.add_field(parent, "Text", "mesh.text", text, FieldType.STRING)

        elif packet.portnum == 4:  # POSITION
            pos_data = decoded.get("position", decoded)
            if "latitude" in pos_data:
                tree.add_field(parent, "Latitude", "mesh.pos.lat",
                               pos_data["latitude"], FieldType.FLOAT)
            if "longitude" in pos_data:
                tree.add_field(parent, "Longitude", "mesh.pos.lon",
                               pos_data["longitude"], FieldType.FLOAT)
            if "altitude" in pos_data:
                tree.add_field(parent, "Altitude", "mesh.pos.alt",
                               pos_data["altitude"], FieldType.INTEGER)

        elif packet.portnum == 5:  # NODEINFO
            user_data = decoded.get("user", decoded)
            if "longName" in user_data:
                tree.add_field(parent, "Long Name", "mesh.user.long",
                               user_data["longName"], FieldType.STRING)
            if "shortName" in user_data:
                tree.add_field(parent, "Short Name", "mesh.user.short",
                               user_data["shortName"], FieldType.STRING)
            if "hwModel" in user_data:
                tree.add_field(parent, "Hardware", "mesh.user.hw",
                               user_data["hwModel"], FieldType.STRING)

        elif packet.portnum == 67:  # TELEMETRY
            telem = decoded.get("telemetry", decoded)
            if "deviceMetrics" in telem:
                dm = telem["deviceMetrics"]
                if "batteryLevel" in dm:
                    tree.add_field(parent, "Battery", "mesh.telem.battery",
                                   dm["batteryLevel"], FieldType.INTEGER)
                if "voltage" in dm:
                    tree.add_field(parent, "Voltage", "mesh.telem.voltage",
                                   dm["voltage"], FieldType.FLOAT)
                if "channelUtilization" in dm:
                    tree.add_field(parent, "Ch Util", "mesh.telem.ch_util",
                                   dm["channelUtilization"], FieldType.FLOAT)

        elif packet.portnum == 70:  # TRACEROUTE
            route = decoded.get("route", decoded.get("routeBack", []))
            tree.add_field(parent, "Route", "mesh.traceroute",
                           route, FieldType.STRING)


class RNSDissector(PacketDissector):
    """
    Dissector for Reticulum (RNS) packets.

    Parses RNS protocol data including:
    - Destination hash
    - Hop count
    - Interface info
    - Service type
    """

    def can_dissect(self, data: bytes, metadata: Dict[str, Any]) -> bool:
        """Check if this looks like an RNS packet."""
        if metadata.get("protocol") == "rns":
            return True
        if "dest_hash" in metadata or "destination_hash" in metadata:
            return True
        return False

    def dissect(self, data: bytes, metadata: Dict[str, Any]) -> MeshPacket:
        """Parse RNS packet into MeshPacket."""
        packet = MeshPacket(
            protocol=PacketProtocol.RNS,
            raw_bytes=data,
            size=len(data) if data else 0,
        )

        # Extract RNS-specific fields
        dest_hash = metadata.get("dest_hash", metadata.get("destination_hash"))
        if isinstance(dest_hash, str):
            packet.rns_dest_hash = bytes.fromhex(dest_hash)
        elif isinstance(dest_hash, bytes):
            packet.rns_dest_hash = dest_hash

        packet.destination = metadata.get("destination", "")
        if packet.rns_dest_hash:
            packet.destination = packet.rns_dest_hash.hex()[:16]

        packet.source = metadata.get("source", "local")
        packet.rns_interface = metadata.get("interface", "")
        packet.rns_service = metadata.get("service_type", metadata.get("aspect", ""))

        # Hop count
        packet.hops_taken = int(metadata.get("hops", 0))
        packet.hop_limit = 128 - packet.hops_taken  # RNS has higher hop limit

        # Direction
        if metadata.get("direction") == "outbound":
            packet.direction = PacketDirection.OUTBOUND
        else:
            packet.direction = PacketDirection.INBOUND

        # Build protocol tree
        packet.tree = self._build_tree(packet, metadata)

        return packet

    def _build_tree(self, packet: MeshPacket, metadata: Dict[str, Any]) -> PacketTree:
        """Build hierarchical protocol tree for RNS."""
        tree = PacketTree()

        # Frame layer
        self._add_frame_layer(tree, packet)

        # RNS layer
        rns = tree.add_layer("Reticulum", "rns")

        # Destination
        tree.add_field(rns, "Destination Hash", "rns.dest_hash",
                       packet.rns_dest_hash.hex() if packet.rns_dest_hash else "",
                       FieldType.BYTES, "16-byte destination hash")

        # Routing
        tree.add_field(rns, "Hops", "rns.hops", packet.hops_taken, FieldType.INTEGER,
                       "Number of hops to destination")
        tree.add_field(rns, "Interface", "rns.interface", packet.rns_interface, FieldType.STRING,
                       "RNS interface name")

        # Service info
        if packet.rns_service:
            tree.add_field(rns, "Service", "rns.service", packet.rns_service, FieldType.STRING,
                           "Service type/aspect")

        # Announce details if available
        if "announce_data" in metadata:
            announce = PacketField(name="Announce", abbrev="rns.announce",
                                   value=None, field_type=FieldType.NESTED)
            rns.children.append(announce)

            ann_data = metadata["announce_data"]
            if "app_data" in ann_data:
                tree.add_field(announce, "App Data", "rns.announce.app",
                               ann_data["app_data"], FieldType.BYTES)

        return tree


# =============================================================================
# DISPLAY FILTER (FILTERING SYSTEM)
# =============================================================================

class DisplayFilter:
    """
    Wireshark-style display filter for packets.

    Supports expressions like:
    - mesh.hops > 2
    - mesh.from == "!abc123"
    - mesh.snr >= -5 and mesh.portnum == 1
    - rns.hops <= 3 or mesh.mqtt == true
    """

    # Regex for parsing filter expressions
    FIELD_PATTERN = re.compile(
        r'(\w+\.\w+(?:\.\w+)?)\s*(==|!=|>=|<=|>|<|contains|matches)\s*'
        r'(?:"([^"]*)"|\'([^\']*)\'|(\S+))'
    )

    def __init__(self, expression: str = ""):
        self.expression = expression.strip()
        self._compiled: Optional[List[Tuple[str, str, str, str]]] = None

    def compile(self) -> bool:
        """Compile the filter expression."""
        if not self.expression:
            self._compiled = []
            return True

        # Split on 'and' / 'or' (simple implementation)
        # For now, treat all conditions as AND
        expr = self.expression.lower()
        expr = re.sub(r'\s+and\s+', ' AND ', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\s+or\s+', ' OR ', expr, flags=re.IGNORECASE)

        parts = []
        current_logic = "AND"

        # Parse each condition
        for match in self.FIELD_PATTERN.finditer(self.expression):
            field_name = match.group(1)
            operator = match.group(2)
            value = match.group(3) or match.group(4) or match.group(5)
            parts.append((field_name, operator, value, current_logic))

        self._compiled = parts
        return len(parts) > 0 or not self.expression

    def matches(self, packet: MeshPacket) -> bool:
        """Check if packet matches the filter."""
        if self._compiled is None:
            self.compile()

        if not self._compiled:
            return True  # Empty filter matches all

        if not packet.tree:
            return False

        # Check all conditions (simple AND logic for now)
        for field_name, operator, value, logic in self._compiled:
            field = packet.tree.get_field(field_name)
            if field is None:
                # Field not found - doesn't match
                return False
            if not field.matches_filter(operator, value):
                return False

        return True

    @classmethod
    def get_available_fields(cls) -> Dict[str, str]:
        """Get list of available filter fields."""
        return {
            # Frame fields
            "frame.time": "Packet timestamp",
            "frame.direction": "Packet direction (inbound/outbound)",
            "frame.size": "Packet size in bytes",
            "frame.protocol": "Protocol (meshtastic/rns)",

            # Meshtastic fields
            "mesh.from": "Source node ID",
            "mesh.to": "Destination node ID",
            "mesh.channel": "Channel number",
            "mesh.hop_limit": "Remaining hops",
            "mesh.hop_start": "Original hop limit",
            "mesh.hops": "Hops taken",
            "mesh.mqtt": "Via MQTT (true/false)",
            "mesh.ack": "ACK requested (true/false)",
            "mesh.portnum": "Port number",
            "mesh.port": "Port name",
            "mesh.snr": "Signal-to-Noise Ratio",
            "mesh.rssi": "Received Signal Strength",
            "mesh.text": "Text message content",

            # RNS fields
            "rns.dest_hash": "Destination hash",
            "rns.hops": "Hop count",
            "rns.interface": "Interface name",
            "rns.service": "Service type",
        }


# =============================================================================
# TRAFFIC CAPTURE (STORAGE AND RETRIEVAL)
# =============================================================================

class TrafficCapture:
    """
    Captures and stores mesh traffic for analysis.

    Features:
    - SQLite-backed persistent storage
    - Real-time packet callbacks
    - Time-based and filter-based queries
    - Path trace aggregation
    """

    DEFAULT_MAX_PACKETS = 10000
    CLEANUP_INTERVAL = 3600  # 1 hour

    def __init__(self, db_path: Optional[str] = None,
                 max_packets: int = DEFAULT_MAX_PACKETS):
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "traffic_capture.db")

        self._db_path = db_path
        self._max_packets = max_packets
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[MeshPacket], None]] = []
        self._running = False
        self._last_cleanup = 0.0

        # Dissectors
        self._dissectors: List[PacketDissector] = [
            MeshtasticDissector(),
            RNSDissector(),
        ]

        # Statistics
        self._stats = {
            "packets_captured": 0,
            "packets_meshtastic": 0,
            "packets_rns": 0,
            "bytes_captured": 0,
        }

        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management."""
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS packets (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    source TEXT,
                    destination TEXT,
                    channel INTEGER,
                    hop_limit INTEGER,
                    hop_start INTEGER,
                    hops_taken INTEGER,
                    portnum INTEGER,
                    port_name TEXT,
                    snr REAL,
                    rssi INTEGER,
                    size INTEGER,
                    data TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_timestamp
                ON packets(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_source
                ON packets(source)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_packets_protocol
                ON packets(protocol)
            """)

            # Path traces table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS path_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    packet_id TEXT NOT NULL,
                    hop_number INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    node_name TEXT,
                    state TEXT,
                    timestamp TEXT,
                    snr REAL,
                    rssi INTEGER,
                    latency_ms REAL,
                    latitude REAL,
                    longitude REAL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_packet
                ON path_traces(packet_id)
            """)

    def capture_packet(self, data: bytes, metadata: Dict[str, Any]) -> Optional[MeshPacket]:
        """
        Capture and dissect a packet.

        Args:
            data: Raw packet bytes (can be empty for metadata-only)
            metadata: Packet metadata from the source

        Returns:
            Dissected MeshPacket, or None if cannot dissect
        """
        # Find appropriate dissector
        packet = None
        for dissector in self._dissectors:
            if dissector.can_dissect(data, metadata):
                packet = dissector.dissect(data, metadata)
                break

        if packet is None:
            # Create basic packet from metadata
            packet = MeshPacket(
                protocol=PacketProtocol.UNKNOWN,
                raw_bytes=data,
                size=len(data) if data else 0,
            )

        # Store packet
        self._store_packet(packet)

        # Update stats
        with self._lock:
            self._stats["packets_captured"] += 1
            self._stats["bytes_captured"] += packet.size
            if packet.protocol == PacketProtocol.MESHTASTIC:
                self._stats["packets_meshtastic"] += 1
            elif packet.protocol == PacketProtocol.RNS:
                self._stats["packets_rns"] += 1

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(packet)
            except Exception as e:
                logger.debug(f"Packet callback error: {e}")

        # Periodic cleanup
        self._maybe_cleanup()

        return packet

    def _store_packet(self, packet: MeshPacket) -> None:
        """Store packet in database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO packets
                (id, timestamp, direction, protocol, source, destination,
                 channel, hop_limit, hop_start, hops_taken, portnum,
                 port_name, snr, rssi, size, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                packet.id,
                packet.timestamp.isoformat(),
                packet.direction.value,
                packet.protocol.value,
                packet.source,
                packet.destination,
                packet.channel,
                packet.hop_limit,
                packet.hop_start,
                packet.hops_taken,
                packet.portnum,
                packet.port_name,
                packet.snr,
                packet.rssi,
                packet.size,
                json.dumps(packet.to_dict()),
            ))

            # Store path traces
            for hop in packet.path_trace:
                conn.execute("""
                    INSERT INTO path_traces
                    (packet_id, hop_number, node_id, node_name, state,
                     timestamp, snr, rssi, latency_ms, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    packet.id,
                    hop.hop_number,
                    hop.node_id,
                    hop.node_name,
                    hop.state.value,
                    hop.timestamp.isoformat(),
                    hop.snr,
                    hop.rssi,
                    hop.latency_ms,
                    hop.latitude,
                    hop.longitude,
                ))

    def get_packets(self, limit: int = 100, offset: int = 0,
                    filter_expr: Optional[str] = None,
                    since: Optional[datetime] = None,
                    until: Optional[datetime] = None,
                    protocol: Optional[PacketProtocol] = None,
                    source: Optional[str] = None) -> List[MeshPacket]:
        """
        Retrieve packets from capture database.

        Args:
            limit: Maximum packets to return
            offset: Skip first N packets
            filter_expr: Display filter expression
            since: Only packets after this time
            until: Only packets before this time
            protocol: Filter by protocol
            source: Filter by source node

        Returns:
            List of MeshPacket objects
        """
        query = "SELECT data FROM packets WHERE 1=1"
        params: List[Any] = []

        if since:
            query += " AND timestamp >= ?"
            params.append(since.isoformat())

        if until:
            query += " AND timestamp <= ?"
            params.append(until.isoformat())

        if protocol:
            query += " AND protocol = ?"
            params.append(protocol.value)

        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        packets = []
        display_filter = DisplayFilter(filter_expr) if filter_expr else None

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            for row in cursor.fetchall():
                try:
                    data = json.loads(row["data"])
                    packet = MeshPacket.from_dict(data)

                    # Rebuild tree for filtering
                    for dissector in self._dissectors:
                        if dissector.can_dissect(b"", {"protocol": packet.protocol.value}):
                            packet.tree = dissector._build_tree(packet, data)
                            break

                    # Apply display filter
                    if display_filter and not display_filter.matches(packet):
                        continue

                    packets.append(packet)

                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Error parsing packet: {e}")

        return packets

    def get_packet_count(self, protocol: Optional[PacketProtocol] = None) -> int:
        """Get total packet count."""
        with self._get_connection() as conn:
            if protocol:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM packets WHERE protocol = ?",
                    (protocol.value,)
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM packets")
            return cursor.fetchone()[0]

    def get_path_trace(self, packet_id: str) -> List[HopInfo]:
        """Get path trace for a packet."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM path_traces
                WHERE packet_id = ?
                ORDER BY hop_number ASC
            """, (packet_id,))

            hops = []
            for row in cursor.fetchall():
                hops.append(HopInfo(
                    hop_number=row["hop_number"],
                    node_id=row["node_id"],
                    node_name=row["node_name"] or "",
                    state=HopState(row["state"]) if row["state"] else HopState.RECEIVED,
                    timestamp=datetime.fromisoformat(row["timestamp"]) if row["timestamp"] else datetime.now(),
                    snr=row["snr"],
                    rssi=row["rssi"],
                    latency_ms=row["latency_ms"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                ))

            return hops

    def register_callback(self, callback: Callable[[MeshPacket], None]) -> None:
        """Register callback for new packets."""
        with self._lock:
            self._callbacks.append(callback)

    def get_stats(self) -> Dict[str, Any]:
        """Get capture statistics."""
        with self._lock:
            stats = dict(self._stats)

        stats["packet_count"] = self.get_packet_count()
        stats["meshtastic_count"] = self.get_packet_count(PacketProtocol.MESHTASTIC)
        stats["rns_count"] = self.get_packet_count(PacketProtocol.RNS)

        return stats

    def _maybe_cleanup(self) -> None:
        """Periodically clean up old packets."""
        now = time.time()
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return

        self._last_cleanup = now
        self._cleanup_old_packets()

    def _cleanup_old_packets(self) -> int:
        """Remove oldest packets if over limit."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM packets")
            count = cursor.fetchone()[0]

            if count <= self._max_packets:
                return 0

            # Delete oldest packets
            to_delete = count - self._max_packets
            conn.execute("""
                DELETE FROM packets WHERE id IN (
                    SELECT id FROM packets
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
            """, (to_delete,))

            logger.debug(f"Cleaned up {to_delete} old packets")
            return to_delete

    def clear_all(self) -> int:
        """Clear all captured packets."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM packets")
            deleted = cursor.rowcount
            conn.execute("DELETE FROM path_traces")
            return deleted


# =============================================================================
# TRAFFIC STATISTICS
# =============================================================================

@dataclass
class TrafficStats:
    """Aggregated traffic statistics."""
    total_packets: int = 0
    total_bytes: int = 0

    packets_by_protocol: Dict[str, int] = field(default_factory=dict)
    packets_by_port: Dict[str, int] = field(default_factory=dict)
    packets_by_direction: Dict[str, int] = field(default_factory=dict)

    # Time series data (for graphing)
    packets_per_minute: List[Tuple[datetime, int]] = field(default_factory=list)
    bytes_per_minute: List[Tuple[datetime, int]] = field(default_factory=list)

    # Hop statistics
    avg_hops: float = 0.0
    max_hops: int = 0
    hops_distribution: Dict[int, int] = field(default_factory=dict)

    # Signal statistics
    avg_snr: Optional[float] = None
    avg_rssi: Optional[float] = None
    snr_distribution: Dict[int, int] = field(default_factory=dict)

    # Top nodes
    top_sources: List[Tuple[str, int]] = field(default_factory=list)
    top_destinations: List[Tuple[str, int]] = field(default_factory=list)


class TrafficAnalyzer:
    """
    Analyzes captured traffic for statistics and patterns.

    Provides:
    - Packet/byte counts by protocol, port, direction
    - Time series data for I/O graphs
    - Hop count distribution
    - Signal quality statistics
    - Top talkers/listeners
    """

    def __init__(self, capture: TrafficCapture):
        self._capture = capture

    def get_stats(self, since: Optional[datetime] = None,
                  until: Optional[datetime] = None) -> TrafficStats:
        """
        Calculate traffic statistics for a time range.

        Args:
            since: Start of time range (default: last 24 hours)
            until: End of time range (default: now)

        Returns:
            TrafficStats with aggregated data
        """
        if since is None:
            since = datetime.now() - timedelta(hours=24)
        if until is None:
            until = datetime.now()

        stats = TrafficStats()

        # Get all packets in range
        packets = self._capture.get_packets(
            limit=10000,
            since=since,
            until=until,
        )

        if not packets:
            return stats

        stats.total_packets = len(packets)
        stats.total_bytes = sum(p.size for p in packets)

        # Aggregate by protocol
        for p in packets:
            proto = p.protocol.value
            stats.packets_by_protocol[proto] = stats.packets_by_protocol.get(proto, 0) + 1

        # Aggregate by port
        for p in packets:
            if p.port_name:
                stats.packets_by_port[p.port_name] = stats.packets_by_port.get(p.port_name, 0) + 1

        # Aggregate by direction
        for p in packets:
            direction = p.direction.value
            stats.packets_by_direction[direction] = stats.packets_by_direction.get(direction, 0) + 1

        # Hop statistics
        hops = [p.hops_taken for p in packets if p.hops_taken > 0]
        if hops:
            stats.avg_hops = sum(hops) / len(hops)
            stats.max_hops = max(hops)
            for h in hops:
                stats.hops_distribution[h] = stats.hops_distribution.get(h, 0) + 1

        # Signal statistics
        snr_values = [p.snr for p in packets if p.snr is not None]
        rssi_values = [p.rssi for p in packets if p.rssi is not None]

        if snr_values:
            stats.avg_snr = sum(snr_values) / len(snr_values)
            for snr in snr_values:
                bucket = int(snr // 5) * 5  # 5 dB buckets
                stats.snr_distribution[bucket] = stats.snr_distribution.get(bucket, 0) + 1

        if rssi_values:
            stats.avg_rssi = sum(rssi_values) / len(rssi_values)

        # Top sources
        source_counts: Dict[str, int] = defaultdict(int)
        for p in packets:
            if p.source:
                source_counts[p.source] += 1
        stats.top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Top destinations
        dest_counts: Dict[str, int] = defaultdict(int)
        for p in packets:
            if p.destination:
                dest_counts[p.destination] += 1
        stats.top_destinations = sorted(dest_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Time series (packets per minute)
        minute_buckets: Dict[str, int] = defaultdict(int)
        byte_buckets: Dict[str, int] = defaultdict(int)
        for p in packets:
            bucket = p.timestamp.replace(second=0, microsecond=0)
            bucket_key = bucket.isoformat()
            minute_buckets[bucket_key] += 1
            byte_buckets[bucket_key] += p.size

        stats.packets_per_minute = [
            (datetime.fromisoformat(k), v)
            for k, v in sorted(minute_buckets.items())
        ]
        stats.bytes_per_minute = [
            (datetime.fromisoformat(k), v)
            for k, v in sorted(byte_buckets.items())
        ]

        return stats

    def get_node_stats(self, node_id: str,
                       since: Optional[datetime] = None) -> Dict[str, Any]:
        """Get statistics for a specific node."""
        packets = self._capture.get_packets(
            limit=5000,
            source=node_id,
            since=since,
        )

        sent = [p for p in packets if p.source == node_id]
        received = [p for p in packets if p.destination == node_id]

        snr_values = [p.snr for p in packets if p.snr is not None]
        rssi_values = [p.rssi for p in packets if p.rssi is not None]

        return {
            "node_id": node_id,
            "packets_sent": len(sent),
            "packets_received": len(received),
            "bytes_sent": sum(p.size for p in sent),
            "bytes_received": sum(p.size for p in received),
            "avg_snr": sum(snr_values) / len(snr_values) if snr_values else None,
            "avg_rssi": sum(rssi_values) / len(rssi_values) if rssi_values else None,
            "ports_used": list(set(p.port_name for p in sent if p.port_name)),
        }


# =============================================================================
# TRAFFIC LOGGER (HUMAN-READABLE LOG FILE)
# =============================================================================

class TrafficLogger:
    """
    Writes mesh traffic to a human-readable log file.

    Provides real-time visibility into mesh traffic similar to Wireshark's
    packet log, but in a format suitable for terminal viewing.
    """

    def __init__(self, log_path: Optional[str] = None):
        if log_path is None:
            log_dir = get_real_user_home() / ".cache" / "meshforge" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = str(log_dir / "traffic.log")

        self._log_path = log_path
        self._enabled = True
        self._lock = threading.Lock()
        self._packet_count = 0

        # Create/truncate log file with header
        self._write_header()

    def _write_header(self) -> None:
        """Write log file header."""
        try:
            with open(self._log_path, 'w') as f:
                f.write("=" * 100 + "\n")
                f.write(" MESHFORGE TRAFFIC LOG ".center(100, "=") + "\n")
                f.write("=" * 100 + "\n")
                f.write(f"Started: {datetime.now().isoformat()}\n")
                f.write(f"Log file: {self._log_path}\n")
                f.write("-" * 100 + "\n")
                f.write(f"{'Time':<12} {'Dir':<4} {'Proto':<10} {'Source':<14} "
                        f"{'Dest':<14} {'Port':<16} {'Hops':<5} {'SNR':<8} {'Size':<8}\n")
                f.write("-" * 100 + "\n")
        except IOError as e:
            logger.error(f"Failed to create traffic log: {e}")

    def log_packet(self, packet: 'MeshPacket') -> None:
        """Log a packet to the traffic log file."""
        if not self._enabled:
            return

        with self._lock:
            try:
                self._packet_count += 1

                # Format packet line
                time_str = packet.timestamp.strftime("%H:%M:%S.%f")[:12]
                dir_sym = {"inbound": "<-", "outbound": "->", "relayed": "<>", "internal": ".."}
                dir_str = dir_sym.get(packet.direction.value, "??")
                proto = packet.protocol.value[:10]
                src = packet.source[:14] if packet.source else "?"
                dst = packet.destination[:14] if packet.destination else "bcast"
                port = (packet.port_name[:16] if packet.port_name else "-")
                hops = str(packet.hops_taken) if packet.hops_taken else "-"
                snr = f"{packet.snr:.1f}" if packet.snr is not None else "-"
                size = str(packet.size) if packet.size else "-"

                line = f"{time_str:<12} {dir_str:<4} {proto:<10} {src:<14} {dst:<14} {port:<16} {hops:<5} {snr:<8} {size:<8}\n"

                # Check file size and rotate if needed
                self._maybe_rotate()

                with open(self._log_path, 'a') as f:
                    f.write(line)

            except IOError as e:
                logger.debug(f"Failed to write traffic log: {e}")

    def _maybe_rotate(self) -> None:
        """Rotate log file if it exceeds max size."""
        try:
            if Path(self._log_path).stat().st_size > TRAFFIC_LOG_MAX_SIZE:
                # Rotate backup files
                for i in range(TRAFFIC_LOG_BACKUP_COUNT - 1, 0, -1):
                    src = f"{self._log_path}.{i}"
                    dst = f"{self._log_path}.{i + 1}"
                    if Path(src).exists():
                        Path(src).rename(dst)

                # Move current to .1
                Path(self._log_path).rename(f"{self._log_path}.1")

                # Start fresh
                self._write_header()
        except (IOError, OSError):
            pass

    def get_log_path(self) -> str:
        """Get the path to the traffic log file."""
        return self._log_path

    def get_packet_count(self) -> int:
        """Get number of packets logged."""
        return self._packet_count

    def enable(self) -> None:
        """Enable traffic logging."""
        self._enabled = True

    def disable(self) -> None:
        """Disable traffic logging."""
        self._enabled = False

    def is_enabled(self) -> bool:
        """Check if logging is enabled."""
        return self._enabled

    def clear(self) -> None:
        """Clear the log file."""
        self._packet_count = 0
        self._write_header()


# =============================================================================
# TRAFFIC INSPECTOR (MAIN INTERFACE)
# =============================================================================

class TrafficInspector:
    """
    Main interface for Wireshark-grade traffic visibility.

    Combines capture, dissection, filtering, and analysis into
    a unified interface for mesh network traffic inspection.

    Usage:
        inspector = TrafficInspector()

        # Capture a packet (typically from meshtastic/RNS callbacks)
        packet = inspector.capture(data, metadata)

        # Get recent packets with filter
        filtered = inspector.get_packets(filter="mesh.hops > 2")

        # Get statistics
        stats = inspector.get_stats()

        # Trace message path
        trace = inspector.trace_path(packet_id)
    """

    def __init__(self, db_path: Optional[str] = None,
                 max_packets: int = 10000,
                 enable_logging: bool = True):
        self._capture = TrafficCapture(db_path, max_packets)
        self._analyzer = TrafficAnalyzer(self._capture)
        self._running = False

        # Traffic logging to human-readable file
        self._logger: Optional[TrafficLogger] = None
        if enable_logging:
            self._logger = TrafficLogger()
            # Register logger as callback for new packets
            self._capture.register_callback(self._log_packet)

    def _log_packet(self, packet: MeshPacket) -> None:
        """Internal callback to log packets."""
        if self._logger:
            self._logger.log_packet(packet)

    def capture(self, data: bytes, metadata: Dict[str, Any]) -> Optional[MeshPacket]:
        """Capture and dissect a packet."""
        return self._capture.capture_packet(data, metadata)

    def get_packets(self, limit: int = 100, offset: int = 0,
                    filter: Optional[str] = None,
                    since: Optional[datetime] = None,
                    until: Optional[datetime] = None,
                    protocol: Optional[str] = None) -> List[MeshPacket]:
        """Get captured packets with optional filtering."""
        proto = PacketProtocol(protocol) if protocol else None
        return self._capture.get_packets(
            limit=limit,
            offset=offset,
            filter_expr=filter,
            since=since,
            until=until,
            protocol=proto,
        )

    def get_packet(self, packet_id: str) -> Optional[MeshPacket]:
        """Get a specific packet by ID."""
        packets = self._capture.get_packets(limit=1000)
        for p in packets:
            if p.id == packet_id:
                return p
        return None

    def trace_path(self, packet_id: str) -> List[HopInfo]:
        """Get path trace for a packet."""
        return self._capture.get_path_trace(packet_id)

    def get_stats(self, since: Optional[datetime] = None) -> TrafficStats:
        """Get traffic statistics."""
        return self._analyzer.get_stats(since=since)

    def get_node_stats(self, node_id: str) -> Dict[str, Any]:
        """Get statistics for a specific node."""
        return self._analyzer.get_node_stats(node_id)

    def get_capture_stats(self) -> Dict[str, Any]:
        """Get capture session statistics."""
        return self._capture.get_stats()

    def register_callback(self, callback: Callable[[MeshPacket], None]) -> None:
        """Register callback for new packets."""
        self._capture.register_callback(callback)

    def clear(self) -> int:
        """Clear all captured packets."""
        if self._logger:
            self._logger.clear()
        return self._capture.clear_all()

    def get_log_path(self) -> Optional[str]:
        """Get the path to the traffic log file."""
        if self._logger:
            return self._logger.get_log_path()
        return None

    def enable_logging(self) -> None:
        """Enable traffic logging."""
        if self._logger is None:
            self._logger = TrafficLogger()
            self._capture.register_callback(self._log_packet)
        else:
            self._logger.enable()

    def disable_logging(self) -> None:
        """Disable traffic logging."""
        if self._logger:
            self._logger.disable()

    def is_logging_enabled(self) -> bool:
        """Check if traffic logging is enabled."""
        return self._logger is not None and self._logger.is_enabled()

    @staticmethod
    def get_filter_fields() -> Dict[str, str]:
        """Get available filter fields and descriptions."""
        return DisplayFilter.get_available_fields()

    def format_packet_list(self, packets: List[MeshPacket],
                           max_width: int = 120) -> str:
        """Format packets as ASCII list for TUI display."""
        lines = []
        lines.append("=" * max_width)
        lines.append(" TRAFFIC CAPTURE ".center(max_width, "="))
        lines.append("=" * max_width)
        lines.append("")

        # Header
        lines.append(f"{'Time':<12} {'Dir':<4} {'Source':<14} {'Dest':<14} {'Port':<16} {'Hops':<5} {'SNR':<8}")
        lines.append("-" * max_width)

        for pkt in packets[:50]:  # Limit display
            time_str = pkt.timestamp.strftime("%H:%M:%S.%f")[:12]
            dir_sym = {"inbound": "<-", "outbound": "->", "relayed": "<>", "internal": ".."}
            dir_str = dir_sym.get(pkt.direction.value, "??")
            src = (pkt.source[:12] + "..") if len(pkt.source) > 14 else pkt.source[:14]
            dst = (pkt.destination[:12] + "..") if len(pkt.destination) > 14 else pkt.destination[:14]
            port = pkt.port_name[:16] if pkt.port_name else "-"
            hops = str(pkt.hops_taken) if pkt.hops_taken else "-"
            snr = f"{pkt.snr:.1f}" if pkt.snr is not None else "-"

            lines.append(f"{time_str:<12} {dir_str:<4} {src:<14} {dst:<14} {port:<16} {hops:<5} {snr:<8}")

        lines.append("")
        lines.append(f"Showing {min(50, len(packets))} of {len(packets)} packets")
        lines.append("=" * max_width)

        return "\n".join(lines)

    def format_packet_detail(self, packet: MeshPacket) -> str:
        """Format single packet detail for TUI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(" PACKET DETAIL ".center(70, "="))
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"ID: {packet.id}")
        lines.append(f"Time: {packet.timestamp.isoformat()}")
        lines.append(f"Direction: {packet.direction.value}")
        lines.append(f"Protocol: {packet.protocol.value}")
        lines.append("")

        # Protocol tree
        if packet.tree:
            lines.append("Protocol Tree:")
            lines.append("-" * 70)
            lines.append(packet.tree.format_ascii())

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)

    def format_stats(self, stats: TrafficStats) -> str:
        """Format statistics for TUI display."""
        lines = []
        lines.append("=" * 70)
        lines.append(" TRAFFIC STATISTICS ".center(70, "="))
        lines.append("=" * 70)
        lines.append("")

        lines.append(f"Total Packets: {stats.total_packets}")
        lines.append(f"Total Bytes:   {stats.total_bytes:,}")
        lines.append("")

        lines.append("By Protocol:")
        for proto, count in stats.packets_by_protocol.items():
            lines.append(f"  {proto}: {count}")
        lines.append("")

        lines.append("By Direction:")
        for direction, count in stats.packets_by_direction.items():
            lines.append(f"  {direction}: {count}")
        lines.append("")

        if stats.hops_distribution:
            lines.append(f"Hop Statistics:")
            lines.append(f"  Average: {stats.avg_hops:.2f}")
            lines.append(f"  Maximum: {stats.max_hops}")
            lines.append(f"  Distribution: {dict(stats.hops_distribution)}")
            lines.append("")

        if stats.avg_snr is not None:
            lines.append(f"Signal Quality:")
            lines.append(f"  Avg SNR:  {stats.avg_snr:.1f} dB")
            if stats.avg_rssi is not None:
                lines.append(f"  Avg RSSI: {stats.avg_rssi:.0f} dBm")
            lines.append("")

        if stats.top_sources:
            lines.append("Top Sources:")
            for node, count in stats.top_sources[:5]:
                lines.append(f"  {node[:20]}: {count}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# =============================================================================
# Global Inspector Instance & Auto-Connect
# =============================================================================

_global_inspector: Optional[TrafficInspector] = None
_capture_subscribed: bool = False


def get_traffic_inspector() -> TrafficInspector:
    """Get or create the global traffic inspector instance."""
    global _global_inspector
    if _global_inspector is None:
        _global_inspector = TrafficInspector()
    return _global_inspector


def start_packet_capture() -> bool:
    """
    Start capturing packets from meshtasticd via pubsub.

    Subscribes to meshtastic.receive to capture all incoming packets.
    Returns True if capture started, False if already running or failed.
    """
    global _capture_subscribed

    if _capture_subscribed:
        return False

    try:
        from pubsub import pub

        inspector = get_traffic_inspector()

        def on_meshtastic_packet(packet, interface=None):
            """Callback for meshtastic packets."""
            try:
                # Extract packet data
                raw_data = packet.get('raw', b'') if isinstance(packet, dict) else b''
                if isinstance(raw_data, str):
                    raw_data = raw_data.encode('utf-8', errors='replace')

                metadata = {
                    'protocol': 'meshtastic',
                    'timestamp': datetime.now().isoformat(),
                    'direction': 'incoming',
                }

                # Extract fields from packet
                if isinstance(packet, dict):
                    if 'from' in packet:
                        metadata['source'] = f"!{packet['from']:08x}"
                    if 'to' in packet:
                        metadata['destination'] = f"!{packet['to']:08x}"
                    if 'hopLimit' in packet:
                        metadata['hop_limit'] = packet['hopLimit']
                    if 'hopStart' in packet:
                        metadata['hop_start'] = packet['hopStart']
                    if 'rxSnr' in packet:
                        metadata['snr'] = packet['rxSnr']
                    if 'rxRssi' in packet:
                        metadata['rssi'] = packet['rxRssi']
                    if 'decoded' in packet:
                        decoded = packet['decoded']
                        if isinstance(decoded, dict):
                            metadata['portnum'] = decoded.get('portnum', 'UNKNOWN')

                inspector.capture(raw_data, metadata)

            except Exception as e:
                logger.debug(f"Error capturing meshtastic packet: {e}")

        pub.subscribe(on_meshtastic_packet, "meshtastic.receive")
        _capture_subscribed = True
        logger.info("Traffic capture started - subscribed to meshtastic.receive")
        return True

    except ImportError:
        logger.warning("pubsub not available - cannot start packet capture")
        return False
    except Exception as e:
        logger.error(f"Failed to start packet capture: {e}")
        return False


def stop_packet_capture() -> bool:
    """Stop capturing packets."""
    global _capture_subscribed

    if not _capture_subscribed:
        return False

    try:
        from pubsub import pub
        pub.unsubscribe(on_meshtastic_packet, "meshtastic.receive")
        _capture_subscribed = False
        logger.info("Traffic capture stopped")
        return True
    except Exception as e:
        logger.debug(f"Error stopping capture: {e}")
        _capture_subscribed = False
        return False


def is_capture_running() -> bool:
    """Check if packet capture is running."""
    return _capture_subscribed
