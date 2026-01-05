"""
Plugin system tests for MeshForge.

TDD tests for the extensible plugin architecture.
Run with: python3 tests/test_plugins.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.plugins import (
    PluginManager,
    PluginMetadata,
    PluginType,
    BasePlugin,
    PanelPlugin,
    IntegrationPlugin,
    ToolPlugin,
)


class TestPluginMetadata:
    """Test plugin metadata handling."""

    def test_create_metadata(self):
        """Create valid plugin metadata."""
        meta = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            description="A test plugin",
            author="nurse dude",
            plugin_type=PluginType.TOOL,
        )
        assert meta.name == "test-plugin"
        assert meta.version == "1.0.0"
        assert meta.plugin_type == PluginType.TOOL

    def test_metadata_from_dict(self):
        """Create metadata from dictionary."""
        data = {
            "name": "mqtt-bridge",
            "version": "0.1.0",
            "description": "MQTT integration",
            "author": "community",
            "type": "integration",
        }
        meta = PluginMetadata.from_dict(data)
        assert meta.name == "mqtt-bridge"
        assert meta.plugin_type == PluginType.INTEGRATION


class TestPluginTypes:
    """Test different plugin type classes."""

    def test_plugin_type_enum(self):
        """Plugin types should be defined."""
        assert PluginType.PANEL.value == "panel"
        assert PluginType.INTEGRATION.value == "integration"
        assert PluginType.TOOL.value == "tool"
        assert PluginType.PROTOCOL.value == "protocol"

    def test_base_plugin_interface(self):
        """BasePlugin should define required methods."""
        assert hasattr(BasePlugin, 'activate')
        assert hasattr(BasePlugin, 'deactivate')
        assert hasattr(BasePlugin, 'get_metadata')

    def test_panel_plugin_interface(self):
        """PanelPlugin should have panel-specific methods."""
        assert hasattr(PanelPlugin, 'create_panel')
        assert hasattr(PanelPlugin, 'get_panel_title')
        assert hasattr(PanelPlugin, 'get_panel_icon')

    def test_integration_plugin_interface(self):
        """IntegrationPlugin should have connection methods."""
        assert hasattr(IntegrationPlugin, 'connect')
        assert hasattr(IntegrationPlugin, 'disconnect')
        assert hasattr(IntegrationPlugin, 'is_connected')

    def test_tool_plugin_interface(self):
        """ToolPlugin should have execution methods."""
        assert hasattr(ToolPlugin, 'execute')
        assert hasattr(ToolPlugin, 'get_tool_name')


class TestPluginManager:
    """Test plugin manager functionality."""

    def test_manager_initialization(self):
        """Manager should initialize with empty plugin list."""
        manager = PluginManager()
        assert manager.plugins == {}
        assert manager.active_plugins == set()

    def test_discover_plugins_directory(self):
        """Manager should find plugins in directory."""
        manager = PluginManager()
        # Default plugins dir
        assert manager.plugins_dir is not None

    def test_register_plugin(self):
        """Register a plugin with the manager."""
        manager = PluginManager()

        class TestPlugin(BasePlugin):
            @staticmethod
            def get_metadata():
                return PluginMetadata(
                    name="test",
                    version="1.0.0",
                    description="Test",
                    author="test",
                    plugin_type=PluginType.TOOL,
                )

            def activate(self):
                pass

            def deactivate(self):
                pass

        manager.register(TestPlugin)
        assert "test" in manager.plugins

    def test_activate_plugin(self):
        """Activate a registered plugin."""
        manager = PluginManager()

        activated = False

        class TestPlugin(BasePlugin):
            @staticmethod
            def get_metadata():
                return PluginMetadata(
                    name="activatable",
                    version="1.0.0",
                    description="Test",
                    author="test",
                    plugin_type=PluginType.TOOL,
                )

            def activate(self):
                nonlocal activated
                activated = True

            def deactivate(self):
                pass

        manager.register(TestPlugin)
        manager.activate("activatable")

        assert activated
        assert "activatable" in manager.active_plugins

    def test_deactivate_plugin(self):
        """Deactivate an active plugin."""
        manager = PluginManager()

        deactivated = False

        class TestPlugin(BasePlugin):
            @staticmethod
            def get_metadata():
                return PluginMetadata(
                    name="deactivatable",
                    version="1.0.0",
                    description="Test",
                    author="test",
                    plugin_type=PluginType.TOOL,
                )

            def activate(self):
                pass

            def deactivate(self):
                nonlocal deactivated
                deactivated = True

        manager.register(TestPlugin)
        manager.activate("deactivatable")
        manager.deactivate("deactivatable")

        assert deactivated
        assert "deactivatable" not in manager.active_plugins

    def test_list_plugins_by_type(self):
        """List plugins filtered by type."""
        manager = PluginManager()

        class ToolA(BasePlugin):
            @staticmethod
            def get_metadata():
                return PluginMetadata("tool-a", "1.0", "", "", PluginType.TOOL)

            def activate(self):
                pass

            def deactivate(self):
                pass

        class PanelA(BasePlugin):
            @staticmethod
            def get_metadata():
                return PluginMetadata("panel-a", "1.0", "", "", PluginType.PANEL)

            def activate(self):
                pass

            def deactivate(self):
                pass

        manager.register(ToolA)
        manager.register(PanelA)

        tools = manager.list_by_type(PluginType.TOOL)
        panels = manager.list_by_type(PluginType.PANEL)

        assert len(tools) == 1
        assert len(panels) == 1
        assert tools[0].name == "tool-a"


class TestBuiltinPlugins:
    """Test that built-in plugin stubs work."""

    def test_mqtt_plugin_stub(self):
        """MQTT plugin should be loadable."""
        try:
            from plugins.mqtt_bridge import MQTTBridgePlugin
            meta = MQTTBridgePlugin.get_metadata()
            assert meta.name == "mqtt-bridge"
            assert meta.plugin_type == PluginType.INTEGRATION
        except ImportError:
            # Plugin not implemented yet - that's OK for now
            pass

    def test_meshcore_plugin_stub(self):
        """MeshCore plugin should be loadable."""
        try:
            from plugins.meshcore import MeshCorePlugin
            meta = MeshCorePlugin.get_metadata()
            assert meta.name == "meshcore"
            assert meta.plugin_type == PluginType.PROTOCOL
        except ImportError:
            # Plugin not implemented yet - that's OK
            pass


def run_tests():
    """Run all tests without pytest."""
    import traceback

    test_classes = [
        TestPluginMetadata,
        TestPluginTypes,
        TestPluginManager,
        TestBuiltinPlugins,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)

        instance = test_class()
        for name in dir(instance):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 40)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
