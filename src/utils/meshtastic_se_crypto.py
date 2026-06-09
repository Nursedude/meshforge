"""Native Meshtastic ServiceEnvelope decrypt — AES-CTR, no external dep.

The MQTT ``…/2/e/<ch>/<node>`` topic carries every MeshPacket the radio
hears, wrapped in a ``ServiceEnvelope`` protobuf with the inner payload
**channel-encrypted** (AES-CTR). This module parses the envelope and
decrypts the packet using only the ``cryptography`` library (already a
MeshForge dep) and the meshtastic protobufs — replacing the absent
``meshing_around`` ``MeshPacketProcessor`` that left ``utils.mqtt_decryptor``
non-functional on the fleet.

Its first consumer (Thread-2 step 4, mqtt_bridge mode) is **honest
Meshtastic delivery confirmation**: a gateway that TXes via HTTP toradio +
RXes via MQTT json can't see ``ROUTING_APP`` ACKs in the json stream, but
they DO ride ``/e/`` — so subscribing to ``/e/``, decrypting, and reading
``ROUTING_APP`` ``request_id`` lets the gateway confirm a wantAck DM
**without a fromradio read** (preserving the #17/#75 zero-interference
invariant). It is independently useful: any consumer can now decode the
``/e/`` traffic the fleet currently sees only as opaque node-existence pings.

Crypto reference (meshtastic CryptoEngine):
  key   : the channel PSK. A 1-byte PSK is an index into the default key —
          ``"AQ=="`` (byte 0x01) IS the default key
          ``d4f1bb3a20290759f0bcffabcf4e6901``; byte N → default key with the
          last byte set to ``(0x01 + N - 1)``. A 16/32-byte PSK is used raw.
  nonce : ``packet_id`` (uint64 LE) ‖ ``from`` (uint64 LE) = 16 bytes; the
          16-byte value is the AES-CTR initial counter.
  cipher: AES-128 (16-byte key) or AES-256 (32-byte key) in CTR mode.

PKC (curve25519) packets (``pki_encrypted``) are NOT channel-decryptable and
return None — only channel-keyed packets are recoverable here.
"""

from __future__ import annotations

import base64
import logging
import struct
from dataclasses import dataclass
from typing import List, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# meshtastic protobufs (external dep, guarded for minimal-deps CI / boxes)
_mqtt_pb2, _HAS_MQTT_PB = safe_import("meshtastic.protobuf.mqtt_pb2")
_mesh_pb2, _HAS_MESH_PB = safe_import("meshtastic.protobuf.mesh_pb2")
_portnums_pb2, _HAS_PORTNUMS = safe_import("meshtastic.protobuf.portnums_pb2")

try:
    from cryptography.hazmat.primitives.ciphers import (
        Cipher, algorithms, modes,
    )
    _HAS_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - import guard
    _HAS_CRYPTOGRAPHY = False

# Meshtastic default channel key — base64 "1PG7OiApB1nwvP+rz05pAQ==".
# The 1-byte PSK "AQ==" (0x01) expands to exactly this.
DEFAULT_KEY = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
DEFAULT_KEY_B64 = "AQ=="

_ROUTING_PORTNUM = 5  # PortNum.ROUTING_APP


def crypto_available() -> bool:
    """Whether decryption can run (protobufs + cryptography present)."""
    return bool(_HAS_MQTT_PB and _HAS_MESH_PB and _HAS_CRYPTOGRAPHY)


def expand_psk(psk_b64: str) -> Optional[bytes]:
    """Expand a base64 channel PSK to AES key bytes.

    Returns None for an unencrypted channel (empty / 0x00 PSK) or invalid
    input. A 1-byte PSK is the default-key index; 16/32-byte PSKs are raw.
    """
    if not isinstance(psk_b64, str) or not psk_b64:
        return None
    try:
        raw = base64.b64decode(psk_b64)
    except Exception:
        return None
    if len(raw) == 0:
        return None
    if len(raw) == 1:
        v = raw[0]
        if v == 0:
            return None  # explicitly unencrypted
        # default key with the last byte advanced by (v - 1)
        return DEFAULT_KEY[:15] + bytes([(DEFAULT_KEY[15] + v - 1) & 0xFF])
    if len(raw) in (16, 32):
        return raw
    return None


def _nonce(packet_id: int, from_node: int) -> bytes:
    # packet_id (uint64 LE) ‖ from (uint64 LE) — 16 bytes, AES-CTR counter.
    return struct.pack("<QQ", packet_id & 0xFFFFFFFF, from_node & 0xFFFFFFFF)


def _aes_ctr(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
    dec = cipher.decryptor()
    return dec.update(ciphertext) + dec.finalize()


@dataclass(frozen=True)
class DecodedPacket:
    """A decoded (decrypted-or-plaintext) MeshPacket from a ServiceEnvelope."""
    portnum: int
    payload: bytes
    request_id: int
    from_node: int
    to_node: int
    packet_id: int
    channel_id: str          # channel NAME from the envelope
    want_ack: bool = False

    @property
    def is_routing(self) -> bool:
        return self.portnum == _ROUTING_PORTNUM

    def routing_error_name(self) -> Optional[str]:
        """For a ROUTING_APP packet, the Routing.error_reason enum NAME.

        Returns "NONE" for a positive ACK (error_reason 0 / absent), the
        enum name for a NAK, or None if not ROUTING_APP / unparseable.
        """
        if self.portnum != _ROUTING_PORTNUM or not _HAS_MESH_PB:
            return None
        try:
            routing = _mesh_pb2.Routing()
            routing.ParseFromString(self.payload)
            return _mesh_pb2.Routing.Error.Name(routing.error_reason)
        except Exception:
            return None


def _build(data, frm, to, pid, ch_id, want_ack) -> Optional[DecodedPacket]:
    try:
        return DecodedPacket(
            portnum=int(data.portnum),
            payload=bytes(data.payload),
            request_id=int(getattr(data, "request_id", 0) or 0),
            from_node=int(frm),
            to_node=int(to),
            packet_id=int(pid),
            channel_id=str(ch_id),
            want_ack=bool(want_ack),
        )
    except Exception:
        return None


def _plausible(data) -> bool:
    """Reject obviously-garbage decrypts (wrong key produces noise that may
    still parse as a lenient protobuf). A real packet has a known portnum in
    the assigned range and, for non-empty apps, some payload."""
    try:
        pn = int(data.portnum)
    except Exception:
        return False
    if pn <= 0 or pn > 511:
        return False
    # ROUTING_APP carries a (possibly empty) Routing payload; most others
    # have a payload. An all-zero decrypt yields portnum 0 (already rejected).
    return True


def decode_service_envelope(
    payload: bytes, keys: Optional[List[str]] = None,
) -> Optional[DecodedPacket]:
    """Parse + decrypt one ServiceEnvelope MQTT payload.

    ``keys`` is a list of base64 channel PSKs to try (default: the meshtastic
    default key). Returns a :class:`DecodedPacket` on success, or None for a
    parse failure, a PKC packet, or a payload no key decrypts.
    """
    if not crypto_available():
        return None
    if keys is None:
        keys = [DEFAULT_KEY_B64]
    try:
        se = _mqtt_pb2.ServiceEnvelope()
        se.ParseFromString(payload)
    except Exception:
        return None
    if not se.HasField("packet"):
        return None
    pkt = se.packet
    pid = int(pkt.id)
    frm = int(getattr(pkt, "from"))
    to = int(pkt.to)
    ch_id = se.channel_id
    want_ack = bool(getattr(pkt, "want_ack", False))

    # Cleartext channel — already decoded.
    if pkt.HasField("decoded"):
        return _build(pkt.decoded, frm, to, pid, ch_id, want_ack)

    if not pkt.encrypted:
        return None
    if getattr(pkt, "pki_encrypted", False):
        return None  # curve25519 DM — not channel-decryptable

    nonce = _nonce(pid, frm)
    ciphertext = bytes(pkt.encrypted)
    for kb64 in keys:
        key = expand_psk(kb64)
        if key is None or len(key) not in (16, 32):
            continue
        try:
            plain = _aes_ctr(key, nonce, ciphertext)
            data = _mesh_pb2.Data()
            data.ParseFromString(plain)
        except Exception:
            continue
        if _plausible(data):
            return _build(data, frm, to, pid, ch_id, want_ack)
    return None
