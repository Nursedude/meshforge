"""Fetch a JSON URL and project items out of it as Conditions.

Used for: `http://localhost:5000/api/status` → Conditions per peer / signal /
whatever rules want to react to.

Standalone users: point at any JSON HTTP endpoint; provide an `extractor` that
turns the response into a list of dicts and a `kind` string.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .._util import fetch_json
from .base import Condition, Source


class HttpJsonSource(Source):
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

    def collect(self) -> Iterable[Condition]:
        data, err = fetch_json(self.url, timeout=self.timeout)
        if err:
            yield Condition(
                kind="source_error",
                subject=self.name,
                detail=f"{self.url} unreachable: {err}",
                source=self.name,
            )
            return
        if data is None:
            return
        try:
            items = self.extractor(data) or []
        except Exception as e:
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
