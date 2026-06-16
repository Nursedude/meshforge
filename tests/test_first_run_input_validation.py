"""
Input-validation tests for the first-run network wizard.

The first-run network wizard (`_wizard_step_network_config`) takes a
meshtasticd host + port that get written to settings.json. Before this guard,
the host was written unvalidated and the port went through a bare `int()`
whose ValueError surfaced only as a generic "Failed to save settings" error.

These tests pin the up-front host/port validation: an invalid host or port
must produce an actionable "Invalid Host" / "Invalid Port" dialog and must NOT
write settings.json. Valid input must still flow through to the write.

RED-FIRST: removing either guard lets the bad value reach the write (host) or
the bare int() generic error (port) and these tests fail.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context

from handlers.first_run import FirstRunHandler


def _make_handler(dialog):
    """Build a FirstRunHandler wired to a FakeDialog-backed context."""
    handler = FirstRunHandler()
    handler.set_context(make_handler_context(dialog=dialog))
    return handler


def _last_msgbox_titles(dialog):
    return [c[1][0] for c in dialog.calls if c[0] == 'msgbox']


class TestInvalidHostRejected:
    def test_invalid_host_shows_dialog_and_does_not_write(self):
        dialog = FakeDialog()
        # host inputbox returns an invalid hostname; port would be next.
        dialog._inputbox_returns = ["bad host!", "4403"]
        handler = _make_handler(dialog)

        with patch('pathlib.Path.write_text') as mock_write:
            handler._wizard_step_network_config()

        assert "Invalid Host" in _last_msgbox_titles(dialog)
        mock_write.assert_not_called()


class TestInvalidPortRejected:
    def test_non_numeric_port_shows_dialog_and_does_not_write(self):
        dialog = FakeDialog()
        dialog._inputbox_returns = ["localhost", "abc"]
        handler = _make_handler(dialog)

        with patch('pathlib.Path.write_text') as mock_write:
            handler._wizard_step_network_config()

        assert "Invalid Port" in _last_msgbox_titles(dialog)
        mock_write.assert_not_called()

    def test_out_of_range_port_shows_dialog_and_does_not_write(self):
        dialog = FakeDialog()
        dialog._inputbox_returns = ["localhost", "99999"]
        handler = _make_handler(dialog)

        with patch('pathlib.Path.write_text') as mock_write:
            handler._wizard_step_network_config()

        assert "Invalid Port" in _last_msgbox_titles(dialog)
        mock_write.assert_not_called()


class TestValidInputProceeds:
    def test_valid_host_and_port_writes_settings(self):
        dialog = FakeDialog()
        dialog._inputbox_returns = ["localhost", "4403"]
        handler = _make_handler(dialog)

        with patch('pathlib.Path.write_text') as mock_write, \
                patch('pathlib.Path.mkdir'), \
                patch('pathlib.Path.exists', return_value=False):
            handler._wizard_step_network_config()

        # Valid input must reach the write and NOT trip a validation dialog.
        mock_write.assert_called_once()
        titles = _last_msgbox_titles(dialog)
        assert "Invalid Host" not in titles
        assert "Invalid Port" not in titles
