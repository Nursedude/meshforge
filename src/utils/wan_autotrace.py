"""Auto-trace the path out when the WAN ladder goes red — so the localization
is already waiting, instead of being a thing the operator must know to ask for.

Born 2026-09-06 from the operator's rule: *a tool that's silent has no
diagnostic meaning to a user.* The ladder (``utils.wan_path``) had been logging
FAIL every ten minutes for seven hours, and answering "where does it start"
still meant somebody deciding to run a trace by hand. An instrument that only
speaks when interrogated is not an instrument; it is homework.

**Why this lives cron-side and not inside mini-dudeai.** mini is
observation-only (MF021): its engine, sources and actions may never run a
subprocess, and a path trace is dozens of ``ping`` invocations. So the
measurement belongs to the cron that already owns the ladder, and mini READS
the artifact. That is the same split every organ here uses — the cron measures,
the watcher observes — and it is the reason this module exists as its own
file rather than as a mini source.

Throttled on purpose. A trace costs ~2 minutes of probes per target, the
ladder runs every 10 minutes, and today's event has lasted 7 hours: naive
wiring would have fired 42 traces and taught nobody anything after the first.
So: once per cooldown, and immediately whenever the CAUSE changes, because a
cause change is new information and re-tracing is how you learn what moved.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from utils.paths import atomic_write_text
from utils.wan_path import state_dir

#: How long a trace stands before the next red tick earns a fresh one.
TRACE_COOLDOWN_S = 3600.0
#: Past this, a stored trace no longer describes now and readers must say so
#: rather than present it as current (honest_failure_modes #2).
TRACE_STALE_S = 4 * 3600.0
#: Causes worth tracing. ``lan`` is deliberately excluded — the ladder has
#: already named the first hop, and walking past it adds probes, not knowledge.
TRACEABLE_CAUSES = ("edge", "transit", "edge-or-transit", "unknown")


def trace_state_path() -> Path:
    return state_dir() / "wan_trace.json"


# --------------------------------------------------------------------------
# decisions — pure
# --------------------------------------------------------------------------

def should_autotrace(status: Optional[str], cause: Optional[str],
                     last: Optional[Dict[str, Any]], now: float,
                     cooldown_s: float = TRACE_COOLDOWN_S) -> Tuple[bool, str]:
    """Should this red tick spend two minutes of probes? ``(run, reason)``.

    The reason is returned in both directions and recorded, because "we did not
    trace" is a thing a reader will want explained — a silent skip is the same
    defect this module exists to cure, one level down.
    """
    if status not in ("fail", "concern"):
        return False, "ladder is %s — nothing to localize" % (status or "unknown")
    if cause not in TRACEABLE_CAUSES:
        if cause == "lan":
            return False, ("loss is on the first hop (lan) — the ladder already "
                           "names it; a path trace past it would add probes, not "
                           "knowledge")
        return False, "cause %r is not traceable" % cause

    if not isinstance(last, dict) or not last.get("generated_at"):
        return True, "no previous trace on this box"

    prev_cause = ((last.get("trigger") or {}).get("cause"))
    if prev_cause and prev_cause != cause:
        return True, "cause changed %s -> %s since the last trace" % (prev_cause, cause)

    age = now - float(last["generated_at"])
    if age < 0:
        # Clock went backwards (RTC-less Pi, NTP step). Re-trace rather than
        # trust a future-stamped artifact.
        return True, "stored trace is stamped in the future — clock moved; re-tracing"
    if age >= cooldown_s:
        return True, "last trace is %.0f min old (cooldown %.0f min)" % (
            age / 60.0, cooldown_s / 60.0)
    return False, "traced %.0f min ago; next after %.0f min" % (
        age / 60.0, (cooldown_s - age) / 60.0)


def pick_targets(wan_state: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """``(worst_far_host, clean_far_control)`` — what to trace, and against what.

    A single trace says a destination is lossy. A clean one beside it is what
    separates "my uplink" from "one provider": on 2026-09-06 the lossy targets
    and the clean one shared two hops and then split into different transit,
    which named the suspect without needing to see inside anyone's network.
    """
    fail_pct = float(wan_state.get("fail_pct") or 5.0)
    far = [r for r in (wan_state.get("rungs") or []) if r.get("rung") == "far"]
    measured = [r for r in far if isinstance(r.get("loss_pct"), (int, float))]
    if not measured:
        return None, None
    worst = max(measured, key=lambda r: r["loss_pct"])
    if worst["loss_pct"] < fail_pct:
        return None, None
    clean = [r for r in measured if r["loss_pct"] < fail_pct]
    control = min(clean, key=lambda r: r["loss_pct"])["host"] if clean else None
    return worst.get("host"), control


def summarize(results: Sequence[Any]) -> Tuple[str, str]:
    """``(status, one_line)`` for the primary (first) trace — what a brief shows."""
    if not results:
        return "unknown", "no trace result"
    primary = results[0]
    finding = getattr(primary, "finding", None) or (
        primary.get("finding") if isinstance(primary, dict) else None)
    if not finding:
        return "unknown", "trace produced no finding"
    if isinstance(finding, dict):
        status, msg, conf = finding.get("status"), finding.get("message"), finding.get("confidence")
    else:
        status, msg, conf = finding.status, finding.message, finding.confidence
    target = getattr(primary, "target", None) or (
        primary.get("target") if isinstance(primary, dict) else "?")
    return str(status), "%s: %s [%s] %s" % (target, str(status).upper().replace("_", " "),
                                            conf, msg)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def read_trace_state(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    p = path or trace_state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_trace_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or trace_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(state, indent=1) + "\n")


def build_trace_state(wan_state: Dict[str, Any], results: Sequence[Any],
                      comparison: Sequence[str], reason: str,
                      now: Optional[float] = None) -> Dict[str, Any]:
    from dataclasses import asdict, is_dataclass
    now = time.time() if now is None else now
    status, summary = summarize(results)
    return {
        "generated_at": now,
        "reason": reason,
        "trigger": {"status": wan_state.get("status"), "cause": wan_state.get("cause"),
                    "message": wan_state.get("message"),
                    "measured_at": wan_state.get("generated_at")},
        "targets": [getattr(r, "target", None) for r in results],
        "status": status,
        "summary": summary,
        "comparison": list(comparison),
        "results": [asdict(r) if is_dataclass(r) else r for r in results],
    }


def autotrace(wan_state: Dict[str, Any], now: Optional[float] = None,
              probes: int = 10, cooldown_s: float = TRACE_COOLDOWN_S,
              progress=None) -> Tuple[bool, str]:
    """Trace if this red tick has earned one. ``(ran, reason)``.

    Impure by design and called only from the cron-side probe — never from
    mini (MF021). Failure to trace is never silent: the reason is returned and
    the caller records it.
    """
    from utils.path_trace import compare, trace

    now = time.time() if now is None else now
    last = read_trace_state()
    run, reason = should_autotrace(wan_state.get("status"), wan_state.get("cause"),
                                   last, now, cooldown_s)
    if not run:
        return False, reason

    primary, control = pick_targets(wan_state)
    if not primary:
        return False, "no far target measured above the fail floor — nothing to trace"

    targets = [primary] + ([control] if control and control != primary else [])
    results = [trace(t, probes=probes, progress=progress) for t in targets]
    state = build_trace_state(wan_state, results,
                              compare(results) if len(results) > 1 else [],
                              reason, now=now)
    write_trace_state(state)
    return True, reason
