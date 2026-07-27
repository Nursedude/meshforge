"""Tests for utils.claw_battery — the claw pack's level + TREND + intent.

Origin (2026-07-24): the battery metric was one voltage against one threshold,
so a pack we were DELIBERATELY discharging and a pack that was failing produced
the identical page, and a claw that died at 4.05 V got "check the battery".
These pin the distinctions that make those sayable — and, just as hard, pin the
places the module must refuse to guess.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import claw_battery as cb  # noqa: E402

NOW = 1_781_000_000.0
HR = 3600.0


def _series(points):
    """[(hours_ago, volts)] -> sample dicts, oldest first."""
    return [{"ts": NOW - h * HR, "volts": v} for h, v in sorted(points, reverse=True)]


def _ramp(v_start, v_per_hr, hours, step_hr=0.25):
    """A clean linear series ending NOW at v_start + v_per_hr*hours."""
    n = int(hours / step_hr)
    return [{"ts": NOW - (n - i) * step_hr * HR,
             "volts": v_start + v_per_hr * (i * step_hr)}
            for i in range(n + 1)]


class TestSlopeRefusesToGuess:
    """A fabricated flat trend reads as "on a charger" — the healthy domain.
    Thin evidence must yield None, never 0.0 (honest_failure_modes #1)."""

    def test_too_few_samples_is_none_not_flat(self):
        assert cb.fit_slope_v_per_hr(_series([(0, 3.9), (2, 4.0), (4, 4.1)])) is None

    def test_too_short_a_window_is_none_not_flat(self):
        # 8 samples, but all inside 5 minutes: no trend can be claimed.
        pts = [{"ts": NOW - i * 40, "volts": 4.0} for i in range(8)]
        assert cb.fit_slope_v_per_hr(pts) is None

    def test_no_samples_is_none(self):
        assert cb.fit_slope_v_per_hr([]) is None

    def test_clean_discharge_fits(self):
        slope = cb.fit_slope_v_per_hr(_ramp(4.1, -0.05, 6))
        assert slope is not None and -0.055 < slope < -0.045

    def test_duplicate_timestamps_do_not_dominate_the_fit(self):
        pts = _ramp(4.1, -0.05, 6)
        pts += [dict(pts[0]) for _ in range(50)]     # same ts repeated
        slope = cb.fit_slope_v_per_hr(pts)
        assert slope is not None and -0.055 < slope < -0.045

    def test_bool_voltage_is_not_a_reading(self):
        # isinstance(True, int) is True in Python — a stray True would become
        # 1.0 V, i.e. a fabricated dead pack.
        pts = [{"ts": NOW - i * HR, "volts": True} for i in range(6)]
        assert cb.fit_slope_v_per_hr(pts) is None
        assert cb.classify(pts)["state"] == cb.UNKNOWN


class TestClassifyStates:
    def test_no_reading_is_unknown_never_charged_never_flat(self):
        c = cb.classify([])
        assert c["state"] == cb.UNKNOWN
        assert c["volts"] is None and c["slope_v_per_hr"] is None
        assert "neither charged nor flat" in c["summary"]

    def test_below_cutoff_is_critical(self):
        assert cb.classify(_ramp(3.6, -0.05, 6))["state"] == cb.CRITICAL

    def test_between_cutoff_and_floor_is_knee(self):
        c = cb.classify(_series([(0, 3.45), (3, 3.6)]))
        assert c["state"] == cb.KNEE

    def test_healthy_level_falling_is_discharging(self):
        c = cb.classify(_ramp(4.1, -0.05, 6))
        assert c["state"] == cb.DISCHARGING
        assert c["hours_to_cutoff"] is not None

    def test_healthy_level_flat_is_float_not_discharging(self):
        c = cb.classify(_ramp(4.06, 0.0, 6))
        assert c["state"] == cb.FLOAT
        assert c["hours_to_cutoff"] is None

    def test_rising_is_charging(self):
        assert cb.classify(_ramp(3.7, +0.08, 6))["state"] == cb.CHARGING

    def test_healthy_level_without_a_trend_is_unknown_not_float(self):
        # Direction unobserved is not "on a charger" — a claw quietly draining
        # would otherwise read as float until it fell into the knee.
        c = cb.classify(_series([(0, 4.0), (0.1, 4.0)]))
        assert c["state"] == cb.UNKNOWN and c["slope_v_per_hr"] is None

    def test_low_pack_on_a_charger_is_still_in_the_knee(self):
        # Being on a charge source does not undo where the cell sits NOW:
        # 3.20 V rising 40 mV/h is at 3.44 V after 6 h — recovering, but still
        # in the knee, and the operator's decisions depend on the level.
        c = cb.classify(_ramp(3.20, +0.04, 6))
        assert c["volts"] == pytest.approx(3.44, abs=0.001)
        assert c["state"] == cb.KNEE


class TestProjectionHonesty:
    def test_projection_is_labelled_as_a_projection(self):
        c = cb.classify(_ramp(4.1, -0.05, 6))
        assert "LINEAR projection, not measured" in c["summary"]

    def test_noise_level_drift_yields_no_countdown(self):
        # 1 mV/h off a full pack would compute ~692 h to cutoff: arithmetic on
        # noise wearing a forecast's clothes. A pack we call FLOAT must not
        # also carry a countdown.
        c = cb.classify(_ramp(4.10, -0.001, 8))
        assert c["state"] == cb.FLOAT
        assert c["hours_to_cutoff"] is None

    def test_horizon_clamp_is_a_backstop_for_a_misconfigured_cutoff(self):
        """The clamp only fires on a nonsense cutoff — which is exactly when a
        confident four-digit countdown would be most misleading. Contrived on
        purpose: a real 1S LiPo cannot reach the 720 h horizon."""
        c = cb.classify(_ramp(4.10, -0.0013, 24), cutoff_v=0.5,
                        fit_window_s=None)
        assert c["trend"] is True             # the gate passed…
        assert c["hours_to_cutoff"] is None   # …and the horizon still withheld

    def test_no_projection_when_the_trend_is_unknown(self):
        c = cb.classify(_series([(0, 3.6), (1, 3.8)]))
        assert c["hours_to_cutoff"] is None

    def test_projection_matches_the_slope(self):
        c = cb.classify(_ramp(4.0, -0.10, 4))       # 3.6 V now, 0.2 V to cutoff
        assert c["hours_to_cutoff"] == pytest.approx(2.0, abs=0.3)


class TestIntentNeverInventsState:
    def test_armed_and_declining_is_expected(self):
        c = cb.classify(_ramp(3.9, -0.05, 6), soak_armed=True)
        assert c["expected"] is True
        assert "EXPECTED" in c["summary"] and "armed battery soak" in c["summary"]

    def test_armed_but_charging_is_not_expected(self):
        # Intent explains a DECLINE. It cannot make a rising pack "expected",
        # and it must never rewrite the measured state.
        c = cb.classify(_ramp(3.7, +0.08, 6), soak_armed=True)
        assert c["state"] == cb.CHARGING and c["expected"] is False

    def test_armed_but_flat_is_not_expected(self):
        c = cb.classify(_ramp(4.06, 0.0, 6), soak_armed=True)
        assert c["state"] == cb.FLOAT and c["expected"] is False

    def test_unarmed_decline_is_never_expected(self):
        c = cb.classify(_ramp(3.9, -0.05, 6), soak_armed=False)
        assert c["state"] == cb.DISCHARGING and c["expected"] is False

    def test_armed_low_without_a_trend_is_still_expected(self):
        # The device is named by a soak config AND its pack is in the knee:
        # thin trend evidence must not turn the experiment back into a page.
        c = cb.classify(_series([(0, 3.45)]), soak_armed=True)
        assert c["state"] == cb.KNEE and c["expected"] is True


class TestTrendCalibration2026_07_24:
    """Pins the four changes made after measuring moc2/dudeclaw-02: 227 samples
    over 37.5 h, 4.03 V → 3.46 V (−570 mV), gauge quantised to 10 mV. That pack
    fits −2.8 mV/h (16 mV, unresolvable) over its trailing 6 h and −7.5 mV/h
    (89 mV, unmistakable) over 12 h — one fixed slope threshold cannot serve
    both without either missing the decline or inventing one from noise."""

    def test_slow_but_real_decline_over_a_long_window_is_a_trend(self):
        # 3 mV/h for 12 h = 36 mV of movement. A fixed 5 mV/h gate called this
        # `float` — the miss that started this.
        c = cb.classify(_ramp(4.10, -0.003, 12), fit_window_s=None)
        assert c["trend"] is True and c["state"] == cb.DISCHARGING

    def test_big_slope_over_a_tiny_window_is_not_a_trend(self):
        # 40 mV/h looks dramatic, but over 35 min it is 23 mV — inside the
        # noise floor, and on a 10 mV gauge that is two steps. Not a claim.
        c = cb.classify(_ramp(4.10, -0.040, 0.583, step_hr=0.083))
        assert c["slope_v_per_hr"] is not None      # a fit exists…
        assert c["trend"] is False                  # …but it claims nothing
        assert c["hours_to_cutoff"] is None

    def test_short_flat_window_is_unknown_not_float(self):
        # 45 min of identical readings says nothing about direction. `float`
        # asserts a charge-backed pack we have not observed — the same shape as
        # the moc2 miss, at a healthy level.
        c = cb.classify(_ramp(4.06, 0.0, 0.75))
        assert c["state"] == cb.UNKNOWN
        assert "direction unobserved" in c["summary"]
        assert "noise floor" in c["summary"]

    def test_level_still_wins_over_the_short_window_rule(self):
        """Precedence guard: the short-window rule governs the DIRECTION-derived
        states only. A pack in the knee is in the knee after 20 minutes or 20
        hours — where the cell sits is observed, not inferred."""
        for volts, expect in ((3.46, cb.KNEE), (3.35, cb.CRITICAL)):
            c = cb.classify(_ramp(volts, 0.0, 0.33))
            assert c["state"] == expect, (volts, c["state"])

    def test_long_flat_window_is_genuinely_float(self):
        # Past MIN_FLOAT_WINDOW_HR, flat is a real observation about the pack.
        c = cb.classify(_ramp(4.06, 0.0, 8))
        assert c["state"] == cb.FLOAT
        assert "no resolvable movement" in c["summary"]

    def test_fit_uses_the_trailing_window_not_the_whole_history(self):
        """A discharge curve is not a line. Fitting all of moc2's 37.5 h gives
        −14.6 mV/h ("3.4 h left") because the average is dominated by the steep
        opening drop; the trailing 12 h gives −7.5 mV/h. "At the current rate"
        must mean recent."""
        steep = _ramp(4.10, -0.10, 12)                      # 12 h ago → 24 h ago
        steep = [{"ts": r["ts"] - 12 * HR, "volts": r["volts"]} for r in steep]
        recent = _ramp(2.90, -0.010, 12)                    # last 12 h, gentle
        c = cb.classify(steep + recent)
        assert c["window_hr"] <= 12.01, "fit must not reach past FIT_WINDOW_S"
        assert -0.011 < c["slope_v_per_hr"] < -0.009, c["slope_v_per_hr"]
        assert c["n"] > c["n_fit"], "older readings held, but not fitted"

    def test_unbounded_fit_is_still_available(self):
        c = cb.classify(_ramp(4.10, -0.02, 30), fit_window_s=None)
        assert c["window_hr"] == pytest.approx(30.0, abs=0.01)

    def test_newest_reading_is_the_level_even_outside_the_fit_window(self):
        # The fit window bounds the TREND, never which voltage we report.
        c = cb.classify(_ramp(4.10, -0.02, 30))
        assert c["volts"] == pytest.approx(3.50, abs=0.001)


class TestSoakSeriesMerge:
    """The probe built a SECOND record of a pack that already had a longer,
    denser one (227 samples / 37.5 h vs 5 / 20 min) and then read the shorter
    — two records of one artifact (honest_failure_modes #5)."""

    def _write_soak(self, home, rows):
        with open(os.path.join(str(home), cb.SOAK_SERIES_BASENAME), "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_soak_rows_are_read_and_normalised(self, tmp_path):
        self._write_soak(tmp_path, [
            {"ts": NOW - 2 * HR, "vbat": 3.60, "device": "dudeclaw-02"},
            {"ts": NOW - 1 * HR, "vbat": 3.55, "device": "dudeclaw-02"}])
        got = cb.read_soak_series(str(tmp_path), "dudeclaw-02")
        assert [r["volts"] for r in got] == [3.60, 3.55]

    def test_other_devices_and_untagged_rows_are_not_this_pack(self, tmp_path):
        self._write_soak(tmp_path, [
            {"ts": NOW - 3 * HR, "vbat": 4.06, "device": "dudeclaw-01"},
            {"ts": NOW - 2 * HR, "vbat": 3.90},                    # pre-tagging
            {"ts": NOW - 1 * HR, "vbat": 3.55, "device": "dudeclaw-02"}])
        got = cb.read_soak_series(str(tmp_path), "dudeclaw-02")
        assert [r["volts"] for r in got] == [3.55]

    def test_blind_sample_is_not_a_zero_volt_reading(self, tmp_path):
        self._write_soak(tmp_path, [
            {"ts": NOW - 1 * HR, "vbat": None, "device": "dudeclaw-02"}])
        assert cb.read_soak_series(str(tmp_path), "dudeclaw-02") == []

    def test_missing_home_or_file_is_empty_not_an_error(self, tmp_path):
        assert cb.read_soak_series(None, "d") == []
        assert cb.read_soak_series(str(tmp_path), "d") == []

    def test_merged_history_fits_what_neither_source_could_alone(self, tmp_path):
        """The moc2 shape: a long soak record + a handful of fresh probe
        readings. Apart, the probe series is too thin to fit; merged, the trend
        is there."""
        probe = [{"ts": NOW - m * 300.0, "volts": 3.46} for m in (3, 2, 1, 0)]
        assert cb.classify(probe)["slope_v_per_hr"] is None       # 15 min
        soak = [{"ts": NOW - h * HR, "vbat": 3.46 + 0.0075 * h,
                 "device": "dudeclaw-02"} for h in range(12, 0, -1)]
        self._write_soak(tmp_path, soak)
        merged = probe + cb.read_soak_series(str(tmp_path), "dudeclaw-02")
        c = cb.classify(merged)
        assert c["trend"] is True and c["state"] == cb.KNEE
        assert -0.008 < c["slope_v_per_hr"] < -0.007
        assert c["hours_to_cutoff"] is not None

    def test_overlapping_timestamps_count_once(self, tmp_path):
        shared = NOW - 1 * HR
        self._write_soak(tmp_path, [
            {"ts": shared, "vbat": 3.50, "device": "dudeclaw-02"}])
        merged = ([{"ts": shared, "volts": 3.50}]
                  + cb.read_soak_series(str(tmp_path), "dudeclaw-02"))
        assert cb.classify(merged)["n"] == 1


class TestWorstState:
    def test_worst_wins(self):
        assert cb.worst_state([cb.FLOAT, cb.KNEE, cb.CHARGING]) == cb.KNEE
        assert cb.worst_state([cb.DISCHARGING, cb.CRITICAL]) == cb.CRITICAL

    def test_empty_is_unknown_not_healthy(self):
        assert cb.worst_state([]) == cb.UNKNOWN


class TestSeriesIO:
    def test_append_and_read_round_trip(self, tmp_path):
        p = cb.series_path(str(tmp_path), "dudeclaw-02")
        assert cb.append_sample(p, NOW, 4.01) == "written"
        assert cb.append_sample(p, NOW + 300, 3.99) == "written"
        rows = cb.read_series(p)
        assert [r["volts"] for r in rows] == [4.01, 3.99]

    def test_same_capture_is_not_appended_twice(self, tmp_path):
        # The probe re-reads the SAME tick every minute while the capture cron
        # writes every 5: blind appends would stack duplicates and let a
        # STALLED capture masquerade as a dense flat trend.
        p = cb.series_path(str(tmp_path), "dudeclaw-02")
        assert cb.append_sample(p, NOW, 4.01) == "written"
        for _ in range(5):
            assert cb.append_sample(p, NOW, 4.01) == "duplicate"
        assert len(cb.read_series(p)) == 1

    def test_unreadable_voltage_is_never_recorded(self, tmp_path):
        p = cb.series_path(str(tmp_path), "d")
        assert cb.append_sample(p, NOW, None) == "unusable"
        assert cb.append_sample(p, NOW, True) == "unusable"   # bool != 1.0 V
        assert cb.read_series(p) == []

    def test_cap_keeps_the_newest(self, tmp_path):
        p = cb.series_path(str(tmp_path), "d")
        for i in range(12):
            cb.append_sample(p, NOW + i, 4.0 - i * 0.01, cap=5)
        rows = cb.read_series(p)
        assert len(rows) == 5 and rows[-1]["ts"] == NOW + 11

    def test_torn_line_is_skipped_not_fatal(self, tmp_path):
        p = str(tmp_path / "s.jsonl")
        with open(p, "w") as fh:
            fh.write(json.dumps({"ts": NOW, "volts": 4.0}) + "\n")
            fh.write('{"ts": 178100')                      # torn mid-write
        rows = cb.read_series(p)
        assert [r["volts"] for r in rows] == [4.0]

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert cb.read_series(str(tmp_path / "nope.jsonl")) == []

    def test_write_failure_returns_false_never_raises(self, tmp_path):
        # A probe must not die on a read-only /var/lib or a full disk.
        d = tmp_path / "ro"
        d.mkdir()
        p = str(d / "s.jsonl")
        os.chmod(d, 0o500)
        try:
            assert cb.append_sample(p, NOW, 4.0) == "io_error"
        finally:
            os.chmod(d, 0o700)

    def test_device_name_cannot_escape_the_state_dir(self, tmp_path):
        p = cb.series_path(str(tmp_path), "../../etc/passwd")
        assert os.path.dirname(os.path.abspath(p)) == str(tmp_path)

    def test_max_age_filter(self, tmp_path):
        p = cb.series_path(str(tmp_path), "d")
        cb.append_sample(p, NOW - 10 * HR, 4.1)
        cb.append_sample(p, NOW - 1 * HR, 3.9)
        rows = cb.read_series(p, now=NOW, max_age_s=2 * HR)
        assert [r["volts"] for r in rows] == [3.9]


class TestThresholdsAreOneSSOT:
    def test_soak_and_probe_read_the_same_constants(self):
        """Two independent hardcodes of one physical constant WILL drift
        (honest_failure_modes #5) — battery_soak said 3.4, the probe said 3.5,
        for the same 1S LiPo."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import battery_soak

        from utils import watchdog_probes_liveness as wpl
        assert battery_soak.DEFAULT_CUTOFF_V is cb.CUTOFF_V
        assert battery_soak.DISCHARGE_NOISE_V is cb.NOISE_V
        assert wpl.CLAW_BATTERY_FLOOR_V is cb.FLOOR_V

    def test_the_gate_is_the_noise_floor_at_every_timescale(self):
        # ONE threshold, self-calibrating: 60 mV/h over half an hour and
        # 3 mV/h over ten hours are the same claim — 30 mV of real movement.
        assert cb.is_trend(-0.060, 0.5) is True
        assert cb.is_trend(-0.059, 0.5) is False
        assert cb.is_trend(-0.003, 10.0) is True
        assert cb.is_trend(-0.0029, 10.0) is False
