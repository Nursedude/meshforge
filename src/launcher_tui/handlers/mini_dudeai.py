"""mini-dudeai — the local watcher, now actionable in-app.

mini-dudeai (the 24/7 rule daemon) PROPOSES findings: currently-active rules in
its state and escalation markers in its history. This handler is the consuming
side of the propose→ratify trust model — it surfaces those findings and, where
a safe LOCAL fix exists, offers it through the remediation surface so the
operator ratifies and applies it in-app. That closes the loop the In-Domain
Principle is about: the detect engine (mini-dudeai) finally meets the fix engine
(the remediation surface), with no shell in between.

Design notes:
- The escalation→fix MAP lives HERE, ratified by the operator — not in the
  daemon, which only proposes (the daemon must never dictate side-effects).
- Escalations are read via ``mini_dudeai.recent_escalations`` — the SAME SSOT
  the warm-start brief and the situation digest use, so the three never
  disagree (the 2026-05-29 duplicate-renderer lesson).
- A finding whose subject is a REMOTE box has no local fix; we say so plainly
  rather than pretend. The fix map only contains safe, local services.

See ``.claude/foundations/in_domain_principle.md``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import List

from handler_protocol import BaseHandler
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


def _mini_paths():
    home = get_real_user_home()
    return home / "mini_dudeai_state.json", home / "mini_dudeai_history.jsonl"


def _read_json(path) -> dict:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_history(path, last: int = 80) -> list:
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in [ln for ln in f if ln.strip()][-last:]:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _restart_action(service: str, label: str, description: str):
    """Build a remediation action that restarts a LOCAL service via the SSOT."""
    from remediation import RemediationAction
    from utils.service_check import restart_service
    return RemediationAction(
        label=label,
        description=description,
        apply=lambda: restart_service(service),
        requires_admin=True,
    )


def _fixes_for(rule_id: str) -> list:
    """Operator-ratified map: rule_id → safe LOCAL remediation actions.

    Only services this box owns and that a restart can genuinely recover.
    A rule absent here (e.g. a remote-peer escalation) yields no action — the
    caller then presents it as informational rather than pretending to fix it.
    Extend conservatively as fixes prove out.
    """
    if rule_id == "source_error_federator":
        # The local /api/status (map/federation server) is unreachable; the
        # dream loop traced these to meshforge-map restarts. Restart is the fix.
        return [_restart_action("meshforge-map", "Restart meshforge-map",
                                "restart the local map / federation server")]
    if rule_id == "source_error_watchdog":
        return [_restart_action("meshforge-watchdog", "Restart meshforge-watchdog",
                                "restart the local reliability watchdog")]
    return []


def build_findings(state: dict, history: list, now_ts: float) -> list:
    """Pure: combine active rules + fresh escalations into actionable findings.

    Returns a list of dicts: {rule, subject, detail, source, actions}. ``source``
    is 'active' (a currently-active rule in state) or 'escalation' (a fresh
    propose_escalation from history, via the recent_escalations SSOT). ``actions``
    is a possibly-empty list of RemediationAction; empty ⇒ informational only.
    """
    from mini_dudeai import recent_escalations

    findings = []
    seen = set()
    for rs in (state.get("rules") or {}).values():
        if not isinstance(rs, dict) or not rs.get("currently_active"):
            continue
        rid, subj = rs.get("rule_id"), rs.get("subject")
        seen.add((rid, subj))
        findings.append({
            "rule": rid, "subject": subj,
            "detail": str(rs.get("last_detail", "")), "source": "active",
            "actions": _fixes_for(rid),
        })
    for esc in recent_escalations(history, now_ts):
        rid, subj = esc.get("rule"), esc.get("subject")
        if (rid, subj) in seen:  # already shown as an active rule
            continue
        findings.append({
            "rule": rid, "subject": subj,
            "detail": str(esc.get("detail", "")), "source": "escalation",
            "actions": _fixes_for(rid),
        })
    return findings


def _posture(state: dict, now_ts: float) -> str:
    last_tick = state.get("last_tick_ts")
    rule_count = state.get("rule_count", len(state.get("rules") or {}))
    errs = state.get("error_count", 0)
    if not last_tick:
        return "⚠️ mini-dudeai has no state here (never ticked, or file missing)."
    age = int(max(0, now_ts - float(last_tick)))
    if age > 300:
        return (f"🔴 STALE — last tick {age}s ago (>300s). The watcher itself may "
                f"be down: systemctl --user status meshforge-mini-dudeai")
    return f"🟢 alive — {rule_count} rules, src_errors={errs}, last tick {age}s ago."


class MiniDudeaiHandler(BaseHandler):
    """Surface mini-dudeai findings and apply safe local fixes in-app."""

    handler_id = "mini_dudeai"
    menu_section = "dashboard"

    def menu_items(self):
        return [("mini_dudeai", "mini-dudeai (local watcher + fixes)", None)]

    def execute(self, action):
        if action == "mini_dudeai":
            self.ctx.safe_call("mini-dudeai", self._render)

    def _render(self):
        state_p, hist_p = _mini_paths()
        while True:
            state = _read_json(state_p)
            history = _read_history(hist_p)
            now = time.time()
            header = _posture(state, now)
            findings = build_findings(state, history, now)

            if not findings:
                self.ctx.dialog.msgbox(
                    "mini-dudeai", f"{header}\n\nNothing actionable right now ✓")
                return

            choices = []
            for i, f in enumerate(findings):
                mark = "[fix]" if f["actions"] else "[info]"
                detail = f["detail"][:48]
                choices.append((str(i + 1),
                                f"{mark} {f['rule']} · {f['subject']} — {detail}"))
            choices.append(("back", "Back"))

            sel = self.ctx.dialog.menu("mini-dudeai — findings", header, choices)
            if not sel or sel == "back":
                return
            try:
                f = findings[int(sel) - 1]
            except (ValueError, IndexError):
                continue

            if f["actions"]:
                from remediation import propose_remediation
                propose_remediation(
                    self.ctx, f"Fix: {f['rule']}",
                    f"{f['subject']} — {f['detail']}", f["actions"])
                # loop re-reads state, so a successful fix updates the list
            else:
                self.ctx.dialog.msgbox(
                    f["rule"],
                    f"{f['subject']} — {f['detail']}\n\n"
                    f"No local fix on this box: the affected subject is elsewhere "
                    f"(another box), or this finding is informational. Review it "
                    f"via Fleet tools, or address it on {f['subject']}.")
