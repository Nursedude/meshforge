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
- **🟢 fresh · 🔀 meshanchor mini** — ticking, but it's the *other* twin's
  daemon answering on MeshAnchor's convention. Healthy and foreign, not ours.
- **⚪ no_state** — box has a state file but has never ticked.
- **— no_state_file** — no state file under **any** known convention. The line
  names the paths tried; that list *is* the finding. It does **not** mean
  "runs no mini" — this leg observes files, and whether a daemon is running is
  a larger claim one `cat` cannot make.
- **❌ unreachable** — ssh transport failed (box down or network).

Problems sort to the top, so a clean pane means a clean fleet.

⚠️ **`no_state_file` on a box you believe runs a mini is a finding, not
furniture.** Until 2026-08-12 this status was called `no_mini` and its docs
here said a MeshAnchor box legitimately reads that way — so the pane reported
the MA replica as mini-less for **19 days** (since MA moved off the home-dir
convention on 07-24) while its daemon ticked every 30s and its own warmstart
read FRESH. Both twins' conventions are now tried. If a **new** convention
appears, it must land in `_util.PEER_APPS` in the same commit or it is
invisible here. Verify a box directly with
`ssh <box> systemctl --user status '*mini-dudeai*'`.

**Drill in (on-demand):** when a box in the pane warrants it, merge every box's
escalations + recent fires into one box-tagged feed:

```bash
PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.rollup --deep
```

Escalations first (uncapped, "look here first"), then recent fleet fires (capped).
This is the global *deep* view — each box's local detections (tracer, watchdog),
not just its posture. Heavier than the default pane (pulls each box's history), so
it's a separate command, not part of the default `/warmstart` flow.

## Why manual *and* automatic

The automatic path is a `SessionStart` hook (settings.json) that injects this at
session start — because a cold session doesn't know to ask. `/warmstart` is the
on-demand refresh for mid-session, after a `/clear`, or when the brief may have
moved on since the session began. Same renderer, same honesty guarantee.
