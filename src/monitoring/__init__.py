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

# Exports resolve lazily (PEP 562): node_monitor pulls the meshtastic
# package (~150 ms) — an eager import here taxed every consumer of ANY
# monitoring submodule at import time (e.g. TUI startup via
# utils.map_data_collector -> monitoring.mqtt_subscriber).
from importlib import import_module

# Public symbol -> defining submodule. Keep in sync with __all__.
_LAZY_EXPORTS = {
    # Submodules (the old eager imports bound these as package attributes)
    'node_monitor': '.node_monitor',
    'tcp_monitor': '.tcp_monitor',
    # Node monitoring
    'NodeMonitor': '.node_monitor',
    'NodeInfo': '.node_monitor',
    'NodeMetrics': '.node_monitor',
    'NodePosition': '.node_monitor',
    # TCP monitoring
    'TCPMonitor': '.tcp_monitor',
    'TCPConnection': '.tcp_monitor',
    'TCPState': '.tcp_monitor',
    'NetworkScanner': '.tcp_monitor',
    'NetworkDevice': '.tcp_monitor',
    'measure_connection_rtt': '.tcp_monitor',
    'discover_meshtasticd_devices': '.tcp_monitor',
}

__all__ = list(_LAZY_EXPORTS)
__version__ = '0.2.0'


def __getattr__(name):
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(submodule, __name__)
    value = module if submodule == '.' + name else getattr(module, name)
    globals()[name] = value  # cache: __getattr__ only fires on the first miss
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
