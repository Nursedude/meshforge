"""health_score grades SNR through the ONE grader (SNR stage 1, 2026-10-04).

Its own constants (SNR_EXCELLENT=-5, GOOD=-10, FAIR=-15) scored the fleet's
median LongFast link (-9 dB) ~77 while link_quality scored it ~29 and the
topology pane called it "Bad". Nodes carry no SF, so a below-ceiling SNR is
ungradeable here and is EXCLUDED, never averaged in.

⚠️ Measured while writing this: report_node_metrics() has NO caller in src/,
so in production this scorer never sees a node and the heartbeat's
performance subscore is a constant 50 ("no node metrics"). These tests pin
the grading for when it is fed; the constant is a separate finding.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.health_score import HealthScorer  # noqa: E402
from utils.rf import grade_snr  # noqa: E402


def _scorer():
    return HealthScorer()


def test_snr_to_score_is_the_shared_grade():
    hs = _scorer()
    assert hs._snr_to_score(6.75) == grade_snr(6.75).score == 100.0
    assert hs._snr_to_score(-9.0) is None          # no SF -> not knowable


def test_ungradeable_snr_is_excluded_from_performance():
    hs = _scorer()
    hs.report_node_metrics("!a", snr=-9.0, rssi=-80)   # SNR ungradeable, RSSI best
    score, details = hs._score_performance()
    assert details["snr_ungraded"] == 1
    assert score == details["avg_rssi_score"]          # RSSI alone, no neutral 50


def test_no_signal_at_all_is_flagged_unobservable():
    hs = _scorer()
    hs.report_node_metrics("!a", snr=None, rssi=None)
    _score, details = hs._score_performance()
    assert details["signal_unobservable"] is True


def test_node_health_skips_ungradeable_snr():
    hs = _scorer()
    hs.report_node_metrics("!a", snr=-9.0, rssi=-80, battery_level=100)
    hs.report_node_metrics("!b", snr=None, rssi=-80, battery_level=100)
    assert hs.get_node_health("!a") == hs.get_node_health("!b")


def test_ceiling_readings_alone_do_not_stand_for_the_network():
    # Reviewer A (RF), must-fix: with no SF only at-ceiling readings grade,
    # all at 100 — one co-located node at +7 dB beside nine at -15 dB made
    # the network's SNR leg read 100. Average only with FULL coverage.
    hs = _scorer()
    hs.report_node_metrics("!near", snr=7.0, rssi=-60)
    for i in range(9):
        hs.report_node_metrics(f"!far{i}", snr=-15.0, rssi=-118)
    _score, details = hs._score_performance()
    assert details["avg_snr_score"] is None
    assert details["snr_graded"] == 1 and details["snr_ungraded"] == 9
