"""probe_mini_rule_orphaned_exclusion — the LIVE half of a rule retirement.

WHY THIS EXISTS (2026-09-03): retiring `moc3_federation_backoff_known_normal`
had two halves — the suppression rule, and the `*moc3*` entry in
`federation_peer_unhealthy_unexpected`'s `subject_exclude_globs`. An exclusion
is a deliberate blind spot that is only safe while another rule OWNS that
subject; drop the owner and keep the exclusion and a real event on that subject
is silently dropped, with the box reading clean.

`merge_seed_rules` is strictly ADDITIVE and `promote_seed_rules --prune` keeps
box-TUNED rules verbatim BY DESIGN, so a retirement lands fully on every box
running an unmodified seed copy and HALF on any box that tuned the rule. The
federator hit exactly that: its catch-all carries operator-specific
MeshAnchor-peer globs (MF014, deliberately absent from the repo seed), so the
merge kept its copy and the dead `*moc3*` glob outlived the rule.

Seed-side halves are guarded by TestFederationPerVantageRow6. Nothing guarded
the LIVE box copies — the half that actually runs. This is that guard.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_mini import (  # noqa: E402
    probe_mini_rule_orphaned_exclusion,
)

CLS = "mini_rule_orphaned_exclusion"
KIND = "federation_peer_unhealthy"


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


def _catchall(excludes=None):
    m = {"kind": KIND, "subject_glob": "*"}
    if excludes is not None:
        m["subject_exclude_globs"] = list(excludes)
    return {"id": "federation_peer_unhealthy_unexpected", "match": m,
            "action": {"kind": "propose_escalation"}}


def _owner(rid="moc3_federation_backoff_known_normal", glob="*moc3*",
           kind=KIND, **extra):
    m = {"kind": kind, "peer_glob": glob}
    m.update(extra)
    return {"id": rid, "match": m, "action": {"kind": "annotate_digest"}}


def _write(tmp_path, rules):
    (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": rules}))
    return str(tmp_path)


# ── the defect this exists for ────────────────────────────────────────
def test_exclusion_whose_owner_was_retired_is_degraded(dispositions):
    """THE half-landed retirement: rule gone, glob left behind."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_catchall(["*moc3*"])])
    assert sig is not None
    assert sig.cls == CLS and sig.severity == "degraded"
    assert "*moc3*" in sig.detail
    assert sig.extra["orphaned"] == ["federation_peer_unhealthy_unexpected:*moc3*"]


def test_both_halves_present_is_clean(dispositions):
    """The pre-retirement steady state — exclusion + its owner."""
    sig = probe_mini_rule_orphaned_exclusion(
        live_rules=[_catchall(["*moc3*"]), _owner()])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_both_halves_retired_is_clean(dispositions):
    """The post-retirement steady state — neither half remains."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_catchall()])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


# ── the false positive that would make it unusable ────────────────────
def test_box_local_owner_pattern_reads_clean(dispositions):
    """The DOCUMENTED MF014 pattern: a box-local *_known_normal rule plus its
    matching exclude. Operator-specific, never in the repo seed, and entirely
    legitimate — the local rule IS the owner. If this fired, the manager box
    would page on its own sanctioned config."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[
        _catchall(["*meshanchor-server*"]),
        _owner(rid="meshanchor_server_federation_known_normal",
               glob="*meshanchor-server*"),
    ])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


# ── ownership is structural, not nominal ──────────────────────────────
def test_owner_of_a_different_kind_does_not_count(dispositions):
    """A rule watching the same NAME under another signal kind cannot own the
    exclusion — the blind spot is per-kind."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[
        _catchall(["*moc3*"]), _owner(kind="service_inactive")])
    assert sig is not None


def test_a_would_be_owner_that_also_excludes_the_core_does_not_count(dispositions):
    """Two catch-alls that BOTH exclude the subject own nothing between them —
    the hole is still open."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[
        _catchall(["*moc3*"]),
        {"id": "second_catchall",
         "match": {"kind": KIND, "subject_glob": "*",
                   "subject_exclude_globs": ["*moc3*"]},
         "action": {"kind": "propose_escalation"}},
    ])
    assert sig is not None


def test_a_rule_cannot_own_its_own_exclusion(dispositions):
    """Self-ownership would make every exclusion look covered."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_catchall(["*moc3*"])])
    assert sig is not None


def test_kind_only_rule_counts_as_an_owner(dispositions):
    """A rule with no selector matches every subject of its kind, so the
    subject is still watched — not a blind spot."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[
        _catchall(["*moc3*"]),
        {"id": "kind_only", "match": {"kind": KIND},
         "action": {"kind": "annotate_digest"}},
    ])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_no_exclusions_anywhere_is_clean(dispositions):
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_owner()])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


# ── tri-state honesty: absent != unreadable != clean ──────────────────
def test_missing_rules_file_is_inert(tmp_path, dispositions):
    sig = probe_mini_rule_orphaned_exclusion(mini_home=str(tmp_path))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "inert"


def test_corrupt_rules_file_is_indeterminate_not_clean(tmp_path, dispositions):
    """A present-but-corrupt file must not read as 'no exclusions' — an empty
    ruleset and an unreadable one must never agree (honest_failure_modes #1)."""
    (tmp_path / "mini_dudeai_rules.json").write_text("{not json")
    sig = probe_mini_rule_orphaned_exclusion(mini_home=str(tmp_path))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_bare_star_exclusion_is_indeterminate_not_clean(dispositions):
    """No owner is nameable for 'everything'. Counted, never silently dropped
    into an affirmative clean."""
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_catchall(["*"])])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_reads_the_live_file_from_disk(tmp_path, dispositions):
    """End-to-end through the real read path, not just the injection seam."""
    home = _write(tmp_path, [_catchall(["*moc3*"])])
    sig = probe_mini_rule_orphaned_exclusion(mini_home=home)
    assert sig is not None and "*moc3*" in sig.detail
