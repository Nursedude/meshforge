# MeshForge - Claude Code Configuration

> **Dude AI**: Network Engineer, Physicist, Programmer, Project Manager
> **Architect**: WH6GXZ (Nursedude) - HAM General, Infrastructure Engineering, RN BSN

## Quick Context

MeshForge is a **Network Operations Center (NOC)** bridging Meshtastic and Reticulum (RNS) mesh networks. First open-source tool to unify these incompatible mesh ecosystems.

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

# Version is in src/__version__.py (currently 0.5.0-beta)
```

## Architecture Overview

```
src/
├── launcher_tui/      # Terminal UI — PRIMARY INTERFACE
│   └── main.py        # NOC dispatcher (whiptail/dialog)
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py  # Main gateway bridge
│   └── message_queue.py # Persistent message queue (SQLite)
├── monitoring/        # Network monitoring
│   └── mqtt_subscriber.py # Nodeless MQTT monitoring
├── utils/             # RF tools, common utilities
│   ├── rf.py          # RF calculations (tested)
│   ├── rf_fast.pyx    # Cython optimization
│   ├── common.py      # SettingsManager
│   ├── service_check.py     # Service management (SINGLE SOURCE OF TRUTH)
│   ├── auto_review.py # Self-audit system
│   ├── diagnostic_engine.py # Intelligent diagnostics
│   ├── knowledge_base.py    # Mesh networking knowledge
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   └── coverage_map.py      # Folium map generator
├── launcher.py        # Auto-detect (falls through to TUI)
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version and changelog
```

## Code Standards

### Security (Non-negotiable)
- NO `shell=True` in subprocess calls
- NO bare `except:` clauses
- Validate all user inputs
- Use `subprocess.run()` with list args and timeouts

### Style
- Python 3.9+ features OK
- Type hints encouraged
- 4-space indentation
- ~100 char line limit

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

Deep documentation in `.claude/`:
- `dude_ai_university.md` - Complete project knowledge base
- `foundations/domain_architecture.md` - **ARCHITECTURE: Core vs Plugin model**
- `foundations/ai_principles.md` - Human-centered design philosophy
- `foundations/persistent_issues.md` - **CRITICAL: Known issues & fixes**
- `foundations/documentation_audit.md` - Doc structure & conflicts
- `research/README.md` - Index of technical deep dives (RNS, AREDN, HamClock, etc.)

## Architecture Model

**Privilege Separation** (see `foundations/domain_architecture.md`):
- **Viewer Mode** (default, no sudo): Monitoring, RF calcs, API data
- **Admin Mode** (sudo): Service control, /etc/ config, hardware

**Core vs Plugin**:
- **Core**: Gateway bridge, node tracker, RF tools, diagnostics
- **Plugins**: HamClock, AREDN, MQTT, third-party integrations

**Services run independently** - MeshForge connects to them, doesn't embed them.

## Persistent Issues (MUST READ)

Before making changes, review `.claude/foundations/persistent_issues.md`:

### Path.home() Bug
**NEVER use `Path.home()`** for user config files. It returns `/root` when running with sudo.
```python
# WRONG
config = Path.home() / ".config" / "meshforge"

# CORRECT
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshforge"
```

### WebKit Root Sandbox
WebKit doesn't work when running as root. Always provide browser fallback.

### Service Verification
Always check if services (rnsd, HamClock, meshtasticd) are running before using. Provide actionable error messages.

## Service Management (utils/service_check.py)

**SINGLE SOURCE OF TRUTH** for systemd service operations. Always use these helpers instead of raw subprocess calls.

### Checking Service Status
```python
from utils.service_check import check_service, ServiceState

# Check if service is available
status = check_service('meshtasticd')
if not status.available:
    show_error(status.message)
    show_fix(status.fix_hint)

# Check specific states
if status.state == ServiceState.FAILED:
    print("Service crashed - check logs")
elif status.state == ServiceState.NOT_RUNNING:
    print("Service stopped")
```

### Restarting Services (after config changes)
```python
from utils.service_check import apply_config_and_restart

# After modifying /etc/meshtasticd/config.yaml:
success, msg = apply_config_and_restart('meshtasticd')
if not success:
    show_error(msg)
```

### Enabling Services at Boot
```python
from utils.service_check import enable_service

# After creating a new service file:
success, msg = enable_service('rnsd')

# Enable AND start immediately:
success, msg = enable_service('meshtasticd', start=True)
```

### Daemon Reload Only
```python
from utils.service_check import daemon_reload

# After modifying service unit files:
success, msg = daemon_reload()
```

### Available Helpers
| Function | Use Case |
|----------|----------|
| `check_service(name)` | Pre-flight check before connecting |
| `apply_config_and_restart(name)` | After config file changes |
| `enable_service(name, start=False)` | After creating service files |
| `daemon_reload()` | After modifying .service units |

### Fallback Pattern (for compatibility)
```python
try:
    from utils.service_check import apply_config_and_restart
    _HAS_APPLY_RESTART = True
except ImportError:
    _HAS_APPLY_RESTART = False

# Usage:
if _HAS_APPLY_RESTART:
    success, msg = apply_config_and_restart('meshtasticd')
else:
    subprocess.run(['systemctl', 'daemon-reload'], timeout=30)
    subprocess.run(['systemctl', 'restart', 'meshtasticd'], timeout=30)
```

## File Size Guidelines

Split files exceeding 1,500 lines (see `.claude/foundations/persistent_issues.md` Issue #6):

**Current files needing attention (2026-02-04):**
- `traffic_inspector.py` (2,194) → Extract dissectors, models, storage
- `rns_bridge.py` (1,991) → Extract Meshtastic handler
- `node_tracker.py` (1,808) → Extract data classes
- `launcher_tui/main.py` (1,799) → **Regressed!** Extract network tools, web client mixins

**Previously refactored:**
- ✅ `launcher_tui/main.py` (was 2,822 → 1,336, now regressed to 1,799)
- ✅ `hamclock.py` (2,625 → 1,525)
- ✅ GTK4 panels removed (TUI is now only interface)

*Note: Always check if a mixin exists before adding to main.py.*

## Commit Style

```
feat: Add new feature
fix: Bug fix
docs: Documentation
refactor: Code restructure
test: Add tests
security: Security fix
```

## Intelligent Diagnostics System

MeshForge includes an AI-native diagnostics system:

### Standalone Mode (Offline)
```python
from utils.diagnostic_engine import diagnose, Category, Severity

# Report a symptom
diagnosis = diagnose(
    "Connection refused to meshtasticd",
    category=Category.CONNECTIVITY,
    severity=Severity.ERROR
)
if diagnosis:
    print(diagnosis.likely_cause)
    print(diagnosis.suggestions)
```

### Knowledge Base Query
```python
from utils.knowledge_base import get_knowledge_base

kb = get_knowledge_base()
results = kb.query("What is SNR?")
```

### AI Assistant
```python
from utils.claude_assistant import ClaudeAssistant

assistant = ClaudeAssistant()  # Standalone mode
response = assistant.ask("Why is my node offline?")
```

### Coverage Map Generation
```python
from utils.coverage_map import CoverageMapGenerator

gen = CoverageMapGenerator()
gen.add_nodes_from_geojson(geojson_data)
gen.generate("coverage_map.html")
```

## Contact

- GitHub: github.com/Nursedude/meshforge
- Callsign: WH6GXZ

---
*Made with aloha for the mesh community*
