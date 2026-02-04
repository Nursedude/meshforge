# MeshForge - Development Session Notes

## Current Version: v4.2.0
## Session Date: 2026-02-04
## Branch: `claude/extract-node-tracker-dataclasses-Fiiod`

---

## QUICK RESUME - Start Here

When resuming this project, read this file and `CLAUDE.md` first.

```bash
# 1. Check current status
git status && git log --oneline -10

# 2. Test the application
sudo python3 src/launcher_tui/main.py  # TUI (primary interface)
python3 src/standalone.py               # Zero-dependency RF tools
```

---

## Latest Session Summary (2026-02-04)

### Major Accomplishments - File Size Refactoring

1. **Extracted node_tracker.py dataclasses** (`src/gateway/node_models.py`) - NEW!
   - Created new `node_models.py` with extracted dataclasses (931 lines)
   - Reduced `node_tracker.py` from 1,808 to 911 lines (51% reduction)
   - Both files now under 1,500 line threshold per Issue #6 guidelines
   - Extracted types: Position, PKIKeyState, PKIStatus, AirQualityMetrics,
     HealthMetrics, DetectionSensor, SignalSample, Telemetry, UnifiedNode

### Files Changed
- `src/gateway/node_models.py` - NEW: Extracted dataclasses
- `src/gateway/node_tracker.py` - Refactored to import from node_models.py

### Commit
```
f3827e8 refactor: Extract node_tracker dataclasses to node_models.py
```

---

## Remaining Work (for next session)

### rns_bridge.py (1,991 lines) - Meshtastic Handler Extraction

The Meshtastic handling code is deeply interleaved with RNSMeshtasticBridge class.
Extraction requires creating a MeshtasticHandler class that delegates to shared state.

**Key methods to extract (~400 lines):**
- `_meshtastic_loop` (line 728) - Main thread loop
- `_connect_meshtastic` (line 856) - Connection establishment
- `_disconnect_meshtastic` (line 906) - Disconnection
- `_on_meshtastic_receive` (line 1123) - Message receive handler
- `_poll_meshtastic` (line 1683) - Health checks
- `_handle_connection_lost` (line 1705) - Connection loss handling
- `_update_meshtastic_nodes` (line 1666) - Node tracking
- `_test_meshtastic` (line 1735) - Connection testing
- `send_to_meshtastic` (line 469) - Message sending

**Complexity Note:**
These methods share state like `self._mesh_interface`, `self._connected_mesh`,
`self.node_tracker`, `self.health`, `self.config`. Extraction requires passing
references to shared objects via MeshtasticHandler constructor.

### Other Files Over Threshold (from persistent_issues.md)
- `traffic_inspector.py` (2,194 lines) - Extract dissectors, models, storage
- `launcher_tui/main.py` (1,799 lines) - Extract network tools, web client mixins

---

## Previous Session Summary (2026-01-04)

### Major Accomplishments - Node Map, MQTT & HamClock

1. **HamClock Integration** (`src/gtk_ui/panels/hamclock.py`) - NEW!
   - NEW: Full HamClock panel in GTK navigation
   - NEW: Connection settings (URL, API port 8080, Live port 8081)
   - NEW: Space weather display (SFI, Kp, A index, X-ray, Sunspots, Band Conditions)
   - NEW: Embedded WebKit live view (or browser fallback)
   - NEW: REST API integration with get_sys.txt and get_spacewx.txt endpoints
   - NEW: Settings persistence to ~/.config/meshforge/hamclock.json
   - NEW: Auto-connect on startup if URL is configured

2. **Fixed GTK Node Map** (`src/gtk_ui/panels/map.py`)
   - FIX: NodeMonitor import path corrected for GTK runtime
   - FIX: Browser map now uses actual node data (not empty tracker)
   - FIX: Position parsing handles all coordinate formats (lat/lon and latI/lonI)
   - NEW: Smart node loading - waits up to 10s for MQTT meshes with 100+ nodes
   - NEW: Persistent NodeMonitor with auto-reconnect
   - NEW: `sync_nodes()` refreshes from interface on each update

2. **MQTT Visualization** (`src/gtk_ui/panels/map.py`, `web/node_map.html`)
   - NEW: `via_mqtt` flag tracked in GeoJSON properties
   - NEW: MQTT nodes shown in **purple** on map
   - NEW: MQTT count displayed in status bar
   - NEW: MQTT badge in node popup
   - NEW: Node role displayed in popup

3. **MQTT Broker Presets** (`src/gtk_ui/panels/radio_config.py`)
   - NEW: Preset dropdown in Radio Config → MQTT Settings
   - Meshtastic Public (mqtt.meshtastic.org - meshdev/large4cats)
   - Hawaii Mesh Big Island (gt.wildc.net:1884)
   - Chicagoland Mesh (mqtt.chimesh.org)
   - Boston Mesh (mqttmt01.bostonme.sh)
   - MichMesh (mqtt.michmesh.net)
   - NEW: "Apply All MQTT Settings" button for one-click config

4. **Diagnostic Tools**
   - NEW: `diagnose_nodes.py` - Debug script showing node loading over time
   - Shows node count growing second-by-second
   - Identifies MQTT vs local RF nodes
   - Shows position data for debugging

### Map Color Legend
| Color | Meaning |
|-------|---------|
| Green | Online (local RF) |
| Purple | Via MQTT |
| Orange | Gateway/Router |
| Red | Offline |

### Files Modified This Session
- `src/gtk_ui/panels/hamclock.py` - NEW: HamClock integration panel
- `src/gtk_ui/app.py` - Added HamClock navigation and page
- `src/gtk_ui/panels/map.py` - Major fixes for node display, browser map fix
- `src/gtk_ui/panels/radio_config.py` - MQTT presets
- `src/monitoring/node_monitor.py` - sync_nodes(), position parsing, connection fixes
- `web/node_map.html` - MQTT visualization
- `diagnose_nodes.py` - New diagnostic tool

### Key Commits
```
0aaf80e feat: Add HamClock integration panel
a95c3ab fix: More robust connection handling to prevent BrokenPipeError
fea5ddb fix: Handle None values in node parsing to prevent crashes
3809123 fix: Browser map now shows all 80+ nodes
a9e41fb feat: Add regional mesh MQTT broker presets
f77b72a feat: MQTT node visualization on map
6b6da57 feat: Add MQTT broker presets with one-click configuration
4353721 feat: Smart node loading for large MQTT meshes
e3a78df fix: Improve position parsing to handle all coordinate formats
8ba3d51 feat: Add sync_nodes() to refresh node list from interface
44319c1 fix: Browser map now uses actual node data from NodeMonitor
e9c1889 fix: Correct NodeMonitor import path for GTK runtime
8963be4 fix: Use persistent NodeMonitor with delay for node loading
```

---

## Hawaii Mesh Stats
- 150+ nodes across Hawaiian islands
- MQTT broker: gt.wildc.net:1884
- Credentials: mesh_publish / mesh.kula.smoke
- Active mesh with good MQTT coverage

---

## Known Issues / Future Work

1. **WebKit not available** - Map opens in browser instead of embedded
2. **Connection recovery** - BrokenPipeError logs from meshtastic lib (non-fatal, auto-recovers)
3. **Node tracker import warning** - Relative import warning (non-critical)

### Planned Features
- RNS bridge completion
- More RF calculation tools
- Artifacts for Claude.ai (frequency calculator, link budget, maidenhead)

---

## Development Philosophy

From `.claude/dude_ai_university.md`:
1. **Reliability** - It must work consistently
2. **Functionality** - Complete features before adding new ones
3. **Maintainability** - Clean, documented code
4. **Architecture** - Consistent patterns
5. **Roadmap** - Clear development path

"Cross that bridge when we reach it" - Focus on working code now, optimize later.

---

## References

- [Meshtastic MQTT Docs](https://meshtastic.org/docs/software/integrations/mqtt/)
- [MeshSense](https://affirmatech.com/meshsense) - Network monitoring reference
- Default MQTT credentials: meshdev / large4cats

---

*73 de Dude AI - Session saved 2026-01-04*
