#!/usr/bin/env python3
"""readme_stats.py — SSOT for the volatile structural counts cited in README.md.

The problem: README prose hardcodes numbers (test-file count, handler-module
count) that drift the instant code changes, and nothing catches it. The
2026-07-23 doc audit found meshforge's README claiming 193 test files / 76
handlers / "6,318 tests" while the tree actually held 283 / 102 / ~9,300 — and
the "6,318" figure was even self-contradictory across four spots in the file.
That is the same "two consumers of one constant drift" failure class the code
linter already guards against, just in docs.

The fix: every count this tool manages is wrapped in an HTML-comment sentinel
in README.md, which renders invisibly on GitHub:

    across <!--STAT:testfiles-->283<!--/STAT--> test files

`--check` recomputes each sentinel's value from the filesystem (the ground
truth) and fails if the README disagrees; `--update` rewrites the sentinels in
place. Wire `--check` into the test suite (tests/test_readme_stats.py) so the
CI run the repo already performs enforces README accuracy — no hook or CI-config
change needed.

Deliberately NOT managed here: the *total* test count. It depends on which
optional deps are installed (CI installs a subset and ignores one file), so a
hardcoded total would be honest in one environment and a lie in another. The
README states it qualitatively instead ("~9,300 tests; run `pytest tests/
--co -q` for the live count"). Only environment-STABLE, filesystem-derived
counts belong in a sentinel.

Usage:
    python3 scripts/readme_stats.py --check     # exit 0 ok / 1 drift / 2 unknown
    python3 scripts/readme_stats.py --update     # rewrite sentinels in place
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

SENTINEL_RE = re.compile(r"<!--STAT:(\w+)-->(.*?)<!--/STAT-->", re.DOTALL)


def _count_test_files() -> int | None:
    d = ROOT / "tests"
    if not d.is_dir():
        return None
    return len(list(d.glob("test_*.py")))


def _count_handler_modules() -> int | None:
    d = ROOT / "src" / "launcher_tui" / "handlers"
    if not d.is_dir():
        return None
    return len([p for p in d.glob("*.py") if p.name != "__init__.py"])


# key -> zero-arg computation returning the ground-truth int, or None when the
# stat does not apply to this repo (a repo with no such sentinel is unaffected).
COMPUTERS = {
    "testfiles": _count_test_files,
    "handlers": _count_handler_modules,
}


def _norm(text: str) -> int | None:
    try:
        return int(text.replace(",", "").strip())
    except ValueError:
        return None


def check() -> int:
    """Return 0 (all sentinels match), 1 (a sentinel drifted), 2 (a value could
    not be computed — unobservable is never treated as a pass)."""
    if not README.is_file():
        print(f"readme_stats: {README} not found", file=sys.stderr)
        return 2
    text = README.read_text(encoding="utf-8")
    found = SENTINEL_RE.findall(text)
    if not found:
        print("readme_stats: no <!--STAT:*--> sentinels in README (nothing to check)")
        return 0
    rc = 0
    for key, inner in found:
        computer = COMPUTERS.get(key)
        if computer is None:
            print(f"  UNKNOWN  {key}: no computer registered for this sentinel")
            rc = max(rc, 2)
            continue
        actual = computer()
        if actual is None:
            print(f"  UNKNOWN  {key}: value could not be computed in this repo")
            rc = max(rc, 2)
            continue
        stated = _norm(inner)
        if stated == actual:
            print(f"  ok       {key}: {actual}")
        else:
            print(f"  DRIFT    {key}: README says {inner!r}, tree has {actual} "
                  f"— run `python3 scripts/readme_stats.py --update`")
            rc = max(rc, 1)
    return rc


def update() -> int:
    if not README.is_file():
        print(f"readme_stats: {README} not found", file=sys.stderr)
        return 2
    text = README.read_text(encoding="utf-8")
    changed = []

    def repl(m: re.Match) -> str:
        key, inner = m.group(1), m.group(2)
        computer = COMPUTERS.get(key)
        if computer is None:
            return m.group(0)
        actual = computer()
        if actual is None:
            return m.group(0)
        if _norm(inner) != actual:
            changed.append((key, inner, actual))
        return f"<!--STAT:{key}-->{actual}<!--/STAT-->"

    new = SENTINEL_RE.sub(repl, text)
    if new != text:
        README.write_text(new, encoding="utf-8")
    for key, old, actual in changed:
        print(f"  updated  {key}: {old!r} -> {actual}")
    if not changed:
        print("readme_stats: all sentinels already current")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="fail if any sentinel drifted")
    g.add_argument("--update", action="store_true", help="rewrite sentinels from the tree")
    args = ap.parse_args()
    return check() if args.check else update()


if __name__ == "__main__":
    raise SystemExit(main())
