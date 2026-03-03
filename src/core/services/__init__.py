"""
MeshForge Service Management

Centralized service checking, lifecycle management, and connection
handling for systemd services (meshtasticd, rnsd, mosquitto, etc.).

Key modules:
    service_check: Service status queries and systemd operations (SINGLE SOURCE OF TRUTH)
    meshtastic_connection: TCP/serial connection management for Meshtastic radios
    connection_manager: Global connection lock for meshtasticd's single-client limit
    meshtastic_http: HTTP API client for meshtasticd
    active_health_probe: NGINX-style background health monitoring
    rns_config: Reticulum configuration file management
    rns_status_parser: rnstatus output parser

Canonical imports::

    from core.services.service_check import check_service, ServiceStatus
    from core.services.meshtastic_connection import MeshtasticConnectionManager
    from core.services.active_health_probe import ActiveHealthProbe

Backward-compatible imports (via shims in utils/)::

    from utils.service_check import check_service  # still works
"""
