"""Tests for gateway/mqtt_downlink_inject.py — true-origin downlink injection.

Wire-format assertions are pinned against the exact shape meshtasticd accepted
in the step-0 field proof (moc, 2026-06-03):
  - channel hash for "meshforge" + the ch2 PSK == 0x7a
  - from == spoofed origin, gateway_id == "!<origin>"
  - encrypted payload decrypts back to the original Data protobuf
"""

import base64
import struct

import pytest

# Decode-side crypto for the round-trip assertion. importorskip, not a bare
# import: downlink deps are OPTIONAL by design (_HAS_DOWNLINK_DEPS guards the
# runtime), and CI's minimal-deps profile has no cryptography — a top-level
# import errored the whole collection there, keeping CI red from the downlink
# port (e98612a, 2026-06-03) until 2026-06-04. Same fix as MeshAnchor 75f172db.
pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from gateway.mqtt_downlink_inject import (
    DownlinkInjector,
    build_downlink_envelope,
    channel_hash,
    _HAS_DOWNLINK_DEPS,
)

# moc ch2 "meshforge" PSK (test vector — matches the live radio used in step 0)
PSK_B64 = "SlVxOEZEZWhqencwR0NCOWlWdGJkSTVZdWY5aUIwblY="
PSK = base64.b64decode(PSK_B64)
ORIGIN = 0xDDFB8065  # moc2

pytestmark = pytest.mark.skipif(
    not _HAS_DOWNLINK_DEPS, reason="downlink deps (protobuf/crypto/paho) not installed"
)


def _decrypt(packet_id, from_node, ciphertext):
    nonce = struct.pack("<QI", packet_id, from_node) + b"\x00" * 4
    dec = Cipher(algorithms.AES(PSK), modes.CTR(nonce)).decryptor()
    return dec.update(ciphertext) + dec.finalize()


class TestChannelHash:
    def test_meshforge_hash_is_pinned_value(self):
        # Step-0 accepted value — do not change without re-proving on a radio.
        assert channel_hash("meshforge", PSK) == 0x7A

    def test_hash_is_single_byte(self):
        assert 0 <= channel_hash("anything", PSK) <= 0xFF


class TestBuildDownlinkEnvelope:
    def test_topic_and_attribution(self):
        topic, payload, pid = build_downlink_envelope(
            "meshforge", PSK, ORIGIN, "hello", packet_id=0x58581914)
        assert topic == "msh/2/e/meshforge/!ddfb8065"
        assert pid == 0x58581914
        assert len(payload) > 0

    def test_envelope_fields(self):
        from meshtastic.protobuf import mqtt_pb2
        _, payload, _ = build_downlink_envelope(
            "meshforge", PSK, ORIGIN, "hello", packet_id=0x1234)
        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(payload)
        assert getattr(env.packet, "from") == ORIGIN          # TRUE origin
        assert env.gateway_id == "!ddfb8065"
        assert env.channel_id == "meshforge"
        assert env.packet.channel == 0x7A
        assert env.packet.to == 0xFFFFFFFF

    def test_payload_round_trips(self):
        from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2
        _, payload, pid = build_downlink_envelope(
            "meshforge", PSK, ORIGIN, "round trip text", packet_id=0xABCD)
        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(payload)
        plain = _decrypt(pid, ORIGIN, env.packet.encrypted)
        data = mesh_pb2.Data()
        data.ParseFromString(plain)
        assert data.portnum == portnums_pb2.PortNum.TEXT_MESSAGE_APP
        assert data.payload.decode() == "round trip text"

    def test_random_packet_id_high_bit(self):
        _, _, pid = build_downlink_envelope("meshforge", PSK, ORIGIN, "x")
        assert pid & 0x40000000

    def test_rejects_wrong_psk_length(self):
        with pytest.raises(ValueError):
            build_downlink_envelope("meshforge", b"tooshort", ORIGIN, "x")


class TestDownlinkInjectorGuards:
    """The injector must degrade loudly to 'unusable' (caller falls back to
    toradio) rather than silently no-op, on bad config."""

    def test_bad_psk_length_unusable(self):
        inj = DownlinkInjector("localhost", 1883, "meshforge",
                               base64.b64encode(b"short").decode())
        assert not inj.usable
        assert "32 bytes" in inj.fatal_reason
        assert inj.inject("x", ORIGIN) is False

    def test_empty_psk_unusable(self):
        inj = DownlinkInjector("localhost", 1883, "meshforge", "")
        assert not inj.usable
        assert inj.inject("x", ORIGIN) is False

    def test_valid_psk_usable(self):
        inj = DownlinkInjector("localhost", 1883, "meshforge", PSK_B64)
        assert inj.usable
        assert inj.fatal_reason is None

    def test_inject_returns_false_when_broker_unreachable(self):
        # Port 1 → connect fails; inject must return False, never raise.
        inj = DownlinkInjector("127.0.0.1", 1, "meshforge", PSK_B64)
        assert inj.usable
        assert inj.inject("hello", ORIGIN) is False


class TestBuildNodeInfoEnvelope:
    def test_topic_and_attribution(self):
        from gateway.mqtt_downlink_inject import build_nodeinfo_envelope
        topic, payload, pid = build_nodeinfo_envelope(
            "meshforge", PSK, ORIGIN, "meshforge moc2", "moc2", packet_id=0x11)
        assert topic == "msh/2/e/meshforge/!ddfb8065"
        assert pid == 0x11
        assert len(payload) > 0

    def test_decrypts_to_user_with_names(self):
        from gateway.mqtt_downlink_inject import build_nodeinfo_envelope
        from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2
        _, payload, pid = build_nodeinfo_envelope(
            "meshforge", PSK, ORIGIN, "meshforge moc2", "moc2",
            hw_model="PORTDUINO", packet_id=0x22)
        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(payload)
        assert getattr(env.packet, "from") == ORIGIN
        plain = _decrypt(pid, ORIGIN, env.packet.encrypted)
        data = mesh_pb2.Data()
        data.ParseFromString(plain)
        assert data.portnum == portnums_pb2.PortNum.NODEINFO_APP
        user = mesh_pb2.User()
        user.ParseFromString(data.payload)
        assert user.id == "!ddfb8065"
        assert user.long_name == "meshforge moc2"
        assert user.short_name == "moc2"

    def test_unknown_hw_model_tolerated(self):
        from gateway.mqtt_downlink_inject import build_nodeinfo_envelope
        # must not raise on a hw model the enum doesn't have
        topic, payload, _ = build_nodeinfo_envelope(
            "meshforge", PSK, ORIGIN, "n", "n", hw_model="NOT_A_REAL_MODEL")
        assert payload


class TestInjectNodeInfoGuards:
    def test_unusable_injector_returns_false(self):
        from gateway.mqtt_downlink_inject import DownlinkInjector
        inj = DownlinkInjector("localhost", 1883, "meshforge", "")  # no psk
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is False

    def test_broker_unreachable_returns_false(self):
        from gateway.mqtt_downlink_inject import DownlinkInjector
        inj = DownlinkInjector("127.0.0.1", 1, "meshforge", PSK_B64)
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is False
