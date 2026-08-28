# What MeshForge Does

The full capability inventory. The README carries a summary; this is the detail behind it.

## What Works (v0.6.2-beta)

### Status Definitions

| Status | Meaning |
|--------|---------|
| **Field-Tested** | Validated in real deployments with actual hardware and services |
| **Beta** | Code works in automated tests, needs real-world soak time |
| **Code-Ready** | Implemented and unit-tested, not yet validated with hardware/services |
| **MeshAnchor** | Live at [Nursedude/meshanchor](https://github.com/Nursedude/meshanchor), field testing in progress |

### Field-Tested (Real-World Validated)

These features have been used in actual mesh deployments with physical radios and running services:

| Category | Capabilities |
|----------|-------------|
| **TUI Interface** | Installer, service control, device config wizard, gateway config, diagnostics — <!--STAT:handlers-->103<!--/STAT--> handler modules via registry pattern |
| **Radio Management** | Install/configure meshtasticd, LoRa presets, channels, SPI/USB auto-detect |
| **RF Engineering** | Link budget, Fresnel zone, path loss, site planning, space weather (NOAA), Cython-optimized |
| **AI Diagnostics** | Offline knowledge base (20+ topics), rule-based troubleshooting, confidence scoring |
| **RNS/Reticulum** | Config editor, interface templates, rnstatus/rnpath, identity management, shared instance detection (domain socket + TCP + UDP), pre-flight checks |
| **NomadNet** | Install/launch/configure via TUI, LXMF messaging |
| **meshtasticd** | Full lifecycle management, SPI HAT and USB radio auto-detection |
| **Service Management** | systemd integration via `service_check.py` (single source of truth), health monitoring |
| **First-Run Wizard** | Hardware auto-detect templates, region selection, service verification |
| **Standalone RF Tools** | Zero-dependency RF calculator, works without sudo or radio hardware |
| **Multi-Mesh Gateway** | Meshtastic ↔ RNS/LXMF bridge via MQTT, composable-bridges model, refusal-on-inconsistency preflight, persistent SQLite queue. Field-deployed across the operator's 5-box LAN fleet + 1 cloud peer (since 2026-04-24) |
| **Gateway + RNode** | rnsd RNodeInterface on USB LoRa radio alongside Meshtastic HAT; RNS-LoRa egress at 903.625 MHz / SF7 — validated on fleet-host-3 |
| **Dude-claw (standalone agent)** | mini-dudeai brain + WireClaw ESP32 edge over NATS: sensor polls → threshold rules → actuation + pages, end-to-end proven on a Heltec V4 (2026-06-11). Display fork (`0.4.0+dudeclaw.N`) paints fleet metrics onto the board's OLED via a cron-verdict-wired pusher; stdlib NATS client, no new runtime deps |

### Beta (Automated Tests Pass, Needs Field Validation)

Code works in testing but hasn't been validated in real-world deployments with actual traffic:

| Category | Capabilities | Notes |
|----------|-------------|-------|
| **Radio Failover** | Dual-radio state machine, automatic TX switchover at >25% channel utilization, anti-flap, HTTP health polling | Needs dual meshtasticd |
| **Dual-radio mesh_bridge serial** | Cross-preset bridge (LF HAT + ST USB) via `connection_type: "serial"` on secondary — code merged + unit-tested, live-probed on a fleet box's Heltec | Needs sustained cross-preset traffic to validate |
| **MQTT Monitoring** | Nodeless mesh observation, protobuf decode, telemetry tracking, congestion alerts | Needs real MQTT traffic |
| **Coverage Maps** | Interactive Folium maps, SNR-based link quality, offline tile caching | **Priority QA target** — needs GPS position data |
| **Live NOC Map** | Browser view with WebSocket updates, node markers, signal heatmap | **Priority QA target** — needs running bridge |
| **Traffic Inspector** | Packet capture, protocol tree, display filters, path tracing | Needs real packet flow |
| **Emergency Alerts** | NOAA/NWS weather, USGS volcano, FEMA iPAWS | API-dependent |
| **Node Favorites** | Meshtastic 2.7+ favorites management, sync with device | Needs 2.7+ firmware |
| **Protobuf HTTP Client** | Full device config via protobuf HTTP (8 device + 13 module configs) | Needs meshtasticd 9443 |
| **Config API** | RESTful configuration management with NGINX reliability patterns | Needs integration test |
| **Network Topology** | D3.js force-directed graphs, path tracing, ASCII display | Needs live node data |
| **Node Health** | Predictive maintenance, battery forecasting, signal trending | Needs historical data |
| **RNS Packet Sniffer** | Live RNS capture, announce tracking, destination filtering | Needs RNS traffic |
| **Device Backup** | Configuration backup/restore, versioned snapshots | Needs real device |
| **Prometheus Metrics** | HTTP endpoint on port 9090, metrics exporter | Ready for Grafana |
| **Tactical Ops** | XTOC interop, 8 templates, X1 codec, KML/CoT/ATAK export | Implemented v0.5.4 |
| **AI PRO Mode** | Claude API integration, log analysis, predictive diagnostics | Requires API key |
| **Messaging** | Broadcast/direct messaging, LXMF routing, message history | Needs bridge running |

### Code-Ready (Implemented, Awaiting Hardware/Services)

| Category | Capabilities | Blocker |
|----------|-------------|---------|
| **AREDN** | Node discovery, link quality, service enumeration — live at an AREDN site; Meshtastic↔AREDN messaging via Raven bridge (pilot, field-proven 2026-06-11) | Raven on the production AREDN router itself (Phase 2) pending |
| **Grafana Dashboards** | Pre-built JSON dashboards for Prometheus | Needs Grafana + Prometheus setup |
| **uConsole AIO V2** | Hardware detection, GPIO power control, auto-config | Hardware ships Q2 2026 |

### MeshAnchor (Live — [Nursedude/meshanchor](https://github.com/Nursedude/meshanchor))

These features are now in the [MeshAnchor](https://github.com/Nursedude/meshanchor) repository:

| Category | Capabilities |
|----------|-------------|
| **MeshCore 3-Way Bridge** | Meshtastic <> RNS <> MeshCore routing, CanonicalMessage format |
| **RadioMode** | Select primary radio (Meshtastic / MeshCore / Dual) |
| **MeshCore Config** | Device detection, connection management, TUI menus |
| **MeshCore Diagnostics** | Library, device, config, bridge status checks |

MeshForge retains MeshCore as an optional gateway handler.

### Roadmap

**Completed (v0.5.x — Stability & Reliability)**

| Feature | Version | Notes |
|---------|---------|-------|
| MQTT bridge architecture | v0.5.4 | Zero-interference gateway |
| Gateway-essential test suite | v0.5.3 | 2,975 tests across 81 files |
| First-run setup wizard | v0.5.1 | Hardware auto-detect templates |
| Network topology visualization | v0.5.3 | D3.js + ASCII modes |
| Tactical messaging (XTOC interop) | v0.5.4 | 8 templates, X1 codec, KML/CoT/ATAK export |
| Handler registry migration | v0.5.4 | All 49 mixins replaced with 64 handler registry modules |
| Service pre-flight expansion | v0.5.4 | Advisory `check_service()` on all TCP/MQTT connections |
| Logging consolidation | v0.5.4 | 9 `basicConfig()` calls → canonical `setup_logging()` |
| Meshtastic API 2.7.x upgrade | v0.5.4 | Latest meshtastic library support |
| MeshChat removal + dead code cleanup | v0.5.5 | Removed 2,683-line MeshChat handler, simplified LXMF utils, bumped to 5 profiles |
| Mesh alert engine | v0.5.5 | Battery, emergency, disconnect, new node, noisy node, SNR alerts with cooldowns |
| Demo mode | v0.5.5 | Simulated mesh traffic for hardware-free testing (via meshing_around MockAPI) |
| MQTT packet decryption | v0.5.5 | Optional AES-256-CTR decryption via meshing_around crypto bridge |
| Cross-repo ecosystem config | v0.5.5 | `.claude/` configurations synced across meshforge, meshing_around, meshforge-maps |

**Shipped in v0.6.0 (post-v0.5.5)**

| Feature | Notes |
|---------|-------|
| Gateway field-deployed | Canonical gateway live in the fleet; LXMF directed downlink via `@id` / `@short_name`; bidirectional Meshtastic ↔ LXMF broadcast bridge symmetric on both ecosystems |
| Live NOC map (`:5000`) | Field-validated on 5-Pi fleet; per-protocol counts, federation across fleet boxes |
| Public cloud demo (`:8808` → VPS) | `https://meshforge-maps.ddns.net/` — static-push from on-prem, dark CartoDB tiles, NOAA space weather + alerts, slide-14-style network-layer pills |
| Federation across fleet | Each box's map sees every other box's nodes (24h freshness window) |
| Federation self-pacing | Two-tier exponential backoff per peer (Issues #59/#65): permanently-failing peers stop drowning the rollup; tier-2 cap (60×) for long outages preserves recovery detection |
| Federation directory gzip + size alarm | `/api/nodes/directory` 35 MB→4.7 MB on the wire (~7.6× compression); 40 MB alarm threshold (Issue #64) |
| Coverage maps | Folium generation field-tested with real GPS position data |
| Tiered node-directory retention | 30d for local origins, 7d for external bulk; 50k LRU cap |
| Honest delivery counters (Issue #66) | Sender→destination ack tracking substrate; SQLite-backed schema + LXMF synthesis + sweep; observability via `scripts/issue66_ack_status.py` |
| Sandbox preflight + audit (Issue #60) | Hardened systemd units fail loud at startup if `ReadWritePaths=` is missing a required dir; MF017 audit lints unit templates at PR time |
| `meshforge-tracer` lab measurement | Per-pair PING+ACK across the fleet; `/lab/rollup` aggregator with `Last 1h` and `Last 24h` tables (mean / p95 / fail % / breakdown) |
| RNS listener-owner preflight (Issue #69) | `lab/_lab_common.py:check_rns_listener_owner` catches foreign daemons claiming `@rns/<instance>` before they EOF every RNS client |
| Fleet-sync classifier | `scripts/fleet_sync.sh` skips daemon restarts on docs-only commits |
| Memory persistence + mirror | Private GitHub backup of operator's Claude memory with secrets-grep gate |

**Reliability arc (Issues #58–#80, 2026-05-18 → 2026-06-09)**

A class of "service running but not serving" failures was identified across the fleet — hardened systemd sandboxes failing silently, single-thread `socketserver` deadlocks during shutdown, default-value drift defeating bumps, rnsd RPC fragility wedging map server main thread, and foreign daemons claiming RNS shared-instance listeners. Each closed in code (preflight, audit, or refusal-to-start) with a regression-pinning test against the actual incident shape. Full ledger in `.claude/foundations/persistent_issues.md`.

**Shipped since v0.6.0**

| Feature | Notes |
|---------|-------|
| RNS/LXMF maintained forks | RNS and LXMF are MeshForge-maintained hard forks ([Nursedude/reticulum](https://github.com/Nursedude/reticulum), [Nursedude/lxmf](https://github.com/Nursedude/lxmf)) pinned by tag + SHA in `requirements/rns.txt` — rnsd RPC fragility now fixed at the source (wire format and crypto unchanged; fully interoperable with stock RNS) |
| Watchdog probe layer | One probe per field-learned failure class (wedged RPC, fd leaks, permission/role/version drift, channel silence, stale cron verdicts…); signals flow to mini-dudeai briefs and pages |
| mini-dudeai hardening (Issues #79/#80) | Deploy gap closed, memory guards + rotation, self-probes, hold-don't-lie edge semantics — see `.claude/rules/honest_failure_modes.md` |
| Gateway delivery confirmation (Issue #74) | Meshtastic ROUTING_APP ACK consumption in both bridge modes (TCP + MQTT `/e/` ServiceEnvelope) — honest CONFIRMED/DROPPED instead of "sent"; gated off by default |
| 1,500-line file-split arc complete | Every source file under the 1,500-line maintainability threshold (facade-hub/mixin splits across watchdog, gateway, and map modules) |
| Dependabot auto-merge | Green dependency PRs land themselves; CI required-context gate enforced |
| AREDN ↔ Meshtastic bridge (Raven) | Persistent bidirectional bridge live on the AREDN-site box; both directions verified over real RF (2026-06-11) |
| Dude-claw Phase A + display fork | mini-dudeai standalone preset driving a WireClaw Heltec V4 edge node over NATS — sensor→rule→actuator+page proven both edges; firmware fork lights the OLED with fleet metrics (2026-06-11). Next: chat-compiler (Phase B), upstream PRs (display tool, NATS token auth) |

**Currently Soaking**

| Feature | Status |
|---------|--------|
| Cross-broker MQTT bridge | LongFast ecosystem multi-broker visibility |
| MeshAnchor `/fleet/rollup` parity | Federation triad (#54/#55/#56/#59/#64/#65) collectively closed |
| Issue #66 ack first callers | Software path complete; radio-side smoke + MeshCore correlation are remaining operator handoffs |

**MeshAnchor Track**

MeshCore-primary features have moved to [MeshAnchor](https://github.com/Nursedude/meshanchor).
MeshForge retains MeshCore as an optional gateway handler.

**Future Releases (v0.6.x+)**

| Feature | Target | Status |
|---------|--------|--------|
| Historical playback (Live Map) | v0.6.x | Planned |
| SDR spectrum analysis (RTL-SDR) | v0.6.x | Planned — hardware dependent |
| Hardware support matrix | v0.7.0 | RAK, Heltec, uConsole AIO V2 |
| GPS tracking + GPX export | v0.7.0 | Planned |
| MeshForge ↔ MeshAnchor gateway | v0.8.0 | Inter-app bridging protocol |
| Firmware flashing | v1.0.0 | High risk — needs extensive testing |
| v1.0 stable release | -- | After field-validated gateway + MeshAnchor ecosystem |

### Known Limitations

| Feature | Limitation | Workaround |
|---------|-----------|------------|
| **Coverage Maps** | Not yet validated with real GPS position data | Requires MQTT subscriber collecting positions |
| **Live NOC Map** | Node trails require historical data | Enable MQTT subscriber for data collection |
| **MeshCore** | Optional handler on main; full support lives in MeshAnchor | Use [MeshAnchor](https://github.com/Nursedude/meshanchor) for MeshCore-primary |
| **Grafana** | Dashboards require manual import | See `dashboards/README.md` for instructions |
| **TCP:4403** | Only one client can connect | Gateway uses MQTT (v0.5.4+), TCP free for CLI |
| **AREDN** | Correct API implemented, needs AREDN hardware | Code-ready, awaiting hardware |
| **Fleet Monitor (multi-host)** | Handler-thread pile-up on Pi-class hardware under sustained dashboard polling. Field-stable but newer code. | In-flight semaphore + dashboard 429-retry shipped in sister project ([MA #128](https://github.com/Nursedude/meshanchor/issues/128)); follow-on improvements tracked in [MA #126](https://github.com/Nursedude/meshanchor/issues/126) / [#127](https://github.com/Nursedude/meshanchor/issues/127) / [#131](https://github.com/Nursedude/meshanchor/issues/131). **Single-box install is the most reliable mode today.** |

### Testing Reality Check

MeshForge has **~9,300 automated tests** (run `python3 -m pytest tests/ --co -q`
for the live count) across <!--STAT:testfiles-->335<!--/STAT--> test files. However, automated tests
validate code paths with mocks — they do not replace field testing. The following
features have strong unit test coverage but have **not been run with real services
and radios** in a live deployment:

- Coverage maps (tested with synthetic position data)
- MeshCore handler (mocked meshcore_py; full support moved to MeshAnchor)
- Tri-bridge routing (all three protocols mocked; moved to MeshAnchor)

**Field-validated features** (tested with real hardware): TUI, meshtasticd config,
RF tools, RNS/rnsd integration, NomadNet, service management, standalone tools,
gateway bridge (Meshtastic ↔ LXMF directed + broadcast), lab tracer, federation
across the fleet.

**Reliability ratio — single-box vs fleet monitor:** The cross-host fleet
rollup has the **least field time** of any subsystem and the most documented
recurrence patterns. Single-box install (one Pi, one TUI, local map at
`:5000`) is significantly more reliable than the multi-host fleet monitor
view. Plan accordingly: start with single-box, layer in the cross-host
dashboard once you've understood the restart cadence + known limitations
above. See sister project [MeshAnchor's #131](https://github.com/Nursedude/meshanchor/issues/131) for the full failure-mode log.

---

## AI Intelligence

MeshForge includes two tiers of AI-powered network diagnostics:

### Standalone Mode (No Internet Required)
- 20+ topic knowledge base covering mesh networking fundamentals
- Rule-based diagnostic engine with pattern matching
- Structured troubleshooting guides for common issues
- Confidence scoring on diagnoses
- Works completely offline — ideal for field deployment

### PRO Mode (Claude API)
- Natural language troubleshooting ("Why is my node offline?")
- Log file analysis with suggested actions
- Context-aware responses (knows your network topology)
- Predictive issue detection
- Expertise-level adaptation (novice → expert)
- Falls back to Standalone when API unavailable

```python
from utils.claude_assistant import ClaudeAssistant

assistant = ClaudeAssistant()  # Auto-detects mode
response = assistant.ask("Node !abc123 has -15dB SNR, is that okay?")
print(response.answer)
print(response.suggested_actions)
```

### mini-dudeai — Always-On Local Agent

A small, stdlib-only **rule-loop agent** (`src/mini_dudeai/`) that runs 24/7 on
each box, watching local health signals and firing actions (ntfy, escalation,
digest annotation) on the *transition*, not every tick. Borrowed from
[wireclaw.io](https://wireclaw.io): **the LLM is the compiler, this is the
runtime** — a cloud Claude session edits the rules file when invoked; the agent
runs them with no model in the hot path (cheap, offline-tolerant, Pi-friendly).

- **Two modes:** a `meshforge_fleet` preset (live on the fleet) and a config- or
  SDK-driven **standalone** mode for your own gear.
- **Warm-start:** maintains a brief + a nightly *synthesis* ("dream") that
  distills its own history into candidate memory-deltas a cloud session
  ratifies — so the next session doesn't start cold.
- **Observation-only:** no `subprocess`/`systemctl`; the worst a bad rule can do
  is send a notification.

```bash
python3 -m mini_dudeai --preset meshforge_fleet     # fleet daemon
python3 -m mini_dudeai --config my_config.json       # standalone
python3 -m mini_dudeai --preset meshforge_fleet --dream   # nightly synthesis
```

> Full SDK docs: [`src/mini_dudeai/README.md`](../src/mini_dudeai/README.md)

---

## Coverage Maps

Interactive network visualization powered by Folium and Leaflet.js:

### Static Coverage Maps (Stable)

- **Node markers** with status, battery, RSSI, hardware info
- **SNR-based link coloring** — green (excellent) → red (marginal)
- **Coverage radius estimation** based on LoRa preset
- **Offline tile caching** — works without internet in the field
- **Multiple tile layers** — OpenStreetMap, Terrain, Satellite
- **Heatmap generation** — node density visualization
- **GeoJSON import/export** — interoperate with other tools

```python
from utils.coverage_map import CoverageMapGenerator

gen = CoverageMapGenerator(offline=True)
gen.add_nodes_from_geojson(node_data)
gen.generate("field_coverage.html")  # Opens in any browser
```

### Live NOC Map (Beta)

Real-time browser-based network operations view at `http://localhost:5000`:

**Working Features**:
- **WebSocket updates** — real-time node position refresh (requires bridge running)
- **Node markers** — color-coded by status (online/stale/offline)
- **Signal heatmap** — toggle SNR-based heat visualization
- **Node popup details** — battery, SNR, hardware, altitude
- **Node list** — click to focus map on node

**In Development**:
- **Node trails** — requires historical data collection (enable MQTT subscriber)
- **Network topology** — D3.js force-directed graph view
- **Alert system** — visual notifications for node events

**Access**:
```bash
# Via TUI: Maps → Start Map Server
# Or directly:
sudo python3 src/utils/map_data_service.py
# Open http://localhost:5000 in browser
```

**Data Sources**:
- Gateway Bridge → WebSocket:5001 (real-time)
- MQTT Subscriber → mosquitto:1883 (multi-consumer)
- MQTT → WebSocket Bridge (connects MQTT to web UI)

---

