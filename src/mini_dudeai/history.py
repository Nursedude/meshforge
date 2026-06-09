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
import os

# Default cap for history.jsonl. Generous — at one line per rule fire this
# retains weeks of forensic history, but it is bounded so an unattended box
# cannot grow it without limit. The same line-oriented rotation helper serves
# dreams + audit (the other append-only writers) via _rotate_if_needed below.
DEFAULT_HISTORY_MAX_BYTES = 1_000_000  # 1 MB


def _rotate_if_needed(path: str, max_bytes: int) -> str | None:
    """Line-oriented retention for an append-only JSONL file.

    When ``path`` exceeds ``max_bytes``, rewrite it keeping only the
    most-recent lines that fit under a target (half the cap, so we don't
    re-rotate on the very next write). Rotation is atomic (tmp + os.replace)
    and line-oriented, so the file stays valid JSONL and the tail-based
    readers keep working.

    Resilience matches the writers' posture: any OSError is swallowed and
    returned as a string, never raised — a disk/perms problem must never
    crash the observation loop, and a failed rotation simply leaves the file
    as-is for the append to (try to) proceed. Returns None when nothing
    needed doing or rotation succeeded, else an error string.
    """
    if max_bytes <= 0:
        return None
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    if size <= max_bytes:
        return None
    target = max_bytes // 2
    try:
        with open(path, "rb") as f:
            data = f.read()
        # Keep the trailing-most whole lines whose total bytes fit the target.
        # Iterate from the end so the newest entries survive; always keep at
        # least the last line so a single over-target line can't empty the file.
        lines = data.splitlines(keepends=True)
        # A crash mid-append can leave a torn final line with no trailing
        # newline. Terminate it so it stays an ISOLATED malformed line readers
        # skip, instead of fusing with the next appended record.
        if lines and not lines[-1].endswith(b"\n"):
            lines[-1] += b"\n"
        kept: list[bytes] = []
        total = 0
        for line in reversed(lines):
            if kept and total + len(line) > target:
                break
            kept.append(line)
            total += len(line)
        kept.reverse()
        tmp = path + ".rot.tmp"
        with open(tmp, "wb") as f:
            f.writelines(kept)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def _repair_torn_tail(path: str) -> None:
    """If the file's last byte is not a newline (crash mid-append), write one.

    This converts a torn fragment into a single isolated malformed line (one
    record lost, readers skip it) instead of letting the NEXT append fuse two
    records into one unparseable line (two records lost, invisibly).
    Best-effort: OSError is swallowed — the append's own error is what gets
    reported.
    """
    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
    except OSError:
        pass


def append_jsonl(path: str, entries: list[dict], max_bytes: int) -> str | None:
    """The one append-only JSONL write posture, shared by every appender
    (history, audit ledger, dream deltas): rotate-if-over-cap, repair a torn
    tail, append, swallow OSError into a returned string.

    Returns None on success, error string on failure. Never raises — a
    disk/perms problem must never crash an observation loop.
    """
    if not entries:
        return None
    _rotate_if_needed(path, max_bytes)
    if os.path.exists(path):
        _repair_torn_tail(path)
    try:
        with open(path, "a") as f:
            for e in entries:
                f.write(json.dumps(e, default=str) + "\n")
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"


class HistoryWriter:
    """Append JSON lines to a file. Errors are swallowed + reported, never raised."""

    def __init__(self, path: str, max_bytes: int = DEFAULT_HISTORY_MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def append(self, entries: list[dict]) -> str | None:
        """Append entries. Return None on success, error str on failure.

        Rotate the file first if it is over its byte cap, so the line being
        written is never the one dropped. A rotation failure is non-fatal — we
        still attempt the append (the append's own OSError is what's reported).
        """
        return append_jsonl(self.path, entries, self.max_bytes)
