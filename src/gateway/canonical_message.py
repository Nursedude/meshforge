"""
Canonical Message Format for Multi-Protocol Bridging.

Protocol-agnostic intermediate message representation that enables
N-protocol bridging with 2*N conversions instead of N*(N-1).

Each protocol handler converts its native format to/from CanonicalMessage.
The bridge loop and routing engine operate exclusively on CanonicalMessage.

BridgedMessage (existing) is preserved for backward compatibility —
CanonicalMessage can convert to/from BridgedMessage losslessly.

Supported protocols:
- Meshtastic (via meshtasticd TCP/MQTT)
- MeshCore (via meshcore_py companion radio)
- RNS/LXMF (via rnsd)
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from .bridge_health import MessageOrigin

logger = logging.getLogger(__name__)

# Protocol payload size limits (bytes)
MESHTASTIC_MAX_PAYLOAD = 237
MESHCORE_MAX_PAYLOAD = 184
MESHCORE_MAX_TEXT = 160  # Text message limit (payload minus headers)
TRUNCATION_INDICATOR = "\u2026"  # Unicode ellipsis


class MessageType(Enum):
    """Canonical message type classification."""
    TEXT = "text"              # Human-readable text message
    TELEMETRY = "telemetry"   # Sensor/device telemetry data
    POSITION = "position"     # GPS/location update
    COMMAND = "command"       # System/control command
    ACK = "ack"               # Delivery acknowledgment
    TRACEROUTE = "traceroute" # Path trace result
    NODEINFO = "nodeinfo"     # Node identity/capability info
    TACTICAL = "tactical"     # Structured tactical message (X1 format)
    UNKNOWN = "unknown"       # Unclassified


class Protocol(Enum):
    """Supported mesh network protocols."""
    MESHTASTIC = "meshtastic"
    MESHCORE = "meshcore"
    RNS = "rns"


@dataclass
class CanonicalMessage:
    """
    Protocol-agnostic message representation.

    All protocol handlers convert their native formats to CanonicalMessage
    for routing, then convert back to destination-native format for delivery.

    This eliminates N*(N-1) conversion paths in favor of 2*N:
    - N from_X() methods (one per protocol)
    - N to_X() methods (one per protocol)
    """

    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Source
    source_network: str = ""         # "meshtastic" | "meshcore" | "rns"
    source_address: str = ""         # Network-specific node address

    # Destination
    destination_address: Optional[str] = None  # None = broadcast
    destination_network: Optional[str] = None  # Target network (set by router)

    # Content
    content: str = ""                # Text content (decoded)
    payload: Optional[bytes] = None  # Raw binary payload (if applicable)
    message_type: MessageType = MessageType.TEXT

    # Routing
    is_broadcast: bool = False
    hop_limit: int = 3
    hop_count: int = 0               # Hops traversed so far
    via_internet: bool = False        # True if arrived via MQTT/internet
    origin: MessageOrigin = MessageOrigin.UNKNOWN

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)

    # Protocol-specific extras (preserved for round-trip fidelity)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- Factory Methods ---

    @classmethod
    def from_meshtastic(cls, packet: dict) -> 'CanonicalMessage':
        """
        Create CanonicalMessage from a Meshtastic packet dict.

        Args:
            packet: Meshtastic packet as decoded by meshtastic Python lib
                    or MQTT JSON. Expected keys: 'from', 'to', 'decoded',
                    'rxSnr', 'rxRssi', 'hopLimit', 'hopStart', etc.
        """
        decoded = packet.get('decoded', {})
        portnum = decoded.get('portnum', 'TEXT_MESSAGE_APP')

        # Determine message type from portnum
        msg_type = _portnum_to_message_type(portnum)

        # Extract text content
        text = decoded.get('text', '')

        # Detect X1 tactical messages (override portnum-based type)
        if msg_type == MessageType.TEXT and _detect_tactical_x1(text):
            msg_type = MessageType.TACTICAL
        if not text:
            raw_payload = decoded.get('payload', b'')
            if isinstance(raw_payload, bytes):
                text = raw_payload.decode('utf-8', errors='replace')
            elif raw_payload:
                text = str(raw_payload)

        # Source/destination addresses
        from_id = packet.get('fromId', '') or f"!{packet.get('from', 0):08x}"
        to_id = packet.get('toId', '')
        if not to_id and packet.get('to'):
            to_raw = packet['to']
            # Meshtastic broadcast address is 0xFFFFFFFF
            if to_raw == 0xFFFFFFFF or to_raw == 4294967295:
                to_id = None
            else:
                to_id = f"!{to_raw:08x}"

        # Determine if broadcast
        is_broadcast = to_id is None or to_id == '!ffffffff'
        if is_broadcast:
            to_id = None

        # Detect internet origin (MQTT)
        via_internet = packet.get('viaMqtt', False)
        origin = MessageOrigin.MQTT if via_internet else MessageOrigin.RADIO

        return cls(
            source_network=Protocol.MESHTASTIC.value,
            source_address=from_id,
            destination_address=to_id,
            content=text if msg_type == MessageType.TEXT else '',
            payload=decoded.get('payload') if isinstance(
                decoded.get('payload'), bytes
            ) else None,
            message_type=msg_type,
            is_broadcast=is_broadcast,
            hop_limit=packet.get('hopLimit', 3),
            hop_count=packet.get('hopStart', 3) - packet.get('hopLimit', 3),
            via_internet=via_internet,
            origin=origin,
            metadata={
                'portnum': portnum,
                'rxSnr': packet.get('rxSnr'),
                'rxRssi': packet.get('rxRssi'),
                'channel': packet.get('channel', 0),
                'packet_id': packet.get('id'),
                'raw_packet': packet,
            },
        )

    @classmethod
    def from_meshcore(cls, event: Any) -> 'CanonicalMessage':
        """
        Create CanonicalMessage from a meshcore_py event.

        Args:
            event: Event object from meshcore_py subscription.
                   For CONTACT_MSG_RECV: event.payload has .text, .contact
                   For CHANNEL_MSG_RECV: event.payload has .text, .channel
                   For ADVERTISEMENT: event.payload has node info
        """
        payload = getattr(event, 'payload', None) or {}

        # Handle both object attributes and dict access
        if isinstance(payload, dict):
            text = payload.get('text', '')
            sender = payload.get('sender', '') or payload.get('pubkey_prefix', '')
            destination = payload.get('destination', None)
            is_channel = payload.get('is_channel', False)
            channel = payload.get('channel', 0)
        else:
            text = getattr(payload, 'text', '') or ''
            contact = getattr(payload, 'contact', None)
            sender = getattr(contact, 'adv_name', '') if contact else ''
            sender_key = getattr(contact, 'public_key', b'') if contact else b''
            if sender_key and isinstance(sender_key, bytes):
                sender = sender_key.hex()[:12]
            elif not sender:
                sender = getattr(payload, 'sender', '') or ''
            destination = getattr(payload, 'destination', None)
            is_channel = getattr(payload, 'is_channel', False)
            channel = getattr(payload, 'channel', 0)

        # Determine event type
        event_type = getattr(event, 'type', None) or getattr(event, 'event_type', None)
        event_type_str = str(event_type) if event_type else ''

        if 'ADVERTISEMENT' in event_type_str.upper():
            msg_type = MessageType.NODEINFO
        elif 'ACK' in event_type_str.upper():
            msg_type = MessageType.ACK
        else:
            msg_type = MessageType.TEXT

        is_broadcast = is_channel or destination is None

        return cls(
            source_network=Protocol.MESHCORE.value,
            source_address=str(sender),
            destination_address=str(destination) if destination and not is_broadcast else None,
            content=text,
            message_type=msg_type,
            is_broadcast=is_broadcast,
            hop_limit=64,  # MeshCore supports up to 64 hops
            via_internet=False,  # MeshCore is pure radio
            origin=MessageOrigin.RADIO,
            metadata={
                'event_type': event_type_str,
                'channel': channel,
                'raw_event': event,
            },
        )

    @classmethod
    def from_rns(cls, lxmf_delivery: Any) -> 'CanonicalMessage':
        """
        Create CanonicalMessage from an LXMF delivery.

        Args:
            lxmf_delivery: LXMF message object with .content, .source_hash,
                          .destination_hash, .title, .fields, etc.
        """
        # Extract content
        content_bytes = getattr(lxmf_delivery, 'content', b'')
        if isinstance(content_bytes, bytes):
            content = content_bytes.decode('utf-8', errors='replace')
        else:
            content = str(content_bytes) if content_bytes else ''

        # Source/destination hashes
        source_hash = getattr(lxmf_delivery, 'source_hash', b'')
        dest_hash = getattr(lxmf_delivery, 'destination_hash', b'')

        source_addr = source_hash.hex() if isinstance(source_hash, bytes) else str(source_hash)
        dest_addr = dest_hash.hex() if isinstance(dest_hash, bytes) and dest_hash else None

        title = getattr(lxmf_delivery, 'title', None)
        if isinstance(title, bytes):
            title = title.decode('utf-8', errors='replace')

        # Detect X1 tactical messages
        msg_type = MessageType.TACTICAL if _detect_tactical_x1(content) else MessageType.TEXT

        return cls(
            source_network=Protocol.RNS.value,
            source_address=source_addr,
            destination_address=dest_addr,
            content=content,
            message_type=msg_type,
            is_broadcast=dest_addr is None,
            via_internet=False,
            origin=MessageOrigin.RADIO,
            metadata={
                'title': title,
                'fields': getattr(lxmf_delivery, 'fields', {}),
                'raw_lxmf': lxmf_delivery,
            },
        )

    @classmethod
    def from_bridged_message(cls, msg: Any) -> 'CanonicalMessage':
        """
        Create CanonicalMessage from existing BridgedMessage (backward compat).

        Args:
            msg: BridgedMessage dataclass from rns_bridge.py
        """
        return cls(
            source_network=msg.source_network,
            source_address=msg.source_id,
            destination_address=msg.destination_id,
            content=msg.content,
            message_type=MessageType.TEXT,
            is_broadcast=msg.is_broadcast,
            via_internet=msg.via_internet,
            origin=msg.origin,
            timestamp=msg.timestamp or datetime.now(),
            metadata=dict(msg.metadata) if msg.metadata else {},
        )

    # --- Serialization Methods ---

    def to_meshtastic_text(self) -> str:
        """
        Convert to text suitable for Meshtastic transmission.

        Returns text content, truncated to Meshtastic payload limit if needed.
        """
        text = self.content
        if len(text.encode('utf-8')) > MESHTASTIC_MAX_PAYLOAD:
            text = _truncate_utf8(text, MESHTASTIC_MAX_PAYLOAD)
        return text

    def to_meshcore_text(self) -> str:
        """
        Convert to text suitable for MeshCore transmission.

        MeshCore text messages are limited to ~160 bytes. Truncates with
        ellipsis indicator if content exceeds limit.
        """
        text = self.content
        if len(text.encode('utf-8')) > MESHCORE_MAX_TEXT:
            text = _truncate_utf8(text, MESHCORE_MAX_TEXT)
        return text

    def to_bridged_message(self) -> Any:
        """
        Convert back to BridgedMessage for backward compatibility.

        Allows CanonicalMessage to integrate with existing code that
        expects BridgedMessage without requiring changes.
        """
        # Import here to avoid circular dependency
        from .rns_bridge import BridgedMessage

        return BridgedMessage(
            source_network=self.source_network,
            source_id=self.source_address,
            destination_id=self.destination_address,
            content=self.content,
            title=self.metadata.get('title'),
            timestamp=self.timestamp,
            is_broadcast=self.is_broadcast,
            metadata=dict(self.metadata),
            origin=self.origin,
            via_internet=self.via_internet,
        )

    def should_bridge(self, filter_mqtt: bool = False,
                      filter_internet_to_meshcore: bool = True) -> bool:
        """
        Check if this message should be bridged.

        Args:
            filter_mqtt: If True, drop MQTT-originated messages entirely.
            filter_internet_to_meshcore: If True, drop internet-originated
                messages destined for MeshCore (pure radio network).

        Returns:
            True if message should be bridged.
        """
        if filter_mqtt and self.via_internet:
            return False
        if filter_mqtt and self.origin == MessageOrigin.MQTT:
            return False
        # MeshCore is pure radio — never bridge internet traffic to it
        if (filter_internet_to_meshcore
                and self.via_internet
                and self.destination_network == Protocol.MESHCORE.value):
            return False
        return True

    def get_destinations(self) -> List[str]:
        """
        Get list of destination networks this message should be routed to.

        For broadcast messages, returns all networks except the source.
        For directed messages, returns the single destination network.
        """
        all_networks = [p.value for p in Protocol]

        if self.destination_network:
            return [self.destination_network]

        if self.is_broadcast:
            return [n for n in all_networks if n != self.source_network]

        return []

    def __str__(self) -> str:
        direction = "broadcast" if self.is_broadcast else f"→{self.destination_address}"
        preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return (
            f"[{self.source_network}:{self.source_address}] "
            f"{direction} ({self.message_type.value}): {preview}"
        )


# --- Helper Functions ---

def _detect_tactical_x1(text: str) -> bool:
    """Check if message text is an X1 tactical message."""
    return bool(text and text.startswith('X1.'))


def _portnum_to_message_type(portnum: str) -> MessageType:
    """Map Meshtastic portnum to canonical MessageType."""
    mapping = {
        'TEXT_MESSAGE_APP': MessageType.TEXT,
        'TELEMETRY_APP': MessageType.TELEMETRY,
        'POSITION_APP': MessageType.POSITION,
        'NODEINFO_APP': MessageType.NODEINFO,
        'TRACEROUTE_APP': MessageType.TRACEROUTE,
        'ROUTING_APP': MessageType.ACK,
        'ADMIN_APP': MessageType.COMMAND,
    }
    # Handle both string and int portnums
    if isinstance(portnum, int):
        int_mapping = {
            1: MessageType.TEXT,
            67: MessageType.TELEMETRY,
            3: MessageType.POSITION,
            4: MessageType.NODEINFO,
            69: MessageType.TRACEROUTE,
            70: MessageType.ACK,
        }
        return int_mapping.get(portnum, MessageType.UNKNOWN)
    return mapping.get(str(portnum), MessageType.UNKNOWN)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """
    Truncate text to fit within max_bytes when UTF-8 encoded.

    Ensures clean truncation at character boundaries (no broken
    multi-byte sequences) and appends ellipsis indicator.
    """
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text

    # Reserve space for ellipsis (3 bytes for Unicode ellipsis)
    target = max_bytes - len(TRUNCATION_INDICATOR.encode('utf-8'))

    # Truncate at UTF-8 character boundary
    truncated = encoded[:target].decode('utf-8', errors='ignore')
    return truncated + TRUNCATION_INDICATOR
