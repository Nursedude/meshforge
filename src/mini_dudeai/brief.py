"""Warm-start brief — the artifact a cloud/LLM session reads FIRST.

mini-dudeai is the local 24/7 presence; this turns its state + history into a
short "here's what happened while you were away, and what to look at first"
note. The point (operator's framing): *you don't start cold, because a piece
of you is already here.*

`build_brief` is pure (state dict + history list + now → markdown string) so it
is trivially testable. `write_brief` is the I/O wrapper that reads the standard
mini files and atomic-writes the brief. Neither touches the running engine —
generation is decoupled (a cron, the digest daemon, or `--brief`), so it never
affects the daemon's fire behavior.
"""
from __future__ import annotations

import datetime
import json
import os
import re

from ._util import atomic_write_text, read_json

DEFAULT_STALE_S = 300.0  # 30s tick → >5m means the daemon likely stopped
ESCALATION_WINDOW_S = 86400.0  # only surface escalations fired in the last 24h


def _escalation_of(h: dict) -> dict | None:
    """Extract the escalation payload from a history row, schema-tolerant.

    The current engine writes it under ``outcome.extras.escalation``; an older
    schema wrote it at ``outcome.escalation``. Read both so neither shape is
    silently dropped from the warm-start brief.
    """
    out = h.get("outcome") or {}
    return (out.get("extras") or {}).get("escalation") or out.get("escalation")


def recent_escalations(history: list[dict], now_ts: float,
                       window_s: float = ESCALATION_WINDOW_S,
                       with_ts: bool = False) -> list:
    """Escalation payloads fired within `window_s`, deduped, oldest→newest.

    Single source of truth for "what should the warm session chase" — used by
    both the brief and the situation digest so the two never disagree. Drops
    escalations older than the window (resolved noise replayed from the history
    tail), dedups by (rule, subject, detail) keeping the most recent fire, and
    reads both the current (outcome.extras.escalation) and legacy
    (outcome.escalation) schemas.

    with_ts=True returns ``[(ts, esc), ...]`` instead of ``[esc, ...]`` so callers
    (the fleet deep-merge) can sort escalations across boxes by fire time. Default
    False keeps the existing brief/digest callers unchanged.
    """
    cutoff = now_ts - window_s
    fresh: dict = {}
    for h in history:
        esc = _escalation_of(h)
        if not esc:
            continue
        ts = h.get("ts")
        if ts is not None and float(ts) < cutoff:
            continue
        key = (esc.get("rule"), esc.get("subject"), str(esc.get("detail", "")))
        prev = fresh.get(key)
        if prev is None or float(ts or 0) >= float(prev[0] or 0):
            fresh[key] = (ts, esc)
    ordered = sorted(fresh.values(), key=lambda x: float(x[0] or 0))
    if with_ts:
        return ordered
    return [esc for _ts, esc in ordered]


def _split_escalations_by_activity(escalations: list, rules: dict) -> tuple:
    """Partition escalations into (active, resolved) using rule state.

    A window of recent escalations can include conditions that have since
    cleared (edge_down) — e.g. the 2026-06-03 parity_drift port window, which
    self-healed in 10 minutes but sat under "Look here first" for a day and
    read as a live 22-tick escalation. The state's per-rule
    ``currently_active`` flag is the truth; gate the headline section on it.

    Conservative by design: an escalation is demoted to "resolved" ONLY when
    the state positively shows its (rule, subject) with currently_active
    false. Unknown pairs (rule renamed/pruned, schema drift, missing state)
    stay in "active" — we never hide a live escalation on a state mismatch.
    """
    status = {}
    for rs in rules.values():
        if isinstance(rs, dict):
            status[(rs.get("rule_id"), rs.get("subject"))] = bool(
                rs.get("currently_active"))
    active, resolved = [], []
    for esc in escalations:
        if status.get((esc.get("rule"), esc.get("subject")), True):
            active.append(esc)
        else:
            resolved.append(esc)
    return active, resolved


def _age(now_ts: float, ts: float | None) -> str:
    if not ts:
        return "?"
    s = max(0, int(now_ts - float(ts)))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def build_brief(state: dict, history: list[dict], now_ts: float,
                stale_s: float = DEFAULT_STALE_S, pending_deltas: int = 0,
                escalation_window_s: float = ESCALATION_WINDOW_S,
                cadence_triage: dict | None = None,
                delta_track_record: dict | None = None,
                rejection_reasons: dict | None = None) -> str:
    """Render the warm-start brief markdown from mini's state + recent history.

    `pending_deltas` is the count of unratified B3 memory-deltas; when >0 the
    brief points the warm session at them (the dream log is where to review).

    `cadence_triage` is the local-tier fallback witness
    (cadence_fallback.CADENCE_TRIAGE_BASENAME): when fresh it earns its own
    section so a returning frontier session cannot miss that the cadence ran
    degraded — tier provenance in the reader's face, never buried (#80).
    """
    state = state if isinstance(state, dict) else {}
    rules = state.get("rules") or {}
    last_tick = state.get("last_tick_ts")
    age = _age(now_ts, last_tick)
    stale = bool(last_tick and (now_ts - float(last_tick)) > stale_s)
    host = state.get("host", "?")

    stamp = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    # No "last tick Ns ago" here: that claim is only true at WRITE time, and
    # the SD-wear guard skips rewrites when content is unchanged — a frozen
    # "0s ago" would be a valid-looking stale claim (the exact #80 defect
    # class). Freshness is re-derived from state.last_tick_ts by the
    # consumers (warmstart banner, rollup pane); the stamp honestly means
    # "content as of".
    lines = [
        f"# mini-dudeai warm brief — {host}",
        f"_generated {stamp} · read this FIRST, then "
        f"mini_dudeai_history.jsonl → state → memory_",
        "",
    ]

    # Posture
    if not last_tick:
        lines.append("⚠️ **mini-dudeai has no state yet** (never ticked here, or state file missing).")
    elif stale:
        lines.append(f"🔴 **STALE** — last tick {age} ago (> {int(stale_s)}s). The watcher itself "
                     f"may be down: `systemctl --user status meshforge-mini-dudeai`.")
    else:
        err_names = "; ".join(state.get("source_errors") or [])
        lines.append(f"🟢 alive — {state.get('rule_count', len(rules))} rules, "
                     f"src_errors={state.get('error_count', 0)} this tick."
                     + (f" ({err_names})" if err_names else ""))

    # Still active now
    active = [rs for rs in rules.values() if isinstance(rs, dict) and rs.get("currently_active")]
    if active:
        lines.append("\n## Still active now")
        for rs in active:
            lines.append(f"- **{rs.get('rule_id')}** · {rs.get('subject')} · "
                         f"{str(rs.get('last_detail', ''))[:120]}")

    # Undelivered sends still retrying — a fire whose action (page/annotate)
    # failed and is being re-attempted each tick. Surfaced so an operator or
    # warm-start session knows a page exists that nobody has received yet.
    pending_sends = [
        (rs, ent)
        for rs in rules.values() if isinstance(rs, dict)
        for ent in (rs.get("pending_sends") or []) if isinstance(ent, dict)
    ]
    if pending_sends:
        lines.append(f"\n## 📨 {len(pending_sends)} undelivered send(s) — retrying each tick")
        for rs, ent in pending_sends[:6]:
            lines.append(f"- **{rs.get('rule_id')}** · {rs.get('subject')} · "
                         f"{ent.get('transition')} attempt {ent.get('attempts')} · "
                         f"last error: {str(ent.get('error', ''))[:80]}")

    # B3 — memory-deltas the nightly dream pass proposed and that no session has
    # ratified yet. The dream log (mini_dudeai_dreams.md) holds the reasoning.
    if pending_deltas:
        lines.append(f"\n## 💭 {pending_deltas} memory-delta(s) await ratification")
        lines.append("- See `mini_dudeai_dreams.md` for the synthesis + evidence; "
                     "ratify/reject via `dreams.resolve_delta()`.")

    # B3-mirror — the proposer's own precision, from resolved history. The
    # same honesty the calibration ledger points at the session, pointed at
    # the local tier: a proposer that never sees its hit-rate stays
    # convincingly wrong at the same rate forever. Rendered only once
    # resolutions exist (no data = no line, never a fabricated 0%).
    tr = delta_track_record or {}
    resolved = tr.get("ratified", 0) + tr.get("rejected", 0)
    if resolved:
        lines.append(f"\n## 🪞 dream-proposal track record — "
                     f"{tr.get('ratified', 0)}/{resolved} ratified")
        if tr.get("ratified", 0) == 0:
            lines.append("- Every reviewed proposal was judged not "
                         "memory-worthy. Be a skeptic of my next one — the "
                         "evidence bar for proposing should probably rise.")
        else:
            lines.append("- Ratification ratio over all reviewed proposals; "
                         "rejection notes in the deltas file say why.")
        # WHY proposals get rejected — turns "the loop is ignored" into a
        # retune target (e.g. one noisy detector dominating). Rendered only
        # when rejections carry recorded reasons; absence stays absence.
        rr = rejection_reasons or {}
        if rr:
            top = sorted(rr.items(), key=lambda kv: (-kv[1], kv[0]))
            breakdown = ", ".join(f"{reason} ×{n}" for reason, n in top)
            lines.append(f"- rejected by reason: {breakdown}")

    # W1 — the cadence ran DEGRADED while the frontier was away. Freshness is
    # re-derived here from the witness ts (a stale witness must not keep
    # claiming a recent local run), and the wording states the invariant:
    # suggestions only, nothing was ratified.
    if isinstance(cadence_triage, dict):
        from .cadence_fallback import TRIAGE_FRESH_S
        t_ts = cadence_triage.get("ts")
        if isinstance(t_ts, (int, float)) and 0 <= now_ts - t_ts < TRIAGE_FRESH_S:
            tier = cadence_triage.get("brain_tier")
            frc = cadence_triage.get("frontier_rc")
            frc_txt = "claude CLI missing" if frc is None else f"frontier rc={frc}"
            if tier == "local":
                lines.append(
                    f"\n## 🥈 cadence ran on LOCAL tier {_age(now_ts, t_ts)} ago "
                    f"({frc_txt})")
                lines.append(
                    f"- {cadence_triage.get('triaged', 0)}/"
                    f"{cadence_triage.get('proposed_total', '?')} delta(s) "
                    f"triaged by {cadence_triage.get('model', '?')} — "
                    f"SUGGESTIONS ONLY, nothing ratified; dispositions in "
                    f"`mini_dudeai_cadence_triage.json`. "
                    f"{str(cadence_triage.get('summary', ''))[:160]}")
            elif tier == "rules":
                lines.append(
                    f"\n## 🥉 cadence fell to RULES tier {_age(now_ts, t_ts)} ago "
                    f"({frc_txt}; local LLM also unavailable)")
                lines.append(
                    f"- {cadence_triage.get('proposed_total', '?')} delta(s) "
                    f"pending, UNTRIAGED — "
                    f"{str(cadence_triage.get('summary', ''))[:160]}")

    # Look here first — escalations fired within the window, deduped (see
    # recent_escalations; shared with the situation digest so they never
    # disagree). Stale entries replayed from the history tail would mislead a
    # warm-start session into chasing resolved noise — and so would a
    # condition that escalated then CLEARED (edge_down) inside the window, so
    # the headline section is gated on the rule's currently_active state;
    # cleared ones move to "Recently resolved" below.
    escalations = recent_escalations(history, now_ts, escalation_window_s)
    esc_active, esc_resolved = _split_escalations_by_activity(escalations, rules)
    if esc_active:
        lines.append("\n## 🔎 Look here first (escalations)")
        for esc in esc_active[-8:]:
            lines.append(f"- {esc.get('rule')} · {esc.get('subject')} · "
                         f"{str(esc.get('detail', ''))[:120]}"
                         + (f" — _{esc['note']}_" if esc.get("note") else ""))
    if esc_resolved:
        lines.append("\n## ✅ Recently resolved (escalated in window, no longer active)")
        for esc in esc_resolved[-4:]:
            lines.append(f"- {esc.get('rule')} · {esc.get('subject')} · "
                         f"{str(esc.get('detail', ''))[:100]}")

    # Recent transitions
    today = datetime.datetime.fromtimestamp(now_ts).date().isoformat()
    ups = [h for h in history if h.get("transition") == "edge_up"]
    today_ups = [h for h in ups if str(h.get("iso", "")).startswith(today)]
    if ups:
        lines.append("\n## Recent fires")
        lines.append(f"- {len(today_ups)} edge_up today; {len(ups)} in the last window.")
        for h in ups[-8:]:
            lines.append(f"  - [{str(h.get('iso',''))[:19]}] {h.get('rule_id')} · "
                         f"{h.get('subject')} · {str(h.get('detail',''))[:90]}")

    if (not active and not escalations and not ups and not pending_deltas
            and last_tick and not stale):
        lines.append("\n_Quiet: no active conditions, no escalations, no fires in window. "
                     "Nothing demands attention._")

    return "\n".join(lines) + "\n"


def write_brief(state_path: str, history_path: str, out_path: str,
                history_tail: int = 60, now_ts: float | None = None,
                deltas_path: str | None = None,
                state: dict | None = None) -> str:
    """Read mini's state + history-tail, build the brief, atomic-write it. Returns text.

    If `deltas_path` is given (or the standard sibling file exists), surfaces the
    count of unratified B3 memory-deltas in the brief.

    `state` lets the engine pass the tick's in-memory dict so the per-tick
    path doesn't re-read and re-parse the state file it wrote microseconds
    earlier; the decoupled callers (--brief CLI, cron) omit it and read disk.

    SD-wear guard: the rendered text is compared against the existing brief
    with the volatile `_generated …` stamp line excluded — when nothing else
    changed (the common quiet-box tick), the write is skipped entirely
    instead of burning a tmp+fsync+rename cycle on flash every 30s forever.
    The file's mtime then honestly reflects the last CONTENT change; the
    freshness story lives in the state file's last_tick_ts, which warmstart
    already re-derives from.
    """
    import time
    now_ts = time.time() if now_ts is None else now_ts
    if state is None:
        state, _ = read_json(state_path)
    history = _read_history_tail(history_path, history_tail)
    if deltas_path is None:
        from .dreams import DELTAS_BASENAME
        deltas_path = os.path.join(
            os.path.dirname(state_path) or ".", DELTAS_BASENAME)
    pending = 0
    track_record = None
    reasons = None
    if os.path.exists(deltas_path):
        from .dreams import (count_pending_deltas, proposal_track_record,
                             rejection_reason_histogram)
        pending = count_pending_deltas(deltas_path)
        track_record = proposal_track_record(deltas_path)
        reasons = rejection_reason_histogram(deltas_path)
    # Local-tier cadence witness lives beside the deltas file; unreadable or
    # absent → None (the section simply doesn't render — absence is absence).
    from .cadence_fallback import CADENCE_TRIAGE_BASENAME
    triage, _terr = read_json(os.path.join(
        os.path.dirname(deltas_path) or ".", CADENCE_TRIAGE_BASENAME))
    text = build_brief(state or {}, history, now_ts, pending_deltas=pending,
                       cadence_triage=triage if isinstance(triage, dict) else None,
                       delta_track_record=track_record,
                       rejection_reasons=reasons)
    if not _brief_unchanged(out_path, text):
        atomic_write_text(out_path, text)
    return text


_STAMP_RE = re.compile(r"^(_generated )\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _strip_volatile(text: str) -> str:
    """Mask the timestamp inside the `_generated <stamp> …` line — the one
    token guaranteed to differ every tick even when the brief's substance is
    identical. Only the STAMP is masked, not the whole line: a change to the
    line's wording (a format bump) must still register as changed content,
    or quiet boxes would keep serving the old wording forever."""
    return "\n".join(
        _STAMP_RE.sub(r"\1<stamp>", l) if l.startswith("_generated ") else l
        for l in text.splitlines())


def _brief_unchanged(out_path: str, new_text: str) -> bool:
    """True when the existing brief matches new_text modulo the volatile
    stamp. Never raises — any read problem means 'changed, write it'."""
    try:
        with open(out_path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    except OSError:
        return False
    return _strip_volatile(existing) == _strip_volatile(new_text)


# Tail-read window: 60 entries × ~300-500B each ≈ 30KB; 128KB gives ample
# slack. A box sitting near history's 1MB rotation cap used to re-read and
# line-split the whole file every 30s tick to extract these lines.
_TAIL_WINDOW_BYTES = 131072


def _read_history_tail(path: str, last: int) -> list[dict]:
    """Last `last` parsed JSONL objects, read block-wise from the file's end.
    Skips malformed lines, never raises."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            start = max(0, size - _TAIL_WINDOW_BYTES)
            f.seek(start)
            data = f.read()
        lines = [l for l in data.decode("utf-8", "replace").splitlines()
                 if l.strip()]
        if start > 0:
            # The window almost certainly cut the first line mid-record.
            lines = lines[1:]
            if len(lines) < last:
                # Pathological line lengths — fall back to the full read.
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = [l for l in f if l.strip()]
        lines = lines[-last:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out
