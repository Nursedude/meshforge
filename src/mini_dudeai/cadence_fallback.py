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
  looks-ratifiable   internally consistent, evidence named, low-risk
  looks-rejectable   duplicate/transient/superseded on its face
  needs-live-check   anything whose truth needs a command or live source

Include EVERY listed delta exactly once — a triage that skips deltas is
incomplete. Use each delta's `key` verbatim. Output JSON only, matching the
schema."""


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


def build_user_prompt(proposed: List[dict]) -> str:
    lines = ["Proposed memory-deltas awaiting the frontier session:"]
    for i, d in enumerate(proposed, 1):
        summary = str(d.get("summary") or "")[:200]
        lines.append(f'{i}. key="{d.get("key")}" kind={d.get("kind")} '
                     f"summary: {summary}")
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
        now: Optional[float] = None) -> dict:
    """Produce the witness dict (pure orchestration; no writes here)."""
    now = time.time() if now is None else now
    iso = iso_or_none(now)
    proposed, total = load_proposed_deltas(deltas_path, cap=max_deltas)
    base = {
        "ts": now,
        "iso": iso,
        "frontier_rc": frontier_rc,
        "proposed_total": total,
        "never_ratifies": True,
        "deltas_path": deltas_path,
    }
    if not proposed:
        return {**base, "brain_tier": "rules", "triaged": 0, "deltas": [],
                "summary": "no proposed deltas at fallback time"}
    try:
        raw = backend.complete(_SYSTEM_PROMPT, build_user_prompt(proposed),
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
    return {**base, "brain_tier": "local",
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
        resolve_home(), "mini_dudeai_memory_deltas.jsonl"))
    ap.add_argument("--out", default=default_witness_path())
    ap.add_argument("--clear", action="store_true",
                    help="retire the witness (a SUCCESSFUL frontier session "
                         "consumed the backlog it described); idempotent")
    ap.add_argument("--frontier-rc", default="",
                    help="exit code of the failed frontier session "
                         "(empty = claude CLI missing)")
    ap.add_argument("--max-deltas", type=int, default=MAX_DELTAS_DEFAULT)
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
                  max_deltas=args.max_deltas)
    try:
        atomic_write_json(args.out, witness)
    except OSError as e:
        print(f"cadence_fallback: witness write FAILED: {e}", file=sys.stderr)
        return 1
    print(f"cadence_fallback: brain_tier={witness['brain_tier']} "
          f"triaged={witness.get('triaged', 0)}/{witness['proposed_total']} "
          f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
