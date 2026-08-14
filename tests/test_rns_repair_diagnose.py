"""RNS repair destructive path must leave a witness — Batch 4 (audit C1/C2).

Before this fix, ``_diagnose_timeout`` wrapped a 35-line block — including
a user-confirmed destructive config change (``disable_interfaces_in_config``)
plus an rnsd stop/start — in a single ``except Exception: pass``. A user
could answer "yes, disable my interfaces", have the write or the restart
throw, and see nothing at all.

The extracted ``_offer_disable_blocking`` must now show an honest failure
dialog on EVERY failure, and the dialog must say which side of the config
write the failure happened on.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context

from handlers import _rns_repair


class _Handler:
    def __init__(self):
        self.ctx = make_handler_context()


BLOCKING = [("iface_a", "no carrier", "fix"), ("iface_b", "bad port", "fix")]


def _dialog_texts(dialog):
    return " | ".join(
        f"{args[0]}::{args[1]}" for name, args, _ in dialog.calls
        if name == 'msgbox'
    )


class TestOfferDisableBlocking:
    def test_decline_changes_nothing(self):
        h = _Handler()
        h.ctx.dialog._yesno_returns = [False]
        with patch.object(_rns_repair, 'disable_interfaces_in_config') as dis:
            assert _rns_repair._offer_disable_blocking(h, BLOCKING) is False
            dis.assert_not_called()

    def test_pre_write_failure_shows_nothing_was_changed(self):
        h = _Handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(_rns_repair, 'disable_interfaces_in_config',
                          side_effect=OSError("read-only fs")):
            assert _rns_repair._offer_disable_blocking(h, BLOCKING) is False
        texts = _dialog_texts(h.ctx.dialog)
        assert "FAILED" in texts
        assert "nothing was changed" in texts.lower()

    def test_post_write_failure_names_the_half_state(self):
        # THE C1 regression pin: disable succeeds, restart raises — the
        # user must be told the config IS modified, not shown silence.
        h = _Handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(_rns_repair, 'disable_interfaces_in_config',
                          return_value=["iface_a", "iface_b"]), \
             patch.object(_rns_repair, 'stop_service',
                          side_effect=RuntimeError("systemd exploded")):
            assert _rns_repair._offer_disable_blocking(h, BLOCKING) is False
        texts = _dialog_texts(h.ctx.dialog)
        assert "FAILED" in texts
        assert "WERE disabled" in texts or "WAS applied" in texts, (
            "post-write failure dialog must say the config is already "
            f"modified; got: {texts}"
        )

    def test_success_reports_via_report_action(self):
        h = _Handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(_rns_repair, 'disable_interfaces_in_config',
                          return_value=["iface_a"]), \
             patch.object(_rns_repair, 'stop_service'), \
             patch.object(_rns_repair, 'start_service'), \
             patch.object(_rns_repair, 'check_rns_shared_instance',
                          return_value=True), \
             patch.object(_rns_repair, 'get_rns_shared_instance_info',
                          return_value={'detail': 'up on @rns/default'}), \
             patch.object(_rns_repair.time, 'sleep'):
            assert _rns_repair._offer_disable_blocking(h, BLOCKING) is True
        texts = _dialog_texts(h.ctx.dialog)
        assert "Restored" in texts

    def test_timeout_after_write_is_honest(self):
        h = _Handler()
        h.ctx.dialog._yesno_returns = [True]
        with patch.object(_rns_repair, 'disable_interfaces_in_config',
                          return_value=["iface_a"]), \
             patch.object(_rns_repair, 'stop_service'), \
             patch.object(_rns_repair, 'start_service'), \
             patch.object(_rns_repair, 'check_rns_shared_instance',
                          return_value=False), \
             patch.object(_rns_repair.time, 'sleep'):
            assert _rns_repair._offer_disable_blocking(h, BLOCKING) is False
        texts = _dialog_texts(h.ctx.dialog)
        assert "WAS applied" in texts


class TestDiagnoseStepsLeaveWitness:
    def test_no_silent_pass_in_diagnose_timeout(self):
        # C2: the four bare `except Exception: pass` diagnosis steps now
        # log what they skipped. Source-level pin: no except-pass remains
        # in _diagnose_timeout or _offer_disable_blocking.
        import inspect
        for fn in (_rns_repair._diagnose_timeout,
                   _rns_repair._offer_disable_blocking):
            src = inspect.getsource(fn)
            assert 'except Exception:\n        pass' not in src, (
                f"{fn.__name__} regained a silent except-pass"
            )
