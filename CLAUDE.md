# MeshForge - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) - HAM General, Infrastructure Engineering, RN BSN

## Quick Context

MeshForge is a **Network Operations Center (NOC)** bridging Meshtastic and Reticulum (RNS) mesh networks. First open-source tool to unify these incompatible mesh ecosystems.

## Branch Strategy

| Branch | Version | Purpose |
|--------|---------|---------|
| `main` | `0.5.4-beta` | Stable beta — gateway bridge, TUI, monitoring, RF tools |
| `alpha/meshcore-bridge` | `0.6.0-alpha` | MeshCore integration — 3-way routing, MeshCore handler, companion radio management |

- **main** is the production-ready line. All PRs targeting stable features merge here.
  Includes tactical ops (XTOC interop, ATAK/KML/CoT), MQTT bridge, security hardening.
- **alpha/meshcore-bridge** has diverged significantly from main (~2,200 commits ahead,
  ~100 behind as of 2026-02-22). Contains MeshCore 3-way routing and handler but lacks
  main's tactical module, contact mapping, and several utilities. These are parallel
  development tracks; convergence requires a dedicated reconciliation effort.
- Feature branches use `claude/` prefix and merge via PR to the appropriate target branch.

## Development Principles

```
1. Make it work       ← First priority
2. Make it reliable   ← Security, testing
3. Make it maintainable ← Clean code, docs
4. Make it fast       ← Only when proven necessary
```

## Key Commands

```bash
# Launch
sudo python3 src/launcher_tui/main.py   # Primary interface (TUI)
python3 src/standalone.py               # Zero-dependency RF tools

# GTK4 desktop was REMOVED (TUI is the only interface now)

# Verify changes
python3 -m pytest tests/ -v       # Run tests
python3 -c "from src.__version__ import __version__; print(__version__)"

# Regression prevention (Issue #29)
python3 scripts/lint.py --all                          # Lint (MF001-MF010)
python3 -m pytest tests/test_regression_guards.py -v   # Regression guards
git config core.hooksPath .githooks                    # Enable pre-commit hook

# Version is in src/__version__.py
# main: 0.5.4-beta | alpha/meshcore-bridge: 0.6.0-alpha
```

## Architecture Overview

```
src/
├── launcher_tui/      # Terminal UI — PRIMARY INTERFACE
│   ├── main.py        # NOC dispatcher (whiptail/dialog)
│   ├── meshcore_mixin.py    # MeshCore TUI menu (alpha branch)
│   ├── rns_config_mixin.py  # RNS config editor (extracted)
│   └── rns_diagnostics_mixin.py  # RNS diagnostics (extracted)
├── commands/          # Command modules
│   ├── propagation.py # Space weather & HF propagation (NOAA primary)
│   ├── hamclock.py    # HamClock client (optional/legacy)
│   └── base.py        # CommandResult base class
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py  # Main gateway bridge
│   ├── gateway_cli.py # Headless CLI helpers (extracted)
│   ├── meshcore_handler.py    # MeshCore protocol handler (alpha branch)
│   ├── canonical_message.py   # Multi-protocol message format (alpha branch)
│   ├── meshcore_bridge_mixin.py # MeshCore bridge mixin (alpha branch)
│   ├── message_routing.py     # 3-way routing classifier (alpha branch)
│   └── message_queue.py # Persistent message queue (SQLite)
├── monitoring/        # Network monitoring
│   ├── mqtt_subscriber.py # Nodeless MQTT monitoring
│   ├── node_monitor.py    # Node status tracking
│   ├── traffic_inspector.py # Packet capture & analysis
│   └── packet_dissectors.py # Protocol-specific packet parsing
├── plugins/           # Protocol plugins
│   └── meshcore.py    # MeshCore plugin (alpha branch)
├── utils/             # RF tools, common utilities
│   ├── rf.py          # RF calculations (tested)
│   ├── rf_fast.pyx    # Cython optimization
│   ├── common.py      # SettingsManager
│   ├── service_check.py     # Service management (SINGLE SOURCE OF TRUTH)
│   ├── auto_review.py # Self-audit system
│   ├── diagnostic_engine.py # Intelligent diagnostics
│   ├── knowledge_base.py    # Mesh networking knowledge
│   ├── classifier.py  # Traffic routing classifier
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   └── coverage_map.py      # Folium map generator
├── launcher.py        # Auto-detect (falls through to TUI)
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version and changelog
```

## Deployment Profiles

MeshForge supports 5 deployment scenarios. Dependencies don't block your choice.

| Profile | Services Needed | Install | Use Case |
|---------|----------------|---------|----------|
| `radio_maps` | meshtasticd | `pip install -r requirements/core.txt -r requirements/maps.txt` | Radio config + coverage maps |
| `monitor` | (none) | `pip install -r requirements/core.txt -r requirements/mqtt.txt` | MQTT packet analysis |
| `meshcore` | (none) | `pip install -r requirements/core.txt` + meshcore | MeshCore companion radio |
| `gateway` | meshtasticd, rnsd | `pip install -r requirements/core.txt -r requirements/rns.txt -r requirements/mqtt.txt` | Meshtastic <> RNS bridge |
| `full` | meshtasticd, rnsd, mosquitto | `pip install -r requirements.txt` | Everything |

```bash
# Select profile at launch
python3 src/launcher.py --profile gateway

# Auto-detect (default): scans running services and installed packages
python3 src/launcher.py

# Profile is saved to ~/.config/meshforge/deployment.json
# Setup wizard also offers profile selection
```

**Key files**: `src/utils/deployment_profiles.py` (definitions), `src/utils/startup_health.py` (health checks), `src/utils/defaults.py` (constants), `src/utils/validation.py` (input validators)

## Code Standards

**Security**: See `.claude/rules/security.md` (auto-loaded). Non-negotiable: no `shell=True`, no bare `except:`, validate inputs, timeouts on subprocess.

**Service Interaction Rules** (Issue #29 — regression prevention):
- **NEVER** create `TCPInterface()` directly — use `MeshtasticConnection` from `connection_manager.py` or acquire `MESHTASTIC_CONNECTION_LOCK` first (meshtasticd supports ONE TCP client)
- **NEVER** read `/api/v1/fromradio` in TX paths — use `send_text_direct()` from `meshtastic_protobuf_client.py`
- **NEVER** call `RNS.Reticulum()` without `configdir=` — causes EADDRINUSE when rnsd is running
- **NEVER** use raw `systemctl is-active` for service state — use `check_service()` from `service_check.py`
- **ALWAYS** use `_stop_event.wait()` instead of `time.sleep()` in daemon loops

**Style**: Python 3.9+, type hints encouraged, 4-space indent, ~100 char lines.

## When Exploring

Use the Explore agent for:
- "Where is X implemented?"
- "How does feature Y work?"
- "What files handle Z?"

## Auto-Review System

Run self-audit:
```python
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Issues: {report.total_issues}')
"
```

## Research Documents

Deep documentation in `.claude/` (~48 active files after 2026-02-23 dedup audit):
- `foundations/meshforge_ecosystem.md` - **ECOSYSTEM: All 5 repos, boundaries, APIs** (canonical)
- `dude_ai_university.md` - Project vision, self-healing principles, plugin & Dude AI architecture
- `foundations/domain_architecture.md` - **ARCHITECTURE: Core vs Plugin model**
- `foundations/ai_principles.md` - Human-centered design philosophy
- `foundations/persistent_issues.md` - **CRITICAL: Known issues & fixes**
- `INDEX.md` - Full documentation index with quick lookups
- `research/README.md` - Index of 21 technical deep dives (RNS, AREDN, HamClock, RF, etc.)

## Architecture Model

**Privilege Separation** (see `foundations/domain_architecture.md`):
- **Viewer Mode** (default, no sudo): Monitoring, RF calcs, API data
- **Admin Mode** (sudo): Service control, /etc/ config, hardware

**Core vs Plugin**:
- **Core**: Gateway bridge, node tracker, RF tools, diagnostics
- **Plugins**: HamClock, AREDN, MQTT, third-party integrations

**Services run independently** - MeshForge connects to them, doesn't embed them.

## Persistent Issues (MUST READ)

**Before making changes**, review `.claude/foundations/persistent_issues.md`. Critical items:
- **#1 Path.home()**: NEVER use directly — use `utils.paths.get_real_user_home()` (see `rules/security.md` MF001)
- **#3 Service verification**: Always check services before using — use `utils.service_check`
- **#5 safe_import**: Only for external deps, never first-party modules
- **#6 File size**: Split files exceeding 1,500 lines

## Service Management

`utils/service_check.py` is the **SINGLE SOURCE OF TRUTH** for systemd operations. Read its docstrings for API details. Key imports: `check_service`, `apply_config_and_restart`, `enable_service`, `start_service`, `stop_service`, `_sudo_cmd`, `_sudo_write`.

## Commit Style

```
feat: Add new feature
fix: Bug fix
docs: Documentation
refactor: Code restructure
test: Add tests
security: Security fix
```

## Key Modules (read docstrings for API details)

- `utils/diagnostic_engine.py` — `diagnose(symptom, category, severity)` for offline diagnostics
- `utils/knowledge_base.py` — `get_knowledge_base().query("topic")` for mesh knowledge
- `utils/claude_assistant.py` — AI assistant (Standalone + PRO tiers)
- `utils/coverage_map.py` — Folium coverage map generator
- `commands/propagation.py` — Space weather (NOAA primary, OpenHamClock/HamClock optional)

## Contact

- GitHub: github.com/Nursedude/meshforge
- Callsign: WH6GXZ

---
*Made with aloha for the mesh community*
