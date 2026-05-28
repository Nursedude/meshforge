"""Watch a file's mtime: emit a Condition when it goes stale.

Used for: `~/situation_digest.md` — fire if the digest hasn't been refreshed
in N seconds (which means the digest daemon may be down or wedged).

Standalone users: monitor any "is this file being touched?" canary.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

from .base import Condition, Source


class FileMtimeSource(Source):
    """Emit a stale-condition when path mtime is older than max_age_s.

    Args:
        path: filesystem path to monitor
        max_age_s: stale threshold in seconds
        kind: kind to emit on stale (default "file_stale")
        subject: subject string (default = path basename)
        name: source identity (defaults to "file_mtime:<path>")
    """

    def __init__(
        self,
        path: str,
        max_age_s: float,
        kind: str = "file_stale",
        subject: str | None = None,
        name: str | None = None,
    ) -> None:
        self.path = path
        self.max_age_s = max_age_s
        self.kind = kind
        self.subject = subject or os.path.basename(path)
        self.name = name or f"file_mtime:{path}"

    def collect(self) -> Iterable[Condition]:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            yield Condition(
                kind="source_error",
                subject=self.subject,
                detail=f"{self.path} missing or unreadable",
                source=self.name,
            )
            return
        age = time.time() - mtime
        if age > self.max_age_s:
            yield Condition(
                kind=self.kind,
                subject=self.subject,
                detail=f"age={int(age // 60)}m (>{int(self.max_age_s // 60)}m threshold)",
                source=self.name,
                extras={"age_seconds": int(age),
                        "threshold_seconds": int(self.max_age_s)},
            )
