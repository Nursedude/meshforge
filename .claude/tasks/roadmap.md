# MeshForge Development Roadmap & Task List

> Comprehensive task list for ongoing development
> Generated: 2026-01-05

---

## Priority Legend
- **P1** - Critical / Blocking
- **P2** - Important / High Value
- **P3** - Enhancement / Nice to Have
- **P4** - Future / Research

---

## Completed This Session

- [x] Window sizing constraints (min 800x600, smart defaults based on monitor)
- [x] Responsive sidebar (auto-hide below 900px, F9 toggle)
- [x] Keyboard shortcuts (Ctrl+1-9 navigation, F9 sidebar, F11 fullscreen)
- [x] MeshForge University system (courses, lessons, assessments, progress tracking)
- [x] Design patterns documentation
- [x] Codebase structure analysis

---

## Active Development Tasks

### P1 - Critical

| Task | Description | Panel/Module | Status |
|------|-------------|--------------|--------|
| RNS/LXMF Bridge | Complete Reticulum LXMF messaging integration | rns.py | Pending |
| Error Suppression | Continue cleanup of noisy library output | main_gtk.py | In Progress |
| RPi Testing | Full test suite on Raspberry Pi hardware | All | Pending |

### P2 - Important

| Task | Description | Panel/Module | Status |
|------|-------------|--------------|--------|
| Refactor rns.py | Split 2189-line file into modules | rns.py | Pending |
| Refactor tools.py | Split 1752-line file into modules | tools.py | Pending |
| Refactor radio_config.py | Split 1635-line file into modules | radio_config.py | Pending |
| BasePanel Class | Create shared base class for panels | gtk_ui/ | Pending |
| Unified Settings | Single settings manager for all config | config/ | Pending |
| Type Hints | Add type annotations across codebase | All | Pending |
| MQTT Integration | Home automation bridge | New | Research |

### P3 - Enhancement

| Task | Description | Panel/Module | Status |
|------|-------------|--------------|--------|
| Offline Maps | Download/cache map tiles | map.py | Pending |
| Topo Maps | Topographic map layer support | map.py | Pending |
| ClockworkPi Support | uConsole hardware integration | hardware.py | Research |
| Theme System | User theme customization | settings.py | Pending |
| Plugin Architecture | Extensible plugin system | plugins/ | Pending |
| More University Content | Additional courses/assessments | university/ | Ongoing |

### P4 - Future

| Task | Description | Priority |
|------|-------------|----------|
| Test Coverage | Increase to 60%+ | Medium |
| Mobile Companion | React Native app concept | Low |
| Cloud Sync | Optional settings sync | Low |
| i18n | Internationalization | Low |

---

## Feature-Specific Tasks

### AREDN Integration (Completed Basic)
- [x] Node discovery/scanning
- [x] API client implementation
- [x] MikroTik setup wizard
- [ ] VLAN configuration helper
- [ ] Firmware download/flash automation
- [ ] Link quality visualization
- [ ] Service browser

### Reticulum/RNS (In Progress)
- [x] Basic panel structure
- [x] Interface management
- [ ] LXMF messaging UI
- [ ] Store-and-forward queue
- [ ] Gateway configuration wizard
- [ ] Network statistics dashboard

### MeshForge University (Core Complete)
- [x] Course management system
- [x] Progress tracking
- [x] Assessment engine
- [x] 6 initial courses
- [ ] Practical lab exercises
- [ ] Certificate generation
- [ ] More advanced courses

### Node Map
- [x] Basic WebKit map view
- [ ] Offline tile caching
- [ ] Topographic layer
- [ ] Link quality overlay
- [ ] Historical position tracking
- [ ] Export to KML/GPX

### System Tools
- [x] RF calculators
- [x] Fresnel zone calculator
- [ ] Antenna pattern visualizer
- [ ] Propagation simulator
- [ ] Channel planning tool

---

## Code Quality Tasks

### Refactoring
```
Files exceeding 1000 lines (target: <800):
- src/gtk_ui/panels/rns.py (2189 lines)
- src/gtk_ui/panels/tools.py (1752 lines)
- src/gtk_ui/panels/radio_config.py (1635 lines)
- src/main_web.py (2472 lines) - Web UI
```

### Technical Debt
- [ ] Replace print() with logger.debug() (remaining instances)
- [ ] Add docstrings to all public methods
- [ ] Consistent error handling across panels
- [ ] Remove deprecated code paths
- [ ] Consolidate duplicate utility functions

### Testing
- [ ] Unit tests for university module
- [ ] Unit tests for AREDN client
- [ ] Integration tests for service management
- [ ] UI snapshot tests (if feasible)

---

## Documentation Tasks

- [x] Code structure analysis (`.claude/analysis/meshforge_structure_review.md`)
- [x] Design patterns guide (`.claude/analysis/design_patterns.md`)
- [x] AREDN research (`.claude/research/aredn_integration.md`)
- [ ] User guide (installation, first setup)
- [ ] Developer contribution guide
- [ ] API documentation
- [ ] Troubleshooting guide

---

## Hardware Support Matrix

| Device | Status | Notes |
|--------|--------|-------|
| Raspberry Pi 4/5 | Supported | Primary target |
| Generic Linux | Supported | x86_64, ARM |
| MikroTik hAP ac3 | Supported | AREDN |
| ClockworkPi uConsole | Research | GPIO, display |
| RAK WisGate | Supported | LoRa gateway |
| Heltec LoRa32 | Supported | Via meshtasticd |

---

## Integration Roadmap

### Phase 1: Core Stability (Current)
- Error handling improvements
- Window/UI polish
- RPi optimization
- Bug fixes

### Phase 2: Communication
- Complete RNS/LXMF bridge
- MQTT home automation
- AREDN advanced features
- Cross-network messaging

### Phase 3: Visualization
- Enhanced maps
- Network topology graphs
- Signal analysis tools
- Historical data

### Phase 4: Extensibility
- Plugin system
- Custom themes
- External tool integration
- API for automation

---

## Quick Reference: File Locations

| Purpose | Location |
|---------|----------|
| GTK Panels | `src/gtk_ui/panels/` |
| Utilities | `src/utils/` |
| Configuration | `src/config/` |
| University | `src/university/` |
| Tests | `tests/` |
| Analysis Docs | `.claude/analysis/` |
| Research Docs | `.claude/research/` |
| Session Notes | `.claude/sessions/` |

---

## Session Notes

### 2026-01-05 Accomplishments
1. Fixed window sizing with monitor-aware defaults
2. Added responsive sidebar with F9 toggle
3. Implemented keyboard navigation (Ctrl+1-9)
4. Created MeshForge University learning system
5. Added 6 courses with assessments
6. Documented design patterns and workflow
7. Generated comprehensive task roadmap

### Next Session Focus
1. Continue RNS/LXMF messaging integration
2. Begin MQTT home automation research
3. Test on Raspberry Pi hardware
4. Address any reported bugs

---

*This roadmap is maintained in `.claude/tasks/roadmap.md`*
