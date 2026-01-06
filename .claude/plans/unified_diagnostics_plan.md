# Unified Diagnostic Engine - Architecture Plan

> **Goal**: One industry-grade diagnostic engine that ALL UIs consume (CLI, GTK, Web)
> **Status**: Approved for implementation
> **Created**: 2026-01-06

---

## Problem Statement

Current state: **5-7 fragmented diagnostic implementations** with:
- Duplicated logic across files
- Different data structures that can't compose
- CLI works great (12 check areas) but GTK is limited
- Web UI has almost no diagnostics
- No error correlation across subsystems

| File | Lines | Issue |
|------|-------|-------|
| `cli/diagnose.py` | 620 | Not reusable by other UIs |
| `utils/network_diagnostics.py` | 671 | Good architecture, limited checks |
| `utils/gateway_diagnostic.py` | 889 | Best fix hints, separate from network |
| `gtk_ui/panels/diagnostics.py` | 902 | Tied to NetworkDiagnostics only |
| `diagnostics/system_diagnostics.py` | 956 | Rich-specific, meshtasticd context only |
| `tools/mesh_diag.py` | 354 | Standalone, no integration |

---

## Solution Architecture

```
                ┌────────────────────────────────┐
                │    DiagnosticEngine            │
                │    (Singleton, Thread-Safe)    │
                │                                │
                │  ┌──────────────────────────┐  │
                │  │ 9 Check Categories       │  │
                │  │ - services, network, rns │  │
                │  │ - meshtastic, serial     │  │
                │  │ - hardware, system       │  │
                │  │ - ham_radio, logs        │  │
                │  └──────────────────────────┘  │
                │                                │
                │  ┌──────────────────────────┐  │
                │  │ Callbacks & Events       │  │
                │  │ - Real-time updates      │  │
                │  │ - Persistent logging     │  │
                │  └──────────────────────────┘  │
                └────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐       ┌──────────┐       ┌─────────┐
   │   CLI   │       │   GTK    │       │   Web   │
   │         │       │          │       │         │
   │ Tables  │       │ Cards +  │       │ JSON +  │
   │ Icons   │       │ Panels   │       │ REST    │
   └─────────┘       └──────────┘       └─────────┘
```

---

## Core Data Structures

### 1. CheckResult
```python
@dataclass
class CheckResult:
    name: str                          # "meshtasticd service"
    category: CheckCategory            # CheckCategory.SERVICES
    status: CheckStatus                # PASS, FAIL, WARN, SKIP
    message: str                       # "Service is running"
    fix_hint: Optional[str] = None     # "sudo systemctl start meshtasticd"
    details: Optional[Dict] = None     # {"pid": 1234}
    duration_ms: Optional[float] = None
    timestamp: datetime
```

### 2. SubsystemHealth
```python
@dataclass
class SubsystemHealth:
    name: str                      # "meshtastic"
    status: HealthStatus           # HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN
    message: str                   # "2 failed checks"
    checks: List[CheckResult]      # All checks in this subsystem
    last_check: datetime
    fix_hint: Optional[str]        # First fix hint from failures
```

### 3. DiagnosticEvent
```python
@dataclass
class DiagnosticEvent:
    timestamp: datetime
    severity: EventSeverity        # DEBUG, INFO, WARNING, ERROR, CRITICAL
    source: str                    # Component name
    message: str
    category: Optional[CheckCategory]
    details: Optional[Dict]
    fix_hint: Optional[str]
```

---

## Check Categories (9 total)

| Category | Checks |
|----------|--------|
| **SERVICES** | meshtasticd, rnsd, nomadnet, bluetooth |
| **NETWORK** | internet, DNS, gateway, MQTT, ports |
| **RNS** | installed, config, daemon, port 29716, interface file |
| **MESHTASTIC** | library, CLI, TCP connection, web UI |
| **SERIAL** | ports, permissions, device detection |
| **HARDWARE** | SPI, I2C, GPIO, LoRa, temperature, SDR |
| **SYSTEM** | Python, packages, memory, disk, CPU, throttling |
| **HAM_RADIO** | callsign, NomadNet identity |
| **LOGS** | meshtasticd errors, rnsd errors |

---

## File Organization

```
src/
├── core/
│   └── diagnostics/
│       ├── __init__.py        # Exports
│       ├── models.py          # Data structures
│       ├── engine.py          # DiagnosticEngine singleton
│       ├── checks/            # Check implementations
│       │   ├── services.py
│       │   ├── network.py
│       │   ├── rns.py
│       │   ├── meshtastic.py
│       │   ├── hardware.py
│       │   ├── system.py
│       │   └── ham_radio.py
│       └── fix_hints.py       # Centralized fix hints
│
├── api/
│   └── diagnostics.py         # Web API endpoints
│
├── cli/
│   └── diagnose.py            # CLI consumer (refactored)
│
└── gtk_ui/
    └── panels/
        └── diagnostics.py     # GTK consumer (refactored)
```

---

## Engine API

```python
engine = DiagnosticEngine.get_instance()

# Run checks
results = engine.run_all()                    # All categories
results = engine.run_category(CheckCategory.RNS)  # Single category
result = engine.run_single("meshtasticd service") # Single check

# Real-time callbacks (for GTK/Web)
engine.register_check_callback(on_check)      # Each check result
engine.register_health_callback(on_health)    # Subsystem health changes
engine.register_event_callback(on_event)      # Logged events
engine.register_progress_callback(on_progress) # Progress updates

# Query results
health = engine.get_health()                  # All subsystem health
health = engine.get_health("meshtastic")      # Single subsystem
results = engine.get_results(category=cat)    # Filter by category
events = engine.get_events(limit=100)         # Recent events

# Reports
report = engine.generate_report()             # Full report object
path = engine.save_report()                   # Save to JSON file

# Background monitoring
engine.start_monitoring(interval=30)          # Auto-check every 30s
engine.stop_monitoring()

# Wizard support
wizard_data = engine.run_wizard('gateway')    # Interactive setup wizard
```

---

## Web API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/diagnostics/health` | GET | Overall health status |
| `/api/diagnostics/health/<subsystem>` | GET | Single subsystem health |
| `/api/diagnostics/checks` | GET | All check results |
| `/api/diagnostics/checks/run` | POST | Run diagnostics |
| `/api/diagnostics/events` | GET | Recent events |
| `/api/diagnostics/report` | GET | Full report JSON |
| `/api/diagnostics/report/save` | POST | Save report to file |
| `/api/diagnostics/wizard/<type>` | GET | Run wizard |

---

## Implementation Phases

### Phase 1: Core Engine
1. Create `src/core/diagnostics/` structure
2. Implement `models.py` with unified data structures
3. Implement `engine.py` with singleton and callbacks
4. Port all check implementations from existing files
5. Create centralized `fix_hints.py`

### Phase 2: Migrate Consumers
1. Refactor `cli/diagnose.py` to use engine
2. Refactor `gtk_ui/panels/diagnostics.py` to use engine
3. Create `api/diagnostics.py` for web endpoints
4. Update `tools/mesh_diag.py` to use engine

### Phase 3: Cleanup
1. Remove deprecated files:
   - `utils/network_diagnostics.py`
   - `utils/gateway_diagnostic.py`
   - `diagnostics/system_diagnostics.py`
2. Update imports across codebase
3. Add deprecation warnings during transition

### Phase 4: Testing
1. Unit tests for engine
2. Integration tests for each consumer
3. Verify all existing functionality preserved
4. Performance testing for callbacks

---

## Key Design Decisions

1. **Singleton Pattern**: One engine per process, thread-safe
2. **Callback-Driven**: GUI/Web get real-time updates without polling
3. **Category-Based**: Checks grouped logically for selective runs
4. **Persistent Logging**: Events written to daily log files
5. **Unified Fix Hints**: Centralized database of actionable fixes
6. **JSON Serialization**: All models have `to_dict()` for API/CLI

---

## Files to Preserve Best Patterns From

| File | Pattern to Preserve |
|------|---------------------|
| `network_diagnostics.py` | Singleton, callbacks, persistent logging |
| `gateway_diagnostic.py` | Fix hints, wizard flow |
| `cli/diagnose.py` | Comprehensive checks (12 areas) |

---

## Success Criteria

- [ ] All 9 check categories implemented
- [ ] CLI, GTK, Web all use same engine
- [ ] Real-time updates work in GTK
- [ ] Web API endpoints functional
- [ ] Fix hints display everywhere
- [ ] Background monitoring works
- [ ] Reports generate correctly
- [ ] Zero duplicated check logic

---

*Last updated: 2026-01-06*
