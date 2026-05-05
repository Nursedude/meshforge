"""Tests for utils.boundary_timing.

The helper is the shared floor for every cross-process call site in
MeshAnchor (see .claude/plans/boundary_observability_charter.md).
A regression here would silently degrade the diagnostic floor across
the whole codebase, so the test surface is intentionally generous:
happy path, slow path, exception path, threshold logic, counter
accuracy, percentile correctness, label formatting, and concurrency.

Run: python3 -m pytest tests/test_boundary_timing.py -v
"""
from __future__ import annotations

import logging
import threading
import time
from unittest.mock import patch

import pytest

from utils.boundary_timing import (
    DEFAULT_THRESHOLD_S,
    call_boundary,
    get_boundary_stats,
    reset_boundary_stats,
    timed_boundary,
)


@pytest.fixture(autouse=True)
def _clear_stats():
    """Each test starts with empty counters/samples."""
    reset_boundary_stats()
    yield
    reset_boundary_stats()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:

    def test_fast_call_records_count(self):
        with timed_boundary("test.fast"):
            pass
        stats = get_boundary_stats("test.fast")
        assert stats["count"] == 1
        assert stats["slow_count"] == 0
        assert stats["error_count"] == 0

    def test_fast_call_logs_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="boundary"):
            with timed_boundary("test.fast"):
                pass
        # No WARN-level records
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)
        # At least one DEBUG record mentions our label
        debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("test.fast" in m and "ok" in m for m in debug_msgs)

    def test_call_boundary_returns_fn_result(self):
        result = call_boundary("test.call", lambda x, y: x + y, 2, 3)
        assert result == 5

    def test_call_boundary_passes_kwargs(self):
        def fn(a, b, c=0):
            return a + b + c
        result = call_boundary("test.kwargs", fn, 1, 2, c=10)
        assert result == 13


# ---------------------------------------------------------------------------
# Slow path
# ---------------------------------------------------------------------------

class TestSlowPath:

    def test_slow_call_increments_slow_count(self):
        # Use threshold near zero so any real elapsed time trips it.
        with timed_boundary("test.slow", threshold_s=0.0):
            time.sleep(0.001)
        stats = get_boundary_stats("test.slow")
        assert stats["count"] == 1
        assert stats["slow_count"] == 1
        assert stats["error_count"] == 0

    def test_slow_call_logs_at_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="boundary"):
            with timed_boundary("test.slow_warn", threshold_s=0.0):
                time.sleep(0.001)
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("test.slow_warn" in m and "slow" in m for m in warn_msgs)

    def test_threshold_above_elapsed_does_not_count_as_slow(self):
        with timed_boundary("test.fast_under_threshold", threshold_s=10.0):
            pass  # ~microseconds
        stats = get_boundary_stats("test.fast_under_threshold")
        assert stats["slow_count"] == 0


# ---------------------------------------------------------------------------
# Exception path
# ---------------------------------------------------------------------------

class TestExceptionPath:

    def test_exception_propagates(self):
        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with timed_boundary("test.boom"):
                raise Boom("kaboom")

    def test_exception_increments_error_count(self):
        with pytest.raises(ValueError):
            with timed_boundary("test.err"):
                raise ValueError("bad")
        stats = get_boundary_stats("test.err")
        assert stats["count"] == 1
        assert stats["error_count"] == 1
        # Errors do not also count as slow even if they were over threshold.
        assert stats["slow_count"] == 0

    def test_exception_logs_at_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="boundary"):
            with pytest.raises(RuntimeError):
                with timed_boundary("test.err_log"):
                    raise RuntimeError("nope")
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("test.err_log" in m and "raised" in m for m in warn_msgs)

    def test_call_boundary_propagates_exception(self):
        def boom():
            raise KeyError("missing")

        with pytest.raises(KeyError):
            call_boundary("test.call_err", boom)
        stats = get_boundary_stats("test.call_err")
        assert stats["error_count"] == 1


# ---------------------------------------------------------------------------
# Label formatting
# ---------------------------------------------------------------------------

class TestLabelFormatting:

    def test_target_appended_to_label(self):
        with timed_boundary("rnsd.has_path", target="abc12345"):
            pass
        stats = get_boundary_stats()
        assert "rnsd.has_path[abc12345]" in stats

    def test_no_target_uses_bare_label(self):
        with timed_boundary("rnsd.has_path"):
            pass
        stats = get_boundary_stats()
        assert "rnsd.has_path" in stats
        assert "rnsd.has_path[]" not in stats

    def test_different_targets_record_separately(self):
        with timed_boundary("rnsd.send", target="aa"):
            pass
        with timed_boundary("rnsd.send", target="bb"):
            pass
        stats = get_boundary_stats()
        assert stats["rnsd.send[aa]"]["count"] == 1
        assert stats["rnsd.send[bb]"]["count"] == 1


# ---------------------------------------------------------------------------
# Counters and percentiles
# ---------------------------------------------------------------------------

class TestCountersAndPercentiles:

    def test_counts_accumulate(self):
        for _ in range(5):
            with timed_boundary("test.repeat"):
                pass
        stats = get_boundary_stats("test.repeat")
        assert stats["count"] == 5

    def test_percentiles_with_known_samples(self):
        # Drive deterministic samples by patching time.monotonic.
        # Sequence: each enter/exit pair reads monotonic twice.
        # We provide pairs (t0, t1) so elapsed = 0.0, 0.1, 0.2, ..., 0.9
        # → samples = [0.0, 0.1, 0.2, ..., 0.9]
        ticks = []
        for i in range(10):
            ticks.append(0.0)         # enter
            ticks.append(i * 0.1)     # exit
        with patch("utils.boundary_timing.time.monotonic", side_effect=ticks):
            for _ in range(10):
                with timed_boundary("test.pctl", threshold_s=10.0):
                    pass
        stats = get_boundary_stats("test.pctl")
        assert stats["count"] == 10
        # Sorted samples are [0.0, 0.1, ..., 0.9]
        # Nearest-rank: p50 → idx 5 → 0.5, p95 → idx 9 → 0.9, p99 → idx 9 → 0.9
        assert stats["p50_s"] == pytest.approx(0.5, abs=1e-9)
        assert stats["p95_s"] == pytest.approx(0.9, abs=1e-9)
        assert stats["p99_s"] == pytest.approx(0.9, abs=1e-9)

    def test_empty_stats_zero_percentiles(self):
        # Querying a label with no samples returns zeros, not crash.
        stats = get_boundary_stats("never.observed")
        assert stats["count"] == 0
        assert stats["p50_s"] == 0.0
        assert stats["p95_s"] == 0.0
        assert stats["p99_s"] == 0.0
        assert stats["samples"] == 0

    def test_get_all_returns_every_label(self):
        with timed_boundary("a.x"):
            pass
        with timed_boundary("b.y"):
            pass
        stats = get_boundary_stats()
        assert set(stats.keys()) >= {"a.x", "b.y"}


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_all(self):
        with timed_boundary("reset.a"):
            pass
        with timed_boundary("reset.b"):
            pass
        reset_boundary_stats()
        stats = get_boundary_stats()
        assert stats == {}

    def test_reset_single_label(self):
        with timed_boundary("keep.me"):
            pass
        with timed_boundary("drop.me"):
            pass
        reset_boundary_stats("drop.me")
        stats = get_boundary_stats()
        assert "keep.me" in stats
        assert "drop.me" not in stats


# ---------------------------------------------------------------------------
# Defaults and concurrency
# ---------------------------------------------------------------------------

class TestDefaults:

    def test_default_threshold_constant(self):
        # The charter calls out 2.0s as the project-wide default.
        # If this changes, every boundary inherits a new floor.
        assert DEFAULT_THRESHOLD_S == 2.0


class TestConcurrency:

    def test_threadsafe_count(self):
        # Many threads incrementing the same boundary should produce an
        # exact count — the lock around counters/samples prevents lost
        # increments. 8 threads × 200 calls = 1600 expected.
        n_threads = 8
        per_thread = 200

        def worker():
            for _ in range(per_thread):
                with timed_boundary("concurrent.x"):
                    pass

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = get_boundary_stats("concurrent.x")
        assert stats["count"] == n_threads * per_thread
