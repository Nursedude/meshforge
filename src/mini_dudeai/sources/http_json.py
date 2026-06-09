"""Fetch a JSON URL and project items out of it as Conditions.

Used for: `http://localhost:5000/api/status` → Conditions per peer / signal /
whatever rules want to react to.

Standalone users: point at any JSON HTTP endpoint; provide an `extractor` that
turns the response into a list of dicts and a `kind` string.
"""
from __future__ import annotations

from typing import Any, Callable

from .._util import fetch_json
from .base import ExtractorSource


class HttpJsonSource(ExtractorSource):
    """Fetch a URL each tick, emit one Condition per extracted item.

    Args:
        url: full URL (gzip auto-handled)
        kind: kind string applied to every emitted Condition
        extractor: callable(parsed_json) -> list[dict]. Each dict needs
            'subject' + 'detail'; other entries → Condition.extras.
        timeout: seconds for the HTTP request (default 8)
        name: source identity (defaults to "http_json:<url>")
    """

    def __init__(
        self,
        url: str,
        kind: str,
        extractor: Callable[[Any], list[dict]],
        timeout: float = 8.0,
        name: str | None = None,
    ) -> None:
        self.url = url
        self.kind = kind
        self.extractor = extractor
        self.timeout = timeout
        self.name = name or f"http_json:{url}"

    def _read(self):
        data, err = fetch_json(self.url, timeout=self.timeout)
        if err:
            return None, f"{self.url} unreachable: {err}"
        return data, None
