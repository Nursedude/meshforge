# Signal-class yield audit — the subtraction arc

**Date**: 2026-08-08 · **Scope**: all 62 members of `SIGNAL_CLASSES`
(`src/utils/watchdog_probe_core.py`) · **Deliverable**: a DELETE list, per
[[feedback_my_footprint_is_the_constraint]] ("prefer removing to adding").

No new code was written and no auditor was built. Every number below is
re-derived from artifacts that already existed:

| Source | What it gives |
|---|---|
| `SIGNAL_CLASSES` | the 62-member closed enum |
| `~/mini_dudeai_history.jsonl` on **all 8 MeshForge boxes** | every `edge_up` since 2026-05-28 → "has it EVER fired" |
| `/api/fleet/truth` (`coverage.classes`) | live per-class disposition × 8 boxes → "can it even look" |
| `~/mini_dudeai_memory_deltas.jsonl` (186 rows) | proposal ratify/reject + the prose reason |
| `~/morning-watchdog-reports/` (80 daily reports) | independent cross-check on which classes ever appeared |
| `configs/mini_dudeai_rules.*.json` + live rules | confound check: does every class have a rule that *could* fire |

---

## The headline

**The enum is not mostly wrong. It is mostly scoped.** Only a small tail is
genuinely dead weight, and the single test that separates the two was sitting
in the coverage map the whole time:

> **A class that has never fired but reports `clean` is an armed backstop —
> it is proving every tick that it can see. A class that has never fired and
> reports `inert` everywhere has never once been able to look.**
>
> Only the second is dead weight. `clean` and `inert` are not the same silence.

18 of 62 classes have never fired anywhere. **Ten of those are `clean` on 8/8
boxes** — `fd_exhaustion` (#73), `phoneapi_tcp_leak` (#17/#75),
`meshtasticd_vsz_leak`, `delivery_write_canary` (#63), `queue_backlog`,
`foundation_perms_drift`, `history_write_stalled`, `user_unit_inactive`,
`rns_shared_instance_unresponsive`, `rns_instance_name_mismatch`. Those are
working exactly as designed and are **not** removal candidates. `meshtasticd_vsz_leak`
is explicitly threshold-set never to fire while the weekly restart holds the
envelope — silence there is the specification.

---

## DELETE list

### Tier 1 — remove now (2 classes, `inert` on 8/8 boxes)

These have never been observable on any box in the fleet.

| Class | Fires | Why it can never look |
|---|---|---|
| `aredn_organ_undeclared` | 1 (moc1, once) | `inert`×8, and for **two mutually exclusive reasons**: 4 boxes *"no AREDN LAN here"*, 3 boxes *"aredn_node_ips configured — the configured-source legs own this box."* Both the has-it and hasn't-it populations are covered by something else. There is no third state where it fires. |
| `lxmf_propagation_unused` | 2 | Same contradiction: `inert` on boxes with no gateway, and `inert` on the 2 boxes where *"propagation_node configured — capability adopted."* It is a one-time **adoption nudge**, already answered, running in a 30 s loop forever. |

**Cut**: the enum member, its probe, its rule in the role seeds, its tests.

> ⚠️ **`propagation_soak_degraded` was in this tier and it does not belong here.
> The delete recommendation was wrong.** See "The correction" below. It is a
> FIX, not a cut, and it is the most valuable finding in this audit.

### Tier 2 — remove, and say why out loud (2 classes)

| Class | Age / fires | Argument |
|---|---|---|
| `json_uplink_dark` | added **2026-08-07**, 0 fires | Its coverage split is `clean:4 / inert:4` — **byte-for-byte the same split as `channel_feed_dark`**, the detector it sits behind. It carries no information the class below it does not already have. This is the one the operator flagged in his own accounting: *"it watches the instrument behind another detector, which is literally a layer up. The cheaper design was a richer disposition on the existing class."* The measurement agrees with the instinct. |
| `dream_ratification_stalled` | added 2026-07-22, 0 fires in 17 days, observable on 1 box | Machinery watching machinery, by definition: it watches mini's own ratification cadence. The warm brief already prints the ratio on every session start. Nothing is added by a 30 s probe on 9 boxes. |

### Tier 3 — HANDOFF: re-decide before implementing

> ⚠️ **Read this before doing the demotions below.** Written 2026-08-08 at the
> end of the session that executed Tiers 1–2, from things learned *during*
> execution that change the recommendation.
>
> **The cost side is smaller than I assumed.** Each of these three is `inert`
> on 7 of 8 boxes, and an `inert` probe is an early return — a file-existence
> check, not work. The real per-tick cost is on the ONE box where each is
> observable. So "demote to cron" buys very little tick time.
>
> **The cost of demoting is larger than I assumed.** A demotion is not a code
> move; it is a script + a crontab line + `cron_verdict` wiring, **per box**.
> And **crontabs are per-box and NOT repo-tracked** — so this adds three
> hand-maintained entries that only `cron_verdict_stale` can see, on three
> different boxes. That is adding machinery to watch machinery, which is the
> rule this whole arc serves.
>
> **So the honest options are three, not one**, and the next session should
> pick deliberately rather than execute the word "demote":
>   1. **Leave them.** Cheapest. They cost ~nothing on 7 boxes and one file
>      read on the eighth. Revisit if a tick-cost measurement ever indicts them.
>   2. **Delete them**, on the same Tier-1 reasoning — never fired, and the
>      thing they watch is a deploy-timescale fact a human is present for.
>   3. **Demote**, accepting three untracked crontab entries.
>
> My recommendation is **(1) leave `claw_uplink_node_moved`** (10 days old,
> too young to judge), **(2) delete `oracle_delivery_degraded`** (0 fires in
> 47 days; `inert` ×7 because the oracle *never wrote a log* — the organ is
> out of service fleet-wide, so there is nothing to move to a cron), and
> **(1) or (3) for `inherited_app_drift`** on measurement of what it costs on
> moc5, the only box where it can see.
>
> **If you do demote, the cron-verdict wiring has TWO idioms and they must not
> be mixed** (learned the hard way in `9193dd6a`):
>   - **`$?`-mapping**: the crontab runs `<script> ; cron_verdict.sh <name> $?`.
>     `cron_verdict.sh` maps `0 → OK`, nonzero → `FAIL(n)`. **`CONCERN` is
>     unreachable in this idiom** — which is why `fleet_ntfy_loopback.sh` had
>     to exit 0 on a transient rather than emit a CONCERN.
>   - **script-speaks-its-own-verdict**: the script calls `cron_verdict.sh`
>     itself (`say CONCERN "..."`) and the crontab guards with
>     `|| cron_verdict.sh <name> FAIL wrapper_crashed`. `CONCERN` is reachable.
>     ⚠️ The script must then **exit 0 after speaking**, or the wrapper appends
>     a second, contradicting verdict over the real one — and the newest line
>     is what `cron_verdict_stale` and the operator read first.
>     `pytest_tmp_prune.sh` is the reference implementation.
>
> Whichever idiom: a newly-wired cron is only visible to `cron_verdict_stale`
> once it writes a verdict, and that probe judges **only** wired crons.

### Tier 3 — the original recommendation (demote off the 30 s tick)

Real checks, wrong cadence. Move to a daily/6-hourly cron with a
`cron_verdict` wire; keep the finding, drop 2,880 ticks/day/box.

- `oracle_delivery_degraded` — 0 fires in 47 days; `inert`×7 (*"oracle never wrote a log (disabled/never queried)"*). The organ is effectively out of service fleet-wide.
- `inherited_app_drift` — 0 fires in 48 days; observable on **moc5 only**. Checkout drift changes on the timescale of deploys, not seconds.
- `claw_uplink_node_moved` — 0 fires; observable on **moc2 only**. Node-moved is a deploy-timescale fact.

(`claw_rf_silent`, same shape, is 20 days old and belongs to the live claw-RF
arc — **too young to judge**. Re-measure 2026-09.)

### Tier 3 — DECIDED 2026-08-08 (second session): **none of the three moves**

The handoff above asked for a deliberate choice among leave / delete / demote.
Choice made, from live evidence read this session rather than from the
dispositions the audit was written on.

**The live coverage cells** (each box's own `/var/lib/meshforge/watchdog.json`,
all ticks < 35 s old, boxes at `828daf42`, VolcanoAI at `45b52fec`):

| Class | VolcanoAI | moc | moc1 | moc2 | moc3 | moc4 | moc5 | kiai |
|---|---|---|---|---|---|---|---|---|
| `oracle_delivery_degraded` | inert | inert | inert | inert | **indeterminate** | inert | inert | inert |
| `inherited_app_drift` | inert | inert | inert | inert | inert | inert | **clean** | inert |
| `claw_uplink_node_moved` | inert | inert | inert | **clean** | inert | inert | inert | inert |
| `propagation_soak_degraded` | inert | **clean** | inert | inert | **clean** | inert | inert | inert |

**`claw_uplink_node_moved` → LEAVE.** `clean` on moc2 — an armed backstop by
the arc's own KEEP rule, and it reads two plain files (`/proc/net/arp` + a
declaration), no subprocess. There is nothing to buy here.

**`inherited_app_drift` → LEAVE**, and now on a measurement instead of a guess.
On moc5 — the only box where it can see — the probe's real work (scan the
operator home + `/opt`, read each `.git/config` origin, `git status
--porcelain` per inherited checkout) is **4 inherited checkouts, 0.062 s per
tick** (mean of 3, measured in root context on the box itself, using the
probe's own helpers so nothing in the detector spine was touched). That is
~1.4 % of one tick's CPU on one box, ≈3 min of CPU per day. Demoting it costs a
script, a crontab line, and a `cron_verdict` wire — untracked, on one box — to
save that. The trade is not worth it, and it reports `clean`: armed backstop.

**`oracle_delivery_degraded` → DO NOT DELETE. Its delete recommendation rested
on a false premise, and the premise came from reading reason strings again.**

The audit said *"the organ is effectively out of service fleet-wide"*, from
`inert`×7 (*"oracle never wrote a log"*). What the live check found:

- **The oracle is enabled and running on moc3** — `MESHFORGE_ORACLE_ENABLED=1`,
  `MESHFORGE_ORACLE_RNS_ALLOWLIST=*`, `MESHFORGE_ORACLE_PHONEAPI_TAP=1`, in
  `/etc/systemd/system/meshforge-gateway.service.d/10-oracle.conf`, on a gateway
  that is `active`. Not out of service. Enrolled.
- Its audit log holds **106 lifetime queries, every one delivered**, newest
  **19 days old** (Jul 20). Zero in the last 24 h.
- So moc3's cell was not `inert` at all — it was **`indeterminate`,
  permanently**, reason *"confirmable sample below minimum — cannot judge"*.

The 7 `inert` boxes were honest. The error was the generalisation: I read
"never wrote a log" on seven boxes as a fact about the fleet and never asked
the eighth what it was actually saying. Third instance in this arc of the same
move (`propagation_soak_degraded`, then the `unspecified` statistic, now this).

**What was wrong, and what was fixed instead of deleted**: an empty window is
not an unobservable one. The probe read the log successfully; the answer was
"nobody asked". Rendering that as `indeterminate` made the detector read
**blind forever on the only box that can see** — the standing-indeterminate
trap, from the inside. `_read_oracle_window` already computed the distinction
(`total`) and the probe threw it away as `_total`: the collapse was one
discarded variable wide.

Now: `total == 0` → **`inert`**, reason *"oracle enrolled but idle — 0 queries
in the last ~6h; no delivery to rate"*, distinct from the absent-log reason.
⚠️ A **non-empty** window under `min_sample` stays `indeterminate` — an oracle
answering only declines, or only reason-less RNS non-deliveries, IS being
exercised, and the all-benign-RNS shape is precisely the row-2 blind spot;
calling that "nothing to watch" would hide the failure the probe exists to
size. Two tests pin that boundary from both sides.

**And the class, not the instance** (the 08-05 lesson, applied the same day):
grepping every `note_disposition(..., "inert")` whose reason names an
unobservable condition found exactly one more — `claw_uplink_node_moved` said
`inert`, *"operator home unresolvable; no uplink declaration here"*. That is a
claim about the box made from a failure to look. Now `indeterminate`.

~~**Open question left for the operator**: even fixed, this probe cannot yield
on today's traffic — it needs ≥8 confirmable queries inside 6 h, and moc3's
oracle has served 106 queries in its *lifetime*.~~ **ANSWERED by the operator
the same session: do the last-N window with a staleness guard.** Shipped as
v2 below.

### v2 — the count window + staleness guard (2026-08-08)

**The measure changed; the guard is what makes it honest.** v1 rated a 6 h
*time* window and required ≥8 confirmable inside it — a gate the fleet's only
oracle box cannot meet, because moc3 answers ~5 queries a month in bursts
weeks apart. v2 rates **the last 8 confirmable answers, however long ago they
were given**. That yields on the real traffic; it also opens a new way to
lie, since a sample can now span weeks and "the last 8 answers" is not
automatically a statement about *now*. Hence:

- **Freshness gates the verdict.** Only a sample whose newest confirmable
  record is within 24 h may read `clean` or fire. A **stale** sample is
  `inert` with its rate and age in the reason — *"oracle idle — last 8
  answers rate 1.00, newest 19.2d ago (stale > 24h; not paged)"*. The finding
  stays readable in coverage without paging at a 30 s cadence about answers
  given three weeks ago.
- **Firing additionally requires a fresh failure.** Without that, a rate
  dragged below threshold by month-old `send_error`s would page while every
  recent answer landed — the pollution a count window introduces. The guard is
  on **action**, not on the measure: the rate is always reported.
- **Order is FILE order, not `ts` order.** The append sequence is causal; a
  stepped clock on an RTC-less Pi is not (honest_failure_modes #6). `ts` is
  used only for freshness and to clamp forgery — non-numeric, ≤ 0, and
  far-future stamps are still skipped, exactly as the time window did.
- **Small-N now resolves permanently.** "Fewer than 8 confirmable answers
  *ever recorded*" replaces "…inside this window", so the guard stops
  re-arming every 6 h once the oracle has answered 8 times.

**Both guards drilled.** Neutering the staleness branch fails 5 tests;
neutering the fresh-failure branch fails the one that exists for it. A guard
that has never refused anything is not evidence it works.

**Rehearsed on the real artifact before deploying**: moc3's actual
`mesh_oracle_log.jsonl` (106 records, 99 confirmable, all delivered, newest
19.2 d) run through the new code returns no signal and the cell above.
**Prediction for the deploy**: moc3 reads `inert` with that reason; the other
seven are untouched (`inert`, "never wrote a log").

**Result: held 8/8.** Deployed `fdc7acee` to all 8 boxes, watchdog restarted
only where already active, read from each box's own `watchdog.json` on ticks
< 15 s old:

    moc3     inert  ["oracle idle — last 8 answers rate 1.00, newest 19.2d ago
                      (stale > 24h; not paged)"]
    other 7  inert  ["oracle never wrote a log (disabled/never queried)"]

byte-identical to the rehearsal. `bash scripts/honest_status.sh` → **`exit 0`**
(CI `fdc7acee` run 31282157337 success, fleet SHA 8/8, full suite `exit 0`
10433 passed / 1 skipped, lint `exit 0`); the one WARN is the same two
pre-existing legs (`synth_soak_degraded` on meshanchor-server,
`local_brain_regressed` on VolcanoAI).

~~**What is still BELIEVED**: that the *firing* path behaves in production.~~
**DRILLED AND VERIFIED** the same session (`5e63aa19`,
`scripts/oracle_fire_drill.py`), by the check that was named. It runs the
**deployed** probe on moc3 under the watchdog's own uid and interpreter —
`/usr/bin/python3`, `uid 0`, `PYTHONPATH=/opt/meshforge/src`, matching the
unit's `ExecStart` — against the box's **real log content** with synthetic
`send_error` records appended to a **copy**. `RESULT: ALL PASS`, **`exit 0`**,
12 checks:

| Case | Expected | Observed |
|---|---|---|
| baseline (untouched copy) | no signal, `inert` | `inert` — "last 8 answers rate 1.00, newest 19.3d ago (stale > 24h; not paged)" |
| 6 send_errors **3 days** old | staleness guard refuses to page | no signal; `inert` — "rate 0.25 **BELOW** threshold 0.80, newest 3.0d ago" |
| 5 send_errors **30 days** old + 3 fresh deliveries | fresh-failure guard | no signal; **`clean`** — "every send-error is older than 24h — recent answers all landed" |
| 6 send_errors **now** | debounce, then FIRE | tick 1 `indeterminate` (held); tick 2 **Signal**, rate 0.25, `fresh_send_errors: 6`, `debounce_streak: 2` |

The real log was opened read-only and asserted unchanged at the end —
`(25208, 1784565698) -> (25208, 1784565698)`. Synthetic records carry
`from: DRILLSYNTH` so a stray copy can never read as telemetry. On a box with
no oracle the drill SKIPs rather than pretending to test.

**The one gap that remains, named rather than folded in**: the drill runs the
probe in a root process *beside* the daemon, not inside the systemd unit's
sandbox, so the runner's own dispatch of **this class** (tracker → `watchdog.json`
→ the seed's ntfy rule) is still inferred rather than observed. That path is
class-agnostic and other classes ride it daily; closing it for this one would
mean falsifying moc3's live audit log, which is not worth the page.

**The prediction, written before the deploy** (so it can be wrong): after this
lands and each box's watchdog restarts, `oracle_delivery_degraded` reads
**`inert` with a reason containing "idle"** on moc3 and **`inert` with "never
wrote a log"** on the other seven; `claw_uplink_node_moved` is **unchanged
everywhere** (moc2 `clean`, the rest `inert`) because its altered branch is
unreachable while the operator home resolves — which it does on all eight.

**Result: held 8/8.** Deployed `951ae565` to all 8 boxes (`fleet_pull.sh`,
ff-only), restarted `meshforge-watchdog` only where it was already `is-active`
(the 07-24 rule), and read each box's own `watchdog.json` on a tick < 30 s old:

    moc3       oracle=inert  ["oracle enrolled but idle — 0 queries in the ~6h…"]
    other 7    oracle=inert  ["oracle never wrote a log (disabled/never queried)"]
    moc2       claw=clean    ["2 claw uplink node(s) at their declared addr…"]
    moc5       inherited=clean

moc3's permanent `indeterminate` is gone, and the two idle-vs-absent reasons
are distinguishable at a glance. `claw_uplink_node_moved` and
`inherited_app_drift` are byte-for-byte where they were.

---

## The correction — I made the exact error this audit is about

`propagation_soak_degraded` was Tier 1 on this evidence: `inert` on 8/8 boxes,
reason *"no propagation node adopted — nothing to exercise."* I read that
string as a fact about the fleet. **It was the probe lying about itself**, and
the memory that warns about precisely this (*"distrust the probe's explanatory
text — both named culprits they could not see"*) was in context when I wrote it.

What execution turned up, from checking the thing instead of its description:

- The soak timer is **enabled and firing hourly on moc and moc3**.
- On moc3 it publishes healthy envelopes — `pass_envelope: true`, `ok_ratio 1.0`,
  a real `propagation_node`, newest one minutes old.
- Each fire costs **4 min 47 s of CPU** — on the **905 MB box**, hourly, ≈8 % of
  a core continuously.
- And the probe that exists to read those envelopes reported, every 30 s, that
  there was nothing to exercise.

**Root cause**: the intent gate resolved the operator home with
`get_real_user_home()`. Under `User=root` with no `SUDO_USER` and `LOGNAME=root`
that falls back to `/root`, and `/root/.config/meshforge/gateway.json` does not
exist → `absent` → `inert`. Verified on moc3: the operator-UID lookup returns
`/home/wh6gxz`, whose `gateway.json` holds exactly the node the drill uses;
`/root/.config/meshforge/gateway.json` is confirmed absent.

This is the **same root cause as the 2026-08-05 `rns_instance_name_mismatch`
dig** — a root service's `~` is `/root` — and
`watchdog_runner._operator_home_for_root` documents the trap in this very
codebase. Fixed by using the operator-UID resolver the sibling propagation
probes already use.

Two lessons, both already written down and both walked past:

1. *A fix applied to one instance is not applied to the class.* 08-05 fixed the
   RNS probe. Nobody grepped the other `get_real_user_home()` callers in probe
   code. There was exactly one left, and this is it.
2. *A long-running `inert` is a finding, not furniture* — and an audit that
   reads dispositions **must not accept the reason string as evidence**. The
   disposition told me where to look; only the live check told me what was true.

**Open question for the operator, not decided here**: now that the probe can
see, is an hourly 4m47s-CPU drill on the 905 MB box worth its cost? The drill
passes, so the probe will read `clean`. Options: keep, stretch the cadence, or
run it only on moc. That is a cost/coverage call, and it is yours.

## Two dark corners the audit surfaced

### 1. `nomadnet_crashloop` can never report `clean`

It is `inert` on 8/8 with the reason
*"no live restart loop (healthy, remediated, or no nomadnet)."*
That string **collapses healthy with absent** — the exact `honest_failure_modes`
#2 defect, and the same class as the 2026-08-05 and 08-07 findings.

The consequence is precise: this detector's coverage cell is
**indistinguishable from a detector that has been deleted**. It cannot pass
the "armed backstop" test above, not because it is broken, but because it has
no vocabulary to say "I am watching and it is fine."

⚠️ **Do not delete it** — it guards #82, which ran undetected for 10 days
(NRestarts=7842). **Fix the disposition**: split *no nomadnet user unit
enrolled here* (`inert`) from *enrolled, observed, not looping* (`clean`).

**FIXED + DEPLOYED 2026-08-08 (`e25469ff`).** Four dispositions now: `clean`
(enrolled + journal read + no loop), `inert` (not enrolled — nothing to
watch), `indeterminate` (journal unobservable **or** enrollment unreadable),
`degraded`/`wedge` (confirmed loop). The declaration is the
`default.target.wants` symlink, read through the same helper
`probe_user_unit_inactive` uses so the two probes can never disagree about
what is enrolled. Detection is unchanged — the journal read still decides
firing; only the quiet case is refined.

*How it was verified, because this is the part that matters*: the existing 13
tests asserted only `sig is None` — which cannot distinguish four reasons for
returning None, **the same collapse one layer up**, and exactly why the defect
survived. New tests assert the disposition, with `enabled_fn` injected so the
verdict cannot depend on whether the box running the suite has nomadnet
enrolled. Then a **drill**: reverted the probe to the old code → 7 failed;
restored → 31 passed. And a **live prediction made before deploying** —
`clean` on moc/moc1/moc2/moc3/VolcanoAI (enrolled), `inert` on
moc4/moc5/kiai (not) — **held 8/8**, read from each box's own
`watchdog.json` where the relay lagged. Before the fix all 8 read `inert`.

### 2. The "49 rejected `unspecified`" statistic is misleading

The warm brief reports rejections *"by reason: unspecified ×49"*, and the arc
premise reads that as *"a detector whose proposals are always dismissed
without a reason."* Re-derived from the deltas file:

- **149 rejections. 149 of them carry a written `resolved_note`. Zero do not.**
- 107 lack the *structured* `resolved_reason` field; 42 have one
  (`known_benign` 19, `noisy_detector` 9, `duplicate` 7, `already_fixed` 6,
  `not_actionable` 1).

The notes are substantial — most record a live verification done at the time
("HTTP 200/2 ms, federation 0 failures, verified live this session"). The
reasons exist; only the machine-readable field is missing, and the brief
renders that absence as *"unspecified"*, which reads as *"nobody thought about
it."*

**Subtract the misleading stat, not the detectors.** Either backfill
`resolved_reason` at rejection time or stop printing the breakdown.

*Secondary discrepancy, flagged not resolved*: the brief reports **17/97**
ratified; the deltas file itself holds **37 ratified / 149 rejected = 186**
rows, all host `VolcanoAI`. The rollup and its own source disagree by roughly
half. UNKNOWN which is right — worth one look, since this ratio is quoted at
every session start.

### Both stats FIXED 2026-08-09 (`d2fc0c37`) — neither was a broken calculation

Both were **true numbers wearing a wrong label**, which is why neither showed
up as a bug: the arithmetic was right and the sentence was not.

**1. `unspecified ×49` — measured, and the word was the whole defect.** Of 80
rejected keys, 49 carry no structured `resolved_reason` — and **zero of those
49 lack a written `resolved_note`**. Every rejection on this box had a stated
reason; only the machine-readable field was missing, and the brief rendered
that absence as negligence. They now bucket as **`note_only`**, with an inline
line saying what it means, and `unspecified` is reserved for a rejection that
recorded nothing at all — so the word is now earned when it appears, and it
appears when it is earned.

**2. The 17/97-vs-37/186 "discrepancy" was two correct measurements.**
`proposal_track_record` collapses by delta **KEY** (latest status per finding);
the file counts **ROWS** (every judgement ever made). Neither was wrong —
they answer different questions: *how many distinct findings did I get right*
vs *how many proposals did a human have to judge*. The brief now renders both,
labelled, and names the gap for what it is: **38 keys re-proposed** after
`DEFAULT_RESOLVE_SUPPRESS_S` expired and the condition still detected. Churn,
not disagreement. The row line renders only when the two views differ.

Live brief on the manager box after deploy — the consumer of record, not a
fixture:

    ## 🪞 dream-proposal track record — 17/97 distinct findings ratified
    - Ratification ratio over distinct FINDINGS (one per delta key)…
    - 37/186 PROPOSALS judged — 38 finding(s) re-proposed after their
      suppression window expired. The gap is churn, not disagreement.
    - rejected by reason: note_only ×49, known_benign ×13, noisy_detector ×8,
      duplicate ×7, already_fixed ×2, not_actionable ×1
      - `note_only` ×49 = reason WRITTEN in the delta's resolved_note, just
        not in the structured field. Only `unspecified` means nothing was
        recorded.

Measured before and after against the real 186-row ledger, not fixtures. Three
legs drilled (remove the `note_only` split, the row counts, or the brief's row
line — a test goes red for each). Ported to MeshAnchor: `dreams.py` and
`brief.py` are byte-locked twins.

---

## HANDOFF — `synth_soak_degraded` on the MeshAnchor box (written 2026-08-09)

> Every item in this audit is now closed. This is the **next** thread: the last
> standing fleet WARN, and the only `degraded` on the fleet that predates this
> whole arc. **I have not investigated it** — what follows separates what was
> OBSERVED from what is HYPOTHESIS, because the last handoff's recommendation
> rested on unverified reason strings and cost this session a re-derivation.

**OBSERVED** (read from the boxes' own `watchdog.json`, 2026-08-09 ~03:50 UTC):

    meshanchor-server   degraded   "synth soak went DARK: newest result is
                                    1872.2h old (cadence ~1h) — the LXMF
                                    round-trip exerciser stopped producing
                                    output. Check meshforge-synth-soak.timer
                                    (systemd --user) + its fire log."
                        extra: newest = synth-20260523T040140Z.json
                               age_s  = 6,740,001.9   (~78 days)
    moc                 clean      (the soak runs and passes there)
    other 7 boxes       inert      "synth-soak state dir absent — box doesn't run it"

**What that rules OUT.** My first instinct — *"probably a disposition bug, it
should be `inert`"* — **does not survive the fleet view.** This probe already
has a working `inert` leg and uses it on 7 boxes, and it has a live `clean` on
moc proving it can see. meshanchor-server has the state dir *and* a real
artifact; it is not absent-by-design in the probe's terms. So the likeliest
read is the boring one: **the exerciser genuinely stopped on that box on
2026-05-23**, and nothing has produced a soak envelope there since.

**HYPOTHESIS, untested** — the timer is a **`systemd --user`** unit
(`meshforge-synth-soak.timer`), and user units are structurally invisible to
system-level checks (#82). I got caught by exactly that this session hunting
mini-dudeai: `systemctl is-active` said `inactive` on all 8 boxes while the
user units were `active` on all 8.

**First moves, in order:**

1. `XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user status
   meshforge-synth-soak.timer meshforge-synth-soak.service` on
   meshanchor-server. Enabled? Last trigger? `NRestarts`?
2. **What happened on 2026-05-23?** The artifact name dates the stop precisely
   (`synth-20260523T040140Z.json`). Check that box's journal and git history
   around then — a deploy, a role change, a deliberate retirement.
3. **Ask why the sibling was silent.** `probe_user_timer_unit_failing` exists
   and did NOT fire here. Either the timer is *disabled* (not failing — a
   different, correct answer, and then the question becomes who disabled it and
   whether the soak is meant to run on MA at all), or that probe has a gap of
   its own. Both outcomes are worth the ten minutes.
4. Only then decide: restore the organ, or declare it retired on that box and
   make the probe say `inert` **from a declaration**, never by widening the
   "state dir absent" test to swallow a stale-but-present dir.

⚠️ **Do not start the soak just to clear the signal.** That is the 2026-07-24
deploy incident in a new costume (a sweep that started a unit disabled by
design). Decide what SHOULD run there first.

⚠️ **Check the twin before editing either side.** The probe lives in
`src/utils/watchdog_probes_gateway_flow.py`; run `scripts/parity_check.py`
before touching it, and remember the 08-09 ordering rule — port to MeshAnchor
**after** MeshForge's final commit, never before an amend.

---

## The noise ranking (yield's other half)

Not a delete list — a *tuning* list, ordered by fires-per-insight.

| Class | Fires (fleet) | Proposals | Finding |
|---|---|---|---|
| `tracer_peer_unreachable` | **200**, all 8 boxes | 12 rejected / 2 ratified | The loudest class on the fleet, and the notes are near-unanimous: *"transient RNS lab-tracer timeouts"* while the same box verifies healthy on ICMP, HTTP 200, and federation `ok=true, 0 consecutive_failures`. **Federation peer status already knows.** Gate the signal on the federation view disagreeing, or it stays a 200-fire tax on attention. |
| `ntfy_loopback` | 16 (1 box) | **12 rejected / 0 ratified** | Every rejection is the same shape: a *single* transient publish miss that self-cleared by the next 2 h run. Fires on `consecutive_misses=1`. **Raise to ≥2 consecutive.** |
| `cron_verdict_stale` | 125 | 12 rejected / 6 ratified | Genuinely useful — but it **compound-fires on `ntfy_loopback`'s verdict**, and the notes say so explicitly: *"Watcher-watching-the-watcher: cron_verdict_stale reports FAIL ONLY BECAUSE ntfy_loopback wrote FAIL(1) — same single transient."* One event, two pages. This is the operator's exact worry, already documented in his own rejection notes since 2026-06-25. **Decouple, or fixing `ntfy_loopback` alone halves this too.** |
| `rules_seed_drift` | 181, all 8 boxes | 11 rejected / 4 ratified | High volume, but the ratified ones were real un-merged seed bumps. Keep. |

**One cross-cutting noise source is not a signal class at all**: the
`new_subject` dream kind. Nearly every rejection reading *"first-ever sighting
of <a known fleet box>"* is a rule-birth or state-reset artifact — a fresh
rule, or a mini state reset, makes every long-standing subject look new. That
one heuristic accounts for the largest single block of rejected proposals.
Suppress `new_subject` for subjects present in the fleet registry, and for the
first N ticks after a rule is merged.

### `new_subject` — FIXED 2026-08-09 (`fa7725fe`), and it was not a tuning problem

The audit called this a noise source to filter. Reading the code found the
premise underneath it was **false**, which is why no amount of filtering would
have been enough.

`detect_new_subject` inferred "first-ever sighting" from
`fire_count == fire_count_24h`, and said so in its docstring: *"every fire ever
recorded for this subject happened in the last 24h."* But `StateStore.prune_24h`
**deletes** a rule-state key after `STALE_KEY_RETENTION_S` (7 days) of silence,
and `get_or_init` rebuilds it at `fire_count = 0`. A subject known for months
comes back **byte-identical to one never seen**. Retired read as absent —
honest_failure_modes #2, one layer up from the code that has been finding this
class all week, and exactly what the operator wrote on 2026-08-06: *"FALSE
'first-ever sighting'. Subject `ntfy` is the long-lived alerting channel, live
since 2026-06-18."*

**Measured on this box's live ledger**: `new_subject` is **76 of 186 proposals,
74 rejected** — the largest single block, 41% of everything mini ever proposed.

**The fix is memory, not a filter.** `state["subjects_seen"]` stamps the first
fire of each `(rule, subject)` and the prune never touches it. ONE home for the
fact (a copy in the rule state would drift the moment prune ran, #5); ts-only,
~60 bytes an entry, **53 distinct pairs in this box's entire recorded history**
(~3 KB), capped at 4096 with an eviction counter — a memory that silently
forgot is worse than one that says how much it forgot (#9).

On top of that, the three suppressions, each with its measured share of the 76:

| Leg | Share | Why it carries no information |
|---|---|---|
| already-known (the root fix) | 21 | the durable stamp says we have seen it for weeks |
| rule birth (< 24 h since the rule's first sighting) | 27 | `detector_blind_any` merged 07-25 and minted **four** first-ever sightings on its first fire — one event, four identical rejections |
| known fleet member | 20 | the registry says it is expected; also matches bare **addresses** via the `/etc/hosts` block the fleet already generates (read, not resolved — no network, no AAAA round trip) |
| per-event identity `<host>@<boot_id>` | 6 | new by construction, forever; the reboot it stands for has its own signal kind |

⚠️ **Fails open in three places.** An unreadable registry, an unknown self
hostname, and an unreadable address map each make *fewer* names known, which
means *more* proposals — never silence. Every emitted delta carries
`fleet_registry_readable` so a reader can tell a real topology finding from a
registry that could not be read. And with **no durable memory at all** (fresh
or pre-upgrade state) the detector proposes **nothing**: *"I have no memory"*
must not be spoken as *"everything is new."* It self-heals as rules fire.

**Replayed against the real ledger: 74 of 76 suppressed by a positive reason.**
Of the two survivors, one is **the only `new_subject` delta ever ratified** —
the suppression keeps the yield and drops the noise — and the other is a
non-fleet AREDN router, which is arguably the detector working. The ratified
one is safe for a second reason: its finding was ratified on `unexpected_reboot`
evidence, which is its own signal kind, so only the duplicate path goes.

**Live drill, deployed code, isolated home** (`MINI_DUDEAI_HOME` pointed at a
scratch dir so the real state was never touched — verified unchanged by mtime):
one real tick, `rules=73 conds=4 fires=3`, and

- 3 fires → **3 durable stamps written** (the wiring, at the consumer of record);
- the detector run against that live state proposes **0**;
- the v1 inference run against **the same state** proposes **3** — `meshforge-moc3`
  (known member), `meshforge<->meshanchor` (a fixed pair subject) and
  `local-brain`, which are precisely the three shapes the operator has been
  rejecting by hand for two months.

Six legs drilled by neutering each in turn; every one has a test that fails
without it, including the root fix. Ported to MeshAnchor (`engine.py` +
`state.py` are byte-locked twins, `dreams.py` was identical in practice).

---

## What I did NOT measure

**Per-probe CPU cost — UNKNOWN.** There is no per-probe timing instrumentation,
and the only honest way to get it is to run the probe suite with a timing shim,
which writes to live debounce/streak state files and would perturb the running
detector spine on the box being measured. Not worth corrupting the spine for a
ranking. The cost argument here rests on tick count (62 classes × 2 ticks/min ×
9 boxes) and on the moc3 datum already on record (**4.3 s CPU per 30 s tick, 64 MB
= 7.1 % of a 905 MB box**), not on per-class attribution.

## What was executed — 2026-08-08

**4 classes deleted, 62 → 58** (Tier 1 minus the correction, plus Tier 2):

| Class | Disposition of the code |
|---|---|
| `json_uplink_dark` | probe folded back into `channel_feed_dark`: declared-and-absent is now `indeterminate` with the whole diagnosis in its reason (`detector_blind` escalates a persistent one). A regression test pins that this probe emits exactly ONE class. |
| `dream_ratification_stalled` | probe, its 3 private helpers, its tunables, and the `mini_dudeai.dreams` import removed from the watchdog entirely (−226 lines). |
| `aredn_organ_undeclared` | probe + `_resolve_aredn_localnode` / `_fetch_aredn_sysinfo` / `AREDN_LOCALNODE_NAME` removed (−211 lines). Its structural-dark row is marked **REOPENED BY CHOICE**, not deleted. |
| `lxmf_propagation_unused` | probe removed (−123 lines); the shape-A sibling `lxmf_propagation_node_dark` stays (it watches a LIVE dependency). Its "one fault, one owner" reason now says the gap is knowingly unwatched instead of naming a deleted probe. |

Plus: seed rules dropped from both role seeds; `traffic_pulse`, the
re-export hubs, and every ghost reference updated.

**1 blindness fixed**: `propagation_soak_degraded` intent gate now resolves the
operator home by UID instead of `get_real_user_home()`.

**Also fixed** (`e25469ff`): the `nomadnet_crashloop` disposition split — see
the dark-corner section above. It now reports `clean` on the 5 boxes where
nomadnet is enrolled and `inert` on the 3 where it isn't; previously all 8
read `inert` and the probe could not prove it was watching anything.

**Tier 3 — DECIDED, no demotions** (second session, 2026-08-08): all three
stay on the tick; the oracle probe's collapsed quiet-answer was fixed and
`claw_uplink_node_moved`'s sibling instance with it. Full reasoning + the live
evidence in the Tier-3 DECIDED section above.

**Not yet done** (the tuning list): `new_subject` suppression; the
`unspecified` stat; the oracle probe's sample gate (an open operator question,
above); `ntfy_loopback`'s threshold and the post-commit hook were fixed in
`9193dd6a` / `e7201bdd`.

Nothing removed coverage of any incident recorded in `persistent_issues.md`.

---

## Verification status

**VERIFIED — second session (Tier 3), `951ae565`:**

- `python3 -m pytest tests/ -q` → **`exit 0`, 10425 passed, 1 skipped**; the touched-file subset re-run after the last (comment-only) edit → **`exit 0`, 752 passed**.
- `python3 scripts/lint.py --all` → **`exit 0`**; `python3 scripts/parity_check.py` → **`exit 0`, "in sync"**.
- **Drilled**, both fixes: reverting the probe file makes 2 oracle tests and 1 claw test fail; restoring it makes them pass. A guard that has never failed is not evidence.
- **Live, post-deploy**: all 8 boxes at `951ae565`, watchdog restarted only where already active, cells read from each box's own `watchdog.json` — the pre-written prediction held 8/8 (table above).
- **Measured, not guessed**: `inherited_app_drift` costs 0.062 s/tick on moc5 (4 inherited checkouts, mean of 3 runs, root context, via the probe's own helpers).
- **Live premise check**: the oracle is `MESHFORGE_ORACLE_ENABLED=1` on moc3's active gateway with 106 lifetime queries (newest 19 d old) — which is what falsified the delete recommendation.
- **Check of record**: `bash scripts/honest_status.sh` → **`exit 0`** — CI(`951ae565`) run 31280690694 success, fleet SHA drift 8/8, full suite exit 0, lint exit 0, live `confirmation_rate ≤ 1.0` 8 checked. One WARN, both legs **pre-existing and unrelated**: `synth_soak_degraded` on meshanchor-server and `local_brain_regressed` on VolcanoAI (already active in this session's warm brief).

**VERIFIED for the code change (first session)** — checks run on that working tree:

- `python3 -m pytest tests/ -q` → **`exit 0`, 10397 passed, 1 skipped** (baseline before the cuts: 836 passed on the watchdog subset, exit 0).
- `python3 scripts/lint.py --all` → **`exit 0`**.
- `python3 scripts/parity_check.py` → **`exit 0`** after porting the twinned `fleet_truth.py` to MeshAnchor (it caught the drift; that is the gate working).
- The `propagation_soak_degraded` diagnosis, on moc3 itself: operator-UID home → `gateway.json` → `3968a2ee…`, the same node the drill uses, and `/root/.config/meshforge/gateway.json` confirmed absent.

~~**BELIEVED, not verified**: that the `propagation_soak_degraded` cell actually
flips `inert` → `clean` in production.~~ **UPGRADED TO VERIFIED 2026-08-08**
(second session), by the check that was named: the fleet now runs `828daf42`
(which carries the `dfb862fc` fix), and moc and moc3 — the two boxes whose soak
timer is enabled — both read `propagation_soak_degraded: {"disp": "clean"}`
from their own `/var/lib/meshforge/watchdog.json`, under the ROOT watchdog, on
ticks 21 s and 5 s old. The other six read `inert`. Before the fix all eight
read `inert`. Prediction held 8/8.

**UNKNOWN**: per-probe CPU cost (see above). The 4m47s figure is for the soak
*drill*, measured from systemd's own accounting on moc3 — not for a probe.
