"""
Tests for MeshCoreConfigHandler.

Covers:
- Handler metadata (id, section, menu items, feature flag)
- Execute dispatch
- Status line formatting
- Device access helper

Run: python3 -m pytest tests/test_meshcore_config_handler.py -v
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import BaseHandler, TUIContext
from handlers.meshcore_config import MeshCoreConfigHandler, _check_device_access


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
    """Create a MeshCoreConfigHandler with mock context."""
    handler = MeshCoreConfigHandler()
    dialog = FakeDialog()
    ctx = TUIContext(dialog=dialog, feature_flags={"meshcore": True})
    handler.set_context(ctx)
    return handler, dialog


class TestHandlerMetadata:
    """Test handler registration metadata."""

    def test_handler_id(self):
        handler = MeshCoreConfigHandler()
        assert handler.handler_id == "meshcore_config"

    def test_menu_section(self):
        handler = MeshCoreConfigHandler()
        assert handler.menu_section == "configuration"

    def test_is_base_handler(self):
        handler = MeshCoreConfigHandler()
        assert isinstance(handler, BaseHandler)

    def test_menu_items_structure(self):
        handler = MeshCoreConfigHandler()
        items = handler.menu_items()
        assert len(items) == 1
        tag, desc, flag = items[0]
        assert tag == "meshcore_config"
        assert flag == "meshcore"

    def test_menu_items_description(self):
        handler = MeshCoreConfigHandler()
        items = handler.menu_items()
        assert "MeshCore Config" in items[0][1]


class TestExecuteDispatch:
    """Test execute routes to the correct method."""

    def test_execute_calls_menu(self):
        handler, dialog = _make_handler()
        with patch.object(handler, '_meshcore_config_menu') as mock_menu:
            handler.execute("meshcore_config")
            mock_menu.assert_called_once()

    def test_execute_ignores_unknown_action(self):
        handler, _ = _make_handler()
        handler.execute("unknown_action")


class TestStatusLine:
    """Test _mc_config_status_line formatting."""

    def test_initialized_structure(self):
        handler, _ = _make_handler()
        status = {
            "structure_exists": True,
            "available_configs": 3,
            "enabled_configs": 1,
            "detected_devices": [],
            "meshcore_py_installed": True,
        }
        result = handler._mc_config_status_line(status)
        assert "available: 3" in result
        assert "enabled: 1" in result
        assert "meshcore_py: yes" in result

    def test_uninitialized_structure(self):
        handler, _ = _make_handler()
        status = {
            "structure_exists": False,
            "detected_devices": [],
            "meshcore_py_installed": False,
        }
        result = handler._mc_config_status_line(status)
        assert "/etc/meshcore not initialized" in result
        assert "meshcore_py: no" in result

    def test_with_devices(self):
        handler, _ = _make_handler()
        status = {
            "structure_exists": True,
            "available_configs": 2,
            "enabled_configs": 0,
            "detected_devices": ["/dev/ttyUSB0", "/dev/ttyUSB1"],
            "meshcore_py_installed": True,
        }
        result = handler._mc_config_status_line(status)
        assert "devices: 2" in result


class TestCheckDeviceAccess:
    """Test _check_device_access helper."""

    def test_accessible_device(self):
        with patch('handlers.meshcore_config.os.access', return_value=True):
            assert _check_device_access("/dev/ttyUSB0") is True

    def test_inaccessible_device(self):
        with patch('handlers.meshcore_config.os.access', return_value=False):
            assert _check_device_access("/dev/ttyUSB0") is False


class TestConfigUnavailable:
    """Test behavior when core.meshcore_config is not available."""

    def test_shows_message_when_unavailable(self):
        handler, dialog = _make_handler()
        with patch('handlers.meshcore_config._HAS_MC_CONFIG', False):
            handler._meshcore_config_menu()
            assert dialog.last_title == "MeshCore Config"
            assert "not available" in dialog.last_text
