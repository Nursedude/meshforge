"""A tracer peer pages only at the PERSISTENT tier (measured 2026-10-09).

30 days of fleet mini history: 369 ``tracer_peer_unreachable_any`` edge_ups,
307 ntfy pages, and every one carried the probe's tier-1 text "Transient —
cold-start or single blip" at ``severity="info"``. The probe tiers correctly
(``persistent_cycles=3`` consecutive misses → ``degraded``); the seed rule
matched the class with no severity key, and ``_match_rule`` gates on nothing
else, so ONE missed 10-minute fire paged the phone — ~12 pages/day while the
LAN fleet measured ≥99.8% reachable over ~72k PINGs.

Observe-before-alarm: alarm on a RUN, never one event. The rule now matches
``severity: degraded``; the info tier stays visible in watchdog.json and the
TUI, and pages nobody.
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


def _rule(seed):
    with open(seed_rules_path(REPO, seed), encoding="utf-8") as fh:
        rules = {r["id"]: r for r in json.load(fh)["rules"]}
    return rules["tracer_peer_unreachable_any"]


def _cond(severity, tier):
    # Shape of a watchdog.json signal as WatchdogJsonSource projects it:
    # every key except subject/detail lands in extras (sources/base.py).
    return Condition(kind="signal_class", subject="peer-a",
                     detail="x",
                     extras={"class": "tracer_peer_unreachable",
                             "severity": severity, "tier": tier})


def test_single_miss_transient_does_not_page():
    for seed in SEEDS:
        rule = _rule(seed)
        assert rule["action"]["kind"] == "ntfy", seed
        assert not _match_rule(rule, _cond("info", "transient")), \
            f"{seed}: a single missed fire must not page"


def test_persistent_absent_and_unresponsive_still_page():
    for seed in SEEDS:
        rule = _rule(seed)
        assert _match_rule(rule, _cond("degraded", "absent")), seed
        assert _match_rule(rule, _cond("degraded", "unresponsive")), seed


def test_rule_gates_on_severity_not_tier():
    # Severity is the probe's contract (degraded = persistent_cycles met);
    # tier is a label for the operator. Pin the key so a future edit that
    # swaps it for ``tier`` must come through here.
    for seed in SEEDS:
        assert _rule(seed)["match"].get("severity") == "degraded", seed
