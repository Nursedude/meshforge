# MeshForge + meshtasticd Conflict Research Notes

**Date:** 2026-01-04
**Issue:** NomadNet Text UI button fails when meshtasticd is running, but works from command line

---

## The Problem

1. meshtasticd running → MeshForge GTK shows "no gateway or daemon"
2. "Launch Text UI" button fails when meshtasticd is running
3. NomadNet works fine from command line regardless of meshtasticd state
4. meshtasticd runs on `/dev/ttyACM0`, web client on port 9443

---

## Root Cause

**Port/Device Conflict:** MeshForge and meshtasticd both want access to the Meshtastic device.

- meshtasticd locks `/dev/ttyACM0` exclusively
- MeshForge gateway expects Meshtastic at `localhost:4403` (TCP)
- When meshtasticd owns the serial port, RNS initialization fails in GTK subprocess
- Command line works because it runs in user's full shell environment with proper RNS config

**Key Insight:** The subprocess isolation (setsid + temp script) used by the GTK button breaks RNS's ability to initialize when meshtasticd is using the serial port.

---

## Why Command Line Works But GTK Doesn't

| Aspect | Command Line | GTK Button |
|--------|--------------|------------|
| Environment | Full user shell | Isolated subprocess |
| RNS Config | Found naturally | May not inherit paths |
| Process | Direct execution | setsid + lxterminal + script |
| Result | Works | Fails when meshtasticd running |

---

## Code Locations Involved

### 1. NomadNet Launch (`src/gtk_ui/panels/rns.py:823-888`)
```python
def _launch_nomadnet(self, mode):
    # Creates temp script, launches with setsid
    os.system(f"setsid lxterminal -e {script_path} >/dev/null 2>&1 &")
```

### 2. Gateway Connection (`src/gateway/rns_bridge.py:348-383`)
```python
def _connect_meshtastic(self):
    # Tries localhost:4403 - fails if meshtasticd has port
    self._mesh_interface = meshtastic.tcp_interface.TCPInterface(hostname=host)
```

### 3. Gateway Status Detection (`src/gtk_ui/panels/rns.py:1817-1885`)
- Only tests if port 4403 is open
- Doesn't check if Python meshtastic library can actually initialize

---

## Potential Solutions

### Option A: Detect meshtasticd Conflict (Quick Fix)
Add check before launching NomadNet:

```python
def _check_meshtasticd_conflict(self):
    """Check if meshtasticd is blocking resources"""
    result = subprocess.run(['pgrep', '-f', 'meshtasticd'],
                          capture_output=True, text=True)
    return result.returncode == 0

# In _launch_nomadnet:
if self._check_meshtasticd_conflict():
    self.main_window.set_status_message(
        "Stop meshtasticd first (systemctl stop meshtasticd)")
    return
```

### Option B: Support Both Modes
Let user choose:
1. **Direct Mode:** RNS uses serial port directly (no meshtasticd)
2. **Service Mode:** RNS connects to meshtasticd via TCP

Add UI toggle to select mode and configure RNS accordingly.

### Option C: Fix Subprocess Environment
Ensure GTK-launched NomadNet inherits proper environment:
- Pass RNS config path explicitly
- Set RETICULUM_CONFIG environment variable
- Don't rely on HOME directory discovery

### Option D: Connect RNS to meshtasticd TCP
Instead of fighting over serial port:
- meshtasticd owns the radio hardware
- RNS connects to meshtasticd's TCP interface (port 4403)
- Configure RNS with TCPClientInterface to meshtasticd

This requires proper RNS config:
```
[[Meshtastic via meshtasticd]]
  type = TCPClientInterface
  target_host = 127.0.0.1
  target_port = 4403
```

---

## Recommended Fix Priority

1. **Immediate:** Add meshtasticd detection, show clear message to user
2. **Short Term:** Add UI to select connection mode (direct vs service)
3. **Medium Term:** Fix subprocess environment handling

---

## Test Procedure

1. `systemctl start meshtasticd`
2. Start MeshForge GTK
3. Click "Launch Text UI" → Should fail/show message
4. Run `nomadnet --config CONFIG` from terminal → Should work
5. `systemctl stop meshtasticd`
6. Click "Launch Text UI" again → Should work

---

## Architecture Decision Needed

**Who owns the Meshtastic radio?**

| Owner | Pros | Cons |
|-------|------|------|
| meshtasticd | Web UI works, systemd managed | RNS must connect via TCP |
| RNS directly | Simple, direct | No web UI, manual management |

The user needs to decide which mode they prefer, and MeshForge should support both cleanly.

---

## Files to Modify

- `src/gtk_ui/panels/rns.py` - Add meshtasticd detection, improve launch
- `src/gateway/rns_bridge.py` - Better connection error handling
- UI - Add mode selection toggle

---

*Notes prepared for next session. 73!*
