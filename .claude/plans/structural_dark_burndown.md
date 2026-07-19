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
| 3 | `dep_version_drift_strays_blind` residual | Either extend `_DEP_VERSION_WATCHED` + coherence to other critical deps (paho, folium…) or formally accept-as-permanent with a dated note | **Haiku/fast** sweep; accept-decision is operator's | small | low value — don't spend frontier on it |
| 4 | ~~`calibration_drift_not_paging`~~ | REMOVED 2026-07-19 — see above | — | — | — |
| 5 | `aredn_configured_source_only` | Role-aware expectation: `fleet_roles.yaml` declares which boxes SHOULD run AREDN; probe fires on declared-but-unconfigured (covers the "config wiped" case today's probe can't see) | **Opus** | ~half session | touches role engine both repos (MA role port exists) |
| 6 | `federation_digest_federator_only` | POLICY RATIFIED 2026-07-19 (frontier beat, this session) — see "Row 6 policy" below. Implementation is Opus-tier, spec-complete. | impl: **Opus** (fresh session) | small-mod | none — READY TO IMPLEMENT |
| 7 | `live_claw_nats_not_wired_to_mini` | Wire `nats_sensor`/`http_json` source kinds into the fleet preset on the brain box; MF021 observation-only invariant applies | **Opus** | moderate | claw NATS reachability from mini's context unverified |
| 8 | `cross_gateway_dups_unsuppressed` | STEP 6 cross-gateway suppression: distributed coordination (which gateway yields, race windows, idempotency, partition behavior) | **frontier design pass** → Opus impl | full session+ | hardest row; design doc first, never straight to code |
| 9 | `mesh_rf_ota_leg_unwatched` | RF-side receipt: mesh ACK consumption (#74 T2 step 4) or a second receiver node as OTA witness (reference-node arc fit) | **frontier + operator/field** | multi-session | hardware/field-gated |

## Row 6 policy — RATIFIED 2026-07-19 (implement in a fresh Opus session)

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

**Opus implementation checklist:**
(a) flip `MINI_DUDEAI_ENABLE_FEDERATION=1` (digest stays 0) in each
    map-running box's mini user unit + `systemctl --user daemon-reload` +
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
