# Documentation Audit & Consolidation

> **Purpose**: Single source of truth for documentation structure.
> Identifies conflicts, fragmentation, and establishes hierarchy.

---

## Critical Conflicts Found

### 1. Architectural Vision Contradiction

| Document | Location | Says |
|----------|----------|------|
| `DUDE_AI_UNIVERSITY.md` | Root (75 lines) | "NO GUI (No GTK, No Web Server)" - headless appliance |
| `dude_ai_university.md` | .claude/ (1206 lines) | "Multi-interface - GTK, CLI, Web UI" |

**Resolution Needed**: Which is the current vision?
- The root file describes an "April Shift" to headless + separate Windows NOC
- The .claude file describes the current GTK-based multi-interface system
- The actual codebase has GTK panels, web interface, CLI - matching .claude version

**Recommendation**: The root DUDE_AI_UNIVERSITY.md appears outdated or represents future state. Current truth is in .claude/dude_ai_university.md.

---

### 2. README Border Rendering Issues

The README.md uses UTF-8 box-drawing characters (╔═╗║) that:
- Render correctly on GitHub
- Appear as `M-bM-^UM-^T` garbage in non-UTF-8 terminals
- May confuse text-based tools

**Recommendation**: Consider ASCII-only fallback or simpler header.

---

### 3. Documentation Fragmentation

**51 .md files** across multiple locations:

```
Root Level (13 files) - User-facing documentation
├── README.md              # Main entry point
├── CLAUDE.md              # AI assistant config
├── QUICK_START.md         # Getting started
├── INSTALL_OPTIONS.md     # Installation guide
├── DEVELOPMENT.md         # Developer guide
├── CONTRIBUTING.md        # Contribution guide
├── ARCHITECTURE.md        # System architecture
├── SECURITY.md            # Security policy
├── ... others ...

.claude/ Directory (38 files) - Development knowledge
├── dude_ai_university.md  # Main knowledge base (AUTHORITATIVE)
├── foundations/           # Core principles
│   ├── ai_principles.md
│   ├── persistent_issues.md
│   └── ...
├── research/              # Technical deep-dives
│   ├── rns_integration.md
│   ├── hamclock_api.md
│   └── ...
├── audits/                # Code reviews
└── analysis/              # Design patterns
```

**Problem**: No clear hierarchy. Multiple files cover same topics.

---

## Recommended Documentation Hierarchy

### Tier 1: Single Source of Truth
1. **README.md** - Project overview (user-facing)
2. **.claude/dude_ai_university.md** - Complete knowledge base (AI/dev)
3. **CLAUDE.md** - Minimal config pointing to knowledge base

### Tier 2: Specialized References
- `.claude/research/` - Deep technical dives
- `.claude/foundations/` - Principles and patterns

### Tier 3: Deprecate or Merge
- `DUDE_AI_UNIVERSITY.md` (root) - Merge into .claude/ or delete
- `CLAUDE_CONTEXT.md` - Merge into CLAUDE.md
- `SESSION_NOTES.md` - Temporal, can be cleaned periodically

---

## Key Knowledge That Must Persist

### Development Patterns (from persistent_issues.md)
1. **Always use `get_real_user_home()`** not `Path.home()`
2. **WebKit doesn't work as root** - provide browser fallback
3. **Verify services before using** - actionable error messages
4. **Log at appropriate levels** - INFO for user actions, not DEBUG

### Architecture Facts
- GTK4 + libadwaita for desktop UI
- Flask for web interface
- Runs with sudo (affects paths, WebKit)
- Bridges Meshtastic (LoRa) and RNS (cryptographic mesh)

### Target Audience
- HAM Radio Operators (callsigns, emergency comms)
- Network Engineers (infrastructure-grade reliability)
- RF Engineers (propagation analysis, site planning)

---

## Action Items

1. [ ] Resolve DUDE_AI_UNIVERSITY.md conflict (root vs .claude)
2. [ ] Simplify README header for universal rendering
3. [ ] Establish clear doc hierarchy
4. [ ] Remove duplicate information
5. [ ] Cross-reference persistent_issues.md in CLAUDE.md

---

*Created: 2026-01-06*
