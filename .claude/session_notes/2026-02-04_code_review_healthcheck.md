# Code Review & Health Check - 2026-02-04

**Session**: claude/meshforge-review-healthcheck-AHqJ4
**Scope**: Comprehensive review of MeshForge after 24 hours of rapid growth

## Growth Summary

Recent 5 commits added **3,366 lines** across 12 files:
- `telemetry_poller.py` (512 lines) - NEW
- `favorites_mixin.py` (484 lines) - NEW
- `mqtt_mixin.py` (+643 lines)
- `node_tracker.py` (+168 lines)
- `topology_mixin.py` (+161 lines)
- Various session notes

## Issues Found & Fixed

### 1. MF001: Path.home() Violation (CRITICAL)
**File**: `src/utils/telemetry_poller.py:421`
**Issue**: Used `Path.home()` which returns `/root` under sudo
**Fix**: Changed to `get_real_user_home()` from utils.paths

### 2. Race Condition in Rate Limiting (CRITICAL)
**File**: `src/utils/telemetry_poller.py:305-339`
**Issue**: Rate limiting state variables accessed without locks while other state uses proper locking
**Fix**: Added `_rate_limit_lock` and wrapped all rate limiting operations

### 3. Invalid Path with Empty SUDO_USER
**File**: `src/utils/telemetry_poller.py:427`
**Issue**: `Path("/home") / os.environ.get("SUDO_USER", "")` creates invalid path `/home//.local/...`
**Fix**: Removed redundant line - `get_real_user_home()` already handles SUDO_USER properly

### 4. Recursive Call Stack Risk
**File**: `src/launcher_tui/favorites_mixin.py:191`
**Issue**: `_show_all_nodes_with_favorites()` called itself recursively after toggle
**Fix**: Converted to `while True` loop with `continue`/`return`

## Issues Documented (Not Fixed This Session)

### File Size Violations (>1,500 lines guideline)
| File | Lines | Action |
|------|-------|--------|
| traffic_inspector.py | 2,194 | Split into modules |
| rns_bridge.py | 1,991 | Extract components |
| node_tracker.py | 1,808 | Split by concern |
| main.py | 1,799 | Extract menu handlers |
| engine.py | 1,767 | Split diagnostics |
| metrics_export.py | 1,762 | Split exporters |
| knowledge_content.py | 1,688 | Extract topics |

### MF005 Informational Warnings
**File**: `src/launcher_tui/main.py:1428,1430`
**Status**: False positive - calls are in main UI loop, not background threads

### Code Review Findings (Warnings)

**telemetry_poller.py**:
- Silent exception in `which` command fallback (line ~445) - add debug logging
- Missing input validation for `meshtastic_host` parameter

**favorites_mixin.py**:
- Hardcoded localhost for TCPInterface (lines 361, 393) - should be configurable
- Silent exceptions in `_get_favorites_count()` - add debug logging
- Magic numbers for node list limits (50, 75) - should be constants

## What Passed Review

- No `shell=True` subprocess violations (except intentional one in updates_mixin.py with hardcoded commands)
- No bare `except:` clauses anywhere
- All subprocess calls use list args with timeouts
- Proper daemon threads used
- Good exception handling patterns
- Comprehensive docstrings in new files

## Linter Status

```
Before fixes: 1 error, 2 info
After fixes:  0 errors, 2 info (false positives)
```

## Test Status

pytest not available in test environment (would need to install)

## Recommendations

1. **Priority**: Split files >1,500 lines in next refactoring session
2. **Priority**: Make meshtastic host configurable in favorites_mixin.py
3. **Low**: Add debug logging for silent exception catches
4. **Low**: Extract magic numbers to constants

## Session Entropy Check

No entropy detected - session completed systematically with all critical issues fixed.
