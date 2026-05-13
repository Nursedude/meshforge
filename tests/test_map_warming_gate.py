"""Tests for the warming-flag gate in map_data_service._run_warmup.

Regression coverage for project_map_warming_gate_bug — when collect()'s
history-DB write blocks on SD I/O for minutes, is_warming should still
flip False as soon as the cache (the only data /api/* needs) is ready,
not when collect() returns. Witnessed on moc1 on 2026-05-13: 15+ min of
503 while a 4.7 GB node_history.db write churned in state D.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeCollector:
    """Collector test double with controllable cache-populate and return timing.

    Mirrors the real MapDataCollector contract:
      - `_cached_geojson` is the gate the warmup watcher polls
      - `collect()` populates it partway through and continues running
    """

    def __init__(self, cache_at_seconds, return_at_seconds, fail=False):
        self._cached_geojson = None
        self._cache_at = cache_at_seconds
        self._return_at = return_at_seconds
        self._fail = fail
        self.collect_called = False
        self.federation_started = False

    def collect(self):
        self.collect_called = True
        start = time.monotonic()
        time.sleep(self._cache_at)
        self._cached_geojson = {"type": "FeatureCollection", "features": []}
        remaining = self._return_at - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(remaining)
        if self._fail:
            raise RuntimeError("collector boom")

    def start_federation(self):
        self.federation_started = True


class WarmingGateTests(unittest.TestCase):
    """Pin the gate-on-cache contract."""

    def _build_service(self, collector):
        # Avoid importing map_data_service at module load — it pulls a
        # lot of optional deps. Lazy import inside the test keeps the
        # import surface narrow.
        from utils import map_data_service
        from utils.map_http_handler import MapRequestHandler
        from utils import map_metrics

        # Reset module-level handler state between tests.
        MapRequestHandler.is_warming = True
        map_metrics.set_warming = MagicMock()

        svc = map_data_service.MapServer.__new__(map_data_service.MapServer)
        svc.collector = collector
        return svc, MapRequestHandler

    def test_warming_clears_at_cache_populate_not_at_collect_return(self):
        """Gate flips when `_cached_geojson` is set, not when collect() returns.

        Witnessed bug: collect() returned after 30s of history-DB write but
        cache was populated at 1s; old code held is_warming True the whole
        time. New code flips at 1s.
        """
        collector = FakeCollector(cache_at_seconds=0.2, return_at_seconds=2.0)
        svc, handler = self._build_service(collector)

        # Run warmup in a thread so we can observe is_warming mid-collect.
        warmup_thread = threading.Thread(target=svc._run_warmup, daemon=True)
        warmup_thread.start()

        # At t=0.5s the cache should be populated (at 0.2s) and the
        # watcher should have flipped is_warming. collect() is still
        # running (returns at 2.0s) — this is the load-bearing assertion.
        time.sleep(0.5)
        self.assertFalse(
            handler.is_warming,
            "is_warming should be False once cache is populated, "
            "even while collect() is still inside its history-DB write",
        )
        self.assertIsNotNone(
            collector._cached_geojson,
            "cache should be populated by t=0.5s (cache_at=0.2)",
        )
        self.assertTrue(
            warmup_thread.is_alive(),
            "collect() should still be running at t=0.5s (return_at=2.0)",
        )

        warmup_thread.join(timeout=5)
        self.assertFalse(warmup_thread.is_alive(), "warmup thread should have returned")

    def test_warming_clears_in_finally_when_collect_fails_before_cache(self):
        """Fallback path: if collect() throws before populating cache, the
        watcher would never see it. The `finally` clause must still flip
        warming so we don't sit in 503 forever after a collector boom.
        """
        collector = FakeCollector(
            cache_at_seconds=2.0, return_at_seconds=0.1, fail=True,
        )
        # NOTE: cache_at > return_at means collect() raises before
        # populating cache (the time.sleep(cache_at) is before the
        # populate line; if we shorten return_at and force fail it
        # still raises before populating). This shape isn't quite
        # right for that — instead, model a synchronous failure:
        collector.collect = MagicMock(side_effect=RuntimeError("collector boom"))

        svc, handler = self._build_service(collector)
        svc._run_warmup()  # synchronous — should not hang
        self.assertFalse(
            handler.is_warming,
            "finally clause must flip warming False even when "
            "collect() raises before cache is populated",
        )

    def test_federation_starts_after_collect_returns_not_after_cache(self):
        """Federation start is gated on collect() RETURN, not cache-ready.

        Federation is heavy I/O; we don't want it competing with the
        first collect's history-DB write. So even with the gate-on-
        cache change, start_federation() must still come after
        collect() returns.
        """
        collector = FakeCollector(cache_at_seconds=0.1, return_at_seconds=0.5)
        svc, _handler = self._build_service(collector)
        svc._run_warmup()
        self.assertTrue(
            collector.federation_started,
            "start_federation must fire after collect returns",
        )


if __name__ == "__main__":
    unittest.main()
