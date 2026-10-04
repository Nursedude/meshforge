"""link_quality grades SNR through the ONE grader (SNR stage 1, 2026-10-04).

Before: SNR_EXCELLENT=10 (unreachable — reported SNR saturates ~+6), a
decoded -16 dB LongFast link scored 0 ("unusable"), unknown SNR/RSSI scored a
neutral 50 into the composite, and any SNR below -5 recommended an "antenna
upgrade" — which flags the fleet's MEDIAN LongFast link and contradicts the
dense-site doctrine (placement / a relay hop before gain).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.link_quality import LinkQualityScorer, LinkQuality  # noqa: E402
from utils.rf import grade_snr  # noqa: E402


@pytest.fixture
def scorer():
    return LinkQualityScorer()


def test_snr_component_is_the_shared_grade(scorer):
    s = scorer.score(snr=-9.0, sf=11, rssi=-100, hops=1, age_seconds=30)
    assert s.snr_score == pytest.approx(grade_snr(-9.0, sf=11).score)


def test_unknown_sf_leaves_snr_ungraded_not_neutral(scorer):
    s = scorer.score(snr=-9.0, rssi=-100, hops=1, age_seconds=30)
    assert s.snr_score is None


def test_unknown_components_are_excluded_not_averaged_as_50(scorer):
    known = scorer.score(snr=None, rssi=-80, hops=1, age_seconds=30,
                         announce_count=10)
    # every KNOWN component is at or near its best; a neutral 50 for the
    # missing SNR used to drag this down by ~17 points
    assert known.snr_score is None
    assert known.score > 90.0


def test_no_signal_evidence_at_all_is_unknown_quality(scorer):
    s = scorer.score(snr=None, rssi=None, hops=1, age_seconds=30)
    assert s.quality is LinkQuality.UNKNOWN
    assert s.snr_score is None and s.rssi_score is None


def test_median_longfast_link_gets_no_antenna_upgrade_advice(scorer):
    s = scorer.score(snr=-9.0, sf=11, rssi=-100, hops=1, age_seconds=30)
    assert not any("antenna" in r.lower() for r in s.recommendations)


def test_thin_margin_advice_is_placement_or_a_hop_before_gain(scorer):
    s = scorer.score(snr=-16.0, sf=11, rssi=-118, hops=1, age_seconds=30)
    text = " ".join(s.recommendations).lower()
    assert "relay" in text or "placement" in text
    assert "higher gain" not in text and "antenna upgrade" not in text


def test_a_decoded_link_never_scores_zero_snr(scorer):
    s = scorer.score(snr=-16.0, sf=11, rssi=-118, hops=1, age_seconds=30)
    assert s.snr_score > 0.0


# ---------------------------------------------------------------- the panes
from unittest.mock import patch  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from launcher_tui.handlers import link_quality as lq  # noqa: E402


class _Topo:
    def is_tracking(self):
        return True


def _pane(pane, scores):
    d = FakeDialog()
    h = lq.LinkQualityHandler()
    h.ctx = make_handler_context(dialog=d)
    with patch.object(lq, "get_network_topology", return_value=_Topo()), \
         patch.object(lq, "score_topology_edges", return_value=scores):
        getattr(h, pane)()
    return "\n".join(c[1][1] for c in d.calls if c[0] == "msgbox")


def _mixed():
    sc = LinkQualityScorer()
    return {
        "aaaa_bbbb": sc.score(snr=6.5, rssi=-90, hops=1, age_seconds=30),
        "cccc_dddd": sc.score(snr=0.0, rssi=-118, hops=1, age_seconds=30),
        "eeee_ffff": sc.score(snr=None, rssi=None, hops=1, age_seconds=30),
    }


@pytest.mark.parametrize("pane", ["_show_best_links", "_show_worst_links"])
def test_rankings_never_rank_a_link_with_no_signal_evidence(pane):
    text = _pane(pane, _mixed())
    assert "eeee" not in text
    assert "1 link(s) with no signal evidence" in text


def test_zero_db_is_a_reading_not_na():
    text = _pane("_show_worst_links", _mixed())
    assert "+0.0 dB" in text


def test_no_alerts_does_not_claim_unknown_links_are_fine():
    sc = LinkQualityScorer()
    scores = {"aaaa_bbbb": sc.score(snr=6.5, rssi=-90, hops=1, age_seconds=30),
              "eeee_ffff": sc.score(snr=None, rssi=None, hops=1, age_seconds=30)}
    text = _pane("_show_quality_alerts", scores)
    assert "All links are in fair or better" not in text
    assert "no signal evidence" in text


def test_overview_counts_unknown_separately():
    text = _pane("_show_quality_overview", _mixed())
    assert "Unknown" in text


# ------------------------------------------------ review round 1 (2026-10-04)
# Two contextless reviewers (RF physics; consumer paths) on the first cut.

def test_weak_rssi_with_ungradeable_snr_cannot_read_good(scorer):
    # Reviewer B, CONFIRMED by probe: snr=-9 (no SF) + rssi=-125 renormalised
    # to 72.3 GOOD — dropping SNR's 35% let hops/age/stability carry it.
    s = scorer.score(snr=-9.0, rssi=-125, hops=1, announce_count=60, age_seconds=30)
    assert s.quality in (LinkQuality.POOR, LinkQuality.BAD)


def test_signal_evidence_caps_quality_when_snr_ungraded(scorer):
    s = scorer.score(snr=-9.0, rssi=-80, hops=1, announce_count=60, age_seconds=30)
    assert s.quality in (LinkQuality.GOOD, LinkQuality.EXCELLENT)


def test_unknown_age_and_stability_are_excluded_not_50(scorer):
    s = scorer.score(snr=6.5, rssi=-80, hops=1)        # no age, no announces
    assert s.age_score is None and s.stability_score is None
    assert s.score > 95.0


def test_unknown_link_publishes_no_score(scorer):
    d = scorer.score(snr=None, rssi=None, hops=1, age_seconds=30).to_dict()
    assert d["quality"] == "unknown" and d["score"] is None


def test_default_component_scores_are_unknown_not_worst():
    from utils.link_quality import LinkScore
    ls = LinkScore(score=0.0, quality=LinkQuality.UNKNOWN)
    assert ls.snr_score is None and ls.rssi_score is None


def test_tracker_never_averages_or_alerts_on_unknown(scorer):
    from utils.link_quality import LinkQualityTracker
    t = LinkQualityTracker()
    for _ in range(3):
        t.record("x_y", scorer.score(snr=None, rssi=None, hops=1, age_seconds=30))
    assert t.get_average("x_y") is None
    assert t.get_alerts() == []          # not an alert, and not "fine": unknown


def test_trends_pane_does_not_call_unknown_links_critical():
    sc = LinkQualityScorer()
    scores = {f"n{i}_m{i}": sc.score(snr=None, rssi=None, hops=1, age_seconds=30)
              for i in range(8)}
    scores["a_b"] = sc.score(snr=6.5, rssi=-80, hops=1, age_seconds=30, announce_count=60)
    text = _pane("_show_quality_trends", scores)
    assert "CRITICAL" not in text
    assert "8 link(s) with no signal evidence" in text


def test_trends_pane_with_nothing_known_says_unknown():
    sc = LinkQualityScorer()
    scores = {"n_m": sc.score(snr=None, rssi=None, hops=1, age_seconds=30)}
    text = _pane("_show_quality_trends", scores)
    assert "UNKNOWN" in text and "CRITICAL" not in text


# ------------------------------------------------ review round 2 (2026-10-04)

def test_rssi_cap_applies_to_the_score_not_only_the_label(scorer):
    # Reviewer C: snr=-5 (no SF), rssi=-125 -> "79.0 / poor" — the label was
    # capped, the NUMBER (which alerts, rankings and averages read) was not.
    s = scorer.score(snr=-5.0, rssi=-125, hops=1, announce_count=50, age_seconds=30)
    assert s.score <= scorer._score_rssi(-125)
    assert s.quality in (LinkQuality.POOR, LinkQuality.BAD)


def test_bad_link_does_not_vanish_when_latest_sample_is_unknown(scorer):
    from utils.link_quality import LinkQualityTracker
    t = LinkQualityTracker()
    t.record("x_y", scorer.score(snr=-16.0, sf=11, rssi=-135, hops=6, age_seconds=30))
    t.record("x_y", scorer.score(snr=None, rssi=None, hops=1, age_seconds=30))
    alerts = t.get_alerts()
    assert len(alerts) == 1 and alerts[0]["latest_unobservable"] is True
    assert t.get_stats("x_y")["latest_unobservable"] is True


def test_below_every_floor_caps_at_poor(scorer):
    # review 1b probe: snr=-21 (no SF), no RSSI, direct+fresh+stable read FAIR
    s = scorer.score(snr=-21.0, hops=1, age_seconds=30, announce_count=60)
    assert s.quality in (LinkQuality.POOR, LinkQuality.BAD)


def test_recommendation_text_is_not_nested_parens(scorer):
    s = scorer.score(snr=-16.0, sf=11, rssi=-118, hops=1, age_seconds=30)
    rec = next(r for r in s.recommendations if "Thin SNR margin" in r)
    assert "((" not in rec and "))" not in rec and "dB (" not in rec.split(":", 1)[0]
