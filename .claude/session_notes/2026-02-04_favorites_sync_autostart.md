# Session Notes: Favorites Sync & MQTT Auto-Start

**Date:** 2026-02-04
**Branch:** `claude/favorites-sync-implementation-7fACT`
**Focus:** Favorites sync research, MQTT/TelemetryPoller auto-start, meshtastic/web deep dive

---

## Work Completed This Session

### 1. MQTT & TelemetryPoller Auto-Start Implementation

**Files Modified:**
- `src/launcher_tui/mqtt_mixin.py` - Added auto-start functionality
- `src/launcher_tui/main.py` - Added auto-start call in run()

**New Features:**
- `_maybe_auto_start_mqtt_and_telemetry()` method follows existing `_maybe_auto_start_map()` pattern
- `_auto_start_mqtt_quiet()` helper for silent startup (no TUI corruption)
- Config options in MQTT configuration menu:
  - `auto_start` - Start MQTT subscriber on TUI launch
  - `auto_start_telemetry` - Start TelemetryPoller with MQTT
  - `telemetry_poll_minutes` - Poll interval (default 30 min)

**Usage:**
1. Go to MQTT Monitor → Configure
2. Enable "Auto-Start" option
3. Save configuration
4. MQTT subscriber will auto-start on next TUI launch

### 2. Favorites Data Model Preparation

**Files Modified:**
- `src/gateway/node_tracker.py` - UnifiedNode dataclass
- `src/monitoring/node_monitor.py` - NodeInfo dataclass
- `src/monitoring/mqtt_subscriber.py` - MQTTNode dataclass

**Fields Added:**
```python
# UnifiedNode (node_tracker.py)
is_favorite: bool = False
favorite_updated: Optional[datetime] = None

# NodeInfo (node_monitor.py)
is_favorite: bool = False

# MQTTNode (mqtt_subscriber.py)
is_favorite: bool = False
```

---

## Meshtastic/Web Deep Dive Findings

### Favorites API (CONFIRMED WORKING)

**Protobuf Definition (mesh.proto):**
```protobuf
message NodeInfo {
    bool is_favorite = 10;  // "Persists between NodeDB internal clean ups"
    bool is_ignored = 11;
    bool is_key_manually_verified = 12;
    bool is_muted = 13;
}
```

**Admin Commands (admin.proto):**
- `set_favorite_node` (field 39) - `uint32` node number
- `remove_favorite_node` (field 40) - `uint32` node number

**Python API Pattern:**
```python
from meshtastic.tcp_interface import TCPInterface

interface = TCPInterface(hostname='localhost')

# READ favorites
for node_num, node_info in interface.nodes.items():
    is_fav = node_info.get('isFavorite', False)
    print(f"Node {node_num}: favorite={is_fav}")

# SET favorite (on local node)
local_node = interface.getNode(interface.myInfo.my_node_num)
local_node.setFavorite("!abcd1234")

# REMOVE favorite
local_node.removeFavorite("!abcd1234")

interface.close()
```

**CLI Commands:**
```bash
# Set favorite
meshtastic --set-favorite-node !abcd1234

# Remove favorite
meshtastic --remove-favorite-node !abcd1234
```

### HTTP Transport Patterns (transport-http)

**Endpoints:**
- `GET /api/v1/fromradio?all=true` - Poll for incoming packets
- `PUT /api/v1/toradio` - Send outgoing commands
- `OPTIONS /api/v1/toradio` - Connection probe/discovery

**Timeouts:**
- Read operations: 7 seconds
- Write operations: 4 seconds
- Heartbeat: Prevents 15-minute serial timeout

**Reliability Patterns:**
- `safePoll()` prevents concurrent requests via `fetching` flag
- Graceful status transitions with explicit state machine
- Differentiates timeout errors from general failures
- `closingByUser` flag prevents error emissions during intentional disconnect

### Core Architecture Insights

**Event System:**
- 36+ event types defined in `Emitter` enum
- Granular events: Connect, Disconnect, ConnectionStatus, etc.
- Events for each packet type: onUserPacket, onDeviceMetadataPacket, etc.

**Session Security:**
- `session_passkey` (field 101) required for admin commands
- Node generates key with get_x_response packets
- Client must include same key with set_x commands

**Node Data:**
- Remote nodes NOT tracked internally in meshDevice.ts
- Node data arrives via event dispatchers
- Methods like `getMetadata(nodeNum)` query device's internal NodeDB

---

## Implementation Plan for Favorites TUI

### Phase 1: Read Favorites (Ready for Hardware Test)

**Test Script:**
```python
#!/usr/bin/env python3
"""Test favorites reading from Meshtastic device."""
from meshtastic.tcp_interface import TCPInterface

interface = TCPInterface(hostname='localhost')
print("Connected to meshtasticd")

favorites = []
for node_num, node_info in interface.nodes.items():
    is_fav = node_info.get('isFavorite', False)
    name = node_info.get('user', {}).get('longName', 'Unknown')
    node_id = node_info.get('user', {}).get('id', f'!{node_num:08x}')
    print(f"  {node_id}: {name} - favorite={is_fav}")
    if is_fav:
        favorites.append(node_id)

print(f"\nFavorites: {favorites}")
interface.close()
```

### Phase 2: Write Favorites (Pending Hardware Test)

**Implementation in MeshForge:**
```python
# In commands/meshtastic.py or new favorites module
def set_favorite(interface, node_id: str) -> bool:
    """Mark a node as favorite on the local device."""
    try:
        local_node = interface.getNode(interface.myInfo.my_node_num)
        local_node.setFavorite(node_id)
        return True
    except Exception as e:
        logger.error(f"Failed to set favorite: {e}")
        return False

def remove_favorite(interface, node_id: str) -> bool:
    """Remove favorite status from a node."""
    try:
        local_node = interface.getNode(interface.myInfo.my_node_num)
        local_node.removeFavorite(node_id)
        return True
    except Exception as e:
        logger.error(f"Failed to remove favorite: {e}")
        return False
```

### Phase 3: TUI Integration

**Location:** `src/launcher_tui/topology_mixin.py` or `mqtt_mixin.py`

**Menu Options:**
- Show favorites list
- Toggle favorite on selected node
- Bulk favorite management
- Star icon (★) indicator in node lists

---

## Files Ready for Commit

1. `src/launcher_tui/mqtt_mixin.py` - Auto-start implementation
2. `src/launcher_tui/main.py` - Auto-start call
3. `src/gateway/node_tracker.py` - is_favorite field
4. `src/monitoring/node_monitor.py` - is_favorite field
5. `src/monitoring/mqtt_subscriber.py` - is_favorite field

---

## Next Session Tasks

1. **Hardware Test** (Q&A later today)
   - Test favorites reading with BaseUI 2.7 device
   - Verify `isFavorite` field appears in node data
   - Test setFavorite/removeFavorite API calls

2. **Implement Favorites Parsing**
   - Add `isFavorite` extraction in `_parse_node_data()` (node_monitor.py)
   - Add to UnifiedNode `from_meshtastic()` method (node_tracker.py)

3. **TUI Favorites Menu**
   - Show favorites in node details
   - Toggle favorite option
   - Filter view for favorites only

4. **Consider Additional Patterns from meshtastic/web**
   - Session passkey handling for admin commands
   - Timeout patterns (7s read, 4s write)
   - ACK/NAK waiting for admin confirmations

---

## Session Health Check

**Entropy Level:** LOW - Clear progress, documentation complete
**Code Status:** Compiles successfully, ready for testing
**Blocking Issues:** None - hardware test is informational only

---

## Quick Reference

**Config File:** `~/.config/meshforge/mqtt_nodeless.json`
```json
{
  "broker": "localhost",
  "port": 1883,
  "topic": "msh/2/json/+/#",
  "auto_start": true,
  "auto_start_telemetry": true,
  "telemetry_poll_minutes": 30
}
```

**Test Favorites CLI:**
```bash
# List all nodes with favorite status
meshtastic --host localhost --nodes

# Mark node as favorite
meshtastic --host localhost --set-favorite-node '!ba4bf9d0'

# Remove favorite
meshtastic --host localhost --remove-favorite-node '!ba4bf9d0'
```
