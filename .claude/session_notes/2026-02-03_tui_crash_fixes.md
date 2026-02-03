# Session Notes: TUI Crash Fixes
**Date**: 2026-02-03
**Branch**: `claude/review-notes-sync-repo-orTBf`

## Summary

Fixed multiple TUI crashes and display corruption issues. Traffic Inspector and Maps now functional.

## Commits Pushed

| Commit | Description |
|--------|-------------|
| `844f5a6` | Add exception handling to Traffic Inspector TUI methods |
| `d179dd7` | Add height/width kwargs to DialogBackend methods |
| `37b138a` | Suppress stderr/logging during TUI to prevent display corruption |

## Issues Fixed

### 1. Traffic Inspector Crash
**Symptom**: TypeError on menu open
**Root Cause**: `DialogBackend.menu()` didn't accept `height`/`width` kwargs
**Fix**: Added optional height/width/list_height params to all DialogBackend methods

### 2. TUI Display Corruption
**Symptom**: Serial port errors corrupting whiptail display
**Root Cause**: stderr/logging output during TUI operation
**Fix**: Redirect stderr to `~/.cache/meshforge/logs/tui_errors.log` and set logging to CRITICAL

### 3. Map Data Collection
**Status**: Working - 150 nodes collected from node_cache.json
**Sources**: meshtasticd:0, mqtt:0, node_tracker:150, aredn:0
**RNS nodes**: 4 nodes with valid coordinates

## Known Issues (Not Fixed This Session)

### Map Output Path (MF001 variant)
**Symptom**: Map HTML written to `/root/.local/share/` when running with sudo
**Workaround**: Use `/tmp/test_map.html` or run map server
**Root Cause**: Needs investigation - `get_real_user_home()` may not be called in all code paths

### Node Cache Age
- `node_cache.json` dated Jan 29 (5 days old)
- Still being read despite 48hr default max age
- May need cache refresh mechanism

## Test Commands

```bash
# Test Traffic Inspector
sudo python3 -c "
import sys; sys.path.insert(0, 'src')
from monitoring.traffic_inspector import TrafficInspector
t = TrafficInspector()
print(t.get_capture_stats())
"

# Test Map Data Collection
sudo python3 -c "
import sys; sys.path.insert(0, 'src')
from utils.map_data_collector import MapDataCollector
c = MapDataCollector()
data = c.collect()
print(f'Nodes: {len(data[\"features\"])}')
"

# Generate test map
sudo python3 -c "
import sys, json; sys.path.insert(0, 'src')
from utils.map_data_collector import MapDataCollector
from pathlib import Path
c = MapDataCollector()
g = c.collect()
t = Path('web/node_map.html').read_text()
t = t.replace('</body>', f'<script>window.meshforgeData={json.dumps(g)};</script></body>')
Path('/tmp/test_map.html').write_text(t)
print('Map: /tmp/test_map.html')
"
```

## User Environment

- Location: Hawaii (Big Island)
- Services: meshtasticd, rnsd, NomadNet
- Nodes: 150 Meshtastic + 4 RNS
- Installation: /opt/meshforge

## Next Session Priorities

1. Investigate map output path issue with sudo
2. Consider cache refresh mechanism for stale node data
3. Test full TUI workflow after fixes merged to main
