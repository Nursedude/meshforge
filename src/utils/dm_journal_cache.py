"""BIC-2 DM slice 1: the map's ONE cache of this box's DM journal facts.

code-review r1 finding 1 (2026-11-25): an unsettled DM (``no_ack_seen``
never settles) made EVERY ``/api/inbox`` read -- every 10 s, per open tab
-- re-run a cursor-less ``journalctl --since`` up to 6 h back (lehua's cold
read measured 6.3 s for 24 h). This cache does one bounded full read, then
resumes after the journal cursor, so a poll walks only the lines since the
last poll.

Ids only (``box_truth_rx.JournalLine``, D-1). In-process: a map restart
costs one full read. One lock: concurrent HTTP reads never interleave a
cursor (hfm #8). A failed read returns ``None`` + why and drops the cursor,
so the next read starts over rather than trusting a broken resume.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, List, Optional, Tuple

from utils import box_truth_rx
from utils.box_truth_rx import DM_READ_MAX_S, JournalLine


class DmJournalCache:
    def __init__(self, reader: Optional[Callable[..., Any]] = None) -> None:
        self._reader = reader
        self._lock = threading.Lock()
        self._cursor: Optional[str] = None
        self._covered_since: Optional[float] = None
        self._lines: List[JournalLine] = []

    def read(self, runner: Any, *, since: float, now: float
             ) -> Tuple[Optional[List[JournalLine]], Optional[str]]:
        """Lines with ``t >= since`` (``since`` capped at DM_READ_MAX_S
        back), or (None, why) when the journal could not be read."""
        reader = self._reader or box_truth_rx.read_dm_journal
        lo = max(float(since), now - DM_READ_MAX_S)
        with self._lock:
            resume = (self._cursor is not None and self._covered_since is not None
                      and lo >= self._covered_since)
            if resume:
                new, cur, why = reader(runner, since=lo, now=now, cursor=self._cursor)
            else:
                new, cur, why = reader(runner, since=lo, now=now)
            if new is None:
                self._cursor, self._covered_since, self._lines = None, None, []
                return None, why
            if resume:
                self._lines.extend(new)
            else:
                self._lines, self._covered_since = list(new), lo
            self._cursor = cur
            floor = now - DM_READ_MAX_S
            self._lines = [ln for ln in self._lines if ln.t >= floor]
            self._covered_since = max(self._covered_since, floor)
            return [ln for ln in self._lines if ln.t >= lo], None

    def held(self, *, since: float, now: float) -> Optional[List[JournalLine]]:
        """What the cache already holds from ``since`` -- never a read (Fable
        r2 #10: old unsettled rows forked journalctl on every poll). None =
        it holds nothing that covers ``since`` (never read, failed, or the
        window predates its coverage)."""
        with self._lock:
            if self._covered_since is None or since < self._covered_since:
                return None
            return [ln for ln in self._lines if ln.t >= since]


_CACHE: Optional[DmJournalCache] = None
_CACHE_LOCK = threading.Lock()


def get_dm_journal_cache() -> DmJournalCache:
    """This process's one cache (the map)."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = DmJournalCache()
        return _CACHE
