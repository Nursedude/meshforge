"""Append-only history file.

One line per rule fire (edge_up or edge_down). The cloud Claude session
reads this on warm invocation to learn what's been happening on the box
since last session — the cheap warm-context mechanism mini-dudeai was
designed to feed.

History MUST NOT be allowed to fail loudly. A disk-full or perms problem
should never crash the daemon; observation tools surface the failure but
keep ticking.
"""
from __future__ import annotations

import json


class HistoryWriter:
    """Append JSON lines to a file. Errors are swallowed + reported, never raised."""

    def __init__(self, path: str) -> None:
        self.path = path

    def append(self, entries: list[dict]) -> str | None:
        """Append entries. Return None on success, error str on failure."""
        if not entries:
            return None
        try:
            with open(self.path, "a") as f:
                for e in entries:
                    f.write(json.dumps(e, default=str) + "\n")
            return None
        except OSError as e:
            return f"{type(e).__name__}: {e}"
