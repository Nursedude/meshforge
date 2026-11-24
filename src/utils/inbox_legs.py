"""BIC-2 step 2 — "heard by" for one inbox send, read from the fleet's spools.

The pure judge is ``utils.inbox_heard`` (INBOX-2 step 3). This module is its
caller: it reads the center's target list and each box's FRESH spool at the
edge (``box_truth_center.read_spool`` — one reader, so freshness means the
same thing everywhere), hands the ``rx_heard`` sections to the judge, and
renders design D4's words (.claude/plans/inbox2_heard_by_design_2026_11_07.md):

    "heard by moc, kiai · 2 of 6 listening · 1 unobservable"

Honesty rules kept here (honest_failure_modes #1, #2):
  * a LISTED box whose spool is absent, stale or carries no ``rx_heard`` is
    passed as None → ``unobservable``; it is never dropped from the count and
    never read as "not heard";
  * no target list at all (a standalone box, no fleet) → ``None``: never a
    fleet it does not have; the inbox read turns it into ``NO_FLEET_LINE``
    (blind, said — not silence, not "heard by none");
  * the words are "heard", never "delivered" — a hearing proves a radio
    decoded the packet, not that a person read it.
Segments (``cannot_hear``) are passed only when declared; undeclared is never
a reason (inbox_heard D3).
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from utils import box_truth_center as center
from utils import inbox_heard

NO_ID_LINE = "heard by: cannot tell (this send has no radio packet id)"
#: a box with no target list: BLIND, said per send — never "heard by none",
#: never a count (BIC-2 one-box, measured silent 11-24; hfm #2/#11). Worded
#: for BOTH a standalone box and a non-center fleet member (measured 11-24:
#: only the center has a list) — never "not in a fleet".
NO_TARGETS = "it reads no box's rx spool — no target list here"
NO_FLEET_LINE = f"heard by: this box cannot see hearings ({NO_TARGETS})"


def kept_hearings(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The kept rows that ARE hearings — judged, not assumed from what the
    store happens to keep today (``inbox_heard.FREEZABLE`` may grow)."""
    return sorted((dict(b, kept=True) for b in rows if b.get("state") == "heard"),
                  key=lambda b: b["alias"])


def kept_only_line(hearings: Iterable[Mapping[str, Any]], why: str = NO_TARGETS) -> Optional[str]:
    """This box cannot judge the send NOW (``why``), but the store KEPT
    hearings for it (r1: a box that once read the fleet never calls itself
    blind about evidence it holds). A skewed hearing stays qualified, as on
    the fleet path (r5). None when there is no hearing to name."""
    named = [b["alias"] + (" (clock off)" if b.get("skewed") else "") for b in hearings]
    if not named:
        return None
    return f"heard by {', '.join(named)} (kept from an earlier read) · now {why}"


def blind_heard(kept_rows: Iterable[Mapping[str, Any]], *, line: str,
                why: str) -> Dict[str, Any]:
    """THE show-kept step for every exit that cannot judge a send now (no
    target list, unreadable fleet evidence, a per-send failure — BIC-2 seam
    11-24): the kept hearings still show beside ``why``; with none, the
    exit's own blind ``line``. One step, so no exit can drop them again."""
    hearings = kept_hearings(kept_rows)
    named = kept_only_line(hearings, why)
    return {"line": named, "boxes": hearings} if named else {"line": line}


def summarize_results(results: Iterable[inbox_heard.BoxHeard]) -> Dict[str, List[str]]:
    return inbox_heard.summarize(results)


def heard_line(summary: Mapping[str, List[str]], *, packet_id: Any,
               skewed: Iterable[str] = (), kept: int = 0, aged: int = 0) -> str:
    """D4's words for one send. Every state is counted; none is implied. A
    hearing outside the join window is QUALIFIED, never shown plain (r5)."""
    if not inbox_heard._valid_pid(packet_id):
        return NO_ID_LINE
    off = set(skewed)
    heard = [a + (" (clock off)" if a in off else "") for a in summary.get("heard", [])]
    could = len(heard) + len(summary.get("not_heard", []))
    parts = ["heard by " + (", ".join(heard) if heard else "none"),
             # "listening" = covered across the send's window (heard or
             # not_heard). NOT "could hear": with no declared segment a box
             # on the other preset is still counted here (moc3, 11-14)
             f"{len(heard)} of {could} listening"]
    blind = len(summary.get("unobservable", []))
    if blind:
        # r1 M2: the gap is SAID where the operator reads, not only in the API
        # r4 NIT-1: never more aged than blind (aged is a subset of blind)
        n = max(0, min(aged, blind))
        parts.append(f"{blind} unobservable" + (
            f" ({n} past the box's rx ring; only hearings are kept)" if n else ""))
    if kept:
        parts.append(f"{kept} kept from an earlier read")
    other = len(summary.get("cannot_hear", []))
    if other:
        # DECLARED, not measured (utils.rf_segments: Linux meshtasticd has no
        # /json/report). A bridged copy reaches the other preset under a NEW
        # packet id, so this id is never expected there.
        parts.append(f"{other} on another preset/slot (declared; a bridged copy carries a new id)")
    return " · ".join(parts)


def load_sections(now: float, *, targets_file: Optional[Path] = None,
                  spool_dir: Optional[Path] = None) -> Optional[Dict[str, Optional[dict]]]:
    """``{alias: rx_heard section or None}`` for every LISTED box, read once
    (an inbox read judges many sends against one fleet snapshot). None = no
    target list (a standalone box). A listed box with no fresh spool or no
    ``rx_heard`` maps to None → ``unobservable``, never dropped."""
    path = targets_file or center.targets_path()
    targets = center.read_targets(path)
    if not targets:
        if _has_content(path):
            # a damaged list is NOT "no fleet" (review r5): every heard line
            # would vanish silently — raise, so the read says why
            raise ValueError(f"target list {path} has no valid line")
        return None
    sections: Dict[str, Optional[dict]] = {}
    for t in targets:
        doc = center.read_spool(t.alias, now=now, spool_dir=spool_dir)
        sec = doc.get("rx_heard") if isinstance(doc, dict) else None
        sections[t.alias] = sec if isinstance(sec, dict) else None
    return sections


def _has_content(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return any(line.split("#", 1)[0].strip() for line in text.splitlines())


def heard_from(packet_id: Any, sent_ts: float, sections: Mapping[str, Optional[dict]], *,
               now: float, segments: Optional[Mapping[str, Optional[str]]] = None,
               send_segment: Optional[str] = None,
               frozen: Optional[Mapping[str, inbox_heard.BoxHeard]] = None) -> Dict[str, Any]:
    """``{"line", "summary", "boxes", "freeze"}`` for one send. ``frozen`` =
    the hearings the store kept; ``freeze`` = the hearings to keep now."""
    results = inbox_heard.judge_send(packet_id, sent_ts, sections, now=now,
                                     segments=segments, send_segment=send_segment)
    results, freeze = inbox_heard.merge_frozen(results, frozen or {})
    summary = summarize_results(results)
    skewed = [r.alias for r in results if r.state == "heard" and r.skewed]
    kept = sum(1 for r in results if r.kept)
    aged = sum(1 for r in results
               if r.state == "unobservable" and r.reason.startswith(inbox_heard.RING_AGED_PREFIX))
    return {"line": heard_line(summary, packet_id=packet_id, skewed=skewed,
                               kept=kept, aged=aged),
            "summary": summary, "boxes": [asdict(r) for r in results],
            "freeze": freeze}


def heard_for_send(packet_id: Any, sent_ts: float, *, now: float,
                   targets_file: Optional[Path] = None,
                   spool_dir: Optional[Path] = None,
                   segments: Optional[Mapping[str, Optional[str]]] = None,
                   send_segment: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """``{"line", "summary", "boxes"}`` for one send, or None with no fleet."""
    sections = load_sections(now, targets_file=targets_file, spool_dir=spool_dir)
    if sections is None:
        return None
    out = heard_from(packet_id, sent_ts, sections, now=now, segments=segments,
                     send_segment=send_segment)
    out.pop("freeze")   # nothing here keeps verdicts; the inbox read does
    return out
