"""Mark a fire as one the cloud Claude session should pick up next time it warms.

This action's outcome carries an "escalation" payload the history file
captures. On warm-invocation, the cloud session greps history for
escalation entries (e.g. via `jq '.outcome.escalation'`) and decides what
to chase.

No side effects — just structured data into history. Pairs with the digest
arc's Phase 2 (cloud session reads mini history on warm-up).
"""
from __future__ import annotations

from ..sources.base import Condition
from .base import Action, Outcome


class ProposeEscalationAction(Action):
    """Record an escalation payload in the history outcome. Side-effect free."""

    name = "propose_escalation"

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        note = (rule.get("annotation") or "").strip()
        return Outcome(
            action="propose_escalation",
            ok=True,
            extras={
                "escalation": {
                    "rule": rule["id"],
                    "subject": cond.subject,
                    "detail": cond.detail,
                    "note": note,
                    "transition": transition,
                }
            },
        )
