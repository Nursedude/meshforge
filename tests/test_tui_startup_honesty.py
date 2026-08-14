"""Honest startup — 2026-08-14 TUI hardening arc, Batch 3.

Pins four startup fixes:

- S1: `_check_startup_updates` must NOT block — the old inline call ran
  git fetch / apt / GitHub queries for up to ~2 minutes of blank screen.
- W1: `_run_basic_launcher` must EXIST (the old code called it without
  defining it — AttributeError on any box without whiptail/dialog) and
  must return False without raising in a non-interactive context.
- W10: `FirstRunHandler.on_startup` is idempotent per process (it is
  invoked both explicitly by main.py and again via startup_all), and
  `_check_first_run` refuses non-tty stdin — the moc 26k-restart
  crash-loop class, previously guarded only on the launcher.py path.
- W3: `--no-startup-checks` is actually honored (it was parsed and
  silently ignored) and `--debug` is gone.
"""

import io
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _import_main():
    import importlib
    import main as tui_main
    return importlib.reload(tui_main) if False else tui_main


class TestStartupUpdateCheckNonBlocking:
    def test_spawner_returns_fast_and_thread_sets_count(self):
        tui_main = _import_main()

        def slow_check(self):
            time.sleep(2)
            self._updates_available = 3

        fake_self = SimpleNamespace()
        fake_self._check_updates_now = lambda: slow_check(fake_self)

        start = time.monotonic()
        tui_main.MeshForgeLauncher._check_startup_updates(fake_self)
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, (
            f"_check_startup_updates blocked for {elapsed:.2f}s — it must "
            "spawn, not run inline (audit S1)"
        )
        assert fake_self._updates_available == 0  # badge starts honest
        fake_self._update_check_thread.join(timeout=5)
        assert fake_self._updates_available == 3


class TestBasicLauncherFallback:
    def test_method_exists(self):
        # W1: the old run() called a method that was never defined.
        tui_main = _import_main()
        assert hasattr(tui_main.MeshForgeLauncher, '_run_basic_launcher')

    def test_non_tty_returns_false_without_raising(self):
        tui_main = _import_main()
        fake_self = SimpleNamespace()
        fake_stdin = io.StringIO()  # not a tty
        with patch.object(sys, 'stdin', fake_stdin):
            result = tui_main.MeshForgeLauncher._run_basic_launcher(fake_self)
        assert result is False


class TestFirstRunStartup:
    def _make_handler(self):
        from handlers.first_run import FirstRunHandler
        h = FirstRunHandler()
        ctx = make_handler_context()
        h.set_context(ctx)
        return h, ctx.dialog

    def test_on_startup_idempotent(self):
        h, dialog = self._make_handler()
        dialog._yesno_returns = [False, False, False, False]
        with patch.object(h, '_check_first_run', return_value=True):
            h.on_startup()
            first_calls = len(dialog.calls)
            h.on_startup()  # startup_all()'s second invocation
        assert first_calls > 0, "wizard should have prompted on first call"
        assert len(dialog.calls) == first_calls, (
            "second on_startup re-prompted the wizard (audit W10)"
        )

    def test_check_first_run_refuses_non_tty(self):
        h, dialog = self._make_handler()
        fake_stdin = io.StringIO()
        with patch.object(sys, 'stdin', fake_stdin):
            assert h._check_first_run() is False
        assert dialog.calls == []


class TestCliFlags:
    def test_no_startup_checks_skips_both(self):
        tui_main = _import_main()
        calls = []
        fake_self = SimpleNamespace(
            dialog=SimpleNamespace(available=True),
            skip_startup_checks=True,
            _check_root_without_sudo_user=lambda: calls.append('root_check'),
            _run_startup_checks=lambda: calls.append('startup_checks') or True,
            _check_startup_updates=lambda: calls.append('update_check'),
            _registry=SimpleNamespace(
                get_handler=lambda _id: None,
                startup_all=lambda: None,
                shutdown_all=lambda: None,
            ),
            _tui_context=SimpleNamespace(daemon_active=False),
            _is_daemon_running=lambda: True,  # daemon mode: skip startup_all
            _run_main_menu=lambda: calls.append('main_menu'),
        )
        tui_main.MeshForgeLauncher.run(fake_self)
        assert 'startup_checks' not in calls, "--no-startup-checks ignored (audit W3)"
        assert 'update_check' not in calls
        assert 'main_menu' in calls
        assert fake_self._updates_available == 0

    def test_debug_flag_deleted(self):
        tui_main = _import_main()
        src = open(tui_main.__file__).read()
        assert "'--debug'" not in src, (
            "--debug returned; it was deleted 2026-08-14 because it was "
            "parsed and never read"
        )
