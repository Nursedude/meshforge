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
  * no target list at all (a standalone box, no fleet) → ``None``: the page
    keeps its single-box text instead of claiming a fleet it does not have;
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


def summarize_results(results: Iterable[inbox_heard.BoxHeard]) -> Dict[str, List[str]]:
    return inbox_heard.summarize(results)


def heard_line(summary: Mapping[str, List[str]], *, packet_id: Any) -> str:
    """D4's words for one send. Every state is counted; none is implied."""
    if not inbox_heard._valid_pid(packet_id):
        return NO_ID_LINE
    heard = list(summary.get("heard", []))
    could = len(heard) + len(summary.get("not_heard", []))
    parts = ["heard by " + (", ".join(heard) if heard else "none"),
             # "listening" = covered across the send's window (heard or
             # not_heard). NOT "could hear": with no declared segment a box
             # on the other preset is still counted here (moc3, 11-14)
             f"{len(heard)} of {could} listening"]
    blind = len(summary.get("unobservable", []))
    if blind:
        parts.append(f"{blind} unobservable")
    other = len(summary.get("cannot_hear", []))
    if other:
        parts.append(f"{other} on another preset")
    return " · ".join(parts)


def heard_for_send(packet_id: Any, sent_ts: float, *, now: float,
                   targets_file: Optional[Path] = None,
                   spool_dir: Optional[Path] = None,
                   segments: Optional[Mapping[str, Optional[str]]] = None,
                   send_segment: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """``{"line", "summary", "boxes"}`` for one send, or None with no fleet."""
    targets = center.read_targets(targets_file or center.targets_path())
    if not targets:
        return None
    sections: Dict[str, Any] = {}
    for t in targets:
        doc = center.read_spool(t.alias, now=now, spool_dir=spool_dir)
        sec = doc.get("rx_heard") if isinstance(doc, dict) else None
        sections[t.alias] = sec if isinstance(sec, dict) else None
    results = inbox_heard.judge_send(packet_id, sent_ts, sections, now=now,
                                     segments=segments, send_segment=send_segment)
    summary = summarize_results(results)
    return {"line": heard_line(summary, packet_id=packet_id), "summary": summary,
            "boxes": [asdict(r) for r in results]}
