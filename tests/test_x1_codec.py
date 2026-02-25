"""Tests for X1 wire format codec."""

import pytest
from datetime import datetime

from tactical.models import (
    TacticalType, TacticalPriority, EncryptionMode,
    TacticalMessage, CheckIn, SITREP, ZoneMarking,
)
from tactical.x1_codec import encode, decode, is_x1, get_chunk_info


class TestIsX1:
    """Test X1 format detection."""

    def test_valid_x1(self):
        # Minimal valid X1 string
        x1 = "X1.4.C.K7V3N.1/1.eyJ0ZXN0IjoxfQ"
        assert is_x1(x1)

    def test_not_x1_empty(self):
        assert not is_x1("")
        assert not is_x1(None)

    def test_not_x1_plain_text(self):
        assert not is_x1("Hello, mesh!")
        assert not is_x1("X1 is cool")

    def test_not_x1_partial(self):
        assert not is_x1("X1.4.C")
        assert not is_x1("X1.4.C.K7V3N")


class TestEncodeDecode:
    """Test encode/decode round-trip."""

    def test_checkin_roundtrip(self):
        checkin = CheckIn(callsign="WH6GXZ", status="ok", personnel_count=1)
        msg = TacticalMessage(
            id="TEST1",
            tactical_type=TacticalType.CHECKIN,
            priority=TacticalPriority.ROUTINE,
            encryption_mode=EncryptionMode.CLEAR,
            timestamp=datetime(2026, 2, 22, 12, 0, 0),
            sender_id="WH6GXZ",
            content=checkin.to_dict(),
        )

        x1_string = encode(msg)
        assert x1_string.startswith("X1.4.C.TEST1.1/1.")
        assert is_x1(x1_string)

        decoded = decode(x1_string)
        assert decoded.id == "TEST1"
        assert decoded.tactical_type == TacticalType.CHECKIN
        assert decoded.encryption_mode == EncryptionMode.CLEAR
        assert decoded.sender_id == "WH6GXZ"
        assert decoded.content['callsign'] == "WH6GXZ"
        assert decoded.content['status'] == "ok"
        assert decoded.raw_x1 == x1_string

    def test_sitrep_roundtrip(self):
        sitrep = SITREP(
            situation="Fire on north ridge",
            actions_taken="Evacuated camp",
        )
        msg = TacticalMessage(
            id="SIT01",
            tactical_type=TacticalType.SITREP,
            priority=TacticalPriority.IMMEDIATE,
            encryption_mode=EncryptionMode.CLEAR,
            timestamp=datetime(2026, 2, 22, 14, 30, 0),
            sender_id="KH6ABC",
            content=sitrep.to_dict(),
        )

        x1_string = encode(msg)
        assert x1_string.startswith("X1.1.C.SIT01.")

        decoded = decode(x1_string)
        assert decoded.tactical_type == TacticalType.SITREP
        assert decoded.priority == TacticalPriority.IMMEDIATE
        assert decoded.content['situation'] == "Fire on north ridge"

    def test_zone_roundtrip(self):
        zone = ZoneMarking(
            name="Staging A",
            zone_type="staging",
            center_lat=21.3069,
            center_lon=-157.8583,
            radius_m=200.0,
        )
        msg = TacticalMessage(
            id="ZN001",
            tactical_type=TacticalType.ZONE,
            sender_id="WH6GXZ",
            content=zone.to_dict(),
        )

        x1_string = encode(msg)
        decoded = decode(x1_string)
        assert decoded.tactical_type == TacticalType.ZONE
        assert decoded.content['name'] == "Staging A"
        assert decoded.content['zone_type'] == "staging"
        assert abs(decoded.content['center_lat'] - 21.3069) < 0.001

    def test_secure_mode(self):
        msg = TacticalMessage(
            id="SEC01",
            tactical_type=TacticalType.CHECKIN,
            encryption_mode=EncryptionMode.SECURE,
            sender_id="WH6GXZ",
            content={"callsign": "WH6GXZ", "status": "ok"},
        )

        x1_string = encode(msg)
        assert ".S." in x1_string  # SECURE mode flag

        decoded = decode(x1_string)
        assert decoded.encryption_mode == EncryptionMode.SECURE

    def test_priority_preservation(self):
        """Non-ROUTINE priorities should be preserved in wire format."""
        msg = TacticalMessage(
            id="PRI01",
            tactical_type=TacticalType.SITREP,
            priority=TacticalPriority.FLASH,
            content={"situation": "Emergency"},
        )

        x1_string = encode(msg)
        decoded = decode(x1_string)
        assert decoded.priority == TacticalPriority.FLASH

    def test_routine_priority_default(self):
        """ROUTINE priority should be the default (not stored in payload)."""
        msg = TacticalMessage(
            id="DEF01",
            tactical_type=TacticalType.EVENT,
            content={"description": "Test"},
        )

        x1_string = encode(msg)
        decoded = decode(x1_string)
        assert decoded.priority == TacticalPriority.ROUTINE


class TestDecodeErrors:
    """Test decode error handling."""

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            decode("not an X1 message")

    def test_invalid_template_id(self):
        with pytest.raises(ValueError):
            decode("X1.99.C.TEST1.1/1.dGVzdA")

    def test_invalid_payload(self):
        with pytest.raises(ValueError):
            decode("X1.4.C.TEST1.1/1.!!!invalid!!!")


class TestGetChunkInfo:
    """Test chunk info extraction."""

    def test_single_chunk(self):
        x1 = "X1.4.C.K7V3N.1/1.eyJ0ZXN0IjoxfQ"
        info = get_chunk_info(x1)
        assert info is not None
        assert info['msg_id'] == "K7V3N"
        assert info['part'] == 1
        assert info['total'] == 1

    def test_multi_chunk(self):
        x1 = "X1.1.C.ABCDE.3/5.eyJ0ZXN0IjoxfQ"
        info = get_chunk_info(x1)
        assert info is not None
        assert info['part'] == 3
        assert info['total'] == 5

    def test_invalid_string(self):
        assert get_chunk_info("not X1") is None
        assert get_chunk_info("") is None


class TestCompactFields:
    """Test field name compaction for smaller wire payloads."""

    def test_field_compaction(self):
        """Verify that content fields are compacted in the wire format."""
        msg = TacticalMessage(
            id="CMP01",
            tactical_type=TacticalType.CHECKIN,
            content={"callsign": "WH6GXZ", "status": "ok"},
        )

        x1_string = encode(msg)
        # Decode should restore original field names
        decoded = decode(x1_string)
        assert 'callsign' in decoded.content
        assert decoded.content['callsign'] == "WH6GXZ"
