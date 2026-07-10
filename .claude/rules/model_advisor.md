# Model Advisor — match the task to the tier, out loud

> Born 2026-07-09 from the cross-model agentic-health arc: the operator runs
> different Claude models by cost/availability (frontier passes are rationed),
> and the fleet has a measured local tier below them. Reliability lives in the
> executable harness, not the model — but *routing* a task to the right tier
> is a judgment every session should make at intake, visibly.

## The tier ladder (cognition, descending)

| Tier | Fit |
|------|-----|
| **Frontier-class** (rationed) | adversarial review passes, novel-arc design, incident forensics with ambiguous/conflicting evidence, security-sensitive refactors |
| **Opus-class** (day-to-day default) | dev, deploys, probes, docs, fixes with test guards, fleet ops — months proven on the cadence |
| **Fast/Haiku-class** | mechanical sweeps, formatting, log triage, single-file lookups |
| **L — local LLM** (Ollama, eval-gated) | cadence triage fallback, offline oracle — PROPOSE-only, never ratifies |
| **R — rules/probes** | everything already compiled downward; the always-on tier |

## The behavior — one line at task intake, then proceed

Judge the task's tier against the running model and say so in ONE line, then
do the work anyway unless the operator redirects:

- **Upshift tell** ("could use more of me"): review-shaped, novel-design-
  shaped, or ambiguous-evidence work on a non-frontier session → say so and
  queue the range in `.claude/audits/review_provenance.md` as a ready
  worklist for the next frontier pass. Never fake a frontier-quality
  adversarial pass on a smaller model — a shallow review that blesses code is
  worse than an honest queue entry.
- **Downshift tell** ("could use less of me"): mechanical/formatting/triage
  work on a frontier session → say it would run fine on a smaller model or
  fast mode, and offer to batch it rather than burn the rationed session.
- **Right-sized**: say nothing; just work.

## Invariants (every tier)

- Gates never scale down with the model: lint, regression guards, claim-gate,
  `honest_status.sh` run identically. Smaller model = lean on them HARDER.
- Calibrated claims are tier-independent: VERIFIED needs a quoted check ran
  this turn, on every model.
- Tier-L artifacts always carry `brain_tier: local`; its competence claims
  come from the eval ledger (`local_brain_evals.jsonl` + the weekly
  `local_brain_eval` cron verdict), never vibes.
