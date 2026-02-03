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

TCP/IP Monitoring:
    from src.monitoring import TCPMonitor, NetworkScanner

    # Monitor TCP connections
    monitor = TCPMonitor()
    monitor.start()
    connections = monitor.get_meshtasticd_connections()

    # Discover meshtasticd devices on network
    scanner = NetworkScanner()
    devices = scanner.scan_subnet("192.168.1.0/24")
"""

from .node_monitor import NodeMonitor, NodeInfo, NodeMetrics, NodePosition
from .tcp_monitor import (
    TCPMonitor,
    TCPConnection,
    TCPState,
    NetworkScanner,
    NetworkDevice,
    measure_connection_rtt,
    discover_meshtasticd_devices,
)

__all__ = [
    # Node monitoring
    'NodeMonitor',
    'NodeInfo',
    'NodeMetrics',
    'NodePosition',
    # TCP monitoring
    'TCPMonitor',
    'TCPConnection',
    'TCPState',
    'NetworkScanner',
    'NetworkDevice',
    'measure_connection_rtt',
    'discover_meshtasticd_devices',
]
__version__ = '0.2.0'
