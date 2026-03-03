"""
MeshForge Plugins

Built-in and community plugins for extending MeshForge functionality.

Available Plugins:
- mqtt_bridge: MQTT integration for Home Assistant, Node-RED
- meshcore: MeshCore protocol support
- meshing_around: Bot framework integration
- eas_alerts: Emergency Alert System (NOAA, FEMA, USGS)
- nanovna: NanoVNA antenna analyzer integration
- meshchat: MeshChat LXMF messaging integration
"""

from pathlib import Path

PLUGINS_DIR = Path(__file__).parent

# Built-in plugins: (name, module_path, plugin_class_name)
# Used by PluginManager.discover_builtin() for auto-discovery.
BUILTIN_PLUGINS = [
    ("meshcore", "plugins.meshcore", "MeshCorePlugin"),
    ("mqtt-bridge", "plugins.mqtt_bridge", "MQTTBridgePlugin"),
    ("meshing-around", "plugins.meshing_around", "MeshingAroundPlugin"),
    ("eas-alerts", "plugins.eas_alerts", "EASAlertsPlugin"),
]

__all__ = ['PLUGINS_DIR', 'BUILTIN_PLUGINS']
