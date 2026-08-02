"""Tests for the env-gated tracemalloc heap-attribution probe.

The probe is a diagnostic that must be (a) provably inert when the env var
is absent — it will ride in the gateway on every box — and (b) actually
functional when armed, because a reader-less/half-wired detector is the #80
class. Report rendering is tested against REAL tracemalloc snapshots, not
mocks: the 2026-07-25 hosts-drift lesson is that a mock can stand in for
exactly the layer that is broken.
"""

import os
import sys
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import tracemalloc_probe as tp


@pytest.fixture(autouse=True)
def _clean_probe_state():
    """Every test starts and ends with the probe disarmed and tracing off."""
    yield
    tp.stop()
    tp._thread = None
    if tracemalloc.is_tracing():
        tracemalloc.stop()


class TestEnvGate:
    def test_unset_is_inert(self):
        assert tp._parse_env(None) == 0

    def test_empty_is_inert(self):
        assert tp._parse_env("") == 0
        assert tp._parse_env("  ") == 0

    def test_zero_is_inert(self):
        assert tp._parse_env("0") == 0

    def test_negative_is_inert(self):
        assert tp._parse_env("-3") == 0

    def test_valid_depth_passes_through(self):
        assert tp._parse_env("8") == 8
        assert tp._parse_env(" 12 ") == 12

    def test_depth_clamped_to_max(self):
        assert tp._parse_env("999") == tp.MAX_FRAMES

    def test_garbage_arms_at_default_not_silent_inert(self):
        # The operator who set the var wanted tracing; a typo must not
        # silently produce "no tracing" (degraded state reading as valid).
        assert tp._parse_env("banana") == 8

    def test_maybe_start_inert_without_env(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != tp.ENV_VAR}
        with patch.dict(os.environ, env, clear=True):
            assert tp.maybe_start_from_env(tmp_path / "r.txt") is False
        assert not tracemalloc.is_tracing()
        assert tp._thread is None


class TestArmedProbe:
    def test_arms_traces_and_is_idempotent(self, tmp_path):
        report = tmp_path / "report.txt"
        with patch.dict(os.environ, {tp.ENV_VAR: "4"}):
            assert tp.maybe_start_from_env(report) is True
            assert tracemalloc.is_tracing()
            first_thread = tp._thread
            # Second call while alive: no-op, same thread.
            assert tp.maybe_start_from_env(report) is True
            assert tp._thread is first_thread

    def test_report_renders_real_snapshots_with_growth(self, tmp_path):
        tracemalloc.start(4)
        baseline = tp._snapshot()
        baseline_ts = time.time()
        hoard = ["x" * 1000 for _ in range(2000)]  # ~2MB of real growth
        current = tp._snapshot()

        text = tp._render_report(current, baseline, baseline_ts)
        assert "traced_current=" in text
        assert "GROWTH since baseline" in text
        # The hoard allocation in THIS file must be attributed by line.
        assert "test_tracemalloc_probe.py" in text
        assert len(hoard) == 2000  # keep the hoard alive through the snapshot

    def test_report_without_baseline_says_so(self):
        tracemalloc.start(1)
        current = tp._snapshot()
        text = tp._render_report(current, None, None)
        assert "baseline=NOT_YET_TAKEN" in text
        assert "GROWTH" not in text

    def test_tick_failure_leaves_witness_and_loop_survives(self, tmp_path, caplog):
        # A failing write must log ERROR (the swallow's witness) and must
        # not propagate out of the loop thread.
        tracemalloc.start(1)
        report = tmp_path / "sub" / "report.txt"
        with patch.object(tp, "atomic_write_text",
                          side_effect=OSError("disk on fire")):
            with patch.object(tp, "BASELINE_DELAY_S", 0.01):
                tp._stop_event.clear()
                import threading
                t = threading.Thread(target=tp._probe_loop, args=(report,),
                                     daemon=True)
                t.start()
                time.sleep(0.3)
                tp._stop_event.set()
                t.join(timeout=5)
        assert not t.is_alive()
        assert any("tracemalloc probe tick failed" in r.message
                   for r in caplog.records)

    def test_probe_loop_writes_report_file(self, tmp_path):
        tracemalloc.start(1)
        report = tmp_path / "report.txt"
        with patch.object(tp, "BASELINE_DELAY_S", 0.01):
            tp._stop_event.clear()
            import threading
            t = threading.Thread(target=tp._probe_loop, args=(report,),
                                 daemon=True)
            t.start()
            deadline = time.time() + 5
            while not report.exists() and time.time() < deadline:
                time.sleep(0.05)
            tp._stop_event.set()
            t.join(timeout=5)
        assert report.exists(), "armed probe never produced a report (half-wired)"
        assert "tracemalloc_report" in report.read_text()
