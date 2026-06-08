"""Honest-signal guard suite (Issues #74-#77, TUI audit 2026-06-08).

The TUI must not show a hardcoded success for an action whose result was never
checked ("the app works and does not fail silently / produce false info"). This
file is the regression home for that class as the multi-session burn-down
proceeds:

  * TestApplyConfigRestartReturnChecked — no handler discards
    apply_config_and_restart()'s (ok, msg) (the MF020 contract).
  * TestRnsRestartReturnChecked — rns_interfaces.py binds stop/start_service()
    so a failed rnsd restart can't read as success (S3 item 1).
  * TestPortConflictVerifyBeforeDone — diagnose_rns_port_conflict() verifies the
    shared instance is up before printing "Done." (S3 item 2).
  * TestReportActionHelper — the shared confirm-or-honest dialog primitive.
  * TestMF020LintRule — the lint rule fires on the bad shape, stays quiet on
    the honest one and outside the handler tree.

Future sessions widen this (the *_service / cfg.save() / fabricated-data
clusters) — add their guards here.
"""
import os
import re
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS_DIR = REPO_ROOT / "src" / "launcher_tui" / "handlers"

# src/launcher_tui on path for `from handler_protocol import TUIContext`
# (conftest also does this; belt-and-suspenders so the file runs standalone).
sys.path.insert(0, str(REPO_ROOT / "src" / "launcher_tui"))
sys.path.insert(0, str(REPO_ROOT / "src"))

# scripts/lint.py is not packaged — import it by file path (mirrors
# tests/test_lint_mf017.py).
_lint_path = REPO_ROOT / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


# Statement-start call (return discarded). Mirrors the MF020 regex in lint.py.
_BARE_APPLY = re.compile(r'^_?apply_config_and_restart\s*\(')

# Statement-start service-control call (return discarded). Scoped per-file, not
# handler-wide: ~10 handler sites legitimately fire-and-forget a stop before a
# checked start, and others belong to later burn-down slices.
_BARE_SVC = re.compile(r'^(?:stop|start|restart)_service\s*\(')


class TestApplyConfigRestartReturnChecked:
    """apply_config_and_restart() returns (success, msg) precisely so callers
    surface a failed restart. A bare-statement call drops it and shows a
    hardcoded "restarted" even when the daemon stayed down (#74-#77)."""

    def test_no_bare_apply_config_and_restart_in_handlers(self):
        violations = []
        for root, _dirs, files in os.walk(HANDLERS_DIR):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = Path(root) / fn
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for n, line in enumerate(f, 1):
                        s = line.strip()
                        if s.startswith("#"):
                            continue
                        if _BARE_APPLY.match(s):
                            rel = fp.relative_to(REPO_ROOT)
                            violations.append(f"{rel}:{n}")
        assert not violations, (
            "apply_config_and_restart() return discarded (MF020 / honest-signal "
            "#74-#77) — bind 'ok, msg = ...' and surface restart failure:\n  "
            + "\n  ".join(violations)
        )


class TestRnsRestartReturnChecked:
    """S3 item 1 (#74-#77): rns_interfaces._fix_rns_ownership restarts rnsd after
    a permission fix. The stop/start_service returns must be bound so a daemon
    left stopped never reads "rnsd restarted" (mirrors rns_monitor.py:149-153).
    Scoped to rns_interfaces.py — the broad *_service sweep is later slices."""

    def test_no_bare_service_control_in_rns_interfaces(self):
        fp = HANDLERS_DIR / "rns_interfaces.py"
        violations = []
        with open(fp, encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if _BARE_SVC.match(s):
                    violations.append(f"rns_interfaces.py:{n}: {s}")
        assert not violations, (
            "stop/start_service() return discarded in rns_interfaces.py "
            "(honest-signal #74-#77) — bind 'ok, msg = ...' and gate the "
            "'restarted' message on the start result:\n  "
            + "\n  ".join(violations)
        )


class TestPortConflictVerifyBeforeDone:
    """S3 item 2 (#74-#77): _rns_diagnostics_engine.diagnose_rns_port_conflict()
    must confirm rnsd actually claimed the shared instance
    (handler._wait_for_rns_shared_instance) before printing 'Done.' — starting
    rnsd != resolving the port conflict, and the discarded start_service() return
    hid a failed start. Behavioral (not a source scan: line 459's sibling
    stop_service is a legitimate stop-then-pkill path)."""

    def _run(self, monkeypatch, capsys, *, start_ok, instance_up):
        import handlers._rns_diagnostics_engine as eng
        # Isolate side effects: replace the module's subprocess/time refs so the
        # real pkill never fires and sleeps are no-ops (real modules untouched).
        monkeypatch.setattr(eng, "subprocess", MagicMock())
        monkeypatch.setattr(eng, "time", MagicMock())
        monkeypatch.setattr(eng, "start_service", lambda name: (start_ok, "boom"))
        handler = MagicMock()
        handler._check_lxmf_app_conflict.return_value = "NomadNet"
        handler.ctx.dialog.yesno.return_value = True
        handler._wait_for_rns_shared_instance.return_value = instance_up
        eng.diagnose_rns_port_conflict(handler)
        return capsys.readouterr().out, handler

    def test_done_only_when_instance_verified(self, monkeypatch, capsys):
        out, _ = self._run(monkeypatch, capsys, start_ok=True, instance_up=True)
        assert "Done." in out

    def test_no_done_when_instance_never_up(self, monkeypatch, capsys):
        out, _ = self._run(monkeypatch, capsys, start_ok=True, instance_up=False)
        assert "Done." not in out
        assert "NOT available" in out

    def test_no_done_and_no_verify_when_start_fails(self, monkeypatch, capsys):
        out, handler = self._run(monkeypatch, capsys, start_ok=False, instance_up=True)
        assert "Done." not in out
        assert "FAILED to start rnsd" in out
        # Never started → never claim to have verified.
        handler._wait_for_rns_shared_instance.assert_not_called()


class TestReportActionHelper:
    """TUIContext.report_action — the shared confirm-or-honest dialog primitive."""

    def _ctx(self):
        from handler_protocol import TUIContext
        return TUIContext(dialog=MagicMock())

    def test_success_shows_success_dialog_and_returns_true(self):
        ctx = self._ctx()
        assert ctx.report_action(True, "Applied", "did it") is True
        ctx.dialog.msgbox.assert_called_once_with("Applied", "did it")

    def test_failure_shows_failure_dialog_and_returns_false(self):
        ctx = self._ctx()
        assert ctx.report_action(False, "Applied", "did it", "Restart Failed", "nope") is False
        ctx.dialog.msgbox.assert_called_once_with("Restart Failed", "nope")

    def test_failure_default_title_and_body(self):
        ctx = self._ctx()
        ctx.report_action(False, "Applied", "did it")
        title, body = ctx.dialog.msgbox.call_args[0]
        assert title == "Action Failed"
        assert "did not complete" in body

    def test_truthiness_is_coerced_to_bool(self):
        ctx = self._ctx()
        # a (False, msg)[0]-style falsy value still routes to the failure dialog
        assert ctx.report_action(0, "Applied", "did it") is False
        assert ctx.report_action(1, "Applied", "did it") is True


class TestMF020LintRule:
    """The MF020 lint rule: fire on a discarded apply_config_and_restart() in a
    TUI handler, stay quiet on the honest bound form and outside the handler tree."""

    def _handler_file(self, tmp_path: Path, body: str) -> Path:
        d = tmp_path / "src" / "launcher_tui" / "handlers"
        d.mkdir(parents=True)
        fp = d / "fake_handler.py"
        fp.write_text(body)
        return fp

    def _mf020(self, issues):
        return [i for i in issues if i.code == "MF020"]

    def test_fires_on_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path,
            "def go(self):\n    apply_config_and_restart('meshtasticd')\n",
        )
        issues = lint.MeshForgeLinter().lint_file(str(fp))
        assert self._mf020(issues), "MF020 should fire on a discarded apply_config_and_restart()"

    def test_fires_on_aliased_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path,
            "def go(self):\n    _apply_config_and_restart('meshtasticd')\n",
        )
        assert self._mf020(lint.MeshForgeLinter().lint_file(str(fp)))

    def test_quiet_when_result_is_bound(self, tmp_path):
        fp = self._handler_file(
            tmp_path,
            "def go(self):\n    ok, msg = apply_config_and_restart('meshtasticd')\n"
            "    self.ctx.report_action(ok, 'A', 'b', 'C', msg)\n",
        )
        assert not self._mf020(lint.MeshForgeLinter().lint_file(str(fp)))

    def test_quiet_outside_handler_tree(self, tmp_path):
        # Same bare call, but not under launcher_tui/handlers/ — out of scope.
        d = tmp_path / "src" / "utils"
        d.mkdir(parents=True)
        fp = d / "elsewhere.py"
        fp.write_text("def go():\n    apply_config_and_restart('meshtasticd')\n")
        assert not self._mf020(lint.MeshForgeLinter().lint_file(str(fp)))
