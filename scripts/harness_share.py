#!/usr/bin/env python3
"""harness_share.py — where did the commits go? (harness vs product, by window)

The pinned definition from .claude/plans/post_freeze_harness_budget_2026_10_09.md §1,
so two sessions never again disagree about the number they are both deciding on
(09-09 said 53%, 09-10 said 47%, same window — different unstated definitions).

Each commit is classified by the MAJORITY of files it touches:
  harness : .claude/**  src/mini_dudeai/**  src/utils/watchdog**  evals/**
            scripts/{*audit*,*verdict*,claim_gate*,parity_check*,honest_status*,*drill*,*calibration*}
  product : src/**  templates/**  requirements/**  docs/**   (after the harness match)
  tests/** are NOT classified — a test follows its subject, it is not a class.
Ties and no-match land in neither/mixed, which is REPORTED, never folded into a side.

This is a hand-run measurement for a human reading it. It is deliberately NOT a
probe, signal class, cron, hook or gate — a detector that watches the harness
share would be machinery watching machinery (feedback_my_footprint_is_the_constraint),
and a ratio gate is a freeze in disguise; the 2026-09-09→10-09 freeze, measured
on 10-09, showed that axis never bound (post_freeze_harness_budget §8).

Usage:  python3 scripts/harness_share.py 2026-09-09 2026-10-09
        python3 scripts/harness_share.py --days 30
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import subprocess
import sys

HARNESS_SCRIPT_GLOBS = ("*audit*", "*verdict*", "claim_gate*", "parity_check*",
                        "honest_status*", "*drill*", "*calibration*")
HARNESS_PREFIXES = (".claude/", "src/mini_dudeai/", "src/utils/watchdog", "evals/")
PRODUCT_PREFIXES = ("src/", "templates/", "requirements/", "docs/")


def classify(path: str) -> str | None:
    if path.startswith("tests/"):
        return None
    if path.startswith(HARNESS_PREFIXES):
        return "harness"
    if path.startswith("scripts/") and any(
            fnmatch.fnmatch(path.rsplit("/", 1)[-1], g) for g in HARNESS_SCRIPT_GLOBS):
        return "harness"
    if path.startswith(PRODUCT_PREFIXES):
        return "product"
    return None


def measure(since: str, until: str) -> dict:
    out = subprocess.run(
        ["git", "log", "--no-merges", f"--since={since}", f"--until={until}",
         "--format=%x00%H", "--name-only"],
        capture_output=True, text=True, timeout=60, check=True).stdout
    counts = {"harness": 0, "product": 0, "neither": 0}
    for chunk in out.split("\x00"):
        lines = [ln for ln in chunk.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        votes = {"harness": 0, "product": 0}
        for f in lines[1:]:
            k = classify(f)
            if k:
                votes[k] += 1
        if votes["harness"] > votes["product"]:
            counts["harness"] += 1
        elif votes["product"] > votes["harness"]:
            counts["product"] += 1
        else:
            counts["neither"] += 1
    counts["total"] = sum(counts[k] for k in ("harness", "product", "neither"))
    counts["days"] = max((_dt.date.fromisoformat(until) - _dt.date.fromisoformat(since)).days, 1)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("since", nargs="?", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("until", nargs="?", help="YYYY-MM-DD (exclusive); default today")
    ap.add_argument("--days", type=int, help="trailing window ending today")
    a = ap.parse_args(argv)
    today = _dt.date.today()
    if a.days:
        since, until = (today - _dt.timedelta(days=a.days)).isoformat(), today.isoformat()
    elif a.since:
        since, until = a.since, a.until or today.isoformat()
    else:
        ap.error("give SINCE [UNTIL] or --days N")
    c = measure(since, until)
    t, d = c["total"], c["days"]

    def pct(n: int) -> str:
        return f"{100 * n / t:.0f}%" if t else "n/a"

    print(f"window {since}..{until} ({d} d): commits {t} ({t / d:.1f}/day)")
    print(f"  harness {c['harness']} ({pct(c['harness'])})  product {c['product']} "
          f"({pct(c['product'])})  neither/mixed {c['neither']} ({pct(c['neither'])})")
    print(f"  harness/day {c['harness'] / d:.1f}  product/day {c['product'] / d:.1f}  "
          f"h:p {c['harness'] / max(c['product'], 1):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
