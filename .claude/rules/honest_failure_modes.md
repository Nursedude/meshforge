# Honest Failure Modes — the write-time checklist

> Born 2026-06-09 from the mini-dudeai engine review (Issue #80): 18 findings,
> 18 confirmed, ONE defect class in many skins. Hundreds of operator-hours have
> been spent on this topic across the fleet's life (#29, #58, #60, #63, #74,
> #78, #79, #80). This file is the lesson, stated once, to be applied when
> WRITING code — not found later by review. MeshForge serves engineers,
> scientists, HAMs, and hobbyists: a silent failure that makes the user leave
> the app to discover the truth IS an app failure (MF018).

## The defect class

**A degraded internal state gets mapped to a valid-looking value, and
downstream logic converts it into a real-world claim.**

| Error path | Valid-looking value | False claim downstream |
|---|---|---|
| rules file read error | `[]` (empty ruleset) | "no conditions are active" → silent mass-deactivation |
| source collect error | absent conditions | "everything recovered" → false CLEARED page |
| `{"rules": null}` | zero validation errors | "valid document" → promotion wipes alerting |
| typo'd match key | extras equality filter | "legal rule" → rule silently matches nothing forever |
| reader configured, writer not | marker absent | "crash detected" on every planned reboot |
| two consumers, two constants | both look authoritative | writer warns while watcher sleeps |

The error path answered a question it wasn't asked. The type system was
satisfied; the truth wasn't.

## The checklist — run it over every error path you write

1. **Audit every `except`, `or []`, `or {}`, `.get(default)`:** does the
   degraded value overlap the healthy domain? If yes, tri-state it
   (ok / unobservable / error) or hold prior state. Empty ≠ error.
2. **Absence of evidence is not evidence of absence.** Never emit a
   recovery/cleared/healthy transition from data that is missing *because the
   observation channel failed*. Hold last-known state and surface the
   blindness as its own signal. (Fleet creed: unobservable ≠ dark ≠ resolved.)
3. **Validators must reject what the author cannot have meant.** Null where a
   list belongs, zero items where the document's whole purpose is items,
   unknown keys adjacent to a known vocabulary — error or warn LOUDLY at the
   authoring/promotion boundary, never absorb.
4. **Reader/writer pairs wire together or fail together.** If a mechanism has
   a producing half and a consuming half, the code path that constructs one
   must construct (or loudly refuse without) the other. Half-wired = a
   detector that's confidently wrong.
5. **Two consumers of one artifact share ONE constant.** Thresholds, limits,
   paths, formats: derive, import, or test-pin them together. Independent
   hardcodes WILL drift (24,000 vs 24,576).
6. **Wall-clock durations are forgeable on this fleet** (RTC-less Pis, NTP
   steps, fake-hwclock). Durations need monotonic anchors, observed-tick
   counts, or clamps on negative/absurd deltas. Persisted timestamps need a
   "clock went backward" branch. (#74's monotonic fix; #80's grace ticks.)
7. **Closed enums need closed consumers.** When a registry/enum grows (signal
   classes, source kinds, roles), every consumer that switches on it must fail
   a TEST until updated — coverage gates, not memory. (Seed-coverage test;
   closed-enum probe gate.)
8. **Concurrent writers: exclude or merge, never interleave.** Same state
   files + two processes = flock-refuse-loud or single-writer design. Fixed
   tmp names are a collision, not a convention.
9. **Every swallow gets a witness.** A swallowed exception must leave a
   counter, a marker, or a signal a PROBE can see (history_appends_total,
   consecutive_write_errors, preflight keys). If failure leaves no artifact,
   it never happened — until the operator finds out outside the app.

## How to apply

Writing any monitor, daemon loop, validator, or persistence layer: walk the
checklist over the diff BEFORE committing — every item is a 30-second question
about code you just wrote. When VERIFYING a detector you wired, observe the
consumer-of-record (the live unit/interpreter that hosts it), not the wiring
trace — a registered check is not a running check (calibrated_claims rule 7). In review, candidates matching this class are
PLAUSIBLE by default; the burden of proof is on the error path to show what
the consumer sees.

Slow wins the race.
