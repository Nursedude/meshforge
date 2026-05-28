"""Read a JSON file and project items out of it as Conditions.

Used for: `/var/lib/meshforge/watchdog.json` → one Condition per signal.

Standalone users: point at any JSON file; provide an `extractor` that turns
the file's contents into a list of dicts, plus a `kind` string the rule
engine will see on each emitted Condition.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .._util import read_json
from .base import Condition, Source


class JsonFileSource(Source):
    """Read a JSON file each tick, emit one Condition per extracted item.

    Args:
        path: filesystem path to the JSON file
        kind: kind string applied to every emitted Condition
        extractor: callable(parsed_json) -> list[dict]. Each dict should have
            at minimum 'subject' and 'detail' keys. All other dict entries
            become Condition.extras (rules can match on them).
        name: source identity (defaults to "json_file:<path>")
    """

    def __init__(
        self,
        path: str,
        kind: str,
        extractor: Callable[[Any], list[dict]],
        name: str | None = None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.extractor = extractor
        self.name = name or f"json_file:{path}"

    def collect(self) -> Iterable[Condition]:
        data, err = read_json(self.path)
        if err:
            yield Condition(
                kind="source_error",
                subject=self.name,
                detail=f"{self.path} unreadable: {err}",
                source=self.name,
            )
            return
        if data is None:
            return
        try:
            items = self.extractor(data) or []
        except Exception as e:  # extractor is user code — never crash on it
            yield Condition(
                kind="source_error",
                subject=self.name,
                detail=f"extractor raised {type(e).__name__}: {e}",
                source=self.name,
            )
            return
        for item in items:
            subject = str(item.get("subject", "?"))
            detail = str(item.get("detail", ""))
            extras = {k: v for k, v in item.items() if k not in ("subject", "detail")}
            yield Condition(
                kind=self.kind,
                subject=subject,
                detail=detail,
                source=self.name,
                extras=extras,
            )
