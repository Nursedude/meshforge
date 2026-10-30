"""GATE: no TUI action lands unclassified (2026-10-30).

The rule "every feature owes a TUI surface, verified" lived as prose in
memory and drifted: on 10-30, 112 of 121 actions had never been dispatched
by anything. Prose drifts, gates hold. Every action in the handler manifest
must sit in EXACTLY ONE of:

  ALLOWLIST        hand-read, dispatched by scripts/tui_leaf_walk.py
  DENYLIST         blast radius holds the walk itself (reboot)
  NOT_WALKED       read, and deliberately not dispatched — written reason
  LEGACY_UNPROVEN  the frozen 10-30 baseline: never read. SHRINK-ONLY.

A NEW action therefore cannot join the never-read pile: whoever adds it must
read it and say which set it belongs in. The baseline may only lose entries
(an entry that is also classified elsewhere, or no longer in the manifest,
fails here until it is deleted) — the MF025 frozen-baseline shape.

Static manifest only — no handler is dispatched, so this runs in CI.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts")]

import tui_leaf_walk as w  # noqa: E402

MIN_REASON = 20


def _manifest():
    return {(s, t) for s, t, _d in w.all_actions()}


def _sets():
    return {
        "ALLOWLIST": set(w.ALLOWLIST),
        "DENYLIST": set(w.DENYLIST),
        "NOT_WALKED": set(w.NOT_WALKED),
        "LEGACY_UNPROVEN": set(w.LEGACY_UNPROVEN),
    }


def test_every_manifest_action_is_classified():
    classified = set().union(*_sets().values())
    missing = sorted(_manifest() - classified)
    assert not missing, (
        "TUI action(s) with no classification in scripts/tui_leaf_walk.py: "
        f"{missing}. Read the handler (follow function-local imports), then "
        "add it to ALLOWLIST (read-only to its first prompt — walk it: "
        "`python3 scripts/tui_leaf_walk.py --only TAG`) or NOT_WALKED (with "
        "the reason it must not be dispatched). LEGACY_UNPROVEN is closed.")


def test_every_action_is_in_at_most_one_set():
    sets = _sets()
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = sorted(sets[a] & sets[b])
            assert not both, f"in {a} AND {b}: {both} — pick one"


def test_legacy_baseline_only_names_live_actions():
    stale = sorted(set(w.LEGACY_UNPROVEN) - _manifest())
    assert not stale, (f"LEGACY_UNPROVEN names action(s) no longer in the "
                       f"manifest: {stale} — delete them (shrink-only)")


def test_legacy_baseline_never_grows():
    # 10-30 baseline 105 (121 - 15 allowlisted - 1 denylisted); 85 after the
    # same-day diagnostics batch. Lower
    # this number when the baseline shrinks; it must never be raised.
    assert len(w.LEGACY_UNPROVEN) <= 85


def test_every_reason_is_written():
    for name in ("ALLOWLIST", "DENYLIST", "NOT_WALKED"):
        for key, why in getattr(w, name).items():
            assert isinstance(why, str) and len(why.strip()) >= MIN_REASON, (
                f"{name}{key}: reason missing or too short — say what was "
                f"read and why")


def test_gate_fails_on_a_planted_new_action(monkeypatch):
    # A gate that never failed is not evidence: plant an unclassified action.
    real = w.all_actions
    monkeypatch.setattr(w, "all_actions",
                        lambda: real() + [("dashboard", "planted_new", "x")])
    try:
        test_every_manifest_action_is_classified()
    except AssertionError as e:
        assert "planted_new" in str(e)
    else:
        raise AssertionError("gate passed with an unclassified action")


def test_partition_names_an_unclassified_action_never_legacy():
    # review 10-30 #6: --list derived "legacy" as "not in the other sets",
    # so an unclassified action hid inside the shrink-only baseline.
    acts = w.all_actions() + [("dashboard", "planted_new", "x")]
    b = w.partition(acts)
    assert ("dashboard", "planted_new") in {(s, t) for s, t, _ in b["unclassified"]}
    assert ("dashboard", "planted_new") not in {(s, t) for s, t, _ in b["legacy"]}


def test_partition_buckets_cover_every_action_once():
    acts = w.all_actions()
    b = w.partition(acts)
    seen = [(s, t) for k in b for s, t, _ in b[k]]
    assert sorted(seen) == sorted((s, t) for s, t, _ in acts)
    assert len(b["legacy"]) == len(w.LEGACY_UNPROVEN)
