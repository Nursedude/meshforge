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


# ── 2026-09-03 frontier pass: the ENGINE is the consumer of record ─────
# The first cut judged ownership with its own glob-core solver. Against
# engine._match_rule (the thing that actually decides what mini watches) it
# read a hole as owned in two shapes and paged on an owned subject in three.
# Every case below was first confirmed as a disagreement between the probe
# and the engine on the same rules, then pinned.

def _state(*subjects, rid="retired_owner"):
    """A mini state.json fragment: rule-state keys are rule_id::subject and
    survive the rule's retirement (STALE_KEY_RETENTION_S) — which is what makes
    the retirement case judgeable on a subject the box has actually seen."""
    return {"rules": {f"{rid}::{s}": {"rule_id": rid, "subject": s}
                      for s in subjects}}


def test_owner_filtering_a_different_class_extra_is_not_an_owner(dispositions):
    """kind=signal_class carries a `class` extras filter on 60 of this box's
    68 live rules. An owner whose class filter differs never sees the excluded
    subject in the engine — crediting it hides a real hole (false CLEAN)."""
    rules = [
        {"id": "drift_catchall",
         "match": {"kind": "signal_class", "class": "rns_version_drift",
                   "subject_glob": "*", "subject_exclude_globs": ["*moc3*"]},
         "action": {"kind": "propose_escalation"}},
        {"id": "other_class",
         "match": {"kind": "signal_class", "class": "service_inactive",
                   "subject_glob": "*"},
         "action": {"kind": "ntfy"}},
    ]
    sig = probe_mini_rule_orphaned_exclusion(live_rules=rules)
    assert sig is not None, "an owner of a DIFFERENT class was credited"


def test_owner_with_the_same_class_extra_counts(dispositions):
    rules = [
        {"id": "drift_catchall",
         "match": {"kind": "signal_class", "class": "rns_version_drift",
                   "subject_glob": "*", "subject_exclude_globs": ["*moc3*"]},
         "action": {"kind": "propose_escalation"}},
        {"id": "moc3_drift_known_normal",
         "match": {"kind": "signal_class", "class": "rns_version_drift",
                   "subject_glob": "*moc3*"},
         "action": {"kind": "annotate_digest"}},
    ]
    assert probe_mini_rule_orphaned_exclusion(live_rules=rules) is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_engine_selector_precedence_subject_glob_wins_over_peer_glob(dispositions):
    """The engine reads ONE selector (subject_glob, else peer_glob, else
    source_glob). A rule carrying a missing subject_glob AND a matching
    peer_glob matches nothing in the engine and must not be credited."""
    rules = [_catchall(["*moc3*"]),
             {"id": "half_migrated",
              "match": {"kind": KIND, "subject_glob": "*moc4*",
                        "peer_glob": "*moc3*"},
              "action": {"kind": "annotate_digest"}}]
    assert probe_mini_rule_orphaned_exclusion(live_rules=rules) is not None


def test_empty_subject_glob_is_a_catch_all_like_the_engine(dispositions):
    """`subject_glob: ""` falls through to "*" in the engine; the first cut
    read it as a selector that matches nothing and paged (false page)."""
    rules = [_catchall(["*moc3*"]),
             {"id": "empty_sel", "match": {"kind": KIND, "subject_glob": ""},
              "action": {"kind": "annotate_digest"}}]
    assert probe_mini_rule_orphaned_exclusion(live_rules=rules) is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_specific_owner_is_judged_on_the_observed_subject(dispositions):
    """peer_glob 'peer-moc3' owns the only real subject the exclusion
    '*moc3*' ever blinds. The synthetic core 'moc3' is NOT a subject; judging on
    it paged the operator about a hole that does not exist."""
    rules = [_catchall(["*moc3*"]), _owner(glob="peer-moc3")]
    sig = probe_mini_rule_orphaned_exclusion(
        live_rules=rules, live_state=_state("peer-moc3"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_metachar_exclusion_without_an_observed_subject_is_unjudgeable(dispositions):
    """'*moc?*' has no literal core to synthesise; with no observed subject in
    scope the honest answer is 'cannot judge', named — never a page, never clean."""
    rules = [_catchall(["*moc?*"]), _owner(glob="*moc3*")]
    sig = probe_mini_rule_orphaned_exclusion(live_rules=rules, live_state={})
    assert sig is None
    d = dispositions()[CLS]
    assert d["disp"] == "indeterminate" and "*moc?*" in (d.get("reason") or "")


def test_metachar_exclusion_is_judged_once_a_subject_was_observed(dispositions):
    rules = [_catchall(["*moc?*"]), _owner(glob="*moc3*")]
    sig = probe_mini_rule_orphaned_exclusion(
        live_rules=rules, live_state=_state("peer-moc3"))
    assert sig is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_bare_star_beside_an_owned_exclusion_is_not_swallowed(dispositions):
    """The author's own known defect: one owned exclusion plus one bare '*'
    used to note a flat clean and drop the un-analysable one (hfm #9)."""
    rules = [_catchall(["*moc3*", "*"]), _owner()]
    sig = probe_mini_rule_orphaned_exclusion(live_rules=rules, live_state={})
    assert sig is None
    d = dispositions()[CLS]
    assert d["disp"] == "indeterminate"
    assert "federation_peer_unhealthy_unexpected:*" in (d.get("reason") or "")


def test_orphaned_verdict_names_the_witness_subject(dispositions):
    sig = probe_mini_rule_orphaned_exclusion(
        live_rules=[_catchall(["*moc3*"])], live_state=_state("peer-moc3"))
    assert sig is not None
    assert "peer-moc3" in sig.detail
    assert sig.extra["orphaned"] == ["federation_peer_unhealthy_unexpected:*moc3*"]
    assert sig.extra["witness"] == {"federation_peer_unhealthy_unexpected:*moc3*":
                                    "observed subject 'peer-moc3'"}


def test_partial_hole_is_caught_on_an_observed_subject(dispositions):
    """Owner '*' that itself excludes a NARROWER sub-glob: the wide exclusion
    still blinds 'peer-moc3-gw' and nothing owns it."""
    rules = [_catchall(["*moc3*"]),
             {"id": "owner", "match": {"kind": KIND, "subject_glob": "*",
                                        "subject_exclude_globs": ["*moc3-gw*"]},
              "action": {"kind": "annotate_digest"}}]
    sig = probe_mini_rule_orphaned_exclusion(
        live_rules=rules, live_state=_state("peer-moc3", "peer-moc3-gw"))
    assert sig is not None
    assert "peer-moc3-gw" in sig.detail


def test_engine_matcher_unavailable_is_indeterminate(dispositions, monkeypatch):
    """No engine, no judgement: never fall back to a private re-typing of the
    match semantics (that re-typing is what this pass found wrong)."""
    monkeypatch.setitem(sys.modules, "mini_dudeai.engine", None)
    sig = probe_mini_rule_orphaned_exclusion(live_rules=[_catchall(["*moc3*"])])
    assert sig is None
    assert dispositions()[CLS]["disp"] == "indeterminate"


def test_reads_observed_subjects_from_the_state_file_on_disk(tmp_path, dispositions):
    home = _write(tmp_path, [_catchall(["*moc3*"]), _owner(glob="peer-moc3")])
    (tmp_path / "mini_dudeai_state.json").write_text(
        json.dumps(_state("peer-moc3")))
    assert probe_mini_rule_orphaned_exclusion(mini_home=home) is None
    assert dispositions()[CLS]["disp"] == "clean"


def test_unreadable_state_file_degrades_to_structural_and_says_so(tmp_path, dispositions):
    """Observed subjects are an enrichment; losing them must leave a witness in
    the reason, never silently change which method judged."""
    home = _write(tmp_path, [_catchall(["*moc3*"]), _owner()])
    (tmp_path / "mini_dudeai_state.json").write_text("{corrupt")
    assert probe_mini_rule_orphaned_exclusion(mini_home=home) is None
    d = dispositions()[CLS]
    assert d["disp"] == "clean" and "state" in (d.get("reason") or "")
