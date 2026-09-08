"""Tests for scripts/link_test_capture.py — the RNode path-loss instrument.

The serial I/O cannot be exercised without a radio on the bench, so the tool is
built with the load-bearing logic split out from it: a pure KISS byte-stream
decoder and a pure CSV reducer. Those are what these tests pin.

The emphasis is deliberately on the REFUSALS. This tool exists to produce one
number that gets reused for antenna siting; a wrong number is worse than no
number, because nothing downstream would catch it. So most of what follows
asserts that bad captures are refused rather than summarized.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "link_test_capture.py"
_spec = importlib.util.spec_from_file_location("link_test_capture", SCRIPT)
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

K = lt.KISS


def frame(command: int, payload: bytes) -> bytes:
    return bytes([K.FEND, command]) + payload + bytes([K.FEND])


def rssi_frame(dbm: int) -> bytes:
    return frame(K.CMD_STAT_RSSI, bytes([dbm + K.RSSI_OFFSET]))


def snr_frame(db: float) -> bytes:
    return frame(K.CMD_STAT_SNR, int(db / 0.25).to_bytes(1, "big", signed=True))


def data_frame(payload: bytes) -> bytes:
    return frame(K.CMD_DATA, K.escape(payload))


class TestKissDecoder:
    def test_decodes_a_packet_with_its_stats(self):
        d = lt.KissDecoder()
        d.feed(rssi_frame(-92) + snr_frame(7.5) + data_frame(b"hello"))
        packets = d.drain_packets()
        assert len(packets) == 1
        _, rssi, snr, nbytes = packets[0]
        assert rssi == -92
        assert snr == 7.5
        assert nbytes == 5

    def test_rssi_offset_applied(self):
        """Firmware reports RSSI as an unsigned byte with a +157 offset."""
        d = lt.KissDecoder()
        d.feed(rssi_frame(-137) + data_frame(b"x"))
        assert d.drain_packets()[0][1] == -137

    def test_negative_snr_is_signed(self):
        """A weak link reports negative SNR; an unsigned read would show ~+50 dB."""
        d = lt.KissDecoder()
        d.feed(snr_frame(-12.25) + data_frame(b"x"))
        assert d.drain_packets()[0][2] == -12.25

    def test_escaped_bytes_are_restored(self):
        payload = bytes([0xC0, 0xDB, 0x41])
        d = lt.KissDecoder()
        d.feed(data_frame(payload))
        assert d.drain_packets()[0][3] == 3

    def test_multiple_packets_in_one_read(self):
        d = lt.KissDecoder()
        d.feed(rssi_frame(-80) + data_frame(b"aa") + rssi_frame(-85) + data_frame(b"bbb"))
        packets = d.drain_packets()
        assert [p[1] for p in packets] == [-80, -85]
        assert [p[3] for p in packets] == [2, 3]

    def test_byte_at_a_time_matches_bulk(self):
        """Serial delivers arbitrary chunk sizes; framing must not depend on them."""
        stream = rssi_frame(-77) + snr_frame(3.0) + data_frame(b"chunked")
        bulk = lt.KissDecoder()
        bulk.feed(stream)
        drip = lt.KissDecoder()
        for b in stream:
            drip.feed(bytes([b]))
        assert bulk.drain_packets() == drip.drain_packets()

    def test_stat_seq_marks_stale_stats(self):
        """Two packets, one stats frame: only the first pairing is trustworthy."""
        d = lt.KissDecoder()
        d.feed(rssi_frame(-90) + data_frame(b"a") + data_frame(b"b"))
        first, second = d.drain_packets()
        assert first[0] == second[0], "no new stats arrived, so the seq must not move"

    def test_detect_response_recognised(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_DETECT, bytes([K.DETECT_RESP])))
        assert d.detected is True

    def test_wrong_detect_byte_is_not_detection(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_DETECT, bytes([0x00])))
        assert d.detected is False

    def test_garbage_outside_a_frame_is_ignored(self):
        d = lt.KissDecoder()
        d.feed(b"\x01\x02noise" + rssi_frame(-70) + data_frame(b"ok"))
        packets = d.drain_packets()
        assert len(packets) == 1 and packets[0][1] == -70


def unescape(data: bytes) -> bytes:
    return data.replace(bytes([K.FESC, K.TFEND]), bytes([K.FEND])) \
               .replace(bytes([K.FESC, K.TFESC]), bytes([K.FESC]))


class TestRadioCommands:
    def test_frequency_encoded_big_endian_u32(self):
        cmds = lt.radio_init_commands(903000000, 125000, 8, 5, 0)
        freq_cmd = cmds[0]
        assert freq_cmd[0] == K.FEND and freq_cmd[1] == K.CMD_FREQUENCY
        assert freq_cmd[-1] == K.FEND
        payload = unescape(freq_cmd[2:-1])
        assert int.from_bytes(payload, "big") == 903000000

    def test_903_mhz_encodes_a_frame_delimiter_and_must_be_escaped(self):
        """903000000 == 0x35D2AFC0 -- its low byte IS the KISS frame delimiter.

        The band we actually operate on is a live instance of the escaping case,
        so an implementation that forgot to escape would fail on the ONE
        frequency this tool exists to measure, and work everywhere else.
        """
        assert 903000000 & 0xFF == K.FEND
        cmd = lt.radio_init_commands(903000000, 125000, 8, 5, 0)[0]
        assert cmd.count(bytes([K.FEND])) == 2, "payload byte terminated its own frame"
        assert int.from_bytes(unescape(cmd[2:-1]), "big") == 903000000

    def test_bandwidth_encoded_big_endian_u32(self):
        cmds = lt.radio_init_commands(903000000, 125000, 8, 5, 0)
        assert int.from_bytes(unescape(cmds[1][2:-1]), "big") == 125000

    def test_sequence_ends_with_radio_on_then_promiscuous(self):
        """Promiscuous must be set after the radio is on, or nothing is captured."""
        cmds = lt.radio_init_commands(903000000, 125000, 8, 5, 0)
        assert cmds[-2][1] == K.CMD_RADIO_STATE and cmds[-2][2] == K.RADIO_STATE_ON
        assert cmds[-1][1] == K.CMD_PROMISC and cmds[-1][2] == 0x01

    def test_frequency_containing_both_escape_bytes_survives(self):
        """0xC0 and 0xDB in one payload -- both escape forms, round-tripped."""
        freq = 0xC0DB00C0
        cmd = lt.radio_init_commands(freq, 125000, 8, 5, 0)[0]
        assert cmd.count(bytes([K.FEND])) == 2, "payload byte was mistaken for a delimiter"
        assert int.from_bytes(unescape(cmd[2:-1]), "big") == freq


class TestPathLossMath:
    def test_link_budget_arithmetic(self):
        # 22 dBm out, 2.15 dBi each end, -100 dBm in -> 126.3 dB of loss
        assert lt.measured_path_loss(22, 2.15, 2.15, -100) == pytest.approx(126.3)

    def test_gains_reduce_implied_loss(self):
        low = lt.measured_path_loss(22, 0, 0, -100)
        high = lt.measured_path_loss(22, 6, 6, -100)
        assert high - low == pytest.approx(12.0)


def _rows(n=30, **over):
    base = {
        "run": "ohia", "direction": "a->b", "tx_hw": "V4", "rx_hw": "RAK",
        "tx_power_dbm": "22", "tx_gain_dbi": "2.15", "rx_gain_dbi": "2.15",
        "freq_hz": "903000000", "bw_hz": "125000", "sf": "8", "cr": "5",
        "distance_m": "76.2", "rssi_dbm": "-100", "snr_db": "6.0",
        "bytes": "20", "stats_fresh": "1",
    }
    base.update(over)
    return [dict(base) for _ in range(n)]


class TestSummarize:
    def test_produces_excess_loss_over_free_space(self):
        s = lt.summarize_rows(_rows())
        assert not s["blockers"]
        assert s["path_loss_db"] == pytest.approx(126.3, abs=0.1)
        # 76.2 m at 903 MHz is ~69 dB free space; the excess is the vegetation
        assert s["excess_loss_db"] == pytest.approx(s["path_loss_db"] - s["fspl_db"], abs=0.1)
        assert s["excess_loss_db"] > 0

    def test_empty_capture_is_blocked(self):
        s = lt.summarize_rows([])
        assert s["blockers"] and "empty" in s["blockers"][0]

    def test_stale_stats_block_the_headline(self):
        """The unverified frame-ordering assumption must refuse, not average."""
        s = lt.summarize_rows(_rows(stats_fresh="0"))
        assert any("stale-by-one" in b for b in s["blockers"])
        assert "excess_loss_db" not in s

    def test_missing_tx_power_blocks_and_says_it_is_unrecoverable(self):
        s = lt.summarize_rows(_rows(tx_power_dbm=""))
        assert any("tx_power_dbm" in b for b in s["blockers"])
        assert "path_loss_db" not in s

    def test_too_few_packets_warns_but_still_computes(self):
        s = lt.summarize_rows(_rows(n=5))
        assert any("noise" in w for w in s["warnings"])
        assert "excess_loss_db" in s, "a warning must not suppress the figure"

    def test_readings_near_the_floor_warn_about_understating_loss(self):
        s = lt.summarize_rows(_rows(rssi_dbm="-135"))
        assert any("floor" in w for w in s["warnings"])

    def test_missing_distance_still_reports_total_path_loss(self):
        s = lt.summarize_rows(_rows(distance_m=""))
        assert "path_loss_db" in s
        assert "excess_loss_db" not in s
        assert any("excess-over-free-space" in w for w in s["warnings"])

    def test_median_not_mean_so_one_outlier_cannot_move_it(self):
        rows = _rows(n=21)
        rows[0]["rssi_dbm"] = "-10"  # an impossible near-field spike
        s = lt.summarize_rows(rows)
        assert s["rssi_median"] == pytest.approx(-100)

    def test_provenance_is_carried_into_the_summary(self):
        s = lt.summarize_rows(_rows())
        assert s["tx_hw"] == "V4" and s["rx_hw"] == "RAK"
        assert s["direction"] == "a->b"

    def test_unparseable_rssi_rows_are_skipped_not_zeroed(self):
        """A blank RSSI must not enter the median as 0 dBm."""
        rows = _rows(n=20) + _rows(n=5, rssi_dbm="")
        s = lt.summarize_rows(rows)
        assert s["rssi_n"] == 20
        assert s["rssi_median"] == pytest.approx(-100)

class FakePort:
    """Minimal serial stand-in: scripted replies, and it honours a buffer flush.

    The flush behaviour is the point -- see TestConfirmRadioOn's echo test.
    """

    def __init__(self, reply_for_ask=None, prequeued=b"", answer_after=0):
        self.reply_for_ask = reply_for_ask
        self.buffer = bytearray(prequeued)
        self.writes = []
        self.asks = 0
        self.answer_after = answer_after   # ignore this many ASKs before replying
        self.flushes = 0

    def write(self, data):
        self.writes.append(bytes(data))
        if len(data) >= 3 and data[1] == K.CMD_RADIO_STATE and data[2] == K.RADIO_STATE_ASK:
            self.asks += 1
            if self.reply_for_ask is not None and self.asks > self.answer_after:
                self.buffer += frame(K.CMD_RADIO_STATE, bytes([self.reply_for_ask]))
        return len(data)

    def read(self, n):
        out = bytes(self.buffer[:n])
        del self.buffer[:n]
        return out

    def reset_input_buffer(self):
        self.flushes += 1
        self.buffer.clear()


class TestRadioStateDecode:
    def test_radio_state_is_decoded(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_RADIO_STATE, bytes([0x01])))
        assert d.radio_state == 0x01

    def test_radio_state_off_is_decoded(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_RADIO_STATE, bytes([0x00])))
        assert d.radio_state == 0x00

    def test_starts_unknown_not_assumed_on(self):
        """Absence of a report must never read as a healthy radio."""
        assert lt.KissDecoder().radio_state is None

    def test_radio_error_is_captured_with_a_name(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_ERROR, bytes([0x01])))
        assert len(d.errors) == 1
        assert "INITRADIO" in d.errors[0]

    def test_unknown_error_code_still_recorded(self):
        d = lt.KissDecoder()
        d.feed(frame(K.CMD_ERROR, bytes([0x7E])))
        assert d.errors and "0x7e" in d.errors[0]


class TestConfirmRadioOn:
    def test_radio_on_is_confirmed(self):
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=0x01)
        state, _ = lt.confirm_radio_on(port, dec, timeout_s=2.0)
        assert state == K.RADIO_STATE_ON

    def test_radio_off_is_reported_not_assumed(self):
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=0x00)
        state, why = lt.confirm_radio_on(port, dec, timeout_s=2.0)
        assert state == K.RADIO_STATE_OFF
        assert "OFF" in why

    def test_silent_radio_is_unknown_never_on(self):
        """No answer must not fall through to 'fine'."""
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=None)
        state, why = lt.confirm_radio_on(port, dec, timeout_s=1.0)
        assert state is None
        assert "never answered" in why

    def test_it_actually_asks(self):
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=0x01)
        lt.confirm_radio_on(port, dec, timeout_s=2.0)
        assert port.asks >= 1, "must send RADIO_STATE_ASK, not trust an unsolicited frame"

    def test_a_stale_echo_is_flushed_and_not_believed(self):
        """THE bug this guard exists for (2026-09-08).

        A CMD_RADIO_STATE frame already sitting in the buffer is an echo of the
        value we just SET, not an observation. Here the echo says ON and the
        real answer says OFF -- believing the echo is the failure that let a
        dead receiver look healthy.
        """
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=0x00,
                        prequeued=frame(K.CMD_RADIO_STATE, bytes([0x01])))
        state, _ = lt.confirm_radio_on(port, dec, timeout_s=2.0)
        assert port.flushes >= 1, "must flush the port, not just the decoder"
        assert state == K.RADIO_STATE_OFF, "believed a stale echo over the real answer"

    def test_retries_until_the_radio_answers(self):
        dec = lt.KissDecoder()
        port = FakePort(reply_for_ask=0x01, answer_after=2)
        state, _ = lt.confirm_radio_on(port, dec, timeout_s=4.0)
        assert state == K.RADIO_STATE_ON
        assert port.asks >= 3

    def test_errors_are_cleared_before_asking(self):
        """A stale error from init must not be attributed to this check."""
        dec = lt.KissDecoder()
        dec.errors.append("stale error from an earlier phase")
        port = FakePort(reply_for_ask=0x01)
        lt.confirm_radio_on(port, dec, timeout_s=2.0)
        assert dec.errors == []

