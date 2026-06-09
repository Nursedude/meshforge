"""Read a JSON file and project items out of it as Conditions.

Used for: `/var/lib/meshforge/watchdog.json` → one Condition per signal.

Standalone users: point at any JSON file; provide an `extractor` that turns
the file's contents into a list of dicts, plus a `kind` string the rule
engine will see on each emitted Condition.
"""
from __future__ import annotations

from typing import Any, Callable

from .._util import read_json
from .base import ExtractorSource


class JsonFileSource(ExtractorSource):
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

    def _read(self):
        data, err = read_json(self.path)
        if err:
            return None, f"{self.path} unreadable: {err}"
        return data, None
