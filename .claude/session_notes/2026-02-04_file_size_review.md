# Session Notes: File Size Guidelines Review
**Date:** 2026-02-04
**Branch:** `claude/review-file-size-guidelines-EFega`

## Summary

Comprehensive review of Python files exceeding the 1,500 line guideline.

## Files Analyzed

### High Priority (>1,800 lines)

| File | Lines | Extraction Plan |
|------|-------|-----------------|
| `traffic_inspector.py` | 2,194 | 3-way split: models, dissectors, storage |
| `rns_bridge.py` | 1,991 | Extract Meshtastic connection handler |
| `node_tracker.py` | 1,808 | Extract data classes (Position, PKIStatus, etc.) |
| `launcher_tui/main.py` | 1,799 | Extract network/web client mixins |

### Medium Priority (1,500-1,800 lines)

| File | Lines | Notes |
|------|-------|-------|
| `diagnostics/engine.py` | 1,767 | Already has models.py - monitor |
| `metrics_export.py` | 1,762 | Could extract metric definitions |
| `knowledge_content.py` | 1,688 | Content file by design - no split |
| `rns_menu_mixin.py` | 1,524 | Near threshold - monitor |

## Key Findings

### Regression Alert: launcher_tui/main.py
- Previously refactored: 2,822 → 1,336 lines
- Current: 1,799 lines (+463 lines regression)
- Cause: New methods added to main.py instead of creating mixins
- Methods to extract:
  - `_ping_test`, `_meshtastic_discovery`, `_dns_lookup` → `network_tools_mixin.py`
  - `_open_web_client`, `_launch_web_client_browser` → `web_client_mixin.py`
  - `_data_path_diagnostic` (160 lines) → `data_path_mixin.py`

### traffic_inspector.py Structure
Already well-organized with clear boundaries:
1. **Enums/Constants** (83-151) - 70 lines
2. **Data Models** (158-535) - 380 lines (PacketField, PacketTree, MeshPacket, HopInfo)
3. **Dissectors** (542-1045) - 500 lines (PacketDissector, Meshtastic, RNS)
4. **DisplayFilter** (1052-1173) - 120 lines
5. **TrafficCapture** (1180-1561) - 380 lines
6. **TrafficStats/Analyzer** (1567-1737) - 170 lines
7. **TrafficLogger** (1744-1856) - 110 lines
8. **TrafficInspector** (1863-2087) - 225 lines
9. **Globals** (2093-2194) - 100 lines

Recommended extractions:
- `traffic_models.py`: Enums + Data Models (~450 lines)
- `packet_dissectors.py`: Dissectors (~500 lines)
- `traffic_storage.py`: TrafficCapture + Stats (~550 lines)

### node_tracker.py Structure
Many data classes at the start:
- Position (67-87)
- PKIKeyState (90-100)
- PKIStatus (103-148)
- AirQualityMetrics (151-181)
- HealthMetrics (184-199)
- More...

Recommended:
- Extract all data classes to `node_models.py` (~400 lines)

## Documents Updated

1. `.claude/foundations/persistent_issues.md` - Issue #6 updated with:
   - Current file sizes (2026-02-04)
   - Extraction plans
   - Regression alert for launcher_tui/main.py
   - Priority order for refactoring

2. `CLAUDE.md` - File Size Guidelines section updated

## Next Steps (for future sessions)

1. **Priority 1:** Extract from `traffic_inspector.py`
   - Create `src/monitoring/traffic_models.py`
   - Create `src/monitoring/packet_dissectors.py`
   - Create `src/monitoring/traffic_storage.py`

2. **Priority 2:** Fix `launcher_tui/main.py` regression
   - Create `src/launcher_tui/network_tools_mixin.py`
   - Create `src/launcher_tui/web_client_mixin.py`
   - Create `src/launcher_tui/data_path_mixin.py`

3. **Priority 3:** Extract from `node_tracker.py`
   - Create `src/gateway/node_models.py`

4. **Priority 4:** Extract from `rns_bridge.py`
   - Create `src/gateway/meshtastic_handler.py`

## Commands Used

```bash
# Check file sizes
wc -l src/**/*.py src/**/**/*.py 2>/dev/null | sort -rn | head -30

# Check markdown sizes
wc -l .claude/**/*.md .claude/*.md 2>/dev/null | sort -rn | head -15

# List methods in main.py
grep -n "def _" src/launcher_tui/main.py | head -80
```

## Session Entropy

No entropy detected. Clean analysis session.

---
*Session complete. Changes documented, ready for follow-up refactoring work.*
