"""Source ABC + Condition dataclass.

A Source is anything mini-dudeai reads each tick. It returns a list of
Conditions — observations about the world that rules can match on.

Source emits errors (kind="source_error") as conditions too, so rules can
react to mini going blind on its own data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class Condition:
    """One observation a Source emits this tick.

    Engine matches Rules against Conditions by (kind, subject_glob, extras).
    """
    kind: str               # what kind of condition (sources define their own)
    subject: str            # the thing the condition is about
    detail: str = ""        # human-readable detail (for messages, history)
    source: str = ""        # which source emitted (traceability)
    extras: dict[str, Any] = field(default_factory=dict)  # extra fields rules can match on

    def key(self) -> tuple[str, str]:
        """Identity used for edge-transition tracking."""
        return (self.kind, self.subject)


class Source:
    """Abstract source. Subclasses override collect() to emit Conditions.

    The `kind` field on subclass instances is informational only — actual
    Condition kinds are set per-condition in collect().
    """

    name: str = "source"

    def collect(self) -> Iterable[Condition]:
        """Read the underlying data, return Conditions for anything notable.

        Errors that prevent collection should be emitted as Conditions with
        kind="source_error" so rules can react to going blind.
        """
        raise NotImplementedError
