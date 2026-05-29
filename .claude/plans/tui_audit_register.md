# TUI Reliability Audit — Register & Rubric

> **This file is the persistent source of truth for the audit — not the model's
> context.** Every selection's verdict is written here as it is assessed. If the
> session compacts or is `/clear`ed, resume from this file: nothing is lost.
> Staged 2026-05-29 (warm-context scaffold); rows filled by the audit session.

Mission (operator-set): reliability-test **every selection** in the TUI —
verify each works, remove bloat, optimize functionality + workflow. Lens =
the In-Domain Principle (`foundations/in_domain_principle.md`): if the user has
to quit the app to complete or fix a selection, that's a defect.

---

## How to run it (self-guided; survives a /clear)

**Architecture rule — defends against silent context degradation:** the main
loop holds only this table, never file dumps. Fan-out one read-only Explore
subagent per `menu_section`; each returns structured rows; append them here.
Keep the main context lean. Re-read this file after any compaction.

- **Phase 0 — Inventory.** Enumerate every selection. From repo root:
  `python3 -c "import sys; sys.path[:0]=['src','src/launcher_tui']; from handlers import get_all_handlers; [print(h.__name__, h.menu_section, h().menu_items()) for h in get_all_handlers()]"`
  → one row per (section, tag) below, status `TODO`. This is the worklist.
- **Phase 1 — Assess.** One fan-out per section. Each selection scored on the
  rubric. Mark **READ vs RAN** honestly (see Verification).
- **Phase 2 — Triage.** keep / fix / merge / **cut**. Operator ratifies every
  cut (don't silently delete — "verify the work-holder before retiring": confirm
  nothing depends on it and it's truly dead first).
- **Phase 3 — Execute.** Fix arcs, each through the remediation surface
  (`remediation.py`) or the config-form pattern; decrement the **MF018 baseline
  (76→0)** in `scripts/lint.py` as escapes close; ship → test → lint →
  `scripts/fleet_sync.sh` → verify on moc (`git -C /opt/meshforge`). Close one
  arc fully before opening the next.

## Verification honesty (non-negotiable)

The TUI needs whiptail/dialog, usually sudo, sometimes hardware (radios). Most
selections can only be **READ** (static trace) headless. Every row states which:

- `READ` — traced the code path; no crash/escape found by inspection.
- `RAN` — actually executed in the TUI and observed correct behavior.
- `RAN(hw)` — exercised against real hardware (operator-in-the-loop).
- Never record `RAN` for a path only read. "Looks fine" ≠ "verified."

## Bloat taxonomy

- **dead** — registered but unreachable, or never actually works.
- **dup** — duplicates another selection's function (e.g. service-control overlap).
- **dubious** — claims/prints but doesn't deliver (read-only "diagnostics" dressed
  as fixes; copy-paste-the-command flows). MF018 escapes often live here.

## Degradation tells (operator watch-list)

Re-asking a settled point · contradicting an earlier row · citing a file:line a
fresh read doesn't match · verdicts going vague ("looks fine") · claiming
"verified" without READ/RAN. **Recovery: `/clear`, resume from this file.**

---

## Register

Legend — **verified**: READ | RAN | RAN(hw) · **in-app**: yes | escape(MF018) |
mixed · **rec**: keep | fix | merge→\<tag\> | cut · **status**: TODO | DONE

| # | section | tag | handler | what it does | verified | in-app | risk | bloat | rec | notes | status |
|---|---------|-----|---------|--------------|----------|--------|------|-------|-----|-------|--------|
| — | _EXAMPLE (format only — delete in Phase 0)_ | `foo` | FooHandler | one-line purpose | READ | escape(MF018) | nano spawn | dubious | fix | route apply via surface | TODO |

### Already touched this session (expect green; re-verify, don't assume)
- `dashboard / mini_dudeai` (MiniDudeaiHandler) — findings → fixes via surface. In-app. RAN(tests).
- `dashboard / mini_dudeai_rules` (MiniDudeaiHandler) — rule-knob editor → candidate. In-app. RAN(tests).
- `rns_config` loglevel apply — now "Restart rnsd now" via surface. In-app. READ.
- `meshtasticd_radio` hardware-templates bootstrap — "Create templates now" via surface. In-app. READ.

---

## Rollup (fill as Phase 1 completes)

- selections total: _TBD (Phase 0)_
- keep / fix / merge / cut: _ / _ / _ / _
- MF018 escapes remaining: 76 (baseline in `scripts/lint.py`)
- RAN vs READ coverage: _ / _
