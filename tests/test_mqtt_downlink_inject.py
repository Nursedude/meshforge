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

import contextlib
import socket
import threading
import time


def _split_mqtt(buf):
    """Split a byte buffer into complete MQTT packets: [(type, body)], rest.

    Real framing (fixed header + varint remaining length), so packets that
    share one recv() are each counted (review r1 F5)."""
    out = []
    while len(buf) >= 2:
        mult, length, i = 1, 0, 1
        while True:
            if i >= len(buf):
                return out, buf
            byte = buf[i]
            length += (byte & 0x7F) * mult
            mult *= 128
            i += 1
            if not byte & 0x80:
                break
        if len(buf) < i + length:
            return out, buf
        out.append((buf[0] >> 4, buf[i:i + length]))
        buf = buf[i + length:]
    return out, buf


class _FakeBroker:
    """MQTT broker stub on 127.0.0.1, one connection at a time.

    mode: "ok"          CONNACK rc 0 to every CONNECT
          "silent"      accept TCP, never CONNACK (a wedged broker)
          "drop"        CONNACK rc 0; close the session after the first PUBLISH;
                        later sessions behave like "ok"
          "drop_silent" like "drop", but every LATER session is "silent"
          "refuse"      CONNACK rc 5 (not authorized) — bad credentials
          "mute"        CONNACK rc 0, then never answer again (no PINGRESP):
                        a broker that wedges AFTER accepting the session
    """

    def __init__(self, mode):
        self.mode = mode
        self.packet_types = []
        self.sessions = 0
        self.stop_reading = False   # True: leave bytes unread (fill the socket)
        self._kick = False          # kick(): close the live session once
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self._srv.settimeout(0.1)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def count(self, ptype):
        return self.packet_types.count(ptype)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            self.sessions += 1
            first = self.sessions == 1
            mode = self.mode
            if mode == "drop_silent":
                mode = "drop" if first else "silent"
            elif mode == "drop" and not first:
                mode = "ok"
            self._session(conn, mode)

    def _session(self, conn, mode):
        conn.settimeout(0.1)
        buf = b""
        with conn:
            while not self._stop.is_set():
                if self._kick:
                    self._kick = False
                    return                  # with conn: closes the socket
                if self.stop_reading:
                    time.sleep(0.05)
                    continue
                try:
                    b = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not b:
                    return
                pkts, buf = _split_mqtt(buf + b)
                for ptype, _body in pkts:
                    self.packet_types.append(ptype)
                    if ptype == 1 and mode == "refuse":
                        conn.sendall(bytes([0x20, 0x02, 0x00, 0x05]))
                    if ptype == 1 and mode in ("ok", "drop", "mute"):
                        conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                    if ptype == 12 and mode not in ("silent", "mute"):   # PINGREQ
                        conn.sendall(bytes([0xD0, 0x00]))
                    if ptype == 3 and mode == "drop":
                        return

    def kick(self):
        """Drop the current session from the broker side."""
        self._kick = True

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self._srv.close()


def _until(pred, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture
def fake_broker(monkeypatch):
    """Factory: make(mode) -> (broker, injector). Grants tx_guard for the
    ephemeral port, shortens the timeouts far under CI's 30 s cap, and
    closes every injector before its broker (review r1 F6)."""
    from gateway import mqtt_downlink_inject as mdi
    from utils import tx_guard
    monkeypatch.setattr(mdi, "CONNACK_TIMEOUT_S", 1.0)
    monkeypatch.setattr(mdi, "PUBLISH_TIMEOUT_S", 0.5)
    monkeypatch.setattr(mdi, "CONNECT_RETRY_S", 30.0)
    with contextlib.ExitStack() as stack:
        def make(mode):
            b = _FakeBroker(mode)
            stack.callback(b.close)
            stack.enter_context(tx_guard.allow_targets(f"127.0.0.1:{b.port}"))
            inj = DownlinkInjector("127.0.0.1", b.port, "meshforge", PSK_B64)
            stack.callback(inj.close)
            return b, inj
        yield make


class TestInjectRequiresAcceptedSession:
    """#25 — True only for an accepted session and a written publish."""

    def test_control_ok_broker_true_and_publish_reaches_broker(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("hello", ORIGIN) is True
        assert _until(lambda: b.count(3) == 1)
        assert inj.connected is True

    def test_silent_broker_no_connack_is_false(self, fake_broker):
        b, inj = fake_broker("silent")
        t0 = time.monotonic()
        assert inj.inject("hello", ORIGIN) is False
        assert time.monotonic() - t0 < 5   # bounded by CONNACK_TIMEOUT_S
        time.sleep(0.3)
        assert b.count(1) == 1             # it did try: one CONNECT
        assert b.count(3) == 0             # nothing published into the void
        assert inj.connected is False

    def test_silent_broker_nodeinfo_is_false(self, fake_broker):
        b, inj = fake_broker("silent")
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is False
        assert b.count(3) == 0

    def test_failed_connect_refuses_fast_without_reconnecting(self, fake_broker):
        # r1 F2: nodeinfo + text per message must not cost two CONNACK waits.
        b, inj = fake_broker("silent")
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is False
        t0 = time.monotonic()
        assert inj.inject("hello", ORIGIN) is False
        assert time.monotonic() - t0 < 0.3
        time.sleep(0.3)
        assert b.sessions == 1             # no second connect attempt

    def test_retry_window_expiry_reconnects(self, fake_broker, monkeypatch):
        from gateway import mqtt_downlink_inject as mdi
        b, inj = fake_broker("drop_silent")
        assert inj.inject("one", ORIGIN) is True       # session 1, then dropped
        assert _until(lambda: not inj.connected)
        monkeypatch.setattr(mdi, "CONNECT_RETRY_S", 0.0)
        b.mode = "ok"                                    # broker recovers
        assert _until(lambda: inj.inject("two", ORIGIN), t=5)
        assert _until(lambda: b.count(3) >= 2)

    def test_session_drop_then_wedged_broker_is_false(self, fake_broker):
        # r1 F7: deterministic — after the drop, no session can be accepted,
        # so neither paho's auto-reconnect nor ours can read connected.
        b, inj = fake_broker("drop_silent")
        assert inj.inject("one", ORIGIN) is True
        assert _until(lambda: not inj.connected)
        assert inj.inject("two", ORIGIN) is False
        assert b.count(3) == 1

    def test_session_drop_reconnects_on_next_inject(self, fake_broker):
        b, inj = fake_broker("drop")
        assert inj.inject("one", ORIGIN) is True
        assert _until(lambda: b.sessions >= 1 and b.count(3) == 1)
        assert _until(lambda: inj.inject("two", ORIGIN), t=5)
        assert _until(lambda: b.count(3) == 2)
        assert b.sessions == 2

    def test_publish_not_written_within_timeout_is_false(self, fake_broker, monkeypatch):
        # r1 F1: CONNACK'd, then the message stays queued in the client.
        # wait_for_publish RETURNS on timeout; True would be the #25 class.
        b, inj = fake_broker("ok")
        assert inj.inject("warm", ORIGIN) is True
        client = inj._client
        real_publish = client.publish

        def queued_never_written(*a, **k):
            info = real_publish("meshforge/unused", b"", qos=0)
            info.wait_for_publish(timeout=1)
            info._published = False        # the real class, held unwritten
            info._condition = threading.Condition()
            return info
        monkeypatch.setattr(client, "publish", queued_never_written)
        assert inj.inject("stalled", ORIGIN) is False
        assert inj._client is None         # torn down: next call reconnects


def _paho_threads():
    return sum(1 for t in threading.enumerate()
               if t.name.startswith("paho-mqtt-client-") and t.is_alive())


class TestInjectorLifecycle:
    """#25 round 2 (Fable by running): the paths r1 left unpinned —
    refusal, the retry window both ways, thread/teardown hygiene, close(),
    a builder error, and a broker that wedges AFTER CONNACK."""

    def test_refused_session_is_false_and_refuses_fast(self, fake_broker, caplog):
        b, inj = fake_broker("refuse")
        with caplog.at_level("WARNING"):
            assert inj.inject("hello", ORIGIN) is False
            assert inj.inject("again", ORIGIN) is False
        assert b.count(3) == 0
        assert b.sessions == 1                      # 2nd call inside the window
        assert "CONNACK rc 5" in caplog.text

    def test_retry_window_holds_then_expires(self, fake_broker, monkeypatch):
        # The window must be OPENED and EXPIRE through the code's own clock —
        # writing _retry_after by hand let "never expires" survive (m18).
        from gateway import mqtt_downlink_inject as mdi
        monkeypatch.setattr(mdi, "CONNECT_RETRY_S", 0.5)
        b, inj = fake_broker("silent")
        assert inj.inject("one", ORIGIN) is False   # opens the window
        b.mode = "ok"                               # broker recovers
        assert inj.inject("two", ORIGIN) is False   # still inside the window
        assert b.sessions == 1
        time.sleep(0.6)                             # window expires
        assert inj.inject("three", ORIGIN) is True
        assert _until(lambda: b.count(3) == 1)

    def test_no_paho_thread_survives_a_failed_connect(self, fake_broker):
        base = _paho_threads()
        b, inj = fake_broker("silent")
        assert inj.inject("x", ORIGIN) is False
        assert _until(lambda: _paho_threads() == base, t=2)

    def test_reconnect_keeps_one_thread_and_close_leaves_none(self, fake_broker):
        base = _paho_threads()
        b, inj = fake_broker("drop")
        assert inj.inject("one", ORIGIN) is True
        assert _until(lambda: not inj.connected)
        assert _until(lambda: inj.inject("two", ORIGIN), t=5)
        assert _paho_threads() <= base + 1
        inj.close()
        assert _until(lambda: _paho_threads() == base, t=2)
        assert inj._client is None

    def test_close_disconnects_cleanly(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        inj.close()
        assert _until(lambda: b.count(14) == 1)     # DISCONNECT reached the broker

    def test_builder_error_keeps_the_healthy_session(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        client = inj._client
        assert inj._publish("text", ORIGIN, lambda: 1 / 0) is False
        assert inj._client is client and inj.connected   # not torn down
        assert inj.inject("two", ORIGIN) is True
        assert b.sessions == 1

    def test_teardown_is_bounded_with_unwritten_output(self, fake_broker):
        # Fable r2 F2: paho's loop thread exits only once its output queue
        # drains, so loop_stop() joined until keepalive closed the socket.
        b, inj = fake_broker("mute")
        assert inj.inject("one", ORIGIN) is True
        b.stop_reading = True
        inj._client.publish("meshforge/bulk", b"\0" * (32 << 20), qos=0)
        t0 = time.monotonic()
        inj.close()
        assert time.monotonic() - t0 < 3

    def test_wedge_after_connack_is_noticed_within_two_keepalives(self, fake_broker, monkeypatch):
        # Fable r2 F1, measured on real mosquitto: True for 60 s at keepalive
        # 30. The window is 2 x KEEPALIVE_S; the knob must reach connect().
        from gateway import mqtt_downlink_inject as mdi
        monkeypatch.setattr(mdi, "KEEPALIVE_S", 1)
        b, inj = fake_broker("mute")
        assert inj.inject("one", ORIGIN) is True
        b.mode = "silent"     # a wedged broker answers no NEW session either
        t0 = time.monotonic()
        assert _until(lambda: not inj.connected, t=4)
        assert time.monotonic() - t0 < 2 * 1 + 1.5   # 2 x KEEPALIVE_S + slack
        assert inj.inject("two", ORIGIN) is False


class TestInjectorCloseAndPins:
    """#25 round 3 (Fable by running): close() is terminal until reopen();
    teardown always stops paho's thread; the production keepalive is pinned."""

    def test_inject_after_close_does_not_reconnect(self, fake_broker):
        # r3 F1: an inject queued behind close() resurrected a session that
        # nobody would close (mesh_bridge.stop closes before joining threads).
        base = _paho_threads()
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        inj.close()
        assert inj.inject("queued", ORIGIN) is False
        time.sleep(0.3)
        assert b.sessions == 1
        assert _until(lambda: _paho_threads() == base, t=2)

    def test_inject_waiting_on_the_lock_during_close_is_refused(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        result = {}
        with inj._lock:                       # close() body runs while...
            t = threading.Thread(target=lambda: result.update(
                r=inj.inject("queued", ORIGIN)))
            t.start()                         # ...an inject waits on the lock
            time.sleep(0.2)
            inj._closed = True                # what close() sets under the lock
            inj._teardown()
        t.join(5)
        assert result["r"] is False
        assert b.sessions == 1

    def test_reopen_after_close_reconnects(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        inj.close()
        inj.reopen()
        assert inj.inject("two", ORIGIN) is True
        assert _until(lambda: b.sessions == 2 and b.count(3) == 2)

    def test_teardown_stops_paho_thread_even_if_disconnect_raises(self, fake_broker):
        # r3 F2: one try around disconnect/shutdown/loop_stop let a raise skip
        # loop_stop, leaving an orphan auto-reconnecting under our client_id.
        base = _paho_threads()
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        client = inj._client

        def boom(*a, **k):
            raise RuntimeError("injected disconnect fault")
        client.disconnect = boom
        inj.close()
        assert _until(lambda: _paho_threads() == base, t=3)
        assert client.is_connected() is False

    def test_production_keepalive_is_the_measured_value(self):
        # r3 F3: the stall window (~2 x KEEPALIVE_S, measured on real
        # mosquitto 11-12) is pinned to the value it was measured at. Changing
        # it means re-running the SIGSTOP-after-CONNACK measurement and
        # recording the new window, then updating this pin.
        from gateway import mqtt_downlink_inject as mdi
        assert mdi.KEEPALIVE_S == 5

    def test_reopen_clears_the_retry_window(self, fake_broker):
        # r4 I1: reopen() must also end a refuse-fast window, or a restart
        # right after a failed connect hides a healthy broker for 30 s.
        b, inj = fake_broker("silent")
        assert inj.inject("one", ORIGIN) is False   # opens the 30 s window
        b.mode = "ok"
        inj.close()
        inj.reopen()
        assert inj.inject("two", ORIGIN) is True

    def test_teardown_stops_paho_thread_when_disconnect_and_shutdown_raise(
            self, fake_broker, monkeypatch):
        # r4 I2: pins the finally — with only disconnect faulted, shutdown
        # succeeds and loop_stop ran either way.
        import socket as _socket
        base = _paho_threads()
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        client = inj._client

        def boom(*a, **k):
            raise RuntimeError("injected fault")
        client.disconnect = boom
        monkeypatch.setattr(_socket.socket, "shutdown", boom)
        inj.close()
        monkeypatch.undo()
        assert _until(lambda: _paho_threads() == base, t=3)


class TestDownlinkCounters:
    """#25 counters slice (operator 11-12: "anything that stalls is not an
    outcome - it's something to measure"). Every call lands in EXACTLY one
    named counter; a lost session yields an upper-bound stall witness."""

    @staticmethod
    def _counts(inj):
        return {k: v for k, v in inj.stats()["counts"].items() if v}

    def test_ok_counts_ok(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        assert inj.inject_nodeinfo(ORIGIN, "long", "sh") is True
        assert self._counts(inj) == {"ok": 2}
        assert inj.stats()["calls"] == 2

    def test_no_connack_then_refused_fast(self, fake_broker):
        b, inj = fake_broker("silent")
        assert inj.inject("one", ORIGIN) is False
        assert inj.inject("two", ORIGIN) is False
        assert self._counts(inj) == {"no_connack": 1, "refused_fast": 1}

    def test_refused_session(self, fake_broker):
        b, inj = fake_broker("refuse")
        assert inj.inject("one", ORIGIN) is False
        assert self._counts(inj) == {"refused": 1}

    def test_connect_error(self, monkeypatch):
        from utils import tx_guard
        with tx_guard.allow_targets("127.0.0.1:1"):
            inj = DownlinkInjector("127.0.0.1", 1, "meshforge", PSK_B64)
            try:
                assert inj.inject("one", ORIGIN) is False
                assert self._counts(inj) == {"connect_error": 1}
            finally:
                inj.close()

    def test_closed(self, fake_broker):
        b, inj = fake_broker("ok")
        inj.close()
        assert inj.inject("one", ORIGIN) is False
        assert self._counts(inj) == {"closed": 1}

    def test_build_error(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj._publish("text", ORIGIN, lambda: 1 / 0) is False
        assert self._counts(inj) == {"build_error": 1}

    def test_unusable(self):
        inj = DownlinkInjector("127.0.0.1", 1, "meshforge", "")
        assert inj.inject("one", ORIGIN) is False
        assert self._counts(inj) == {"unusable": 1}

    def test_not_written(self, fake_broker, monkeypatch):
        b, inj = fake_broker("ok")
        assert inj.inject("warm", ORIGIN) is True
        client = inj._client
        real_publish = client.publish

        def queued_never_written(*a, **k):
            info = real_publish("meshforge/unused", b"", qos=0)
            info.wait_for_publish(timeout=1)
            info._published = False
            info._condition = threading.Condition()
            return info
        monkeypatch.setattr(client, "publish", queued_never_written)
        assert inj.inject("stalled", ORIGIN) is False
        assert self._counts(inj) == {"ok": 1, "not_written": 1}

    def test_every_outcome_has_a_test_above(self):
        # Closed enum, closed consumer (honest_failure_modes #7): a new
        # outcome must come with its own test here.
        from gateway.mqtt_downlink_inject import DOWNLINK_OUTCOMES
        tested = {"ok", "no_connack", "refused", "connect_error", "not_written",
                  "refused_fast", "closed", "build_error", "unusable", "tx_blocked"}
        assert set(DOWNLINK_OUTCOMES) == tested

    def test_tx_blocked(self, fake_broker):
        from utils import tx_guard
        from unittest.mock import patch as _patch
        b, inj = fake_broker("ok")
        with _patch("gateway.mqtt_downlink_inject.assert_tx_allowed",
                    side_effect=tx_guard.TransmitBlocked({"target": "x"})):
            assert inj.inject("one", ORIGIN) is False
        assert self._counts(inj) == {"tx_blocked": 1}

    def test_stall_witness_counts_sends_inside_the_window(self, fake_broker, monkeypatch):
        # Broker accepts, then goes mute (no PINGRESP): paho drops the session
        # at ~2 x KEEPALIVE_S. Both sends returned True inside the window.
        from gateway import mqtt_downlink_inject as mdi
        monkeypatch.setattr(mdi, "KEEPALIVE_S", 1)
        b, inj = fake_broker("mute")
        assert inj.inject("one", ORIGIN) is True
        assert inj.inject("two", ORIGIN) is True
        b.mode = "silent"
        assert _until(lambda: inj.stats()["sessions_lost"] == 1, t=5)
        time.sleep(2.5)     # paho retries the silent broker meanwhile
        st = inj.stats()
        assert st["sessions_lost"] == 1      # retries are not lost sessions
        assert st["possibly_lost"] == 2
        assert st["window_s"] == 2 * 1 + 2.5

    def test_control_clean_close_is_not_a_lost_session(self, fake_broker):
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        inj.close()
        time.sleep(0.3)
        st = inj.stats()
        assert st["sessions_lost"] == 0 and st["possibly_lost"] == 0

    def test_sends_older_than_the_window_are_not_counted(self, fake_broker, monkeypatch):
        from gateway import mqtt_downlink_inject as mdi
        monkeypatch.setattr(mdi, "KEEPALIVE_S", 1)
        b, inj = fake_broker("ok")                  # answers PINGREQ: stays up
        assert inj.inject("old", ORIGIN) is True
        time.sleep(mdi.stall_window_s() + 0.5)      # older than the window
        b.mode = "silent"
        b.kick()                                    # broker drops the session
        assert _until(lambda: inj.stats()["sessions_lost"] == 1, t=5)
        assert inj.stats()["possibly_lost"] == 0


class TestDownlinkStatusLine:
    """Writer (bridge_cli status block) and reader (TUI) share ONE format."""

    def test_line_round_trips_through_the_shared_regex(self, fake_broker):
        from gateway.mqtt_downlink_inject import DOWNLINK_LINE_RE, format_downlink_line
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        line = format_downlink_line({"label": "LONG_FAST", **inj.stats()})
        m = DOWNLINK_LINE_RE.search("    " + line)
        assert m and m.group("label") == "LONG_FAST"
        assert "session up" in m.group("body") and "ok 1" in m.group("body")
        assert "<=0 possibly lost" in m.group("body")

    def test_idle_injector_says_idle_not_zero(self):
        from gateway.mqtt_downlink_inject import DOWNLINK_LINE_RE, format_downlink_line
        line = format_downlink_line({"label": "rns", "idle": True})
        m = DOWNLINK_LINE_RE.search(line)
        assert m and "idle" in m.group("body") and "ok 0" not in line


class TestDownlinkStatusWriters:
    """The gateway's ~30 s status block carries the line (single + multi)."""

    def test_print_status_prints_the_downlink_line(self, capsys):
        from gateway.bridge_cli import print_status
        print_status({"running": True,
                      "downlink": {"label": "rns", "idle": True}})
        assert "Downlink[rns]: idle" in capsys.readouterr().out

    def test_print_multi_status_prints_each_bridges_line(self, capsys):
        from unittest.mock import MagicMock
        from gateway.bridge_cli import print_multi_status
        a, b = MagicMock(), MagicMock()
        a.get_status.return_value = {"running": True, "downlink": {
            "label": "mesh-LONG_FAST", "counts": {"ok": 2}, "connected": True}}
        b.get_status.return_value = {"running": True, "downlink": None}
        print_multi_status([a, b])
        out = capsys.readouterr().out
        assert "    Downlink[mesh-LONG_FAST]: session up · lost-sessions 0" in out
        assert "ok 2" in out
        assert out.count("Downlink[") == 1

    def test_downlink_status_helper_states(self):
        from unittest.mock import MagicMock
        from gateway.mqtt_downlink_inject import downlink_status
        assert downlink_status("x", None, configured=False, built=True) is None
        assert downlink_status("x", None, configured=True, built=False) == {"label": "x", "idle": True}
        assert downlink_status("x", None, configured=True, built=True) == {"label": "x", "unavailable": True}
        inj = MagicMock(); inj.stats.return_value = {"calls": 1}
        assert downlink_status("x", inj, configured=True, built=True) == {"label": "x", "calls": 1}


class TestDownlinkCountersRound1:
    """Counters slice, non-author round 1 (Fable by running), each finding."""

    def test_window_covers_the_measured_detection_delay(self):
        # F1: noticed up to 11.9 s after CONNACK at K=5 on real mosquitto;
        # 2K+1 = 11 undercounted 3/3. The window must cover 2K + 2 (two
        # 1 s loop-tick checks), with margin.
        from gateway import mqtt_downlink_inject as mdi
        assert mdi.stall_window_s() >= 2 * mdi.KEEPALIVE_S + 2
        assert mdi.KEEPALIVE_S == 5 and mdi.stall_window_s() >= 11.9

    def test_teardown_disarms_the_witness(self, fake_broker):
        # F3: ok session, close(), reopen(), a failed connect -> must NOT be
        # a lost session (both sends were delivered).
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        inj.close()
        assert inj._witness_up is False
        inj.reopen()
        b.mode = "silent"
        assert inj.inject("two", ORIGIN) is False
        time.sleep(0.5)
        assert inj.stats()["sessions_lost"] == 0

    def test_publish_that_discovers_the_dead_session_counts_it(self, fake_broker, monkeypatch):
        # F4: not_written tears down before paho's callback can see the
        # current client — the publish path must count the loss itself.
        b, inj = fake_broker("ok")
        assert inj.inject("warm", ORIGIN) is True
        client = inj._client
        real_publish = client.publish

        def queued_never_written(*a, **k):
            # The session is GONE (paho says so) and no callback fires: only
            # the publish path can notice. (r2: a healthy-session failure is
            # deliberately NOT a lost session — see the round-2 F2 test.)
            info = real_publish("meshforge/unused", b"", qos=0)
            info.wait_for_publish(timeout=1)
            info._published = False
            info._condition = threading.Condition()
            monkeypatch.setattr(client, "is_connected", lambda: False)
            return info
        monkeypatch.setattr(client, "publish", queued_never_written)
        assert inj.inject("stalled", ORIGIN) is False
        st = inj.stats()
        # warm send = possibly lost; the stalled one returned False (the caller
        # falls back), so it is withdrawn from the books.
        assert st["sessions_lost"] == 1 and st["possibly_lost"] == 1

    def test_callback_guards_each_pinned(self, fake_broker):
        # F8 M6/M7/M19: every guard on the witness callbacks, alone.
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        client = inj._client
        on_disc, on_conn = client.on_disconnect, client.on_connect
        on_disc(object(), None, 7)                      # a STALE client's loss
        assert inj.stats()["sessions_lost"] == 0        # M6
        on_disc(client, None, 0)                        # our own clean disconnect
        assert inj.stats()["sessions_lost"] == 0        # M7
        assert inj._witness_up is True
        on_disc(client, None, 7)                        # a real loss
        assert inj.stats()["sessions_lost"] == 1
        on_conn(client, None, {}, 5)                    # a REFUSED session
        assert inj._witness_up is False                 # M19: not armed
        on_disc(client, None, 7)
        assert inj.stats()["sessions_lost"] == 1

    def test_one_send_is_never_counted_twice(self, fake_broker):
        # F8 M4: two losses in a row; the sends before the first count once.
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        assert inj.inject("two", ORIGIN) is True
        inj._note_session_lost()
        inj._note_session_lost()
        assert inj.stats()["possibly_lost"] == 2

    def test_line_carries_the_real_witness_numbers(self):
        # F8 M27 + F7: the numbers are the stats', and the state word is not
        # a counter's name.
        from gateway.mqtt_downlink_inject import format_downlink_line
        line = format_downlink_line({"label": "rns", "counts": {"ok": 9}, "connected": False,
                                     "sessions_lost": 2, "possibly_lost": 7, "window_s": 12.5})
        assert "lost-sessions 2 · <=7 possibly lost" in line
        assert line.index("lost-sessions") < line.index("ok 9")     # witness first
        closed = format_downlink_line({"label": "rns", "closed": True, "counts": {}})
        assert "]: injector closed · " in closed

    def test_state_word_follows_the_session(self, fake_broker):
        # F8 M26/M28: the state word comes from paho's real session state.
        from gateway.mqtt_downlink_inject import format_downlink_line
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        assert "]: session up · " in format_downlink_line({"label": "x", **inj.stats()})
        b2, inj2 = fake_broker("silent")
        assert inj2.inject("one", ORIGIN) is False
        assert "]: no session · " in format_downlink_line({"label": "x", **inj2.stats()})

    def test_unavailable_line_is_clean_text(self):
        # F5 + F8 M9: the "unavailable" branch, and no mojibake in it.
        from gateway.mqtt_downlink_inject import DOWNLINK_LINE_RE, format_downlink_line
        line = format_downlink_line({"label": "rns", "unavailable": True})
        assert "unavailable \u2014 downlink is configured" in line
        assert all(ord(c) < 0x80 or ord(c) >= 0xa0 for c in line)   # no C1 controls
        assert DOWNLINK_LINE_RE.search(line)

    def test_odd_labels_round_trip(self):
        # F6: free-form presets survive writer -> regex -> reader; no preset
        # can collide with the RNS row.
        from gateway.mqtt_downlink_inject import (DOWNLINK_LINE_RE, downlink_label,
                                                  format_downlink_line)
        for raw in ("odd]preset", "A: B [x]", "with\nnewline", "", None, "LONG_FAST"):
            label = "mesh-" + downlink_label(raw)
            m = DOWNLINK_LINE_RE.search(format_downlink_line({"label": label, "counts": {}}))
            assert m and m.group("label") == label
        assert "mesh-" + downlink_label("rns") != "rns"


class TestDownlinkCountersRound2:
    """Counters slice, non-author round 2 (Fable by running)."""

    def test_send_in_flight_when_the_loss_is_noted_is_counted(self, fake_broker, monkeypatch):
        # F1 HIGH: paho's _loop runs loop_write() then loop_misc() in ONE pass,
        # so a publish can itself trigger the keepalive close — the loss
        # callback then runs BEFORE the caller records its True. The seam:
        # a send must be on the witness's books BEFORE it goes on the wire.
        b, inj = fake_broker("ok")
        assert inj.inject("warm", ORIGIN) is True
        client = inj._client
        real_publish = client.publish

        def publish_then_keepalive_drop(*a, **k):
            info = real_publish(*a, **k)
            info.wait_for_publish(timeout=1)
            client.on_disconnect(client, None, 16)      # MQTT_ERR_KEEPALIVE, same pass
            return info
        monkeypatch.setattr(client, "publish", publish_then_keepalive_drop)
        assert inj.inject("in flight", ORIGIN) is True
        st = inj.stats()
        assert st["sessions_lost"] == 1
        assert st["possibly_lost"] == 2                  # warm + the in-flight send

    def test_publish_error_on_a_healthy_session_is_not_a_lost_session(self, fake_broker, monkeypatch):
        # F2: a client-side raise (bad topic, a bug) with the session still up
        # is not_written — never "lost session".
        b, inj = fake_broker("ok")
        for _ in range(4):
            assert inj.inject("ok", ORIGIN) is True
        client = inj._client

        def boom(*a, **k):
            raise ValueError("Publish topic cannot contain wildcards.")
        monkeypatch.setattr(client, "publish", boom)
        assert inj.inject("bad", ORIGIN) is False
        st = inj.stats()
        assert st["counts"]["not_written"] == 1
        assert st["sessions_lost"] == 0 and st["possibly_lost"] == 0

    def test_wildcard_channel_is_unusable_up_front(self):
        # F2: '#'/'+' in the channel make every topic invalid — refuse at
        # construction (loud, once) instead of failing every send.
        for ch in ("Long#Fast", "Long+Fast", "a/b"):
            inj = DownlinkInjector("127.0.0.1", 1883, ch, PSK_B64)
            assert not inj.usable and "channel" in inj.fatal_reason

    def test_window_holds_every_send_not_a_capped_tail(self, fake_broker):
        # suspected: deque(maxlen=1024) undercounted past 1024 sends in a window.
        b, inj = fake_broker("ok")
        for _ in range(1500):
            inj._note_send()
        inj._note_session_lost()
        assert inj.stats()["possibly_lost"] == 1500

    def test_unknown_outcome_maps_to_connect_error_and_calls_is_the_sum(self, fake_broker):
        # F7 m11/m12
        b, inj = fake_broker("ok")
        assert inj._count("no_such_outcome") is False
        assert inj.inject("one", ORIGIN) is True
        st = inj.stats()
        assert st["counts"]["connect_error"] == 1
        assert st["calls"] == sum(st["counts"].values()) == 2

    def test_label_fallback_and_sanitizing(self):
        # F7 m6
        from gateway.mqtt_downlink_inject import downlink_label
        assert downlink_label(None) == downlink_label("") == downlink_label("  ") == "primary"
        assert downlink_label("A: B [x]") == "A__B__x_"

    def test_on_connect_from_a_stale_client_does_not_arm(self, fake_broker):
        # F7 m20 + suspected race: only the CURRENT client's CONNACK arms.
        b, inj = fake_broker("ok")
        assert inj.inject("one", ORIGIN) is True
        client = inj._client
        client.on_disconnect(client, None, 7)           # real loss: disarms
        assert inj._witness_up is False
        client.on_connect(object(), None, {}, 0)        # a stale client's CONNACK
        assert inj._witness_up is False
