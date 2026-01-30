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

# GTK4 desktop exists but is FROZEN (not actively developed)
# sudo python3 src/main_gtk.py

# Verify changes
python3 -m pytest tests/ -v       # Run tests
python3 -c "from src.__version__ import __version__; print(__version__)"

# Version is in src/__version__.py (currently 0.4.7-beta)
```

## Architecture Overview

```
src/
├── launcher_tui/      # Terminal UI — PRIMARY INTERFACE
│   └── main.py        # NOC dispatcher (whiptail/dialog)
├── gtk_ui/            # GTK4 Desktop — FROZEN (not actively developed)
├── gateway/           # RNS-Meshtastic bridge
│   ├── rns_bridge.py  # Main gateway bridge
│   ├── node_tracker.py # Unified node tracking (RNS + Meshtastic)
│   ├── network_topology.py # Network graph and path tracking
│   └── message_queue.py # Persistent message queue (SQLite)
├── monitoring/        # Network monitoring
│   └── mqtt_subscriber.py # Nodeless MQTT monitoring
├── utils/             # RF tools, common utilities
│   ├── rf.py          # RF calculations (tested)
│   ├── rf_fast.pyx    # Cython optimization
│   ├── common.py      # SettingsManager
│   ├── auto_review.py # Self-audit system
│   ├── diagnostic_engine.py # Intelligent diagnostics
│   ├── knowledge_base.py    # Mesh networking knowledge
│   ├── claude_assistant.py  # AI assistant (Standalone + PRO)
│   ├── map_data_service.py  # Node map server (MapServer class)
│   ├── topology_visualizer.py # D3.js topology visualization
│   └── coverage_map.py      # Folium coverage map generator
├── launcher.py        # Auto-detect (falls through to TUI)
├── standalone.py      # Zero-dependency RF tools
└── __version__.py     # Version and changelog

web/
└── node_map.html      # Live node map (Leaflet.js) — PRIMARY MAP UI
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

## File Size Guidelines

Split files exceeding 1,500 lines (see `.claude/foundations/persistent_issues.md` Issue #6):
- `launcher_tui/main.py` (2,822) → Extract menu handlers
- `hamclock.py` (2,625) → Extract API client
- `mesh_tools.py` (1,953) → Monitor

*Note: 7 UIs consolidated → TUI + browser maps. GTK4 frozen (exists, not developed).*

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

### Node Map Server (Primary Visualization)
The live node map (`web/node_map.html`) is the primary visualization for mesh nodes:
```python
from utils.map_data_service import MapServer, MapDataCollector

# Start HTTP server (accessible from other devices)
server = MapServer(port=5000)
server.start_background()  # Serves at http://localhost:5000

# Or just collect node data without server
collector = MapDataCollector()
geojson = collector.collect()  # Unified GeoJSON from all sources
```

Data sources merged by MapDataCollector:
- meshtasticd TCP (localhost:4403) — local mesh nodes
- MQTT subscriber — global/regional nodes
- Node tracker cache — RNS + Meshtastic nodes

### Topology Visualization
Export network topology in various formats:
```python
from utils.topology_visualizer import TopologyVisualizer
from gateway.network_topology import get_network_topology

# Create from live topology data
topology = get_network_topology()
viz = TopologyVisualizer.from_topology(topology)

# Export options
viz.generate("topology.html")           # Interactive D3.js graph
viz.export_geojson("nodes.geojson")     # For mapping tools
viz.export_graphml("topology.graphml")  # For Gephi
viz.export_csv(".")                     # nodes.csv + edges.csv
```

## Contact

- GitHub: github.com/Nursedude/meshforge
- Callsign: WH6GXZ

---
*Made with aloha for the mesh community*
