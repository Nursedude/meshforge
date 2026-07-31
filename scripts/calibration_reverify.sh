#!/usr/bin/env bash
# calibration_reverify.sh — daily HEAVYWEIGHT re-derivation of the calibration
# ledger. "Silence is not golden in this domain": the lightweight warmstart
# re-derivation only flips a claim when an honest_status marker happens to cover
# its head. THIS job actively RE-RUNS the code verification (pytest + lint) on
# the current HEAD and flips any open VERIFIED claims held/broke — the literal
# "do it N more times" pass^k check that catches a green that does not hold on
# repeat (flaky/nondeterministic/env-drifted).
#
# DESIGN — re-derivation keys on CODE (pytest + lint), NOT honest_status's overall
# verdict. A remote fleet watchdog wedge or a pending CI run must never false-flip
# MY code claim to "broke" (the cry-wolf trap; calibration_drift is /fleet-only,
# no ntfy, until it soaks low-false-positive). Fleet/CI health is watched
# separately (honest_status, the watchdog). A broken claim surfaces via the
# calibration_drift watchdog signal (which reads the ledger); THIS job's own
# liveness is recorded by the cron-verdict regime in the crontab line — so the
# re-derivation loop itself can never go silently dead.
#
# Exit codes (consumed by cron_verdict.sh in the crontab line):
#   0 = reverify ran to completion (held/broke recorded in the ledger)
#   3 = could not run (repo / env / ledger problem) — an infra failure, NOT a
#       claim verdict; cron_verdict records it FAIL so #78 surfaces the dead job.
set -u
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
REPO="${MESHFORGE_REPO:-/opt/meshforge}"
cd "$REPO" || { echo "calibration_reverify: cannot cd $REPO" >&2; exit 3; }

HEAD=$(git rev-parse HEAD 2>/dev/null) || { echo "reverify: no git HEAD" >&2; exit 3; }
[ -n "$HEAD" ] || { echo "reverify: empty HEAD" >&2; exit 3; }

# Re-run the code verification of record. File-routed real exit codes, never a
# streamed tail (the exact masked-exit trap this whole arc exists to kill).
python3 -m pytest "$REPO/tests/" -q -p no:cacheprovider >/tmp/.creverify_pytest 2>&1; pyrc=$?
python3 "$REPO/scripts/lint.py" --all >/tmp/.creverify_lint 2>&1; lintrc=$?

# The bare `pyrc` is NOT sufficient evidence on this fleet, and this job is the
# reason it matters: it writes held/broke verdicts into the ledger, so a lost
# exit code becomes a fabricated track record. On the full suite the interpreter
# exits 0 while pytest computed TESTS_FAILED:1 (~50% of runs, measured
# 2026-07-28) — the flap only loses failures toward zero, i.e. it manufactures
# false HELD. Classify through the shared verdict script, which cross-checks the
# exit code against the summary line (honest_failure_modes #5 — one phenomenon,
# one classifier, not a second copy that drifts).
#
# Audited 2026-07-31: 21 of the 34 `held` verdicts in the ledger were minted
# through the unguarded path this replaces.
suite_line=$("$REPO/scripts/pytest_verdict.sh" --log /tmp/.creverify_pytest --rc "$pyrc")
suite_rc=$?
suite_verdict=${suite_line%%	*}

# Tri-state, and the UNKNOWN leg is not cosmetic: the old collapse
# (`pyrc==0 && lintrc==0 ? 0 : 1`) mapped an OOM-killed or unreported suite to
# code_exit=1, which flips previously-verified claims to **broke** — proving
# not-green from a run that proved nothing (honest_failure_modes #2). Exit 2
# leaves those claims open, which rederive_open already treats as "could not
# verify"; open is an honest terminal state, a false `broke` is not.
if [ "$suite_rc" = 2 ]; then
  code_exit=2
elif [ "$suite_rc" = 0 ] && [ "$lintrc" = 0 ]; then
  code_exit=0
else
  code_exit=1
fi

# Flip open claims on HEAD against this fresh code verdict and persist the
# verdict events into the ledger. Reuses the unit-tested rederive_and_persist —
# no duplicated fold logic (honest_failure_modes #5).
HV_HEAD="$HEAD" HV_EXIT="$code_exit" HV_PY="$pyrc" HV_LINT="$lintrc" \
  HV_SUITE="$suite_verdict" \
  python3 - <<'PY' || { echo "reverify: re-derivation step failed" >&2; exit 3; }
import os, sys, time
sys.path.insert(0, os.path.join(
    os.environ.get("MESHFORGE_REPO", "/opt/meshforge"), "src"))
from mini_dudeai import calibration_ledger as cl
head = os.environ["HV_HEAD"]
code_exit = int(os.environ["HV_EXIT"])
green = "green" if code_exit == 0 else "RED"
marker = {
    "head_full": head,
    "exit_code": code_exit,          # 0 held / 1 broke (code-only, no fleet/CI)
    "ran_full_suite": True,
    "ts": time.time(),
    # Read by rederive_open into the verdict's `detail`, so a verdict names the
    # instrument that produced it rather than borrowing honest_status's name.
    "summary": (f"calibration_reverify suite={os.environ['HV_SUITE']}"
                f"(rc={os.environ['HV_PY']})+lint(rc={os.environ['HV_LINT']})"),
}
state = cl.rederive_and_persist(cl.ledger_path(), head, marker, time.time())
ratio = state["ratio"]
print(f"reverify head={head[:7]} code={green} -> held={state['n_held']} "
      f"broke={state['n_broke']} open={state['n_open']} "
      f"ratio={'n/a' if ratio is None else round(ratio, 3)}")
PY

exit 0
