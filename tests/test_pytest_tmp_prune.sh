#!/usr/bin/env bash
# Behavior test for scripts/pytest_tmp_prune.sh (2026-07-28).
#
# The property that matters most is the SAFETY one: a run dir whose .lock is
# younger than pytest's LOCK_TIMEOUT may belong to a pytest running right now,
# and deleting it would corrupt a live run. Everything else is bookkeeping.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/pytest_tmp_prune.sh"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
VERDICTS="$TMP/verdicts.log"

mkrun() {  # $1 = number, $2 = optional lock age in seconds
  d="$BASE/pytest-$1"; mkdir -p "$d/some_test0"
  echo "payload" > "$d/some_test0/f"
  if [ -n "${2:-}" ]; then
    touch "$d/.lock"; touch -d "@$(( $(date +%s) - $2 ))" "$d/.lock"
  fi
}

run() {
  PYTEST_TMP_BASE="$BASE" CRON_VERDICT_LOG="$VERDICTS" \
    CRON_VERDICT_BIN="$HERE/../scripts/cron_verdict.sh" \
    HOME="$TMP" bash "$SCRIPT" 2>&1
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

STUB="$TMP/stubbin"; mkdir -p "$STUB"
run_pgrep() {  # $1 = exit code the stub pgrep should return ("" = real pgrep)
  if [ -n "$1" ]; then printf '#!/usr/bin/env bash\nexit %s\n' "$1" > "$STUB/pgrep"
                       chmod +x "$STUB/pgrep"; _p="$STUB:$PATH"
  else rm -f "$STUB/pgrep"; _p="$PATH"; fi
  PATH="$_p" PYTEST_TMP_BASE="$BASE" CRON_VERDICT_LOG="$VERDICTS" \
    CRON_VERDICT_BIN="$HERE/../scripts/cron_verdict.sh" HOME="$TMP" \
    bash "$SCRIPT" 2>&1
}


# pytest's OWN lock window, asked of pytest — every age below is expressed
# relative to this, so no test re-hardcodes it either.
WANT_LOCK="$(python3 - <<'PY' 2>/dev/null
from _pytest.pathlib import LOCK_TIMEOUT
print(int(LOCK_TIMEOUT))
PY
)"

# ── keeps the newest 3, prunes the rest ──────────────────────────────────
BASE="$TMP/a"; mkdir -p "$BASE"
for n in 1 2 3 4 5 6 7; do mkrun $n; done
out="$(run)"
left="$(ls -d "$BASE"/pytest-[0-9]* | sed 's/.*pytest-//' | sort -n | tr '\n' ' ')"
check "keeps the newest 3 by RUN NUMBER (got: $left)" \
  "$([ "$left" = "5 6 7 " ] && echo ok)"
check "reports what it pruned" "$(echo "$out" | grep -q 'pruned 4 of 7' && echo ok)"

# ── SAFETY: a fresh lock means a live run — never delete it ──────────────
BASE="$TMP/b"; mkdir -p "$BASE"
for n in 1 2 3 4 5 6; do mkrun $n; done
mkrun 1 60          # re-stamp #1 with a 60-second-old lock
out="$(run_pgrep 0)"   # stub: a pytest IS running — the condition these two
                       # assertions NAME but never used to establish
check "a dir with a FRESH lock survives (live pytest)" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"
check "and the skip is stated, not silent" \
  "$(echo "$out" | grep -q 'skipped (lock' && echo ok)"

# ── a STALE lock is not a live run — prune it ────────────────────────────
BASE="$TMP/c"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 $((WANT_LOCK + 3600))   # past LOCK_TIMEOUT — was 99999, which is
                                # INSIDE the real 3-day window and only read as
                                # "far older" against the wrong 3h constant
run >/dev/null
check "a dir with a STALE lock is pruned" \
  "$([ -d "$BASE/pytest-1" ] && echo '' || echo ok)"

# ── nothing to do is OK, not an error ────────────────────────────────────
BASE="$TMP/missing"
out="$(run)"; rc=$?
check "absent tree exits 0 and says nothing-to-do" \
  "$([ "$rc" = 0 ] && echo "$out" | grep -q 'nothing to do' && echo ok)"

BASE="$TMP/d"; mkdir -p "$BASE"; for n in 1 2; do mkrun $n; done
out="$(run)"
check "fewer dirs than KEEP prunes nothing, reports OK" \
  "$(echo "$out" | grep -q 'OK.*pruned 0 of 2' && echo ok)"

# ── the lock window is PINNED to pytest's, drift caught HERE ─────────────
#
# The safety property above is only as good as the number it compares against.
# That number WAS an independent hardcode of pytest's own LOCK_TIMEOUT
# (honest_failure_modes #5: two consumers of one constant WILL drift) — and a
# THIRD copy of it lived in the display text as the literal "<3h", which lies
# outright the moment the window is overridden. If upstream raises LOCK_TIMEOUT
# the pruner starts deleting live runs' temp dirs, which is exactly the flaky
# suite the script's own header says is worse than the memory it reclaims.
#
# This pin — asking pytest ITSELF for the constant — is the ONE drift guard
# (2026-07-28 review): a second, runtime python3+_pytest derivation on every
# cron run guarded the same constant a third time, asked bare python3 rather
# than the venv consumer-of-record, and cost an interpreter spawn per run on
# every box for an answer that changes ~never.
HAVE_FB="$(grep -oE 'LOCK_PINNED_S=[0-9]+' "$SCRIPT" | head -1 | cut -d= -f2)"
check "pinned lock window matches pytest's own LOCK_TIMEOUT (${WANT_LOCK:-?})" \
  "$([ -n "$WANT_LOCK" ] && [ "$HAVE_FB" = "$WANT_LOCK" ] && echo ok)"

BASE="$TMP/e"; mkdir -p "$BASE"
for n in 1 2 3 4 5 6; do mkrun $n; done
mkrun 1 30          # fresh lock -> skipped, so the window gets reported
out="$(run_pgrep 0)"   # skip requires a LIVE pytest now, not just a fresh lock
check "the window it actually used is the pinned one" \
  "$(echo "$out" | grep -qE "lock < *${HAVE_FB}s" && echo ok)"

export PYTEST_TMP_LOCK_AGE_S=60
out="$(run_pgrep 0)"
unset PYTEST_TMP_LOCK_AGE_S
check "the skip message states the REAL window, not a hardcoded 3h" \
  "$(echo "$out" | grep -qE 'lock < *60s' && echo ok)"
check "and the stale '<3h' literal is gone" \
  "$(echo "$out" | grep -q '<3h' && echo '' || echo ok)"

# ── the LIVENESS gate: a lock only protects a LIVE run ───────────────────
#
# WHY (measured 2026-07-29, right after correcting LOCK_TIMEOUT to its true 3
# days): the manager held 15 run dirs / 1.9 GB of tmpfs and the pruner freed
# ZERO — 12 locks were younger than the window and NO pytest was running. Every
# one was STRANDED, left by an interpreter whose atexit never completed, the
# same shutdown unreliability that produces the exit-status flap. On this fleet
# stranded locks are the NORM, so a lock-age-only guard refuses to prune for
# three days while tmpfs fills. The correct constant made the tool ineffective.
#
# The real safety property was never "the lock is fresh" — it is "a pytest is
# using this directory RIGHT NOW". Gate on that. Unobservable liveness must
# resolve to SKIP, never to prune (honest_failure_modes #2): "I could not tell"
# is not "nothing is running".
# 1. fresh lock + NO pytest alive => the lock is stranded => PRUNE.
#    Driven by a STUB pgrep reporting "no match" (rc 1). It must be a stub:
#    when this harness runs under the pytest wrapper there IS a live pytest —
#    the suite itself — so a real-pgrep version of this case would assert the
#    opposite of the truth depending on how it was invoked. (It did, exactly
#    once, before this comment existed.)
BASE="$TMP/live1"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 60           # 60s-old lock — far inside the 3-day window
out="$(run_pgrep 1)"
check "fresh lock + no live pytest => pruned (lock was stranded)" \
  "$([ -d "$BASE/pytest-1" ] && echo '' || echo ok)"
check "stranded locks are reported, not silently reclaimed" \
  "$(echo "$out" | grep -q 'stranded' && echo ok)"

# 1b. THE SELF-MATCH TRAP, tested on the PATTERN itself rather than through the
#     pruner's behaviour — so it holds no matter what is running on the box.
#     This script's own path ends in "pytest_tmp_prune.sh"; a naive
#     `pgrep -f pytest` matches that substring, so the pruner would see ITSELF,
#     conclude a pytest is running, and skip forever. The pattern is read OUT
#     of the script so the two cannot drift.
PAT="$(grep -o "pgrep -u .* -f '[^']*'" "$SCRIPT" | sed "s/.*-f '//; s/'$//")"
check "liveness pattern was extracted from the script" "$([ -n "$PAT" ] && echo ok)"
check "pattern does NOT match the pruner's OWN cmdline (the self-match trap)" \
  "$(printf '%s' "/bin/bash $SCRIPT" | grep -Eq "$PAT" && echo '' || echo ok)"
check "pattern does NOT match a sibling like pytest_tmp_prune.sh alone" \
  "$(printf '%s' "pytest_tmp_prune.sh" | grep -Eq "$PAT" && echo '' || echo ok)"
check "pattern DOES match a real pytest invocation" \
  "$(printf '%s' "/usr/bin/pytest tests/ -q" | grep -Eq "$PAT" && echo ok)"
check "pattern DOES match 'python3 -m pytest'" \
  "$(printf '%s' "/usr/bin/python3 -m pytest tests/ -q" | grep -Eq "$PAT" && echo ok)"

# 2. fresh lock + a pytest IS alive => skip. The safety property, preserved.
BASE="$TMP/live2"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 60
out="$(run_pgrep 0)"          # stub pgrep: match found => pytest running
check "fresh lock + live pytest => SKIPPED (never delete a live run)" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"
check "and the skip is stated" \
  "$(echo "$out" | grep -q 'skipped' && echo ok)"

# 3. liveness UNDETERMINABLE (pgrep errors) => skip, never prune.
BASE="$TMP/live3"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 60
out="$(run_pgrep 2)"          # pgrep rc>1 = error, not "no match"
check "liveness unobservable => SKIPPED (unobservable is not 'nothing runs')" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"

# 4. an OLD lock is pruned even while a pytest runs — it cannot be that run's,
#    and this is the pre-existing behaviour the liveness gate must not weaken.
BASE="$TMP/live4"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 $((WANT_LOCK + 3600))
run_pgrep 0 >/dev/null
check "stale lock still pruned even with a live pytest" \
  "$([ -d "$BASE/pytest-1" ] && echo '' || echo ok)"

# ── a SPOKEN fail exits 0 — the crontab || guard is for dying UNSPOKEN ───
#
# The documented idiom appends `|| cron_verdict.sh ... FAIL wrapper_crashed`.
# Exiting 1 after WRITING the informative FAIL verdict made that guard fire
# too, appending a second, FALSE verdict — and the newest line is what #78
# and the operator read first, so real triage ("N could NOT be removed") was
# masked by "the wrapper crashed" when it ran fine (2026-07-28 review).
# root can delete anything, so the undeletable-dir fixture only works unprivileged.
if [ "$(id -u)" != 0 ]; then
  BASE="$TMP/spoken"; mkdir -p "$BASE"
  for n in 1 2 3 4 5; do mkrun $n; done
  chmod 555 "$BASE/pytest-1/some_test0"   # blocks unlinking the file inside
  out="$(run_pgrep 1)"; rc=$?
  chmod 755 "$BASE/pytest-1/some_test0"   # let the EXIT-trap cleanup succeed
  check "undeletable dir => the informative FAIL verdict is spoken" \
    "$(echo "$out" | grep -q 'could NOT be removed' && echo ok)"
  check "spoken FAIL exits 0, so the || guard cannot overwrite it" \
    "$([ "$rc" = 0 ] && echo ok)"
  check "newest verdict line stays the informative FAIL, never wrapper_crashed" \
    "$(grep 'pytest_tmp_prune' "$VERDICTS" | tail -1 | grep -q 'could NOT be removed' && echo ok)"

  # …but when the verdict CANNOT be recorded, exit 1 so the guard speaks:
  # wrapper_crashed is then the closest available truth, not a lie over a
  # better verdict.
  BASE="$TMP/unspoken"; mkdir -p "$BASE"
  for n in 1 2 3 4 5; do mkrun $n; done
  chmod 555 "$BASE/pytest-1/some_test0"
  PYTEST_TMP_BASE="$BASE" CRON_VERDICT_BIN="$TMP/no-such-verdict-bin" \
    HOME="$TMP" bash "$SCRIPT" >/dev/null 2>&1; rc=$?
  chmod 755 "$BASE/pytest-1/some_test0"
  check "FAIL with no verdict sink exits 1 (the || guard becomes the witness)" \
    "$([ "$rc" = 1 ] && echo ok)"
fi

# ── it leaves a cron verdict so #78 can see it ───────────────────────────
check "writes a cron_verdict line under its own name" \
  "$(grep -q 'pytest_tmp_prune' "$VERDICTS" 2>/dev/null && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
