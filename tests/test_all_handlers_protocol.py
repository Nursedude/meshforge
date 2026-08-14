"""
Comprehensive Handler Protocol Compliance Tests

Tests ALL handler classes registered via get_all_handlers() for:
1. CommandHandler protocol compliance
2. Unique handler_id across the registry
3. Valid menu_items() format (3-tuple with tag, desc, flag)
4. set_context() works correctly
5. All menu_items tags are dispatchable via execute()
6. Full registry dispatch integration

Covers Batches 2-10 (Batch 1/pilot tested in test_phase1_handlers.py).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Ensure src and launcher_tui directories are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import BaseHandler, CommandHandler, TUIContext
from handler_registry import HandlerRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Single source of truth for the dialog stub — a duplicate copy used to
# live here and drifted (it grew a radiolist() the real backend never had,
# so tests certified a phantom API). tests/test_fake_dialog_parity.py now
# pins FakeDialog's surface to DialogBackend's.
sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog


def _make_context(**overrides) -> TUIContext:
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)


def _get_handler_classes():
    """Get all handler classes for parametrized tests."""
    from handlers import get_all_handlers
    return get_all_handlers()


# ---------------------------------------------------------------------------
# Protocol Compliance Tests (all handlers)
# ---------------------------------------------------------------------------

class TestAllHandlersProtocol:
    """Verify every handler satisfies the CommandHandler protocol."""

    @pytest.fixture(params=_get_handler_classes(), ids=lambda cls: cls.__name__)
    def handler_cls(self, request):
        return request.param

    def test_is_command_handler(self, handler_cls):
        """Handler must implement CommandHandler protocol."""
        h = handler_cls()
        assert isinstance(h, CommandHandler), (
            f"{handler_cls.__name__} does not satisfy CommandHandler protocol"
        )

    def test_has_handler_id(self, handler_cls):
        """handler_id must be a non-empty string."""
        h = handler_cls()
        assert isinstance(h.handler_id, str) and h.handler_id, (
            f"{handler_cls.__name__}.handler_id is empty or not a string"
        )

    def test_has_menu_section(self, handler_cls):
        """menu_section must be a non-empty string."""
        h = handler_cls()
        assert isinstance(h.menu_section, str) and h.menu_section, (
            f"{handler_cls.__name__}.menu_section is empty or not a string"
        )

    def test_menu_items_format(self, handler_cls):
        """menu_items() must return list of (tag, desc, flag) tuples."""
        h = handler_cls()
        items = h.menu_items()
        assert isinstance(items, list), (
            f"{handler_cls.__name__}.menu_items() must return a list"
        )
        for item in items:
            assert len(item) == 3, (
                f"{handler_cls.__name__}.menu_items() item must be 3-tuple, got {item}"
            )
            tag, desc, flag = item
            assert isinstance(tag, str), f"tag must be str, got {type(tag)}"
            assert isinstance(desc, str), f"desc must be str, got {type(desc)}"
            assert flag is None or isinstance(flag, str), (
                f"flag must be None or str, got {type(flag)}"
            )

    def test_set_context(self, handler_cls):
        """set_context() must store TUIContext on the handler."""
        h = handler_cls()
        ctx = _make_context()
        h.set_context(ctx)
        assert h.ctx is ctx, (
            f"{handler_cls.__name__}.set_context() did not store context"
        )

    def test_extends_base_handler(self, handler_cls):
        """Handler should extend BaseHandler."""
        h = handler_cls()
        assert isinstance(h, BaseHandler), (
            f"{handler_cls.__name__} should extend BaseHandler"
        )


# ---------------------------------------------------------------------------
# Uniqueness Tests
# ---------------------------------------------------------------------------

class TestHandlerUniqueness:
    """Verify handler IDs and tags are unique."""

    def test_handler_ids_unique(self):
        """All handler IDs must be unique across the entire registry."""
        classes = _get_handler_classes()
        ids = [cls().handler_id for cls in classes]
        duplicates = [hid for hid in ids if ids.count(hid) > 1]
        assert len(ids) == len(set(ids)), (
            f"Duplicate handler_ids found: {set(duplicates)}"
        )

    def test_no_duplicate_tags_in_section(self):
        """No two handlers should register the same tag in the same section."""
        classes = _get_handler_classes()
        seen = {}  # (section, tag) -> handler_id
        for cls in classes:
            h = cls()
            for tag, _, _ in h.menu_items():
                key = (h.menu_section, tag)
                if key in seen:
                    pytest.fail(
                        f"Duplicate tag {tag!r} in section {h.menu_section!r}: "
                        f"{seen[key]} and {h.handler_id}"
                    )
                seen[key] = h.handler_id


class TestNoOrphanHandlerModules:
    """Every handler module on disk must be registered.

    ``get_all_handlers()`` is a hand-maintained batch list — the one wiring
    step nothing else verifies. A new ``handlers/foo.py`` that defines a
    ``*Handler`` class but never gets a batch entry is silently unreachable
    from the TUI (the #79 deploy-gap class, TUI form). This walks the
    handlers/ directory and fails on any such orphan.
    """

    # CLASSES allowed to exist without being registered (e.g. abstract
    # bases). Keep empty unless a class is deliberately registration-exempt
    # — and say why here when adding one. Class-level (not module-level)
    # since 2026-07-05: one registered class must not whitelist an
    # unregistered sibling in the same file.
    EXEMPT_CLASSES: set = set()

    def _handler_classes_on_disk(self):
        """{class name: module} for every concrete-looking *Handler class
        in handlers/ — INCLUDING _-prefixed helper modules (a handler
        hidden in a 'helper' file is still unreachable, not exempt)."""
        import ast
        from pathlib import Path

        handlers_dir = (
            Path(__file__).parent.parent / "src" / "launcher_tui" / "handlers"
        )
        found = {}
        for path in sorted(handlers_dir.glob("*.py")):
            if path.stem == "__init__":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.ClassDef)
                        and node.name.endswith("Handler")
                        # only classes with a menu surface are dispatchable
                        and any(isinstance(b, ast.FunctionDef)
                                and b.name == "menu_items"
                                for b in node.body)):
                    found[node.name] = path.stem
        return found

    def test_every_handler_class_is_registered(self):
        registered = {cls.__name__ for cls in _get_handler_classes()}
        orphans = [
            f"{name} (in {module}.py)"
            for name, module in self._handler_classes_on_disk().items()
            if name not in registered and name not in self.EXEMPT_CLASSES
        ]
        assert not orphans, (
            "Handler class(es) on disk but missing from get_all_handlers() — "
            "unreachable from the TUI (a second class in an already-"
            "registered module counts; so does one in a _-prefixed file). "
            "Add a batch entry in handlers/__init__.py or EXEMPT_CLASSES "
            f"(with a reason): {orphans}"
        )

    def test_exempt_classes_do_not_rot(self):
        """An EXEMPT_CLASSES entry that no longer exists on disk, or that
        became registered anyway, is stale — exemptions must be re-earned,
        not accumulate."""
        on_disk = set(self._handler_classes_on_disk())
        registered = {cls.__name__ for cls in _get_handler_classes()}
        stale = [c for c in self.EXEMPT_CLASSES
                 if c not in on_disk or c in registered]
        assert not stale, f"stale EXEMPT_CLASSES entries: {stale}"


# ---------------------------------------------------------------------------
# Registry Integration Tests
# ---------------------------------------------------------------------------

class TestFullRegistryIntegration:
    """Test full registry creation and dispatch."""

    @pytest.fixture
    def registry(self):
        ctx = _make_context()
        reg = HandlerRegistry(ctx)
        for cls in _get_handler_classes():
            reg.register(cls())
        return reg

    def test_all_handlers_registered(self, registry):
        """Every handler class should be in the registry."""
        expected = len(_get_handler_classes())
        assert registry.handler_count == expected, (
            f"Expected {expected} handlers, got {registry.handler_count}"
        )

    def test_all_sections_have_items(self, registry):
        """Every section should have at least one menu item."""
        for section in registry.section_names:
            items = registry.get_menu_items(section)
            assert len(items) > 0, (
                f"Section {section!r} has no menu items"
            )

    def test_all_tags_dispatch(self, registry, no_network):
        """Every registered tag should dispatch successfully.

        ``no_network`` makes this hermetic: dispatch actually runs each
        handler's ``execute()`` (wrapped in ``safe_call``), and some paths
        reach the network — e.g. the AREDN worldmap fetch in
        ``_map_collector_public._fetch_aredn_worldmap_nodes``. In CI a blocked
        connect usually fails fast, but a reachable-but-slow host let the
        ``socket.connect`` hang past pytest-timeout and reddened the suite
        (a real CI flake, observed 2026-06-16). Blocking ``socket.socket``
        turns any such attempt into an instant OSError that ``safe_call``
        catches, so dispatch still returns True — the routing assertion this
        test exists for is unchanged, just no longer at the mercy of the net.
        """
        for section in registry.section_names:
            for tag, _ in registry.get_menu_items(section):
                result = registry.dispatch(section, tag)
                assert result is True, (
                    f"Dispatch failed: section={section!r}, tag={tag!r}"
                )


# ---------------------------------------------------------------------------
# Batch 10 Specific Tests (new handlers from QA cleanup)
# ---------------------------------------------------------------------------

class TestAboutHandler:
    """Tests for the About menu handler."""

    @pytest.fixture
    def handler(self):
        from handlers.about import AboutHandler
        h = AboutHandler()
        h.set_context(_make_context())
        return h

    def test_handler_id(self, handler):
        assert handler.handler_id == "about"

    def test_menu_section(self, handler):
        assert handler.menu_section == "about"

    def test_menu_items_tags(self, handler):
        tags = [tag for tag, _, _ in handler.menu_items()]
        assert "version" in tags
        assert "changelog" in tags
        assert "sysinfo" in tags
        assert "deps" in tags
        assert "help" in tags

    def test_execute_version(self, handler):
        """execute('version') should call _show_version via safe_call."""
        handler.execute("version")
        assert handler.ctx.dialog.last_msgbox_title == "About MeshForge"


class TestDaemonHandler:
    """Tests for the Daemon mode handler."""

    @pytest.fixture
    def handler(self):
        from handlers.daemon import DaemonHandler
        h = DaemonHandler()
        h.set_context(_make_context())
        return h

    def test_handler_id(self, handler):
        assert handler.handler_id == "daemon"

    def test_menu_section(self, handler):
        assert handler.menu_section == "system"

    def test_menu_items_tag(self, handler):
        tags = [tag for tag, _, _ in handler.menu_items()]
        assert "daemon" in tags


class TestRebootHandler:
    """Tests for the Reboot/Shutdown handler."""

    @pytest.fixture
    def handler(self):
        from handlers.reboot import RebootHandler
        h = RebootHandler()
        h.set_context(_make_context())
        return h

    def test_handler_id(self, handler):
        assert handler.handler_id == "reboot"

    def test_menu_section(self, handler):
        assert handler.menu_section == "system"

    def test_menu_items_tag(self, handler):
        tags = [tag for tag, _, _ in handler.menu_items()]
        assert "reboot" in tags


class TestDiagnosticsHandler:
    """Tests for the CLI diagnostics handler."""

    @pytest.fixture
    def handler(self):
        from handlers.diagnostics import DiagnosticsHandler
        h = DiagnosticsHandler()
        h.set_context(_make_context())
        return h

    def test_handler_id(self, handler):
        assert handler.handler_id == "diagnostics"

    def test_menu_section(self, handler):
        assert handler.menu_section == "system"

    def test_menu_items_tags(self, handler):
        tags = [tag for tag, _, _ in handler.menu_items()]
        assert "diagnose" in tags
        assert "status" in tags


class TestConfigAPIHandler:
    """Tests for the Config API Server handler."""

    @pytest.fixture
    def handler(self):
        from handlers.config_api import ConfigAPIHandler
        h = ConfigAPIHandler()
        h.set_context(_make_context())
        return h

    def test_handler_id(self, handler):
        assert handler.handler_id == "config_api"

    def test_menu_section(self, handler):
        assert handler.menu_section == "configuration"

    def test_menu_items_tag(self, handler):
        tags = [tag for tag, _, _ in handler.menu_items()]
        assert "config-api" in tags

    def test_has_lifecycle_methods(self, handler):
        """ConfigAPIHandler should implement lifecycle hooks."""
        assert hasattr(handler, 'on_startup')
        assert hasattr(handler, 'on_shutdown')
        assert callable(handler.on_startup)
        assert callable(handler.on_shutdown)

    def test_server_initially_none(self, handler):
        assert handler._server is None
