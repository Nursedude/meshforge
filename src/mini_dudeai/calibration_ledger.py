"""Calibration ledger — mini-dudeai's observe→fire pattern, turned on ME.

Born 2026-06-15 with `.claude/rules/calibrated_claims.md` and the reflective
claim-gate (`scripts/claim_gate.py`). Those two make me *say* calibrated things;
this makes the calibration **measurable**. The operator's concern was the math:
*"when you say 100%% and we do it N more times, the math is wrong."* A ledger
that records every VERIFIED claim and later re-derives whether it HELD turns that
from an assertion into a number we can both watch — and shrink.

Append-only event log, exactly like `history.jsonl` (reusing its shared
`append_jsonl` posture: rotate-if-over-cap, repair-torn-tail, swallow-OSError).
Two event kinds:

  * ``claim``   — one per VERIFIED completion claim of consequence:
                  {kind, id, ts, session_id, model_id, claim_class, claim_text,
                   evidence, head_full, status:"open"}.  ``model_id`` is recorded
                  so a reliability shift after a model swap (the Fable 5 → Opus
                  lurch) is detectable, not invisible.
  * ``verdict`` — a later re-derivation of a claim's truth:
                  {kind, claim_id, ts, outcome:"held"|"broke", detail}.

Re-derivation is deliberately CONSERVATIVE (honest_failure_modes #2: unobservable
≠ resolved). A claim flips to ``held``/``broke`` ONLY when an external
honest_status verdict definitively covers the head it was claimed on. Everything
else stays ``open`` and is surfaced as *unverified*, never silently "recovered".
The heavyweight re-run-the-suite re-derivation is a daily cron's job; this module
provides the substrate and the cheap warm-start pass.

Stdlib only. Pure folding/decision functions are unit-tested (RED + GREEN); the
thin I/O uses the shared mini helpers.
"""
from __future__ import annotations

import hashlib
import os
import time

from ._util import log_warning, resolve_home
from .history import append_jsonl

#: 2 MB cap — matches the #79 ledger-rotation bound. Plenty of forensic history
#: at a handful of claims per session, but bounded on an unattended box.
DEFAULT_LEDGER_MAX_BYTES = 2_000_000

_DEFINITIVE = ("held", "broke")


def ledger_path(home: str | None = None) -> str:
    """The one ledger location: $CALIBRATION_LEDGER_PATH → <mini home>/…jsonl.

    Lives in the mini home next to ``mini_dudeai_brief.md``/``…_state.json`` so
    the warm-start emitter reads it with no extra path config."""
    env = os.environ.get("CALIBRATION_LEDGER_PATH")
    if env:
        return env
    home = home or resolve_home()
    return os.path.join(home, "calibration_ledger.jsonl")


def make_claim_id(ts: float, claim_text: str, head_full: str) -> str:
    """Stable short id from the claim's identity. Deterministic given inputs
    (so tests are not clock/random dependent)."""
    h = hashlib.sha1(
        f"{ts}\x00{claim_text}\x00{head_full}".encode("utf-8", "replace")
    ).hexdigest()
    return h[:12]


def record_claim(claim_text: str, claim_class: str, evidence: str,
                 head_full: str, *, model_id: str | None = None,
                 session_id: str | None = None, ts: float | None = None,
                 path: str | None = None,
                 max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Append one ``claim`` event and return the record (with its generated id).

    Best-effort persistence: an append failure leaves a log witness
    (honest_failure_modes #9) but never raises — recording a claim must not be
    able to crash whatever made the claim."""
    ts = time.time() if ts is None else ts
    path = path or ledger_path()
    rec = {
        "kind": "claim",
        "id": make_claim_id(ts, claim_text, head_full),
        "ts": ts,
        "session_id": session_id,
        "model_id": model_id,
        "claim_class": claim_class,
        "claim_text": claim_text,
        "evidence": evidence,
        "head_full": head_full,
        "status": "open",
    }
    err = append_jsonl(path, [rec], max_bytes)
    if err:
        log_warning(f"calibration_ledger: could not record claim {rec['id']}: {err}")
    return rec


def record_verdict(claim_id: str, outcome: str, detail: str = "", *,
                   ts: float | None = None, path: str | None = None,
                   max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Append one ``verdict`` event (a later re-derivation of a claim's truth)."""
    ts = time.time() if ts is None else ts
    path = path or ledger_path()
    rec = {"kind": "verdict", "claim_id": claim_id, "ts": ts,
           "outcome": outcome, "detail": detail}
    err = append_jsonl(path, [rec], max_bytes)
    if err:
        log_warning(f"calibration_ledger: could not record verdict for "
                    f"{claim_id}: {err}")
    return rec


def load_events(path: str | None = None) -> list[dict]:
    """Parse the ledger into a list of event dicts. Skips blank, torn, and
    unparseable lines (torn-tail safe). Never raises — a missing file is []."""
    import json
    path = path or ledger_path()
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return out
    except OSError as e:
        log_warning(f"calibration_ledger: could not read {path}: {e}")
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue  # torn / malformed line — skip, never fuse
        if isinstance(obj, dict):
            out.append(obj)
    return out


def fold(events: list[dict]) -> dict:
    """Reduce the event log to current calibration state.

    For each claim, its LATEST verdict (by ts) decides its bucket: ``held``,
    ``broke``, or — with no definitive verdict — ``open`` (unverified, NOT
    counted as either; unobservable ≠ resolved). The ratio is held / (held +
    broke) over claims that GOT a definitive re-derivation, or None when none
    have — honestly "of the claims we could re-check, X%% held", never a
    fabricated 100%% / 0%% from an empty set.
    """
    claims: dict[str, dict] = {}
    # latest verdict per claim_id: (ts, outcome)
    latest_verdict: dict[str, tuple] = {}
    for ev in events:
        kind = ev.get("kind")
        if kind == "claim":
            cid = ev.get("id")
            if isinstance(cid, str):
                claims[cid] = ev  # a later claim record with same id wins
        elif kind == "verdict":
            cid = ev.get("claim_id")
            outcome = ev.get("outcome")
            ts = ev.get("ts")
            if (isinstance(cid, str) and outcome in _DEFINITIVE
                    and isinstance(ts, (int, float))):
                prev = latest_verdict.get(cid)
                if prev is None or ts >= prev[0]:
                    latest_verdict[cid] = (ts, outcome)

    held, broke, open_ = [], [], []
    for cid, rec in claims.items():
        v = latest_verdict.get(cid)
        if v is None:
            open_.append(rec)
        elif v[1] == "held":
            held.append(rec)
        else:
            broke.append(rec)

    n_def = len(held) + len(broke)
    ratio = (len(held) / n_def) if n_def else None
    return {
        "n_total": len(claims),
        "n_held": len(held),
        "n_broke": len(broke),
        "n_open": len(open_),
        "ratio": ratio,
        "held": held,
        "broke": broke,
        "open": open_,
    }


def rederive_open(events: list[dict], head_full_now: str | None,
                  marker: dict | None, now_ts: float) -> list[dict]:
    """Cheap warm-start re-derivation. Returns NEW verdict events (only the
    DEFINITIVE held/broke ones) for currently-open claims.

    A claim can be cheaply re-derived ONLY when a full honest_status verdict
    covers the SAME head it was claimed on:
      * exit_code 0 on that head → ``held`` (re-confirmed green)
      * exit_code 1 on that head → ``broke`` (proven not-green on the head I
        called green — the exact "you said 100%%" miss made visible)
    Any other state (head moved on, no marker, --quick/exit 2 = couldn't verify)
    yields NO event — the claim stays open and is surfaced as unverified. We
    never manufacture a verdict from absence (honest_failure_modes #2)."""
    state = fold(events)
    if not isinstance(marker, dict):
        return []
    if not marker.get("ran_full_suite"):
        return []
    m_head = marker.get("head_full")
    m_exit = marker.get("exit_code")
    if not m_head or m_head != head_full_now:
        return []
    if m_exit == 0:
        outcome, detail = "held", f"honest_status green on {str(m_head)[:7]}"
    elif m_exit == 1:
        outcome, detail = "broke", (
            f"honest_status FAILED (exit 1) on {str(m_head)[:7]} — a head "
            "previously claimed verified")
    else:
        return []  # exit 2 / unknown — could not verify; leave open

    new: list[dict] = []
    for rec in state["open"]:
        if rec.get("head_full") == m_head:
            new.append({"kind": "verdict", "claim_id": rec.get("id"),
                        "ts": now_ts, "outcome": outcome, "detail": detail})
    return new


def rederive_and_persist(path: str | None, head_full_now: str | None,
                         marker: dict | None, now_ts: float,
                         max_bytes: int = DEFAULT_LEDGER_MAX_BYTES) -> dict:
    """Load, re-derive open claims against the marker, persist any definitive
    verdicts, and return the folded state AFTER the re-derivation. The
    warm-start emitter calls this; an append failure is logged, not raised."""
    path = path or ledger_path()
    events = load_events(path)
    new = rederive_open(events, head_full_now, marker, now_ts)
    if new:
        err = append_jsonl(path, new, max_bytes)
        if err:
            log_warning(f"calibration_ledger: could not persist "
                        f"{len(new)} re-derived verdict(s): {err}")
        events = events + new
    return fold(events)


def format_brief_block(state: dict, max_show: int = 3) -> str:
    """Render the folded state as a warm-brief section. ``""`` when nothing is
    tracked (don't inject noise). Surfaces BROKE claims loudly — those are the
    "you said 100%% and the math was wrong" cases the operator must see first.

    Honest about the denominator: a percentage is shown only when claims have
    actually been re-checked; with none re-checked it says exactly that, never a
    fabricated 100%%."""
    n = state.get("n_total", 0)
    if not n:
        return ""
    held, broke, open_ = state["n_held"], state["n_broke"], state["n_open"]
    n_def = held + broke
    lines = ["## calibration ledger — my own track record"]
    if n_def:
        pct = round(100 * state["ratio"])
        icon = "🟢" if broke == 0 else "⚠️"
        lines.append(
            f"{icon} {n} VERIFIED claim(s) logged · re-checked: {held} held / "
            f"{broke} broke ({pct}% held) · {open_} still unverified")
    else:
        lines.append(
            f"🔵 {n} VERIFIED claim(s) logged · none re-checked yet · "
            f"{open_} still unverified")
    for rec in state.get("broke", [])[:max_show]:
        ct = (rec.get("claim_text") or "")[:80]
        head = str(rec.get("head_full") or "")[:7]
        lines.append(
            f"- ⚠️ BROKE on {head}: \"{ct}\" — was claimed VERIFIED; "
            "re-derivation says not-green. Treat my completion claims with "
            "this in mind this session.")
    return "\n".join(lines) + "\n"
