"""Fleet Posture — the DECLARED per-box state, and the front door to declare it.

Roadmap 6c P3 (operator, 2026-10-05). Reads ``utils.fleet_posture`` (active /
shed / dormant / detached, with reason, since/until, who) and — on the manager
box only — declares, powers off, or resumes boxes by running
``scripts/fleet_power.py``. There is NO second implementation here: every write
is that tool's own dry run, shown first, then the same command with
``--apply`` after the operator TYPES the box name(s).

⚠️ Doctrine: the TUI stays a surface (09-11). The operator amended it on
2026-10-05 for THIS action only — declare + power off, via fleet_power.py,
typed confirm. That amendment is not a precedent for any other writing
handler; each needs its own justification.

Where acting is offered: only where the SSOT lives — the posture file is NOT a
mirror, it reads cleanly, and the fleet dependency graph
(``fleet_offline_boxes.json``, which fleet_power.py needs for ordering) exists.
Everywhere else the screen is read-only and says why.

Not to be confused with **Fleet Watchers** (``fleet_watchers``): that is the
mini-daemon rollup, which took this name's place until 2026-09-15 precisely so
"posture" could mean the declaration.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from handler_protocol import BaseHandler
from utils import fleet_posture as fp
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[3]
FLEET_POWER = str(_REPO / "scripts" / "fleet_power.py")

#: The dependency graph fleet_power.py orders by — its presence is what makes
#: a box the place declarations are made (same env override as the tool).
def graph_path() -> str:
    return os.environ.get(
        "MESHFORGE_OFFLINE_BOXES",
        str(get_real_user_home() / ".config" / "meshforge" / "fleet_offline_boxes.json"),
    )


REASON_MAX = 120
#: Per-box seconds the tool may spend on a power-off (settle 60 s + ssh).
_DOWN_PER_BOX_S = 120
RESUME_WAITS = (("300", "5 min"), ("1800", "30 min"), ("60", "1 min — box already back"))


# --- pure helpers (tested) -----------------------------------------------------

def kind_choices() -> List[str]:
    return list(fp.KINDS)


def clean_reason(text: Optional[str]) -> str:
    """One printable line, bounded — it lands in a JSON file and in pages."""
    s = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return " ".join(s.split())[:REASON_MAX]


def confirm_matches(typed: Optional[str], boxes: Sequence[str]) -> bool:
    """Exact, case-sensitive SET match of every target name. Anything else —
    'yes', a prefix, an extra name — is a refusal."""
    if not typed or not boxes:
        return False
    got = typed.replace(",", " ").split()
    return len(got) == len(set(got)) and set(got) == set(boxes)


def build_down_cmd(boxes: Sequence[str], kind: str, reason: str,
                   declare_only: bool, apply: bool) -> List[str]:
    if kind not in fp.KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    cmd = ["python3", FLEET_POWER, "down", *boxes, "--kind", kind,
           "--reason", clean_reason(reason)]
    if declare_only:
        cmd.append("--declare-only")
    if apply:
        cmd.append("--apply")
    return cmd


def build_resume_cmd(boxes: Sequence[str], wait: int, apply: bool) -> List[str]:
    cmd = ["python3", FLEET_POWER, "resume", *boxes, "--wait", str(int(wait))]
    if apply:
        cmd.append("--apply")
    return cmd


def as_operator(cmd: List[str], euid: Optional[int] = None,
                sudo_user: Optional[str] = None) -> Tuple[Optional[List[str]], Optional[str]]:
    """Fleet ssh needs the operator's keys and home. Under sudo, drop to the
    invoking user; as plain root, REFUSE (None) — root has no fleet keys, so
    every box would read unreachable and a power-off would 'fail' after the
    declaration landed."""
    euid = os.geteuid() if euid is None else euid
    sudo_user = os.environ.get("SUDO_USER") if sudo_user is None else sudo_user
    if euid == 0 and sudo_user:
        return (["sudo", "-n", "-u", sudo_user, "-H"] + cmd,
                f"(runs as {sudo_user}: fleet ssh keys + posture file)")
    if euid == 0:
        return None, ("plain root (no SUDO_USER): root has no fleet ssh keys — "
                      "launch via scripts/meshforge-launcher.sh as your user")
    return cmd, None


def actable(p: fp.Posture, graph: str) -> Tuple[bool, str]:
    if p.is_mirror:
        return False, (f"read-only: this is a MIRRORED copy from "
                       f"{p.mirror_from or 'the manager'} — declare there")
    if p.status in (fp.UNREADABLE, fp.INVALID):
        return False, f"read-only: posture file {p.status.upper()} — {p.detail}"
    if not os.path.isfile(graph):
        return False, ("read-only: no fleet dependency graph here "
                       f"({graph}) — this is not the manager box")
    return True, ""


def _ts(epoch: Optional[float]) -> str:
    if not epoch:
        return "-"
    local = fp.fmt_local(epoch)
    return f"{fp.fmt_ts(epoch)} ({local})" if local else fp.fmt_ts(epoch)


def _rel(epoch: Optional[float], now: float) -> str:
    return fp.fmt_rel(epoch, now)


def render_posture(p: fp.Posture, now: Optional[float] = None) -> List[str]:
    now = time.time() if now is None else now
    # The reader's status names describe the FILE, not the boxes: DECLARED
    # means "read and valid", which beside an empty file read as a
    # contradiction ("status : declared" / "Nothing declared", 10-05).
    status = {
        fp.UNDECLARED: "no posture file",
        fp.DECLARED: f"file valid, {len(p.boxes)} boxes declared",
    }.get(p.status, p.status.upper())
    lines = [f"file   : {p.path}", f"status : {status}"
             + (f"   (MIRROR from {p.mirror_from or '?'})" if p.is_mirror else "")]
    if p.status in (fp.UNREADABLE, fp.INVALID):
        lines += ["", f"⚠ {p.status.upper()}: {p.detail}",
                  "  Consumers treat EVERY box as active (watch everything) "
                  "until this is fixed."]
        return lines
    if p.status == fp.UNDECLARED or not p.boxes:
        lines += ["", "Nothing declared — every box is expected ACTIVE and is paged as such."]
        return lines
    if p.declared_at or p.declared_by:
        lines.append(f"last   : {_ts(p.declared_at)} by {p.declared_by or '?'}")
    if p.clock_note:
        lines.append(f"clock  : {p.clock_note}")
    lines.append("")
    for name in sorted(p.boxes):
        b = p.boxes[name]
        tag = ""
        if b.expired:
            tag = f"  EXPIRED (was {b.declared_state}) — watched as active"
        elif b.held:
            tag = "  HELD past until"
        lines.append(f"{name:<18} {b.state:<9}{tag}")
        if b.reason:
            lines.append(f"    reason : {b.reason}")
        lines.append(f"    since  : {_ts(b.since)} {_rel(b.since, now)}")
        if b.until:
            lines.append(f"    until  : {_ts(b.until)} {_rel(b.until, now)}")
        if b.services:
            lines.append(f"    shed to: {', '.join(b.services)}")
        if b.note:
            lines.append(f"    note   : {b.note}")
    lines += ["", "dormant  = OFF; answering pages POSTURE-DRIFT.",
              "detached = up or down off our net; answering reads REJOINED."]
    return lines


# --- handler ----------------------------------------------------------------------

class FleetPostureHandler(BaseHandler):
    """Fleet Posture — declared state per box; declare / power off / resume."""

    handler_id = "fleet_posture"
    menu_section = "fleet"

    def menu_items(self):
        return [
            (
                "fleet_posture",
                "Fleet Posture       Declared state; declare/power off/resume",
                None,
            ),
        ]

    def execute(self, action):
        if action == "fleet_posture":
            self.ctx.safe_call("Fleet Posture", self._menu)

    # -- menu ------------------------------------------------------------------

    def _menu(self) -> None:
        while True:
            p = fp.read_posture()
            ok, why = actable(p, graph_path())
            silent = p.silent_boxes()
            head = (f"{len(p.boxes)} declared, {len(silent)} silent"
                    if p.boxes else "nothing declared — every box active")
            choices = [("view", "View declarations")]
            if ok:
                choices += [
                    ("declare", "Declare only        box already gone / pull power by hand"),
                    ("down", "Declare + power off  via fleet_power.py, in dependency order"),
                    ("resume", "Resume              clear boxes as they answer again"),
                ]
            text = f"{head}\n{why}" if why else head
            sel = self.ctx.dialog.menu("Fleet Posture", text, choices)
            if sel is None:
                return
            if sel == "view":
                self.ctx.dialog.textbox("Fleet Posture", "\n".join(render_posture(p)))
            elif sel in ("declare", "down"):
                self._down_flow(declare_only=(sel == "declare"))
            elif sel == "resume":
                self._resume_flow(p)

    # -- subprocess seams --------------------------------------------------------

    def _capture(self, cmd: List[str], timeout: int = 60) -> Tuple[int, str]:
        run, note = as_operator(cmd)
        if run is None:
            return 2, note or ""
        try:
            r = subprocess.run(run, capture_output=True, text=True, timeout=timeout)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            return r.returncode, out
        except subprocess.TimeoutExpired:
            return 124, f"[timed out after {timeout}s]"
        except (subprocess.SubprocessError, OSError) as e:
            return 2, f"[could not run: {e}]"

    def _stream(self, cmd: List[str], timeout: int) -> int:
        """Run with the terminal attached: a power-off takes minutes, and the
        operator must SEE each box go dark, not stare at a frozen dialog."""
        from backend import clear_screen
        run, note = as_operator(cmd)
        if run is None:
            self.ctx.dialog.msgbox("Fleet Posture", note or "cannot run")
            return 2
        clear_screen()
        if note:
            print(note)
        try:
            rc = subprocess.run(run, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            print(f"\n[timed out after {timeout}s — check `fleet_power.py resume` "
                  "and the posture file before acting again]")
            rc = 124
        except (subprocess.SubprocessError, OSError) as e:
            print(f"\n[could not run: {e}]")
            rc = 2
        try:
            self.ctx.wait_for_enter("\nPress Enter to continue...")
        except KeyboardInterrupt:
            print()
        return rc

    # -- flows ---------------------------------------------------------------------

    def _pick_members(self, title: str) -> Optional[List[str]]:
        rc, out = self._capture(["python3", FLEET_POWER, "members"], timeout=30)
        names = [ln.strip() for ln in out.splitlines() if ln.strip() and rc == 0]
        if rc != 0 or not names:
            self.ctx.dialog.msgbox(title, f"Could not list fleet members (rc={rc}).\n\n{out}")
            return None
        sel = self.ctx.dialog.checklist(title, "Select box(es) (space toggles):",
                                        [(n, "", False) for n in names])
        return sel or None

    def _down_flow(self, declare_only: bool) -> None:
        title = "Declare only" if declare_only else "Declare + power off"
        boxes = self._pick_members(title)
        if not boxes:
            return
        kind = self.ctx.dialog.menu(
            title, "Why is it going quiet?",
            [(k, f"{st:<9} default {until}") for k, (st, until) in fp.KINDS.items()])
        if not kind:
            return
        reason = clean_reason(self.ctx.dialog.inputbox(
            title, "Free-text reason (optional, shows in pages):", ""))
        dry = build_down_cmd(boxes, kind, reason, declare_only, apply=False)
        rc, out = self._capture(dry, timeout=60)
        self.ctx.dialog.textbox(f"{title} — dry run (rc={rc})", out or "(no output)")
        if rc != 0:
            self.ctx.dialog.msgbox(title, "The dry run refused — nothing changed.")
            return
        timeout = 300 + (0 if declare_only else _DOWN_PER_BOX_S * len(boxes))
        self._confirm_and_apply(title, boxes,
                                build_down_cmd(boxes, kind, reason, declare_only, apply=True),
                                timeout)

    def _resume_flow(self, p: fp.Posture) -> None:
        declared = sorted(p.boxes)
        if not declared:
            self.ctx.dialog.msgbox("Resume", "Nothing is declared — nothing to resume.")
            return
        boxes = self.ctx.dialog.checklist(
            "Resume", "Clear which box(es) as they answer?",
            [(n, p.boxes[n].state, False) for n in declared])
        if not boxes:
            return
        wait = self.ctx.dialog.menu("Resume", "Keep watching for how long?", list(RESUME_WAITS))
        if not wait:
            return
        rc, out = self._capture(build_resume_cmd(boxes, int(wait), apply=False))
        self.ctx.dialog.textbox(f"Resume — dry run (rc={rc})", out or "(no output)")
        if rc != 0:
            self.ctx.dialog.msgbox("Resume", "The dry run refused — nothing changed.")
            return
        self._confirm_and_apply("Resume", boxes,
                                build_resume_cmd(boxes, int(wait), apply=True),
                                int(wait) + 300)

    def _confirm_and_apply(self, title: str, boxes: Sequence[str],
                           cmd: List[str], timeout: int) -> bool:
        typed = self.ctx.dialog.inputbox(
            f"{title} — confirm",
            "Type the box name(s) exactly to proceed:\n  " + " ".join(boxes), "")
        if not confirm_matches(typed, boxes):
            self.ctx.dialog.msgbox(title, "Not confirmed — nothing changed.")
            return False
        logger.info("fleet_posture %s: boxes=%s cmd=%s", title, ",".join(boxes), cmd[2:])
        rc = self._stream(cmd, timeout)
        self.ctx.report_action(
            rc == 0, title, f"fleet_power.py exited 0 for: {' '.join(boxes)}",
            f"{title} — rc={rc}",
            f"fleet_power.py exited {rc}. Read its output above; re-open View "
            "to see what is declared NOW (a partial run may have landed).")
        return True
