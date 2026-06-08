"""Honest-signal guard suite (Issues #74-#77, TUI audit 2026-06-08).

The TUI must not show a hardcoded success for an action whose result was never
checked ("the app works and does not fail silently / produce false info"). This
file is the regression home for that class as the multi-session burn-down
proceeds:

  * TestApplyConfigRestartReturnChecked — no handler discards
    apply_config_and_restart()'s (ok, msg) (the MF020 contract).
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
