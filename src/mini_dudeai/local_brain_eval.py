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
    ClaudeCLIBackend,
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
        # The vocabulary is closed and comes from the production triage path
        # (SSOT import, not a copied literal). A typo'd disposition would make
        # the case PERMANENTLY unpassable while looking like a model failure --
        # an authoring error graded as a capability loss (honest_failure_modes
        # #3). Latent until 2026-07-25, when the ratifiable-direction cases
        # became the first to type "looks-ratifiable" at all.
        if not allowed:
            raise EvalConfigError(
                f"{where}: dispositions[{key!r}] is empty — no answer could "
                f"ever satisfy it")
        bad = [d for d in allowed if d not in cadence_fallback.DISPOSITIONS]
        if bad:
            raise EvalConfigError(
                f"{where}: dispositions[{key!r}] has unknown disposition(s) "
                f"{bad} — allowed: {list(cadence_fallback.DISPOSITIONS)}")
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
    wheres: List[str] = []
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
            wheres.append(where)
    _validate_discriminating(cases, wheres)
    return cases


#: A term this short cannot characterise an answer. Measured 2026-08-04: the
#: literal term ``"0"`` let five unrelated cases' answers pass, because almost
#: any prose containing a number satisfies it. The floor is deliberately LOW —
#: short does not mean weak (``GIL`` is decisive, and so is the issue number
#: ``10468``) — because the containment rule below, not length, is what actually
#: does the discriminating.
_MIN_TERM_LEN = 3


def _validate_discriminating(cases: List[dict],
                             wheres: Optional[List[str]] = None) -> None:
    """Reject an expectation that cannot tell a RIGHT answer from a WRONG one.

    WHY THIS EXISTS (2026-08-04). The eval's structure was sound — retrieval and
    synthesis graded separately, production code paths, best-of-N — but its
    ASSERTIONS were not, and nothing measured that. Mutation-testing the eval
    (feed each case another case's answer and assert it FAILS) found 17 of 31
    answer-graded cases accepting an answer about a DIFFERENT case, every one of
    them through a term shared with another case's list: ``restart`` alone let 12
    foreign answers through, then ``offline``, ``announce``, ``0``, ``gate``.
    Since ``answer_contains_any`` is an OR, ONE such term makes the whole
    assertion vacuous no matter how precise its siblings are.

    Fixing the 17 cases by hand would leave the next author free to reintroduce
    it, so the rule lives here instead: reject at AUTHORING time what the author
    cannot have meant (honest_failure_modes #3). A term that appears in two
    cases' lists is by construction unable to separate them.
    """
    seen: List[Tuple[str, str]] = []          # (term, owning case id)
    problems: List[str] = []
    for i, case in enumerate(cases):
        where = (wheres[i] if wheres and i < len(wheres)
                 else case.get("id", "?"))
        for term in (case.get("expect", {}).get("answer_contains_any") or []):
            key = term.strip().lower()
            if len(key) < _MIN_TERM_LEN:
                problems.append(
                    f"{where}: term {term!r} is too short to discriminate "
                    f"(need >= {_MIN_TERM_LEN} chars)")
                continue
            # CONTAINMENT, not equality — the grader asks `term in answer`, so
            # a case claiming 'truncat' is satisfied by an answer whose real
            # subject is another case's 'truncated'. Equality would have called
            # that pair distinct while the grader could not tell them apart;
            # the check has to use the same relation the consumer does.
            clash = next(((t, owner) for t, owner in seen
                          if owner != case["id"] and (t in key or key in t)),
                         None)
            if clash:
                t, owner = clash
                how = "identical to" if t == key else \
                      (f"contains {t!r} from" if t in key else
                       f"is contained in {t!r} from")
                problems.append(
                    f"{where}: term {term!r} {how} case {owner!r} — the grader "
                    f"could not tell those two answers apart")
                continue
            seen.append((key, case["id"]))
    if problems:
        # Report the WHOLE set, not the first: an author fixing these one
        # re-run at a time learns the rule far more slowly than one who sees
        # the shape of the problem at once.
        raise EvalConfigError(
            f"{len(problems)} non-discriminating answer term(s) — "
            f"answer_contains_any is an OR, so ONE of these makes its whole "
            f"case vacuous:\n  " + "\n  ".join(problems))


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
    expected_tier = getattr(backend, "brain_tier", "local")
    if witness.get("brain_tier") != expected_tier:
        reasons.append(f"no {expected_tier} triage produced: "
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


def _path_satisfies(frag: str, paths: List[str]) -> bool:
    """Does any retrieved path NAME the file this fragment asks for?

    Matched on the basename's PREFIX. The old rule — substring against every
    path joined into one string — is satisfied by anything that merely contains
    the fragment somewhere: a fabricated ``/tmp/decoy-<frag>-notreal.md``, or
    the corpus DIRECTORY, which is why ``memory`` was satisfied by all 339 files
    under the memory root. Measured 2026-08-04 by mutation-testing the eval: a
    decoy path satisfied 29 of 29 retrieval assertions, so this axis was
    asserting nothing at all. A checker that accepts fabricated evidence is the
    07-25 self-confirming-detector lesson, one layer up.
    """
    return any(os.path.basename(p).startswith(frag) for p in paths)


def grade_oracle(case: dict, backend) -> Tuple[bool, List[str], dict]:
    """Ask the PRODUCTION oracle path and grade retrieval + citations +
    answer content separately — a retrieval miss and a synthesis miss are
    different diagnoses."""
    inp = case["input"]
    expect = case["expect"]
    result = offline_oracle.ask(inp.get("question") or "", backend,
                                top_k=inp.get("top_k", 6))
    reasons: List[str] = []
    expected_tier = getattr(backend, "brain_tier", "local")
    retrieved_paths = [r["path"] for r in result.get("retrieved") or []]
    for frag in expect.get("retrieve_must_include") or []:
        if not _path_satisfies(frag, retrieved_paths):
            reasons.append(f"retrieval missing {frag!r}")
    if expect.get("expect_refusal"):
        # Honest-refusal case (the substitute-and-narrate-success wart, W5.1):
        # the ONLY passing behavior for an ungroundable question is declining
        # to answer — a confident grounded-looking answer IS the failure.
        if result.get("brain_tier") == expected_tier:
            reasons.append(
                f"fabricated a grounded answer where honest output is a "
                f"refusal: {str(result.get('answer', ''))[:160]}")
        return not reasons, reasons, result
    if expect.get("require_answer", True):
        if result.get("brain_tier") != expected_tier:
            reasons.append(f"no grounded answer: "
                           f"{str(result.get('note', '?'))[:160]}")
        else:
            cited_paths = [s["path"] for s in result["sources"]]
            for frag in expect.get("cite_must_include") or []:
                if not _path_satisfies(frag, cited_paths):
                    reasons.append(f"citations missing {frag!r}")
            anyof = expect.get("answer_contains_any") or []
            if anyof and not any(a.lower() in result["answer"].lower()
                                 for a in anyof):
                reasons.append(f"answer contains none of {anyof!r}")
    return not reasons, reasons, result


_GRADERS = {"triage": grade_triage, "compile": grade_compile,
            "oracle": grade_oracle}


#: Markers that identify a failure as the BACKEND being unreachable rather
#: than the model answering wrongly. Kept as ONE constant, here beside the
#: graders that surface them, and pinned by a test against the real strings
#: (honest_failure_modes #5 — two independent copies WILL drift).
#:
#: ⚠️ "synthesis failed" is deliberately NOT a marker. offline_oracle wraps
#: BOTH a transport ``CompilerError`` AND a genuine bad-shape ``ValueError``
#: from the model in that same sentence, so matching on it would launder a
#: real model failure into "unobserved" — the opposite error, and the more
#: dangerous one (a capability loss silently excused).
TRANSPORT_FAILURE_MARKERS = (
    # chat_compiler: URLError / OSError / TimeoutError reaching Ollama.
    "is the server up and the model",
    # chat_compiler: the backend answered, with nothing in it.
    "returned no message content",
)


def _is_transport_failure(reasons) -> bool:
    """True when EVERY reason is a backend-unreachable marker.

    Deliberately ALL, not ANY: a case that both timed out on one attempt and
    produced a wrong answer on another has demonstrated a real miss, and
    excusing it would hide a regression behind a flaky link.
    """
    texts = [str(r) for r in (reasons or [])]
    if not texts:
        return False
    return all(any(m in t for m in TRANSPORT_FAILURE_MARKERS) for t in texts)


def _rotate_cases(cases: List[dict], last_id: Optional[str]) -> List[dict]:
    """Rotate the (deterministically ordered) case list to start AFTER the
    last completed case, wrapping — so budget-chunked runs walk the whole
    set across successive firings instead of re-grading the same head. An
    unknown/absent last_id starts from the top (case-set edits self-heal)."""
    if not last_id:
        return cases
    ids = [c["id"] for c in cases]
    if last_id not in ids:
        return cases
    idx = ids.index(last_id)
    return cases[idx + 1:] + cases[:idx + 1]


def _read_cursor(path: str) -> Optional[str]:
    """Return the last completed case id, or None. A missing cursor is a
    fresh start; a CORRUPT one is said out loud (stderr) and treated as
    fresh — silently resuming from garbage would skew which cases run
    (honest_failure_modes #1: the degraded value must not look healthy)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        last = data.get("last_id")
        return last if isinstance(last, str) and last else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"local_brain_eval: WARN cursor {path} unreadable "
              f"({e}) — starting from the top", file=sys.stderr)
        return None


def _write_cursor(path: str, case_id: str) -> None:
    """Atomic (tmp+rename) so a cut mid-write can't leave a torn cursor;
    written after EVERY completed case so even a killed run resumes at the
    right spot. Failure is loud, never fatal — the eval matters more than
    its bookmark."""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"last_id": case_id, "ts": time.time()}, f)
            os.replace(tmp, path)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    except OSError as e:
        print(f"local_brain_eval: WARN cursor write failed: {e}",
              file=sys.stderr)


def run_cases(cases: List[dict], backend, progress=None,
              budget_s: Optional[float] = None) -> Tuple[List[dict], dict]:
    """SEQUENTIAL by construction — one local model resident at a time (the
    box's freeze class is why this harness exists at all).

    ``progress(done, planned, result)`` fires after EVERY graded case — the
    per-case witness that makes a killed run leave evidence instead of a
    blank log (the 2026-07-21 rerun burned 100 min and reported NOTHING).

    ``budget_s`` stops STARTING new cases once the wall-clock budget is
    spent (at least one case always runs, so every firing makes progress).
    Cases not reached are recorded as ``not_run_ids`` — deferred honestly,
    never counted as passed or failed."""
    results: List[dict] = []
    not_run_ids: List[str] = []
    budget_exhausted = False
    planned = len(cases)
    t_run0 = time.monotonic()
    for i, case in enumerate(cases):
        if (budget_s is not None and results
                and (time.monotonic() - t_run0) >= budget_s):
            budget_exhausted = True
            not_run_ids = [c["id"] for c in cases[i:]]
            break
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
            # Did this case fail because the BACKEND could not be reached,
            # rather than because the model answered wrongly? Recorded
            # structurally so no consumer has to substring-match a human
            # sentence (2026-08-07): probe_local_brain_regressed counted a
            # timed-out Ollama as "the tier-L model lost a capability" and
            # asserted that for three days off one saturated run. A case the
            # channel never reached was not OBSERVED — it is neither a pass
            # nor a regression (honest_failure_modes #2).
            "transport_error": (not ok) and _is_transport_failure(reasons),
            "latency_s": round(time.monotonic() - t0, 1),
            "attempts": attempts,
            "attempts_used": used,
            "provenance": case.get("provenance"),
        })
        if progress is not None:
            progress(len(results), planned, results[-1])
    passed = sum(1 for r in results if r["ok"])
    # pass@1 alongside pass@N, always. PRODUCTION NEVER RETRIES —
    # offline_oracle.ask and cadence_fallback each make exactly ONE call per
    # question/chunk — so a best-of-N rate measures a capability the night
    # watcher does not have, and model_router reads these numbers to decide
    # whether to delegate to tier-L at all. Reporting only the retried rate
    # would overstate delivered reliability; reporting only pass@1 would fail a
    # capable tier on sampling variance. Both, so the DIFFERENCE is visible as
    # its own quantity instead of folded into a headline (calibrated-claims:
    # surface the blind spot, never average it away).
    passed_first = sum(1 for r in results
                       if r["ok"] and r.get("attempts_used") == 1)
    per_kind: Dict[str, dict] = {}
    for r in results:
        k = per_kind.setdefault(r["kind"],
                                {"passed": 0, "total": 0, "passed_first": 0})
        k["total"] += 1
        k["passed"] += 1 if r["ok"] else 0
        k["passed_first"] += 1 if (r["ok"]
                                   and r.get("attempts_used") == 1) else 0
    now = time.time()
    iso = iso_or_none(now)
    summary = {
        "ts": now, "iso": iso,
        "model": getattr(backend, "model", "?"),
        "url": getattr(backend, "url", "?"),
        "backend": type(backend).__name__,
        "brain_tier": getattr(backend, "brain_tier", "local"),
        "total": len(results), "passed": passed,
        "passed_first": passed_first,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "pass_at_1": round(passed_first / len(results), 3) if results else 0.0,
        "per_kind": per_kind,
        "failed_ids": [r["id"] for r in results if not r["ok"]],
        # Budget-chunking honesty: pass_rate/total judge only COMPLETED
        # cases; deferred ones are named, not averaged away (calibrated-
        # claims rule 5 — surface the blind spot).
        "planned_total": planned,
        "not_run_ids": not_run_ids,
        "budget_exhausted": budget_exhausted,
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
    ap.add_argument("--backend", choices=("ollama", "claude-cli"),
                    default="ollama",
                    help="candidate brain: 'ollama' (production tier-L, the "
                         "default and the only backend the weekly gate "
                         "judges) or 'claude-cli' (Anthropic small model "
                         "via the local claude CLI — the QTH middle-rung "
                         "CANDIDATE; haiku_watcher_eval charter)")
    ap.add_argument("--cli-model", default=os.environ.get(
        "MINI_DUDEAI_CLI_MODEL", "claude-haiku-4-5"),
        help="model id for --backend claude-cli")
    ap.add_argument("--history", default=os.path.join(
        resolve_home(), EVAL_RESULTS_BASENAME),
        help="results ledger (JSONL, appended); empty string disables")
    ap.add_argument("--gate", type=float, default=None,
                    help="exit 1 unless pass_rate >= this fraction — the "
                         "'tier L can X' claim gate")
    ap.add_argument("--budget-s", type=float, default=None,
                    help="stop starting new cases after this many seconds "
                         "(completed cases still grade + gate; the rest are "
                         "deferred as not_run_ids). Pair with --cursor so "
                         "successive runs walk the whole set. 33 cases at "
                         "~340s/case outgrew the weekly cron's timeout 6000 "
                         "(2026-07-21: exit 124, zero output)")
    ap.add_argument("--cursor", default=None,
                    help="JSON bookmark file; each run resumes AFTER the "
                         "last case the previous run completed (wrapping), "
                         "updated per-case so even a killed run resumes "
                         "correctly")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    paths = args.cases if args.cases else sorted(glob.glob(DEFAULT_CASES_GLOB))
    try:
        cases = load_cases(paths)
    except (EvalConfigError, OSError) as e:
        print(f"local_brain_eval: {e}", file=sys.stderr)
        return 2

    if args.cursor:
        cases = _rotate_cases(cases, _read_cursor(args.cursor))

    if args.backend == "claude-cli":
        backend = ClaudeCLIBackend(model=args.cli_model,
                                   timeout_s=args.timeout_s)
    else:
        backend = OllamaBackend(url=args.url, model=args.model,
                                timeout_s=args.timeout_s)

    def _progress(done: int, planned: int, r: dict) -> None:
        # stderr, flushed: the per-case witness lands in the cron log AS
        # each case grades — a timeout can no longer wipe a whole run's
        # evidence (--json keeps stdout machine-clean either way).
        mark = "PASS" if r["ok"] else "FAIL"
        tail = "" if r["ok"] else f"  — {'; '.join(r['reasons'])[:160]}"
        print(f"[{done}/{planned}] {mark}  {r['id']}  "
              f"({r['kind']}, {r['latency_s']}s, "
              f"try {r['attempts_used']}/{r['attempts']}){tail}",
              file=sys.stderr, flush=True)
        if args.cursor:
            _write_cursor(args.cursor, r["id"])

    results, summary = run_cases(cases, backend, progress=_progress,
                                 budget_s=args.budget_s)

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
        # The retried rate is what the gate judges; pass@1 is what production
        # actually gets, since neither the oracle nor the cadence fallback
        # retries. Printed side by side so a widening gap is visible.
        if summary["pass_at_1"] != summary["pass_rate"]:
            print(f"local_brain_eval: pass@1 {summary['passed_first']}/"
                  f"{summary['total']} (rate {summary['pass_at_1']}) — "
                  f"{summary['passed'] - summary['passed_first']} case(s) "
                  f"needed a retry; production does NOT retry")
    if summary["budget_exhausted"]:
        print(f"local_brain_eval: budget exhausted — "
              f"{len(summary['not_run_ids'])} of "
              f"{summary['planned_total']} case(s) deferred to the next run"
              + (f" (cursor {args.cursor})" if args.cursor else ""),
              file=sys.stderr, flush=True)

    if args.gate is not None and summary["pass_rate"] < args.gate:
        print(f"local_brain_eval: GATE FAILED "
              f"({summary['pass_rate']} < {args.gate})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
