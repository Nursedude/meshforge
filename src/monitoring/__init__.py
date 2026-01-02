"""
Meshtastic Node Monitoring Module

This module provides sudo-free monitoring of Meshtastic nodes via TCP interface.
It connects to meshtasticd on localhost:4403 and provides real-time node data.

Usage:
    from src.monitoring import NodeMonitor

    monitor = NodeMonitor()
    monitor.connect()

    # Get current nodes
    nodes = monitor.get_nodes()

    # Subscribe to events
    monitor.on_node_update = my_callback

    monitor.disconnect()
"""

from .node_monitor import NodeMonitor, NodeInfo, NodeMetrics

__all__ = ['NodeMonitor', 'NodeInfo', 'NodeMetrics']
__version__ = '0.1.0'
