"""
Tests for CanonicalMessage — protocol-agnostic message format.

Tests cover:
- Factory methods (from_meshtastic, from_meshcore, from_rns, from_bridged_message)
- Serialization (to_meshtastic_text, to_meshcore_text, to_bridged_message)
- Text truncation for payload-limited protocols
- Internet origin filtering
- Round-trip fidelity
"""

import sys
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.canonical_message import (
    CanonicalMessage,
    MessageType,
    Protocol,
    MESHCORE_MAX_TEXT,
    MESHTASTIC_MAX_PAYLOAD,
    TRUNCATION_INDICATOR,
    _truncate_utf8,
    _portnum_to_message_type,
    format_reply_token,
    parse_reply_token,
    compute_content_id,
    normalize_content_for_id,
    CONTENT_ID_SCHEME,
    _CONTENT_ID_BRIDGE_TAGS,
)
from gateway.bridge_health import MessageOrigin


# =============================================================================
# Factory: from_meshtastic
# =============================================================================

class TestFromMeshtastic:
    """Test CanonicalMessage.from_meshtastic()."""

    def test_basic_text_message(self):
        """Parse a simple text message packet."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'fromId': '!aabbccdd',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'Hello from Meshtastic',
            },
            'hopLimit': 3,
            'hopStart': 3,
            'channel': 0,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.source_network == 'meshtastic'
        assert msg.source_address == '!aabbccdd'
        assert msg.content == 'Hello from Meshtastic'
        assert msg.message_type == MessageType.TEXT
        assert msg.is_broadcast is True
        assert msg.destination_address is None
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.RADIO

    def test_direct_message(self):
        """Parse a direct message (non-broadcast)."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0x11223344,
            'fromId': '!aabbccdd',
            'toId': '!11223344',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'DM to you',
            },
            'hopLimit': 2,
            'hopStart': 3,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.is_broadcast is False
        assert msg.destination_address == '!11223344'
        assert msg.hop_count == 1  # 3 - 2

    def test_mqtt_origin(self):
        """MQTT-originated messages flagged correctly."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'via MQTT'},
            'viaMqtt': True,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.via_internet is True
        assert msg.origin == MessageOrigin.MQTT

    def test_telemetry_portnum(self):
        """Telemetry portnum maps to TELEMETRY type."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TELEMETRY_APP', 'payload': b'\x01\x02'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.TELEMETRY

    def test_position_portnum(self):
        """Position portnum maps to POSITION type."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'POSITION_APP'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.POSITION

    def test_from_id_fallback(self):
        """fromId fallback to hex-formatted 'from' field."""
        packet = {
            'from': 0x12345678,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'test'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.source_address == '!12345678'

    def test_metadata_preserved(self):
        """Protocol-specific metadata preserved in metadata dict."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'test'},
            'rxSnr': 10.5,
            'rxRssi': -85,
            'channel': 2,
            'id': 12345,
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.metadata['rxSnr'] == 10.5
        assert msg.metadata['rxRssi'] == -85
        assert msg.metadata['channel'] == 2
        assert msg.metadata['packet_id'] == 12345

    def test_integer_portnum(self):
        """Integer portnums (from protobuf) handled correctly."""
        packet = {
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'decoded': {'portnum': 1, 'text': 'text msg'},
        }
        msg = CanonicalMessage.from_meshtastic(packet)

        assert msg.message_type == MessageType.TEXT


# =============================================================================
# Factory: from_meshcore
# =============================================================================

class TestFromMeshcore:
    """Test CanonicalMessage.from_meshcore()."""

    def test_contact_message(self):
        """Parse a MeshCore direct message event."""
        contact = SimpleNamespace(
            adv_name='NodeA',
            public_key=b'\xaa\xbb\xcc\xdd\xee\xff',
        )
        payload = SimpleNamespace(
            text='Hello from MeshCore',
            contact=contact,
            destination='some_dest',
            is_channel=False,
            channel=0,
        )
        event = SimpleNamespace(
            type='CONTACT_MSG_RECV',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.source_network == 'meshcore'
        assert msg.content == 'Hello from MeshCore'
        assert msg.message_type == MessageType.TEXT
        assert msg.is_broadcast is False
        assert msg.via_internet is False
        assert msg.origin == MessageOrigin.RADIO
        assert msg.hop_limit == 64

    def test_channel_message(self):
        """Parse a MeshCore channel (broadcast) message."""
        payload = SimpleNamespace(
            text='Channel broadcast',
            contact=None,
            sender='abc123',
            destination=None,
            is_channel=True,
            channel=1,
        )
        event = SimpleNamespace(
            type='CHANNEL_MSG_RECV',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.is_broadcast is True
        assert msg.destination_address is None
        assert msg.metadata['channel'] == 1

    def test_advertisement_event(self):
        """Parse a MeshCore advertisement (node discovery)."""
        payload = SimpleNamespace(
            text='',
            contact=None,
            sender='deadbeef',
            destination=None,
            is_channel=False,
            channel=0,
        )
        event = SimpleNamespace(
            type='ADVERTISEMENT',
            payload=payload,
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.message_type == MessageType.NODEINFO

    def test_dict_payload(self):
        """Handle dict-style payloads (for simulation/testing)."""
        event = SimpleNamespace(
            type='CONTACT_MSG_RECV',
            payload={
                'text': 'Dict payload test',
                'sender': 'node123',
                'destination': None,
                'is_channel': False,
                'channel': 0,
            },
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.content == 'Dict payload test'
        assert msg.source_address == 'node123'

    def test_ack_event(self):
        """ACK events map to ACK message type."""
        event = SimpleNamespace(
            type='ACK',
            payload={'text': '', 'sender': 'x', 'destination': None,
                     'is_channel': False, 'channel': 0},
        )
        msg = CanonicalMessage.from_meshcore(event)

        assert msg.message_type == MessageType.ACK


# =============================================================================
# Factory: from_rns
# =============================================================================

class TestFromRNS:
    """Test CanonicalMessage.from_rns()."""

    def test_basic_lxmf_delivery(self):
        """Parse a basic LXMF delivery."""
        lxmf = SimpleNamespace(
            content=b'Hello from RNS',
            source_hash=b'\xde\xad\xbe\xef',
            destination_hash=b'\xca\xfe\xba\xbe',
            title=b'Test Title',
            fields={},
        )
        msg = CanonicalMessage.from_rns(lxmf)

        assert msg.source_network == 'rns'
        assert msg.source_address == 'deadbeef'
        assert msg.destination_address == 'cafebabe'
        assert msg.content == 'Hello from RNS'
        assert msg.metadata['title'] == 'Test Title'

    def test_broadcast_lxmf(self):
        """Broadcast LXMF (no destination hash)."""
        lxmf = SimpleNamespace(
            content=b'Broadcast',
            source_hash=b'\xaa\xbb',
            destination_hash=None,
            title=None,
            fields={},
        )
        msg = CanonicalMessage.from_rns(lxmf)

        assert msg.is_broadcast is True
        assert msg.destination_address is None


# =============================================================================
# Factory: from_bridged_message (backward compatibility)
# =============================================================================

class TestFromBridgedMessage:
    """Test CanonicalMessage.from_bridged_message()."""

    def test_round_trip(self):
        """BridgedMessage → CanonicalMessage → BridgedMessage preserves fields."""
        from gateway.rns_bridge import BridgedMessage

        original = BridgedMessage(
            source_network='meshtastic',
            source_id='!aabbccdd',
            destination_id='!11223344',
            content='Round trip test',
            title='Test',
            is_broadcast=False,
            origin=MessageOrigin.RADIO,
            via_internet=False,
            metadata={'channel': 2},
        )

        canonical = CanonicalMessage.from_bridged_message(original)
        restored = canonical.to_bridged_message()

        assert restored.source_network == original.source_network
        assert restored.source_id == original.source_id
        assert restored.destination_id == original.destination_id
        assert restored.content == original.content
        assert restored.is_broadcast == original.is_broadcast
        assert restored.origin == original.origin
        assert restored.via_internet == original.via_internet

    def test_broadcast_round_trip(self):
        """Broadcast BridgedMessage round-trips correctly."""
        from gateway.rns_bridge import BridgedMessage

        original = BridgedMessage(
            source_network='rns',
            source_id='deadbeef',
            destination_id=None,
            content='Broadcast test',
            is_broadcast=True,
            origin=MessageOrigin.UNKNOWN,
        )

        canonical = CanonicalMessage.from_bridged_message(original)
        assert canonical.is_broadcast is True
        assert canonical.destination_address is None

        restored = canonical.to_bridged_message()
        assert restored.is_broadcast is True
        assert restored.destination_id is None


# =============================================================================
# Serialization: Text Truncation
# =============================================================================

class TestTextTruncation:
    """Test payload-size-aware text truncation."""

    def test_meshcore_truncation(self):
        """Long messages truncated for MeshCore's 160-byte limit."""
        long_text = "A" * 200
        msg = CanonicalMessage(content=long_text)
        result = msg.to_meshcore_text()

        assert len(result.encode('utf-8')) <= MESHCORE_MAX_TEXT
        assert result.endswith(TRUNCATION_INDICATOR)

    def test_meshtastic_no_truncation(self):
        """Short messages pass through untruncated."""
        short_text = "Short message"
        msg = CanonicalMessage(content=short_text)
        result = msg.to_meshtastic_text()

        assert result == short_text

    def test_meshtastic_truncation(self):
        """Messages exceeding Meshtastic limit are truncated."""
        long_text = "B" * 300
        msg = CanonicalMessage(content=long_text)
        result = msg.to_meshtastic_text()

        assert len(result.encode('utf-8')) <= MESHTASTIC_MAX_PAYLOAD
        assert result.endswith(TRUNCATION_INDICATOR)

    def test_utf8_clean_truncation(self):
        """Multi-byte UTF-8 characters truncated at character boundaries."""
        # Each emoji is 4 bytes in UTF-8
        emoji_text = "\U0001F600" * 50  # 200 bytes
        result = _truncate_utf8(emoji_text, 100)

        # Should not have broken multi-byte sequences
        result.encode('utf-8')  # Should not raise
        assert len(result.encode('utf-8')) <= 100

    def test_no_truncation_when_fits(self):
        """Text within limits returned unchanged."""
        text = "Fits fine"
        result = _truncate_utf8(text, 100)
        assert result == text


# =============================================================================
# Filtering: should_bridge
# =============================================================================

class TestShouldBridge:
    """Test message filtering logic."""

    def test_normal_message_bridges(self):
        """Normal radio message should bridge."""
        msg = CanonicalMessage(
            origin=MessageOrigin.RADIO,
            via_internet=False,
        )
        assert msg.should_bridge() is True

    def test_mqtt_filtered(self):
        """MQTT messages filtered when filter_mqtt=True."""
        msg = CanonicalMessage(
            origin=MessageOrigin.MQTT,
            via_internet=True,
        )
        assert msg.should_bridge(filter_mqtt=True) is False

    def test_mqtt_not_filtered_by_default(self):
        """MQTT messages pass by default."""
        msg = CanonicalMessage(
            origin=MessageOrigin.MQTT,
            via_internet=True,
        )
        assert msg.should_bridge() is True

    def test_internet_to_meshcore_filtered(self):
        """Internet-originated messages dropped when destined for MeshCore."""
        msg = CanonicalMessage(
            via_internet=True,
            destination_network='meshcore',
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is False

    def test_radio_to_meshcore_allowed(self):
        """Radio-originated messages pass to MeshCore."""
        msg = CanonicalMessage(
            via_internet=False,
            destination_network='meshcore',
        )
        assert msg.should_bridge(filter_internet_to_meshcore=True) is True


# =============================================================================
# Routing: get_destinations
# =============================================================================

class TestGetDestinations:
    """Test destination network routing."""

    def test_broadcast_excludes_source(self):
        """Broadcast messages route to all networks except source."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            is_broadcast=True,
        )
        dests = msg.get_destinations()

        assert 'meshtastic' not in dests
        assert 'meshcore' in dests
        assert 'rns' in dests

    def test_directed_uses_destination_network(self):
        """Directed messages route to specified destination."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            destination_network='rns',
            is_broadcast=False,
        )
        dests = msg.get_destinations()

        assert dests == ['rns']

    def test_no_destination(self):
        """Non-broadcast with no destination returns empty."""
        msg = CanonicalMessage(
            source_network='meshtastic',
            is_broadcast=False,
        )
        dests = msg.get_destinations()

        assert dests == []


# =============================================================================
# Portnum Mapping
# =============================================================================

class TestPortnumMapping:
    """Test Meshtastic portnum to MessageType mapping."""

    def test_known_portnums(self):
        assert _portnum_to_message_type('TEXT_MESSAGE_APP') == MessageType.TEXT
        assert _portnum_to_message_type('TELEMETRY_APP') == MessageType.TELEMETRY
        assert _portnum_to_message_type('POSITION_APP') == MessageType.POSITION
        assert _portnum_to_message_type('NODEINFO_APP') == MessageType.NODEINFO

    def test_unknown_portnum(self):
        assert _portnum_to_message_type('CUSTOM_APP') == MessageType.UNKNOWN

    def test_integer_portnums(self):
        assert _portnum_to_message_type(1) == MessageType.TEXT
        assert _portnum_to_message_type(67) == MessageType.TELEMETRY
        assert _portnum_to_message_type(999) == MessageType.UNKNOWN


# =============================================================================
# String Representation
# =============================================================================

class TestStringRepr:
    """Test __str__ for logging."""

    def test_broadcast_str(self):
        msg = CanonicalMessage(
            source_network='meshcore',
            source_address='abc123',
            content='Test broadcast',
            is_broadcast=True,
        )
        s = str(msg)
        assert 'meshcore:abc123' in s
        assert 'broadcast' in s

    def test_directed_str(self):
        msg = CanonicalMessage(
            source_network='meshtastic',
            source_address='!aabb',
            destination_address='!ccdd',
            content='Test DM',
        )
        s = str(msg)
        assert '!ccdd' in s

    def test_long_content_truncated_in_str(self):
        msg = CanonicalMessage(content='X' * 100)
        s = str(msg)
        assert '...' in s
        assert len(s) < 200


# =============================================================================
# Issue #66: application-layer ack fields
# =============================================================================

class TestAckFieldsIssue66:
    """
    ack_required / ack_of / reply_to wire application-layer delivery
    semantics across the bridge boundary so the origin protocol learns
    that its message reached the destination protocol's recipient.
    """

    def test_defaults_are_fire_and_forget(self):
        """A vanilla CanonicalMessage carries no ack semantics."""
        msg = CanonicalMessage(content="hello")
        assert msg.ack_required is False
        assert msg.ack_of is None
        assert msg.reply_to is None

    def test_factory_methods_default_ack_fields(self):
        """All three factory methods leave ack fields at safe defaults."""
        mt = CanonicalMessage.from_meshtastic({
            'from': 0xAABBCCDD,
            'to': 0xFFFFFFFF,
            'fromId': '!aabbccdd',
            'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'text': 'hi'},
        })
        mc = CanonicalMessage.from_meshcore(SimpleNamespace(
            type='CONTACT_MSG_RECV',
            payload=SimpleNamespace(text='hi', contact=None,
                                    destination='!dest', is_channel=False),
        ))
        rns = CanonicalMessage.from_rns(SimpleNamespace(
            content=b'hi',
            source_hash=b'\xaa' * 16,
            destination_hash=b'\xbb' * 16,
            title=None,
            fields={},
        ))
        for m in (mt, mc, rns):
            assert m.ack_required is False
            assert m.ack_of is None
            assert m.reply_to is None

    def test_explicit_ack_required(self):
        """Sender can set ack_required=True at construction."""
        msg = CanonicalMessage(content="weather pls", ack_required=True)
        assert msg.ack_required is True

    def test_ack_message_carries_ack_of(self):
        """An ACK message references the id it acknowledges."""
        original = CanonicalMessage(content="hello", ack_required=True)
        ack = CanonicalMessage(
            message_type=MessageType.ACK,
            ack_of=original.id,
            source_network='meshtastic',
            destination_address=original.source_address,
        )
        assert ack.message_type == MessageType.ACK
        assert ack.ack_of == original.id

    def test_reply_to_routes_response_elsewhere(self):
        """reply_to lets a sender redirect replies to another address."""
        msg = CanonicalMessage(
            source_network='meshcore',
            source_address='abc123',
            content="please respond",
            reply_to='!ffeedd00',  # meshtastic node id
        )
        assert msg.reply_to == '!ffeedd00'

    def test_round_trip_through_bridged_message(self):
        """
        CanonicalMessage → BridgedMessage → CanonicalMessage preserves
        the ack fields. They stash in BridgedMessage.metadata under the
        meshforge_ack_* namespace.
        """
        original = CanonicalMessage(
            source_network='meshcore',
            source_address='abc123',
            destination_address='!aabbccdd',
            content="weather pls",
            ack_required=True,
            reply_to='!dead0000',
        )
        bridged = original.to_bridged_message()
        # The namespace lands in metadata so older consumers can see it.
        assert bridged.metadata.get('meshforge_ack_required') is True
        assert bridged.metadata.get('meshforge_reply_to') == '!dead0000'
        # Round-trip restores the fields and cleans the namespace.
        round_tripped = CanonicalMessage.from_bridged_message(bridged)
        assert round_tripped.ack_required is True
        assert round_tripped.reply_to == '!dead0000'
        assert 'meshforge_ack_required' not in round_tripped.metadata
        assert 'meshforge_reply_to' not in round_tripped.metadata

    def test_round_trip_preserves_ack_of(self):
        """Ack messages also survive the BridgedMessage round-trip."""
        ack = CanonicalMessage(
            message_type=MessageType.ACK,
            source_network='rns',
            source_address='aabb',
            destination_address='ccdd',
            ack_of='msg-uuid-1234',
        )
        bridged = ack.to_bridged_message()
        assert bridged.metadata.get('meshforge_ack_of') == 'msg-uuid-1234'
        restored = CanonicalMessage.from_bridged_message(bridged)
        assert restored.ack_of == 'msg-uuid-1234'

    def test_default_fields_not_stashed_in_bridged_metadata(self):
        """
        When ack fields are at defaults, to_bridged_message doesn't pollute
        BridgedMessage.metadata with meshforge_ack_* keys.

        Why: existing consumers of BridgedMessage shouldn't suddenly see
        new keys appear on every message; only ack-bearing messages get
        the stash.
        """
        msg = CanonicalMessage(content="ordinary message")
        bridged = msg.to_bridged_message()
        assert 'meshforge_ack_required' not in bridged.metadata
        assert 'meshforge_ack_of' not in bridged.metadata
        assert 'meshforge_reply_to' not in bridged.metadata

    def test_pre_issue_66_bridged_message_deserializes_safely(self):
        """
        Backwards compat: a BridgedMessage produced by pre-Issue-#66 code
        has no meshforge_ack_* keys in metadata. from_bridged_message
        must return a CanonicalMessage with ack fields at defaults.

        Why: gateway upgrades will roll out one box at a time. A newer
        gateway must accept messages produced by an older gateway.
        """
        from gateway.rns_bridge import BridgedMessage
        legacy = BridgedMessage(
            source_network='rns',
            source_id='aabb',
            destination_id='ccdd',
            content='hi',
            metadata={'title': 'something', 'fields': {}},
        )
        restored = CanonicalMessage.from_bridged_message(legacy)
        assert restored.ack_required is False
        assert restored.ack_of is None
        assert restored.reply_to is None
        # And legacy metadata keys still come through untouched.
        assert restored.metadata.get('title') == 'something'

    def test_ack_fields_independent_of_message_type_enum(self):
        """
        ack_required can be True on a TEXT message (it's a request for an
        ACK from the recipient, not an assertion that *this* message is
        an ACK). MessageType.ACK is for the synthesized response.

        Why: this distinction is the core of Issue #66 and tests should
        pin it so a future refactor doesn't fuse the two concepts.
        """
        request = CanonicalMessage(
            message_type=MessageType.TEXT,
            content="please confirm",
            ack_required=True,
        )
        response = CanonicalMessage(
            message_type=MessageType.ACK,
            ack_of=request.id,
        )
        assert request.message_type == MessageType.TEXT
        assert request.ack_required is True
        assert response.message_type == MessageType.ACK
        assert response.ack_of == request.id


# =============================================================================
# Theme-A step 1: canonical reply token format/parse
# =============================================================================

class TestReplyTokenFormat:
    """
    format_reply_token / parse_reply_token define the protocol-qualified
    reply_to wire shape ("proto:addr"). Pure string helpers — per-protocol
    address validity is the consuming bridge's job.
    """

    def test_format_basic_all_protocols(self):
        assert format_reply_token("meshtastic", "!aabb0042") == "meshtastic:!aabb0042"
        assert format_reply_token("rns", "aa" * 16) == f"rns:{'aa' * 16}"
        assert format_reply_token("meshcore", "abcdef012345") == "meshcore:abcdef012345"

    def test_parse_round_trip(self):
        token = format_reply_token("meshtastic", "!aabb0042")
        assert parse_reply_token(token) == ("meshtastic", "!aabb0042")

    def test_parse_malformed_returns_none_pair(self):
        assert parse_reply_token("no-separator") == (None, None)
        assert parse_reply_token("") == (None, None)
        assert parse_reply_token(None) == (None, None)
        assert parse_reply_token(1234) == (None, None)

    def test_parse_empty_parts_rejected(self):
        assert parse_reply_token(":addr-only") == (None, None)
        assert parse_reply_token("proto-only:") == (None, None)
        assert parse_reply_token(":") == (None, None)

    def test_parse_address_with_colon_preserved(self):
        # split-once semantics: only the first colon separates.
        assert parse_reply_token("rns:ab:cd") == ("rns", "ab:cd")


# =============================================================================
# Dedup / identity arc — STEP 1: the unified logical content-id
# =============================================================================

import hashlib  # noqa: E402  (local to this test section)
from gateway import base_handler  # noqa: E402


class TestComputeContentId:
    """compute_content_id() — ONE stable id per logical message, computable
    identically on every box (dedup/identity arc, STEP 1, measure-only).

    Unifies base_handler.mqtt_content_dedup_key (#34) with
    RecentRfTxRegistry._key normalization: channel-by-NAME (#77),
    canonical_origin_token sender axis, tag-stripped + whitespace-normalized
    content, NO timestamp.
    """

    OT = "meshtastic:!a2e95ba4"   # a canonical origin token
    CH = "meshforge"

    # ---- core invariant: deterministic + cross-box identical ----
    def test_deterministic(self):
        a = compute_content_id(self.OT, "hello world", self.CH)
        b = compute_content_id(self.OT, "hello world", self.CH)
        assert a == b and a != ""

    def test_cross_box_identical_same_inputs(self):
        # The whole point: two independent gateways feeding the SAME logical
        # message (same origin token, same content, same channel NAME) mint
        # the SAME id with no shared state.
        box_moc = compute_content_id(self.OT, "talk story to me", self.CH)
        box_moc3 = compute_content_id(self.OT, "talk story to me", self.CH)
        assert box_moc == box_moc3

    def test_no_timestamp_component(self):
        # Re-derivation across calls must be stable — proves no wall-clock /
        # NTP-forgeable component (honest_failure_modes #6).
        ids = {compute_content_id(self.OT, "stable", self.CH) for _ in range(5)}
        assert len(ids) == 1

    # ---- tag invariance: raw == gateway-re-injected-tagged ----
    def test_bridge_tag_invariance(self):
        # The dup-A / identity unification: the SAME logical message hashes
        # identically whether raw on one path or [RNS:]/[MC:] re-injected on
        # another. '[RNS:xx] hello' == '[MC:yy] hello' == 'hello'.
        raw = compute_content_id(self.OT, "hello", self.CH)
        rns = compute_content_id(self.OT, "[RNS:1f68] hello", self.CH)
        mc = compute_content_id(self.OT, "[MC:p4] hello", self.CH)
        mesh = compute_content_id(self.OT, "[Mesh:LONG_FAST:2f10] hello", self.CH)
        assert raw == rns == mc == mesh

    def test_nested_tags_stripped(self):
        # Iterative strip (a gateway can re-tag already-tagged content).
        nested = compute_content_id(self.OT, "[RNS:627f] [ch0:p4] hi", self.CH)
        plain = compute_content_id(self.OT, "hi", self.CH)
        assert nested == plain

    def test_whitespace_normalized(self):
        a = compute_content_id(self.OT, "hello   world", self.CH)
        b = compute_content_id(self.OT, "  hello world  ", self.CH)
        c = compute_content_id(self.OT, "hello\tworld", self.CH)
        assert a == b == c

    # ---- origin axis: identical text from different senders must differ ----
    def test_origin_distinguishes(self):
        # Fixes the RecentRfTxRegistry gap (content-only key collides two
        # senders with identical text).
        a = compute_content_id("meshtastic:!aaaaaaaa", "ping", self.CH)
        b = compute_content_id("meshtastic:!bbbbbbbb", "ping", self.CH)
        assert a != b

    def test_empty_origin_still_ids_when_content_present(self):
        cid = compute_content_id("", "hello", self.CH)
        assert cid != "" and cid == compute_content_id("", "hello", self.CH)

    # ---- channel-by-NAME (#77) ----
    def test_channel_name_distinguishes(self):
        a = compute_content_id(self.OT, "hi", "meshforge")
        b = compute_content_id(self.OT, "hi", "LongFast")
        assert a != b

    def test_channel_name_case_insensitive(self):
        # Two boxes reporting the channel name in different case still match;
        # the numeric slot index (which #77 warns differs per box) is never
        # an input — only the NAME is.
        a = compute_content_id(self.OT, "hi", "MeshForge")
        b = compute_content_id(self.OT, "hi", "meshforge")
        assert a == b

    # ---- empty/degenerate: '' never collapses to a shared hash (honest #1) ----
    def test_empty_content_returns_empty(self):
        assert compute_content_id(self.OT, "", self.CH) == ""

    def test_whitespace_only_returns_empty(self):
        assert compute_content_id(self.OT, "   \t  ", self.CH) == ""

    def test_tag_only_returns_empty(self):
        # Tag with no body normalizes to empty -> no stable identity.
        assert compute_content_id(self.OT, "[RNS:1f68]", self.CH) == ""

    def test_empty_is_not_a_shared_hash(self):
        # Two distinct origins with empty content must BOTH be '' (explicit
        # unidentifiable), never the same non-empty hash.
        a = compute_content_id("meshtastic:!aaaaaaaa", "", self.CH)
        b = compute_content_id("meshtastic:!bbbbbbbb", "", self.CH)
        assert a == "" and b == ""

    # ---- format / namespace ----
    def test_scheme_prefix_and_shape(self):
        cid = compute_content_id(self.OT, "hello", self.CH)
        assert cid.startswith(CONTENT_ID_SCHEME + ":")
        digest = cid.split(":", 1)[1]
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)

    # ---- unification proof: normalization == RecentRfTxRegistry._key ----
    def test_normalization_matches_rf_registry_key(self):
        # The content-half of content_id normalizes IDENTICALLY to the
        # seen-on-RF dedup key, so the two are ONE function.
        for t in ("hello world", "[RNS:1f68] hello world",
                  "[MC:p4] hello", "  spaced   out  ",
                  "[RNS:627f] [ch0:p4] nested"):
            assert (hashlib.sha256(
                normalize_content_for_id(t).encode("utf-8")).hexdigest()
                == base_handler.RecentRfTxRegistry._key(t))

    # ---- drift guard: ONE prefix list (honest_failure_modes #5) ----
    def test_bridge_tag_list_pinned_to_base_handler(self):
        assert tuple(_CONTENT_ID_BRIDGE_TAGS) == tuple(
            base_handler.BRIDGE_TAG_PREFIXES)


class TestCanonicalMessageContentIdField:
    """CanonicalMessage carries the content_id (STEP 2: mint + carry)."""

    def test_default_empty(self):
        m = CanonicalMessage(source_network="meshtastic", content="hi")
        assert m.content_id == ""

    def test_settable(self):
        m = CanonicalMessage(content="hi", content_id="c1:deadbeef")
        assert m.content_id == "c1:deadbeef"


class TestFromMeshcoreStampsContentId:
    """from_meshcore mints a content_id on the MeshCore ingress leg (dup-birth
    B), keyed on meshcore:<sender>, content, channel (STEP 2b, measure-only)."""

    def test_channel_text_stamped(self):
        ev = SimpleNamespace(
            type="CHANNEL_MSG_RECV",
            payload={"text": "elo", "sender": "p4abc",
                     "channel": 0, "is_channel": True})
        m = CanonicalMessage.from_meshcore(ev)
        assert m.content_id == compute_content_id("meshcore:p4abc", "elo", "0")
        assert m.content_id != ""

    def test_advertisement_empty_text_no_id(self):
        # An advertisement (no text) has no logical content identity -> ''.
        ev = SimpleNamespace(
            type="ADVERTISEMENT",
            payload={"text": "", "sender": "p4abc", "channel": 0})
        m = CanonicalMessage.from_meshcore(ev)
        assert m.content_id == ""

    def test_same_message_same_id(self):
        ev1 = SimpleNamespace(type="CONTACT_MSG_RECV",
                              payload={"text": "hi", "sender": "kk", "channel": 1})
        ev2 = SimpleNamespace(type="CONTACT_MSG_RECV",
                              payload={"text": "hi", "sender": "kk", "channel": 1})
        assert (CanonicalMessage.from_meshcore(ev1).content_id
                == CanonicalMessage.from_meshcore(ev2).content_id != "")
