"""
Tests for NanoVNA TUI handler -- registry pattern compliance.

Tests cover:
- Handler protocol compliance (handler_id, menu_section, menu_items, execute)
- Context injection
- Menu dispatch
- MF001 compliance (no Path.home())
"""

import inspect
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from handlers.nanovna import NanoVNAHandler


class TestHandlerProtocol:
    """Test NanoVNAHandler implements CommandHandler protocol."""

    def test_handler_id(self):
        handler = NanoVNAHandler()
        assert handler.handler_id == "nanovna"

    def test_menu_section(self):
        handler = NanoVNAHandler()
        assert handler.menu_section == "rf_sdr"

    def test_menu_items_format(self):
        """Menu items return (tag, desc, flag) tuples."""
        handler = NanoVNAHandler()
        items = handler.menu_items()
        assert len(items) >= 1
        tag, desc, flag = items[0]
        assert tag == "nanovna"
        assert isinstance(desc, str)

    def test_has_required_methods(self):
        """Handler has all required protocol methods."""
        handler = NanoVNAHandler()
        assert hasattr(handler, 'menu_items')
        assert hasattr(handler, 'execute')
        assert hasattr(handler, 'set_context')
        assert callable(handler.menu_items)
        assert callable(handler.execute)

    def test_set_context(self):
        handler = NanoVNAHandler()
        mock_ctx = MagicMock()
        handler.set_context(mock_ctx)
        assert handler.ctx is mock_ctx


class TestMenuDispatch:
    """Test menu execution and dispatch."""

    @pytest.fixture
    def handler_with_ctx(self):
        handler = NanoVNAHandler()
        ctx = MagicMock()
        ctx.dialog = MagicMock()
        ctx.safe_call = MagicMock()
        ctx.wait_for_enter = MagicMock()
        handler.set_context(ctx)
        return handler

    def test_execute_opens_menu(self, handler_with_ctx):
        """execute('nanovna') opens NanoVNA submenu."""
        handler_with_ctx.ctx.dialog.menu.return_value = "back"
        handler_with_ctx.execute("nanovna")
        handler_with_ctx.ctx.dialog.menu.assert_called()

    def test_unknown_action_ignored(self, handler_with_ctx):
        """Unknown action does nothing (no crash)."""
        handler_with_ctx.execute("unknown_action")

    def test_sweep_dispatches_via_safe_call(self, handler_with_ctx):
        """Selecting sweep calls ctx.safe_call with sweep function."""
        handler_with_ctx.ctx.dialog.menu.side_effect = ["sweep", "back"]
        handler_with_ctx.execute("nanovna")
        handler_with_ctx.ctx.safe_call.assert_called_once()
        call_args = handler_with_ctx.ctx.safe_call.call_args
        assert call_args[0][0] == "Frequency Sweep"


class TestMF001Compliance:
    """Verify no Path.home() usage (security rule MF001)."""

    def test_export_method_uses_get_real_user_home(self):
        """_nanovna_export source code uses get_real_user_home, not Path.home()."""
        source = inspect.getsource(NanoVNAHandler._nanovna_export)
        assert 'Path.home()' not in source
        assert 'get_real_user_home' in source

    def test_no_path_home_in_module(self):
        """Entire handler module has no Path.home() usage."""
        handler_file = os.path.join(
            os.path.dirname(__file__), '..', 'src', 'launcher_tui',
            'handlers', 'nanovna.py'
        )
        with open(handler_file) as f:
            content = f.read()
        assert 'Path.home()' not in content
