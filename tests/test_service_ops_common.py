"""Tests for handlers/_service_ops_common — the Q2 dedup chokepoint.

One implementation now backs what used to be 3 installer-run copies (E7),
2 verbatim RNS-alignment flows (E6), ~8 journal tails (E2), and the
execute-and-report verdict shape. These tests pin the honest-failure
contract every consumer inherits.
"""

import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context

from handlers import _service_ops_common as soc


class _Proc:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


class TestRunScriptCaptured:
    def test_success_returns_rc_and_output(self):
        with patch('subprocess.run', return_value=_Proc(0, "done\n")):
            rc, out = soc.run_script_captured(['x'])
        assert rc == 0 and "done" in out

    def test_stderr_is_labeled_not_lost(self):
        with patch('subprocess.run', return_value=_Proc(2, "o", "bad")):
            rc, out = soc.run_script_captured(['x'])
        assert rc == 2 and "[stderr]" in out and "bad" in out

    def test_timeout_is_minus_one_with_reason(self):
        with patch('subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5)):
            rc, out = soc.run_script_captured(['x'], timeout=5)
        assert rc == -1 and "time" in out.lower()

    def test_exec_error_is_minus_one_with_reason(self):
        with patch('subprocess.run', side_effect=OSError("no such file")):
            rc, out = soc.run_script_captured(['x'])
        assert rc == -1 and "no such file" in out

    def test_tail_truncation(self):
        with patch('subprocess.run', return_value=_Proc(0, "a" * 5000)):
            rc, out = soc.run_script_captured(['x'], tail=100)
        assert len(out) == 100


class TestRunCommandReport:
    def _titles(self, dialog):
        return [args[0] for name, args, _ in dialog.calls if name == 'msgbox']

    def test_ok_verdict_only_on_rc_zero(self):
        ctx = make_handler_context()
        with patch('subprocess.run', return_value=_Proc(0, "fine")):
            rc = soc.run_command_report(ctx, ['x'], "Thing")
        assert rc == 0
        assert any(t == "Thing: OK" for t in self._titles(ctx.dialog))

    def test_nonzero_exit_named_in_title(self):
        ctx = make_handler_context()
        with patch('subprocess.run', return_value=_Proc(3, "boom")):
            rc = soc.run_command_report(ctx, ['x'], "Thing")
        assert rc == 3
        assert any("returned 3" in t for t in self._titles(ctx.dialog))

    def test_never_ran_reads_failed_not_ok(self):
        ctx = make_handler_context()
        with patch('subprocess.run', side_effect=OSError("gone")):
            rc = soc.run_command_report(ctx, ['x'], "Thing")
        assert rc == -1
        titles = self._titles(ctx.dialog)
        assert any("FAILED" in t for t in titles)
        assert not any("OK" in t for t in titles)

    def test_shows_progress_infobox_before_running(self):
        ctx = make_handler_context()
        with patch('subprocess.run', return_value=_Proc(0, "")):
            soc.run_command_report(ctx, ['x'], "Thing", timeout=600)
        first = ctx.dialog.calls[0]
        assert first[0] == 'infobox', "long op must show progress first (B2/B3)"


class TestJournalTailText:
    def test_builds_expected_argv(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = cmd
            return _Proc(0, "line")

        with patch('subprocess.run', side_effect=fake_run):
            soc.journal_tail_text('rnsd', lines=5, quiet=True,
                                  no_hostname=True)
        assert seen['cmd'][:5] == ['journalctl', '-u', 'rnsd', '-n', '5']
        assert '-q' in seen['cmd'] and '--no-hostname' in seen['cmd']

    def test_user_scope_flag(self):
        seen = {}
        with patch('subprocess.run',
                   side_effect=lambda cmd, **kw: seen.update(cmd=cmd) or _Proc(0, "x")):
            soc.journal_tail_text('meshchatx', user=True)
        assert seen['cmd'][1] == '--user'

    def test_unreadable_never_reads_as_quiet(self):
        # honest_failure_modes #2: a failed observation must not look like
        # an empty (healthy) journal.
        with patch('subprocess.run', return_value=_Proc(1, "", "permission denied")):
            out = soc.journal_tail_text('rnsd')
        assert "unreadable" in out and "permission denied" in out

    def test_empty_text_is_configurable(self):
        with patch('subprocess.run', return_value=_Proc(0, "  \n")):
            out = soc.journal_tail_text('rnsd', empty_text="NOTHING")
        assert out == "NOTHING"


class TestWaitForCondition:
    """Q4 (audit B8): bounded waits show progress and honor the predicate."""

    def test_immediate_true_returns_fast(self):
        import time
        start = time.monotonic()
        assert soc.wait_for_condition(lambda: True, 15) is True
        assert time.monotonic() - start < 0.5, "must not sleep when already up"

    def test_timeout_returns_false(self):
        import time
        start = time.monotonic()
        assert soc.wait_for_condition(lambda: False, 1, tick=0.05) is False
        elapsed = time.monotonic() - start
        assert 0.8 < elapsed < 3, f"1s timeout took {elapsed:.2f}s"

    def test_becomes_true_mid_wait(self):
        state = {'n': 0}

        def pred():
            state['n'] += 1
            return state['n'] >= 3

        assert soc.wait_for_condition(pred, 5, tick=0.01) is True

    def test_label_prints_dots_and_outcome(self, capsys):
        soc.wait_for_condition(lambda: False, 1, label="waiting", tick=0.2)
        out = capsys.readouterr().out
        assert "waiting" in out and "." in out and "timeout" in out

    def test_silent_without_label(self, capsys):
        soc.wait_for_condition(lambda: True, 1)
        assert capsys.readouterr().out == ""


class TestRepairAlignmentNeverPromptsForPassword:
    def test_sudo_runs_noninteractive(self):
        # Q4 (audit B5): output is captured, so an interactive sudo password
        # prompt would hang invisibly under a cleared screen for 120s.
        ctx = make_handler_context()
        seen = {}
        from pathlib import Path
        with patch('subprocess.run',
                   side_effect=lambda cmd, **kw: seen.update(cmd=cmd) or _Proc(0, "ok")), \
             patch.object(Path, 'is_file', return_value=True):
            soc.repair_rns_alignment(ctx, Path('/opt/meshforge'))
        assert seen['cmd'][0] == 'sudo' and seen['cmd'][1] == '-n', (
            f"sudo must run non-interactive (-n); got {seen['cmd'][:3]}"
        )
