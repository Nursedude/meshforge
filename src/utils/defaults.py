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

# Maximum text message length (Meshtastic limit) — the BROADCAST egress text
# budget (measured on-air max 232 B; see gateway.canonical_message).
MAX_MESHTASTIC_MSG_LENGTH = 228
# A DM's budget: the firmware adds 12 B for PKC on a DM (fleet radios are not
# in licensed mode, so PKC is live) — measured max text 220 B, 215 B with
# reply_id. Our senders set no reply_id today, so 215 keeps a deliberate 5 B
# margin (one budget whatever a future sender adds). Bridge matrix F17, 2026-10-26.
MAX_MESHTASTIC_DM_MSG_LENGTH = 215

# =============================================================================
# Timeouts — canonical source is utils.timeouts
# Re-exports for backward compatibility
# =============================================================================

from utils.timeouts import TCP_CONNECT as TCP_CONNECT_TIMEOUT_SEC  # noqa: F401
from utils.timeouts import SUBPROCESS_DEFAULT as SUBPROCESS_DEFAULT_TIMEOUT_SEC  # noqa: F401
from utils.timeouts import MQTT_CONNECT as MQTT_CONNECT_TIMEOUT_SEC  # noqa: F401
