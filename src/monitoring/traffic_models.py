"""
Traffic Models - Data classes and enums for traffic inspection.

Contains:
- PacketDirection, PacketProtocol, FieldType, HopState enums
- MESHTASTIC_PORTS constant mapping
- PacketField: Protocol tree field representation
- PacketTree: Hierarchical packet display structure
- MeshPacket: Unified packet representation
- HopInfo: Path trace hop information
"""

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # For forward references if needed


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

    # Relay tracking (Meshtastic 2.6+)
    relay_node: Optional[int] = None  # Last byte of relay node ID
    next_hop: Optional[int] = None    # Last byte of next-hop node ID

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
