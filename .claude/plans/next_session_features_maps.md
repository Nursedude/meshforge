# MeshForge Next Session: Features, Function, Maps

## Context

Previous session (completed 2026-03-01): MeshCore gateway hardening (poll limiting, contact resolution, version check, structured errors), bouncer queue integration, NanoVNA handler conversion, 69 new tests. All pushed to `claude/review-meshforge-codebase-UeiOY`.

Focus: **features, functionality, and maps**.

---

## Completion Status (11 of 12 tasks DONE)

| Task | Description | Status | Notes |
|------|-------------|--------|-------|
| **A1** | SNR-weighted signal heatmap | **DONE** | `coverage_map.py` heatmap layer + Leaflet toggle |
| **A2** | RNS link quality polylines | **DONE** | `map_data_collector.py` + `map_http_handler.py` |
| **A3** | Terrain-aware RF coverage | DEFERRED | High effort; `src/utils/terrain.py` (612 lines) has SRTM/LOS infrastructure ready |
| **A4** | Historical node playback | **DONE** | Time-slider in `web/node_map.html` |
| **B1** | MeshCore TUI-bridge wiring | **DONE** | 10 menu items wired to live APIs |
| **B2** | Message lifecycle tracking | **DONE** | Message flow view in dashboard |
| **B3** | KML/CoT export for ATAK | **DONE** | Pre-existing in `src/tactical/tactical_map.py` with full TUI wiring |
| **B4** | Predictive health alerts | **DONE** | `PredictiveAnalyzer` in `utils/analytics.py` |
| **C1** | AREDN routing integration | **DONE** | `get_routing_hint()` in `aredn_topology.py`, `_annotate_aredn_hints()` in `message_routing.py`, TUI "Routing Influence" display. 16 tests. |
| **C2** | Analytics backend completion | **DONE** | `AnalyticsCollector` class, `get_hourly_summary()`/`get_daily_summary()` aggregation, wired into `rns_bridge.py` lifecycle. 28 tests. |
| **C3** | MeshCore diagnostics | **DONE** | 4 diagnostic checks in `core/diagnostics/` |
| **C4** | Merge overlapping handlers | N/A | OBSOLETE — referenced files (`hardware_config.py`, `radio_config.py`, `radio.py`) don't exist |

### Corrections from original doc
- **B3**: Was pre-existing in `src/tactical/tactical_map.py`, not missing
- **C2**: Prometheus exporter is at `src/utils/prometheus_exporter.py` (NOT `src/monitoring/`)
- **C4**: OBSOLETE — the handler files referenced don't exist in the codebase
- **A3**: `src/utils/terrain.py` (612 lines) already exists with SRTM/LOS infrastructure, reducing scope

---

## Remaining Work

Only **A3 (Terrain-Aware RF Coverage)** remains. It requires:
- Integrating `src/utils/terrain.py` SRTM data with site planner RF calcs
- Adding ITM/Longley-Rice simplified path-loss over terrain
- Overlay predicted coverage on Leaflet map as gradient polygon
- Files: `src/utils/terrain.py`, `src/launcher_tui/handlers/site_planner.py`, `src/utils/rf.py`, `src/utils/coverage_map.py`

---

## Verification

```bash
python3 -m pytest tests/ -v
python3 scripts/lint.py --all
python3 -m pytest tests/test_regression_guards.py -v
python3 -m pytest tests/test_aredn_routing.py tests/test_analytics_collector.py -v
```
