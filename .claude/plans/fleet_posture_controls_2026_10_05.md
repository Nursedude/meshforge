# Fleet posture controls — dormant / detached by REASON (operator, 2026-10-05)

**Verdict:** the machinery exists (`fleet_power.py` down/resume, posture SSOT,
9 consumers). The work is mapping the operator's REASONS onto the two existing
silent states, closing three consumers that ignore posture, and a TUI front door.
No new posture state (closed enum, hfm #7 — every consumer would owe a change).

## Decisions (operator, 2026-10-05)

1. **TUI may declare AND power off** — amends the 09-11 "TUI stays a surface"
   doctrine for THIS action only. It runs the SAME `fleet_power.py` path (no second
   implementation), shows the plan first, and needs the box name typed to confirm.
2. **Reason → state** (no new state):

   | Reason | State | Box | Answers while declared | Return |
   |---|---|---|---|---|
   | `move` (node in transit) | dormant | off | POSTURE-DRIFT page | resume + identity check (hostkey) + RF re-baseline note |
   | `travel` (ECOMM kiai) | detached | up or down, off our net | REJOINED, not a fault | resume on tunnel return; renew at the 14 d cap |
   | `hardware` (bench work) | detached | bounces | never paged | resume when done |
   | `power` (outage) | dormant, whole fleet | off, ordered | POSTURE-DRIFT | `fleet_up.sh` |

3. **Power-out stays operator-pressed** (`fleet_down.sh`): MEASURED 10-05, no UPS
   or EcoFlow data cable on any of 10 boxes, no apcupsd/NUT installed. The auto
   trigger waits for cabling (P4).

## Gaps measured 10-05

- `fleet_power.py` writes only `dormant` (`:393`) — a travelling kiai that powers
  up in the field would page POSTURE-DRIFT. Needs `--state detached`.
- `--until` defaults `+4h` for every reason; no reason CATEGORY recorded.
- `scripts/fleet_sync.sh` has 0 posture references → a dormant box counts as a
  deploy failure. `mini_dudeai/rollup.py` renders a silent box ❌ unreachable.
- `fleet_os_upgrade.sh reboot` reboots with no declaration (agent inference).
- No TUI screen shows or sets posture (`fleet_watchers` reserved the name).
- Side finding: moc `throttled=0xe0000` — CORRECTED 10-05: bits 17/18/19 = THERMAL
  (freq-capped/throttled/soft-temp occurred); under-voltage bit 16 is CLEAR. Fanless
  by design (only kiai has a fan). moc3 `0x60000` = bits 17/18, also not under-voltage.

## Phases

- **P1 `fleet_power.py`**: `--reason {move,travel,hardware,power,other}` →
  state + default `until` (move 8h, hardware 8h, travel 7d, power 24h); reason
  category stored beside the free text; `--state` override; `--declare-only`
  (box already gone, e.g. kiai off-site). `resume` for `move` verifies identity
  (registry `expect_hostkey`) before clearing — reachable is not ours (09-11).
- **P2 consumers**: `fleet_sync.sh` skips silent boxes as SKIP (counted, not a
  failure), mirroring `fleet_pull.sh:117-134`; rollup renders `💤 dormant` /
  `🧳 detached` + reason + until instead of ❌; `fleet_os_upgrade.sh reboot`
  declares a short dormant first.
- **P3 TUI** (BUILT 10-05: `handlers/fleet_posture.py`; web tiles now say DETACHED /
  REJOINED instead of DORMANT for every silent box): Fleet → Fleet Posture. Read: every declaration (state, reason,
  since/until, who). Act (manager box only — the SSOT lives there; elsewhere the
  pane is read-only on the mirror): declare / power off / resume via
  `fleet_power.py`, plan shown first, typed box-name confirm.
- **P4 power-out auto** (blocked on cabling): APC USB → apcupsd/NUT on ONE box →
  on-battery + delay → `fleet_down.sh --yes`. ⚠️ physics: a central trigger
  cannot reach a box once the switch/AP loses power — the switch must outlast the
  Pis, or each box shuts itself down on its own UPS signal. EcoFlow (VolcanoAI,
  Starlink): research whether it exposes a local state API before designing.

## Acceptance (before code, per observe-before-alarm)

A box declared `travel` that powers up off-net and later tunnels back produces
ZERO pages, is skipped (not failed) by fleet_sync/pull, reads `🧳 detached` in
the watchers pane, and `resume` clears it. A `move` box that comes back answering
with the WRONG host key is NOT cleared.
