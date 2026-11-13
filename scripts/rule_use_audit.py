#!/usr/bin/env python3
"""rule_use_audit.py — which per-turn rules does the ASSISTANT actually cite?

One-off, re-runnable audit (operator 2026-11-13: "is it measured? does it add
bloat?"). Counts SESSIONS in which an assistant turn (text or tool input) names
a persistent_issues tell row's signature, or a MEMORY.md hot line's topic file.
Injected context (system/user turns, sidechains) is ignored — every transcript
carries these files, so counting them would count nothing.

BASELINE 2026-11-13, THIS script (105 sessions / 30 d, table still per-turn-loaded):
persistent_issues 47 of 52 rows cited >= once; memory 67 of 85 topic files.
Re-run after the on-demand move (deferred `persistent-issues-on-demand-reaudit`)
— a collapse in citations is the signal that rows fell into shadow.

Blind spots, said: a line that shapes behaviour without being named reads 0;
a row whose signature fell back to its first words reads ~0 (not quoted).
SELF-CHECK: if no session cites `#69` (the most-cited row), the reader is
broken — the run prints UNKNOWN and exits 2 instead of a confident zero.

Usage: scripts/rule_use_audit.py [--days 30]
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
REPO = os.path.join(_HERE, "..")
PI_REL = ".claude/foundations/persistent_issues.md"
KNOWN_POSITIVE = "#69"


def assistant_text(lines):
    """The assistant's own words + tool inputs from transcript JSONL lines."""
    buf = []
    for line in lines:
        if '"assistant"' not in line:   # cheap prefilter; json decides (spacing varies)
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get("message") or {}
        if msg.get("role") != "assistant" or o.get("isSidechain"):
            continue
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "text":
                buf.append(c.get("text", ""))
            elif isinstance(c, dict) and c.get("type") == "tool_use":
                buf.append(json.dumps(c.get("input", {})))
    return "\n".join(buf)


def pi_signatures(text):
    rows = []
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith(("| Symptom", "| Rule")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        sym = cells[0]
        sigs = set(re.findall(r"#\d{2,3}\b", sym + " " + " ".join(cells[1:])[:120]))
        sigs.update(t for t in re.findall(r"`([^`]{8,60})`", sym))
        sigs.update(re.findall(r'"([^"]{8,})"', (re.search(r"<!--\s*tell:(.*?)-->", line) or [None, ""])[1]))
        if not sigs:
            sigs.add(" ".join(re.sub(r"[`*]", "", sym).split()[:5]))
        rows.append((sym[:90], sigs))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    from utils.paths import get_real_user_home
    home = str(get_real_user_home())
    proj = os.path.join(home, ".claude/projects/-opt-meshforge")
    mem = os.path.join(proj, "memory/MEMORY.md")
    pi = pi_signatures(open(os.path.join(REPO, PI_REL), encoding="utf-8").read())
    mem_rows = []
    if os.path.exists(mem):
        for line in open(mem, encoding="utf-8"):
            m = re.search(r"\]\(([^)]+\.md)\)", line)
            if m:
                mem_rows.append((m.group(1), {m.group(1), m.group(1)[:-3]}))
    cut = time.time() - a.days * 86400
    files = [f for f in glob.glob(os.path.join(proj, "*.jsonl")) if os.path.getmtime(f) >= cut]
    pi_hits, mem_hits, positive, n = collections.Counter(), collections.Counter(), 0, 0
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                text = assistant_text(fh)
        except OSError:
            continue
        n += 1
        positive += KNOWN_POSITIVE in text
        for i, (_s, sigs) in enumerate(pi):
            pi_hits[i] += any(s in text for s in sigs)
        for i, (_f, sigs) in enumerate(mem_rows):
            mem_hits[i] += any(s in text for s in sigs)
    print(f"sessions scanned (last {a.days} d): {n} · known positive {KNOWN_POSITIVE} in {positive}")
    if n and not positive:
        print("UNKNOWN — the reader found no citation of the known positive; it is not reading")
        return 2
    for i, (sym, _s) in sorted(enumerate(pi), key=lambda x: pi_hits[x[0]]):
        print(f"{pi_hits[i]:4d}  {sym[:90]}")
    for i, (f, _s) in sorted(enumerate(mem_rows), key=lambda x: mem_hits[x[0]]):
        print(f"{mem_hits[i]:4d}  memory: {f}")
    print(f"cited >= once: persistent_issues {sum(1 for i in range(len(pi)) if pi_hits[i])}/{len(pi)}"
          f" · memory {sum(1 for i in range(len(mem_rows)) if mem_hits[i])}/{len(mem_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
