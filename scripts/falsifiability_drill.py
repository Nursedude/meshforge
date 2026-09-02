#!/usr/bin/env python3
"""Phase 2 of the falsifiability audit — KILL each probe and see whether the
suite notices. Measured, never read.

WHY (2026-09-02, operator: "silent failures are not acceptable with a NOC").
Phase 1 (`falsifiability_audit.py`) inventories what REFERENCES exist per
signal class and refuses to say "falsifiable", because the real question —
"would this test still pass if the probe were dead?" — is not answerable by
pattern-match. It IS answerable by experiment. This script runs it:

  dead   the entry probe(s) that can emit the class return None / [] —
         a frozen-GREEN detector. Does any test fail?
  loud   the entry probe(s) return the class unconditionally —
         a stuck-LOUD detector. Does any test fail?

A class whose `dead` mutant survives the suite is a detector the suite would
let die silently. That is the finding this phase exists to produce, and it is
the number that must be RE-DERIVED (re-run this) rather than carried.

WHAT THIS DOES NOT CLAIM. A caught mutant proves the suite notices the ENTRY
PROBE dying as a unit — the shape in which real probes die (an except-swallow,
a wrong path, a wrong name). It says nothing about whether the drill resembles
the real condition (2026-08-11 rule), nor whether the probe is hosted by a
running consumer (calibrated_claims rule 7). Those stay a frontier read; the
provenance ledger records them per class beside this script's numbers.

Attribution: a class's entry probes are the runner-called `probe_*` functions
whose body — transitively through same-tree helpers — contains the class
literal. Killing ALL of them at once is deliberate: the question is "can the
class still be emitted", not "is this one function alive". Collateral is
reported, not hidden: a failing test is tagged `named` when its own source
mentions the class, so a catch that comes only from a SIBLING class's
assertion is visible as such and must not be counted as this class's cover.

Mutations run in a throwaway `git worktree` (never the live tree — the box's
own watchdog imports from it) and every file is restored after each run.

Usage:
    python3 scripts/falsifiability_drill.py                # all classes
    python3 scripts/falsifiability_drill.py --classes a b  # a subset
    python3 scripts/falsifiability_drill.py --json out.json --md out.md
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PROBE_GLOB = "src/utils/watchdog_probe*.py"
RUNNER_GLOB = "src/utils/watchdog_runner*.py"
PYTEST_TIMEOUT = 300
# Entry probes the runner consumes as a LIST (`signals.extend(...)`); their
# dead stub must return [] and their loud stub [Signal]. Derived from the
# runner's own call sites, never hand-listed.
EXTEND_RE = re.compile(r"\.extend\(\s*(probe_\w+)\s*\(")
CALL_RE = re.compile(r"\b(probe_\w+)\s*\(")


def signal_classes() -> list:
    from utils.watchdog_probe_core import SIGNAL_CLASSES
    return sorted(SIGNAL_CLASSES)


class Fn:
    __slots__ = ("name", "path", "node", "literals", "calls", "severities")

    def __init__(self, name, path, node):
        self.name, self.path, self.node = name, path, node
        self.literals: set = set()
        self.calls: set = set()
        self.severities: dict = {}   # cls -> severity literal seen in Signal()


def _index_functions(root: Path, classes: set) -> dict:
    """name -> [Fn] for every top-level function in the probe tree."""
    fns: dict = {}
    for path in sorted(root.glob(PROBE_GLOB)):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            fn = Fn(node.name, path, node)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value in classes:
                    fn.literals.add(sub.value)
                elif isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name):
                        fn.calls.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        fn.calls.add(f.attr)
                    if isinstance(f, ast.Name) and f.id == "Signal":
                        kw = {k.arg: k.value for k in sub.keywords}
                        c, s = kw.get("cls"), kw.get("severity")
                        if isinstance(c, ast.Constant) and isinstance(s, ast.Constant):
                            fn.severities[c.value] = s.value
            fns.setdefault(node.name, []).append(fn)
    return fns


def _runner_entries(root: Path) -> tuple:
    entries, extend = set(), set()
    for path in sorted(root.glob(RUNNER_GLOB)):
        txt = path.read_text()
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("#") or s.startswith("def ") or "import" in s:
                continue
            entries.update(CALL_RE.findall(line))
            extend.update(EXTEND_RE.findall(line))
    return entries, extend


def _emits(fns: dict, name: str, cls: str, seen: set) -> bool:
    if name in seen:
        return False
    seen.add(name)
    for fn in fns.get(name, []):
        if cls in fn.literals:
            return True
        for callee in fn.calls:
            if callee in fns and _emits(fns, callee, cls, seen):
                return True
    return False


def attribute(root: Path, classes: list) -> dict:
    """cls -> {entries: [probe names], extend: [names], severity: str}"""
    fns = _index_functions(root, set(classes))
    entries, extend = _runner_entries(root)
    out = {}
    for cls in classes:
        hits = sorted(e for e in entries if e in fns and _emits(fns, e, cls, set()))
        sev = None
        for e in hits:
            for fn in fns[e]:
                sev = sev or fn.severities.get(cls)
        if sev is None:
            # The literal may live in a helper; take any Signal() that names it.
            for lst in fns.values():
                for fn in lst:
                    sev = sev or fn.severities.get(cls)
        out[cls] = {
            "entries": hits,
            "extend": sorted(e for e in hits if e in extend),
            "severity": sev or "degraded",
            "files": sorted({str(fn.path.relative_to(root)) for e in hits for fn in fns[e]}),
        }
    return out, fns


def _stub(fn: Fn, body: str) -> tuple:
    """(path, new_text) with fn's body replaced by `body` (already a statement)."""
    lines = fn.path.read_text().splitlines(keepends=True)
    first = fn.node.body[0]
    start = first.lineno - 1
    end = fn.node.end_lineno
    indent = " " * first.col_offset
    new = lines[:start] + [indent + body + "\n"] + lines[end:]
    return fn.path, "".join(new)


def mutate(wt: Path, fns: dict, cls: str, attr: dict, mode: str) -> list:
    """Apply the mutation in the worktree; return the files touched."""
    touched = set()
    for e in attr["entries"]:
        for fn in fns[e]:
            is_list = e in attr["extend"]
            if mode == "dead":
                body = "return []" if is_list else "return None"
            else:
                sig = (f'Signal(cls={cls!r}, subject="fdrill", '
                       f'severity={attr["severity"]!r}, detail="fdrill: stuck loud")')
                body = f"return [{sig}]" if is_list else f"return {sig}"
            wt_path = wt / fn.path.relative_to(ROOT)
            # Re-parse from the WORKTREE copy so line numbers match after a
            # prior stub in the same file shifted them.
            tree = ast.parse(wt_path.read_text())
            node = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == e)
            wfn = Fn(e, wt_path, node)
            path, text = _stub(wfn, body)
            if mode != "dead" and not re.search(
                    r"^\s*from [\w.]+ import [^\n]*\bSignal\b|^\s+Signal,?\s*$", text, re.M):
                text = "from utils.watchdog_probe_core import Signal  # fdrill\n" + text
            path.write_text(text)
            touched.add(path)
    return sorted(touched)


def restore(wt: Path, files: list) -> None:
    if not files:
        return
    rel = [str(Path(f).relative_to(wt)) for f in files]
    subprocess.run(["git", "-C", str(wt), "checkout", "--", *rel],
                   check=True, timeout=60, capture_output=True)


def tests_for(root: Path, cls: str, attr: dict) -> list:
    names = [cls] + attr["entries"]
    out = []
    for f in sorted((root / "tests").glob("test_*.py")):
        txt = f.read_text(errors="ignore")
        if any(n in txt for n in names):
            out.append(f.name)
    return out


def _test_mentions(wt: Path, nodeid: str, cls: str) -> bool:
    """Does the failing test's OWN source mention the class? (collateral tag)"""
    try:
        file, _, rest = nodeid.partition("::")
        fn = rest.split("::")[-1].split("[")[0]
        tree = ast.parse((wt / file).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn:
                return cls in ast.get_source_segment((wt / file).read_text(), node)
    except (OSError, SyntaxError, ValueError):
        pass
    return False


def run_pytest(wt: Path, files: list) -> dict:
    cmd = ["timeout", "-s", "KILL", str(PYTEST_TIMEOUT), sys.executable, "-m",
           "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf",
           "-o", "addopts=", *[f"tests/{f}" for f in files]]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=wt, capture_output=True, text=True,
                       timeout=PYTEST_TIMEOUT + 30, env=env)
    failed = re.findall(r"^FAILED (\S+)", p.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", p.stdout, re.M)
    m = re.search(r"(\d+) passed", p.stdout)
    return {
        "rc": p.returncode, "failed": failed, "errors": errors,
        "passed": int(m.group(1)) if m else 0,
        "secs": round(time.monotonic() - t0, 1),
        "tail": p.stdout.strip().splitlines()[-1:] if p.stdout.strip() else p.stderr.strip().splitlines()[-3:],
    }


def verdict(base: dict, dead: dict, loud: dict) -> str:
    if base["rc"] != 0:
        return "baseline-red"
    d = bool(dead["named"]) if dead else False
    l = bool(loud["named"]) if loud else False
    if d and l:
        return "caught-both"
    if d:
        return "caught-dead-only"
    if l:
        return "caught-loud-only"
    if (dead and dead["failed"]) or (loud and loud["failed"]):
        return "collateral-only"
    return "SURVIVED"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--classes", nargs="*")
    ap.add_argument("--worktree", default=os.path.expanduser("~/.cache/meshforge-fdrill"))
    ap.add_argument("--json")
    ap.add_argument("--md")
    ap.add_argument("--keep", action="store_true", help="keep the worktree")
    ap.add_argument("--fail-on-survivor", action="store_true",
                    help="exit 1 if any class is not caught-both (the standing gate)")
    a = ap.parse_args()

    classes = signal_classes()
    if a.classes:
        bad = set(a.classes) - set(classes)
        if bad:
            print(f"unknown class(es): {sorted(bad)}", file=sys.stderr)
            return 2
        classes = [c for c in classes if c in set(a.classes)]
    attrs, fns = attribute(ROOT, classes)

    wt = Path(a.worktree)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=30).stdout.strip()
    if wt.exists():
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                       capture_output=True, timeout=60)
        shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), head],
                   check=True, capture_output=True, timeout=120)

    results = []
    baselines: dict = {}
    try:
        for cls in classes:
            attr = attrs[cls]
            row = {"cls": cls, **attr}
            if not attr["entries"]:
                row["verdict"] = "NO-ENTRY-PROBE"
                row["tests"] = []
                results.append(row)
                print(f"{cls:<38} NO-ENTRY-PROBE (no runner-called probe carries the literal)")
                continue
            files = tests_for(wt, cls, attr)
            row["tests"] = files
            key = tuple(files)
            if key not in baselines:
                baselines[key] = run_pytest(wt, files)
            base = baselines[key]
            row["baseline"] = {k: base[k] for k in ("rc", "passed", "secs")}
            out = {}
            for mode in ("dead", "loud"):
                touched = mutate(wt, fns, cls, attr, mode)
                try:
                    r = run_pytest(wt, files)
                finally:
                    restore(wt, touched)
                r["named"] = [t for t in r["failed"] if _test_mentions(wt, t, cls)]
                r["collateral"] = [t for t in r["failed"] if t not in r["named"]]
                out[mode] = r
            row["dead"], row["loud"] = out["dead"], out["loud"]
            row["verdict"] = verdict(base, out["dead"], out["loud"])
            results.append(row)
            print(f"{cls:<38} {row['verdict']:<18} "
                  f"dead: {len(out['dead']['named'])}n/{len(out['dead']['collateral'])}c  "
                  f"loud: {len(out['loud']['named'])}n/{len(out['loud']['collateral'])}c  "
                  f"({','.join(attr['entries'])})", flush=True)
    finally:
        if not a.keep:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                           capture_output=True, timeout=60)

    counts: dict = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    summary = {"head": head, "total": len(results), "counts": counts, "rows": results}
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=2))
    if a.md:
        Path(a.md).write_text(render_md(summary))
    if a.fail_on_survivor:
        bad = [r["cls"] for r in results if r["verdict"] != "caught-both"]
        if bad:
            print(f"FAIL — {len(bad)} class(es) not caught in both polarities: "
                  + ", ".join(bad), file=sys.stderr)
            return 1
        print(f"PASS — all {len(results)} classes caught in both polarities")
    return 0


def render_md(s: dict) -> str:
    L = [f"### Falsifiability drill — phase 2 measurement @ `{s['head'][:8]}`", "",
         "Generated by `scripts/falsifiability_drill.py`: each class's entry probe(s) "
         "were replaced by a dead stub (returns None/[]) and a stuck-loud stub, and "
         "the referencing test files re-run in a throwaway worktree. `named` = failing "
         "tests whose own source mentions the class; `collateral` = failures that "
         "belong to a sibling class sharing the probe.", "",
         "Verdict counts: " + "; ".join(f"`{k}` {v}" for k, v in sorted(s["counts"].items())) + ".", ""]
    order = ["SURVIVED", "NO-ENTRY-PROBE", "collateral-only", "baseline-red",
             "caught-dead-only", "caught-loud-only", "caught-both"]
    for v in order:
        sel = [r for r in s["rows"] if r["verdict"] == v]
        if not sel:
            continue
        L.append(f"#### {v} ({len(sel)})")
        L.append("")
        for r in sel:
            if v == "NO-ENTRY-PROBE":
                L.append(f"- `{r['cls']}`")
                continue
            d, l = r["dead"], r["loud"]
            L.append(f"- `{r['cls']}` via {', '.join(f'`{e}`' for e in r['entries'])} — "
                     f"dead {len(d['named'])} named / {len(d['collateral'])} collateral; "
                     f"loud {len(l['named'])} named / {len(l['collateral'])} collateral; "
                     f"tests {', '.join(f'`{t}`' for t in r['tests'])}")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
