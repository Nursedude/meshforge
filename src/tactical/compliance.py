"""
Ham Compliance Module for MeshForge Tactical Messaging.

Enforces CLEAR/SECURE encryption modes per FCC Part 97:
- CLEAR: No encryption, ham-legal. Required on amateur radio frequencies.
- SECURE: AES-256-GCM encryption for non-amateur (Part 15, ISM) use.

Default is CLEAR for safety — SECURE requires explicit opt-in.

Usage:
    from tactical.compliance import (
        apply_encryption, strip_encryption,
        validate_ham_compliance, get_compliance_badge,
    )
    from tactical.models import EncryptionMode

    # Encrypt payload (CLEAR = pass-through)
    encrypted = apply_encryption(payload, EncryptionMode.CLEAR)

    # Decrypt
    decrypted = strip_encryption(encrypted, EncryptionMode.CLEAR)

    # Check compliance
    is_compliant = validate_ham_compliance(msg)

    # Display badge
    badge = get_compliance_badge(msg.encryption_mode)  # "[CLEAR]" or "[SECURE]"
"""

import logging
import os
import struct
from typing import Optional

from tactical.models import EncryptionMode, TacticalMessage

logger = logging.getLogger(__name__)

# Optional cryptography library for SECURE mode
# Use try/except BaseException because cryptography can raise pyo3 panics (broken cffi)
_HAS_CRYPTO = False
_AESGCM = None  # type: ignore
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    _HAS_CRYPTO = True
except BaseException:
    pass

# AES-256-GCM nonce size (bytes)
_NONCE_SIZE = 12

# AES-256 key size (bytes)
_KEY_SIZE = 32

# Wire format for SECURE payload: [nonce (12B)] [ciphertext (variable)] [tag (16B)]
# Tag is appended by AES-GCM automatically


def get_compliance_badge(mode: EncryptionMode) -> str:
    """Return display badge for encryption mode.

    Args:
        mode: Encryption mode.

    Returns:
        "[CLEAR]" or "[SECURE]" string for display in messages.
    """
    if mode == EncryptionMode.CLEAR:
        return "[CLEAR]"
    return "[SECURE]"


def validate_ham_compliance(msg: TacticalMessage) -> bool:
    """Validate that a message meets FCC Part 97 requirements.

    CLEAR mode messages must not contain encrypted payloads.
    SECURE mode messages are inherently non-compliant for ham use.

    Args:
        msg: TacticalMessage to validate.

    Returns:
        True if message is ham-compliant (CLEAR mode with no encrypted content).
    """
    if msg.encryption_mode == EncryptionMode.SECURE:
        return False

    # In CLEAR mode, verify content is plaintext (no binary blobs)
    for value in msg.content.values():
        if isinstance(value, bytes):
            logger.warning(
                f"CLEAR mode message {msg.id} contains binary content — "
                "not ham-compliant"
            )
            return False

    return True


def apply_encryption(
    payload: bytes,
    mode: EncryptionMode,
    key: Optional[bytes] = None,
) -> bytes:
    """Apply encryption based on mode.

    Args:
        payload: Raw payload bytes to encrypt.
        mode: CLEAR (pass-through) or SECURE (AES-256-GCM).
        key: 32-byte AES key. Required for SECURE mode.

    Returns:
        Encrypted payload (SECURE) or unchanged payload (CLEAR).

    Raises:
        ValueError: If SECURE mode requested but cryptography not available or key missing.
    """
    if mode == EncryptionMode.CLEAR:
        return payload

    # SECURE mode
    if not _HAS_CRYPTO:
        raise ValueError(
            "SECURE mode requires 'cryptography' package. "
            "Install with: pip install cryptography"
        )

    if key is None or len(key) != _KEY_SIZE:
        raise ValueError(
            f"SECURE mode requires a {_KEY_SIZE}-byte AES key"
        )

    try:
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = _AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, payload, None)

        # Prepend nonce to ciphertext
        return nonce + ciphertext

    except Exception as e:
        raise ValueError(f"Encryption failed: {e}") from e


def strip_encryption(
    payload: bytes,
    mode: EncryptionMode,
    key: Optional[bytes] = None,
) -> bytes:
    """Decrypt payload based on mode.

    Args:
        payload: Encrypted payload bytes (for SECURE) or raw bytes (for CLEAR).
        mode: CLEAR (pass-through) or SECURE (AES-256-GCM).
        key: 32-byte AES key. Required for SECURE mode.

    Returns:
        Decrypted payload.

    Raises:
        ValueError: If decryption fails or key is missing.
    """
    if mode == EncryptionMode.CLEAR:
        return payload

    # SECURE mode
    if not _HAS_CRYPTO:
        raise ValueError(
            "SECURE mode requires 'cryptography' package. "
            "Install with: pip install cryptography"
        )

    if key is None or len(key) != _KEY_SIZE:
        raise ValueError(
            f"SECURE mode requires a {_KEY_SIZE}-byte AES key"
        )

    if len(payload) < _NONCE_SIZE + 16:  # nonce + minimum tag
        raise ValueError("Payload too short for SECURE decryption")

    try:
        nonce = payload[:_NONCE_SIZE]
        ciphertext = payload[_NONCE_SIZE:]

        aesgcm = _AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)

    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e


def generate_key() -> bytes:
    """Generate a random 256-bit AES key for SECURE mode.

    Returns:
        32 random bytes suitable for AES-256-GCM.
    """
    return os.urandom(_KEY_SIZE)


def is_secure_available() -> bool:
    """Check if SECURE mode is available (cryptography package installed).

    Returns:
        True if cryptography package is available.
    """
    return _HAS_CRYPTO
