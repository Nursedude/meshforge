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
