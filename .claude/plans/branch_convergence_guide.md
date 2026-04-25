# Branch Convergence Guide: main ↔ alpha/meshcore-bridge

> **STATUS (2026-04-24): HISTORICAL.** The convergence path documented below was not
> executed. Instead the team chose to (a) **split** the MeshCore-primary work into a
> sister repo `Nursedude/meshanchor` (2026-04-01), and (b) archive the alpha branch
> as the tag `alpha-archived` (2026-04-19). MeshCore lives on `main` as an optional
> gateway handler. This file is preserved for the divergence analysis and conflict
> resolution patterns, which are still useful reference for any future merge work.
>
> **Purpose**: Technical reference for merging the two parallel development tracks
> **Created**: 2026-03-03
> **Merge-base**: Commit `f5521af` (PR #1000)
> **Original target**: Execute in a dedicated Claude Code session after field testing passes

---

## Divergence Summary

| Metric | Value |
|--------|-------|
| Alpha commits ahead of main | 139 |
| Main commits ahead of merge-base (not on alpha) | 18 |
| Files changed between branches | 305 |
| Net line delta | +23,437 LOC (alpha larger) |

---

## Recommended Strategy: Rebase Main onto Alpha

**Why**: Alpha is the more architecturally advanced branch (139 commits of
structural refactoring + MeshCore). Main's 18 unique commits are smaller and
easier to forward-port than cherry-picking 139 commits in the other direction.

### Steps

```bash
# 1. Create a safety tag on main
git tag main-pre-convergence main

# 2. Create convergence branch from alpha
git checkout alpha/meshcore-bridge
git checkout -b convergence/v0.7.0

# 3. Cherry-pick main's unique commits (in order)
# Use `git log alpha/meshcore-bridge..main --oneline` to get the list
git cherry-pick <commit1> <commit2> ... <commit18>

# 4. Resolve conflicts (see hotspots below)

# 5. Run full test suite
python3 -m pytest tests/ -v
python3 scripts/lint.py --all

# 6. If all passes, merge convergence branch to main
git checkout main
git merge convergence/v0.7.0

# 7. Tag alpha as archived
git tag alpha-meshcore-bridge-archived alpha/meshcore-bridge
```

---

## Main's 18 Unique Commits (to cherry-pick)

These are on main but NOT on alpha (post merge-base):

| PR | Key Commit | Description |
|----|-----------|-------------|
| #1038 | `2649a87` | refactor: Split 2 oversized files, fix MF010 lint warnings |
| #1037 | `3d1c017` | feat: Gateway config schema validation + MQTT message queue persistence |
| #1036 | `44968fe` | refactor: Split 3 oversized files per CLAUDE.md #6 |
| #1034 | `db14d2e` | feat: Timeout module, TUI handler tests, circuit breaker extension |
| #1031 | `8ade5ee` | fix: TUI connectivity responsiveness with poll-based waits |
| #1025 | `5f231e1` | feat: Meshtastic API 2.7.x upgrade, TUI library upgrade option |
| #1022 | `8cd486d` | docs: README and SECURITY.md security audit results |
| #1020 | `5ef9b19` | refactor: Delete 3,457 lines dead diagnostic code |
| #1016 | `16661a3` | fix: Timeout to proc.wait() in logs handler |
| #1014 | `9cd3880` | docs: persistent_issues.md update |

Note: Some PRs have merge commits — cherry-pick the feature commits, not merge commits.

---

## Conflict Hotspots

These files were modified on BOTH branches independently:

### High Risk
| File | Main's Changes | Alpha's Changes | Resolution |
|------|---------------|-----------------|------------|
| `src/gateway/config.py` | Schema validation added | MeshCoreConfig restructured | Manual merge — keep both changes |
| `src/gateway/rns_bridge.py` | MQTT persistence | Structural refactoring | Manual merge — alpha's structure + main's persistence |
| `src/launcher_tui/main.py` | Handler count updates, file splits | Slimmed to 481 lines, run_menu_loop() | Alpha's version preferred, port main's specific changes |

### Medium Risk
| File | Main's Changes | Alpha's Changes | Resolution |
|------|---------------|-----------------|------------|
| `src/launcher_tui/handler_registry.py` | Minor updates | Section menu extraction | Alpha's version preferred |
| `src/utils/service_check.py` | Minor fixes | Extracted to core/services/ | Use alpha's extraction, apply main's fixes |
| `tests/test_regression_guards.py` | Updated | Updated differently | Merge both test additions |
| `tests/test_rns_bridge.py` | Minor changes | Minor changes | Should merge cleanly |

### Low Risk (likely auto-merge)
| File | Notes |
|------|-------|
| `CLAUDE.md` | Both updated metadata — manual merge of numbers |
| `README.md` | Will be freshly rewritten before convergence |
| `SECURITY.md` | Main added audit results — append to alpha's version |
| `requirements.txt` | Both added deps — union of both |

---

## Alpha-Only Files (No Conflict, Direct Addition)

These are new files on alpha that don't exist on main:

### MeshCore-Specific (11 files)
- `src/core/radio_mode.py` — RadioMode enum + persistence
- `src/core/meshcore_config.py` — /etc/meshcore/ config manager
- `src/core/diagnostics/checks/meshcore.py` — MeshCore diagnostic checks
- `src/launcher_tui/handlers/meshcore_config.py` — MeshCore config TUI
- `src/gateway/aredn_topology.py` — AREDN topology viewer
- `tests/test_meshcore_config.py`
- `tests/test_meshcore_config_handler.py`
- `tests/test_meshcore_diagnostics.py`
- `tests/test_meshcore_tui_wiring.py`
- `tests/test_radio_mode.py`
- `tests/test_radio_mode_handler.py`

### Structural Refactoring (new directories)
- `src/core/rf/` — 13 modules extracted from utils/rf.py
- `src/core/services/` — 8 modules extracted from utils
- `src/core/diagnostics/` — 12 modules (diagnostic engine refactored)
- `src/mapping/` — 12 modules extracted from utils
- `src/diagnostics/` — system diagnostics

### Tests (new)
- `tests/test_plugins.py` — 533 lines, 41 tests
- `tests/test_viewer_mode.py` — 443 lines
- `tests/test_terrain.py` — 426 lines
- `tests/test_rns_link_quality_map.py` — 343 lines
- `tests/test_rns_config.py` — 251 lines
- `tests/test_rns_link_map.py` — 226 lines
- `tests/test_tui_security.py` — 188 lines
- `tests/test_radio_mode.py` — 189 lines
- `tests/test_node_history_api.py` — 179 lines
- `tests/test_transport_registry.py` — 171 lines
- `tests/test_wifi_ap.py` — 166 lines
- `tests/test_radio_mode_handler.py` — 161 lines
- `tests/test_orchestrator_radio_mode.py` — 127 lines
- `tests/test_nanovna_handler.py` — 109 lines

---

## Main-Only Files (to Cherry-pick)

These exist on main but not alpha — they come with the cherry-picked commits:

- `tests/test_timeouts.py` — 212 lines (timeout module tests)
- Various file splits (moved code between files)

---

## Import Path Changes

Alpha refactored several import paths. After convergence, these imports change:

| Old Path (main) | New Path (alpha) | Notes |
|-----------------|-------------------|-------|
| `from utils.rf import ...` | `from core.rf.calculator import ...` | RF module split into 13 files |
| `from utils.service_check import ...` | `from core.services.service_check import ...` | Service management extracted |
| `from utils.coverage_map import ...` | `from mapping.coverage_map import ...` | Mapping extracted |
| `from core.plugin_base import ...` | `from utils.plugins import ...` | Plugin base deprecated |

**Backward compatibility**: Alpha maintains `utils/rf.py` and `utils/service_check.py`
as thin wrappers that re-export from the new locations. This means existing imports
won't break, but new code should use the new paths.

---

## Python Version Consideration

- **Main**: Targets Python 3.9+
- **Alpha**: Requires Python 3.10+ for meshcore_py
- **Resolution**: Keep 3.9+ as the minimum for MeshForge. MeshCore features are
  gated behind `safe_import('meshcore')` — Python 3.9 users get full
  Meshtastic + RNS functionality, MeshCore is simply unavailable.
- **Note**: Raspberry Pi OS Bookworm ships Python 3.11, so 3.10+ is not a
  practical barrier for the target platform.

---

## Test Reconciliation

After convergence, run BOTH test suites:

```bash
# Alpha's tests (should all pass on converged branch)
python3 -m pytest tests/ -v

# Specific areas to verify:
python3 -m pytest tests/test_rns_bridge.py -v          # Gateway bridge
python3 -m pytest tests/test_meshcore_handler.py -v     # MeshCore handler
python3 -m pytest tests/test_tribridge_integration.py -v # 3-way routing
python3 -m pytest tests/test_regression_guards.py -v    # Architecture guards
python3 -m pytest tests/test_timeouts.py -v             # Main's new timeout tests
```

Expected test count after convergence: ~3,000+ (main's 2,459 + alpha's unique tests).

---

## Post-Convergence Cleanup

1. Update `src/__version__.py` to `0.7.0-beta`
2. Update README.md to remove dual-branch documentation
3. Update CLAUDE.md branch strategy table
4. Tag: `git tag v0.7.0-beta`
5. Archive alpha: `git tag alpha-meshcore-bridge-archived alpha/meshcore-bridge`
6. Keep alpha branch for 1 release cycle, then delete

---

## Decision: REVISED — MeshAnchor Split (2026-03-05)

> **Previous decision** (2026-03-03): MeshCore stays in MeshForge, converge branches.
>
> **New decision** (2026-03-05): Alpha branch will become **MeshAnchor** (`Nursedude/meshanchor`),
> a standalone sister app where MeshCore is the primary radio. MeshForge main continues with
> Meshtastic as primary. The convergence described above is **superseded** by the split plan.

**Why the change**: The `RadioMode` abstraction on alpha already models two distinct apps —
one Meshtastic-primary, one MeshCore-primary. Rather than forcing them into one codebase,
the split lets each app optimize for its primary radio while sharing the gateway protocol
for interop.

**New plan**: See `.claude/plans/meshanchor_split_plan.md` for the full analysis.

**Sequence**:
1. Field test alpha with real MeshCore hardware (validate before split)
2. Create `Nursedude/meshanchor` from alpha (clean history)
3. Archive `alpha/meshcore-bridge` branch on MeshForge
4. Both repos maintain independent copies (fork-and-diverge), cherry-pick critical fixes

---

*Reference for future sessions — WH6GXZ + Claude Code*
