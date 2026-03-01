"""
MeshForge Commands Layer

Unified command interface for TUI and CLI.
All UI-independent operations go here.

Usage:
    from commands import meshtastic, service, hardware, gateway, diagnostics, propagation, rns, messaging

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

    # Propagation - Space Weather & HF Propagation (NOAA primary, standalone)
    result = propagation.get_space_weather()       # Always works (NOAA)
    result = propagation.get_band_conditions()     # Derived from NOAA data
    result = propagation.get_propagation_summary() # One-line summary
    result = propagation.get_enhanced_data()       # + optional HamClock/OpenHamClock

    # HamClock - Optional data source plugin (legacy)
    # For standalone propagation data, use `propagation` module instead.
    hamclock.configure("localhost", api_port=8082)
    result = hamclock.get_voacap()                 # HamClock-specific
    result = hamclock.get_dx_spots()               # HamClock-specific

    # RNS - Reticulum Network Stack
    result = rns.get_status()
    result = rns.read_config()
    result = rns.add_interface("My Server", "TCPServerInterface", {"listen_port": "4242"})
    result = rns.check_connectivity()

    # Messaging - Native mesh messaging
    result = messaging.send_message("Hello mesh!", destination="!abcd1234")
    result = messaging.get_messages(limit=20)
    result = messaging.get_conversations()

    # RNode - LoRa device detection and management
    result = rnode.detect_rnode_devices(probe=True)
    result = rnode.get_device_info("/dev/ttyUSB0")
    result = rnode.get_recommended_config("/dev/ttyUSB0", region="US")

    # Device Backup - Meshtastic device backup/restore
    result = device_backup.create_backup(name="pre-upgrade")
    backups = device_backup.list_backups()
    result = device_backup.restore_backup(backup_id)
"""

from . import meshtastic
from . import service
from . import hardware
from . import gateway
from . import diagnostics
from . import propagation
from . import hamclock
from . import rns
from . import messaging
from . import rnode
from . import device_backup
from .base import CommandResult, CommandError

__all__ = [
    'meshtastic',
    'service',
    'hardware',
    'gateway',
    'diagnostics',
    'propagation',
    'hamclock',
    'rns',
    'messaging',
    'rnode',
    'device_backup',
    'CommandResult',
    'CommandError',
]
