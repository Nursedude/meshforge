"""In-app remediation surface — the spine of the In-Domain Principle.

A producer (a diagnostic, a config-change handler, a mini-dudeai escalation)
describes a FINDING and one or more proposed fix ACTIONS. This module renders
them, takes the operator's ratification, applies the chosen action, and reports
the result — all in-app. No handler should hand the user a shell command to
type; it builds a RemediationAction and hands it here.

This is the single surface that turns a detected problem into a one-keystroke
fix. It is shared by every producer so the app never punts the user to a shell.
See `.claude/foundations/in_domain_principle.md` (the In-Domain Principle and
the producer → surface → backend architecture).

Usage:

    from remediation import RemediationAction, propose_remediation
    from utils.service_check import restart_service

    propose_remediation(
        ctx, "Apply RNS Loglevel",
        "rnsd must restart to load the new loglevel.",
        [RemediationAction(
            label="Restart rnsd now",
            description="systemctl restart rnsd",
            apply=lambda: restart_service("rnsd"),
            requires_admin=True,
        )],
    )
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# An action's apply() returns (success, message) — the SAME contract as
# utils.service_check.restart_service/start_service, so wiring those is direct.
ApplyResult = Tuple[bool, str]


@dataclass
class RemediationAction:
    """One proposed in-app fix for a detected problem.

    apply() performs the fix and returns (success, message). It must not raise
    for an expected failure (return (False, msg)); propose_remediation guards
    unexpected exceptions so a fix can never crash the TUI.
    """
    label: str                              # menu label, e.g. "Restart rnsd now"
    description: str                        # one line: what it does / the command
    apply: Callable[[], ApplyResult]        # performs the fix → (ok, message)
    requires_admin: bool = False            # needs root (Viewer/Admin boundary)
    requires_ratify: bool = False           # extra confirm for destructive fixes


def _safe_apply(action: RemediationAction) -> ApplyResult:
    """Run action.apply(), normalizing its return and never raising."""
    try:
        result = action.apply()
    except Exception as e:  # a fix must never crash the TUI
        return (False, f"{action.label} errored: {e}")
    if isinstance(result, tuple) and len(result) == 2:
        return (bool(result[0]), str(result[1]))
    # Tolerate a bare-bool / None return.
    return (bool(result), f"{action.label} {'done' if result else 'failed'}")


def propose_remediation(
    ctx,
    title: str,
    finding: str,
    actions: List[RemediationAction],
) -> Optional[ApplyResult]:
    """Show a finding + proposed fixes, ratify, apply, report — all in-app.

    Returns the (success, message) of the applied action, or None if the
    operator declined / chose to do nothing. Never raises for an action
    failure; the failure is reported in-app and returned.
    """
    if not actions:
        ctx.dialog.msgbox(title, finding)
        return None

    # The menu IS the primary ratification — every choice is explicit, and
    # "Do nothing" is always present so declining is a first-class option.
    choices = [(str(i + 1), f"{a.label} — {a.description}")
               for i, a in enumerate(actions)]
    choices.append(("0", "Do nothing (leave as-is)"))

    sel = ctx.dialog.menu(title, f"{finding}\n\nProposed fix:", choices)
    if not sel or str(sel) == "0":
        return None
    try:
        action = actions[int(sel) - 1]
    except (ValueError, IndexError):
        return None

    # Viewer/Admin boundary — a privileged fix needs root. We do NOT trigger a
    # sudo password prompt inside whiptail (it can hang the TUI); we tell the
    # operator how to get Admin mode. This is the one documented, legitimate
    # in-app→relaunch boundary (privilege separation, not a shell-escape).
    if action.requires_admin and os.geteuid() != 0:
        ctx.dialog.msgbox(
            title,
            f"'{action.label}' needs Admin mode.\n\n"
            f"Relaunch MeshForge with sudo to apply this fix, then retry.",
        )
        return (False, "admin required")

    # Extra confirmation only for fixes flagged destructive/irreversible.
    if action.requires_ratify and not ctx.dialog.yesno(
        title, f"Apply this fix now?\n\n{action.label}\n{action.description}"
    ):
        return None

    ok, msg = _safe_apply(action)
    ctx.dialog.msgbox(
        title,
        ("✓ " if ok else "✗ ")
        + (msg or f"{action.label} {'succeeded' if ok else 'failed'}"),
    )
    return (ok, msg)
