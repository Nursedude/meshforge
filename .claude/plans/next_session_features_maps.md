# MeshForge Next Session: Features, Function, Maps

## Context

Previous session (completed 2026-03-01): MeshCore gateway hardening (poll limiting, contact resolution, version check, structured errors), bouncer queue integration, NanoVNA handler conversion, 69 new tests. All pushed to `claude/review-meshforge-codebase-UeiOY`.

Next session focus: **features, functionality, and maps**.

---

## Recommended Tasks (by Focus Area)

### A. Maps — Coverage & Visualization Enhancements

**A1. Heatmap Data Generation** (Low effort, High impact)
- `src/utils/coverage_map.py` has Folium infrastructure but no heatmap layer generation
- Add `generate_heatmap_layer()` using existing node RSSI/SNR data from `map_data_collector.py`
- Wire into `web/node_map.html` as a toggleable Leaflet layer
- Files: `src/utils/coverage_map.py`, `src/utils/map_data_collector.py`, `web/node_map.html`

**A2. RNS Node Link Quality on Map** (Medium effort, High impact)
- `map_data_collector.py` collects Meshtastic SNR but RNS link quality (from `rns_transport.py`) is not wired to the map
- Add RNS link annotations (SNR, hop count, path quality) to GeoJSON node export
- Files: `src/utils/map_data_collector.py`, `src/utils/map_http_handler.py`

**A3. Terrain-Aware RF Coverage Prediction** (Medium-High effort, High impact)
- `src/launcher_tui/handlers/site_planner.py` (289 lines) does basic RF calcs
- Add SRTM elevation data integration for path-loss modeling (Longley-Rice or ITM simplified)
- Overlay predicted coverage on the Leaflet map as a gradient polygon layer
- Files: `src/launcher_tui/handlers/site_planner.py`, `src/utils/rf.py`, `src/utils/coverage_map.py`

**A4. Historical Node Playback** (Medium effort, Medium impact)
- `map_data_collector.py` stores timestamped snapshots but no temporal replay
- Add time-slider UI to `web/node_map.html` showing node positions over time
- Files: `src/utils/map_data_collector.py`, `web/node_map.html`

---

### B. Features — New Capabilities

**B1. MeshCore TUI-Bridge API Wiring** (Medium effort, High impact) — FROM TODO_PRIORITIES
- Wire `_meshcore_nodes()` to live node tracker (filter `meshcore:` prefix)
- Wire `_meshcore_stats()` to bridge stats API (`meshcore_rx/tx/acks`)
- Verify `BRIDGE_MESHCORE` classifier end-to-end
- Files: `src/launcher_tui/handlers/meshcore.py`, `src/gateway/meshcore_handler.py`, `src/gateway/message_routing.py`

**B2. Message Lifecycle Visibility** (Medium effort, High impact)
- Add message tracking from send → route → deliver → ack across all 3 protocols
- Surface in TUI dashboard as a "message flow" view showing recent messages + status
- Builds on canonical_message.py which already has protocol-agnostic IDs
- Files: `src/gateway/canonical_message.py`, `src/launcher_tui/handlers/dashboard.py`, `src/gateway/rns_bridge.py`

**B3. KML/CoT Export for ATAK Integration** (Low-Medium effort, Medium impact)
- Tactical ops handler exists (`handlers/tactical_ops.py`) but export formats are limited
- Add KML and CoT (Cursor on Target) XML export from node positions
- Enables interop with ATAK/WinTAK for field teams
- Files: `src/launcher_tui/handlers/tactical_ops.py`, new `src/utils/kml_export.py`

**B4. Predictive Node Health Alerts** (Medium effort, Medium impact)
- `src/monitoring/node_monitor.py` tracks current state but doesn't predict failures
- Add trending analysis: if a node's SNR/battery is declining, alert before it goes offline
- Wire to dashboard notifications
- Files: `src/monitoring/node_monitor.py`, `src/launcher_tui/handlers/node_health.py`

---

### C. Function — Operational Improvements

**C1. AREDN Topology Integration into Routing** (Medium effort, High impact)
- AREDN handler (`handlers/aredn.py`) shows topology but doesn't feed into routing decisions
- Wire AREDN link state into `message_routing.py` as a routing hint (prefer AREDN backhaul for long-haul)
- Files: `src/launcher_tui/handlers/aredn.py`, `src/gateway/message_routing.py`

**C2. Analytics Backend Completion** (Medium effort, Medium impact)
- `handlers/analytics.py` has TUI frontend but limited data aggregation
- Wire Prometheus exporter (`prometheus_exporter.py`) metrics into the analytics dashboard
- Add time-series summaries (hourly/daily message counts, link quality trends)
- Files: `src/launcher_tui/handlers/analytics.py`, `src/monitoring/prometheus_exporter.py`

**C3. Diagnostic Engine Enhancement** (Low-Medium effort, Medium impact)
- `src/core/diagnostics/` has 8 check modules — extend with MeshCore-specific checks
- Add: MeshCore connectivity check, companion radio firmware version check, channel sync verification
- Files: `src/core/diagnostics/`, `src/gateway/meshcore_handler.py`

**C4. Technical Debt: Merge Overlapping Handler Pairs** (Low effort, High maintainability)
- FROM TODO: `hardware.py` + `hardware_config.py`, `radio.py` + `radio_config.py` overlap
- Consolidate to reduce handler count and eliminate duplicated logic
- Files: `src/launcher_tui/handlers/hardware*.py`, `src/launcher_tui/handlers/radio*.py`

---

## Suggested Session Plan (Pick 3-4 tasks)

**Recommended combo for maximum impact:**

| Priority | Task | Why |
|----------|------|-----|
| 1 | **A1** Heatmap generation | Quick win, highly visible, builds on existing infra |
| 2 | **B1** MeshCore TUI-bridge wiring | Unblocks MeshCore usability, already in TODO |
| 3 | **A2** RNS link quality on map | Completes the tri-protocol map picture |
| 4 | **C3** MeshCore diagnostics | Leverages this session's gateway hardening work |

**Alternative combos:**
- *Maps-heavy*: A1 + A2 + A3 + A4
- *Features-heavy*: B1 + B2 + B3 + B4
- *Function-heavy*: C1 + C2 + C3 + C4
- *Balanced*: A1 + B1 + C1

---

## Verification (applies to any combo)

```bash
python3 -m pytest tests/ -v
python3 scripts/lint.py --all
python3 -m pytest tests/test_regression_guards.py -v
```
