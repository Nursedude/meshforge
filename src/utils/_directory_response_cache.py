"""Short-TTL cache of serialized /api/nodes/directory bytes.

Issue #70 (steady-state GIL pile-up on /api/nodes/directory):
``_serve_directory`` produces a 35 MB JSON body that takes ~3-5 s to
``json.dumps`` plus ~0.5-2 s to ``gzip.compress`` on cloud-class CPU —
both C extensions that hold the GIL throughout. With four federation
peers polling at ~60 s cadence, occasional pileups serialize the work
sequentially behind the GIL; concurrent ``/healthz`` requests on the
same handler stall, which trips the watchdog's ``http_local_unresponsive``
signal (the same class Issue #61 used to trip, different root cause).

This cache holds the serialized + gzipped bytes for a few seconds so a
burst of federation polls or browser refreshes share one build. Two
distinct locks coalesce work without serializing reads:

* ``_entries_lock`` — held only while reading/writing the cache dict;
  every cache hit takes it briefly. Cheap.
* ``_build_lock`` — held during the actual ``build_fn()`` call. A
  concurrent miss waits here, then re-checks the cache and either
  returns the bytes the first caller just stored or builds itself.
  Holding this lock during the slow path means other endpoints (and
  cache hits on other keys, though typically there is only one) wait;
  but the GIL was already serializing the build, so total wall-time
  for those callers is unchanged — what we save is the redundant
  serialization work the other waiters used to repeat.

Keyed by the optional ``?preset=`` filter the handler honors. ``None``
is the canonical "unfiltered" key; preset filters cache independently.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple


# Cache entry: (expires_monotonic_ts, raw_bytes, gzip_bytes_or_None).
_CacheEntry = Tuple[float, bytes, Optional[bytes]]


class DirectoryResponseCache:
    """Single-flight TTL cache of ``(raw_bytes, gzip_bytes)`` tuples."""

    def __init__(self, ttl_s: float = 5.0):
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s!r}")
        self._ttl_s = ttl_s
        self._entries: Dict[Optional[str], _CacheEntry] = {}
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

    def get(self, key: Optional[str]) -> Optional[Tuple[bytes, Optional[bytes]]]:
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
        key: Optional[str],
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
        which is acceptable for this surface (in practice the
        unfiltered key dominates federation traffic).
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
