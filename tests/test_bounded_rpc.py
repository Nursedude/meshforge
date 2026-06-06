"""Tests for the gateway-hot-path bounded_call watchdog wrapper.

`bounded_call` is the substrate that turns "silent hang past init" into
"loud log + counter + WedgeEvent + process abort." Every gateway RNS
RPC call goes through this — pin every behavior the call sites depend on.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from unittest.mock import patch

import pytest

from gateway import bounded_rpc
from gateway.bounded_rpc import (
    WedgeTimeout,
    bounded_call,
    get_wedge_counters,
)
from utils import wedge_events
from utils.boundary_timing import get_boundary_stats, reset_boundary_stats


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """Wedge counters, ring buffer, and boundary stats are module-level
    singletons. Reset between tests so cases don't leak."""
    bounded_rpc._reset_counters_for_tests()
    wedge_events._reset_for_tests()
    reset_boundary_stats()
    yield
    bounded_rpc._reset_counters_for_tests()
    wedge_events._reset_for_tests()
    reset_boundary_stats()


# ── Happy paths ───────────────────────────────────────────────────────────


class TestFastPath:
    def test_returns_wrapped_fn_result(self):
        result = bounded_call(
            "test.fast", lambda x: x * 2, 21, timeout_s=10.0,
        )
        assert result == 42

    def test_propagates_kwargs(self):
        def fn(a, b=None):
            return (a, b)
        out = bounded_call("test.kwargs", fn, "x", b="y", timeout_s=10.0)
        assert out == ("x", "y")

    def test_re_raises_non_wedge_exception(self):
        """A wrapped fn raising ValueError must bubble out unchanged —
        bounded_call only intervenes on WedgeTimeout."""
        def explode():
            raise ValueError("kaboom")
        with pytest.raises(ValueError, match="kaboom"):
            bounded_call("test.raise", explode, timeout_s=10.0)

    def test_populates_boundary_timing_ring_buffer(self):
        """Regression guard: bounded_call MUST still record samples
        in the boundary_timing ring buffer that operators read for
        p50/p95/p99 dashboards. Loss would break Issue #55-era
        observability."""
        bounded_call("test.metrics", lambda: None, timeout_s=10.0)
        # get_boundary_stats("label") returns the per-label stats dict
        # directly, not {label: stats}. Pin the contract so a future
        # refactor of either API surfaces the break here.
        stats = get_boundary_stats("test.metrics")
        assert stats["count"] == 1

    def test_no_wedge_counter_bump_on_fast_call(self):
        bounded_call("test.fast2", lambda: None, timeout_s=10.0)
        assert get_wedge_counters() == {}

    def test_no_wedge_event_published_on_fast_call(self):
        bounded_call("test.fast3", lambda: None, timeout_s=10.0)
        assert wedge_events.recent() == []


# ── Wedge behavior (without _exit) ────────────────────────────────────────


class TestWedgeBehavior:
    def test_wedge_via_synthetic_exception_fires_hook(self):
        """Tests use WedgeTimeout raised by the wrapped fn to simulate a
        wedge without needing the real watchdog timer to fire. The
        on_wedge hook must still run and the exception must propagate
        when exit_on_wedge=False."""
        def synth_wedge():
            raise WedgeTimeout("synthetic")

        with pytest.raises(WedgeTimeout):
            bounded_call(
                "test.synth", synth_wedge,
                timeout_s=10.0, exit_on_wedge=False,
            )

        # Default hook bumped the counter and published.
        assert get_wedge_counters() == {"test.synth": 1}
        events = wedge_events.recent()
        assert len(events) == 1
        assert events[0].label == "test.synth"
        assert events[0].source == "gateway.rns"

    def test_real_timeout_fires_hook_without_exit(self):
        """A real slow fn (exceeds timeout_s) triggers the watchdog
        thread to fire on_wedge. With exit_on_wedge=False, the
        process survives and the test can assert on side effects."""
        captured = []

        def hook(label, target, timeout_s):
            captured.append((label, target, timeout_s))

        def slow_fn():
            time.sleep(0.3)
            return "should-not-be-reached-in-time"

        # 0.05s budget, fn sleeps 0.3s → watchdog fires at ~0.05s.
        # The fn ultimately returns (test mode, no _exit), so
        # bounded_call returns whatever it returned.
        result = bounded_call(
            "test.real_slow", slow_fn,
            timeout_s=0.05, exit_on_wedge=False, on_wedge=hook,
        )
        assert result == "should-not-be-reached-in-time"
        assert captured == [("test.real_slow", None, 0.05)]

    def test_target_passed_through_to_hook(self):
        """The destination identifier (e.g. RNS hash prefix) must
        survive into the WedgeEvent so operators can correlate the
        wedge to the destination it tried to reach."""
        def synth_wedge():
            raise WedgeTimeout("synthetic")
        with pytest.raises(WedgeTimeout):
            bounded_call(
                "test.with_target", synth_wedge,
                target="abcd1234",
                timeout_s=10.0, exit_on_wedge=False,
            )
        evt = wedge_events.recent()[0]
        assert evt.target == "abcd1234"

    def test_custom_on_wedge_replaces_default(self):
        """If caller passes a custom hook, the default (counter +
        wedge_events) is SUPERSEDED, not added on top of. Caller is
        responsible for chaining side effects."""
        called = []

        def custom_hook(label, target, timeout_s):
            called.append("custom")

        def synth_wedge():
            raise WedgeTimeout("synthetic")
        with pytest.raises(WedgeTimeout):
            bounded_call(
                "test.custom_hook", synth_wedge,
                timeout_s=10.0, on_wedge=custom_hook,
                exit_on_wedge=False,
            )
        assert called == ["custom"]
        # Default side effects did NOT happen.
        assert get_wedge_counters() == {}
        assert wedge_events.recent() == []

    def test_hook_exception_is_isolated(self):
        """A buggy hook must not prevent on_wedge from "completing"
        from the watchdog's perspective. (The exit_on_wedge=True case
        in production would proceed to abort; here we just verify
        the bounded_call returns control.)"""
        def bad_hook(label, target, timeout_s):
            raise RuntimeError("hook bug")

        def synth_wedge():
            raise WedgeTimeout("synthetic")
        with pytest.raises(WedgeTimeout):
            bounded_call(
                "test.bad_hook", synth_wedge,
                timeout_s=10.0, on_wedge=bad_hook,
                exit_on_wedge=False,
            )

    def test_wedge_timeout_error_contains_timeout_word(self):
        """RetryPolicy in message_queue.py classifies error strings
        containing 'timeout' as retriable. Persisted wedged messages
        survive the gateway restart. Pin the contract."""
        try:
            raise WedgeTimeout("rnsd.handle_outbound budget exceeded")
        except WedgeTimeout as e:
            # Both the type repr and the message must contain "timeout"
            # to match the retriable-error classifier.
            classname = type(e).__name__.lower()
            msg = str(e).lower()
            assert "timeout" in classname or "timeout" in msg

    def test_counter_distinguishes_labels(self):
        def synth():
            raise WedgeTimeout("synth")
        for _ in range(3):
            with pytest.raises(WedgeTimeout):
                bounded_call("rnsd.has_path", synth,
                             timeout_s=10.0, exit_on_wedge=False)
        for _ in range(2):
            with pytest.raises(WedgeTimeout):
                bounded_call("rnsd.handle_outbound", synth,
                             timeout_s=10.0, exit_on_wedge=False)
        assert get_wedge_counters() == {
            "rnsd.has_path": 3,
            "rnsd.handle_outbound": 2,
        }


# ── Env var override ──────────────────────────────────────────────────────


class TestEnvOverride:
    def test_env_override_uses_env_value(self, monkeypatch):
        """Operator escape hatch — env var bumps the compiled-in
        timeout without redeploy. Verified by watching the watchdog
        fire at the env'd value rather than the arg."""
        monkeypatch.setenv(
            "MESHFORGE_BOUNDED_RPC_TIMEOUT_TEST_OVERRIDE", "0.05",
        )

        def slow_fn():
            time.sleep(0.3)

        captured = []

        def hook(label, target, timeout_s):
            captured.append(timeout_s)

        # Arg says 10s — env says 0.05s. Env wins.
        bounded_call(
            "test.override", slow_fn,
            timeout_s=10.0, exit_on_wedge=False, on_wedge=hook,
        )
        assert captured == [0.05]

    def test_env_override_label_normalization(self, monkeypatch):
        """`rnsd.handle_outbound` → `MESHFORGE_BOUNDED_RPC_TIMEOUT_RNSD_HANDLE_OUTBOUND`.
        Dots and hyphens both become underscores; everything uppercased."""
        monkeypatch.setenv(
            "MESHFORGE_BOUNDED_RPC_TIMEOUT_RNSD_HANDLE_OUTBOUND", "0.05",
        )

        captured = []

        def hook(label, target, timeout_s):
            captured.append(timeout_s)

        def slow():
            time.sleep(0.3)

        bounded_call(
            "rnsd.handle_outbound", slow,
            timeout_s=15.0, exit_on_wedge=False, on_wedge=hook,
        )
        assert captured == [0.05]

    def test_env_override_malformed_value_ignored(self, monkeypatch, caplog):
        """A bad env value (e.g. typo, negative, non-numeric) must
        fall back to the compiled-in timeout — not crash, not silently
        misbehave."""
        monkeypatch.setenv(
            "MESHFORGE_BOUNDED_RPC_TIMEOUT_TEST_BADENV", "not-a-number",
        )

        with caplog.at_level(logging.WARNING, logger="gateway.bounded_rpc"):
            result = bounded_call(
                "test.badenv", lambda: 42, timeout_s=10.0,
            )
        assert result == 42
        # Warning was logged about the malformed value.
        assert any(
            "malformed" in r.message.lower()
            for r in caplog.records
        )

    def test_env_override_negative_ignored(self, monkeypatch):
        """Negative timeouts make no sense; fall back to the arg."""
        monkeypatch.setenv(
            "MESHFORGE_BOUNDED_RPC_TIMEOUT_TEST_NEG", "-1.0",
        )
        result = bounded_call("test.neg", lambda: 99, timeout_s=10.0)
        assert result == 99


# ── Threshold vs timeout independence ─────────────────────────────────────


class TestThresholdVsTimeout:
    """`threshold_s` (slow-call WARNING threshold from call_boundary)
    and `timeout_s` (hard watchdog) are independent. A call slower
    than threshold but faster than timeout must log WARNING; a call
    slower than timeout must trigger the watchdog."""

    def test_slow_below_timeout_records_slow_count(self, caplog):
        """Call that exceeds threshold but completes under timeout
        produces the existing 'slow' WARNING + sample count."""
        def kinda_slow():
            time.sleep(0.05)

        with caplog.at_level(logging.WARNING, logger="utils.boundary_timing"):
            bounded_call(
                "test.threshold", kinda_slow,
                timeout_s=10.0, threshold_s=0.01,
            )
        assert any("slow" in r.message.lower() for r in caplog.records)
        assert get_wedge_counters() == {}


# ── exit_on_wedge default semantics ───────────────────────────────────────


class TestExitOnWedgeDefault:
    """exit_on_wedge defaults to True — production semantics. Tests
    that don't override it must NOT actually call os._exit (would
    kill the test runner). Patch the _exit symbol bounded_rpc uses
    and assert it would have been called."""

    def test_default_exit_on_wedge_true_calls_exit(self):
        with patch("gateway.bounded_rpc.os._exit") as mock_exit:
            def synth():
                raise WedgeTimeout("synthetic")
            # Don't suppress the exception either way; we're checking
            # that _exit would have run before propagation.
            with pytest.raises(WedgeTimeout):
                bounded_call("test.default_exit", synth, timeout_s=10.0)
            mock_exit.assert_called_once_with(2)


class TestNoExitEnvBackstopIssue74:
    """MESHFORGE_BOUNDED_RPC_NO_EXIT=1 — incident-response kill switch
    for the wedge abort, plus the outstanding-wedge gauge that keeps
    the suppressed aborts visible (each is a thread blocked in a hung
    call that os._exit would normally reap)."""

    def test_backstop_suppresses_exit_on_synthetic_wedge(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_BOUNDED_RPC_NO_EXIT", "1")
        with patch("gateway.bounded_rpc.os._exit") as mock_exit:
            def synth():
                raise WedgeTimeout("synthetic")
            with pytest.raises(WedgeTimeout):
                bounded_call("test.backstop", synth, timeout_s=10.0)
            mock_exit.assert_not_called()
        # The wedge EVENT still counts — only the abort is suppressed.
        assert get_wedge_counters().get("test.backstop") == 1

    def test_backstop_real_timeout_tracks_outstanding_gauge(self, monkeypatch):
        """Watchdog fires, abort suppressed → gauge goes 1 while the
        call is wedged, returns to 0 when it finally completes."""
        monkeypatch.setenv("MESHFORGE_BOUNDED_RPC_NO_EXIT", "1")
        release = threading.Event()
        result = {}

        with patch("gateway.bounded_rpc.os._exit") as mock_exit:
            def hang():
                release.wait(5.0)
                return "done"

            def worker():
                result["r"] = bounded_call(
                    "test.outstanding", hang,
                    timeout_s=0.05, exit_on_wedge=True,
                )

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            deadline = time.monotonic() + 3.0
            while (bounded_rpc.get_outstanding_wedges() != 1
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            assert bounded_rpc.get_outstanding_wedges() == 1, (
                "suppressed abort must surface as an outstanding wedge"
            )
            mock_exit.assert_not_called()

            release.set()
            t.join(timeout=3.0)

        assert result.get("r") == "done"
        assert bounded_rpc.get_outstanding_wedges() == 0, (
            "late completion must return the gauge unit"
        )

    def test_backstop_unset_means_exit_still_fires(self):
        """Default (env unset) preserves production abort semantics."""
        assert bounded_rpc._no_exit_env() is False
        with patch("gateway.bounded_rpc.os._exit") as mock_exit:
            def synth():
                raise WedgeTimeout("synthetic")
            with pytest.raises(WedgeTimeout):
                bounded_call("test.backstop_off", synth, timeout_s=10.0)
            mock_exit.assert_called_once_with(2)

    def test_backstop_garbage_value_means_off(self, monkeypatch):
        monkeypatch.setenv("MESHFORGE_BOUNDED_RPC_NO_EXIT", "banana")
        assert bounded_rpc._no_exit_env() is False
