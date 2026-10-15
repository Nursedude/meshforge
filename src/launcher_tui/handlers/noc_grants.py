"""NOC Read Grants — who may read this box, and what this box reads.

NOC read grant session 3 step 5 (2026-10-15). The TUI face of
``scripts/box_truth_grant.py``. This is the SECOND acting carve-out (see
fleet_posture.py's doctrine, granted 2026-10-13 Q8/Q17), under exactly its
constraints:

* ONE CLI — every write is the grant tool's own command; this handler never
  touches a key file, a grant line or the targets file itself;
* plan first — a revoke shows the tool's own plan (run without its confirm
  token) and a plan that REFUSES ends the flow; push / unpush have no dry
  run in the tool (push identity-checks the target BEFORE writing), so the
  screen states what the action does before asking;
* typed confirm — the tool's own token: a fingerprint's last 8 to revoke on
  this box, the target's name to push / unpush from a center;
* logged — every apply goes to the log and to report_action.

Under sudo the tool runs as the invoking operator (F7: nothing root-owned in
the operator's ``~/.ssh``); as plain root the screen refuses, because root
holds neither the operator's keys nor their grant lines.

MeshAnchor carries the same file except ``menu_section`` (a literal: the
handler manifest discovers handlers by it); other per-app values come from
``utils.box_truth_app``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler
from utils import box_truth_app as app

logger = logging.getLogger(__name__)

GRANT_CLI = str(Path(__file__).resolve().parents[3] / "scripts" / "box_truth_grant.py")
_FP_RE = re.compile(r"^(SHA256:[A-Za-z0-9+/]{43})\s+(\S*)")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
NOTHING_WRITTEN = "nothing written"
#: the grant tool's exact FOREIGN marker — never the bare word, which a
#: grant's own comment may contain (step-8 pass, F4)
FOREIGN_MARK = "\u26a0 FOREIGN \u2014"
TITLE = "NOC Read Grants"


# --- pure helpers (tested) -------------------------------------------------

@dataclass
class Grant:
    fingerprint: str
    comment: str
    foreign: bool
    line: str


def parse_grants(out: str) -> List[Grant]:
    """Rows of the tool's ``list`` output. A FOREIGN row (mentions the export
    but is not our exact restricted line) needs ``--force`` to revoke."""
    rows = []
    for line in (out or "").splitlines():
        m = _FP_RE.match(line.strip())
        if m:
            rows.append(Grant(m.group(1), m.group(2), FOREIGN_MARK in line, line.strip()))
    return rows


def _cli(*args: str) -> List[str]:
    return [sys.executable, GRANT_CLI, *args]


def revoke_cmd(fp: str, *, foreign: bool, confirm: Optional[str] = None) -> List[str]:
    cmd = _cli("revoke", "--fp", fp)
    if foreign:
        cmd.append("--force")
    if confirm is not None:
        cmd += ["--confirm", confirm]
    return cmd


def push_cmd(verb: str, alias: str, confirm: str) -> List[str]:
    return _cli(verb, alias, "--confirm", confirm)


def valid_alias(alias: Optional[str]) -> bool:
    return bool(alias) and _ALIAS_RE.fullmatch(alias) is not None


def as_operator(cmd: List[str], euid: Optional[int] = None,
                sudo_user: Optional[str] = None) -> Tuple[Optional[List[str]], Optional[str]]:
    """Under sudo, run as the invoking operator; as plain root, refuse."""
    euid = os.geteuid() if euid is None else euid
    sudo_user = os.environ.get("SUDO_USER") if sudo_user is None else sudo_user
    if euid == 0 and sudo_user and sudo_user != "root":   # `sudo su` = plain root
        return ["sudo", "-n", "-u", sudo_user, "-H"] + cmd, f"(runs as {sudo_user})"
    if euid == 0:
        return None, ("plain root (no SUDO_USER): root holds neither the operator's "
                      "keys nor their grant lines — launch the TUI as your user")
    return cmd, None


# --- handler ---------------------------------------------------------------

class NocGrantsHandler(BaseHandler):
    """NOC read grants: list / revoke on this box; targets / push / unpush."""

    handler_id = "noc_grants"
    menu_section = "fleet"  # MeshAnchor: "dashboard" (its TUI has no fleet section)

    def menu_items(self):
        return [("noc_grants", "NOC Read Grants     who may read this box; what it reads", None)]

    def execute(self, action):
        if action == "noc_grants":
            self.ctx.safe_call(TITLE, self._menu)

    def _menu(self) -> None:
        while True:
            sel = self.ctx.dialog.menu(TITLE, f"{app.APP} grant tool: {GRANT_CLI}", [
                ("list", "This box: who may read me    (read-only)"),
                ("revoke", "This box: revoke a grant      plan, then type the last 8"),
                ("center", "As a center: my key + targets (read-only)"),
                ("push", "As a center: grant a target  plan, then type its name"),
                ("unpush", "As a center: revoke a target type its name"),
            ])
            if sel is None:
                return
            if sel == "list":
                rc, out = self._capture(_cli("list"))
                self.ctx.dialog.msgbox(TITLE, out or f"(no output, rc={rc})")
            elif sel == "revoke":
                self._revoke_flow()
            elif sel == "center":
                self._center_view()
            elif sel in ("push", "unpush"):
                self._push_flow(sel)

    # -- subprocess seam ---------------------------------------------------------

    def _capture(self, cmd: List[str], timeout: int = 120) -> Tuple[int, str]:
        run, note = as_operator(cmd)
        if run is None:
            return 2, note or ""
        try:
            r = subprocess.run(run, capture_output=True, text=True, timeout=timeout)
            return r.returncode, (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        except subprocess.TimeoutExpired:
            return 124, f"[timed out after {timeout}s]"
        except (subprocess.SubprocessError, OSError) as e:
            return 2, f"[could not run: {e}]"

    # -- flows -------------------------------------------------------------------

    def _center_view(self) -> None:
        from utils.box_truth_center import read_targets, targets_path
        rc, out = self._capture(_cli("center-key"))
        path = targets_path()
        rows = [f"  {t.alias:<20} {'pinned (grant read)' if t.pinned else 'NOT pinned - reads DARK'}"
                for t in read_targets(path)]
        self.ctx.dialog.msgbox(TITLE, f"{out.strip()}\n\nTargets ({path}):\n"
                               + ("\n".join(rows) if rows else "  (none)"))

    def _revoke_flow(self) -> None:
        rc, out = self._capture(_cli("list"))
        grants = parse_grants(out)
        if not grants:
            self.ctx.dialog.msgbox(TITLE, out or f"no grants (rc={rc})")
            return
        fp = self.ctx.dialog.menu(TITLE, "Revoke which grant on THIS box?",
                                  [(g.fingerprint, g.line[len(g.fingerprint):].strip()[:60])
                                   for g in grants])
        g = next((x for x in grants if x.fingerprint == fp), None)
        if g is None:
            return
        rc, plan = self._capture(revoke_cmd(g.fingerprint, foreign=g.foreign))
        if NOTHING_WRITTEN not in plan:
            self.ctx.dialog.msgbox(TITLE, f"The tool refused (rc={rc}) — nothing changed.\n\n{plan}")
            return
        token = g.fingerprint[-8:]
        typed = self.ctx.dialog.inputbox(TITLE, f"{plan}\n\nType {token} to revoke:", "")
        if typed != token:
            self.ctx.dialog.msgbox(TITLE, "Not confirmed — nothing changed.")
            return
        logger.info("noc_grants revoke fp=%s foreign=%s", g.fingerprint, g.foreign)
        rc, out = self._capture(revoke_cmd(g.fingerprint, foreign=g.foreign, confirm=token))
        self.ctx.report_action(rc == 0, "Revoke grant", out.strip()[-300:],
                               f"Revoke grant — rc={rc}", out.strip()[-600:])

    def _push_flow(self, verb: str) -> None:
        alias = self.ctx.dialog.inputbox(
            TITLE, f"{verb}: target box alias (as in your fleet registry):", "")
        if alias is None:
            return
        if not valid_alias(alias):
            self.ctx.dialog.msgbox(TITLE, f"{alias!r} is not a box alias — nothing changed.")
            return
        what = ("grant this center's key on it (identity-checked first), verify a pinned "
                "read, register it, ensure the spool cron" if verb == "push" else
                "revoke this center's key on it; it stays listed and reads DARK")
        typed = self.ctx.dialog.inputbox(TITLE, f"{verb} {alias}: {what}.\n\nType {alias} to proceed:", "")
        if typed != alias:
            self.ctx.dialog.msgbox(TITLE, "Not confirmed — nothing changed.")
            return
        logger.info("noc_grants %s target=%s", verb, alias)
        rc, out = self._capture(push_cmd(verb, alias, alias), timeout=300)
        self.ctx.report_action(rc == 0, f"{verb} {alias}", out.strip()[-300:],
                               f"{verb} {alias} — rc={rc}", out.strip()[-600:])
