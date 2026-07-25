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

### STEP 0 (PREREQUISITE — do not skip, do not reorder)

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

### STEP 1 — make tier-R primary for triage

The decision "does this assert system behaviour, and does retrieval contradict it?"
is largely deterministic. `cadence_fallback` **already** degrades honestly to
`brain_tier: rules` with a "backlog pending, untriaged" note when the LLM fails —
promote that path from fallback to DEFAULT, and run any model as an optional
second opinion.

Measure both against the balanced suite from Step 0. Candidate deterministic
signals (all cheap, all inspectable):
- retrieval returns a chunk whose text contradicts the proposal's claim → not ratifiable
- the summary asserts behaviour/causation ("so", "therefore", "conclude",
  "impossible", "correctly") → `needs-live-check`
- retrieval returns nothing → `needs-live-check` (absence is not corroboration —
  already the shipped prompt's rule, make it the code's rule)

⚠️ **The honest risk:** a heuristic tuned to today's cases is brittle on shapes
nobody anticipated. That is exactly what a model generalises over. So Step 1's exit
criterion is the BALANCED suite, not the five cases that motivated it — and if
tier-R only passes by memorising them, that is a fail, not a win.

### STEP 2 — decide about a model, with data

- **tier-R passes the balanced suite** → done. No ollama, no llama.cpp, no model.
  Continuity runs fleet-wide including moc3.
- **tier-R fails specific cases** → those cases tell you what KIND of model. Most
  likely a small **discriminative classifier**, not a chat model: the task is
  classification and the labels already exist — the dream-proposal record has
  **80 reviewed / 19 ratified with rejection reasons**, plus the calibration ledger's
  47 claims with held/broke outcomes. TF-IDF + logistic regression or a ~100 MB
  encoder; tens of MB, CPU, retrainable on the fleet.

### STEP 3 — llama.cpp, only if a generative tier survives Step 2

And only for **oracle synthesis** — the one place tonight showed a model genuinely
earning its cost (4B 3/3 vs 1.5B 1/3, the 1.5B failures being "answer contains none
of [`memory.max`, `cgroup`, `daemon-reexec`]" — it could not carry the specifics
that were in the retrieved excerpts).

Integration seam **already exists**: `local_brain_eval --backend {ollama,claude-cli}`.
Adding `llamacpp` is a bounded change, and the eval ledger is how you prove the lean
runtime is not worse — same cases, same grading, three backends on evidence.

Config targets: reuse the GGUF ollama already downloaded, `-c 2048` (not 4096),
`--cache-type-k/v q8_0`, mmap. Expect **~1.2–1.6 GB total** for 1.5B — fits inside
moc2's 2,560 MB and moc1's 2,816 MB user caps. Not moc3.

⚠️ Needs a cmake build on the box (real CPU/RAM/time) and becomes a dependency you
own. Smaller than a model fork, not free.

## Explicitly REJECTED, with reasons

| option | why not |
|---|---|
| **Train/fine-tune our own LLM** | You already own two hard forks (RNS, LXMF) and know the arithmetic — governance triggers, upstream tracking, interop proofs per roll. A bespoke model is a third fork with worse tooling, **no wire-compat analogue to keep it honest**, and no independent way to prove it has not regressed except an eval ledger you would have to build anyway. Revisit only if Step 2 shows a gap that constraint and a classifier cannot close. |
| **Build a retrieval index** | **Already built.** `offline_oracle` has lexical BM25 over `persistent_issues*` + the memory dir, and `--retrieve-only` is documented as *"deterministic (tier R) and needs no LLM at all."* Step 1 is WIRING, not building. (I proposed building it before checking — do not repeat that.) |
| **Add a probe to watch the observer's cost** | The recursion the operator warned about. `probe_host_memory_pressure` already names top RSS consumers and top cgroups, so a bloating watchdog surfaces there for free. |
| **Lower the rtun bounce threshold** (adjacent) | 115 `TUN_DEAD` in 3 days with only 4 bounces: 111 self-healed inside one 3-min cycle. A threshold of 1 means ~38 restarts/day. The flap interval is the defect, not the threshold. |

## What "done" looks like

1. Balanced eval suite exists; both directions measured for every candidate tier.
2. Triage runs deterministically by default, grounded in retrieval, on **all nine
   boxes** — moc3 included.
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
  triage ones that flipped 0/3 → 3/3 are the red tests for grounding).
- Memory: `project_volcanoai_reset_8_memory_pressure_2026_07_24`,
  `feedback_my_footprint_is_the_constraint`.
- Rule this serves: `honest_failure_modes` #10 — a resolved incident owes an eval
  case to tier-L, or its competence on the class stays permanently BELIEVED.
