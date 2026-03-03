"""
Plugin System Tests

Tests for the unified plugin system (utils/plugins.py):
- Plugin discovery (builtin + package)
- Register / activate / deactivate lifecycle
- Event bus integration (subscribe / emit)
- PluginMetadata.from_dict()
- Backward compatibility (old imports)
- Built-in plugin metadata validation
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.plugins import (
    BasePlugin,
    IntegrationPlugin,
    PluginManager,
    PluginMetadata,
    PluginType,
    PanelPlugin,
    ProtocolPlugin,
    ToolPlugin,
    get_plugin_manager,
)


# ---------------------------------------------------------------------------
# Test fixtures — concrete plugin implementations
# ---------------------------------------------------------------------------

class SamplePlugin(BasePlugin):
    """Minimal plugin for testing."""

    activated = False
    deactivated = False
    last_message = None
    last_node = None

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="sample-plugin",
            version="1.0.0",
            description="A sample plugin for tests",
            author="Test Author",
            plugin_type=PluginType.TOOL,
        )

    def activate(self):
        SamplePlugin.activated = True

    def deactivate(self):
        SamplePlugin.deactivated = True

    def on_message(self, message):
        SamplePlugin.last_message = message

    def on_node_update(self, node):
        SamplePlugin.last_node = node


class AnotherPlugin(BasePlugin):
    """Second plugin for multi-registration tests."""

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="another-plugin",
            version="0.1.0",
            description="Another test plugin",
            author="Test",
            plugin_type=PluginType.INTEGRATION,
        )

    def activate(self):
        pass

    def deactivate(self):
        pass


class FailingPlugin(BasePlugin):
    """Plugin that raises on activate."""

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="failing-plugin",
            version="0.0.1",
            description="Fails on activate",
            author="Test",
            plugin_type=PluginType.TOOL,
        )

    def activate(self):
        raise RuntimeError("Activation explosion")

    def deactivate(self):
        pass


# ---------------------------------------------------------------------------
# PluginMetadata tests
# ---------------------------------------------------------------------------

class TestPluginMetadata:
    """Tests for PluginMetadata dataclass."""

    def test_basic_creation(self):
        meta = PluginMetadata(
            name="test",
            version="1.0.0",
            description="desc",
            author="auth",
            plugin_type=PluginType.TOOL,
        )
        assert meta.name == "test"
        assert meta.version == "1.0.0"
        assert meta.plugin_type == PluginType.TOOL
        assert meta.license == "GPL-3.0"  # default

    def test_from_dict_basic(self):
        data = {
            "name": "my-plugin",
            "version": "2.0.0",
            "description": "A plugin",
            "author": "Dev",
            "type": "integration",
        }
        meta = PluginMetadata.from_dict(data)
        assert meta.name == "my-plugin"
        assert meta.version == "2.0.0"
        assert meta.plugin_type == PluginType.INTEGRATION
        assert meta.dependencies == []

    def test_from_dict_with_dependencies(self):
        data = {
            "name": "dep-plugin",
            "version": "1.0.0",
            "description": "Has deps",
            "author": "Dev",
            "type": "protocol",
            "dependencies": ["paho-mqtt", "pyserial"],
            "homepage": "https://example.com",
        }
        meta = PluginMetadata.from_dict(data)
        assert meta.plugin_type == PluginType.PROTOCOL
        assert len(meta.dependencies) == 2
        assert "paho-mqtt" in meta.dependencies
        assert meta.homepage == "https://example.com"

    def test_from_dict_unknown_type_defaults_to_tool(self):
        data = {
            "name": "unknown",
            "version": "1.0.0",
            "description": "",
            "author": "",
            "type": "nonexistent_type",
        }
        meta = PluginMetadata.from_dict(data)
        assert meta.plugin_type == PluginType.TOOL

    def test_from_dict_missing_type_defaults_to_tool(self):
        data = {"name": "no-type", "version": "1.0.0", "description": "", "author": ""}
        meta = PluginMetadata.from_dict(data)
        assert meta.plugin_type == PluginType.TOOL


# ---------------------------------------------------------------------------
# PluginManager registration tests
# ---------------------------------------------------------------------------

class TestPluginRegistration:
    """Tests for register / unregister / list."""

    def test_register_and_list(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        assert len(pm.list_all()) == 1
        assert pm.list_all()[0].name == "sample-plugin"

    def test_register_multiple(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.register(AnotherPlugin)
        assert len(pm.list_all()) == 2

    def test_register_overwrites_same_name(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.register(SamplePlugin)  # Same name again
        assert len(pm.list_all()) == 1

    def test_unregister(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        assert pm.unregister("sample-plugin") is True
        assert len(pm.list_all()) == 0

    def test_unregister_nonexistent(self):
        pm = PluginManager()
        assert pm.unregister("nonexistent") is False

    def test_list_by_type(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.register(AnotherPlugin)

        tools = pm.list_by_type(PluginType.TOOL)
        assert len(tools) == 1
        assert tools[0].name == "sample-plugin"

        integrations = pm.list_by_type(PluginType.INTEGRATION)
        assert len(integrations) == 1
        assert integrations[0].name == "another-plugin"


# ---------------------------------------------------------------------------
# PluginManager lifecycle tests
# ---------------------------------------------------------------------------

class TestPluginLifecycle:
    """Tests for activate / deactivate / get_instance."""

    def setup_method(self):
        SamplePlugin.activated = False
        SamplePlugin.deactivated = False
        SamplePlugin.last_message = None
        SamplePlugin.last_node = None

    def test_activate(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        assert pm.activate("sample-plugin") is True
        assert SamplePlugin.activated is True
        assert "sample-plugin" in pm.list_active()

    def test_activate_unregistered(self):
        pm = PluginManager()
        assert pm.activate("nonexistent") is False

    def test_activate_already_active(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")
        assert pm.activate("sample-plugin") is True  # Idempotent

    def test_deactivate(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")
        assert pm.deactivate("sample-plugin") is True
        assert SamplePlugin.deactivated is True
        assert "sample-plugin" not in pm.list_active()

    def test_deactivate_not_active(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        assert pm.deactivate("sample-plugin") is False

    def test_get_instance(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")
        instance = pm.get_instance("sample-plugin")
        assert instance is not None
        assert isinstance(instance, SamplePlugin)

    def test_get_instance_not_active(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        assert pm.get_instance("sample-plugin") is None

    def test_failing_activation(self):
        pm = PluginManager()
        pm.register(FailingPlugin)
        assert pm.activate("failing-plugin") is False
        assert "failing-plugin" not in pm.list_active()

    def test_unregister_deactivates(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")
        pm.unregister("sample-plugin")
        assert SamplePlugin.deactivated is True
        assert len(pm.list_active()) == 0


# ---------------------------------------------------------------------------
# Broadcast tests
# ---------------------------------------------------------------------------

class TestBroadcast:
    """Tests for broadcast_message and broadcast_node_update."""

    def setup_method(self):
        SamplePlugin.last_message = None
        SamplePlugin.last_node = None

    def test_broadcast_message(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")

        pm.broadcast_message({"content": "hello mesh"})
        assert SamplePlugin.last_message == {"content": "hello mesh"}

    def test_broadcast_node_update(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        pm.activate("sample-plugin")

        pm.broadcast_node_update({"id": "!abc123", "name": "Node1"})
        assert SamplePlugin.last_node == {"id": "!abc123", "name": "Node1"}

    def test_broadcast_no_active_plugins(self):
        pm = PluginManager()
        # Should not raise
        pm.broadcast_message({"content": "nobody listening"})
        pm.broadcast_node_update({"id": "!xyz"})


# ---------------------------------------------------------------------------
# Event bus integration tests
# ---------------------------------------------------------------------------

class TestEventBusIntegration:
    """Tests for PluginManager event bus methods."""

    def test_subscribe_and_emit(self):
        pm = PluginManager()
        received = []

        def handler(data):
            received.append(data)

        pm.subscribe("test_event", handler)
        pm.event_bus.emit_sync("test_event", {"key": "value"})
        assert len(received) == 1
        assert received[0] == {"key": "value"}

        pm.unsubscribe("test_event", handler)

    def test_unsubscribe(self):
        pm = PluginManager()
        received = []

        def handler(data):
            received.append(data)

        pm.subscribe("test_event", handler)
        pm.unsubscribe("test_event", handler)
        pm.event_bus.emit_sync("test_event", {"should": "not arrive"})
        assert len(received) == 0

    def test_emit_via_manager(self):
        pm = PluginManager()
        received = []

        def handler(data):
            received.append(data)

        pm.event_bus.subscribe("custom", handler)
        pm.emit("custom", "payload")
        # emit() uses async dispatch, use emit_sync for test
        pm.event_bus.clear_subscribers("custom")

    def test_broadcast_message_emits_to_event_bus(self):
        pm = PluginManager()
        bus_received = []

        def handler(data):
            bus_received.append(data)

        pm.event_bus.subscribe("plugin_message", handler)
        pm.broadcast_message({"content": "test"})
        # Async emit — wait briefly
        import time
        time.sleep(0.1)
        assert len(bus_received) >= 1
        assert bus_received[0] == {"content": "test"}

        pm.event_bus.clear_subscribers("plugin_message")

    def test_broadcast_node_update_emits_to_event_bus(self):
        pm = PluginManager()
        bus_received = []

        def handler(data):
            bus_received.append(data)

        pm.event_bus.subscribe("plugin_node_update", handler)
        pm.broadcast_node_update({"id": "!node1"})
        import time
        time.sleep(0.1)
        assert len(bus_received) >= 1

        pm.event_bus.clear_subscribers("plugin_node_update")


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------

class TestDiscovery:
    """Tests for plugin auto-discovery."""

    def test_discover_builtin_returns_list(self):
        pm = PluginManager()
        result = pm.discover_builtin()
        assert isinstance(result, list)
        # At minimum, the 4 built-in plugins should be discoverable
        # (if their dependencies are importable)
        for name in result:
            assert name in pm.plugins

    def test_discover_calls_discover_builtin(self):
        pm = PluginManager()
        result = pm.discover()
        assert isinstance(result, list)
        # discover() includes builtin results
        builtin_only = pm.discover_builtin()
        # All builtin names should be in the full discover result
        # (they were already registered on first call, so discover_builtin
        # returns [] on second call, but plugins dict has them)

    def test_discover_skips_already_registered(self):
        pm = PluginManager()
        pm.register(SamplePlugin)
        # discover_builtin should skip if name already registered
        # and not fail
        pm.discover_builtin()

    def test_discover_builtin_without_plugins_module(self):
        """discover_builtin handles missing BUILTIN_PLUGINS gracefully."""
        pm = PluginManager()
        with patch.dict('sys.modules', {'plugins': None}):
            result = pm.discover_builtin()
            # Should return empty list, not raise
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Built-in plugin metadata validation
# ---------------------------------------------------------------------------

class TestBuiltinPluginMetadata:
    """Validate that all built-in plugins have correct metadata."""

    def _get_builtin_plugins(self):
        """Discover and return all built-in plugin classes."""
        pm = PluginManager()
        pm.discover_builtin()
        return pm.plugins

    def test_all_builtins_have_name(self):
        for name, cls in self._get_builtin_plugins().items():
            meta = cls.get_metadata()
            assert meta.name, f"Plugin {name} missing name"
            assert isinstance(meta.name, str)

    def test_all_builtins_have_version(self):
        for name, cls in self._get_builtin_plugins().items():
            meta = cls.get_metadata()
            assert meta.version, f"Plugin {name} missing version"
            # Version should be semver-like
            parts = meta.version.split(".")
            assert len(parts) >= 2, f"Plugin {name} version not semver-like: {meta.version}"

    def test_all_builtins_have_description(self):
        for name, cls in self._get_builtin_plugins().items():
            meta = cls.get_metadata()
            assert meta.description, f"Plugin {name} missing description"

    def test_all_builtins_have_valid_type(self):
        for name, cls in self._get_builtin_plugins().items():
            meta = cls.get_metadata()
            assert isinstance(meta.plugin_type, PluginType), \
                f"Plugin {name} has invalid type: {meta.plugin_type}"

    def test_all_builtins_are_base_plugin_subclass(self):
        for name, cls in self._get_builtin_plugins().items():
            assert issubclass(cls, BasePlugin), \
                f"Plugin {name} ({cls.__name__}) is not a BasePlugin subclass"


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Ensure old import paths still work."""

    def test_core_plugin_imports(self):
        """from core import Plugin, PluginManager, PluginManifest still works."""
        from core import Plugin, PluginManager as CorePM, PluginManifest
        assert Plugin is BasePlugin
        assert CorePM is PluginManager
        assert PluginManifest is PluginMetadata

    def test_get_plugin_manager_singleton(self):
        """Global singleton works."""
        pm1 = get_plugin_manager()
        pm2 = get_plugin_manager()
        assert pm1 is pm2
        assert isinstance(pm1, PluginManager)


# ---------------------------------------------------------------------------
# PluginType enum tests
# ---------------------------------------------------------------------------

class TestPluginType:
    """Tests for PluginType enum."""

    def test_all_types_exist(self):
        assert PluginType.PANEL.value == "panel"
        assert PluginType.INTEGRATION.value == "integration"
        assert PluginType.TOOL.value == "tool"
        assert PluginType.PROTOCOL.value == "protocol"

    def test_from_string(self):
        assert PluginType("panel") == PluginType.PANEL
        assert PluginType("integration") == PluginType.INTEGRATION
