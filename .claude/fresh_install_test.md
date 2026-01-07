# Fresh Install Test Checklist

> For testing MeshForge on a clean system (2026-01-07)

## Pre-Install Requirements

```bash
# Clean Raspberry Pi OS or Debian/Ubuntu
# Python 3.9+
python3 --version
```

## Installation Steps

```bash
# 1. Clone repo
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# 2. Install system dependencies
sudo apt update
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1 lxterminal

# 3. Install Python packages
pip3 install rich textual flask meshtastic --break-system-packages

# 4. For RNS/NomadNet support
pip3 install rns lxmf nomadnet --break-system-packages

# 5. Verify install
python3 -c "from src.__version__ import __version__; print(f'MeshForge v{__version__}')"
```

## Test Checklist

### GTK UI Launch
- [ ] `sudo python3 src/launcher.py` - GTK UI opens
- [ ] All panels load without errors
- [ ] Status bar shows correctly

### Tools Panel - Network Diagnostics
- [ ] UDP Listeners button works
- [ ] TCP Listeners button works
- [ ] Multicast Groups button works
- [ ] Process→Port Map works
- [ ] Check RNS Ports works
- [ ] Check Meshtastic Ports works
- [ ] Full Diagnostics runs all checks
- [ ] Kill Competing Clients works
- [ ] Stop All RNS works
- [ ] Watch API Connections works

### RNS Panel - NomadNet Launch
- [ ] "Launch Text UI" button opens lxterminal
- [ ] NomadNet starts in terminal (not blank)
- [ ] Terminal stays open after NomadNet exit
- [ ] Config path fallback works:
  - If ~/CONFIG exists → uses it
  - Otherwise → uses ~/.nomadnetwork

### Config Files Created
- [ ] ~/.config/meshforge/ directory created
- [ ] ~/.reticulum/config exists (after RNS install)
- [ ] ~/.nomadnetwork/ exists (after NomadNet install)

### Known Issues to Watch
1. **Path.home() bug** - Config files should be in user home, not /root
2. **WebKit disabled as root** - HamClock embeded view shows fallback
3. **RNS port 29716** - May need to disable AutoInterface in ~/.reticulum/config

## Expected Behavior

### NomadNet First Run
On fresh install, NomadNet will create ~/.nomadnetwork/ with defaults.
If AutoInterface causes "Address already in use":

```bash
# Edit RNS config
nano ~/.reticulum/config

# Disable AutoInterface:
[[Default Interface]]
  type = AutoInterface
  enabled = no
```

### Meshtastic Connection
- meshtasticd should be installed separately or via MeshForge installer
- TCP port 4403 for API connection

## Report Results

After testing, note:
- Any errors in terminal output
- Features that didn't work
- UI issues or crashes
- Memory usage (`htop` or `free -h`)

---
*Test checklist for MeshForge v0.4.3-beta*
