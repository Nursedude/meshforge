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

from utils.box_truth_rx import FUTURE_SLOP_S, RING_WINDOW_S, _valid_entry

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
    #: r2: shown from the store (a hearing an earlier read kept), not from the
    #: live ring — a FIELD, never inferred from the reason text
    kept: bool = False


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
    """One box's verdict on one send. Never raises on a malformed section:
    ``float()`` of a 310-digit JSON integer raised OverflowError and took the
    whole inbox read down (BIC-2 review r5 MED) — any such section is
    ``unobservable``, with the reason."""
    try:
        return _judge_box(alias, section, packet_id=packet_id, sent_ts=sent_ts, now=now,
                          box_segment=box_segment, send_segment=send_segment)
    except (OverflowError, ValueError, TypeError) as e:
        return BoxHeard(alias, "unobservable",
                        f"malformed rx_heard section ({type(e).__name__})")


def _judge_box(alias: str, section: Any, *, packet_id: Any, sent_ts: float, now: float,
               box_segment: Optional[str] = None,
               send_segment: Optional[str] = None) -> BoxHeard:
    def out(state: str, reason: str, **kw) -> BoxHeard:
        return BoxHeard(alias, state, reason, **kw)

    if not _valid_pid(packet_id):
        return out("unobservable", "send has no packet_id to join on")
    if _ts(sent_ts) is None:
        # INBOX-T F10: NaN made every coverage comparison False → "not_heard"
        return out("unobservable", "send time unknown")

    entries = section.get("entries") if isinstance(section, dict) else None
    if isinstance(entries, list):
        hits = [(e[0], e[2]) for e in map(_valid_entry, entries) if e and e[1] == packet_id]
        if hits:
            # A packet id is unique per SENDER, not per mesh (F10): a 24 h ring
            # can hold an unrelated packet that reused the id. Prefer hearings
            # inside the window; two senders among the chosen ones prove nothing.
            def in_window(ts):
                return sent_ts - JOIN_BEFORE_S <= ts <= sent_ts + JOIN_AFTER_S
            pool = [h for h in hits if in_window(h[1])] or hits
            senders = {f for f, _ in pool}
            if len(senders) > 1:
                return out("unobservable",
                           f"packet id decoded from {len(senders)} senders — ambiguous")
            rx = min(ts for _, ts in pool)
            skewed = not in_window(rx)
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
        window = _ts(section.get("window_s"))
        if window is not None and window > 0 and sent_ts < until - window:
            # the box's RING moved past the send (11-23: its journal still held
            # the packet). Whether a read judged the send in time is NOT known
            # here — the caller says it (r2 MED-1, ``heard_judged``)
            return out("unobservable", f"{RING_AGED_PREFIX}{window / 3600:g} h rx ring")
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


#: Verdicts a center may KEEP once judged (BIC-2, 11-23): HEARINGS only.
#: A box's rx_heard ring vouches for 24 h (box_truth_rx RING_WINDOW_S), so a
#: send judged ``heard`` read "unobservable" a day later (measured: 7638/7640).
#: A hearing is positive evidence on the box's own wire fact (the packet id).
#: ``not_heard`` is an ABSENCE judged against the center's send stamp — a
#: center clock off by minutes judged it early (review r1 L2), and a kept
#: absence then outlived the evidence that would correct it (hfm #2). It
#: stays live-only: after the ring it reads unobservable, saying why.
#: ``cannot_hear`` is re-derived from the declaration on every read.
FREEZABLE = ("heard",)
KEPT_NOTE = (f" (kept: decoded while the box's {RING_WINDOW_S / 3600:g} h "
             f"rx ring covered this send)")
RING_AGED_PREFIX = "send is older than this box's "   # + "<N> h rx ring …"
#: r2 MED-1: the two endings of a ring-aged reason — ONE place decides which
AGED_JUDGED = "; a read judged this send in time and kept no hearing from this box"
AGED_UNJUDGED = "; no read judged this send while the ring covered it"


def judged_in_time(sent_ts: float, now: float) -> bool:
    """A read at ``now`` JUDGES a send when its listen window has closed and
    the boxes' rings can still cover it (center clock; hfm #6 — a center
    clock off by days is the accepted r1 L7 residual)."""
    return sent_ts + JOIN_AFTER_S <= now < sent_ts + RING_WINDOW_S


def _hearing_rank(b: Optional[BoxHeard]) -> int:
    """0 nothing kept · 1 a hearing outside the join window · 2 a clean one."""
    if b is None or b.state != "heard":
        return 0
    return 1 if b.skewed else 2


def merge_frozen(live: Iterable[BoxHeard],
                 frozen: Mapping[str, BoxHeard]) -> "tuple[List[BoxHeard], List[BoxHeard]]":
    """``(merged, to_keep)`` for one send. Pure.

    * the better HEARING shows — live or kept, a clean one over one outside
      the join window (r1 L5); a live hearing better than the kept one is
      returned to keep;
    * a kept hearing beats every live non-hearing — ``unobservable`` once the
      ring has moved on, and ``cannot_hear`` too: positive evidence outranks a
      declaration, exactly as the live judge orders it (r1 M1);
    * nothing else is kept, ever;
    * a kept box the fleet list no longer names still counts (it heard it).
    """
    merged: List[BoxHeard] = []
    new: List[BoxHeard] = []
    seen = set()
    for r in live:
        seen.add(r.alias)
        f = frozen.get(r.alias)
        if _hearing_rank(r) >= _hearing_rank(f):
            # r2 L-B: the live ring still confirms it → shown LIVE (no kept label)
            merged.append(r)
            if _hearing_rank(r) > _hearing_rank(f):
                new.append(r)
        else:
            merged.append(f)
    merged.extend(frozen[a] for a in sorted(frozen) if a not in seen)
    merged.sort(key=lambda b: b.alias)
    return merged, new
