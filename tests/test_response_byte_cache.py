"""Tests for the generalized response-byte cache (Issue #71 / GitHub #1168).

Mirrors `tests/test_directory_response_cache.py` against the new
``ResponseByteCache`` name. The class is generic — Issue #70's
directory cache, Issue #71's geojson + topology caches, and any
future big-body endpoint share the implementation, so the contracts
pinned here cover all three.

Two extras vs. the Issue #70 file:

1. ``TestMultiInstanceIsolation`` — under Issue #70 there was only one
   cache instance (directory). Under Issue #71 the collector owns
   three; the regression we're pinning is that two ``ResponseByteCache``
   objects don't share state through any class-level surface.
2. ``TestTupleKeys`` — geojson keys on a ``(bbox, region, preset)``
   tuple. The directory cache only ever used ``str | None``. Tuple
   keys are hashable so this works, but pin it.
"""

from __future__ import annotations

import threading
import time

import pytest

from utils._response_byte_cache import ResponseByteCache


class TestBasics:
    def test_constructor_rejects_nonpositive_ttl(self):
        with pytest.raises(ValueError):
            ResponseByteCache(ttl_s=0)
        with pytest.raises(ValueError):
            ResponseByteCache(ttl_s=-1.0)

    def test_get_returns_none_on_empty_cache(self):
        cache = ResponseByteCache(ttl_s=5.0)
        assert cache.get(None) is None
        assert cache.get("any_key") is None

    def test_get_or_build_returns_built_value_on_miss(self):
        cache = ResponseByteCache(ttl_s=5.0)
        raw, gz, was_built = cache.get_or_build(
            None, lambda: (b'{"k": 1}', b"gzbytes")
        )
        assert raw == b'{"k": 1}'
        assert gz == b"gzbytes"
        assert was_built is True

    def test_subsequent_get_returns_cached_bytes(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw1", b"gz1"))
        hit = cache.get(None)
        assert hit == (b"raw1", b"gz1")

    def test_was_built_false_on_cache_hit(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"raw", b"gz"))
        build_calls = []
        _, _, was_built = cache.get_or_build(
            None, lambda: build_calls.append(1) or (b"unused", None)
        )
        assert was_built is False
        assert build_calls == [], "build_fn must NOT run on a hit"


class TestTTLExpiry:
    def test_entry_expires_after_ttl(self):
        cache = ResponseByteCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"v1", None))
        assert cache.get(None) == (b"v1", None)
        time.sleep(0.08)
        assert cache.get(None) is None, "entry must be invisible past TTL"

    def test_expired_entry_triggers_rebuild(self):
        cache = ResponseByteCache(ttl_s=0.05)
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


class TestKeysIsolated:
    def test_different_keys_cache_independently(self):
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"unfiltered", None))
        cache.get_or_build("live_rf", lambda: (b"filtered", None))
        assert cache.get(None) == (b"unfiltered", None)
        assert cache.get("live_rf") == (b"filtered", None)

    def test_one_key_expiry_does_not_affect_another(self):
        cache = ResponseByteCache(ttl_s=0.05)
        cache.get_or_build(None, lambda: (b"a", None))
        time.sleep(0.02)
        cache.get_or_build("k2", lambda: (b"b", None))
        time.sleep(0.04)
        # Now None has expired (>0.05 s old) but k2 is still fresh (~0.04 s).
        assert cache.get(None) is None
        assert cache.get("k2") == (b"b", None)


class TestTupleKeys:
    """Geojson keys on ``(bbox_str, region_key, preset_key)`` — pin that
    tuple keys round-trip through the entry dict the same way string
    keys do under Issue #70's directory cache."""

    def test_tuple_key_round_trips(self):
        cache = ResponseByteCache(ttl_s=5.0)
        key = (None, None, "live_rf")
        cache.get_or_build(key, lambda: (b"a", None))
        assert cache.get(key) == (b"a", None)

    def test_distinct_tuple_keys_cache_independently(self):
        cache = ResponseByteCache(ttl_s=5.0)
        k_a = ("21.0,-158.0,22.0,-157.0", None, None)
        k_b = ("19.0,-156.0,20.0,-155.0", None, None)
        cache.get_or_build(k_a, lambda: (b"oahu", None))
        cache.get_or_build(k_b, lambda: (b"hawaii", None))
        assert cache.get(k_a) == (b"oahu", None)
        assert cache.get(k_b) == (b"hawaii", None)


class TestSingleFlightCoalescing:
    """The headline regression guard — applies to all three endpoints
    that share this class. Without single-flight, a burst of concurrent
    requests each rebuilds independently and stacks behind the GIL."""

    def test_concurrent_misses_invoke_build_fn_once(self):
        cache = ResponseByteCache(ttl_s=5.0)
        call_counter = {"n": 0}
        lock = threading.Lock()

        def _slow_build():
            with lock:
                call_counter["n"] += 1
            time.sleep(0.05)   # simulate slow json.dumps + gzip
            return b"shared", b"shared_gz"

        results: list = []
        ready = threading.Barrier(8)

        def _worker():
            ready.wait()
            raw, gz, was_built = cache.get_or_build(None, _slow_build)
            results.append((raw, gz, was_built))

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert call_counter["n"] == 1, (
            f"single-flight invariant broken — build_fn ran "
            f"{call_counter['n']} times for 8 concurrent misses."
        )
        assert len(results) == 8
        built_count = sum(1 for r in results if r[2])
        assert built_count == 1, (
            f"expected exactly one was_built=True, got {built_count}"
        )
        assert all(r[0] == b"shared" and r[1] == b"shared_gz" for r in results)

    def test_coalesced_count_tracks_waiters(self):
        cache = ResponseByteCache(ttl_s=5.0)
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
        assert stats["coalesced_count"] == 3


class TestObservabilityCounters:
    def test_hits_misses_and_entries_tracked(self):
        cache = ResponseByteCache(ttl_s=5.0)
        assert cache.stats() == {
            "hit_count": 0, "miss_count": 0,
            "coalesced_count": 0, "warmed_count": 0, "entry_count": 0,
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
        cache = ResponseByteCache(ttl_s=5.0)
        cache.get_or_build(None, lambda: (b"a", None))
        cache.get_or_build("k2", lambda: (b"b", None))
        cache.clear()
        assert cache.get(None) is None
        assert cache.get("k2") is None


class TestMultiInstanceIsolation:
    """Issue #71 puts THREE caches on one collector. Pin that they
    don't share state — Issue #70 had a single instance so this was
    never load-bearing. Counter shape is verified via stats() because
    the in-process attrs are intended to be cheap reads."""

    def test_two_caches_track_counters_independently(self):
        a = ResponseByteCache(ttl_s=5.0)
        b = ResponseByteCache(ttl_s=5.0)
        a.get_or_build(None, lambda: (b"a", None))   # miss on a
        a.get_or_build(None, lambda: (b"unused", None))  # hit on a
        b.get_or_build(None, lambda: (b"b", None))   # miss on b
        assert a.stats() == {
            "hit_count": 1, "miss_count": 1,
            "coalesced_count": 0, "warmed_count": 0, "entry_count": 1,
        }
        assert b.stats() == {
            "hit_count": 0, "miss_count": 1,
            "coalesced_count": 0, "warmed_count": 0, "entry_count": 1,
        }

    def test_clear_on_one_does_not_affect_the_other(self):
        a = ResponseByteCache(ttl_s=5.0)
        b = ResponseByteCache(ttl_s=5.0)
        a.get_or_build(None, lambda: (b"a", None))
        b.get_or_build(None, lambda: (b"b", None))
        a.clear()
        assert a.get(None) is None
        assert b.get(None) == (b"b", None)


class TestBackCompatAlias:
    """``DirectoryResponseCache`` was the original name (Issue #70) and
    is imported by callers + the existing test file. Issue #71 renamed
    the class; pin that the back-compat alias resolves to the same
    object so neither name diverges from the other."""

    def test_alias_resolves_to_response_byte_cache(self):
        from utils._directory_response_cache import DirectoryResponseCache
        from utils._response_byte_cache import ResponseByteCache
        assert DirectoryResponseCache is ResponseByteCache

    def test_alias_constructed_instance_behaves_identically(self):
        from utils._directory_response_cache import DirectoryResponseCache
        cache = DirectoryResponseCache(ttl_s=5.0)
        raw, gz, was_built = cache.get_or_build(
            None, lambda: (b"x", None)
        )
        assert (raw, gz, was_built) == (b"x", None, True)
        # Second call is a hit — proves it's the real class, not a stub.
        _, _, was_built2 = cache.get_or_build(
            None, lambda: (b"unused", None)
        )
        assert was_built2 is False
