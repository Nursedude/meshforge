# MeshForge Development Priorities

> **Last Updated:** 2026-01-07
> **Maintainer:** WH6GXZ / Dude AI

---

## 🔴 Priority 1: Critical / Core Functionality

### Gateway Bridge (rns_over_meshtastic_gateway)
- [ ] **RNS-Meshtastic bidirectional messaging** - Core bridge functionality
- [ ] **Message routing visualization** - See message flow between networks
- [ ] **Gateway setup wizard** - Guided configuration for new users
- [ ] **Bridge status monitoring** - Real-time health checks
- [ ] `rns_bridge.py:624` - Implement regex matching for filters

### Code Quality
- [ ] **Consolidate `get_real_user_home()`** - 35 duplicate definitions → single import
- [ ] **Split large files** (>1500 lines):
  - `rns.py` (2953 lines) → Extract config editor, MeshChat to separate modules
  - `main_web.py` (2911 lines) → Flask blueprints
  - `tools.py` (2695 lines) → Split into rf_tools.py, network_diag.py

### Testing
- [ ] **Install pytest** - Currently missing from environment
- [ ] **Add tests for gateway bridge** - Critical path needs coverage
- [ ] **Add tests for network diagnostics** - New feature needs tests

---

## 🟠 Priority 2: Feature Completion

### RNS Management Panel (Phase 2)
- [x] Install/update RNS, LXMF, NomadNet, MeshChat
- [x] Service management for rnsd
- [ ] **RNODE device detection and setup** - Hardware wizard
- [x] Configuration editor

### Plugins
- [ ] `meshcore.py:81` - Implement actual MeshCore connection
- [ ] `meshcore.py:107` - Implement actual message sending
- [ ] **MQTT dashboard** - Bridge to MQTT brokers
- [ ] **NanoVNA plugin** - Antenna tuning integration

### Node Firmware
- [ ] **Firmware flashing from GTK** - Flash meshtastic firmware
- [ ] **Device backup/restore** - Save and restore node configs

---

## 🟡 Priority 3: UI/UX Improvements

### Dark Mode
- [ ] GTK dark mode toggle
- [ ] Web UI dark mode
- [ ] TUI dark mode
- [ ] Unified theme system

### TUI Improvements
- [ ] Better navigation
- [ ] Keyboard shortcuts
- [ ] Status bar with key info

### Map Panel
- [x] Memory leak fix (timer cleanup)
- [ ] Offline map tiles
- [ ] Custom markers for node types

---

## 🟢 Priority 4: Nice to Have

### Analytics
- [ ] Coverage analytics
- [ ] VOACAP propagation predictions (in progress)
- [ ] Link budget history/trends

### API
- [ ] Local REST API documentation
- [ ] Webhook support for events
- [ ] Integration with external tools

### Documentation
- [ ] Video tutorials
- [ ] Deployment guides for Pi/SBC
- [ ] Network planning guide

---

## Completed ✅

- [x] GTK4 Desktop UI
- [x] Unified Node Map
- [x] RNS-Meshtastic Gateway (basic)
- [x] AREDN Integration
- [x] Amateur Radio Compliance course
- [x] Standalone boot mode
- [x] MeshChat web interface integration
- [x] Network Diagnostics panel
- [x] NomadNet launch from GTK
- [x] VOACAP HF propagation links
- [x] Map panel memory leak fix

---

## Quick Wins (< 1 hour each)

1. [ ] Add pytest to requirements.txt
2. [ ] Create test for network diagnostics API
3. [ ] Add dark mode CSS variable foundation
4. [ ] Document gateway setup steps

---

## Technical Debt

| File | Lines | Action |
|------|-------|--------|
| rns.py | 2953 | Split: config_editor.py, meshchat_panel.py |
| main_web.py | 2911 | Split: Flask blueprints |
| tools.py | 2695 | Split: rf_tools.py, network_diag.py |
| hamclock.py | 1893 | OK for now |
| radio_config.py | 1839 | Consider splitting |

---

## For rns_over_meshtastic_gateway TDD Session

Focus areas for `/ralph-wiggum`:
1. Message passing between RNS and Meshtastic
2. Position/telemetry bridging
3. Identity mapping (RNS hash ↔ Meshtastic node ID)
4. Error handling and reconnection
5. Rate limiting and queue management

---

*Made with aloha for the mesh community* 🤙
