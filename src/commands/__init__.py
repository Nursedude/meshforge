"""
MeshForge Commands Layer

Unified command interface for GTK and CLI.
All UI-independent operations go here.

Usage:
    from commands import meshtastic, service, hardware, gateway

    # Meshtastic operations
    result = meshtastic.get_node_info()
    result = meshtastic.list_nodes()
    result = meshtastic.send_message("Hello", dest="!ba4bf9d0")

    # Service management
    result = service.check_status("meshtasticd")
    result = service.get_logs("meshtasticd", lines=20)

    # Hardware detection
    result = hardware.detect_devices()
    result = hardware.check_spi()

    # Gateway operations
    result = gateway.get_status()
    result = gateway.start()
"""

from . import meshtastic
from . import service
from . import hardware
from . import gateway
from .base import CommandResult, CommandError

__all__ = [
    'meshtastic',
    'service',
    'hardware',
    'gateway',
    'CommandResult',
    'CommandError',
]
