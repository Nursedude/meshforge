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
oracle input:  {"question": "...", ["top_k": 6]}
        expect: {["retrieve_must_include": [path fragments]],
                 ["cite_must_include": [path fragments]],
                 ["answer_contains_any": [strings, case-insensitive]],
                 ["require_answer": true]}
        (oracle cases must only rely on the REPO corpus — the memory root
        exists on one box and is skipped elsewhere by design)

    Any expect may set ["attempts": N] (default 1): best-of-N retry for a
    probabilistic tier — the case passes on the first attempt that grades ok,
    so a CAPABLE-but-non-deterministic model is not failed by single-shot
    sampling variance. It does NOT relax the assertion (never mask a real
    miss); each result's ``attempts_used`` surfaces how many tries it needed,
    so a case creeping toward its cap is a visible degradation tell.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cadence_fallback, offline_oracle
from ._util import iso_or_none, resolve_home
from .history import append_jsonl
from .chat_compiler import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    LOCAL_BRAIN_TIMEOUT_S,
    CompilerError,
    OllamaBackend,
    compile_rule,
)
from .config import registered_action_kinds, registered_source_kinds

CASE_KINDS = ("triage", "compile", "oracle")

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


def _validate_expect(case: dict, where: str) -> None:
    """Reject malformed expectations at LOAD time — grade time is too late.

    Without this, a typo'd case (fields_range with 3 elements, a string
    coverage_min) slipped through load and blew up inside the graders,
    where run_cases records it as 'grader crashed' — a FAILED case that
    counts against pass_rate and can trip --gate on an authoring error.
    That's the half of the fail-LOUD contract load_cases claimed but
    didn't enforce (honest_failure_modes #3)."""
    expect = case["expect"]
    if "coverage_min" in expect \
            and not isinstance(expect["coverage_min"], (int, float)):
        raise EvalConfigError(f"{where}: coverage_min must be a number")
    if "max_dropped" in expect and not isinstance(expect["max_dropped"], int):
        raise EvalConfigError(f"{where}: max_dropped must be an integer")
    if "attempts" in expect:
        # best-of-N knob (bool is an int subclass — reject it explicitly so a
        # typo'd `true` can't silently mean 1 attempt).
        if isinstance(expect["attempts"], bool) \
                or not isinstance(expect["attempts"], int) \
                or expect["attempts"] < 1:
            raise EvalConfigError(
                f"{where}: attempts must be a positive integer")
    for knob in ("fields", "fields_in", "fields_range", "dispositions"):
        if knob in expect and not isinstance(expect[knob], dict):
            raise EvalConfigError(f"{where}: {knob} must be an object")
    for fpath, rng in (expect.get("fields_range") or {}).items():
        if (not isinstance(rng, list) or len(rng) != 2
                or not all(isinstance(v, (int, float)) for v in rng)):
            raise EvalConfigError(
                f"{where}: fields_range[{fpath!r}] must be [min, max]")
    for key, allowed in (expect.get("dispositions") or {}).items():
        if not isinstance(allowed, list):
            raise EvalConfigError(
                f"{where}: dispositions[{key!r}] must be a list")
    for knob in ("retrieve_must_include", "cite_must_include",
                 "answer_contains_any"):
        if knob in expect and not isinstance(expect[knob], list):
            raise EvalConfigError(f"{where}: {knob} must be a list")
    if "expect_refusal" in expect:
        if not isinstance(expect["expect_refusal"], bool):
            raise EvalConfigError(f"{where}: expect_refusal must be a bool")
        if expect["expect_refusal"] and (expect.get("cite_must_include")
                                         or expect.get("answer_contains_any")):
            # The author cannot have meant both: a refusal has no grounded
            # answer to cite or match against (honest_failure_modes #3).
            raise EvalConfigError(
                f"{where}: expect_refusal conflicts with cite_must_include/"
                f"answer_contains_any — a refusal has no answer to grade")


def load_cases(paths: List[str]) -> List[dict]:
    cases: List[dict] = []
    seen_ids: set = set()
    if not paths:
        raise EvalConfigError("no eval case files found — nothing to run is "
                              "an error, not a 100% pass")
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError as e:
            # UnicodeDecodeError is a ValueError, which main()'s except
            # doesn't (and shouldn't) blanket-catch — name it here so a
            # mis-encoded case file gets the clean rc-2 path, not a traceback.
            raise EvalConfigError(f"{path}: not valid UTF-8: {e}")
        for n, line in enumerate(lines, 1):
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
            _validate_expect(case, where)
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
    # Coverage counts UNIQUE triaged keys, re-derived from the deltas list —
    # never the witness's own `triaged` tally (defense in depth vs the
    # duplicate-key inflation class: a repeated delta must not certify a
    # skipped one as covered; calibrated-claims rule 3, re-derive not trust).
    unique_triaged = len({d.get("key") for d in witness.get("deltas") or []})
    coverage = (unique_triaged / total) if total else 0.0
    if coverage < float(expect.get("coverage_min", 1.0)):
        reasons.append(f"coverage {unique_triaged}/{total} below "
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


def grade_oracle(case: dict, backend) -> Tuple[bool, List[str], dict]:
    """Ask the PRODUCTION oracle path and grade retrieval + citations +
    answer content separately — a retrieval miss and a synthesis miss are
    different diagnoses."""
    inp = case["input"]
    expect = case["expect"]
    result = offline_oracle.ask(inp.get("question") or "", backend,
                                top_k=inp.get("top_k", 6))
    reasons: List[str] = []
    retrieved_paths = " ".join(r["path"] for r in result.get("retrieved") or [])
    for frag in expect.get("retrieve_must_include") or []:
        if frag not in retrieved_paths:
            reasons.append(f"retrieval missing {frag!r}")
    if expect.get("expect_refusal"):
        # Honest-refusal case (the substitute-and-narrate-success wart, W5.1):
        # the ONLY passing behavior for an ungroundable question is declining
        # to answer — a confident grounded-looking answer IS the failure.
        if result.get("brain_tier") == "local":
            reasons.append(
                f"fabricated a grounded answer where honest output is a "
                f"refusal: {str(result.get('answer', ''))[:160]}")
        return not reasons, reasons, result
    if expect.get("require_answer", True):
        if result.get("brain_tier") != "local":
            reasons.append(f"no grounded answer: "
                           f"{str(result.get('note', '?'))[:160]}")
        else:
            cited_paths = " ".join(s["path"] for s in result["sources"])
            for frag in expect.get("cite_must_include") or []:
                if frag not in cited_paths:
                    reasons.append(f"citations missing {frag!r}")
            anyof = expect.get("answer_contains_any") or []
            if anyof and not any(a.lower() in result["answer"].lower()
                                 for a in anyof):
                reasons.append(f"answer contains none of {anyof!r}")
    return not reasons, reasons, result


_GRADERS = {"triage": grade_triage, "compile": grade_compile,
            "oracle": grade_oracle}


def run_cases(cases: List[dict], backend) -> Tuple[List[dict], dict]:
    """SEQUENTIAL by construction — one local model resident at a time (the
    box's freeze class is why this harness exists at all)."""
    results: List[dict] = []
    for case in cases:
        t0 = time.monotonic()
        # best-of-N for a probabilistic tier: a case may set expect.attempts>1
        # (default 1). The local model is non-deterministic, so a single-shot
        # coverage/citation assertion on a CAPABLE-but-variable model is a
        # flaky TEST of a real capability. Retrying up to `attempts` and
        # passing on the first success tests "the tier CAN produce this" — the
        # capability question a model-bump acceptance case asks — WITHOUT
        # lowering the assertion itself (never mask a real miss). `attempts_used`
        # is recorded so a case that increasingly needs its retries is a visible
        # degradation tell, not silently absorbed (calibrated-claims: surface
        # the blind spot, don't average it away).
        attempts = int(case["expect"].get("attempts", 1))
        ok, reasons, artifact = False, ["no attempt ran"], {}
        used = 0
        for used in range(1, attempts + 1):
            try:
                ok, reasons, artifact = _GRADERS[case["kind"]](case, backend)
            except Exception as e:  # a grader crash is a FAILED case, loudly
                ok, reasons, artifact = False, [
                    f"grader crashed: {type(e).__name__}: {e}"], {}
            if ok:
                break
        results.append({
            "id": case["id"],
            "kind": case["kind"],
            "ok": ok,
            "reasons": reasons,
            "latency_s": round(time.monotonic() - t0, 1),
            "attempts": attempts,
            "attempts_used": used,
            "provenance": case.get("provenance"),
        })
    passed = sum(1 for r in results if r["ok"])
    per_kind: Dict[str, dict] = {}
    for r in results:
        k = per_kind.setdefault(r["kind"], {"passed": 0, "total": 0})
        k["total"] += 1
        k["passed"] += 1 if r["ok"] else 0
    now = time.time()
    iso = iso_or_none(now)
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
    ap.add_argument("--timeout-s", type=float, default=LOCAL_BRAIN_TIMEOUT_S)
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
        # 2 MB rotation, same convention as the calibration ledger.
        # append_jsonl NEVER raises — it returns an error string (the shared
        # observation-loop contract); an earlier try/except OSError here was
        # dead code that ALSO discarded the return, so a lost calibration
        # record was doubly silent in the one module whose purpose is "the
        # pass-rate has a history, not a vibe".
        err = append_jsonl(args.history, [{**summary, "results": results}],
                           max_bytes=2 * 1024 * 1024)
        if err:
            print(f"local_brain_eval: WARN history append failed: {err}",
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
