# Why the Calibration Spine — the research behind it

> Companion to the **calibration spine** (shipped 2026-06-15): the discipline for
> what Claude *claims* about its own work, moved out of the model's disposition
> and into the harness. Code:
> - `.claude/rules/calibrated_claims.md` — the write-time discipline (evidence tiers)
> - `scripts/claim_gate.py` — the reflective Stop hook (one beat, fails open)
> - `src/mini_dudeai/calibration_ledger.py` — the ledger (records claims, re-derives)
> - `scripts/calibration_reverify.sh` — the daily re-derivation (pass^k re-run)
> - `calibration_drift` — the watchdog signal that surfaces a broken claim
>
> MeshAnchor carries the portable core (rule + gate).

## The problem this answers

The operator's words: *"honesty is not enough when honesty is a house of cards…
when you say 100% and we do it N more times, the math is wrong."* And: mid-session
the underlying model swapped (Fable 5 → Opus) and the behavior lurched.

Both observations point at one root cause — **miscalibration, not dishonesty.**
Claude wasn't lying; its *stated certainty* didn't match its *actual* success rate,
and any honesty that lives in the model's disposition collapses the next time the
model changes. The research below says this is exactly what to expect, and points
at the only fixes that hold. Each finding is paired with the spine mechanism it
justifies — the design is downstream of the evidence, not the other way around.

## What the research says — and the mechanism each finding justifies

### 1. LLM overconfidence is structural, not a willpower problem
Post-training (instruction-tuning + RLHF) systematically *degrades* calibration —
base models are better-calibrated than their instruction-tuned descendants. The
gap between stated confidence and real accuracy is stable, reproducible, and
domain-independent. "Try harder to be accurate about your confidence" does not
work, because the overconfidence is learned behavior, not a lapse.

**→ Mechanism.** You cannot fix a dispositional defect with disposition. The rule
and the gate live in the **harness** — a Stop hook that fires regardless of which
model is driving. Calibration becomes a property of the *environment*, not the
model's mood.
*Sources: "Mind the Confidence Gap" (arXiv 2502.11028); "Dunning-Kruger in LLMs"
(2603.09985); "Uncertainty Calibration in Long-Form QA" (2602.00279).*

### 2. Agents can't reliably self-verify — external ground truth beats self-critique
Test execution closes the generator–verifier gap far better than any self-critique
loop. LLM-as-judge has been measured with >50% error rates, driven by position,
length, and agreeableness biases. Deterministic graders and maintainer test suites
are the gold standard; the verifier gap (~40–60% task success on modern benchmarks)
persists and **cannot be prompted away**.

**→ Mechanism.** The gate is satisfied only by an **external, unfabricatable**
check (an `honest_status` verdict, a captured `pytest` exit) — never by Claude's
own say-so. The re-derivation cron *re-runs the suite* rather than asking the model
whether it still passes. `calibration_drift` keys on code (pytest+lint), not on
judgment.
*Sources: "AI IDEs or Autonomous Agents?" (2601.13597); "Guideline-Grounded Evidence
Accumulation for High-Stakes Agent Verification" (2603.02798); "Are Coding Agents
Generating Over-Mocked Tests?" (2602.00409); "Professional Software Developers Don't
Vibe, They Control" (2512.14012).*

### 3. "It worked once" overstates the true rate — pass@1 vs pass^k
LLM outputs are non-deterministic even at temperature 0 (sampling, floating-point
precision, parallel execution order). A single success is noise; credible success
estimates need multiple runs. Reported benchmark variance is often *larger* than
the improvements models claim over prior versions.

**→ Mechanism.** The rule says "it worked once" is **BELIEVED**, not VERIFIED,
unless reproduced (≥2 runs / determinism established) or root-caused. The daily
`calibration_reverify.sh` is the literal *"do it N more times"* — it re-runs the
verification on a head previously called green and flips the claim to *broke* if
the green doesn't hold.
*Sources: "Understanding and Mitigating Numerical Sources of Nondeterminism in LLM
Inference" (2506.09501); "Towards Reproducible LLM Evaluation" (2410.03492); "The
Non-Determinism of 'Deterministic' LLM Settings" (ACL 2025.eval4nlp-1.12); "LLM-42:
Enabling Determinism…" (2601.17768).*

### 4. Behavior shifts across model versions — encode discipline in scaffolding
The same prompt on a different model version produces meaningfully different
behavior. These consistency gaps are rooted in architecture and training, not
prompt phrasing — so prompt-engineering a model into calibration does not transfer
when the model changes. Scaffolding (external verification) is the resilient layer.

**→ Mechanism.** The entire spine is hooks + rule files + ledger — it survives a
model swap by construction. And the ledger records the **`model_id`** of every
claim, so a reliability shift *after* a swap (the Fable 5 → Opus lurch) becomes a
visible, attributable number instead of an invisible drift.
*Sources: "Consistency in Language Models: Current Landscape…" (2505.00268);
"Language Models Exhibit Inconsistent Biases…" (2602.22070); "Consistency of
Responses and Continuations…" (2501.08102).*

### 5. Overconfident AI erodes trust — calibrated confidence enables calibrated reliance
When an AI expresses confidence it later violates, human trust degrades *durably* —
users swing to under-reliance and discount the AI even when it's right ("boy who
cried wolf"). The corrective is real-time, ground-truth feedback on AI reliability;
transparency *without* calibration backfires (explanations read as post-hoc
rationalization).

**→ Mechanism.** The ledger turns "you said 100%" from a private impression into a
**measured held/broke ratio** surfaced in the warm brief. And — deliberately —
`calibration_drift` stays a quiet /fleet signal with **no page** until it soaks
low-false-positive: paging on early false positives would re-create the very
cry-wolf dynamic the spine exists to end.
*Sources: "Calibrating Reliance on Automated Advice" (tandfonline
10.1080/10447318.2025.2487861); "Trust in AI emerges from distrust in humans"
(2511.16769); "Trust Formation… in Human–AI Financial Advisory" (PMC12561693);
"Adaptive Cognitive Mechanisms to Maintain Calibrated Trust" (PMC8181412).*

### 6. Abstention is a reliable output — and must be scaffolded, not hoped for
Models can *verbalize* uncertainty yet still answer confidently; uncertainty and
correctness are encoded by *different* internal features, so a model won't reliably
abstain just by being asked. Selective abstention measurably improves accuracy when
it's an explicit, supported behavior.

**→ Mechanism.** The rule makes **UNKNOWN** and **BELIEVED** first-class outputs:
"I don't know yet — here's the check that would tell us" is a *good* answer, not a
failure. The gate's exempt path explicitly passes honest hedging.
*Sources: "Teaching LLMs to Abstain via Fine-Grained Semantic Confidence Reward"
(2510.24020); "Are LLM Decisions Faithful to Verbal Confidence?" (2601.07767);
"Are LLM Uncertainty and Correctness Encoded by the Same Features?" (2604.19974).*

## The honest limit

None of this makes Claude *correct* — the literature is explicit that calibration
improves but never reaches zero error. The spine doesn't promise fewer mistakes. It
promises that mistakes **stop masquerading as certainty**, that the discipline is
**durable** (in the harness, not the mood) and **model-agnostic** (survives swaps),
and that the miscalibration becomes a **visible number that can shrink**.
Better-calibrated, not better. That distinction is the entire point.

## Provenance (the calibrated-claims discipline, applied to this doc)

The *findings* above are well-established across the 2023–2026 literature, and are
stated with confidence. The *specific arXiv IDs / URLs* were surfaced by the
deep-research fan-out on 2026-06-15 (web search + fetch by a research subagent) and
have **not each been re-fetched** while writing this doc. Treat the citations as
**BELIEVED-accurate, not VERIFIED-each** — confirm every link before any external
publication (e.g. a substack post). Fitting: a writeup about not dressing a guess as
a guarantee should not present its own references as more certain than they are.
