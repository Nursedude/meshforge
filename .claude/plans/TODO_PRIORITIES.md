# MeshForge Development Priorities

> **Last Updated:** 2026-04-24
> **Maintainer:** WH6GXZ / Dude AI

---

## Branch Strategy

**Solo-dev direct-to-main** as of 2026-04-19. Alpha branch archived as tag `alpha-archived`.

| Branch | Version | Purpose |
|--------|---------|---------|
| `main` | `0.5.7-beta` | Sole branch — gateway bridge, TUI, monitoring, RF tools, optional MeshCore handler |

**Sister NOC**: `Nursedude/meshanchor` (split from main 2026-04-01) is the MeshCore-primary NOC. `CanonicalMessage` in `src/gateway/canonical_message.py` is the shared bridge contract — keep compatible across both repos.

---

## Open Work

### File Size Compliance (1,500-line cap)
- [ ] **Split `utils/map_data_collector.py`** (1,974 lines) — extract per-source collectors
- [ ] **Split `handlers/ai_tools.py`** (1,953 lines) — separate orchestration from menu wiring
- [ ] **Split `gateway/rns_bridge.py`** (1,632 lines) — further extract LXMF transport / link mgmt

### Service Pre-flight Expansion (Issue #3) — MOSTLY COMPLETE
- [x] **TCPInterface pre-flight**: device_controller, connections, rns_transport, node_monitor, mesh_bridge
- [x] **MQTT pre-flight**: mqtt_bridge plugin, mqtt_subscriber, mesh_bridge (localhost only)
- [x] **Raw systemctl migration**: diagnose.py, handlers/metrics.py (check_service)
- [ ] **Display-only systemctl calls**: a few handlers still use raw systemctl for info-only display (acceptable — showing info, not deciding state)

### Plugins
- [ ] **NanoVNA plugin** — Antenna tuning integration
- [ ] **Firmware flashing from TUI** — Flash meshtastic firmware

### Code Quality (from PR #976 audit — deferred items)
- [ ] **Merge hardware/radio config pairs** — `hardware.py`+`hardware_config.py`, `radio.py`+`radio_config.py` overlap
- [x] **Logging consolidation** — 9 `basicConfig()` calls replaced with `setup_logging()`, `logger.py` documented as installer-only
- [x] **Migrate TUI mixins → command registry** — COMPLETE: 49 mixins → 60 handlers, main.py 1,947→1,148 lines (Session 3)
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

### 2026-03-03: Session 4 — v0.5.5 Medium-Term Completions (PRs #1036-#1037)
- [x] **File Splits (1400-line threshold)**: 3 files split (PR #1036)
  - `meshtastic_protobuf_client.py` 1,457→915 (extracted `_protobuf_admin.py`)
  - `service_check.py` 1,410→941 (extracted `_port_detection.py`)
  - `handlers/rns_diagnostics.py` 1,403→859 (extracted `_rns_diagnostics_engine.py`)
- [x] **Gateway Config Schema Validation** (PR #1037): 6 new validators, mode-specific conditional validation, 27 tests
- [x] **MQTT Message Queue Persistence** (PR #1037): `RetryPolicy.for_mqtt()`, `publish_to_mqtt()` callback, SQLite-backed queue for MQTT path, 7 tests
- [x] **File Splits (new threshold breakers)**: `mqtt_subscriber.py` and `map_http_handler.py` split
- [x] **MF010 Lint Fixes**: 7 `time.sleep()` → `_stop_event.wait()` conversions

### 2026-03-02: Session 3 — TUI Consolidation + Subprocess Timeouts (PRs #988-#1014)
- [x] **Handler Registry Migration**: 49 mixins → 60 self-contained handlers (Batches 1-10)
- [x] **Dead Code Removal**: 8,776 lines across 18 utils + 3 tests (PR #1012)
- [x] **File Size Compliance**: All 9 oversized files split under 1,500-line guideline (PR #1014)
- [x] **Logging Consolidation**: 4 modules → 2 (logging_config.py canonical)
- [x] **Test Fixes**: 36 pre-existing failures resolved (PR #1000)
- [x] **Subprocess Timeout Hardening**: MF004 verified across all handler files (PR #999)
- [x] **rns_diagnostics Split**: 2,261 → 1,403 lines (transport + identity + sniffer modules)
- [x] **Daemon Loop Fixes**: 5 time.sleep → _stop_event.wait conversions
- [x] **main.py Reduction**: 1,947 → 1,148 lines (41% reduction)

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

**Threshold: 1,400 lines proactive split / 1,500 hard max** (updated 2026-04-24)

Three files currently over the hard cap (see Open Work above). Largest files:

| File | Lines | Status |
|------|-------|--------|
| utils/map_data_collector.py | 1,974 | OVER — split pending |
| handlers/ai_tools.py | 1,953 | OVER — split pending |
| utils/knowledge_content.py | 1,993 | OK — content/data file by design (Issue #6 exemption) |
| gateway/rns_bridge.py | 1,632 | OVER — further extraction pending |
| utils/meshtastic_protobuf_client.py | 1,433 | Monitor — approaching cap |
| utils/prometheus_exporter.py | 1,399 | OK |
| utils/map_http_handler.py | 1,335 | OK |
| utils/service_check.py | 1,061 | OK — single source of truth |
| launcher_tui/main.py | 1,075 | OK — handler registry migration complete |

Session 4 splits: meshtastic_protobuf_client (1,457→915, extracted `_protobuf_admin.py`),
service_check (1,410→941, extracted `_port_detection.py`),
rns_diagnostics (1,403→859, extracted `_rns_diagnostics_engine.py`),
mqtt_subscriber (split), map_http_handler (split)

Session 3 splits: rns_diagnostics (2,261→1,403), meshtasticd_config (1,497→516+templates),
rns_bridge (extracted lifecycle), service_check (extracted iptables), map_data_collector
(extracted RNS collector), map_http_handler (extracted proxy), prometheus_exporter
(extracted server), commands/rns.py (extracted templates), nomadnet (extracted RNS checks)

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
