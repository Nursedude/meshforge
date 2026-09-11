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

### Shipped — batch 1 (2026-09-01, same session as the design pass)

`src/utils/fleet_posture.py` (SSOT: four states, mandatory capped `until`,
expiry-as-default, clock-gated HOLD, loud unreadable/invalid, mesh-less
refusal) + `scripts/fleet_posture.py` (show/check/declare/clear, `--force`
records the refusal in the file) + consumer #1 `scripts/fleet_offline_check.sh`
(DORMANT witness / POSTURE-DRIFT gentle page / POSTURE-LIFTED / no-file
invariance pinned by the 29 pre-existing tests). 62 tests; the monitor drilled
by making it ignore posture (5 of 8 new tests fail). No posture is declared
live — declaring is the operator's first act.

**Batches 2–3 (same day):** consumer #2 the `/fleet` truth (collector stamps,
builder renders a dormant box as a fourth state, disclosed under
`declared_posture`, out of the fan-out denominator, non-tainting; a declared
box that answers is `posture_drift`; page badges + count chip;
`fleet_truth.py` byte-locked → ported to MeshAnchor) and consumers #3–#5 the
manager-side shell organs via `scripts/lib/fleet_posture.sh` (ONE reader over
the Python SSOT): `honest_status.sh` (`<box>:dormant`, out of both fleet
denominators), `fleet_pull.sh` (DORMANT row, not a failure),
`fleet_registry_sync.sh` (`dormant=` in the summary, never UNOBSERVABLE).
Closed-consumer test pins all nine consumers. Remaining, in priority order:
peer-facing watchdog probes (`tracer_peer_unreachable`, federation, delivery
fan-out) + `probe_fleet_box_unreachable` reading the new verdicts, claw RF
watch lists, `shed` overlay on the role plan,
validator with roles, `posture_drift` as a watchdog class, V4–V8 in the
virtual fleet.

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

---

## ⚖️ Swappable-WAN invariant (2026-09-02, Fable 5.1 + operator, at session close)

**Context**: Starlink (router mode) lands at the QTH this week; the current ISP
network is phased out once Starlink is proven; the field kit is Starlink Mini
behind the OpenWrt box. The public-IP question cannot be answered until
Starlink is up — so the design must not need the answer.

**Invariant**: the fleet never knows what its WAN is. Everything upstream of
the fleet's own edge router is replaceable; names, DHCP, `mf.internal` DNS,
NTP island, firewall and every fleet address are authored BEHIND the edge.
m1 is that edge at the QTH; the OpenWrt box is the same edge in the field.
Same class, two scales — QTH and kit differ only in WAN and box count. That is
the recursion: each cutover is the template the next one re-runs.

**Assumption adopted**: there is NO inbound, anywhere (CGNAT is Starlink's
general case and the field's always). Every cross-site path dials OUT to a
rendezvous the operator owns (the cloud host). Consequence: the alaula/kiai
reverse tunnels — the fleet's only inbound dependency — must be re-homed from
the QTH to the cloud host, and this is the one thing that breaks on install
day if not done first.

**What learns**: every cutover yields the same four artifacts — baseline
captured BEFORE, measured delta AFTER, runbook entry, eval case. Requires a
**WAN leg in fleet truth** (per site, observed at the edge: which uplink,
CGNAT y/n, latency, last-changed) so a swap is visible rather than inferred.

**Pre-truck batch (Opus, in order; displaces other queued items this week)**:
1. Re-home alaula/kiai tunnels to the cloud rendezvous while BOTH WANs are
   alive — prove the swap-safe shape, don't discover it in the outage.
2. WAN leg in fleet truth (+ honest_status disclosure), so install day has a
   before.
3. Virtual-fleet cutover drill: edge loses WAN → new WAN, new address →
   tunnels reconnect via rendezvous → a page arrives. Then run it for real.

**Install-day**: router mode, m1 as a client on a Starlink LAN port, fleet
unchanged behind it (double NAT is harmless for a push-only fleet). Bypass
mode stays a later option (single NAT + delegated IPv6, but kills Starlink
WiFi → household onto m1 VLANs). IPv6: keep firewalled at the edge; a v6
inbound path is a deliberate arc, never an install side effect. Expect
`fleet_offline_check` to page kiai/alaula as PATH-down during the re-home —
the instrument being right. Watch the clock leg (NTP island stays inside).

---

## 🌀 Lala reframe + RF/TCP data-path findings (2026-09-07, Opus 5 + operator)

**The organizing fact, from the operator's own outage record:** during Lala the
power went out — no internet, no intranet — and **LoRa kept working. Several
hundred nodes on RF + battery + solar stayed up.** What died was **the bot and
much of the MeshForge fleet.** The mesh did not need us; we needed mains power
and a switch.

**So the defect is an inversion: THE OBSERVER IS MORE FRAGILE THAN THE
OBSERVED.** Every "route the control plane over better transport" idea is
downstream of this. A perfect RNS-over-RF design on a Pi that is OFF is still
off. ⚠️ This invalidated ~an hour of this session's own work (a cloud ssh
rendezvous, VPS consolidation) — all of it optimizing the plane that fails
first. Recorded so the next session does not repeat it.

### The three planes, and how they failed differently

| Plane | Carries | Medium today | Lala |
|---|---|---|---|
| **Data** | messages — the product's END | Meshtastic + RNS, RF-native | **survived** |
| **Access** | ssh, tunnels, control | 100% internet | dead |
| **Evidence** | cloud push, map, pages | 100% internet | dead (staleness) |

The product kept working; **observability and control died**, and the operator
found out by looking outside the app. Access and Evidence need RF fallbacks.

### The survivability ladder (why the fleet died and the nodes did not)

| Tier | Power | Lala outcome |
|---|---|---|
| ESP32 + LoRa + solar | ~0.1–0.5 W | **survived, hundreds** |
| Pi + RNode LoRa + battery | ~3–7 W | possible, UNTESTED — **no Pi has a battery** |
| Pi + AREDN | ~10–15 W | needs infrastructure |
| Pi + Starlink | ~20–100 W | dies first |

One to two orders of magnitude separates the fleet from the network it watches.
That gap is the finding, and it is why the V4 discharge number (54.1 h) and the
INA219 shunt thread matter more than they looked — they measure the constraint
that decides who is alive.

**Design target for STANDALONE**: not "runs on a Pi" but **"the same survival
profile as the nodes it watches"** — battery/solar, RF-only, duty-cycled, no
switch, no uplink, still telling the truth. Not met today. This is also what
gives the DORMANT posture arc its real purpose: staying ALIVE and HONEST at a
duty cycle a panel can carry, not power thrift.

Open question that sizes the whole design: **what is the smallest MeshForge
that still tells the truth, and how long does it run on a battery and a panel?**

### MEASURED: the fleet's RNS interface inventory (2026-09-07)

Swept `/etc/reticulum/config` on every reachable box, comments stripped:

| Box | AutoInterface | TCP | RNode |
|---|---|---|---|
| moc | ✓ | 1 client + 1 server | — |
| moc1 / moc2 / moc4 / kiai | ✓ | 2 client | — |
| moc5 | ✓ | 1 client | — |
| moc3 | ✓ | 1 server | **1 RNodeInterface** |
| lehua | *(config unread — UNKNOWN, not "missing")* | | |
| **VolcanoAI** | **NONE** | 1 client (hardcoded LAN IP) | — |

**Two findings.** (1) **VolcanoAI — the manager, canonical writer and cloud
publisher — is the LEAST-connected RNS node in the fleet**: no AutoInterface,
one TCP client to a hardcoded LAN address. If that one target goes, the manager
is RNS-partitioned. Every peer has an AutoInterface; this box does not. Whether
that is deliberate is UNKNOWN — ask before "fixing" it (a declared absence is a
human decision).
(2) moc3 is the only box with an RNodeInterface.

### ⚠️ MEASURED: the RF control plane has ZERO working links

moc3's RNodeInterface is `Status: Up` — and has **never received a byte**:

```
RNodeInterface[RNode LoRa]   Status: Up   Rate: 10.94 kbps
  Traffic : ↑910.11 KB      ← transmitted
            ↓0 B            ← never received, ever
  Airtime : 1.28% (15s), 0.43% (1h)     Intrfrnc.: -71 dBm (noise fl. -100)
  Access  : 64-bit IFAC      Battery : 0% (charging)
```

910 KB announced into the void, spending airtime and power, for nothing.
**`Up` is presence, not function** — an RNodeInterface with no peer is Up and
carries nothing (the calibrated_claims coverage lesson, in RF form). Cause is
almost certainly the simplest: moc3 is the ONLY RNodeInterface in the fleet —
**one radio is a transmitter, not a network**. Secondary suspects once a peer
exists: the `64-bit IFAC` (derived from `network_name` alone — no passphrase
line is set) must match EXACTLY or packets drop silently, reproducing this same
`↓0 B` symptom; and RX itself is unproven.

**So the RF-control-plane premise is at zero, not at one.** First step is
HARDWARE, not config: a second RNode, then prove a packet crosses. That is the
smallest falsifiable unit of the whole idea.

moc3's exact stanza (replicate verbatim, change only `port`; use the
`/dev/serial/by-id/` path, never `ttyUSB0`):
`type=RNodeInterface, frequency=903625000, bandwidth=250000, txpower=17,
spreadingfactor=7, codingrate=5, id_callsign=WH6GXZ, id_interval=600,
network_name="hawaiinet rns"`.

**Where the second one goes: VolcanoAI** — it is the manager AND the
least-connected node, so this closes both gaps at once. Bench the two radios in
the SAME ROOM first: prove a packet crosses before proving range.

### 🔑 The unifying principle: a portable fleet cannot know its own addresses

The operator's goal — *"portable, self-healing, plug into a new network (e.g. a
satellite) without a multi-day config"* — is blocked by ONE defect class found
three times tonight:

- `rtun` pins the endpoint as a **hardcoded LAN IP in two places**
  (`/etc/init.d/rtun` AND `RTUN_REMOTE` in `rtun_watchdog.conf`).
- VolcanoAI's only RNS interface is a **TCPClientInterface to a hardcoded LAN
  IP**, and it has no AutoInterface to fall back on.
- The `/etc/hosts` fleet block is seeded from live DNS — correct today, but it
  couples names to the edge being up and unchanged.

**The fleet is ADDRESS-PINNED, and that is exactly what makes a network change
a multi-day reconfiguration.** Cure is discovery-first: AutoInterface for RNS,
names not addresses everywhere, and **RF as the discovery medium of last
resort** — a box that comes up on an unknown network should find the fleet over
LoRa and be told where it is.

### Tiered evidence — the real design work

RNS is transport-agnostic and already on every box, so the Access and Evidence
planes should ride RNS rather than raw TCP-to-cloud: same code path over fiber,
AREDN or LoRa, with only bandwidth changing. But **LoRa is ~1–5 kbps effective
— a 260 KB map snapshot over LoRa is not slow, it is impossible.** So the work
is defining what survives each step down:

- **Broadband** — full snapshot, maps, git (today's behaviour).
- **AREDN (Mbps, line-of-sight)** — the missing middle; real IP over ham RF.
  Status digests, `/fleet` truth, ssh. This is what makes "TCP works with RF"
  literally true. AREDN is already first-class in the code
  (`utils/aredn.py`, `watchdog_probes_aredn.py`, `_map_collector_aredn.py`),
  and moc1 ran AREDN-only during the 06-24 outage — but its RF DEPLOYMENT state
  is UNKNOWN.
- **LoRa (kbps)** — heartbeat, alerts, posture. Tens of bytes.
  *What is the smallest message that still tells the truth?*

### The yurt — a second site you can walk to

A **Starlink Standard v4 now lives in the yurt** (2026-09-07). Putting OpenWrt
there makes it a real second edge with a physically independent WAN — the
plan's "QTH and kit differ only in WAN and box count", on-property.
**Join it to the house over AREDN RF, not wire** (wiring it with FiOS proves
nothing — operator's point). Then Lala becomes a weekly DRILL rather than a
weather event: kill the yurt's Starlink → does RF hold? kill the house WAN →
does the yurt's Starlink become the fleet's uplink? kill both → does the fleet
still tell the truth locally over LoRa?
⚠️ Needs line-of-sight (a physical survey, not a config), and **Standard v4 is
the BIG dish** — materially more power than a Mini, which changes the battery
budget.

### Clock: the silent multiplier

RTC-less Pis + no NTP during an outage = drift, and moc4 has already run ~8 days
behind. Wall-clock instruments (cron, verdict freshness, wtmp) all lie
TOGETHER. **GPS-disciplined stratum-0 is outage-proof** and needs no internet —
already planned for the kit; it should extend to the fleet's own time island.

### Next steps, ordered by what Lala actually taught

1. **Second RNode on VolcanoAI; prove a packet crosses** (same room first).
   Everything RF is downstream of this and it is falsifiable in an hour.
   ⚠️ `rnodeconf` is installed (`/usr/local/bin`, rns 1.3.8+mf.0) but the TUI's
   RNode handler only does Detect / Deep Scan / Recommended Config — it never
   calls `rnodeconf`. Flashing means leaving the app, which is an MF018
   (in-domain remediation) gap worth closing.
2. **Battery/solar bench for the Zero 2W bot.** The bot died for want of a
   battery; the new bot is a Pi Zero 2W + mini HAT. NO PI HAS A BATTERY TODAY.
3. **Power inventory per box** — measured draw + what each has for battery and
   solar. Mostly UNKNOWN, and that is itself the finding.
4. **Then** the architecture map — organized by SURVIVABILITY UNDER POWER LOSS,
   not by protocol, with a "never tested" column (falsifiability applied to
   architecture).

**Deferred deliberately:** the cloud ssh rendezvous and Vultr consolidation.
Measured while there: the map VPS is **327 ms** away (Hetzner EU, 0% loss —
distance, not fault; 260 KB takes 13–16 s, latency-bound, 144×/day) while the
operator's own Vultr box `wh6gxzhub.ddns.net` is **9.5 ms**. The Europe box is
2 vCPU / 3.8 GB / load 0.00 serving 4.3 MB of static files and nothing else, so
consolidating onto Vultr would be cost-NEGATIVE and ~34× closer. ⚠️ Inbound :22
to the Vultr box TIMES OUT from VolcanoAI (filtered, not refused) — access
details needed. **But none of this survives a power outage, which is why it is
parked below the hardware items.**

### MEASURED: ~80% of the mesh is power-invisible (2026-09-07)

```
distinct nodes seen (2-day window):  385
             reporting battery:       75
             reporting voltage:       67
```

**310 of 385 nodes tell you nothing about whether they can survive the next
outage.** So the question Lala posed — *which of these is still here when the
grid is not?* — is UNANSWERABLE from our own data. The V4's silent telemetry
was not a one-node bug; it is the NORM, and it was treated as an anomaly for a
day. ⚠️ Consequence for the INA219 plan: a shunt measures ONE node beautifully;
`telemetry.device_update_interval` on the other 310 gives the whole mesh in
outline. Coarse and quantized, but ~300x the reach. Do both; the cheap one first.

⚠️ Position data cannot substitute: 385 nodes report only **270 distinct
coordinates** (one point shared by 10 nodes, another by 9, another by 8; lat
decimal places range 3-7). Fixed positions are hand-entered and copied, so
co-location CANNOT be inferred from the DB — a distance of `0.0 m` between two
nodes is not evidence they share a site. Also means map placement is looser
than it looks.

### Farley-server: regional infrastructure with the fleet's own defect

`!a2ed8ea0` `Farley-server`, **STATION_G2**, role **ROUTER_LATE**, 8 dBi at
23 ft, reaches Mauna Kea / Mauna Loa / HPP — part of **Hawaii Meshtastic
critical infrastructure**, and **it went down in Lala. Needs solar.** It also
reports NO battery and NO voltage, so its loss is only visible as silence.

**Why an elevated relay outranks leaf nodes for power work:** Meshtastic's hop
limit is a hard budget (3 default, 7 max). An outage disproportionately kills
the *mains-powered, well-sited relays* — so surviving battery nodes are alive
but the paths BETWEEN them lengthen, and routes that took 3 hops may need 5 and
**silently exceed the limit**. Both endpoints healthy, no route. The mesh does
not degrade gracefully at that boundary; it partitions. One autonomous node at
height keeps everyone else's hop count down. **Rank the power work: elevated
relays first, leaves last** — the opposite of what is cheapest.
⚠️ `FOh-ENV` (`!96393e02`, RAK4631, CLIENT_BASE) also reaches Mauna Kea, so the
regional path has RF redundancy. UNKNOWN whether it has POWER redundancy — if
both are mains at one site, two paths that fail together are not two paths.
Open question: did FOh-ENV survive Lala?

### ⚠️ THE structural finding: the tent is a single point of failure

**Tent** — VolcanoAI (manager, canonical writer, cloud publisher, + the CH341
toad at 225 pkt/hr), kiai, alaula (tunnel box, OpenWrt), moc, moc5,
meshanchor-server, **m1 (border, .248)**, TWO AREDN routers, wh6gxz-POE,
several SHORT_TURBO nodes. Fed by ~300 ft of fiber with a few routers in
between (none of which the fleet can see).
**Yurt** (~250 ft away, ʻōhiʻa forest, no LOS) — moc3 (gateway-only, the only
RNode), Starlink Standard v4, fiber, AREDN, two routers.

The fleet is **not distributed in any sense that matters for survival**: one
structure, ONE POWER FEED. That is why Lala took "the bot and much of the fleet"
while several hundred battery/solar RF nodes carried on. Not nine power
problems; ONE shared fate.

⚠️ **CORRECTED 2026-09-07 (operator).** This section first said "a TENT is the
least storm-survivable building on the property." **That was wrong** — it is a
**Cimmaron platform tent, standing 12 years through many storms including
Lala**. I reasoned from the word "tent" and wrote an assumption into the record
as a finding, in a file full of rules against exactly that. The correction
SHARPENS the finding rather than removing it: the structure held and the fleet
went down anyway, so the shared fate was never the building — **it is the single
POWER DOMAIN.** State it that way; it is both more accurate and more actionable.
Corollary: alaula's reverse tunnel crosses a LOGICAL boundary (m1's hardened
`.88`) between two machines sitting FEET APART in the same tent. It was never
an internet problem — see the deferred rendezvous work above.
⚠️ A second WAN for the tent (Starlink Mini) treats the wrong problem: the
tent's exposure is not its uplink, it is that it holds everything.

### ⚠️ AREDN — the TCP-over-RF middle tier is DARK

| | Count |
|---|---|
| AREDN nodes that exist | **3** — `WH6GXZ-6-VOLCANO-QTH-HAP` (tent), `WH6GXZ-6-VOLCANO-HI-HAP`, `WH6GXZ-6-BI-ECOM` |
| declared in `fleet_naming.json` | **2** — the tent's node is UNDECLARED |
| configured for polling (`map_settings.json`) | **1** |
| successfully polled | **0** |

Declared in `deployment.json` as *"polled by the map collector since
2026-07-17"*; `aredn_config_capture.log` has been **0 bytes since Sep 5**.
Root cause is the documented **wrong-vantage** class: the poll target `hap`
resolves fine (`192.168.86.249`, same /24), but AREDN serves `sysinfo` on the
MESH side and **VolcanoAI has no route to any 172.16/12 network** — the polling
box structurally cannot see what it asks for.
**A hand-maintained registry drifted 3 → 2 → 1 → 0 with no alert.** The
"missing middle" between LoRa and broadband is real hardware the NOC has never
once seen.

### 🎯 The product thesis (operator, 2026-09-07): the TUI sees what is around it

> *"my goal was the tui to see what around it - to diagnose - connect - fix"*

**The architecture map should be GENERATED, not written.** A hand-drawn map is
stale the first time a router moves and can diagnose nothing; the AREDN drift
above is that failure in miniature. Make the diagnostic tool and the map the
same artifact and it cannot go stale, because LOOKING IS THE PRODUCT.

Nearly every piece already exists — it is aimed wrong, not missing:

| Capability | Exists as | Aimed at today |
|---|---|---|
| hop tracing (no `traceroute` binary on the fleet) | `utils.wan_autotrace` | the WAN only — takes no target |
| AREDN node + RF `link_info` | `utils/aredn.py` | one node, wrong vantage |
| RNS topology | `rnstatus` / `rnpath` | per-box, manual |
| 385 RF nodes, SNR, position | `node_history.db` | the map |
| service + port state | `utils/service_check.py` | per-box |

**And it is the same feature as portability.** "Plug into a new network without
a multi-day config" and "see what is around me" are one capability: discovery
replaces configuration, which is the cure for the address-pinning defect found
three times tonight. It is also MF018 (never quit the app to fix it) finally
applied to the NETWORK layer instead of just the service layer.
**First build: give the tracer a `--target`, add a "what is around me" TUI view**
(L2/L3 neighbours, hops to each fleet box, AREDN links, RNS interfaces + peers,
RF nodes heard). Mostly wiring together code we already own.

### 🚨 NEW DIRECTION: "remote MeshForge" — an ecomm package for VERT

Operator works with an emergency response team (**VERT**). The package shape:
**mini-dudeai + OpenWrt**, standalone, deployable by people who did not build it.

**This changes the requirements class, and it should be treated as a different
product than the lab fleet:**
- **Non-operator users.** It must be diagnosable by a volunteer at 2 a.m. with
  no NOC, no manager, and no one to call. The TUI is expert-facing today.
- **Zero-config deploy.** The "multi-day config" problem stops being an
  annoyance and becomes disqualifying.
- **Honest degradation becomes LIFE-SAFETY.** The calibrated-claims and
  honest_failure_modes doctrine was written for infrastructure trust; in an ERT
  context a map that LOOKS live while frozen is something a responder acts on.
  The existing discipline is exactly right — the stakes are now higher.
- **Power autonomy is the gate** (see the ladder above), and it is unproven.
- **RF-first, local-first**: responders' phones on the OpenWrt AP using local
  services with no internet; LoRa/Meshtastic when nothing else works.

The STANDALONE offering finally has a concrete customer. Design target stands:
*the same survival profile as the nodes it watches.*

---

## ⚡ Power sizing — the math, for a network engineer (2026-09-07)

> Operator: *"i'm a network geek - power is something i need to learn more about
> - and to get the formula right batt/solar/gen/etc"*. Power is the scarce
> resource this domain does not measure (see the 80%-blind finding above), so
> the formulas live here beside the findings that need them.

### The mapping — this is capacity planning with different units

| Power | Networking |
|---|---|
| Watts (W) | **rate** — bits/sec |
| Watt-hours (Wh) | **volume** — bytes transferred |
| Battery capacity | **buffer depth** |
| Solar | a **bursty ingress link**: scheduled nightly outage + unscheduled weather ones |
| Load | constant **egress** |
| Days of autonomy | how long the buffer covers a TOTAL ingress outage |
| Depth of discharge (DoD) | **usable** buffer vs allocated |
| Derate factors (η) | protocol/framing **overhead** |
| Voltage drop in wire | **cable loss** |

### The four formulas

**1. Daily load**
```
E_day (Wh) = P_idle × 24  +  (P_tx − P_idle) × duty × 24
```
For a PA-equipped router like Station G2, `P_tx` is large and `duty` dominates —
which is why it must be MEASURED, not assumed.

**2. Battery**
```
Usable Wh  = E_day × days_of_autonomy
Nominal Wh = Usable ÷ DoD
Amp-hours  = Nominal Wh ÷ V_nominal
```
DoD: **LiFePO4 ≈ 0.8**, lead-acid ≈ 0.5 (deeper destroys it), Li-ion ≈ 0.8.
LiFePO4 is the right chemistry here: cycle life, heat tolerance, tolerates
partial states of charge.

**3. Solar**
```
Daily harvest = P_panel × PSH × η
⇒ P_panel ≥ E_day ÷ (PSH_worst × η)
```
`PSH` = peak sun hours (location + worst month). `η` = panel derate (dirt,
temperature, angle) × controller: **PWM ≈ 0.65, MPPT ≈ 0.75–0.85**. MPPT earns
its cost exactly when you are marginal — which, in Volcano, you will be.

**4. Generator** — a different problem: it must carry the load AND the charge
current simultaneously; runtime = fuel ÷ burn rate. A recovery tool, not a supply.

### ⚠️ The error almost everyone makes

**A panel sized to match AVERAGE consumption produces a system that never
recovers.** It is the 100%-utilized-link problem: if ingress equals egress the
buffer never refills after a deficit, so the first three-day storm drains the
battery and it STAYS drained. Size from the **worst month**, then add **20–30%
headroom** so good days pay back bad ones.

### Worked example — Farley-server (assumes 2 W avg, UNMEASURED)

```
E_day   = 2 W × 24 h                 =  48 Wh/day
battery = 48 × 3 days ÷ 0.8 DoD      = 180 Wh  → 12 V × 15 Ah LiFePO4
panel   = 48 ÷ (3 PSH × 0.7 η)       =  23 W minimum
        + 30% recovery headroom      ≈  30 W
```
**So the on-hand 20 W panel is likely marginal-to-short.** Note the
sensitivity: at 3 W average the panel wants ~45 W. Everything hinges on the load
measurement — hence the shunt first. ⚠️ `PSH = 3` is an ESTIMATE for Volcano
(windward, ~4,000 ft, rainforest); Kona-side Hawaii gets 5–6 and we do not.
Verify against NREL **PVWatts** — that number moves panel size linearly.
Station G2 facts that drive this: LoRa **PA, max RF out 36.5 dBm (4.46 W)
US915**; input **15 W USB-C PD or 9–19 VDC**, with a **12 V connector on the
side** (so: 12 V battery → charge controller → side connector, no USB-PD in the
path). ⚠️ TX power is the biggest single lever on the energy budget, but
Farley's VALUE is its reach to Mauna Kea / HPP — measure SNR margin on the long
links before trading any of it away.

### Order of operations

1. **MEASURE the load** (INA219 on the 12 V line). Do not guess — two estimates
   in one session were wrong, one by a whole device class.
2. `E_day` from the measurement, including duty cycle.
3. Autonomy days — **3 is standard for critical**; Volcano's weather argues more.
4. Battery = `E_day × days ÷ DoD`.
5. Panel from **worst-month** PSH, +30%.
6. Charge controller rated above panel short-circuit current; MPPT if marginal.
7. Wire gauge for **≤3% voltage drop** (`V_drop = I × R` — same instinct as cable loss).

Refs: PVWatts <https://pvwatts.nrel.gov/> ·
Meshtastic power measurement
<https://meshtastic.org/docs/hardware/solar-powered/measure-power-consumption/> ·
Solar Station G2 thread <https://meshtastic.discourse.group/t/solar-node-station-g2/13284>

### 🏆 There is a PROVEN reference design — get its BOM before computing anything

A friend of the operator built the **BIARC Mauna Loa Station G2**, and it has
**survived 150 mph winds**. That is a field-hardened solar Station G2 install on
this island, same hardware, built by someone reachable.

**This outranks the arithmetic above.** Calibrated-claims rule: a witness you did
not author beats one you did, and a design that survived a real 150 mph event
beats a spreadsheet. Ask for: panel watts, battery chemistry + Ah, charge
controller model, the 12 V connector/cabling used (the operator's "hard to find"
part), enclosure + mounting/guying detail, and — most valuable — **whether it has
ever run down, and in what weather**. That last answer is the only real
autonomy measurement anyone has.
⚠️ It also sets the mechanical bar: 150 mph is Cat-4/5. The tent that currently
holds most of the fleet does not meet it, and neither does anything we would
have specified.

---

## 🏗️ Consolidation analysis — moving the lab to the yurt (2026-09-07)

Operator is considering consolidating the whole lab into the yurt, and flagged
the obvious problem themselves: *"that's a lot of RF so that would need some
planning."*

**The tension is real and worth naming: consolidation serves MANAGEABILITY;
Lala's lesson is DISTRIBUTION.** Today there are two sites and two power
domains. Consolidating gives one of each — and if the yurt then loses power you
are back to exactly Lala: the mesh carries on and the thing watching it is dark,
with no observer left to say so.

### The rule

> **Never let the observer share a failure domain with everything it observes.**

### The resolution — consolidate compute, distribute RF and power

- **Compute in the yurt: fine, even good.** Ten boxes on ONE well-sized battery
  bank is far easier to get right than ten scattered power problems, and it is
  one place to maintain, cool and secure.
- **⚠️ Designate an AUTONOMOUS OUTPOST.** At least one node outside that power
  domain, on its own solar, running mini-dudeai, able to say *"the yurt is
  dark."* Farley or base1 are the candidates. One honest observer outside the
  blast radius is the whole difference between an outage and a SILENT outage.
  This is cheap insurance and it buys back exactly what consolidation costs.

### RF planning — three concrete items

1. **Desense is already measured, before anything moves.** moc3's RNode sits in
   **29 dB** of local interference (`Intrfrnc. -71 dBm` vs a `-100 dBm` noise
   floor) in the yurt TODAY. Adding ten boxes' worth of 900 MHz radios to that
   structure degrades every receiver in it. Cure: **antennas OUT of the
   building, on masts, with VERTICAL separation**, coax or POE down — the same
   move that buys canopy clearance for the tent↔yurt path (~1.2 dB in 30 ft of
   LMR-400 against ~15–30 dB of vegetation avoided). Co-located blocking at 2 ft
   is ~**-6 dBm** into a neighbouring front end, ~117 dB above sensitivity —
   frequency separation does NOT help, only physical separation does.
2. **Write an actual channel plan** — Meshtastic presets (LongFast/ch20 +
   SHORT_TURBO/ch8), the RNode at 903.625 MHz, AREDN's 2.4/5.8 — rather than
   discovering collisions empirically.
3. **RF exposure.** Multiple co-located transmitters — including Station G2's
   **4.46 W PA** — in an OCCUPIED structure requires the aggregate evaluation
   under §97.13(c)(1). Each radio passes alone; the aggregate is what catches
   people, and it is a human-safety question, not paperwork.

---

## 🔗 The unifying principle: infrastructure is UNTRUSTED (operator, 2026-09-07)

> *"this also frames why we have autonomy with tcp - dns. when we plug into a
> network - we can use it like we do rf - the user has the tools, tui, and you
> if they choose … building linked connections"*

**This is the through-line for the whole domain, and it explains work that
already exists.** RF taught the posture: there is no DHCP on a LoRa mesh, no
authoritative resolver, no admin to call — a node ANNOUNCES and DISCOVERS, and
depends on nothing it did not bring. **Applying that same posture to TCP/IP is
the insight**: treat a plugged-in ethernet the way you treat a band — probe it,
learn it, use what is there, depend on none of it.

Seen this way, several existing efforts are one principle, not separate chores:

| Work | What it really was |
|---|---|
| `gen_fleet_hosts.py` + the `/etc/hosts` block | **DNS autonomy** — names resolve with the resolver or the uplink DOWN |
| the mf.internal AAAA fix (902 ms → 4 ms) | removing an **internet dependency** from LOCAL name lookup |
| the cloud-init `manage_etc_hosts` fight | refusing to let a foreign owner overwrite our autonomy |
| ⚠️ address-pinning (rtun ×2, VolcanoAI's RNS target) | the **FAILURE** of this principle — three live instances |
| "the TUI sees what is around it" | the **IMPLEMENTATION** of it |

**So discovery is not a feature request; it is the principle made executable.**
And it is why "plug into a new network without a multi-day config" and "see what
is around me" are the SAME capability — configuration is what you need when you
assume the infrastructure; discovery is what you do when you do not.

### And the user-facing half

*"the user has the tools, tui, and you if they choose."* For the CERT/VERT
package that is three layers, and the third one has a constraint: **an assistant
in a disconnected field kit cannot be a cloud assistant.**
⚠️ That retroactively gives **tier-L (Ollama) its missing justification.** It was
parked earlier the SAME DAY (`MINI_CADENCE_PRESCORE=0`) precisely because it
"earns nothing in production today… its only product justification is the
field-kit chat compiler, which has no code yet." Tonight's arc gives that
justification a concrete shape and a customer. Parked, not deleted, was the
right call — revisit at the field-kit milestone, not before.
See [[project_ollama_parked_2026_09_07]].

### Shipped — batch 4: the MIRROR, and the first off-manager consumers (2026-09-10)

The declaration now reaches the boxes that act on it. Until this batch every
one of the nine wired consumers ran on the MANAGER, so the design's own
attack #4 — *if the declaration lives only on the manager, every other box
loses it exactly when it needs it* — was still fully open, and mini's
per-box rules (which run on all ten boxes) had never heard of posture at all.

**The mirror** — `scripts/fleet_posture_sync.sh` (+ `fleet_posture_stamp.py`).
Registry-sync pattern, with three deliberate inversions: a box with NO copy is
SEEDED (absence here means "page everything", which is the case that needs the
file); CLEARING propagates (a mirror that only learns about declarations and
never their end keeps a fleet silent about a peer that came back); and NO
manager file at all WITHDRAWS the remote copies, because absent is the safe
state. The audience is the boxes STAYING UP — a box being powered down does
not need to be told it is dormant, and the survivors are the ones whose
detectors would otherwise page. Landed artifacts are verified by re-hashing on
the remote, never by trusting scp's rc.

⚠️ **Not cron-wired, deliberately** (harness_restraint.md, in force to
2026-10-09). It runs where it is load-bearing: inside `fleet_power.py down`,
between the confirmed declaration and the first poweroff, and again on the
return leg once boxes are cleared. A timer is a separate decision for after
the freeze.

**A mirror is not the original.** Every copy carries `mirror: {from, at}`; the
manager's file never does. A MIRROR whose reader cannot confirm its own clock
silences NOTHING and says so, where the authoritative document HOLDS. HOLD is
right on the manager — there is an operator and a real file. On an RTC-less Pi
restoring a stale time from fake-hwclock it has no upper bound: it is "the box
that died in the storm is still dormant in November", rebuilt by the mirror.

**Clock confidence, finally wired.** `_effective()` has carried a
`clock_confident` HOLD branch since 2026-09-01 and **no caller had ever passed
the flag** — a written mechanism with no way in, harmless while the posture had
one reader on a disciplined clock, wrong the moment it had ten. `read_posture`
now ASKS (timesyncd stamp → `timedatectl NTPSynchronized`), and anything it
cannot observe is NOT confidence. moc4 is the case in point: the plan picks it
for the duty-cycle drill *because* it has "hardware RTC — the least forgeable
clock", and on 2026-09-10 that RTC was proven dead, its early journal stamped
33 minutes in the past. That premise is false and this leg is why it matters.

**First off-manager consumers**: `probe_tracer_peer_unreachable` and mini's
federation source skip a peer the operator declared dormant/detached — the two
detectors that fired for moc1/moc2/moc4 during the 09-10 UPS shutdown while all
three were off exactly as intended. Every skip is WITNESSED (hfm #9): as the
class disposition when it is the whole story, on each surviving signal when it
is not, and as a `posture_expected_absence` condition in mini. No rule is
seeded for that kind and none may be during the freeze.

**Drilled, not traced** — `scratchpad/drill.sh` plants 14 defects (mirror
trusted like the original; strip-before-exact peer resolution; read_posture
assuming its clock; an unobservable clock read as confident; the probe ignoring
the declaration; a silent suppression; `shed` treated as expected-absent; the
stamp fanning out an invalid document; the empty declaration reading as
in-effect; the mirror dropped from the shutdown path, from the return leg, and
made fatal). **14/14 caught.** The first run caught only 12 — one gap was a
test of mine that injected a fake probe and therefore exercised the lambda
instead of the branch it claimed to cover (the 2026-07-25 lesson, reproduced
inside the tests written to prevent it), and the other was a new test class
accidentally inserted mid-class, orphaning four tests into the wrong one.

Remaining in the arc: `probe_fleet_box_unreachable` reading the new verdicts,
claw RF watch lists, `shed` overlay on the role plan, validator with roles,
`posture_drift` as a watchdog class (FROZEN until 2026-10-09), V4–V8 in the
virtual fleet.
