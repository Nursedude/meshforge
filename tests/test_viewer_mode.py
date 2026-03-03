"""
Viewer Mode Tests

Tests for the privilege model that enables MeshForge TUI to run without sudo
(Viewer Mode), blocking admin operations with a dialog instead of errors.
"""

import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

# Ensure src and launcher_tui directories are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import (
    BaseHandler, TUIContext,
    PRIVILEGE_ADMIN, PRIVILEGE_VIEWER,
)
from handler_registry import HandlerRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeDialog:
    """Minimal dialog stub for testing."""

    def __init__(self):
        self.last_msgbox_title = None
        self.last_msgbox_text = None

    def msgbox(self, title, text, **kwargs):
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices):
        return None

    def yesno(self, title, text):
        return False

    def inputbox(self, title, text, default=""):
        return default


@dataclass
class FakeEnvState:
    """Minimal env_state stub with is_root flag."""
    is_root: bool = False

    def get_status_line(self, plain=False):
        return "meshtasticd: --  rnsd: --"


def _make_context(is_root=False, **overrides) -> TUIContext:
    """Create a TUIContext with sensible defaults for testing."""
    defaults = dict(
        dialog=FakeDialog(),
        env_state=FakeEnvState(is_root=is_root),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)


class ViewerHandler(BaseHandler):
    """A handler that requires no privileges (default)."""
    handler_id = "viewer_handler"
    menu_section = "test"

    def menu_items(self):
        return [("view", "View data", None)]

    def execute(self, action):
        self._last_action = action


class AdminHandler(BaseHandler):
    """A handler that requires admin privileges."""
    handler_id = "admin_handler"
    menu_section = "test"
    privilege_level = PRIVILEGE_ADMIN

    def menu_items(self):
        return [("restart", "Restart service", None)]

    def execute(self, action):
        self._last_action = action


class MixedHandler(BaseHandler):
    """A handler with both viewer and admin items."""
    handler_id = "mixed_handler"
    menu_section = "test"
    admin_tags = frozenset({"restart", "config_write"})

    def menu_items(self):
        return [
            ("status", "Show status", None),
            ("restart", "Restart service", None),
            ("config_write", "Write config", None),
        ]

    def execute(self, action):
        self._last_action = action


# ---------------------------------------------------------------------------
# TUIContext.is_admin tests
# ---------------------------------------------------------------------------

class TestIsAdmin:
    """Tests for TUIContext.is_admin property."""

    def test_is_admin_false_when_env_state_not_root(self):
        ctx = _make_context(is_root=False)
        assert ctx.is_admin is False

    def test_is_admin_true_when_env_state_is_root(self):
        ctx = _make_context(is_root=True)
        assert ctx.is_admin is True

    def test_is_admin_falls_back_to_euid_when_no_env_state(self):
        ctx = _make_context()
        ctx.env_state = None
        with patch('os.geteuid', return_value=1000):
            assert ctx.is_admin is False
        with patch('os.geteuid', return_value=0):
            assert ctx.is_admin is True


# ---------------------------------------------------------------------------
# TUIContext.check_privilege tests
# ---------------------------------------------------------------------------

class TestCheckPrivilege:
    """Tests for TUIContext.check_privilege method."""

    def test_viewer_privilege_always_allowed(self):
        ctx = _make_context(is_root=False)
        assert ctx.check_privilege("test", PRIVILEGE_VIEWER) is True

    def test_admin_privilege_allowed_when_root(self):
        ctx = _make_context(is_root=True)
        assert ctx.check_privilege("test", PRIVILEGE_ADMIN) is True

    def test_admin_privilege_blocked_when_not_root(self):
        ctx = _make_context(is_root=False)
        assert ctx.check_privilege("test", PRIVILEGE_ADMIN) is False

    def test_admin_privilege_shows_dialog_when_blocked(self):
        ctx = _make_context(is_root=False)
        ctx.check_privilege("Restart Service", PRIVILEGE_ADMIN)
        assert ctx.dialog.last_msgbox_title == "Admin Privileges Required"
        assert "Viewer Mode" in ctx.dialog.last_msgbox_text


# ---------------------------------------------------------------------------
# BaseHandler privilege defaults
# ---------------------------------------------------------------------------

class TestBaseHandlerPrivilege:
    """Tests for BaseHandler privilege_level and admin_tags defaults."""

    def test_default_privilege_is_viewer(self):
        handler = ViewerHandler()
        assert handler.privilege_level == PRIVILEGE_VIEWER

    def test_admin_handler_privilege(self):
        handler = AdminHandler()
        assert handler.privilege_level == PRIVILEGE_ADMIN

    def test_default_admin_tags_empty(self):
        handler = ViewerHandler()
        assert handler.admin_tags == frozenset()

    def test_mixed_handler_admin_tags(self):
        handler = MixedHandler()
        assert "restart" in handler.admin_tags
        assert "status" not in handler.admin_tags


# ---------------------------------------------------------------------------
# BaseHandler.get_item_privilege tests
# ---------------------------------------------------------------------------

class TestGetItemPrivilege:
    """Tests for BaseHandler.get_item_privilege method."""

    def test_viewer_handler_all_items_viewer(self):
        handler = ViewerHandler()
        assert handler.get_item_privilege("view") == PRIVILEGE_VIEWER
        assert handler.get_item_privilege("unknown") == PRIVILEGE_VIEWER

    def test_admin_handler_all_items_admin(self):
        handler = AdminHandler()
        assert handler.get_item_privilege("restart") == PRIVILEGE_ADMIN
        assert handler.get_item_privilege("unknown") == PRIVILEGE_ADMIN

    def test_mixed_handler_viewer_item(self):
        handler = MixedHandler()
        assert handler.get_item_privilege("status") == PRIVILEGE_VIEWER

    def test_mixed_handler_admin_item(self):
        handler = MixedHandler()
        assert handler.get_item_privilege("restart") == PRIVILEGE_ADMIN
        assert handler.get_item_privilege("config_write") == PRIVILEGE_ADMIN


# ---------------------------------------------------------------------------
# HandlerRegistry privilege gating tests
# ---------------------------------------------------------------------------

class TestRegistryPrivilegeGating:
    """Tests for registry-level privilege checks in dispatch and menus."""

    def test_dispatch_allows_viewer_in_viewer_mode(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        handler = ViewerHandler()
        registry.register(handler)

        result = registry.dispatch("test", "view")

        assert result is True
        assert handler._last_action == "view"

    def test_dispatch_blocks_admin_in_viewer_mode(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        handler = AdminHandler()
        registry.register(handler)

        result = registry.dispatch("test", "restart")

        assert result is True  # Handled (dialog shown)
        assert not hasattr(handler, '_last_action')  # NOT executed
        assert ctx.dialog.last_msgbox_title == "Admin Privileges Required"

    def test_dispatch_allows_admin_in_admin_mode(self):
        ctx = _make_context(is_root=True)
        registry = HandlerRegistry(ctx)
        handler = AdminHandler()
        registry.register(handler)

        result = registry.dispatch("test", "restart")

        assert result is True
        assert handler._last_action == "restart"

    def test_dispatch_blocks_mixed_admin_tag_in_viewer_mode(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        handler = MixedHandler()
        registry.register(handler)

        result = registry.dispatch("test", "restart")

        assert result is True
        assert not hasattr(handler, '_last_action')
        assert ctx.dialog.last_msgbox_title == "Admin Privileges Required"

    def test_dispatch_allows_mixed_viewer_tag_in_viewer_mode(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        handler = MixedHandler()
        registry.register(handler)

        result = registry.dispatch("test", "status")

        assert result is True
        assert handler._last_action == "status"


# ---------------------------------------------------------------------------
# Menu annotation tests
# ---------------------------------------------------------------------------

class TestMenuAnnotation:
    """Tests for [sudo] annotation in menu descriptions."""

    def test_admin_items_annotated_in_viewer_mode(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        registry.register(AdminHandler())

        items = registry.get_menu_items("test")

        assert len(items) == 1
        tag, desc = items[0]
        assert tag == "restart"
        assert "[sudo]" in desc

    def test_admin_items_not_annotated_in_admin_mode(self):
        ctx = _make_context(is_root=True)
        registry = HandlerRegistry(ctx)
        registry.register(AdminHandler())

        items = registry.get_menu_items("test")

        assert len(items) == 1
        _tag, desc = items[0]
        assert "[sudo]" not in desc

    def test_viewer_items_never_annotated(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        registry.register(ViewerHandler())

        items = registry.get_menu_items("test")

        assert len(items) == 1
        _tag, desc = items[0]
        assert "[sudo]" not in desc

    def test_mixed_handler_only_admin_tags_annotated(self):
        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        registry.register(MixedHandler())

        items = registry.get_menu_items("test")

        annotations = {tag: "[sudo]" in desc for tag, desc in items}
        assert annotations["status"] is False
        assert annotations["restart"] is True
        assert annotations["config_write"] is True


# ---------------------------------------------------------------------------
# Status hint tests
# ---------------------------------------------------------------------------

class TestStatusHint:
    """Tests for mode indicator in menu status hint."""

    def test_viewer_mode_in_hint(self):
        ctx = _make_context(is_root=False)
        hint = ctx.get_menu_status_hint()
        assert "[Viewer]" in hint

    def test_admin_mode_in_hint(self):
        ctx = _make_context(is_root=True)
        hint = ctx.get_menu_status_hint()
        assert "[Admin]" in hint


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Ensure handlers without privilege declarations default to viewer."""

    def test_handler_without_privilege_level_defaults_viewer(self):
        """A handler that doesn't set privilege_level should be VIEWER."""

        class LegacyHandler(BaseHandler):
            handler_id = "legacy"
            menu_section = "test"

            def menu_items(self):
                return [("action", "Do something", None)]

            def execute(self, action):
                self._last_action = action

        handler = LegacyHandler()
        assert handler.privilege_level == PRIVILEGE_VIEWER
        assert handler.admin_tags == frozenset()

    def test_legacy_handler_dispatches_in_viewer_mode(self):
        """Legacy handlers (no privilege declared) should work in viewer mode."""

        class LegacyHandler(BaseHandler):
            handler_id = "legacy"
            menu_section = "test"

            def menu_items(self):
                return [("action", "Do something", None)]

            def execute(self, action):
                self._last_action = action

        ctx = _make_context(is_root=False)
        registry = HandlerRegistry(ctx)
        handler = LegacyHandler()
        registry.register(handler)

        result = registry.dispatch("test", "action")

        assert result is True
        assert handler._last_action == "action"


# ---------------------------------------------------------------------------
# PermissionError message tests
# ---------------------------------------------------------------------------

class TestPermissionErrorMessage:
    """Tests for improved PermissionError dialog in viewer mode."""

    def test_permission_error_viewer_mode_mentions_viewer(self):
        ctx = _make_context(is_root=False)

        def failing():
            raise PermissionError("cannot write /etc/config")

        ctx.safe_call("test_op", failing)
        assert "Viewer Mode" in ctx.dialog.last_msgbox_text

    def test_permission_error_admin_mode_no_viewer_mention(self):
        ctx = _make_context(is_root=True)

        def failing():
            raise PermissionError("cannot write /etc/config")

        ctx.safe_call("test_op", failing)
        assert "Viewer Mode" not in ctx.dialog.last_msgbox_text


# ---------------------------------------------------------------------------
# _is_admin_item static method tests
# ---------------------------------------------------------------------------

class TestIsAdminItem:
    """Tests for HandlerRegistry._is_admin_item static method."""

    def test_viewer_handler_not_admin(self):
        assert HandlerRegistry._is_admin_item(ViewerHandler(), "view") is False

    def test_admin_handler_is_admin(self):
        assert HandlerRegistry._is_admin_item(AdminHandler(), "restart") is True

    def test_mixed_handler_viewer_tag(self):
        assert HandlerRegistry._is_admin_item(MixedHandler(), "status") is False

    def test_mixed_handler_admin_tag(self):
        assert HandlerRegistry._is_admin_item(MixedHandler(), "restart") is True
