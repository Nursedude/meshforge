# MeshForge Missing TUI Features

> **Purpose**: Track features that exist in code but aren't accessible via TUI
> **Created**: 2026-01-30
> **Last verified**: 2026-02-21
> **For**: Alpha release planning

---

## Command Modules Not Exposed in TUI

### HIGH PRIORITY (Alpha blockers)

#### 1. Device Backup/Restore
- **Module**: `commands/device_backup.py`
- **Functions**: `create_backup()`, `list_backups()`, `restore_backup()`, `export_backup()`, `compare_backups()`
- **Why Important**: Users need to backup configs before updates/changes
- **Suggested Location**: Configuration menu
- **Effort**: LOW (module ready, just need menu)

#### 2. Message History
- **Module**: `commands/messaging.py`
- **Functions**: `get_messages()`, `get_conversations()`, `search_messages()`, `export_messages()`
- **Why Important**: Messages are stored but users can't view history
- **Suggested Location**: Dashboard or new Messages submenu
- **Effort**: LOW (module ready, just need menu)

### MEDIUM PRIORITY (Should have)

#### 3. Space Weather / Propagation Data
- **Module**: `commands/propagation.py` (primary), `commands/hamclock.py` (legacy)
- **Functions**: `get_space_weather()`, `get_enhanced_data()`, `configure_source()`
- **Why Important**: RF propagation data for HAMs
- **Suggested Location**: RF & SDR Tools or Propagation submenu
- **Effort**: LOW (module ready)
- **Note**: NOAA SWPC is primary (always works). HamClock/OpenHamClock are optional enhancements.

#### 4. RNode Configuration
- **Module**: `commands/rnode.py`
- **Functions**: `detect_rnode_devices()`, `get_device_info()`, `get_recommended_config()`, `configure_rnode()`
- **Why Important**: RNodes are common LoRa devices
- **Suggested Location**: Hardware menu or Configuration
- **Effort**: MEDIUM (needs device selection UI)

#### 5. Advanced Diagnostics
- **Module**: `commands/diagnostics.py`
- **Functions**: `get_system_health()`, `run_gateway_diagnostics()`, `check_mesh_connectivity()`
- **Why Important**: Troubleshooting without CLI
- **Suggested Location**: System menu
- **Effort**: LOW (module ready)
- **Note**: Some diagnostics already in system_tools_mixin

### LOW PRIORITY (Post-alpha)

#### 6. Gateway Control
- **Module**: `commands/gateway.py`
- **Functions**: `get_status()`, `start()`, `stop()`, `get_bridge_stats()`
- **Why Important**: Direct gateway control
- **Note**: May overlap with service control
- **Effort**: MEDIUM

---

## Utility Modules Not Exposed

### Network Health
- **Module**: `utils/network_health.py`
- **Functions**: `get_health_score()`, `get_component_health()`
- **Suggested Location**: Dashboard widget
- **Effort**: LOW

### Predictive Maintenance
- **Module**: `utils/predictive_maintenance.py`
- **Functions**: `predict_battery_life()`, `predict_failures()`
- **Suggested Location**: Metrics menu
- **Effort**: MEDIUM

### Firmware Flasher
- **Module**: `utils/firmware_flasher.py`
- **Functions**: `check_updates()`, `flash_firmware()`
- **Suggested Location**: Hardware menu
- **Effort**: HIGH (risky operation needs careful UI)

---

## Implementation Templates

### Adding Device Backup to Configuration Menu

```python
# In configuration_menu or new submenu
def _device_backup_menu(self):
    """Device backup and restore menu."""
    choices = [
        ("create", "Create Backup"),
        ("list", "List Backups"),
        ("restore", "Restore Backup"),
        ("export", "Export Backup"),
        ("back", "Back"),
    ]

    while True:
        choice = self.dialog.menu("Device Backup", "Manage device backups:", choices)

        if choice is None or choice == "back":
            break

        if choice == "create":
            from commands import device_backup
            result = device_backup.create_backup(name=f"backup_{datetime.now():%Y%m%d}")
            if result.success:
                self.dialog.msgbox("Backup Created", f"Saved: {result.data['path']}")
            else:
                self.dialog.msgbox("Backup Failed", result.error)
```

### Adding Message History

```python
def _message_history_menu(self):
    """View message history."""
    from commands import messaging

    result = messaging.get_messages(limit=50)
    if not result.success:
        self.dialog.msgbox("Error", result.error)
        return

    messages = result.data.get("messages", [])
    if not messages:
        self.dialog.msgbox("No Messages", "No message history found.")
        return

    # Format for display
    lines = []
    for msg in messages:
        lines.append(f"[{msg['timestamp']}] {msg['from']} -> {msg['to']}")
        lines.append(f"  {msg['text'][:60]}...")
        lines.append("")

    self.dialog.msgbox("Message History", "\n".join(lines))
```

---

## Tracking

| Feature | PR | Session | Status | Verified |
|---------|----|---------|---------|----------|
| Device Backup | | | Not started | 2026-02-21 |
| Message History | | | Not started | 2026-02-21 |
| Propagation Data | | | Not started | 2026-02-21 |
| RNode Config | | | Not started | 2026-02-21 |
| Advanced Diagnostics | | | Not started | 2026-02-21 |
| Network Health | | | Not started | 2026-02-21 |

---

## Notes

- All HIGH priority features have modules ready - just need TUI menus
- Focus on features that support meshtasticd + rnsd (required services)
- HamClock features only matter if service is running
- Firmware flashing is risky - save for post-alpha
