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
            ("platform_updates", "Update Readiness    pending, reboot owed, holds", None),
        ]

    def execute(self, action):
        if action == "platform_posture":
            self._show_posture()
        elif action == "platform_pins":
            self._show_pins()
        elif action == "platform_updates":
            self._show_updates()

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

        # ── the ACTUAL side (2026-09-19) ──────────────────────────────────
        # This ledger used to record only what we DECLARE. A declared hold
        # that is not in force still rendered as authoritative, which is the
        # silent failure this block closes: the apt_hold is the only guard
        # against a published build regressing the USB boxes, and several
        # boxes carry a third-party repo at priority 500.
        from utils.fleet_platform import (
            HOLD_HELD, HOLD_INERT, HOLD_NOT_HELD, HOLD_UNDECLARED,
            HOLD_UNKNOWN, holds_worst_state, reconcile_holds,
        )

        actual = self._holds()
        wanted = [n for n, p in catalog.pins.items()
                  if getattr(p, "mechanism", "") == "apt_hold"]
        installed = self._installed_packages(wanted) if wanted else []
        rows = reconcile_holds(catalog.pins, actual, installed)
        worst = holds_worst_state(rows)

        if worst == HOLD_NOT_HELD:
            out += ["!! A DECLARED HOLD IS NOT IN FORCE ON THIS BOX !!",
                    "   The next upgrade can move that package.", ""]
        elif worst == HOLD_UNKNOWN:
            out += ["Hold state UNKNOWN — apt could not be read.",
                    "UNKNOWN is not a pass.", ""]

        if rows:
            label = {HOLD_HELD: "HELD", HOLD_NOT_HELD: "NOT HELD",
                     HOLD_INERT: "inert", HOLD_UNKNOWN: "UNKNOWN",
                     HOLD_UNDECLARED: "UNDECLARED"}
            out.append("Holds on this box (declared vs actual):")
            for name, state, detail in rows:
                out.append(f"  {name:<16} {label[state]:<11} {detail}")
            out.append("")

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

    # -- update readiness -------------------------------------------------
    # Added 2026-09-19 after a fleet OS roll. The pane already answered "is
    # this box's BASE intended"; it could not answer "is this box CURRENT,
    # and does it owe a reboot" -- and that gap cost real time the same day:
    # a session read a kernel_reboot_pending entry out of mini's RECENT-FIRES
    # list and reported a reboot owed on a box that had already taken it 13
    # minutes after the fire. The standing fact existed nowhere renderable,
    # so the only available answer was an event that had already resolved.
    #
    # Still read-only. This renders what the box already knows; upgrading
    # stays in a script the operator runs knowingly (see the module docstring
    # -- a fleet-wide apt path behind a menu keystroke is the 2026-07-24
    # shape, and nothing here should inherit that by proximity).

    def _probe(self, argv, timeout=20):
        """Run a read-only probe. Returns (rc, stdout) or (None, "") if it
        could not run at all. None means UNOBSERVED, never 'fine'."""
        import subprocess
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, check=False)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("probe %s failed: %s", argv[0], e)
            return None, ""

    def _installed_kernels(self):
        rc, out = self._probe(
            ["dpkg-query", "-W", "-f", "${Package}\t${Status}\n", "linux-image-*"])
        if rc is None:
            return None
        rels = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 2 or "installed" not in parts[1]:
                continue
            name = parts[0]
            rel = name[len("linux-image-"):] if name.startswith("linux-image-") else ""
            # meta-packages (linux-image-rpi-2712) carry no numeric head
            if rel[:1].isdigit():
                rels.append(rel)
        return rels

    def _pending_count(self):
        rc, out = self._probe(["apt-get", "-s", "upgrade"], timeout=60)
        if rc is None or rc != 0:
            return None
        return sum(1 for ln in out.splitlines() if ln.startswith("Inst "))

    def _installed_packages(self, names):
        """Which of ``names`` are installed here. None = could not look."""
        if not names:
            return []
        rc, out = self._probe(
            ["dpkg-query", "-W", "-f", "${Package} ${Status}\n"] + list(names))
        if rc is None:
            return None
        found = []
        for line in out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and "install ok installed" in parts[1]:
                found.append(parts[0])
        return found

    def _holds(self):
        rc, out = self._probe(["apt-mark", "showhold"])
        if rc is None or rc != 0:
            return None
        return [x.strip() for x in out.split() if x.strip()]

    def _show_updates(self):
        import os
        import time as _time
        from utils.fleet_platform import reboot_owed

        box = os.uname().nodename
        running = os.uname().release

        try:
            flag = os.path.exists("/var/run/reboot-required")
        except OSError as e:
            logger.warning("reboot-required unreadable: %s", e)
            flag = None

        kernels = self._installed_kernels()
        owed, why = reboot_owed(reboot_required_flag=flag,
                                running_kernel=running,
                                installed_kernels=kernels)
        verdict = {True: "OWED", False: "not owed", None: "UNKNOWN"}[owed]

        pending = self._pending_count()
        holds = self._holds()

        try:
            age_s = _time.time() - os.stat("/var/lib/apt/lists").st_mtime
            age = f"{int(age_s // 3600)}h ago" if age_s >= 3600 else "under an hour ago"
        except OSError:
            age = None

        lines = [
            f"Box:      {box}",
            f"Kernel:   {running}",
            "",
            f"Reboot:   {verdict}",
            f"          {why}",
            "",
        ]
        if pending is None:
            lines.append("Pending:  UNKNOWN - the upgrade simulation could not be run.")
            lines.append("          That is not the same as 'nothing pending'.")
        else:
            lines.append(f"Pending:  {pending} package(s) upgradable")
        lines.append(f"Lists:    {'refreshed ' + age if age else 'age UNKNOWN'}")
        if age is None or (pending is not None and pending == 0 and age is None):
            lines.append("          A count read from stale lists is not a current count.")

        if holds is None:
            lines.append("Holds:    UNKNOWN - could not read apt-mark.")
        elif holds:
            lines.append(f"Holds:    {', '.join(sorted(holds))}")
            lines.append("          A hold is a DECISION. Why each one exists is on")
            lines.append("          the Dependency Pins screen; removing one to")
            lines.append("          'unblock' an upgrade is how an unintended build")
            lines.append("          lands fleet-wide.")
        else:
            lines.append("Holds:    none on this box")

        if owed is None:
            lines += ["", "UNKNOWN is not a pass."]

        lines += [
            "",
            "Fleet-wide:  scripts/fleet_platform.py show",
            "",
            "This screen never changes anything.",
        ]
        self.ctx.dialog.msgbox("Update Readiness", "\n".join(lines))
