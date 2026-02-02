# MeshForge Development Priorities

> **Last Updated:** 2026-02-02
> **Maintainer:** WH6GXZ / Dude AI

---

## Branch Strategy

| Branch | Purpose | Merges To |
|--------|---------|-----------|
| `main` | Stable releases, fixes, safe improvements | - |
| `beta-from-main` | UI polish, features being tested | → main |
| `alpha-from-main` | Experimental (firmware, hardware) | → beta |

### Feature → Branch Mapping

| Feature | Branch | Risk Level |
|---------|--------|------------|
| Offline map tiles | main | Low |
| Web UI dark mode | main | Low |
| Custom markers | main | Low |
| Device backup/restore | beta | Medium |
| TUI dark mode + nav | beta | Low |
| NanoVNA plugin | alpha | Medium |
| Firmware flashing | alpha | **High** |

---

## 🔴 Priority 1: Critical / Core Functionality

### Gateway Bridge (rns_over_meshtastic_gateway)
- [x] **RNS-Meshtastic bidirectional messaging** - Core bridge functionality
- [x] **RNS Over Meshtastic transport layer** - Packet transport via LoRa
- [x] **Message routing visualization** - See message flow between networks (2026-01-17)
- [x] **Gateway setup wizard** - Guided configuration for new users (2026-01-12)
- [x] **Bridge status monitoring** - Real-time health checks (API endpoints)
- [x] `rns_bridge.py:624` - Implement regex matching for filters (2026-01-11)
- [x] **Gateway config template** - gateway-hawaiinet.yaml (2026-01-20)

### Rich CLI Enhancements (MOC2 Testing)
- [x] **RNS/RNSD tools menu** - Mirror GTK panel functionality (2026-02-02):
  - [x] rnsd service status/control - service_menu_mixin.py
  - [x] Config file editor - rns_menu_mixin.py
  - [x] Interface list - rns_interfaces_mixin.py
  - [x] Announce management - auto-handled by RNS
  - [x] NomadNet launcher - nomadnet_client_mixin.py
- [x] **Device config wizard** - Complete setup flow (2026-02-02):
  - [x] Long name (40 char) - meshtasticd_config_mixin.py
  - [x] Short name (4 char) - meshtasticd_config_mixin.py
  - [x] Region selection - radio_menu_mixin.py
  - [x] Frequency slot - meshtasticd_config_mixin.py
  - [x] Modem preset (Short Turbo, etc.) - meshtasticd_config_mixin.py
  - [x] TX power - radio_menu_mixin.py (NEW)
  - [x] Position (lat/lon) - radio_menu_mixin.py
  - [x] MQTT policy - meshtasticd_config_mixin.py (NEW)
- [x] **Gateway config menu** - For RNS bridge setup (2026-02-02) - gateway_config_mixin.py (NEW)

### Code Quality
- [x] **Consolidate `get_real_user_home()`** - Reviewed: try/except fallback pattern is intentional for robustness when utils.paths unavailable
- [x] **Split large files** (>1500 lines) - COMPLETE:
  - [x] `rns.py` (673 lines now) - Successfully refactored
  - [x] `main_web.py` (1314 lines now) - Successfully refactored
  - [x] `launcher_tui/main.py` (1845 lines now) - Extracted to mixins (2026-01-15)
  - [x] `hamclock.py` (2107 lines now) - Extracted to mixins (2026-01-15)

### Testing
- [x] **Install pytest** - Available in environment
- [x] **Add tests for gateway transport** - 39 tests for transport layer
- [x] **Add tests for network diagnostics** - 28 tests (2026-01-15)

---

## 🟠 Priority 2: Feature Completion

### RNS Management Panel (Phase 2)
- [x] Install/update RNS, LXMF, NomadNet, MeshChat
- [x] Service management for rnsd
- [x] **RNODE device detection and setup** - Hardware wizard (2026-01-12)
- [x] Configuration editor

### Plugins
- [x] `meshcore.py:81` - Implement actual MeshCore connection (2026-01-12)
- [x] `meshcore.py:107` - Implement actual message sending (2026-01-12)
- [x] **MQTT dashboard** - Bridge to MQTT brokers (2026-01-17)
- [ ] **NanoVNA plugin** - Antenna tuning integration → `alpha`

### Node Firmware (→ `alpha` branch)
- [ ] **Firmware flashing from GTK** - Flash meshtastic firmware ⚠️ HIGH RISK
- [x] **Device backup/restore** - Save and restore node configs (2026-01-17)

---

## 🟡 Priority 3: UI/UX Improvements

### Dark Mode
- [x] **CSS variable foundation** - Theme system with light/dark support (2026-01-12)
- [x] GTK dark mode toggle - Settings panel with Force Dark Mode switch (verified 2026-01-15)
- [x] Web UI dark mode (integration) - data-theme attribute support (2026-01-17)
- [x] TUI dark mode - Toggle with T key (2026-01-17)
- [x] Unified theme system - Settings persist across UI (2026-01-17)

### TUI Improvements (→ `beta` branch)
- [x] Better navigation - Tab switching with hotkeys (2026-01-17)
- [x] Keyboard shortcuts - q, d, s, c, m, t, r, T (2026-01-17)
- [x] Status bar with key info - Footer with bindings (2026-01-17)

### Map Panel
- [x] Memory leak fix (timer cleanup)
- [x] Offline map tiles - OFFLINE_TILE_PROVIDERS added (2026-01-17)
- [x] Custom markers for node types - NODE_ICONS by role (2026-01-17)

---

## 🟢 Priority 4: Nice to Have

### Analytics
- [x] Coverage analytics - CoverageAnalyzer class (2026-01-17)
- [x] VOACAP propagation predictions (2026-01-12)
- [x] Link budget history/trends - AnalyticsStore with SQLite (2026-01-17)

### API
- [x] Local REST API documentation - docs/REST_API.md (2026-01-17)
- [x] Webhook support for events - WebhookManager class (2026-01-17)
- [x] Integration with external tools - via webhooks (2026-01-17)

### Documentation
- [ ] Video tutorials
- [ ] Deployment guides for Pi/SBC
- [ ] Network planning guide

---

## Completed ✅

- [x] GTK4 Desktop UI
- [x] Unified Node Map
- [x] RNS-Meshtastic Gateway (basic)
- [x] **RNS Over Meshtastic transport layer** (2026-01-11)
- [x] **Gateway transport API endpoints** (2026-01-11)
- [x] **Gateway transport CLI commands** (2026-01-11)
- [x] **Gateway transport tests (39 tests)** (2026-01-11)
- [x] AREDN Integration
- [x] Amateur Radio Compliance course
- [x] Standalone boot mode
- [x] MeshChat web interface integration
- [x] Network Diagnostics panel
- [x] NomadNet launch from GTK
- [x] VOACAP HF propagation links
- [x] Map panel memory leak fix
- [x] **VTE launcher fallback improvements** (2026-01-11)
- [x] **Frequency slot calculator with tests** (2026-01-11)
- [x] **Network diagnostics tests** (2026-01-12)
- [x] **Gateway setup documentation** (2026-01-12)
- [x] **RNODE device detection module** (2026-01-12)
- [x] **Gateway setup wizard** (2026-01-12)
- [x] **MeshCore TCP connection** (2026-01-12)
- [x] **Dark mode CSS foundation** (2026-01-12)
- [x] **HamClock DX Spots integration** (2026-01-12)
- [x] **HamClock Satellite tracking** (2026-01-12)
- [x] **HamClock DE/DX location display** (2026-01-12)
- [x] **meshtasticd responsive detection** (2026-01-17)
- [x] **Sensors tab in Mesh Tools** (2026-01-17)
- [x] **Message routing visualization** (2026-01-17)
- [x] **MQTT dashboard improvements** (2026-01-17)
- [x] **Device backup/restore** (2026-01-17)
- [x] **Offline map tiles** (2026-01-17)
- [x] **Custom node markers** (2026-01-17)
- [x] **TUI dark mode + navigation + shortcuts** (2026-01-17)
- [x] **Coverage analytics** (2026-01-17)
- [x] **Link budget history/trends** (2026-01-17)
- [x] **REST API documentation** (2026-01-17)
- [x] **Webhook support** (2026-01-17)
- [x] **MOC2 Health Check Fixes** (2026-01-20):
  - [x] Fixed Table import in radio.py
  - [x] Fixed meshtasticd.service (was using `meshtastic --tcp` client instead of daemon)
  - [x] Added post-reboot guidance ("type meshforge to continue")
  - [x] Added MeshAdv-Pi-Hat to template menu
  - [x] Added post-config guidance (browser/CLI next steps)
  - [x] Improved hardware-to-template mapping
  - [x] Improved auto-review index_no_check pattern (19→0 false positives)
  - [x] Created gateway-hawaiinet.yaml template

---

## Quick Wins (< 1 hour each)

1. [x] Add pytest to requirements.txt
2. [x] Create test for network diagnostics API (2026-01-12)
3. [x] Add dark mode CSS variable foundation (2026-01-12)
4. [x] Document gateway setup steps (2026-01-12)

---

## Technical Debt

| File | Lines | Action |
|------|-------|--------|
| mesh_tools.py | 1953 | Monitor |
| tools.py | 1842 | Monitor |
| tui/app.py | 1734 | Consider extracting panes |

*Note: rns.py (673), main_web.py (1314), launcher_tui/main.py (1845), and hamclock.py (2107) successfully refactored.*

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
