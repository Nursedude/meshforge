# MeshForge Development Priorities

> **Last Updated:** 2026-02-21
> **Maintainer:** WH6GXZ / Dude AI

---

## Branch Strategy

**Dual-branch model** as of v0.5.4 (alpha restored for MeshCore development).

| Branch | Version | Purpose |
|--------|---------|---------|
| `main` | `0.5.4-beta` | Stable beta — gateway bridge, TUI, monitoring, RF tools |
| `alpha/meshcore-bridge` | `0.6.0-alpha` | MeshCore integration — 3-way routing, companion radio mgmt |

**Alpha branch contents** (16 commits ahead of main, PRs #847-#851):
- `meshcore_handler.py` — MeshCore protocol handler (796 lines)
- `canonical_message.py` — Multi-protocol canonical message format (437 lines)
- `meshcore_bridge_mixin.py` — Bridge integration mixin (169 lines)
- `meshcore_mixin.py` — TUI menu for MeshCore operations (391 lines)
- `rns_config_mixin.py` + `rns_diagnostics_mixin.py` — Extracted from rns_menu_mixin
- `message_routing.py` — Enhanced with 3-way routing (MeshCore source)
- 1,839 lines of new tests (canonical_message, meshcore_handler, tribridge)

---

## Open Work

### TUI-Bridge API Wiring (Alpha)
- [ ] **MeshCore node listing** — Wire `_meshcore_nodes()` to live node tracker (filter `meshcore:` prefix)
- [ ] **MeshCore stats** — Wire `_meshcore_stats()` to bridge stats API (`meshcore_rx/tx/acks`)
- [ ] **Classifier MeshCore 3-way routing** — Verify `BRIDGE_MESHCORE` end-to-end
- [ ] **Auto-review reliability triage** — Review 64 reliability issues: real vs false positive

### Service Pre-flight Expansion (Issue #3)
- [ ] **34+ locations** still create TCPInterface/MQTT connections without `check_service()` pre-flight
- [ ] **8 files** use raw `subprocess.run(['systemctl', ...])` bypassing `service_check` module

### Plugins
- [ ] **NanoVNA plugin** — Antenna tuning integration
- [ ] **Firmware flashing from TUI** — Flash meshtastic firmware

### Documentation
- [ ] Video tutorials
- [ ] Deployment guides for Pi/SBC
- [ ] Network planning guide

---

## Recently Completed

### 2026-02-20: Code Quality Sprint (PR pending)
- [x] **Issue #1**: Path.home() — RESOLVED (3 violations fixed, 0 remaining)
- [x] **Issue #5**: Fallback copies — RESOLVED (20 files consolidated to direct imports)
- [x] **Issue #9**: Exception swallowing — 28 of 30 instances fixed across 7 files
- [x] **Issue #3**: Service pre-flight — Gateway files (meshtastic_handler, mqtt_bridge, rns_bridge)
- [x] **Issue #20 Phase 2**: Status display separation in meshtasticd_config_mixin
- [x] **Issue #20 Phase 3**: Event bus wired to WebSocket server
- [x] **.claude/ cleanup**: Removed 62 stale files (session notes, GTK issues), consolidated AI docs

### v0.5.4-beta (2026-02-11)
- [x] Gateway TX path fix — HTTP protobuf instead of CLI subprocess
- [x] TUI service menu, MQTT mixin, meshtasticd config wizard
- [x] Refactoring wave — traffic_inspector, node_tracker, metrics_export splits
- [x] All daemon loops interruptible (H1 fix)

---

## Technical Debt

**Threshold: 1,500 lines max per file**

| File | Lines | Status |
|------|-------|--------|
| knowledge_content.py | 1,993 | OK — content file by design |
| service_menu_mixin.py | 1,575 | MONITOR — OpenHamClock/MQTT extraction candidates |
| rns_bridge.py | 1,570 | MONITOR — MeshCoreBridgeMixin + MessageRouter + gateway_cli extracted |
| map_data_collector.py | 1,529 | Borderline, monitor |
| nomadnet_client_mixin.py | 1,519 | MONITOR — new to tracking |
| commands/rns.py | 1,516 | MONITOR — new to tracking |
| launcher_tui/main.py | 1,507 | Borderline — 33 mixins, monitor |
| prometheus_exporter.py | 1,505 | MONITOR — grew after metrics_export split |

---

## For rns_over_meshtastic_gateway TDD Session

Focus areas for `/ralph-wiggum`:
1. Message passing between RNS and Meshtastic
2. Position/telemetry bridging
3. Identity mapping (RNS hash <-> Meshtastic node ID)
4. Error handling and reconnection
5. Rate limiting and queue management

---

*Made with aloha for the mesh community*
