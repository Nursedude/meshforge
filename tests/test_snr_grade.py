"""One SNR grader for every screen (SNR stage 1, 2026-10-04).

Before this, four graders disagreed on the SAME reading. -9 dB — the median
distinct over-the-air SNR on the manager box (n=1948) — scored ~77 in
health_score, ~29 in link_quality and "Bad" in the topology pane. The physics
they all skipped: a radio only decodes its OWN spreading factor, so an SNR is
meaningful as MARGIN over that SF's demodulation floor (Semtech datasheet,
rf.SNR_THRESHOLD_DB). -9 dB is 8.5 dB of margin on LongFast (SF11) and below
the floor on ShortTurbo (SF7) — no absolute threshold is right for both, and
this fleet runs both by design. And reported SNR saturates: p90 +6.75, p99
+7.5, a pile-up at +6.0..+6.75 — above the ceiling, SNR cannot rank links.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.rf import grade_snr, REPORTED_SNR_CEILING_DB, SNR_THRESHOLD_DB  # noqa: E402


def test_ceiling_is_the_measured_one():
    assert REPORTED_SNR_CEILING_DB == 6.0


def test_unknown_snr_is_unknown_never_neutral():
    g = grade_snr(None, sf=11)
    assert g.label == "unknown" and g.score is None and g.margin_db is None


def test_unknown_sf_refuses_to_guess_a_margin():
    g = grade_snr(-9.0)
    assert g.score is None and g.margin_db is None
    assert "preset unknown" in g.text()


def test_at_ceiling_needs_no_sf():
    for sf in (None, 7, 11):
        g = grade_snr(6.75, sf=sf)
        assert g.at_ceiling and g.score == 100.0
        assert "ceiling" in g.text()


def test_median_longfast_link_is_fair_not_bad():
    g = grade_snr(-9.0, sf=11)            # 8.5 dB over the SF11 floor
    assert g.margin_db == pytest.approx(8.5)
    assert g.label == "fair"
    assert 50.0 <= g.score < 75.0


def test_same_snr_on_shortturbo_is_below_floor():
    g = grade_snr(-9.0, sf=7)             # SF7 floor is -7.5
    assert g.margin_db == pytest.approx(-1.5)
    assert g.label == "below floor"


def test_a_decoded_link_is_never_scored_zero():
    for sf, floor in SNR_THRESHOLD_DB.items():
        for snr in (floor - 4.0, floor, floor + 0.1):
            assert grade_snr(snr, sf=sf).score > 0.0


def test_bands_follow_fade_margin():
    assert grade_snr(-17.5 + 12.0, sf=11).label == "good"     # m = 12
    assert grade_snr(-17.5 + 6.0, sf=11).label == "fair"      # m = 6
    assert grade_snr(-17.5 + 2.0, sf=11).label == "edge"      # m = 2


def test_score_is_monotonic_in_snr():
    for sf in SNR_THRESHOLD_DB:
        scores = [grade_snr(x / 4.0, sf=sf).score for x in range(-96, 41)]
        assert scores == sorted(scores), f"SF{sf} score not monotonic"


def test_unknown_sf_value_is_treated_as_unknown():
    g = grade_snr(-9.0, sf=13)
    assert g.score is None and "preset unknown" in g.text()


def test_ceiling_with_known_sf_shows_margin_as_lower_bound():
    g = grade_snr(6.75, sf=11)
    assert "≥+24.2 dB vs SF11 floor" in g.text()


def test_nan_is_unknown_not_good():
    g = grade_snr(float("nan"), sf=11)
    assert g.label == "unknown" and g.score is None


def test_below_every_presets_floor_needs_no_sf():
    # No LoRa preset's floor is lower than SF12's -20 dB, so a reading under
    # it is below floor whatever radio heard it — a claim that needs no SF.
    g = grade_snr(-21.5)
    assert g.label == "below floor" and g.score == 5.0
    assert "below every preset's floor" in g.text()


def test_between_floors_without_sf_stays_unknown():
    assert grade_snr(-19.0).score is None      # below SF7..SF11, above SF12
