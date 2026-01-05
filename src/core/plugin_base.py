"""
MeshForge Plugin Architecture

Provides extensibility for MeshForge.io through a plugin system.

Plugin Types:
- Panel Plugins: Add new UI panels
- Tool Plugins: Add calculation/analysis tools
- Integration Plugins: Connect external services
- Theme Plugins: Customize appearance
"""

import json
import logging
import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class PluginType(Enum):
    """Types of plugins supported"""
    PANEL = "panel"          # Adds a UI panel
    TOOL = "tool"            # Adds tools/calculators
    INTEGRATION = "integration"  # Connects external services
    THEME = "theme"          # Customizes appearance
    EXTENSION = "extension"  # Extends existing features


class PluginState(Enum):
    """Plugin lifecycle states"""
    DISCOVERED = "discovered"
    LOADED = "loaded"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    ERROR = "error"


@dataclass
class PluginManifest:
    """
    Plugin metadata from manifest.json

    Required fields in manifest.json:
    {
        "id": "com.example.myplugin",
        "name": "My Plugin",
        "version": "1.0.0",
        "description": "Does something useful",
        "author": "Developer Name",
        "type": "panel",
        "entry_point": "main.py",
        "min_meshforge_version": "1.0.0"
    }
    """
    id: str
    name: str
    version: str
    description: str
    author: str
    plugin_type: PluginType
    entry_point: str
    min_meshforge_version: str = "1.0.0"

    # Optional fields
    homepage: Optional[str] = None
    license: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    settings_schema: Optional[Dict] = None
    premium: bool = False

    @classmethod
    def from_dict(cls, data: Dict) -> 'PluginManifest':
        """Create manifest from dictionary"""
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            description=data["description"],
            author=data["author"],
            plugin_type=PluginType(data["type"]),
            entry_point=data["entry_point"],
            min_meshforge_version=data.get("min_meshforge_version", "1.0.0"),
            homepage=data.get("homepage"),
            license=data.get("license"),
            icon=data.get("icon"),
            category=data.get("category"),
            tags=data.get("tags", []),
            dependencies=data.get("dependencies", []),
            permissions=data.get("permissions", []),
            settings_schema=data.get("settings_schema"),
            premium=data.get("premium", False),
        )

    @classmethod
    def from_file(cls, manifest_path: Path) -> 'PluginManifest':
        """Load manifest from JSON file"""
        data = json.loads(manifest_path.read_text())
        return cls.from_dict(data)


class Plugin(ABC):
    """
    Base class for all MeshForge plugins.

    To create a plugin:
    1. Create a directory with manifest.json
    2. Create main.py with a class extending Plugin
    3. Implement required methods

    Example:
        class MyPlugin(Plugin):
            def activate(self, context):
                # Called when plugin is enabled
                self.register_panel("my_panel", MyPanelWidget)

            def deactivate(self):
                # Called when plugin is disabled
                pass
    """

    def __init__(self, manifest: PluginManifest, plugin_dir: Path):
        self.manifest = manifest
        self.plugin_dir = plugin_dir
        self.state = PluginState.LOADED
        self._context: Optional['PluginContext'] = None
        self._settings: Dict[str, Any] = {}

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @abstractmethod
    def activate(self, context: 'PluginContext') -> None:
        """
        Called when the plugin is activated.

        Use this to:
        - Register UI components
        - Set up event handlers
        - Initialize resources

        Args:
            context: Plugin context for interacting with MeshForge
        """
        pass

    @abstractmethod
    def deactivate(self) -> None:
        """
        Called when the plugin is deactivated.

        Use this to:
        - Clean up resources
        - Remove event handlers
        - Save state
        """
        pass

    def get_settings(self) -> Dict[str, Any]:
        """Get plugin settings"""
        return self._settings.copy()

    def update_settings(self, settings: Dict[str, Any]) -> None:
        """Update plugin settings"""
        self._settings.update(settings)
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist settings to disk"""
        if self._context:
            settings_dir = self._context.get_plugin_data_dir(self.id)
            settings_file = settings_dir / "settings.json"
            settings_file.write_text(json.dumps(self._settings, indent=2))

    def _load_settings(self) -> None:
        """Load settings from disk"""
        if self._context:
            settings_dir = self._context.get_plugin_data_dir(self.id)
            settings_file = settings_dir / "settings.json"
            if settings_file.exists():
                self._settings = json.loads(settings_file.read_text())


@dataclass
class PluginContext:
    """
    Context provided to plugins for interacting with MeshForge.

    This is the plugin's interface to the host application.
    """
    app_version: str
    data_dir: Path
    config_dir: Path

    # Callbacks for plugin actions
    _register_panel: Optional[Callable] = None
    _register_tool: Optional[Callable] = None
    _register_menu_item: Optional[Callable] = None
    _subscribe_event: Optional[Callable] = None
    _emit_event: Optional[Callable] = None
    _show_notification: Optional[Callable] = None
    _get_service: Optional[Callable] = None

    def register_panel(self, panel_id: str, panel_class: type,
                       title: str, icon: str = "extension-symbolic") -> None:
        """
        Register a new UI panel.

        Args:
            panel_id: Unique identifier for the panel
            panel_class: GTK widget class for the panel
            title: Display title in sidebar
            icon: Icon name for sidebar
        """
        if self._register_panel:
            self._register_panel(panel_id, panel_class, title, icon)

    def register_tool(self, tool_id: str, tool_func: Callable,
                      name: str, description: str) -> None:
        """
        Register a tool/calculator.

        Args:
            tool_id: Unique identifier
            tool_func: Function implementing the tool
            name: Display name
            description: Tool description
        """
        if self._register_tool:
            self._register_tool(tool_id, tool_func, name, description)

    def register_menu_item(self, menu_path: str, label: str,
                           callback: Callable, icon: Optional[str] = None) -> None:
        """
        Add an item to application menus.

        Args:
            menu_path: Path like "tools/my_tool"
            label: Menu item label
            callback: Function to call when clicked
            icon: Optional icon name
        """
        if self._register_menu_item:
            self._register_menu_item(menu_path, label, callback, icon)

    def subscribe(self, event_name: str, handler: Callable) -> None:
        """
        Subscribe to application events.

        Available events:
        - "node_discovered": New node found
        - "message_received": Message arrived
        - "config_changed": Configuration updated
        - "service_status_changed": Service started/stopped

        Args:
            event_name: Event to subscribe to
            handler: Function to call with event data
        """
        if self._subscribe_event:
            self._subscribe_event(event_name, handler)

    def emit(self, event_name: str, data: Any = None) -> None:
        """
        Emit an event for other plugins/app.

        Args:
            event_name: Event name (prefix with plugin id)
            data: Event payload
        """
        if self._emit_event:
            self._emit_event(event_name, data)

    def notify(self, title: str, message: str,
               urgency: str = "normal") -> None:
        """
        Show a notification to the user.

        Args:
            title: Notification title
            message: Notification body
            urgency: "low", "normal", or "critical"
        """
        if self._show_notification:
            self._show_notification(title, message, urgency)

    def get_service(self, service_name: str) -> Optional[Any]:
        """
        Get a MeshForge service.

        Available services:
        - "meshtastic": Meshtastic connection
        - "config": Configuration manager
        - "map": Map service
        - "rns": Reticulum service (if available)

        Args:
            service_name: Service to retrieve

        Returns:
            Service instance or None
        """
        if self._get_service:
            return self._get_service(service_name)
        return None

    def get_plugin_data_dir(self, plugin_id: str) -> Path:
        """Get data directory for a plugin"""
        plugin_dir = self.data_dir / "plugins" / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        return plugin_dir


class PluginManager:
    """
    Manages plugin discovery, loading, and lifecycle.

    Usage:
        manager = PluginManager()
        manager.discover_plugins()
        manager.activate_plugin("com.example.myplugin")
    """

    def __init__(self, plugins_dir: Optional[Path] = None):
        if plugins_dir is None:
            plugins_dir = Path.home() / ".config" / "meshforge" / "plugins"
        self.plugins_dir = plugins_dir
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        self._plugins: Dict[str, Plugin] = {}
        self._manifests: Dict[str, PluginManifest] = {}
        self._context: Optional[PluginContext] = None

        # Event system
        self._event_handlers: Dict[str, List[Callable]] = {}

        # Registered components
        self._panels: Dict[str, Dict] = {}
        self._tools: Dict[str, Dict] = {}
        self._menu_items: List[Dict] = []

    def set_context(self, context: PluginContext) -> None:
        """Set the plugin context"""
        self._context = context

        # Wire up context callbacks
        context._register_panel = self._register_panel
        context._register_tool = self._register_tool
        context._register_menu_item = self._register_menu_item
        context._subscribe_event = self._subscribe_event
        context._emit_event = self._emit_event

    def discover_plugins(self) -> List[PluginManifest]:
        """
        Discover available plugins.

        Returns:
            List of discovered plugin manifests
        """
        discovered = []

        for plugin_dir in self.plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                logger.warning(f"No manifest.json in {plugin_dir}")
                continue

            try:
                manifest = PluginManifest.from_file(manifest_path)
                self._manifests[manifest.id] = manifest
                discovered.append(manifest)
                logger.info(f"Discovered plugin: {manifest.name} ({manifest.id})")
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Invalid manifest in {plugin_dir}: {e}")

        return discovered

    def load_plugin(self, plugin_id: str) -> Optional[Plugin]:
        """
        Load a plugin by ID.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Loaded plugin instance or None
        """
        if plugin_id in self._plugins:
            return self._plugins[plugin_id]

        manifest = self._manifests.get(plugin_id)
        if not manifest:
            logger.error(f"Plugin not found: {plugin_id}")
            return None

        plugin_dir = self.plugins_dir / plugin_id.replace(".", "_")
        entry_point = plugin_dir / manifest.entry_point

        if not entry_point.exists():
            logger.error(f"Entry point not found: {entry_point}")
            return None

        try:
            # Load the plugin module
            spec = importlib.util.spec_from_file_location(
                f"meshforge_plugin_{plugin_id}",
                entry_point
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the Plugin subclass
            plugin_class = None
            for name in dir(module):
                obj = getattr(module, name)
                if (isinstance(obj, type) and
                    issubclass(obj, Plugin) and
                    obj is not Plugin):
                    plugin_class = obj
                    break

            if not plugin_class:
                logger.error(f"No Plugin subclass found in {entry_point}")
                return None

            # Instantiate
            plugin = plugin_class(manifest, plugin_dir)
            self._plugins[plugin_id] = plugin
            logger.info(f"Loaded plugin: {manifest.name}")
            return plugin

        except Exception as e:
            logger.exception(f"Failed to load plugin {plugin_id}: {e}")
            return None

    def activate_plugin(self, plugin_id: str) -> bool:
        """
        Activate a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if activated successfully
        """
        plugin = self.load_plugin(plugin_id)
        if not plugin:
            return False

        if plugin.state == PluginState.ACTIVATED:
            return True

        if not self._context:
            logger.error("Plugin context not set")
            return False

        try:
            plugin._context = self._context
            plugin._load_settings()
            plugin.activate(self._context)
            plugin.state = PluginState.ACTIVATED
            logger.info(f"Activated plugin: {plugin.name}")
            return True
        except Exception as e:
            logger.exception(f"Failed to activate plugin {plugin_id}: {e}")
            plugin.state = PluginState.ERROR
            return False

    def deactivate_plugin(self, plugin_id: str) -> bool:
        """
        Deactivate a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if deactivated successfully
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return False

        if plugin.state != PluginState.ACTIVATED:
            return True

        try:
            plugin.deactivate()
            plugin.state = PluginState.DEACTIVATED
            logger.info(f"Deactivated plugin: {plugin.name}")
            return True
        except Exception as e:
            logger.exception(f"Failed to deactivate plugin {plugin_id}: {e}")
            return False

    def get_plugin(self, plugin_id: str) -> Optional[Plugin]:
        """Get a loaded plugin by ID"""
        return self._plugins.get(plugin_id)

    def get_active_plugins(self) -> List[Plugin]:
        """Get all active plugins"""
        return [p for p in self._plugins.values()
                if p.state == PluginState.ACTIVATED]

    def get_all_manifests(self) -> List[PluginManifest]:
        """Get all discovered plugin manifests"""
        return list(self._manifests.values())

    # Internal registration methods
    def _register_panel(self, panel_id: str, panel_class: type,
                        title: str, icon: str) -> None:
        """Register a panel from a plugin"""
        self._panels[panel_id] = {
            "class": panel_class,
            "title": title,
            "icon": icon,
        }
        logger.debug(f"Registered panel: {panel_id}")

    def _register_tool(self, tool_id: str, tool_func: Callable,
                       name: str, description: str) -> None:
        """Register a tool from a plugin"""
        self._tools[tool_id] = {
            "func": tool_func,
            "name": name,
            "description": description,
        }
        logger.debug(f"Registered tool: {tool_id}")

    def _register_menu_item(self, menu_path: str, label: str,
                            callback: Callable, icon: Optional[str]) -> None:
        """Register a menu item from a plugin"""
        self._menu_items.append({
            "path": menu_path,
            "label": label,
            "callback": callback,
            "icon": icon,
        })
        logger.debug(f"Registered menu item: {menu_path}/{label}")

    def _subscribe_event(self, event_name: str, handler: Callable) -> None:
        """Subscribe to an event"""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)

    def _emit_event(self, event_name: str, data: Any = None) -> None:
        """Emit an event to all subscribers"""
        handlers = self._event_handlers.get(event_name, [])
        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                logger.exception(f"Error in event handler for {event_name}: {e}")

    def get_registered_panels(self) -> Dict[str, Dict]:
        """Get all registered panels"""
        return self._panels.copy()

    def get_registered_tools(self) -> Dict[str, Dict]:
        """Get all registered tools"""
        return self._tools.copy()


# Example plugin template
EXAMPLE_PLUGIN_TEMPLATE = '''
"""
Example MeshForge Plugin

manifest.json:
{
    "id": "com.example.myplugin",
    "name": "My Plugin",
    "version": "1.0.0",
    "description": "An example plugin",
    "author": "Your Name",
    "type": "panel",
    "entry_point": "main.py",
    "min_meshforge_version": "1.0.0"
}
"""

from meshforge.core.plugin_base import Plugin, PluginContext
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


class MyPluginPanel(Gtk.Box):
    """Custom panel widget"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        label = Gtk.Label(label="Hello from My Plugin!")
        self.append(label)


class MyPlugin(Plugin):
    """Example plugin implementation"""

    def activate(self, context: PluginContext) -> None:
        """Called when plugin is activated"""
        # Register our panel
        context.register_panel(
            panel_id="my_plugin_panel",
            panel_class=MyPluginPanel,
            title="My Plugin",
            icon="extension-symbolic"
        )

        # Subscribe to events
        context.subscribe("node_discovered", self._on_node_discovered)

        # Show activation notification
        context.notify("My Plugin", "Plugin activated successfully!")

    def deactivate(self) -> None:
        """Called when plugin is deactivated"""
        pass

    def _on_node_discovered(self, node_data):
        """Handle node discovery event"""
        print(f"New node discovered: {node_data}")
'''


def create_plugin_template(plugin_dir: Path, plugin_id: str,
                           plugin_name: str, plugin_type: PluginType) -> None:
    """
    Create a new plugin from template.

    Args:
        plugin_dir: Directory to create plugin in
        plugin_id: Plugin identifier (e.g., "com.example.myplugin")
        plugin_name: Human-readable name
        plugin_type: Type of plugin
    """
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Create manifest
    manifest = {
        "id": plugin_id,
        "name": plugin_name,
        "version": "1.0.0",
        "description": f"A {plugin_type.value} plugin for MeshForge",
        "author": "Your Name",
        "type": plugin_type.value,
        "entry_point": "main.py",
        "min_meshforge_version": "1.0.0",
        "permissions": [],
        "tags": [],
    }

    manifest_path = plugin_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Create entry point
    main_path = plugin_dir / "main.py"
    main_path.write_text(EXAMPLE_PLUGIN_TEMPLATE)

    logger.info(f"Created plugin template at {plugin_dir}")
