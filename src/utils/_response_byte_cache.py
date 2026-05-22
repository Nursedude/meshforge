"""Short-TTL cache of serialized HTTP response bytes.

Generalized from ``DirectoryResponseCache`` (Issue #70) to cover any
endpoint whose serialization cost is GIL-bound and dominates the
handler. Issue #71 / GitHub #1168 extends the pattern to
``/api/nodes/geojson`` (47 MB body, ~35 s cold) and
``/api/network/topology`` (~24 MB, ~1.4 s every request) — the two
remaining instances of the same wedge class that motivated #70.

The mechanism is identical across endpoints: any ``json.dumps`` +
``gzip.compress`` over a multi-MB body holds the GIL for seconds at a
time. Concurrent callers — federation peers on a TTL boundary, browser
auto-refresh on multiple tabs, the operator dashboard hitting several
endpoints in parallel — each pay that cost independently, stacking
sequentially under the GIL. The watchdog's ``http_local_unresponsive``
signal trips when ``/healthz`` stalls behind the pile.

This cache holds ``(raw_bytes, gzip_bytes)`` for a few seconds so a
burst of misses on the same key share one build. Two distinct locks
coalesce work without serializing reads:

* ``_entries_lock`` — held only while reading/writing the cache dict;
  every cache hit takes it briefly. Cheap.
* ``_build_lock`` — held during the actual ``build_fn()`` call. A
  concurrent miss waits here, then re-checks the cache and either
  returns the bytes the first caller just stored or builds itself.
  Holding this lock during the slow path means other keys (and cache
  hits on other keys, though typically only a few keys are hot) wait;
  but the GIL was already serializing the build, so total wall-time
  for those callers is unchanged — what we save is the redundant
  serialization work the other waiters used to repeat.

Cache key is opaque to the cache itself — any hashable. Callers
choose what discriminates a response: ``None`` for endpoints with no
query params (topology), a tuple ``(bbox, region, preset)`` for
endpoints that filter on query string (geojson), a single preset key
for the directory endpoint.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Hashable, Optional, Tuple


# Cache entry: (expires_monotonic_ts, raw_bytes, gzip_bytes_or_None).
_CacheEntry = Tuple[float, bytes, Optional[bytes]]


class ResponseByteCache:
    """Single-flight TTL cache of ``(raw_bytes, gzip_bytes)`` tuples."""

    def __init__(self, ttl_s: float = 5.0):
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s!r}")
        self._ttl_s = ttl_s
        self._entries: Dict[Hashable, _CacheEntry] = {}
        self._entries_lock = threading.Lock()
        self._build_lock = threading.Lock()
        # Observability counters — surfaced via /api/status if a caller
        # wants to confirm the cache is doing work. Reads need no lock
        # because Python's GIL makes int increments atomic enough for
        # diagnostic counters; the value can race by ±1 under contention,
        # which is acceptable for what these signal.
        self.hit_count: int = 0
        self.miss_count: int = 0
        self.coalesced_count: int = 0  # caller waited on _build_lock and found a fresh entry

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    def get(self, key: Hashable) -> Optional[Tuple[bytes, Optional[bytes]]]:
        """Return cached ``(raw_bytes, gzip_bytes_or_None)`` if fresh, else None.

        Fast path — only the entries lock, no build coordination.
        """
        with self._entries_lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires, raw, gz = entry
            if time.monotonic() >= expires:
                return None
            return raw, gz

    def get_or_build(
        self,
        key: Hashable,
        build_fn: Callable[[], Tuple[bytes, Optional[bytes]]],
    ) -> Tuple[bytes, Optional[bytes], bool]:
        """Return ``(raw, gz, was_built)`` for ``key``.

        ``was_built`` is True iff this caller actually invoked
        ``build_fn``. The directory handler uses it to decide whether
        to fire the Issue #64 ``size_observer`` — cache hits reuse the
        size already recorded on the originating build.

        Single-flight: at most one concurrent caller per cache instance
        runs ``build_fn``; others wait on ``_build_lock`` and pick up
        the fresh entry the originating caller stored. If multiple
        keys are hot simultaneously the build lock serializes them,
        which is acceptable for this surface (in practice one or two
        keys dominate per endpoint).
        """
        # Fast path.
        hit = self.get(key)
        if hit is not None:
            self.hit_count += 1
            return hit[0], hit[1], False

        # Slow path: coalesce with any in-flight rebuild.
        with self._build_lock:
            # Re-check — another thread may have populated the cache
            # while we waited.
            hit = self.get(key)
            if hit is not None:
                self.coalesced_count += 1
                return hit[0], hit[1], False
            self.miss_count += 1
            raw, gz = build_fn()
            with self._entries_lock:
                self._entries[key] = (
                    time.monotonic() + self._ttl_s,
                    raw,
                    gz,
                )
            return raw, gz, True

    def clear(self) -> None:
        """Drop all cached entries. Used by tests; not needed in prod
        because TTL expiry handles invalidation on its own."""
        with self._entries_lock:
            self._entries.clear()

    def stats(self) -> Dict[str, int]:
        """Snapshot of observability counters."""
        return {
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "coalesced_count": self.coalesced_count,
            "entry_count": len(self._entries),
        }
