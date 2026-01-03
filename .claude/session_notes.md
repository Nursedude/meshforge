# MeshForge - Development Session Notes

> **LoRa Mesh Network Development & Operations Suite**
> *Build. Test. Deploy. Monitor.*

## Current Version: 4.1.0

## Project Identity

**Name**: MeshForge
**Tagline**: "LoRa Mesh Network Development & Operations Suite"
**Pillars**: Build. Test. Deploy. Monitor.

### Naming Decision (2026-01-03)
- Considered: MOC (Meshtastic Operations Center), MeshOps, LoRaBase
- Chosen: **MeshForge** - implies building/creating, craftsmanship, professional grade
- "Forge" connects to: heat/radio waves, maker culture, industrial strength

### Application ID
- GTK: `org.meshforge.app`
- Old: `org.meshtastic.installer`

---

## Recent Work Summary

### Session Focus Areas (v4.1.0)
1. **Mesh Network Map** - Interactive Leaflet.js map with node positions
2. **Version Checker** - Auto-update detection for meshtasticd/CLI/firmware
3. **Desktop Integration** - .desktop launcher and install script
4. **/api/nodes/full** - Rich node data API with positions and metrics
5. **Updates Tab** - Web UI for checking component versions

### Previous Session (v4.0.x)
1. Web UI process cleanup and signal handling
2. GTK D-Bus registration timeout fix
3. Radio Configuration panel parsing improvements
4. Config File Manager enhancements
5. Hardware detection without node dependency
6. **Rebrand to MeshForge v4.0.0**

---

## Key Fixes Implemented

### 1. Web UI Process Cleanup (`src/main_web.py`)
**Problem**: GTK wouldn't start after Web UI because processes were lingering.

**Solution**:
- Added `_running_processes` list to track subprocesses
- Added `cleanup_processes()` function with atexit handler
- Added signal handlers for SIGTERM and SIGINT
- Added PID file management (`/tmp/meshtasticd-web.pid`)
- Added `--stop` and `--status` CLI options
- Set `use_reloader=False` to prevent duplicate Flask processes

### 2. GTK D-Bus Timeout Fix (`src/gtk_ui/app.py`)
**Problem**: "Failed to register: Timeout was reached" when running as root.

**Solution**:
- Changed `Gio.ApplicationFlags.FLAGS_NONE` to `Gio.ApplicationFlags.NON_UNIQUE`
- This allows the app to run without requiring D-Bus session bus registration

### 3. Radio Config Parsing (`src/gtk_ui/panels/radio_config.py`)
**Problem**: Radio info fields showing "--" or partial data.

**Solution** - Rewrote `_parse_radio_info()`:
- Multi-pattern Owner line parsing for various formats:
  - `Owner: LongName (ShortName) !nodeId`
  - `Owner: LongName (!nodeId)`
  - `Owner: LongName !nodeId`
- Uses `ast.literal_eval()` for safe Python dict parsing (handles single quotes, True/False)
- Extracts from multiple blocks: My info, Metadata, Nodes in mesh, Preferences
- Flexible regex patterns for both single and double quotes
- Added debug output to terminal for troubleshooting

**Solution** - Rewrote `_parse_and_populate_config()`:
- Field tracking dictionary to prevent duplicate parsing
- Helper functions: `set_dropdown_by_value()`, `extract_value()`
- Section-aware parsing for structured CLI output
- Improved regex patterns for all dropdown values

### 4. Config File Manager (`src/gtk_ui/panels/config.py`)
**Improvements**:
- Added Main Configuration status frame
- Shows config.yaml size and module info
- Shows available.d template count
- Shows config.d active count
- Added `_update_main_config_status()` method
- Added `_on_active_config_selected()` handler

### 5. Hardware Detection (`src/gtk_ui/panels/hardware.py`)
**Improvements**:
- USB device detection via `lsusb` with known vendor:product IDs
- Known devices: CH340, CH9102, CP2102, CP2105, FT232R, FT231X, ESP32-S3, ESP32-S2, nRF52840, RP2040, Arduino
- Serial port detection for GPS modules
- Enhanced I2C device identification (30+ addresses)
- Better interface status with device counts

---

## Architecture Notes

### Meshtastic CLI Integration
- CLI path detection: `/root/.local/bin/meshtastic`, `~/.local/bin/meshtastic`, SUDO_USER paths
- Always uses `--host localhost` to connect via TCP to meshtasticd on port 4403
- Pre-check socket connection before running CLI commands

### CLI Output Formats
The meshtastic CLI outputs Python dict format (not JSON):
- Uses single quotes: `'key': 'value'`
- Uses Python booleans: `True`, `False`, `None`
- Use `ast.literal_eval()` for safe parsing

### Key CLI Commands
- `meshtastic --info` - Radio info, owner, nodes, metadata
- `meshtastic --get lora` - LoRa settings (region, preset, hop_limit)
- `meshtastic --get device` - Device settings (role, rebroadcast_mode)
- `meshtastic --get position` - Position settings (GPS, coordinates)
- `meshtastic --get mqtt` - MQTT configuration
- `meshtastic --nodes` - List mesh nodes
- `meshtastic --sendtext "msg"` - Send broadcast message
- `meshtastic --sendtext "msg" --dest !nodeId` - Send direct message

### Service Management
- meshtasticd runs on TCP port 4403
- Config files: `/etc/meshtasticd/config.yaml`, `/etc/meshtasticd/config.d/*.yaml`
- Available templates: `/etc/meshtasticd/available.d/*.yaml`

---

## Debug Tips

### Radio Config Not Populating
1. Check terminal for `[DEBUG] Raw --info output:` to see CLI response
2. Check `[DEBUG] Parsed radio info:` to see what was extracted
3. Check `[DEBUG] Config fields populated:` for dropdown values

### Web UI Issues
1. Check if already running: `sudo python3 src/main_web.py --status`
2. Stop existing: `sudo python3 src/main_web.py --stop`
3. Check PID file: `cat /tmp/meshtasticd-web.pid`

### GTK Won't Start
1. Kill any lingering processes: `pkill -f main_web.py`
2. Check D-Bus session: GTK uses NON_UNIQUE flag now

---

## Files Modified in v3.2.7

| File | Changes |
|------|---------|
| `src/main_web.py` | Subprocess tracking, signal handlers, PID management, --stop/--status |
| `src/gtk_ui/app.py` | NON_UNIQUE flag for D-Bus |
| `src/gtk_ui/panels/radio_config.py` | Complete parsing rewrite with debug output |
| `src/gtk_ui/panels/config.py` | Main config status, active status |
| `src/gtk_ui/panels/hardware.py` | USB detection, serial detection, I2C identification |
| `README.md` | v3.2.7 features documented |
| `QUICK_START.md` | Web UI section added |

---

## Known Patterns for Future Reference

### Owner Line Patterns (meshtastic --info)
```
Owner: NodeName (SHRT) !abcd1234
Owner: NodeName (!abcd1234)
Owner: NodeName !abcd1234
```

### My info Block
```
My info: {'myNodeNum': 123456789, 'numChannels': 8, ...}
```

### Metadata Block
```
Metadata: {'firmwareVersion': '2.x.x', 'hwModel': 'DEVICE_NAME', ...}
```

### Config Output (--get)
```
lora:
  region: US
  modem_preset: LONG_FAST
  hop_limit: 3
device:
  role: CLIENT
```

---

## Testing Checklist

- [ ] Web UI starts and stops cleanly
- [ ] GTK starts after Web UI stops
- [ ] Radio Config loads all fields when connected
- [ ] Radio Config shows helpful message when not connected
- [ ] Config Manager shows config.yaml status
- [ ] Hardware detection shows USB/Serial/I2C devices
- [ ] No orphan processes after exit

---

## Version History Reference

- **v3.2.7**: Web UI nodes/messages, clean shutdown, D-Bus fix, radio parsing
- **v3.2.6**: System monitor, htop, daemon control
- **v3.2.5**: Keyboard shortcuts, connection error fixes
- **v3.2.4**: Web UI authentication, custom ports
