# Second-brain taxonomy — PREP for a frontier planning session

> **Status**: prep only. This is the material, not the design. It was built on
> an Opus-class session deliberately, so the rationed frontier pass spends
> itself on judgment rather than on discovery (`model_advisor.md`).
>
> **The question the planning session exists to answer**: "second brain"
> started meaning *mini-dudeai*. It now means **the set of organs that make
> claims about the domain**. There is no taxonomy, so we cannot answer
> *what watches X?*, *who would catch Y?*, or *is this organ any good?*
>
> **Built 2026-07-26.** Every count below was re-derived from the tree/live
> state this session, not recalled. Where a number is a live-state read it is
> marked LIVE (it will drift; re-derive before using it).

---

## 1. The organ inventory

Grouped by **what kind of claim the organ makes**, because that turns out to
be the axis that predicts everything else — who consumes it, what happens when
it is wrong, and whether anyone would notice.

### Tier R — deterministic detectors (always-on, no model)

| Organ | Scale | Emits | Consumed by |
|---|---|---|---|
| watchdog probes | **58 probes / 14 modules**, **57 signal classes** (closed enum, `watchdog_probe_core.SIGNAL_CLASSES`) | `clean` / `degraded` / `wedge` signals → `watchdog.json` | `fleet_truth_collector`, `watchdog_actions`, `oracle/snapshot`, mini's `json_file` source |
| `cron_verdict.sh` | every wired fleet cron | `OK` / `CONCERN` / `FAIL(n)` lines → `~/cron_verdicts.log` | `probe_cron_verdict_stale` (#78) |
| `honest_status.sh` | 6 legs | the operator-owned gate verdict, `exit 0/1/2` | humans; pre-push discipline |
| `fleet_naming_drift_check`, `gen_fleet_hosts --check` | per box | drift / in-sync / UNKNOWN | cron + verdict |
| `rtun_watchdog.sh` (owrt1) | 1 tunnel | TUN_OK / TUN_DEAD / **UNOBSERVED** / BOUNCE | itself (it acts), `meshforge-scout` heartbeat |
| `meshforge-scout` (owrt1) | 1 box | scout tick incl. `rtun_watchdog` state | mirrored home |

### Tier mini — the cadence PROPOSE engine (deterministic core, model at the edges)

| Component | Scale | Notes |
|---|---|---|
| live rules | **67** (LIVE) | shape: `id`, `match`, `action`, `annotation`, `cooldown_s`, `seed_provenance` |
| sources | `boot_health`, `file_mtime`, `http_json`, `json_file`, `nats_sensor` | how it observes |
| actions | `file_annotate`, `nats_action`, `ntfy`, `propose_escalation`, `noop` | **observation-only invariant, lint MF021** |
| dreams | **146 deltas judged** (LIVE) | proposals to change its own rules/memory |
| warm brief | per session | the continuity layer — first thing a session reads |

### Tier L — the local brain (Ollama, eval-gated)

| Component | Scale | Notes |
|---|---|---|
| eval cases | **61 cases / 5 files** (`evals/local_brain/`) | seed 33, ws_d3_skew 12, reset8_caps 8, ratifiable_direction 6, audit 2 |
| `model_router` | advisory | emits a *tier*, never a model id; keeps its own self-score |
| `offline_oracle`, `src/oracle/` | responder + snapshot + intents | the away-from-keyboard answerer |

### Tier claw — physical edge witnesses

| Component | Notes |
|---|---|
| `claw_telemetry.py`, `claw_battery.py` | claw-01 / claw-02; battery + RF ears; the fleet's over-the-air witness |

### The assistant itself

| Organ | Scale | Notes |
|---|---|---|
| calibration ledger | **78 events** (LIVE) | my VERIFIED claims, re-derived later |
| `probe_calibration_drift` | 1 probe | **the only probe that scores an agent's claims** |

---

## 2. The finding that should drive the session

**Score-keeping is inverted.** The organs whose claims we hold to a measured
track record are the *model-driven* ones. The 58 deterministic detectors that
actually watch the domain while the operator is away have **no accuracy record
at all**.

```
my claims (calibration ledger) .... 78 events, re-derived, ~93% held
tier-L local brain ................ 61 eval cases + weekly graded cron
mini's dream proposals ............ ratification ratio (19/80 at last brief)
58 watchdog probes ................ nothing
```

`grep -rn "false_positive|precision|probe_score" src/utils/watchdog_probe*.py`
→ **zero matches.**

**This session produced the proof of what that costs.** The owrt1 rtun
watchdog ran at a **~7% false-positive rate for days** — 134 "tunnel dead" in
91 h against a tunnel that `router_scout` proved healthy 30/30 — and **bounced
a healthy tunnel 4 times**, i.e. the guard's false positives caused the very
outage it existed to prevent. Nobody knew, because nothing scores a detector.

Note the asymmetry that makes this sting: we built `probe_calibration_drift`
specifically so *Claude's* overconfidence became a tracked number. We never
turned that instinct on the probes.

### This composes with an already-queued Pri-1 pass — read that row first

`.claude/audits/review_provenance.md` carries **SIGNAL-YIELD DELETION PASS —
retire before adding** (queued 2026-07-25 at operator request: *"ai bloat and
harness is causing a high failure rate… find the 'genius' in simplicity"*).
Its measurement over 58 days / 481 events is the empirical half of §2 and must
not be re-derived here:

- 57 classes declared, **57 routed by both seeds, 0 unrouted, 0 dead rules**
  (the closed-enum discipline is working — do not "fix" it)
- 20 classes fired, **37 never fired**, 165 escalations total
- `cron_verdict_stale` 49 + `rules_seed_drift` 25 + `parity_drift` 24 =
  **59% of all escalations, all three DEPLOY LAG** on a fleet whose deploy is
  manual by design
- adding `memory_index_oversize` + `calibration_drift` → **61% is the
  observability layer reporting on itself**

⚠️ That row already establishes the trap this taxonomy must not fall into:
**fire count is a REPRESENTATION of value, not value** — the 37 never-fired are
dominated by wedge guards whose incidents were fixed *at source*, where zero
fires is the fix working. So "score the detectors" (T1) **cannot** mean "rank
them by fire count." That is the same lesson as
[[feedback_verify_the_verification]], arriving from a third direction.

**Sequencing recommendation**: the deletion pass is Pri-1 and operator-framed
as *run this BEFORE adding anything new*. This taxonomy is the **general form
of the same judgment** — the deletion pass asks "does this signal earn its
place?", T1 asks "how would we ever know?" Run the deletion pass first, then
let its per-signal reasoning become the taxonomy's evidence. Nothing in this
document proposes adding an organ.

---

## 3. Tensions to resolve (the actual agenda)

**T1 — Who scores the scorers?**
Proposal shape, NOT a design: a detector's claim is falsifiable when an
independent consumer exercises the same path (`router_scout` vs the rtun
watchdog; reachability-by-name vs the DNS drift check). Is there a *convention*
the 58 probes could adopt so precision becomes visible — without building a new
daemon? **Constraint: the answer must not be a meta-organ.**

**T2 — Always-loaded docs have a size guard and no staleness guard.**
`persistent_issues.md` has MF012 (fails at 40,000 chars). Nothing fails when a
claim in it goes *false*. It said "NOT FLEET-ROLLED / do NOT bump the SSOT /
moc3 IS THE CANARY" for **six days** after the roll, and it **cost a real
decision** (the 07-25 hosts roll skipped moc3 for a soak that was over). Hours
later my own memory topic file went self-contradicting after an append. Several
such claims are *mechanically checkable* — "do NOT bump the SSOT" is verifiable
against the SSOT.

**T3 — Layers that agree by construction.**
registry → m1 static entries → `/etc/hosts` block all descend from the same
operator-maintained values, and the drift check compares two descendants of one
source. They would agree even if every box had moved; only reachability-by-name
tests reality. This is the self-confirming-detector bug at *architecture* scale.
Where else does "check A against B" compare siblings?

**T4 — A rule is not a check.**
`feedback_verify_the_verification` was in context for this entire session and I
still committed the same error **twice** (read `server-sig-algs` as proof of
RSA-only instead of trying the real username; read an average as a period).
Both were one cheap test away. This is the sharpest available evidence for what
belongs in the executable tier versus prose — and it generalises past me, since
mini's rules are prose-shaped too.

**T5 — Organ scope: who owns "while you're away"?**
mini proposes, probes detect, oracle answers, claw witnesses, ntfy pages,
cron_verdict records, honest_status gates. Overlaps and gaps are undocumented.
The 2026-07-20 optional-organ sweep found that **of ~50 signal classes only ONE
watched for an available-but-unadopted capability** — everything else waits to
be told. That asymmetry is probably a category, not an accident.

> ⚠️ **STALE AS WRITTEN — corrected 2026-07-26. Do not carry the "only ONE"
> figure forward.** It is the sweep's *pre-fix* count: the sweep found one and
> then shipped the second (`lxmf_propagation_unused`), so there were already
> **two** shape-C classes when this artifact was written —
> `watchdog_probe_core.py:60-61`, plus the shape-A companion
> `lxmf_propagation_node_dark`. The number was carried from this file into the
> taxonomy AND through its frontier ratification without anyone re-reading
> `SIGNAL_CLASSES` (T4 — a representation read as a verification). T5 is
> RESOLVED; the verdict is in `second_brain_taxonomy_2026_07_26.md`.

**T6 — Unversioned organs on the boxes they guard.**
`rtun_watchdog.sh` existed only as an untracked file on owrt1 — the box whose
sole access path it manages — until this session. What else is like that?

---

## 4. Constraints the design must respect

- **Footprint is the constraint.** Measured: one session ≈ 2,237 MB vs *all*
  MeshForge services 1,512 MB; the watchdog on 905 MB moc3 = 7.1% RAM.
  *Never add machinery to watch the machinery.* If we finish with more organs
  than we started, we did it wrong — **consolidation is the likely win.**
- **Observation-only invariant** for mini (lint MF021) is load-bearing.
- **Gates never scale down with the model** (`model_advisor.md`).
- **Tier-L competence comes from the eval ledger, never vibes.**
- Fleet code deploy is **manual**; anything shipped needs a convergence story.

---

## 5. What the frontier session should NOT re-derive

All of §1 is measured and current as of 2026-07-26. Start at §2. The three
concrete incidents that motivate the whole agenda are already root-caused and
written up — the rtun false-positive arc (T1), the stale-doc arc (T2), and the
agree-by-construction arc (T3) all landed this session with evidence, so they
are *examples to reason from*, not open investigations.

**Open thread that is genuinely still unknown** (do not theorise — read the
log): 115 of the 134 rtun events were *genuine* fast `TUN_DEAD` replies whose
cause remains unidentified. Stderr capture now lands in
`/root/rtun_watchdog.log` on the next occurrence.

---

Related: `.claude/rules/model_advisor.md` (why this is prep and not design),
`.claude/rules/honest_failure_modes.md` (the write-time checklist most of these
tensions are instances of), `.claude/rules/calibrated_claims.md` (the
score-keeping discipline §2 wants to generalise).
