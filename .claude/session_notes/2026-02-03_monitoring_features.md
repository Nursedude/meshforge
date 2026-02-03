# Session Notes: Add Monitoring Features

**Date**: 2026-02-03
**Branch**: `claude/add-monitoring-features-7payK`
**Version**: 0.5.0-beta

## Session Summary

Added comprehensive monitoring features including InfluxDB export, Grafana dashboards, and historical topology snapshots.

## Completed This Session

### 1. Unit Tests for JSON API Endpoints

Added tests to `tests/test_metrics_export.py`:
- `TestJSONAPIEndpoints` - Tests for `/api/json/metrics`, `/api/json/nodes`, `/api/json/status`
- `TestLabelEscaping` - Tests for Prometheus label value escaping
- `TestNodeMetricsCollection` - Tests for node metrics fallback behavior
- `TestGatewayMetricsCollection` - Tests for gateway metrics with both networks
- `TestInfluxDBExporter` - Comprehensive tests for the new InfluxDB exporter

### 2. InfluxDB Export Option

Created `InfluxDBExporter` class in `src/utils/metrics_export.py`:

Features:
- Support for InfluxDB 1.x (database + basic auth) and 2.x (token + org/bucket)
- HTTP and UDP transport options
- InfluxDB Line Protocol formatting
- Batch writes with configurable batch size and flush interval
- Per-node metrics (SNR, RSSI, battery)
- Service health metrics
- Message queue statistics
- Background export thread

Usage:
```python
from utils.metrics_export import start_influxdb_exporter, InfluxDBExporter

# InfluxDB 2.x
exporter = start_influxdb_exporter(
    url="http://localhost:8086",
    token="your-token",
    org="meshforge",
    bucket="metrics"
)

# InfluxDB 1.x
exporter = InfluxDBExporter(
    url="http://localhost:8086",
    database="meshforge",
    username="admin",
    password="admin"
)
exporter.write_metrics()
```

### 3. Grafana Dashboard Templates

Created two new dashboard templates in `dashboards/`:

| Dashboard | File | Description |
|-----------|------|-------------|
| MeshForge Infinity | `meshforge-infinity.json` | JSON API via Grafana Infinity plugin |
| MeshForge InfluxDB | `meshforge-influxdb.json` | InfluxDB time-series visualization |

Features:
- Service status (meshtasticd, rnsd)
- Node count with GPS tracking
- Signal quality trends (SNR, battery)
- Message activity visualization
- Node table with sortable columns

Updated `dashboards/README.md` with setup instructions for all data sources.

### 4. Historical Topology Snapshots

Created `src/utils/topology_snapshot.py`:

Classes:
- `TopologySnapshot` - Point-in-time network state (nodes, edges, stats)
- `TopologyDiff` - Differences between two snapshots
- `TopologySnapshotStore` - SQLite-backed persistent storage

Features:
- Periodic topology capture (configurable interval)
- Time-travel queries (`get_topology_at()`)
- Snapshot comparison (`compare_snapshots()`)
- Network evolution summary for charting
- Topology event logging
- Automatic cleanup with retention policy
- Integration with existing `UnifiedNodeTracker` and `MapDataCollector`

Usage:
```python
from utils.topology_snapshot import start_topology_capture, get_topology_snapshot_store

# Start periodic capture
store = start_topology_capture(interval_seconds=300)

# Get snapshots from last 24 hours
snapshots = store.get_snapshots(hours=24)

# Compare two snapshots
diff = store.compare_snapshots(snap1_id, snap2_id)
print(diff.get_summary())

# Get evolution data for charting
evolution = store.get_evolution_summary(hours=24, intervals=12)
```

### 5. Test Coverage

Added `tests/test_topology_snapshot.py`:
- 22 tests covering all functionality
- `TopologySnapshot` serialization
- `TopologyDiff` computation and summary
- `TopologySnapshotStore` CRUD operations
- Time-based queries
- Periodic capture start/stop
- Cleanup and retention

## Files Created/Modified

### New Files
```
src/utils/topology_snapshot.py           # Historical topology tracking
tests/test_topology_snapshot.py          # Tests for topology snapshots
dashboards/meshforge-infinity.json       # Grafana Infinity plugin dashboard
dashboards/meshforge-influxdb.json       # Grafana InfluxDB dashboard
```

### Modified Files
```
src/utils/metrics_export.py              # Added InfluxDBExporter class
tests/test_metrics_export.py             # Added JSON API and InfluxDB tests
dashboards/README.md                     # Updated with new dashboard docs
```

## Metrics Now Available

### Prometheus (existing)
- All existing metrics via HTTP server and textfile exporter

### InfluxDB (new)
| Measurement | Fields | Tags |
|-------------|--------|------|
| `meshforge_info` | value | version |
| `meshforge_service_healthy` | healthy | service |
| `meshforge_nodes` | total, with_gps | - |
| `meshforge_node` | snr, rssi, battery | node_id, name |
| `meshforge_messages` | pending, delivered, failed, retried | - |

### JSON API (existing, tested)
- `/api/json/metrics` - System metrics
- `/api/json/nodes` - Node list
- `/api/json/status` - Service status

## Integration Points

1. **InfluxDB** - Use `start_influxdb_exporter()` in TUI or daemon startup
2. **Grafana** - Import dashboards from `dashboards/` directory
3. **Topology History** - Use `start_topology_capture()` for evolution tracking

## Test Results

```
tests/test_topology_snapshot.py: 22 passed
tests/test_metrics_export.py: 34 passed (5 skipped - pre-existing)
```

## Next Steps (Suggested)

1. Add TUI menu option to start/stop metrics exporters
2. Integrate topology snapshots with visualization
3. Add alerting rules for Prometheus/InfluxDB
4. Create network evolution visualization (time-lapse of topology)

## Session Entropy

Low - Clean deliverables with comprehensive tests.

---

# Session 2: Fix Monitoring Map Bugs

**Date**: 2026-02-03
**Branch**: `claude/fix-monitoring-map-bugs-Wt4Je`

## Reported Issues

1. **Zoom controls buggy**: +/- buttons on map at :5000 not working correctly
   - "+" only works from full view with little zoom
   - Zoom out also has issues

2. **Missing nodes**: Nodes like "Farley-server Direct" (CC:8D:A2:ED:8E:A0) not appearing on map

## Investigation Findings

### Zoom Bug Root Cause

**Mobile CSS Overlap**: On screens < 768px width, the legend was repositioned to `top: 10px; left: 10px` which directly overlaps Leaflet's zoom controls (also positioned at top-left).

```css
/* BEFORE - Bug: Legend covers zoom controls on mobile */
@media (max-width: 768px) {
    .legend {
        bottom: auto;
        top: 10px;  /* Overlaps zoom controls! */
        left: 10px;
    }
}
```

### Missing Nodes Root Cause

**No GPS Display**: Nodes without GPS coordinates (like "Farley-server Direct") are tracked in `nodes_without_position` but were only shown as a count ("No GPS: 5") with no way to see which nodes were affected.

The backend correctly tracks these nodes with:
- Node ID
- Name
- Last seen timestamp
- Online status
- Hardware model
- SNR/battery data

But the UI had no way to display this list to users.

## Fixes Applied

### 1. Mobile Legend Position (web/node_map.html)

```css
/* AFTER - Legend below zoom controls */
@media (max-width: 768px) {
    .legend {
        bottom: auto;
        top: 80px;  /* Moved down, below Leaflet zoom controls */
        left: 10px;
    }
}
```

### 2. Expandable "No GPS" Node List (web/node_map.html)

Added interactive functionality:

- **Clickable stat row**: "No GPS: 5 ▸" now toggles to show/hide list
- **Expandable list**: Shows node name, online status (green/gray dot), and last seen time
- **Sorted by recency**: Most recently seen nodes first
- **State tracking**: Added `state.nodesWithoutPosition` to store data from API

New functions:
- `toggleNoGpsList()` - Toggle visibility of the list
- `updateNoGpsList()` - Populate list from state data

Example output when expanded:
```
No GPS: 3 ▾
┌─────────────────────────────────────┐
│ ● Farley-server Direct    13m ago  │
│ ○ Node-ABC123             2h ago   │
│ ○ Unknown-DEF456          5h ago   │
└─────────────────────────────────────┘
```

## Files Modified

| File | Changes |
|------|---------|
| `web/node_map.html` | Fixed mobile legend CSS, added No GPS list UI and JS functions |

## Verification

- Python imports verified OK
- HTML structure validated
- All new functions present in output

## Remaining Considerations

1. **maxZoom: 13 on fitBounds**: The "Fit All" button and initial load limit zoom to level 13. This is intentional to prevent over-zooming on sparse data, but users can still manually zoom past this.

2. **Tile layer maxZoom: 19**: CartoDB tiles support zoom up to 19, which is sufficient.

## Session Entropy

Low - Focused on two specific bugs with clear fixes.

---

# Session 3: Fix Network Topology D3.js Graph Display

**Date**: 2026-02-03
**Branch**: `claude/fix-network-topology-0rwRp`

## Reported Issues

1. **D3.js topology graph showing only one node**: Network Topology view shows just the "local" node
2. **Wireshark data sources**: What other devices (AREDN routers, TCP/IP devices) should appear?

## Investigation Findings

### Root Cause: Meshtastic Nodes Missing from Topology Graph

The D3.js network topology at `~/.cache/meshforge/topology.html` only showed the "local" node because:

1. **Nodes only exist if they have edges**: `NetworkTopology.to_dict()` (line 548-555 in `network_topology.py`) returns only nodes that have edges in `_nodes` dict
2. **Edges created only for RNS nodes**: When RNS nodes are discovered, `node_tracker.py:1516-1523` adds edges to topology
3. **Meshtastic nodes NOT added as edges**: When Meshtastic nodes are added via `add_node()` (line 1053), no topology edge was created
4. **Result**: Meshtastic nodes in `node_tracker._nodes` were invisible in the D3.js graph

### Data Flow Analysis

```
Meshtastic packet received
    ↓
_on_meshtastic_receive() in rns_bridge.py
    ↓
UnifiedNode.from_meshtastic() creates node
    ↓
node_tracker.add_node(node)  ← Adds to _nodes dict only
    ↓
NetworkTopology has NO edge  ← BUG: Node not in topology graph
```

vs

```
RNS announce received
    ↓
_process_rns_announce() in node_tracker.py
    ↓
node_tracker.add_node(node)
    ↓
node_tracker._network_topology.add_edge()  ← Edge created!
    ↓
NetworkTopology has edge  ← Node appears in D3.js graph
```

## Fix Applied

Modified `add_node()` in `src/gateway/node_tracker.py` to also create topology edges for Meshtastic nodes:

```python
def add_node(self, node: UnifiedNode):
    """Add or update a node"""
    is_new = False
    with self._lock:
        # ... existing merge logic ...

    # Add topology edge for Meshtastic nodes (outside lock to avoid deadlock)
    # This ensures Meshtastic nodes appear in the D3.js topology graph
    if self._network_topology and node.network in ("meshtastic", "both"):
        try:
            self._network_topology.add_edge(
                source_id="local",
                dest_id=node.id,
                hops=node.hops or 0,
                snr=node.snr,
                rssi=node.rssi,
            )
        except Exception as e:
            logger.debug(f"Could not add topology edge for {node.id}: {e}")
```

## Network Devices That Should Appear in Topology

| Device Type | Currently Shown? | Data Source |
|-------------|------------------|-------------|
| **Meshtastic nodes** | YES (after fix) | Radio packets via meshtasticd |
| **RNS nodes** | YES | RNS path table & announces |
| **AREDN nodes** | NO (separate system) | `aredn.py` API client (not integrated) |
| **TCP/IP routers** | NO | Not tracked by MeshForge |

### AREDN Integration Notes

AREDN (Amateur Radio Emergency Data Network) has a comprehensive API:
- **Sysinfo API**: `http://<node>.local.mesh/a/sysinfo?hosts=1&lqm=1`
- **Topology endpoint**: Added in Oct 2024 release (#1637)
- **Link Quality Manager (LQM)**: Shows RF, DTD, TUN links with signal quality

MeshForge has AREDN support in `src/utils/aredn.py`:
- `AREDNClient` - API communication
- `AREDNNode`, `AREDNLink`, `AREDNService` - Data structures
- Used by `launcher_tui/aredn_mixin.py` for TUI menus

**Current status**: AREDN is a separate network from Meshtastic/RNS. The topology graph focuses on the mesh networks MeshForge bridges. AREDN nodes could be integrated as a future enhancement.

### Traffic Inspector (Wireshark-like)

MeshForge includes `monitoring/traffic_inspector.py` for deep packet inspection:
- Protocol-aware packet parsing
- Meshtastic and RNS protocols
- Display filters (e.g., `"mesh.hops > 2"`)
- Path tracing through mesh

This focuses on Meshtastic/RNS mesh traffic, not general TCP/IP.

## Files Modified

| File | Changes |
|------|---------|
| `src/gateway/node_tracker.py` | Added topology edge creation for Meshtastic nodes in `add_node()` |

## Verification

- Python syntax check: PASSED
- Code logic verified against `add_edge()` signature in `network_topology.py`

## References

- [AREDN Tools for Integrators](http://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html)
- [Topologr - AREDN Mesh Topology Explorer](https://www.arednmesh.org/content/topologr-aredn-mesh-topology-and-data-explorer)
- [AREDN GitHub](https://github.com/aredn/aredn)

## Session Entropy

Low - Clear root cause identified and fixed. Well-scoped investigation.
