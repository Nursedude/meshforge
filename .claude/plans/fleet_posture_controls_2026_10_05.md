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

## P4 site facts (operator 10-05) + measurement (10-05 15:2x)

**Operator:** the APC UPS feeds VolcanoAI, alaula, moc5, moc, the AREDN hAP, three
5-port switches and the Apple router. EcoFlow is a **1600** (earlier text said 3600).
~~NOT on the APC (by omission): moc1, moc2, moc3, moc4, kiai, lehua, meshanchor-server.~~
SUPERSEDED same day — every box but meshanchor-server is on an APC; see SITE MAP below.

**Measured:** no APC on USB (vendor `051d`) on VolcanoAI, moc, moc5 (lsusb) or
alaula (sysfs); no apcupsd / NUT binary or unit on any of the four. So the trigger
does not exist yet — cabling is the first step, as stated.
**Operator 10-05:** no USB yet to the UPS OR the EcoFlow; the EcoFlow 1600 is on
Wi-Fi via m1. Its state can only come over the network: get its IP/MAC from the
EcoFlow app or m1's DHCP list, THEN research whether it answers locally (EcoFlow is
cloud-MQTT by default — a local API is UNKNOWN). Not scanned: it is not a fleet box.
**Operator idea 10-05 (yurt):** an EcoFlow 3600/3000 powers ONLY Starlink + a 5-port
switch; put a Pi on it — USB to the EcoFlow, Ethernet to that switch everyone sees.
CONFIRMED (operator 10-05): TWO units, not connected — EcoFlow 1600 in the TENT (Wi-Fi via m1), EcoFlow 3600 in the YURT (Starlink + 5-port switch). Operator has the EcoFlow phone app; API research queued (.claude/research/ecoflow_api_2026_10_05.md).
ASSERTIONS to settle when cabled: (1) EcoFlow USB ports are believed charge-only (no
HID-UPS like APC) — state comes via cloud API or reverse-engineered BLE, local API
UNKNOWN; (2) grid loss flips APC and EcoFlow to battery at the same instant, so APC USB →
VolcanoAI may be the ONE site-wide grid-loss signal and the EcoFlow only adds runtime
remaining; (3) the house↔yurt link must be battery-backed end to end or the yurt boxes
are unreachable from the trigger at the very moment it fires.

**⚠️ CORRECTION — power chain (operator 10-05): wall/grid → EcoFlow → APC UPS → devices.**
Assertion (2) above is WRONG for this wiring: the EcoFlow rides through grid loss, so the
APC stays ON-LINE until the EcoFlow is EMPTY. APC on-battery therefore means "EcoFlow
exhausted, APC runtime only" — a LATE but unambiguous, local, HID-standard signal.
Design becomes TWO STAGES (assertion until cabled):
1. **Grid lost** (early): EcoFlow telemetry (BLE/cloud — research file) OR a probe that
   sits on the WALL side, not behind the EcoFlow (a mains-powered pingable device, or a
   wall USB charger into a GPIO via opto) → declare + announce, shed load.
2. **APC on battery** (late): ordered `fleet_power.py down` NOW, VolcanoAI last.
**SITE MAP (operator 10-05, completed late) — THREE APCs, two sites:**
- TENT: wall → **RIVER 2 Pro** → **APC** → VolcanoAI, alaula, moc, moc5, kiai, hAP,
  3× 5-port switches, Apple router. ⚠️ SIZE OPEN: recorded as 650VA/360W, but the
  operator also called kiai's (= this same APC) 900VA/480W — read the label.
- YURT-A: wall → **DELTA 3** → **APC 900VA/480W** → Starlink + 5-port switch.
- YURT-B: wall → **APC 650VA/360W** (NOT behind the DELTA 3, no EcoFlow) → moc1, lehua,
  moc2, moc3, moc4, the hAPs, the Pi Zero 2 Ws.
- **Honda 2200 generator** keeps the EcoFlow batteries up in a long outage, so EcoFlow-fed
  runtime is long; the shutdown trigger is for when THAT chain fails, not every grid blip.
Implications (assertion): TENT and YURT-A get the two-stage shape — EcoFlow telemetry (or
a wall-side probe) = grid lost; that APC on-battery = EcoFlow empty, shut down now.
YURT-B is the opposite: no EcoFlow, so its APC goes on-battery AT grid loss — the
earliest, local, HID-standard grid-lost signal on site — and its boxes have only APC
runtime (minutes, not generator-hours) unless the Honda feeds that circuit: ASK.
No APC has a USB data cable yet. Tent APC → VolcanoAI (the trigger host). Both yurt APCs
need a Pi ON them to read them (operator's idea: a Pi on the yurt switch; YURT-B already
carries moc1/lehua/moc2/moc3/moc4, so one of them can read the YURT-B APC). When YURT-A empties,
Starlink (WAN) and the yurt switch die together — yurt boxes are unreachable from the
tent and the cloud path is blind from that moment.
**Direction (operator 10-05):** today an outage keeps only PART of the lab up — the P4
trigger's job is to shed cleanly to that part, not to keep everything running. Later:
more battery + solar to run the lab 24/7 (the shed list shrinks then; re-read this map).
**Backup WAN:** a **Starlink Mini** is on hand to deploy wherever it keeps work in the
domain going (e.g. if YURT-A empties and the main dish goes dark). Not yet in the fleet
registry or any posture — where it plugs in is a decision for when it is used.
**Research spot-checked 10-05 (VERIFIED at pinned source, by the session, not the
researcher):** ha-ef-ble @511e0470 `_delta3_base.py:88` `plugged_in_ac`; `river2.py:58`
`ac_input_power ... default_when_missing(0)` (missing reading == "grid lost" — guard it);
tolwi @38986e6e `registry.py:108` DELTA 3 public API COMMENTED OUT, `:111` RIVER 2 Pro public.

**What that implies (ASSERTION, settle when cabled):**
- Trigger host = **VolcanoAI**: on the APC, holds the posture SSOT, runs fleet_power.py.
- The network path (switches, router, hAP, alaula) is on the SAME UPS, so it outlasts
  the boxes it must reach as long as the trigger fires on-battery + delay, well
  before runtime ends.
- EVERY fleet box is on an APC (operator 10-05; meshanchor-server not yet stated).
  YURT-B boxes (moc1, lehua, moc2, moc3, moc4) are on a UPS with NO EcoFlow behind it:
  they are the FIRST to need a real `down`, on YURT-B on-battery + delay — 5 Pis +
  hAPs + Pi Zeros on one 650VA, so runtime is the shortest on site (measure the load).
  `down --declare-only --kind power` is now only for a box that goes dark uncleanly.
  Real `down` order: YURT-B boxes first, tent boxes later, VolcanoAI LAST (it runs the tool).
- kiai and alaula share the tent APC, so kiai's only route (via alaula) lasts as long as it does.

## Acceptance (before code, per observe-before-alarm)

A box declared `travel` that powers up off-net and later tunnels back produces
ZERO pages, is skipped (not failed) by fleet_sync/pull, reads `🧳 detached` in
the watchers pane, and `resume` clears it. A `move` box that comes back answering
with the WRONG host key is NOT cleared.
