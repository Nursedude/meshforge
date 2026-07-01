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

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .bridge_health import MessageOrigin

logger = logging.getLogger(__name__)

# Protocol payload size limits (bytes)
MESHTASTIC_MAX_PAYLOAD = 237
MESHCORE_MAX_PAYLOAD = 184
MESHCORE_MAX_TEXT = 160  # Text message limit (payload minus headers)
TRUNCATION_INDICATOR = "\u2026"  # Unicode ellipsis


# \u2500\u2500 Logical content-identity (dedup/identity arc, STEP 1) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# ONE deterministic id per logical message, computed identically on every box,
# so independent active-active gateways (and the MeshForge\u2194MeshAnchor pair)
# recognize the SAME logical message. Unifies the two existing content hashes:
# base_handler.mqtt_content_dedup_key (#34) and RecentRfTxRegistry._key.
# canonical_message.py is byte-identical across both repos (parity_check
# BYTE_IDENTICAL tier), so a content_id minted here is byte-identical fleetwide.
#
# Leading bridge-routing tags are stripped before hashing so the SAME logical
# message hashes identically whether it arrives raw ("hello") or tagged by a
# gateway re-injection ("[RNS:xx] hello" / "[MC:yy] hello"). This list MUST stay
# equal to base_handler.BRIDGE_TAG_PREFIXES (MeshForge) and
# config.nested_drop_prefixes (MeshAnchor) \u2014 pinned by
# TestComputeContentId.test_bridge_tag_list_pinned_to_base_handler.
_CONTENT_ID_BRIDGE_TAGS = (
    "[MeshCore]", "[MC:", "[RNS:", "[ch0:", "[ch1:", "[Mesh:",
)

# Scheme version prefix on every content_id: self-describing + version-evolvable
# so a future algorithm change can never silently collide with an old id.
CONTENT_ID_SCHEME = "c1"

# ASCII unit separator between key fields (matches mqtt_content_dedup_key).
_CONTENT_ID_SEP = "\x1f"


def _strip_leading_bridge_tags(text: str) -> str:
    """Iteratively remove LEADING bridge tags ([RNS:..]/[MC:..]/[Mesh:..]/...).

    Mirrors base_handler._strip_bridge_tags so the content-identity hash and
    the RecentRfTxRegistry seen-on-RF key normalize content the same way.
    Stops at the first non-tag text or a malformed (unclosed) tag.
    """
    out = (text or "").lstrip()
    while out.startswith(_CONTENT_ID_BRIDGE_TAGS):
        close = out.find("]")
        if close < 0:
            break
        out = out[close + 1:].lstrip()
    return out


def normalize_content_for_id(content: str) -> str:
    """Canonical content normalization for the logical-message identity.

    Strips leading bridge tags then collapses every run of whitespace to a
    single space (identical to RecentRfTxRegistry._key), so
    '[RNS:xx] hello  world' == '[MC:yy] hello world' == 'hello world'.
    Returns '' for empty / tag-only / whitespace-only content.
    """
    return " ".join(_strip_leading_bridge_tags(content).split())


def compute_content_id(origin_token: str, content: str,
                       channel_name: str = "") -> str:
    """Deterministic logical-message content identity (dedup/identity arc).

    Keys on three fleet-stable axes, with NO wall-clock component:
      - origin_token: the canonical (protocol,address) origin, e.g.
        'meshtastic:!a2e95ba4' / 'rns:<hash>' / 'meshcore:<sender>' (see
        format_reply_token). The sender axis \u2014 so identical text from two
        different originators does NOT collide (the RecentRfTxRegistry gap).
        Caller-supplied; may be '' (yields a thinner content+channel identity).
      - content: tag-stripped + whitespace-normalized (normalize_content_for_id).
      - channel_name: the channel NAME, lowercased \u2014 NOT the box-local numeric
        slot index, which differs per box (#77). The name is fleet-stable.

    Deliberately EXCLUDES any timestamp: the fleet is RTC-less Pis with NTP
    steps (honest_failure_modes #6); origin+content+channel is collision-safe
    except for genuine within-window repeats, which the dedup LAYER handles
    with a short suppress window \u2014 never the identity itself.

    Returns '' when content normalizes to empty (tag-only / no text): there is
    no stable text identity to mint, and the caller MUST treat '' as
    "unidentifiable", never collapse every empty onto one shared hash
    (honest_failure_modes #1).

    Suppression use is deliberately NARROW (contract revised 2026-07-01): the
    id is minted, carried, and counted everywhere; it is used to SUPPRESS a
    copy ONLY inside an explicitly-gated INTRA-box dual-path dedup window
    whose register side runs on confirmed delivery (MeshForge transport-truth
    Phase 4). Cross-box observation (/fleet/dups) stays measure-only. Any new
    suppression consumer must preserve the failure direction: a missed match
    is a cosmetic dup, but a false or unearned hit (registration without real
    delivery) is message LOSS \u2014 suppress-only-on-hit, empty id never matches,
    and cid-only suppressions leave a witness stat.

    Result form: '<CONTENT_ID_SCHEME>:<sha256-hex>' (e.g. 'c1:ab34\u2026').
    """
    norm = normalize_content_for_id(content)
    if not norm:
        return ""
    parts = (
        (origin_token or "").strip(),
        norm,
        (channel_name or "").strip().lower(),
    )
    digest = hashlib.sha256(
        _CONTENT_ID_SEP.join(parts).encode("utf-8")).hexdigest()
    return f"{CONTENT_ID_SCHEME}:{digest}"


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

    # Logical content identity (dedup/identity arc) — ONE stable id per logical
    # message, stamped at first ingress (compute_content_id) and carried across
    # every transport leg. '' when not yet minted / no text to mint from.
    # Measure-only: carried + counted, never used to suppress a copy.
    content_id: str = ""

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

    # --- Issue #66: application-layer sync/ack semantics ---
    # Sender's request: "I want to know this got there." When True, the
    # receiving gateway is responsible for synthesizing an ACK
    # CanonicalMessage back toward the sender once it observes proof-of-
    # delivery from the destination protocol. Defaults False so existing
    # callers keep their fire-and-forget semantics.
    ack_required: bool = False

    # When this message IS an ack, the id of the message it acknowledges.
    # None for non-ack messages. Paired with message_type == ACK; receivers
    # correlate inbound acks to their pending-ack table by this field.
    ack_of: Optional[str] = None

    # When the sender wants a *reply* (not just an ack) routed to a
    # different protocol/address than its origin. Optional — most messages
    # leave this None. Useful for cross-protocol bridges where the origin
    # address isn't directly addressable by the destination protocol.
    reply_to: Optional[str] = None

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

        # content_id (dedup/identity arc, STEP 2b — measure-only): mint on the
        # MeshCore ingress leg (dup-birth B) keyed on meshcore:<sender>, content,
        # channel. '' for advertisements / empty text. NOTE: MeshCore channel
        # broadcasts can arrive with an empty sender (baked into the text
        # header), giving a thinner content+channel identity — still deterministic.
        content_id = compute_content_id(
            f"meshcore:{sender}", str(text), str(channel))

        return cls(
            source_network=Protocol.MESHCORE.value,
            source_address=str(sender),
            destination_address=str(destination) if destination and not is_broadcast else None,
            content=text,
            content_id=content_id,
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
        meta = dict(msg.metadata) if msg.metadata else {}
        # Issue #66: BridgedMessage doesn't carry ack fields natively;
        # to_bridged_message() stashes them under meshforge_ack_* keys so
        # the Canonical → Bridged → Canonical round-trip stays lossless.
        ack_required = bool(meta.pop('meshforge_ack_required', False))
        ack_of = meta.pop('meshforge_ack_of', None)
        reply_to = meta.pop('meshforge_reply_to', None)
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
            metadata=meta,
            ack_required=ack_required,
            ack_of=ack_of,
            reply_to=reply_to,
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

        merged_meta = dict(self.metadata)
        # Issue #66: BridgedMessage has no native ack fields; stash under
        # the meshforge_ack_* namespace so from_bridged_message() restores
        # them. Only emit keys with non-default values to keep metadata
        # tidy for existing consumers.
        if self.ack_required:
            merged_meta['meshforge_ack_required'] = True
        if self.ack_of is not None:
            merged_meta['meshforge_ack_of'] = self.ack_of
        if self.reply_to is not None:
            merged_meta['meshforge_reply_to'] = self.reply_to

        return BridgedMessage(
            source_network=self.source_network,
            source_id=self.source_address,
            destination_id=self.destination_address,
            content=self.content,
            title=self.metadata.get('title'),
            timestamp=self.timestamp,
            is_broadcast=self.is_broadcast,
            metadata=merged_meta,
            origin=self.origin,
            via_internet=self.via_internet,
            # Carry the logical content_id across the Canonical→Bridged
            # conversion so the identity survives (dedup/identity arc STEP 3).
            content_id=self.content_id,
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

def format_reply_token(protocol: str, address: str) -> str:
    """Format a protocol-qualified reply address token.

    The canonical reply_to wire shape: ``"{protocol}:{address}"``, e.g.
    ``meshtastic:!abcd1234``, ``rns:<32-hex>``, ``meshcore:<12-hex>``.
    Pure string concatenation — per-protocol address validity is the
    consuming bridge's job (it must re-resolve before delivery anyway).
    """
    return f"{protocol}:{address}"


def parse_reply_token(token: Any) -> Tuple[Optional[str], Optional[str]]:
    """Split a reply token into ``(protocol, address)``.

    Returns ``(None, None)`` for anything malformed: non-str input, missing
    separator, or an empty protocol/address part. The address may itself
    contain colons (``split(':', 1)`` keeps them intact). Callers must
    treat the parts as untrusted and re-validate per protocol.
    """
    if not isinstance(token, str) or ':' not in token:
        return (None, None)
    protocol, _, address = token.partition(':')
    if not protocol or not address:
        return (None, None)
    return (protocol, address)


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
