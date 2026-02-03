# MeshForge - Development Session Notes

> **LoRa Mesh Network Development & Operations Suite**
> *Build. Test. Deploy. Monitor.*

## Current Version: 0.5.0-beta
## Last Updated: 2026-02-03
## Branch: `main` (PRs merged)

---

## Active Research Links

- **RNS Gateway**: https://github.com/landandair/RNS_Over_Meshtastic
- **MeshForge**: https://github.com/Nursedude/meshforge

---

## Quick Resume - Start Here

```bash
# 1. Check current state
git status && git log --oneline -5

# 2. Launch TUI (PRIMARY INTERFACE)
sudo python3 src/launcher_tui/main.py

# 3. Test MQTT Setup (new feature)
# TUI: Configuration → Service Config → MQTT Setup

# 4. Test version checker standalone
python3 src/updates/version_checker.py

# 5. Verify MQTT messages
mosquitto_sub -h localhost -t 'msh/#' -v
```

---

## Session: 2026-02-03 - MQTT Multi-Consumer Architecture

**Major PRs Merged:**

| PR | Description |
|----|-------------|
| #670 | MQTT → WebSocket bridge for web UI access |
| #669 | meshtasticd architecture validation script |
| #668 | MQTT mixin API fix |
| #667 | MQTT setup wizard and local broker mode |
| #666 | Local MQTT broker support for multi-consumer |
| #664 | WebSocket broadcast to Gateway Bridge |
| #663 | MeshForge self-update feature |

**Architecture Completed:**
```
meshtasticd
    ├── TCP:4403 → Gateway Bridge → RNS → WebSocket:5001
    └── MQTT → mosquitto:1883 → MQTT Monitor
                             → meshing-around
                             → Grafana/InfluxDB
```

**New TUI Features:**
- MQTT Setup Wizard (Configuration → Service Config → MQTT Setup)
- Local/Public broker toggle (MQTT Monitor → Configure)
- WebSocket Bridge for web UI (MQTT Monitor → WebSocket Bridge)
- MeshForge self-update (Updates → Update MeshForge)

---

## Project Identity

**Name**: MeshForge
**Tagline**: "LoRa Mesh Network Development & Operations Suite"
**Pillars**: Build. Test. Deploy. Monitor.

### Application ID
- GTK: `org.meshforge.app`
- Old: `org.meshtastic.installer`

---

## Recent Work Summary

### Session: 2026-02-02 - Map Features & TUI Integration

**Features Implemented:**

1. **Node Movement Trails** (`web/node_map.html`)
   - Added trajectory visualization for individual nodes
   - "Show Trail (24h)" button in node popup
   - Animated trail lines with time markers
   - Trail info panel showing duration and point count
   - Integration with existing `/api/nodes/trajectory/<id>` API

2. **Signal Strength Heatmap** (Already existed, verified)
   - Leaflet.heat layer using real SNR data
   - Toggle via checkbox in control panel
   - Color gradient: blue (weak) to green (excellent)

3. **Network Topology** (Already existed, verified)
   - D3.js force-directed graph
   - View toggle between Map and Topology
   - Network-colored nodes and links

4. **One-Click Updates Mixin** (`src/launcher_tui/updates_mixin.py`)
   - Check for available updates (meshtasticd, CLI, firmware)
   - One-click update execution for meshtasticd and CLI
   - Version comparison with installed vs latest
   - Integrated into TUI Configuration menu

5. **MQTT Monitoring Mixin** (`src/launcher_tui/mqtt_mixin.py`)
   - Start/stop MQTT subscriber from TUI
   - Configure MQTT broker settings
   - View discovered nodes and statistics
   - Export MQTT data to file
   - Integrated into TUI Mesh Networks menu

**Files Added:**
| File | Description |
|------|-------------|
| `src/launcher_tui/updates_mixin.py` | One-click software update management |
| `src/launcher_tui/mqtt_mixin.py` | MQTT monitoring control |

**Files Modified:**
| File | Change |
|------|--------|
| `web/node_map.html` | Node trails CSS, trails layer, trajectory functions |
| `src/launcher_tui/main.py` | Added UpdatesMixin and MQTTMixin, menu entries |

**TUI Menu Additions:**
- Configuration > Software Updates (one-click updates)
- Mesh Networks > MQTT Monitor (nodeless observation)

**Map Features Summary:**
- Node trails: Click "Show Trail (24h)" in node popup
- Signal heatmap: Toggle "Signal heatmap" checkbox
- Network topology: Click "Topology" view toggle
- All features accessible from web map at `/` endpoint

---

### Session: 2026-02-02 (earlier) - Device Persistence & Node State Machine

**Features Implemented:**

1. **Node State Machine** (`src/gateway/node_state.py`)
   - Granular node states beyond simple online/offline
   - States: DISCOVERED, ONLINE, WEAK_SIGNAL, INTERMITTENT, SUSPECTED_OFFLINE, OFFLINE, UNREACHABLE, STALE_CACHE
   - State transition tracking with history
   - Signal quality-based state transitions (weak signal detection)
   - Timeout-based transitions (suspect -> offline)
   - Integrated into UnifiedNode dataclass

2. **Device Persistence** (`src/utils/device_persistence.py`)
   - Remembers last successfully connected device
   - Auto-reconnect to last known device on startup
   - Connection history tracking (success/fail)
   - Configurable auto-reconnect (enable/disable)
   - Preferred connection type setting
   - Reconnect config generation for DeviceController

3. **DeviceController Integration** (`src/utils/device_controller.py`)
   - Auto-connect now tries last known device first
   - Records successful/failed connection attempts
   - Integrated with DevicePersistence singleton

4. **UnifiedNode Enhancements** (`src/gateway/node_tracker.py`)
   - Added state machine to UnifiedNode
   - New properties: state, state_name, state_icon
   - check_timeout() method for state machine updates
   - State history available via get_state_history()
   - State data included in to_dict() serialization
   - State machine persisted in node cache

**Files Added:**
| File | Description |
|------|-------------|
| `src/gateway/node_state.py` | NodeState enum and NodeStateMachine class |
| `src/utils/device_persistence.py` | Device connection persistence |
| `tests/test_node_state.py` | Tests for node state machine |
| `tests/test_device_persistence.py` | Tests for device persistence |

**Files Modified:**
| File | Change |
|------|--------|
| `src/gateway/node_tracker.py` | State machine integration |
| `src/utils/device_controller.py` | Persistence integration |

**Node State Transitions:**
```
STALE_CACHE --> (response) --> DISCOVERED/ONLINE
DISCOVERED --> (good signal) --> ONLINE
ONLINE --> (weak signal) --> WEAK_SIGNAL
WEAK_SIGNAL --> (strong signal) --> ONLINE
ANY_ACTIVE --> (no response 5min) --> SUSPECTED_OFFLINE
SUSPECTED_OFFLINE --> (no response 1hr) --> OFFLINE
OFFLINE --> (response) --> ONLINE
```

**Usage Examples:**

```python
# Node state machine
node = UnifiedNode(id="test", network="meshtastic")
print(node.state_name)  # "Cached"
node.update_seen()
print(node.state_name)  # "Online"
node.record_signal_quality(snr=-15.0)  # Weak signal
print(node.state_name)  # May become "Weak Signal"

# Device persistence
from utils.device_persistence import get_device_persistence
persistence = get_device_persistence()
if persistence.has_last_device():
    config = persistence.get_reconnect_config()
    # Use config with DeviceController
```

---

### Session: 2026-01-15 - Auto-Review & Stability Milestone

**Milestone: MeshForge GTK running stable for several hours**

**Auto-Review Results:**
- Files scanned: 197
- Total issues: 2 (both false positives)
- Security: 0, Redundancy: 0, Performance: 2 (FP), Reliability: 0

**False Positives Identified:**

1. `dashboard.py:43` - `GLib.timeout_add(500, self._initial_refresh)`
   - Flagged as "timer may leak without cleanup"
   - Reality: One-time startup timer, fires once then done
   - No action needed

2. `tools.py:846` - `GLib.timeout_add(50, self._scroll_to_end)`
   - Flagged as "timer may leak without cleanup"
   - Reality: Brief 50ms fire-and-forget with widget guards
   - No action needed

**Future Enhancement (auto_review.py):**

The `glib_timeout_no_cleanup` pattern should be improved to recognize:
- One-time timers (methods that return `False` or don't loop)
- Very short timers (<100ms) used for UI scheduling/deferral
- Timers with existing widget guards (hasattr checks)

These are fire-and-forget patterns, not the long-running periodic timers the rule is designed to catch.

---

### Session: 2026-01-06/07 - GTK Stabilization & HamClock

**Key Accomplishments:**

1. **NomadNet Terminal Fix** (`src/gtk_ui/panels/rns.py`)
   - Terminal was closing immediately after NomadNet exit
   - Wrapped command in bash with "Press Enter to close..." message
   - Uses xterm's native `-hold` flag when available
   - Fixed terminal detection for multiple emulators (lxterminal, xfce4, gnome, konsole, xterm)

2. **Region Dropdown** (`src/gtk_ui/panels/radio_config_simple.py`)
   - Converted region from display-only label to configurable dropdown
   - All 22 Meshtastic regions: US, EU_433, EU_868, CN, JP, ANZ, KR, TW, RU, IN, NZ_865, TH, LORA_24, UA_433, UA_868, MY_433, MY_919, SG_923, PH, UK_868, SINGAPORE
   - Warning tooltip about local radio regulations compliance

3. **Radio Config - Load ALL Settings on Refresh**
   - LoRa: Region, Preset, Hop Limit, TX Power, TX Enabled, Channel Num
   - Advanced LoRa: Bandwidth, SF, CR, Freq Offset, RX Boost, Duty Cycle
   - Device: Role, Rebroadcast, Node Info, Buzzer, LED
   - Position: GPS Mode, Broadcast interval, Smart, Fixed
   - Display: Screen timeout, Flip, Units, OLED type
   - Bluetooth: Enabled, Mode, PIN
   - Network: WiFi enabled, SSID, NTP server
   - Channel: Name, Uplink, Downlink
   - All values populate UI widgets AND show in info display

4. **HamClock Web Setup Button** (`src/gtk_ui/panels/hamclock.py`)
   - Added prominent "Open Web Setup" button (blue/suggested-action style)
   - Opens `http://localhost:8081/live.html` for configuration
   - Removed misleading nano edit button (eeprom is binary, not text)
   - HamClock uses web interface for all configuration

5. **HamClock Headless Pi Setup** (documented in `.claude/research/hamclock.md`)
   - Pre-built framebuffer packages need `libbcm_host.so` (unavailable on arm64)
   - Solution: Build from source with `make hamclock-web-1600x960`
   - Systemd service runs as user (needs HOME env and ~/.hamclock write access)
   - Ports: 8081 (live view), 8082 (REST API)
   - Fixed permission issues, corrupted config recovery

6. **RNS Reinitialize Loop Fix** (`src/gateway/rns_bridge.py`)
   - Fixed "Attempt to reinitialise Reticulum" error spam
   - Sets `_rns_init_failed_permanently = True` when catching reinitialize exception
   - Prevents infinite retry loop

**Files Modified:**
| File | Change |
|------|--------|
| `src/gtk_ui/panels/rns.py` | NomadNet terminal fix |
| `src/gtk_ui/panels/radio_config_simple.py` | Region dropdown, load all settings |
| `src/gtk_ui/panels/hamclock.py` | Web setup button |
| `src/gateway/rns_bridge.py` | RNS reinitialize fix |
| `.claude/research/hamclock.md` | NEW - Headless Pi setup docs |

---

### Session: 2026-01-03 (v4.1.0) - Map, Updates & Calculator

**New Features Implemented:**

1. **Frequency Slot Calculator Redesign** (`src/gtk_ui/panels/radio_config.py`)
   - Dropdown-based interface matching Meshtastic docs
   - All 22 Meshtastic regions supported (US, EU_433, EU_868, CN, JP, ANZ, KR, TW, RU, IN, NZ_865, TH, LORA_24, UA_433, UA_868, MY_433, MY_919, SG_923, PH, UK_868, SINGAPORE)
   - Auto-calculated fields:
     - Default Frequency Slot (from LongFast hash)
     - Number of slots (from region bandwidth)
     - Frequency of slot (MHz)
   - Channel Preset dropdown for quick slot selection

2. **Mesh Network Map** (`src/main_web.py`)
   - Interactive Leaflet.js map with dark CARTO tiles
   - Color-coded node markers:
     - Green = My node
     - Blue = Online (< 1 hour)
     - Orange = Stale (1-24 hours)
     - Gray = Offline (> 24 hours)
   - Click popups with node details (battery, SNR, hardware, altitude)
   - Node list below map - click to focus
   - Auto-zoom to fit all nodes with positions

2. **Version Checker** (`src/updates/version_checker.py`)
   - Checks installed versions of:
     - meshtasticd (from dpkg/binary)
     - Meshtastic CLI (from pipx)
     - Node firmware (from connected device via CLI)
   - Compares against latest from GitHub/PyPI
   - Caches results for 1 hour
   - Shows update availability

3. **Updates Tab** in Web UI
   - Component version table
   - Update status badges (Up to date / Update Available)
   - Update command instructions

4. **Desktop Integration**
   - `.desktop` launcher (`meshforge.desktop`)
   - SVG icon (`assets/meshforge-icon.svg`)
   - Install script (`scripts/install-desktop.sh`)

5. **New API Endpoint** (`/api/nodes/full`)
   - Uses NodeMonitor for rich data
   - Returns positions (lat/lon/altitude)
   - Returns metrics (battery, voltage, temp, humidity)
   - Returns last heard timestamps

### Previous Session (v4.0.x)
1. Web UI process cleanup and signal handling
2. GTK D-Bus registration timeout fix
3. Radio Configuration panel parsing improvements
4. Config File Manager enhancements
5. Hardware detection without node dependency
6. Rebrand to MeshForge v4.0.0
7. Security hardening (subprocess.run, no shell=True)
8. Frequency Slot Calculator with djb2 hash

---

## Files Modified/Added in v4.1.0

| File | Status | Description |
|------|--------|-------------|
| `src/gtk_ui/panels/radio_config.py` | Modified | Frequency slot calculator redesign, all 22 regions |
| `src/main_web.py` | Modified | Map tab, Updates tab, /api/nodes/full, /api/versions |
| `src/updates/__init__.py` | New | Module exports |
| `src/updates/version_checker.py` | New | Version detection logic |
| `src/monitoring/__init__.py` | Modified | Export NodePosition |
| `assets/meshforge-icon.svg` | New | App icon (mesh network design) |
| `meshforge.desktop` | New | Desktop launcher with actions |
| `scripts/install-desktop.sh` | New | Desktop install script |
| `src/__version__.py` | Modified | v4.1.0 changelog |
| `README.md` | Modified | v4.1.0 features documented |

---

## Architecture Notes

### Web UI Map Implementation
```
Browser
  ↓ clicks "Map" tab
JavaScript
  ↓ initMap() - creates Leaflet map with CARTO dark tiles
  ↓ refreshMap() - fetches /api/nodes/full
Flask API
  ↓ get_nodes_full() - uses NodeMonitor
NodeMonitor
  ↓ connects to meshtasticd:4403 via TCP
  ↓ gets all node data including positions
Returns JSON with:
  - nodes[] with position, metrics, last_heard
  - total_nodes, nodes_with_position counts
```

### Version Checker Architecture
```
get_version_summary()
  ├── get_meshtasticd_version() - dpkg -s meshtasticd
  ├── get_meshtastic_cli_version() - meshtastic --version
  ├── get_node_firmware_version() - meshtastic --info (JSON parse)
  ├── get_latest_meshtasticd_version() - GitHub API
  ├── get_latest_meshtastic_cli_version() - PyPI API
  └── compare_versions() - tuple comparison
```

### Meshtastic CLI Integration
- CLI path detection: `/root/.local/bin/meshtastic`, `~/.local/bin/meshtastic`, SUDO_USER paths
- Always uses `--host localhost` to connect via TCP to meshtasticd on port 4403
- Pre-check socket connection before running CLI commands

### Key CLI Commands
- `meshtastic --info` - Radio info, owner, nodes, metadata
- `meshtastic --nodes` - List mesh nodes
- `meshtastic --get lora` - LoRa settings
- `meshtastic --sendtext "msg"` - Send broadcast message

---

## Testing Checklist

### v4.1.x GTK Stabilization
- [x] NomadNet terminal stays open after exit
- [x] Region dropdown shows all 22 regions
- [x] Radio config loads ALL settings on refresh
- [x] HamClock "Open Web Setup" opens browser to :8081
- [x] HamClock running headless on arm64 Pi
- [ ] RNS reinitialize loop no longer spams errors

### v4.1.0 Features
- [ ] Web UI Map tab loads without errors
- [ ] Map shows nodes with GPS positions
- [ ] Map popups display correct node details
- [ ] Click node in list focuses map on that node
- [ ] Updates tab shows component versions
- [ ] Version checker compares installed vs latest correctly
- [ ] Desktop launcher installs correctly
- [ ] Desktop icon appears in Raspberry Pi menu
- [ ] All existing functionality (service, config, radio) still works

---

## Roadmap

### Completed
- [x] v4.0.0 - MeshForge Rebrand
- [x] v4.0.1 - Security hardening, frequency calculator
- [x] v4.1.0 - Map, version checker, desktop integration

### Next Steps (v4.2+)
- [x] Node movement trails on map (position history)
- [x] Signal strength visualization/heatmap
- [x] One-click update execution (run apt/pipx from UI)
- [ ] Firmware flashing integration
- [x] MQTT integration for remote monitoring
- [x] Network topology visualization (node connections)

---

## Debug Tips

### Map Not Loading
1. Open browser console (F12) for JavaScript errors
2. Check if `/api/nodes/full` returns data
3. Verify meshtasticd is running on port 4403
4. Check if nodes have GPS positions set

### Version Checker Issues
1. Run standalone: `python3 src/updates/version_checker.py`
2. Check network connectivity to GitHub/PyPI
3. Look for SSL certificate issues

### Web UI Issues
1. Check if already running: `sudo python3 src/main_web.py --status`
2. Stop existing: `sudo python3 src/main_web.py --stop`
3. Check PID file: `cat /tmp/meshtasticd-web.pid`

### GTK Won't Start
1. Kill lingering processes: `pkill -f main_web.py`
2. GTK uses NON_UNIQUE flag (no D-Bus registration needed)

---

## Git Commands

```bash
# Current branch
git checkout claude/fix-address-in-use-qYem5

# View recent changes
git log --oneline -10

# Push changes
git push -u origin claude/fix-address-in-use-qYem5
```

---

## Version History Reference

| Version | Date | Key Features |
|---------|------|--------------|
| **4.1.0** | 2026-01-03 | Mesh Network Map, Version Checker, Desktop Integration |
| 4.0.1 | 2026-01-03 | Security hardening, Frequency Calculator |
| 4.0.0 | 2026-01-03 | MeshForge Rebrand |
| 3.2.7 | 2026-01-02 | Web UI, Dashboard, Nodes/Messages |
| 3.2.x | 2026-01-02 | GTK fixes, Radio Config parsing |

---

## Contact / Repository

- **GitHub:** https://github.com/Nursedude/meshforge
- **Branch:** claude/fix-address-in-use-qYem5
- **Callsign:** WH6GXZ
- **License:** GPL-3.0

---

## HamClock Quick Reference

```bash
# Service
sudo systemctl status hamclock
sudo systemctl restart hamclock

# URLs (replace with your Pi IP)
Live View: http://192.168.x.x:8081/live.html
REST API:  http://192.168.x.x:8082/

# Logs
journalctl -u hamclock -n 50 --no-pager

# Config location
~/.hamclock/eeprom (binary - use web UI to configure)
```

---

*Mahalo for using MeshForge!* 🤙
