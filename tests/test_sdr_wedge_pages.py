"""An SDR receiver wedge PAGES (operator, 2026-10-08: "yes page the SDR wedge").

``cd7f929c`` made a receiver that yields no samples exit 1, so a wedge surfaces
as ``user_timer_unit_failing`` with subject ``meshforge-sdr.service``. The
catch-all rule for that class is a side-effect-free escalation — on 10-08
moc1's RTL-SDR wedged at 15:33 and nothing paged for 7.5 h.

So the SDR subject gets its own ntfy rule, and the catch-all excludes it so one
wedge is one page, not a page plus a duplicate escalation.

⚠️ Known gap, pinned below so it is a decision and not a surprise: the probe
collapses the subject to ``"N units"`` when more than one user timer fails at
once, and that subject matches only the catch-all (escalation).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mini_dudeai.candidate import seed_rules_path  # noqa: E402
from mini_dudeai.engine import _match_rule  # noqa: E402
from mini_dudeai.sources.base import Condition  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = ("fleet_gateway", "federator")


def _rules(seed):
    with open(seed_rules_path(REPO, seed), encoding="utf-8") as fh:
        return {r["id"]: r for r in json.load(fh)["rules"]}


def _cond(subject):
    return Condition(kind="signal_class", subject=subject,
                     extras={"class": "user_timer_unit_failing"})


def test_an_sdr_wedge_pages_and_does_not_double_escalate():
    for seed in SEEDS:
        rules = _rules(seed)
        page = rules["sdr_receiver_wedged_any"]
        assert page["action"]["kind"] == "ntfy"
        cond = _cond("meshforge-sdr.service")
        assert _match_rule(page, cond)
        assert not _match_rule(rules["user_timer_unit_failing_any"], cond)


def test_other_failing_timers_still_escalate_quietly():
    for seed in SEEDS:
        rules = _rules(seed)
        cond = _cond("meshforge-tracer.service")
        assert not _match_rule(rules["sdr_receiver_wedged_any"], cond)
        assert _match_rule(rules["user_timer_unit_failing_any"], cond)
        assert rules["user_timer_unit_failing_any"]["action"]["kind"] == \
            "propose_escalation"


def test_known_gap_collapsed_subject_only_escalates():
    for seed in SEEDS:
        rules = _rules(seed)
        cond = _cond("2 units")
        assert not _match_rule(rules["sdr_receiver_wedged_any"], cond)
        assert _match_rule(rules["user_timer_unit_failing_any"], cond)
