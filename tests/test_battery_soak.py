"""Tests for scripts/battery_soak.py — the §5 dude-claw power-draw soak collector.

Covers the two pure functions (no NATS, no I/O): ``parse_battery_reading`` (the
firmware reply parser) and ``summarize_samples`` (the runtime-to-cutoff judge),
including the honest-failure-modes contract: a blind/charging/too-short series
must NOT read as a measured average.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import battery_soak  # noqa: E402


class TestParseBatteryReading:
    def test_normal_reading(self):
        vbat, mv, err = battery_soak.parse_battery_reading("Battery: 3.87 V (adc 790 mV)")
        assert vbat == 3.87
        assert mv == 790
        assert err is None

    def test_integer_volts(self):
        vbat, mv, err = battery_soak.parse_battery_reading("Battery: 4 V (adc 816 mV)")
        assert vbat == 4.0
        assert mv == 816
        assert err is None

    def test_case_insensitive(self):
        vbat, mv, err = battery_soak.parse_battery_reading("battery: 3.50 V (ADC 714 mV)")
        assert vbat == 3.50
        assert mv == 714
        assert err is None

    def test_firmware_error_no_sense(self):
        vbat, mv, err = battery_soak.parse_battery_reading(
            "Error: no battery sense on this device")
        assert vbat is None
        assert mv is None
        assert "no battery sense" in err

    def test_empty_reply(self):
        vbat, mv, err = battery_soak.parse_battery_reading("")
        assert vbat is None
        assert err == "empty reply"

    def test_unparseable_text(self):
        vbat, mv, err = battery_soak.parse_battery_reading("garbage telemetry")
        assert vbat is None
        assert "unparseable" in err

    def test_none_input(self):
        vbat, mv, err = battery_soak.parse_battery_reading(None)
        assert vbat is None
        assert err == "empty reply"


def _series(points):
    """points: list of (ts, vbat) → sample dicts (vbat None = blind)."""
    return [{"ts": ts, "vbat": v, "error": None if v is not None else "blind"}
            for ts, v in points]


class TestReadSamplesDeviceFilter:
    """A retargeted soak (config repointed to another claw) must not blend the
    previous claw's samples into the new pack's curve — row-7's cross-device
    class, guarded at the read boundary."""

    def _write(self, home, rows):
        (home / battery_soak.STATE_BASENAME).write_text(
            "\n".join(__import__("json").dumps(r) for r in rows) + "\n")

    def test_unfiltered_read_returns_all(self, tmp_path):
        self._write(tmp_path, [
            {"ts": 1, "vbat": 4.0, "device": "dudeclaw-01"},
            {"ts": 2, "vbat": 3.8, "device": "dudeclaw-02"}])
        assert len(battery_soak._read_samples(tmp_path)) == 2

    def test_device_filter_isolates_one_claw(self, tmp_path):
        self._write(tmp_path, [
            {"ts": 1, "vbat": 4.06, "device": "dudeclaw-01"},   # old USB claw
            {"ts": 2, "vbat": 3.9, "device": "dudeclaw-02"},
            {"ts": 3, "vbat": 3.7, "device": "dudeclaw-02"}])
        kept, dropped = battery_soak._read_samples(tmp_path, device="dudeclaw-02")
        assert [s["vbat"] for s in kept] == [3.9, 3.7]
        assert dropped == 1  # the dudeclaw-01 row is ignored, and counted

    def test_missing_file_returns_empty_tuple_when_scoped(self, tmp_path):
        assert battery_soak._read_samples(tmp_path, device="dudeclaw-02") == ([], 0)

    def test_legacy_device_less_rows_dropped_when_scoped(self, tmp_path):
        self._write(tmp_path, [
            {"ts": 1, "vbat": 4.0},  # pre-device-field row
            {"ts": 2, "vbat": 3.8, "device": "dudeclaw-02"}])
        kept, dropped = battery_soak._read_samples(tmp_path, device="dudeclaw-02")
        assert len(kept) == 1 and dropped == 1


class TestSummarizeSamples:
    USABLE = 4100  # mAh usable to cutoff
    CUTOFF = 3.4

    def test_indeterminate_too_few(self):
        res = battery_soak.summarize_samples(_series([(0, 4.10)]), self.USABLE,
                                             self.CUTOFF, stale_after_s=None)
        assert res["status"] == "INDETERMINATE"
        assert res["n_valid"] == 1

    def test_indeterminate_all_blind(self):
        res = battery_soak.summarize_samples(_series([(0, None), (600, None)]),
                                             self.USABLE, self.CUTOFF, stale_after_s=None)
        assert res["status"] == "INDETERMINATE"
        assert res["n_valid"] == 0
        assert res["n_blind"] == 2  # blindness surfaced, not read as healthy

    def test_measured_avg_current_via_runtime_to_cutoff(self):
        # 4.10 V at t=0, reaches 3.40 V cutoff at t=34 h → 4100 mAh / 34 h ≈ 120.6 mA
        hr = 3600
        samples = _series([(0, 4.10), (17 * hr, 3.75), (34 * hr, 3.40)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "MEASURED"
        assert res["runtime_to_cutoff_hr"] == 34.0
        assert abs(res["avg_ma"] - 120.6) < 0.5
        assert "mA" in res["summary"]

    def test_measured_lower_current_when_runs_longer(self):
        hr = 3600
        # Same pack lasts 50 h → ~82 mA (DTIM modem-sleep working)
        samples = _series([(0, 4.10), (25 * hr, 3.70), (50 * hr, 3.35)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "MEASURED"
        assert abs(res["avg_ma"] - 82.0) < 2.0

    def test_in_progress_not_yet_cutoff(self):
        hr = 3600
        # Declining but still above cutoff → no measured current, has projection
        samples = _series([(0, 4.10), (10 * hr, 3.80)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "IN_PROGRESS"
        assert res["avg_ma"] is None
        assert res["proj_hr_to_cutoff"] is not None
        assert "projection" in res["summary"].lower()

    def test_not_discharging_on_charge(self):
        hr = 3600
        # Flat/rising terminal voltage ⇒ on USB/solar; cannot measure draw
        samples = _series([(0, 4.05), (5 * hr, 4.06), (10 * hr, 4.08)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "NOT_DISCHARGING"
        assert res["avg_ma"] is None
        assert "battery only" in res["summary"].lower()

    def test_stale_collector_stopped(self):
        # Newest sample 2 h old, threshold 1 h → STALE (silence is the failure)
        now = time.time()
        samples = _series([(now - 7300, 3.9), (now - 7200, 3.85)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=3600.0)
        assert res["status"] == "STALE"

    def test_blind_samples_counted_in_measured(self):
        hr = 3600
        samples = _series([(0, 4.10), (10 * hr, None), (34 * hr, 3.40)])
        res = battery_soak.summarize_samples(samples, self.USABLE, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "MEASURED"
        assert res["n_blind"] == 1
        assert res["n_valid"] == 2

    def test_no_usable_capacity_stays_in_progress(self):
        # Without a configured capacity we can't derive mA even past cutoff →
        # never fabricate a number; fall through to the trend report.
        hr = 3600
        samples = _series([(0, 4.10), (34 * hr, 3.40)])
        res = battery_soak.summarize_samples(samples, None, self.CUTOFF,
                                             stale_after_s=None)
        assert res["status"] == "IN_PROGRESS"
        assert res["avg_ma"] is None
