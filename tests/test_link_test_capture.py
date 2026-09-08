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

