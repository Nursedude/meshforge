# Maintenance-day seed — the exit-code gate + a falsifiability audit of the harness

> Written 2026-09-07 (Opus 5) after a session in which SIX agent-process
> defects landed and ZERO were caught by any gate. Queued for the maintenance
> day ~6h out. **This is a seed, not the work** — do not let planning become
> the deliverable.
>
> Provenance: the session's own errors are the requirements document. They are
> listed below because a gate designed from a class NAME rather than from
> observed instances tends to catch the name.

---

## 0. What ALREADY exists — read before building anything

The single most expensive mistake available on this task is rebuilding one of
these. (The session that wrote this plan spent its day proving that lesson: it
called a declared absence a "gap" that four in-repo sources already explained.)

| Thing | Where | What it already does |
|---|---|---|
| `MF022` | `scripts/lint.py` (`_check_pip_invocations_in_file`) | flags `pip install` piped to `tail`/`head` — the exit-code-masking idea, **scoped to pip only** |
| `pytest_checked.sh` | `scripts/lib/` | the honest "run pytest, keep the real rc" wrapper |
| `harness_audit.sh` | `scripts/` | daily manager cron; hooks, claim gate, ledger, mini, cron spine, memory index, deadman; PASS/FAIL/UNKNOWN, exit 0/1/2 |
| `harness_map.md` | `.claude/foundations/` | the companion map this audit is the executable half of |
| `guard_drill.py` | `scripts/` | plants a violation to prove a guard FIRES (never reads) |
| `falsifiability_drill.py` | `scripts/` | kills each probe, reports which classes would die silently |
| PreToolUse Bash hook | `.claude/settings.json` → `.claude/hooks/psk_leak_guard.sh` | **the proven place a Bash-command gate lives.** A second hook of this shape needs no new mechanism |

---

## 1. The six observed defects (the requirements)

From 2026-09-06/07, all mine, all in the agent-process lane, none caught:

1. Reported `GATE_RC=0` from `review_provenance_check.py` while it judged an
   **empty** `origin/main..HEAD` range against an uncommitted edit.
2. Built a test around `--ledger`, a flag the checked function never consumes
   (it reads the ledger **from git at HEAD**) — the test validated nothing.
3. Printed `rc=0` that was `head`'s exit code from a pipeline, not python's.
4. Called a declared absence an unexplained gap (4 sources said otherwise).
5. Ran a live-box measurement before checking (4).
6. Reconstructed another session's plants from mtimes with the write-up on
   disk and the operator present.

**Coverage today: 0/6.** That number is the audit's baseline and its scoreboard.

---

## 2. The exit-code gate — three layers, each matched to where a defect LIVES

⚠️ Build them in the order below, which is **inverse to how satisfying they
are**. Layer A is the one that feels like progress and would have caught none
of the six.

### Layer C (BUILD FIRST — highest yield, no new machinery)
**"The check ran against nothing" is not syntactic and cannot be linted or
hooked. Cure it with legibility, not a gate.**

Every checker must announce the SCOPE it judged, so an empty check is
self-announcing instead of green:

```
review_provenance_check: judged 0 commit(s) in origin/main..HEAD — nothing to check
```

Targets: `review_provenance_check.py`, `honest_status.sh` legs,
`dep_advisory_check.py`, `guard_drill.py`, `falsifiability_drill.py`, and any
checker whose exit code a session is likely to quote. A leg that judged an
empty input set must not print PASS — it prints the count, or UNKNOWN.

This is the `feedback_instruments_fail_at_legibility` lesson in gate form, and
it would have caught defect **1** outright and made **2** visible.

### Layer B (BUILD SECOND — catches the idiom, needs soak)
**A `PreToolUse` Bash hook**, sibling to `psk_leak_guard.sh`.

Fire when a command both (a) contains a pipeline, and (b) then captures or
reports an exit code — i.e. the `... | tail` / `| head` … `$?` idiom. That
conjunction is what makes it precise: `cmd | head` alone is a legitimate read
and must NOT warn.

Detect at minimum:
- `<pipeline> ; echo "...$?"` and `<pipeline> && echo`
- `rc=$?` / `RC=$?` following a pipeline on the same logical line
- suggest the fix in the message: `PIPESTATUS[0]`, or redirect to a file and
  read `$?` (the `pytest_checked.sh` pattern)

**WARN first, with a witness log** (`~/exit_code_guard.log`, the "did the guard
ever run" question — `review_provenance_check.py` line ~47 is the precedent).
Promote to deny only after a soak shows the false-positive rate. A blunt deny
on a Bash hook is a footgun of exactly the shape
`feedback_never_arm_a_guard_that_can_kill_the_session` warns about.

Would have caught defects **3** and one earlier unreported instance.

### Layer A (BUILD LAST — cheap, low yield, do it because it's 20 minutes)
Generalize `MF022` from `pip install` to **any** checked command piped to
`head`/`tail` in a repo script. Reuses the existing rule id, matcher, and
quote-awareness (`_match_in_quotes`).

**Honest note: this would have caught NONE of the six**, because all six lived
in ad-hoc tool calls, not repo files. Build it for the class, not for today.

---

## 3. The harness audit — falsify the harness, don't re-describe it

`harness_audit.sh` exists and passes. The question this audit asks is the one
`feedback_a_guard_that_never_failed_is_not_evidence` insists on:

> **Can each leg FAIL? Plant the failure and watch it.**

Method — reuse `guard_drill.py`'s pattern, do not invent a third drill:

1. **Per-leg falsification.** For each leg of `harness_audit.sh`, construct the
   state it claims to detect (unset `core.hooksPath`; stale a verdict past its
   window; remove a Stop-hook entry in a COPY of settings.json; age the ledger)
   and confirm the leg goes FAIL, not PASS and not UNKNOWN. Record per-leg:
   `fires / silent / unobservable`. A leg that cannot be made to fail is the
   finding.
   ⚠️ A drill that DEFEATS a guard must first assert the guard EXISTS — else a
   typo in the drill reads as a passing guard.
   ⚠️ **Plant in a tmp tree and inject the path; never on a live-probed path.**
   That rule was bought on 2026-09-06 with a 2.5-min real page.

2. **Self-verdict orphan check.** `harness_audit.sh`'s crontab line pipes its
   own `$?` into `cron_verdict.sh`. Confirm something JUDGES that verdict —
   the 2026-09-06 WAN ladder found a self-verdicting cron is an orphan to
   `cron_verdict_stale`, and 45 FAILs went unseen for 7h.

3. **Coverage, stated as a number.** Walk the six defects above against every
   harness leg and report how many any leg would catch. Baseline is 0/6.
   Publish the number; do not average it into a healthy-looking summary.

4. **The blind-spot pass.** What does the harness watch? Code and fleet state.
   What does it not watch? The session's own reasoning process. Say plainly
   whether that is closeable or whether it is the permanent residual — an
   honest "not closeable by an instrument" is a first-class result here and
   better than a gate that pretends.

---

## 4. Constraints — read these before adding anything

- **The 09-03 volume brake applies.** This plan ADDS three things to a fleet
  whose instrument churn already exceeds its product churn. Before landing
  Layer A or B, name what gets REMOVED or explicitly accept the growth in the
  commit message. Layer C is a modification, not an addition — prefer it.
- **`feedback_my_footprint_is_the_constraint`** — a PreToolUse hook runs on
  EVERY Bash call. Measure its latency on the slowest box before landing.
- **No same-session self-deploy of a review fix** (09-03 brake): whatever is
  built here gets reviewed before it rolls, and not by its own author.
- **Gates never scale down with the model** — build so a smaller model leans on
  these HARDER, not so a bigger one can skip them.

---

## 5. Suggested order for the day

1. Layer C on `review_provenance_check.py` + `honest_status.sh` (highest yield)
2. Harness falsifiability drill, per-leg, with the 0/6 baseline re-derived
3. Layer B hook in WARN mode + witness log
4. Layer A `MF022` generalization
5. Row in `.claude/audits/review_provenance.md` — and a line in the **live-box
   touch log** if any drill touched a box

Stop after 1–2 if they take the day. Two landed, verified, falsified items beat
four believed ones.
