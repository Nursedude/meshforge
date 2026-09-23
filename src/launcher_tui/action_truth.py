"""Success-truth classification of every TUI action — DATA, not behaviour.

Born 2026-09-22 from the operator's close-of-session question: *"have we
checked every single item in the TUI?"* The measured answer was NO — every
review row was a commit-RANGE review; the 116-action surface had never been
walked as a whole, and the one test that touched all of it
(`test_all_tags_dispatch`, since retired into the sweep) proved ROUTING only: dispatch returned True. A
screen that says "no alerts" with the network dead passes that test.

`tests/test_tui_success_truth_sweep.py` closes the gap: it dispatches EVERY
registered action with every external DEAD (sockets refused, subprocess
absent, no tools on PATH, the operator's home swapped for an empty one, box state — /etc/reticulum,
/etc/meshtasticd, unit files, /proc/net, device nodes — absent) and
requires the FIRST SCREEN — every dialog including infobox, plus stdout —
to carry a word of uncertainty — UNKNOWN / unreachable / not installed /
failed … — or to be listed here as an action that never asks an external
in the first place. Scope is TWO dialog levels: every action's first
screen, and every item of its first menu (level two gates crashes, hangs,
status rows and the home/box-state witnesses — not text). New actions are covered by construction (honest_failure_modes #7: a
closed enum needs closed consumers), so the audit TERMINATES instead of
recurring every session.

Two tables, both closed:

* ``LOCAL_ONLY`` — actions whose screen is a pure function of the box's own
  files/CPU (RF maths, about/version, viewing a local config). They are
  allowed to render with no uncertainty word because they had nothing to be
  uncertain about. Each entry names WHY; an entry without a why is a lie
  waiting to age.
* ``KNOWN_FALSE_OK`` — the FROZEN BASELINE of actions that DO consult an
  external and still rendered a confident screen with everything dead.
  These are findings, listed so they are legible and cannot grow: the sweep
  fails if a NEW action lands here, and it fails the other way when a
  listed action starts telling the truth ("remove it from the baseline").
  The baseline only shrinks (the MF025 pattern).

`scripts/gen_capability_index.py` renders these as the **Truth** column of
the capability index, so "was this checked?" is a grep, not archaeology.
"""
from __future__ import annotations

from typing import Dict, Tuple

Action = Tuple[str, str]  # (menu_section, action_tag)

#: (section, tag) -> why this action needs no external and so may render
#: without an uncertainty word. Keep the why short and checkable.
LOCAL_ONLY: Dict[Action, str] = {
    ("about", "version"): "static text: version string, feature list, licence",
    ("about", "sysinfo"): "reads /proc, os.uname, disk usage of THIS box — no external",
    ("system", "platform_posture"): "compares /etc/os-release + python to the "
                                    "declared target in a local file; 'never changes anything'",
    ("about", "help"): "static keyboard-shortcut and documentation text",
    ("about", "deps"): "importlib probes of THIS interpreter's packages — [OK] means "
                       "'imports here', which is local truth, not a service claim",
}

#: (section, tag) -> the false-OK text it rendered, dated. FROZEN: add
#: nothing here without a provenance row; remove an entry the day its
#: action starts saying UNKNOWN.
KNOWN_FALSE_OK: Dict[Action, str] = {}

#: (section, tag) -> the exception that escaped the handler into safe_call
#: with every external dead, dated. safe_call's dialog is honest; the
#: handler did not handle its own failure (honest_failure_modes #1).
#: FROZEN like KNOWN_FALSE_OK: a NEW crash fails the sweep; remove an entry
#: the day its handler catches the failure itself.
KNOWN_CRASHED: Dict[Action, str] = {
    ("system", "status"): "2026-09-22: 'Quick Status' — FileNotFoundError from a "
                          "subprocess escapes to safe_call ('File Not Found' dialog)",
}


_NO_TOOL = "FileNotFoundError from a subprocess escapes to safe_call ('File Not Found' dialog)"

#: (section, tag, item) -> the exception that escaped a LEVEL-TWO item (one
#: menu below the action) into safe_call with every external dead. Same
#: contract as KNOWN_CRASHED: frozen, shrink-only, a NEW crash fails the
#: sweep. First walk 2026-09-22. safe_call's dialog is honest in each case —
#: these are handlers that let the failure escape rather than lies; the
#: latency pair is deliberately NOT "fixed" by returning unreachable, since
#: a socket that could not be created observed nothing (hfm #1).
KNOWN_CRASHED_L2: Dict[Tuple[str, str, str], str] = {
    ("dashboard", "health", "latency"): "2026-09-22: probe_tcp creates its socket outside "
                                        "the try; OSError escapes",
    ("dashboard", "latency", "probe"): "2026-09-22: same probe_tcp OSError",
    ("dashboard", "reports", "generate"): f"2026-09-22: {_NO_TOOL}",
    ("dashboard", "reports", "save"): f"2026-09-22: {_NO_TOOL}",
    ("fleet", "fleet_backup", "setup"): f"2026-09-22: {_NO_TOOL}",
    ("system", "discover", "full"): f"2026-09-22: {_NO_TOOL}",
    ("system", "hardware", "detect"): f"2026-09-22: {_NO_TOOL}",
    ("system", "logs", "live-all"): f"2026-09-22: {_NO_TOOL}",
    ("system", "logs", "live-mesh"): f"2026-09-22: {_NO_TOOL}",
    ("system", "logs", "live-rns"): f"2026-09-22: {_NO_TOOL}",
}


def truth_class(section: str, tag: str) -> str:
    """The Truth-column value for one action.

    ``local-only`` — listed in LOCAL_ONLY; ``⚠️ false-ok`` / ``⚠️ crashes``
    — in a frozen baseline; ``sweep`` — proven by the dead-externals sweep on
    every commit.
    """
    key = (section, tag)
    if key in KNOWN_FALSE_OK:
        return "⚠️ false-ok"
    if key in KNOWN_CRASHED:
        return "⚠️ crashes"
    if key in LOCAL_ONLY:
        return "local-only"
    return "sweep"
