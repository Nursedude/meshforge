"""The map's published snapshot -- ONE owner of what a request may read (MAP-H H3).

Request threads never wait on ``_collect_lock`` (H3 #1): they read the last
PUBLISHED snapshot and say its age (H3 #2). Two seams, each the single place
its class of mistake can live (review round 1, 2026-11-06):

* **Coherence** -- a snapshot is published as ONE atomic value
  ``(geojson, monotonic stamp, generation)``. A reader takes it once and derives
  the bytes, the response-cache key and the age header from that same read, so a
  body can never carry another snapshot's age (hfm #5), and age is never wall
  clock (hfm #6).
* **Witness** -- every collect, whoever drives it (request kick, periodic tick,
  prewarm/TUI ``collect()``), runs through ``_run_collect_witnessed``: failures
  are counted and the last error kept (hfm #9), so "no snapshot because the
  collector keeps failing" reads BLIND with its reason -- never "warming" for
  hours (hfm #1/#2).
"""

import logging
import threading
import time
from typing import Any, Dict, NamedTuple, Optional

logger = logging.getLogger(__name__)


class SnapshotWarming(RuntimeError):
    """No snapshot to serve. ``reason`` None = cold start; else the collect error."""

    def __init__(self, reason: Optional[str] = None):
        super().__init__(reason or "no map snapshot published yet")
        self.reason = reason


class Snapshot(NamedTuple):
    geojson: Dict[str, Any]
    age_s: float
    generation: int


class MapSnapshotMixin:
    """Mixed into MapDataCollector; call ``_init_snapshot_state()`` in __init__."""

    # The kicked refresh waits this long for a running collect, then re-checks.
    _SNAPSHOT_REFRESH_LOCK_WAIT_S = 60.0
    # A lock-wait timeout is a WEDGE signal; warn at most this often (r2#2).
    _LOCK_TIMEOUT_WARN_EVERY_S = 600.0

    def _init_snapshot_state(self) -> None:
        self._snapshot_state: Optional[tuple] = None  # (geojson, mono, generation)
        self._snapshot_generation = 0
        self._snapshot_kick_lock = threading.Lock()
        self._snapshot_refresh_thread: Optional[threading.Thread] = None
        self._collect_failures = 0
        self._last_collect_error: Optional[str] = None
        self._refresh_lock_timeouts = 0
        self._last_lock_timeout_mono: Optional[float] = None
        self._last_lock_timeout_warn_mono: Optional[float] = None

    # ── publish / witness ────────────────────────────────────────────────
    def _publish_snapshot(self, geojson: Dict[str, Any],
                          mono: Optional[float] = None) -> None:
        """The ONLY writer. The atomic tuple lands BEFORE the legacy
        ``_cached_geojson`` the warming watcher polls, so a request that sees
        warming clear always finds a stamped snapshot."""
        now_mono = time.monotonic()
        stamp = now_mono if mono is None else mono
        self._snapshot_generation += 1
        self._snapshot_state = (geojson, stamp, self._snapshot_generation)
        # Response bytes are keyed by generation: every older entry is now
        # unreachable, so drop them or they are held forever (review r2#1).
        cache = getattr(self, "_geojson_response_cache", None)
        if cache is not None:
            cache.clear()
        self._cached_geojson = geojson
        # The legacy wall stamp says the SAME age (one snapshot, two readers; r2#3).
        self._last_collect = time.time() - (now_mono - stamp)

    def _run_collect_witnessed(self):
        """Run ``_collect_locked()`` (caller holds ``_collect_lock``) with a witness."""
        try:
            result = self._collect_locked()
        except Exception as e:
            self._collect_failures += 1
            self._last_collect_error = f"{type(e).__name__}: {e}"[:200]
            raise
        self._last_collect_error = None
        return result

    # ── read ─────────────────────────────────────────────────────────────
    def _max_age_s(self, max_age_seconds: Optional[int]) -> int:
        if max_age_seconds is not None:
            return max_age_seconds
        return int(self._settings.get("collect_cache_max_age_seconds",
                                      self.DEFAULT_COLLECT_CACHE_MAX_AGE_SECONDS))

    def peek_snapshot(self) -> Optional[Snapshot]:
        """The published snapshot, read ONCE; never kicks, never waits."""
        state = self._snapshot_state
        if state is None:
            return None
        geo, mono, gen = state
        return Snapshot(geo, max(0.0, time.monotonic() - mono), gen)

    def snapshot(self, max_age_seconds: Optional[int] = None) -> Optional[Snapshot]:
        """Request-thread read (H3 #1): NEVER waits on ``_collect_lock``.

        The published snapshot, kicking ONE background refresh when it is past
        the cache window; None before any snapshot exists. Non-request callers
        that must have data now keep ``collect()``.
        """
        snap = self.peek_snapshot()
        if snap is None or snap.age_s >= self._max_age_s(max_age_seconds):
            self._kick_snapshot_refresh()
        return snap

    def snapshot_or_warming(self) -> Snapshot:
        """snapshot() for a handler, or raise SnapshotWarming (reason = last error)."""
        snap = self.snapshot()
        if snap is None:
            raise SnapshotWarming(self._last_collect_error)
        return snap

    def snapshot_age(self) -> Optional[float]:
        snap = self.peek_snapshot()
        return None if snap is None else snap.age_s

    def snapshot_stale_after(self) -> float:
        """STALE once the periodic refresher missed twice (its default when off)."""
        try:
            interval = float(self._settings.get(
                "periodic_refresh_seconds", self.DEFAULT_PERIODIC_REFRESH_SECONDS))
        except (TypeError, ValueError):
            interval = 0.0
        if interval <= 0:  # refresher disabled: requests drive refresh; keep a real line
            interval = float(self.DEFAULT_PERIODIC_REFRESH_SECONDS)
        return 2.0 * interval

    def snapshot_status(self) -> Dict[str, Any]:
        """``/api/status.snapshot``: none | ok | stale, the age that decided it,
        and the collect witness (failures from EVERY driver + the last error)."""
        snap = self.peek_snapshot()
        out: Dict[str, Any] = {"collect_failures": self._collect_failures,
                               "last_error": self._last_collect_error,
                               "refresh_lock_timeouts": self._refresh_lock_timeouts}
        if snap is None:
            out.update(state="none", age_s=None)
            return out
        stale_after = self.snapshot_stale_after()
        out.update(state="stale" if snap.age_s > stale_after else "ok",
                   age_s=round(snap.age_s, 1), stale_after_s=stale_after)
        # Blame a wedge only for a timeout DURING this snapshot's life, never a
        # lifetime count (review r3#2: an old, cleared wedge is not today's cause).
        state, last_to = self._snapshot_state, self._last_lock_timeout_mono
        if (out["state"] == "stale" and state is not None and last_to is not None
                and last_to >= state[1]):
            out["stale_reason"] = (
                f"collect lock held > {self._SNAPSHOT_REFRESH_LOCK_WAIT_S:.0f} s "
                f"({self._refresh_lock_timeouts}x) -- a collect may be wedged")
        return out

    # ── the one background refresh ───────────────────────────────────────
    def _kick_snapshot_refresh(self) -> None:
        with self._snapshot_kick_lock:
            t = self._snapshot_refresh_thread
            if t is not None and t.is_alive():
                return
            t = threading.Thread(target=self._snapshot_refresh, daemon=True,
                                 name="MapDataCollector-snapshot-refresh")
            self._snapshot_refresh_thread = t
            t.start()

    def _note_lock_timeout(self) -> None:
        self._refresh_lock_timeouts += 1
        now = time.monotonic()
        self._last_lock_timeout_mono = now
        last = self._last_lock_timeout_warn_mono
        if last is None or now - last >= self._LOCK_TIMEOUT_WARN_EVERY_S:
            self._last_lock_timeout_warn_mono = now
            logger.warning(
                "map snapshot refresh waited %.0f s for _collect_lock and gave up "
                "(%d time(s)); a collect may be wedged -- the map serves its last "
                "snapshot and says its age", self._SNAPSHOT_REFRESH_LOCK_WAIT_S,
                self._refresh_lock_timeouts)

    def _snapshot_refresh(self) -> None:
        """Wait (bounded) for any running collect, then refresh only if still stale.

        Waiting -- rather than skipping when the lock is held -- means a kick
        is never dropped behind a collect that re-checked its cache and did not
        refresh; the re-check means one that DID refresh is not repeated.
        """
        if not self._collect_lock.acquire(timeout=self._SNAPSHOT_REFRESH_LOCK_WAIT_S):
            self._note_lock_timeout()  # a wedged holder: witness it; the next request kicks again
            return
        try:
            snap = self.peek_snapshot()
            if snap is not None and snap.age_s < self._max_age_s(None):
                return
            try:
                self._run_collect_witnessed()
            except Exception:
                logger.exception("Background map snapshot refresh failed")
                return  # nothing new to warm (the finally still releases)
        finally:
            self._collect_lock.release()
        try:  # a request-driven publish keeps the warmed slices on its generation
            self._warm_geojson_regions()
        except Exception:
            logger.exception("geojson region warm failed")
