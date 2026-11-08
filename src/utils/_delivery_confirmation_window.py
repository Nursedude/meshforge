"""The ONE windowed confirmation count behind the #74 delivery stall probe.

Split out of ``watchdog_probes_gateway`` (2026-11-07, MF025) and re-exported
there, so the probe, the TUI Delivery screen and ``traffic_pulse`` keep one
name and one rule (honest_failure_modes #5).

2026-11-07 — TIME window. The judgement used to be "whatever the ring holds".
On moc3 that was ~26 h, because Meshtastic queued/sent rows pruned the RNS
terminals out of the shared events pool; the probe sat ``indeterminate`` on 2
of 20 events from 10-30 on. The writer now keeps terminals in their own pool
(``gateway.delivery_counters``), and this window judges the confirmable
terminals of the last :data:`DELIVERY_STALL_WINDOW_S`. A quiet gateway then
gets a slow verdict instead of none; a busy one still clears the 20-event
floor in minutes.

The short-window COLLAPSE COUNTER (72 h, ≥5 terminal, 0 confirmed) is
OBSERVATION ONLY by operator decision 2026-11-07 ("step 3 as counter"): it is
reported, never turned into a Signal, until a soak baseline says what moc3
actually produces (feedback: observe before alarm). The dated rows it counts
stay in the terminal pool, so that baseline is re-derivable from the DB itself.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

# Drop reasons that mean a delivery was ATTEMPTED and FAILED — the
# denominator-mates of `confirmed` for a confirmable protocol. Benign
# drops (dedup, capacity shedding) are NOT delivery failures and must
# never count against the confirmation rate.
_DELIVERY_FAILURE_REASONS = frozenset({
    "rns_delivery_failed", "retries_exhausted", "destination_unreachable",
    "delivery_timeout", "non_retriable_error", "circuit_open", "wedged",
    "expired", "unsendable",
})

#: The policy refusal reason (gateway.delivery_counters.DropReason
#: .INGRESS_REFUSED, test-pinned): a refusal is never a delivery outcome.
INGRESS_REFUSED = "ingress_refused"

_FAIL_SORTED = tuple(sorted(_DELIVERY_FAILURE_REASONS))

#: The ONE "judged terminal" predicate (2026-11-07): a confirmation, or a drop
#: whose reason is a real delivery failure — exactly what the rate judges.
#: Benign drops (dedup, queue_shed …) are terminal but NOT judged, so they
#: live with in-flight rows and can never evict a judged one (review r1 #1).
#: Used by the writer's prune pool, the served ``recent_terminal`` query and
#: traffic_pulse's DB fallback; :func:`is_judged_terminal` is its Python twin,
#: pinned equal over every state × reason by test. NULL-safe by COALESCE.
JUDGED_TERMINAL_SQL = (
    "COALESCE(drop_reason, '') != ? AND (state = ? OR (state = ? AND "
    "COALESCE(drop_reason, '') IN (" + ", ".join("?" * len(_FAIL_SORTED)) + ")))")
JUDGED_TERMINAL_ARGS = (INGRESS_REFUSED, "confirmed", "dropped") + _FAIL_SORTED


def is_judged_terminal(state: Optional[str], drop_reason: Optional[str]) -> bool:
    """Python twin of :data:`JUDGED_TERMINAL_SQL`."""
    if drop_reason == INGRESS_REFUSED:
        return False
    return state == "confirmed" or (
        state == "dropped" and drop_reason in _DELIVERY_FAILURE_REASONS)


#: Rows ``snapshot()`` serves as ``recent_terminal`` (gateway.delivery_counters
#: .SNAPSHOT_RECENT_LIMIT, test-pinned). A full ring with nothing aged out
#: means the COUNT, not the age, bounded the window — said so, never "7 d".
SERVED_TERMINAL_LIMIT = 200

#: An event stamped further ahead than this is a clock artifact: kept in the
#: 7-day window (evidence), but its age is unknown, so it never counts toward
#: the 72 h counter (review r1 #3; the watchdog's snapshot slop).
FUTURE_SLOP_S = 900.0

#: The rate verdict's window: confirmable terminals newer than this count.
DELIVERY_STALL_WINDOW_S = 7 * 86400.0

#: The collapse COUNTER's window and floor (observation only — see module doc).
COLLAPSE_COUNTER_WINDOW_S = 72 * 3600.0
COLLAPSE_COUNTER_MIN_TERMINAL = 5


def _ts(e: dict) -> Optional[float]:
    v = e.get("ts")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def confirmation_window(payload: dict, *, now: Optional[float] = None,
                        window_s: float = DELIVERY_STALL_WINDOW_S) -> dict:
    """The windowed confirmation count ``probe_delivery_confirmation_stall``
    judges, as a pure function of a delivery snapshot.

    Keys: ``confirmable`` (sorted protocols that have ever confirmed),
    ``ring_source`` (``recent_terminal`` | ``recent`` | None when neither ring
    is a list), ``ring`` (the in-window entries of the ring judged, or None),
    ``confirmed``, ``failed`` (drops whose reason is a real delivery failure),
    ``terminal`` (their sum), ``window_s``, ``out_of_window`` (confirmable
    terminals older than the window — evidence that existed but was too old),
    ``undated`` (confirmable terminals with no usable ts, kept IN the window:
    unknown age is not old), and ``collapse`` (the 72 h observation counter).

    Wall clock is forgeable here (honest_failure_modes #6): an event stamped
    in the FUTURE (the clock stepped back) stays in the window, never dropped;
    a clock that jumped FORWARD ages everything out, which reads as too-few —
    blindness, the honest direction, never a false green.

    Prefer the terminal-only ring (2026-09-10). ``recent`` is a general-purpose
    FIFO: on a gateway whose traffic is mostly a protocol that can never
    confirm, it fills with queued/sent events and evicts the confirmable
    terminals needed here. Older gateways (and older snapshot files) carry no
    ``recent_terminal``; fall back to ``recent`` rather than going blind on the
    box that has not rolled yet."""
    now = time.time() if now is None else now
    by_proto = payload.get("state_by_protocol") or {}
    if not isinstance(by_proto, dict):
        by_proto = {}
    confirmed_by_proto = by_proto.get("confirmed") or {}
    if not isinstance(confirmed_by_proto, dict):
        confirmed_by_proto = {}
    confirmable = sorted(
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    )
    ring = payload.get("recent_terminal")
    ring_source: Optional[str] = "recent_terminal"
    if not isinstance(ring, list):
        ring = payload.get("recent")
        ring_source = "recent"
    if not isinstance(ring, list):
        ring, ring_source = None, None

    cutoff = now - window_s
    short_cutoff = now - COLLAPSE_COUNTER_WINDOW_S
    in_window: List[Any] = []
    confirmed = failed = out_of_window = undated = 0
    s_confirmed = s_failed = 0
    oldest = newest = None
    for e in ring or ():
        if not isinstance(e, dict):
            continue
        judged = (e.get("protocol") in confirmable
                  and is_judged_terminal(e.get("state"), e.get("drop_reason")))
        ts = _ts(e)
        if ts is not None and ts > now + FUTURE_SLOP_S:
            ts = None           # clock artifact: keep the evidence, not its age
        if ts is not None and ts < cutoff:
            out_of_window += judged
            continue
        in_window.append(e)
        if not judged:
            continue
        is_conf = e.get("state") == "confirmed"
        confirmed += is_conf
        failed += not is_conf
        if ts is None:
            undated += 1        # unknown age: in the window, never "recent"
            continue
        oldest = ts if oldest is None else min(oldest, ts)
        newest = ts if newest is None else max(newest, ts)
        if ts >= short_cutoff:
            s_confirmed += is_conf
            s_failed += not is_conf
    s_terminal = s_confirmed + s_failed
    collapse: Dict[str, Any] = {
        "window_s": COLLAPSE_COUNTER_WINDOW_S,
        "terminal": s_terminal, "confirmed": s_confirmed, "failed": s_failed,
        "min_terminal": COLLAPSE_COUNTER_MIN_TERMINAL,
        "candidate": (s_terminal >= COLLAPSE_COUNTER_MIN_TERMINAL
                      and s_confirmed == 0),
        "observe_only": True,
    }
    ring_total = sum(1 for e in ring or () if isinstance(e, dict))
    # Count-bounded, not age-bounded (Fable r1 F3): the served ring is full
    # and nothing aged out, so the oldest judged event is a ring edge, not
    # the window edge — the label must say which bound applied.
    # Either ring: a full legacy `recent` ring is count-bounded too (Fable r2).
    truncated = ring_total >= SERVED_TERMINAL_LIMIT and out_of_window == 0
    span_s = (newest - oldest) if (oldest is not None and newest is not None) else None
    return {"confirmable": confirmable, "ring_source": ring_source,
            "ring": in_window if ring is not None else None,
            "ring_total": ring_total,
            "confirmed": confirmed, "failed": failed,
            "terminal": confirmed + failed, "window_s": window_s,
            "out_of_window": out_of_window, "undated": undated,
            "truncated": truncated, "span_s": span_s,
            "collapse": collapse}


def _dur(seconds: float) -> str:
    """168 h → "7 d", 72 h → "72 h", 47.8 h → "47.8 h"."""
    h = seconds / 3600.0
    if h > 72:
        d = h / 24
        return f"{d:g} d" if d == int(d) else f"{d:.1f} d"
    return f"{h:g} h" if h == int(h) else f"{h:.1f} h"


def format_window(win: dict) -> str:
    """One short phrase naming the window and the collapse counter, for the
    probe's disposition reason and the TUI (one wording, two surfaces).
    Labels derive from the constants the counts used (review r1 #5)."""
    c = win.get("collapse") or {}
    if win.get("truncated"):
        span = win.get("span_s")
        noun = ("terminals" if win.get("ring_source") == "recent_terminal"
                else f"`{win.get('ring_source')}` events")
        parts = [f"newest {win.get('ring_total')} {noun}"
                 + (f", spanning {_dur(span)}" if span is not None else "")
                 + f" (count-bounded, under {_dur(win.get('window_s', DELIVERY_STALL_WINDOW_S))})"]
    else:
        parts = [f"last {_dur(win.get('window_s', DELIVERY_STALL_WINDOW_S))}"]
    if win.get("out_of_window"):
        parts.append(f"{win['out_of_window']} older outside")
    if win.get("undated"):
        parts.append(f"{win['undated']} of unknown age")
    parts.append(f"{_dur(c.get('window_s', COLLAPSE_COUNTER_WINDOW_S))}: "
                 f"{c.get('terminal', 0)} terminal / {c.get('confirmed', 0)} confirmed"
                 + (" — COLLAPSE CANDIDATE (observe-only)" if c.get("candidate") else ""))
    return "; ".join(parts)
