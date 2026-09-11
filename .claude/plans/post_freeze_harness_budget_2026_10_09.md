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
