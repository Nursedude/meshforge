"""Platform Posture Handler — the update SURFACE inside the TUI.

Answers two questions that previously cost a session each:

  * is this box's OS base intended, deliberately different, or drifting?
  * for every pinned dependency: what do we hold, why, and what would
    change the answer?

⚠️ READ-ONLY, DELIBERATELY. This handler renders; it never upgrades,
installs, holds, reboots, or touches a service. Acting stays in scripts the
operator runs knowingly. The reason is the 2026-07-24 incident: a sweep that
restarted "every installed unit" started a service that was off BY DESIGN.
An update path with fleet-wide blast radius does not belong behind a menu
item, and putting one here later should be argued for on its own merits, not
inherited from this screen's existence.

The three-way verdict is the point (see src/utils/fleet_platform.py):
``inert`` = deviates by DECISION, ``drift`` = deviates and nobody decided,
``unknown`` = we could not tell. A surface that nags about a settled decision
teaches its reader to skip it.
"""

import logging
import sys
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class PlatformPostureHandler(BaseHandler):
    """Renders declared-vs-actual platform state and the pin ledger."""

    handler_id = "platform_posture"
    menu_section = "system"

    def menu_items(self):
        return [
            ("platform_posture", "Platform Posture    OS base vs declared, per box", None),
            ("platform_pins", "Dependency Pins     what we hold and why", None),
        ]

    def execute(self, action):
        if action == "platform_posture":
            self._show_posture()
        elif action == "platform_pins":
            self._show_pins()

    # -- helpers ----------------------------------------------------------

    def _load(self):
        from utils.fleet_platform import (
            load_catalog, load_declarations,
        )
        catalog, errs = load_catalog()
        if catalog is None:
            self.ctx.dialog.msgbox(
                "Platform Posture",
                "The platform catalog could not be read, so nothing can be "
                "judged against it:\n\n" + "\n".join(errs) +
                "\n\nExpected at docs/fleet_platform.yaml.")
            return None, None, None
        decls, status = load_declarations()
        return catalog, decls, status

    def _show_posture(self):
        catalog, decls, status = self._load()
        if catalog is None:
            return
        from utils.fleet_platform import (
            DRIFT, INERT, OK, UNKNOWN, judge_box, summarize,
        )

        # Local box only: the TUI runs on ONE box and must be useful in the
        # standalone offering, where there is no fleet to fan out to. The
        # fleet-wide pane is `scripts/fleet_platform.py show`, which is a
        # deliberate command rather than a menu keystroke that ssh-fans to
        # ten boxes while someone is just browsing.
        import os
        import platform as _platform
        base = None
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VERSION_CODENAME="):
                        base = line.split("=", 1)[1].strip().strip('"')
        except OSError as e:
            logger.warning("could not read /etc/os-release: %s", e)

        box = os.uname().nodename
        v = judge_box(box, base, catalog, decls,
                      actual_python=_platform.python_version(),
                      decl_status=status,
                      unobserved_why="/etc/os-release unreadable on this box")
        s = summarize([v])

        glyph = {OK: "OK", INERT: "INERT (deliberate)", DRIFT: "DRIFT",
                 UNKNOWN: "UNKNOWN"}[v.verdict]
        lines = [
            f"Box:      {box}",
            f"Base:     {v.actual_base or '?'}   python {v.actual_python or '?'}",
            f"Target:   {', '.join(catalog.target_bases())}",
            f"Verdict:  {glyph}",
            "",
            v.detail,
        ]
        if v.stale_declaration:
            lines += ["",
                      "The deviation is still deliberate, but the DECISION has "
                      "not been re-affirmed in a long time. An indefinite "
                      "declaration is how a call quietly becomes its own "
                      "warrant."]
        if status in ("unreadable", "invalid"):
            lines += ["",
                      f"WARNING: the declarations file is {status}, so a "
                      f"deliberate deviation cannot be told apart from drift. "
                      f"That is why this reads UNKNOWN rather than guessing."]
        if v.verdict == UNKNOWN:
            lines += ["", "UNKNOWN is not a pass."]
        lines += [
            "",
            "Fleet-wide:  scripts/fleet_platform.py show",
            "Declare:     scripts/fleet_platform.py declare <box> <base> --reason ...",
            "",
            "This screen never changes anything.",
        ]
        self.ctx.dialog.msgbox("Platform Posture", "\n".join(lines))

    def _show_pins(self):
        catalog, _decls, _status = self._load()
        if catalog is None:
            return
        if not catalog.pins:
            self.ctx.dialog.msgbox(
                "Dependency Pins",
                "The catalog declares no pins.\n\nThat is a claim, not an "
                "absence of holds — if something IS held on this box, it is "
                "held without a recorded reason.")
            return

        out = []
        for name, pin in sorted(catalog.pins.items()):
            watched = ("watched: a predicate can tell us when to look again"
                       if pin.self_monitoring
                       else "NOT WATCHED: re-evaluating this is a human act")
            out.append(f"── {name}  [{pin.mechanism}]  @ {pin.current}")
            if pin.verified_at:
                out.append(f"   verified: {pin.verified_at}")
            out.append(f"   {watched}")
            out.append(f"   why: {pin.why}")
            if pin.blast_radius:
                out.append(f"   if wrong: {pin.blast_radius}")
            for r in pin.recheck:
                out.append(f"   recheck [{r.kind}]: {r.means}")
            out.append("")
        out.append("Detail: scripts/fleet_platform.py pins")
        self.ctx.dialog.msgbox("Dependency Pins", "\n".join(out))
