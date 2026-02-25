# MeshForge Domain Memory & Timeline

**Date:** 2026-02-17
**Purpose:** Complete institutional memory and timeline verification
**Scope:** Full project history v1.0.0 → v0.5.4-beta

---

## Project Identity

| Attribute | Value |
|-----------|-------|
| **Name** | MeshForge |
| **Tagline** | "LoRa Mesh Network Development & Operations Suite" |
| **Pillars** | Build. Test. Deploy. Monitor. |
| **Target** | RF engineers, network operators, amateur radio operators |
| **Callsign** | WH6GXZ (Nursedude) |
| **Status** | Beta |

---

## Timeline Summary

### Phase 1: Foundation (v1.0.0 - v2.0.0) [Nov-Dec 2025]

| Version | Date | Milestone |
|---------|------|-----------|
| 1.0.0 | 2025-11-15 | Initial release - basic installation, LoRa config, hardware detection |
| 1.1.0 | 2025-12-01 | Modem presets, channel slots, module config |
| 1.2.0 | 2025-12-15 | Device config, SPI HAT support (MeshAdv-Mini) |
| 2.0.0 | 2025-12-29 | Dashboard, channel config, templates (Emergency/SAR, Urban, MtnMesh) |

**Key Decisions:**
- Python-first approach for cross-platform compatibility
- Rich CLI for terminal UI (before GTK/TUI split)
- Template-based config for rapid deployment

### Phase 2: Multi-UI Architecture (v3.0.0 - v3.2.7) [Dec 2025 - Jan 2026]

| Version | Date | Milestone |
|---------|------|-----------|
| 3.0.0 | 2025-12-30 | GTK4 + Textual TUI + Rich CLI - three UI options |
| 3.0.6 | 2025-12-31 | Centralized CLI detection (utils/cli.py) |
| 3.2.0 | 2026-01-01 | Network/RF/MUDP tools across all UIs |
| 3.2.7 | 2026-01-02 | Web UI (Flask) - browser interface on port 8080 |

**Key Decisions:**
- Auto-detect display → suggest appropriate UI
- Three parallel interfaces maintained (GTK, TUI, CLI)
- Web UI for remote/headless access

### Phase 3: MeshForge Rebrand (v4.0.0 - v4.2.1) [Jan 2026]

| Version | Date | Milestone |
|---------|------|-----------|
| 4.0.0 | 2026-01-03 | Rebrand to MeshForge |
| 4.0.1 | 2026-01-03 | Security hardening (subprocess, shell=True removal) |
| 4.1.0 | 2026-01-03 | Mesh Network Map, Version Checker, Desktop launcher |
| 4.2.0 | 2026-01-03 | Unified Node Map (Meshtastic + RNS), Config Editors |
| 4.2.1 | 2026-01-04 | Security fixes (shlex.quote), Gateway TX fix |

**Key Decisions:**
- Project identity: "LoRa Mesh Network Development & Operations Suite"
- Security-first: No shell=True, specific exception types
- RNS-Meshtastic bridge as core feature

### Phase 4: Stability & Polish (v0.4.3 - v0.4.7) [Jan 2026]

| Version | Date | Milestone |
|---------|------|-----------|
| 0.4.3 | 2026-01-05 | SDR/OpenWebRX, HF propagation, enhanced diagnostics |
| 0.4.4 | 2026-01-08 | Enum fixes, HamClock API, /ralph-wiggum-healthcheck |
| 0.4.5 | 2026-01-08 | NomadNet fix, Setup Wizard, safe config editing |
| 0.4.6 | 2026-01-11 | VTE wrapper, Frequency Slot Calculator, TUI enhancements |
| 0.4.7 | 2026-01-17 | Status consistency, Predictive analytics, Message lifecycle |

**Key Decisions:**
- Mixin pattern for TUI menu organization
- Single source of truth for service status (check_service)

### Phase 5: TUI-Only & MQTT Bridge (v0.4.8 - v0.5.4) [Jan-Feb 2026]

| Version | Date | Milestone |
|---------|------|-----------|
| 0.4.8 | 2026-01-30 | RF Calculator Hawaii presets, map improvements |
| 0.5.0 | 2026-02-01 | Beta milestone — TUI stable, NomadNet fixes |
| 0.5.1 | 2026-02-06 | Telemetry pipeline, RNS sniffer, MQTT bridge, security audit |
| 0.5.2 | 2026-02-08 | EAS alerts, _safe_call() reliability for all menus |
| 0.5.3 | 2026-02-08 | 350 unit tests for core gateway modules |
| 0.5.4 | 2026-02-11 | MQTT bridge architecture — zero web client interference |

**Key Decisions:**
- GTK4 desktop removed — TUI is the only interface
- _safe_call() dispatch wrapper for error isolation in all menus
- Gateway rewritten to use MQTT transport (no persistent TCP:4403 hold)
- alpha/meshcore-bridge branch for MeshCore 3-way routing experiment

---

## Architecture Decisions & Rationale

### 1. Privilege Separation Model

**Decision:** Viewer Mode (no sudo) vs Admin Mode (sudo)
**Rationale:** Most operations (monitoring, RF calcs, API queries) don't need root. Only service control, /etc/ config, and hardware access need sudo.
**Implementation:** `utils/paths.py:get_real_user_home()` handles sudo context

### 2. Core vs Plugin Architecture

**Decision:** Core features embedded, integrations as plugins
**Rationale:** Gateway bridge, node tracker, RF tools are critical path. HamClock, AREDN, MQTT are optional.
**Implementation:** Services run independently - MeshForge connects, doesn't embed

### 3. Service Status - Single Source of Truth

**Decision:** Trust systemctl for systemd services, no conflicting fallbacks
**Rationale:** Multiple detection methods (port, pgrep, systemctl) gave conflicting results
**Implementation:** `utils/service_check.py` with `is_systemd=True` flag

### 4. Message Flow - Existing Callbacks

**Decision:** Extend existing `pub.subscribe("meshtastic.receive")` rather than new event bus
**Rationale:** Stability and simplicity - infrastructure already exists
**Implementation:** `MessageListener.add_callback()` for UI subscription

### 5. TUI Mixin Architecture (supersedes GTK PanelBase)

**Decision:** Feature mixins composed into single MeshForgeLauncher class
**Rationale:** GTK4 removed in v0.5.x; TUI uses whiptail/dialog which is blocking (no lifecycle leaks)
**Implementation:** 36 mixins in `launcher_tui/`, `_safe_call()` for error isolation

---

## Persistent Issues Status

| Issue | Status | Resolution |
|-------|--------|------------|
| #1 Path.home() | RESOLVED | `get_real_user_home()` everywhere |
| #2 WebKit Root | ARCHIVED (GTK removed) | GTK4 removed, no longer relevant |
| #3 Service Verification | PARTIALLY RESOLVED | Gateway pre-flight done, 34+ locations remain |
| #4 Silent DEBUG Logging | IMPROVED | Exception handlers now log |
| #5 Duplicate Utilities | RESOLVED | Centralized in utils/ |
| #6 Large Files | MONITORED | 8 files over 1,500 lines |
| #9 Exception Swallowing | MOSTLY RESOLVED (2026-02-20) | 28 of 30 fixed, 2 benign by design |
| #10-#11, #13-#15 | ARCHIVED (GTK removed) | GTK4 removed, no longer relevant |
| #12 RNS Address In Use | FIXED | Client-only config in /tmp/ |
| #16 Gateway Reliability | DOCUMENTED | Best-effort delivery |
| #17 Connection Contention | FIXED | Shared connection manager |
| #18 Auto-Reconnect | IMPLEMENTED | Exponential backoff |
| #19 path_table Discovery | IMPLEMENTED | Check path_table for nodes |
| #20 Service Detection | ALL 3 PHASES DONE | systemctl-only, status display, event bus |

---

## MOC Architecture

**MOC = Mesh Operations Center**

| Node | Hardware | Preset | Purpose |
|------|----------|--------|---------|
| MOC1 | Meshtoad SX1262 | MEDIUM_FAST | Primary gateway |
| MOC2 | TBD | SHORT_TURBO | High-throughput testing |

**Network Topology:**
```
Meshtastic Nodes
      ↓
[meshtasticd:4403]
      ↓
MeshForge Gateway
      ↓
[rnsd → TCPClient]
      ↓
HawaiiNet RNS (192.168.86.38:4242)
```

---

## Key File Locations

| Category | Path |
|----------|------|
| Version | `src/__version__.py` |
| Paths | `src/utils/paths.py` |
| Service Check | `src/utils/service_check.py` |
| Message Listener | `src/utils/message_listener.py` |
| Gateway Bridge | `src/gateway/rns_bridge.py` |
| Orchestrator | `src/core/orchestrator.py` |
| TUI Main | `src/launcher_tui/main.py` |
| Knowledge Base | `src/utils/knowledge_base.py` |
| Persistent Issues | `.claude/foundations/persistent_issues.md` |

---

## Menu Navigation Pattern

**Consistent across all UIs:**
- `0` or `b` = Back
- `m` = Main Menu
- `q` = Quit
- Always offer escape before commit

**Future Dev Note (Back-out Mechanisms):**
- Config snapshots before changes
- Rollback scripts
- Undo history for config edits

---

*Memory verified: 2026-02-21*
*Timeline continuous: v1.0.0 → v0.5.4-beta*
*No gaps detected*
