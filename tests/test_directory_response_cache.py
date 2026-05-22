"""Tests for the /api/nodes/directory response cache (Issue #70).

Pinning the single-flight + TTL semantics that keep concurrent
federation polls from each paying the ~6-10 s json.dumps + gzip.compress
cost under GIL.
"""

from __future__ import annotations

import threading
import time

import pytest

from utils._directory_response_cache import DirectoryResponseCache


class TestBasics:
    def test_constructor_rejects_nonpositive_ttl(self):
        with pytest.raises(ValueError):
            DirectoryResponseCache(ttl_s=0)
        with pytest.raises(ValueError):
            DirectoryResponseCache(ttl_s=-1.0)

    def test_get_returns_none_on_empty_cache(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        assert cache.get(None) is None
        assert cache.get("any_key") is None

    def test_get_or_build_returns_built_value_on_miss(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        raw, gz, was_built = cache.get_or_build(
            None, lambda: (b'{"k": 1}', b"gzbytes")
        )
        assert raw == b'{"k": 1}'
        assert gz == b"gzbytes"
        assert was_built is True

    def test_subsequent_get_returns_cached_bytes(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw1", b"gz1"))
        hit = cache.get(None)
        assert hit == (b"raw1", b"gz1")

    def test_was_built_false_on_cache_hit(self):
        """The directory handler keys size_observer firing on was_built —
        cache hits must NOT re-fire the size observer, otherwise the
        stats cache invalidates on every request and Issue #64's
        observability cost-budget is blown."""
        cache = DirectoryResponseCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw", b"gz"))
        build_calls = []
        _, _, was_built = cache.get_or_build(
            None, lambda: build_calls.append(1) or (b"unused", None)
        )
        assert was_built is False
        assert build_calls == [], "build_fn must NOT run on a hit"


class TestTTLExpiry:
    def test_entry_expires_after_ttl(self):
        cache = DirectoryResponseCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"v1", None))
        assert cache.get(None) == (b"v1", None)
        time.sleep(0.08)
        assert cache.get(None) is None, "entry must be invisible past TTL"

    def test_expired_entry_triggers_rebuild(self):
        cache = DirectoryResponseCache(ttl_s=0.05)
        build_calls: list = []

        def _build():
            build_calls.append(time.monotonic())
            return b"v" + str(len(build_calls)).encode(), None

        cache.get_or_build(None, _build)
        time.sleep(0.08)
        raw, _, was_built = cache.get_or_build(None, _build)
        assert was_built is True
        assert raw == b"v2"
        assert len(build_calls) == 2


class TestPresetKeysIsolated:
    def test_different_keys_cache_independently(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"unfiltered", None))
        cache.get_or_build("live_rf", lambda: (b"filtered", None))
        assert cache.get(None) == (b"unfiltered", None)
        assert cache.get("live_rf") == (b"filtered", None)

    def test_one_key_expiry_does_not_affect_another(self):
        cache = DirectoryResponseCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"a", None))
        time.sleep(0.02)
        cache.get_or_build("k2", lambda: (b"b", None))
        time.sleep(0.04)
        # Now None has expired (>0.05 s old) but k2 is still fresh (~0.04 s).
        assert cache.get(None) is None
        assert cache.get("k2") == (b"b", None)


class TestSingleFlightCoalescing:
    """The headline regression guard for Issue #70.

    Before this fix, a burst of concurrent /api/nodes/directory
    requests each independently ran json.dumps + gzip.compress on the
    35 MB body — N rebuilds stacked behind the GIL. Single-flight
    means: one caller runs the build, the rest wait briefly on
    _build_lock and pick up the bytes the first caller stored.

    The test here uses a slow build_fn to give all N threads time to
    arrive at the cache. Without the lock, all N would invoke
    build_fn; with the lock, exactly one does.
    """

    def test_concurrent_misses_invoke_build_fn_once(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        build_calls = threading.Event()
        call_counter = {"n": 0}
        lock = threading.Lock()

        def _slow_build():
            with lock:
                call_counter["n"] += 1
            build_calls.set()  # let the test know we entered build_fn
            time.sleep(0.05)   # simulate the slow json.dumps + gzip
            return b"shared", b"shared_gz"

        results: list = []
        ready = threading.Barrier(8)

        def _worker():
            ready.wait()  # release all threads simultaneously
            raw, gz, was_built = cache.get_or_build(None, _slow_build)
            results.append((raw, gz, was_built))

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert call_counter["n"] == 1, (
            f"single-flight invariant broken — build_fn ran "
            f"{call_counter['n']} times for 8 concurrent misses. The whole "
            f"point of the cache is to coalesce these."
        )
        assert len(results) == 8
        # Exactly one caller saw was_built=True; the rest got the
        # coalesced (cached) value.
        built_count = sum(1 for r in results if r[2])
        assert built_count == 1, (
            f"expected exactly one was_built=True, got {built_count}"
        )
        # All callers got identical bytes — the originating caller's
        # build is the source of truth for every coalesced waiter.
        assert all(r[0] == b"shared" and r[1] == b"shared_gz" for r in results)

    def test_coalesced_count_tracks_waiters(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        ready = threading.Barrier(4)

        def _slow():
            time.sleep(0.03)
            return b"x", None

        def _worker():
            ready.wait()
            cache.get_or_build(None, _slow)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        stats = cache.stats()
        assert stats["miss_count"] == 1
        assert stats["coalesced_count"] == 3, (
            f"expected 3 waiters to coalesce, got {stats['coalesced_count']}"
        )


class TestObservabilityCounters:
    def test_hits_misses_and_entries_tracked(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        assert cache.stats() == {
            "hit_count": 0, "miss_count": 0,
            "coalesced_count": 0, "entry_count": 0,
        }
        cache.get_or_build(None, lambda: (b"a", None))   # miss
        cache.get_or_build(None, lambda: (b"unused", None))  # hit
        cache.get_or_build(None, lambda: (b"unused", None))  # hit
        cache.get_or_build("k2", lambda: (b"b", None))   # miss (new key)
        s = cache.stats()
        assert s["miss_count"] == 2
        assert s["hit_count"] == 2
        assert s["entry_count"] == 2


class TestClear:
    def test_clear_drops_all_entries(self):
        cache = DirectoryResponseCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"a", None))
        cache.get_or_build("k2", lambda: (b"b", None))
        cache.clear()
        assert cache.get(None) is None
        assert cache.get("k2") is None
