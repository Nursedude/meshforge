# Model Advisor — match the task to the tier, out loud

| Tier | Fit |
|------|-----|
| **Frontier-class** (rationed) | adversarial review, novel-arc design, ambiguous-evidence forensics, security-sensitive refactors |
| **Opus-class** (default) | dev, deploys, probes, docs, fixes with test guards, fleet ops |
| **Fast/Haiku-class** | mechanical sweeps, formatting, log triage, single-file lookups |
| **L — local LLM** (Ollama, eval-gated) | cadence triage fallback, offline oracle — PROPOSE-only, never ratifies |
| **R — rules/probes** | everything compiled downward; always on |

At task intake, judge the tier against the running model in ONE line, then
do the work unless redirected:

- **Upshift** (review / novel design / ambiguous evidence on a non-frontier
  session) → say so; queue the range in `.claude/audits/review_provenance.md`
  as a ready worklist for the next frontier pass. Never fake a
  frontier-quality adversarial pass.
- **Downshift** (mechanical work on a frontier session) → say so; offer to batch.
- **Right-sized** → say nothing.

Invariants: gates (lint, regression guards, claim-gate, `honest_status.sh`)
never scale down with the model — smaller model = lean on them harder;
calibrated claims are tier-independent; tier-L artifacts carry
`brain_tier: local`, and their competence comes from the eval ledger
(`local_brain_evals.jsonl` + the weekly `local_brain_eval` cron verdict),
never vibes.
Origin: `.claude/foundations/rules_evidence.md`.
