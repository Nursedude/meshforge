# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> This serves as institutional memory for development.
>
> **Last audited**: 2026-02-24 — Security & QA sweep (v0.5.4-beta): GTK4 remnant removed from plugin_base.py, raw systemctl bypasses fixed in orchestrator + installer, safe_import violation fixed, main.py trimmed below 1500 via export extraction to topology_mixin, line counts refreshed, H1 fully resolved

---

## Health Check Reconciliation (2026-02-20)

The code review health check (2026-01-24) identified 5 critical (C1-C5) and 1 high (H1)
issues. After auditing the current codebase, here is their status:

| ID | Issue | Status | Evidence |
|----|-------|--------|----------|
| C1 | LXMF Source None after partial RNS init | **MITIGATED** | Guard at `rns_bridge.py:579-580` returns False instead of crashing |
| C2 | reconnect.py raises None on early interruption | **FIXED** | `reconnect.py:176-178` raises ConnectionError |
| C3 | Unbounded node tracking dicts (memory leak) | **FIXED** | MAX_NODES caps + eviction in node_tracker.py and node_monitor.py |
| C4 | Stats dict race conditions (24 racy increments) | **FIXED** | threading.Lock added across all affected files |
| C5 | Atomic write uses deterministic temp path | **FIXED** | `paths.py` uses `tempfile.mkstemp()` for unique temp files |
| H1 | Non-interruptible shutdown in daemon loops | **FIXED** (2026-02-20) | All daemon loops now use `_stop_event.wait()` instead of `time.sleep()` |

**Key lesson**: File-scoped fixes applied between Jan 24 — Feb 20 resolved C2-C5 individually.
H1 was fully resolved by Feb 20 — all daemon loops now use `_stop_event.wait()`. Remaining
`time.sleep()` calls (49 files) are in bounded connection-wait loops, one-shot delays, and
interactive pauses, which are acceptable patterns.

---

## Issue #1: Path.home() Returns /root with sudo — RESOLVED (2026-02-20)

### Status: **RESOLVED** — Zero `Path.home()` violations remain in codebase.

### Rule
**ALWAYS use `get_real_user_home()` from `utils/paths.py`** instead of `Path.home()`:

```python
# WRONG - breaks with sudo
from pathlib import Path
config_file = Path.home() / ".config" / "meshforge" / "settings.json"

# CORRECT - works with sudo
from utils.paths import get_real_user_home
config_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
```

### Resolution (2026-02-20)
- Fixed last 3 violations: `mqtt_bridge_handler.py`, `cli.py`, `rns_config.py`
- Consolidated 20 `safe_import` fallback copies to direct imports (Issue #5)
- Linter (`scripts/lint.py`) checks MF001

### Prevention
- Use `from utils.paths import get_real_user_home, MeshForgePaths`
- Grep for `Path.home()` before committing
- Linter enforces MF001

---

## Archived GTK Issues (#2, #10, #11, #13, #14, #15)

GTK4 was removed in v0.5.x. These issues are no longer relevant.
Historical details in `persistent_issues_archive.md`.

---

## Issue #3: Services Not Started/Verified — PARTIALLY RESOLVED (2026-02-20)

### Status: **Gateway pre-flight checks done.** 34+ secondary locations remain.

### Rule
**Always call `check_service()` before connecting to services.**

**Advisory vs Blocking**:
- **Advisory** (background daemons): Warn but continue — service may run outside systemd
- **Blocking** (user-facing TUI menus): Show error + fix hint, don't proceed

```python
# ADVISORY — for background daemon connections (gateway, bridges)
status = check_service('meshtasticd')
if not status.available:
    logger.warning("meshtasticd service check: %s (attempting connection anyway)",
                   status.message)
    # Continue — TCP connect attempt is the definitive test

# BLOCKING — for user-initiated TUI actions
status = check_service('meshtasticd')
if not status.available:
    show_error(status.message)
    show_fix(status.fix_hint)
    return  # Don't proceed
```

### Completed (2026-02-20)
- `meshtastic_handler.py` — Advisory `check_service('meshtasticd')` before TCP connect
- `mqtt_bridge_handler.py` — Advisory `check_service('mosquitto')` for localhost brokers
- `rns_bridge.py` — Advisory `check_service('rnsd')` before RNS init
- `meshtastic_connection.py` — Replaced raw `systemctl restart` with `restart_service()`

**Note**: Gateway pre-flight checks are ADVISORY (warn + continue), not blocking.
Services may run outside systemd (Docker, manual start). The actual connection
attempt is the definitive test. Blocking checks caused "waiting for delivery"
regression when mosquitto wasn't detectable via systemctl.

### Remaining (34+ locations)
- 8 files create `TCPInterface` without meshtasticd checks
- 4 MQTT connections without mosquitto checks
- 8 files with raw `subprocess.run(['systemctl', ...])` bypassing service_check

### Known Services (in `utils/service_check.py`)
| Service | Port | systemd name |
|---------|------|--------------|
| meshtasticd | 4403 | meshtasticd |
| rnsd | None | rnsd |
| hamclock | 8080 | hamclock |
| mosquitto | 1883 | mosquitto |

---

## Issue #4: Silent Debug-Level Logging

### Symptom
Errors occur but user/developer sees no indication because logs are at DEBUG level.

### Root Cause
Over-cautious logging to avoid "spam" means real errors are hidden.

### Proper Fix
Use appropriate log levels:
- **ERROR**: Something broke, needs attention
- **WARNING**: Something unusual, might be a problem
- **INFO**: User-visible operations (connected, saved, etc.)
- **DEBUG**: Internal details for developers

```python
# WRONG - hides important info
logger.debug(f"Connection failed: {error}")

# CORRECT - visible in normal logging
logger.info(f"[Component] Connection failed: {error}")
```

---

## Issue #5: Duplicate Utility Functions — RESOLVED (2026-02-20)

### Status: **RESOLVED** — All 20 `safe_import` fallback copies consolidated to direct imports.

### Rule
**Single source of truth**: Define once in `utils/paths.py`, import everywhere else.

```python
# CORRECT — first-party module, always available
from utils.paths import get_real_user_home

# WRONG — safe_import is for EXTERNAL deps only
_home, _HAS_PATHS = safe_import('utils.paths', 'get_real_user_home')
```

### Resolution (2026-02-20)
Consolidated 20 files from `safe_import('utils.paths', ...)` fallback patterns to direct `from utils.paths import get_real_user_home`. Net -220 lines removed.

### Follow-up (2026-02-23)
Review found `startup_checks.py` still used `safe_import('utils.service_check', ...)` with `_HAS_SERVICE_CHECK` guard — converted to direct import. The guard created a dead fallback path that would silently revert RNS socket detection to broken UDP-only behavior.

---

## Development Checklist

Before committing, verify:

- [ ] No new `Path.home()` calls added (use `get_real_user_home()`)
- [ ] Error messages are actionable, not generic
- [ ] Log levels appropriate (INFO for user actions, ERROR for failures)
- [ ] Services are verified before use (use `check_service()`)
- [ ] subprocess calls have timeout parameters (MF004)
- [ ] Utilities imported from central location, not duplicated

---

## Quick Reference: Import Patterns

```python
# Paths - use these instead of Path.home()
from utils.paths import get_real_user_home, get_real_username
from utils.paths import MeshForgePaths, ReticulumPaths

# Settings
from utils.common import SettingsManager, CONFIG_DIR

# Logging
from utils.logging_utils import get_logger

# Service availability checks - use before service-dependent operations
from utils.service_check import check_service, check_port, ServiceState

# Optional external dependencies — use safe_import
from utils.safe_import import safe_import
RNS, _HAS_RNS = safe_import('RNS')                              # External
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')                 # External
_yaml, _HAS_YAML = safe_import('yaml')                            # External

# First-party modules — ALWAYS use direct imports (never safe_import)
from utils.service_check import check_service    # ✓ Direct
from utils.event_bus import emit_message         # ✓ Direct
from gateway.rns_bridge import RNSMeshtasticBridge  # ✓ Direct
```

### Test Patching with safe_import

When testing code that uses safe_import for external deps, patch the
module-level `_HAS_*` flags directly — NOT `sys.modules`:

```python
# WRONG - flags already evaluated at import time
@patch.dict('sys.modules', {'RNS': MagicMock()})
def test_rns(self): ...  # _HAS_RNS is still False!

# CORRECT - patch the flag directly
@patch('gateway.rns_bridge._HAS_RNS', True)
def test_rns(self): ...  # Now _HAS_RNS is True
```

---

## Issue #6: Large Files Exceeding Guidelines

### Symptom
Files exceed the 1,500 line guideline from CLAUDE.md, making them difficult to navigate, test, and maintain.

### Current Status (2026-02-24, refreshed)

**Python files over 1,500 lines:**

| File | Lines | Status | Notes |
|------|-------|--------|-------|
| `src/utils/knowledge_content.py` | 1,993 | OK | Content file by design - no split needed |
| `src/gateway/rns_bridge.py` | 1,599 | MONITOR | MeshCoreBridgeMixin + MessageRouter + gateway_cli extracted |
| `src/utils/prometheus_exporter.py` | 1,521 | MONITOR | Grew after metrics_export split |
| `src/utils/service_check.py` | 1,515 | MONITOR | Growing — watch for extraction candidates |
| `src/launcher_tui/nomadnet_client_mixin.py` | 1,505 | MONITOR | Stable |
| `src/commands/rns.py` | 1,505 | MONITOR | Stable |

**Under threshold (previously tracked):**

| File | Lines | Notes |
|------|-------|-------|
| `src/launcher_tui/rns_menu_mixin.py` | 1,498 | Grew slightly but still under 1,500 |
| `src/launcher_tui/service_menu_mixin.py` | 1,487 | Dropped below threshold |
| `src/utils/map_http_handler.py` | 1,475 | Under threshold |
| `src/utils/map_data_collector.py` | 1,475 | Under threshold |
| `src/launcher_tui/main.py` | 1,463 | Trimmed 2026-02-24: export functions → topology_mixin |
| `src/utils/config_api.py` | 1,316 | Well under threshold |

**Previously over threshold (NOW RESOLVED):**

| File | Was | Now | Resolution |
|------|-----|-----|------------|
| `src/monitoring/traffic_inspector.py` | 2,194 | 442 | Extracted to packet_dissectors, traffic_models, traffic_storage |
| `src/gateway/node_tracker.py` | 1,808 | 989 | Extracted to node_models.py |
| `src/launcher_tui/main.py` | 1,799 | 1,489 | Extracted network_tools, web_client, data_path mixins; removed dead code |
| `src/core/diagnostics/engine.py` | 1,767 | 709 | Extracted to models.py |
| `src/utils/metrics_export.py` | 1,762 | 96 | Split to common/prometheus/influxdb modules |
| `src/launcher_tui/rns_menu_mixin.py` | 1,524 | 1,496 | Extracted rns_sniffer_mixin + rns_config_mixin + rns_diagnostics_mixin |

**GTK files removed from tracking (GTK deprecated):**
- GTK4 interface was removed; TUI is now the only interface

**Markdown files over 1,000 lines:**

| File | Lines | Action |
|------|-------|--------|
| `.claude/dude_ai_university.md` | ~200 | Trimmed in dedup audit (2026-02-23) |
| `.claude/foundations/persistent_issues.md` | 1,165 | Stable after archiving resolved issues |

### Remaining Extraction Candidates

1. **rns_bridge.py** (1,599 lines) - Over threshold
   - Potential: Extract `meshtastic_handler.py` (Meshtastic connection/send/receive) ~400 lines
   - Only split if file grows further
2. **prometheus_exporter.py** (1,521 lines) - Over threshold after metrics_export split
   - Monitor for now; split if it grows
3. **service_check.py** (1,515 lines) - Growing, new to tracking
   - Monitor for now

### Completed Extractions (2026-02-06)

All previously tracked files are now under 1,500 lines:
- traffic_inspector.py: 2,194 → 442 (split to 4 modules)
- main.py: 1,799 → 1,489 (43 mixins extracted, dead code removed)
- node_tracker.py: 1,808 → 989 (node_models.py extracted)
- metrics_export.py: 1,762 → 96 (split to common/prometheus/influxdb)
- engine.py: 1,767 → 709 (models.py extracted)
- rns_menu_mixin.py: 1,524 → 1,210 (sniffer extracted)

### Proper Fix

Files over 1,500 lines should be split when adding new features to them.
Previously refactored: launcher_tui (extracted 30 mixins), hamclock (extracted API client),
rns.py (extracted config editor + mixins). Web UI and Rich CLI were deleted in consolidation.

### Prevention
- Check file length before adding new features
- Split files proactively at 1,000 lines
- Use `wc -l src/**/*.py | sort -rn | head -10` to monitor
- When adding to launcher_tui/main.py, **always check if a mixin exists or should be created**

---

## Issue #7: Missing File References in Launchers

### Symptom
TUI or launcher crashes when selecting a menu option because the referenced file doesn't exist.

### Example
`launcher_tui.py` line 349 referenced `gateway/bridge_cli.py` which didn't exist:
```python
subprocess.run([sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')])
```

### Root Cause
Adding menu options that reference new scripts without creating the scripts first.

### Proper Fix
1. **Create the script before referencing it**
2. **Add verification step**: Check all file references exist before committing
3. **Use commands layer when possible**: Instead of running scripts, use commands module

### Prevention
Run this verification before committing launcher changes:
```bash
# Check all referenced files exist
for f in src/launcher.py src/launcher_tui/main.py \
         src/standalone.py; do
  [ -f "$f" ] && echo "OK: $f" || echo "MISSING: $f"
done
```

---

## Issue #8: Outdated Fallback Version Strings

### Symptom
Application shows old version number even after version bump.

### Root Cause
Fallback version strings in try/except blocks don't get updated:
```python
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.3"  # Outdated!
```

### Proper Fix
1. Search for hardcoded version strings when bumping version
2. Use `grep -r "0\.4\." src/` to find all occurrences

### Prevention
```bash
# Before releasing, search for version strings
grep -rn "0\.[0-9]\.[0-9]" src/*.py | grep -v __version__.py
```

---

## Issue #9: Broad Exception Swallowing — MOSTLY RESOLVED (2026-02-20)

### Status: **MOSTLY RESOLVED** — 28 of 30 instances fixed. 2 benign exceptions remain by design.

### Rule
```python
# BAD - hides all errors
except Exception:
    pass

# GOOD - log it
except Exception as e:
    logger.debug("Non-critical operation failed: %s", e)
```

### Resolution (2026-02-20)
Fixed 28 silent `except Exception: pass` across 7 files:
- `tcp_monitor.py` (7) — callback failures now logged as warnings
- `system_diagnostics.py` (8) — converted to `logger.debug()`
- `setup_wizard.py` (3) — converted to `logger.debug()`
- `hardware_config.py` (2) — converted to logged warnings
- `rns_sniffer.py` (2) — converted to `logger.debug()`
- `site_planner.py` (2) — converted to `logger.debug()`

### Remaining (by design)
- `packet_dissectors.py` — benign decode try/except (no logger in file)
- `pskreporter_subscriber.py` — cleanup exceptions inside outer try that already logs

### Prevention
- Grep for `except.*:.*pass` before committing
- Code review should flag broad exception handlers

---

## Issue #12: RNS "Address Already in Use" When Connecting as Client

### Symptom
Application errors like:
```
[Error] The interface "Default Interface" could not be created
[Error] The contained exception was: [Errno 98] Address already in use
```

This happens when MeshForge tries to connect to an existing rnsd instance.

### Root Cause
`RNS.Reticulum()` reads the user's `~/.reticulum/config` which defines interfaces (like AutoInterface). Even when connecting to a shared instance, RNS tries to create these interfaces, which fails because rnsd already bound those ports.

### Wrong Fix (documented but not implemented)
The old workaround in `fresh_install_test.md` said to manually edit `~/.reticulum/config` to disable AutoInterface. This requires user intervention and doesn't scale.

### Proper Fix (2026-01-13)
MeshForge now creates a client-only config in `/tmp/meshforge_rns_client/` with:
- `share_instance = Yes`
- No interface definitions

This allows connecting to rnsd without trying to bind ports.

### Location
`src/gateway/node_tracker.py` - `_init_rns_main_thread()` method

### Prevention
- When connecting to shared RNS instances, always use a client-only config
- Never call `RNS.Reticulum()` without a configdir when rnsd is running

---

## Issue #16: Gateway Message Routing Reliability

### Symptom
- Messages may not reach destination
- Delivery confirmation unreliable over LoRa

### Root Cause
Inherent to mesh networking: best-effort delivery, node unreachability, queue overflow.

### Current State
- Message transmission implemented via HTTP protobuf (v0.5.4)
- Gateway bridge connects RNS and Meshtastic networks
- Message queue persists to SQLite for retry

### Proper Fix
**Accept reliability limitations** — document delivery as "best effort":
```python
result = messaging.send_message(dest, text)
if result.get("status") == "sent":
    show_status("Sent (delivery not guaranteed)")
elif result.get("status") == "queued":
    show_status("Queued - gateway not connected")
```

### Files Involved
- `src/commands/messaging.py` — Message sending logic
- `src/gateway/rns_bridge.py` — RNS-Meshtastic bridge
- `src/gateway/message_queue.py` — SQLite message queue

---

## Issue #17: Meshtastic Connection Contention (meshtasticd Single-Client)

### Symptom
- Recurring "Connection reset by peer" and "Broken pipe" errors
- meshtasticd logs show "Force close previous TCP connection" every second
- Gateway connection drops intermittently
- Multiple components competing for TCP connection

### Root Cause (Identified 2026-01-18)
**meshtasticd only supports ONE TCP client at a time.** When multiple components create independent TCP connections:
```
Component A connects → OK
Component B connects → A disconnected by meshtasticd
Component A reconnects → B disconnected
... cycle continues
```

### Impact
- Connection thrashing every 1-2 seconds
- Messages may be lost during reconnection
- Gateway stability compromised
- External tools (Meshtastic Web UI on port 9443) also compete

### Proper Fix (Implemented 2026-01-18)
**Shared connection manager** - All components share ONE persistent connection:

```python
# message_listener.py - Check for existing connection BEFORE creating new one
def _run(self):
    conn_mgr = get_connection_manager(host=self.host)
    if conn_mgr.has_persistent():
        # Another component owns the connection - just subscribe to pub/sub
        self._interface = conn_mgr.get_interface()
        self._owns_connection = False
        logger.info(f"Using existing connection from {conn_mgr.get_persistent_owner()}")
    else:
        # No existing connection - we need to create one
        if conn_mgr.acquire_persistent(owner="message_listener"):
            self._interface = conn_mgr.get_interface()
            self._owns_connection = True
```

### Files Changed
- `src/utils/message_listener.py` - Check for existing persistent connection
- `src/utils/meshtastic_connection.py` - Connection manager

### HTTP fromradio Contention Fix (2026-02-23)

**Additional root cause**: The `/api/v1/fromradio` HTTP endpoint is also single-consumer.
When the protobuf client calls `connect()`, it drains all fromradio packets (including
delivery ACKs) during session setup, starving the meshtasticd web client at :9443 and
causing "waiting for delivery" hangs. The meshtastic CLI fallback also competes via TCP.

**Fix**: `send_text_direct()` — a stateless TX function that POSTs directly to
`/api/v1/toradio` without ever reading from `/api/v1/fromradio`. meshtasticd fills
in the source node number automatically, so no session handshake is needed for sending.

All TX paths now use `send_text_direct()` as primary, with session-based send as fallback:
- `mqtt_bridge_handler.py` — gateway MQTT bridge TX
- `mesh_bridge.py` — preset bridge TX
- `commands/meshtastic.py` — TUI/CLI message sending (before CLI subprocess fallback)

### Files Changed
- `src/gateway/meshtastic_protobuf_client.py` — Added `send_text_direct()` stateless TX
- `src/gateway/mqtt_bridge_handler.py` — Stateless TX as primary path
- `src/gateway/mesh_bridge.py` — Stateless TX as primary path
- `src/commands/meshtastic.py` — HTTP protobuf before CLI subprocess

### Prevention
- **NEVER read from `/api/v1/fromradio` during TX operations**
- Use `send_text_direct()` for all message sending (write-only to toradio)
- Reserve session-based `connect()` + `start_polling()` for config read operations only
- Always use `get_connection_manager()` instead of creating `TCPInterface` directly
- Check `has_persistent()` before creating connections
- Use `acquire_persistent(owner="component_name")` for long-lived connections
- For short operations, use the existing interface without taking ownership

---

## Issue #18: Meshtastic Auto-Reconnect on Connection Drop

### Symptom
- Gateway stops working after meshtasticd restart
- No automatic recovery from network issues
- User must manually restart MeshForge

### Root Cause
Original implementation had no reconnection logic - once connection dropped, it stayed dropped.

### Proper Fix (Implemented 2026-01-18)
**Health monitoring + exponential backoff reconnect**:

```python
# rns_bridge.py
def _poll_meshtastic(self):
    """Poll Meshtastic for health check"""
    if self._mesh_interface:
        try:
            if hasattr(self._mesh_interface, 'isConnected'):
                if not self._mesh_interface.isConnected:
                    self._handle_connection_lost()
                    return
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.warning(f"Meshtastic connection lost: {e}")
            self._handle_connection_lost()

def _handle_connection_lost(self):
    """Cleanup and prepare for reconnect"""
    self._connected_mesh = False
    if hasattr(self, '_conn_manager') and self._conn_manager:
        self._conn_manager.release_persistent()
    # Clear subscriptions, wait for cooldown

def _meshtastic_loop(self):
    """Main loop with auto-reconnect"""
    reconnect_delay = 1
    max_reconnect_delay = 30
    while self._running:
        if not self._connected_mesh:
            self._connect_meshtastic()
            if self._connected_mesh:
                reconnect_delay = 1  # Reset on success
            else:
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
```

### Files Changed
- `src/gateway/rns_bridge.py` - Health monitoring, auto-reconnect, exponential backoff

### Prevention
- All persistent connections should have health monitoring
- Use exponential backoff (1s → 2s → 4s → ... → 30s max) to avoid hammering
- Release connection manager resources on disconnect

---

## Issue #19: RNS Node Discovery from path_table

### Symptom
- RNS gateway only discovers 2 of 6+ nodes on network
- Nodes visible in `rnstatus` but not in MeshForge
- Node count doesn't match actual network

### Root Cause (Identified 2026-01-18)
MeshForge was only checking `RNS.Transport.destinations` which is limited. The complete routing table is in `RNS.Transport.path_table` which contains ALL destinations rnsd knows about.

### Proper Fix (Implemented 2026-01-18)
**Check path_table first** for complete routing information:

```python
# node_tracker.py
def _load_known_rns_destinations(self, RNS):
    # PRIMARY: Check path_table - contains ALL destinations rnsd knows about
    if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
        for dest_hash, path_data in RNS.Transport.path_table.items():
            if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                node_id = f"rns_{dest_hash.hex()[:16]}"
                if node_id not in self._nodes:
                    hops = path_data[1] if isinstance(path_data, tuple) and len(path_data) > 1 else 0
                    node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                    self.add_node(node)
                    logger.info(f"[RNS] Discovered node from path_table: {node_id} ({hops} hops)")
```

### Timing Issue
**path_table may be empty immediately after connect** - rnsd syncs data asynchronously.
Use delayed checks (5s after connection) and periodic re-checks (30s intervals).

### Files Changed
- `src/gateway/node_tracker.py` - path_table discovery + delayed/periodic checks

### Prevention
- When connecting to shared RNS instances, always check path_table
- Allow time for data sync before assuming empty
- Implement periodic re-checks for dynamic networks

---

*Last updated: 2026-02-21 - Cleanup: consolidated archived stubs, removed GTK references, cleaned separators*

---

## Issue #20: Service Detection & Status Display Redesign Required

### Symptom
After multiple fix attempts, these issues persist:
1. **RNS panel shows wrong status** - Lights/indicators show running/stopped incorrectly
2. **Meshtastic detection shows "FAILED"** - Even when meshtasticd service is running and functional
3. **TX works but RX doesn't display** - Messages sent successfully, received messages not shown in UI

### Root Cause Analysis

**Problem 1: Too Many Detection Methods**
Current `service_check.py` uses 3+ fallback methods with conflicting results:
```
UDP port check → pgrep → systemctl is-active → systemctl status
```
Each method can give different answers. When they conflict, UI shows wrong state.

**Problem 2: Conflating "Service Running" with "CLI Detection"**
The meshtastic detection treats CLI failures as service failures:
```
Service: RUNNING (systemctl says active)
CLI:     FAILS (can't connect via meshtastic --export-config)
UI:      Shows "DETECTION FAILED" ← Misleading
```

**Problem 3: No Event System for RX Messages**
Messages flow: `meshtasticd → gateway → logs` but NOT to UI
- TX: User action → API call → works
- RX: Incoming packet → log entry → UI never updated

### Failed Fix Attempts (2026-01-17)
1. Added UDP port check for 0.0.0.0 in addition to 127.0.0.1 - Still fails
2. Improved pgrep with exact match and word boundaries - Still matches incorrectly
3. Added service_running flag to detection result - UI still shows "FAILED"
4. Fixed NodeTracker import (was wrong class name) - Telemetry still doesn't show

### Redesign Specification

#### Component 1: Service Detection (service_check.py)

**Current Architecture (BROKEN):**
```
check_service() {
  if check_udp_port() → return running
  if check_process_running() → return running
  if check_systemd_service() → return running
  return not_running
}
```

**Proposed Architecture:**
```
check_service() {
  # SINGLE SOURCE OF TRUTH for systemd services
  if is_systemd_service(name):
    return systemctl_is_active(name)  # That's it. No fallbacks.

  # Only use port/process for non-systemd services
  return check_port_or_process(name)
}
```

**Rationale:**
- If rnsd/meshtasticd are managed by systemd, trust systemd
- Fallback methods (port check, pgrep) are unreliable
- "Unknown" state is better than wrong state

#### Component 2: Status Display (UI panels)

**Current Architecture (BROKEN):**
```
detection = detect_meshtastic_settings()
if detection is None or detection['preset'] is None:
    show "DETECTION FAILED"  ← Wrong when service runs but CLI unavailable
```

**Proposed Architecture:**
```
# Separate service status from detection capability
service_status = check_service('meshtasticd')
detection = detect_meshtastic_settings()

# Show BOTH states clearly
"Service: Running" or "Service: Stopped"
"Preset: MEDIUM_FAST" or "Preset: Unknown (select manually)"

# Never show "FAILED" when service is running
```

#### Component 3: RX Message Display

**Current Architecture (BROKEN):**
```
gateway.rns_bridge receives packet → logger.info("Received...")
                                   → No UI notification
```

**Proposed Architecture:**
```
# Event-based message notification
class MessageEvent:
    direction: "tx" | "rx"
    content: str
    timestamp: datetime
    node_id: str

# Gateway emits events
gateway.on_message_received(packet):
    event = MessageEvent(direction="rx", ...)
    event_bus.emit("message", event)

# UI subscribes to events
panel.on_init():
    event_bus.subscribe("message", self._on_message)

def _on_message(self, event):
    GLib.idle_add(self._add_message_to_list, event)
```

### Implementation Priority

| Component | Effort | Impact | Priority | Status (2026-02-20) |
|-----------|--------|--------|----------|---------------------|
| Service Detection Simplification | LOW | HIGH | 1 | **DONE** — systemctl trusted as SSOT for systemd services |
| Status Display Separation | MEDIUM | HIGH | 2 | **DONE** — meshtasticd_config_mixin separates service state from CLI detection |
| RX Message Events | HIGH | MEDIUM | 3 | **DONE** — event_bus wired to WebSocket server |

### Files Modified

**Phase 1: Service Detection** (completed earlier)
- `src/utils/service_check.py` — Simplified to systemctl-only for systemd services

**Phase 2: Status Display** (completed 2026-02-20)
- `src/launcher_tui/meshtasticd_config_mixin.py` — Shows service state and CLI detection separately
- Shows fix hints when stopped, "(CLI detection unavailable — select preset manually)" when detection fails

**Phase 3: RX Messages** (completed 2026-02-20)
- `src/utils/event_bus.py` — Thread-safe pub/sub with MessageEvent, ServiceEvent, NodeEvent
- `src/gateway/rns_bridge.py` — Emits message events on RX
- `src/utils/websocket_server.py` — Subscribes to event_bus, broadcasts to WebSocket clients
- `src/launcher_tui/messaging_mixin.py` — TUI live feed subscribes to message events

### RNS Socket Detection Enhancement (2026-02-22, PRs #920-922)
RNS uses abstract Unix domain sockets (`\0rns/{instance_name}`), not UDP port 37428.
All 20+ `check_udp_port(37428)` calls replaced with `check_rns_shared_instance()` which
uses 3-tier detection: abstract Unix socket -> TCP -> UDP fallback. KNOWN_SERVICES rnsd
`port_type` changed from `'udp'` to `'unix_socket'`. 18 unit tests added; consistency
test prevents drift between `startup_checks.SERVICES_TO_CHECK` and `KNOWN_SERVICES`.

Stale docstring in `status_bar.py` (said "UDP port 37428") and `safe_import` for
first-party `utils.service_check` in `startup_checks.py` cleaned up 2026-02-23.

### Prevention
- Don't add more detection fallback methods - simplify instead
- UI should always distinguish "service state" from "detection capability"
- Use `check_rns_shared_instance()` for all rnsd reachability checks (never raw UDP)

---

## Issue #21: Meshtastic CLI Preset Settings Not Reliably Applied

### Symptom (Discovered MOC2 2026-01-20)
- User sets modem preset via CLI: `meshtastic --host localhost --set lora.modem_preset SHORT_TURBO`
- CLI reports success
- Browser UI at localhost:9443 shows LONG_FAST (not SHORT_TURBO)
- Other settings (region, owner) apply correctly

### Root Cause
**Upstream meshtastic CLI issue** - The Python meshtastic CLI doesn't always apply preset changes correctly. This is NOT a MeshForge bug.

### Impact
- Users think they're on one preset but actually on another
- Network performance expectations don't match reality
- Slot coordination fails if nodes on different presets

### Workaround
**Always verify in browser** - The Web UI at port 9443 is the source of truth:
1. Apply settings via CLI
2. Verify in browser: `http://localhost:9443`
3. If mismatch, use browser to set correct value

### MeshForge Recommendation
Add verification step after CLI config:

```python
# In device config wizard
def apply_preset(preset_name):
    result = run_meshtastic_cli(['--set', 'lora.modem_preset', preset_name])
    if result.success:
        console.print(f"[yellow]Verify preset in browser: http://localhost:9443[/yellow]")
        console.print("[dim]Note: CLI preset changes may not always apply correctly[/dim]")
```

### Files to Update
- `src/config/radio.py` - Add verification warning
- `src/launcher_tui/main.py` - Add verification step in config wizard
- Documentation - Note the CLI limitation

### Prevention
- Always recommend browser verification after CLI changes
- Consider implementing direct meshtasticd API calls instead of CLI
- Track upstream meshtastic-python issue

---

## Issue #22: MeshForge Overwriting meshtasticd's config.yaml

### Symptom
- Web client (https://localhost:9443) not working
- config.yaml contains radio parameters (Bandwidth, SpreadFactor, TXpower) instead of base config
- User's HAT works but then stops after MeshForge install/update
- "Webserver:" section missing from config.yaml

### Root Cause
MeshForge install scripts and TUI were **overwriting** `/etc/meshtasticd/config.yaml` with our own templates, even when meshtasticd package already provided a valid one.

Multiple places were creating HAT templates in `available.d/` that might conflict with meshtasticd's official templates.

### Impact
- Web client inaccessible (missing Webserver config)
- Users think meshtasticd is broken when it's a config issue
- Radio parameters (Bandwidth, SpreadFactor, TXpower) appear in config.yaml where they shouldn't be
- User has to manually fix config.yaml

### The Correct Architecture

```
/etc/meshtasticd/
├── config.yaml              # Base config (Module: auto, Webserver, Logging)
│                            # PROVIDED BY meshtasticd package - DO NOT OVERWRITE
├── available.d/             # HAT templates (GPIO pins only)
│   ├── lora-MeshAdv-900M30S.yaml
│   ├── lora-waveshare-sxxx.yaml
│   └── ...                  # PROVIDED BY meshtasticd package - DO NOT CREATE OUR OWN
└── config.d/                # User's active HAT config
    └── lora-MeshAdv-900M30S.yaml  # COPIED from available.d/ by user
```

**Radio parameters (Bandwidth, SpreadFactor, TXpower) are:**
- Set via `meshtastic --set lora.modem_preset LONG_TURBO`
- Stored in meshtasticd's internal device database
- **NEVER in yaml files**

### Proper Fix

**In install scripts:**
```bash
# CHECK if config.yaml exists and is valid BEFORE touching it
if [[ -f "$CONFIG_DIR/config.yaml" ]] && grep -q "Webserver:" "$CONFIG_DIR/config.yaml"; then
    echo "Using existing config.yaml from meshtasticd package"
else
    # Only create if missing/empty
    create_minimal_config
fi
```

**In Python code:**
```python
config_yaml = Path('/etc/meshtasticd/config.yaml')

# Check if valid config exists
if config_yaml.exists() and 'Webserver:' in config_yaml.read_text():
    # DO NOT OVERWRITE - meshtasticd provided a good one
    pass
elif not config_yaml.exists():
    # Only create if missing
    create_minimal_config(config_yaml)
```

**MeshForge's job is to:**
1. Help users SELECT their HAT from meshtasticd's available.d/
2. COPY (not create) the HAT config to config.d/
3. NEVER overwrite config.yaml if meshtasticd provided a valid one
4. NEVER create HAT templates - meshtasticd provides them

### Files Fixed (2026-01-22)
- [x] `scripts/install_noc.sh` - Don't overwrite config.yaml, don't create HAT templates
- [x] `src/launcher_tui/main.py` - _fix_spi_config(), _install_native_meshtasticd()
- [x] `templates/config.yaml` - Simplified to minimal base config
- [x] Removed `templates/available.d/` HAT configs (meshtasticd provides these)

### Prevention
- NEVER use `cp templates/config.yaml /etc/meshtasticd/config.yaml` without checking
- NEVER create HAT templates - point users to meshtasticd's available.d/
- Always CHECK for "Webserver:" in existing config before modifying
- Test fresh installs with `apt install meshtasticd` THEN run MeshForge

---

## Issue #23: No Post-Install Verification (Installation Unreliability)

### Symptom
Installation completes "successfully" but:
- meshtasticd doesn't start
- Web client (port 9443) doesn't respond
- Gateway can't connect
- User spends more time troubleshooting than manual install would take

### Root Cause (Identified 2026-01-22)
**No automated verification after installation.** The install script:
1. Installs packages ✓
2. Creates config files ✓
3. Creates systemd services ✓
4. **Does NOT verify anything actually works** ✗

### Impact
- User thinks install succeeded when it didn't
- Silent failures lead to confusion hours later
- MeshForge takes MORE time than manual install (defeats purpose)
- Support burden from "it doesn't work" reports

### The Problem Pattern
```
install_noc.sh runs...
  ✓ meshtasticd package installed
  ✓ config.yaml created
  ✓ systemd service created
  "Installation Complete!"

User runs meshforge...
  ✗ meshtasticd won't start (config invalid)
  ✗ Web client unreachable (Webserver section missing)
  ✗ Gateway fails (no HAT config selected)
```

### Proper Fix (Implemented 2026-01-22)

**1. Created `scripts/verify_post_install.sh`:**
```bash
#!/bin/bash
# Verify MeshForge installation health
# Run after install_noc.sh or anytime to check system state

# Checks performed:
# - meshtasticd binary exists and is executable
# - config.yaml exists and has required sections
# - systemd service can start (or is already running)
# - Web client port 9443 responds
# - At least one radio configured (SPI HAT or USB)
# - RNS installed and rnsd functional
```

**2. Added `meshforge --verify-install` command**

**3. Install script now calls verification automatically:**
```bash
# At end of install_noc.sh:
echo "Verifying installation..."
if bash scripts/verify_post_install.sh; then
    echo "✓ Installation verified successfully"
else
    echo "⚠ Installation needs attention - see above"
fi
```

### Required Verification Checks

| Check | What It Verifies | Failure Action |
|-------|------------------|----------------|
| meshtasticd binary | Native daemon installed | Suggest apt install |
| config.yaml exists | Base config created | Create minimal config |
| Webserver section | Web client will work | Warn, show fix command |
| Port 9443 | Web client responding | Check service status |
| Radio detected | Hardware present | Warn, suggest HAT selection |
| config.d/ populated | HAT config selected (SPI) | Prompt HAT selection |
| rnsd available | RNS tools installed | Suggest pip install rns |
| udev rules | Device permissions correct | Reload udev rules |

### Files Changed
- [NEW] `scripts/verify_post_install.sh` - Comprehensive verification script
- [MOD] `scripts/install_noc.sh` - Call verification at end
- [NEW] `src/commands/verify.py` - Python verification for CLI
- [MOD] `src/launcher.py` - Add --verify-install flag

### Prevention
- ALWAYS run verification after install changes
- CI should test verification on all supported platforms
- Verification failures should be actionable (show how to fix)
- Never mark install "complete" until verification passes

---

## Issue #24: Meshtastic Module Not Found by rnsd (Python Environment Mismatch)

### Symptom
NomadNet or rnsd fails to start with:
```
[Critical] Using this interface requires a meshtastic module to be installed.
[Critical] You can install one with the command: python3 -m pip install meshtastic
```

rnsd repeatedly crashes with exit code 255/EXCEPTION:
```
systemd[1]: rnsd.service: Main process exited, code=exited, status=255/EXCEPTION
```

This happens even when the user has previously installed `meshtastic` via CLI.

### Root Cause
**Python environment mismatch.** The `Meshtastic_Interface.py` plugin in `/etc/reticulum/interfaces/` requires the `meshtastic` Python module. However:

1. **pipx isolation**: Installing meshtastic CLI with `pipx install meshtastic` puts it in an isolated virtual environment that rnsd cannot access
2. **Different Python version**: rnsd may use `/usr/bin/python3` while user installed meshtastic to `/usr/local/bin/python3`
3. **User vs system site-packages**: `pip3 install --user meshtastic` installs to `~/.local/lib/python3.x/` which root's rnsd cannot access

### Impact
- rnsd enters restart loop (every 5 seconds per systemd restart policy)
- NomadNet refuses to launch
- RNS-Meshtastic gateway completely broken
- User thinks system is broken when it's just a module path issue

### Proper Fix

**Option 1: System-wide install (recommended for rnsd)**
```bash
# Install to system site-packages where rnsd can find it
# --break-system-packages required on Debian 12+ / Pi OS Bookworm
# --ignore-installed avoids "Cannot uninstall packaging" errors
sudo pip3 install --break-system-packages --ignore-installed meshtastic
```

**Option 2: Install to same Python that rnsd uses**
```bash
# Check which Python rnsd uses
head -1 $(which rnsd 2>/dev/null || sudo find /usr -name rnsd 2>/dev/null | head -1)

# If rnsd uses /usr/local/bin/python3:
sudo /usr/local/bin/python3 -m pip install --break-system-packages --ignore-installed meshtastic

# If rnsd uses /usr/bin/python3:
sudo /usr/bin/python3 -m pip install --break-system-packages --ignore-installed meshtastic
```

**Option 3: Disable the Meshtastic interface if not needed**
```bash
# Edit RNS config to disable the interface
sudo nano /etc/reticulum/config
# Change 'enabled = yes' to 'enabled = no' under [[Meshtastic Interface]]

# Or remove the interface file entirely
sudo rm /etc/reticulum/interfaces/Meshtastic_Interface.py
sudo systemctl restart rnsd
```

### Diagnosing the Issue
```bash
# Check if meshtastic is importable by root's Python
sudo python3 -c "import meshtastic; print(f'OK: {meshtastic.__version__}')" 2>&1

# If "No module named 'meshtastic'":
# The module is not installed in root's Python path

# Check where meshtastic is installed (if at all)
pip3 show meshtastic 2>/dev/null && echo "User install found"
sudo pip3 show meshtastic 2>/dev/null && echo "System install found"
pipx list 2>/dev/null | grep meshtastic && echo "pipx install found (isolated!)"
```

### Files Involved
- `/etc/reticulum/interfaces/Meshtastic_Interface.py` - The RNS plugin that requires meshtastic
- `/etc/reticulum/config` - RNS configuration referencing the interface
- RNS interface plugin from: https://github.com/landandair/RNS_Over_Meshtastic

### MeshForge Detection
The gateway diagnostic (`src/utils/gateway_diagnostic.py`) should be updated to:
1. Check if Meshtastic_Interface.py exists
2. If it exists, verify meshtastic is importable as root
3. Show specific fix instructions if not

### Prevention
- When installing Meshtastic_Interface plugin, always verify meshtastic module is available
- Add pre-flight check in TUI before enabling RNS-Meshtastic bridge
- Document in installation wizard that meshtastic must be installed system-wide

---

## Archived Resolved Issues (#25, #26, #28)

Issues #25 (rnsd ratchets PermissionError), #26 (ReticulumPaths fallback copies), and #28 (API proxy fromradio) are resolved. Historical details in `persistent_issues_archive.md`.

---

## Issue #27: rnsd is OPTIONAL for Meshtastic-only Deployments

### Context
MeshForge supports two independent transport layers:
1. **MQTT** — Meshtastic native MQTT protocol (via mosquitto)
2. **RNS** — Reticulum Network Stack (via rnsd)

### When rnsd IS Needed
- RNS/LXMF messaging (NomadNet, Sideband)
- Cross-protocol bridging: Meshtastic <-> RNS/LXMF
- RNS-only mesh networks (non-Meshtastic)

### When rnsd is NOT Needed
- Meshtastic-to-Meshtastic bridging across presets (e.g., LongFast <-> ShortTurbo)
- MQTT monitoring (nodeless observation)
- RF calculations, propagation tools
- Node tracking via MQTT subscriber

### Architecture: Meshtastic LF <-> Private Broker <-> Meshtastic ST
For bridging between Meshtastic presets (e.g., LongFast slot 20 <-> ShortTurbo slot 8),
**no gateway code or rnsd is needed**. Both radios connect to the same MQTT broker
with the same channel name/PSK and use uplink_enabled + downlink_enabled:

```
Radio A (LONG_FAST)  --WiFi-->  mosquitto  <--WiFi--  Radio B (SHORT_TURBO)
  Channel: "MeshBridge"         (broker)              Channel: "MeshBridge"
  PSK: <custom_key>                                   PSK: <same_key>
  uplink: true                                        uplink: true
  downlink: true                                      downlink: true
```

Messages are bridged by the radios themselves via native Meshtastic MQTT.
MeshForge's role is running mosquitto and monitoring traffic.

### Architecture: Full MeshForge NOC (Meshtastic + RNS)
For the complete NOC with both transports:

```
Meshtastic LF ──> mosquitto ──> MeshForge MQTT Subscriber (monitoring)
Meshtastic ST ──>     │
                      └──> RNS Gateway Bridge ──> rnsd ──> NomadNet/Sideband
```

Both MQTT and RNS can coexist. The private broker handles Meshtastic transport,
RNS handles encrypted mesh-independent routing.

### Status: DOCUMENTED

---

## Issue #29: Regression Prevention System (2026-02-23)

### Status: **ACTIVE** — Automated guards preventing circular regressions.

### Problem
100+ hours spent in circular regressions across meshtasticd (:4403/:9443), rnsd,
gateway bridge, and MQTT. Fix one service → break another → fix that → break the first.

### Root Causes
1. **TCP Connection Contention** (Issue #17): meshtasticd supports ONE TCP client.
   Multiple components creating `TCPInterface()` directly caused connection thrashing.
2. **fromradio Endpoint Draining**: Reading `/api/v1/fromradio` starved :9443 web client.
3. **Config Drift**: Gateway and rnsd reading different config files silently.
4. **No automated regression guards** to catch anti-patterns at code-change time.

### Solution: 4-Layer Prevention

#### Layer 1: Lint Rules (`scripts/lint.py`)
| Rule | Severity | What It Catches |
|------|----------|-----------------|
| MF007 | ERROR | Direct `TCPInterface()` creation outside connection infrastructure |
| MF008 | WARNING | Raw `systemctl` for service state decisions (use `service_check`) |
| MF009 | ERROR | `RNS.Reticulum()` without `configdir=` (causes EADDRINUSE) |
| MF010 | WARNING | `time.sleep()` in daemon loops (blocks clean shutdown) |

#### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
Codebase-scanning pytest tests that fail if anti-patterns reappear:
- `TestTCPConnectionContract` — No new files creating TCPInterface directly
- `TestFromradioContract` — TX paths use `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct

#### Layer 3: Pre-Commit Hook (`.githooks/pre-commit`)
Runs linter + regression guards before every commit.
Setup: `git config core.hooksPath .githooks`

#### Layer 4: TCPInterface Violations Fixed
All direct `TCPInterface()` creation routed through connection manager or global lock:
- `node_health_mixin.py` — TCP fallback removed (HTTP-only)
- `favorites_mixin.py` — Uses `MeshtasticConnection` context manager
- `config/device.py` — Uses `MeshtasticConnection` context manager
- `device_controller.py` — Acquires `MESHTASTIC_CONNECTION_LOCK`
- `mesh_bridge.py` — Acquires lock + deprecation warning
- `rns_transport.py` — Acquires lock + releases on disconnect

### How to Work With This System

**Adding a new file that needs meshtasticd TCP:**
```python
# For short-lived reads:
from utils.connection_manager import MeshtasticConnection
with MeshtasticConnection() as conn:
    if conn:
        nodes = conn.nodes

# For long-lived connections:
from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
if MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10):
    wait_for_cooldown()
    interface = TCPInterface(hostname='localhost')
    # ... use interface ...
    # Release lock on disconnect
```

**Adding a legitimate TCPInterface creation:**
1. Add the filename to `ALLOWLISTED` in `TestTCPConnectionContract`
2. Add the filename to `lock_aware_files` in lint.py MF007 rule
3. Ensure the code acquires `MESHTASTIC_CONNECTION_LOCK` before creating

**Updating ratchet counts:**
When fixing a violation, update the expected count in the corresponding test class.
The test will fail if the count goes DOWN without updating (forces tightening).

---

## #10 Map Control Panel Scrollbar Overlap (2026-02-25)

**Symptom**: On the `:5000` map page, the right-side control panel's native browser scrollbar
(~15-17px wide) obstructs filter checkboxes, buttons, and stat rows when content overflows.

**Root cause**: `.panel-body` used `overflow-y: auto` without any scrollbar styling, so the
browser rendered a full-width native scrollbar inside the panel, consuming content space.

**Fix**: Added thin dark-themed scrollbar CSS to `web/node_map.html`:
- `scrollbar-width: thin` + `scrollbar-color` (Firefox)
- `::-webkit-scrollbar` at 6px width (Chrome/Safari/Edge)
- `scrollbar-gutter: stable` to prevent layout shift
- `padding-right: 4px` on `.panel-body` for content buffer
- Applied to `.panel-body`, `.no-gps-list`, and `.sim-links-list`

**Status**: **FIXED**
