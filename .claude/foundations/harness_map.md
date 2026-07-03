# Harness Map — how a Claude session works in this domain

> **Audience: the NEXT session** (any model). Born from the 2026-07-03 harness
> self-audit. This is the one map of the second-brain layer — hooks, claim
> gate, calibration ledger, mini, probes, cron spine, ntfy paging — so a cold
> session answers "what watches what, where does truth live, how do I verify"
> in zero exploration reads. Companion docs (do NOT duplicate them):
> `fleet_architecture_map.md` (boxes/roles/protocol legs), `INDEX.md` (doc
> router), `persistent_issues.md` (bug history). Executable ground truth:
> `bash scripts/harness_audit.sh` (exit 0 = harness green; UNKNOWN ≠ pass).
> Keep ≤10KB; update the SPOF/residual section when arcs land.

## Cold-start quickstart (first 5 minutes of any session)

Run: `/warmstart` (mini brief + fleet pane; auto-injected at SessionStart too)
· `bash scripts/honest_status.sh --quick` (repo/fleet truth) ·
`bash scripts/harness_audit.sh` (harness truth).
Read: this file · `CLAUDE.md` (rules; auto-loaded) · head of
`~/.claude/plans/gateway-session-notes-<box>.md` (work in flight) ·
`MEMORY.md` (auto-loaded index; topic files on demand).

## The session-lifecycle chain (all wired, verified 2026-07-03)

| Moment | Hook | What it does |
|---|---|---|
| SessionStart | `~/.claude/hooks/memory_health_surface.sh` (user settings) | surfaces memory hot-index % when ≥75% of 24KB |
| SessionStart | `python3 -m mini_dudeai.warmstart --hook` (repo settings) | injects mini brief + fleet pane + calibration block; **also re-derives open ledger claims** (cheap path) |
| PostToolUse (Write/Edit) | `scripts/lint.py --severity warning` | lints the edited file live |
| **Stop** | **`scripts/claim_gate.py`** | blocks an unevidenced "all green / 100%" claim ONCE and injects re-derived truth; logs backed VERIFIED claims to the ledger. Fail-open. |
| daily 04:45 cron | `scripts/calibration_reverify.sh` | re-runs pytest+lint on HEAD, flips open ledger claims held/broke (the pass^k check) |

**Calibration ledger** (`~/calibration_ledger.jsonl`, module
`src/mini_dudeai/calibration_ledger.py`): append-only claim/verdict events,
records `model_id` (so a model swap's reliability shift is measurable).
Consumers: claim_gate (writes), warmstart brief (shows track record),
`probe_calibration_drift` (mini pages on drift), reverify cron (flips).

## Organs → watchers (who watches what; every watcher has a watcher)

| Organ | Watched by | That watcher's watcher |
|---|---|---|
| fleet boxes (moc*) | `fleet_offline_check.sh` cron on manager → `fleet_box_unreachable` | cron_verdict + freshness watcher |
| manager (VolcanoAI) **← was the SPOF** | `scripts/manager_heartbeat.sh` (manager cron, pushes beat to moc1) + `scripts/manager_deadman.sh` (moc1 cron, checks local mtime, **pages ntfy directly** — cannot ride mini: the dead thing IS the pager) | moc1's cron_verdict + #78 + mini |
| each box's services | `meshforge-watchdog` (~40 signal classes, `src/utils/watchdog_probe_core.py`) | mini rules (seed-coverage test pins ZERO unrouted classes) |
| mini daemons | rollup freshness (re-derived at read) + `mini_honest_fire` cron | cron freshness watcher |
| crons themselves | `cron_verdict.sh` verdicts + `probe_cron_verdict_stale` (#78) + hourly `cron_verdict_freshness.sh` | freshness watcher emits its own verdict |
| meshanchor-server (no MF, no mini) | `ma_health_uplink.sh` manager cron (units + dep floor via venv one-shot) | cron_verdict + #78 |
| ntfy channel | Phase-1 email backbone · Phase-2 `ntfy_loopback` cron · Phase-3 weekly tap-to-ack (`ntfy_ack`) — the ONLY device-leg proof | cron_verdict + mini rules |
| oracle | `probe_oracle_delivery_degraded` (delivery-rate; no silence leg by design — reactive service) | watchdog → mini |
| dudeclaw | `host_frozen` probe (claw = out-of-band witness) + rollup claw card (display-only staleness) | collector cron verdict-wired |
| my claims | claim gate + ledger + reverify cron (above) | `probe_calibration_drift` |
| memory index | SessionStart surfacer + `memory_health` cron + `probe_memory_index_oversize` | cron_verdict + mini |

## Where truth lives (ask the right oracle, never synthesize)

| Question | Source of truth |
|---|---|
| is the repo/fleet green? | `scripts/honest_status.sh` (exit 0 only) |
| is the harness green? | `scripts/harness_audit.sh` |
| what's happening on boxes now? | mini brief / `rollup` / `/fleet` endpoints |
| did my past claims hold? | calibration ledger (warmstart shows it) |
| what work is in flight? | session notes + `~/deferred_work.json` (durable; harness task lists are ephemeral) |
| durable domain facts | `MEMORY.md` index → topic files; cold history in `MEMORY_ARCHIVE.md` (grep it) |
| recurring bug? | `persistent_issues.md` FIRST (it's probably been fixed before) |

## Verification invariants (model-agnostic; the harness enforces what it can)

1. **Consumer-of-record, not the wiring** (calibrated_claims rule 7): a static
   trace ("X registers Y") is BELIEVED; VERIFIED = observing the live
   process/interpreter/unit. Canonical failures: `core.orchestrator` doesn't
   host the probe `daemon.py` registers; the venv, not system python3, is what
   services import (both 2026-07-03); both broke ledger claims were proxies.
2. **Pipe exit codes lie**: `cmd > f 2>&1; rc=$?` — never `cmd | tail`.
3. **Unobservable ≠ healthy** — UNKNOWN never passes; absence of evidence
   never emits recovery.
4. **Re-derive counts at the end**; never patch a running tally.
5. **File-redirected results over streamed echoes** under harness entropy.
6. **Every claim of consequence leaves a witness** (ledger, verdict, marker).

## Known SPOFs / residuals (update when arcs land)

- **ntfy topic name is the paging channel's only auth** (free tier): anyone
  holding it can spoof pages or forge acks (ack-topic accepts unauth POST).
  Verified 2026-07-03: topic in NEITHER public repo; box-local memory repo is
  PRIVATE. If burned: rotate topic file + phone app + reserve/token (Pro).
  NEVER post "ack" to the ack topic yourself — it forges device confirmation.
- **Resident `ActiveHealthProbe` (MA) runs only under daemon.py/TUI**, NOT
  `core.orchestrator` units — cron one-shots are the check host there.
- **No multi-agent fan-outs on VolcanoAI** until the kernel-lockup class is
  root-caused (2/2 froze the box). One sequential agent is fine.
- **fleet_sync restarts gateways** — during any soak, deploy with targeted
  `git pull --ff-only` only.
- Deadman covers manager-death; a simultaneous manager+moc1 death pages no
  one (accepted residual — two-box failure also takes out the mesh NOC role).

*Made with aloha for the next session.*
