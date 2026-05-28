"""Append a line to an annotations file.

Used for the moc3-known-normal pattern: a rule that should NOT push ntfy
but should still record "this thing fired and was suppressed" somewhere a
human can read. The digest's Phase 1 plan includes a step where the digest
ingests this annotations file and shows known-false events alongside the
fresh signals.
"""
from __future__ import annotations

import datetime

from ..sources.base import Condition
from .base import Action, Outcome


class FileAnnotateAction(Action):
    """Append a single markdown line to a file each fire."""

    name = "annotate"

    def __init__(self, path: str) -> None:
        self.path = path

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note = (rule.get("annotation") or "").strip()
        line = f"- [{ts}] [{transition}] **{rule['id']}** · {cond.subject} · {cond.detail}"
        if note:
            line += f" · _{note}_"
        try:
            with open(self.path, "a") as f:
                f.write(line + "\n")
            return Outcome(action="annotate", ok=True)
        except OSError as e:
            return Outcome(action="annotate", ok=False, error=str(e))
