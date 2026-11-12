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

# HISTORICAL test vector — this WAS the live ch2 "meshforge" PSK during the
# step-0 field proof (2026-06-03), which is what makes the pinned wire-format
# assertions below (channel_hash == 0x7a etc.) meaningful. The fleet channel
# was ROTATED OFF this key on 2026-06-04 precisely because committing it here
# published it; the key below decrypts nothing on the air anymore.
# RULE (learned the hard way): never use a LIVE secret as a test vector —
# commit a synthetic one, or accept that the secret is burned the moment it
# lands in a public repo and rotate it.
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


@pytest.fixture(autouse=True)
def _allow_fake_brokers():
    """Every broker in this file is a deliberately-unreachable local target
    (port 1, or 1883 with no broker running) — the tests assert that inject()
    returns False rather than raising. The RF egress guard cannot tell a fake
    broker from the real one, so the intent is declared here."""
    from utils import tx_guard
    with tx_guard.allow_targets("127.0.0.1:1", "127.0.0.1:1883"):
        yield


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


# ---------------------------------------------------------------------------
# #25 (2026-11-12): inject() True must mean the broker ACCEPTED the session.
# Measured before the fix (real paho 2.1.0, loopback): a broker that accepts
# TCP but never sends CONNACK — a fake, and a real mosquitto under SIGSTOP —
# made inject() return True on every call while the broker had accepted
# nothing; mesh_bridge then marks the row `delivered` and skips the toradio
# fallback, and the RNS->mesh true-origin path arms the loop guard on it.
# These tests drive the REAL paho client against a fake broker socket.
# ---------------------------------------------------------------------------

import socket
import threading
import time


class _FakeBroker:
    """One-connection MQTT broker stub on 127.0.0.1.

    mode: "ok"     CONNACK rc 0 to every CONNECT
          "silent" accept TCP, never CONNACK (a wedged broker)
          "drop"   CONNACK rc 0, then close the socket after the first PUBLISH
    """

    def __init__(self, mode):
        self.mode = mode
        self.packet_types = []
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self._srv.settimeout(0.2)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    b = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not b:
                    break
                ptype = b[0] >> 4
                self.packet_types.append(ptype)
                if ptype == 1 and self.mode in ("ok", "drop"):
                    conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                if ptype == 3 and self.mode == "drop":
                    break
            conn.close()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self._srv.close()


@pytest.fixture
def fake_broker():
    """Factory: a fake broker plus a tx_guard grant for its ephemeral port
    (the guard cannot tell a fake broker from the real one)."""
    import contextlib
    from utils import tx_guard
    with contextlib.ExitStack() as stack:
        def make(mode):
            b = _FakeBroker(mode)
            stack.callback(b.close)
            stack.enter_context(tx_guard.allow_targets(f"127.0.0.1:{b.port}"))
            return b
        yield make


def _injector(port, monkeypatch):
    from gateway import mqtt_downlink_inject as mdi
    # Keep every test far under CI's 30 s per-test cap.
    monkeypatch.setattr(mdi, "CONNACK_TIMEOUT_S", 1.0, raising=False)
    return DownlinkInjector("127.0.0.1", port, "meshforge", PSK_B64)


class TestInjectRequiresAcceptedSession:
    """#25 — True only after CONNACK rc 0; a lost session is noticed."""

    def test_control_ok_broker_true_and_publish_reaches_broker(self, fake_broker, monkeypatch):
        b = fake_broker("ok")
        inj = _injector(b.port, monkeypatch)
        assert inj.inject("hello", ORIGIN) is True
        deadline = time.monotonic() + 2
        while 3 not in b.packet_types and time.monotonic() < deadline:
            time.sleep(0.02)
        assert 3 in b.packet_types  # a PUBLISH reached the broker

    def test_silent_broker_no_connack_is_false(self, fake_broker, monkeypatch):
        b = fake_broker("silent")
        inj = _injector(b.port, monkeypatch)
        t0 = time.monotonic()
        assert inj.inject("hello", ORIGIN) is False
        assert time.monotonic() - t0 < 5   # bounded by CONNACK_TIMEOUT_S
        assert 3 not in b.packet_types     # nothing published into the void
        assert inj.inject("again", ORIGIN) is False  # still no session

    def test_silent_broker_nodeinfo_is_false(self, fake_broker, monkeypatch):
        b = fake_broker("silent")
        inj = _injector(b.port, monkeypatch)
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is False

    def test_session_drop_is_noticed_before_next_publish(self, fake_broker, monkeypatch):
        # Broker closes the session after the first PUBLISH; the stub serves
        # one CONNECT per accept, so a reconnect gets a fresh CONNACK.
        b = fake_broker("drop")
        inj = _injector(b.port, monkeypatch)
        assert inj.inject("one", ORIGIN) is True
        deadline = time.monotonic() + 2
        while inj._connected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert inj._connected is False  # the drop cleared the flag
