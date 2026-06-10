"""Regression guard for the daemon-mode shutdown deadlock.

Reliability backlog #3 — moc2 graceful-shutdown futex_wait hang
(2026-05-18). `meshforge-map.service` got stuck in `deactivating` 5+
min mid-shutdown. Sub-systems (MQTT/MessageListener/WebSocket) stopped
cleanly but the main thread refused to join (`futex_wait` per
`/proc/PID/stack`), past systemd's `TimeoutStopSec=5min` — needed
manual SIGKILL.

Root cause: the SIGTERM handler in `main()` called `server.stop()`
which calls `_server.shutdown()`. `BaseServer.shutdown()` must run on
a different thread from `serve_forever()` (stdlib invariant — the
docstring explicitly says so). The daemon path runs `serve_forever()`
on the main thread, and Python routes signals to the main thread, so
the handler-as-written deadlocked on `__is_shut_down.wait()` waiting
for a `serve_forever` loop iteration that couldn't happen until the
handler returned.

Fix: handler spawns a daemon thread to call `server.stop()`. Main
thread stays free to notice `__shutdown_request` and return from
`serve_forever()` naturally.

These tests pin the handler's behavior so a future cleanup can't
silently regress to the deadlocking single-thread pattern.
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

from utils.map_data_service import _build_daemon_signal_handler  # noqa: E402


class TestHandlerDoesNotBlockCaller(unittest.TestCase):
    """The handler MUST return promptly even if server.stop() blocks.

    This is the regression guard. The pre-fix handler called
    server.stop() inline, which on the daemon path meant the calling
    (main) thread blocked inside `_server.shutdown()` — never returning
    until SIGKILL. The fixed handler spawns a thread and returns.
    """

    def test_handler_returns_when_server_stop_blocks_indefinitely(self):
        server = MagicMock()
        # Simulate the deadlock: server.stop() never returns. If the
        # handler calls it inline (the bug), this test hangs forever
        # and pytest kills it with a timeout. Pass = handler returns
        # in <2s even though stop() is still blocked.
        stop_running = threading.Event()
        release_stop = threading.Event()

        def slow_stop():
            stop_running.set()
            release_stop.wait(timeout=30)  # bounded so test cleans up

        server.stop.side_effect = slow_stop

        handler = _build_daemon_signal_handler(server, "/tmp/nope.pid")

        # Invoke the handler; assert it returns quickly.
        import signal
        handler_done = threading.Event()

        def call_handler():
            handler(signal.SIGTERM, None)
            handler_done.set()

        caller = threading.Thread(target=call_handler, daemon=True)
        caller.start()

        # Handler must return within 2s — well under the 30s slow_stop
        # would otherwise hold things up. Pre-fix: this hangs.
        self.assertTrue(
            handler_done.wait(timeout=2.0),
            "Handler did not return — it likely called server.stop() inline "
            "on the calling thread (the pre-fix deadlock).",
        )

        # And separately verify the shutdown thread did actually start.
        self.assertTrue(
            stop_running.wait(timeout=2.0),
            "Shutdown thread never ran server.stop().",
        )

        # Cleanup: release the fake stop so the shutdown thread exits,
        # then JOIN it. Leaking it poisoned the sibling thread-shape
        # test under load: the leftover "map-shutdown" name landed in
        # its before-snapshot and the new-thread filter saw nothing
        # (false-fail caught during the 2026-06-09 split-arc suite runs).
        release_stop.set()
        caller.join(timeout=5.0)
        for t in threading.enumerate():
            if t.name == "map-shutdown":
                t.join(timeout=5.0)

    def test_handler_spawns_daemon_thread_named_map_shutdown(self):
        """Lock the thread shape — name + daemon flag — so a future
        change can't accidentally make it a non-daemon thread (which
        would block process exit) or rename it (operators grep for
        this name in `ps` output during a hung shutdown)."""
        server = MagicMock()
        proceed = threading.Event()
        server.stop.side_effect = lambda: proceed.wait(timeout=5)

        handler = _build_daemon_signal_handler(server, "/tmp/nope.pid")

        # Snapshot by thread IDENTITY, not name — a leaked same-named
        # thread from another test (or this one re-run) must not be able
        # to mask the newly spawned thread from the filter below.
        before = set(threading.enumerate())
        import signal
        handler(signal.SIGTERM, None)

        # Give the spawn a moment.
        deadline = time.monotonic() + 1.0
        new_threads = set()
        while time.monotonic() < deadline:
            new_threads = {
                t for t in threading.enumerate() if t not in before
            }
            if new_threads:
                break
            time.sleep(0.01)

        shutdown_threads = [t for t in new_threads if t.name == "map-shutdown"]
        self.assertEqual(
            len(shutdown_threads), 1,
            "Expected exactly one new 'map-shutdown' thread, got: "
            f"{[t.name for t in new_threads]}",
        )
        self.assertTrue(
            shutdown_threads[0].daemon,
            "Shutdown thread must be a daemon so a stuck stop() doesn't "
            "block process exit at TimeoutStopSec.",
        )

        proceed.set()
        shutdown_threads[0].join(timeout=5.0)


class TestSecondSignalEscalates(unittest.TestCase):
    """First signal → orderly shutdown. Second signal during shutdown
    → `os._exit(1)`. The escalation exists because if the shutdown
    thread itself wedges, the operator's only remaining lever is
    another SIGTERM (or SIGKILL from systemd). Honor it."""

    def test_second_signal_calls_os_exit(self):
        server = MagicMock()
        # Block stop() so shutdown is "in progress" when the second
        # signal arrives.
        block_stop = threading.Event()
        server.stop.side_effect = lambda: block_stop.wait(timeout=10)

        handler = _build_daemon_signal_handler(server, "/tmp/nope.pid")

        import signal
        # First signal: orderly path. Spawns shutdown thread, returns.
        handler(signal.SIGTERM, None)

        # Second signal must escalate to os._exit. We can't actually
        # exit the test process, so patch os._exit and verify it was
        # called with the right code.
        from unittest.mock import patch
        with patch("utils.map_data_service.os._exit") as mock_exit:
            handler(signal.SIGTERM, None)
            mock_exit.assert_called_once_with(1)

        block_stop.set()


class TestStopRunsServerStopAndRemovesPidFile(unittest.TestCase):
    """The shutdown thread must call server.stop() AND remove the PID
    file. The pre-fix handler did both inline; the fix moves both into
    `_do_shutdown`. Lose either step and we leak — a stale PID file
    keeps `_check_server_status` reporting "running" after exit."""

    def test_do_shutdown_calls_stop_then_removes_pid(self):
        server = MagicMock()
        pid_file = "/tmp/test-map-shutdown.pid"

        # Create the PID file so we can verify removal.
        Path(pid_file).write_text("99999\n")
        self.addCleanup(lambda: Path(pid_file).unlink(missing_ok=True))

        handler = _build_daemon_signal_handler(server, pid_file)

        # Invoke the inner _do_shutdown directly to assert ordering.
        handler._do_shutdown()

        server.stop.assert_called_once()
        self.assertFalse(
            Path(pid_file).exists(),
            "PID file must be removed by the shutdown thread.",
        )

    def test_do_shutdown_removes_pid_even_when_stop_raises(self):
        server = MagicMock()
        server.stop.side_effect = RuntimeError("simulated cleanup error")
        pid_file = "/tmp/test-map-shutdown-err.pid"

        Path(pid_file).write_text("99999\n")
        self.addCleanup(lambda: Path(pid_file).unlink(missing_ok=True))

        handler = _build_daemon_signal_handler(server, pid_file)
        handler._do_shutdown()  # must not raise

        self.assertFalse(
            Path(pid_file).exists(),
            "PID file removal must run in finally — a stale PID after a "
            "crash during stop() would make the next `--status` check lie.",
        )


class TestRealServerStopFromBackgroundThread(unittest.TestCase):
    """End-to-end-ish: spin a real ThreadingHTTPServer in a background
    thread and confirm that calling its `shutdown()` from a *separate*
    thread (which is what our fix does) completes within a reasonable
    window. This is the positive control proving the chosen pattern
    actually works against the stdlib classes we use in production.
    """

    def test_shutdown_from_other_thread_completes(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _Quiet(BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):  # silence stderr
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
        srv.daemon_threads = True

        serve_thread = threading.Thread(
            target=srv.serve_forever, daemon=True, name="test-serve"
        )
        serve_thread.start()

        # Give serve_forever a moment to enter its poll loop.
        time.sleep(0.05)

        # Shutdown FROM a different thread (the pattern our fix uses).
        shutdown_thread = threading.Thread(target=srv.shutdown, daemon=True)
        start = time.monotonic()
        shutdown_thread.start()
        shutdown_thread.join(timeout=5.0)
        elapsed = time.monotonic() - start

        self.assertFalse(
            shutdown_thread.is_alive(),
            f"shutdown() did not return within 5s (elapsed={elapsed:.2f}s) — "
            "the cross-thread invariant we rely on is broken.",
        )
        serve_thread.join(timeout=5.0)
        self.assertFalse(serve_thread.is_alive())
        srv.server_close()


if __name__ == "__main__":
    unittest.main()
