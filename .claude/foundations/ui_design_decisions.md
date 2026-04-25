# MeshForge UI Design Decisions

> **Status:** ACTIVE — guides all UI development
> **Originally drafted:** 2026-01-18
> **Last updated:** 2026-04-24 (GTK removed in v0.5.x — TUI is the sole interface)
> **Authors:** WH6GXZ (Nursedude) + Dude AI

## Core Principle

**The UI must make sense.** Users should always know:
- Where they are
- How to go back
- What happens next

No Ctrl+C to escape. Every menu has a cancel/back option.

---

## UI Strategy

### Primary Interfaces by Environment

| Environment | Primary UI | Why |
|-------------|-----------|-----|
| **Headless / Desktop / SSH / Pi** | Launcher TUI (whiptail/dialog) | Sole interface — works as root, raspi-config familiar, identical experience everywhere |
| **Visual mapping** | Browser-rendered map (`http://localhost:5000`) | Leaflet map served by `meshforge-map` systemd service |
| **Advanced/Scripting** | CLI commands | `meshtastic`, `rnsd`, direct control |

### UI Status

| UI | Status | Action |
|----|--------|--------|
| **Launcher TUI** | C — Core | Primary interface, only interface |
| **Browser map (`:5000`)** | C — Core | Folium/Leaflet served by `map_data_service.py` |
| **External `:8808` map (`meshforge-maps` repo)** | C — Core | Sister visualization repo, optional plugin |
| **Rich CLI configs** | N — Advanced | Keep for power users |
| **Web UI (full)** | X — Cut | Don't invest time here |
| **GTK Desktop** | X — Removed in v0.5.x | Historical only; do not resurrect |

---

## First-Run Experience

### After Install Completes

```
╔══════════════════════════════════════════════════════════════╗
║                    MeshForge Installed                       ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Installation complete!                                      ║
║                                                              ║
║  What would you like to do?                                  ║
║                                                              ║
║    1. Run Setup Wizard (recommended for new installs)        ║
║    2. Launch MeshForge                                       ║
║    3. Exit (run 'meshforge' later)                           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

### Setup Wizard Flow

```
1. Hardware Detection
   └── Detect SPI devices
   └── Identify HAT type (if possible)
   └── Offer hardware config selection

2. Node Identity
   └── Set Owner Name (long name)
   └── Set Short Name (4 char)

3. Region Selection
   └── Select regulatory region (US, EU, etc.)

4. Radio Preset
   └── Choose modem preset (LONG_FAST default)
   └── Explain tradeoffs

5. Channel Setup
   └── Primary channel name
   └── PSK (generate or use default)

6. Service Start
   └── Start meshtasticd?
   └── Enable on boot?

7. Complete
   └── Summary of configuration
   └── "Run 'meshforge' to access full interface"
```

---

## Menu Navigation Standard

### Every Menu Must Have

1. **Clear title** — Where am I?
2. **Back/Cancel option** — Always visible, always works
3. **Keyboard shortcuts** — q=quit, b=back, Enter=select

### Dialog Menu Template

```
┌─────────── Menu Title ───────────┐
│                                  │
│  Description of what this does   │
│                                  │
│  ○ Option 1                      │
│  ○ Option 2                      │
│  ○ Option 3                      │
│  ────────────────────────────    │
│  ○ Back                          │
│                                  │
│     < Cancel >    < OK >         │
└──────────────────────────────────┘
```

### Input Prompt Template

For Y/N questions, always accept: y, n, c (cancel), q (quit), b (back)

```
Configure custom channel slot? [y/n/c] (n):
  y = yes, n = no, c = cancel/back
```

---

## Feature Scope

### Core (Must Work Perfectly)

| Feature | Component | Status |
|---------|-----------|--------|
| Meshtastic ↔ RNS Bridge | `gateway/rns_bridge.py` | Working |
| Node Tracking | `gateway/node_tracker.py` | Working |
| SPI HAT Detection | `config/spi_hats.py` | Working |
| Radio Presets | `config/lora.py` | Working |
| Channel Config | `launcher_tui/handlers/channel_config.py` | Working |
| Owner Name | `commands/meshtastic.py` | Working |
| Region Selection | `config/lora.py` | Working |
| Node List/Telemetry | TUI handlers + map | Working |
| MQTT Dashboard | `launcher_tui/handlers/mqtt.py` | Working |
| Diagnostics | `utils/diagnostic_engine.py` | Working |
| Coverage Maps | `utils/coverage_map.py` | Working |
| Live network map | `utils/map_data_service.py` (port 5000) | Working |
| RF Calculator | `utils/rf.py` | Working, tested |
| Launcher TUI | `launcher_tui/` | Sole interface |

### Nice-to-Have (Can Be Rough)

| Feature | Notes |
|---------|-------|
| Message Queue | SQLite queue exists, works |
| Webhooks | Implemented, low maintenance |

### Kept (Strategic)

| Feature | Notes |
|---------|-------|
| AREDN Panel | User is part of AREDN network |
| AI Assistant | Differentiator, future of mesh NOC |

### Cut/Deferred

| Feature | Reason |
|---------|--------|
| Web UI (full) | TUI + browser map cover the need |
| HamClock Integration | Defer beyond service start/stop |

---

## Maps Strategy

Maps are **Core** — essential for NOC visualization. Two surfaces:

1. **In-process `:5000`** — `utils/map_data_service.py` + `utils/map_data_collector.py` aggregate Meshtastic, RNS, MeshCore, AREDN into one Folium/Leaflet map. ThreadingHTTPServer; RNS pre-warmed on main thread (Issue #44).
2. **External `:8808`** — `meshforge-maps` sibling repo (optional plugin, separate systemd service), consumes the same `/api/nodes/geojson`.

### Unified Map Vision
```
┌─────────────────────────────────────────────────────────────┐
│  MeshForge Network Map                                      │
│                                                             │
│  Legend:                                                    │
│    ● Meshtastic Node (circle) — from meshtasticd / MQTT     │
│    ◆ RNS Destination (diamond) — from rnsd path table       │
│    ⬡ AREDN Node (hexagon) — from AREDN sysinfo API          │
│    ▼ MeshCore Node (operator-positioned)                    │
│    ★ Gateway/Repeater (star)                                │
│                                                             │
│  Data Sources:                                              │
│    - meshtasticd (localhost:443/9443 HTTPS)                 │
│    - rnsd (shared instance via /tmp/meshforge_rns_client/)  │
│    - AREDN sysinfo API (*.local.mesh)                       │
│    - meshcore_positions (operator-set in map_settings.json) │
└─────────────────────────────────────────────────────────────┘
```

### Implementation
- Folium generates HTML with Leaflet.js
- Served by `meshforge-map.service` (systemd) on `:5000`
- Per-source diagnostics surfaced via `/api/status` (Issue #43)
- Headless: open in external browser via SSH port forward or LAN IP

### Map Features
1. Node positions with per-network icons
2. Link lines between nodes (RF, tunnel, IP)
3. AREDN integration (query *.local.mesh nodes)
4. Coverage circles (toggle)
5. Auto-refresh

### AREDN Map Integration
Reference: https://worldmap.arednmesh.org/
- AREDN nodes report location to AREDN servers
- MeshForge queries local AREDN nodes via API
- See `.claude/research/aredn_integration.md` for API details

---

## Implementation Priorities

### Active Workstreams
1. Field validation of gateway, MeshCore handler, coverage maps
2. Per-source map diagnostics (Issue #43, ongoing)
3. NomadNet TUI service-mgmt parity (Issue #45, shipped — monitoring)

### Reliability
1. Ensure all Core features work 100% across the fleet (5 Pis)
2. Maintain regression-guard tests + lint MF001-MF012

---

## Technical Decisions

### Dialog Backend (Launcher TUI)
- Use `dialog` or `whiptail` (whichever available)
- Consistent look across systems
- Works over SSH, works as root

### Questionary (Rich CLI)
- Replace `Confirm.ask()` with questionary menus
- Arrow key navigation
- Escape to cancel
- Only where Rich CLI is still needed

### Service Detection
- Single source of truth: `utils/service_check.py`
- Systemd services: trust `systemctl` only (lint MF008)
- Non-systemd / user-scope: explicit `user=True` kwarg
- See Issue #17, #20 redesigns

---

## References

- `.claude/foundations/persistent_issues.md` — Known bugs and fixes
- `.claude/foundations/domain_architecture.md` — Core vs Plugin model
- `.claude/foundations/tui_architecture.md` — Handler registry pattern
- `.claude/rules/security.md` — Security rules (Path.home, shell=True, etc.)
