"""
MeshForge Port Constants

Centralized port number definitions for all services used by MeshForge.
Import these constants instead of hardcoding port numbers.

Usage:
    from utils.ports import MESHTASTICD_PORT, RNS_AUTOINTERFACE_PORT

    if check_port(MESHTASTICD_PORT):
        connect_to_meshtasticd()
"""

# =============================================================================
# Meshtastic Service Ports
# =============================================================================

# Primary meshtasticd TCP API port
MESHTASTICD_PORT = 4403

# Alternate meshtasticd port (for multi-instance setups)
MESHTASTICD_ALT_PORT = 4404

# Meshtasticd Web UI port (HTTPS, separate from TCP API)
MESHTASTICD_WEB_PORT = 9443

# Meshtasticd ports for detection (ordered by priority)
MESHTASTICD_PORTS = [MESHTASTICD_PORT, MESHTASTICD_ALT_PORT]


# =============================================================================
# Reticulum (RNS) Ports
# =============================================================================

# RNS AutoInterface multicast discovery port
RNS_AUTOINTERFACE_PORT = 29716

# RNS Shared Instance port (for multiple apps sharing RNS)
RNS_SHARED_INSTANCE_PORT = 37428

# RNS TCP Server port (for TCP interface)
RNS_TCP_SERVER_PORT = 4242


# =============================================================================
# Other Service Ports
# =============================================================================

# HamClock web interface
HAMCLOCK_PORT = 8080

# MQTT broker (Mosquitto)
MQTT_PORT = 1883

# OpenWebRX SDR interface
OPENWEBRX_PORT = 8073

# WebSocket server (real-time message broadcast)
WEBSOCKET_PORT = 8080

# Config API HTTP server
CONFIG_API_PORT = 8081


# =============================================================================
# Port Groups (for diagnostics and status checks)
# =============================================================================

# All Meshtastic-related ports
MESHTASTIC_ALL_PORTS = {
    'api': MESHTASTICD_PORT,
    'api_alt': MESHTASTICD_ALT_PORT,
    'web': MESHTASTICD_WEB_PORT,
}

# All RNS-related ports
RNS_ALL_PORTS = {
    'autointerface': RNS_AUTOINTERFACE_PORT,
    'shared_instance': RNS_SHARED_INSTANCE_PORT,
    'tcp_server': RNS_TCP_SERVER_PORT,
}

# Common service ports for health checks
SERVICE_PORTS = {
    'meshtasticd': MESHTASTICD_PORT,
    'mqtt': MQTT_PORT,
    'openwebrx': OPENWEBRX_PORT,
    'nomadnet': None,  # NomadNet uses RNS shared instance, no dedicated port
}
