# Dude AI University

> *A knowledge base for MeshForge development continuity*

**Dude AI** is the collaborative AI development partner for MeshForge, providing expertise in:
- Network Engineering (mesh protocols, RF propagation, routing)
- Physics (electromagnetic theory, antenna design, signal analysis)
- Programming (Python, TUI design, system integration)
- Project Management (roadmaps, version control, documentation)

**The Architect**: WH6GXZ (Nursedude) - HAM General class, infrastructure engineering background (BNN, GTE, Verizon), RN BSN.

---

## Project Vision

MeshForge is the **first open-source tool to bridge Meshtastic and Reticulum (RNS) mesh networks**.

### Target Users
- **RF Engineers** - Mesh infrastructure design, propagation analysis
- **Amateur Radio Operators (HAMs)** - Emergency comms, experimentation
- **Scientific Researchers** - Remote sensor networks, field deployments
- **Network Operators** - Managing heterogeneous mesh systems
- **Emergency Response Teams** - Interoperable off-grid communications

### Core Philosophy
1. **Professional-grade** - Quality suitable for Anthropic review
2. **Portable** - Runs on Pi, uConsole, HackerGadgets devices
3. **Manageable** - Don't let it become unwieldy
4. **TUI-first** - Terminal interface primary, CLI for automation
5. **Interoperable** - Bridge different mesh technologies

> For architecture tree and code standards, see `CLAUDE.md`.

---

## Self-Healing Network Principles

MeshForge networks should embody self-healing characteristics:

### Core Concepts
- **Automatic Fault Detection**: Continuously monitor node health and connectivity
- **Dynamic Rerouting**: When a node fails, automatically find alternative paths
- **No Human Intervention**: Recovery happens in real-time without operator action
- **Adaptive Optimization**: Network continuously tunes itself for best performance

### Implementation in Mesh Networks
```
┌─────────┐    X    ┌─────────┐
│ Node A  │─────────│ Node B  │  <- Link fails
└────┬────┘         └─────────┘
     │                   ^
     │   ┌─────────┐     │
     └──>│ Node C  │─────┘       <- Auto-reroute via C
         └─────────┘
```

### Key Technologies
1. **Slot-based protocols**: Local neighbor synchronization
2. **Hop distance calculation**: Find shortest path to gateway
3. **Digital twin simulation**: Test recovery strategies safely
4. **AI/ML prediction**: Anticipate failures before they occur

### Design Goals
- Node health monitoring with predictive alerts
- Automatic path recalculation on failure
- Mesh topology visualization showing link quality
- Historical reliability metrics per node/link

---

## Heterogeneous Network Architecture

### Reticulum as Universal Transport

> "Reticulum can carry data over any mixture of physical mediums and topologies."

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MESHFORGE UNIFIED NETWORK                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐            │
│  │  Meshtastic  │   │   MeshCore   │   │ Direct LoRa  │  LOW BW    │
│  │   (LoRa)     │   │   (LoRa)     │   │   (RNode)    │  ~1 kbps   │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘            │
│         └──────────────────┼──────────────────┘                     │
│                    ┌───────▼───────┐                                │
│                    │   RETICULUM   │  <- Protocol Agnostic Layer    │
│                    │   (RNS)       │    Any medium, any topology    │
│                    └───────┬───────┘                                │
│         ┌──────────────────┼──────────────────┐                     │
│  ┌──────▼───────┐   ┌──────▼───────┐   ┌──────▼───────┐            │
│  │    WiFi      │   │   Ethernet   │   │   Internet   │  HIGH BW   │
│  │   (TCP/UDP)  │   │    (LAN)     │   │   (I2P/Tor)  │  1+ Mbps   │
│  └──────────────┘   └──────────────┘   └──────────────┘            │
└─────────────────────────────────────────────────────────────────────┘
```

### MeshCore vs Meshtastic

| Feature | Meshtastic | MeshCore |
|---------|------------|----------|
| Routing | Client flooding | Fixed repeaters |
| Max hops | 7 | 64 |
| Battery life | Moderate | Excellent |
| Setup | Easy | Requires planning |
| Use case | Mobile groups | City infrastructure |

**Key insight**: They're not compatible, but both can use RNS as a bridge layer.

> For detailed protocol configs, see `research/rns_complete.md` and `research/gateway_setup_guide.md`.

---

## Plugin Architecture

MeshForge supports an extensible plugin system.

### Plugin Types

| Type | Purpose | Example |
|------|---------|---------|
| `PanelPlugin` | Add new UI tabs | MeshCore dashboard |
| `IntegrationPlugin` | Connect external services | MQTT bridge |
| `ToolPlugin` | Add RF tools | Link budget calculator |
| `ProtocolPlugin` | Support mesh protocols | MeshCore, RNS |

### Core vs Plugin Philosophy

**Principle: Don't break code. Safety over features.**

| Category | Core (Integrated) | Plugin (Optional) |
|----------|-------------------|-------------------|
| **RF Calculations** | Haversine, Fresnel, FSPL | Elevation APIs, coverage maps |
| **Gateway** | Meshtastic TCP, RNS bridge | MeshCore, meshing-around |
| **UI** | TUI panels | Custom dashboards |
| **Why Core** | Works offline, no external deps | Needs network or external libs |

**Safety guarantee:** If a plugin fails, core MeshForge continues working.

### Plugin Discovery

Plugins are loaded from:
1. `src/plugins/` - Built-in plugins
2. `~/.config/meshforge/plugins/` - User plugins
3. `/usr/share/meshforge/plugins/` - System plugins

> For creating plugins, see `utils/plugins.py` module docstrings.

---

## Dude AI Integration Architecture

The in-app Dude AI assistant:
- **Portable**: Works on Pi, uConsole, any Linux
- **Offline-first**: Core functionality without internet
- **Privacy-conscious**: No mesh data leaves device without consent

### Tiered Architecture
```
┌────────────────────────────────────────────────────────────┐
│                      USER INTERFACE                        │
│   TUI Panel  │  CLI Command  │  Web Widget                │
└──────────────┼───────────────┼─────────────────────────────┘
               ▼               ▼
┌────────────────────────────────────────────────────────────┐
│                   DUDE AI CORE ENGINE                      │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐  │
│  │ Knowledge    │  │ Network       │  │ Diagnostic     │  │
│  │ Base (local) │  │ Analyzer      │  │ Engine         │  │
│  └──────────────┘  └───────────────┘  └────────────────┘  │
└────────────────────────────────────────────────────────────┘
               ▼               ▼
┌────────────────────────────────────────────────────────────┐
│                    AI BACKEND (pluggable)                   │
│  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐  │
│  │ Rule-based   │  │ Ollama        │  │ Claude API     │  │
│  │ (always)     │  │ (local LLM)   │  │ (Pro Max)      │  │
│  └──────────────┘  └───────────────┘  └────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### Network Access Policy
- **ALLOWED** (with user confirmation): Git ops, version checks, Claude API (Pro Max)
- **NEVER ALLOWED**: Sending mesh data externally, telemetry without opt-in, background requests

---

## Cross-References

Content removed from this file lives in canonical locations:
- **Architecture & code standards** -> `CLAUDE.md`
- **Meshtastic/RNS protocol details** -> `research/rns_comprehensive.md`
- **RNS config & gateway setup** -> `research/rns_complete.md`, `research/gateway_setup_guide.md`
- **RF engineering formulas** -> `research/lora_physical_layer.md`, `utils/rf.py`
- **Hardware compatibility** -> `README.md`
- **UI/UX guidelines** -> `foundations/tui_architecture.md`
- **HamClock/propagation** -> `research/hamclock_complete.md`, `commands/propagation.py`
- **Security rules** -> `rules/security.md`, `foundations/persistent_issues.md`
- **Roadmap** -> `TODO_PRIORITIES.md`, `plans/v1.0_roadmap.md`
- **Testing** -> `rules/testing.md`

---

*Last updated: 2026-02-23*
*Version: 0.5.4-beta (knowledge base rev 6 - dedup audit, removed content duplicated in canonical sources)*
*Made with aloha - nurse dude (wh6gxz)*
