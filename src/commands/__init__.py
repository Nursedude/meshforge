"""
MeshForge Commands Layer

Unified command interface for GTK and CLI.
All UI-independent operations go here.

Usage:
    from commands import meshtastic, service, hardware, gateway, diagnostics, hamclock, rns, messaging

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

    # Diagnostics
    result = diagnostics.get_system_health()
    result = diagnostics.run_gateway_diagnostics()

    # HamClock - Space Weather & Propagation
    hamclock.configure("localhost", api_port=8082)
    result = hamclock.get_space_weather()
    result = hamclock.get_voacap()
    result = hamclock.get_noaa_solar_data()  # NOAA fallback

    # RNS - Reticulum Network Stack
    result = rns.get_status()
    result = rns.read_config()
    result = rns.add_interface("My Server", "TCPServerInterface", {"listen_port": "4242"})
    result = rns.check_connectivity()

    # Messaging - Native mesh messaging
    result = messaging.send_message("Hello mesh!", destination="!abcd1234")
    result = messaging.get_messages(limit=20)
    result = messaging.get_conversations()
"""

from . import meshtastic
from . import service
from . import hardware
from . import gateway
from . import diagnostics
from . import hamclock
from . import rns
from . import messaging
from .base import CommandResult, CommandError

__all__ = [
    'meshtastic',
    'service',
    'hardware',
    'gateway',
    'diagnostics',
    'hamclock',
    'rns',
    'messaging',
    'CommandResult',
    'CommandError',
]
