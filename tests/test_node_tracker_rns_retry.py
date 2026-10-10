"""node_tracker must rejoin RNS without a restart (deferred node-tracker-join-only-giveup).

Before 2026-10-09 a degraded attach — rnsd absent at start, or open_reticulum()
returning None (wedged, or a join-only refusal during an rnsd restart) — logged
"will retry on next start" and gave up for the whole run: node discovery
silently lost until the gateway restarted. These tests drive start() with the
RNS collaborators faked at their real import sites and require the tracker to
reach a registered, connected state on its own once rnsd comes back.

Run: python3 -m pytest tests/test_node_tracker_rns_retry.py -v
"""

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from src.gateway.node_tracker import UnifiedNodeTracker


def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def fake_rns():
    rns = MagicMock(name="RNS")
    rns.Transport.path_table = {}
    with patch.dict(sys.modules, {"RNS": rns}):
        yield rns


@pytest.fixture
def tracker(tmp_path):
    with patch.object(UnifiedNodeTracker, "_load_cache"):
        t = UnifiedNodeTracker()
    t._network_topology = None
    t.RNS_RETRY_INITIAL_S = 0.02
    t.RNS_RETRY_MAX_S = 0.05
    yield t
    t.stop(timeout=2.0)


def _env(pids, open_results):
    """Patch the attach collaborators; open_reticulum yields open_results in order."""
    results = list(open_results)

    def _open(*a, **kw):
        return results.pop(0) if len(results) > 1 else results[0]

    return [
        patch("utils.gateway_diagnostic.find_rns_processes", side_effect=pids),
        patch("utils.service_check.check_rns_shared_instance", return_value=True),
        patch("utils.paths.ReticulumPaths.ensure_rns_client_configdir",
              return_value="/tmp/nt-client"),
        patch("utils.paths.ReticulumPaths.get_configured_instance_name",
              return_value="default"),
        patch("utils.rns_init.open_reticulum", side_effect=_open),
    ]


def _start(tracker, patches):
    for p in patches:
        p.start()
    try:
        tracker.start()
    except Exception:
        for p in patches:
            p.stop()
        raise
    return patches


class TestRejoinWithoutRestart:

    def test_degraded_attach_retries_until_connected(self, tracker, fake_rns):
        live = MagicMock(name="Reticulum")
        patches = _start(tracker, _env(lambda: [4242], [None, None, live]))
        try:
            assert _wait_for(lambda: tracker._rns_connected), \
                "tracker never rejoined RNS after a degraded attach"
            # Handlers registered exactly once (3 aspects), not per attempt.
            assert fake_rns.Transport.register_announce_handler.call_count == 3
            assert tracker.get_stats()["rns"] == 0
            assert tracker.get_rns_attach_state()["connected"] is True
            assert tracker.get_rns_attach_state()["attempts"] >= 3
        finally:
            for p in patches:
                p.stop()

    def test_rnsd_absent_at_start_then_appears(self, tracker, fake_rns):
        pids = iter([[], [], [777]])
        live = MagicMock(name="Reticulum")
        patches = _start(tracker, _env(
            lambda: next(pids, [777]), [live]))
        try:
            assert not tracker._rns_connected
            assert _wait_for(lambda: tracker._rns_connected), \
                "tracker never attached once rnsd appeared"
        finally:
            for p in patches:
                p.stop()

    def test_degraded_state_is_visible_while_retrying(self, tracker, fake_rns):
        tracker.RNS_RETRY_INITIAL_S = 30.0  # stay degraded for the assertion
        patches = _start(tracker, _env(lambda: [4242], [None]))
        try:
            state = tracker.get_rns_attach_state()
            assert state["connected"] is False
            assert state["retrying"] is True
            assert state["last_error"]
        finally:
            for p in patches:
                p.stop()

    def test_stop_ends_the_retry_thread(self, tracker, fake_rns):
        patches = _start(tracker, _env(lambda: [4242], [None]))
        try:
            assert _wait_for(lambda: tracker.get_rns_attach_state()["attempts"] >= 2)
            thread = tracker._rns_retry_thread
            tracker.stop(timeout=2.0)
            thread.join(timeout=2.0)
            assert not thread.is_alive()
            assert tracker.get_rns_attach_state()["retrying"] is False
        finally:
            for p in patches:
                p.stop()

    def test_rns_not_installed_does_not_retry(self, tracker):
        with patch.dict(sys.modules, {"RNS": None}):
            tracker.start()
        assert tracker.get_rns_attach_state()["retrying"] is False


class TestReviewFindings20261009:
    """Contextless review of the first cut (2026-10-09): the signal guard was
    unpinned, overlapping guards could strand a no-op, and an attach could
    finish after stop()."""

    def test_retry_attach_survives_rns_registering_signals(self, tracker, fake_rns):
        # Real RNS.Reticulum() calls signal.signal(); off the main thread
        # that raises unless the guard is in place. The first attach is on
        # the main thread (fine); the retry is not.
        import signal
        live = MagicMock(name="Reticulum")
        calls = {"n": 0}

        def _open(*a, **kw):
            calls["n"] += 1
            signal.signal(signal.SIGUSR2, signal.getsignal(signal.SIGUSR2))
            return None if calls["n"] == 1 else live

        patches = _env(lambda: [4242], [None])
        patches[-1] = patch("utils.rns_init.open_reticulum", side_effect=_open)
        _start(tracker, patches)
        try:
            assert _wait_for(lambda: tracker._rns_connected), (
                "retry attach failed — signal.signal raised off the main "
                f"thread? last_error={tracker.get_rns_attach_state()['last_error']}")
        finally:
            for p in patches:
                p.stop()

    def test_stop_during_attach_does_not_connect(self, tracker, fake_rns):
        import threading
        entered, release = threading.Event(), threading.Event()
        calls = {"n": 0}

        def _open(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            entered.set()
            release.wait(3)
            return MagicMock(name="Reticulum")

        patches = _env(lambda: [4242], [None])
        patches[-1] = patch("utils.rns_init.open_reticulum", side_effect=_open)
        _start(tracker, patches)
        try:
            assert entered.wait(3), "retry never reached open_reticulum"
            retry = tracker._rns_retry_thread
            tracker.stop(timeout=0.05)      # returns while the attach is in flight
            release.set()
            retry.join(timeout=3)
            assert tracker._rns_connected is False
            assert fake_rns.Transport.register_announce_handler.call_count == 0
        finally:
            release.set()
            for p in patches:
                p.stop()


class TestSharedSignalGuard:

    def test_overlapping_guards_restore_the_real_function(self):
        import signal
        import threading
        from src.gateway._signal_guard import suppress_signal_off_main
        real = signal.signal
        a_in, b_in, a_out = threading.Event(), threading.Event(), threading.Event()

        def a():
            with suppress_signal_off_main():
                a_in.set()
                b_in.wait(2)
            a_out.set()

        def b():
            a_in.wait(2)
            with suppress_signal_off_main():
                b_in.set()
                a_out.wait(2)      # A leaves first: the interleave that stranded a no-op
                assert signal.signal is not real

        ta, tb = threading.Thread(target=a), threading.Thread(target=b)
        ta.start(); tb.start(); ta.join(3); tb.join(3)
        assert signal.signal is real, "a no-op signal.signal was left installed"

    def test_all_three_callers_use_the_shared_guard(self):
        import inspect
        from src.gateway import _rns_bridge_connection, meshtastic_broadcast_bridge, _node_tracker_rns
        for mod in (_rns_bridge_connection, meshtastic_broadcast_bridge, _node_tracker_rns):
            src = inspect.getsource(mod)
            assert "suppress_signal_off_main" in src, mod.__name__
            assert "_signal_mod.signal = " not in src, (
                f"{mod.__name__} swaps signal.signal itself — use _signal_guard")
