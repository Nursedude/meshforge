"""Local-brain eval harness — W4 of the degraded-brain ladder
(.claude/research/dudeclaw_local_brain_2026_07_03.md §4).

Without measurement, every "tier L can X" claim is permanently BELIEVED
(calibrated-claims applied to a model). This harness turns the claims into
numbers: eval cases distilled from REAL fleet incidents run through the
PRODUCTION code paths — ``cadence_fallback.run`` for triage,
``chat_compiler.compile_rule`` with the LIVE registries for compilation —
never a lookalike prompt (consumer-of-record, calibrated-claims rule 7).

Grading is DETERMINISTIC (coverage fractions, dotted-field expectations):
a local LLM judging a local LLM would be circular; mechanical checks are
the honest gauge at this tier. Runs are SEQUENTIAL by construction — one
model resident at a time on a box whose freeze class is documented — and
every run appends a summary to the results ledger so the pass-rate has a
history, not a vibe.

The flywheel convention (research doc §3): every incident the frontier
tier resolves should precipitate an eval case here — the case names its
provenance. Case files live in ``evals/local_brain/*.jsonl``; one JSON
object per line:

    {"id": "...", "kind": "triage" | "compile",
     "provenance": "which real incident taught this",
     "input": {...}, "expect": {...}}

triage input:  {"deltas": [{key, summary[, kind]}], ["frontier_rc": N]}
        expect: {["coverage_min": 1.0], ["max_dropped": 0],
                 ["dispositions": {key: [allowed, ...]}]}
compile input: {"intent": "...", ["condition_kinds": [...]], ["notes": ""]}
        expect: {["fields": {dotted: value}], ["fields_in": {dotted: [...]}],
                 ["fields_range": {dotted: [min, max]}]}
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cadence_fallback
from ._util import resolve_home
from .history import append_jsonl
from .chat_compiler import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    CompilerError,
    OllamaBackend,
    compile_rule,
)
from .config import registered_action_kinds, registered_source_kinds

CASE_KINDS = ("triage", "compile")

# Results ledger — one summary record per run, appended forever (the
# model's calibration history; the gate reads the latest, humans read the
# trend). Torn-tail-safe shared writer.
EVAL_RESULTS_BASENAME = "local_brain_evals.jsonl"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DEFAULT_CASES_GLOB = os.path.join(_REPO_ROOT, "evals", "local_brain",
                                  "*.jsonl")


class EvalConfigError(Exception):
    """A malformed eval file fails LOUDLY at load time — absorbing an
    authoring error would grade the model against a case nobody meant
    (honest_failure_modes #3)."""


def load_cases(paths: List[str]) -> List[dict]:
    cases: List[dict] = []
    seen_ids: set = set()
    if not paths:
        raise EvalConfigError("no eval case files found — nothing to run is "
                              "an error, not a 100% pass")
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                where = f"{path}:{n}"
                try:
                    case = json.loads(line)
                except ValueError as e:
                    raise EvalConfigError(f"{where}: not valid JSON: {e}")
                cid = case.get("id")
                if not cid or not isinstance(cid, str):
                    raise EvalConfigError(f"{where}: case missing an id")
                if cid in seen_ids:
                    raise EvalConfigError(f"{where}: duplicate case id {cid!r}")
                if case.get("kind") not in CASE_KINDS:
                    raise EvalConfigError(
                        f"{where}: unknown kind {case.get('kind')!r} "
                        f"(known: {', '.join(CASE_KINDS)})")
                if not isinstance(case.get("input"), dict) \
                        or not isinstance(case.get("expect"), dict):
                    raise EvalConfigError(
                        f"{where}: case needs object 'input' and 'expect'")
                seen_ids.add(cid)
                cases.append(case)
    return cases


def _dotted(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _grade_fields(rule: dict, expect: dict, reasons: List[str]) -> None:
    for path, want in (expect.get("fields") or {}).items():
        got = _dotted(rule, path)
        if got != want:
            reasons.append(f"{path}: expected {want!r}, got {got!r}")
    for path, allowed in (expect.get("fields_in") or {}).items():
        got = _dotted(rule, path)
        if got not in allowed:
            reasons.append(f"{path}: {got!r} not in allowed {allowed!r}")
    for path, (lo, hi) in (expect.get("fields_range") or {}).items():
        got = _dotted(rule, path)
        if not isinstance(got, (int, float)) or not (lo <= got <= hi):
            reasons.append(f"{path}: {got!r} outside [{lo}, {hi}]")


def grade_triage(case: dict, backend) -> Tuple[bool, List[str], dict]:
    """Run the PRODUCTION triage path against the case's deltas and grade
    coverage/validity. Returns (ok, reasons, witness)."""
    inp = case["input"]
    expect = case["expect"]
    deltas = inp.get("deltas") or []
    if not deltas:
        return False, ["case has no input deltas"], {}
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as f:
        for d in deltas:
            rec = dict(d)
            rec.setdefault("status", "proposed")
            rec.setdefault("ts", time.time())
            f.write(json.dumps(rec) + "\n")
        tmp = f.name
    try:
        witness = cadence_fallback.run(
            tmp, backend, frontier_rc=inp.get("frontier_rc", 1),
            max_deltas=max(len(deltas), 1))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    reasons: List[str] = []
    if witness.get("brain_tier") != "local":
        reasons.append(f"no local triage produced: "
                       f"{witness.get('error', 'unknown')[:160]}")
        return False, reasons, witness
    total = witness.get("proposed_total") or 0
    triaged = witness.get("triaged") or 0
    coverage = (triaged / total) if total else 0.0
    if coverage < float(expect.get("coverage_min", 1.0)):
        reasons.append(f"coverage {triaged}/{total} below "
                       f"{expect.get('coverage_min', 1.0)}")
    if (witness.get("dropped_entries") or 0) > int(expect.get("max_dropped", 0)):
        reasons.append(f"{witness['dropped_entries']} entries dropped in "
                       f"validation (> {expect.get('max_dropped', 0)})")
    by_key = {d["key"]: d for d in witness.get("deltas") or []}
    for key, allowed in (expect.get("dispositions") or {}).items():
        got = (by_key.get(key) or {}).get("suggested_disposition")
        if got not in allowed:
            reasons.append(f"disposition[{key}]: {got!r} not in {allowed!r}")
    return not reasons, reasons, witness


def grade_compile(case: dict, backend) -> Tuple[bool, List[str], dict]:
    """Compile the case's intent through the PRODUCTION compiler (live
    registries, real validator + repair round) and grade the rule fields."""
    inp = case["input"]
    expect = case["expect"]
    intent = inp.get("intent") or ""
    try:
        rule, _warnings = compile_rule(
            intent, backend, existing_rules=[],
            source_kinds=registered_source_kinds(),
            action_kinds=registered_action_kinds(),
            condition_kinds=inp.get("condition_kinds"),
            notes=inp.get("notes", ""))
    except CompilerError as e:
        return False, [f"compile failed: {e}"] + list(e.errors)[:3], {}
    reasons: List[str] = []
    _grade_fields(rule, expect, reasons)
    return not reasons, reasons, rule


_GRADERS = {"triage": grade_triage, "compile": grade_compile}


def run_cases(cases: List[dict], backend) -> Tuple[List[dict], dict]:
    """SEQUENTIAL by construction — one local model resident at a time (the
    box's freeze class is why this harness exists at all)."""
    results: List[dict] = []
    for case in cases:
        t0 = time.monotonic()
        try:
            ok, reasons, artifact = _GRADERS[case["kind"]](case, backend)
        except Exception as e:  # a grader crash is a FAILED case, loudly
            ok, reasons, artifact = False, [
                f"grader crashed: {type(e).__name__}: {e}"], {}
        results.append({
            "id": case["id"],
            "kind": case["kind"],
            "ok": ok,
            "reasons": reasons,
            "latency_s": round(time.monotonic() - t0, 1),
            "provenance": case.get("provenance"),
        })
    passed = sum(1 for r in results if r["ok"])
    per_kind: Dict[str, dict] = {}
    for r in results:
        k = per_kind.setdefault(r["kind"], {"passed": 0, "total": 0})
        k["total"] += 1
        k["passed"] += 1 if r["ok"] else 0
    now = time.time()
    try:
        iso = datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        iso = None
    summary = {
        "ts": now, "iso": iso,
        "model": getattr(backend, "model", "?"),
        "url": getattr(backend, "url", "?"),
        "total": len(results), "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "per_kind": per_kind,
        "failed_ids": [r["id"] for r in results if not r["ok"]],
    }
    return results, summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the local-brain eval cases (sequential, "
                    "production code paths, deterministic grading).")
    ap.add_argument("--cases", nargs="*",
                    help=f"case files (default: {DEFAULT_CASES_GLOB})")
    ap.add_argument("--url", default=os.environ.get(
        "MINI_DUDEAI_OLLAMA_URL", DEFAULT_OLLAMA_URL))
    ap.add_argument("--model", default=os.environ.get(
        "MINI_DUDEAI_OLLAMA_MODEL", DEFAULT_MODEL))
    ap.add_argument("--timeout-s", type=float, default=480.0)
    ap.add_argument("--history", default=os.path.join(
        resolve_home(), EVAL_RESULTS_BASENAME),
        help="results ledger (JSONL, appended); empty string disables")
    ap.add_argument("--gate", type=float, default=None,
                    help="exit 1 unless pass_rate >= this fraction — the "
                         "'tier L can X' claim gate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    paths = args.cases if args.cases else sorted(glob.glob(DEFAULT_CASES_GLOB))
    try:
        cases = load_cases(paths)
    except (EvalConfigError, OSError) as e:
        print(f"local_brain_eval: {e}", file=sys.stderr)
        return 2

    backend = OllamaBackend(url=args.url, model=args.model,
                            timeout_s=args.timeout_s)
    results, summary = run_cases(cases, backend)

    if args.history:
        try:
            # 2 MB rotation, same convention as the calibration ledger.
            append_jsonl(args.history, [{**summary, "results": results}],
                         max_bytes=2 * 1024 * 1024)
        except OSError as e:
            print(f"local_brain_eval: WARN history append failed: {e}",
                  file=sys.stderr)

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            print(f"  {mark}  {r['id']}  ({r['kind']}, {r['latency_s']}s)"
                  + ("" if r["ok"] else f"  — {'; '.join(r['reasons'])[:200]}"))
        print(f"local_brain_eval: {summary['passed']}/{summary['total']} "
              f"passed (rate {summary['pass_rate']}) — model {summary['model']}")

    if args.gate is not None and summary["pass_rate"] < args.gate:
        print(f"local_brain_eval: GATE FAILED "
              f"({summary['pass_rate']} < {args.gate})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
