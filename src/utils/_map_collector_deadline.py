"""ONE overall collect deadline (MAP-H H3 #4, maps audit 2026-11-06).

Sources run serially inside ``_collect_locked``. Each now runs in a bounded
worker whose budget is what is LEFT of one per-collect deadline
(``collect_deadline_seconds``, default 45 s — above the slowest HEALTHY whole
COLLECT measured fleet-wide: total max 37.6 s over 24 h on the manager, of
which ``meshcore_public`` max 34.1 s; re-measure the TOTAL before lowering it).
A source that misses its budget is abandoned and reads ``collect_timeout``.
"The collect finishes with the rest": once the deadline is spent, each source
still to run gets a FLOOR budget (``SOURCE_FLOOR_SECONDS``, 5 s — every local
source measured <= 2.4 s healthy) instead of being skipped. Fable review
2026-11-07, run: without the floor a wedged meshtasticd (source #2, its own
45 s cap) spent the whole budget and blinded the 8 sources after it EVERY
cycle — the #17/#75 class the inner cap was built to contain. Worst case is
therefore deadline + floor x the sources left, stated, not one number. A
source whose previous worker is still alive is not started a second time.

The abandoned worker keeps running and writes per-cycle state through
``self`` — its diagnostic, the position-less list, the meshtasticd node count
(the full inventory, 2026-11-07: those three; everything else a source writes
is a cache, where a late refresh is welcome). hfm #8: a worker thread sees a
PRIVATE copy of those three (a thread-local sink, via the properties below);
the caller merges it ONLY on a clean join, so a late worker writes into a
sink nobody reads — never into the cycle that gave up on it, nor the next.
This is ``_collect_interface_bounded``'s rule ("the worker writes only to its
local result") applied by the wrapper, so no source had to change — WHILE every
source keeps that rule: a thread a source spawns itself has no sink, so it
must write only locals and let the source's own thread publish (today's only
one, ``_collect_interface_bounded``, does).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_tls = threading.local()


class _CycleSink:
    """A source worker's private view of the per-cycle state."""

    __slots__ = ("owner", "diagnostics", "nodes_without_position",
                 "total_nodes_seen", "abandoned")

    def __init__(self, owner, diagnostics, nodes_without_position, total_nodes_seen):
        self.owner = owner
        # Entries are copied too: _timed_collect / _record_diagnostic mutate an
        # entry dict in place, and a shared entry would leak a late write.
        self.diagnostics = {k: dict(v) for k, v in diagnostics.items()}
        self.nodes_without_position = list(nodes_without_position)
        self.total_nodes_seen = total_nodes_seen
        self.abandoned = False


def _sink_for(obj) -> Optional[_CycleSink]:
    s = getattr(_tls, "sink", None)
    return s if s is not None and s.owner is obj else None


class CollectDeadlineMixin:
    DEFAULT_COLLECT_DEADLINE_SECONDS = 45
    # Above this a setting is refused: Lock.acquire/Thread.join overflow near
    # 9.3e9 s, and inf/1e10 made EVERY collect raise (Fable r3, run).
    MAX_COLLECT_DEADLINE_SECONDS = 3600.0
    SOURCE_FLOOR_SECONDS = 5.0  # budget each source still gets once the deadline is spent
    # Sources _collect_locked can time in one collect (pinned by a test that
    # counts them): the worst case is deadline + floor x this.
    MAX_TIMED_SOURCES_PER_COLLECT = 10
    _collect_deadline_at: Optional[float] = None  # armed only inside a collect

    # ── per-cycle state: published, or the calling worker's private sink ──
    @property
    def _source_diagnostics(self) -> Dict[str, Dict[str, Any]]:
        s = _sink_for(self)
        return s.diagnostics if s else self.__dict__.setdefault("_pub_source_diagnostics", {})

    @_source_diagnostics.setter
    def _source_diagnostics(self, value) -> None:
        s = _sink_for(self)
        if s:
            s.diagnostics = value
        else:
            self.__dict__["_pub_source_diagnostics"] = value

    @property
    def _nodes_without_position(self) -> List[Dict]:
        s = _sink_for(self)
        return s.nodes_without_position if s else self.__dict__.setdefault("_pub_nodes_without_position", [])

    @_nodes_without_position.setter
    def _nodes_without_position(self, value) -> None:
        s = _sink_for(self)
        if s:
            s.nodes_without_position = value
        else:
            self.__dict__["_pub_nodes_without_position"] = value

    @property
    def _total_nodes_seen(self) -> int:
        s = _sink_for(self)
        return s.total_nodes_seen if s else self.__dict__.setdefault("_pub_total_nodes_seen", 0)

    @_total_nodes_seen.setter
    def _total_nodes_seen(self, value) -> None:
        s = _sink_for(self)
        if s:
            s.total_nodes_seen = value
        else:
            self.__dict__["_pub_total_nodes_seen"] = value

    def _in_abandoned_worker(self) -> bool:
        s = _sink_for(self)
        return bool(s and s.abandoned)

    def _worst_case_collect_s(self) -> float:
        return self._collect_deadline_s() + self.SOURCE_FLOOR_SECONDS * self.MAX_TIMED_SOURCES_PER_COLLECT

    # ── the deadline ──
    def _collect_deadline_s(self) -> float:
        raw = self._settings.get("collect_deadline_seconds", self.DEFAULT_COLLECT_DEADLINE_SECONDS)
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = -1.0
        if not (0 < val <= self.MAX_COLLECT_DEADLINE_SECONDS):  # also nan, inf
            # hfm #3: a value the author cannot have meant — say so once, never
            # absorb it into "no deadline" or "deadline 0 = run nothing".
            if not self.__dict__.get("_bad_deadline_warned"):
                self.__dict__["_bad_deadline_warned"] = True
                logger.warning("map_settings collect_deadline_seconds=%r is not a number in "
                               "(0, %.0f] — using %ss", raw, self.MAX_COLLECT_DEADLINE_SECONDS,
                               self.DEFAULT_COLLECT_DEADLINE_SECONDS)
            val = float(self.DEFAULT_COLLECT_DEADLINE_SECONDS)
        return val

    def _arm_collect_deadline(self) -> None:
        self._collect_deadline_at = time.monotonic() + self._collect_deadline_s()

    def _disarm_collect_deadline(self) -> None:
        self._collect_deadline_at = None

    def _deadline_skip(self, source: str, notes: str) -> list:
        self._source_diagnostics[source] = {
            "attempted": 0, "yielded": 0, "reason_if_zero": "collect_timeout",
            "notes": notes, "latency_ms": 0,
        }
        return []

    def _bounded_source(self, source: str, run_inline):
        """Run ``run_inline()`` (the timed+isolated source body) within what is
        left of this collect's deadline. Outside a collect: inline, unbounded."""
        deadline_at = self._collect_deadline_at
        if deadline_at is None or _sink_for(self) is not None:
            return run_inline()
        late = self.__dict__.setdefault("_late_source_workers", {})
        prev = late.get(source)
        if prev is not None and prev.is_alive():
            return self._deadline_skip(
                source, "previous cycle's fetch still running — not started again")
        remaining = deadline_at - time.monotonic()
        on_floor = remaining < self.SOURCE_FLOOR_SECONDS
        budget = self.SOURCE_FLOOR_SECONDS if on_floor else remaining
        sink = _CycleSink(self, self._source_diagnostics, self._nodes_without_position,
                          self._total_nodes_seen)
        box: Dict[str, Any] = {}
        started = time.monotonic()

        def worker():
            _tls.sink = sink
            try:
                box["result"] = run_inline()
            except BaseException as e:  # noqa: BLE001 — recorded below, never silent
                box["error"] = e
            finally:
                _tls.sink = None
                if sink.abandoned:
                    logger.info("map source %r finished %.1fs after the collect deadline; "
                                "its result was discarded (the cycle published without it)",
                                source, time.monotonic() - started - budget)

        t = threading.Thread(target=worker, daemon=True, name=f"map-src-{source}")
        t.start()
        t.join(budget)
        if t.is_alive():
            sink.abandoned = True
            late[source] = t
            spent_ms = int((time.monotonic() - started) * 1000)
            logger.warning("map source %r exceeded its %.1fs budget (%s; deadline %.0fs) — "
                           "published without it this cycle", source, budget,
                           "floor, deadline already spent" if on_floor else "rest of the deadline",
                           self._collect_deadline_s())
            self._source_diagnostics[source] = {
                "attempted": 0, "yielded": 0, "reason_if_zero": "collect_timeout",
                "notes": (f"exceeded its {budget:.0f}s floor (collect deadline already spent) "
                          f"after {spent_ms / 1000:.1f}s; abandoned" if on_floor else
                          f"exceeded collect deadline after {spent_ms / 1000:.1f}s; abandoned"),
                "latency_ms": spent_ms,
            }
            return []
        late.pop(source, None)
        # Clean join: the worker's private state becomes the cycle's state.
        self._source_diagnostics = sink.diagnostics
        self._nodes_without_position = sink.nodes_without_position
        self._total_nodes_seen = sink.total_nodes_seen
        if "error" in box:  # run_inline isolates Exception; this is BaseException
            e = box["error"]
            self._source_diagnostics[source] = {
                "attempted": 0, "yielded": 0, "reason_if_zero": "unreachable",
                "notes": f"source worker died: {type(e).__name__}",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
            return []
        return box.get("result", [])
