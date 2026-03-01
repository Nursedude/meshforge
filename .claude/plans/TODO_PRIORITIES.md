# MeshForge Development Priorities

> **Last Updated:** 2026-02-27
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

### Service Pre-flight Expansion (Issue #3) — MOSTLY COMPLETE (2026-02-27)
- [x] **TCPInterface pre-flight**: device_controller, connections, rns_transport, node_monitor, mesh_bridge
- [x] **MQTT pre-flight**: mqtt_bridge plugin, mqtt_subscriber, mesh_bridge (localhost only)
- [x] **Raw systemctl migration**: diagnose.py (direct import), handlers/metrics.py (check_service)
- [ ] **Display-only systemctl calls**: system_tools_mixin, service_menu_mixin (acceptable — showing info, not deciding state)

### Plugins
- [ ] **NanoVNA plugin** — Antenna tuning integration
- [ ] **Firmware flashing from TUI** — Flash meshtastic firmware

### Code Quality (from PR #976 audit — deferred items)
- [ ] **Merge hardware/radio config pairs** — `hardware.py`+`hardware_config.py`, `radio.py`+`radio_config.py` overlap
- [x] **Logging consolidation** — 9 `basicConfig()` calls replaced with `setup_logging()`, `logger.py` documented as installer-only
- [ ] **Migrate TUI mixins → command registry** — 27/49 handlers registered; ~24 mixins remaining (see `deferred-issues.md`)
- [ ] **Add actionable fix hints to error messages** — Replicate `cli/diagnose.py:192-197` pattern everywhere
- [ ] **Add quick health-check CLI command** — One-liner system health check
- [ ] **Clean `.claude/archive/`** — 200KB dead documentation weight
- [ ] **Merge RNS docs (3 → 1)** — 15KB overlap across `rns_comprehensive`, `rns_complete`, `rns_integration`

### Documentation
- [ ] Video tutorials
- [ ] Deployment guides for Pi/SBC
- [ ] Network planning guide

---

## Recently Completed

### 2026-02-26: Code Quality Audit & BaseHandler Extraction (PRs #969-#977)
- [x] **PR #976**: Comprehensive code quality audit — 14 prioritized action items
- [x] **PR #977**: `BaseMessageHandler` ABC extraction — shared constructor, `_truncate_if_needed`, `_notify_status`
- [x] **PR #977**: Logging consolidation — `logging_utils.py` merged into `logging_config.py`
- [x] **Message length validation**: `_truncate_if_needed` in `BaseMessageHandler` (228-byte Meshtastic limit)
- [x] **Silent exception handlers**: All 3 cited locations now log at DEBUG level
- [x] **Hot-path log levels**: `meshtastic_handler` and `mqtt_subscriber` already at DEBUG
- [x] **launcher.py --help**: Full argparse already exists (`launcher.py:399-424`)
- [x] **MQTT subscription log noise**: Downgraded reconnect subscription logs INFO→DEBUG
- [x] **MQTTBridgeHandler.queue_send**: Explicit `_truncate_if_needed` for consistency
- [x] **PRs #969-#975**: TUI stability, daemon mode, space weather, HF propagation, map reliability

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

**Threshold: 1,500 lines max per file** (updated 2026-02-26)

| File | Lines | Status |
|------|-------|--------|
| launcher_tui/main.py | 2,022 | OVER — 46 mixins, command registry migration planned |
| meshtasticd_config_mixin.py | 2,016 | OVER — extraction candidate (43 methods) |
| knowledge_content.py | 1,993 | OK — content file by design |
| rns_bridge.py | 1,599 | OVER — MeshCoreBridgeMixin + MessageRouter + gateway_cli already extracted |
| service_check.py | 1,573 | OVER — single source of truth, monitor |
| map_data_collector.py | 1,568 | OVER — monitor |
| map_http_handler.py | 1,557 | OVER — monitor |
| prometheus_exporter.py | 1,521 | OVER — grew after metrics_export split |
| nomadnet_client_mixin.py | 1,505 | BORDERLINE — monitor |
| commands/rns.py | 1,505 | BORDERLINE — monitor |
| rns_menu_mixin.py | 1,498 | BORDERLINE — monitor |
| service_menu_mixin.py | 1,467 | OK — under threshold |

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
