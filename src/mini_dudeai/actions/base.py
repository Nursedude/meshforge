"""Action ABC + Outcome dataclass.

An Action is something mini-dudeai *does* when a rule fires. The engine calls
execute() with the rule, the matched condition, and the transition direction
(edge_up = condition newly present, edge_down = condition newly cleared).

Actions return an Outcome the engine records in history.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..sources.base import Condition


@dataclass
class Outcome:
    """Result of an Action.execute() call. Recorded in history.jsonl."""
    action: str             # what action ran (for history)
    ok: bool = True
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class Action:
    """Abstract action. Subclasses override execute()."""

    name: str = "action"

    # Send-retry semantics (#81). Default False = EVENT semantics: every
    # undelivered send still matters (a late page is still information), so
    # a delivered opposite-edge send never cancels a queued one. Set True
    # for STATE-SET actions (actuators/displays): only the LATEST state is
    # truth — any older undelivered send is superseded the moment a newer
    # transition exists, because replaying stale state would re-assert a
    # condition that is no longer true (the display_alert banner-
    # resurrection class). The engine leaves a history witness per supersede.
    supersedes_pending_sends: bool = False

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        """Run the action.

        transition is one of "edge_up" | "edge_down".
        rule is the raw rule dict (so actions can read action-specific config
        like ntfy title/priority/tags from rule.action.*).
        """
        raise NotImplementedError
