# EVAL-PF — planted-fault benchmark, slice 1 (grilled 2026-11-13)

> Roadmap row: `EVAL-PF` (NOW). Operator: WH6GXZ. Grilled with Opus 5.5, 30
> questions in 5 rounds, every answer "yes" (Q15 + Q25 withdrawn in round 5 for
> Q30). **Redacted on purpose:** the specific faults planted (case 1's unit and
> box details) live OFF-repo with the answer key (Q8, Q20). This file names
> classes only. Off-repo full record: `~/.local/share/meshforge/eval_pf/design_2026_11_13.md`.

## Why

The harness is the domain's deliberate second purpose (operator 11-11). The
README's "How it's built" says improvement from one model to the next "is not
shown yet". EVAL-PF is the instrument that shows it, or fails to. Operator
11-13: "the niche is 1+1=2 — I will never compete with a team of AIs and
engineers — this is our way." The benchmark measures the 1+1: what a model
does with the harness versus without it, on this domain's real radios and boxes.

## Settled

| Q | Decision |
|---|---|
| Q1 | **Grade the co-pilot**, not the product detector: a fresh model session diagnosing a planted fault. Product output (brief, TUI, journal) is evidence it may read; detector correctness stays with each arc's own done gate. |
| Q2 | Case 1 = a past, already-root-caused failure class, software-only on `meshforge-lab`, reversible, no radio, no rnsd restart. CLAW-1 Q16 plants become cases when CLAW-1 ships them. |
| Q3 | **Two arms**: harness ON / harness OFF. The score gap is the harness's measured value. |
| Q4 | Read-only diagnosis; no fix is graded in slice 1. |
| Q5 | A **null case** (nothing planted) ships with case 1; an invented fault on a healthy box fails. |
| Q6 | Manual runs on the operator's go: each new model + monthly with LAND-1. No cron. |
| Q7 | n = 3 per (case × arm). Row: model id, harness HEAD sha, arm, case id, date, verdict, score, cost, transcript path → off-repo jsonl. |
| Q8 | Cases (plant, restore, key) live OFF-repo in `~/.local/share/meshforge/eval_pf/cases/`; the repo holds runner, scorer, schema; provenance names case ids, never contents. |
| Q9 | One neutral prompt for every case: "Check meshforge-lab's health. Report a fault with its cause, your evidence and the one check that confirms it — or 'no fault' with what you checked." |
| Q10 | Required final fenced JSON `{verdict: fault|no_fault|unknown, component, mechanism, evidence[], confirm_check}`; missing = scored as wrong. |
| Q11 | **+1** right component + mechanism · **0** honest `unknown` with checks listed · **−1** confident wrong, or a fault invented on the null case. Deterministic match on component name + mechanism keyword set; no LLM judge (same vendor); the operator adjudicates borderline answers, logged. |
| Q12 | Plant **pre-check** must pass or the run is VOID; restore runs in a trap and must match the pre-plant snapshot (active + failed units); a failed restore pages the operator and blocks the next run. |
| Q13 | Case 1's plant, restore and safe run window are in the off-repo case file (Q20: the instance never enters the repo). |
| Q14 | Lab access = a **dedicated eval key** on meshforge-lab, `restrict,command="…/eval_pf_readonly.sh"`: a fixed list of read commands (systemctl status/show/list/--failed, journalctl --no-pager, cat/ls under the app's data + config dirs, ps, ss -ltn, df, free); everything else refused; every command logged. `box_truth` cannot serve: it reads system units only (queued separately). |
| Q15 | ~~separate `evalpf` user~~ — WITHDRAWN for Q30 (operator: don't complicate the user env; VolcanoAI is the SoT lab Pi). |
| Q16 | ON = full clean clone at HEAD (`CLAUDE.md`, `.claude/`, hooks). OFF = same clone minus `CLAUDE.md` and `.claude/` (what a ham gets). Both: `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `--tools Read,Grep,Glob,Bash`, `--permission-mode dontAsk`, only `Bash(ssh eval-lab *)` allowed, `--max-turns 40`, `--max-budget-usd 3`, `timeout 900`. |
| Q17 | Results → `~/.local/share/meshforge/eval_pf/results.jsonl`; a warm-brief line per model `EVAL-PF <model>: ON +x/n · OFF +y/n · Δ`; the README's "not shown yet" changes only from a real run, quoted with its date. |
| Q18 | Wrapper `scripts/eval_pf_readonly.sh` + a refusal test (`rm`, `systemctl restart`, `bash -c`, `;`/`&&`, `..`) + an `--apply` installer that lays the `authorized_keys` line, reads it back, writes a touch line. Nothing hand-laid. |
| Q19 | Case schema: `id, box, kind(fault|null), plant[], precheck[], restore[], snapshot_keys[], avoid_window, key{verdict, component, mechanism_keywords[]}`; a missing field refuses the case. |
| Q20 | Knowledge of a failure **class** in the harness is fair game (that is the harness); the **instance** never enters the repo. |
| Q21 | **Acceptance (slice 1):** one model, both arms, n=3, case 1 + null = 12 scored rows; every run pre-check-passed and restore-verified; the wrapper log shows only allowlisted commands; the refusal test passes in CI; a hand-fed wrong answer scores −1. |
| Q22 | Opus 5.5 first, then Fable — operator: "these are the models of this moment". |
| Q23 | A contextless Fable review **by running** before the first scored run; round 2+ also Fable. |
| Q24 | Model is a parameter (`--model`); the recorded id is the one the session reports (`system/init`); no model name in code, cases or scorer. |
| Q25 | ~~separate credentials~~ — WITHDRAWN for Q30. |
| Q26 | Transcripts + wrapper logs off-repo, `eval_pf/runs/<run-id>/`, 90 days; only scored rows may be shared. |
| Q27 | Spend ceiling $36 for slice 1; the runner refuses a batch whose worst case exceeds it and stops at it. |
| Q28 | The lab's mini will see the plant: a touch line per run (window), mini deltas in that window resolved `eval plant`, and whether mini named the fault is recorded as a bonus field. No new code. |
| Q29 | **Order:** (1) roadmap row + acceptance · (2) wrapper + refusal test through CI · (3) eval key + installer `--apply` (operator go, touch line) · (4) login + isolation probe · (5) runner, scorer, schema · (6) hand-fed wrong answer scores −1 · (7) Fable review by running · (8) first scored batch. Each step lands and is verified before the next. |
| Q30 | Sessions run as the operator's own user with `--restricted` (file tools confined to the scratch clone; settings files ignored) + the Q16 allowlist; ON arm adds `--settings <clone>/.claude/settings.json`. The probe (step 4) must show: (a) a read of the answer-key path is REFUSED; (b) the ON arm can quote a line of `CLAUDE.md` (else add the clone via `--add-dir`). |

## Shared understanding (confirmed 2026-11-13)

EVAL-PF grades a fresh model session, not the product, on faults planted on the
lab box, in two arms (harness ON vs what a ham gets) so the gap measures the
harness. One neutral prompt, a required JSON answer, a deterministic +1/0/−1
score with the operator adjudicating. Case 1 is a known failure class plus a
null case; cases and keys stay off-repo. Plants are pre-checked or VOID;
restores are trapped and verified. Lab access is a forced-command read-only
key; sessions run as the operator under `--restricted`; models are an input;
n=3; results to a jsonl and a warm-brief line; $36 ceiling; a touch line per
run. Built in the Q29 order, Fable-reviewed by running before the first score.

## Open / known residuals

- `box_truth` does not read user units — a blind spot of its own (queue as a row).
- `--restricted` behaviour for CLAUDE.md discovery and OAuth is from `claude --help` 2.1.325 text, not yet observed — the Q30 probe settles it.
- The CLAW-1 plan names its future EVAL-PF plants by class; when they become cases, plant them so the instance differs from anything written in the repo.
