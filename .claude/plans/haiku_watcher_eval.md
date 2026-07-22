# Haiku watcher eval — is claude-haiku-4-5 a better middle brain than Ollama?

> **QUEUED 2026-07-21 (Fable 5 session, operator-approved). Sized: ONE Opus
> session.** Origin: operator wants a small always-on watcher at the QTH and
> is "good with Anthropic at any size"; the QTH runs Max for the foreseeable
> future. Decision discipline: MEASURE before wiring — the eval ledger, not
> vibes, decides whether Haiku-via-API earns the rung between tier-L and
> Opus (model_advisor ladder; calibrated_claims: tier-L competence claims
> come from the eval ledger).

## Goal

Add `claude-haiku-4-5` (Anthropic API) as a CANDIDATE brain in the
`local_brain_eval` harness, run it head-to-head against the production
Ollama brain on the full oracle case set (33 cases in
`evals/local_brain/seed.jsonl` as of 07-21), record both in the results
ledger with explicit brain identity, and make the adopt/reject call from
the numbers.

## Charter invariants (do not renegotiate)

1. **PROPOSE/triage only, never ratify** — identical charter to tier-L. The
   `mini_cadence OK` verdict stays frontier-only evidence; the claw F-tier
   glyph must remain unforgeable by any non-frontier run.
2. **The offline floor is unchanged** — Haiku is API-only, therefore NOT
   offline. Rules + Ollama remain the floor everywhere; Haiku (if adopted)
   is a QTH-only middle rung that DEGRADES to Ollama when the WAN dies
   (fallback chain, not replacement).
3. **The wired weekly cron does not change in this session** — it gates the
   PRODUCTION brain (Ollama, `--gate 0.85`). Haiku runs are manual
   `--backend anthropic` invocations until an adoption decision, and
   adoption/wiring is a SEPARATE step (canary-first, per
   feedback_version_env_rigor).
4. `brain_tier` stamping: any artifact a Haiku backend produces must carry a
   distinct tier marker (e.g. `brain_tier: "api_small"` — NOT `local`, which
   would poison the tier-L calibration history; NOT absent). Grade-time
   checks in the harness assert `brain_tier == "local"` today
   (`local_brain_eval.py:235,297,303`) — those assertions must become
   backend-aware, not deleted (hfm #7: closed enums need closed consumers).

## Implementation sketch (the session's worklist)

1. **`AnthropicBackend`** — same seam as `OllamaBackend`
   (`chat_compiler.py:90`): `complete(system, user, fmt="json") -> str`,
   raising `CompilerError` on transport/shape failure (never a fake reply).
   - `anthropic` is an EXTERNAL dep → `safe_import` (CLAUDE.md rule), and it
     is NOT in requirements — backend must fail LOUD-and-clean when absent
     (CompilerError "anthropic sdk not installed"), CI minimal-deps must
     stay green (see `04da8c18` for the env-dependent-test lesson).
   - Model `claude-haiku-4-5` (verified current 2026-07-21 via the
     claude-api reference; 200K context, $1/$5 per MTok). Plain
     `client.messages.create`, `max_tokens` ~2048, no thinking config
     (Haiku 4.5 thinking needs `budget_tokens` — skip it), temperature OK on
     this model tier if parity with Ollama's 0.2 is wanted. `fmt`: when a
     JSON-Schema dict is passed, prefer `output_config={"format":
     {"type": "json_schema", "schema": ...}}` (supported on Haiku 4.5);
     plain `"json"` mode → instruct via system prompt + consumer-side
     validation (which the harness already does).
   - Key resolution: `ANTHROPIC_API_KEY` env only (matches
     `claude_assistant.py`); missing key → loud CompilerError, never a
     silent skip. Timeout bound (LOCAL_BRAIN_TIMEOUT_S analogue).
2. **Harness knob** — `local_brain_eval.py` gains `--backend
   {ollama,anthropic}` (+ `--anthropic-model`, default claude-haiku-4-5).
   The results-ledger record must carry the backend + model identity so the
   two calibration histories never blend (re-derive, never patch: the
   weekly-gate reader must keep reading ONLY ollama records — check
   `watchdog` / warm-brief consumers of `local_brain_evals.jsonl` before
   changing its schema; additive keys only).
3. **Run the head-to-head** — full case set, both backends, ≥2 runs each
   (pass@1 ≠ reliable). Cost note: 33 cases × small prompts on Haiku is
   well under $1/run.
4. **Decision gate** (write the verdict into this file + the eval ledger):
   - Haiku pass_rate ≤ Ollama + 5pts → REJECT the rung; close this plan
     (negative result is a first-class outcome; the local tier is
     sufficient).
   - Haiku clearly wins (≥ +10pts or fixes the 2 flapping oracle cases) →
     queue a SECOND session for wiring: `cadence_fallback --backend
     anthropic` as the preferred fallback at the QTH with Ollama as its
     own fallback (chain: haiku → ollama → rules), witness key extended,
     `MINI_DUDEAI_*` env knobs, canary on VolcanoAI only.
   - In between → operator call; present both numbers.
5. **Tests** — backend unit tests (mocked transport: shape failure, missing
   sdk, missing key, happy path), harness backend-selection test, ledger
   identity-key test. Red-first where pinnable.

## Out of scope for this session

Wiring into any cron/cadence; MA port (no mini on MA boxes); touching the
weekly gate; any always-on API loop. Adoption is step 4's SECOND session.

## Cold-start pointers

`src/mini_dudeai/local_brain_eval.py` (harness), `chat_compiler.py:90`
(backend seam + CompilerError), `cadence_fallback.py` (eventual consumer),
`evals/local_brain/seed.jsonl` (cases), weekly cron: `crontab -l | grep
local_brain_eval` (gate 0.85, Sun 03:25). Memory:
`project_fable5_window_plan_2026_07_16.md` 07-21 addendum (the gradient +
sanction). API facts current as of 07-21 — re-check model id via
`client.models.retrieve("claude-haiku-4-5")` if months have passed.
