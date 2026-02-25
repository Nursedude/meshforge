"""Tests for ham compliance module."""

import pytest

from tactical.models import (
    TacticalMessage, TacticalType, EncryptionMode,
)
from tactical.compliance import (
    get_compliance_badge,
    validate_ham_compliance,
    apply_encryption,
    strip_encryption,
    generate_key,
    is_secure_available,
)


class TestComplianceBadge:
    """Test compliance badge display."""

    def test_clear_badge(self):
        assert get_compliance_badge(EncryptionMode.CLEAR) == "[CLEAR]"

    def test_secure_badge(self):
        assert get_compliance_badge(EncryptionMode.SECURE) == "[SECURE]"


class TestHamCompliance:
    """Test ham compliance validation."""

    def test_clear_mode_compliant(self):
        msg = TacticalMessage(
            tactical_type=TacticalType.CHECKIN,
            encryption_mode=EncryptionMode.CLEAR,
            content={"callsign": "WH6GXZ", "status": "ok"},
        )
        assert validate_ham_compliance(msg)

    def test_secure_mode_not_compliant(self):
        msg = TacticalMessage(
            tactical_type=TacticalType.CHECKIN,
            encryption_mode=EncryptionMode.SECURE,
            content={"callsign": "WH6GXZ"},
        )
        assert not validate_ham_compliance(msg)

    def test_clear_with_binary_not_compliant(self):
        msg = TacticalMessage(
            tactical_type=TacticalType.CHECKIN,
            encryption_mode=EncryptionMode.CLEAR,
            content={"data": b"binary content"},
        )
        assert not validate_ham_compliance(msg)


class TestClearEncryption:
    """Test CLEAR mode (pass-through)."""

    def test_clear_passthrough(self):
        payload = b"Hello, mesh network!"
        result = apply_encryption(payload, EncryptionMode.CLEAR)
        assert result == payload

    def test_clear_strip_passthrough(self):
        payload = b"Hello, mesh network!"
        result = strip_encryption(payload, EncryptionMode.CLEAR)
        assert result == payload


class TestSecureEncryption:
    """Test SECURE mode (AES-256-GCM)."""

    def test_generate_key(self):
        key = generate_key()
        assert len(key) == 32
        assert isinstance(key, bytes)

    def test_key_uniqueness(self):
        key1 = generate_key()
        key2 = generate_key()
        assert key1 != key2

    @pytest.mark.skipif(
        not is_secure_available(),
        reason="cryptography package not installed"
    )
    def test_secure_roundtrip(self):
        key = generate_key()
        payload = b"Secret tactical data"

        encrypted = apply_encryption(payload, EncryptionMode.SECURE, key=key)
        assert encrypted != payload
        assert len(encrypted) > len(payload)  # nonce + tag overhead

        decrypted = strip_encryption(encrypted, EncryptionMode.SECURE, key=key)
        assert decrypted == payload

    @pytest.mark.skipif(
        not is_secure_available(),
        reason="cryptography package not installed"
    )
    def test_secure_wrong_key_fails(self):
        key1 = generate_key()
        key2 = generate_key()
        payload = b"Secret data"

        encrypted = apply_encryption(payload, EncryptionMode.SECURE, key=key1)

        with pytest.raises(ValueError):
            strip_encryption(encrypted, EncryptionMode.SECURE, key=key2)

    def test_secure_no_key_raises(self):
        with pytest.raises(ValueError):
            apply_encryption(b"data", EncryptionMode.SECURE)

    def test_secure_short_key_raises(self):
        with pytest.raises(ValueError):
            apply_encryption(b"data", EncryptionMode.SECURE, key=b"too_short")
