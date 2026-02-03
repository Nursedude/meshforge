# Session Notes: Grafana & Metrics Wiring
**Date:** 2026-02-03
**Branch:** `claude/fix-tui-crashes-g75S5`
**Session ID:** session_01XDC6qb32PK15Bfm3cxLYJU

## Commits This Session (7 total)

| Commit | Description |
|--------|-------------|
| `a97cd90` | fix: Sync version fallbacks to 0.5.0-beta |
| `4c72010` | fix: Wire Prometheus metrics to real data sources |
| `b86aeef` | feat: Add Grafana Dashboards menu to TUI |
| `fcde38b` | feat: Wire TrafficInspector to live packet capture |
| `6a5904a` | feat: Add Data Path Diagnostic to TUI Dashboard |
| `ecdc64b` | fix: Update Grafana install instructions for RPi |
| `504364e` | Merge origin/main - resolve conflict |
| `45c125b` | feat: Add JSON API endpoints for Grafana Infinity |

## What Was Fixed

1. **Version mismatch** - All fallback versions synced to 0.5.0-beta
2. **Prometheus metrics showing 0** - Now pulls from MapDataCollector + service_check
3. **Grafana not in TUI** - Added menu at Dashboard → Historical Trends → Grafana
4. **Traffic Inspector had no data** - Wired to meshtastic.receive pubsub
5. **No way to diagnose data flow** - Added Data Path Diagnostic
6. **Grafana install failed on RPi** - Fixed instructions (GPG key method)
7. **Grafana needed Prometheus middleware** - Added direct JSON API endpoints

## Outstanding Issues (Next Session)

### 1. Network Topology Statistics - Not Working
- Location: Maps & Visualization → Network Topology
- Symptom: Unknown - user reported issue but no details yet
- Action: Run and capture error output

### 2. Grafana Running But No Data
- Grafana is active (systemd shows running)
- But dashboards show no data
- **Root cause likely:** MeshForge metrics server not started

### 3. Data Path Not Verified
- User didn't run the Data Path Diagnostic yet
- This will show exactly where data flow breaks:
  - meshtasticd TCP (4403)
  - meshtastic CLI
  - meshtastic Python API
  - pubsub listeners
  - MapDataCollector
  - RNS paths

## Next Session Checklist

1. **Start MeshForge metrics server**
   - TUI: Dashboard → Historical Trends → Prometheus Server → Start
   - Verify: `curl http://localhost:9090/api/json/metrics`

2. **Run Data Path Diagnostic**
   - TUI: Dashboard → Data Path Check
   - Paste output to see which sources are failing

3. **Test JSON endpoints in Grafana**
   - Install Infinity plugin
   - Add data source: `http://localhost:9090`
   - Query: `/api/json/metrics` or `/api/json/nodes`

4. **Debug Network Topology**
   - TUI: Maps & Visualization → Network Topology
   - Capture any error messages

## Services Status (from user)

```
meshtasticd: active (running) since Sat 2026-01-31 - 2 days
rnsd: active (running) since Mon 2026-02-02 - 3 hours
grafana-server: active (running)
Port 4403: LISTEN (meshtasticd TCP)
```

## Key Files Modified

- `src/utils/metrics_export.py` - JSON API endpoints, real data sources
- `src/launcher_tui/metrics_mixin.py` - Grafana menu, install instructions
- `src/launcher_tui/main.py` - Data Path Diagnostic
- `src/launcher_tui/traffic_inspector_mixin.py` - Live capture toggle
- `src/monitoring/traffic_inspector.py` - Global inspector, pubsub hook

## Quick Test Commands

```bash
# Test metrics server
curl http://localhost:9090/api/json/metrics

# Check meshtasticd connection
meshtastic --host localhost --info

# Check services
systemctl status meshtasticd rnsd grafana-server

# Run TUI
sudo python3 src/launcher_tui/main.py
```

## Notes

- User is on Raspberry Pi (Debian minimal)
- Wireshark shows no traffic either - suggests data isn't flowing at all
- 282 nodes were collected in previous session - data exists somewhere
- Need to trace why MapDataCollector returns 0 when services are running
