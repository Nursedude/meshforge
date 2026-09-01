# Field eComm unit + duty-cycled fleet — planning seed (2026-08-27)

> Operator brief at session close, post-Lala: (1) an OpenWrt-anchored
> MeshForge eComm kit that BREAKS AWAY from the fleet and deploys to the
> field on a Starlink Mini; (2) FLEET MODE for power-out reality —
> battery/generator, boxes not-always-on; (3) Starlink on the fleet.
> Research + architecture arc. Seeded by the session that shipped the Lala
> forensics, the NTP island, the virtual fleet, and the radio-off pattern —
> those are this arc's building blocks, use them.

## For the next session (any model — written Opus-ready)

Truth sources first: `foundations/harness_map.md`, the warm brief,
`honest_status.sh`. Measured inputs this plan leans on:
`research/storm_prep_power_starlink_2026_08_27.md` (per-box watts, battery
tiers, 2-week Starlink checklist) and
`research/lala_outage_recovery_2026_08_27.md` (WAN-decoupling state).

Tier routing (post-x5): the DESIGN decisions below marked ⚖️ are
frontier-shaped — batch them for one frontier pass. Everything else
(role defs, probes, virtual-fleet drills, provisioning scripts) is
day-work: build behind the existing gates, drill in the virtual fleet
BEFORE hardware.

## Arc 1 — the field eComm kit (break-away standalone)

Composition (all measured/owned pieces):
  Starlink Mini (20–40 W) + OpenWrt travel router (the alaula class —
  already proven: factory-reset-and-restore drilled, NTP client, reverse
  tunnel) + one Pi with a LoRa HAT (pw2lab's **MeshAdv Mini** is the
  natural prototype radio — its rebuild can BE the field-kit bring-up)
  + LiFePO4 (kit ≈ 35–45 W → one 12 V 100 Ah ≈ 1.2–1.4 days; solar for
  indefinite).

Break-away contract (the architecture core — what "standalone" must mean):
- ⚖️ ZERO fleet dependencies when detached: local time (chrony orphan on
  the kit — the island pattern shrunk to one node), local names (hosts
  file), no fleet_hosts organs, map local-only, tx_guard defaults sane
  with no operator env. The alaula LAB-ZERO drill + the `standalone`
  offering doctrine are the priors.
- ⚖️ REJOIN semantics: when Starlink is up, artifacts flow OUT only
  (snapshots, journals, map push — the cloud-map rsync pattern is already
  CGNAT-proof). Never inbound; never fleet organs reaching in.
- Provisioning: a `field` role in `docs/fleet_roles.yaml` +
  `provision_role.py`, so the kit is a declared role, not a hand-built
  snowflake — and `role_expected_active` / the watchdog read it honestly
  (the radio-off override pattern from 2026-08-27 shows the mechanics).
- TEST IT VIRTUALLY FIRST: a vfleet node provisioned with the `field`
  role, chaos drill = cut its transport link (already exists) and assert
  the kit's own canaries stay honest while detached.

## Arc 2 — duty-cycled fleet (power-out mode)

The new assumption the current architecture lacks: **a box that is OFF on
purpose.** Today's creed handles unobservable ≠ dark ≠ resolved; it needs a
fourth state:
- ⚖️ **DORMANT — declared power posture.** A fleet-level declaration
  (SSOT beside fleet_roles/deployment.json, mirrored like the registry)
  that says which boxes/tiers are deliberately down. Every instrument
  that judges freshness or reachability (offline monitor, cron_verdict
  staleness, watchdog probes, mini rules) must read it: a dormant box
  pages NOTHING, and — the honest half — a box dormant-declared but
  still ANSWERING is itself a finding (posture drift). This generalizes
  the radio-off/service_overrides pattern from box-level to fleet-level,
  and the declaration re-read (utils/watchdog_retarget) means posture
  changes take effect in one tick.
- Load tiers exist on paper (storm doc): Tier-1 rides battery
  (m1 + gateway + one radio + dish), Tier-2 sheds. Architecture work:
  tiers as systemd targets or role attributes, one command to enter/exit
  posture, and the posture change itself verdict-wired.
- **Store-and-forward becomes the backbone**: LXMF propagation nodes are
  DESIGNED for intermittent peers — messages must survive a sleeping
  gateway (the propagation-soak drill already proves the mechanism;
  extend it to a deliberately-sleeping peer).
- **Battery/solar voltage metrics — findings + design (2026-08-28, moc4).**
  moc4's RAK6421 pHAT carries an ADS1115 ADC @0x48, live on i2c-1 since the
  08-28 sensor wiring (BME680 env telemetry VERIFIED on-mesh same day). Two
  measured blockers before it can carry power metrics:
  1. **Inputs are floating** — all 4 channels read ~0.12 V (leakage), nothing
     connected. Hands-on wiring needed: battery+ → A0 via 100k:10k divider
     (11:1 → 14.6 V max reads 1.33 V; use PGA 2.048 FSR for resolution),
     solar panel V → A1 via 100k:4.7k (~22:1 for panels to ~40 Voc),
     divider grounds common with Pi GND.
  2. **Stock meshtasticd has NO ADS1115 telemetry consumer** — ScanI2C
     detects it and the protobuf enum exists, but modules/Telemetry/Sensor/
     implements only INA219/INA260/MAX17048 for power (verified in the
     2.7.26 source tree). Wired channels would still publish nothing.
  Paths, in preference order:
  * **INA219/INA226 module (e.g. RAK16000 class) instead** — stock firmware
    consumes it natively via PowerTelemetry: voltage+current on the mesh
    with ZERO custom software. Best fit if buying hardware anyway; current
    sensing (a shunt) is what the duty-cycle decisions actually need.
  * **Host-side reader for the existing ADS1115** — small MeshForge
    collector reads i2c (the 08-28 channel-read recipe), publishes a
    state file the watchdog/DORMANT machinery consumes (same file-fallback
    pattern as delivery_snapshot). No firmware fork growth. Build only
    after wires exist — an instrument on floating inputs publishes lies.
  * Firmware fork addition of an ADS1x15Sensor: rejected for now — grows
    the fork maintenance surface for something the host can read directly.
- Research items (hardware truths to establish, not design):
  * Pi 5 RTC **wake alarm** — with the battery, Pi5s may self-wake on
    schedule (duty-cycle without external hardware). Pi4s cannot; they
    need a smart switch or stay Tier-1. Verify on real hardware.
  * Starlink Mini sleep-schedule control (app/API) — dish duty-cycling
    dominates the energy budget.
  * Real Pi4 watt measurements (USB meter — PMIC only exists on Pi5).
  * Generator-transition behavior: brownout tolerance, the get_throttled
    audit rerun under generator power.

## Arc 3 — Starlink

Fleet side: the 2-week checklist in the storm doc stands (inventory
inbound deps → outbound anchor → cutover-as-NTP-drill → post-cutover
gate). Field side: Mini is the kit's WAN; note CGNAT + IPv6-preference
(re-run the AF_UNSPEC timing sweep on Starlink DNS).

## Notes to future-me (model notes, honestly)

- You may be Opus, not frontier. That changes NOTHING about the gates:
  lint, guards, claim-gate, honest_status run identical — lean on them
  HARDER, and the virtual fleet gives you live-ish verification without
  touching production. Say the tier judgment out loud at intake
  (model_advisor rule) and queue the ⚖️ items for a frontier pass rather
  than faking a design review.
- The instruments will disagree with your first read sometimes — this
  session alone: "watchdog failed first" (overturned by journals),
  "canary broken" (it was aimed at the wrong peer by ME), a green suite
  hiding an ambient-state dependency. The pattern that worked every
  time: get the artifact, not the summary of it.
- The operator is a senior infra peer with HAM discipline: measured
  numbers beat adjectives, reversibility beats speed, and "message
  arrives / truth told in-app" is the END every design serves.

---

## ⚖️ Frontier design pass — DECIDED 2026-09-01 (Fable 5.1)

> Closes the review_provenance queue entry "field-eComm + duty-cycle DESIGN
> pass". Method: each ⚖️ decision was attacked with concrete failure
> timelines drawn from THIS fleet's measured history (Lala forensics, the
> moc4 8-day clock, kiai's tunnel-only DOWN page, the two-preset split,
> lehua's bring-up), then written as a position + the invariants it needs +
> the drill that proves it in the virtual fleet BEFORE hardware. Positions
> are recommendations for the operator to ratify; the three OPEN QUESTIONS
> at the end are the ones only the operator can settle.

### Decision 1 — DETACHED = zero fleet dependencies

**Attacks that shaped it.**
1. *Cold boot, Starlink down, no sky.* The kit Pi (Zero 2W-class, lehua's
   shape) has NO I2C RTC (i2cdetect-verified) and the OpenWrt router boots
   to its build date. fake-hwclock restores the last saved time — stale by
   the whole off-interval. Every wall-clock instrument on the kit lies
   together (the moc4 class), and there is no peer to cross-check.
2. *Fleet organs retrying into the void.* Anything on the kit that names
   `*.mf.internal`, the manager, the NTP island, or ntfy will retry
   forever when detached — and on a 512 MB board a retry storm is a
   resource fault, not noise (moc3's 806-thread leak was a retry loop).
3. *The two-preset split.* The kit leaves the home segments; at the field
   it is either alone or among strangers' nodes. A kit radio pinned to the
   fleet's SHORT_TURBO segment reaches nothing.
4. *SD power-loss.* The kit is the most power-cycled box in the domain.
   The zero-byte class hits meshtasticd's own prefs/nodedb, not LXMF —
   there is no ratchet guard to save it.

**Position.**
- **The kit is a STANDALONE-offering box while detached, not a fleet
  member.** It is the `field` role: meshtasticd + TUI (+ optional bot);
  no rnsd, no map, no watchdog, no mini, no fleet crons. This is lehua's
  `field-node` role with the fleet enrollment (registry, hosts block, RF
  watch lists, mini) made CONDITIONAL on posture (Decision 3) rather than
  stripped: enrolled when home, declared-detached when away.
- **Time truth on the kit is GPS, then the mesh, then WAN — never the
  island.** The MeshAdv Mini's GPS (ATGM336H on ttyS0, PPS on GPIO17) is a
  stratum-0 the fleet does not otherwise own: gpsd + chrony refclock on the
  Pi; the router takes NTP from the Pi (`uci system.ntp.server`), Starlink
  pools as fallback when up. meshtasticd's own RTC-quality (from GPS or
  from a peer node's position packet) is the second source. The kit's
  status bar MUST wear its clock confidence — `clock: GPS-locked | WAN |
  UNCONFIRMED since <boot>` — and every artifact it writes carries that
  flag, because "time unconfirmed" is a state the fleet later has to
  reason about (Decision 2).
- **Names**: a static hosts file for the kit's own two hosts. No fleet
  hosts block (it is seeded from live DNS the kit cannot see — lehua
  already runs this way BY DESIGN). The `field` role's converge REFUSES
  to install `fleet_hosts_selfheal`, `fleet_registry_sync`, or any
  manager-targeting cron; the validator says why.
- **Identity**: the radio's own keys + the three channel PSKs (public
  LongFast, `meshforge`, HawaiiNet) — captured in fleet-vault BEFORE the
  kit ships (pw2lab had no capture; runbook-zero's lesson). No RNS
  identity on the kit at all. If message continuity across detachment is
  ever wanted, that is a SECOND kit tier (`gateway-only` + lxmd on a 1 GB
  board), not a feature of this one.
- **Radio**: community defaults (LONG_FAST/ch20 + HawaiiNet + meshforge
  PSK). Preset is a property of the LINK; the kit joins whatever segment
  is in range and is never expected to hear the home SHORT_TURBO segment.
- **Storage**: high-endurance card + the boot_survival_audit self-report;
  rebuild-from-vault is the kit's restore path and its FIRST drill (the
  lehua rebuild already is that drill).
- **Refuses to need**: manager, DNS beyond itself, NTP island, fleet ssh
  key, ntfy (paging is the operator standing next to it), `/fleet`.

**Drills (virtual fleet — needs a `field` node type, day-work).**
- **V1 detached canary**: a vfleet node provisioned `field`, no transport
  link, resolver returning NXDOMAIN for `*.mf.internal`, no NTP reachable,
  meshtasticd `--sim`. Assert over 10 min: zero DNS queries for fleet
  names, zero ssh attempts outbound (or bounded backoff with a witness
  line), TUI status serves locally, status bar reads `clock: UNCONFIRMED`.
- **V2 cold boot on a stale clock**: libfaketime steps the node back 8
  days at start; assert artifacts stay monotonic by `boot_id + uptime`,
  the clock flag is `UNCONFIRMED`; then feed a simulated NMEA source →
  chrony steps, flag flips to `GPS-locked`, and the STEP is logged as an
  event a later reader can see (a discontinuity that leaves no record is
  the moc4 forgery again).

### Decision 2 — REJOIN: artifacts flow OUT only

**Attacks that shaped it.**
1. *The tunnel is inbound.* alaula's reverse tunnel is exactly the
   "reaching in" the seed forbids — and it is how the operator reaches
   kiai today. "Never inbound" cannot mean "no tunnel".
2. *Clock discontinuity.* A kit that ran days on an unconfirmed clock
   pushes artifacts stamped in the past (or future); a manager that sorts
   by wall-clock files them under the wrong day and its freshness logic
   calls a live kit stale — or a dead one fresh.
3. *The truth-spool is a PULL.* lehua's own observability is "the NOC
   reaches it over ssh". That is an inbound organ path, and during
   detachment it pages (or with `via`, pages UNOBSERVABLE) forever.
4. *Partial writes over rsync.* A jsonl mid-append arrives truncated; a
   reader that trusts it repeats Lala's NUL-line class at the manager.

**Position.**
- **Inbound is a DOOR for the operator, never a PATH for an organ.** The
  tunnel stays (human access). No manager cron may name the kit as a
  target while it is declared detached; the ones that already do
  (`fleet_offline_check`, `fleet_registry_sync`, `fleet_pull`,
  `fleet_hosts_selfheal`, `fleet_front_probe`) read the posture file and
  write a `SKIPPED-DETACHED [kit]` witness line — never OK (that destroys
  the signal), never CONCERN (that is the Lala page storm).
- **The kit PUSHES its evidence; nothing pulls.** On WAN-up the kit
  rsyncs its spool (boot_survival_audit, meshtasticd journal excerpt,
  bot log, clock-confidence timeline) to a manager drop-box, kit-
  initiated, `--partial` + atomic rename at the receiver, path-allowlisted
  (public evidence only — identity material never rides the push). The
  existing cloud-push pattern is the template; it is already CGNAT-proof.
- **The kit carries EVIDENCE across the boundary, not MESSAGES.** No
  store-and-forward on this tier (no lxmd); a message sent on the kit's
  mesh while detached stays on that mesh. Say it in the TUI.
- **Ordering key is `boot_id + monotonic uptime`, wall-clock is display
  only** (hfm #6). Every pushed row carries `clock_confidence`; the
  manager's freshness verdict for the kit must be `indeterminate` — not
  stale, not fresh — for rows written under `UNCONFIRMED`.
- **"Caught up" is observable, and it is a fleet_truth cell**:
  `catch_up = {last_push_age_s, boot_id_seen == kit's current boot_id,
  clock_confidence}`; states `detached → catching_up → caught_up`. The
  cell renders beside the box; it never taints the fleet verdict while
  the posture is `detached` (that is Decision 3's rule), and it DOES taint
  once the posture expires with no push received.

**Drills.**
- **V3 rejoin**: detach the `field` node (iptables to the manager), let it
  write spool rows under libfaketime skew; reconnect; assert the manager's
  cell walks `detached → catching_up → caught_up`, rows are ordered by
  boot_id+uptime, skewed rows read `indeterminate`, and the manager made
  ZERO ssh attempts to the kit during detachment (count in the auth log,
  witness lines present instead).
- **V3b torn push**: SIGKILL the kit mid-rsync; assert the receiver holds
  the previous complete file (atomic rename) and the reader tolerates a
  truncated staging file with a witness, never a crash.

### Decision 3 — DORMANT as a DECLARED power posture

**Attacks that shaped it.**
1. *Lala, replayed with today's instruments.* Power off eight boxes:
   offline_check pages DOWN ×8 every hour; `fleet_registry_sync`,
   `fleet_pull`, `fleet_hosts_selfheal`, `fleet_front_probe` each go
   CONCERN → `cron_verdict_stale` fires on the manager all storm;
   `tracer_peer_unreachable` fires on every surviving box; federation
   backoff escalates; the claws report `claw_rf_silent` for every dark
   radio; `honest_status` SHA leg reads 0/8 FAIL. Every one of those was
   a TRUE statement about an EXPECTED condition — the exact noise that
   made "the watchdog died" indistinguishable from "the fleet is fine and
   dark on purpose".
2. *The furniture failure.* A dormant declaration with no end date is how
   a box that died during the storm stays "dormant" in November — the
   `known_benign` class in posture form.
3. *Forgeable expiry.* `expires_at` is wall-clock on RTC-less boxes whose
   clocks ran 8 days stale last time. A stale clock keeps a dead box
   "dormant" past its window; a fast one un-dormants a sleeping box and
   pages it.
4. *The manager is dormant too.* If the declaration lives only on the
   manager and the manager is Tier-2, every other box loses the
   declaration exactly when it needs it.
5. *Three shapes, not one.* A box OFF (dormant), a box UP with services
   shed (Tier-2 shed), and a box permanently radio-off (VolcanoAI's
   `service_overrides`) are different claims; collapsing them makes
   `service_inactive` page the shed box or hides a real service death.
6. *A posture that removes the mesh.* "Everything dormant but the
   manager" leaves zero bridges and zero propagation nodes — a fleet
   that cannot deliver a message, declared healthy.

**Position.**
- **One declaration file, operator values, manager SSOT, mirrored to
  every box by the registry-sync organ BEFORE the storm** (the
  declaration is a storm-prep artifact, like the vault capture):
  `~/.config/meshforge/fleet_posture.json` →
  `{posture, declared_at, expires_at (MANDATORY, cap 14 d, renewable),
  boxes: {name: {state: active|shed|dormant|detached, reason, until}}}`.
  Each box's own copy governs its judgments (watchdogs run per box and
  may not reach the manager); the watchdog's declaration-mtime gate makes
  a change take effect in one tick.
- **Four per-box states, each a different claim**: `active` (today),
  `shed` (box up, expected-active units reduced to a declared set —
  implemented by overlaying the posture on the role plan, the SAME
  `_plan_role_actions` path `role_expected_active` already reads, so the
  paging probe and the drift probe agree by construction), `dormant`
  (box off), `detached` (Decision 1/2: reachable only by its own push).
  `service_overrides` stays box-level and open-ended; posture is
  fleet-level and time-bounded; they compose, they do not merge.
- **Dormant pages NOTHING and is never OK**: every consumer writes a
  witness (`DORMANT [moc4] until <ts>`), visible in `/fleet` as its own
  cell state (not `dark`, not `healthy`), non-tainting.
- **Posture DRIFT is a finding, both ways.** Declared dormant but
  answering (ssh OK, or a claw hears its radio) → `posture_drift`
  degraded: the declaration is stale or the box is burning a battery it
  was meant to save. Declared active and dark → DOWN, exactly as today.
- **Expiry is the honest default, and clock-gated.** Past `expires_at` a
  still-dark box pages DOWN. A consumer whose own clock is unconfirmed
  (chrony unsynced > N h — roadmap #5's detector, now required) reports
  `posture expiry unverifiable: clock unconfirmed` as its own line and
  HOLDS the last posture (hfm #2), never silently un-dormants.
- **A posture validator, run at declare time**, refuses (without
  `--force`) any posture that leaves zero active transport-rnsd, zero
  bridges, or zero LXMF propagation nodes: Tier-1 must contain one of
  each or the fleet cannot deliver — the storm doc's Tier-1 list becomes
  executable, and store-and-forward's "backbone" claim gets a guard.
- **Entry/exit are operator commands** (`posture enter storm`, `posture
  exit`) that: validate → write → mirror → (for `shed`) apply the reduced
  plan on each box → only THEN power down. Automatic entry from battery
  metrics is autonomy rung 3 (control) — NOT in this arc; it waits for a
  real current sensor (the INA219 path) and its own drill. An instrument
  on floating ADC inputs must never drive power posture.
- **Closed-enum consumers — the list that must FAIL A TEST until each
  reads posture** (hfm #7; this is the bulk of the build):
  `scripts/fleet_offline_check.sh`, `probe_fleet_box_unreachable`,
  `fleet_truth.build_box_truth` (+ `/fleet` renderer), the five
  manager-side fleet crons named in Decision 2, `honest_status.sh` fleet
  SHA leg (`skipped-dormant`, listed under `--strict`), peer-facing probes
  (`tracer_peer_unreachable`, `federation_peer_*`, delivery fan-out peer
  legs → `inert (dormant peer)`), claw RF watch lists (`claw_rf_silent`
  for a dormant box's node → inert), node-tracker UNHEARD for a dormant
  box's own node, `service_inactive`/`role_drift` on a `shed` box, and
  mini's seed rules downstream of each. A seed-coverage test enumerates
  consumers against the posture enum so a new state cannot be added
  without every consumer learning it.

**Drills.**
- **V4 dormant declared**: declare vfleet-echo `dormant` (1 h), SIGKILL
  it. Assert: offline-check witness `DORMANT [echo]`, zero pushes to the
  throwaway ntfy topic, `/fleet` cell `dormant` non-tainting, gw's
  `tracer_peer_unreachable` → `inert (dormant peer)`, the canary aimed at
  echo → `SKIPPED-DORMANT` (never FAIL, never OK).
- **V5 posture drift**: declare echo dormant, leave it running →
  `posture_drift` degraded within 2 ticks.
- **V6 expiry, both clocks**: libfaketime past `expires_at` with echo
  still dark → DOWN page resumes; repeat with the consumer's clock marked
  unconfirmed → `expiry unverifiable` line, posture held, no page.
- **V7 validator**: a posture leaving no transport node is refused;
  `--force` writes it with the refusal reason recorded in the file.
- **V8 shed**: declare gw `shed` to {rnsd only}; assert gateway stops,
  `service_inactive` stays silent on gw, and `role_drift` reads the shed
  set as declared, not drift.
- **Real-fleet drill (after V4–V8 are green)**: moc4 (hardware RTC —
  the least forgeable clock) declared dormant 2 h and powered off → zero
  pages; power on → converge; measure power-on → `caught_up` and record
  it as the fleet's first duty-cycle number.

### Priority against the Starlink cutover clock (~2 weeks from 08-27)

1. **DORMANT declaration file + its four manager-side consumers**
   (offline_check, fleet_truth, the fleet crons, honest_status SHA leg)
   + V4/V6. This alone removes the Lala page storm and is needed the
   next time the grid drops, cutover or not.
2. **`field` role converge refusals + vfleet `field` node + V1/V2**, so
   the lehua rebuild becomes the kit's bring-up under a declared role.
3. **Posture validator + `shed` overlay + V7/V8**, then the peer-facing
   probes and claw watch lists.
4. **Kit push + catch_up cell + V3**, last — it has no value until a kit
   exists and detaches.
The cutover itself remains the NTP-island takeover drill (storm doc
week 2); with GPS on lehua it can also seed the fleet's own stratum-1,
which shrinks the island's WAN coupling further — worth doing in the
same window.

### OPEN QUESTIONS — operator only

1. **Kit tier**: is the first kit the Zero-2W standalone shape above
   (evidence, no messages across the boundary), or the 1 GB gateway-only
   shape with lxmd (messages survive detachment, ~3× the power)? The
   positions above assume the first; the second changes Decision 2.
2. **Does the ssh truth-spool PULL stay** for home-enrolled field nodes
   (lehua today), with `detached` merely skipping it — or does the kit's
   push replace it fleet-wide so there is one evidence path?
3. **Automatic posture entry** from a real battery-current sensor: wanted
   in a later arc, or never (operator-declared only, by doctrine)?
