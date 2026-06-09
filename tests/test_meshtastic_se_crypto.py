"""Tests for the native Meshtastic ServiceEnvelope decrypt (AES-CTR).

The crypto is proven against LIVE meshtasticd /e/ output (moc 2026-06-08:
NODEINFO/POSITION decoded + cross-checked vs /json/). These tests pin the
logic portably via a synthetic encrypt→decrypt round-trip + edge cases, and
skip cleanly when the optional meshtastic protobuf / cryptography deps are
absent (minimal-deps CI).
"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.meshtastic_se_crypto import (  # noqa: E402
    DEFAULT_KEY,
    DEFAULT_KEY_B64,
    DecodedPacket,
    crypto_available,
    decode_service_envelope,
    expand_psk,
    _nonce,
)

pytestmark = pytest.mark.skipif(
    not crypto_available(),
    reason="meshtastic protobufs / cryptography not installed (minimal-deps)",
)


# --- key expansion (no deps needed) --------------------------------------

class TestExpandPsk:
    def test_default_key_aq(self):
        assert expand_psk("AQ==") == DEFAULT_KEY

    def test_byte_two_advances_last_byte(self):
        # 0x02 → default key with last byte 0x01+1 = 0x02
        k = expand_psk("Ag==")  # base64 of 0x02
        assert k[:15] == DEFAULT_KEY[:15]
        assert k[15] == 0x02

    def test_zero_byte_is_unencrypted(self):
        assert expand_psk("AA==") is None  # 0x00

    def test_full_32_byte_key_raw(self):
        import base64
        raw = bytes(range(32))
        assert expand_psk(base64.b64encode(raw).decode()) == raw

    def test_full_16_byte_key_raw(self):
        import base64
        raw = bytes(range(16))
        assert expand_psk(base64.b64encode(raw).decode()) == raw

    def test_garbage_is_none(self):
        assert expand_psk("not base64 !!!") is None
        assert expand_psk("") is None
        assert expand_psk(None) is None

    def test_odd_length_key_rejected(self):
        import base64
        raw = bytes(range(20))  # not 1/16/32
        assert expand_psk(base64.b64encode(raw).decode()) is None


class TestNonce:
    def test_nonce_shape(self):
        # packet_id LE (8) ‖ from LE (8) = 16 bytes
        n = _nonce(0x11223344, 0xAABBCCDD)
        assert len(n) == 16
        assert n == struct.pack("<QQ", 0x11223344, 0xAABBCCDD)


# --- round-trip via the real meshtastic protobufs ------------------------

def _encrypt_envelope(*, portnum, payload, packet_id, from_node, to_node,
                      key_b64=DEFAULT_KEY_B64, channel_id="LongFast",
                      pki=False, cleartext=False):
    """Build a ServiceEnvelope exactly as meshtasticd would: a MeshPacket
    whose Data is AES-CTR-encrypted under the channel key (or cleartext)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from meshtastic.protobuf import mqtt_pb2, mesh_pb2

    data = mesh_pb2.Data()
    data.portnum = portnum
    data.payload = payload
    plain = data.SerializeToString()

    pkt = mesh_pb2.MeshPacket()
    pkt.id = packet_id
    setattr(pkt, "from", from_node)
    pkt.to = to_node
    if cleartext:
        pkt.decoded.CopyFrom(data)
    else:
        key = expand_psk(key_b64)
        nonce = _nonce(packet_id, from_node)
        enc = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
        pkt.encrypted = enc.update(plain) + enc.finalize()
        if pki:
            pkt.pki_encrypted = True

    se = mqtt_pb2.ServiceEnvelope()
    se.packet.CopyFrom(pkt)
    se.channel_id = channel_id
    se.gateway_id = "!11111111"
    return se.SerializeToString()


class TestRoundTrip:
    def test_text_packet_decrypts(self):
        env = _encrypt_envelope(
            portnum=1, payload=b"hello mesh", packet_id=0xABCD,
            from_node=0x12345678, to_node=0xFFFFFFFF)
        dp = decode_service_envelope(env, [DEFAULT_KEY_B64])
        assert dp is not None
        assert dp.portnum == 1
        assert dp.payload == b"hello mesh"
        assert dp.packet_id == 0xABCD
        assert dp.from_node == 0x12345678
        assert dp.channel_id == "LongFast"

    def test_cleartext_packet_decodes(self):
        env = _encrypt_envelope(
            portnum=4, payload=b"\x08\x01", packet_id=7, from_node=9,
            to_node=0xFFFFFFFF, cleartext=True)
        dp = decode_service_envelope(env)
        assert dp is not None and dp.portnum == 4

    def test_wrong_key_returns_none(self):
        import base64
        env = _encrypt_envelope(
            portnum=1, payload=b"secret", packet_id=1, from_node=2,
            to_node=3)
        wrong = base64.b64encode(bytes([7] * 16)).decode()
        assert decode_service_envelope(env, [wrong]) is None

    def test_right_key_among_several(self):
        import base64
        env = _encrypt_envelope(
            portnum=1, payload=b"multi", packet_id=5, from_node=6, to_node=7)
        wrong = base64.b64encode(bytes([9] * 16)).decode()
        dp = decode_service_envelope(env, [wrong, DEFAULT_KEY_B64])
        assert dp is not None and dp.payload == b"multi"

    def test_pki_packet_skipped(self):
        env = _encrypt_envelope(
            portnum=1, payload=b"pkc", packet_id=2, from_node=3, to_node=4,
            pki=True)
        assert decode_service_envelope(env, [DEFAULT_KEY_B64]) is None

    def test_garbage_payload_none(self):
        assert decode_service_envelope(b"not a protobuf at all") is None


class TestRoutingDecode:
    def _routing_payload(self, error_reason):
        from meshtastic.protobuf import mesh_pb2
        r = mesh_pb2.Routing()
        r.error_reason = error_reason
        return r.SerializeToString()

    def test_positive_ack_request_id(self):
        from meshtastic.protobuf import mesh_pb2
        env = _encrypt_envelope(
            portnum=5, payload=self._routing_payload(0),  # NONE
            packet_id=0xACE, from_node=0x99, to_node=0x11)
        # add a request_id by re-encoding Data with request_id set
        dp = decode_service_envelope(env, [DEFAULT_KEY_B64])
        assert dp is not None and dp.is_routing
        assert dp.routing_error_name() == "NONE"

    def test_nak_reason_name(self):
        from meshtastic.protobuf import mesh_pb2
        env = _encrypt_envelope(
            portnum=5,
            payload=self._routing_payload(mesh_pb2.Routing.Error.MAX_RETRANSMIT),
            packet_id=0xBAD, from_node=1, to_node=2)
        dp = decode_service_envelope(env, [DEFAULT_KEY_B64])
        assert dp is not None and dp.is_routing
        assert dp.routing_error_name() == "MAX_RETRANSMIT"

    def test_request_id_recovered(self):
        """A real ACK carries request_id == the original packet's id."""
        from cryptography.hazmat.primitives.ciphers import (
            Cipher, algorithms, modes)
        from meshtastic.protobuf import mqtt_pb2, mesh_pb2
        data = mesh_pb2.Data()
        data.portnum = 5
        data.request_id = 0xDEADBEEF
        data.payload = self._routing_payload(0)
        plain = data.SerializeToString()
        key = expand_psk(DEFAULT_KEY_B64)
        nonce = _nonce(0x1234, 0x5678)
        enc = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
        pkt = mesh_pb2.MeshPacket()
        pkt.id = 0x1234
        setattr(pkt, "from", 0x5678)
        pkt.to = 0xAA
        pkt.encrypted = enc.update(plain) + enc.finalize()
        se = mqtt_pb2.ServiceEnvelope()
        se.packet.CopyFrom(pkt)
        se.channel_id = "LongFast"
        dp = decode_service_envelope(se.SerializeToString(), [DEFAULT_KEY_B64])
        assert dp is not None
        assert dp.request_id == 0xDEADBEEF
        assert dp.is_routing
