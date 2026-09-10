"""Tests for utils.rnode_session — the RNode access chokepoint.

This module exists because a tool put two production radios into promiscuous
mode and walked away (2026-09-08). So the emphasis here is on the CONTRACT
rather than the happy path: the session must refuse a radio that will not come
up, must refuse a port another process holds, must believe only an explicit
state ASK, and must hand every mode AND state back on every exit path
including exceptions.

Everything runs against a fake serial device — the KISS layer is pure by design
precisely so it can be tested without a radio on the bench. ⚠️ Nothing here
opens a real serial port: rnsd on the dev box holds the RNode.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import rnode_session as rs  # noqa: E402
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

    def __init__(self, *, detect=True, ask_reply=0x01, fw=(1, 86), prequeued=b"",
                 error_on_on=None):
        self.buffer = bytearray(prequeued)
        self.writes = []
        self.closed = False
        self.flushes = 0
        self._detect = detect
        self._ask_reply = ask_reply
        self._fw = fw
        self._error_on_on = error_on_on

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if len(data) >= 3 and data[1] == K.CMD_DETECT and self._detect:
            self.buffer += frame(K.CMD_DETECT, bytes([K.DETECT_RESP]))
            self.buffer += frame(K.CMD_FW_VERSION, bytes(self._fw))
        if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE \
                and data[2] == K.RADIO_STATE_ASK and self._ask_reply is not None:
            self.buffer += frame(K.CMD_RADIO_STATE, bytes([self._ask_reply]))
        if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE \
                and data[2] == K.RADIO_STATE_ON and self._error_on_on is not None:
            self.buffer += frame(K.CMD_ERROR, bytes([self._error_on_on]))
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

    def state_writes(self):
        """RADIO_STATE writes that SET a state (asks excluded)."""
        return [w for w in self.writes if len(w) >= 3 and w[1] == K.CMD_RADIO_STATE
                and w[2] in (K.RADIO_STATE_ON, K.RADIO_STATE_OFF)]


class StatefulPort(FakePort):
    """An RNode whose radio state is real: ON/OFF writes change it, ASK reports it."""

    def __init__(self, *, initial_state=0x00, answer_asks=True, **kw):
        super().__init__(ask_reply=None, **kw)
        self.state = initial_state
        self._answer_asks = answer_asks

    def write(self, data):
        data = bytes(data)
        if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE:
            if data[2] == K.RADIO_STATE_ON:
                self.state = K.RADIO_STATE_ON
            elif data[2] == K.RADIO_STATE_OFF:
                self.state = K.RADIO_STATE_OFF
            elif data[2] == K.RADIO_STATE_ASK and self._answer_asks:
                super().write(data)
                self.buffer += frame(K.CMD_RADIO_STATE, bytes([self.state]))
                return len(data)
        return super().write(data)


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
        (seq, rssi, snr, n), = d.drain_packets()
        assert (rssi, snr, n) == (-92, 7.5, 5)
        assert seq == 1

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

    def test_rssi_only_does_not_flag_the_pair_fresh(self):
        """Finding 10 (2026-09-09): one seq bumped on EITHER stat frame, so a
        packet preceded by only an RSSI frame was flagged fresh while its SNR
        was the previous packet's — the flag lied in the case it exists for."""
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_RSSI, bytes([-100 + K.RSSI_OFFSET])))
        d.feed(frame(K.CMD_STAT_SNR, (-30).to_bytes(1, "big", signed=True)))
        d.feed(frame(K.CMD_DATA, b"pkt1"))
        d.feed(frame(K.CMD_STAT_RSSI, bytes([-95 + K.RSSI_OFFSET])))   # RSSI only
        d.feed(frame(K.CMD_DATA, b"pkt2"))
        p1, p2 = d.drain_packets()
        assert p1 == (1, -100, -7.5, 4)
        assert p2[0] == p1[0], "seq moved on half a pair — the consumer would call it fresh"
        assert p2[1] == -95
        assert p2[2] is None, "pkt1's SNR was carried into pkt2"

    def test_snr_only_does_not_carry_the_previous_rssi(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_RSSI, bytes([-100 + K.RSSI_OFFSET])))
        d.feed(frame(K.CMD_STAT_SNR, (-30).to_bytes(1, "big", signed=True)))
        d.feed(frame(K.CMD_DATA, b"pkt1"))
        d.feed(frame(K.CMD_STAT_SNR, (8).to_bytes(1, "big", signed=True)))   # SNR only
        d.feed(frame(K.CMD_DATA, b"pkt2"))
        _, p2 = d.drain_packets()
        assert p2[1] is None and p2[2] == 2.0 and p2[0] == 1

    def test_a_full_pair_per_packet_advances_seq_each_time(self):
        d = KissDecoder()
        for i in range(3):
            d.feed(frame(K.CMD_STAT_RSSI, bytes([-90 + K.RSSI_OFFSET])))
            d.feed(frame(K.CMD_STAT_SNR, (4).to_bytes(1, "big", signed=True)))
            d.feed(frame(K.CMD_DATA, b"p"))
        assert [p[0] for p in d.drain_packets()] == [1, 2, 3]

    def test_escaped_stat_byte_is_recovered(self):
        """An SNR of -16.0 dB is raw 0xC0; escaped on the wire it must decode."""
        d = KissDecoder()
        d.feed(frame(K.CMD_STAT_RSSI, bytes([80])))
        d.feed(frame(K.CMD_STAT_SNR, K.escape(bytes([0xC0]))))
        d.feed(frame(K.CMD_DATA, b"x"))
        assert d.drain_packets()[0][2] == pytest.approx(-16.0)

    def test_radio_state_starts_unknown_not_on(self):
        assert KissDecoder().radio_state is None

    def test_radio_error_named(self):
        d = KissDecoder()
        d.feed(frame(K.CMD_ERROR, bytes([0x01])))
        assert d.errors and "INITRADIO" in d.errors[0]

    def test_reset_framing_forgets_a_half_frame(self):
        d = KissDecoder()
        d.feed(b"\xc0\x00\xaa\xbb")           # DATA frame, no terminator
        assert d.in_frame
        d.reset_framing()
        d.feed(frame(K.CMD_DETECT, bytes([K.DETECT_RESP])))
        assert d.detected and d.packets == []


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

    def test_a_buffered_error_reply_survives_the_flush(self):
        """Finding 7 (2026-09-09): the firmware's CMD_ERROR reply to ON sat in
        the buffer, the flush discarded it, and the tool then reported 'no
        error explaining why' — manufactured, since the radio HAD explained."""
        p = FakePort(ask_reply=0x00, prequeued=frame(K.CMD_ERROR, bytes([0x01])))
        d = KissDecoder()
        state, why = confirm_radio_on(p, d, timeout_s=2.0)
        assert state == K.RADIO_STATE_OFF
        assert "INITRADIO" in why, why
        assert "no error explaining why" not in why

    def test_drain_keeps_errors_drops_packets_and_state(self):
        p = FakePort(prequeued=frame(K.CMD_ERROR, bytes([0x02]))
                     + frame(K.CMD_DATA, b"old-params")
                     + frame(K.CMD_RADIO_STATE, bytes([0x01]))
                     + b"\xc0\x00\xaa")                       # half a frame
        d = KissDecoder()
        n = rs.drain_stale_input(p, d)
        assert n > 0
        assert d.errors == ["ERROR_TXFAILED"]
        assert d.packets == [] and d.radio_state is None and not d.in_frame
        assert p.flushes == 1


class TestPortHolders:
    """Finding 4 (2026-09-09): a tty has no exclusivity by default. The
    chokepoint must refuse a port another process holds — on the dev box that
    is rnsd's production RNode."""

    def test_a_process_holding_the_path_is_named(self, tmp_path):
        dev = tmp_path / "ttyFAKE0"
        dev.write_bytes(b"")
        with open(dev, "rb") as held:
            child = subprocess.Popen(["sleep", "30"], stdin=held,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            holders = rs.port_holders(str(dev))
            assert any(h.startswith(f"{child.pid} ") for h in holders), holders
            assert any("sleep" in h for h in holders), holders
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_an_unheld_path_has_no_holders(self, tmp_path):
        dev = tmp_path / "ttyFAKE1"
        dev.write_bytes(b"")
        assert rs.port_holders(str(dev)) == []

    def test_a_missing_path_has_no_holders_and_spawns_nothing(self, monkeypatch):
        def no_spawn(*a, **k):
            raise AssertionError("lsof must not run for a path that does not exist")
        monkeypatch.setattr(rs.subprocess, "run", no_spawn)
        assert rs.port_holders("/dev/does-not-exist-ttyX") == []

    def test_the_lookup_itself_is_bounded(self, tmp_path, monkeypatch):
        dev = tmp_path / "ttyFAKE2"
        dev.write_bytes(b"")
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw)
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        monkeypatch.setattr(rs.subprocess, "run", fake_run)
        assert rs.port_holders(str(dev)) == []
        assert seen["timeout"] == rs.HOLDER_LOOKUP_TIMEOUT_S

    def test_the_caller_itself_is_not_a_holder(self, tmp_path):
        dev = tmp_path / "ttyFAKE3"
        dev.write_bytes(b"")
        with open(dev, "rb"):
            assert not any(h.startswith(f"{os.getpid()} ") for h in rs.port_holders(str(dev)))


class TestSessionContract:
    def _open(self, port, **kw):
        return rnode_session("/dev/fake", freq=903625000, bw=250000, sf=7, cr=5,
                             settle_s=0, serial_mod=FakeSerialModule(port), **kw)

    def test_yields_a_session_on_a_healthy_radio(self):
        p = FakePort()
        with self._open(p) as rn:
            assert rn.fw_version == "1.86"
        assert p.closed

    def test_refuses_a_port_another_process_holds(self, monkeypatch):
        """Finding 4: the second open() SUCCEEDS on a tty; the refusal is ours."""
        monkeypatch.setattr(rs, "port_holders", lambda path, **kw: ["4242 (rnsd)"])
        p = FakePort()
        with pytest.raises(RNodeUnavailable, match=r"held open by 4242 \(rnsd\)"):
            with self._open(p):
                pass
        assert p.writes == [], "must refuse BEFORE touching the device"

    def test_opens_the_port_exclusively(self):
        p = FakePort()
        mod = FakeSerialModule(p)
        with rnode_session("/dev/fake", freq=903625000, bw=250000, sf=7, cr=5,
                           settle_s=0, serial_mod=mod):
            pass
        assert mod.opened_with.get("exclusive") is True, "no TIOCEXCL: anyone can open it under us"

    def test_refuses_a_device_that_is_not_an_rnode(self):
        with pytest.raises(RNodeUnavailable, match="no RNode responded"):
            with self._open(FakePort(detect=False)):
                pass

    def test_refuses_a_radio_that_will_not_turn_on(self):
        """V4 #2's exact behaviour: answers, but reports state OFF forever."""
        with pytest.raises(RNodeUnavailable, match="did not come up"):
            with self._open(FakePort(ask_reply=0x00)):
                pass

    def test_the_refusal_quotes_the_radios_own_error(self):
        """Finding 7, end to end: ERROR_INITRADIO in reply to ON must reach
        the operator, not be flushed into 'no error reported'."""
        with pytest.raises(RNodeUnavailable, match="INITRADIO"):
            with self._open(FakePort(ask_reply=0x00, error_on_on=0x01)):
                pass

    def test_a_half_frame_ahead_of_the_detect_reply_does_not_hide_the_rnode(self):
        """Finding 9: a partial DATA frame in flight when the port opened left
        the decoder in_frame; the DETECT reply's FEND closed it as a phantom
        packet and the reply itself was dropped — 'no RNode responded' on a
        healthy device."""
        p = FakePort(prequeued=b"\xc0\x00\xaa\xbb\xcc")
        with self._open(p) as rn:
            assert rn.fw_version == "1.86"

    def test_packets_from_before_the_session_are_not_handed_to_the_caller(self):
        """Finding 9: frames received on the OLD modem parameters must not
        surface at yield as provenance-tagged rows for the new ones."""
        stale = (frame(K.CMD_STAT_RSSI, bytes([100])) + frame(K.CMD_STAT_SNR, bytes([20]))
                 + frame(K.CMD_DATA, b"old-params"))
        p = FakePort(prequeued=stale)
        with self._open(p) as rn:
            assert rn.decoder.packets == []
            # idle boundary (in_frame after a closing FEND) is fine; a half
            # frame with content is not
            assert rn.decoder.command == K.CMD_UNKNOWN and not rn.decoder.buf

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


class TestRadioStateIsRestored:
    """Finding 14 (2026-09-09): the contract said 'restores every mode it
    changed' but the radio was turned ON unconditionally and never put back.
    rnsd parks its RNode OFF on release; a field-kit board left ON draws RX
    current with no host reading it."""

    def _open(self, port, **kw):
        return rnode_session("/dev/fake", freq=903625000, bw=250000, sf=7, cr=5,
                             settle_s=0, serial_mod=FakeSerialModule(port), **kw)

    def test_a_radio_that_was_off_is_put_back_off(self):
        p = StatefulPort(initial_state=K.RADIO_STATE_OFF)
        with self._open(p) as rn:
            assert rn.prior_radio_state == K.RADIO_STATE_OFF
            assert p.state == K.RADIO_STATE_ON, "the session runs with the radio on"
        assert p.state == K.RADIO_STATE_OFF, "left ON a radio that was OFF"
        assert p.state_writes()[-1][2] == K.RADIO_STATE_OFF

    def test_a_radio_that_was_off_is_put_back_off_when_the_body_raises(self):
        p = StatefulPort(initial_state=K.RADIO_STATE_OFF)
        with pytest.raises(KeyboardInterrupt):
            with self._open(p):
                raise KeyboardInterrupt
        assert p.state == K.RADIO_STATE_OFF

    def test_a_radio_that_was_already_on_is_left_on(self):
        """Restore only what we changed: no OFF for a radio that was ON."""
        p = StatefulPort(initial_state=K.RADIO_STATE_ON)
        with self._open(p) as rn:
            assert rn.prior_radio_state == K.RADIO_STATE_ON
        assert p.state == K.RADIO_STATE_ON
        assert all(w[2] != K.RADIO_STATE_OFF for w in p.state_writes())

    def test_the_prior_state_is_asked_before_anything_is_written(self):
        p = StatefulPort(initial_state=K.RADIO_STATE_OFF)
        with self._open(p):
            pass
        asks = [i for i, w in enumerate(p.writes)
                if len(w) >= 3 and w[1] == K.CMD_RADIO_STATE and w[2] == K.RADIO_STATE_ASK]
        first_set = next(i for i, w in enumerate(p.writes)
                         if len(w) >= 3 and w[1] in (K.CMD_FREQUENCY, K.CMD_RADIO_STATE)
                         and not (w[1] == K.CMD_RADIO_STATE and w[2] == K.RADIO_STATE_ASK))
        assert asks and asks[0] < first_set

    def test_unknown_prior_state_leaves_a_witness_not_silence(self):
        """If the radio never answered the prior ASK we cannot restore it —
        say so, rather than leaving it ON quietly."""
        class LateAnswerer(StatefulPort):
            def write(self, data):
                data = bytes(data)
                if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE \
                        and data[2] == K.RADIO_STATE_ON:
                    self._answer_asks = True
                return super().write(data)

        p = LateAnswerer(initial_state=K.RADIO_STATE_OFF, answer_asks=False)
        with self._open(p) as rn:
            assert rn.prior_radio_state is None
            session = rn
        assert any("prior state is unknown" in w for w in session.restore_warnings)

    def test_a_failed_off_restore_leaves_a_witness(self):
        class Stubborn(StatefulPort):
            def write(self, data):
                data = bytes(data)
                if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE \
                        and data[2] == K.RADIO_STATE_OFF:
                    raise OSError("port went away")
                return super().write(data)

        p = Stubborn(initial_state=K.RADIO_STATE_OFF)
        with self._open(p) as rn:
            session = rn
        assert any("left ON" in w for w in session.restore_warnings)
