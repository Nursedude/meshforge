"""Tests for QR transport module."""

import pytest

from tactical.models import TacticalMessage, TacticalType, CheckIn
from tactical.x1_codec import encode, is_x1
from tactical.qr_transport import decode_qr_text, generate_x1_for_qr, is_qr_available


class TestDecodeQRText:
    """Test QR text decoding."""

    def test_valid_x1_from_qr(self):
        msg = TacticalMessage(
            id="QR001",
            tactical_type=TacticalType.CHECKIN,
            sender_id="WH6GXZ",
            content=CheckIn(callsign="WH6GXZ", status="ok").to_dict(),
        )
        x1 = encode(msg)

        # Simulate scanning QR that contains the X1 string
        decoded = decode_qr_text(x1)
        assert decoded is not None
        assert decoded.id == "QR001"
        assert decoded.content['callsign'] == "WH6GXZ"

    def test_invalid_qr_text(self):
        assert decode_qr_text("Not an X1 message") is None
        assert decode_qr_text("") is None
        assert decode_qr_text(None) is None

    def test_whitespace_stripped(self):
        msg = TacticalMessage(
            id="QR002",
            tactical_type=TacticalType.CHECKIN,
            content={"callsign": "TEST"},
        )
        x1 = encode(msg)

        # QR scanners sometimes add whitespace
        decoded = decode_qr_text(f"  {x1}  ")
        assert decoded is not None
        assert decoded.id == "QR002"


class TestGenerateX1ForQR:
    """Test X1 generation for QR transport."""

    def test_generates_valid_x1(self):
        msg = TacticalMessage(
            id="GEN01",
            tactical_type=TacticalType.CHECKIN,
            content={"callsign": "WH6GXZ"},
        )
        x1 = generate_x1_for_qr(msg)
        assert is_x1(x1)

    def test_fits_in_qr(self):
        """Even a large message should fit in QR v40 (2953 bytes)."""
        msg = TacticalMessage(
            id="BIG01",
            tactical_type=TacticalType.SITREP,
            content={"situation": "x" * 500},
        )
        x1 = generate_x1_for_qr(msg)
        assert len(x1.encode('utf-8')) < 2953


class TestQRAvailability:
    """Test QR availability detection."""

    def test_availability_returns_bool(self):
        result = is_qr_available()
        assert isinstance(result, bool)
