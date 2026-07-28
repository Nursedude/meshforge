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
mkrun 1 99999       # far older than LOCK_TIMEOUT
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

# ── it leaves a cron verdict so #78 can see it ───────────────────────────
check "writes a cron_verdict line under its own name" \
  "$(grep -q 'pytest_tmp_prune' "$VERDICTS" 2>/dev/null && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
