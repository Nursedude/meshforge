#!/usr/bin/env python3
"""DISPATCH allowlisted TUI leaves and prove each one returns to its parent.

The sibling driver ``scripts/tui_smoke.py`` renders every screen through
real whiptail and states plainly that it NEVER dispatches. This is the
other half, kept in a separate file on purpose: the safe tool and the
risky one must not share a binary, so nobody reaches for this one by
accident.

WHAT "RETURNS TO ITS PARENT" MEANS HERE, EXACTLY
------------------------------------------------
``HandlerRegistry.dispatch`` wraps every ``execute()`` in
``TUIContext.safe_call``, which CATCHES the exception and shows an error
dialog. So "it came back" is not the question — a handler that raises
comes back too. This driver requires BOTH:

  * ``execute()`` returned within the timeout, AND
  * it surfaced none of ``safe_call``'s seven error dialogs.

Otherwise a crashing leaf would score as a pass, which is the fail-dark
class this whole arc exists to stop shipping.

DEFAULT-DENY (the operator's call, 2026-09-16)
-----------------------------------------------
An action is dispatched ONLY if it is in ``ALLOWLIST`` below, with a
written reason. Everything else reports **UNPROVEN** and is never
counted as a pass. Unobservable is not healthy.

⚠️ THE ALLOWLIST CANNOT BE BUILT BY A SCANNER. Scanning handler modules
for mutation markers (subprocess, *_service, _sudo_*, file writes)
returns 48 "read-only" actions, and ``meshtasticd_radio`` — which sets
the radio's owner name, presets and hardware config — is INSIDE that
result. It mutates through ``utils.device_config_store``,
``core.meshtasticd_config`` and ``remediation``, every one a
function-local import one or two hops away, across 22 sibling ``_*.py``
handler modules plus utils/, core/, commands/ and remediation/. What a
file CONTAINS says almost nothing about what its action DOES. A checker
that forecasts the defect is the re-check, not the gate. So each entry
below is hand-read, and the reason names what was followed.

The dialog layer is a scripted stand-in, not whiptail: every prompt
answers "cancel/back" and nothing is typed. That is deliberate — this
driver proves CONTROL FLOW, and tui_smoke.py already proves the same
screens PAINT. Neither alone is the whole claim.

⚠️ DEPTH IS ONE LEVEL, and the report says so on every run. Cancelling
the first prompt means a leaf that opens a submenu is proven to open it
and return — NOT that its contents work. Going deeper means dispatching
sub-actions, which is exactly where the mutating ones live, so depth is
earned per tree the same way entry to this allowlist is. Claiming
"every leaf works" off a one-level walk would be the same shape as
claiming a socket that listens means a feature that functions.

Usage:
    python3 scripts/tui_leaf_walk.py              # walk the allowlist
    python3 scripts/tui_leaf_walk.py --list       # what is/is not allowed
    python3 scripts/tui_leaf_walk.py --only TAG

Exit: 0 when every allowlisted leaf returned clean, 1 on any failure,
2 when the driver could not run. UNPROVEN never affects the exit code
in either direction — it is reported, loudly, as the size of the gap.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
TUI = SRC / "launcher_tui"

#: Titles ``TUIContext.safe_call`` uses when it catches an exception.
#: Seeing one of these means the leaf FAILED, however politely it
#: returned afterwards.
SAFE_CALL_ERROR_TITLES = {
    "Module Not Available", "Operation Timed Out", "Permission Denied",
    "File Not Found", "Connection Failed", "Error",
}

#: Actions that may NEVER be dispatched by this driver, on any box, even
#: if someone adds them to ALLOWLIST below. A rule written only in a
#: comment is a rule the next session can talk itself out of; DENYLIST
#: wins over ALLOWLIST in ``main()`` and says so out loud.
#:
#: The criterion is not "risky" — plenty of allowlisted work is risky in
#: the ordinary sense. It is: **the blast radius includes the thing
#: running the drill, or the operator's ability to intervene.** Never arm
#: a guard whose blast radius holds the session arming it.
DENYLIST: dict[tuple[str, str], str] = {
    ("system", "reboot"):
        "Reboot / Shutdown takes down the host running this walk, along "
        "with every service on it and the operator's way in. No allowlist "
        "entry can make that observable — the process that would report "
        "the result is inside the blast radius.",
}

#: (section, tag) -> why dispatching this is safe ON THIS BOX.
#: Hand-read, one at a time, following the call graph to a terminal that
#: provably only reads. STARTS EMPTY AND GROWS BY EVIDENCE.
#:
#: ⚠️ Service control is a SEPARATE judgement from "does it mutate". A box
#: whose declaration says a unit is stopped has been configured that way
#: ON PURPOSE, and restarting it from a drill would overwrite a human
#: decision with a test fixture. Read the box's own declaration
#: (``service_overrides`` in deployment.json) before allowlisting
#: anything that starts or stops a unit — a deliberate absence is not a
#: gap to be filled.
ALLOWLIST: dict[tuple[str, str], str] = {
    # ---- about/ — read 2026-09-16, handlers/about.py in full (234 lines).
    ("about", "version"):
        "reads __version__ and shows a msgbox. No I/O beyond the import.",
    ("about", "changelog"):
        "reads VERSION_HISTORY from __version__.py, formats, prints, waits "
        "for Enter. clear_screen() writes escape codes to the tty and "
        "nothing else.",
    ("about", "sysinfo"):
        "platform.*, /proc/uptime, /proc/meminfo, os.statvfs('/'), a stat() "
        "sweep of the log dir, and ONE subprocess — `lsb_release -ds`, "
        "capture_output, timeout=5, argument list (no shell). Every leg "
        "reads.",
    ("about", "deps"):
        "__import__ of 9 optional modules to report presence + __version__. "
        "Imports only; none of them is constructed (notably RNS: the module "
        "import does not build a Reticulum instance, which is the #68/#69 "
        "hazard).",
    ("about", "help"):
        "prints a static string and waits for Enter.",

    # ---- rf_sdr/ — read 2026-09-16. handlers/rf_tools.py (565 lines) and
    # handlers/site_planner.py (294) contain ZERO subprocess, file-write,
    # network or sqlite calls, and their imports were followed by hand
    # (including the function-local ones, which is where the
    # meshtasticd_radio counterexample hid): utils/rf.py (914 lines),
    # utils/preset_impact.py (531) and utils/antenna_patterns.py (651) are
    # likewise free of every one of those markers. 2,096 lines of RF maths.
    ("rf_sdr", "link"):
        "Link Budget — a menu over FSPL / Fresnel / link-budget calculators "
        "in utils.rf. Arithmetic and a msgbox; the dialog stand-in cancels "
        "the submenu, so it returns immediately.",
    ("rf_sdr", "freq"):
        "Frequency-slot calculator. Pure arithmetic over region tables.",
    ("rf_sdr", "antenna"):
        "Antenna comparison from the static table in utils.antenna_patterns, "
        "reached through safe_import so an absent module degrades in-app "
        "rather than raising.",
    ("rf_sdr", "site"):
        "Site Planner — coverage estimation over utils.rf and "
        "utils.preset_impact. Reads DeployEnvironment/BuildingType tables "
        "and computes; writes nothing.",
}


def _load_registry(profile_name=None):
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(TUI))
    import logging
    logging.disable(logging.INFO)
    from handlers.manifest import HANDLER_MANIFEST
    return HANDLER_MANIFEST


def all_actions():
    """Every (section, tag, label) the TUI can dispatch, from the manifest."""
    out = []
    for h in _load_registry():
        for tag, desc, _flag in h["menu_items"]:
            out.append((h["menu_section"], tag, desc.strip()))
    return sorted(out)


# ------------------------------------------------------------- the child

CHILD = r'''
import json, logging, sys, os
sys.path.insert(0, %(src)r)
sys.path.insert(0, %(tui)r)
logging.disable(logging.CRITICAL)
from types import SimpleNamespace
from handler_protocol import TUIContext
from handler_registry import HandlerRegistry
from handlers import get_all_handlers

section, tag = %(section)r, %(tag)r
seen = {"dialogs": [], "errors": []}

class ScriptedDialog:
    """Answers every prompt with cancel/back and types nothing.

    Records what it was asked so the parent can tell a leaf that chose
    to explain itself from one that safe_call had to rescue.
    """
    def _note(self, kind, title):
        seen["dialogs"].append([kind, title])
        if kind == "msgbox" and title in %(errtitles)r:
            seen["errors"].append(title)
    def set_status_bar(self, *a, **k): return None
    def available(self): return True
    def msgbox(self, title, text, height=None, width=None):
        self._note("msgbox", title); return None
    def infobox(self, title, text): self._note("infobox", title); return None
    def textbox(self, title, text, height=None, width=None):
        self._note("textbox", title); return None
    def editbox(self, title, file_path, height=None, width=None):
        self._note("editbox", title); return None
    def yesno(self, title, text, default_no=False, height=None, width=None):
        self._note("yesno", title); return False          # always decline
    def menu(self, title, text, choices, height=None, width=None,
             list_height=None):
        self._note("menu", title); return None            # always cancel
    def inputbox(self, title, text, init="", height=None, width=None):
        self._note("inputbox", title); return None
    def checklist(self, title, text, choices, height=None, width=None,
                  list_height=None):
        self._note("checklist", title); return None
    def radiolist(self, title, text, choices, height=None, width=None,
                  list_height=None):
        self._note("radiolist", title); return None
    def gauge(self, *a, **k): self._note("gauge", a[0] if a else ""); return None

ctx = TUIContext(dialog=ScriptedDialog())
ctx.wait_for_enter = lambda msg="": None   # never block on stdin
registry = HandlerRegistry(ctx)
for cls in get_all_handlers():
    registry.register(cls())
ctx.registry = registry

owned = registry.dispatch(section, tag)
print("__RESULT__" + json.dumps(
    {"owned": bool(owned), "dialogs": seen["dialogs"],
     "errors": seen["errors"]}))
'''


def walk_one(section, tag, timeout=45):
    src = CHILD % {"src": str(SRC), "tui": str(TUI), "section": section,
                   "tag": tag, "errtitles": sorted(SAFE_CALL_ERROR_TITLES)}
    try:
        p = subprocess.run([sys.executable, "-c", src], cwd=str(REPO),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "note": f"DID NOT RETURN within {timeout}s "
                                     f"— a leaf that hangs is the worst kind "
                                     f"of not-returning to its parent"}
    line = [l for l in p.stdout.splitlines() if l.startswith("__RESULT__")]
    if not line:
        tail = (p.stderr or p.stdout).strip().splitlines()
        return {"ok": False, "note": "child produced no result: "
                                     + (tail[-1] if tail else "no output")}
    r = json.loads(line[0][len("__RESULT__"):])
    if not r["owned"]:
        return {"ok": False, "note": "no handler owns this tag"}
    if r["errors"]:
        return {"ok": False, "note": "safe_call caught an exception and "
                                     f"showed: {', '.join(r['errors'])}"}
    return {"ok": True, "note": f"{len(r['dialogs'])} dialog(s), returned clean"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="show allowed vs unproven and exit")
    ap.add_argument("--only", default=None, help="one tag")
    ap.add_argument("--timeout", type=int, default=45)
    args = ap.parse_args(argv)

    actions = all_actions()
    if args.only:
        actions = [a for a in actions if a[1] == args.only]
        if not actions:
            print(f"UNKNOWN: no action tagged {args.only!r}", file=sys.stderr)
            return 2

    # DENYLIST wins, always — an entry in both is a mistake worth naming
    # rather than silently resolving in the permissive direction.
    both = sorted(set(ALLOWLIST) & set(DENYLIST))
    for s_, t_ in both:
        print(f"REFUSING {s_}/{t_}: it is in DENYLIST and ALLOWLIST both. "
              f"DENYLIST wins. ({DENYLIST[(s_, t_)]})", file=sys.stderr)
    allowed = [(s, t, d) for s, t, d in actions
               if (s, t) in ALLOWLIST and (s, t) not in DENYLIST]
    denied = [(s, t, d) for s, t, d in actions
              if (s, t) not in ALLOWLIST or (s, t) in DENYLIST]

    if args.list:
        print(f"allowlisted ({len(allowed)}):")
        for s, t, d in allowed:
            print(f"  {s}/{t}\n      {ALLOWLIST[(s, t)]}")
        print(f"\nNEVER dispatched by rule ({len(DENYLIST)}):")
        for (s, t), why in sorted(DENYLIST.items()):
            print(f"  {s}/{t}\n      {why}")
        print(f"\nUNPROVEN — not dispatched, not a pass ({len(denied)}):")
        for s, t, d in denied:
            print(f"  {s}/{t:<18} {d[:46]}")
        return 0

    print(f"tui_leaf_walk — dispatching {len(allowed)} of {len(actions)} "
          f"action(s); {len(denied)} UNPROVEN\n")
    failures = 0
    for s, t, _d in allowed:
        r = walk_one(s, t, args.timeout)
        if not r["ok"]:
            failures += 1
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {s}/{t:<18} {r['note']}")

    print(f"\nreturned to parent: {len(allowed) - failures}/{len(allowed)} "
          f"dispatched")
    print(f"UNPROVEN: {len(denied)}/{len(actions)} — never dispatched, and "
          f"NOT counted as passing")
    print("\nDEPTH — what this number does NOT say: the stand-in answers the\n"
          "  FIRST prompt with cancel, so a leaf that opens a submenu is\n"
          "  proven to open it and come back, not that anything INSIDE it\n"
          "  works. A crash behind submenu item 3 is untouched here. Walking\n"
          "  deeper means dispatching sub-actions, which is where the\n"
          "  mutating ones live — so depth is earned per tree, the same way\n"
          "  this allowlist is, and is not yet claimed for any of them.")
    if failures:
        print(f"\nFAIL — {failures} allowlisted leaf/leaves did not come back "
              f"clean")
        return 1
    if not allowed:
        print("\nUNKNOWN: the allowlist is empty — nothing was proven. "
              "That is the honest state, not a pass.")
        return 2
    print("\nPASS — every allowlisted leaf returned to its parent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
