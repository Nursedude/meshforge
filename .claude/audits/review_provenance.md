# Review provenance ledger — which commits have been through a review pass

> **Why this file exists**: scoping "what's unreviewed" cost a full
> exploration pass in the 2026-07-05 QA session because review provenance
> lived only in commit bodies. One row per review pass, updated when a
> pass closes. A commit range NOT in this table has had **no** adversarial
> review — that absence is the signal this ledger makes legible.
> Convention: record the RANGE + scope paths + fix commits + where the
> residual/refuted notes live. Never delete rows; supersede them.

| Date | Scope (range + paths) | Mechanism | Fix commits | Residuals / refuted notes |
|------|----------------------|-----------|-------------|---------------------------|
| 2026-07-01 | transport-truth arc (gateway cid/dedup/true-origin files: base_handler, canonical_message, mesh_bridge, mqtt_bridge_handler, rns_bridge, _rns_bridge_xform, meshtastic_handler, config) | /code-review xhigh ×2 (second pass re-reviewed the first's fixes) | `2038a2db`, `a3b93007` | deferred_work ledger `transport-truth-arc` keys `review_fixes`/`review2_fixes_2026_07_01`; PSK-canary loss class, claim/confirm TOCTOU, save()-bakes-defaults, true_origin default decision (soak review 2026-07-07), relay_fields; refuted: seen-on-RF founding trade |
| 2026-07-04 | `474d630a..HEAD` — dudeclaw/oracle arc (src/mini_dudeai/*, launcher_tui/handlers/{mini_dudeai,offline_oracle}.py, claw scripts) | xhigh 10-finder + adversarial verify; fresh-eyes re-review of the fix commit | `9fd98ae0` (13+3), `2b47bb0c` (5) | memory `project_dudeclaw_oracle_review_2026_07_04`; refuted-by-design: `_probe_tier` no-localhost default, sequential tier GETs; residual latents: brief.py witness path, unconditional capability-index check, single-banner ERROR, CADENCE_VERDICT_NAME test-pin |
| 2026-07-05 | `adf4ac0c~1..cd500d8c` — kilo arc K0/K0.1/K1 + W5.1 (src/kilo/*, scripts/claw_metrics_push.py, mini_dudeai/claw_telemetry.py, monitoring/_mqtt_message_decoder.py + mqtt_subscriber.py, utils/db_inventory.py) | xhigh 10-finder + 8 verifier batches + sweep (41 verdicts) | this session's fix commit (see git log `fix: QA session 2026-07-05`) | Deferred with rationale: kilo/status.py move (await probe consumer), matrix totals SQL aggregate, single-pass status scan, packet-id UNIQUE semantics (witness added), full monotonic anchoring (witness added), repo-wide node_num_to_id sweep (~20 copies — follow-up arc), ghost-row migration (retention ages; map restart on moc instead). Kept-by-design: stray-glob fail-loud |
| 2026-07-05 | `2e57672d~1..5901e603` + `c88451f6` — TUI fleet_provision handlers + handler_registry guard | xhigh 7-finder + 3 verifier batches | this session's TUI fix commit | ~24 confirmed fixed (apply-path TOCTOU + warn-visibility + clobber-guard + registry dup-tag/orphan). Deferred: mqtt-leg reply-on-wrong-channel (needs radio query the leg avoids). All pinned by +32 tests |
| 2026-07-05 | `e2de394f~1..511c549b -- src/oracle/ src/gateway/` + `8bfa4f3e` + `47ec5ba5` — gateway RF oracle leg + resourcepath + id-less dedup | xhigh 6-finder + 3 verifier batches (27 distinct, 25 CONFIRMED) | this session's oracle fix commit | Contained correctness+security FIXED (paths.py rpc_key 0600/symlink-guard LIVE; MeshCore DM-or-drop + consume-invariant; PhoneAPI consume flag; is_query 'help' tightening; responder monotonic-cooldown + spoofable-id cap + node_num_to_id + answer_all-in-list + loop-guard pin; snapshot TTL cache; 3rd RNS-configdir writer + widened guard; dead-code + env-docs). DEFERRED → deferred_work `oracle-leg-hardening-arc-2026-07-05` (dedup_key-timestamp SOAK-COUPLED for 07-07 reviewer; _healthy liveness heartbeat; wrong-channel reply; 5x/6x/resolver refactors; 4 documented design tradeoffs). Pinned by +~20 tests |

## Known NEVER-reviewed (as of 2026-07-05)

- Everything predating the 2026-07-01 pass that isn't in a range above
  (the bulk of the codebase predates adversarial review; field soak +
  tests are its evidence base instead).
- `20d0b643` (revert of durable r2m reverse-routing) — a revert, not a
  review target.

## Conventions for future passes

1. When a review pass closes, add its row HERE in the same commit as the
   fixes ("review provenance rides the fix commit").
2. Hand every new pass the residual/refuted pointers of overlapping prior
   rows — re-flagging a documented residual wastes a verification cycle.
3. A fix commit is unreviewed code: re-review it before push
   (feedback_review_your_own_fixes), and record the re-review in the row.
