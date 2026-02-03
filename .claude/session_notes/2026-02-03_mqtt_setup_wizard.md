# Session Notes: MQTT Setup Wizard

**Date**: 2026-02-03
**Branch**: `claude/fix-mesh-message-display-lKBWq`
**Status**: Ready for commit

## Context

Continuation of local MQTT integration work from previous session. The PR #666 (local MQTT broker support) was merged. This session adds TUI automation for the MQTT setup process.

## Completed This Session

### 1. MQTT Setup Wizard in Service Menu
**File**: `src/launcher_tui/service_menu_mixin.py`

Added comprehensive MQTT setup wizard accessible from:
`Configuration → Service Config → MQTT Setup`

Features:
- **Install mosquitto**: Detects if installed, offers one-click installation
- **Start mosquitto service**: Enables and starts the systemd service
- **Configure meshtasticd**: Automatically configures:
  - `mqtt.enabled = true`
  - `mqtt.address = localhost`
  - `mqtt.json_enabled = true`
  - Primary channel uplink enabled
- **Auto-detect channel**: Parses channel name from meshtasticd for correct topic

Methods added:
```python
_mqtt_setup_wizard()           # Main wizard flow
_is_mosquitto_installed()      # Check installation
_install_mosquitto()           # apt install mosquitto
_ensure_mosquitto_running()    # systemd enable/start
_auto_detect_primary_channel() # Get channel name for topic
_configure_meshtasticd_mqtt_local()  # CLI configuration
```

### 2. Local/Public Mode Toggle in MQTT Monitor
**File**: `src/launcher_tui/mqtt_mixin.py`

Enhanced MQTT monitoring menu with:
- **Quick mode switch**: "Use Local Broker" / "Use Public Broker" options
- **Auto-detection**: Detects channel name for local topic construction
- **Mode display**: Menu shows current mode (Local/Public) and broker

Changes:
- Updated `_configure_mqtt()` with local/public quick options
- Added `_detect_local_channel()` helper method
- Updated `_mqtt_menu()` to show current mode in subtitle

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│                          MeshForge TUI                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Service Config Menu                    MQTT Monitor Menu            │
│  ├── Service Status                     ├── Status                  │
│  ├── Manage meshtasticd                 ├── Start/Stop Subscriber   │
│  ├── Manage rnsd                        ├── Configure (Local/Public)│
│  ├── Install meshtasticd                │   ├── Use Local Broker ←──│
│  └── MQTT Setup ←─────────────┐         │   └── Use Public Broker   │
│                               │         └── View Nodes/Stats        │
│                               │                                      │
│                   ┌───────────▼───────────┐                         │
│                   │   MQTT Setup Wizard   │                         │
│                   ├───────────────────────┤                         │
│                   │ 1. Install mosquitto  │                         │
│                   │ 2. Start service      │                         │
│                   │ 3. Configure radio    │                         │
│                   │    - mqtt.enabled     │                         │
│                   │    - mqtt.address     │                         │
│                   │    - json_enabled     │                         │
│                   │    - uplink_enabled   │                         │
│                   └───────────────────────┘                         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Files Changed

| File | Change |
|------|--------|
| `src/launcher_tui/service_menu_mixin.py` | +180 lines: MQTT setup wizard |
| `src/launcher_tui/mqtt_mixin.py` | +70 lines: Local mode support |

## WebSocket Status

Gateway Bridge WebSocket (PR #664) was reviewed:
- Implementation complete in `src/utils/websocket_server.py`
- Integrated in `src/gateway/rns_bridge.py`
- Port 5001, JSON message format
- Requires hardware testing on Pi

## Testing Notes

### MQTT Setup Wizard
```bash
# Run TUI
sudo python3 src/launcher_tui/main.py

# Navigate to: Configuration → Service Config → MQTT Setup
# Follow wizard prompts

# Verify manually:
systemctl status mosquitto
mosquitto_sub -h localhost -t 'msh/#' -v
```

### MQTT Monitor Local Mode
```bash
# In TUI: Mesh Networks → MQTT Monitor → Configure → Use Local Broker
# Verify: Start Subscriber should connect to localhost:1883
```

## Next Steps

1. **Hardware testing**: Run MQTT setup wizard on Pi with meshtasticd
2. **WebSocket testing**: Verify real-time message display in web client
3. **Create PR**: Push changes and create pull request

## Handoff Notes

- All code changes are syntactically verified
- Follows existing patterns from meshtasticd installation wizard
- Uses service_check.py helpers for service management
- Channel auto-detection matches session note topic discovery: `msh/2/json/{channel}/#`
