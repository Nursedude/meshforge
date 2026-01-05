# MeshForge Plugin Development Guide

> Create plugins for MeshForge.io to extend functionality

---

## Quick Start

1. Create a new directory in `~/.config/meshforge/plugins/`
2. Add a `manifest.json` file
3. Create your `main.py` with a Plugin class
4. Restart MeshForge to discover your plugin

---

## Plugin Structure

```
my_plugin/
├── manifest.json    # Plugin metadata (required)
├── main.py          # Entry point (required)
├── assets/          # Icons, images (optional)
└── README.md        # Documentation (optional)
```

---

## manifest.json

```json
{
    "id": "com.example.my_plugin",
    "name": "My Plugin",
    "version": "1.0.0",
    "description": "What this plugin does",
    "author": "Your Name",
    "type": "panel",
    "entry_point": "main.py",
    "min_meshforge_version": "1.0.0",
    "homepage": "https://github.com/you/plugin",
    "license": "MIT",
    "icon": "extension-symbolic",
    "category": "Tools",
    "tags": ["utility", "example"],
    "dependencies": [],
    "permissions": [],
    "premium": false,
    "settings_schema": {
        "option_name": {
            "type": "string",
            "default": "value",
            "label": "Option Label"
        }
    }
}
```

### Required Fields

| Field | Description |
|-------|-------------|
| `id` | Unique identifier (reverse domain notation) |
| `name` | Human-readable name |
| `version` | Semantic version (x.y.z) |
| `description` | Brief description |
| `author` | Author name or organization |
| `type` | Plugin type (see below) |
| `entry_point` | Main Python file |
| `min_meshforge_version` | Minimum MeshForge version |

### Plugin Types

| Type | Description |
|------|-------------|
| `panel` | Adds a new UI panel to the sidebar |
| `tool` | Adds calculators or analysis tools |
| `integration` | Connects external services |
| `theme` | Customizes appearance |
| `extension` | Extends existing features |

---

## main.py Template

```python
"""
My Plugin for MeshForge.io
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from meshforge.core.plugin_base import Plugin, PluginContext


class MyPanel(Gtk.Box):
    """Custom panel widget"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        label = Gtk.Label(label="Hello from My Plugin!")
        self.append(label)


class MyPlugin(Plugin):
    """Plugin implementation"""

    def activate(self, context: PluginContext) -> None:
        """Called when plugin is enabled"""
        # Register a panel
        context.register_panel(
            panel_id="my_panel",
            panel_class=MyPanel,
            title="My Plugin",
            icon="extension-symbolic"
        )

        # Subscribe to events
        context.subscribe("node_discovered", self._on_node)

        # Show notification
        context.notify("My Plugin", "Plugin activated!")

    def deactivate(self) -> None:
        """Called when plugin is disabled"""
        pass

    def _on_node(self, data):
        """Handle node discovery event"""
        print(f"Node discovered: {data}")
```

---

## Plugin Context API

The `PluginContext` provides these methods:

### register_panel()
```python
context.register_panel(
    panel_id="unique_id",
    panel_class=MyGtkWidget,
    title="Sidebar Title",
    icon="icon-name-symbolic"
)
```

### register_tool()
```python
context.register_tool(
    tool_id="my_tool",
    tool_func=my_function,
    name="Tool Name",
    description="What it does"
)
```

### register_menu_item()
```python
context.register_menu_item(
    menu_path="tools/my_item",
    label="Menu Label",
    callback=my_callback,
    icon="icon-name"
)
```

### subscribe()
```python
context.subscribe("event_name", handler_function)
```

Available events:
- `node_discovered` - New mesh node found
- `message_received` - Message arrived
- `config_changed` - Configuration updated
- `service_status_changed` - Service started/stopped

### emit()
```python
context.emit("my_plugin.custom_event", data)
```

### notify()
```python
context.notify("Title", "Message", urgency="normal")
# urgency: "low", "normal", "critical"
```

### get_service()
```python
meshtastic = context.get_service("meshtastic")
config = context.get_service("config")
```

### get_plugin_data_dir()
```python
data_dir = context.get_plugin_data_dir("my_plugin_id")
# Returns: ~/.config/meshforge/plugins/my_plugin_id/
```

---

## Settings

Define settings in `manifest.json`:

```json
{
    "settings_schema": {
        "api_key": {
            "type": "string",
            "default": "",
            "label": "API Key",
            "description": "Your API key"
        },
        "refresh_interval": {
            "type": "number",
            "default": 60,
            "label": "Refresh Interval (seconds)"
        },
        "enabled_features": {
            "type": "choice",
            "choices": ["basic", "advanced", "all"],
            "default": "basic",
            "label": "Feature Set"
        },
        "auto_start": {
            "type": "boolean",
            "default": true,
            "label": "Start Automatically"
        }
    }
}
```

Access settings in your plugin:

```python
class MyPlugin(Plugin):
    def activate(self, context):
        settings = self.get_settings()
        api_key = settings.get("api_key", "")

    def update_config(self, new_key):
        self.update_settings({"api_key": new_key})
```

---

## Best Practices

### 1. Follow MeshForge Design Patterns
- Use GTK4 widgets
- Follow libadwaita styling
- Respect the edition feature flags

### 2. Handle Errors Gracefully
```python
try:
    result = risky_operation()
except Exception as e:
    context.notify("Error", str(e), urgency="critical")
    logger.exception("Operation failed")
```

### 3. Clean Up Resources
```python
def deactivate(self):
    if self._timer:
        GLib.source_remove(self._timer)
    if self._connection:
        self._connection.close()
```

### 4. Use Logging
```python
import logging
logger = logging.getLogger(__name__)

logger.debug("Debug info")
logger.info("Normal operation")
logger.warning("Potential issue")
logger.error("Error occurred")
```

### 5. Respect User Privacy
- Store data locally only
- Never transmit without consent
- Clear data on uninstall

---

## Example Plugins

See the `examples/` directory:

- `rf_calculator/` - RF calculation tools
- `band_plan/` - Frequency reference
- `link_budget/` - Link budget analysis

---

## Publishing

1. Test thoroughly on all editions
2. Include clear documentation
3. Submit to MeshForge Plugin Marketplace (coming soon)

---

## Support

- GitHub Issues: https://github.com/meshforge/plugins/issues
- Documentation: https://docs.meshforge.org/plugins
- Community: https://discord.gg/meshforge
