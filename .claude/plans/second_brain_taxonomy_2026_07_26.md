# Second-brain taxonomy — the design

> **Status**: DESIGN. Answers the Pri-3 row in `.claude/audits/review_provenance.md`,
> using `second_brain_taxonomy_prep_2026_07_26.md` as its measured input (§1 of the
> prep was NOT re-derived, per its instruction).
>
> ⚠️ **TIER — read before trusting §1–§4.** This was written on an **Opus-class**
> session, *not* the rationed tier the row asked for. `model_router --task-kind
> novel_design` returns `frontier (right-sized)`, which is a recommendation about
> the **task kind**; I read it as a statement about the **running session** and
> stamped a frontier pass, which the upshift-witness gate blocked. That is this
> document's own **T4 — a representation read as a verification — committed while
> writing T4.** It is left in rather than quietly corrected, because it is the
> best evidence in here that T4 is not a discipline problem.
>
> Consequence: **§5 is measurement** (live, quoted, re-derivable) and stands on
> its own. **§1–§4 are argument** and are queued for ratification at the rationed
> tier (`review_provenance.md`, Pri-3). Treat them as a strong proposal, not a
> ratified design; §3's claim that R1 re-derived a weaker mini-grace is the one
> most worth an independent eye, since it indicts a commit shipped hours earlier.
>
> **Constraints honored**: output is CONSOLIDATION, not addition; T1's answer is
> not a meta-organ; no detector is ranked by fire count. Organ count at the end
> of this design is **unchanged**; the thing that gets consolidated is a
> *discipline* that had been re-implemented six times.

---

## 1. The axis

The prep grouped organs by **what kind of claim they make**. That axis predicts
*who consumes* an organ. It does not predict *how the organ will lie*, which is
the question a taxonomy has to answer to be worth its own footprint.

Every defect this arc produced — and every one the prep's tensions describe —
lands in one of three fields. So the taxonomy is three questions per organ:

| Field | The question | The incident that proves it belongs |
|---|---|---|
| **UNIT** | In what unit does the phenomenon live, and does the organ measure in *that* unit? | `cron_verdict_stale` debounced in **watchdog ticks** while its phenomenon lives in **cron cycles** (R1, `59d9ef0a`). Same session: an **average** read as a **period**, sending a whole investigation at the router. |
| **CHANNEL** | Can the observation channel fail *independently* of the phenomenon — and when it does, is that a third state or does it collapse into a verdict? | The owrt1 rtun watchdog counted an **unobservable** outer-hop timeout as a **confirmed death**: ~7% false positives for days, and it bounced a healthy tunnel 4×. #74's `confirmation_rate` is the same shape. |
| **FALSIFIER** | What independent consumer exercises the same path — and does it share an ancestor with the organ? | `gen_fleet_hosts --check` read `/etc/hosts` through NSS, i.e. **the file it audits** (`e71ffc65`). R2b below is the same shape one level up: coverage claimed by *adjacency*. |

**Why three and not six.** T2–T6 are not peers of these; they are *instances*.
T3 (layers that agree by construction) is FALSIFIER at architecture scale. T4 (a
rule is not a check) is what happens when a field is answered in prose instead of
in code. T2 and T6 are CHANNEL applied to documents and to scripts respectively.
T5 is the one genuinely open question and it is a scoping question, not a
falsifiability one — see §4.

The practical form: **three fields, recorded where the organ already lives**
(docstring for probes, rule comment for mini). Not a registry file — a registry
would be a meta-organ and would go stale exactly like the docs in T2.

---

## 2. T1 — who scores the scorers? *The score already exists.*

The prep's headline was that the 58 deterministic detectors carry no accuracy
record, against `grep -rn "false_positive|precision|probe_score"` → zero matches.
That grep was looking for the wrong word.

**`utils/watchdog_probe_core.note_disposition` is the score, and all 58 probes
already call it.** Per class, per tick:

```
clean          — probe RAN, the observation succeeded, nothing wrong
inert          — the organ is LEGITIMATELY not present on this box
indeterminate  — the probe could NOT observe
unknown        — nothing noted at all (fail-dark; silence can never read green)
```

`watchdog_runner.build_coverage` emits an entry for **every member of the closed
enum** — a class missing from the map would be indistinguishable from "not
watched" — and `write_state` puts it in `watchdog.json`. Merge is worst-wins, so
a probe covering N subjects reads `clean` only if *every* subject was clean.

### The reframe that makes this the answer

> **Fire count measures the phenomenon. Disposition measures the detector.**

That single distinction dissolves the Pri-1 constraint. We were already emitting
the right metric and looking at the wrong one. A detector that has read
`indeterminate` for three weeks has been **blind for three weeks**, and its
silence is not evidence of health — *regardless of whether the phenomenon
occurred*. That is a precision-adjacent score that never rewards noise and never
punishes a wedge guard for being quiet.

### Live proof of cost, from inside the main spine

Read off `/var/lib/meshforge/watchdog.json` on the manager this session —
**38 clean / 15 inert / 4 indeterminate / 0 unknown** (57 classes, full
adoption). The four that cannot currently see:

| class | reason it reports |
|---|---|
| `kernel_reboot_pending` | `no same-flavor kernel entries readable under modules dir` |
| `delivery_confirmation_stall` | `no confirmable protocol recorded — cannot judge` |
| `mqtt_root_drift` | `declared consumer root unreadable` |
| `rns_shared_instance_unresponsive` | `connect refused; rnsd shutting down or not serving` |

`kernel_reboot_pending` exists to catch a straggler kernel. It cannot read the
modules dir, so it has been silent — and silence from that probe is *indis-
tinguishable at every consumer* from "no reboot pending." **How long has it been
blind? Nothing on this fleet can answer that**, because the coverage block is
overwritten every tick and `/fleet` renders only *now*. This is a better proof
than the rtun watchdog: it is inside the watchdog spine, on the manager, today.

### The change — one rule, zero new machinery

Not a coverage historian; that would be the meta-organ the constraint forbids.

**mini already ingests `watchdog.json` via its `json_file` source, already
retains `mini_dudeai_history.jsonl` with rotation, and — the part that makes
this free — already implements exactly the right debounce**: `engine.py`'s
grace streak (`grace_s`, `_grace_min_ticks`, `pending_ticks`) counts **OBSERVED
ticks**, resets a broken streak so a transient can never accumulate, and treats
daemon downtime as *not observation* (the #80 observed-tick lesson, already
shipped).

So the whole of T1 is **one mini rule** matching a coverage entry whose `disp`
is `indeterminate`/`unknown`, with `grace_s` on the order of days, action
`file_annotate` (observation-only, MF021-safe). No new file, no new daemon, no
new probe, no new enum member.

### Pri-2 update (same day) — answered YES, and it changes T1's shape

`SignalTracker.update` diffed on signal presence alone and never saw the
disposition map, so **any probe going blind emitted a false CLEARED**. Fixed:
only a positive observation may clear; an unobserved class HOLDS and is
re-emitted carrying `extra.unobserved_hold`.

That does **not** make T1 redundant — the two cover disjoint populations:

| | a blind probe that WAS signalling | a blind probe that was NOT signalling |
|---|---|---|
| **Pri-2 hold** | covered — signal persists, no false recovery | nothing to hold |
| **T1 rule** | (already visible) | **only this** — e.g. `kernel_reboot_pending`, blind and silent, has never fired |

So T1's rule keeps its whole reason to exist, and its scope sharpens: it is the
guard for **blind-and-quiet**, which is precisely the case where silence is
indistinguishable from health at every consumer.

⚠️ Two honest limits to carry into implementation: `inert` must NOT be scored
(a legitimately-absent organ is not a blind one), and the rule reads a *local*
coverage block — fleet-wide blindness is `fleet_truth.merge_coverage`'s existing
fan-out, which already carries the per-box maps.

---

## 3. The consolidation — six implementations of one discipline

The T6 sweep (§5) was expected to find duplicated *detectors*. It found
something better, and my duplication hypothesis was **refuted on reading the
source**: `cron_verdict_freshness.sh` covers *missing* verdicts, which #78 by
design ignores; `rnsd_owner_check.sh` is a nightly fleet sweep where
`check_rns_listener_owner` is a per-client-start preflight. Different units,
different scopes. Not duplicates.

What *is* duplicated is the discipline underneath them. Six independent
implementations of **"debounce a condition, confirm it, re-alert on a cadence,
and witness the delivery"**:

| # | Organ | Its own mechanism |
|---|---|---|
| 1 | `probe_*` (watchdog) | 2-tick debounce, per-probe |
| 2 | `probe_cron_verdict_stale` | cadence-aware 2-run confirmation (R1, `59d9ef0a`) |
| 3 | mini `engine.py` | `grace_s` + **observed-tick** streak + downtime-aware reset |
| 4 | `cron_verdict_freshness.sh` | `REALERT_S=21600` + `~/.cron_freshness_state` |
| 5 | `fleet_offline_check.sh` | `REALERT_INTERVAL` 1h + `~/fleet_push_witness.log` |
| 6 | `rtun_watchdog.sh` (owrt1) | consecutive-failure counter — **the one that was wrong** |

This is `honest_failure_modes` #5 — *two consumers of one artifact share ONE
constant; independent hardcodes WILL drift* — at **organ scale** rather than
constant scale. And it is not hypothetical: #6 counted unobservables as deaths,
#2 counted in the wrong unit, and both were fixed *this arc, separately*, each
without noticing the other four.

### ⚠️ CORRECTION 2026-07-25 — the original conclusion here was WRONG

The first version of this section said *"Row 3 is the reference implementation
… R1 spent a commit re-deriving a weaker version of what mini already had
correct,"* and proposed converging rows 1, 2, 4, 5, 6 onto mini's semantics.
**That is wrong, and it was the claim this document had flagged as most
deserving an outside eye. It was settled by reading the two implementations,
not by judgment** — so it never needed the rationed tier at all.

Compare them:

| | R1 (`watchdog_probes_liveness.py:273`) | mini (`engine.py` grace) |
|---|---|---|
| counts | **cron RUNS**, from the verdict log | **observed mini TICKS**, in memory |
| state lives | on disk (durable across restarts) | in the process (reset on restart, deliberately) |
| streak break | a passing run breaks it (`prev` check) | `_reset_pending_streaks()` + break-resets |

R1 counts in **cron runs — the unit its phenomenon actually lives in.** That
was R1's entire insight, and it is this document's own UNIT axis applied
correctly. Converging R1 onto mini's tick-based grace would **reintroduce the
exact wrong-unit defect R1 fixed.** On durability R1 is arguably *stronger*:
its evidence is a log on disk, so it cannot lose a streak to a restart, which
is precisely the case mini has to defend against in memory.

**What survives:** the inventory of six independent implementations, and that
they can drift (honest_failure_modes #5 at organ scale) — both factual.

**What replaces the conclusion:**

> **The consolidation is NOT "converge on one implementation" — it is that each
> debouncer must DECLARE the unit its phenomenon lives in, and be checked
> against it.** Sharing code across cron runs, mini ticks, watchdog ticks and
> RF cycles would force a single wrong unit on four different phenomena. What
> should be shared is the DISCIPLINE — name the unit, tri-state the channel,
> reset the streak — not the code. **R1 is the exemplar of doing this right,
> not the counter-example.**

That is a better answer than the one it replaces, and it is the same conclusion
the UNIT axis predicts — which is mild evidence *for* the axis. The original
error was mine: I ranked six mechanisms on mini's axis instead of asking what
each one's phenomenon was measured in, one paragraph after arguing that is the
question that matters.

This is the answer to *"find the genius in simplicity."* The alerting burden was
never too many detectors — the deletion pass already proved that by finding zero
deletion candidates. It was one discipline, implemented six times, wrong twice.

---

## 4. The remaining tensions, answered

**T2 — always-loaded docs have a size guard and no staleness guard.** MF012
fails `persistent_issues.md` at 40,000 chars; nothing fails when a claim in it
goes false. It read "NOT FLEET-ROLLED / do NOT bump the SSOT / moc3 IS THE
CANARY" for six days after the roll and **cost a real decision** (the 07-25
hosts roll skipped moc3 for a soak that was over).
**Answer, in the axis**: this is a CHANNEL failure — the doc's claims have no
observation channel at all. The fix is not a doc-linter (meta-organ); it is that
a claim which is *mechanically checkable* must be written as a check, not as
prose. "Do NOT bump the SSOT" is verifiable against the SSOT. **Convention: a
prose claim about live state carries the command that would falsify it**, the
way the persistent_issues entries already carry "Quick check:" lines. Several
already do. Make it the rule, and the staleness guard is the reader.

**T3 — layers that agree by construction.** registry → m1 static entries →
`/etc/hosts` all descend from one operator-maintained source; the drift check
compared two descendants. **Answer**: this is the FALSIFIER field, and it now
has a one-line test — *does the checker share an ancestor with the checked?* If
yes, it can only detect transcription errors, never reality drift. Only
reachability-by-name tests reality. Worth a sweep for other sibling-comparisons;
`parity_check.py` is the obvious next candidate to examine (it compares two
repos that a single session updates in lockstep).

**T4 — a rule is not a check.** `feedback_verify_the_verification` was in
context for the entire prior session and the same error was committed twice.
**Answer**: this is not a discipline problem to be solved with more prose — that
is the disease. It is the argument for why the three fields belong in
*docstrings adjacent to the code* and, where cheap, in *tests*. It also
generalises past me: mini's rules are prose-shaped too, which is precisely why
row 3's grace semantics being *code* is what made them the correct ones.

**T5 — who owns "while you're away"?** Genuinely open; the only tension not
resolved by the axis. Recorded finding, not an answer: of ~50 signal classes,
**one** watches for an available-but-unadopted capability; everything else waits
to be told. That asymmetry is a category. Not designed here — it is the only
part of the Pri-3 row that would require *adding*, and the constraint says
consolidate. Leave queued.

**T6 — unversioned organs on the boxes they guard.** Measured, §5. Answered:
version them — but see the MF014 hazard, which is why this session did not.

---

## 5. What was measured this session

All figures re-derived live, quoted from the runs.

**R5 — `tracer_peer_unreachable`: fixed, not blind. VERIFIED.** The queue row
flagged it silent since 07-10 and asked which. The tracer is alive and the
condition genuinely stopped:
```
retained fires: 1015  (2026-07-19 → now, 10-min cadence, newest 3 min old)
result rows:    8831 "ok"  ·  0 non-ok
latest fire:    9/9 peers ok, incl. moc5 — the only subject it ever fired on
```

**R2b — the dude-claw engine's seed drift is watched by nothing. CONFIRMED,
and sharper than queued; current drift zero.** moc2 declares role `collector`,
which `_ROLE_TO_MINI_SEED` maps to the `fleet_gateway` **mini** seed. The probe
therefore reads `~/mini_dudeai_rules.json` and reports `clean` — while
`~/mini_dudeai_claw_rules.json` and `~/mini_dudeai_claw_rules.dudeclaw-02.json`,
seeded from `configs/mini_dudeai_rules.claw.json`, are read by nothing. There is
no `claw` entry in the role map at all, so the probe is not even *inert with a
witness* on that engine — it is unaware of it, and a same-named detector nearby
reading `clean` makes the gap invisible. **This is coverage claimed by adjacency
— the FALSIFIER field.**
Measured drift *today* — none:
```
seed (repo, claw): 5 ids
claw-01 live:      7 ids  = all 5 seed + 2 box-local (legitimate, one-directional check)
claw-02 live:      5 ids  = exact seed match
```
So: real structural gap, zero current harm. Do not overstate it as a live defect.

**T6 sweep — 11 unversioned organs on the manager, 4 more on the fleet.**
Cron-invoked scripts outside `/opt/meshforge`, all confirmed absent from the
repo (`find` → UNVERSIONED, 11/11):
```
manager (VolcanoAI): backup_rotate · cron_verdict_freshness · dualinject_watch
                     fleet_offline_check · memory_git_sync · memory_health_cron
                     mini_honest_fire · power_capture · rnsd_owner_check
                     scout_draw_cron · soak_cron
fleet:  power_capture.sh on moc/moc1/moc2/moc3/moc5 — md5 IDENTICAL on all 5
        mesh_client_positioned_watch.sh (moc1) · claw_ble_soak.sh (moc2)
```
Two observations worth more than the list:
- **The manager has hard-reset 8 times** (`project_volcanoai_hard_reset_2026_05_28`).
  Eleven organs exist only there — including `backup_rotate.sh` and
  `memory_git_sync.sh`, the two you would need *after* losing the box.
- `mini_honest_fire.sh` is an **independent, fail-closed auditor of mini** that
  refuses to trust mini's self-report. That is the T1 falsifier pattern, already
  built, living in an untracked file. It is the most valuable script in the list
  and the least durable.

⚠️ **Do not bulk-commit these.** They carry operator-specific values —
`NTFY_TOPIC="mf-fleet-…"`, `~/` paths, literal box names — i.e. exactly the
MF014 / `TestOperatorValueContract` class the 07-23 audit sanitized repo-wide.
Versioning them is a templating job (`templates/`, values injected), not a `git
add`. Scoped, gated, not done here.

---

## 6. Ledger

| | before | after |
|---|---|---|
| organs | 8 families | **8 families** — nothing added, nothing deleted |
| new files created by this design | — | **0** (one rule inside an existing engine) |
| new probes / signal classes | — | **0** |
| behaviours consolidated | 6 debounce implementations | 1 named contract, 6 call sites |

## 7. What this design does NOT do

- Does not implement the T1 rule — designed, not built; it is a live-alerting
  change and belongs behind operator sign-off.
- Does not version the T6 scripts — MF014 templating job, scoped above.
- Does not close R2b — the fix (a `claw` entry in the role map, or an explicit
  `inert` witness naming the claw engine) is small, but it changes probe
  semantics and drift is currently zero; no urgency, no blind ship.
- Does not answer T5.
- **Every claim in §5 is VERIFIED with quoted output; every claim in §2–§4 is a
  DESIGN ARGUMENT, not a verified behaviour.** The T1 rule has not been written,
  so its cost and correctness are BELIEVED.

Related: `.claude/plans/second_brain_taxonomy_prep_2026_07_26.md` (the measured
input), `.claude/plans/signal_yield_deletion_pass_2026_07_26.md` (the Pri-1 pass
whose per-signal reasoning is this design's evidence),
`.claude/rules/honest_failure_modes.md` (#5 is §3's whole argument),
`.claude/rules/calibrated_claims.md` (#7 is the FALSIFIER field).
