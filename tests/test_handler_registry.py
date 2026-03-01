"""
Handler Registry Tests

Tests for the TUIContext, CommandHandler Protocol, BaseHandler, and
HandlerRegistry — the Phase 0 infrastructure for the mixin-to-registry
migration.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Ensure src and launcher_tui directories are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import BaseHandler, CommandHandler, LifecycleHandler, TUIContext
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


def _make_context(**overrides) -> TUIContext:
    """Create a TUIContext with sensible defaults for testing."""
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)


class SampleHandler(BaseHandler):
    """A concrete handler for testing."""

    handler_id = "sample"
    menu_section = "test_section"

    def menu_items(self):
        return [
            ("alpha", "Alpha action", None),
            ("beta", "Beta action (gated)", "beta_feature"),
        ]

    def execute(self, action):
        self._last_action = action


class AnotherHandler(BaseHandler):
    """A second handler in the same section."""

    handler_id = "another"
    menu_section = "test_section"

    def menu_items(self):
        return [
            ("gamma", "Gamma action", None),
        ]

    def execute(self, action):
        self._last_action = action


class DifferentSectionHandler(BaseHandler):
    """A handler in a different section."""

    handler_id = "different"
    menu_section = "other_section"

    def menu_items(self):
        return [
            ("delta", "Delta action", None),
        ]

    def execute(self, action):
        self._last_action = action


class LifecycleTestHandler(BaseHandler):
    """A handler that implements the LifecycleHandler protocol."""

    handler_id = "lifecycle"
    menu_section = "test_section"

    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = False

    def menu_items(self):
        return []

    def execute(self, action):
        pass

    def on_startup(self):
        self.started = True

    def on_shutdown(self):
        self.stopped = True


# ---------------------------------------------------------------------------
# TUIContext tests
# ---------------------------------------------------------------------------

class TestTUIContext:
    """Tests for TUIContext shared-state object."""

    def test_feature_enabled_no_flags(self):
        ctx = _make_context(feature_flags={})
        assert ctx.feature_enabled("anything") is True

    def test_feature_enabled_with_flags(self):
        ctx = _make_context(feature_flags={"maps": True, "mqtt": False})
        assert ctx.feature_enabled("maps") is True
        assert ctx.feature_enabled("mqtt") is False
        # Unknown features default to True
        assert ctx.feature_enabled("unknown") is True

    def test_validate_hostname_valid(self):
        assert TUIContext.validate_hostname("localhost") is True
        assert TUIContext.validate_hostname("192.168.1.1") is True
        assert TUIContext.validate_hostname("my-host.local") is True
        assert TUIContext.validate_hostname("::1") is True

    def test_validate_hostname_invalid(self):
        assert TUIContext.validate_hostname("") is False
        assert TUIContext.validate_hostname("-flag") is False
        assert TUIContext.validate_hostname("a" * 254) is False
        assert TUIContext.validate_hostname("host name") is False

    def test_validate_port(self):
        assert TUIContext.validate_port("80") is True
        assert TUIContext.validate_port("1") is True
        assert TUIContext.validate_port("65535") is True
        assert TUIContext.validate_port("0") is False
        assert TUIContext.validate_port("65536") is False
        assert TUIContext.validate_port("abc") is False
        assert TUIContext.validate_port("") is False

    def test_safe_call_success(self):
        ctx = _make_context()
        result = ctx.safe_call("test", lambda: 42)
        assert result == 42

    def test_safe_call_catches_import_error(self):
        ctx = _make_context()
        dialog = ctx.dialog

        def failing():
            raise ImportError("No module named 'missing_module'")

        ctx.safe_call("test", failing)
        assert dialog.last_msgbox_title == "Module Not Available"

    def test_safe_call_catches_generic_exception(self):
        ctx = _make_context()
        dialog = ctx.dialog

        def failing():
            raise RuntimeError("something broke")

        ctx.safe_call("test", failing)
        assert dialog.last_msgbox_title == "Error"
        assert "something broke" in dialog.last_msgbox_text

    def test_safe_call_reraises_keyboard_interrupt(self):
        ctx = _make_context()

        def failing():
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            ctx.safe_call("test", failing)


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """Verify that BaseHandler and test handlers satisfy the Protocols."""

    def test_base_handler_is_command_handler(self):
        handler = SampleHandler()
        assert isinstance(handler, CommandHandler)

    def test_lifecycle_handler_protocol(self):
        handler = LifecycleTestHandler()
        assert isinstance(handler, LifecycleHandler)

    def test_base_handler_not_lifecycle(self):
        handler = SampleHandler()
        assert not isinstance(handler, LifecycleHandler)


# ---------------------------------------------------------------------------
# HandlerRegistry tests
# ---------------------------------------------------------------------------

class TestHandlerRegistry:
    """Tests for HandlerRegistry registration and lookup."""

    def test_register_and_lookup(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        handler = SampleHandler()

        registry.register(handler)

        assert registry.get_handler("sample") is handler
        assert registry.handler_count == 1

    def test_register_injects_context(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        handler = SampleHandler()

        registry.register(handler)

        assert handler.ctx is ctx

    def test_duplicate_id_raises(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(SampleHandler())

    def test_lookup_missing_returns_none(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        assert registry.get_handler("nonexistent") is None

    def test_handler_count(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        assert registry.handler_count == 0

        registry.register(SampleHandler())
        assert registry.handler_count == 1

        registry.register(AnotherHandler())
        assert registry.handler_count == 2

    def test_section_names(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())
        registry.register(DifferentSectionHandler())

        sections = registry.section_names
        assert "test_section" in sections
        assert "other_section" in sections


class TestRegistryMenuItems:
    """Tests for get_menu_items() with feature-flag filtering."""

    def test_get_menu_items_all_visible(self):
        ctx = _make_context(feature_flags={})
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        items = registry.get_menu_items("test_section")
        tags = [tag for tag, _desc in items]
        assert "alpha" in tags
        assert "beta" in tags

    def test_get_menu_items_feature_gated_hidden(self):
        ctx = _make_context(feature_flags={"beta_feature": False})
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        items = registry.get_menu_items("test_section")
        tags = [tag for tag, _desc in items]
        assert "alpha" in tags
        assert "beta" not in tags

    def test_get_menu_items_multiple_handlers(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())
        registry.register(AnotherHandler())

        items = registry.get_menu_items("test_section")
        tags = [tag for tag, _desc in items]
        assert "alpha" in tags
        assert "gamma" in tags

    def test_get_menu_items_empty_section(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        items = registry.get_menu_items("nonexistent_section")
        assert items == []


class TestRegistryDispatch:
    """Tests for dispatch() — finding and executing handlers by tag."""

    def test_dispatch_success(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        handler = SampleHandler()
        registry.register(handler)

        result = registry.dispatch("test_section", "alpha")

        assert result is True
        assert handler._last_action == "alpha"

    def test_dispatch_unknown_tag_returns_false(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        result = registry.dispatch("test_section", "nonexistent")

        assert result is False

    def test_dispatch_wrong_section_returns_false(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        result = registry.dispatch("wrong_section", "alpha")

        assert result is False

    def test_dispatch_routes_to_correct_handler(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        sample = SampleHandler()
        another = AnotherHandler()
        registry.register(sample)
        registry.register(another)

        registry.dispatch("test_section", "gamma")

        assert another._last_action == "gamma"
        assert not hasattr(sample, '_last_action')


class TestRegistryLifecycle:
    """Tests for startup_all() and shutdown_all() hooks."""

    def test_startup_calls_lifecycle_handlers(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        lifecycle = LifecycleTestHandler()
        plain = SampleHandler()
        registry.register(lifecycle)
        registry.register(plain)

        registry.startup_all()

        assert lifecycle.started is True

    def test_shutdown_calls_lifecycle_handlers(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        lifecycle = LifecycleTestHandler()
        registry.register(lifecycle)

        registry.shutdown_all()

        assert lifecycle.stopped is True

    def test_startup_handles_errors_gracefully(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)

        class FailingLifecycle(BaseHandler):
            handler_id = "failing_lifecycle"
            menu_section = "test"

            def on_startup(self):
                raise RuntimeError("startup boom")

            def on_shutdown(self):
                pass

        registry.register(FailingLifecycle())
        # Should not raise
        registry.startup_all()


class TestRegistryRepr:
    """Test __repr__ for debugging."""

    def test_repr(self):
        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())
        r = repr(registry)
        assert "handlers=1" in r
        assert "test_section" in r
