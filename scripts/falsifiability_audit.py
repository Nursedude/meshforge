#!/usr/bin/env python3
"""Phase 1 of the falsifiability audit — what EVIDENCE exists that each signal
class can be made to fire.

WHY THIS EXISTS (2026-09-02, operator): the fleet's failure mode is not code
crashing, it is an instrument lying or going quiet. Three did in a single
session. The maturity marker for a NOC is therefore not "how many detectors"
but "what fraction of detectors has ever been OBSERVED failing" — and that
number was unknown. `feedback_a_guard_that_never_failed_is_not_evidence`
records eight Issue-#29 contracts that enforced nothing for 891 commits; that
is what an un-drilled detector looks like from the inside.

⚠️ WHAT THIS SCRIPT DOES NOT DO. It does NOT decide falsifiability, and it
never prints the word. It reports only what REFERENCES exist, because the real
question — "would this test still pass if the probe were dead?" — is a
judgement about what a test actually exercises, and a script answering it by
pattern-match would manufacture exactly the false coverage number this audit
exists to expose. A blessed-blind detector is worse than a known-blind one,
because the known one stays on the worklist. So:

    this script  -> mechanical inventory (phase 1, no model needed)
    frontier pass -> the verdict per class (phase 2, adversarial_review)
    opus         -> write the missing drills (phase 3, probe_authoring)

PHASE 2 RAN 2026-09-02 and the polarity axis below was wrong about itself
THREE more ways than the worked example records: it misses `assert x == []`
(list-returning probes), multi-line `assert probe(...) is None`, and tests
that reach the probe through a wrapper helper (`_prop_probe`, `_lpd`), so
five of its sixteen "not both" rows were drilled both ways all along. The
MEASUREMENT of falsifiability is `scripts/falsifiability_drill.py` (it kills
each probe and runs the tests); this inventory stays a cheap first look and
its polarity column is advisory. Its numbers are never carried into a claim.

Evidence tiers, weakest first — the ORDER is the worklist:

  no-reference    the class name appears in no test and no eval. It cannot be
                  drilled by anything; nothing needs judging. Strongest finding
                  this script can produce on its own.
  enum-only       named ONLY in a coverage/enum gate. Those assert the class is
                  DECLARED, never that a probe emits it — presence, not function.
  referenced      named in a test, but no assertion found that a probe RETURNED
                  it. Might drill it indirectly; that is a phase-2 read.
  fire-asserted   a test asserts `.cls == "<class>"` — a probe returned this
                  class. CANDIDATE only: phase 2 still has to ask whether the
                  assertion would survive the probe being gutted.

SECOND AXIS — polarity. The tier above asks "has it ever been seen to FIRE".
That is half an instrument. `feedback_a_guard_that_never_failed_is_not_evidence`
is about the missing half in one direction; the frozen-GREEN detector is the
missing half in the other, and it is the dangerous one — a probe that can only
be shown firing has never been shown to stay correctly SILENT, so nothing
catches it going permanently loud (the 2026-09-02 silence-watch latch), and a
probe only ever shown silent has never been shown to fire at all. This axis is
mechanical and worth more than the tier:

  both       the probe's tests assert BOTH a signal and a None/clean return
  fire-only  only ever shown firing — never shown to stay quiet
  none-only  only ever shown quiet — never shown to fire
  unknown    no direct probe_*() call found to judge from

⚠️ A WORKED EXAMPLE OF THIS SCRIPT BEING WRONG, kept deliberately. On its first
run the weakest-ranked class was `service_inactive` — the single most
load-bearing probe on the fleet. Inspection showed it IS drilled in BOTH
polarities; it simply never asserts `.cls`, which is all the tier heuristic can
see. The weakest finding was an artifact of how the audit was written, not of
the code. That is exactly the defect class this audit exists to surface, landing
in the audit itself, and it is why phase 1 may never emit a falsifiability
verdict — only evidence, ranked, for a human or a frontier pass to judge.

Usage:
    python3 scripts/falsifiability_audit.py                 # ranked table
    python3 scripts/falsifiability_audit.py --worklist      # markdown for the
                                                            # frontier queue
    python3 scripts/falsifiability_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TESTS = ROOT / "tests"
EVALS = ROOT / "evals"
# Files that assert a class is DECLARED rather than that it fires. Being named
# only here is the "enum-only" tier — a closed-enum gate is real coverage of a
# different question (honest_failure_modes #7) and must not be mistaken for a
# fire drill.
ENUM_GATES = {"test_watchdog_coverage.py"}

TIERS = ("no-reference", "enum-only", "referenced", "fire-asserted")


def signal_classes() -> list:
    from utils.watchdog_probe_core import SIGNAL_CLASSES
    return sorted(SIGNAL_CLASSES)


def _scan(paths):
    out = {}
    for f in paths:
        try:
            out[f] = f.read_text(errors="ignore")
        except OSError:
            continue
    return out


def polarity(txt: str, cls: str) -> str:
    """Which outcomes of this class's probe do its tests actually exercise?

    Looks for a probe_*() call whose nearby assertions cover a returned signal
    and/or a None return. Deliberately conservative: anything it cannot trace
    to a probe call reads `unknown`, never `both` — an audit that guesses
    generously is the failure it is auditing.
    """
    fired = quiet = seen = False
    lines = txt.splitlines()
    # The call must be THIS class's probe, not any probe in the file. Without
    # this the big shared test module lends its both-polarity coverage to every
    # class it merely mentions, and the axis reads 58/58 `both` — a saturated
    # metric that discriminates nothing (found 2026-09-02, first run of this
    # very check; the same defect it audits for).
    call = re.compile(r"\bprobe_" + re.escape(cls) + r"\s*\(")
    for i, l in enumerate(lines):
        if not call.search(l):
            continue
        win = "\n".join(lines[i:i + 10])
        seen = True
        if re.search(r"assert\s+\w+\s+is\s+None|==\s*\[\]", win):
            quiet = True
        if re.search(r"assert\s+\w+\s+is\s+not\s+None|\.cls\s*==|\.severity\s*==", win):
            fired = True
    if not seen:
        return "unknown"
    if fired and quiet:
        return "both"
    if fired:
        return "fire-only"
    if quiet:
        return "none-only"
    return "unknown"


def audit() -> list:
    classes = signal_classes()
    tests = _scan(sorted(TESTS.rglob("*.py")) + sorted(TESTS.rglob("*.sh")))
    evals = _scan(sorted(EVALS.rglob("*.jsonl"))) if EVALS.is_dir() else {}

    rows = []
    for cls in classes:
        q = re.escape(cls)
        # A probe RETURNED this class. Both orderings, and the dict form some
        # probes use, because one spelling would silently under-report.
        fire_re = re.compile(
            rf'(?:\.cls\s*==\s*["\']{q}["\']'
            rf'|["\']{q}["\']\s*==\s*\w*\.cls'
            rf'|["\']cls["\']\s*:\s*["\']{q}["\'])'
        )
        fire, ref, enum = [], [], []
        for f, txt in tests.items():
            if cls not in txt:
                continue
            if fire_re.search(txt):
                fire.append(f.name)
            elif f.name in ENUM_GATES:
                enum.append(f.name)
            else:
                ref.append(f.name)
        ev = sorted({f.name for f, t in evals.items() if cls in t})

        if fire:
            tier = "fire-asserted"
        elif ref:
            tier = "referenced"
        elif enum:
            tier = "enum-only"
        else:
            tier = "no-reference"
        pol = "unknown"
        for f, txt in tests.items():
            if cls not in txt:
                continue
            p2 = polarity(txt, cls)
            if p2 == "both":
                pol = "both"
                break
            if p2 != "unknown" and pol == "unknown":
                pol = p2
            elif {pol, p2} == {"fire-only", "none-only"}:
                pol = "both"
                break
        rows.append({
            "cls": cls, "tier": tier, "polarity": pol,
            "fire_tests": sorted(set(fire)),
            "ref_tests": sorted(set(ref)),
            "enum_tests": sorted(set(enum)),
            "evals": ev,
        })
    rows.sort(key=lambda r: (TIERS.index(r["tier"]), r["cls"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--worklist", action="store_true",
                    help="markdown for .claude/audits/review_provenance.md")
    a = ap.parse_args()
    rows = audit()
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in TIERS}
    pols = {}
    for r in rows:
        pols[r["polarity"]] = pols.get(r["polarity"], 0) + 1
    n = len(rows)

    if a.json:
        print(json.dumps({"total": n, "counts": counts, "rows": rows}, indent=2))
        return 0

    if a.worklist:
        print("### Falsifiability audit — phase 1 inventory "
              f"({n} signal classes)\n")
        print("Generated by `scripts/falsifiability_audit.py`. This is "
              "REFERENCE evidence only — phase 1 makes no falsifiability "
              "claim.\n")
        pol = {}
        for r in rows:
            pol[r["polarity"]] = pol.get(r["polarity"], 0) + 1
        # NO pipe tables here. review_provenance.md's rows are PARSED — 5 cells
        # for a completed pass, 3 for a worklist row — so a 2-cell summary table
        # is a parser-invisible line and a 3-cell one is silently read AS a
        # worklist row. The pre-push upshift-witness gate rejects both, and it is
        # right to: "a merged line hid a whole completed pass for 12 days".
        print("**Polarity is the axis that matters** — a probe shown only firing "
              "has never been shown to stay correctly silent, and vice versa.\n")
        print("Polarity counts: "
              + "; ".join(f"`{k}` {pol[k]}" for k in
                          ("both", "fire-only", "none-only", "unknown") if k in pol)
              + ".\n")
        gaps = [r for r in rows if r["polarity"] != "both"]
        if gaps:
            print(f"#### Phase-2 worklist — {len(gaps)} classes not shown in BOTH polarities\n")
            for r in gaps:
                where = ", ".join(f"`{x}`" for x in (r["fire_tests"] or r["ref_tests"])[:2])
                print(f"- `{r['cls']}` — polarity `{r['polarity']}`; {where}")
            print()
        owes = {
            "no-reference": "nothing to judge — needs a drill WRITTEN (phase 3)",
            "enum-only": "confirm the enum gate is the only cover, then drill",
            "referenced": "**the real judgement** — does the test drill it, or only import it?",
            "fire-asserted": "adversarial read: would the assertion survive the probe being gutted?",
        }
        print("Reference tiers, and what phase 2 owes each:\n")
        for t in TIERS:
            print(f"- `{t}` ({counts[t]}) — {owes[t]}")
        print()
        for t in TIERS:
            sel = [r for r in rows if r["tier"] == t]
            if not sel:
                continue
            print(f"\n#### {t} ({len(sel)})\n")
            for r in sel:
                bits = []
                if r["fire_tests"]:
                    bits.append("fires in " + ", ".join(f"`{x}`" for x in r["fire_tests"]))
                if r["ref_tests"]:
                    bits.append("named in " + ", ".join(f"`{x}`" for x in r["ref_tests"][:3])
                                + ("…" if len(r["ref_tests"]) > 3 else ""))
                if r["enum_tests"]:
                    bits.append("enum gate only")
                if r["evals"]:
                    bits.append("eval: " + ", ".join(f"`{x}`" for x in r["evals"]))
                print(f"- `{r['cls']}` — {'; '.join(bits) or 'no test, no eval'}")
        return 0

    print(f"falsifiability audit — {n} signal classes "
          f"(phase 1: reference evidence only, no falsifiability claim)\n")
    for t in TIERS:
        print(f"  {t:<14} {counts[t]:>3}")
    print("\n  polarity (the half that matters):")
    for k in ("both", "fire-only", "none-only", "unknown"):
        if k in pols:
            print(f"  {k:<14} {pols[k]:>3}")
    print()
    for r in rows:
        extra = ""
        if r["tier"] == "fire-asserted":
            extra = " <- " + ", ".join(r["fire_tests"][:2])
        elif r["tier"] == "referenced":
            extra = " <- " + ", ".join(r["ref_tests"][:2])
        elif r["tier"] == "enum-only":
            extra = " <- " + ", ".join(r["enum_tests"][:2])
        ev = f" [eval x{len(r['evals'])}]" if r["evals"] else ""
        print(f"  {r['tier']:<14} {r['polarity']:<10} {r['cls']:<38}{ev}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
