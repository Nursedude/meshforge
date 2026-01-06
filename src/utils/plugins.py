"""
MeshForge Plugin System.

Extensible architecture for adding new functionality:
- Panel Plugins: Add new UI panels (MeshCore, MQTT, etc.)
- Integration Plugins: Connect to external services
- Tool Plugins: Add RF tools, calculators
- Protocol Plugins: Support different mesh protocols

Usage:
    from utils.plugins import PluginManager, PanelPlugin, PluginType

    class MyPlugin(PanelPlugin):
        @staticmethod
        def get_metadata():
            return PluginMetadata(
                name="my-plugin",
                version="1.0.0",
                description="My custom plugin",
                author="Your Name",
                plugin_type=PluginType.PANEL,
            )

        def activate(self):
            print("Plugin activated!")

        def deactivate(self):
            print("Plugin deactivated!")

        def create_panel(self):
            # Return GTK widget
            pass

    # Register with manager
    manager = PluginManager()
    manager.register(MyPlugin)
    manager.activate("my-plugin")
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Type, Any
import importlib.util
import logging
import os

logger = logging.getLogger(__name__)

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class PluginType(Enum):
    """Types of plugins supported by MeshForge."""
    PANEL = "panel"           # Adds a new UI panel/tab
    INTEGRATION = "integration"  # Connects to external services (MQTT, HA, etc.)
    TOOL = "tool"             # Adds tools to Tools panel
    PROTOCOL = "protocol"     # Adds mesh protocol support (MeshCore, etc.)


@dataclass
class PluginMetadata:
    """Metadata describing a plugin."""
    name: str
    version: str
    description: str
    author: str
    plugin_type: PluginType
    dependencies: List[str] = field(default_factory=list)
    homepage: Optional[str] = None
    license: str = "GPL-3.0"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginMetadata':
        """Create metadata from dictionary (e.g., from plugin.json)."""
        plugin_type_str = data.get("type", "tool")
        try:
            plugin_type = PluginType(plugin_type_str)
        except ValueError:
            plugin_type = PluginType.TOOL

        return cls(
            name=data.get("name", "unknown"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            author=data.get("author", "unknown"),
            plugin_type=plugin_type,
            dependencies=data.get("dependencies", []),
            homepage=data.get("homepage"),
            license=data.get("license", "GPL-3.0"),
        )


class BasePlugin(ABC):
    """Base class for all MeshForge plugins."""

    @staticmethod
    @abstractmethod
    def get_metadata() -> PluginMetadata:
        """Return plugin metadata."""
        pass

    @abstractmethod
    def activate(self) -> None:
        """Called when plugin is activated."""
        pass

    @abstractmethod
    def deactivate(self) -> None:
        """Called when plugin is deactivated."""
        pass

    def on_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming mesh message (optional override)."""
        pass

    def on_node_update(self, node: Dict[str, Any]) -> None:
        """Handle node update (optional override)."""
        pass


class PanelPlugin(BasePlugin):
    """Plugin that adds a new UI panel."""

    @abstractmethod
    def create_panel(self) -> Any:
        """Create and return the GTK panel widget."""
        pass

    @abstractmethod
    def get_panel_title(self) -> str:
        """Return the panel tab title."""
        pass

    @abstractmethod
    def get_panel_icon(self) -> str:
        """Return the panel icon name (e.g., 'network-wireless-symbolic')."""
        pass


class IntegrationPlugin(BasePlugin):
    """Plugin for external service integration (MQTT, Home Assistant, etc.)."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the external service. Returns True on success."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the external service."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if currently connected."""
        pass

    def send(self, data: Dict[str, Any]) -> bool:
        """Send data to the external service. Returns True on success."""
        return False

    def receive(self) -> Optional[Dict[str, Any]]:
        """Receive data from the external service."""
        return None


class ToolPlugin(BasePlugin):
    """Plugin that adds a tool to the Tools panel."""

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""
        pass

    @abstractmethod
    def get_tool_name(self) -> str:
        """Return the display name for the tool."""
        pass

    def get_tool_description(self) -> str:
        """Return a description of what the tool does."""
        return self.get_metadata().description

    def get_parameters(self) -> List[Dict[str, Any]]:
        """Return list of parameters the tool accepts.

        Each parameter dict has: name, type, description, default, required
        """
        return []


class ProtocolPlugin(BasePlugin):
    """Plugin for supporting additional mesh protocols (MeshCore, etc.)."""

    @abstractmethod
    def get_protocol_name(self) -> str:
        """Return the protocol name (e.g., 'MeshCore', 'RNS')."""
        pass

    @abstractmethod
    def connect_device(self, **kwargs) -> bool:
        """Connect to a device using this protocol."""
        pass

    @abstractmethod
    def send_message(self, destination: str, message: str) -> bool:
        """Send a message using this protocol."""
        pass

    @abstractmethod
    def get_nodes(self) -> List[Dict[str, Any]]:
        """Get list of nodes visible to this protocol."""
        pass


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.

    Plugins are discovered from:
    1. Built-in plugins (src/plugins/)
    2. User plugins (~/.config/meshforge/plugins/)
    3. System plugins (/usr/share/meshforge/plugins/)
    """

    def __init__(self, plugins_dir: Optional[Path] = None):
        self.plugins: Dict[str, Type[BasePlugin]] = {}
        self.active_plugins: Set[str] = set()
        self.plugin_instances: Dict[str, BasePlugin] = {}

        # Default plugins directory
        if plugins_dir:
            self.plugins_dir = plugins_dir
        else:
            # Look for plugins in src/plugins/
            self.plugins_dir = Path(__file__).parent.parent / "plugins"

        # User plugins directory
        self.user_plugins_dir = get_real_user_home() / ".config" / "meshforge" / "plugins"

    def discover(self) -> List[str]:
        """Discover plugins in configured directories."""
        discovered = []

        for plugins_dir in [self.plugins_dir, self.user_plugins_dir]:
            if not plugins_dir.exists():
                continue

            for plugin_path in plugins_dir.glob("*/plugin.py"):
                try:
                    name = plugin_path.parent.name
                    self._load_plugin_from_path(plugin_path)
                    discovered.append(name)
                    logger.info(f"Discovered plugin: {name}")
                except Exception as e:
                    logger.error(f"Failed to load plugin from {plugin_path}: {e}")

        return discovered

    def _load_plugin_from_path(self, path: Path) -> None:
        """Load a plugin from a file path."""
        spec = importlib.util.spec_from_file_location("plugin", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Look for Plugin class in module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                    issubclass(attr, BasePlugin) and
                    attr is not BasePlugin and
                    not attr.__name__.startswith('_')):
                    self.register(attr)

    def register(self, plugin_class: Type[BasePlugin]) -> None:
        """Register a plugin class."""
        metadata = plugin_class.get_metadata()
        self.plugins[metadata.name] = plugin_class
        logger.info(f"Registered plugin: {metadata.name} v{metadata.version}")

    def unregister(self, name: str) -> bool:
        """Unregister a plugin by name."""
        if name in self.plugins:
            if name in self.active_plugins:
                self.deactivate(name)
            del self.plugins[name]
            return True
        return False

    def activate(self, name: str) -> bool:
        """Activate a registered plugin."""
        if name not in self.plugins:
            logger.error(f"Plugin not found: {name}")
            return False

        if name in self.active_plugins:
            logger.warning(f"Plugin already active: {name}")
            return True

        try:
            plugin_class = self.plugins[name]
            instance = plugin_class()
            instance.activate()
            self.plugin_instances[name] = instance
            self.active_plugins.add(name)
            logger.info(f"Activated plugin: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to activate plugin {name}: {e}")
            return False

    def deactivate(self, name: str) -> bool:
        """Deactivate an active plugin."""
        if name not in self.active_plugins:
            return False

        try:
            instance = self.plugin_instances.get(name)
            if instance:
                instance.deactivate()
                del self.plugin_instances[name]
            self.active_plugins.remove(name)
            logger.info(f"Deactivated plugin: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to deactivate plugin {name}: {e}")
            return False

    def get_instance(self, name: str) -> Optional[BasePlugin]:
        """Get the active instance of a plugin."""
        return self.plugin_instances.get(name)

    def list_all(self) -> List[PluginMetadata]:
        """List metadata for all registered plugins."""
        return [cls.get_metadata() for cls in self.plugins.values()]

    def list_by_type(self, plugin_type: PluginType) -> List[PluginMetadata]:
        """List plugins filtered by type."""
        return [
            cls.get_metadata()
            for cls in self.plugins.values()
            if cls.get_metadata().plugin_type == plugin_type
        ]

    def list_active(self) -> List[str]:
        """List names of active plugins."""
        return list(self.active_plugins)

    def get_panel_plugins(self) -> List[PanelPlugin]:
        """Get all active panel plugins."""
        panels = []
        for name in self.active_plugins:
            instance = self.plugin_instances.get(name)
            if isinstance(instance, PanelPlugin):
                panels.append(instance)
        return panels

    def get_integration_plugins(self) -> List[IntegrationPlugin]:
        """Get all active integration plugins."""
        integrations = []
        for name in self.active_plugins:
            instance = self.plugin_instances.get(name)
            if isinstance(instance, IntegrationPlugin):
                integrations.append(instance)
        return integrations

    def broadcast_message(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all active plugins."""
        for instance in self.plugin_instances.values():
            try:
                instance.on_message(message)
            except Exception as e:
                logger.error(f"Plugin message handler error: {e}")

    def broadcast_node_update(self, node: Dict[str, Any]) -> None:
        """Broadcast a node update to all active plugins."""
        for instance in self.plugin_instances.values():
            try:
                instance.on_node_update(node)
            except Exception as e:
                logger.error(f"Plugin node update handler error: {e}")


# Global plugin manager instance
_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance."""
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
