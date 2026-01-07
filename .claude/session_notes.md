# MeshForge - Development Session Notes

> **LoRa Mesh Network Development & Operations Suite**
> *Build. Test. Deploy. Monitor.*

## Current Version: 0.4.3-beta
## Last Updated: 2026-01-07
## Branch: `claude/fix-address-in-use-qYem5`

---

## Active Research Links

- **RNS Gateway**: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway
- **MeshForge**: https://github.com/Nursedude/meshforge

---

## Quick Resume - Start Here

```bash
# 1. Check current state
git status && git log --oneline -5

# 2. Test the Web UI with Map
sudo python3 src/main_web.py
# Then open http://localhost:8080 and click the "Map" tab

# 3. Test GTK UI
sudo python3 src/main_gtk.py

# 4. Test version checker standalone
python3 src/updates/version_checker.py

# 5. Install desktop integration (optional)
sudo ./scripts/install-desktop.sh
```

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
- [ ] Node movement trails on map (position history)
- [ ] Signal strength visualization/heatmap
- [ ] One-click update execution (run apt/pipx from UI)
- [ ] Firmware flashing integration
- [ ] MQTT integration for remote monitoring
- [ ] Network topology visualization (node connections)

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
