# Post-freeze harness budget — what to do on 2026-10-09

> **Trigger date: 2026-10-09**, when `.claude/rules/harness_restraint.md` expires.
> That file's own instruction is *re-run the two measurements, then DELETE rather
> than renew*. This is the "and replace it with what?" answer, written
> 2026-09-10 at the end of a multi-day maintenance pass, at the operator's ask.
>
> **Written by**: Opus 5, after a session whose four commits were *all* harness.
> The operator's framing, which this file exists to serve: *"multi-day
> maintenance pass — and bugs — that's a failure in design, from me and frontier
> models. Lessons must be learned right."*

---

## 1. The measurement, pinned

The September freeze quoted "316 commits / 53% harness-subject". Re-derived
2026-09-10 with an explicit definition:

    window: 30 days ago   commits: 326
      harness-subject : 155  (47%)
      product-subject :  74  (22%)
      neither/mixed   :  97  (29%)

**harness : product ≈ 2.1 : 1**, at ~10.9 commits/day.

⚠️ **My 47% and September's 53% are not the same quantity.** They used
different definitions (chiefly how `tests/` counts). The *absolute* share is
definition-sensitive; the *order* is not — harness outweighs product roughly
2:1 under either. That discrepancy is the first argument for this file: an
un-pinned definition cannot be tracked over time, and two sessions already
disagreed about the number they were both using to make a decision.

So the definition belongs in a script, not in prose. Proposed
`scripts/harness_share.sh` (working version lives in this session's scratchpad;
reproduce it rather than trusting this quote):

```bash
# Classify each commit in a window by the MAJORITY of files it touches.
#   harness: .claude/** src/mini_dudeai/** src/utils/watchdog** evals/**
#            scripts/{*audit*,*verdict*,claim_gate*,parity_check*,
#                     honest_status*,*drill*,*calibration*}
#   product: src/** templates/** requirements/** docs/**  (after harness match)
#   tests/**: NOT classified — tests follow their subject, they are not a class
# Ties and no-match land in neither/mixed, which must be REPORTED, never
# folded into either side (the #74 lesson, applied to my own bookkeeping).
```

Pin the definition, print all three buckets, and let the ratio be the headline.

---

## 2. Why the freeze did not bind — the part worth learning

The freeze banned **additions**: no new signal class, probe, gate, audit, drill,
or detector. A month later the harness share had not moved.

**2026-09-10 is the case study.** Four commits landed, and *every one was
legitimately exempt*:

| commit | what | exemption claimed |
|---|---|---|
| MF `83d2bc73` | rf_leg cadence calibration | "narrowing one that false-fires" |
| MF `7e26f2fa` | delivery ring starvation | "fixing a detector that is blind" |
| MA `50f9c071` | port of the above | same, in the twin |
| MA `333be0a2` | MF025 split, 1500→893 | adds no class at all |

Each exemption is **individually correct**. I would defend all four. And
collectively they swallow the rule, because a mature instrument layer generates
an endless supply of *legitimately exempt* work on itself. The freeze gates
**new instruments** when the binding constraint is **total harness touch**.

That is the design defect, stated plainly: *the freeze measured the wrong axis,
so it could be obeyed perfectly and change nothing.*

---

## 3. What to put in its place

**Gate the measured axis, at a moment a human already reads, and nowhere else.**

Concretely:

1. **`scripts/harness_share.sh`** — one script, the definition above, prints the
   three buckets and the ratio. No daemon, no cron, no state file.
2. **One line in the pre-push check** (CLAUDE.md already has a four-line
   pre-push ritual): a fifth line — *"trailing-30d harness:product ratio, and
   is this push on the heavy side of it?"* It **reports**; it does not block.
   A number in front of a human at the moment of decision is the intervention.
3. **A budget the operator sets, not me.** Something like: *if trailing-30d
   harness-subject exceeds N%, the next M commits are product or nothing.*
   N and M are his call — see §5.

### The anti-requirements (these matter more than the design)

- ⛔ **This must NEVER become a probe, signal class, cron, or mini rule.** A
  detector that watches the harness share is machinery watching machinery — the
  exact cost this whole file exists to refuse, and it would be self-refuting on
  arrival. `feedback_my_footprint_is_the_constraint` is the standing rule.
- ⛔ **It must be a NET SUBTRACTION.** `harness_restraint.md` is `@`-included
  into CLAUDE.md, so it costs every turn on every box, forever. Landing this
  means **deleting that file and its `@` line** and adding one small script plus
  one pre-push line. If the replacement is bigger than what it replaces, do not
  land it — just delete the freeze and keep the measurement as a habit.
- ⛔ **Do not renew the freeze out of habit.** Its own words: if the harness
  share has not moved, the freeze was not the binding constraint, and *something
  else is — say so plainly.* The something else is named in §2.

---

## 4. Two cheap fixes that protect product directly

Worth more per line than any gate, both surfaced 2026-09-10:

**(a) A maintenance-window marker the probes can read.** Three of six dream
deltas that day were the system reacting to *our own maintenance* — five
deliberate `rnsd` restarts on moc3 became `service_inactive::rnsd`,
`role_drift::gateway-only`, and moc2 sighting its RNS peer drop 10s after a
restart. Every instrument was correct; the *event* was us. This is the fourth
instance in four days of the class in
[[feedback_my_actions_become_the_telemetry_i_read]]. A marker written by
`fleet_sync`/`try-restart` paths and read by the disposition layer deletes the
whole category — and it *removes* triage work rather than adding surface.
⚠️ It must fail SAFE: a stale marker must never suppress a real fault. Bound it
by time, and treat marker-present as "annotate", never "silence".

**(b) Announce which END a task serves, at intake.** `model_advisor.md` already
makes a session state task-tier out loud in one line. There is no equivalent for
`domain_clarity_two_offerings`' question — *which END does this serve?* On
2026-09-10 the honest answer at intake was "harness", and I never said it. I'd
have done the work anyway (the blindness was real and would have hidden a total
confirmation collapse), but **the choice to spend a day on it would have been
the operator's, before the day, not after**. One line at intake, same shape as
the tier line. This is a rule, not an instrument — no code, no gate.

---

## 5. Open questions — operator's call, not mine

1. **What are N and M?** What trailing-30d harness share is *acceptable*, and
   what's the penalty when it's exceeded? I have no principled number; you have
   the lived cost. A defensible starting point: N = 40%, M = 3.
2. **Is "product" the right opposite?** 29% landed in neither/mixed. Some of
   that is genuinely product-adjacent (docs, requirements). If that bucket is
   large forever, the binary framing may be wrong and the real question is
   "what fraction moved a message."
3. **Does the fleet want fewer instruments, not just fewer instrument commits?**
   The September census: 61 signal classes, 314 clean / 234 inert / 1
   indeterminate. **`inert` everywhere means CUT** (footprint rule). A one-time
   subtraction pass on the inert set may beat any ongoing budget — and it's the
   one kind of harness work that is unambiguously exempt, because it removes.

---

## 6. What is NOT the lesson

Worth writing down because it is the tempting conclusion and it is wrong.

**"Bugs exist, therefore the design failed."** No. The two bugs fixed on
2026-09-10 were both found *by the instruments doing their job*, and the
delivery one was real: a 200-slot ring holding 196 unconfirmable events against
a threshold of 20 meant a **total** confirmation collapse would have read as
"quiet leg" forever. Finding that cost a day. Not finding it costs an outage the
operator learns about from a browser — which is exactly the 2026-08-11 lesson.
That instrument paid for itself.

The failure is not that bugs exist in a 12,219-test system nine boxes depend on.
It is the **ratio**, and the fact that the harness gates *correctness* (lint,
guards, claim-gate, `honest_status`) while **nothing gates volume** — so the
volume lands on one 65-year-old operator's hours. That is
[[feedback_opus_fable_positive_feedback_loop_2026_09_03]], measured again.

*Slow wins the race.*

---

## 7. INTERIM measurement — 2026-09-28 (Opus 5.5), 11 days before the review

Operator asked for the review early. These are READINGS for 10-09, not its
verdict — re-run them then; do not carry these numbers.

**Commit split** — §1's pinned definition (majority-of-files; `tests/` unclassified;
neither/mixed reported). Awk: `scratchpad/share.awk` of that session; reproduce from §1.

| window | commits | /day | harness | product | neither | h:p |
|---|---|---|---|---|---|---|
| 08-10..09-09 (pre-freeze, 30 d) | 323 | 10.8 | 165 (51%) | 73 (23%) | 85 (26%) | 2.26 |
| 09-09..09-28 (freeze, 19 d) | 463 | 24.4 | 199 (43%) | 181 (39%) | 83 (18%) | 1.10 |

The RATIO moved (2.26 → 1.10) — but VOLUME more than doubled, and harness
commits/day went UP (5.5 → 10.5). The freeze shifted the mix toward product;
it did not brake the rate. Nothing gates volume (the 09-03 loop memory).

**Disposition census** — `/var/lib/meshforge/watchdog.json` `coverage`, 9 boxes
(lehua is `field-node`: no watchdog BY DESIGN, `docs/fleet_roles.yaml` §field-node):
62 classes · **299 clean / 258 inert / 1 active / 0 indeterminate** (Sept: 61 ·
314 / 234 / 1 indeterminate). Inert on EVERY box: `oracle_delivery_degraded`
(the Sept one) **+ `inherited_app_drift`** — the two cut candidates, pending
`falsifiability_drill.py` to rule out BLIND. One class added in-freeze:
`bridge_leg_down` (09-27), forced by an operator drill (5.7 h dark, nothing
paged) and proven end-to-end 09-28 (ntfy received) — the freeze's "evidence
worth having" clause working as written.

**Not yet run for 10-09**: the inert-tier cut's ACTED-ON column (which fired
AND drove a change), and the drill on the two all-inert classes.

**Product-counter decision owed at 10-09** (recorded, not fixed): p2s queue
`delivered` = the RAK accepted it over SERIAL, not heard on RF (09-28: 3
delivered / 1 arrived). An honest-claims gap in a PRODUCT counter — decide
whether it earns a rename/second state.

---

## 8. THE REVIEW — 2026-10-09 (Fable 5.1) — freeze DELETED, not renewed

Operator's brief for this session: *"be adversarial — critical — freezes are
the last thing I want to do — this is AI dev."* Every number below was
re-derived this session (scripts in the session scratchpad; the commit split is
now `scripts/harness_share.py`). Nothing is carried from §1 or §7.

### 8.1 Commit split (pinned §1 definition, `scripts/harness_share.py`)

| window | commits | /day | harness | product | neither | harness/day | product/day | h:p |
|---|---|---|---|---|---|---|---|---|
| 08-10..09-09 pre-freeze | 320 | 10.7 | 168 (52%) | 72 (22%) | 80 (25%) | 5.6 | 2.4 | 2.33 |
| 09-09..10-09 FREEZE | 728 | 24.3 | 323 (44%) | 294 (40%) | 111 (15%) | 10.8 | 9.8 | 1.10 |

The freeze did not brake anything. Harness commits/day DOUBLED under it
(5.6 → 10.8). The ratio moved only because product quadrupled (2.4 → 9.8/day).
§2's diagnosis stands, now with the full window: the freeze gated NEW
instruments while the work was fixes to EXISTING ones, each legitimately
exempt. Two probes (`bridge_leg_down`, `peer_cron_verdict_stale`) and three
seed rules (`bridge_leg_down_any`, `cron_verdict_concern_any`,
`sdr_receiver_wedged_any`) were added in-freeze — every one forced by a real
event (5.7 h dark leg; a watchdog-less box's failing crons; two RTL-SDR wedges).
The exemption clause carried all of the right work and the freeze carried none
of the wrong work. A rule that is obeyed perfectly and changes nothing is not a
brake; it is a 5,141-char per-turn tax.

### 8.2 Disposition census (`/var/lib/meshforge/watchdog.json` coverage, 9 boxes)

62 classes · 558 cells · **299 clean / 258 inert / 1 indeterminate** — byte-for-byte
the 09-28 interim (Sept: 61 · 314/234/1). Inert on EVERY box:
`oracle_delivery_degraded` + `inherited_app_drift`. Drill
(`falsifiability_drill.py --classes …`, exit 0): **both `caught-both`** — neither
is blind. Reasons from the boxes themselves:
- `inherited_app_drift` → "no inherited checkouts on this box" ×9. The
  policy's subject left the fleet (RNS-Mgmt-Tool + Gateway-Tool archived
  09-25). **CUT candidate** — delete probe + rule + its 7–9 named tests
  (`watchdog_probes_env.py`, seeds, `test_watchdog_probes.py`,
  `test_regression_guards.py`). Needs a deploy; not done in the review commit.
- `oracle_delivery_degraded` → "oracle never wrote a log" ×7, idle ×2. Inert
  because tier-L is PARKED by operator decision (09-07/09-18), not because the
  class is wrong. **KEEP parked with its organ**; cut the day Ollama is removed,
  together (24/31 collateral test failures in the drill say it shares fixtures).
The 17 classes inert on 8 of 9 boxes are role-scoped (claw_* on moc2, the
gateway family on moc) — inert-by-role is a correct `inert`, not waste. The
architectural question §QUEUED asked — separate budgets for author-facing gates
vs fleet-facing detectors — is answered by 8.3: the detector tier's cost is not
commits, it is PAGES.

### 8.3 The third measurement — what fired and was acted on (30 d, mini history, 9 boxes)

- **37 rules fired · 850 edge_ups · 487 ntfy pages delivered + 63 failed
  (`URLError`, WAN down) + 297 `propose_escalation`.** That is **~16 pages/day
  to one phone.** This is the operator-hours number; the commit ratio never was.
- **`tracer_peer_unreachable_any` = 369 edge_ups (43% of all), 307 pages, on 8
  boxes.** Fixed TWICE in-freeze (`7edefbb8` blind-sibling hold, `5c5986a2`
  returned_at) and the rate did not move: **12.4/day before 10-06, 11.7/day
  after.** Both fixes cured a different failure mode (false pages after Resume).
  Episode shape: median 10.0 min = ONE tracer tick (`OnCalendar *:00/10`), 62%
  ≤ 15 min, 43 ≥ 60 min, several boxes share a 269–270 min max (one fleet event).
  ⚠️ Not judged here. Two readings, and this is the next PRODUCT question:
  (a) RNS paths between fleet boxes really drop ~12×/day — a finding about the
  hub (`project_rns_is_the_hub_priority`), or (b) the probe pages on one missed
  traceroute. Discriminator: a week of fires against `rnpath`/announce events at
  both ends, then `persistent_cycles`. Not `known_benign`.
- Commit-touched classes: 21 of 62. `cron_verdict_stale` 12 commits / 36 fires;
  `delivery_confirmation_stall` 4 commits / **0 fires** (fixed while silent — the
  09-10 ring finding); `service_inactive` 3 / 54; `rf_leg_silent` 3 / 42 (0 since
  10-06 — the 17 dBm / antenna arc). `kernel_reboot_pending` 9 fires / 0 commits
  = acted on by reboots, which git cannot see; the acted-on column is a proxy.
- Judgment layer: 65 deltas resolved in-window, `known_benign` 29, five of them
  keyword-match "blind/indeterminate" and **none is a `detector_blind` or
  `indeterminate` subject** (aredn transient ×2, tracer persistent_active,
  cron_verdict_concern by-design, a `drill` subject). Freeze §3 held.

### 8.4 What the measurement found by failing (hfm #9, applied to the measurer)

My first fire count read **563**, not 850: four history files carry the
08-27 power-loss NUL class — **moc 368 B / moc3 3,180 B / kiai 1,487 B /
meshanchor-server 3,802 B, one or two non-JSON lines each** — and `grep` without
`-a` calls the file binary and stops at the first match. `history_write_stalled`
reads `clean` on all of them (it measures writes, not corpses). JSON consumers
(`brief.py`, `rollup.py`) skip the line silently with no witness.
`scripts/mf5_soak_watch.py:171` uses `grep -c` on the same file. Owed: quarantine
the NUL lines on the 4 boxes (operator go — fleet state files), `-a` or a
JSON reader in every grep consumer, and a counter for skipped lines.

### 8.5 Per-turn cost — the axis AI dev actually pays

`@`-included into every turn of every session on every box, measured:
CLAUDE.md 15,212 + persistent_issues 39,998 (**2 chars under its 40,000 cap**)
+ calibrated_claims 10,548 + honest_failure_modes 6,979 + harness_restraint
5,141 + security 3,063 + testing 3,118 + model_advisor 2,452 = **86,511 chars
≈ 20k+ tokens per turn** before a word of work. Commits are free in AI dev;
tokens-per-turn and pages-per-day are not. Deleting the freeze file is −5,141
per turn. `persistent_issues.md` at its cap is the next cut target (archive
rows older than their guard), and `review_provenance.md` at 965 KB / 57
touches a month is the 09-09 finding still true.

### 8.6 Verdict and what replaced the freeze

1. **Deleted** `.claude/rules/harness_restraint.md` and its `@` line; ROADMAP
   rule rewritten. No renewal: the axis never bound, and the operator does not
   want freezes. Nothing per-turn replaces it.
2. **Landed** `scripts/harness_share.py` — the pinned definition, hand-run,
   zero per-turn cost, NOT a gate (§3's pre-push line and N/M budget are
   rejected: a ratio gate is a freeze in disguise).
3. **Kept** from the freeze file, where it already lives: §3 "`known_benign` is
   not available for a blindness subject" (`feedback_detector_blind_is_a_finding`,
   review_provenance) and the END test (`feedback_domain_clarity_two_offerings`).
4. **Owed, in order of operator-hours saved:** tracer judgement (8.3); NUL
   quarantine (8.4); `inherited_app_drift` cut (8.2); persistent_issues
   archive pass (8.5); the p2s `delivered` rename (§7, still open — not decided
   here, scope was the freeze).
