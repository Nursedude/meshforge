---
name: warmstart
description: >
  Pull mini-dudeai's warm-start brief on demand — what mini saw while you were
  away, with an honest freshness banner re-derived from the last tick — PLUS a
  single all-boxes fleet posture pane. Use when starting work mid-session, after
  a /clear, or any time you want mini's current posture (active conditions,
  escalations, recent fires) and the whole fleet's health without the automatic
  SessionStart injection.

  Triggers: warmstart, warm start, mini brief, what did mini see, situation brief, fleet posture
---

# /warmstart — mini-dudeai warm-start brief (manual refresh)

mini-dudeai is the local 24/7 presence. `brief.py` writes its warm-start brief;
`warmstart.py` reads it and stamps an **honest freshness banner** derived from
`mini_dudeai_state.json`'s `last_tick_ts` *now* — so a brief frozen by a dead
daemon is flagged 🔴 STALE instead of lying about health.

## What to run

Run BOTH, in order — the local brief (this box, deep) then the fleet pane (every
box, shallow):

```bash
# 1. This box's warm brief — active conditions, escalations, recent fires
PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.warmstart

# 2. All-boxes posture — one line per box, per-box freshness re-derived now
PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.rollup
```

Both emit plain markdown to stdout. Read the brief first, then follow its
pointers in order: `mini_dudeai_history.jsonl` → `state` → memory. The rollup is
the breadth check — which boxes are healthy, stale, or running no mini.

(On a box that can't ssh the fleet — e.g. a gateway Pi — step 2 prints only the
local box; that's expected, run it from the manager box for the full pane.)

## Reading the banner (the part that matters)

- **🟢 FRESH** — last tick recent; the brief is current, trust it.
- **🔴 STALE** — last tick older than the stale window; the brief is FROZEN and
  may misreport health. **Verify live before acting** (the brief's own "alive"
  line is from generation time, not now). Check the watcher:
  `systemctl --user status meshforge-mini-dudeai`.
- **⚠️ FRESHNESS UNKNOWN** — no `last_tick_ts`; treat as historical.
- **no brief yet** — transient now: the daemon writes the brief every tick, so
  this only shows in the ~seconds between a restart and its first tick. Re-run,
  or force one with `python3 -m mini_dudeai --preset meshforge_fleet --brief`.
- **silent / "no brief and no state"** — mini has never run on this box.

## Fleet posture pane (step 2)

`rollup.py` ssh-fans the fleet (host list from `~/.config/meshforge/fleet_hosts`,
same as `fleet_sync`), reads each box's `mini_dudeai_state.json`, and folds in the
local box. Each box's freshness is re-derived **now** — same honesty contract as
the brief, applied fleet-wide. Per-box banners:

- **🟢 fresh** — daemon ticking; rule count + `src_errors` shown.
- **🔴 stale** — last tick past the window; that box's daemon may be down
  (`ssh <box> systemctl --user status meshforge-mini-dudeai`).
- **⚪ no_state / — no_mini** — box reachable but never ticked / runs no mini
  (e.g. a MeshAnchor-only box) — not an error.
- **❌ unreachable** — ssh transport failed (box down or network).

Problems sort to the top, so a clean pane means a clean fleet.

## Why manual *and* automatic

The automatic path is a `SessionStart` hook (settings.json) that injects this at
session start — because a cold session doesn't know to ask. `/warmstart` is the
on-demand refresh for mid-session, after a `/clear`, or when the brief may have
moved on since the session began. Same renderer, same honesty guarantee.
