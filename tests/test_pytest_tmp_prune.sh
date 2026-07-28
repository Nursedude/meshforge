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
out="$(run)"
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

# ── the lock window is pytest's, not a second copy of it ────────────────
#
# The safety property above is only as good as the number it compares against.
# That number WAS an independent hardcode of pytest's own LOCK_TIMEOUT
# (honest_failure_modes #5: two consumers of one constant WILL drift) — and a
# THIRD copy of it lived in the display text as the literal "<3h", which lies
# outright the moment the window is overridden. If upstream raises LOCK_TIMEOUT
# the pruner starts deleting live runs' temp dirs, which is exactly the flaky
# suite the script's own header says is worse than the memory it reclaims.
HAVE_FB="$(grep -oE 'LOCK_FALLBACK_S=[0-9]+' "$SCRIPT" | head -1 | cut -d= -f2)"
check "fallback lock window is test-pinned to pytest's LOCK_TIMEOUT (${WANT_LOCK:-?})" \
  "$([ -n "$WANT_LOCK" ] && [ "$HAVE_FB" = "$WANT_LOCK" ] && echo ok)"

BASE="$TMP/e"; mkdir -p "$BASE"
for n in 1 2 3 4 5 6; do mkrun $n; done
mkrun 1 30          # fresh lock -> skipped, so the window gets reported
out="$(run)"
check "the window it actually used is DERIVED from pytest at runtime" \
  "$(echo "$out" | grep -qE "lock < *${WANT_LOCK}s" && echo ok)"

export PYTEST_TMP_LOCK_AGE_S=60
out="$(run)"
unset PYTEST_TMP_LOCK_AGE_S
check "the skip message states the REAL window, not a hardcoded 3h" \
  "$(echo "$out" | grep -qE 'lock < *60s' && echo ok)"
check "and the stale '<3h' literal is gone" \
  "$(echo "$out" | grep -q '<3h' && echo '' || echo ok)"

# ── it leaves a cron verdict so #78 can see it ───────────────────────────
check "writes a cron_verdict line under its own name" \
  "$(grep -q 'pytest_tmp_prune' "$VERDICTS" 2>/dev/null && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
