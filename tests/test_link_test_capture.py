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
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

SCRIPT = _ROOT / "scripts" / "link_test_capture.py"
_spec = importlib.util.spec_from_file_location("link_test_capture", SCRIPT)
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

# KISS lives in the rnode_session chokepoint, not in this script. It used to be
# re-exported here and the test reached through the re-export; that made the
# script look like it owned the protocol constants, and left a dead shadowing
# copy of confirm_radio_on beside them. Import from the owner.
from utils.rnode_session import KISS as K  # noqa: E402


def frame(command: int, payload: bytes) -> bytes:
    return bytes([K.FEND, command]) + payload + bytes([K.FEND])


def rssi_frame(dbm: int) -> bytes:
    return frame(K.CMD_STAT_RSSI, bytes([dbm + K.RSSI_OFFSET]))


def snr_frame(db: float) -> bytes:
    return frame(K.CMD_STAT_SNR, int(db / 0.25).to_bytes(1, "big", signed=True))


def data_frame(payload: bytes) -> bytes:
    return frame(K.CMD_DATA, K.escape(payload))


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
        """Rows whose RSSI/SNR pair did not both arrive must refuse, not average."""
        s = lt.summarize_rows(_rows(stats_fresh="0"))
        assert any("cannot be paired" in b for b in s["blockers"])
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



class TestMixedProvenanceIsRefused:
    """Finding 15 (2026-09-09): --out appends and the beacon docstring says to
    concatenate runs. Two runs at different TX power in one file gave a median
    RSSI belonging to neither and a path loss computed against rows[0]'s power
    — 6 dB wrong, warnings [], blockers []."""

    def test_two_runs_at_different_tx_power_are_not_averaged(self):
        rows = (_rows(n=20, run="a", tx_power_dbm="10", rssi_dbm="-98")
                + _rows(n=20, run="b", tx_power_dbm="22", rssi_dbm="-86"))
        s = lt.summarize_rows(rows)
        assert any("mixes 2 different measurements" in b for b in s["blockers"]), s
        assert "path_loss_db" not in s and "excess_loss_db" not in s
        assert s["provenance_groups"] == 2

    def test_a_second_direction_appended_to_the_same_file_is_refused(self):
        rows = _rows(n=20, direction="a->b") + _rows(n=20, direction="b->a")
        s = lt.summarize_rows(rows)
        assert s["blockers"] and "path_loss_db" not in s

    def test_appended_runs_with_identical_provenance_still_reduce(self):
        """Concatenating runs of the SAME measurement is the supported case."""
        s = lt.summarize_rows(_rows(n=20) + _rows(n=20))
        assert not s["blockers"]
        assert s["provenance_groups"] == 1
        assert "excess_loss_db" in s

    def test_the_blocker_names_what_differs(self):
        rows = (_rows(n=20, run="a", tx_power_dbm="10")
                + _rows(n=20, run="b", tx_power_dbm="22"))
        s = lt.summarize_rows(rows)
        b = s["blockers"][0]
        assert "tx_power=10" in b and "tx_power=22" in b


class TestRadioArgValidation:
    """Finding 12, second leg: out-of-range --sf/--cr/--tx-power-setting used
    to surface as bytes() ValueError AFTER the port was open and DETECT had
    passed; a negative --freq went to the radio silently as 0xffffffff."""

    def test_the_fleet_defaults_are_valid(self):
        assert lt.validate_radio_args(903000000, 125000, 8, 5, 0) == []

    @pytest.mark.parametrize("freq,bw,sf,cr,txp", [
        (-1, 125000, 8, 5, 0),
        (0, 125000, 8, 5, 0),
        (2 ** 32, 125000, 8, 5, 0),
        (903000000, 0, 8, 5, 0),
        (903000000, 125000, 4, 5, 0),
        (903000000, 125000, 13, 5, 0),
        (903000000, 125000, 8, 4, 0),
        (903000000, 125000, 8, 9, 0),
        (903000000, 125000, 8, 5, -1),
        (903000000, 125000, 8, 5, 256),
        (903000000, 125000, 8, 5, 38),
    ])
    def test_out_of_range_is_named_before_the_port_opens(self, freq, bw, sf, cr, txp):
        problems = lt.validate_radio_args(freq, bw, sf, cr, txp)
        assert problems, "must refuse, not send it to the radio"


class TestCaptureSerialFailure:
    """Finding 12: the except (SerialException, OSError) handler was dropped in
    08b45db3, so a USB unplug three minutes into a hill-side capture escaped
    as a traceback with no FAIL line and no footer naming the file."""

    @staticmethod
    def _args(tmp_path):
        from types import SimpleNamespace
        return SimpleNamespace(
            out=str(tmp_path / "c.csv"), port="/dev/fake", run="r", direction="d",
            tx_hw="", rx_hw="", tx_power=22.0, tx_gain=0.0, rx_gain=0.0,
            freq=903000000, bw=125000, sf=8, cr=5, distance=10.0,
            tx_power_setting=0, promiscuous=False, baud=115200, seconds=0)

    @staticmethod
    def _fake_session(reads):
        from contextlib import contextmanager
        from utils.rnode_session import KissDecoder

        class FakeRN:
            fw_version = "1.86"

            def __init__(self):
                self.decoder = KissDecoder()
                self._reads = list(reads)

            def read(self, n):
                item = self._reads.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item

        @contextmanager
        def session(port, **kw):
            yield FakeRN()
        return session

    def test_usb_unplug_mid_capture_ends_with_fail_not_traceback(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(lt, "rnode_session", self._fake_session(
            [OSError("device reports readiness to read but returned no data "
                     "(device disconnected or multiple access on port?)")]))
        rc = lt.do_capture(self._args(tmp_path))
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL: serial error during capture" in out

    def test_rows_captured_before_the_unplug_are_kept_and_named(self, tmp_path, capsys, monkeypatch):
        one_packet = rssi_frame(-90) + snr_frame(4.0) + data_frame(b"hello")
        monkeypatch.setattr(lt, "rnode_session", self._fake_session(
            [one_packet, OSError("device disconnected")]))
        rc = lt.do_capture(self._args(tmp_path))
        out = capsys.readouterr().out
        assert rc == 1
        assert "Wrote 1 row(s)" in out
        import csv
        with open(tmp_path / "c.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1 and rows[0]["rssi_dbm"] == "-90"

    def test_pyserials_exception_is_an_oserror(self):
        """The handler catches OSError; pyserial's SerialException must be one."""
        serial = pytest.importorskip("serial")
        assert issubclass(serial.SerialException, OSError)


class TestStatsFreshnessConsumer:
    """Finding 10, consumer side: the row's stats_fresh must be 0 when only one
    of the RSSI/SNR pair arrived, and the missing half written blank."""

    def test_half_a_pair_is_written_stale_with_the_missing_half_blank(self, tmp_path, capsys, monkeypatch):
        stream = (rssi_frame(-90) + snr_frame(4.0) + data_frame(b"one")
                  + rssi_frame(-95) + data_frame(b"two"))
        monkeypatch.setattr(lt, "rnode_session", TestCaptureSerialFailure._fake_session(
            [stream, KeyboardInterrupt()]))
        assert lt.do_capture(TestCaptureSerialFailure._args(tmp_path)) == 0
        import csv
        with open(tmp_path / "c.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        assert [r["stats_fresh"] for r in rows] == ["1", "0"]
        assert rows[1]["rssi_dbm"] == "-95" and rows[1]["snr_db"] == ""
