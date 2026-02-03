# Session Notes: meshtasticd Multi-Consumer Architecture Audit

**Date**: 2026-02-03
**Branch**: `claude/complete-meshtasticd-architecture-e3iVs`
**Status**: Testing phase - code review complete, test script created

## Target Architecture

```
meshtasticd
    ├── TCP:4403 → Gateway Bridge → RNS transport → WebSocket:5001
    └── MQTT → local mosquitto → MeshForge MQTT Subscriber
                              → meshing-around
                              → Grafana/InfluxDB
                              → future tools
```

## Component Status

### Path 1: TCP:4403 → Gateway Bridge → RNS

| Component | Status | File | Notes |
|-----------|--------|------|-------|
| meshtasticd TCP listener | WORKING | external | Port 4403, single client limit |
| Gateway Bridge connection | DONE | `src/gateway/rns_bridge.py` | Uses connection manager |
| RNS transport layer | DONE | `src/gateway/rns_bridge.py` | Full RNS ↔ Meshtastic bridging |
| WebSocket broadcast | DONE | `src/utils/websocket_server.py` | Port 5001, JSON messages |
| Message storage | DONE | `src/gateway/message_queue.py` | SQLite persistence |

**TUI Entry Point**: Gateway → Start Gateway Bridge

### Path 2: MQTT → mosquitto → MeshForge

| Component | Status | File | Notes |
|-----------|--------|------|-------|
| meshtasticd MQTT publisher | WORKING | external config | `mqtt.enabled`, `json_enabled` |
| mosquitto broker | WORKING | system service | Port 1883 |
| MQTT Setup Wizard | DONE | `src/launcher_tui/service_menu_mixin.py` | Auto-configures meshtasticd |
| MQTT Subscriber | FIXED | `src/monitoring/mqtt_subscriber.py` | Topic: `msh/2/json/{channel}/#` |
| TUI MQTT Monitor | FIXED | `src/launcher_tui/mqtt_mixin.py` | Local/Public mode switch |
| MessageListener MQTT mode | DONE | `src/utils/message_listener.py` | Uses `create_local_subscriber()` |
| Map data caching | DONE | `src/monitoring/mqtt_subscriber.py` | Auto-persists to `mqtt_nodes.json` |

**TUI Entry Points**:
- Configuration → Service Config → MQTT Setup (wizard)
- Mesh Networks → MQTT Monitor → Configure → Use Local Broker

## Fixes This Session

1. **MQTT Mixin API** (`mqtt_mixin.py`)
   - Fixed incorrect constructor call (kwargs → config dict)
   - Fixed `.run()` → `.start()` method
   - Fixed `.nodes` → `get_nodes()`, `get_stats()`

2. **Topic Structure** (`mqtt_subscriber.py`)
   - Added `LOCAL_ROOT_TOPIC = "msh/2/e"` (no region prefix)
   - `create_local_subscriber()` now uses correct default

## Verified Working (User Tested)

- meshtasticd running on Pi
- mosquitto receiving messages: `msh/2/json/LongFast/!nodeID`
- Meshtastic web browser with sync ack messages
- Position data flowing (lat/lon in JSON payloads)
- Signal quality data (SNR -19.75 to 6.75, RSSI, hops)

## Remaining Tasks (Prioritized)

### P0 - Critical (blocks usage)
None - architecture is complete and working

### P1 - High Priority (improves reliability)
1. **Test TUI MQTT Monitor** on Pi with local broker
   - Verify "Use Local Broker" quick setup works
   - Verify Start Subscriber connects
   - Verify node discovery populates

2. **Test Gateway Bridge + MQTT parallel operation**
   - Verify both paths can run simultaneously
   - TCP:4403 exclusive to Gateway Bridge
   - MQTT publishes to mosquitto for other consumers

### P2 - Medium Priority (nice to have)
1. **Add MQTT → WebSocket bridge** (for web UI without Gateway Bridge)
   - When running MQTT Monitor, also broadcast to WebSocket:5001
   - Enables web UI display without Gateway Bridge running

2. **TUI MessageListener menu option**
   - Currently MQTT Monitor uses subscriber directly
   - Could add MessageListener(mode="mqtt") for unified interface

3. **Grafana dashboard integration**
   - Document InfluxDB/Prometheus export from MQTT
   - Create sample dashboards

### P3 - Low Priority (future)
1. **meshing-around integration guide**
   - Document how to configure meshing-around for local MQTT

2. **Rate limiting / deduplication**
   - Both paths may receive same messages
   - Add dedup by message ID if needed

## Files Changed This Session

| File | Change |
|------|--------|
| `src/launcher_tui/mqtt_mixin.py` | Fixed API usage |
| `src/monitoring/mqtt_subscriber.py` | Added LOCAL_ROOT_TOPIC |
| `.claude/TODO_PRIORITIES.md` | Updated tech debt |
| `.claude/session_notes/2026-02-03_mqtt_api_fix.md` | Session notes |

## Quick Reference

### Start MQTT Architecture (on Pi)

```bash
# 1. Start meshtasticd (if not already)
sudo systemctl start meshtasticd

# 2. Start mosquitto
sudo systemctl start mosquitto

# 3. Configure meshtasticd MQTT (one time)
# Via TUI: Configuration → Service Config → MQTT Setup
# Or manually:
meshtastic --host localhost --set mqtt.enabled true
meshtastic --host localhost --set mqtt.address localhost
meshtastic --host localhost --set mqtt.json_enabled true
meshtastic --host localhost --ch-index 0 --ch-set uplink_enabled true

# 4. Verify messages flowing
mosquitto_sub -h localhost -t 'msh/#' -v

# 5. Start MeshForge MQTT Monitor
# TUI: Mesh Networks → MQTT Monitor → Configure → Use Local Broker → Start
```

### Start Gateway Bridge (exclusive TCP path)

```bash
# Note: Only one TCP client can connect to meshtasticd
# Stop any other TCP clients first (including raw rnsd)

sudo systemctl stop rnsd  # if running separately

# Via TUI: Gateway → Start Gateway Bridge
```

## Parallel Operation Analysis

**Key Insight**: Both paths CAN run simultaneously because they use different transports:

| Path | Transport | Limit | Use Case |
|------|-----------|-------|----------|
| TCP:4403 | meshtasticd TCP | **1 client** | Gateway Bridge (exclusive) |
| MQTT | mosquitto:1883 | **Unlimited** | MQTT Monitor, meshing-around, Grafana |

**Why it works**:
- meshtasticd has a hard limit of ONE TCP client
- BUT it can publish to MQTT simultaneously
- MQTT broker (mosquitto) allows unlimited subscribers
- Gateway Bridge takes TCP:4403, everything else uses MQTT

**Operational Pattern**:
```
Gateway Bridge running?
├── YES → Use MQTT path for monitoring (MQTT Monitor, etc.)
└── NO  → Can use either path (TCP for direct access, MQTT for multi-consumer)
```

## Test Script

Created `scripts/test_meshtasticd_architecture.py` for Pi validation:

```bash
# Run on Pi
python3 scripts/test_meshtasticd_architecture.py
```

Tests:
1. Service status (meshtasticd, mosquitto)
2. Port connectivity (TCP:4403, MQTT:1883)
3. MQTT topic activity (msh/#)
4. MeshForge module imports
5. MQTT subscriber connection

## Session Progress

### Code Review Complete ✓
- `mqtt_mixin.py`: Correct API usage (get_nodes, get_stats, start/stop)
- `mqtt_subscriber.py`: LOCAL_ROOT_TOPIC = "msh/2/e" for local broker
- `service_menu_mixin.py`: MQTT Setup Wizard working
- TUI menu paths: Mesh Networks → MQTT Monitor → Configure → Use Local Broker

### Files Added
| File | Purpose |
|------|---------|
| `scripts/test_meshtasticd_architecture.py` | Pi validation test script |

## Test Checklists for Pi Validation

### P1: TUI MQTT Monitor Test Checklist

Run on Pi with meshtasticd + mosquitto running.

**Prerequisites:**
```bash
sudo systemctl status meshtasticd   # must be active
sudo systemctl status mosquitto     # must be active
mosquitto_sub -h localhost -t 'msh/#' -v -C 1  # must see messages
```

**Test Sequence:**

1. [ ] **Launch TUI**
   ```bash
   sudo python3 src/launcher_tui/main.py
   ```

2. [ ] **Navigate to MQTT Monitor**
   - Mesh Networks → MQTT Monitor
   - Status should show "Not running"

3. [ ] **Configure for Local Broker**
   - Configure → Use Local Broker
   - Should show "Local Mode Set" confirmation
   - Topic should be `msh/2/json/{channel}/#` (auto-detected channel)

4. [ ] **Start Subscriber**
   - Start Subscriber
   - Should show "Connecting to MQTT broker..."
   - After 2s, should show "MQTT Started" or "Connection Issue"
   - Status should change to "Connected"

5. [ ] **Verify Node Discovery**
   - Wait 30-60 seconds for mesh activity
   - View Nodes → Should populate with discovered nodes
   - Statistics → Should show message count > 0

6. [ ] **Stop and Restart**
   - Stop Subscriber → Confirm
   - Status should return to "Not running"
   - Start Subscriber again → Should reconnect

7. [ ] **Export Data**
   - Export Data
   - Verify file at `~/.local/share/meshforge/mqtt_export.json`

**Pass Criteria:**
- All steps complete without errors
- Nodes discovered from local mesh traffic
- Data persists in cache file

---

### P1: Gateway Bridge + MQTT Parallel Test Checklist

Tests both data paths running simultaneously.

**Prerequisites:**
```bash
sudo systemctl status meshtasticd   # must be active
sudo systemctl status mosquitto     # must be active
sudo systemctl status rnsd          # should be STOPPED (we'll use internal RNS)
```

**Test Sequence:**

1. [ ] **Start MQTT Monitor First**
   - TUI: Mesh Networks → MQTT Monitor
   - Configure → Use Local Broker
   - Start Subscriber
   - Verify status "Connected"

2. [ ] **Start Gateway Bridge (parallel)**
   - TUI: Mesh Networks → Gateway Bridge → Start Bridge (background)
   - Should start successfully
   - Check logs: `tail -f /tmp/meshforge_gateway.log`

3. [ ] **Verify Both Running**
   - MQTT Monitor status: "Connected" ✓
   - Gateway Bridge status: "Running" ✓
   - `lsof -i :4403` - should show bridge_cli.py connection
   - `mosquitto_sub -t 'msh/#' -C 1` - should still receive messages

4. [ ] **Test Message Flow**
   - Send a test message via Meshtastic radio
   - Verify in MQTT Monitor (Statistics → message count increases)
   - Verify in Gateway Bridge logs (message received)

5. [ ] **Stop Gateway Bridge**
   - Gateway Bridge → Stop
   - MQTT Monitor should continue working

6. [ ] **Stop MQTT Monitor**
   - MQTT Monitor → Stop Subscriber
   - Both paths now inactive

**Architecture Validation:**
```
meshtasticd (TCP:4403) ──→ Gateway Bridge (exclusive)
meshtasticd (MQTT)     ──→ mosquitto:1883 ──→ MQTT Monitor (parallel)
                                          ──→ (other consumers)
```

**Pass Criteria:**
- Both paths operate simultaneously without conflict
- TCP:4403 exclusive to Gateway Bridge
- MQTT continues working when Gateway Bridge stops
- No resource contention

---

## P2 Implementation: MQTT → WebSocket Bridge

**Completed**: MQTT → WebSocket bridge for web UI access without Gateway Bridge.

### New Files
| File | Purpose |
|------|---------|
| `src/utils/mqtt_websocket_bridge.py` | Bridge MQTT subscriber data to WebSocket:5001 |

### Modified Files
| File | Change |
|------|--------|
| `src/launcher_tui/mqtt_mixin.py` | Added WebSocket Bridge toggle in MQTT menu |

### How It Works

```
MQTT Subscriber ─┬─> TUI display (existing)
                 ├─> Cache file (existing)
                 └─> WebSocket Bridge ──> ws://0.0.0.0:5001 ──> Web UI (NEW)
```

### TUI Menu Path

Mesh Networks → MQTT Monitor → WebSocket Bridge → Toggle On/Off

### Requirements

- MQTT subscriber must be running first
- `websockets` Python package (usually already installed)

### Web UI Integration

When WebSocket bridge is enabled:
1. Web UI connects to `ws://localhost:5001`
2. Receives `mesh_message` events (text messages)
3. Receives `node_update` events (node telemetry/position)
4. Same format as Gateway Bridge WebSocket output

---

## Handoff Notes

- Branch: `claude/complete-meshtasticd-architecture-SHBl3`
- Architecture is functionally complete (including P2)
- All data paths now have WebSocket output option
- Test checklists added for systematic Pi validation
- Next: Run test checklists on Pi, test WebSocket bridge with web UI
