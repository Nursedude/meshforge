"""Local-tier cadence fallback — W1 of the degraded-brain ladder
(.claude/research/dudeclaw_local_brain_2026_07_03.md §3/§4).

The frontier cadence's JOB — verify each proposed memory-delta against live
truth, then ratify or reject — is high-judgment work a small local model must
NEVER attempt: it cannot run live checks, and ratification on 3B judgment is
the Phase-B lesson inverted ("3B models compile; humans ratify"). What the
local tier CAN do honestly when the frontier is unavailable is **triage**:
organize the proposed backlog so the returning frontier session (or the
operator) starts oriented instead of cold.

Products — every artifact stamped ``brain_tier`` so a local product can never
read as frontier work (#80 at system scale):

  ``~/mini_dudeai_cadence_triage.json``  the witness. ``brain_tier: local``
  when the LLM triaged; ``brain_tier: rules`` when even that was impossible
  and only this deterministic note could be written.

Hard invariants:
  * NEVER ratifies, NEVER writes canonical memory, rules, or candidates —
    the only write is the triage witness (test-pinned by source scan).
  * Dispositions are SUGGESTIONS for the frontier/human; the vocabulary says
    so ("looks-…", "needs-live-check") and every witness carries
    ``never_ratifies: true``.
  * The launcher never exits 0 through this path, so the ``mini_cadence OK``
    cron verdict — the display tier glyph's F evidence — stays frontier-only.
  * Model output is schema-constrained (Ollama ``format``) AND re-validated:
    entries whose ``key`` was not in the fed set are dropped (a model cannot
    invent backlog), oversize text is clamped, and a triage that survives
    with zero valid entries is a failure, not an empty success.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from ._util import atomic_write_json, iso_or_none, resolve_home
from .chat_compiler import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    LOCAL_BRAIN_TIMEOUT_S,
    CompilerError,
    OllamaBackend,
)
from .dreams import DELTAS_BASENAME

# One constant, two consumers: this module writes the witness, brief.py
# renders the "cadence ran on LOCAL tier" line from it.
CADENCE_TRIAGE_BASENAME = "mini_dudeai_cadence_triage.json"

# A witness older than this no longer earns a warm-brief line (the next
# frontier session has either handled the backlog or will re-derive it).
TRIAGE_FRESH_S = 24 * 3600.0

# Fed-set cap, derived from measured latency (qwen3-4B warm ≈ 30 s/entry on
# the Pi 5): 12 entries ≈ 6 min, inside the 480 s client bound below. The
# TOTAL count stays honest in the witness regardless of the cap.
MAX_DELTAS_DEFAULT = 12
_ASSESSMENT_CLAMP = 300
_SUMMARY_CLAMP = 500

DISPOSITIONS = ("looks-ratifiable", "looks-rejectable", "needs-live-check")

# WHY the local triage ran — kept honest and distinct (honest_failure_modes #2:
# never conflate two states). "pre-score": the frontier session is about to run
# and will CONSUME this triage to prioritise (the increment-3 pre-scoring path).
# "fallback": the frontier was absent/failed and this is the degraded stand-in.
# In BOTH modes the local tier only SUGGESTS — it never ratifies.
MODES = ("pre-score", "fallback")
DEFAULT_MODE = "fallback"   # back-compat: existing callers/eval keep prior behavior

TRIAGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "deltas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "assessment": {"type": "string"},
                    "suggested_disposition": {
                        "type": "string", "enum": list(DISPOSITIONS),
                    },
                },
                "required": ["key", "assessment", "suggested_disposition"],
            },
        },
    },
    "required": ["summary", "deltas"],
}

_SYSTEM_PROMPT = """You are the LOCAL fallback tier of a fleet monitoring \
assistant. The frontier session that normally VERIFIES and ratifies proposed \
memory-deltas is unavailable. You cannot run checks and must never claim to \
have verified anything.

Your only job: for each proposed delta below, write a one-line assessment \
(what it appears to record, and what a verifier should check) and suggest a \
disposition for the HUMAN or frontier session to act on later:
  looks-ratifiable   internally consistent, evidence named, low-risk, AND
                     either corroborated by the DOMAIN CONTEXT below or
                     verifiable without a command
  looks-rejectable   duplicate/transient/superseded on its face, OR the
                     domain context contradicts it
  needs-live-check   anything whose truth needs a command or live source

INTERNAL CONSISTENCY IS NOT ENOUGH. A confident, well-written, self-consistent
claim about how a system BEHAVES is exactly the shape of the dangerous ones —
a frontier session's plausible wrong conclusion reads identically to a right
one. If a delta asserts system behaviour and the domain context neither
corroborates nor is present, prefer needs-live-check over looks-ratifiable.
Unverified is not ratifiable.

Where DOMAIN CONTEXT is supplied it comes from this fleet's OWN records and
outranks the delta's own reasoning: if an excerpt contradicts the proposal,
say so in the assessment and do not suggest ratifying it.

Include EVERY listed delta exactly once — a triage that skips deltas is
incomplete. Use each delta's `key` verbatim. Output JSON only, matching the
schema."""

# Retrieval grounding budget. Deliberately small: the prompt is context, context
# is KV cache, and KV cache is RAM on a fleet whose smallest box has 905 MB
# (feedback_my_footprint_is_the_constraint). Three excerpts clamped to ~700 chars
# each is enough to corroborate or contradict a claim without turning a triage
# into a document dump.
_CTX_TOP_K = 3
_CTX_EXCERPT_CLAMP = 700


def load_proposed_deltas(path: str, cap: int = MAX_DELTAS_DEFAULT,
                         ) -> Tuple[List[dict], int]:
    """Proposed deltas (oldest first, capped) + the TOTAL proposed count.

    Tolerant of a torn tail (#80: a partial line is skipped, never lets one
    bad record hide the rest); a missing file is simply zero deltas.
    """
    proposed: List[dict] = []
    total = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("status") == "proposed":
                    total += 1
                    if len(proposed) < cap:
                        proposed.append(d)
    except OSError:
        return [], 0
    return proposed, total


def retrieve_context(proposed: List[dict], *, top_k: int = _CTX_TOP_K,
                     clamp: int = _CTX_EXCERPT_CLAMP,
                     roots=None) -> Tuple[Dict[str, List[dict]], Optional[str]]:
    """Per-delta domain grounding from the fleet's OWN records.

    Returns ``({delta_key: [{path, heading, text}, ...]}, note)``. ``note`` is
    None on success and a human-readable reason when grounding is UNAVAILABLE —
    it is threaded into the prompt so the model is told it is judging blind
    rather than being handed a silently ungrounded prompt (honest_failure_modes
    #2: unobservable must not read as "nothing relevant exists").

    Deterministic and LLM-free: this is tier R (BM25 over markdown), the same
    assembly behind ``offline_oracle --retrieve-only``. The corpus is loaded ONCE
    and re-ranked per delta.

    WHY THIS EXISTS (2026-07-24): the oracle path was fully grounded while this
    triage path — the one that decides what may enter long-term memory — had no
    retrieval at all. That is inverted: the gate protecting memory integrity was
    the ungrounded one. Measured consequence: tier-L rated three of five
    deliberately-wrong proposals ``looks-ratifiable`` because the facts that
    refute them (an ``After=`` in a user unit is inert; moc3's cap window is
    1.25x; a cap kill is ambiguous) live in memory files it was never shown.
    """
    try:
        from . import offline_oracle
    except Exception as exc:                     # pragma: no cover - import guard
        return {}, f"retrieval module unavailable: {str(exc)[:120]}"
    try:
        chunks, _notes, index = offline_oracle.load_corpus_indexed(roots)
    except Exception as exc:
        return {}, f"corpus unreadable: {str(exc)[:120]}"
    if not chunks:
        return {}, "corpus empty (no memory/persistent_issues markdown found)"
    out: Dict[str, List[dict]] = {}
    for d in proposed:
        key = d.get("key")
        if not key:
            continue
        query = f"{key} {d.get('summary') or ''}"
        try:
            hits = offline_oracle.rank(chunks, query, top_k=top_k, index=index)
        except Exception as exc:
            # One bad query never kills the rest — but a FAILED retrieval must
            # not render as observed absence ("no matching record found"); mark
            # it so the prompt states failure distinctly (hfm #2; 07-26 D12).
            out[key] = [{"retrieval_failed": type(exc).__name__}]
            continue
        out[key] = [{"path": h.get("path", "?"),
                     "heading": h.get("heading", ""),
                     "text": str(h.get("text") or "")[:clamp]}
                    for h in hits]
    return out, None


def build_user_prompt(proposed: List[dict],
                      context: Optional[Dict[str, List[dict]]] = None,
                      context_note: Optional[str] = None) -> str:
    """Render the triage prompt, with domain grounding when available.

    ``context=None`` keeps the historic ungrounded rendering byte-identical for
    callers that pass nothing. An empty-but-present context, or a
    ``context_note``, is stated EXPLICITLY so a blind judgement is visibly blind.
    """
    lines = ["Proposed memory-deltas awaiting the frontier session:"]
    for i, d in enumerate(proposed, 1):
        summary = str(d.get("summary") or "")[:200]
        lines.append(f'{i}. key="{d.get("key")}" kind={d.get("kind")} '
                     f"summary: {summary}")
    if context is None and not context_note:
        return "\n".join(lines)

    lines.append("")
    if context_note:
        lines.append(f"DOMAIN CONTEXT UNAVAILABLE ({context_note}). You are "
                     f"judging without the fleet's records: prefer "
                     f"needs-live-check for anything asserting system "
                     f"behaviour.")
        return "\n".join(lines)

    lines.append("DOMAIN CONTEXT — excerpts retrieved from this fleet's own "
                 "records. These outrank the proposals above.")
    for d in proposed:
        key = d.get("key")
        hits = (context or {}).get(key) or []
        if hits and isinstance(hits[0], dict) and hits[0].get("retrieval_failed"):
            # Failed retrieval is its OWN state — never presented as absence.
            lines.append(f'\nkey="{key}": retrieval FAILED for {key}: '
                         f"{hits[0]['retrieval_failed']} — grounding is "
                         f"UNAVAILABLE for this delta (not absent); prefer "
                         f"needs-live-check.")
            continue
        if not hits:
            lines.append(f'\nkey="{key}": no matching record found. Absence of '
                         f"a record is NOT corroboration — prefer "
                         f"needs-live-check.")
            continue
        lines.append(f'\nkey="{key}":')
        for j, h in enumerate(hits, 1):
            lines.append(f"  [{j}] {h['path']} — {h['heading']}")
            lines.append(f"      {h['text']}")
    return "\n".join(lines)


def _validate_triage(raw: str, fed_keys: set) -> Tuple[dict, int]:
    """Parse + re-validate the model's triage. Returns (triage, dropped).

    Schema constraint reduces malformed output but cannot prevent truncation
    or invented keys — the consumer keeps parse-validate (research doc §2.3).
    Raises ValueError when nothing usable survives.
    """
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"triage not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"triage not an object: {type(doc).__name__}")
    summary = doc.get("summary")
    entries = doc.get("deltas")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("triage missing a summary")
    if not isinstance(entries, list):
        raise ValueError("triage missing the deltas list")
    kept: List[dict] = []
    seen_keys: set = set()
    dropped = 0
    for ent in entries:
        # Duplicate keys are DROPPED, not kept: the prompt demands "every
        # delta exactly once", and a kept duplicate inflates `triaged` —
        # a model that repeats delta A while skipping delta B would read
        # as 2/2 coverage (and pass the eval gate) when it really covered
        # 1/2. First occurrence wins; the rest count as drops so the
        # witness (and max_dropped gates) see the sloppiness.
        if (isinstance(ent, dict)
                and ent.get("key") in fed_keys
                and ent.get("key") not in seen_keys
                and isinstance(ent.get("assessment"), str)
                and ent.get("suggested_disposition") in DISPOSITIONS):
            seen_keys.add(ent["key"])
            kept.append({
                "key": ent["key"],
                "assessment": ent["assessment"][:_ASSESSMENT_CLAMP],
                "suggested_disposition": ent["suggested_disposition"],
            })
        else:
            dropped += 1
    if not kept:
        raise ValueError(
            f"no valid triage entries survived validation ({dropped} dropped)")
    return {"summary": summary[:_SUMMARY_CLAMP], "deltas": kept}, dropped


def run(deltas_path: str, backend, frontier_rc: Optional[int],
        max_deltas: int = MAX_DELTAS_DEFAULT,
        now: Optional[float] = None, mode: str = DEFAULT_MODE) -> dict:
    """Produce the witness dict (pure orchestration; no writes here).

    ``mode`` records WHY the triage ran (``MODES``): ``pre-score`` when the
    frontier session is about to consume it to prioritise, ``fallback`` when the
    frontier was gone. It changes nothing about the never-ratifies contract —
    both modes only SUGGEST — it just keeps the two states honest for the brief.
    """
    now = time.time() if now is None else now
    iso = iso_or_none(now)
    if mode not in MODES:
        # Reject what the author cannot have meant (hfm #3; 07-23 audit): a
        # typo'd library-caller mode silently becoming "fallback" would render
        # in the brief as a frontier outage that never happened. CLI callers
        # are argparse-validated and can't reach this.
        raise ValueError(f"unknown triage mode {mode!r} (want one of {MODES})")
    proposed, total = load_proposed_deltas(deltas_path, cap=max_deltas)
    base = {
        "ts": now,
        "iso": iso,
        "mode": mode,
        "frontier_rc": frontier_rc,
        "proposed_total": total,
        "never_ratifies": True,
        "deltas_path": deltas_path,
    }
    if not proposed:
        return {**base, "brain_tier": "rules", "triaged": 0, "deltas": [],
                "summary": "no proposed deltas at fallback time"}
    # Ground the judgement in the fleet's own records before asking the model.
    # The witness records WHETHER grounding happened, so a blind triage is
    # visible downstream instead of looking identical to a grounded one.
    # Grounding is an ENHANCEMENT: an unexpected failure in it must degrade the
    # triage to blind-with-a-witness, never crash the ratifier. retrieve_context
    # already catches its own known failure modes; this is the backstop for the
    # unknown ones (a probe must not die of bookkeeping — hfm #9, and the note is
    # the witness that says the judgement was made blind).
    try:
        ctx, ctx_note = retrieve_context(proposed)
    except Exception as exc:                     # noqa: BLE001 - deliberate backstop
        ctx, ctx_note = {}, f"grounding failed: {str(exc)[:120]}"
        logger_warned = getattr(run, "_grounding_warned", False)
        if not logger_warned:
            setattr(run, "_grounding_warned", True)
            print(f"cadence_fallback: retrieval grounding unavailable "
                  f"({str(exc)[:160]}) — triage will judge blind",
                  file=sys.stderr)
    grounded_keys = sum(1 for v in ctx.values() if v)
    base["context_grounded_keys"] = grounded_keys
    base["context_note"] = ctx_note
    try:
        raw = backend.complete(_SYSTEM_PROMPT,
                               build_user_prompt(proposed, ctx, ctx_note),
                               fmt=TRIAGE_SCHEMA)
        triage, dropped = _validate_triage(raw, {d.get("key") for d in proposed})
    except (CompilerError, ValueError) as e:
        # The LLM tier failed too: the witness degrades to a deterministic
        # note — an honest "backlog pending, nobody triaged it", never a
        # fabricated triage (#80).
        return {**base, "brain_tier": "rules", "triaged": 0, "deltas": [],
                "summary": f"{total} proposed delta(s) pending; local LLM "
                           f"triage unavailable",
                "error": str(e)[:300]}
    # Tier comes from the BACKEND's declaration (default keeps the historic
    # Ollama behavior byte-identical): an api_small triage must never stamp
    # itself tier-L (haiku_watcher_eval charter invariant 4).
    return {**base, "brain_tier": getattr(backend, "brain_tier", "local"),
            "model": getattr(backend, "model", "?"),
            "triaged": len(triage["deltas"]),
            "dropped_entries": dropped,
            "summary": triage["summary"],
            "deltas": triage["deltas"]}


def default_witness_path() -> str:
    return os.path.join(resolve_home(), CADENCE_TRIAGE_BASENAME)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Local-tier cadence fallback: triage proposed memory-"
                    "deltas for the returning frontier. Never ratifies.")
    ap.add_argument("--deltas", default=os.path.join(
        resolve_home(), DELTAS_BASENAME))
    ap.add_argument("--out", default=default_witness_path())
    ap.add_argument("--clear", action="store_true",
                    help="retire the witness (a SUCCESSFUL frontier session "
                         "consumed the backlog it described); idempotent")
    ap.add_argument("--frontier-rc", default="",
                    help="exit code of the failed frontier session "
                         "(empty = claude CLI missing)")
    ap.add_argument("--max-deltas", type=int, default=MAX_DELTAS_DEFAULT)
    ap.add_argument("--mode", choices=MODES, default=DEFAULT_MODE,
                    help="pre-score (frontier will consume this to prioritise) "
                         "or fallback (frontier was gone). Suggestions only in "
                         "both; the tier NEVER ratifies.")
    ap.add_argument("--url",
                    default=os.environ.get("MINI_DUDEAI_OLLAMA_URL",
                                           DEFAULT_OLLAMA_URL))
    ap.add_argument("--model",
                    default=os.environ.get("MINI_DUDEAI_OLLAMA_MODEL",
                                           DEFAULT_MODEL))
    ap.add_argument("--timeout-s", type=float, default=LOCAL_BRAIN_TIMEOUT_S)
    args = ap.parse_args(argv)

    if args.clear:
        try:
            os.remove(args.out)
            print(f"cadence_fallback: witness retired ({args.out})")
        except FileNotFoundError:
            print("cadence_fallback: no witness to retire")
        except OSError as e:
            print(f"cadence_fallback: witness retire FAILED: {e}",
                  file=sys.stderr)
            return 1
        return 0

    frontier_rc: Optional[int] = None
    if str(args.frontier_rc).strip():
        try:
            frontier_rc = int(args.frontier_rc)
        except ValueError:
            frontier_rc = None

    backend = OllamaBackend(url=args.url, model=args.model,
                            timeout_s=args.timeout_s)
    witness = run(args.deltas, backend, frontier_rc,
                  max_deltas=args.max_deltas, mode=args.mode)
    try:
        atomic_write_json(args.out, witness)
    except OSError as e:
        print(f"cadence_fallback: witness write FAILED: {e}", file=sys.stderr)
        return 1
    print(f"cadence_fallback: mode={witness['mode']} "
          f"brain_tier={witness['brain_tier']} "
          f"triaged={witness.get('triaged', 0)}/{witness['proposed_total']} "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
