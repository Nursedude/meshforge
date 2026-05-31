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

from ._util import atomic_write_json, read_json

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
                escalation_window_s: float = ESCALATION_WINDOW_S) -> str:
    """Render the warm-start brief markdown from mini's state + recent history.

    `pending_deltas` is the count of unratified B3 memory-deltas; when >0 the
    brief points the warm session at them (the dream log is where to review).
    """
    state = state if isinstance(state, dict) else {}
    rules = state.get("rules") or {}
    last_tick = state.get("last_tick_ts")
    age = _age(now_ts, last_tick)
    stale = bool(last_tick and (now_ts - float(last_tick)) > stale_s)
    host = state.get("host", "?")

    stamp = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# mini-dudeai warm brief — {host}",
        f"_generated {stamp} · last tick {age} ago · read this FIRST, then "
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
        lines.append(f"🟢 alive — {state.get('rule_count', len(rules))} rules, "
                     f"src_errors={state.get('error_count', 0)} this tick.")

    # Still active now
    active = [rs for rs in rules.values() if isinstance(rs, dict) and rs.get("currently_active")]
    if active:
        lines.append("\n## Still active now")
        for rs in active:
            lines.append(f"- **{rs.get('rule_id')}** · {rs.get('subject')} · "
                         f"{str(rs.get('last_detail', ''))[:120]}")

    # B3 — memory-deltas the nightly dream pass proposed and that no session has
    # ratified yet. The dream log (mini_dudeai_dreams.md) holds the reasoning.
    if pending_deltas:
        lines.append(f"\n## 💭 {pending_deltas} memory-delta(s) await ratification")
        lines.append("- See `mini_dudeai_dreams.md` for the synthesis + evidence; "
                     "ratify/reject via `dreams.resolve_delta()`.")

    # Look here first — escalations fired within the window, deduped (see
    # recent_escalations; shared with the situation digest so they never
    # disagree). Stale entries replayed from the history tail would mislead a
    # warm-start session into chasing resolved noise.
    escalations = recent_escalations(history, now_ts, escalation_window_s)
    if escalations:
        lines.append("\n## 🔎 Look here first (escalations)")
        for esc in escalations[-8:]:
            lines.append(f"- {esc.get('rule')} · {esc.get('subject')} · "
                         f"{str(esc.get('detail', ''))[:120]}"
                         + (f" — _{esc['note']}_" if esc.get("note") else ""))

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
                deltas_path: str | None = None) -> str:
    """Read mini's state + history-tail, build the brief, atomic-write it. Returns text.

    If `deltas_path` is given (or the standard sibling file exists), surfaces the
    count of unratified B3 memory-deltas in the brief.
    """
    import time
    now_ts = time.time() if now_ts is None else now_ts
    state, _ = read_json(state_path)
    history = _read_history_tail(history_path, history_tail)
    if deltas_path is None:
        deltas_path = os.path.join(
            os.path.dirname(state_path) or ".", "mini_dudeai_memory_deltas.jsonl")
    pending = 0
    if os.path.exists(deltas_path):
        from .dreams import count_pending_deltas
        pending = count_pending_deltas(deltas_path)
    text = build_brief(state or {}, history, now_ts, pending_deltas=pending)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, out_path)
    return text


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
