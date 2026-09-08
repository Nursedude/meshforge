"""Tests for utils.rnode_session — the RNode access chokepoint.

This module exists because a tool put two production radios into promiscuous
mode and walked away (2026-09-08). So the emphasis here is on the CONTRACT
rather than the happy path: the session must refuse a radio that will not come
up, must believe only an explicit state ASK, and must hand every mode back on
every exit path including exceptions.

Everything runs against a fake serial device — the KISS layer is pure by design
precisely so it can be tested without a radio on the bench.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.rnode_session import (  # noqa: E402
    KISS,
    KissDecoder,
    RNodeUnavailable,
    confirm_radio_on,
    kiss_cmd,
    radio_config_commands,
    rnode_session,
    u32,
)

K = KISS


def frame(command: int, payload: bytes) -> bytes:
    return bytes([K.FEND, command]) + payload + bytes([K.FEND])


def unescape(data: bytes) -> bytes:
    return data.replace(bytes([K.FESC, K.TFEND]), bytes([K.FEND])) \
               .replace(bytes([K.FESC, K.TFESC]), bytes([K.FESC]))


class FakePort:
    """Scripted RNode. Honours a buffer flush, which is load-bearing here."""

    def __init__(self, *, detect=True, ask_reply=0x01, fw=(1, 86), prequeued=b""):
        self.buffer = bytearray(prequeued)
        self.writes = []
        self.closed = False
        self.flushes = 0
        self._detect = detect
        self._ask_reply = ask_reply
        self._fw = fw

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if len(data) >= 3 and data[1] == K.CMD_DETECT and self._detect:
            self.buffer += frame(K.CMD_DETECT, bytes([K.DETECT_RESP]))
            self.buffer += frame(K.CMD_FW_VERSION, bytes(self._fw))
        if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE \
                and data[2] == K.RADIO_STATE_ASK and self._ask_reply is not None:
            self.buffer += frame(K.CMD_RADIO_STATE, bytes([self._ask_reply]))
        return len(data)

    def read(self, n):
        out = bytes(self.buffer[:n])
        del self.buffer[:n]
        return out

    def reset_input_buffer(self):
        self.flushes += 1
        self.buffer.clear()

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def promisc_writes(self):
        return [w for w in self.writes if len(w) >= 3 and w[1] == K.CMD_PROMISC]


class FakeSerialModule:
    PARITY_NONE = "N"

    def __init__(self, port: FakePort):
        self._port = port
        self.opened_with = None

    def Serial(self, **kwargs):
        self.opened_with = kwargs
        return self._port


class TestKissDecoder:
    def test_decodes_packet_with_stats(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_RSSI, bytes([-92 + K.RSSI_OFFSET])))
        d.feed(frame(K.CMD_STAT_SNR, (30).to_bytes(1, "big", signed=True)))
        d.feed(frame(K.CMD_DATA, b"hello"))
        (_, rssi, snr, n), = d.drain_packets()
        assert (rssi, snr, n) == (-92, 7.5, 5)

    def test_negative_snr_is_signed(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_SNR, (-49).to_bytes(1, "big", signed=True)))
        d.feed(frame(K.CMD_DATA, b"x"))
        assert d.drain_packets()[0][2] == pytest.approx(-12.25)

    def test_escaped_payload_bytes_restored(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_DATA, K.escape(bytes([0xC0, 0xDB, 0x41]))))
        assert d.drain_packets()[0][3] == 3

    def test_chunking_does_not_change_framing(self):
        stream = frame(K.CMD_STAT_RSSI, bytes([80])) + frame(K.CMD_DATA, b"chunked")
        bulk = KissDecoder(); bulk.feed(stream)
        drip = KissDecoder()
        for b in stream:
            drip.feed(bytes([b]))
        assert bulk.drain_packets() == drip.drain_packets()

    def test_stale_stats_are_detectable(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_RSSI, bytes([80])))
        d.feed(frame(K.CMD_DATA, b"a") + frame(K.CMD_DATA, b"b"))
        first, second = d.drain_packets()
        assert first[0] == second[0], "no new stats arrived; seq must not move"

    def test_radio_state_starts_unknown_not_on(self):
        assert KissDecoder().radio_state is None

    def test_radio_error_named(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_ERROR, bytes([0x01])))
        assert d.errors and "INITRADIO" in d.errors[0]


class TestRadioConfigCommands:
    def test_frequency_big_endian_u32(self):
        cmd = radio_config_commands(903625000, 250000, 7, 5, 0)[0]
        assert cmd[0] == K.FEND and cmd[1] == K.CMD_FREQUENCY and cmd[-1] == K.FEND
        assert int.from_bytes(unescape(cmd[2:-1]), "big") == 903625000

    def test_operating_frequency_contains_a_frame_delimiter(self):
        """903000000 == 0x35D2AFC0 — its low byte IS the KISS delimiter.

        The band we actually run on is a live instance of the escaping case, so
        an implementation that forgot to escape would fail on the one frequency
        this fleet uses and work everywhere else.
        """
        assert 903000000 & 0xFF == K.FEND
        cmd = radio_config_commands(903000000, 250000, 7, 5, 0)[0]
        assert cmd.count(bytes([K.FEND])) == 2
        assert int.from_bytes(unescape(cmd[2:-1]), "big") == 903000000

    def test_config_carries_no_state_or_mode(self):
        """Config is idempotent; state and mode are what the session must undo."""
        cmds = radio_config_commands(903625000, 250000, 7, 5, 0)
        assert not any(c[1] in (K.CMD_RADIO_STATE, K.CMD_PROMISC) for c in cmds)


class TestConfirmRadioOn:
    def test_on_confirmed(self):
        p, d = FakePort(ask_reply=0x01), KissDecoder()
        assert confirm_radio_on(p, d, timeout_s=2.0)[0] == K.RADIO_STATE_ON

    def test_off_reported_not_assumed(self):
        p, d = FakePort(ask_reply=0x00), KissDecoder()
        state, why = confirm_radio_on(p, d, timeout_s=2.0)
        assert state == K.RADIO_STATE_OFF and "OFF" in why

    def test_silence_is_unknown_never_on(self):
        p, d = FakePort(ask_reply=None), KissDecoder()
        state, why = confirm_radio_on(p, d, timeout_s=1.0)
        assert state is None and "never answered" in why

    def test_stale_echo_is_flushed_and_not_believed(self):
        """The 2026-09-08 trap: an echo of the value we SET, read as an answer."""
        p = FakePort(ask_reply=0x00, prequeued=frame(K.CMD_RADIO_STATE, bytes([0x01])))
        state, _ = confirm_radio_on(p, KissDecoder(), timeout_s=2.0)
        assert p.flushes >= 1, "must flush the port, not just the decoder"
        assert state == K.RADIO_STATE_OFF, "believed a stale echo over the real answer"


class TestSessionContract:
    def _open(self, port, **kw):
        return rnode_session("/dev/fake", freq=903625000, bw=250000, sf=7, cr=5,
                             settle_s=0, serial_mod=FakeSerialModule(port), **kw)

    def test_yields_a_session_on_a_healthy_radio(self):
        p = FakePort()
        with self._open(p) as rn:
            assert rn.fw_version == "1.86"
        assert p.closed

    def test_refuses_a_device_that_is_not_an_rnode(self):
        with pytest.raises(RNodeUnavailable, match="no RNode responded"):
            with self._open(FakePort(detect=False)):
                pass

    def test_refuses_a_radio_that_will_not_turn_on(self):
        """V4 #2's exact behaviour: answers, but reports state OFF forever."""
        with pytest.raises(RNodeUnavailable, match="did not come up"):
            with self._open(FakePort(ask_reply=0x00)):
                pass

    def test_promiscuous_is_cleared_on_normal_exit(self):
        p = FakePort()
        with self._open(p, promiscuous=True):
            pass
        assert p.promisc_writes()[-1][2] == 0x00, "left the radio promiscuous"

    def test_promiscuous_is_cleared_when_the_body_raises(self):
        """THE bug. An exception must not strand the radio in promiscuous mode."""
        p = FakePort()
        with pytest.raises(ValueError):
            with self._open(p, promiscuous=True):
                raise ValueError("boom")
        assert p.promisc_writes()[-1][2] == 0x00

    def test_promiscuous_is_cleared_on_keyboard_interrupt(self):
        p = FakePort()
        with pytest.raises(KeyboardInterrupt):
            with self._open(p, promiscuous=True):
                raise KeyboardInterrupt
        assert p.promisc_writes()[-1][2] == 0x00

    def test_it_does_not_touch_a_mode_it_never_set(self):
        """Restoring only what we changed — do not clear promiscuous for a
        caller who never asked for it; that would be this bug in reverse."""
        p = FakePort()
        with self._open(p, promiscuous=False):
            pass
        assert p.promisc_writes() == []

    def test_a_failed_restore_leaves_a_witness(self):
        class Stubborn(FakePort):
            def write(self, data):
                data = bytes(data)
                if len(data) >= 3 and data[1] == K.CMD_PROMISC and data[2] == 0x00:
                    raise OSError("port went away")
                return super().write(data)

        p = Stubborn()
        with self._open(p, promiscuous=True) as rn:
            session = rn
        assert session.restore_warnings, "a failed teardown must not be silent"
        assert "may not receive normally" in session.restore_warnings[0]

    def test_port_is_closed_even_when_the_radio_is_refused(self):
        p = FakePort(ask_reply=0x00)
        with pytest.raises(RNodeUnavailable):
            with self._open(p):
                pass
        assert p.closed
