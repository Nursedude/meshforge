"""No-op action — records the fire in history but takes no other action.

Useful for rules whose only purpose is to mark "this condition exists"
without paging or annotating. Edge-counter-only.
"""
from __future__ import annotations

from ..sources.base import Condition
from .base import Action, Outcome


class NoopAction(Action):
    name = "none"

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        return Outcome(action="none", ok=True)
