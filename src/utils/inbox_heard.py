"""INBOX-2 step 3 — "heard by": join one inbox send to each fleet box's
``rx_heard`` evidence (box_truth/v1, step 2) and judge every box in a
CLOSED vocabulary. Pure: no I/O, no clock read — the caller passes ``now``,
the sections (from ``box_truth_center.read_spool``, which already turns a
stale spool into None) and the DECLARED radio segments.

Design: .claude/plans/inbox2_heard_by_design_2026_11_07.md (D2, D3).

- ``heard``        the box decoded a packet with this id. The join is the
                   32-bit packet id ONLY; the clock window rides box wall
                   clocks (hfm #6), so a hearing outside it is TAGGED
                   ``skewed``, never dropped. Positive evidence outranks a
                   declaration, so ``heard`` is checked first.
- ``cannot_hear``  BOTH the box's and the send's segment are declared and
                   differ (two-preset fleet). Never guessed: an undeclared
                   segment is not a reason.
- ``not_heard``    ONLY with coverage on both sides of the send: journal
                   read from before it, through send + JOIN_AFTER_S, and the
                   radio's journal still writing after it (the step-2 blind
                   spot — coverage alone survives a meshtasticd that went quiet).
- ``unobservable`` everything else, each with its reason. Never folded into
                   ``not_heard`` (hfm #2).

Shown words are step 4's; this module says "heard", never "delivered".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from utils.box_truth_rx import FUTURE_SLOP_S, _valid_entry

STATES = ("heard", "not_heard", "unobservable", "cannot_hear")

#: the D2 window — a TAG on a hearing, and the listen span ``not_heard`` needs
JOIN_BEFORE_S = 10.0
JOIN_AFTER_S = 600.0

_ID_MAX = 0xFFFFFFFF


@dataclass(frozen=True)
class BoxHeard:
    alias: str
    state: str
    reason: str
    rx_ts: Optional[float] = None
    skewed: bool = False


def _ts(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _valid_pid(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, int) and 0 < v <= _ID_MAX


def judge_box(alias: str, section: Any, *, packet_id: Any, sent_ts: float, now: float,
              box_segment: Optional[str] = None,
              send_segment: Optional[str] = None) -> BoxHeard:
    """One box's verdict on one send. Never raises on a malformed section."""
    def out(state: str, reason: str, **kw) -> BoxHeard:
        return BoxHeard(alias, state, reason, **kw)

    if not _valid_pid(packet_id):
        return out("unobservable", "send has no packet_id to join on")

    entries = section.get("entries") if isinstance(section, dict) else None
    if isinstance(entries, list):
        hits = [e[2] for e in map(_valid_entry, entries) if e and e[1] == packet_id]
        if hits:
            rx = min(hits)
            skewed = not (sent_ts - JOIN_BEFORE_S <= rx <= sent_ts + JOIN_AFTER_S)
            return out("heard", "decoded this packet id" + (
                " (box clock outside the join window)" if skewed else ""),
                rx_ts=rx, skewed=skewed)

    if box_segment and send_segment and box_segment != send_segment:
        return out("cannot_hear", f"declared {box_segment}, send on {send_segment}")

    if not isinstance(section, dict):
        return out("unobservable", "no rx_heard section (no grant, old export, or stale spool)")
    if not isinstance(entries, list):
        return out("unobservable", "rx_heard entries missing or not a list")
    since = _ts(section.get("covered_since"))
    until = _ts(section.get("covered_until"))
    last_line = _ts(section.get("last_line_ts"))
    if since is None or until is None or last_line is None:
        return out("unobservable", "rx_heard coverage not stated")
    if until > now + FUTURE_SLOP_S or last_line > now + FUTURE_SLOP_S:
        return out("unobservable", "box clock ahead of this center")
    if since > until:
        return out("unobservable", "rx_heard coverage inverted")
    if since > sent_ts:
        return out("unobservable", "box journal coverage starts after the send")
    if until < sent_ts + JOIN_AFTER_S:
        return out("unobservable", "listen window still open (not covered past send + 600 s)")
    if last_line < sent_ts:
        return out("unobservable", "radio journal silent since before the send")
    return out("not_heard", "covered before and after the send; this id never decoded")


def judge_send(packet_id: Any, sent_ts: float, sections: Mapping[str, Any], *, now: float,
               segments: Optional[Mapping[str, Optional[str]]] = None,
               send_segment: Optional[str] = None) -> List[BoxHeard]:
    """Every box in ``sections`` judged once, sorted by alias. A box the caller
    knows of but could not read is passed as None — never left out."""
    segs = segments or {}
    return [judge_box(a, sections[a], packet_id=packet_id, sent_ts=sent_ts, now=now,
                      box_segment=segs.get(a), send_segment=send_segment)
            for a in sorted(sections)]


def summarize(results: Iterable[BoxHeard]) -> Dict[str, List[str]]:
    """{state: [aliases]} over EVERY state, empty lists included."""
    out: Dict[str, List[str]] = {s: [] for s in STATES}
    for r in results:
        if r.state not in out:
            raise ValueError(f"unknown heard state {r.state!r}")  # hfm #7
        out[r.state].append(r.alias)
    return out
