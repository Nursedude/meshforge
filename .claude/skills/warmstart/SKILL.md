---
name: warmstart
description: >
  Pull mini-dudeai's warm-start brief on demand — what mini saw while you were
  away, with an honest freshness banner re-derived from the last tick. Use when
  starting work mid-session, after a /clear, or any time you want mini's current
  posture (active conditions, escalations, recent fires) without the automatic
  SessionStart injection.

  Triggers: warmstart, warm start, mini brief, what did mini see, situation brief
---

# /warmstart — mini-dudeai warm-start brief (manual refresh)

mini-dudeai is the local 24/7 presence. `brief.py` writes its warm-start brief;
`warmstart.py` reads it and stamps an **honest freshness banner** derived from
`mini_dudeai_state.json`'s `last_tick_ts` *now* — so a brief frozen by a dead
daemon is flagged 🔴 STALE instead of lying about health.

## What to run

```bash
PYTHONPATH=/opt/meshforge/src python3 -m mini_dudeai.warmstart
```

Plain markdown to stdout. Read it, then follow its pointers in order:
`mini_dudeai_history.jsonl` → `state` → memory.

## Reading the banner (the part that matters)

- **🟢 FRESH** — last tick recent; the brief is current, trust it.
- **🔴 STALE** — last tick older than the stale window; the brief is FROZEN and
  may misreport health. **Verify live before acting** (the brief's own "alive"
  line is from generation time, not now). Check the watcher:
  `systemctl --user status meshforge-mini-dudeai`.
- **⚠️ FRESHNESS UNKNOWN** — no `last_tick_ts`; treat as historical.
- **no brief yet** — mini ticked but no brief generated; make one with
  `python3 -m mini_dudeai --preset meshforge_fleet --brief`.
- **silent / "no brief and no state"** — mini has never run on this box.

## Why manual *and* automatic

The automatic path is a `SessionStart` hook (settings.json) that injects this at
session start — because a cold session doesn't know to ask. `/warmstart` is the
on-demand refresh for mid-session, after a `/clear`, or when the brief may have
moved on since the session began. Same renderer, same honesty guarantee.
