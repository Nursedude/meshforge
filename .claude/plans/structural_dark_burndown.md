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

- **oracle_rns_send_blind** — NARROWED 2026-07-19 (row 2), deployable half only.
  Premise refined first: the row said `send_to_rns` "swallows" exceptions, but
  the `except` block DOES leave witnesses (`stats['errors']` +
  `record_send_failure`). The real defect is narrower — it returns a **bare
  bool** collapsing no-path / open-circuit / real-exception into one `False`,
  so the ORACLE's call site cannot classify and a reason-less non-delivery
  falls into the probe's benign bucket. MEASURED on moc3's live audit log:
  103 records → 68 rns delivered, 4 declines, **1 ambiguous** (~1%). Blind spot
  real but small. SHIPPED (watchdog-only, not roll-blocked):
  `benign_rns_ambiguous` split out of the blended `benign` count, so the
  ambiguous slice is sized continuously instead of averaged into a
  clean-looking figure (honest_failure_modes #5). RESIDUAL: the bare-bool fix
  touches the LIVE RNS send path (`bridge_send_mixin.send_to_rns`, inside the
  meshforge-gateway process — the oracle responder is imported by
  `bridge_rns_events_mixin`) and stays deferred until the 1.3.8 roll closes.
  ⚠️ Do NOT "land it without restarting": the gateway's own wedge watchdog
  calls `os._exit(2)` → systemd restart, so landing gateway code activates it
  non-deterministically mid-soak. Roll state confirmed 2026-07-19: SSOT still
  pins rns 1.2.5+mf.5, moc3 still carries the deliberate canary drift.

- **mesh_rf_ota_leg_unwatched** → **mesh_rf_ota_egress_unproven**, NARROWED
  2026-07-19 (row 9). Its "hardware/field-gated" constraint DISSOLVED on
  inspection: both claws already answer `lora_stats` and are actively hearing
  (claw-01 heard_age 4s/158k pkts, claw-02 0s/101k). Shipped: lora_stats
  captured into each claw tick + `probe_claw_rf_silent` — a SEPARATE radio on
  separate silicon reporting the air, the first mesh-RF evidence no box can
  fabricate about itself. Rules: only ALL claws quiet counts (one deaf claw is
  that claw's problem); unreachable ≠ silent (claw_device_dark owns it); no
  ears ≠ quiet air.
  ⚠️ **ESCALATE-ONLY, threshold PROVISIONAL.** The 1800s window was the
  operator's staged guess marked "SOAK the heard-rate incl. overnight lulls
  first" — and that soak data COULD NOT EXIST until this capture shipped.
  Promotion path = the calibration_drift precedent (row 4): accumulate
  heard_age across full day/night cycles → read the measured quiet-hours max →
  set the window above it with margin → only then flip the seed rule to ntfy.
  Runbook: `.claude/research/claw_ota_witness_2026_07_19.md`.
  Eval: `oracle-claw-rf-silent-is-not-egress-proof`.
  **RESIDUAL (the real remaining half): this proves traffic EXISTS, not that
  THIS box's TX reached the air** — a deaf/mute gateway beside chatty
  neighbours keeps the probe clean. True egress proof needs per-source counters
  in firmware (`lora_stats` reports only `last from=`) or mesh ACK consumption
  (#74 T2 step 4).

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
| 2 | ~~`oracle_rns_send_blind`~~ | NARROWED 2026-07-19 — blind spot MEASURED (~1% of oracle records) + `benign_rns_ambiguous` shipped so its size is continuously visible. RESIDUAL = the bare-bool `send_to_rns` fix, still gateway code | **Opus** | small, post-roll | STILL waits on the RNS 1.3.8 roll; do not land gateway code mid-soak (wedge watchdog `os._exit(2)` restarts activate it) |
| 3 | ~~`dep_version_drift_strays_blind`~~ | ACCEPTED-PERMANENT 2026-07-19 — see above; scope is deliberate, revisit only on a second installer | — | — | — |
| 4 | ~~`calibration_drift_not_paging`~~ | REMOVED 2026-07-19 — see above | — | — | — |
| 5 | `aredn_configured_source_only` | Role-aware expectation: `fleet_roles.yaml` declares which boxes SHOULD run AREDN; probe fires on declared-but-unconfigured (covers the "config wiped" case today's probe can't see) | **Opus** | ~half session | touches role engine both repos (MA role port exists) |
| 6 | ~~`federation_digest_federator_only`~~ | CLOSED/NARROWED 2026-07-19 — see above; row renamed `federation_mapless_box_unwatched` | — | — | rows 2+8 await the RNS roll; row 9 residual needs firmware/#74 |
| 7 | ~~`live_claw_nats_not_wired_to_mini`~~ | NARROWED 2026-07-19 — see above; row renamed `claw_edge_rf_coverage_partial`. Residual = per-device sensor instances (claw-02), which feeds row 9 | — | — | NEXT DEFAULT PICK is row 9's claw-ears leg (or rows 2/8 post-roll) |
| 8 | ~~`cross_gateway_dups_unsuppressed`~~ | **ACCEPTED-PERMANENT 2026-07-19 (operator): keep the detector, do NOT build coordination.** Reason = COST ASYMMETRY (a dup is redundancy; a yield-protocol bug is SILENCE — on emergency comms, fail toward redundancy), NOT a low rate: 3 HUMAN recipients are dual-homed TODAY, so the precondition is live and time WILL move the rate. Residual/leading indicator: dual-homed-recipient COUNT, observable even on the mesh leg. See the addendum in `.claude/research/cross_gateway_dup_design_inputs_2026_07_19.md` | — | — | next slice = surface `dual_homed_recipients` (map layer, NOT roll-blocked) |
| 9 | ~~`mesh_rf_ota_leg_unwatched`~~ | NARROWED 2026-07-19 — claw OTA witness shipped (escalate-only, provisional threshold); renamed `mesh_rf_ota_egress_unproven`. Residual = true egress proof (per-source counters or ACK consumption) | **frontier + operator/field** | — | remaining half needs firmware or #74 T2 step 4 |

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

## Row 8 — inputs gathered 2026-07-19 (NOT the design pass)

Opus-tier contribution ahead of the frontier pass, since rows 3/7/9 all had
stale premises: **0 human cross-gateway duplicates have EVER been observed**
(exactly one `gateway_dup_degraded` fire in the durable history — 2026-06-29,
recipient `…58…` = the MeshAnchor infra hash, pre-dating the infra/human
classifier that landed 06-30 and cleared it). Detector is fully covered right
now (`status ok`, 2/2 contributing gateways, `uncovered []`, 196 confirmed
pairs, 0 dup pairs of either kind), so that zero is a real observation, not an
`indeterminate`. **Bound: `unconfirmable_sent: 14117`** — the mesh leg sends
without confirmation, so dups there are structurally undetectable; the mesh
half is UNOBSERVABLE, not zero, and closing it is the same ACK-consumption
dependency as row 9's residual (#74 T2 step 4).
Implication for the pass: row 8 may be a row-3 (accept + keep the honest
detector) rather than a build. Distributed coordination adds race windows,
split-brain and idempotency surface to suppress a fault not yet observed to
reach a human. **Decide build-vs-accept first; do not open with a design.**
Full inputs: `.claude/research/cross_gateway_dup_design_inputs_2026_07_19.md`.

## Row 2 — DESIGN + CALL-SITE AUDIT DONE 2026-07-19 (execute next session)

Roll constraint is **CLEARED** (RNS 1.3.8 fleet-wide 2026-07-19). Deliberately
NOT executed the same night as the roll: new gateway code on a 2-hour-old RNS
substrate would confound attribution if delivery wobbled. Everything below is
read-only analysis, so the next session opens on execution, not re-derivation.

### The defect, precisely

`BridgeSendMixin.send_to_rns` (`src/gateway/bridge_send_mixin.py:24`) collapses
**five** distinct outcomes into one bare `False`:

| # | return point | true meaning | classification |
|---|---|---|---|
| 1 | `not self._connected_rns` | RNS never came up | infrastructure |
| 2 | `self._lxmf_source is None` | partial RNS init | infrastructure |
| 3 | `not self.can_send_to(...)` | circuit open (deliberate breaker) | benign |
| 4 | `not RNS.Transport.has_path(...)` | no path — unannounced/ephemeral peer | **benign** |
| 5 | `except Exception` | a real send crash | **send_error — the one that matters** |
| — | broadcast with no destination | unsupported by config | benign/config |

The consumer that cares is the mesh oracle's RNS leg
(`src/gateway/bridge_rns_events_mixin.py:53`), which does
`bool(self.send_to_rns(...))`. A genuine RNS exception therefore lands in the
oracle's *benign* bucket, so `oracle_delivery_degraded` under-counts real
failures. `benign_rns_ambiguous` (shipped 2026-07-19) SIZES that blind spot
(~1% of oracle records) but cannot close it.

### The cure — a bool-compatible result (drop-in safe, VERIFIED)

Return a small result object implementing `__bool__` so truthiness is
unchanged, plus a `.reason` the oracle can classify on:

```python
@dataclass(frozen=True)
class RnsSendResult:
    ok: bool
    reason: str = ""     # "" | no_path | circuit_open | not_connected
                         # | no_lxmf_source | broadcast_unsupported | send_error
    detail: str = ""     # exception text for send_error, else ""
    def __bool__(self) -> bool: return self.ok
```

**Every bridge-layer call site was audited tonight — all are bool-context or an
explicit `bool()`, so the change is ADDITIVE, not a breaking contract change:**

| site | shape | safe? |
|---|---|---|
| `_rns_bridge_xform.py:269` | `if dest_bytes and self.send_to_rns(...)` | ✅ bool ctx |
| `_rns_bridge_xform.py:325` | `if self.send_to_rns(...)` | ✅ bool ctx |
| `_rns_bridge_xform.py:1173` | `ok = ...` then `if ok:` | ✅ bool ctx |
| `bridge_ack_mixin.py:192` | `return bool(...)` | ✅ explicit |
| `bridge_send_mixin.py:374` | `"direct" if ... else None` | ✅ bool ctx |
| `meshcore_bridge_mixin.py:106` | `sum(1 for dh in dests if ...)` | ✅ truthiness |
| `bridge_rns_events_mixin.py:53` | `return bool(...)` | ✅ — **and this is the site to CHANGE** |
| `commands/gateway.py:453` | `success = ...` then `if success:` | ✅ bool ctx |

⚠️ `commands/messaging.py:339` is NOT a bridge call — it targets the
**commands-layer** `send_to_rns` (`src/commands/gateway.py:430`), which already
returns a rich `CommandResult`. Do not confuse the two layers.

### MeshAnchor: do NOT port in the same session (evidence-based)

MA's `bridge_send_mixin` is DIVERGED (untracked tier), and the divergence is
material — measured tonight:

- `bounded_call`: **MF 11 occurrences, MA 0**; `_on_wedge`: **MF 17, MA 0** —
  MA's copy has none of the #57/#74 wedge-bounding machinery, so its return
  points are not the same set.
- **MA has no `bridge_rns_events_mixin.py` at all** — no oracle leg, therefore
  **no consumer that could use the richer result**.

Porting would be speculative churn on diverged code with zero payoff. Correct
move: land in MF, and RECORD the divergence deliberately in the twin map rather
than leaving it to look like drift. Revisit only if MA grows an oracle.

### Execution sequence (next session)

1. Add `RnsSendResult` + return it from all 6 `send_to_rns` return points.
   Leave `_queue_send_rns` alone (separate contract, queue classifies by raise).
2. Red-test-first: a test asserting the oracle classifies a raised send as
   `send_error` and a no-path as benign — it must FAIL before step 3.
3. Change the oracle leg (`bridge_rns_events_mixin.py:53`) to classify on
   `.reason` instead of `bool()`.
4. Verify `benign_rns_ambiguous` shrinks toward 0 in the probe's accounting and
   the blended bucket splits correctly.
5. Suites + lint + `parity_check` + CI. **Re-run pytest AFTER the final edit**
   (standing burn-down warning).
6. Deploy: `fleet_pull` then **restart `meshforge-gateway` on moc** (the only
   gateway box) — code on disk is not code running (#79).
7. Live-verify at the consumer of record: watch the oracle audit log for a real
   RNS send and confirm the new reason field appears; `honest_status.sh` green.

**Rollback**: single-commit revert + gateway restart. The result type is
additive, so a revert cannot strand a caller.

### Narrowing text when it lands

Row 2 becomes CLOSED for the RNS leg; the residual note should then read that
`oracle_delivery_degraded`'s failure set covers all four transports, with the
mesh leg's own confirmability still bounded by #74 T2 step 4 (row 9's
dependency), not by this row.

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
