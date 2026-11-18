#!/usr/bin/env python3
"""bench_guard_replay — what does bench_guard refuse on REAL traffic? (2026-11-18)

The price of a refusal, measured. Replays every distinct Bash/Monitor command
from this project's Claude transcripts through ``bench_guard.decide_bash`` as a
NON-lab box and splits the refusals by an oracle that is NOT the guard's own
mention rule (review 11-18 #2: an oracle equal to the rule can only print PASS):

* named   — the raw command carries the name CONTIGUOUSLY (any case), as an
            accident types it. Most are the accepted plain-shape price (a
            `cd`/`grep` naming the tool beside quotes); a change that raises this
            count is a price too (Fable r2 #5), so it is printed and can be
            compared with ``--baseline-named``;
* UNNAMED — refused without the contiguous name: a FALSE MENTION, or a name
            split by quoting inside one word (``ax'io'meta``). Real traffic holds
            ~none of the latter; read the ``--out`` rows to tell them apart.

Why it exists: r4-r5 widened the mention check on the belief that "a false
mention only costs a re-phrase". Nobody measured it; on 11-18 this replay found
~2,050–2,080 of ~17,300 commands (11.9 %; first replay 2,079 of 17,404) refused
while unnamed. Rule (bench_guard's
docstring): a change to what the guard refuses replays before AND after and
states both counts.

Baseline (11-18): UNNAMED was 0 until the r3 reviewer typed one demo of the
accepted residual (bench_guard `_mentions` comment); it reads 1 until that
transcript leaves the window (~12-02) — use ``--max-unnamed 1`` until then.

Window = transcript FILE mtime (a session file touched today carries its older
commands) and includes the sessions that build this guard; counts drift a few
per run. Quote a count with its date, re-derive it, never carry it.

Read-only. Transcripts are the operator's private data: stdout carries COUNTS
only; commands go only to ``--out`` (0600, refused inside ANY git work tree).
Not a CI check (CI has no transcripts); ``tests/test_bench_guard.py`` pins the
tally with synthetic commands and the mention rule with a truth table.

    python3 scripts/bench_guard_replay.py [--days 14] [--max-unnamed 0]
                                          [--baseline-named N] [--out FILE]

Exit: 0 PASS; 1 UNNAMED over --max-unnamed or named over --baseline-named;
2 no transcripts (UNKNOWN); 3 --out refused or not writable.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO, "src")
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bench_guard  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

PROJECT_DIR = "-opt-meshforge"
TOOLS = ("Bash", "Monitor")


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:                      # rotated/deleted between glob and stat (review #5)
        return None


def commands(days: float, root: str | None = None):
    """(transcripts read, {command: first transcript path that ran it})."""
    if root is None:
        root = os.path.join(str(get_real_user_home()), ".claude", "projects", PROJECT_DIR)
    cut = time.time() - days * 86400
    files = []
    for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        m = _mtime(f)
        if m is not None and m >= cut:
            files.append(f)
    found = {}
    for f in files:
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if not any(f'"{t}"' in line for t in TOOLS):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                # every level shape-checked: an odd record must skip, never crash the
                # gate (Fable r3 #5 — a traceback exits 1, the same code as FAIL)
                msg = rec.get("message") if isinstance(rec, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for c in content:
                    if (isinstance(c, dict) and c.get("type") == "tool_use"
                            and c.get("name") in TOOLS):
                        inp = c.get("input")
                        cmd = inp.get("command") if isinstance(inp, dict) else None
                        if isinstance(cmd, str) and cmd:
                            found.setdefault(cmd, f)
    return files, found


def named(cmd: str) -> bool:
    """The independent oracle: the name typed contiguously (any case)."""
    return bench_guard.NAMES[0] in cmd.lower()


def tally(cmds: dict):
    """(named refusals, [UNNAMED refusal rows]) — judged as a NON-lab box."""
    n_named, unnamed = 0, []
    for cmd, src in cmds.items():
        why = bench_guard.decide_bash(cmd, False, frozenset())
        if why is None:
            continue
        if named(cmd):
            n_named += 1
        else:
            unnamed.append({"cmd": cmd, "reason": why, "transcript": src})
    return n_named, unnamed


def _in_git_tree(path: str) -> bool:
    d = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _out_ok(path: str) -> bool:
    """Never inside this repo or ANY git work tree (an export, a worktree — Fable r2 #1)."""
    real = os.path.realpath(path)
    if real == _REPO or real.startswith(_REPO + os.sep):
        return False
    return not _in_git_tree(real)


def _write_out(path: str, rows) -> None:
    if not rows:
        try:
            os.unlink(path)              # a PASS must not leave an earlier run's rows (r2 #3)
        except FileNotFoundError:
            pass
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)                 # O_CREAT's mode does not apply to an existing file
    with os.fdopen(fd, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def main(argv=None, source=commands) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--max-unnamed", type=int, default=0)
    ap.add_argument("--baseline-named", type=int, default=None,
                    help="fail if named refusals exceed this (the before-count of a change)")
    ap.add_argument("--out", help="write UNNAMED refusals (JSONL, 0600) here; never in a git tree")
    a = ap.parse_args(argv)
    if a.out and not _out_ok(a.out):
        print("REFUSED: --out is inside a git work tree; transcripts are private (MF014/MF015)")
        return 3

    files, cmds = source(a.days)
    if not cmds:
        print(f"UNKNOWN: no transcript commands in the last {a.days:g} d — nothing measured")
        return 2
    n_named, unnamed = tally(cmds)
    n = len(cmds)
    print(f"transcripts={len(files)} (last {a.days:g} d, by file mtime)  distinct commands={n}")
    print(f"refused={n_named + len(unnamed)}  named={n_named}  UNNAMED={len(unnamed)} "
          f"({100 * len(unnamed) / n:.2f}% of commands)")
    if a.out:
        try:
            _write_out(a.out, unnamed)
        except OSError as e:             # r4 #2: a traceback exits 1 = reads as gate FAIL
            print(f"REFUSED: --out not writable ({type(e).__name__}); counts above stand")
            return 3
        print(f"--out: {len(unnamed)} UNNAMED row(s) (0600)" if unnamed else "--out: none, file removed")
    fails = []
    if len(unnamed) > a.max_unnamed:
        fails.append(f"UNNAMED {len(unnamed)} > --max-unnamed {a.max_unnamed}")
    if a.baseline_named is not None and n_named > a.baseline_named:
        fails.append(f"named {n_named} > --baseline-named {a.baseline_named}")
    print("FAIL: " + "; ".join(fails) if fails else
          f"PASS: UNNAMED {len(unnamed)} <= {a.max_unnamed}"
          + (f"; named {n_named} <= {a.baseline_named}" if a.baseline_named is not None else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
