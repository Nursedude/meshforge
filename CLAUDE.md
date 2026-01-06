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
# Launch interfaces
sudo python3 src/launcher.py      # Auto-detect UI
python3 src/standalone.py         # Zero-dependency mode

# Verify changes
python3 -m pytest tests/ -v       # Run tests
python3 -c "from src.__version__ import __version__; print(__version__)"

# Version is in src/__version__.py (currently 0.4.3-beta)
```

## Architecture Overview

```
src/
├── gateway/           # RNS-Meshtastic bridge
├── gtk_ui/            # GTK4 Desktop (panels/)
├── utils/             # RF tools, common utilities
│   ├── rf.py          # RF calculations (tested)
│   ├── rf_fast.pyx    # Cython optimization
│   ├── common.py      # SettingsManager
│   └── auto_review.py # Self-audit system
├── standalone.py      # Zero-dependency boot
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
- `research/` - Technical deep dives (RNS, AREDN, HamClock)

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

## File Size Guidelines

Split files exceeding 1,500 lines:
- `main_web.py` (2,478) → Flask blueprints
- `rns.py` (2,195) → Extract config editor
- `tools.py` (1,770) → Split into panels/

## Commit Style

```
feat: Add new feature
fix: Bug fix
docs: Documentation
refactor: Code restructure
test: Add tests
security: Security fix
```

## Contact

- GitHub: github.com/Nursedude/meshforge
- Callsign: WH6GXZ

---
*Made with aloha for the mesh community*
