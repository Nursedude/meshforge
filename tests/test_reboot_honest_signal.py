"""System > Reboot/Shutdown must report what systemd actually said.

THE DEFECT PINNED (2026-09-15): both branches were a bare
``subprocess.run(_sudo_cmd(['systemctl', 'reboot']), timeout=30)`` with the
result discarded, and ``handlers/reboot.py`` contained neither a msgbox nor a
print — measured, it was the ONLY handler module with no user-facing output at
all (``grep -c "msgbox\\|print(" handlers/reboot.py`` -> 0).

So a polkit denial produced a silent menu re-render: the operator pressed
Reboot, confirmed, and watched nothing happen with no idea why. Same family as
MF020 (hardcoded-success-after-unchecked-action) and honest_failure_modes #9
(every swallow gets a witness).
"""

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
from handlers.reboot import RebootHandler  # noqa: E402


def _handler():
    h = RebootHandler()
    h.ctx = make_handler_context(dialog=FakeDialog())
    return h


def _completed(rc, stderr="", stdout=""):
    return subprocess.CompletedProcess(
        args=['systemctl', 'reboot'], returncode=rc,
        stdout=stdout, stderr=stderr)


class TestViaTheMenuPath:
    """Drive the REAL menu selection, not the helper.

    This is the red-first proof that matters: ``_reboot_menu`` exists in both
    the pre-fix and post-fix trees, so a failure here is about BEHAVIOUR
    (silence vs. a dialog), not about a method that did not exist yet.
    """

    def test_denied_reboot_chosen_from_the_menu_tells_the_operator(self):
        h = RebootHandler()
        dialog = FakeDialog()
        dialog._menu_returns = ['reboot', 'back']   # pick Reboot, then leave
        dialog._yesno_returns = [True]              # confirm it
        h.ctx = make_handler_context(dialog=dialog)

        denial = ("Failed to reboot system via logind: "
                  "Interactive authentication required.")
        with patch('subprocess.run', return_value=_completed(1, stderr=denial)):
            h._reboot_menu()

        said_something = [c for c in dialog.calls
                          if c[0] in ('msgbox', 'textbox')]
        assert said_something, (
            "PRE-FIX BEHAVIOUR: the operator pressed Reboot, confirmed, and "
            "the menu silently re-rendered. A refused power action must speak."
        )
        assert any("authentication required" in str(c[1]).lower()
                   for c in said_something), "must quote the real reason"


class TestRebootReportsFailure:

    def test_polkit_denial_is_reported_not_swallowed(self):
        """The case the whole fix exists for."""
        h = _handler()
        denial = ("Failed to reboot system via logind: "
                  "Interactive authentication required.")
        with patch('subprocess.run', return_value=_completed(1, stderr=denial)):
            h._power_action('reboot', "Reboot")

        boxes = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert boxes, "a refused reboot must produce a dialog, not silence"
        title, text = boxes[-1][1]
        assert "Refused" in title
        assert "Interactive authentication required" in text, (
            "must quote systemd's real stderr, not a guess"
        )
        assert "still running" in text

    def test_nonzero_with_no_stderr_still_speaks(self):
        h = _handler()
        with patch('subprocess.run', return_value=_completed(4)):
            h._power_action('poweroff', "Shutdown")
        boxes = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert boxes
        assert "exited 4" in boxes[-1][1][1]

    def test_timeout_is_reported(self):
        h = _handler()
        with patch('subprocess.run',
                   side_effect=subprocess.TimeoutExpired('systemctl', 30)):
            h._power_action('reboot', "Reboot")
        boxes = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert boxes and "Timed Out" in boxes[-1][1][0]

    def test_missing_systemctl_is_reported(self):
        h = _handler()
        with patch('subprocess.run',
                   side_effect=FileNotFoundError("no systemctl")):
            h._power_action('reboot', "Reboot")
        boxes = [c for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert boxes and "Failed" in boxes[-1][1][0]


class TestRebootSuccessPath:

    def test_success_does_not_claim_failure(self):
        h = _handler()
        with patch('subprocess.run', return_value=_completed(0)):
            h._power_action('reboot', "Reboot")
        titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == 'msgbox']
        assert not any("Refused" in t or "Failed" in t for t in titles)

    def test_success_uses_infobox_not_a_blocking_dialog(self):
        """We are racing the shutdown; a modal the user must dismiss is wrong."""
        h = _handler()
        with patch('subprocess.run', return_value=_completed(0)):
            h._power_action('reboot', "Reboot")
        kinds = [c[0] for c in h.ctx.dialog.calls]
        assert 'infobox' in kinds
        assert 'msgbox' not in kinds


class TestRebootStillRunsTheRightCommand:

    @pytest.mark.parametrize("verb,label", [('reboot', "Reboot"),
                                            ('poweroff', "Shutdown")])
    def test_argv_contains_the_verb(self, verb, label):
        h = _handler()
        with patch('subprocess.run', return_value=_completed(0)) as m:
            h._power_action(verb, label)
        argv = m.call_args[0][0]
        assert 'systemctl' in argv and verb in argv

    def test_result_is_captured_not_discarded(self):
        """The literal regression: capture_output must be on, or there is
        nothing to report and we are back to a silent re-render."""
        h = _handler()
        with patch('subprocess.run', return_value=_completed(0)) as m:
            h._power_action('reboot', "Reboot")
        assert m.call_args.kwargs.get('capture_output') is True
        assert m.call_args.kwargs.get('timeout') == 30
