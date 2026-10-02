# ADR — compiled foundation / "truth kernel" (2026-10-02)

**Status:** DECIDED by a pre-registered measurement. **Compiled rewrite: NO.
Compiled truth kernel: NOT NOW (did not meet its own bar). Typed tri-state result
in Python: YES.** Operator: *"i will follow your advice on this"* — the advice is
the measurement's, not the author's; the author's prior (pro-kernel) lost.

## Question (operator, 2026-10-02)

*"How can we do better at building — a more reliable foundation that can't be
broken to the point where the SSOT is deceptively wrong — should we consider
writing some of the more durable aspects of this domain in compiled code?"*
Context the same day: the check of record read green while 8 of 9 rnsd ran
pre-roll code; the instrument built to fix it had three false greens of its
own, caught only by running it and by a contextless reviewer.

## Method (pre-registered BEFORE classification; hashes in the scratch record)

- **Units:** 164, enumerated mechanically — every `## ` section of
  `persistent_issues_archive.md` + `persistent_issues.md` and every row of the
  live tells table. No judgement in the enumeration.
- **Raters:** two contextless classifiers, identical definitions, read-only,
  text-only, no access to each other or to the author's prior.
- **Primary class (root cause):** C1 a Rust/Go compiler would have rejected the
  code · C2 semantic (ran as written, wrong value/decision) · C3 env/ops/physical
  · C4 concurrency/resource · C5 human/doc · NA not an incident.
- **Flags:** F_SHARED (the observer shared a failure domain / blind spot with
  the observed) · F_TRISTATE (a mandatory Seen/Unobservable/Error type with no
  default collapse would likely have prevented it) · F_RUST (rewriting only that
  component in Rust would likely have prevented it).
- **Decision rules (fixed in advance):** rewrite iff C1 or F_RUST ≥ 25% ·
  kernel iff F_SHARED ≥ 20% AND a majority of F_SHARED units fall in the
  kernel's scope (author's call, listed below for audit) · tri-state iff
  F_TRISTATE ≥ 15% · any flag with kappa < 0.4 is unusable.

## Result (n = 128 units both raters called incidents)

| Measure | Rater 1 | Rater 2 | Mean | Agreement | Cohen's κ |
|---|---|---|---|---|---|
| Primary class | — | — | — | 95% | **0.93** |
| C1 compiler-catchable | 2% | 2% | **2%** | | |
| C2 semantic | 41% | 39% | **40%** | | |
| C3 env/ops/physical | 37% | 38% | **38%** | | |
| C4 concurrency/resource | 17% | 18% | **18%** | | |
| C5 human/doc | 3% | 3% | 3% | | |
| F_SHARED | 30% | 23% | **27%** | 92% | 0.80 |
| F_TRISTATE | 23% | 24% | **23%** | 97% | 0.91 |
| F_RUST | 2% | 2% | **2%** | 100% | 1.00 |

Every flag is reliable (κ ≥ 0.80). The two C1 / F_RUST units are the same in
both raters' hands; the one named in the live file is the MeshCore
`channel` vs `channel_idx` key (a dict key the library never sends — a typed
struct would have refused it).

### Rule 1 — compiled rewrite: **NOT SUPPORTED** (2% vs 25%)

The domain's incidents are overwhelmingly code that RAN AS WRITTEN and told a
wrong story (C2), or the world being other than assumed (C3). A compiler
accepts both. This is the build:fix doctrine measured: *this domain's failures
lie rather than crash.*

### Rule 2 — compiled truth kernel: **NOT SUPPORTED** (F_SHARED passes, scope fails)

F_SHARED = 27% clears 20%. But of the 29 units both raters flagged, the
distinct incidents (three appear twice, archive + tell) number 23, and only
**10 of 23 (43%)** fall inside the proposed kernel scope (process / unit /
install / socket / clock / heartbeat facts):

- **IN (10):** #32 pgrep self-match + shared-instance status · #75 leaked
  TCPInterface (socket count) · #82 hardcoded `@rns/default` + user units blind
  · meshtasticd ABSENT read as INACTIVE (LoadState) · wrong instance name
  (A4008 = tell L106) · probe blind on the dist-packages box (L108, install
  layout — today's class) · probe naming a nonexistent unit (L110) · stale
  clock (L121) · audit can't read root's envs (L134) · user-unit crashloop
  unseen (L138).
- **OUT (13):** #34 mqtt topic shape · #51 ISO-vs-epoch parse · #60 sandbox
  ReadWritePaths · #72 RPC wedge (needs the RNS RPC protocol) · #73 addendum
  sudo-in-sandbox probes · parity_drift working tree · delivery probes on the
  gateway-only shape · checker consuming its artifact (A3982 = L127) · path
  not box (L109) · subprocess timing measured CPU (L124) · AREDN front
  reassigned (L128) · subnet behind a bridged router (L129) · boot guard gated
  on a name (L139) · `channel_idx` wire key (L132).
- **Borderline, called OUT before tallying, and the author's bias ran the
  other way:** #73 addendum, L109 (a kernel heartbeat carrying boot-id would
  have separated box-down from path-down), L124 (PSI/loadavg would have
  annotated it). All three IN would make 13/23 = 57% — the verdict is
  sensitive to these calls, and that sensitivity is itself the finding: the
  case is not strong.

**What F_SHARED actually is, read across the 23:** most shared blind spots
are about IDENTITY and EVIDENCE CHOICE — the wrong name, the wrong artifact,
the wrong layer, the wrong interpreter, two stale sources agreeing — not about
the observer's runtime crashing alongside its subject. A compiled process
reading /proc would have shared most of those blind spots, because the blind
spot lived in what was ASKED, not in what language asked it.

### Rule 3 — typed tri-state result: **SUPPORTED** (23% vs 15%)

## Decision

1. **No compiled rewrite.** Revisit only if a future count (same method)
   shows C1/F_RUST ≥ 25%.
2. **No compiled truth kernel now.** Revisit if the borderline class grows: if
   a future count puts ≥ 13/23-equivalent F_SHARED incidents in process/
   install/socket/clock scope, or a box loses its python env and every eye
   goes dark with it (not yet observed — UNKNOWN, not "won't happen").
3. **Adopt a typed tri-state result in Python** on the truth path, enforced by
   a type checker, not by prose:
   `Observation = Seen[T] | Unobservable(reason) | Failed(reason)` — no
   implicit default, no `or []`, exhaustive handling checked by mypy
   (`assert_never`). First modules: `scripts/hs_substrate_skew.py`,
   `utils/watchdog_probe_core.py`, `utils/rns_init.py`, `utils/service_check.py`.
4. **Treat F_SHARED as a DESIGN obligation, not a language one:** every
   truth-path check names its evidence source and ships one test that feeds
   an input on which the check and its subject DISAGREE (the
   "checker must not consume the artifact it validates" rule, made a test
   shape). Today's substrate leg is the worked example: the venv false green
   was found only by feeding a process whose venv differed from system python.

## Freeze data (interim, measured 2026-10-02 for the 10-09 review)

Same rule applied to both windows (harness = `.claude/`, `.githooks/`,
`evals/`, harness scripts/tests, watchdog + mini code; a commit is "harness"
when most of its files are). The 09-09 baseline used an unrecorded method, so
compare the two rows here, not against its 53%.

| Window | Commits | /day | Harness-majority | `.claude`/docs-only | Files: harness vs product |
|---|---|---|---|---|---|
| 30 d before (08-10 → 09-09) | 324 | 10.8 | 42% | 31% | 105 vs 300 |
| Freeze (09-09 → 10-02) | 553 | **23.6** | 38% | **36%** | 75 vs 607 |

Disposition census (watchdog coverage, class × box, 9 boxes): **295 clean /
258 inert / 5 indeterminate** (09-09: 314 / 234 / 1). Inert everywhere:
`oracle_delivery_degraded` (also inert everywhere on 09-09 — 23 days, still
silent) and `inherited_app_drift`. Indeterminate: VolcanoAI
`gateway_dual_homed_exposure`, `gateway_dup_degraded`; moc3
`delivery_confirmation_stall`; moc5 `phoneapi_tcp_leak`, `aredn_source_dark`.

**Reading:** the freeze shrank harness CODE (harness files touched −29%) and
product work doubled (+102%) — but total volume DOUBLED and docs-only
commits grew as a share. The freeze capped instruments, not churn; the
binding constraint is commit volume and ceremony, which the freeze does not
measure. Inert grew (+24) while classes did not. Recommendation for 10-09:
end the freeze as an instrument cap, replace it with a VOLUME budget
(commits/day, docs-only share) and run the inert-tier cut — the two
inert-everywhere classes are the first candidates (check each is not a
blindness subject first; `oracle_delivery_degraded` is a delivery END, so
ask why it can never fire before cutting).

## Threats to validity

- Raters are the same model family as the author; independent of context,
  not of model. κ is high partly because the corpus uses the domain's own
  vocabulary ("blind by construction", "consumes the artifact").
- **Survivorship toward C2/C3:** compiler-catchable bugs are usually caught by
  tests/CI and never become persistent_issues entries, so C1 is understated
  as a share of ALL bugs. The question was about incidents that cost the
  operator hours, which is this corpus — but a "bugs per KLOC" question would
  need a different corpus.
- The corpus is curated; resolved issues were rewritten as lessons, which
  likely inflates F_SHARED language.
- Kernel scope was judged by the author (biased pro-kernel); the calls and
  the borderlines are listed above for audit.
- Freeze commit split uses a reconstructed path rule, not the 09-09 method.

## Reproduce

Raw record: `.claude/audits/truth_kernel_count_2026_10_02/` — `units.tsv`, `PREREG.md` (sha256 prefix
`10e74429…`), `RATER_DEFS.md`, `rater1.jsonl`, `rater2.jsonl`,
`agree.py`, `census.tsv`. To re-run: enumerate units the same way, give two
contextless raters `RATER_DEFS.md`, compute κ with `agree.py`.
