# Session Notes: Map Data Collection Fixes
**Date**: 2026-02-03
**Branch**: `claude/fix-tui-crashes-E6yK7`

## Summary

Fixed multiple issues causing MapDataCollector to return fewer nodes than expected (150 vs 282+).

## Commits Pushed

| Commit | Description |
|--------|-------------|
| `f85a77f` | Make meshtasticd host/port configurable in MapDataCollector |
| `a1cbbe3` | Improve RNS node collection (24h cache, direct query) |
| `4bb4993` | Fix AUTO mode to prefer TCP over serial when meshtasticd running |

## Issues Fixed

### 1. MapDataCollector Hardcoded Port
**Symptom**: meshtasticd:0 even when meshtasticd running
**Root Cause**: Host/port hardcoded to localhost:4403, not configurable
**Fix**: Added settings for meshtasticd_host and meshtasticd_port

### 2. AUTO Mode Priority Wrong
**Symptom**: "Could not exclusively lock port /dev/ttyUSB0"
**Root Cause**: AUTO mode tried SERIAL before TCP, even when meshtasticd held the port
**Fix**: Changed priority: TCP first (if available), then SERIAL

### 3. RNS Cache Too Short
**Symptom**: RNS nodes disappearing after 1 hour
**Root Cause**: DEFAULT_RNS_CACHE_MAX_AGE_HOURS = 1
**Fix**: Increased to 24 hours

### 4. No Direct RNS Query
**Symptom**: rns_direct:0 even with rnsd running
**Root Cause**: No code to query rnsd path table directly
**Fix**: Added _collect_rns_direct() method + NomadNet peer reading

## Results

**Before fixes:**
```
meshtasticd: 0, node_tracker: 150, rns_direct: 0
Total: 150 nodes
```

**After fixes:**
```
meshtasticd: 132, node_tracker: 300, rns_direct: 0
Total: 282 nodes (278 Meshtastic + 4 RNS)
```

## Notes

- RNS path table was empty (0 entries) - rnsd needs time to discover destinations
- User has 260 total Meshtastic nodes, 132 have valid GPS positions
- rnsd was crash-looping (restart count 1780) - user fixed by stop/start

## TUI Navigation

- **Meshtastic CLI**: Mesh Networks → Meshtastic
- **Prometheus**: Dashboard → Historical Trends → Prometheus Server
- **Connection Settings**: Settings → Connection

## Files Modified

- `src/utils/map_data_collector.py` - Host/port config, RNS improvements
- `src/utils/meshtastic_connection.py` - AUTO mode priority fix
- `src/launcher_tui/settings_menu_mixin.py` - Persist connection settings

## Next Session

- Monitor if rns_direct starts returning nodes once path table populates
- Consider adding RNS node position storage/discovery
- Test full TUI map generation with new collection
