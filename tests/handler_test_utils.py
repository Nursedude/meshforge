"""
Shared test utilities for TUI handler unit tests.

Import FakeDialog and make_handler_context from here rather than conftest.
"""

import os
import sys

# Ensure src and launcher_tui are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import TUIContext


class FakeDialog:
    """Full-featured dialog stub for handler unit testing.

    Supports programmable return sequences for menu/inputbox/yesno,
    call recording for assertion, and attribute tracking.
    """

    def __init__(self):
        self.calls = []
        self._menu_returns = []
        self._inputbox_returns = []
        self._yesno_returns = []
        self._checklist_returns = []
        self._editbox_returns = []
        self.last_msgbox_title = None
        self.last_msgbox_text = None

    @staticmethod
    def _reject_ansi(*parts):
        """whiptail/dialog never interpret ANSI escapes — they render as
        literal bytes on screen (audit W13). Asserting here makes every
        handler test a regression net for free."""
        for p in parts:
            assert '\033' not in str(p), (
                f"ANSI escape in dialog text — whiptail shows it as literal "
                f"garbage: {p!r}"
            )

    def msgbox(self, title, text, **kwargs):
        self._reject_ansi(title, text)
        self.calls.append(('msgbox', (title, text), kwargs))
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices, **kwargs):
        self._reject_ansi(title, text, *(c for pair in choices for c in pair))
        self.calls.append(('menu', (title, text, choices), kwargs))
        if self._menu_returns:
            return self._menu_returns.pop(0)
        return None

    def yesno(self, title, text, **kwargs):
        self._reject_ansi(title, text)
        self.calls.append(('yesno', (title, text), kwargs))
        if self._yesno_returns:
            return self._yesno_returns.pop(0)
        return False

    def inputbox(self, title, text, init="", **kwargs):
        self._reject_ansi(title, text)
        self.calls.append(('inputbox', (title, text), {'init': init, **kwargs}))
        if self._inputbox_returns:
            return self._inputbox_returns.pop(0)
        return init

    def checklist(self, title, text, choices, **kwargs):
        self.calls.append(('checklist', (title, text, choices), kwargs))
        if self._checklist_returns:
            return self._checklist_returns.pop(0)
        return []

    def textbox(self, title, text, **kwargs):
        """Mirror backend.textbox(title, text): show read-only scrollable text."""
        self.calls.append(('textbox', (title, text), kwargs))
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def editbox(self, title, file_path, **kwargs):
        """Mirror backend.editbox(title, file_path): edit a file in-app.

        Returns the next programmed value, or the file's current content
        (unchanged edit), or None on unreadable file — like the backend.
        """
        self.calls.append(('editbox', (title, file_path), kwargs))
        if self._editbox_returns:
            return self._editbox_returns.pop(0)
        try:
            with open(file_path, 'r', encoding='utf-8') as fh:
                return fh.read()
        except OSError:
            return None

    def infobox(self, title_or_text, text=None, **kwargs):
        self.calls.append(('infobox', (title_or_text, text), kwargs))

    def set_status_bar(self, bar):
        self.calls.append(('set_status_bar', (bar,), {}))


def make_handler_context(**overrides):
    """Factory for TUIContext with test defaults."""
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)
