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
