"""
Phase 1 Handler Tests

Tests for the 5 pilot handlers converted from mixins:
- LatencyHandler
- ClassifierHandler
- AmateurRadioHandler
- AnalyticsHandler
- RFToolsHandler

Validates that:
1. Each handler satisfies the CommandHandler protocol
2. menu_items() returns expected tags and sections
3. execute() dispatches to the correct internal method
4. Handlers work with TUIContext instead of self.* access
5. Discovery via get_all_handlers() includes all 5
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure src and launcher_tui directories are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handler_protocol import BaseHandler, CommandHandler, TUIContext
from handler_registry import HandlerRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeDialog:
    """Minimal dialog stub for testing handler dispatch."""

    def __init__(self):
        self.last_msgbox_title = None
        self.last_msgbox_text = None
        self._menu_return = None
        self.inputbox_returns = []

    def msgbox(self, title, text, **kwargs):
        self.last_msgbox_title = title
        self.last_msgbox_text = text

    def menu(self, title, text, choices):
        return self._menu_return

    def yesno(self, title, text):
        return False

    def inputbox(self, title, text, default=""):
        if self.inputbox_returns:
            return self.inputbox_returns.pop(0)
        return default


def _make_context(**overrides) -> TUIContext:
    defaults = dict(
        dialog=FakeDialog(),
        feature_flags={},
    )
    defaults.update(overrides)
    return TUIContext(**defaults)


# ---------------------------------------------------------------------------
# Discovery test
# ---------------------------------------------------------------------------

class TestHandlerDiscovery:
    """Test that get_all_handlers() returns all 5 pilot handlers."""

    def test_get_all_handlers_returns_expected_count(self):
        from handlers import get_all_handlers
        handlers = get_all_handlers()
        assert len(handlers) >= 5  # Phase 1: 5, Batch 1: +8 = 13

    def test_get_all_handlers_classes_are_correct(self):
        from handlers import get_all_handlers
        handler_ids = {cls().handler_id for cls in get_all_handlers()}
        # Phase 1 pilot handlers must always be present
        phase1 = {"latency", "classifier", "amateur_radio", "analytics", "rf_tools"}
        assert phase1.issubset(handler_ids)
        # Batch 1 handlers
        batch1 = {"node_health", "metrics", "propagation", "site_planner",
                   "sdr", "link_quality", "webhooks", "network_tools"}
        assert batch1.issubset(handler_ids)


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """All 5 handlers satisfy CommandHandler protocol."""

    @pytest.fixture(params=[
        "handlers.latency:LatencyHandler",
        "handlers.classifier:ClassifierHandler",
        "handlers.amateur_radio:AmateurRadioHandler",
        "handlers.analytics:AnalyticsHandler",
        "handlers.rf_tools:RFToolsHandler",
    ])
    def handler_instance(self, request):
        module_path, cls_name = request.param.split(":")
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)
        return cls()

    def test_is_command_handler(self, handler_instance):
        assert isinstance(handler_instance, CommandHandler)

    def test_has_handler_id(self, handler_instance):
        assert handler_instance.handler_id
        assert isinstance(handler_instance.handler_id, str)

    def test_has_menu_section(self, handler_instance):
        assert handler_instance.menu_section
        assert isinstance(handler_instance.menu_section, str)

    def test_menu_items_returns_tuples(self, handler_instance):
        items = handler_instance.menu_items()
        assert isinstance(items, list)
        assert len(items) > 0
        for item in items:
            assert len(item) == 3  # (tag, desc, flag_or_None)
            tag, desc, flag = item
            assert isinstance(tag, str)
            assert isinstance(desc, str)
            assert flag is None or isinstance(flag, str)

    def test_set_context(self, handler_instance):
        ctx = _make_context()
        handler_instance.set_context(ctx)
        assert handler_instance.ctx is ctx


# ---------------------------------------------------------------------------
# LatencyHandler tests
# ---------------------------------------------------------------------------

class TestLatencyHandler:

    def test_menu_section(self):
        from handlers.latency import LatencyHandler
        h = LatencyHandler()
        assert h.menu_section == "dashboard"
        assert h.handler_id == "latency"

    def test_menu_items_tag(self):
        from handlers.latency import LatencyHandler
        h = LatencyHandler()
        tags = [t for t, _, _ in h.menu_items()]
        assert "latency" in tags

    @patch('handlers.latency.get_latency_monitor')
    def test_execute_latency_opens_submenu(self, mock_monitor):
        from handlers.latency import LatencyHandler
        ctx = _make_context()
        ctx.dialog._menu_return = "back"  # Immediately exit submenu
        h = LatencyHandler()
        h.set_context(ctx)
        h.execute("latency")  # Should not raise


# ---------------------------------------------------------------------------
# ClassifierHandler tests
# ---------------------------------------------------------------------------

class TestClassifierHandler:

    def test_menu_section(self):
        from handlers.classifier import ClassifierHandler
        h = ClassifierHandler()
        assert h.menu_section == "mesh_networks"
        assert h.handler_id == "classifier"

    def test_menu_items_tag(self):
        from handlers.classifier import ClassifierHandler
        h = ClassifierHandler()
        tags = [t for t, _, _ in h.menu_items()]
        assert "traffic" in tags

    def test_execute_traffic_opens_submenu(self):
        from handlers.classifier import ClassifierHandler
        ctx = _make_context()
        ctx.dialog._menu_return = "back"
        h = ClassifierHandler()
        h.set_context(ctx)
        h.execute("traffic")


# ---------------------------------------------------------------------------
# AmateurRadioHandler tests
# ---------------------------------------------------------------------------

class TestAmateurRadioHandler:

    def test_menu_section(self):
        from handlers.amateur_radio import AmateurRadioHandler
        h = AmateurRadioHandler()
        assert h.menu_section == "mesh_networks"
        assert h.handler_id == "amateur_radio"

    def test_menu_items_tag(self):
        from handlers.amateur_radio import AmateurRadioHandler
        h = AmateurRadioHandler()
        tags = [t for t, _, _ in h.menu_items()]
        assert "ham" in tags

    def test_execute_ham_opens_submenu(self):
        from handlers.amateur_radio import AmateurRadioHandler
        ctx = _make_context()
        ctx.dialog._menu_return = "back"
        h = AmateurRadioHandler()
        h.set_context(ctx)
        h.execute("ham")


# ---------------------------------------------------------------------------
# AnalyticsHandler tests
# ---------------------------------------------------------------------------

class TestAnalyticsHandler:

    def test_menu_section(self):
        from handlers.analytics import AnalyticsHandler
        h = AnalyticsHandler()
        assert h.menu_section == "dashboard"
        assert h.handler_id == "analytics"

    def test_menu_items_tag(self):
        from handlers.analytics import AnalyticsHandler
        h = AnalyticsHandler()
        tags = [t for t, _, _ in h.menu_items()]
        assert "analytics" in tags

    def test_execute_analytics_opens_submenu(self):
        from handlers.analytics import AnalyticsHandler
        ctx = _make_context()
        ctx.dialog._menu_return = "back"
        h = AnalyticsHandler()
        h.set_context(ctx)
        h.execute("analytics")


# ---------------------------------------------------------------------------
# RFToolsHandler tests
# ---------------------------------------------------------------------------

class TestRFToolsHandler:

    def test_menu_section(self):
        from handlers.rf_tools import RFToolsHandler
        h = RFToolsHandler()
        assert h.menu_section == "rf_sdr"
        assert h.handler_id == "rf_tools"

    def test_menu_items_tags(self):
        from handlers.rf_tools import RFToolsHandler
        h = RFToolsHandler()
        tags = [t for t, _, _ in h.menu_items()]
        assert "link" in tags
        assert "freq" in tags
        assert "antenna" in tags

    def test_execute_link_opens_submenu(self):
        from handlers.rf_tools import RFToolsHandler
        ctx = _make_context()
        ctx.dialog._menu_return = "back"
        h = RFToolsHandler()
        h.set_context(ctx)
        h.execute("link")  # Opens RF tools submenu, exits via "back"


# ---------------------------------------------------------------------------
# Integration: registry dispatch reaches converted handlers
# ---------------------------------------------------------------------------

class TestRegistryDispatchIntegration:
    """Verify that registry dispatch reaches the correct handlers."""

    def _make_registry(self):
        from handlers import get_all_handlers
        ctx = _make_context()
        ctx.dialog._menu_return = "back"  # All submenus exit immediately
        registry = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            registry.register(cls())
        return registry

    def test_dashboard_latency_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("dashboard", "latency") is True

    def test_dashboard_analytics_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("dashboard", "analytics") is True

    def test_mesh_networks_traffic_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("mesh_networks", "traffic") is True

    def test_mesh_networks_ham_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("mesh_networks", "ham") is True

    def test_rf_sdr_link_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("rf_sdr", "link") is True

    def test_rf_sdr_freq_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("rf_sdr", "freq") is True

    def test_rf_sdr_antenna_dispatch(self):
        registry = self._make_registry()
        assert registry.dispatch("rf_sdr", "antenna") is True

    def test_unknown_tag_falls_through(self):
        registry = self._make_registry()
        # "nonexistent" is not registered anywhere — should fall through
        assert registry.dispatch("dashboard", "nonexistent") is False
