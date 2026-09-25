"""Live-truth ledger — which TUI actions were ever checked against a LIVE answer.

Born 2026-09-25 from the operator's question about the TUI audit — "going
through each function/feature making sure it's wired and working as it's
supposed to". The truth sweep (`action_truth.py`) proves every action ROUTES
and tells the truth when every external is DEAD. It does not prove a screen is
RIGHT when the externals are alive: Node Health read rnsd DOWN on every
healthy box for months, and no sweep could see it (level-two text is not
gated). This table is the record of which actions a human or a session
actually checked against the real system, when, how far, and on what evidence.

DATA, not behaviour. Rules for an entry (pinned by
`tests/test_live_truth_ledger.py`):
  * the key is a LIVE (section, tag) — a retired action fails the test;
  * `date` is ISO; `box` names where it was observed;
  * `scope` says exactly what was checked — "banner only" is an honest entry,
    a bare "works" is not;
  * `evidence` points at something another reader can re-check (a commit, a
    provenance line, a quoted output), never "I looked at it".

An action with NO entry has never been checked live. That is the honest
default, not a failure; the rendered Live column in the capability index
(`scripts/gen_capability_index.py`) shows which rows are still blank.
Entries age: a screen verified before its code changed is history, so
re-verify after substantive edits and update the date.
"""
from __future__ import annotations

from typing import Dict, Tuple

Action = Tuple[str, str]   # (menu_section, action_tag)

LIVE_VERIFIED: Dict[Action, Dict[str, str]] = {
    ("rf_sdr", "sdr_watch"): {
        "date": "2026-09-25",
        "box": "moc5",
        "scope": "view rendered from moc5's REAL interference.jsonl through the real "
                 "sdr_view code (witness freshness, per-window classes, class D, blind "
                 "spots); the handler itself not launched inside a live TUI session",
        "evidence": "render at 00:04 HST on 4c7866c5 quoted in session; provenance touch "
                    "line 2026-09-24 23:46:36 (moc5)",
    },
    ("rf_sdr", "sdr"): {
        "date": "2026-09-24",
        "box": "moc5",
        "scope": "MOCK-mode banner only (reason + USB bus line: 'SoapySDR is not installed "
                 "… An Airspy IS attached'); the monitor's capture screens NOT verified",
        "evidence": "commit 6f5249f7; banner rendered on moc5 at 21:1x HST on 14c565e6",
    },
}


def live_class(section: str, tag: str) -> str:
    """The Live-column cell: blank when never checked live."""
    e = LIVE_VERIFIED.get((section, tag))
    if not e:
        return ""
    partial = any(w in e["scope"].lower() for w in ("only", "not verified", "not launched"))
    return f"{'◐' if partial else '✓'} {e['date']} {e['box']}"
