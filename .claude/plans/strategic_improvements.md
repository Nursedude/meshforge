# MeshForge Strategic Improvements Plan

> **Mission**: Make MeshForge a dependable, intelligent NOC for HAMs and network engineers
> **Principle**: No bloat — every feature has purpose and meaning

---

## Analysis Summary

After deep analysis of the codebase, MeshForge has strong foundations:

**Strengths**:
- Solid gateway bridging (Meshtastic ↔ RNS)
- Comprehensive diagnostic engine with 25+ rules
- Rich knowledge base (offline-capable)
- TUI-based interface (primary, whiptail/dialog)
- Strong security practices and test coverage (~1,986 tests across 70 files)

**Underutilized Assets**:
- `analytics.py` - SQLite stores exist but not exposed in UIs
- `diagnostic_history.db` - Tracks diagnoses but no trend analysis
- `claude_assistant.py` - PRO mode underutilized
- `message_queue.py` - Persistent queue not exposed for replay/analysis

---

## Prioritized Improvements

### Tier 1: High Impact, Aligned with Core Mission

#### 1. Predictive Network Health (Analytics → Intelligence)

**Problem**: Engineers react to failures instead of preventing them.

**Solution**: Wire analytics data into diagnostic engine for proactive alerts.

**Implementation**:
```
analytics.db (link_budget_history, network_health)
           ↓
   Trend Analyzer (new module)
           ↓
   Predictive Alerts → UI Notifications
```

**Key Features**:
- Battery discharge curve analysis → "Node X likely offline in 4 hours"
- SNR degradation tracking → "Link Y quality declining, check antenna"
- Packet loss patterns → "Congestion detected on channel 3"

**Files to Modify**:
- `src/utils/analytics.py` - Add trend analysis methods
- `src/utils/diagnostic_engine.py` - Add predictive rules
- TUI dashboard mixin - Show health trends

**Why This Matters**: Turns MeshForge from reactive monitoring to proactive network management.

---

#### 2. Message Lifecycle Visibility

**Problem**: "Why didn't my message arrive?" is the #1 troubleshooting question.

**Solution**: Track and display message states through the system.

**Message States**:
```
CREATED → QUEUED → SENT → RELAYED → DELIVERED → ACK
                     ↓
               TIMEOUT/FAILED (with reason)
```

**Implementation**:
- Extend `gateway/message_queue.py` with state tracking
- Add message history query API
- Create message trace UI panel

**Files to Modify**:
- `src/gateway/message_queue.py` - Add state machine
- `src/commands/gateway.py` - Add trace command
- TUI messaging mixin - Display message trace

**Why This Matters**: Engineers need to diagnose routing issues, not guess.

---

#### 3. Unified Health Dashboard (Single Pane of Glass)

**Problem**: Status information scattered across multiple panels.

**Solution**: Correlated health view showing all services with agreement status.

**Design**:
```
┌─────────────────────────────────────────────────┐
│  MESHFORGE HEALTH               Last: 2s ago   │
├─────────────────────────────────────────────────┤
│  SERVICES                                       │
│  meshtasticd  ● ONLINE   [4/4 checks agree]    │
│  rnsd         ● ONLINE   [3/4 checks agree]    │
│  gateway      ● BRIDGING [12 msg/min]          │
├─────────────────────────────────────────────────┤
│  NETWORK HEALTH                                 │
│  Nodes: 8 online, 2 stale    Packets: 94% ok  │
│  Avg SNR: -8.2 dB            Utilization: 12% │
├─────────────────────────────────────────────────┤
│  RECENT ISSUES                                  │
│  14:32 ⚠ Link to !abc123 degraded (SNR -15)   │
│  14:28 ✓ rnsd recovered automatically          │
└─────────────────────────────────────────────────┘
```

**Files to Create**:
- TUI health dashboard mixin or handler

**Why This Matters**: One view to rule them all — instant situational awareness.

---

### Tier 2: Engineer/Scientist Tools

#### 4. RF Link Analysis Tool

**Problem**: Engineers need to validate links before deployment.

**Solution**: Interactive link budget calculator with visualization.

**Features**:
- Input: TX power, antenna gains, distance, frequency, terrain
- Output: Expected SNR, margin, Fresnel clearance
- Visual: Path profile with terrain (if elevation data available)

**Existing Assets**:
- `src/utils/rf.py` - Has FSPL, Fresnel, link budget calculations
- `src/gtk_ui/panels/ham_tools.py` - Has basic RF tools

**Enhancement**:
- Add terrain profile import (GPX, CSV)
- Show Fresnel zone clearance visually
- Integrate with coverage map

**Why This Matters**: Pre-deployment validation saves hours of field debugging.

---

#### 5. AI-Assisted Troubleshooting (Deep Integration)

**Problem**: Claude assistant exists but isn't wired into workflows.

**Solution**: Context-aware AI throughout the UI.

**Integration Points**:
- Diagnostics panel: "Explain this error" button
- Message trace: "Why did this fail?" analysis
- Health dashboard: "What should I check first?"
- Configuration: "Review my settings" validation

**Implementation**:
```python
# In any panel
def on_explain_clicked(self):
    context = self.gather_current_context()
    explanation = self.assistant.explain(context)
    self.show_explanation_dialog(explanation)
```

**Files to Modify**:
- `src/utils/claude_assistant.py` - Add context-aware methods
- TUI mixins - Add "AI Assist" menu options

**Why This Matters**: Leverages existing AI capability where users need it.

---

### Tier 3: Operational Excellence

#### 6. Configuration Validation Engine

**Problem**: Bad configs cause silent failures.

**Solution**: Pre-flight checks before applying configuration.

**Checks**:
- RNS config syntax validation (already exists, extend)
- Meshtasticd config compatibility
- Gateway route validation
- Frequency/region compliance

**Files**:
- Extend `src/commands/rns.py` validate_config()
- Add `src/utils/config_validator.py`

---

#### 7. Export/Report Generation

**Problem**: Engineers need to document and share findings.

**Solution**: One-click diagnostic report generation.

**Report Contents**:
- System state snapshot
- Recent diagnostic history
- Network topology
- Configuration summary
- Recommendations

**Format**: Markdown + JSON (machine-readable)

---

## What NOT to Build (Avoiding Bloat)

| Feature | Reason to Skip |
|---------|----------------|
| Full SDR integration | Out of scope, use dedicated SDR software |
| APRS gateway | Different protocol domain |
| Multi-site federation | Complexity without clear demand |
| Mobile app | Web UI sufficient for remote access |
| Custom map tiles | Use existing tile servers |

---

## Implementation Order

### Sprint A: Foundation (COMPLETED)
- [x] Single Source of Truth (status consistency)
- [x] Pre-commit quality gates
- [x] API documentation

### Sprint B: Analytics & Prediction (COMPLETED)
- [x] Wire analytics.db into diagnostic engine
- [x] Add trend analysis methods (PredictiveAnalyzer class)
- [x] Create health dashboard (analytics mixin)
- [x] Add predictive alerts (PREDICTIVE category)
- [x] 27 new tests for predictive analytics

### Sprint C: Message Visibility (COMPLETED)
- [x] Message state machine in queue (MessageLifecycleState enum)
- [x] Trace API (get_message_trace, get_message_summary)
- [x] 18 new tests for message lifecycle tracking
- [ ] Message trace UI panel (deferred to Sprint E)

### Sprint D: AI Deep Integration
- [ ] Context-aware assistant methods
- [ ] AI assist buttons in panels
- [ ] Configuration review feature

### Sprint E: Engineer Tools
- [ ] Enhanced link budget tool
- [ ] Terrain profile import
- [ ] Report generation

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Test coverage | ~1,986 tests | 2,000+ |
| Mean time to diagnose | Manual | <30 seconds |
| False positive rate | ~5% | <1% |
| TUI menus with AI assist | 1 | 5+ |

---

## Architecture Principle

```
Every feature must answer: "How does this help an engineer
diagnose, configure, or monitor their mesh network?"

If the answer is unclear, don't build it.
```

---

*Created: 2026-01-17*
*Updated: 2026-02-27 — Removed GTK references (TUI is sole interface), updated metrics*
*MeshForge v0.5.4-beta*
