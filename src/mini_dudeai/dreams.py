"""B3 — the "dreams" / synthesis + memory-delta loop.

A low-frequency (nightly) pass that distills mini-dudeai's own state + history
into (a) a narrative "dream log" and (b) candidate *memory-deltas* a cloud
session ratifies. It is the continuity mechanism: the cloud session warms off
the brief (B1), but the dream log is where the local fragment says "here's the
pattern I noticed while you were gone, and here's what I think you should
remember."

Design invariants (per the two-missions plan, non-negotiable):
  * NO LLM in the path — "dreams" here means *deterministic* pattern extraction
    over the history window, not generative text. Stdlib only.
  * Observation-only — no subprocess, no systemctl, no writes to canonical
    memory. Mini PROPOSES memory-deltas; a cloud session RATIFIES them. This
    mirrors the rule candidate-promotion trust model exactly.
  * Decoupled from the hot loop — `build_dreams` is PURE (state + history + now
    -> narrative + deltas), invoked off-loop via `--dream` (a cron/timer), so it
    can never affect the daemon's fire behavior. Soak-safe.

Scalability: detectors are pure functions `(state, history, now_ts) -> [delta]`
registered in `_DETECTORS`. Adding a new pattern is one function + one list
entry — the same registry shape as sources/actions. Each box runs its own dream
pass over its own history, so this federates the way state/history already do.
"""
from __future__ import annotations

import datetime
import json
import os
import time

from ._util import atomic_write_text, read_json, resolve_home
from .history import append_jsonl

# --- tunables (conservative defaults; override per call for tests/tuning) -----

# Cap for the append-only memory-deltas JSONL. Generous (retains many weeks of
# proposals/resolutions) but bounded — the nightly pass is the only writer, so
# growth is slow, yet an unattended box must never let it grow without limit.
DEFAULT_DELTAS_MAX_BYTES = 1_000_000  # 1 MB

DEFAULT_LOOKBACK_S = 86400.0          # 24h synthesis window
FLAP_MIN_FIRES = 4                    # >= this many fires/24h to consider flapping
FLAP_MAX_MEAN_ACTIVE_S = 120.0        # ...and mean active duration under this = flap
PERSISTENT_MIN_ACTIVE_S = 3600.0      # currently_active >= 1h = a persistent condition
# Once a delta is resolved (ratified/rejected), don't re-propose the same key for
# this long — otherwise the nightly pass resurrects already-handled findings every
# run until the 24h state window ages out the signal. After the window, if the
# condition still detects, it re-surfaces (genuinely persistent → worth another look).
DEFAULT_RESOLVE_SUPPRESS_S = 604800.0  # 7 days
# One name for the deltas file across every consumer (engine tick counter,
# daemon, brief, cadence_fallback, audit) — independent hardcodes WILL drift.
DELTAS_BASENAME = "mini_dudeai_memory_deltas.jsonl"


# --- small helpers ------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat()


def _hms(seconds: float) -> str:
    """Compact human duration: 31s, 6m, 3h."""
    s = max(0, int(seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def _delta(kind: str, key: str, summary: str, evidence: dict,
           proposed_action: str, confidence: str, now_ts: float,
           host: str) -> dict:
    """Build one candidate memory-delta. `key` is the dedup identity."""
    return {
        "ts": now_ts,
        "iso": _iso(now_ts),
        "host": host,
        "kind": kind,
        "key": key,
        "summary": summary,
        "evidence": evidence,
        "proposed_action": proposed_action,
        "confidence": confidence,
        "status": "proposed",
    }


def _active_durations(history: list[dict]) -> dict[tuple[str, str], list[float]]:
    """Pair each edge_up with the next edge_down for the same (rule_id, subject),
    returning observed active-period durations (seconds). Unpaired edge_ups
    (still active at window end) are ignored — persistent-active handles those.
    """
    open_up: dict[tuple[str, str], float] = {}
    durations: dict[tuple[str, str], list[float]] = {}
    for h in history:
        k = (h.get("rule_id"), h.get("subject"))
        ts = h.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        t = h.get("transition")
        if t == "edge_up":
            open_up[k] = ts
        elif t == "edge_down" and k in open_up:
            durations.setdefault(k, []).append(max(0.0, ts - open_up.pop(k)))
    return durations


# --- detectors: pure (state, history, now_ts) -> [delta] ----------------------

def detect_chronic_flap(state: dict, history: list[dict], now_ts: float,
                        host: str, *, min_fires: int = FLAP_MIN_FIRES,
                        max_mean_active_s: float = FLAP_MAX_MEAN_ACTIVE_S) -> list[dict]:
    """A rule that fires often but each occurrence clears fast = a flapping
    signal. Exactly the `repeat_signal_counter_watchdog_handoff` pain class:
    chronic-transient that should become a cooldown bump or a known-normal.
    """
    out: list[dict] = []
    durs = _active_durations(history)
    for rs in (state.get("rules") or {}).values():
        if not isinstance(rs, dict):
            continue
        fires = int(rs.get("fire_count_24h", 0) or 0)
        if fires < min_fires:
            continue
        rid, subj = rs.get("rule_id"), rs.get("subject")
        observed = durs.get((rid, subj)) or []
        if not observed:
            continue  # no paired clears in window — can't call it a flap
        mean_active = sum(observed) / len(observed)
        if mean_active >= max_mean_active_s:
            continue  # fires a lot but stays up a while — not a flap, a real condition
        out.append(_delta(
            kind="chronic_flap",
            key=f"chronic_flap::{rid}::{subj}",
            summary=(f"{rid} flapped {fires}× on {subj} in 24h; "
                     f"mean active {_hms(mean_active)} — chronic transient"),
            evidence={
                "rule_id": rid, "subject": subj, "fire_count_24h": fires,
                "mean_active_s": round(mean_active, 1),
                "samples_active_s": [round(d, 1) for d in observed[-8:]],
            },
            proposed_action=("Bump this rule's cooldown_s or add a known-normal "
                             "suppressor — it self-clears every time, so the pages "
                             "are noise, not signal."),
            confidence="high" if fires >= min_fires * 2 else "medium",
            now_ts=now_ts, host=host,
        ))
    return out


def detect_new_subject(state: dict, history: list[dict], now_ts: float,
                       host: str, *, lookback_s: float = DEFAULT_LOOKBACK_S) -> list[dict]:
    """A subject whose entire fire history is inside the window = first seen
    recently. Could be a new fleet member (benign) or an anomaly (worth a look).
    Derived from state alone: fire_count == fire_count_24h means every fire ever
    recorded for this subject happened in the last 24h.
    """
    out: list[dict] = []
    cutoff = now_ts - lookback_s
    for rs in (state.get("rules") or {}).values():
        if not isinstance(rs, dict):
            continue
        total = int(rs.get("fire_count", 0) or 0)
        recent = int(rs.get("fire_count_24h", 0) or 0)
        if total < 1 or total != recent:
            continue  # has prior history before the window — not new
        window = rs.get("fires_window") or []
        first = min(window) if window else None
        if first is None or first < cutoff:
            continue
        rid, subj = rs.get("rule_id"), rs.get("subject")
        out.append(_delta(
            kind="new_subject",
            key=f"new_subject::{rid}::{subj}",
            summary=f"first-ever sighting of {subj} (rule {rid}) — appeared {_hms(now_ts - first)} ago",
            evidence={
                "rule_id": rid, "subject": subj, "fire_count": total,
                "first_seen_iso": _iso(first),
            },
            proposed_action=("Confirm this subject is an expected fleet member. "
                             "If expected, no memory needed; if unexpected, this is "
                             "a topology change worth recording."),
            confidence="medium",
            now_ts=now_ts, host=host,
        ))
    return out


def detect_persistent_active(state: dict, history: list[dict], now_ts: float,
                             host: str, *, min_active_s: float = PERSISTENT_MIN_ACTIVE_S) -> list[dict]:
    """A rule that has been currently_active for hours = a sustained condition
    (long outage, permanent backoff). If expected (cf moc3 gateway-only), it's a
    known-normal candidate; if not, it's a real problem the cloud should chase.
    """
    out: list[dict] = []
    for rs in (state.get("rules") or {}).values():
        if not isinstance(rs, dict) or not rs.get("currently_active"):
            continue
        since = float(rs.get("last_fired_ts", 0) or 0)
        if not since:
            continue
        active_for = now_ts - since
        if active_for < min_active_s:
            continue
        rid, subj = rs.get("rule_id"), rs.get("subject")
        out.append(_delta(
            kind="persistent_active",
            key=f"persistent_active::{rid}::{subj}",
            summary=(f"{rid} active on {subj} for {_hms(active_for)} straight "
                     f"— sustained condition"),
            evidence={
                "rule_id": rid, "subject": subj,
                "active_since_iso": _iso(since),
                "active_for_s": round(active_for, 0),
                "last_detail": str(rs.get("last_detail", ""))[:200],
            },
            proposed_action=("If this is expected (e.g. a gateway-only box's "
                             "permanent backoff), add a known-normal suppressor. "
                             "If not, it's a long outage — investigate the subject."),
            confidence="medium",
            now_ts=now_ts, host=host,
        ))
    return out


def detect_escalation_rollup(state: dict, history: list[dict], now_ts: float,
                             host: str, *, lookback_s: float = DEFAULT_LOOKBACK_S) -> list[dict]:
    """Escalations proposed in the window (via propose_escalation). These are
    already flagged cloud-worthy; the dream log rolls them up per (rule, subject)
    so the warm session sees "here is what I explicitly flagged for you."
    """
    cutoff = now_ts - lookback_s
    grouped: dict[tuple[str, str], dict] = {}
    for h in history:
        ts = h.get("ts")
        if not isinstance(ts, (int, float)) or ts < cutoff:
            continue
        esc = ((h.get("outcome") or {}).get("extras") or {}).get("escalation")
        if not esc:
            continue
        gk = (esc.get("rule"), esc.get("subject"))
        g = grouped.setdefault(gk, {"count": 0, "last_detail": "", "last_iso": "",
                                    "note": esc.get("note", "")})
        g["count"] += 1
        g["last_detail"] = esc.get("detail", "")
        g["last_iso"] = h.get("iso", "")
    out: list[dict] = []
    for (rule, subj), g in grouped.items():
        out.append(_delta(
            kind="escalation",
            key=f"escalation::{rule}::{subj}",
            summary=(f"{rule} escalated {g['count']}× on {subj} in window"
                     + (f" — {g['note']}" if g["note"] else "")),
            evidence={
                "rule": rule, "subject": subj, "count": g["count"],
                "last_iso": g["last_iso"], "last_detail": str(g["last_detail"])[:200],
            },
            proposed_action=("This was explicitly escalated for cloud review. "
                             "Triage the subject; record the resolution if durable."),
            confidence="high",
            now_ts=now_ts, host=host,
        ))
    return out


# Registry: add a detector here and it joins the nightly pass. Same extensibility
# contract as the source/action registries.
_DETECTORS = (
    detect_chronic_flap,
    detect_new_subject,
    detect_persistent_active,
    detect_escalation_rollup,
)


# --- pure builder -------------------------------------------------------------

def build_dreams(state: dict, history: list[dict], now_ts: float,
                 host: str | None = None) -> tuple[str, list[dict]]:
    """Distill state + history into (narrative_md, [memory_delta]). PURE.

    The narrative is first-person but diagnostic — the local fragment's lab
    notebook, not whimsy. The deltas are *proposals*; dedup + persistence happen
    in write_dreams (this function is side-effect free and returns everything it
    found, pre-dedup, so callers/tests see the raw detection).
    """
    state = state if isinstance(state, dict) else {}
    host = host or state.get("host") or os.uname().nodename

    deltas: list[dict] = []
    for detector in _DETECTORS:
        try:
            deltas.extend(detector(state, history, now_ts, host))
        except Exception as e:  # a broken detector must not abort the whole dream
            deltas.append(_delta(
                kind="detector_error",
                key=f"detector_error::{detector.__name__}",
                summary=f"detector {detector.__name__} raised {type(e).__name__}: {e}",
                evidence={"detector": detector.__name__, "error": str(e)},
                proposed_action="A dream detector itself is broken — fix the code.",
                confidence="high", now_ts=now_ts, host=host,
            ))

    narrative = _render_narrative(state, history, deltas, now_ts, host)
    return narrative, deltas


def _render_narrative(state: dict, history: list[dict], deltas: list[dict],
                      now_ts: float, host: str) -> str:
    """First-person, scientific/diagnostic dream log. Evidence-led."""
    stamp = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    n_events = len(history)
    ups = sum(1 for h in history if h.get("transition") == "edge_up")
    downs = sum(1 for h in history if h.get("transition") == "edge_down")
    n_rules = state.get("rule_count", len(state.get("rules") or {}))

    lines = [
        f"# mini-dudeai dream log — {host}",
        f"_synthesized {stamp} · window: last 24h · {n_events} history events · "
        f"{n_rules} rules · NO LLM, deterministic distillation_",
        "",
        f"I reviewed the last 24h of my own telemetry: {ups} edge-up and {downs} "
        f"edge-down transitions across {n_rules} rules. My read of the patterns "
        f"that warrant a memory, below. Nothing here is applied automatically — "
        f"these are proposals for the cloud session to ratify.",
    ]

    by_kind: dict[str, list[dict]] = {}
    for d in deltas:
        by_kind.setdefault(d["kind"], []).append(d)

    if not deltas:
        lines.append("\n_No durable pattern crossed a detector threshold this "
                     "window. The fleet behaved within known parameters; I have "
                     "nothing to propose._")
        return "\n".join(lines) + "\n"

    titles = {
        "chronic_flap": "Chronic-transient signals (flapping)",
        "new_subject": "First-time subjects",
        "persistent_active": "Sustained conditions",
        "escalation": "Escalations I flagged for you",
        "detector_error": "⚠️ Detector faults",
    }
    for kind in ("detector_error", "escalation", "chronic_flap",
                 "persistent_active", "new_subject"):
        group = by_kind.get(kind)
        if not group:
            continue
        lines.append(f"\n## {titles.get(kind, kind)}")
        for d in group:
            lines.append(f"- **{d['summary']}**")
            lines.append(f"  - _proposed:_ {d['proposed_action']} "
                         f"(confidence: {d['confidence']})")

    lines.append(f"\n## Proposed memory-deltas ({len(deltas)}, awaiting ratification)")
    lines.append("| kind | confidence | summary |")
    lines.append("|---|---|---|")
    for d in deltas:
        lines.append(f"| {d['kind']} | {d['confidence']} | {d['summary'][:90]} |")
    lines.append("\n_Mini proposes; the cloud session ratifies. To accept/decline "
                 "a delta, see `resolve_delta()` in dreams.py — nothing is written "
                 "to canonical memory by the daemon._")
    return "\n".join(lines) + "\n"


# --- persistence + dedup + ratification ---------------------------------------

def _load_deltas(path: str) -> list[dict]:
    """Read the append-only deltas JSONL. Tolerant — skips malformed lines."""
    try:
        with open(path) as f:
            raw = [l for l in f if l.strip()]
    except OSError:
        return []
    out: list[dict] = []
    for l in raw:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out


def _unresolved_keys(deltas: list[dict]) -> set[str]:
    """Keys with an outstanding (status == 'proposed') delta — never re-propose."""
    return {d.get("key") for d in deltas
            if d.get("status") == "proposed" and d.get("key")}


def _skip_keys(deltas: list[dict], now_ts: float,
               resolve_suppress_s: float = DEFAULT_RESOLVE_SUPPRESS_S) -> set[str]:
    """Keys a fresh pass must NOT re-propose: still-proposed ones (outstanding),
    plus ones resolved (ratified/rejected) within resolve_suppress_s. Uses the
    most-recent entry per key so a key resolved long ago can re-surface."""
    skip = _unresolved_keys(deltas)
    latest: dict[str, dict] = {}
    for d in deltas:
        k = d.get("key")
        if k:
            latest[k] = d  # last write wins → most-recent entry per key
    cutoff = now_ts - resolve_suppress_s
    for k, d in latest.items():
        if d.get("status") in ("ratified", "rejected") and float(d.get("resolved_ts", 0) or 0) >= cutoff:
            skip.add(k)
    return skip


def write_dreams(state_path: str, history_path: str, deltas_path: str,
                 narrative_path: str, history_tail: int = 500,
                 now_ts: float | None = None) -> dict:
    """Read state + history-tail, build dreams, dedup-append new deltas, write the
    narrative snapshot. Returns a small summary dict. Never raises on I/O — the
    daemon must survive a bad dream.
    """
    now_ts = time.time() if now_ts is None else now_ts
    state, _ = read_json(state_path)
    history = _read_history_tail(history_path, history_tail)
    narrative, detected = build_dreams(state or {}, history, now_ts)

    existing = _load_deltas(deltas_path)
    skip = _skip_keys(existing, now_ts)
    fresh = [d for d in detected if d.get("key") not in skip]

    # Shared append posture (rotate-if-over-cap + torn-tail repair + swallowed
    # OSError) — one implementation for history/audit/dreams, not three
    # divergent copies.
    appended_err = append_jsonl(deltas_path, fresh, DEFAULT_DELTAS_MAX_BYTES)

    narr_err = None
    try:
        atomic_write_text(narrative_path, narrative)
    except OSError as e:
        narr_err = f"{type(e).__name__}: {e}"

    return {
        "detected": len(detected),
        "appended": 0 if appended_err else len(fresh),
        "deduped": len(detected) - len(fresh),
        "narrative_path": narrative_path,
        "deltas_path": deltas_path,
        "append_error": appended_err,
        "narrative_error": narr_err,
    }


def resolve_delta(deltas_path: str, key: str, status: str,
                  note: str = "", now_ts: float | None = None) -> bool:
    """Ratify or reject a proposed delta. The cloud-session side of the trust
    model: mini proposes (status='proposed'); a session marks it 'ratified' or
    'rejected' here. Rewrites the most-recent matching proposed entry in place
    (atomic). Returns True if one was resolved.

    This is the ONLY mutation of the deltas file besides append — and it is
    invoked by the cloud session, never by the daemon. Canonical memory is still
    written through the normal memory tooling; this just closes the proposal.
    """
    if status not in ("ratified", "rejected"):
        raise ValueError("status must be 'ratified' or 'rejected'")
    now_ts = time.time() if now_ts is None else now_ts
    deltas = _load_deltas(deltas_path)
    if not deltas:
        return False
    # newest matching proposed entry
    for d in reversed(deltas):
        if d.get("key") == key and d.get("status") == "proposed":
            d["status"] = status
            d["resolved_ts"] = now_ts
            d["resolved_iso"] = _iso(now_ts)
            if note:
                d["resolved_note"] = note
            break
    else:
        return False
    atomic_write_text(
        deltas_path,
        "".join(json.dumps(d, default=str) + "\n" for d in deltas))
    return True


# mtime-keyed cache: the brief calls count_pending_deltas EVERY tick, but
# the deltas file changes only when the nightly dream pass appends or a
# human ratifies — a full JSONL parse per tick (up to 1MB) to produce an
# integer that changes ~once a day.
_PENDING_COUNT_CACHE: dict = {}


def count_pending_deltas(deltas_path: str) -> int:
    """How many memory-deltas await ratification. Used by the warm-start brief."""
    try:
        mtime_ns = os.stat(deltas_path).st_mtime_ns
    except OSError:
        return 0
    hit = _PENDING_COUNT_CACHE.get(deltas_path)
    if hit and hit[0] == mtime_ns:
        return hit[1]
    n = len(_unresolved_keys(_load_deltas(deltas_path)))
    _PENDING_COUNT_CACHE[deltas_path] = (mtime_ns, n)
    return n


def _read_history_tail(path: str, last: int) -> list[dict]:
    """Last `last` parsed JSONL objects. Skips malformed lines, never raises."""
    try:
        with open(path) as f:
            lines = [l for l in f if l.strip()][-last:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# CLI — the cloud-session side of the trust model as clean commands, so a
# headless cadence session never needs `python3 -c` (banned by the repo deny
# list). Mirrors the mini_dudeai.memory_apply CLI shape: author a memory there,
# close the proposal here.
# ---------------------------------------------------------------------------


def _default_deltas_path() -> str:
    """Standard deltas file in the mini home (package convention)."""
    return os.path.join(resolve_home(), DELTAS_BASENAME)


def main(argv: list[str] | None = None) -> int:
    """CLI: list proposed delta keys, or resolve one (ratify/reject).

    List:    python3 -m mini_dudeai.dreams --list-proposed [--path P]
    Resolve: python3 -m mini_dudeai.dreams --resolve KEY --status ratified|rejected
                                           [--path P] [--note "..."]

    Exit: 0 on success (listed, or one delta resolved), 1 if the key matched no
    proposed delta, 2 on a usage error.
    """
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="mini-dudeai-dreams",
        description="Resolve mini's proposed memory-deltas (the cloud-session "
                    "side of the propose/ratify trust model). Authoring the "
                    "memory itself is mini_dudeai.memory_apply; this just closes "
                    "the proposal.",
    )
    p.add_argument("--path", default=_default_deltas_path(),
                   help="deltas JSONL path (default: ~/mini_dudeai_memory_deltas.jsonl)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--resolve", metavar="KEY",
                   help="key of the proposed delta to resolve.")
    g.add_argument("--list-proposed", action="store_true",
                   help="print proposed (unresolved) delta keys, one per line.")
    p.add_argument("--status", choices=("ratified", "rejected"),
                   help="resolution (required with --resolve).")
    p.add_argument("--note", default="", help="optional resolution note.")
    args = p.parse_args(argv)

    path = os.path.expanduser(args.path)

    if args.list_proposed:
        for key in sorted(_unresolved_keys(_load_deltas(path))):
            print(key)
        return 0

    if not args.status:
        print("ERROR: --status is required with --resolve", file=sys.stderr)
        return 2
    try:
        ok = resolve_delta(path, args.resolve, args.status, note=args.note)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if ok:
        print(f"resolved: {args.resolve} -> {args.status}")
        return 0
    print(f"not found: no proposed delta with key {args.resolve!r} in {path}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
