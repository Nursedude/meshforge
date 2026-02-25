# MeshForge NOC Install Session Notes

**Date:** 2026-01-18/19
**Node:** meshforgeMOC1 (Meshtoad SX1262)
**OS:** Debian 13 (Trixie) on Raspberry Pi

## Summary

Successfully debugged and fixed meshtasticd installation flow. MOC1 now running with native meshtasticd v2.7.15 and web UI on port 9443.

## Key Learnings

### Port Distinction (CRITICAL)
- **Port 4403** = TCP API (meshtastic CLI, Python SDK, MeshForge gateway connections)
- **Port 9443** = HTTPS Web UI (browser configuration interface)

Both ports are used simultaneously - they serve different purposes.

### OS Repo Matching
OpenSUSE Build Service repos MUST match the OS exactly:
- Debian 12 (Bookworm) → `Debian_12`
- Debian 13 (Trixie) → `Debian_13`
- Raspbian 12 → `Raspbian_12`
- Ubuntu 24.04 → `xUbuntu_24.04`

Installing wrong repo (e.g., Deb12 packages on Deb13) causes library mismatches.

### meshtasticd Binary Location
- Native binary installs to `/usr/bin/meshtasticd` (not `/usr/sbin/`)
- Always use `command -v meshtasticd` to find actual path
- Never hardcode paths in systemd services

### Config File Conflicts
meshtasticd loads ALL `.yaml` files from `config.d/` and MERGES them. If multiple files define `Webserver: Port:`, the LAST one wins. This caused port 4403 vs 9443 conflicts.

**Solution:** Keep only ONE radio config in `config.d/`, remove extras.

### Exit Code 203/EXEC
Means systemd can't find the binary at the ExecStart path. Usually:
- Binary not installed
- Wrong path in service file

### SIGABRT (Signal 6)
Common causes:
1. Radio hardware not detected (no SPI device, CH341 not loaded)
2. Port already in use (`errno=98: failed to bind`)
3. Config pointing to non-existent hardware

## Fixes Applied

### 1. Install Script Improvements (`scripts/install_noc.sh`)
- Added quit option (q/Q/0) to menu
- Auto-detect OS for correct OBS repo
- Verify binary exists AFTER apt install before creating service
- Use dynamic path: `MESHTASTICD_BIN=$(command -v meshtasticd)`
- Fall back to Python CLI if native install fails
- All config templates use port 9443 for web UI

### 2. Orchestrator Improvements (`src/core/orchestrator.py`)
- Added `--graceful` flag for no-radio/development scenarios
- Continues startup even when services fail (degraded mode)
- Better error reporting for failed services

### 3. Config Templates
All templates in `available.d/` now use:
```yaml
Webserver:
  Port: 9443
```

## Commands Reference

### Check meshtasticd Status
```bash
sudo systemctl status meshtasticd
sudo journalctl -u meshtasticd -n 50 --no-pager
```

### Fix Wrong Service File
```bash
sudo tee /etc/systemd/system/meshtasticd.service << 'EOF'
[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart=/usr/bin/meshtasticd -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl restart meshtasticd
```

### Clean Up Config Conflicts
```bash
# See what's in config.d
ls /etc/meshtasticd/config.d/

# Check for port conflicts
grep -r "Port:" /etc/meshtasticd/config.d/

# Remove extras, keep only your radio config
sudo rm /etc/meshtasticd/config.d/usb-serial.yaml
sudo rm /etc/meshtasticd/config.d/active.yaml
# etc.
```

### Verify Web UI
```bash
curl -k https://localhost:9443 2>/dev/null | head -3
```

### Add Correct OS Repo Manually
```bash
# For Debian 13 (Trixie)
echo "deb https://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13/ /" | sudo tee /etc/apt/sources.list.d/meshtastic.list
curl -fsSL "https://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13/Release.key" | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/meshtastic.gpg
sudo apt update
sudo apt install meshtasticd
```

## HAT Configs Available

| HAT | Config File | Notes |
|-----|-------------|-------|
| Meshtoad/MeshStick | `meshtoad-spi.yaml` | CH341 USB-to-SPI, SX1262 |
| RAK WisLink | `rak-hat-spi.yaml` | Direct GPIO SPI |
| Waveshare | `waveshare-spi.yaml` | Needs gpiochip=4 for Pi 5 |
| MeshAdv-Pi-Hat | TBD | Next test target |

## Next Steps

1. **Monday AM:** Clean install test with MeshAdv-Pi-Hat
2. Create `meshadv-pi-hat.yaml` config template
3. Test MeshForge GTK UI messaging with this setup
4. Address 24 LOW reliability findings (index access patterns)

## Git Commits from Session

```
72a57f0 feat: add graceful startup mode to orchestrator
81ac62f feat: add quit option to installer menu
38cb30c fix: improve install script robustness for meshtasticd
e8e536b fix: use correct meshtasticd binary path and web UI port
e944c30 fix: correct meshtasticd web UI port to HTTPS 9443
```

---
*73 de WH6GXZ - Made with aloha*
