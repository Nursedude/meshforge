# MeshForge - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) — HAM General, Infrastructure Engineering, RN BSN

<!-- Auto-loaded by Claude Code -->
@.claude/rules/security.md
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
- **ALWAYS** split files exceeding 1,500 lines

> Full security rules: `.claude/rules/security.md`
> Known issues & fixes: `.claude/foundations/persistent_issues.md`

---

## Quick Context

MeshForge is a **Network Operations Center (NOC)** bridging Meshtastic and Reticulum (RNS) mesh networks — the first open-source tool to unify these incompatible ecosystems.

**Active context / current sprint**: per-box session handoff notes at `~/.claude/plans/gateway-session-notes-*.md` (not repo-tracked; each box maintains its own).

---

## Branch Strategy

| Branch | Version | Status |
|--------|---------|--------|
| `main` | `0.5.5-beta` | Stable — TUI, meshtasticd, RNS, RF tools. Field-tested. |

- **Sister project**: [MeshAnchor](https://github.com/Nursedude/meshanchor) — MeshCore-primary NOC, extracted from main on 2026-04-01.
- `main` includes XTOC/ATAK/CoT, MQTT bridge, security hardening. Gateway bridge, coverage maps, NOC map have unit tests but need field validation.
- MeshCore is available as an optional gateway handler on main.
- `CanonicalMessage` in `src/gateway/canonical_message.py` is the shared bridge contract — keep compatible with MeshAnchor's version.
- Solo dev workflow: commit direct to `main`, push via `git push origin main`. PR/feature-branch flow was retired 2026-04-19 (caused more divergence than it prevented). A permission hook may still deny pushes; use `origin` remote rather than literal SSH URL.
- Alpha branch archived as tag `alpha-archived`.

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
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools
# GTK4 desktop REMOVED — TUI is the only interface

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
│   └── handlers/            # 68 registered command handlers (2026-04-19)
├── commands/          # propagation.py, hamclock.py, base.py
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py, gateway_cli.py, meshcore_handler.py
│   ├── canonical_message.py   # Multi-protocol message format
│   └── message_routing.py, message_queue.py (SQLite)
├── monitoring/        # mqtt_subscriber, node_monitor, traffic_inspector, packet_dissectors
├── plugins/           # meshcore.py plugin wrapper
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

Profiles: `radio_maps` | `monitor` | `meshcore` | `gateway` | `full`

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

```python
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Issues: {report.total_issues}')
"
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

If any line says "no", fix before pushing — every fleet box pulls within seconds and runs the change.

---

## Research Docs (`.claude/` — 93 files)

| File | Contents |
|------|----------|
| `foundations/meshforge_ecosystem.md` | All 5 repos, boundaries, APIs (canonical) |
| `foundations/domain_architecture.md` | Core vs Plugin model |
| `foundations/ai_principles.md` | Human-centered design philosophy |
| `foundations/in_domain_principle.md` | **Never quit the app to fix it** — in-app remediation (MF018) |
| `foundations/persistent_issues.md` | **Known issues & fixes** |
| `plans/branch_convergence_guide.md` | main ↔ alpha merge strategy |
| `plans/qa_field_testing_plan.md` | Gateway, maps, MeshCore field-test protocol |
| `plans/standalone_wireclaw_variant.md` | mini-dudeai standalone (Pi-brain + ESP32-edge, chat-compiler) design |
| `INDEX.md` | Full doc index with quick lookups |
| `research/README.md` | 22 technical deep dives (RNS, AREDN, RF, etc.) |

---

*Made with aloha for the mesh community*
