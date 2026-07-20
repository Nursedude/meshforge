# Structural-dark burn-down — routing plan (2026-07-19)

> Operator directive: the Known Blind Spots panel (`STRUCTURAL_DARK` in
> `src/utils/fleet_truth.py`, byte-locked MF↔MA) is a work-list, not wallpaper —
> understand → fix → test → deploy, one row at a time. This doc triages every
> row to a model tier (`.claude/rules/model_advisor.md` ladder) so any future
> session can pick up the next row without re-deriving the plan. Closing or
> narrowing a row edits BOTH repos' fleet_truth.py identically + this doc.
> Every closure ships the three artifacts (honest_failure_modes #10): the
> probe (→R), the doc/runbook row, and an eval case (→L).

## Closed / narrowed

- **live_claw_nats_not_wired_to_mini** → **claw_edge_rf_coverage_partial**,
  NARROWED 2026-07-19 (row 7). The row's premise was STALE: live sensor reads
  already existed (dudeclaw-01's claw mini, healthy) and claw telemetry already
  reached the fleet (tick files → /api/status.claw → rollup card). The REAL gap
  was found by reading the data: **dudeclaw-02 (battery, the out-of-band RF
  witness) drained to 2.41 V and went dark 17.4 h on 07-10, and the fleet's only
  words were `cron_verdict_stale: claw02_metrics FAIL — fix the job`** — the
  capture cron's exit code was the sole downstream witness, so a dead radio node
  was laundered into infrastructure noise in the channel known to flap benignly.
  Two compounding instances of the same class: the `battery_v lt 3.5` spec was
  bound to dudeclaw-01 (USB, 4.06 V forever — can never breach), and
  `build_tick`'s `ok = device_info AND ble` pinned BLE-less dudeclaw-02 at
  `ok:false` in every tick ever, so /fleet rendered a healthy claw
  "unreachable". Cure: `claw_device_dark` + `claw_battery_low` signals read from
  the tick files the capture already writes (NO second NATS poll — one poller,
  one threshold set), battery captured per device, `reachable` made explicit.
  Deliberate: fleet preset still does not poll NATS; one fault one owner; unknown
  ≠ charged; stale ticks stay cron_verdict_stale's page.
  Runbook: `.claude/research/claw_edge_hardware_signals_2026_07_19.md`.
  Eval: `oracle-claw-dark-not-a-failing-cron`.
  RESIDUAL: thresholded live sensing (temp/anomaly/LoRa-ears) still runs only in
  a per-device claw mini; claw-02 has none, deferred while its pack was
  mid-discharge-measurement. The staged `claw_sensors.with_ears.json` (LoRa
  ears) is the strongest candidate for **row 9's** independent OTA witness.

- **dep_version_drift_strays_blind** — **ACCEPTED-PERMANENT 2026-07-19** (row 3;
  the first row closed by DECISION rather than by code). Operator chose accept
  over extend, on a fleet-wide survey of every root-readable install of 12 deps
  across all 8 boxes (run with the probes' own enumeration helpers). Finding:
  the only deps installed by COMPETING TOOLS (pip/pipx/apt/fork-pin) are
  meshtastic + rns/lxmf — both already watched. For OS-shipped libs a venv copy
  shadowing system-dist is the DESIGNED state, so extending would either page
  on benign divergence (moc4's system `requests` 2.28.1 is below the core.txt
  floor while the venv consumer runs a compliant 2.34.2 — a signal true about a
  copy nothing imports and false about the box) or never fire at all against
  floors reality outgrew. paho/folium arrive via pip into the venv only — no
  second installer, no mechanism. Shipped with it: the `_DEP_VERSION_WATCHED[0]`
  closed-consumer gate (`TestDepWatchedTupleClosedConsumer`) — two call sites
  index [0], so growing the tuple silently half-watches the new dep; the test
  now fails and names the lines. Benign-and-recorded: kiai meshtastic
  system-dist 2.7.10 vs user-pipx 2.7.9 (probe silent BY DESIGN — below-floor
  clause), moc5 pypubsub user-site 4.0.3 shadowing system 4.0.7.
  Note: `.claude/research/dep_stray_watch_scope_2026_07_19.md`.
  Eval: `oracle-dep-stray-watch-scope-accepted`.
  **Revisit ONLY when a dep gains a second installer.**

- **federation_digest_federator_only** → **federation_mapless_box_unwatched**,
  NARROWED 2026-07-19 (row 6, same arc): federation is now watched PER VANTAGE
  — `MINI_DUDEAI_ENABLE_FEDERATION=1` on every MAP-RUNNING box (the source
  polls the box's OWN :5000, so it was free data being thrown away), with the
  two federation rules ported into the gateway seed. The gateway copies
  ESCALATE ONLY, never ntfy — paging ownership stays with the manager's
  fleet_offline_check + manager_deadman so one dead peer can't fan out to N
  pages. The digest half closed BY DECLARATION (federator artifact by design).
  moc3's known-normal suppression ported alongside (**retire both copies
  together at the RNS roll**). Env lives in `~/.config/meshforge/mini_dudeai.env`
  (EnvironmentFile), NOT the unit — the checklist said unit; the live-process
  env is the consumer of record. Live vantages at closure: VolcanoAI 6 peers,
  moc 4, kiai 5, moc1/moc2 3 each; moc4/moc5 enabled but 0 peers (inert);
  moc3 (map stopped for the soak) + meshanchor-server (no federation key)
  stay 0 — wiring a map-less box would pin src_errors every tick.
  Runbook + fleet table: `.claude/research/federation_per_vantage_2026_07_19.md`.
  Eval: `oracle-federation-mapless-box-src-errors`.

- **calibration_drift_not_paging** — REMOVED 2026-07-19 (row 4, same arc;
  the first FULL removal): soak criterion met (34 days: one fire episode,
  2 TRUE breaks — 44b5b92/2de68ec — 0 false positives). Both seeds'
  `calibration_drift_any` promoted propose_escalation → ntfy ([AMBER],
  high, 6h cooldown), fleet live-rules refreshed via provenance merge
  (`refreshed 1` on all 8 boxes). MF `d6ac0fb8`, MA `1749275b`.
- **aredn_configured_source_only** — NARROWED 2026-07-19 (row 5, same arc):
  role-aware leg on `probe_aredn_source_dark` (MF `514a4951`) — a box
  declares the organ in deployment.json `organ_expectations.aredn` (per-box
  overrides layer; fleet_roles.yaml stays instance-free per MF014);
  declared + empty `aredn_node_ips` fires `declared-unconfigured` (the wipe
  class). Declarations live on VolcanoAI (hap) + moc5 (site node). Residual:
  a site with neither declaration nor config stays invisible; kiai (at the
  AREDN site, survey pending) gets its declaration when the survey lands.
- **user_unit_inactivity_blind** — NARROWED 2026-07-19 (row 1, same arc):
  `probe_user_unit_inactive` (signal `user_unit_inactive`, MF `8d1e546d`) —
  always-on user `.service` daemons watched bus-free (default.target.wants
  enrollment vs `/run/user/<uid>/systemd/units/invocation:*` liveness),
  covering parked-failed / stopped / user-manager-down (linger). Timers
  deliberately excluded (NO invocation marker — verified; schedules/SLO
  layer owns them). Residual: conditional/nested wants, non-operator users.
- **dep_version_drift_strays_blind** — NARROWED 2026-07-19 (this arc):
  `probe_rns_env_coherence` (signal `rns_stray_env_drift`, MF `621616c7`)
  closes the rns/lxmf leg — every root-readable copy incl. foreign pipx venvs
  must agree intra-box (the moc3 nomadnet-venv lesson; roll-gate serving).
  meshtastic leg was already closed by `probe_dep_install_fragmented`
  (2026-06-17). Residual: other deps' strays — see row 3 below.

## Open rows, triaged (priority order)

| # | Row | Cure shape | Tier | Size | Constraint |
|---|-----|-----------|------|------|------------|
| 1 | ~~`user_unit_inactivity_blind`~~ | CLOSED 2026-07-19 — see above | — | — | NEXT DEFAULT PICK is now row 2 (post-roll) or row 5 |
| 2 | `oracle_rns_send_blind` | `send_to_rns` distinguishes no-path from crash; real send errors leave a witness counter and land in the failure set (not the benign bucket) of `oracle_delivery_degraded` | **Opus** | ~half session | gateway-code deploy → wait for RNS-soak close / roll (deliberately deferred out of the mf.5 soak) |
| 3 | ~~`dep_version_drift_strays_blind`~~ | ACCEPTED-PERMANENT 2026-07-19 — see above; scope is deliberate, revisit only on a second installer | — | — | — |
| 4 | ~~`calibration_drift_not_paging`~~ | REMOVED 2026-07-19 — see above | — | — | — |
| 5 | `aredn_configured_source_only` | Role-aware expectation: `fleet_roles.yaml` declares which boxes SHOULD run AREDN; probe fires on declared-but-unconfigured (covers the "config wiped" case today's probe can't see) | **Opus** | ~half session | touches role engine both repos (MA role port exists) |
| 6 | ~~`federation_digest_federator_only`~~ | CLOSED/NARROWED 2026-07-19 — see above; row renamed `federation_mapless_box_unwatched` | — | — | NEXT DEFAULT PICK is row 9's claw-ears leg (rows 2+8 wait on the RNS roll) |
| 7 | ~~`live_claw_nats_not_wired_to_mini`~~ | NARROWED 2026-07-19 — see above; row renamed `claw_edge_rf_coverage_partial`. Residual = per-device sensor instances (claw-02), which feeds row 9 | — | — | NEXT DEFAULT PICK is row 9's claw-ears leg (or rows 2/8 post-roll) |
| 8 | `cross_gateway_dups_unsuppressed` | STEP 6 cross-gateway suppression: distributed coordination (which gateway yields, race windows, idempotency, partition behavior) | **frontier design pass** → Opus impl | full session+ | hardest row; design doc first, never straight to code |
| 9 | `mesh_rf_ota_leg_unwatched` | RF-side receipt: mesh ACK consumption (#74 T2 step 4) or a second receiver node as OTA witness (reference-node arc fit) | **frontier + operator/field** | multi-session | hardware/field-gated |

## Row 6 policy — RATIFIED 2026-07-19, IMPLEMENTED 2026-07-19 (kept as the decision record)

**Facts established (verified this session, MF HEAD `73043bfb`):**
- `FederationPeerSource` (src/mini_dudeai/presets/meshforge_fleet.py:58)
  polls `DEFAULT_FEDERATOR_URL = http://localhost:5000/api/status` — i.e.
  the box's OWN map. Every map-running box already computes
  `federation.peer_status` locally; enabling the source costs nothing new
  and gives a PER-VANTAGE view (box A sees peer C, box B can't = path
  problem, not box problem — evidence no single-vantage watcher can give).
- Gateway boxes disable it via `MINI_DUDEAI_ENABLE_FEDERATION=0` in each
  box's `~/.config/systemd/user/meshforge-mini-dudeai.service` (per-box
  user unit, not a template drift — check each box).
- Both federation rules (`federation_peer_unhealthy_unexpected` →
  propose_escalation, `moc3_federation_backoff_known_normal` →
  annotate_digest) live ONLY in the federator seed.

**Policy (the noise decision):**
1. **Escalations, never pages, on non-federator boxes.** Box-down PAGING
   ownership stays exactly where it is: the manager's fleet_offline_check
   (3-fail → ntfy) + manager_deadman for the manager itself. Gateway minis
   get `federation_peer_unhealthy` rules with `propose_escalation` ONLY —
   visibility in the brief + /fleet escalations, zero new pagers, so one
   dead peer can never fan out to N phone pages.
2. **Digest stays federator-scoped BY DESIGN** — situation_digest.md is a
   federator artifact; watching it elsewhere is meaningless. The row's
   digest half closes by DECLARATION (row text names it correct-by-design),
   not by wiring.
3. **moc3 canary suppression ports too.** Gateway boxes will see moc3's
   deliberate soak backoff in their local views; port the
   `moc3_federation_backoff_known_normal` annotate rule into the gateway
   seed alongside (retire both copies at the roll).

**Implementation checklist (all executed 2026-07-19; deviations noted inline):**
(a) DONE — flip `MINI_DUDEAI_ENABLE_FEDERATION=1` (digest stays 0) in each
    map-running box's `~/.config/meshforge/mini_dudeai.env` (**correction: the
    env is in the EnvironmentFile, NOT the unit as written here**) + `systemctl --user daemon-reload` +
    restart (linger is on; use the ssh user-bus idiom from this arc);
(b) add `federation_peer_unhealthy_unexpected` (propose_escalation,
    cooldown 1800) + the moc3 known-normal rule to
    `configs/mini_dudeai_rules.fleet_gateway.json`, then
    `promote_seed_rules.py --apply` fleet-wide;
(c) update both seeds' `_comment` headers (they currently DECLARE
    federation is federator-only — reader/writer pair, must move together);
(d) narrow the `federation_digest_federator_only` row in BOTH repos'
    fleet_truth.py (byte-locked): federation watched per-vantage
    everywhere, digest federator-scoped by design; run parity_check;
(e) verify: each box's mini brief shows the federation source alive with
    0 source_errors ≥2 ticks; moc3 rows annotate, not escalate; the
    federator's behavior unchanged; suites+lint+CI green; fleet_pull.

## Sequencing rules

- Anything deploying into `meshforge-gateway` (#2, #8) waits for the RNS
  1.3.8 soak → roll to finish. Watchdog/mini/map deploys are always safe.
- One row per session, done fully (probe + tests + seeds + registry edit
  both repos + parity + fleet deploy + live verification) beats two rows
  half-landed.
- Frontier sessions: spend on #8/#9 design and closure reviews; queue found
  work in `.claude/audits/review_provenance.md` per model_advisor.
- After each closure: narrow (don't delete) the registry row when a residual
  remains; the row text states what is now covered and what still isn't.
