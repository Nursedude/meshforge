# MeshForge HAM Integration Roadmap

> **Purpose**: Make all HAM integrations fully capable
> **Created**: 2026-01-06
> **Status**: Planning

---

## Current State Assessment

| Integration | Completeness | Status |
|-------------|--------------|--------|
| **HamClock** | 60% | Working, needs VOACAP & auto-refresh |
| **RF Tools** | 90% | Solid foundation |
| **AREDN** | 70% | Working, needs topology viz |
| **Amateur Radio** | 35% | Stubbed, critical gaps |
| **Propagation** | 40% | Basic, needs predictions |

---

## High Priority (FCC Compliance & Safety)

### 1. Station ID Timer
**Why**: FCC Part 97.119 requires ID every 10 minutes
**Current**: Framework exists but not implemented
**Implementation**:
```python
# In amateur/callsign.py
class StationIDTimer:
    def __init__(self, callsign: str):
        self.callsign = callsign
        self.id_interval_minutes = 10
        self.last_id_time: Optional[datetime] = None

    def needs_id(self) -> bool:
        if not self.last_id_time:
            return True
        elapsed = datetime.now() - self.last_id_time
        return elapsed.total_seconds() >= self.id_interval_minutes * 60

    def record_id(self):
        self.last_id_time = datetime.now()
```
**Files**: `src/amateur/callsign.py`, `src/gtk_ui/panels/amateur.py`

### 2. Part 97 Compliance Checker
**Why**: Operators need to verify frequency/power/mode privileges
**Current**: ComplianceChecker class stubbed but not implemented
**Implementation**:
- Complete band privilege database (all HF/VHF/UHF bands)
- License class privilege mapping
- Frequency validation against band plan
- Power limit checking
- Mode validation by band segment
**Files**: `src/amateur/compliance.py`

### 3. Callsign Lookup (FCC ULS)
**Why**: Verify callsign validity, get operator info
**Current**: CallsignManager exists but no API integration
**Implementation**:
- FCC ULS API integration (https://data.fcc.gov/api/license-view/)
- QRZ.com API fallback (with subscription key)
- Local cache with expiration
- License expiration warnings
**Files**: `src/amateur/callsign.py`

---

## Medium Priority (Operational Capability)

### 4. VOACAP Propagation Predictions
**Why**: HAMs need band-by-band propagation forecasts
**Current**: HamClock has `get_voacap.txt` endpoint but not parsed
**Implementation**:
- Parse VOACAP response from HamClock
- Display band predictions (10m, 15m, 20m, etc.)
- Show reliability percentages
- Time-of-day predictions
**Files**: `src/gtk_ui/panels/hamclock.py`

### 5. Space Weather Auto-Refresh
**Why**: Solar conditions change; manual refresh is tedious
**Current**: Manual refresh only
**Implementation**:
- Configurable refresh interval (default 10 min)
- Background thread polling
- Status indicator for stale data
- Network failure retry with backoff
**Files**: `src/gtk_ui/panels/hamclock.py`

### 6. Complete Band Plan Reference
**Why**: HAMs need quick reference for band allocations
**Current**: Only 4 amateur bands defined (70cm, 23cm, 13cm, 33cm)
**Missing**: 160m, 80m, 40m, 30m, 20m, 17m, 15m, 12m, 10m, 6m, 2m, 1.25m
**Implementation**:
- Full ARRL band plan with sub-bands
- Mode allocations (CW, Phone, Digital)
- Power limits by sub-band
- Regional variations (ITU Region 1/2/3)
**Files**: `src/amateur/compliance.py`, `src/plugins/band_plan.py`

### 7. AREDN Mesh Topology Visualization
**Why**: Understand mesh network structure at a glance
**Current**: Node list only, no graph
**Implementation**:
- Use graphviz or networkx for layout
- Show nodes and RF links
- Color-code by link quality
- Hop count display
**Files**: `src/gtk_ui/panels/aredn.py`

---

## Lower Priority (Enhanced Experience)

### 8. Link Quality Trending
**Why**: See link stability over time
**Current**: Snapshot only
**Implementation**:
- Store historical link quality (SQLite or JSON)
- 24-hour trend graph
- Alert on degradation
**Files**: `src/gtk_ui/panels/aredn.py`

### 9. Terrain Propagation Models
**Why**: Real-world path loss differs from free-space
**Current**: Only FSPL (ideal)
**Implementation**:
- Hata model for urban/suburban
- Add to RF calculator as option
- Compare ideal vs. realistic
**Files**: `src/utils/rf.py`, `src/tools/rf_tools.py`

### 10. Aurora Forecast Visualization
**Why**: VHF operators care about aurora openings
**Current**: Aurora data fetched but not visual
**Implementation**:
- Aurora oval display
- Kp threshold alerts
- 6-hour forecast
**Files**: `src/gtk_ui/panels/hamclock.py`

### 11. DX Cluster Integration
**Why**: Real-time propagation reports from other HAMs
**Current**: HamClock has `get_dxspots.txt` but not used
**Implementation**:
- Parse DX spots from HamClock
- Filter by band
- Display recent spots
- Alert on wanted DX
**Files**: `src/gtk_ui/panels/hamclock.py`

### 12. ARES/RACES Drill Templates
**Why**: Emergency communication practice
**Current**: ICS-213 template exists but no drill support
**Implementation**:
- Message relay practice mode
- Traffic list management
- Drill scoring
**Files**: `src/amateur/ares_races.py`

---

## Implementation Order

### Phase 1: Compliance (Critical)
1. Station ID Timer
2. Part 97 Compliance Checker
3. Callsign Lookup

### Phase 2: Propagation (High Value)
4. VOACAP Predictions
5. Auto-Refresh
6. Band Plan Reference

### Phase 3: Visualization (UX)
7. AREDN Topology
8. Link Trending
9. Aurora Forecast

### Phase 4: Advanced (Nice-to-have)
10. Terrain Models
11. DX Cluster
12. ARES/RACES Drills

---

## File Structure After Implementation

```
src/amateur/
├── callsign.py          # + FCC ULS API, Station ID Timer
├── compliance.py        # + Full band plan, privilege checker
├── ares_races.py        # + Drill templates
└── band_plan.py         # NEW: Complete ARRL band plan

src/gtk_ui/panels/
├── hamclock.py          # + VOACAP, Auto-refresh, DX Cluster
├── aredn.py             # + Topology graph, Link trending
└── amateur.py           # + Station ID, Compliance UI

src/utils/
├── rf.py                # + Hata model option
└── propagation.py       # NEW: Propagation models
```

---

## Success Criteria

A HAM operator using MeshForge should be able to:

1. **Compliance**: Verify they're operating within Part 97 limits
2. **Station ID**: Get automatic reminders to identify
3. **Propagation**: Know which bands are open right now
4. **Callsign**: Look up any callsign instantly
5. **Network**: See their mesh topology at a glance
6. **Conditions**: Monitor solar/geomagnetic conditions in real-time

---

*Roadmap maintained by: Dude AI*
*Last updated: 2026-01-06*
