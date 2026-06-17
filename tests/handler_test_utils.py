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
        self._radiolist_returns = []
        self._checklist_returns = []
        self.last_msgbox_title = None
        self.last_msgbox_text = None

    def msgbox(self, title, text, **kwargs):
        self.calls.append(('msgbox', (title, text), kwargs))
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices, **kwargs):
        self.calls.append(('menu', (title, text, choices), kwargs))
        if self._menu_returns:
            return self._menu_returns.pop(0)
        return None

    def yesno(self, title, text, **kwargs):
        self.calls.append(('yesno', (title, text), kwargs))
        if self._yesno_returns:
            return self._yesno_returns.pop(0)
        return False

    def inputbox(self, title, text, init="", **kwargs):
        self.calls.append(('inputbox', (title, text), {'init': init, **kwargs}))
        if self._inputbox_returns:
            return self._inputbox_returns.pop(0)
        return init

    def radiolist(self, title, text, choices, **kwargs):
        self.calls.append(('radiolist', (title, text, choices), kwargs))
        if self._radiolist_returns:
            return self._radiolist_returns.pop(0)
        return None

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

    def gauge(self, text, percent, **kwargs):
        self.calls.append(('gauge', (text, percent), kwargs))

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
