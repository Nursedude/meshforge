#!/usr/bin/env bash
# pytest_tmp_prune.sh — keep pytest's temp tree bounded, because /tmp is RAM.
#
# WHY (2026-07-28, measured on the manager box): /tmp is a tmpfs on every
# fleet Pi, so pytest's per-test temp tree is unreclaimable RAM, not disk. A
# green full-suite run leaves ~5,272 entries (~79 MB of shmem), and run dirs
# accumulated to 37 over two days — 3.0 GB, which drove user.slice to 91% of
# its 8 GB cap and tripped memory_cap_engaged. On moc3 (905 MB total) ONE
# retained run is ~9% of the box's memory.
#
# Why not fix it in pytest's own config: setting tmp_path_retention_policy to
# "failed" cuts a run to 64 KB, but pytest truncates temp dir names to 30
# chars, so same-named tests in different classes collide
# (test_debounce_first_tick_silen0) and deleting a passing test's dir frees
# the number for reuse — 4 tests then read a previous test's state file and
# failed. Measured, then reverted. A flaky suite is strictly worse than 79 MB.
#
# Why not rely on pytest's own retention: it works when observed, but is
# unexplained over the window where 37 dirs survived, and it runs at atexit —
# and this interpreter's shutdown is demonstrably unreliable here (see
# .claude/research/pytest_exit_status_flap_2026_07_28.md). This prune does not
# depend on knowing the answer.
#
# SAFETY: never touches a run dir whose .lock is younger than pytest's own
# LOCK_TIMEOUT — that dir may belong to a pytest running right now.
#
# Usage (crontab idiom — the script emits its own verdict; the `||` guard
# catches the case where the script itself dies before it can speak):
#   40 */6 * * * /opt/meshforge/scripts/pytest_tmp_prune.sh >/dev/null 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh pytest_tmp_prune FAIL wrapper_crashed
set -u

KEEP="${PYTEST_TMP_KEEP:-3}"
LOCK_MAX_AGE_S="${PYTEST_TMP_LOCK_AGE_S:-10800}"   # pytest's own LOCK_TIMEOUT (3h)
CONCERN_MB="${PYTEST_TMP_CONCERN_MB:-1024}"        # freed >= this => say so out loud
BASE="${PYTEST_TMP_BASE:-${TMPDIR:-/tmp}/pytest-of-$(id -un)}"
VERDICT="${CRON_VERDICT_BIN:-$(dirname "$0")/cron_verdict.sh}"

say() {  # $1 = status, $2 = message
  if [ -x "$VERDICT" ]; then "$VERDICT" pytest_tmp_prune "$1" "$2" >/dev/null 2>&1; fi
  echo "pytest_tmp_prune: $1 — $2"
}

# A box that never runs the suite has no tree. That is not an error and not a
# silence to explain — it is nothing to do.
if [ ! -d "$BASE" ]; then
  say OK "no pytest temp tree at $BASE — nothing to do"
  exit 0
fi

now=$(date +%s)
before_kb=$(du -sk "$BASE" 2>/dev/null | cut -f1); before_kb=${before_kb:-0}

# Newest-first BY RUN NUMBER, not mtime: the number is the sequence pytest
# itself assigns, and mtime moves when anything inside is touched.
mapfile -t dirs < <(ls -d "$BASE"/pytest-[0-9]* 2>/dev/null \
                    | sed 's/.*pytest-//' | sort -rn | sed "s|^|$BASE/pytest-|")
total=${#dirs[@]}

pruned=0; skipped_locked=0; failed=0
i=0
for d in "${dirs[@]}"; do
  i=$((i + 1))
  [ "$i" -le "$KEEP" ] && continue          # keep the newest $KEEP
  lock="$d/.lock"
  if [ -e "$lock" ]; then
    lock_age=$(( now - $(stat -c %Y "$lock" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -lt "$LOCK_MAX_AGE_S" ]; then
      skipped_locked=$((skipped_locked + 1))   # a live run may own this
      continue
    fi
  fi
  if rm -rf "$d" 2>/dev/null; then pruned=$((pruned + 1)); else failed=$((failed + 1)); fi
done

after_kb=$(du -sk "$BASE" 2>/dev/null | cut -f1); after_kb=${after_kb:-0}
freed_mb=$(( (before_kb - after_kb) / 1024 ))
remain=$(ls -d "$BASE"/pytest-[0-9]* 2>/dev/null | wc -l)
detail="pruned $pruned of $total dir(s), freed ${freed_mb} MB, $remain remain"
[ "$skipped_locked" -gt 0 ] && detail="$detail, $skipped_locked skipped (lock <3h — live run)"

if [ "$failed" -gt 0 ]; then
  say FAIL "$detail, $failed could NOT be removed"
  exit 1
fi
if [ "$freed_mb" -ge "$CONCERN_MB" ]; then
  # Routine pruning is OK and stays quiet. Freeing a GB+ means accumulation
  # outran this cadence — the condition that actually cost memory. Say it, and
  # self-clear next run (the fleet_hosts_drift convention).
  say CONCERN "$detail — accumulation outran the prune cadence"
  exit 0
fi
say OK "$detail"
exit 0
