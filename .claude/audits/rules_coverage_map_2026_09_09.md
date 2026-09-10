# B2 coverage map — step 1 of `plans/rules_ab_2026_09_09.md`

> **Run 2026-09-09 (Opus 5). Step 1 ONLY.** Mechanical lookup: for each
> normative statement in the three always-loaded B2 files, which B1 mechanism
> (lint rule / hook / test / script) already enforces it?
>
> ⚠️ **This session may not retire what it judges** (the plan's own rule, and
> [[feedback_opus_fable_positive_feedback_loop_2026_09_03]]). Every row below
> is a FINDING. Deletions are a separate, separately-authorized pass.
>
> ⚠️ **Step 2 is not available to this session either.** The grader must be
> outside the model under test; Opus 5's claims are in the ledger being judged.

## Byte baseline — re-derived, not carried

    calibrated_claims.md      8,628 B
    honest_failure_modes.md   6,179 B
    harness_restraint.md      4,308 B
    B2 TOTAL                 19,115 B   (matches the plan's figure)

## The map

Coverage: **FULL** = a mechanism refuses/blocks independent of the model ·
**PARTIAL** = enforced for code but not for my own prose, or the gate exists
but is advisory · **NONE** = genuine B2, the model reading it is the only
enforcement.

### calibrated_claims.md — 13 normative statements

| # | statement | B1 enforcement | load-bearing? |
|---|---|---|---|
| CC-T | tag every claim VERIFIED / BELIEVED / UNKNOWN | `claim_gate.py` Stop hook — its block message names all three tiers verbatim (L250-252) | **FULL** |
| CC-1 | banned bare words without a quoted result | `claim_gate.py` `STRONG_CLAIMS` + `EVIDENCE_PATTERNS` (L54-78) | **FULL** |
| CC-2 | pass@1 ≠ reliable; a fix-claim names the root cause | none | NONE |
| CC-3 | re-derive, never patch your own count | none — `honest_status.sh` re-derives its OWN state; nothing watches my tallies | NONE |
| CC-4a | check of record = `honest_status.sh`; exit 2 never a pass | `honest_status.sh` + claim_gate reads its verdict marker for HEAD | **FULL** |
| CC-4b | capture the real exit code, never `pytest \| tail` | `.claude/hooks/exit_code_mask_guard.sh` (PreToolUse) + lint MF022 | **PARTIAL** — warn-only by design, and see Finding 1 |
| CC-5 | surface the blind spot, don't average it away | MF027 covers PROBE code; nothing covers my summaries | PARTIAL |
| CC-6 | every claim leaves a witness; never hand-write a verdict | claim_gate auto-logs marker-backed VERIFIED (L260-276); "verdicts never" is convention only | PARTIAL |
| CC-7 | verify the consumer-of-record, not the wiring | none | NONE |
| CC-C1 | "what would still pass if the feature were dead?" | none | NONE |
| CC-C2 | rank evidence by authorial distance | none | NONE |
| CC-C3 | an absence must be explained, never assumed benign | MF027/MF028 for probes; `harness_restraint` §3 for `known_benign` | PARTIAL |
| CC-A | "How to apply" closing checklist | restates CC-T/1/3/5 — no new normative content | **REDUNDANT** |

### honest_failure_modes.md — 10 normative statements

| # | statement | B1 enforcement | load-bearing? |
|---|---|---|---|
| HFM-1 | tri-state degraded values, empty ≠ error | lint **MF027** (probe fail-dark: except returning None w/o `note_disposition`) | **FULL** (probe code) |
| HFM-2 | absence of evidence ≠ evidence of absence | MF027 + inert/indeterminate split + `tests/test_watchdog_unobserved_hold.py` | **FULL** (probe code) |
| HFM-3 | validators reject what the author cannot have meant | none | NONE |
| HFM-4 | reader/writer pairs wire together or fail together | none | NONE |
| HFM-5 | two consumers of one artifact share ONE constant | **RESOLVED 2026-09-09** — hook now derives from lint's MF022; pinned by `tests/test_exit_code_mask_guard.sh` | **FULL** |
| HFM-6 | wall-clock durations are forgeable; use monotonic | `monotonic` is used in 4+ modules, but no gate requires it | NONE |
| HFM-7 | closed enums need closed consumers | coverage gates exist (`test_honesty_invariants.py`, `test_fleet_posture.py`, …) | **FULL** |
| HFM-8 | concurrent writers: exclude or merge, never interleave | none | NONE |
| HFM-9 | every swallow gets a witness | lint **MF027** + **MF028** (`note_state_write_failure`) | **FULL** |
| HFM-10 | a resolved incident compiles to THREE artifacts | none | NONE |

### harness_restraint.md — 3 normative statements

| # | statement | B1 enforcement | load-bearing? |
|---|---|---|---|
| HR-1 | the 30-day freeze on new instruments | none | NONE — **but it carries a hard expiry, which is the target shape** |
| HR-2 | a harness escalation is a NOTE, not work | none | NONE |
| HR-3 | `known_benign` unavailable for a blindness subject | none | NONE |

## Tally — re-derived from the tables above

    26 normative statements
     7  FULL          (CC-T, CC-1, CC-4a, HFM-1, HFM-2, HFM-7, HFM-9)
     1  REDUNDANT     (CC-A)
     5  PARTIAL       (CC-4b, CC-5, CC-6, CC-C3, HFM-5*)
    13  NONE          genuine B2
    (*HFM-5 is counted PARTIAL but is really BROKEN — see Finding 1.)

    full mechanical coverage: 8/26 = 31%
    counting partials at half: 10.5/26 = 40%

**Against the pre-registered prediction of 40-60%: the low edge, arguably
below it.** The prediction was optimistic. Recording that rather than
re-reading the tables until they agree with it.

**Stop condition NOT triggered.** The plan says ">60% mechanical coverage →
stop and retire before running step 2." We are at 31-40%. Step 2 remains
live for the 13 uncovered statements.

## Byte accounting — what a retirement pass could actually recover

    FULLY-COVERED normative prose
      CC-T   tiers        834 B      HFM-1  #1       209 B
      CC-1   rule 1       217 B      HFM-2  #2       299 B
      CC-4a  rule 4       310 B      HFM-7  #7       262 B
      CC-A   how-to       855 B      HFM-9  #9       294 B
                                     -------------------
                                     subtotal      3,280 B  (17% of B2)

    NARRATIVE PREAMBLES (zero normative statements)
      calibrated_claims  L1-32     2,045 B
      honest_failure_modes L1-29   1,550 B
                                   -------------------
                                   subtotal      3,595 B  (19% of B2)

## Finding 1 — a drift-pin that was never built (the audit's own class)

`.claude/hooks/exit_code_mask_guard.sh` header states:

> "`tests/test_exit_code_mask_guard.sh` pins this guard and lint's MF022 to the
> SAME verdicts on one corpus, which is what keeps two implementations of one
> rule from drifting (honest_failure_modes #5: derive, import, or TEST-PIN
> them together)."

That file does not exist, and `git log --all -- tests/test_exit_code_mask_guard.sh`
returns **nothing** — it never existed. `find`/`grep` across the tree confirm the
only two mentions of `exit_code_mask` are the hook itself and `harness_audit.sh`
(which verifies hook FILES resolve, not that a claimed test exists).

So the one place in the tree that invokes HFM-5 by name to justify two
implementations of one rule is itself the unpinned pair HFM-5 describes. The
guard works; its drift protection is prose. **This is the plan's thesis in
miniature: a rule cited in a comment is not a rule enforced by a test.**

**RESOLVED same session (operator: "write the pin").** Measured the drift on a
9-case corpus before writing expectations, and it was real and ASYMMETRIC:

    python3 -m pytest tests/ | grep -v ok | tail -3; rc=$?
        MF022 FIRED · hook SILENT    <- false NEGATIVE in the guard that runs
    echo "never do: pytest tests/ | tail -3; rc=$?"
        MF022 clean · hook FIRED     <- false positive on a fix-hint

Cause: the hook's bash regex used `[^|]*` (cannot cross an intervening pipe)
and had no quote-awareness. Cured with HFM-5's strongest form — **derive, don't
duplicate**: the slow path now calls lint's real MF022 predicate, inheriting
quote-awareness and the next-line `$?` lookahead. Cost lands on the slow path
only (bare python3 19 ms -> 72 ms with `import lint`); the two-grep bash fast
path is untouched, so per-call session cost is unchanged.

`tests/test_exit_code_mask_guard.sh` now exists (25 assertions). It was run
against the UNFIXED hook first and went red on exactly the two divergences
(`exit=1`, 23 PASS / 2 FAIL) — a pin that cannot fail is not a pin
([[feedback_a_guard_that_never_failed_is_not_evidence]]). Green after: `exit=0`,
`ALL PASS`. Live drill on the previously-blind shape wrote `MASKED-EXIT-CODE`
to the real witness.

### Finding 1b — a SECOND unpinned pair, one level up

The hook that actually RUNS is `~/.claude/hooks/exit_code_mask_guard.sh`, wired
from `~/.claude/settings.json` — a **copy** of the repo file, not a symlink.
Same HFM-5 shape one level up, and `harness_audit.sh` only checks that hook
files EXIST, never that they MATCH. Both copies were installed together and the
pin now asserts `cmp -s` between them (and reports an absent copy as
absent-by-design rather than letting a skip read as agreement).

### Finding 1c — the shared implementation inherits lint's over-inclusiveness

MF022's `.*` crosses `;`, `&&` and `||`, so a compound command whose `$?`
belongs to an EARLIER statement than the `| head` can fire. Pre-existing in
MF022; the hook inherits it by deriving.

**MEASURED, then DECLINED — 2026-09-09.** I first reported this as "a real
increase in noise on the kind of command I write constantly," from a live
impression minutes after install. Then I measured it against the guard's own
163-command witness corpus:

    would fire under the new rule           16
      pipe ATTACHED to a verdict command    14   (true positive)
      only reachable across ; && ||          2   (the 1c class)
      -> false-positive share              12%

⚠️ **The first measurement said 69% and was WRONG** — my classifier counted the
bare `&` inside `2>&1` as a statement separator, so it scored true positives
like `python3 … 2>&1 | head -28; echo "$?"` as false ones. The examples are what
exposed it; the number alone read as plausible. A reminder that a classifier I
write is inside my own reasoning, not outside it
([[feedback_verify_the_verification]]).

**Verdict: do not narrow MF022.** At 88% precision on a real corpus, scoping
`.*` to statement boundaries chases 2 lines in 163 while adding false-negative
risk to a rule whose FNs cost false-GREEN verdicts — the expensive direction.
The guard is warn-only, so those 2 cost a stderr line each. Declining is the
subtraction here; re-open only if the share climbs materially.

**SUPERSEDED 2026-09-09 (close #3, Fable 5.1) — by a measurement, not a
re-read.** The optional 4th ultra pass found the two "1c class" rows were the
guard firing on its OWN cure text (`pytest > log 2>&1; rc=$?; cat log | tail`)
and on `set -o pipefail`, where `$?` is correct — a guard that flags its remedy
trains the reader to dismiss it. The FN the verdict above feared
(`pipeline; rc=$?`) is kept: the new predicate requires the `$?` read to be the
next STATEMENT after the pipeline and exempts an earlier `pipefail`. Re-derived
over the same corpus (230 rows by then): fires 19 → 17, near-miss 211 → 213,
the 2 lost are exactly the 1c rows, 0 gained. Fix commit named in the
provenance row of the same date; the pin carries both directions.

⚠️ **Precision limit of this measurement, stated rather than buried**: witness
lines are truncated at 160 chars and have newlines flattened to spaces, so
multi-line commands lose newline separators and a few long commands lose their
tail. The true FP share could be modestly higher than 12%; it is not plausibly
near 69%.

## Finding 2 — the ledger's 96% should stop being quoted (confirms the plan)

Independently visible in this session's own warm brief:
`99 VERIFIED claims logged · 47 held / 2 broke / 50 still unverified`.
The rate is computed over the ~half that got re-derived, and re-derivation only
fires when a full `honest_status` verdict lands on that exact head — i.e. over
claims made by sessions careful enough to run the full suite. Biased toward
`held` by construction. The plan pre-identified this; the live numbers match.

## Finding 3 — the expensive half of B2 is not normative at all

3,595 B (19%) is defect-class narrative carrying no instruction. This is the
single largest contiguous retirement candidate and the plan did not anticipate
it — the plan assumed the cost was in rules, and the cost is substantially in
provenance.

⚠️ **State the tension rather than resolving it here.** The plan's success
condition keeps a B2 statement only "with its failure NAMED", and this
narrative is where the failures are named. So it is not obviously dead weight —
it may be the part that makes the rules stick across a model swap, which is the
exact exposure the plan exists to measure. **A candidate, not a verdict.**
Deciding it belongs to the operator or a grader outside the model under test.

## What step 2 would still have to cover

The 13 NONE rows. CC-3 (re-derive, never patch a tally), CC-7 (consumer-of-
record), CC-C1 (what would pass if the feature were dead), CC-C2 (authorial
distance) and HFM-10 are the ones with no plausible mechanical form — they are
judgement about how a claim was reached, not a pattern in a file. If any prose
is permanently load-bearing, it is those.

*Slow wins the race.*
