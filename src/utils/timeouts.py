"""
MeshForge Timeout Constants - Single Source of Truth.

All timeout values used across the codebase should be imported from here.
Organized by context for easy discovery.

Usage:
    from utils.timeouts import SUBPROCESS_DEFAULT, HTTP_CONNECT, MQTT_CONNECT
"""

# =============================================================================
# Subprocess Timeouts
# =============================================================================

# Quick subprocess operations (systemctl is-active, version checks)
SUBPROCESS_QUICK = 5  # seconds

# Medium subprocess operations (meshtastic --info, rnpath)
SUBPROCESS_MEDIUM = 15  # seconds

# General subprocess timeout (CLI calls, systemctl commands)
SUBPROCESS_DEFAULT = 30  # seconds

# Longer subprocess operations (firmware updates, large file ops)
SUBPROCESS_LONG = 60  # seconds

# Installation operations (apt install, pip install)
SUBPROCESS_INSTALL = 600  # seconds (10 min)

# =============================================================================
# HTTP / Network Timeouts
# =============================================================================

# TCP connection establishment (meshtasticd port 4403)
TCP_CONNECT = 10  # seconds

# HTTP connection probe (initial connection to HTTP API)
HTTP_CONNECT = 5.0  # seconds

# HTTP response body read
HTTP_READ = 10.0  # seconds

# HTTP protobuf TX (stateless PUT to /api/v1/toradio)
HTTP_PROTOBUF_TX = 5.0  # seconds

# HTTP protobuf session operations (handshake, config read)
HTTP_PROTOBUF_SESSION = 10.0  # seconds

# AREDN node sysinfo API. Constrained mips_24kc routers (e.g. the MikroTik hAP
# ac lite) generate sysinfo on demand; it is slow and variable under OLSR load.
# 2026-06-16 moc5 flap: the hAP's /a/sysinfo intermittently exceeded the old
# hardcoded 3s read overnight (latency pegged at exactly 3.007s = the timeout),
# mislabeling a REACHABLE-but-slow node as unreachable. The connect must stay
# fast — a slow connect IS genuinely down — but the body read needs headroom.
AREDN_SYSINFO_CONNECT = 2  # seconds — port-open probe; a slow connect = down
AREDN_SYSINFO_READ = 8     # seconds — sysinfo body read (was a hardcoded 3/5)

# =============================================================================
# MQTT Timeouts
# =============================================================================

# Initial broker connection
MQTT_CONNECT = 10  # seconds

# Remote broker reconnection backoff
MQTT_RECONNECT_INITIAL = 5  # seconds
MQTT_RECONNECT_MAX = 60  # seconds

# Local broker reconnection (faster recovery)
MQTT_LOCAL_RECONNECT_INITIAL = 2  # seconds
MQTT_LOCAL_RECONNECT_MAX = 30  # seconds

# =============================================================================
# Socket / Low-level
# =============================================================================

# Raw socket connection test (DNS/port checks)
SOCKET_CONNECT = 3  # seconds

# GPS daemon socket timeout
GPSD_CONNECT = 3.0  # seconds

# Online/offline connectivity probe
CONNECTIVITY_CHECK = 3.0  # seconds

# RNS RPC call timeout (local Unix socket, should respond in <100ms)
# Used in meshforge_wrapper.py (hardcoded there since wrapper can't import this)
RNS_RPC = 3  # seconds

# =============================================================================
# Service Check / Daemon
# =============================================================================

# systemctl status queries
SERVICE_CHECK = 5  # seconds

# Wait after restart for service readiness
SERVICE_RESTART_WAIT = 10  # seconds

# =============================================================================
# Database
# =============================================================================

# SQLite busy_timeout for concurrent access (message queue, contacts, traffic)
SQLITE_BUSY = 30  # seconds

# =============================================================================
# Message Delivery
# =============================================================================

# LXMF delivery confirmation window
DELIVERY_CONFIRMATION = 300  # seconds (5 min)

# Message considered stale if stuck in-progress
MESSAGE_STALE = 300  # seconds (5 min)

# Tactical message chunk delivery
TACTICAL_CHUNK = 120  # seconds (2 min)

# =============================================================================
# Circuit Breaker
# =============================================================================

# Time before testing recovery from OPEN state
CIRCUIT_RECOVERY = 60.0  # seconds

# =============================================================================
# Thread Operations
# =============================================================================

# Default thread.join() timeout
THREAD_JOIN = 5.0  # seconds

# Thread join for threads with cleanup work
THREAD_JOIN_LONG = 15.0  # seconds

# =============================================================================
# Node Status Thresholds
# =============================================================================

# Node considered online if seen within this window
NODE_ONLINE = 900  # seconds (15 min)

# Node considered stale after this period
NODE_STALE = 7 * 24 * 3600  # seconds (7 days)

# =============================================================================
# Agent / Heartbeat
# =============================================================================

# Agent heartbeat timeout (3 missed heartbeats at 30s interval)
AGENT_HEARTBEAT = 90.0  # seconds

# =============================================================================
# External API
# =============================================================================

# Timeout for external API calls (NOAA, PSKReporter, etc.)
EXTERNAL_API = 15  # seconds

# DX cluster telnet connection
DX_TELNET = 10  # seconds
