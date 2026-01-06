# HamClock Integration Research

## Current MeshForge Implementation

The existing `gtk_ui/panels/hamclock.py` already has:
- URL/IP configuration for remote HamClock
- API port (8080) and Live port (8081) settings
- REST API fetching (`/get_sys.txt`, `/get_voacap.txt`, etc.)
- Service status checking via systemctl
- WebKit embed (when not running as root)
- Browser fallback for root users
- Auto-refresh capability

## HamClock Deployment Options

### Option 1: Remote HamClock (Current)
**What works now:**
- Connect to HamClock running on another machine (e.g., 192.168.86.37)
- Fetch data via REST API on port 8080
- View live display via port 8081

**Pros:** No changes needed, separation of concerns
**Cons:** Requires separate HamClock host

### Option 2: hamclock-web (Recommended for Pi)
**From pa28/hamclock-systemd:**
```bash
# Add repository
wget -qO- https://pa28.github.io/pa28-pkg/pa28-pkg.gpg.key | sudo apt-key add -
echo "deb https://pa28.github.io/pa28-pkg ./" | sudo tee /etc/apt/sources.list.d/pa28.list
sudo apt update
sudo apt install hamclock-web
sudo systemctl enable --now hamclock
```

**Ports:**
- 8081: Live web view (configurable)
- 8080: REST API (same as standalone)

**Pros:** Runs as systemd service, headless operation, easy install
**Cons:** Needs display configuration, uses resources

### Option 3: hamclock-systemd (Framebuffer)
For dedicated displays (kiosk mode):
```bash
sudo apt install hamclock-systemd
```
Writes directly to `/dev/fb0` - good for Pi with attached display.

### Option 4: hamclock (X11 Desktop)
Standard desktop application - not suitable for headless/server.

## Recommended Integration Strategy

### Don't Break What Works
The current panel already supports:
1. Remote HamClock connection ✓
2. Service status checking ✓
3. Data fetching via API ✓

### Add hamclock-web Installation Helper
```python
def _install_hamclock_web(self):
    """Install hamclock-web package"""
    commands = [
        # Add GPG key
        ['wget', '-qO-', 'https://pa28.github.io/pa28-pkg/pa28-pkg.gpg.key'],
        # Add repo (needs pipe to apt-key)
        # Install package
        ['sudo', 'apt', 'install', '-y', 'hamclock-web'],
        # Enable service
        ['sudo', 'systemctl', 'enable', '--now', 'hamclock'],
    ]
```

### Service Management (Already Exists)
Current code already has systemctl integration:
- `_check_service_status()` - checks hamclock service
- `_service_action()` - start/stop/restart

### Suggested Enhancements

1. **Installation Wizard**
   - Detect if hamclock-web is available
   - Offer one-click install from pa28 repo
   - Configure ports during setup

2. **Local vs Remote Toggle**
   - Auto-detect localhost:8081
   - Fall back to configured remote

3. **DiagnosticEngine Integration**
   ```python
   def _check_hamclock(self):
       # Check if service installed
       # Check if port 8081 responding
       # Check API health
   ```

4. **Configuration Sync**
   - Read HamClock config from `/etc/hamclock/` if local
   - Allow setting callsign, location via MeshForge

## API Endpoints (for reference)

| Endpoint | Returns |
|----------|---------|
| `/get_sys.txt` | System info (uptime, version) |
| `/get_voacap.txt` | VOACAP propagation data |
| `/get_spacewx.txt` | Space weather (SFI, A/K index) |
| `/get_config.txt` | Current configuration |
| `/set_defmt.txt?fmt=X` | Set DE format |

## Files to Modify

1. `gtk_ui/panels/hamclock.py` - Add install helper
2. `core/diagnostics/engine.py` - Add HamClock health check
3. `api/diagnostics.py` - Expose via REST

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Add install wizard | Low | Optional, doesn't change existing flow |
| Local detection | Low | Falls back to current behavior |
| Diagnostic check | None | Additive only |

## Conclusion

**Best approach:** Enhance existing panel with:
1. hamclock-web install button (uses pa28 repo)
2. Auto-detect local vs remote
3. Add to DiagnosticEngine for health monitoring

**Don't:** Rewrite the panel - it already works for remote connections.

---
*Research completed: 2026-01-06*
