# Local tier: ditch ollama — and probably the generative model too (2026-07-25)

> Operator (07-24, after a night of measuring): *"i would say ditch ollama and the
> models - try something else... this is about 2nd brain and you the frontier model
> coming by this domain... context - persistence - continuity to name a few"*
> and *"never been a fan of ollama - too much bloat - prefer building our own,
> focused for this domain."*
>
> Written to disk the same night the measurements were taken, because the session
> that has them will not exist tomorrow. Baseline: MF `702123a8` / MA `9f82530c`
> (grounding shipped, both pushed, both suites green, parity in sync).

## Why this plan exists at all

The second brain is the **continuity layer** — the thing that persists while
frontier models rotate through (Opus 4.8 → Fable 5 → Opus 5, each with its own
new mistakes). Tonight measured how well its two halves work, and they want
opposite things. ollama forces one runtime to serve both.

## The evidence base (all measured 2026-07-24, keep these numbers)

**Model comparison, 8 cases, same suite, one variable:**

| | triage (refuse a wrong conclusion) | oracle (synthesise from excerpts) | speed | ollama RSS |
|---|---|---|---|---|
| `qwen3:4b-instruct-2507-q4_K_M` | **2/5** ungrounded → **3/3** grounded | **3/3** | 96–281 s/case | 6,643 MB peak |
| `qwen2.5:1.5b-instruct` | **5/5** ungrounded | **1/3** | 10–71 s/case | 4,978 MB |

> ⛔ **THE TRIAGE COLUMN IS MISLEADING AND STAYS ONLY AS THE RECORD.** Every
> triage case behind it graded the REFUSE direction, so 1.5B's 5/5 measured
> caution, not judgement. Step 0 ran the balanced suite on 2026-07-25 and the
> ranking **inverted**: 4B **4/6**, 1.5B **0/6**. Do not cite the 5/5 again
> except as an example of what a one-directional scoreboard does.

**Grounding, same model + same cases (the shipped fix, `702123a8`):** ungrounded 4B
rated 3 of 5 deliberately-wrong proposals `looks-ratifiable`; grounded it got those
same three **3/3 on the first try** (it had been burning all 3 retries). The facts
refuting them were in the memory store the whole time. **Tier-R BM25 found them with
no LLM involved.**

**ollama's overhead:** model reported 3,503 MB at a 4096 context, CPU-only; cgroup
6,290–6,821 MB. **~2,800 MB (44%) is runtime, not weights.**

**The eval corpus is retrieval-shaped:** of the oracle expectations across all 55
cases, 28 are `retrieve_must_include` and 30 `answer_contains_any`, only 5
`cite_must_include`. Over half the suite grades **search**, not generation.

## The argument that decides it

**moc3 has 905 MB of RAM.** No LLM fits — not 4B, not 1.5B, not under llama.cpp.
So any design where the local tier *requires* a model gives continuity a hardware
floor a third of the fleet sits below. Today the second brain's judgment layer
lives on exactly one box: the manager, which has hard-reset eight times.

A rules+retrieval tier runs on **all nine**. That is not a performance argument —
it is the difference between a continuity layer and a single point of failure, and
it is what *"best in class MOC that runs mini dudeai, standalone, fleet reliably"*
actually requires.

## Ordered plan

### STEP 0 — ✅ DONE 2026-07-25 (`7607ceed`, CI 4/4 green)

**Deliverable shipped**: `evals/local_brain/ratifiable_direction_2026_07_25.jsonl`
— 6 cases, 4 single-delta and 2 mixed. Three corpus-level guards in
`tests/test_local_brain_eval.py::TestTriageCorpusMeasuresBothDirections` pin the
PROPERTY (the suite must assert the ratifiable direction; a refuse-everything
stub must not pass everything; some case must demand both directions at once),
all verified RED with the file moved aside.

**The asymmetry was worse than this plan said.** Re-derived on the live corpus:
not five cases in one file — **all 15 triage cases, and all 8 of their
disposition assertions**, permitted only `needs-live-check`/`looks-rejectable`.
A stub answering `needs-live-check` to everything scored **15/15**. Corpus now:
21 triage cases, 17 assertions, 6 permitting ratification; the stub is down to
15/21.

**Fairness was gated, not assumed.** `looks-ratifiable` is only correct when the
shipped prompt's own rule is met, so every ratifiable-pinned delta had to surface
its corroborating phrase INSIDE the 700-char clamp the model actually reads,
under both repo-only roots (every fleet box) and default roots (manager box).
Two candidates were **dropped** for failing exactly that — `durations_forgeable_rtc_less`
and `rnprobe_is_not_a_delivery_test` ranked the right file but the corroborating
sentence fell outside the window. Ranking the file is a representation; the
clamped text is the thing. The gate was then re-run against the AUTHORED file,
because the `SYNTHETIC drill:` prefix is part of the retrieval query.

**Also closed**: `dispositions` values were never validated against the
vocabulary, so a typo'd disposition would make a case permanently unpassable
while reading as a model failure (hfm #3). Latent until these became the first
cases to type `looks-ratifiable` at all.

#### 📊 THE MEASUREMENT — both models, same 6 cases, 2026-07-25

| case | 4B | 1.5B |
|---|---|---|
| `triage-ratifiable-documented-lint-rule` | ✅ try 1/3 | ❌ 3/3 |
| `triage-ratifiable-corroborated-incident` | ✅ try 1/3 | ❌ 3/3 |
| `triage-ratifiable-decision-tell` | ❌ 3/3 | ❌ 3/3 |
| `triage-ratifiable-calibration-discipline` | ✅ try 1/3 | ❌ 3/3 |
| `triage-mixed-ratifiable-and-wrong` | ❌ 3/3 | ❌ 3/3 |
| `triage-mixed-three-way-dispositions` | ✅ **try 1/3** | ❌ 3/3 (coverage 2/3) |
| **total** | **4/6 (0.667)** | **0/6 (0.0)** |

- **4B discriminates.** The hardest case — all three dispositions in one
  backlog, two deltas retrieving the SAME chunk read in opposite directions —
  passed on the **first attempt**. Four of its passes were first-try, so no
  best-of-N masking.
- **1.5B is the refuse-everything ratifier**, measured. Every case,
  `needs-live-check`, all three attempts. It also **dropped a delta entirely**
  on the 3-delta backlog (coverage 2/3) — an incompleteness failure independent
  of disposition, and the one thing the OLD suite did test.
- **The ranking is robust to the contested calls below**: granting both would
  make it 4B **6/6** vs 1.5B **2/6**. Same conclusion either way.
- 4B ~32 min for 6 cases (the two failures burned all 3 attempts); 1.5B ~7 min.
  Sequential by construction, `ollama stop` between models — never two resident
  (~11.6 GB against ollama's 8 G cap on a box that reset from memory pressure).

#### ⚖️ OPEN DECISION — where the ratifiable line sits (operator's call)

4B's two failures are the same shape: it answered `needs-live-check` to exactly
the deltas asserting a **causal mechanism** ("`[Errno 24]` *means* an fd leak";
"the loader dropped it, *so* every restart erased it"), while ratifying the four
that assert a static fact or policy. Both mechanisms are corroborated verbatim
in the corpus.

So: does corroboration make a mechanism-claim ratifiable (this file's current
expectation), or does asserting mechanism always warrant a live check? Both are
defensible readings of the shipped prompt. ⚠️ Resolving it in the cases' favour
also happens to clear the gate risk below — **which is precisely why the gate
must not be the reason.** Decide on merits; do not loosen a case to raise a
number, or this whole file was pointless.

> ✅ **RESOLVED 2026-07-25 — operator: "corroboration makes it ratifiable."**
> The cases stand as written; nothing was loosened. Consequences, both larger
> than the dispute itself:
> 1. **4B's gap is on the memories that matter most.** It refuses corroborated
>    mechanism-claims — decision tells, root causes, "X means Y, restart Z" —
>    which is the dominant shape an incident compiles down to. 4/6 understates
>    it; the two it missed are the class the second brain exists to accumulate.
> 2. **Step 1's signal list was wrong as written** and is now ORDERED (see
>    below). Flat, it reproduces 4B's error.
>
> The gate call follows from this: **let Sunday's `--gate 0.85` fire, do not
> touch the threshold.** The FAIL is TRUE against the standard just adopted, and
> moving a bar the same day the standard tightened is scoreboard-tuning by
> definition. Verified from the seed rule rather than assumed: a failing cron
> verdict is `propose_escalation`, `cooldown_s 21600`, annotated *"NO ntfy page
> (degraded, not an outage)"* — so it lands in mini's brief for the next
> session, beside this file, which is the continuity layer doing its job.

#### ⚠️ GATE RISK — Sunday 03:25 `--gate 0.85`

The new cases join the weekly cron automatically via the `*.jsonl` glob. Under
4B they score 0.667, so depending on where `--cursor` lands an honest FAIL is
likely. The 0.85 threshold was calibrated on a suite where refusing was FREE;
the measurement changed, so the bar's meaning changed with it. Recalibrating
deliberately is legitimate — quietly lowering it to go green is not, and it is
the same category error as the model-unaware regression probe below.

---

<details><summary>Original Step 0 statement, kept for the record</summary>

**Fix the eval suite's asymmetry.** All five triage cases in
`evals/local_brain/reset8_caps_2026_07_24.jsonl` test the REFUSE direction. A
system answering `needs-live-check` to everything scores 5/5 and is useless —
it would block every legitimate memory from ever being ratified.

⚠️ **This means 1.5B's 5/5 is NOT yet a verdict on model choice.** It is consistent
with genuine discrimination AND with indiscriminate caution, and nothing currently
distinguishes them.

Deliverable: ratifiable-direction triage cases (proposals that SHOULD earn
`looks-ratifiable` — a clean numeric observation, a duplicate correctly identified,
a transient correctly dismissed). Then re-run 4B and 1.5B. **Until this exists, no
triage change — rules or model — can be validated.** Optimising against a
one-directional scoreboard is how you ship a refuse-everything ratifier.

</details>

### STEP 1 — make tier-R primary for triage

> 🛑 **PREMISE CORRECTED 2026-07-25 (measured) — "largely deterministic" is
> TRUE for refusing and FALSE for ratifying.** Rungs 1 and 3–4 below are cheap
> and lexical. **Rung 2 is not implementable with cheap features**, and that was
> measured, not guessed:
>
> | signal | my 6 synthetic cases | 80 real dream-record labels |
> |---|---|---|
> | longest shared n-gram ≥ 5 | precision **1.00** | precision **0.33** (base rate 0.25) |
> | claim↔excerpt word coverage | ranges overlap | ranges overlap (0.49–0.77 vs 0.31–0.79) |
>
> The n-gram "corroboration tell" was an **artifact of my own authoring** — I
> selected the Step 0 cases by requiring a corroborating phrase in the excerpt,
> so a phrase-matching rule passed them by construction. On labels produced by a
> different process over months it is barely above chance. Deciding that a record
> *corroborates* a claim rather than merely *concerns the same topic* is a
> semantic judgement; BM25 ranks topic, not agreement. Every deliberately-wrong
> proposal in the corpus is wrong ABOUT A TOPIC THE CORPUS COVERS, so topical
> similarity actively misleads here.
>
> ⚠️ **The consequence, and it is the plan's turning point: a tier-R-ONLY brain
> can refuse but cannot ratify — which is exactly 1.5B's failure mode reached by
> a different road.** Pure tier-R is not a lean second brain; it is a
> refuse-everything ratifier with no model to blame. See the SPLIT architecture
> in Step 2.
>
> Limit on this result, stated not buried: the dream record's `ratified`/
> `rejected` is not identical to a triage disposition (a true, corroborated
> proposal is still rejected as `duplicate` or `already_fixed`). So it is a
> PROXY — strong evidence against the specific tell, weaker evidence that no
> corroboration feature exists at all. Method:
> `scratchpad/dream_separability.py` pattern — dedupe by key first (66 of 146
> records are re-proposals; mini's brief was right to count 80).

The decision "does this assert system behaviour, and does retrieval contradict it?"
is largely deterministic **on the refuse side**. `cadence_fallback` **already**
degrades honestly to `brain_tier: rules` with a "backlog pending, untriaged" note
when the LLM fails — promote that path from fallback to DEFAULT for REFUSAL, and
keep a semantic tier for ratification.

Measure both against the balanced suite from Step 0.

#### 🔢 THE SIGNALS ARE ORDERED — the precedence IS the design

⚠️ **This list was flat until 2026-07-25 and was WRONG that way.** Operator
ruling the same day: **corroboration makes a mechanism-claim ratifiable.** A
corroborated claim that asserts causation satisfies BOTH "retrieval corroborates"
and "asserts behaviour", so an unordered rule set resolves it arbitrarily — and
resolving it toward caution reproduces exactly the error 4B made on two of six
cases. Evaluate in this order and stop at the first match:

1. **retrieval CONTRADICTS the claim → not ratifiable.** The fleet's own records
   outrank the proposal's reasoning (already the shipped prompt's wording).
2. **retrieval CORROBORATES the claim → `looks-ratifiable`, even if it asserts a
   mechanism.** ← the operator's ruling; this rung must come BEFORE rung 3 or the
   tier refuses precisely the memories the second brain exists to accumulate.
3. **asserts behaviour/causation** ("so", "therefore", "conclude", "impossible",
   "correctly") **and rung 2 did not fire** → `needs-live-check`.
4. **retrieval returns nothing → `needs-live-check`.** Absence is not
   corroboration — the shipped prompt's rule, made the code's rule.

Rungs 3 and 4 are the ones that must never be reordered above 2. Note the shape:
this is honest_failure_modes #2 in policy form — "no corroborating record found"
and "records contradict this" must not collapse into one answer.

📊 **FIRST EVIDENCE, 2026-07-25 — and it is evidence FOR tier-R, not for the
model.** 4B failed exactly the two deltas asserting a corroborated mechanism and
ratified exactly the four asserting a static fact or policy. Under the ruling
above that split is a **precedence error**: 4B applied rung 3 where rung 2 should
have fired. A rule with an explicit order does not make that mistake, so tier-R
written to this precedence should score **6/6 where 4B scored 4/6** — beating a
2.5 GB model that costs 96–782 s/case, on any box including moc3, with no ollama
and no model resident.

⚠️ That prediction is UNTESTED — it is the cheapest experiment left in this plan
and the honest next step, not a result. Two failures is a hypothesis, not a law:
the exit criterion is the BALANCED suite, not the two cases that motivated the
ordering. **Do not imitate 4B's behaviour as a specification** — that was the
original flat list's mistake, encoded from watching a model instead of deciding
a policy.

> ❌ **THE 6/6 PREDICTION IS WITHDRAWN (same day, before anyone built on it).**
> It assumed rung 2 was free. It is not — see the corrected premise at the top of
> this step. A tier-R written to this precedence would score well on the six cases
> **because I authored them against a phrase gate**, and that number would mean
> nothing. The precedence above is still right as POLICY; what is missing is a
> way to COMPUTE rung 2. Do not build a ratifier to hit 6/6.

⚠️ **The honest risk:** a heuristic tuned to today's cases is brittle on shapes
nobody anticipated. That is exactly what a model generalises over. So Step 1's exit
criterion is the BALANCED suite, not the five cases that motivated it — and if
tier-R only passes by memorising them, that is a fail, not a win.

### STEP 2 — decide about a model, with data

🚫 **`qwen2.5:1.5b-instruct` IS DISQUALIFIED as a triage tier (2026-07-25).**
0/6 on the balanced suite, every case, all three attempts, plus a dropped delta.
It is not "weaker but cheaper" — as a ratifier it is structurally useless, and
its apparent 5/5 advantage was an artifact of a one-directional scoreboard. This
also removes "just run the smaller model" as the cheap way out of the moc3
hardware floor: the small model does not do this job at all. The floor argument
stands on its own — a rules+retrieval tier runs on all nine boxes.

#### 🧭 THE SHAPE TO AIM AT — a SPLIT, not a replacement (revised 2026-07-25)

The measurement in Step 1 says refusal and ratification are different problems
with different hardware floors. So stop looking for one tier that does both:

| direction | tier | runs on | why |
|---|---|---|---|
| **REFUSE** (contradiction, behaviour-without-corroboration, absence) | **R** — rules + BM25 | **all nine boxes, moc3 included** | cheap, lexical, inspectable, measured to work |
| **RATIFY** (does this record corroborate this claim?) | semantic — classifier or model | wherever one fits | measured NOT to work lexically; BM25 ranks topic, not agreement |

This keeps the property that actually matters fleet-wide — **a wrong memory is
refused everywhere, including on the 905 MB box** — while conceding that
*admitting* a memory needs more than BM25. It is a weaker claim than "no model
at all", and it is the one the evidence supports.

⚠️ Note what this does NOT rescue: a box that can only refuse contributes no new
memory. If ratification is centralised on the manager, then the manager's eight
hard resets are again a single point of failure for MEMORY GROWTH (not for
memory integrity). Decide that consciously; do not let it happen by default.

- ~~**tier-R passes the balanced suite** → done. No ollama, no llama.cpp, no
  model.~~ **UNLIKELY as of 2026-07-25** — this branch needed rung 2 to be
  computable and it is not. Kept struck-through rather than deleted: if someone
  later finds a cheap corroboration feature that holds on the dream record
  (precision ≫ 0.25 base rate), this branch reopens and Step 3 never runs.
- **tier-R fails specific cases** → **THIS IS THE LIVE BRANCH.** Those cases tell
  you what KIND of model. Most
  likely a small **discriminative classifier**, not a chat model: the task is
  classification and the labels already exist — the dream-proposal record has
  **80 unique-key proposals / 20 ratified** (146 records before dedupe; dedupe by
  key FIRST), plus the calibration ledger's
  47 claims with held/broke outcomes. TF-IDF + logistic regression or a ~100 MB
  encoder; tens of MB, CPU, retrainable on the fleet.

  ⚠️ **The training set is THIN — say so before fitting anything.** 80 unique
  keys, 20 positives, class-imbalanced 1:3. A classifier will look good on 80
  rows and that number will not be trustworthy. Before fitting: hold out by TIME
  (train on older proposals, test on newer) rather than at random, so the score
  cannot be inflated by near-duplicate keys from the same incident. And note the
  label means "should this enter memory", which folds in `duplicate` and
  `already_fixed` — related to corroboration, not identical to it. Growing the
  label set may be the prerequisite, not the model.

### STEP 3 — llama.cpp, only if a generative tier survives Step 2

And only for **oracle synthesis** — the one place tonight showed a model genuinely
earning its cost (4B 3/3 vs 1.5B 1/3, the 1.5B failures being "answer contains none
of [`memory.max`, `cgroup`, `daemon-reexec`]" — it could not carry the specifics
that were in the retrieved excerpts).

Integration seam **already exists**: `local_brain_eval --backend {ollama,claude-cli}`.
Adding `llamacpp` is a bounded change, and the eval ledger is how you prove the lean
runtime is not worse — same cases, same grading, three backends on evidence.

✅ **FLAW FIXED 2026-07-25 (`1332ae49`, local — not yet pushed).** `probe_local_brain_regressed`
now scopes per-case history to the model of the most-recent run. 5 tests (4 verified RED
against the pre-fix probe); live-smoked clean on the real ledger. The near-miss below was
**re-derived from the ledger** during the fix and holds exactly as written. Backend is
deliberately NOT part of the key — same model on a leaner runtime is still held to what it
could do, which is the Step-3 llama.cpp signal. A model swap resets the baseline (quiet
until 2 passes accumulate: under-fires, never false-pages). **Steps 2 and 3 are now
measurable.** Original finding, kept for the record:

⚠️ **FLAW IN THIS PLAN, found the night it was written — fix before Step 2/3.**
`probe_local_brain_regressed` compares each case's pass/fail history and **does NOT
key on model**: it fires when the latest run failed and `>= min_prior_passes` (2)
earlier runs passed. So the multi-backend comparison this plan depends on will
manufacture false regressions — a case passing twice under 4B then failing under
1.5B is scored as "the local tier lost a capability it demonstrably had", when it
is just a different model.

Tonight escaped it only by luck of arithmetic: the two cases that went PASS(4B) →
FAIL(1.5B) had exactly **1** prior pass against a threshold of 2, verified from the
ledger. One more comparison run and it would have paged.

Fix first (cheap): make the regression comparison model-aware — key the history by
`(case_id, model)` or ignore records whose model differs from the latest. Otherwise
Step 2 and Step 3 cannot be measured without poisoning the very signal that is
supposed to protect tier-L competence.

Config targets: reuse the GGUF ollama already downloaded, `-c 2048` (not 4096),
`--cache-type-k/v q8_0`, mmap. Expect **~1.2–1.6 GB total** for 1.5B — fits inside
moc2's 2,560 MB and moc1's 2,816 MB user caps. Not moc3.

⚠️ Needs a cmake build on the box (real CPU/RAM/time) and becomes a dependency you
own. Smaller than a model fork, not free.

## Explicitly REJECTED, with reasons

| option | why not |
|---|---|
| **Train/fine-tune our own LLM** | You already own two hard forks (RNS, LXMF) and know the arithmetic — governance triggers, upstream tracking, interop proofs per roll. A bespoke model is a third fork with worse tooling, **no wire-compat analogue to keep it honest**, and no independent way to prove it has not regressed except an eval ledger you would have to build anyway. Revisit only if Step 2 shows a gap that constraint and a classifier cannot close. |
| **A lexical "does retrieval corroborate this?" rule** | **MEASURED DEAD 2026-07-25.** Longest-shared-n-gram ≥5 scored precision 1.00 on the 6 cases I authored and **0.33 on 80 real dream-record labels** (base rate 0.25); claim↔excerpt word coverage does not separate the populations at all. BM25 ranks TOPIC, and every deliberately-wrong proposal is wrong about a topic the corpus covers — so topical similarity actively misleads. Reopen only with a feature that beats base rate on labels nobody in the session authored. |
| **Build a retrieval index** | **Already built.** `offline_oracle` has lexical BM25 over `persistent_issues*` + the memory dir, and `--retrieve-only` is documented as *"deterministic (tier R) and needs no LLM at all."* Step 1 is WIRING, not building. (I proposed building it before checking — do not repeat that.) |
| **Add a probe to watch the observer's cost** | The recursion the operator warned about. `probe_host_memory_pressure` already names top RSS consumers and top cgroups, so a bloating watchdog surfaces there for free. |
| **Lower the rtun bounce threshold** (adjacent) | 115 `TUN_DEAD` in 3 days with only 4 bounces: 111 self-healed inside one 3-min cycle. A threshold of 1 means ~38 restarts/day. The flap interval is the defect, not the threshold. |

## What "done" looks like

1. ✅ **DONE 2026-07-25** — balanced eval suite exists (`7607ceed`); both
   directions measured for both candidate models (4B 4/6, 1.5B 0/6). Remaining
   under this item: measure **tier-R** on the same six.
2. **REFUSAL** runs deterministically by default, grounded in retrieval, on **all
   nine boxes** — moc3 included. **RATIFICATION** runs wherever a semantic tier
   fits, and the plan says out loud where that is. (Revised 2026-07-25: the
   original wording said "triage" for both directions, which the measurement
   showed is not achievable — see Step 1's corrected premise.)
3. ollama is either gone or demoted to an optional enrichment nothing depends on.
4. `local_brain_eval` ledger carries a backend-vs-backend comparison on identical
   cases, so the choice is evidence rather than preference.

## Footprint discipline (non-negotiable, from the same night)

Benchmark every candidate on **moc3**, never the manager. Tonight I measured a new
probe's cost at 10.4 ms on the Pi 5 and called it "negligible, 0.03% duty" — on
moc3 the watchdog tick already costs ~4,300 ms and the observer holds **7.1% of
RAM**. Same milliseconds, different meaning. See
`feedback_my_footprint_is_the_constraint`.

## Cross-references

- Shipped grounding: MF `702123a8`, MA `9f82530c` (byte-parity twins —
  `brief.py` + `cadence_fallback.py` are BOTH parity-tracked; I did not expect that
  and the full suite caught the drift).
- Eval cases: `evals/local_brain/reset8_caps_2026_07_24.jsonl` (8 cases; the 3
  triage ones that flipped 0/3 → 3/3 are the red tests for grounding) and
  `evals/local_brain/ratifiable_direction_2026_07_25.jsonl` (the Step 0
  balanced-direction set).
- Ledger evidence for the run above: `~/local_brain_evals.jsonl`, the two
  records stamped 2026-07-25 with `total: 6` (one per model). This was the
  first backend/model comparison run AFTER the regression-probe fix — and it
  immediately produced a **third** case sitting at `prior_passes=1` with a
  cross-model PASS(4B)→FAIL(1.5B) flip. Under the old un-scoped code, one more
  4B run tips those to 2 and pages falsely. The flaw was not hypothetical.
- Memory: `project_volcanoai_reset_8_memory_pressure_2026_07_24`,
  `feedback_my_footprint_is_the_constraint`.
- **Label set for Step 2**: `~/mini_dudeai_memory_deltas.jsonl` — 146 records,
  **80 unique keys after dedupe, 20 ratified / 60 rejected**. Real verdicts from
  a different process over months, which is why it — and not the synthetic eval
  corpus — is the honest test bed for any ratification signal. ⚠️ Dedupe by
  `key` FIRST; 66 records are re-proposals and counting raw records inflates
  both the total and the ratified count (I did exactly that on 2026-07-25 and
  mini's brief had it right).
- Rule this serves: `honest_failure_modes` #10 — a resolved incident owes an eval
  case to tier-L, or its competence on the class stays permanently BELIEVED.
