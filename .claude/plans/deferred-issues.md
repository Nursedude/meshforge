# Deferred Issues — From Code Quality Review (2026-02-26)

> Create these as GitHub issues manually. `gh` CLI not authenticated in this env.
> **Updated**: 2026-02-27 — Issue 1 DONE, Issue 2 DONE, Issue 3 IN PROGRESS, + reliability hardening (pre-flight, log levels)

---

## Issue 1: Consolidate logging modules — MOSTLY COMPLETE

**Labels**: refactor

**Done**:
- `logging_utils.py` merged into `logging_config.py` (PR #977, 2026-02-26)
- All 9 `logging.basicConfig()` calls replaced with `setup_logging()` from canonical module (2026-02-27)
  - Files: agent.py, diagnose.py, meshtasticd_config.py, orchestrator.py, bridge_cli.py, device_backup.py, map_data_service.py, space_weather.py, telemetry_poller.py
- `logger.py` documented as installer-only (intentionally separate)

**Remaining**: `logging_structured.py` and `log_parser.py` kept as-is (specialized, no overlap).
**Status**: No further action needed — logging is consolidated around `logging_config.py`.

---

## ~~Issue 2: Extract BaseMessageHandler from gateway handlers~~ COMPLETED

**Status**: DONE — PR #977 (2026-02-26)
**What was done**: `BaseMessageHandler` ABC extracted with shared constructor, `_truncate_if_needed`, and `_notify_status`. Logging consolidation (`logging_utils.py` merged into `logging_config.py`).

---

## Issue 3: Migrate TUI mixins to command registry pattern — IN PROGRESS

**Labels**: refactor, architecture

`MeshForgeLauncher` inherits from 46 separate mixins (lines 115-161 in `launcher_tui/main.py`). Each is used exactly once. This is a god-class split by file, not true composition.

**Infrastructure**: `handler_protocol.py`, `handler_registry.py`, `handlers/` — proven and stable.

**Progress** (2026-02-27):
- Phase 1 pilot: 5 handlers (latency, classifier, amateur_radio, analytics, rf_tools)
- Batch 1: 8 handlers (node_health, metrics, propagation, site_planner, sdr, link_quality, webhooks, network_tools)
- Batch 2: 8 handlers (favorites, messaging, aredn, rnode, device_backup, logs, hardware, service_discovery)
- Batch 3: 6 handlers registered (channel_config, gateway, radio_menu, settings, meshcore, updates)
- **Total registered: 27 handlers**

**Remaining**: ~24 mixins still in inheritance chain. Key blockers for full migration:
- `meshtasticd_config_mixin.py` (2,016 lines) — needs splitting before conversion
- `rns_menu_mixin.py` (1,498 lines) — composite of 4 sub-mixins, convert sub-mixins first

**Approach**: Migrate to plugin-based command registry where each mixin becomes a standalone handler. Launcher dispatches by menu key, not method inheritance. Registry and mixins coexist during transition.

**Effort**: ~2 days remaining for full migration.

---

*From `.claude/audits/code-review-quality-2026-02-26.md`*
