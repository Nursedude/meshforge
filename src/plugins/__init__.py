"""
MeshForge Plugins

Built-in and community plugins for extending MeshForge functionality.

Available Plugins:
- mqtt_bridge: MQTT integration for Home Assistant, Node-RED
- meshcore: MeshCore protocol support
- meshing_around: Bot framework integration
- sartopo: CalTopo/SARTopo mapping integration
"""

from pathlib import Path

PLUGINS_DIR = Path(__file__).parent

__all__ = ['PLUGINS_DIR']
