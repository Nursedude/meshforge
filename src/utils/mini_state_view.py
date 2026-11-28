"""The ONE reader of a mini-dudeai state document (and its history rows).

Every surface that renders a box's mini state — the fleet watchers pane and
deep feed (``mini_dudeai.rollup``), the warm brief, the warm-start banner, the
audit, the TUI pane, the oracle snapshot — reads REMOTE JSON written by a
daemon that may be old, torn, or wrong. P3 (11-27) review rounds r1 and r2
both found the same defect kind: a reader guarded the fields it was told
about and the NEXT field (``rules:[1]``, ``source_errors:[null]``,
``error_count:NaN``, ``host:5``, a history ``ts`` of ``"x"`` / ``10**400``)
raised the whole pane or read healthy. Each fold patched readers one field at
a time; this module is the seam: readers consume the VIEW, never the raw dict.

Contract (every value is safe to compare, format and ``json.dumps(...,
allow_nan=False)``):

* ``tick_present`` — the state carries a ``last_tick_ts`` that is not null.
  ``last_tick`` — that stamp through ``finite()`` and > 0, else None. Present
  but None = freshness UNOBSERVABLE (never "never ticked", never fresh).
* ``rules`` — a dict of dict rows only; ``rules_unreadable`` /
  ``rules_dropped`` witness what was refused (never silently absorbed).
* ``rule_count`` — the writer's count when it is a finite non-negative
  integer, else ``len(rules)`` (the dict IS the measurement).
* ``error_count`` — absent = 0 (pre-count daemons); present but not a finite
  non-negative integer = None = UNKNOWN, which no reader may render as 0.
* ``source_errors`` — a list of str; a non-text item renders as its type, a
  non-list section sets ``source_errors_unreadable``.
* ``host`` — a non-empty str or None. ``pending_deltas`` / ``fire_count`` —
  finite non-negative int or None.
* ``unreadable`` — the names of PRESENT fields the view refused (rules,
  error_count, source_errors, rule_count); ``unreadable_note`` renders it.
  A reader that shows a verdict shows this and never calls the box quiet.

``history_view`` gives each row a ``ts`` that is finite-or-None (None =
untimed, kept: an escalation is never hidden by a torn stamp) and COUNTS the
rows whose stamp was present-but-unreadable plus the non-object rows dropped.

Stdlib-only (imports only ``finite_stamp``, itself stdlib-only) so every
reader can import it at module top. Byte-identical with MeshAnchor
(parity_check BYTE_IDENTICAL).
"""
from __future__ import annotations

from typing import Any, Optional

from utils.finite_stamp import finite


def _count(v: Any) -> Optional[int]:
    """A finite non-negative integral count, else None."""
    f = finite(v)
    if f is None or f < 0 or f != int(f):
        return None
    return int(f)


def mini_state_view(state: Any) -> dict:
    """Normalize a raw mini state document. Never raises."""
    readable = isinstance(state, dict)
    s = state if readable else {}

    raw_tick = s.get("last_tick_ts")
    tick_present = raw_tick is not None
    t = finite(raw_tick)
    last_tick = t if (t is not None and t > 0) else None

    raw_rules = s.get("rules")
    rules: dict = {}
    rules_dropped = 0
    rules_unreadable = raw_rules is not None and not isinstance(raw_rules, dict)
    if isinstance(raw_rules, dict):
        for k, v in raw_rules.items():
            if isinstance(v, dict):
                rules[str(k)] = v
            else:
                rules_dropped += 1

    rc = _count(s.get("rule_count"))
    rule_count = rc if rc is not None else len(rules)

    if "error_count" not in s:
        error_count: Optional[int] = 0
    else:
        error_count = _count(s.get("error_count"))

    raw_se = s.get("source_errors")
    source_errors = []
    source_errors_unreadable = raw_se is not None and not isinstance(raw_se, list)
    if isinstance(raw_se, list):
        for item in raw_se:
            source_errors.append(item if isinstance(item, str)
                                 else f"<{type(item).__name__}>")

    host = s.get("host")
    host = host if isinstance(host, str) and host else None

    # The witness (hfm #3/#9): every PRESENT field the view had to refuse.
    # A `rules:[1]` box otherwise read "fresh · 0 rules" and the oracle "all
    # quiet" — healthy-shaped off a section nobody could read. Readers that
    # render a verdict show this list and never call the box quiet beside it.
    unreadable = []
    if rules_unreadable or rules_dropped:
        unreadable.append("rules")
    if error_count is None:
        unreadable.append("error_count")
    if source_errors_unreadable:
        unreadable.append("source_errors")
    if s.get("rule_count") is not None and rc is None:
        unreadable.append("rule_count")

    return {
        "readable": readable,
        "tick_present": tick_present,
        "last_tick": last_tick,
        "rules": rules,
        "rules_unreadable": rules_unreadable,
        "rules_dropped": rules_dropped,
        "rule_count": rule_count,
        "error_count": error_count,
        "source_errors": source_errors,
        "source_errors_unreadable": source_errors_unreadable,
        "host": host,
        "pending_deltas": _count(s.get("pending_deltas")),
        "fire_count": _count(s.get("fire_count")),
        "unreadable": unreadable,
    }


def unreadable_note(view: dict) -> str:
    """`` · ⚠️ unreadable: rules, error_count`` — empty when nothing was
    refused. One wording for every reader (hfm #5)."""
    names = view.get("unreadable") or []
    return f" · ⚠️ unreadable: {', '.join(names)}" if names else ""


def errors_label(view: dict) -> str:
    """``src_errors=N`` text, ``?`` when the count is UNKNOWN (never 0)."""
    n = view.get("error_count")
    return "?" if n is None else str(n)


def history_view(rows: Any) -> tuple:
    """``(rows, bad_ts, dropped)``: dict rows only (copies), each ``ts``
    finite-or-None. ``bad_ts`` counts rows whose stamp was present but not a
    finite time (now untimed); ``dropped`` counts non-object rows refused."""
    out = []
    bad_ts = 0
    dropped = 0
    for h in rows if isinstance(rows, list) else []:
        if not isinstance(h, dict):
            dropped += 1
            continue
        raw = h.get("ts")
        ts = finite(raw)
        if raw is not None and ts is None:
            bad_ts += 1
        row = dict(h)
        row["ts"] = ts
        out.append(row)
    return out, bad_ts, dropped
