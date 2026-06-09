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
    # NOTE: edge-transition identity is StateStore.rule_key(rule_id, subject) —
    # keyed per RULE, not per condition. (A Condition.key() method used to live
    # here claiming to be the tracking identity; it had zero callers and sent
    # maintainers to the wrong place.)


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


class ExtractorSource(Source):
    """Template for read→extract→project sources (json_file, http_json).

    The two used to be ~30-line copy-paste twins; any fix to the shared
    error/projection path had to be hand-applied to both and they were one
    patch away from emitting different Condition shapes. Subclasses set
    `kind`/`extractor`/`name` and implement `_read()`:

        def _read(self) -> tuple[Any, str | None]:
            return data, None            # success (data may be None = empty)
            return None, "why it broke"  # → one source_error Condition
    """

    kind: str = "condition"
    extractor = None  # callable(parsed) -> list[dict]; set by subclass __init__

    def _read(self):
        raise NotImplementedError

    def collect(self) -> Iterable[Condition]:
        data, err_detail = self._read()
        if err_detail:
            yield Condition(
                kind="source_error", subject=self.name,
                detail=err_detail, source=self.name,
            )
            return
        if data is None:
            return
        try:
            items = self.extractor(data) or []
        except Exception as e:  # extractor is user code — never crash on it
            yield Condition(
                kind="source_error", subject=self.name,
                detail=f"extractor raised {type(e).__name__}: {e}",
                source=self.name,
            )
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject", "?"))
            detail = str(item.get("detail", ""))
            extras = {k: v for k, v in item.items()
                      if k not in ("subject", "detail")}
            yield Condition(
                kind=self.kind, subject=subject, detail=detail,
                source=self.name, extras=extras,
            )
