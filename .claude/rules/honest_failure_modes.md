# Honest Failure Modes — the write-time checklist

> One defect class in many skins (#29, #58, #60, #63, #74, #78, #79, #80):
> **a degraded internal state gets mapped to a valid-looking value, and
> downstream logic turns it into a real-world claim** (`[]` because a read
> failed → "no conditions active"). Apply when WRITING code. A silent failure
> that makes the user leave the app to learn the truth IS an app failure
> (MF018). Item numbers are cited across code and tests — never renumber.
> Origin table, incidents, dates: `.claude/foundations/rules_evidence.md`.

**The general question:** does this path MEASURE what it reports, or ASSERT it?

1. **Audit every `except`, `or []`, `or {}`, `.get(default)`** — if the
   degraded value overlaps the healthy domain, tri-state it (ok /
   unobservable / error) or hold prior state. Empty ≠ error.
2. **Absence of evidence is not evidence of absence** — never emit
   recovered/cleared/healthy from data missing because the channel failed.
   Hold last-known state; surface the blindness as its own signal.
   (Fleet creed: unobservable ≠ dark ≠ resolved.)
3. **Validators reject what the author cannot have meant** — null for a
   list, zero items where items are the point, unknown keys beside a known
   vocabulary: fail or warn LOUDLY at the authoring/promotion boundary,
   never absorb.
4. **Reader/writer pairs wire together or fail together** — constructing one
   half constructs (or loudly refuses without) the other.
5. **Two consumers of one artifact share ONE constant** — derive, import, or
   test-pin; independent hardcodes drift.
6. **Wall-clock durations are forgeable here** (RTC-less Pis, NTP steps,
   fake-hwclock) — monotonic anchors, observed-tick counts, clamps on
   negative/absurd deltas; persisted timestamps need a "clock went backward"
   branch.
7. **Closed enums need closed consumers** — every switch on a growing enum
   fails a TEST until updated.
8. **Concurrent writers: exclude or merge, never interleave** — flock-refuse
   or single writer; fixed tmp names are a collision.
9. **Every swallow gets a witness** — a counter, marker, or signal a PROBE
   can see.
10. **A resolved incident compiles to THREE artifacts** — when a frontier
    session root-causes one: the probe/rule (→R), the persistent_issues/
    runbook entry (→R), and a triage artifact for the tier that meets this
    class next (→L) — **owed only to a tier that CONSUMES it.**
    * tier-L **live** (Ollama running AND the `local_brain_eval` cron armed)
      → an eval case in `evals/local_brain/`, checked by the weekly
      `local_brain_eval --gate` verdict;
    * tier-L **parked** → the decision-tell row in `persistent_issues.md`;
      add NO `evals/local_brain/` cases. When the chat-compiler arc starts,
      this reverts to the eval-case form.
    **Check the consumer's live state before writing** — never infer it from
    this rule (as of 2026-09-07 it was parked:
    [[project_ollama_parked_2026_09_07]]).
11. **At incident close: would an operator WITHOUT the assistant have seen
    this?** If no, the read-only surface owes a row.

**How to apply:** walk this over every monitor, daemon loop, validator, or
persistence diff BEFORE committing. Verifying a detector = observe the live
consumer, not the wiring (calibrated_claims rule 7). In review, candidates
of this class are PLAUSIBLE by default; the error path carries the burden.

Slow wins the race.
