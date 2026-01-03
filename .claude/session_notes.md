# MeshForge - Development Session Notes

> **LoRa Mesh Network Development & Operations Suite**
> *Build. Test. Deploy. Monitor.*

## Current Version: 4.1.0
## Last Updated: 2026-01-03
## Branch: `claude/continue-previous-work-YB9x9`

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

### Session: 2026-01-03 (v4.1.0) - Map & Updates

**New Features Implemented:**

1. **Mesh Network Map** (`src/main_web.py`)
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

## Testing Checklist for v4.1.0

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
git checkout claude/continue-previous-work-YB9x9

# View recent changes
git log --oneline -10

# Push changes
git push -u origin claude/continue-previous-work-YB9x9
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
- **Branch:** claude/continue-previous-work-YB9x9
- **License:** GPL-3.0

---

*Mahalo for using MeshForge!*
