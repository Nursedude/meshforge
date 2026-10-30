# MeshForge - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) — HAM General, Infrastructure Engineering, RN BSN

<!-- Auto-loaded by Claude Code -->
@.claude/rules/security.md
@.claude/rules/calibrated_claims.md
@.claude/rules/model_advisor.md
@.claude/rules/honest_failure_modes.md
@.claude/foundations/persistent_issues.md

---

## CRITICAL — Read Before Any Code Change

**Service Interaction Rules** (Issue #29 — regression prevention):
- **NEVER** create `TCPInterface()` directly — use `MeshtasticConnection` from `connection_manager.py` or acquire `MESHTASTIC_CONNECTION_LOCK` first
- **NEVER** read `/api/v1/fromradio` in TX paths — use `send_text_direct()` from `meshtastic_protobuf_client.py`
- **NEVER** construct `RNS.Reticulum()` directly — use `open_reticulum()` from `utils.rns_init` (the guarded chokepoint: degrades on a wedged rnsd instead of hanging the thread (#68), fails loud on a foreign `@rns` owner (#69), reuses the singleton). Raw construction is banned by lint MF019 + `TestRNSReticulumChokepoint`. Always pass `configdir=` (causes EADDRINUSE otherwise when rnsd is running, MF009)
- **NEVER** use raw `systemctl is-active` — use `check_service()` from `service_check.py`
- **NEVER** use `Path.home()` directly — use `utils.paths.get_real_user_home()` (MF001)
- **NEVER** use `safe_import` for first-party modules — external deps only
- **NEVER** call `sqlite3.connect()` directly — use `connect_tuned()` from `utils.db_helpers` (MF013). Every new SQLite DB also needs a `DBSpec` entry in `utils.db_inventory`. Run `python3 scripts/db_audit.py` to verify.
- **NEVER** use `shell=True`, bare `except:`, or skip input validation / subprocess timeouts
- **ALWAYS** use `_stop_event.wait()` instead of `time.sleep()` in daemon loops
- **ALWAYS** split files exceeding 1,500 lines — lint-enforced since 2026-07-13 (MF025, frozen baseline for the 5 pre-existing offenders; baseline only shrinks)

**Calibrated claims**: tag every completion claim **VERIFIED / BELIEVED /
UNKNOWN** — the rule is `.claude/rules/calibrated_claims.md` (included above).
Check of record: `bash scripts/honest_status.sh` (`exit 0` = green; `exit 2`/UNKNOWN is never a pass).

**Question → method** (skills fire here only when NAMED — description-matching
measured 0/112 sessions, 2026-10-04; long form + re-count recipe: `meshforge` skill):

| Shape | Reach for |
|---|---|
| broke / failing / "why" | `git log -S'<error>'` + persistent_issues grep, then `mattpocock-skills:diagnosing-bugs` |
| build / change behaviour | `mattpocock-skills:tdd` — fails on old code first |
| define / decide / plan | `mattpocock-skills:grilling` → `.claude/ROADMAP.md` |
| upstream / protocol fact | `mattpocock-skills:research` — the pinned source, not the version |
| diff ready / review | `code-review medium <range>` invoked by ME — it forks its own contextless reviewer (the non-author); never from inside a subagent (nests). More than one agent on the manager: `scripts/fanout_headroom.py` GO + the `meshforge` skill's fan-out rule |
| RF / radio / mesh / traceroute | `meshforge` skill — measure at BOTH ends |

> Full security rules: `.claude/rules/security.md`
> Calibrated-claims discipline: `.claude/rules/calibrated_claims.md`
> Known issues & fixes: `.claude/foundations/persistent_issues.md`

---

## Quick Context

MeshForge is a **Mesh Operations Center (MOC)**: one interface over meshes that cannot hear each other (Meshtastic, MeshCore, RNS, AREDN, MQTT, IP/Starlink), serving the best available route with RNS as the hub — and telling the truth when it cannot see.

**Roadmap SSOT — read first**: `.claude/ROADMAP.md` (what we are, why we win, the best-available-route gap, Now/Next/Later).
**Active context / current sprint**: per-box session handoff notes at `~/.claude/plans/gateway-session-notes-*.md` (not repo-tracked; each box maintains its own).

---

## Branch Strategy

| Branch | Version | Status |
|--------|---------|--------|
| `main` | <!--STAT:version-->0.7.0-beta<!--/STAT--> | Stable — TUI, meshtasticd, RNS, RF tools, gateway, fleet organs. Field-tested. (Version generated from `src/__version__.py`; `readme_stats.py --check` fails on drift.) |

- **Sister project**: [MeshAnchor](https://github.com/Nursedude/meshanchor) — MeshCore-primary NOC, extracted from main on 2026-04-01.
- `main` includes XTOC/ATAK/CoT, MQTT bridge, security hardening. Gateway bridge, coverage maps, NOC map have unit tests but need field validation.
- MeshCore is available as an optional gateway handler on main.
- `CanonicalMessage` in `src/gateway/canonical_message.py` is the shared bridge contract — keep compatible with MeshAnchor's version.
- **RNS-reliability parity**: MeshForge is the **lead repo** for the RNS-reliability arc (chokepoint, fork pin, lint MF019, rnstatus `timed_out` probes). The fleet runs one rnsd per box (shared instance) and both apps connect to it, so they must run the identical RNS substrate. **Land RNS-reliability changes here first, then port to MeshAnchor.** `python3 scripts/parity_check.py` reports drift: byte-identical (`src/utils/rns_init.py`, `src/gateway/canonical_message.py`, the `requirements/rns.txt` `MF-FORK-PIN` block) + shape (`rns_status_parser.py` `timed_out`, lint MF009+MF019, `scripts/rns_version_check.py`, the two RNS-wedge probes — here in `watchdog_probes.py`, in MeshAnchor's `active_health_probe.py`).
- **Trunk only (operator 2026-09-23, MeshForge AND MeshAnchor):** commit direct to `main`, `git push origin main` (the `origin` remote, not a literal SSH URL) right after every hook-passing commit (a permission hook may still deny a push — expected, don't work around it); fleet boxes only ever pull. The gates (hooks, CI, `honest_status` fleet-SHA drift, non-author review) do the job PRs used to. A branch is only short-lived isolation (a reviewer's worktree): it returns a PATCH applied to `main`, never merged, deleted the same session. Old branches: `~/.local/share/meshforge/branch-archive/` bundles; alpha = tag `alpha-archived`. Open Dependabot PR branches are Dependabot's — never delete them.

---

## Development Principles

```
1. Make it work         ← First priority
2. Make it reliable     ← Security, testing
3. Make it maintainable ← Clean code, docs
4. Make it fast         ← Only when proven necessary
```

---

## Key Commands

```bash
# Launch
scripts/meshforge-launcher.sh           # Primary interface (TUI) — not a bare
#   `sudo python3 src/launcher_tui/main.py` (the launcher sets PYTHONPYCACHEPREFIX)
python3 src/standalone.py               # Zero-dependency RF tools
# GTK app REMOVED; the TUI is the only interface. ⚠️ org.meshforge.app.desktop,
# scripts/meshforge-terminal.sh, install-desktop.sh and assets/org.meshforge.policy
# (pkexec) are a LIVE launcher for this TUI — do not "clean up" any of it.
# Categories=Network;HamRadio (NOT System — that buried it in System Tools).

# Verify changes
python3 -m pytest tests/ -v
python3 -c "from src.__version__ import __version__; print(__version__)"

# Regression prevention (Issue #29)
python3 scripts/lint.py --all
python3 -m pytest tests/test_regression_guards.py -v
git config core.hooksPath .githooks
```

---

## Architecture Overview

**TUI Pattern**: Handler Registry (Protocol + BaseHandler + TUIContext). Each menu action is a self-contained handler in `handlers/` dispatched by `handler_registry.py`.

```
src/
├── launcher_tui/      # PRIMARY INTERFACE (TUI)
│   ├── main.py        # NOC launcher + handler registration
│   ├── handler_protocol.py  # CommandHandler Protocol + TUIContext + BaseHandler
│   ├── handler_registry.py  # register/lookup/dispatch
│   ├── backend.py           # whiptail/dialog abstraction
│   └── handlers/            # <!--STAT:handlers-->88<!--/STAT--> handlers (sentinel; action
│                            #   count: SSOT .claude/skills/meshforge/capability_index.md)
├── commands/          # propagation.py, hamclock.py, base.py
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py, gateway_cli.py, meshcore_handler.py
│   ├── canonical_message.py   # Multi-protocol message format
│   └── message_routing.py, message_queue.py (SQLite)
├── monitoring/        # mqtt_subscriber, node_monitor, traffic_inspector, packet_dissectors
├── plugins/           # eas_alerts, meshing_around, mqtt_bridge
│                     #   (MeshCore lives in gateway/meshcore_handler.py)
├── utils/             # rf.py, common.py, service_check.py, coverage_map.py, claude_assistant.py
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version + changelog
```

> Full architecture: `.claude/foundations/domain_architecture.md`

---

## Exploration Entry Points

| Question | Start here |
|----------|-----------|
| Service behavior | `src/utils/service_check.py` |
| Protocol routing | `src/gateway/message_routing.py` |
| TUI handler dispatch | `src/launcher_tui/handler_registry.py` |
| RF math | `src/utils/rf.py` |
| AI assistant | `src/utils/claude_assistant.py` |
| Coverage maps | `src/utils/coverage_map.py` |

---

## Deployment Profiles

Profiles: `radio_maps` | `monitor` | `meshcore` | `gateway` | `field` | `full`

```bash
python3 src/launcher.py --profile gateway   # Select profile
python3 src/launcher.py                      # Auto-detect (default)
# Profile saved to ~/.config/meshforge/deployment.json
```

> Full profile definitions + install commands: `.claude/foundations/deployment_profiles.md`

---

## Service Management

`utils/service_check.py` — **SINGLE SOURCE OF TRUTH** for systemd operations.

Key imports: `check_service`, `apply_config_and_restart`, `enable_service`, `start_service`, `stop_service`, `_sudo_cmd`, `_sudo_write`

**Privilege Separation**:
- **Viewer Mode** (default, no sudo): Monitoring, RF calcs, API data
- **Admin Mode** (sudo): Service control, `/etc/` config, hardware

---

## Key Modules

| Module | API |
|--------|-----|
| `utils/diagnostic_engine.py` | `diagnose(symptom, category, severity)` |
| `utils/knowledge_base.py` | `get_knowledge_base().query("topic")` |
| `utils/claude_assistant.py` | AI assistant (Standalone + PRO tiers) |
| `utils/coverage_map.py` | Folium coverage map generator |
| `commands/propagation.py` | Space weather (NOAA primary) |

---

## Auto-Review

```bash
# CLI entrypoint (python3 -c is deny-listed; runner lives in auto_review.py __main__)
python3 src/utils/auto_review.py
```

---

## Commit Style

```
feat: Add new feature       fix: Bug fix
docs: Documentation         refactor: Code restructure
test: Add tests             security: Security fix
```

---

## Pre-Push Check (four lines, every push)

Before `git push origin main`, mentally run:

1. **On `main`?** — `git branch --show-current` says `main`. Solo workflow per branch strategy above.
2. **Lint green?** — `python3 scripts/lint.py --all` exits 0. The pre-commit hook covers this, but verify if you bypassed.
3. **New runtime dep?** — If `import X` got added, is `X` in `requirements.txt`? CI installs the minimal-deps profile and will surface the gap (see Issue #29 / `project_ci_red_2026_05_03_cascade`).
4. **New service unit or DB?** — If you added a new systemd unit, does `templates/systemd/` carry it? If you added a new SQLite DB, does `utils.db_inventory` have the `DBSpec` (MF013)?

If any line says "no", fix before pushing — a bad commit reaches all nine
boxes at once on the next deploy.

**A push is NOT a deploy** — nothing auto-pulls (the 6h `fleet_sync.sh
--memory-only` cron deploys no code; post-commit fixes only the committing box). The fleet moves only on
`scripts/fleet_sync.sh` THEN `scripts/fleet_pull.sh` (reversed, sync reads
every unit "unchanged" and skips every restart at exit 0). Until then
`honest_status.sh`'s `fleet SHA drift` leg is FAIL: a deploy owed, not a regression.

**Hooks must be ON**: `git config core.hooksPath` says `.githooks` (pre-commit:
lint + regression guards + collection sweep; pre-push: lint + guards + CI advisory).

**After pushing, verify it landed**: `bash scripts/honest_status.sh` re-derives
CI for HEAD, per-box SHA, suite, lint, live `confirmation_rate ≤ 1.0`, watchdog signals. `exit 0` = verified;
UNKNOWN is never a pass. `--quick` skips the local suite; `--strict` fails on fleet warnings.

---

## Research Docs (`.claude/`)

| File | Contents |
|------|----------|
| `foundations/harness_map.md` | **How a Claude session works here** — hooks→claim-gate→ledger→mini→probes→paging spine, truth sources, verification invariants, cold-start quickstart. Executable check: `scripts/harness_audit.sh` |
| `foundations/meshforge_ecosystem.md` | All 5 repos, boundaries, APIs (canonical) |
| `foundations/domain_architecture.md` | Core vs Plugin model |
| `foundations/ai_principles.md` | Human-centered design philosophy |
| `foundations/in_domain_principle.md` | **Never quit the app to fix it** — in-app remediation (MF018) |
| `foundations/persistent_issues.md` | **Known issues & fixes** |
| `plans/branch_convergence_guide.md` | main ↔ alpha merge strategy |
| `plans/qa_field_testing_plan.md` | Gateway, maps, MeshCore field-test protocol |
| `plans/standalone_wireclaw_variant.md` | mini-dudeai standalone (Pi-brain + ESP32-edge, chat-compiler) design |
| `INDEX.md` | Full doc index with quick lookups |
| `research/README.md` | <!--STAT:researchdocs-->56<!--/STAT--> technical deep dives (RNS, AREDN, RF, etc.) |

---

*Made with aloha for the mesh community*
