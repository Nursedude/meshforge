# Upshift-witness — design (Fable, 2026-07-14; Opus executes)

> Gap (named in `model_advisor.md` + the cross-model arc): the upshift path —
> "review/design-shaped work on a non-frontier session → queue a
> review_provenance row instead of faking the pass" — is entirely
> SELF-POLICED. That is disposition-dependence, the exact thing the harness
> exists to remove. This design compiles the behavior into two deterministic
> checks + one advisory nudge. It does NOT try to detect "work that should
> have been reviewed" from semantics — that is not deterministically
> decidable; be honest about the limit and enforce what IS decidable.

## What is deterministically knowable

- **The session's model tier**: the calibration ledger (`~/calibration_ledger.jsonl`)
  records `model_id` on every claim event; the Stop-hook claim gate already
  reads/writes it. Frontier-class = configurable prefix list
  (`claude-fable-`, future ids) in ONE constant (honest_failure_modes §5 —
  shared, not hardcoded twice).
- **Review claims in commit messages**: `git log` for the push range; a
  commit that says "self-review", "adversarial", "N findings", "reviewed"
  is CLAIMING a review happened.
- **Provenance rows**: `.claude/audits/review_provenance.md` is parseable
  (date + scope SHAs/ranges per row; worklist rows have a Pri column).

## The three legs

### Leg 1 (hard gate): review-claims leave provenance witnesses

`scripts/review_provenance_check.py` (new, stdlib-only), wired into
`.githooks/pre-push` beside lint+guards:

- For each commit in the push range whose subject/body matches the
  review-claim pattern set, require `review_provenance.md` (in the same push)
  to contain that SHA / a range covering it, in EITHER the completed table or
  the queued worklist.
- Fail CLOSED with the exact append-ready row skeleton in the error message
  (in-domain remediation, MF018 spirit).
- This is model-independent: a Fable session claiming a review must stamp the
  row too (it already does by convention — now it can't forget).

### Leg 2 (hard gate): non-frontier sessions cannot stamp a COMPLETED frontier-tier row

Same script: a new row in the COMPLETED table whose Mechanism column claims a
frontier-tier pass (`/code-review ultra`, "frontier", "Fable") while the
current session's newest ledger `model_id` is non-frontier → block with
"queue it as a worklist row instead". (The inverse of faking: you may always
QUEUE; you may not CLAIM the frontier pass.) Unknown/absent ledger model_id →
warn, don't block (unobservable ≠ violation — honest_failure_modes §2).

### Leg 3 (advisory nudge): the unreviewed-range tripwire

In the same pre-push run, compute `git diff --stat <last-provenance-row-SHA>..HEAD`
restricted to `src/`. If lines-changed exceeds a threshold (start: 800) AND
the session model is non-frontier, print ONE advisory block: "N src lines
since the last reviewed boundary — consider queueing an upshift row
(review_provenance worklist)". Never blocks. The threshold is a named
constant with a comment explaining it is a nudge, not a judgment.

## Honest-failure-modes walk (write-time, per the checklist)

- §1: absent provenance file / unparseable row → ERROR loudly (leg 1 is a
  validator; null where rows belong must not read as "no obligations").
- §2: missing ledger model_id → warn-only path (leg 2), never silently pass
  as frontier NOR block as non-frontier.
- §5: frontier-prefix list + claim-pattern set live in the script as the ONE
  constant; the tests import them (no second hardcode in tests).
- §9: every leg that fires appends one line to `~/upshift_witness.log`
  (witness for the #78-style "did the guard ever run" question).
- §10: when this ships, add a tier-L eval case (oracle: "what stops a small
  model from faking a frontier review here?").

## Tests (red-first)

- Fixture repo range with a "self-review: 3 findings" commit and no
  provenance diff → leg 1 blocks; with the row → passes.
- Completed-table row claiming "ultra" under a non-frontier ledger → leg 2
  blocks; same row under `claude-fable-*` → passes; absent ledger → warns.
- 900-line src diff, non-frontier → leg 3 prints once; frontier → silent.

## Non-goals / honest limits

- Does NOT detect an unclaimed, unreviewed risky change (undecidable) — leg 3
  is the only (advisory) pressure there.
- Does NOT parse conversation transcripts — commit messages + the ledger +
  the provenance file are the only inputs (all durable artifacts).
- MA: port after MF soaks (shape-parity tier; MA has its own hooks dir).
