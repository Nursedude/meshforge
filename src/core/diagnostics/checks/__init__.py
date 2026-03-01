"""
Diagnostic Check Implementations

Check modules organized by category:
- services: systemd services and process checks
- network: connectivity, DNS, and TCP port checks
- rns: Reticulum Network Stack checks
- meshtastic: Meshtastic library and device checks
- serial: Serial port and permission checks
- hardware: SPI, I2C, temperature, SDR checks
- system: Python, packages, memory, disk, CPU checks
- ham_radio: HAM radio configuration checks
"""

# Services checks
from .services import (
    check_service,
    check_process,
    check_service_logs,
)

# Network checks
from .network import (
    check_tcp_port,
    check_internet,
    check_dns,
)

# RNS checks
from .rns import (
    check_rns_installed,
    check_rns_config,
    check_rns_port,
    check_rns_storage_permissions,
    check_meshtastic_interface_file,
)

# Meshtastic checks
from .meshtastic import (
    check_meshtastic_installed,
    check_meshtastic_cli,
    check_meshtastic_connection,
    find_serial_devices,
)

# Serial checks
from .serial import (
    check_serial_ports,
    check_dialout_group,
    find_serial_devices as find_serial_devices_serial,  # Alias to avoid conflict
)

# Hardware checks
from .hardware import (
    check_spi,
    check_i2c,
    check_temperature,
    check_sdr,
)

# System checks
from .system import (
    check_python_version,
    check_pip_packages,
    check_memory,
    check_disk_space,
    check_cpu_load,
)

# MeshCore checks
from .meshcore import (
    check_meshcore_installed,
    check_meshcore_config,
    check_meshcore_device,
    check_meshcore_bridge_status,
)

# HAM radio checks
from .ham_radio import (
    check_callsign,
)

__all__ = [
    # Services
    'check_service',
    'check_process',
    'check_service_logs',
    # Network
    'check_tcp_port',
    'check_internet',
    'check_dns',
    # RNS
    'check_rns_installed',
    'check_rns_config',
    'check_rns_port',
    'check_rns_storage_permissions',
    'check_meshtastic_interface_file',
    # Meshtastic
    'check_meshtastic_installed',
    'check_meshtastic_cli',
    'check_meshtastic_connection',
    'find_serial_devices',
    # Serial
    'check_serial_ports',
    'check_dialout_group',
    # Hardware
    'check_spi',
    'check_i2c',
    'check_temperature',
    'check_sdr',
    # System
    'check_python_version',
    'check_pip_packages',
    'check_memory',
    'check_disk_space',
    'check_cpu_load',
    # MeshCore
    'check_meshcore_installed',
    'check_meshcore_config',
    'check_meshcore_device',
    'check_meshcore_bridge_status',
    # HAM Radio
    'check_callsign',
]
