# README Audit & Improvement Plan

*Dude AI double-tap assessment — what our audience actually needs*

---

## Our Audience

1. **HAM operators** — technically literate, value reliability, allergic to vapor
2. **Emergency comms (ARES/RACES)** — need things that work in disasters, no fluff
3. **Mesh tinkerers** — building off-grid networks, want to get started fast
4. **Developers** — want to contribute, need honest architecture info

**What they all share**: They can smell over-promising. They've been burned by projects that demo well but don't install.

---

## README Problems (Honest Assessment)

### Problem 1: Over-Promising
**Current**: Phases 2-4 list Wireshark integration, ML prediction, mobile companion, federation, LVGL embedded UI.

**Reality**: None of these exist. The bridge (our core feature) is "In Progress."

**Fix**: Remove Phases 3-4 entirely. Phase 2 is research — label it as such. Only show what works or is actively being built.

---

### Problem 2: "First open-source tool designed to bridge incompatible mesh protocols"
**Current**: This claim is in the first section.

**Reality**: The bridge is marked "In Progress" two paragraphs later. This is contradictory.

**Fix**: Change to "Building toward bridging incompatible mesh protocols" or remove the claim until the bridge actually passes messages end-to-end.

---

### Problem 3: First Launch Output is Fictional
**Current**: Shows a pretty `[Continue] [Configure] [Troubleshoot]` button interface.

**Reality**: Neither the GTK nor TUI interfaces show this. The TUI shows a whiptail menu. The GTK shows panels.

**Fix**: Show actual TUI first-run output or remove entirely.

---

### Problem 4: "MeshForge owns the complete stack"
**Current**: Architecture section says this.

**Reality**: MeshForge connects to meshtasticd and rnsd. It doesn't own them.

**Fix**: "MeshForge connects to and manages the complete stack" or just remove the claim.

---

### Problem 5: "Claude AI" in Architecture Diagram
**Current**: Intelligence Layer shows "Claude AI" as a component.

**Reality**: Claude AI is an optional PRO feature. Standalone diagnostics work offline.

**Fix**: Remove from architecture diagram. Mention in AI Diagnostics section only.

---

### Problem 6: "Who Uses MeshForge?" Implies Production Users
**Current**: Lists 5 user categories in present tense.

**Reality**: At 0.4.7-beta, there may be early adopters but this reads like marketing.

**Fix**: Change to "Who Is MeshForge For?" — positioning who SHOULD use it, not who does.

---

### Problem 7: Missing Practical Information
A HAM landing here wants:
- What radio hardware do I need? (Cost? Where to buy?)
- What Raspberry Pi model? (4? 5? Zero 2W? How much RAM?)
- What frequency band/region?
- Can I use it with my existing Meshtastic network?
- Does it require internet?

None of this is answered.

---

### Problem 8: Progress Bars Mislead
**Current**:
```
[####################] Install & Verification     <- Just completed
```

**Reality**: "Just completed" was weeks ago. If install works first try on a fresh Pi, great. If not, this bar is a lie.

**Fix**: Progress bars should reflect VERIFIED state, not code-exists state.

---

## Improvement TODO (Priority Order)

### P0 — Functionality (Must Fix)
1. **Verify fresh install works** — `scripts/install_noc.sh` on a clean Pi. If it fails, fix it before claiming anything.
2. **Test TUI launches correctly** — `sudo python3 src/launcher_tui/main.py` on a Pi with whiptail
3. **Test GTK launches correctly** — `sudo python3 src/main_gtk.py` on a Pi with display
4. **Verify gateway bridge can start** — Even if it doesn't pass messages yet, it should start without errors

### P1 — README Honesty
5. **Remove Phases 3-4** from roadmap — they're research, not planned features
6. **Fix "First Launch" output** — show real TUI output or remove
7. **Change "Who Uses" to "Who Is This For"**
8. **Remove "owns the complete stack"** claim
9. **Add hardware requirements section** — specific Pi models, RAM, radio hardware
10. **Remove Claude AI from architecture diagram**
11. **Add "Does it require internet?" FAQ** — answer: No for core features

### P2 — Code Quality
12. **Gateway bridge: Add integration test** — start bridge, verify it connects to meshtasticd mock
13. **Launcher TUI: Verify all menu items work** — some may reference features from the deleted Rich CLI
14. **Clean up launcher_tui/main.py** — still 2,600+ lines, needs mixin extraction (per CLAUDE.md guidelines)
15. **Add test for launcher.py** — verify environment detection and interface selection
16. **Remove stale __pycache__** — `find src -name __pycache__ -exec rm -rf {} +`

### P3 — Focus
17. **Gateway bridge validation** — The core differentiator. Make one message pass Meshtastic -> RNS.
18. **MQTT monitoring (existing)** — Already works without radio. Document it as the "no-hardware-needed" entry point.
19. **RF tools (existing)** — Already solid. Make them the showcase for new users who don't have hardware yet.
20. **Channel configuration** — The TUI has presets. Verify they write correct configs.

### P4 — Nice to Have
21. **Man page update** (`docs/meshforge.1`) — references deleted web UI
22. **SESSION_NOTES.md cleanup** — historical but confusing if someone reads it
23. **ARCHITECTURE.md** — references deleted files extensively

---

## Recommended README Structure (For Our Audience)

```
# MeshForge

One-line: what it does, concretely

## What Works Today
- Bullet list of VERIFIED features
- No "in progress" items in this section

## Hardware You Need
- Raspberry Pi (which models, how much RAM)
- Radio hardware (specific models, where to get them)
- Cost estimate

## Quick Start
- 3 commands to install and run
- What you'll see (real output, not mockup)

## What's Coming
- Gateway bridge status (honest)
- One or two near-term goals

## Architecture (For Contributors)
- How the pieces fit
- How to contribute
```

---

*— Dude AI*
