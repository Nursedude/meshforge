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
* ``rules`` — a dict of COPIED rows through ``rule_row`` (r3, one level
  down: ``rule_id`` / ``subject`` hashable str-or-None, ``last_detail``
  capped text, ``currently_active`` bool-or-None — non-bool reads ACTIVE,
  ``fire_count_24h`` a count); ``rules_unreadable`` / ``rules_dropped``
  witness what was refused (never silently absorbed). Escalation payloads
  take the same rule through ``escalation_view``.
* ``rule_count`` — the writer's count when it is a finite non-negative
  integer, else ``len(rules)`` (the dict IS the measurement).
* ``error_count`` — absent = 0 (pre-count daemons); present but not a finite
  non-negative integer = None = UNKNOWN, which no reader may render as 0.
* ``source_errors`` — a list of str; a non-text item renders as its type, a
  non-list section sets ``source_errors_unreadable``.
* ``host`` — a printable str of <= MAX_HOST chars, or None.
  ``pending_deltas`` / ``fire_count`` — count (<= MAX_COUNT) or None.
* ``unreadable`` — the fields the view refused (rules — incl. a coerced row
  or a ``rule_count`` with no rules section — error_count, source_errors,
  rule_count, host); ``unreadable_note`` renders it. ``quiet_ok`` is the ONE
  rule for "may a reader call this quiet": every section read, a known-0
  error count, no named source error.

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


#: Bounds on what a remote document may make a reader print (r3 N4/N5): a
#: 309-digit count or a 20 kB error string is not a value, it is a writer
#: fault. Beyond these the field is REFUSED (and witnessed), never echoed.
MAX_COUNT = 10 ** 9
MAX_TEXT = 200
MAX_HOST = 64


def _count(v: Any) -> Optional[int]:
    """A finite non-negative integral count (<= MAX_COUNT), else None."""
    f = finite(v)
    if f is None or f < 0 or f != int(f) or f > MAX_COUNT:
        return None
    return int(f)


def _ident(v: Any) -> Optional[str]:
    """A rule_id / subject from remote JSON as a hashable, printable str.

    P3 r3 I1/I2: a list ``rule_id`` raised ``unhashable`` in the brief's
    last-seen index and the TUI's seen-set — the seam had normalized the
    top-level fields and left the rows raw. None stays None (absent); a
    finite number reads as its text; anything else names its type."""
    if v is None:
        return None
    if isinstance(v, str):
        return v[:MAX_TEXT]
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int) and _count(v) is not None:
        return str(v)
    if isinstance(v, float) and finite(v) is not None:
        return repr(v)
    return f"<{type(v).__name__}>"


def _text(v: Any) -> str:
    """Free text (detail / note) capped; a non-str renders as its str."""
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v) if isinstance(v, (int, float, bool)) \
        else f"<{type(v).__name__}>"
    return s[:MAX_TEXT]


def rule_row(raw: Any) -> tuple:
    """``(row, coerced)``: a COPY of one state rule row with ``rule_id`` /
    ``subject`` through ``_ident``, ``last_detail`` through ``_text``,
    ``currently_active`` a bool-or-None (a non-bool is read ACTIVE — the
    conservative direction: a live condition is never hidden by a garbled
    flag) and ``fire_count_24h`` a count-or-0. ``coerced`` = any field had
    to change; None when ``raw`` is not an object."""
    if not isinstance(raw, dict):
        return None, True
    row = dict(raw)
    coerced = False
    for k in ("rule_id", "subject"):
        if k in raw:
            row[k] = _ident(raw[k])
            coerced |= row[k] != raw[k]
    if "last_detail" in raw:
        row["last_detail"] = _text(raw["last_detail"])
        coerced |= row["last_detail"] != raw["last_detail"]
    ca = raw.get("currently_active")
    if ca is not None and not isinstance(ca, bool):
        row["currently_active"] = True
        coerced = True
    if "fire_count_24h" in raw:
        fc = _count(raw["fire_count_24h"])
        row["fire_count_24h"] = fc if fc is not None else 0
        coerced |= fc is None
    return row, coerced


def escalation_view(esc: Any) -> Optional[dict]:
    """A COPY of one escalation payload with ``rule`` / ``subject`` through
    ``_ident`` and ``detail`` / ``note`` through ``_text``; None when not an
    object. Every escalation reader (brief, deep feed, TUI, digest) gets its
    payloads through ``brief._escalation_of``, which calls this (r3 I1)."""
    if not isinstance(esc, dict):
        return None
    out = dict(esc)
    for k in ("rule", "subject"):
        out[k] = _ident(esc.get(k))
    out["detail"] = _text(esc.get("detail"))
    if "note" in esc:
        out["note"] = _text(esc.get("note"))
    return out


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
    rows_coerced = 0
    rules_unreadable = raw_rules is not None and not isinstance(raw_rules, dict)
    if isinstance(raw_rules, dict):
        for k, v in raw_rules.items():
            row, coerced = rule_row(v)
            if row is None:
                rules_dropped += 1
                continue
            rules[str(k)] = row
            rows_coerced += bool(coerced)

    rc = _count(s.get("rule_count"))
    rule_count = rc if rc is not None else len(rules)
    # r3 I4: the writer COUNTS rules but no section came with them (null /
    # absent rules beside rule_count 7) — every surface read quiet. A count
    # with nothing to count is a section the view could not read.
    rules_missing = (raw_rules is None and rc is not None and rc > 0)

    if "error_count" not in s:
        error_count: Optional[int] = 0
    else:
        error_count = _count(s.get("error_count"))

    raw_se = s.get("source_errors")
    source_errors = []
    source_errors_unreadable = raw_se is not None and not isinstance(raw_se, list)
    if isinstance(raw_se, list):
        for item in raw_se:
            source_errors.append(_text(item) if item is not None else "<None>")

    # A host is a LABEL rendered in titles and answers (r3 N5: a newline +
    # markdown in `host` became the brief title and the oracle's prefix):
    # printable, bounded, else refused.
    raw_host = s.get("host")
    host = (raw_host if isinstance(raw_host, str) and raw_host
            and len(raw_host) <= MAX_HOST and raw_host.isprintable() else None)
    host_unreadable = raw_host is not None and host is None and raw_host != ""

    # The witness (hfm #3/#9): every PRESENT field the view had to refuse.
    # A `rules:[1]` box otherwise read "fresh · 0 rules" and the oracle "all
    # quiet" — healthy-shaped off a section nobody could read. Readers that
    # render a verdict show this list and never call the box quiet beside it.
    unreadable = []
    if rules_unreadable or rules_dropped or rows_coerced or rules_missing:
        unreadable.append("rules")
    if error_count is None:
        unreadable.append("error_count")
    if source_errors_unreadable:
        unreadable.append("source_errors")
    if s.get("rule_count") is not None and rc is None:
        unreadable.append("rule_count")
    if host_unreadable:
        unreadable.append("host")

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


def quiet_ok(view: dict) -> bool:
    """May a reader call this state QUIET / a clean bill? Only when it read
    every section, the error count is a known 0 and no source named an error.
    ONE rule for the brief's Quiet line, the TUI's ✓, the oracle's "all
    quiet" (r3: a known `error_count: 2`, or a named source error beside a 0
    count, still read quiet — an unknown count was louder than a known one).
    Freshness is each reader's own gate, on top of this."""
    return (view.get("error_count") == 0 and not view.get("unreadable")
            and not view.get("source_errors"))


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
