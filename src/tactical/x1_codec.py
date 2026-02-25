"""
X1 Wire Format Codec for MeshForge Tactical Messaging.

Encodes/decodes tactical messages using the X1 compact packet protocol:

    X1.<T>.<M>.<ID>.<P>/<N>.<PAYLOAD>

    T       = Template ID (1-8, maps to TacticalType enum)
    M       = Mode: C (CLEAR) or S (SECURE)
    ID      = Crockford Base32 message ID (5 chars, dedup/reassembly)
    P/N     = Part number / total parts (1/1 for single-chunk)
    PAYLOAD = base64url-encoded binary (msgpack or JSON serialized)

X1 is MeshForge's native wire format for tactical messages, chosen for
interoperability with the XTOC/XCOM ecosystem.

Usage:
    from tactical.x1_codec import encode, decode, is_x1

    # Encode a tactical message
    x1_string = encode(msg)  # "X1.4.C.K7V3N.1/1.eyJjYWxsc2lnbi..."

    # Decode from wire
    msg = decode(x1_string)

    # Check if text is X1
    if is_x1(text):
        msg = decode(text)
"""

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from utils.safe_import import safe_import

from tactical.models import (
    EncryptionMode,
    TacticalMessage,
    TacticalPriority,
    TacticalType,
    generate_message_id,
)

logger = logging.getLogger(__name__)

# Optional msgpack for compact binary encoding
_msgpack, _HAS_MSGPACK = safe_import('msgpack')

# X1 protocol prefix
X1_PREFIX = "X1"

# Regex to validate and parse X1 wire format
# X1.<T>.<M>.<ID>.<P>/<N>.<PAYLOAD>
_X1_PATTERN = re.compile(
    r'^X1\.(\d+)\.([CS])\.([0-9A-Za-z]{3,8})\.(\d+)/(\d+)\.(.+)$'
)

# Compact field mapping for reduced payload size
# Maps verbose field names to single-char keys for wire encoding
_FIELD_COMPACT = {
    'situation': 's',
    'actions_taken': 'a',
    'resources_needed': 'rn',
    'casualties': 'c',
    'latitude': 'la',
    'longitude': 'lo',
    'altitude': 'al',
    'description': 'd',
    'assignee': 'as',
    'status': 'st',
    'due': 'du',
    'name': 'n',
    'resource_type': 'rt',
    'quantity': 'q',
    'location_name': 'ln',
    'callsign': 'cs',
    'personnel_count': 'pc',
    'notes': 'no',
    'zone_type': 'zt',
    'center_lat': 'cla',
    'center_lon': 'clo',
    'radius_m': 'rm',
    'vertices': 'v',
    'objective': 'ob',
    'commander': 'cm',
    'start_time': 'st0',
    'end_time': 'et',
    'event_type': 'evt',
    'reported_by': 'rb',
    'asset_type': 'at',
    'identifier': 'id',
    'assigned_to': 'ato',
}

# Reverse mapping for decoding
_FIELD_EXPAND = {v: k for k, v in _FIELD_COMPACT.items()}


def is_x1(text: str) -> bool:
    """Check if text is an X1-formatted tactical message.

    Args:
        text: Message text to check.

    Returns:
        True if text starts with 'X1.' and matches the X1 pattern.
    """
    if not text or not text.startswith('X1.'):
        return False
    return _X1_PATTERN.match(text) is not None


def encode(msg: TacticalMessage, part: int = 1, total: int = 1) -> str:
    """Encode a TacticalMessage to X1 wire format string.

    Args:
        msg: TacticalMessage to encode.
        part: Chunk part number (1-indexed).
        total: Total number of chunks.

    Returns:
        X1 wire format string.
    """
    # Template ID
    template_id = msg.tactical_type.value

    # Encryption mode
    mode = msg.encryption_mode.value

    # Message ID (ensure it exists)
    msg_id = msg.id or generate_message_id()

    # Compact the content fields
    compact_content = _compact_fields(msg.content)

    # Add metadata to payload
    payload_dict: Dict[str, Any] = compact_content
    # Include priority if not ROUTINE (save space)
    if msg.priority != TacticalPriority.ROUTINE:
        payload_dict['_p'] = msg.priority.value
    # Include sender if present
    if msg.sender_id:
        payload_dict['_s'] = msg.sender_id
    # Include timestamp as Unix epoch (compact)
    payload_dict['_t'] = int(msg.timestamp.timestamp())

    # Serialize payload
    payload_bytes = _serialize_payload(payload_dict)

    # Base64url encode (no padding)
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b'=').decode('ascii')

    # Assemble X1 string
    return f"{X1_PREFIX}.{template_id}.{mode}.{msg_id}.{part}/{total}.{payload_b64}"


def decode(x1_string: str) -> TacticalMessage:
    """Decode an X1 wire format string to TacticalMessage.

    Args:
        x1_string: X1 wire format string.

    Returns:
        Decoded TacticalMessage.

    Raises:
        ValueError: If x1_string is not valid X1 format.
    """
    match = _X1_PATTERN.match(x1_string)
    if not match:
        raise ValueError(f"Invalid X1 format: {x1_string[:50]}...")

    template_id_str, mode_str, msg_id, part_str, total_str, payload_b64 = match.groups()

    # Parse template type
    try:
        tactical_type = TacticalType(int(template_id_str))
    except ValueError as e:
        raise ValueError(f"Unknown template ID: {template_id_str}") from e

    # Parse encryption mode
    encryption_mode = EncryptionMode(mode_str)

    # Decode base64url payload (add padding)
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += '=' * padding
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
    except Exception as e:
        raise ValueError(f"Invalid base64 payload: {e}") from e

    # Deserialize payload
    payload_dict = _deserialize_payload(payload_bytes)

    # Extract metadata
    priority_val = payload_dict.pop('_p', 'R')
    sender_id = payload_dict.pop('_s', '')
    timestamp_unix = payload_dict.pop('_t', None)

    # Parse priority
    try:
        priority = TacticalPriority(priority_val)
    except ValueError:
        priority = TacticalPriority.ROUTINE

    # Parse timestamp
    if timestamp_unix is not None:
        try:
            timestamp = datetime.fromtimestamp(int(timestamp_unix))
        except (ValueError, OSError, OverflowError):
            timestamp = datetime.now()
    else:
        timestamp = datetime.now()

    # Expand compact field names back to verbose
    content = _expand_fields(payload_dict)

    return TacticalMessage(
        id=msg_id,
        tactical_type=tactical_type,
        priority=priority,
        encryption_mode=encryption_mode,
        timestamp=timestamp,
        sender_id=sender_id,
        content=content,
        raw_x1=x1_string,
    )


def get_chunk_info(x1_string: str) -> Optional[Dict[str, Any]]:
    """Extract chunk info from an X1 string without full decode.

    Args:
        x1_string: X1 wire format string.

    Returns:
        Dict with 'msg_id', 'part', 'total', or None if invalid.
    """
    match = _X1_PATTERN.match(x1_string)
    if not match:
        return None

    _, _, msg_id, part_str, total_str, _ = match.groups()
    return {
        'msg_id': msg_id,
        'part': int(part_str),
        'total': int(total_str),
    }


# ============================================================================
# Internal helpers
# ============================================================================


def _compact_fields(content: Dict[str, Any]) -> Dict[str, Any]:
    """Compact field names for smaller wire payloads."""
    result: Dict[str, Any] = {}
    for key, value in content.items():
        compact_key = _FIELD_COMPACT.get(key, key)
        result[compact_key] = value
    return result


def _expand_fields(compact: Dict[str, Any]) -> Dict[str, Any]:
    """Expand compact field names back to verbose."""
    result: Dict[str, Any] = {}
    for key, value in compact.items():
        expanded_key = _FIELD_EXPAND.get(key, key)
        result[expanded_key] = value
    return result


def _serialize_payload(data: Dict[str, Any]) -> bytes:
    """Serialize payload dict to bytes (msgpack preferred, JSON fallback)."""
    if _HAS_MSGPACK:
        try:
            return _msgpack.packb(data, use_bin_type=True)
        except Exception as e:
            logger.debug(f"msgpack serialize failed, falling back to JSON: {e}")

    # JSON fallback (larger but always available)
    return json.dumps(data, separators=(',', ':')).encode('utf-8')


def _deserialize_payload(data: bytes) -> Dict[str, Any]:
    """Deserialize payload bytes to dict.

    Tries msgpack first, then JSON. msgpack payloads typically start
    with a map marker byte (0x80-0x8f for fixmap, 0xde/0xdf for larger).
    """
    if _HAS_MSGPACK and len(data) > 0:
        first_byte = data[0]
        if (0x80 <= first_byte <= 0x8f) or first_byte in (0xde, 0xdf):
            try:
                result = _msgpack.unpackb(data, raw=False, strict_map_key=False)
                if isinstance(result, dict):
                    return result
            except Exception:
                pass

    # JSON fallback
    try:
        result = json.loads(data.decode('utf-8'))
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    raise ValueError("Failed to deserialize X1 payload (neither msgpack nor JSON)")
