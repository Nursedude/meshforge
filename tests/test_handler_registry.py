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

    def test_duplicate_tag_raises(self):
        """A tag collision within a section raises instead of silently
        shadowing the earlier handler's action."""

        class ShadowingHandler(BaseHandler):
            handler_id = "shadowing"
            menu_section = "test_section"

            def menu_items(self):
                return [("alpha", "Steals SampleHandler's tag", None)]

            def execute(self, action):
                pass

        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        registry.register(SampleHandler())

        with pytest.raises(ValueError, match="Duplicate tag"):
            registry.register(ShadowingHandler())

    def test_duplicate_tag_leaves_registry_unchanged(self):
        """The refused handler must not be half-registered: not findable by
        id, not counted, and the original tag owner still dispatches."""

        class ShadowingHandler(BaseHandler):
            handler_id = "shadowing"
            menu_section = "test_section"

            def menu_items(self):
                return [("gamma", "New tag, fine", None),
                        ("alpha", "Steals SampleHandler's tag", None)]

            def execute(self, action):
                pass

        ctx = _make_context()
        registry = HandlerRegistry(ctx)
        original = SampleHandler()
        registry.register(original)

        with pytest.raises(ValueError, match="Duplicate tag"):
            registry.register(ShadowingHandler())

        assert registry.get_handler("shadowing") is None
        assert registry.handler_count == 1
        assert registry.dispatch("test_section", "alpha") is True
        assert original._last_action == "alpha"
        # The refused handler's non-colliding tag must not dispatch either.
        assert registry.dispatch("test_section", "gamma") is False

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


class TestIntraHandlerDuplicateTag:
    """QA 2026-07-05: the refuse-loud rewrite had lost the OLD code's
    warning on a duplicate tag WITHIN one handler's own menu_items() —
    restored as a raise, same contract as the cross-handler guard."""

    def test_same_handler_duplicate_tag_refused(self):
        from handler_registry import HandlerRegistry

        class _DupHandler:
            handler_id = "dup_test"
            menu_section = "system"

            def menu_items(self):
                return [("status", "Show A", None),
                        ("status", "Show B", None)]  # copy-pasted row

            def set_context(self, ctx):
                self.ctx = ctx

            def execute(self, action):
                pass

        reg = HandlerRegistry(ctx=None)
        with pytest.raises(ValueError, match="WITHIN"):
            reg.register(_DupHandler())
        # refuse-loud left the registry unchanged
        assert reg.get_handler("dup_test") is None

    def test_menu_items_snapshot_called_once_for_validation_and_index(self):
        from handler_registry import HandlerRegistry

        calls = {"n": 0}

        class _CountingHandler:
            handler_id = "count_test"
            menu_section = "system"

            def menu_items(self):
                calls["n"] += 1
                return [("count_tag", "X", None)]

            def set_context(self, ctx):
                self.ctx = ctx

            def execute(self, action):
                pass

        reg = HandlerRegistry(ctx=None)
        reg.register(_CountingHandler())
        # one snapshot serves validate + index + log — a dynamic
        # menu_items() can never validate one set and index another
        assert calls["n"] == 1


class TestShutdownWithoutStartupIsSafe:
    """Q5 (audit W11 re-decide): shutdown_all is DELIBERATELY unconditional
    — in daemon mode startup_all never runs, yet menu actions can still
    create resources that the exit sweep must reclaim. The contract that
    makes this safe: every LifecycleHandler's on_shutdown must tolerate
    being called with on_startup never having run. This test calls
    on_shutdown on a FRESH instance of every registered handler; a hook
    that assumes started-state fails here instead of at a live exit.
    """

    def test_every_lifecycle_shutdown_tolerates_cold_call(self):
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.dirname(__file__))
        from handler_test_utils import make_handler_context
        from handlers import get_all_handlers
        from handler_protocol import LifecycleHandler

        failures = []
        for cls in get_all_handlers():
            h = cls()
            if not isinstance(h, LifecycleHandler):
                continue
            h.set_context(make_handler_context())
            try:
                h.on_shutdown()
            except Exception as e:
                failures.append(f"{h.handler_id}: {type(e).__name__}: {e}")
        assert not failures, (
            "on_shutdown must be safe without on_startup (daemon-mode exit "
            f"sweep): {failures}"
        )


class TestReportActionInvalidatesStatusBar:
    """Q5 (audit W15): report_action is the mutation chokepoint — it must
    refresh the backtitle so a just-fixed service never shows its pre-fix
    state for another cache TTL."""

    def test_invalidate_called_on_success_and_failure(self):
        from unittest.mock import MagicMock
        for ok in (True, False):
            bar = MagicMock()
            ctx = TUIContext(dialog=MagicMock(), status_bar=bar)
            ctx.report_action(ok, "t", "b")
            bar.invalidate.assert_called_once()

    def test_no_status_bar_is_fine(self):
        from unittest.mock import MagicMock
        ctx = TUIContext(dialog=MagicMock(), status_bar=None)
        assert ctx.report_action(True, "t", "b") is True
