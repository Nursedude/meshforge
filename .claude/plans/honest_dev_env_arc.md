# Honest-by-construction dev env — arc + step 3 spec

> Born 2026-06-15 from the operator's concern: *"AI is convincing me things are
> good and that's not true… I need a more honest reliable dev env."* Goal: the
> operator never has to trust an AI summary — every claim is falsifiable from
> external ground truth, and the KNOWN failure classes fail the build, not the
> operator's day.

## Ordering principle (why this order)
Build the checks that catch my next mistake BEFORE making more mistakes — tools
before tasks. Passive knowledge in docs (even auto-loaded) does NOT prevent
misalignment; only enforcement at the moment of action does. Bookend: gate at
session START (ground truth) + at PUSH (can't ship mis-wired work). This extends
the existing Issue #29 spine — do not build a parallel system.

## Done (steps 1–2, all committed/pushed/CI-verified/deployed)
- **Gateway honesty fixes** (the trigger work): `probe_synth_soak_degraded`
  (the hourly synth soak was unwatched); `confirmation_rate` made honest (#74
  residual — was cross-population `confirmed/sent`=1.64=">164%"; now
  `confirmed/(confirmed+failures)` bounded [0,1] + `unconfirmable_sent` surfaces
  the mesh blind spot). `compute_confirmation_view` in `delivery_counters.py`.
- **`scripts/honest_status.sh`** — operator-owned external verifier. Checks CI
  conclusion for HEAD, per-box SHA convergence, full suite (real exit+count),
  lint, live `confirmation_rate<=1.0`, watchdog signals. `exit 0`=fully verified;
  UNKNOWN (unreachable/CI-pending/unparseable) is NEVER counted as PASS;
  watchdog wedge=FAIL, degraded=WARN (loud, doesn't block code-verified);
  `--quick` skips suite, `--strict` fails on warnings, `HONEST_WD_PATH` is a
  fixture seam. It caught two false-greens in ITSELF during the build (SHA
  abbrev-length false-RED; corrupt watchdog.json reading as clean) — fixed.
- **#29 spine turned back ON**: `core.hooksPath` was pointing at the empty
  `.git/hooks` → ALL `.githooks/` hooks were dormant (pre-commit + pre-push ran
  on nothing). Fixed (local config, dev box only). pre-push strengthened: lint +
  regression guards (wiring backstop) + WARN-only parent-CI advisory.

## Step 3 — encode the blind-spot classes as enforced tests (NOT YET DONE)
Convert the 1000-hr knowledge from passive docs into build-fails. Extend
`tests/test_regression_guards.py` / CI; add `tests/test_honesty_invariants.py`.

**3a — false-green invariants:** no displayed metric (field named `*rate*`,
`*ratio*`, `*pct*`) exceeds its logical max (1.0 / 100) across the `/api/*`
snapshot builders; no cross-population `confirmed/sent`-style quotient survives
in any snapshot; (stretch) every `except`/`or []`/`.get(default)` on a read path
that feeds a status field leaves a probe-visible witness.

**3b — wiring invariants:** every `SIGNAL_CLASSES` member is routed in BOTH role
seeds (exists: `TestSeedCoversSignalClasses`) AND wired in
`watchdog_runner.run_all_probes` (NEW — the synth probe needed manual wiring);
every new SQLite DB has a `DBSpec` (MF013, exists); every new `systemd` unit has
a `templates/systemd/` entry; a new long-lived service has a deploy-restart hook
(update.sh / fleet_sync) — the #79 "nothing restarted the daemon after pull" gap.

**3c — user-access invariants (the NomadNet-inaccessible class, MF018):** a TUI
entry point / handler that exists stays REGISTERED and reachable; "service
active ≠ doing the job" — guard that an `active` service maps to a user-facing
function. Hardest to encode; expect to get the first cut wrong.

## Step 3 discipline (load-bearing — do NOT skip)
**Red-test-first for every invariant: prove the new check FAILS on a
deliberately-seeded violation before trusting it.** A guard that can't be shown
to fail is a vacuous false guard — worse than none, and the exact defect this
arc exists to kill. Then 4 (fix known reds: moc5 AREDN sysinfo URL moved
/cgi-bin/sysinfo.json→307→/a/sysinfo, collector uses stale URL), then 5 (sweep).

## Resume in a clean session
1. `bash scripts/honest_status.sh` — establish ground truth (don't trust this doc).
2. `git log --oneline 6bc4a08..HEAD` — read the arc's commits.
3. Build step 3 above, red-test-first.

Pending: synth-soak Monitor was armed on moc (won't survive session end — mini
ntfy is the durable backstop). moc5 AREDN WARN is real (step 4). CI of the last
push lands ~3 min after.
