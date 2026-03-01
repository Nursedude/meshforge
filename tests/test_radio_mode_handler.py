"""
Tests for RadioModeConfigHandler.

Covers:
- Handler metadata (id, section, menu items)
- Execute dispatch
- Radio mode set logic

Run: python3 -m pytest tests/test_radio_mode_handler.py -v
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import BaseHandler, TUIContext
from handlers.radio_mode_config import RadioModeConfigHandler
from core.radio_mode import RadioMode


class FakeDialog:
    """Dialog stub for testing."""

    def __init__(self):
        self.last_title = None
        self.last_text = None
        self._menu_return = None
        self._yesno_return = False

    def msgbox(self, title, text, **kwargs):
        self.last_title = title
        self.last_text = text

    def menu(self, title, text, choices):
        return self._menu_return

    def yesno(self, title, text):
        return self._yesno_return


def _make_handler():
    """Create a RadioModeConfigHandler with mock context."""
    handler = RadioModeConfigHandler()
    dialog = FakeDialog()
    ctx = TUIContext(dialog=dialog, feature_flags={"meshcore": True})
    handler.set_context(ctx)
    return handler, dialog


class TestHandlerMetadata:
    """Test handler registration metadata."""

    def test_handler_id(self):
        handler = RadioModeConfigHandler()
        assert handler.handler_id == "radio_mode_config"

    def test_menu_section(self):
        handler = RadioModeConfigHandler()
        assert handler.menu_section == "configuration"

    def test_is_base_handler(self):
        handler = RadioModeConfigHandler()
        assert isinstance(handler, BaseHandler)

    def test_menu_items_structure(self):
        handler = RadioModeConfigHandler()
        items = handler.menu_items()
        assert len(items) == 1
        tag, desc, flag = items[0]
        assert tag == "radio_mode_config"
        assert "meshcore" == flag

    def test_menu_items_description(self):
        handler = RadioModeConfigHandler()
        items = handler.menu_items()
        assert "Radio Mode" in items[0][1]


class TestExecuteDispatch:
    """Test execute routes to the correct method."""

    def test_execute_calls_menu(self):
        handler, dialog = _make_handler()
        dialog._menu_return = "back"
        with patch.object(handler, '_radio_mode_menu') as mock_menu:
            handler.execute("radio_mode_config")
            mock_menu.assert_called_once()

    def test_execute_ignores_unknown_action(self):
        handler, _ = _make_handler()
        # Should not raise
        handler.execute("unknown_action")


class TestRadioModeSet:
    """Test _radio_mode_set logic."""

    def test_same_mode_shows_no_change(self):
        handler, dialog = _make_handler()
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.MESHTASTIC):
            handler._radio_mode_set(RadioMode.MESHTASTIC)
            assert dialog.last_title == "Radio Mode"
            assert "Already set" in dialog.last_text

    def test_mode_change_not_confirmed(self):
        handler, dialog = _make_handler()
        dialog._yesno_return = False
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.MESHTASTIC), \
             patch('handlers.radio_mode_config.set_radio_mode') as mock_set:
            handler._radio_mode_set(RadioMode.MESHCORE)
            mock_set.assert_not_called()

    def test_mode_change_confirmed_success(self):
        handler, dialog = _make_handler()
        dialog._yesno_return = True
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.MESHTASTIC), \
             patch('handlers.radio_mode_config.set_radio_mode',
                   return_value=True) as mock_set, \
             patch('handlers.radio_mode_config.os.geteuid', return_value=1000):
            handler._radio_mode_set(RadioMode.MESHCORE)
            mock_set.assert_called_once_with(RadioMode.MESHCORE, admin=False)
            assert dialog.last_title == "Radio Mode Updated"

    def test_mode_change_as_root_uses_admin(self):
        handler, dialog = _make_handler()
        dialog._yesno_return = True
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.MESHTASTIC), \
             patch('handlers.radio_mode_config.set_radio_mode',
                   return_value=True) as mock_set, \
             patch('handlers.radio_mode_config.os.geteuid', return_value=0):
            handler._radio_mode_set(RadioMode.DUAL)
            mock_set.assert_called_once_with(RadioMode.DUAL, admin=True)

    def test_mode_change_failure(self):
        handler, dialog = _make_handler()
        dialog._yesno_return = True
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.MESHTASTIC), \
             patch('handlers.radio_mode_config.set_radio_mode',
                   return_value=False), \
             patch('handlers.radio_mode_config.os.geteuid', return_value=1000):
            handler._radio_mode_set(RadioMode.MESHCORE)
            assert dialog.last_title == "Error"


class TestGetRadioModeLabel:
    """Test get_radio_mode_label utility."""

    def test_returns_mode_value(self):
        handler, _ = _make_handler()
        with patch('handlers.radio_mode_config.get_radio_mode',
                   return_value=RadioMode.DUAL):
            assert handler.get_radio_mode_label() == "dual"
