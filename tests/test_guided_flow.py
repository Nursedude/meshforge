"""Tests for src/launcher_tui/guided_flow.py — the reusable wizard engine.

Covers the honest-failure-mode-prone edges: resume after interruption, a clean
run + failed verify surfaced (not averaged away), a raising step that can't crash
the TUI, and the run_script_action subprocess wrapper (rc / timeout / missing).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from guided_flow import (  # noqa: E402
    GuidedFlow, WizardStep, StepResult, StepStatus, run_script_action,
    NAV_RUN, NAV_SKIP, NAV_BACK, NAV_QUIT,
)


class FakeDialog:
    """Scripts responses for menu/yesno/inputbox; records msgbox/textbox."""

    def __init__(self, menu=None, yesno=None, inputbox=None):
        self._menu = list(menu or [])
        self._yesno = list(yesno or [])
        self._inputbox = list(inputbox or [])
        self.msgboxes = []
        self.textboxes = []

    def menu(self, title, text, choices, **kw):
        return self._menu.pop(0) if self._menu else None

    def yesno(self, title, text, **kw):
        return self._yesno.pop(0) if self._yesno else False

    def inputbox(self, title, text, init="", **kw):
        return self._inputbox.pop(0) if self._inputbox else None

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def textbox(self, title, text, **kw):
        self.textboxes.append((title, text))


class FakeCtx:
    def __init__(self, dialog):
        self.dialog = dialog
        self.report_calls = []

    def report_action(self, ok, success_title, success_body,
                       fail_title="Action Failed", fail_body=""):
        self.report_calls.append((bool(ok), success_title if ok else fail_title))
        return bool(ok)


def _step(key, run_result, describe="do it", verify=None, optional=True):
    return WizardStep(
        key=key, title=key.title(),
        describe=lambda ctx, st: describe,
        run=lambda ctx, st: run_result,
        verify=verify, optional=optional,
    )


def _flow(steps, tmp):
    return GuidedFlow("testflow", "Test Flow", steps, state_dir=Path(tmp))


class TestLinearCompletion(unittest.TestCase):
    def test_all_steps_run_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done("ok a")),
                     _step("b", StepResult.done("ok b"))]
            dlg = FakeDialog(menu=[NAV_RUN, NAV_RUN])
            state = _flow(steps, tmp).run(FakeCtx(dlg))
            self.assertEqual(state["_completed"], {"a": "done", "b": "done"})
            # summary textbox shown at the end
            self.assertTrue(dlg.textboxes)


class TestSkip(unittest.TestCase):
    def test_skip_records_skipped_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            dlg = FakeDialog(menu=[NAV_SKIP, NAV_RUN])
            state = _flow(steps, tmp).run(FakeCtx(dlg))
            self.assertEqual(state["_completed"]["a"], "skipped")
            self.assertEqual(state["_completed"]["b"], "done")


class TestBack(unittest.TestCase):
    def test_back_returns_to_prior_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            # a:run -> b:back -> a:run -> b:run
            dlg = FakeDialog(menu=[NAV_RUN, NAV_BACK, NAV_RUN, NAV_RUN])
            state = _flow(steps, tmp).run(FakeCtx(dlg))
            self.assertEqual(state["_completed"], {"a": "done", "b": "done"})


class TestQuitAndResume(unittest.TestCase):
    def test_quit_saves_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            # a:run(done) -> b:quit, confirm yes
            dlg = FakeDialog(menu=[NAV_RUN, NAV_QUIT], yesno=[True])
            _flow(steps, tmp).run(FakeCtx(dlg))
            saved = json.loads((Path(tmp) / "testflow.json").read_text())
            self.assertEqual(saved["_completed"]["a"], "done")
            self.assertNotIn("b", saved["_completed"])

    def test_resume_offers_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            # First run: a done, quit at b
            _flow(steps, tmp).run(FakeCtx(FakeDialog(menu=[NAV_RUN, NAV_QUIT], yesno=[True])))
            # Second run: resume prompt -> "resume", then b:run
            dlg2 = FakeDialog(menu=["resume", NAV_RUN])
            state = _flow(steps, tmp).run(FakeCtx(dlg2))
            self.assertEqual(state["_completed"], {"a": "done", "b": "done"})

    def test_restart_clears_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            _flow(steps, tmp).run(FakeCtx(FakeDialog(menu=[NAV_RUN, NAV_QUIT], yesno=[True])))
            # resume prompt -> restart, then a:run, b:run from scratch
            dlg2 = FakeDialog(menu=["restart", NAV_RUN, NAV_RUN])
            state = _flow(steps, tmp).run(FakeCtx(dlg2))
            self.assertEqual(state["_completed"], {"a": "done", "b": "done"})


class TestVerifyHonesty(unittest.TestCase):
    def test_failed_verify_surfaced_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            step = _step("a", StepResult.done(), verify=lambda ctx, st: (False, "probe saw nothing"))
            dlg = FakeDialog(menu=[NAV_RUN])
            ctx = FakeCtx(dlg)
            state = _flow([step], tmp).run(ctx)
            self.assertFalse(state["verify"]["a"]["ok"])
            # report_action was called with ok=False (honest failure surfaced)
            self.assertIn((False, "A — NOT verified"), ctx.report_calls)

    def test_passing_verify_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            step = _step("a", StepResult.done(), verify=lambda ctx, st: (True, "probe saw the packet"))
            state = _flow([step], tmp).run(FakeCtx(FakeDialog(menu=[NAV_RUN])))
            self.assertTrue(state["verify"]["a"]["ok"])

    def test_erroring_verify_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            def boom(ctx, st):
                raise RuntimeError("kaboom")
            step = _step("a", StepResult.done(), verify=boom)
            state = _flow([step], tmp).run(FakeCtx(FakeDialog(menu=[NAV_RUN])))
            self.assertFalse(state["verify"]["a"]["ok"])


class TestNeverCrash(unittest.TestCase):
    def test_raising_step_becomes_failed_and_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            def boom(ctx, st):
                raise ValueError("nope")
            step = WizardStep("a", "A", describe=lambda c, s: "x", run=boom, optional=True)
            # step raises -> FAILED, stays on step; then operator skips to finish
            dlg = FakeDialog(menu=[NAV_RUN, NAV_SKIP])
            state = _flow([step], tmp).run(FakeCtx(dlg))
            # After run raised it's FAILED, then SKIP overwrites to skipped and advances
            self.assertIn(state["_completed"]["a"], ("failed", "skipped"))


class TestCorruptState(unittest.TestCase):
    def test_corrupt_state_starts_fresh_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "testflow.json").write_text("{ this is not json")
            steps = [_step("a", StepResult.done())]
            flow = _flow(steps, tmp)
            loaded = flow.load_state()
            self.assertEqual(loaded["_completed"], {})  # not a spurious "done"


class TestResumeHonesty(unittest.TestCase):
    """2026-07-09 review: resume must not skip past a DONE-but-unverified step,
    and stale [verified] records must not survive a failed re-run or a skip."""

    def test_resume_reoffers_done_but_unverified_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done(),
                           verify=lambda ctx, st: (False, "service crashlooping")),
                     _step("b", StepResult.done())]
            flow = _flow(steps, tmp)
            # First run: a runs DONE but verify FAILS; operator quits.
            flow.run(FakeCtx(FakeDialog(menu=[NAV_RUN, NAV_QUIT], yesno=[True])))
            state = flow.load_state()
            self.assertEqual(state["_completed"]["a"], "done")
            self.assertFalse(state["verify"]["a"]["ok"])
            # The unverified step counts as UNFINISHED for resume.
            self.assertEqual(flow._first_unfinished_index(state), 0)

    def test_all_done_and_verified_resumes_past_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done(),
                           verify=lambda ctx, st: (True, "observed"))]
            flow = _flow(steps, tmp)
            state = flow.run(FakeCtx(FakeDialog(menu=[NAV_RUN])))
            self.assertEqual(flow._first_unfinished_index(state), 1)

    def test_failed_rerun_clears_stale_verified_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcomes = [StepResult.done(), StepResult.failed("second run broke")]
            step_a = WizardStep("a", "A", describe=lambda c, s: "x",
                                run=lambda c, s: outcomes.pop(0),
                                verify=lambda c, s: (True, "ok"), optional=True)
            step_b = _step("b", StepResult.done())
            # a:RUN (done, verified) -> b:BACK -> a:RUN (fails, stays) ->
            # a:SKIP -> b:RUN. 'failed [verified]' must never render.
            dlg = FakeDialog(menu=[NAV_RUN, NAV_BACK, NAV_RUN, NAV_SKIP, NAV_RUN])
            state = _flow([step_a, step_b], tmp).run(FakeCtx(dlg))
            self.assertNotIn("a", state.get("verify", {}))

    def test_skip_clears_stale_verified_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            step_a = _step("a", StepResult.done(),
                           verify=lambda c, s: (True, "ok"))
            step_b = _step("b", StepResult.done())
            # a:RUN (done, verified) -> b:BACK -> a:SKIP -> b:RUN.
            dlg = FakeDialog(menu=[NAV_RUN, NAV_BACK, NAV_SKIP, NAV_RUN])
            state = _flow([step_a, step_b], tmp).run(FakeCtx(dlg))
            self.assertEqual(state["_completed"]["a"], "skipped")
            self.assertNotIn("a", state.get("verify", {}))

    def test_clear_state_failure_is_surfaced_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            steps = [_step("a", StepResult.done()), _step("b", StepResult.done())]
            flow = _flow(steps, tmp)
            flow.run(FakeCtx(FakeDialog(menu=[NAV_RUN, NAV_QUIT], yesno=[True])))
            # Make clear_state fail while load still returns the old state.
            dlg = FakeDialog(menu=["restart", NAV_QUIT], yesno=[True])
            ctx = FakeCtx(dlg)
            import unittest.mock as um
            with um.patch.object(flow, "clear_state", return_value=False):
                flow.run(ctx)
            self.assertTrue(any("could not be cleared" in t for _, t in dlg.msgboxes))


class TestRunScriptAction(unittest.TestCase):
    def test_success_rc0(self):
        act = run_script_action("echo", "prints", ["true"], requires_admin=False, timeout=10)
        ok, msg = act.apply()
        self.assertTrue(ok)

    def test_failure_nonzero_rc(self):
        act = run_script_action("false", "fails", ["false"], requires_admin=False, timeout=10)
        ok, msg = act.apply()
        self.assertFalse(ok)
        self.assertIn("exit 1", msg)

    def test_missing_binary(self):
        act = run_script_action("nope", "missing",
                                ["/nonexistent/binary_xyz"], requires_admin=False, timeout=10)
        ok, msg = act.apply()
        self.assertFalse(ok)
        self.assertIn("could not run", msg)

    def test_timeout(self):
        act = run_script_action("sleep", "hangs", ["sleep", "5"],
                                requires_admin=False, timeout=1)
        ok, msg = act.apply()
        self.assertFalse(ok)
        self.assertIn("timed out", msg)

    def test_action_is_admin_gated_by_default(self):
        act = run_script_action("x", "y", ["true"])
        self.assertTrue(act.requires_admin)


if __name__ == "__main__":
    unittest.main()
