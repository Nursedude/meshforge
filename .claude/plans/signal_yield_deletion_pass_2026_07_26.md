# SIGNAL-YIELD DELETION PASS — results

> Queued as Pri-1 2026-07-25 (*"ai bloat and harness is causing a high failure
> rate… find the 'genius' in simplicity"*), run 2026-07-26 at operator request.
>
> **Tier honesty**: the row itself splits this — *"the counting is mechanical;
> the DECISION is not."* The measurement, the provenance reading, and the
> root-causing below were done on an Opus-class session. **The one
> detection-weakening change this pass recommends is NOT applied** — it is
> written up for sign-off, because delaying a guard is the unrecoverable
> direction and that judgment is what the row reserves for a frontier pass.
>
> Method pinned by the row: map `rule_id` → `match.class` from both seeds,
> count `transition == "edge_up"` per class in `~/mini_dudeai_history.jsonl`,
> bucket. Re-derivation script was deliberately NOT shipped (rule #4, no
> machinery to watch the machinery) — rebuild from the row on demand.

## The headline: **this pass deletes nothing.**

The 59% "noise" is not dead weight. It is **three true-positive detectors
reporting one real, recurring, self-healing process gap**, plus a fourth the
original row missed. The volume is a *debounce-unit bug* and a *deploy-process
gap* — not a case for retiring a fault detector.

---

## 1. Re-derived measurement (2026-07-26, window 2026-05-28 → 2026-07-25)

Structure reproduces the 07-25 row exactly:

```
declared classes    57      routed by >=1 seed  57
UNROUTED             0      dead rules           0
fired (lower bound) 20      routed, never fired 37
total escalations  166      (history rotates -> LOWER BOUND)
```

The closed-enum discipline is working. **Do not "fix" it.**

| fires | share | cum | class | window |
|---|---|---|---|---|
| 50 | 30.1% | 30.1% | `cron_verdict_stale` | 06-09 → 07-25 |
| 25 | 15.1% | 45.2% | `rules_seed_drift` | 06-09 → 07-24 |
| 24 | 14.5% | 59.6% | **`tracer_peer_unreachable`** | 05-31 → **07-10** |
| 24 | 14.5% | 74.1% | `parity_drift` | 06-01 → 07-24 |
| 15 | 9.0% | 83.1% | `ntfy_loopback` | 06-18 → 07-18 |

## 2. Three corrections to the 07-25 row

**(a) The trio missed a fourth.** `tracer_peer_unreachable` is the
**3rd-largest** source at 24 fires / 14.5% and is **not deploy lag** — it has
6 distinct subjects (moc1, meshanchor-server, moc5, moc2, moc, moc3). It has
also been **silent since 2026-07-10**, so whatever caused it appears fixed.
Any framing built on "the three deploy-lag classes" understates the top of the
distribution.

**(b) The denominator is partial.** 68 further escalations come from
**box-local rules absent from every seed** (`moc3_federation_backoff_known_normal`,
`source_error_federator`, `federation_peer_unhealthy_unexpected`,
`unexpected_reboot_alert`, `unexpected_reboot_annotate`,
`test_send_retry_drill_81`). The row's method maps seed rules only, so these
are invisible to it. Against **all** escalation volume (234) the deploy-lag
trio is **~42%**, not 59%. Both figures are defensible — they answer different
questions — but the 59% must be stated as *of seed-routed escalations*.
⚠️ Some of those 68 are `annotate`-only rules, which are not alert volume at
all; a future cut should separate `ntfy` from `file_annotate` actions.

**(c) These are not 99 alerts — they are 3 conditions flapping.** Each of the
top three collapses to a **single subject**:

```
cron_verdict_stale  50x subject "cron"
rules_seed_drift    25x subject "mini-dudeai"
parity_drift        24x subject "meshforge<->meshanchor"
```

Transitions are perfectly balanced (**50up/50down, 25/25, 24/24**) with
durations of 5 min to 4 h. So every fire genuinely clears — these are real
transients, **not** the false-CLEARED bug the Pri-2 worklist row suspects.
(That row's specific 07-24T18:22:37 `rules_seed_drift` clear *is* in this data;
this pass finds no basis to generalise it, and Pri-2 should still run on its
own evidence.)

## 3. Root cause of the single biggest source — a unit mismatch

`probe_cron_verdict_stale` (`watchdog_probes_liveness.py:127`) takes
`debounce_ticks: int = 2`, documented as riding "a mid-run window where a fresh
run hasn't recorded yet". **But a tick is a watchdog tick (~60 s), while the
phenomenon it must ride out is one *cron cycle* — up to hours.**

So a single transient cron failure holds the signal for the entire gap until
that cron's next run clears it. Today's live example: `fleet_hosts_drift`
FAILed once at 22:20 (a deliberate corruption drill), passed at 22:47 and every
run since — one transient, one full edge_up/edge_down cycle.

**That is 30% of all escalation volume produced by a debounce measured in the
wrong unit.**

## 4. Per-class judgment

### The "fired exactly once" list — **zero deletion candidates**

The row asked for a "did this earn its place" list. Here it is, and every entry
earned it — each is incident-born and each caught something no other class
would have:

| class | what its one fire caught |
|---|---|
| `calibration_drift` | a VERIFIED completion claim of mine that did not hold on re-derivation — the spine turned on the assistant |
| `rns_stray_env_drift` | the missed-venv half of the roll hazard (a pipx venv silently on stock 1.1.4) |
| `user_timer_unit_failing` | kiai's tracer timer failing on **every** firing for a week, invisible to every other leg |
| `meshtasticd_phoneapi_wedge` | the 06-13→15 moc incident (PhoneAPI contention, bot output stopped reaching nodes) |
| `role_drift` | live unit state diverged from the box's declared role |
| `gateway_dup_degraded` | the one-ever cross-gateway dup — the evidence base for Row 8's BUILD-vs-ACCEPT |
| `gateway_dual_homed_exposure` | the row-8 leading indicator (precondition, not duplicate) |

**Firing once is what a good guard for a rare failure does.** Fire count is a
representation of value, not value.

### The 37 never-fired — **no change**

Already established by the 07-25 row and re-confirmed: dominated by wedge
guards whose incidents were fixed **at source** (`rns_rpc_unresponsive`,
`main_thread_wedge`, `fd_exhaustion`, `phoneapi_tcp_leak`,
`http_local_unresponsive`). Zero fires there **is the fix working**.

## 5. Recommendations

**R1 status — DONE 2026-07-26 (operator-directed), cadence-aware variant.**
Shipped the design flagged as "probably right" rather than the naive gate.
`CRON_VERDICT_CONFIRM_MAX_CADENCE_S = 3600` — a FAIL/CONCERN on a cron whose
cadence is **at or below hourly** must be seen on TWO consecutive runs;
anything slower (and `@reboot`, which resolves to inf) fires on sight. So the
worst-case added delay is **one hour, not one day** — the unbounded tail that
made this need sign-off is gone by construction.

Confirmation is read from the verdict log itself (`_prior_verdict_statuses`),
so there is **no new state file** — the history was already on disk. That
needed a local one-step parser: the shared `_parse_cron_verdicts` collapses to
latest-per-name by contract, so the prior run is invisible through it, and
widening that contract would have touched every other consumer.

⚠️ **An unconfirmed failure is NOT clean.** It notes `indeterminate` (the
worst disposition rank, so worst-wins keeps it visible) with the cron named in
the reason, and it does **not** clear the streak — the tick produced no verdict
either way, so prior state holds. Suppressing the *signal* must never suppress
the *observation* (honest_failure_modes #1/#2).

Grounded in the live fleet's only two real failures, which happen to sit on
opposite sides of the threshold: `fleet_hosts_drift` (hourly) failed once and
self-healed next run — now suppressed; `local_brain_eval` (`25 3 * * 0`,
WEEKLY, persistently failing) — fires on sight. Tests: the pre-existing
core-guarantee test now asserts a CONFIRMED failure still fires, plus three
new legs (withheld-but-not-clean, slow-cron-on-sight, @reboot-on-sight).
Red-checked: with the probe stashed the withheld test fails while the two
unchanged legs stay green.

**R1 (original writeup) — Fix the debounce unit, not the detector.**
Gate `cron_verdict_stale`'s FAIL/CONCERN leg on **two consecutive verdicts for
the same cron name**, rather than two watchdog ticks. Same shape as the rtun
watchdog fix landed today. Expected effect: removes most of 30% of escalation
volume with **no loss of fault detection** — a cron that fails once and passes
next run is a transient; twice in a row is real.
⚠️ **The cost, stated plainly**: it delays detection of a genuinely broken cron
by one of *that cron's* cycles — an hour for `fleet_hosts_drift`, but **a full
day for a daily cron**. That asymmetry is why this is not applied here. A
cadence-aware variant (consecutive-gate only below some cadence, or fire
`degraded` immediately and escalate on the second) is probably the right
design, and designing it is frontier work.
⚠️ **Scope guard**: the FAIL/CONCERN leg ONLY. The *silence* leg already gates
on ~3× schedule cadence, and post-2026-07-10 a `silent(never)` page is real
(Issue #78's log-truncation defect was fixed in `d0254dae`).

**R2 — Fix the deploy process, not `rules_seed_drift`. SAFE.**
The signal fires after every seed bump until each box runs
`promote_seed_rules.py --apply` by hand. Fold seed promotion into the deploy
path and this class stops firing *by construction* — a process fix that
retires ~15% of volume without touching a detector.

**R2 status — DONE 2026-07-26.** Folded into all three deploy legs
(`fleet_sync.sh` `promote_seed` remote + `promote_local_seed` self,
`update.sh`), promote-before-restart, unconditional-but-idempotent, no-role
boxes degrade to a visible NOTE. Pinned by `TestFleetSyncPromotesSeed` (5,
red-checked). The self leg was nearly missed — the same half-wired shape
`fleet_sync.sh` already records from 2026-06-09, and the manager box is where
seeds are authored.

**R2b — the claw engine's seed drift is UNWATCHED. Gap, not yet closed.**
Found while doing R2: `probe_rules_seed_drift` judges only the *fleet* mini
engine. The standalone dude-claw instance is a **second rule engine** with its
own seed (`configs/mini_dudeai_rules.claw.json`) and its own live file
(`~/mini_dudeai_claw_rules.json`, plus per-`@instance` siblings), and **no
probe watches it drift**. So claw seed drift is invisible rather than noisy —
the opposite failure from the one this pass was chartered to fix, and worth
more attention for that reason. Deliberately NOT bundled into R2: promoting a
seed nothing watches is an unverifiable change, and adding the probe is an
*addition*, which this pass is chartered against. Hand it to the taxonomy
session (Pri-3) as a concrete instance of T5 (who owns what) — an organ with
no watcher is exactly the structural-dark shape.

**R3 — `parity_drift` is inherent. NO CHANGE.**
MeshForge is the declared lead repo, so a window where MF has landed and MA
has not is the workflow, not a defect. Durations are 5–24 min. Leave it.

**R4 — Separate `annotate` from `page` before the next yield cut.**
Correction (b) showed the current method mixes them. Alert *volume* is the
`ntfy` subset; annotations are free. This changes what "noise" means.

**R5 — Re-check `tracer_peer_unreachable` before acting on it.**
3rd-largest historically, silent since 07-10. Either it was fixed (likely — the
fleet DNS/hosts work removed a whole class of name-resolution failure) or it
went blind. Confirm which before drawing any conclusion from its quiet.

---

**Bottom line for the frontier session**: the deletion pass's honest output is
that **there is nothing to delete**. The alerting burden is real but its causes
are a mis-united debounce, a manual step missing from deploy, and a
lead-repo workflow — all fixable without removing a single fault detector.
That is the "genius in simplicity" the row asked for, and it argues the
taxonomy session (Pri-3) should be about **scoring and consolidation, not
pruning**.

Related: `.claude/plans/second_brain_taxonomy_prep_2026_07_26.md` (T1 asks how
we would ever know a detector is any good — this pass is the manual answer,
done once, by hand).
