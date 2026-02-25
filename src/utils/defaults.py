"""
MeshForge Default Constants

Centralized non-port constants used across the codebase.
Import these instead of hardcoding values.

Usage:
    from utils.defaults import HEALTH_CHECK_INTERVAL_SEC
"""

# =============================================================================
# Health Monitoring
# =============================================================================

# How often to run health checks in background (seconds)
HEALTH_CHECK_INTERVAL_SEC = 30

# Number of consecutive failures before marking service as down
HEALTH_CHECK_FAIL_THRESHOLD = 3

# Number of consecutive passes before marking service as recovered
HEALTH_CHECK_PASS_THRESHOLD = 2

# =============================================================================
# Logging
# =============================================================================

# Maximum log file size before rotation (1 MB)
LOG_ROTATION_MAX_BYTES = 1_048_576

# Number of backup log files to keep
LOG_ROTATION_BACKUP_COUNT = 3

# =============================================================================
# Node Tracking
# =============================================================================

# Hours before a node is considered stale
STALE_NODE_HOURS = 72

# Maximum number of nodes to track
MAX_NODES = 10_000

# =============================================================================
# Message Limits
# =============================================================================

# Maximum message payload in bytes
MAX_PAYLOAD_BYTES = 65_536

# Maximum text message length (Meshtastic limit)
MAX_MESHTASTIC_MSG_LENGTH = 228

# =============================================================================
# Timeouts
# =============================================================================

# Default TCP connection timeout (seconds)
TCP_CONNECT_TIMEOUT_SEC = 10

# Default subprocess timeout (seconds)
SUBPROCESS_DEFAULT_TIMEOUT_SEC = 30

# MQTT connection timeout (seconds)
MQTT_CONNECT_TIMEOUT_SEC = 10
