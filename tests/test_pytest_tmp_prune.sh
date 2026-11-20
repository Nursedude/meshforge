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
# Every invocation below must prune a FIXTURE, never the live session scratch
# under /tmp/claude-<uid> — exported so no call site can forget it.
export CLAUDE_TMP_BASE="$TMP/claude-none"

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

# 5–7. PER-LOCK owner liveness (2026-11-20). pytest writes its OWN pid into
# .lock (_pytest/pathlib.py create_cleanup_lock). A killed run (SIGTERM /
# SIGKILL / pytest-timeout's thread method) never removes it, and the old rule
# — "ANY pytest running => skip EVERY fresh lock" — gave the prune no window on
# a test-heavy box: 2026-11-20 00:11 freed 1,367 MB in one burst (CONCERN),
# 06:11 skipped 71 while 93 locks named DEAD pids. Now a fresh lock whose pid
# is well-formed and NOT alive is stranded => pruned even while others run.
# Anything else (live pid, empty, garbage) is KEPT — unknown is never "dead".
mklockpid() {  # $1 = run number, $2 = lock content (written verbatim)
  mkrun "$1" 60; printf '%s' "$2" > "$BASE/pytest-$1/.lock"
  touch -d "@$(( $(date +%s) - 60 ))" "$BASE/pytest-$1/.lock"
}
sleep 0 & DEADPID=$!; wait "$DEADPID" 2>/dev/null
[ -d "/proc/$DEADPID" ] && DEADPID=""          # reused already: case can't be built
sleep 30 & LIVEPID=$!

BASE="$TMP/live5"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
if [ -n "$DEADPID" ]; then
  mklockpid 1 "$DEADPID"
  out="$(run_pgrep 0)"        # a pytest IS running — but not THIS lock's owner
  check "fresh lock, owner pid DEAD, other pytest live => PRUNED (stranded)" \
    "$([ -d "$BASE/pytest-1" ] && echo '' || echo ok)"
  check "and the dead-owner reclaim is stated" \
    "$(echo "$out" | grep -q 'owner pid dead' && echo ok)"
  # Fable review 2026-11-20: the reclaim was ALSO counted as "stranded (… no
  # live pytest …)" — a falsehood on a run where a pytest WAS live, and 2N.
  check "dead-owner reclaim is counted ONCE, under its own words" \
    "$(echo "$out" | grep -q '1 stranded though a pytest may be running' && echo ok)"
  check "and never claims 'no live pytest' while one runs" \
    "$(echo "$out" | grep -q 'no live pytest' && echo '' || echo ok)"
else
  check "dead-pid fixture could be built (pid reused instantly)" ""
fi

BASE="$TMP/live6"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mklockpid 1 "$LIVEPID"
out="$(run_pgrep 0)"
check "fresh lock, owner pid ALIVE => SKIPPED (its run may be live)" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"
kill "$LIVEPID" 2>/dev/null; wait "$LIVEPID" 2>/dev/null

# 7. content we cannot read as ONE positive pid => keep (never infer "dead").
for content in "" "abc" "0" "-5" "12 34" "1e3" "$(printf '42\n43')" "12345678901"; do
  BASE="$TMP/live7-$RANDOM"; mkdir -p "$BASE"
  for n in 1 2 3 4 5; do mkrun $n; done
  mklockpid 1 "$content"
  run_pgrep 0 >/dev/null
  check "fresh lock with unparseable owner '$(printf '%s' "$content" | tr '\n' '|')' + live pytest => KEPT" \
    "$([ -d "$BASE/pytest-1" ] && echo ok)"
done

# 7b. bytes the shell would silently DROP before the digit test sees them
#     (NUL; a trailing newline) — the file must be exactly the digits read.
if [ -n "$DEADPID" ]; then
  for shape in nul-inside nul-after newline-after; do
    BASE="$TMP/live7b-$shape"; mkdir -p "$BASE"
    for n in 1 2 3 4 5; do mkrun $n; done
    mkrun 1 60
    case "$shape" in
      nul-inside)    printf '%s\0%s' "${DEADPID:0:1}" "${DEADPID:1}" > "$BASE/pytest-1/.lock" ;;
      nul-after)     printf '%s\0\0' "$DEADPID" > "$BASE/pytest-1/.lock" ;;
      newline-after) printf '%s\n' "$DEADPID" > "$BASE/pytest-1/.lock" ;;
    esac
    touch -d "@$(( $(date +%s) - 60 ))" "$BASE/pytest-1/.lock"
    run_pgrep 0 >/dev/null
    check "lock '$shape' (not exactly the digits) + live pytest => KEPT" \
      "$([ -d "$BASE/pytest-1" ] && echo ok)"
  done
fi

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

# ── Claude session scratch: idle session dirs are RAM too (2026-10-13) ───
#
# /tmp/claude-<uid>/<project>/<session-uuid>/ is never cleaned up; on the
# manager 49 of them held 642 MB of tmpfs while user.slice paged
# memory_cap_engaged. A dir is pruned only when NOTHING inside it changed for
# the idle window, and only when its name is a session UUID — the shared
# pycache tree beside them is not ours to delete.
CB="$TMP/claude"; CLAUDE_TMP_BASE="$CB"
OLD_S="11111111-2222-3333-4444-555555555555"
LIVE_S="66666666-7777-8888-9999-aaaaaaaaaaaa"
mkdir -p "$CB/-opt-meshforge/$OLD_S/scratchpad" "$CB/-opt-meshforge/$LIVE_S/scratchpad" \
         "$CB/pycache/opt"
echo x > "$CB/-opt-meshforge/$OLD_S/scratchpad/f"
echo x > "$CB/-opt-meshforge/$LIVE_S/scratchpad/old"
echo x > "$CB/-opt-meshforge/$LIVE_S/scratchpad/new"
echo x > "$CB/pycache/opt/m.pyc"
three_days_ago="@$(( $(date +%s) - 3*86400 ))"
find "$CB" -exec touch -h -d "$three_days_ago" {} +
touch "$CB/-opt-meshforge/$LIVE_S/scratchpad/new"   # one fresh file = in use
BASE="$TMP/no-pytest-tree"                            # pytest leg has nothing
out="$(run_pgrep 1)"
check "idle session scratch dir is pruned" \
  "$([ -d "$CB/-opt-meshforge/$OLD_S" ] && echo '' || echo ok)"
check "a session dir with ANY fresh file is kept" \
  "$([ -d "$CB/-opt-meshforge/$LIVE_S" ] && echo ok)"
check "non-session dirs (pycache) are never touched" \
  "$([ -f "$CB/pycache/opt/m.pyc" ] && echo ok)"
check "the scratch leg runs even when there is no pytest tree" \
  "$(echo "$out" | grep -q 'claude scratch: pruned 1 of 2' && echo ok)"

# ── a scratch dir that cannot be removed NAMES itself (2026-10-18) ───────
#
# Measured on the manager: a reviewer session ran a drill under sudo with HOME
# in its scratchpad, leaving root-owned dirs the user cron can never delete.
# The verdict said "1 could NOT be removed, 1 could NOT be removed" — the
# count twice, and neither which dir nor why — so triage meant re-deriving
# both by hand. A failure must name the stuck path, its owner, and the command
# that clears it. (Root ownership can't be fixtured unprivileged; a 555 dir
# takes the same code path: the first non-writable dir left behind.)
if [ "$(id -u)" != 0 ]; then
  STUCK_S="bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
  mkdir -p "$CB/-opt-meshforge/$STUCK_S/scratchpad/ro"
  echo x > "$CB/-opt-meshforge/$STUCK_S/scratchpad/ro/f"
  chmod 555 "$CB/-opt-meshforge/$STUCK_S/scratchpad/ro"
  find "$CB/-opt-meshforge/$STUCK_S" -exec touch -h -d "$three_days_ago" {} +
  BASE="$TMP/stuck-tree"; mkdir -p "$BASE"; mkrun 1   # full path: both legs speak
  out="$(run_pgrep 1)"
  chmod 755 "$CB/-opt-meshforge/$STUCK_S/scratchpad/ro"   # let EXIT cleanup work
  check "an unremovable scratch dir is a FAIL" \
    "$(echo "$out" | grep -q 'FAIL' && echo ok)"
  check "the failure count is said ONCE, not once per leg" \
    "$([ "$(echo "$out" | grep -o 'could NOT be removed' | wc -l)" = 1 ] && echo ok)"
  check "the FAIL names the stuck path" \
    "$(echo "$out" | grep -q "$STUCK_S/scratchpad/ro" && echo ok)"
  check "the FAIL names its owner and the command that clears it" \
    "$(echo "$out" | grep -q "owner $(id -un)" && echo "$out" | grep -q 'chmod -R u+rwx' && echo ok)"
fi
CLAUDE_TMP_BASE="$TMP/claude-none"

# ── a failed removal must not READ AS LIVE on the next run (2026-11-03) ──
#
# Measured on the manager: rm -rf on a stuck session dir deletes everything it
# CAN, which bumps the parent dirs' mtimes. The idle check counted dir mtimes,
# so the pruner's OWN failed attempt read as "a live session wrote here": the
# next run skipped the dir and said OK, and the FAIL returned 2 days later — a
# flap that reads as self-healing. Controls: a stuck tree with a fresh FILE is
# still live, and a fresh EMPTY session dir (no blocker) is still kept.
if [ "$(id -u)" != 0 ]; then
  CB="$TMP/claude-flap"; CLAUDE_TMP_BASE="$CB"
  FLAP_S="12121212-3434-5656-7878-909090909090"
  LIVEST_S="abababab-cdcd-efef-0101-232323232323"
  EMPTY_S="45454545-6767-8989-0a0a-bcbcbcbcbcbc"
  for s in "$FLAP_S" "$LIVEST_S"; do
    mkdir -p "$CB/-p/$s/scratchpad/drill/ro" "$CB/-p/$s/scratchpad/other"
    echo x > "$CB/-p/$s/scratchpad/drill/ro/f"
    echo x > "$CB/-p/$s/scratchpad/other/g"
    chmod 555 "$CB/-p/$s/scratchpad/drill/ro"
  done
  mkdir -p "$CB/-p/$EMPTY_S/scratchpad"
  find "$CB" -exec touch -h -d "$three_days_ago" {} +
  touch -h "$CB/-p/$EMPTY_S/scratchpad" "$CB/-p/$EMPTY_S"   # brand-new, no files yet
  echo y > "$CB/-p/$LIVEST_S/scratchpad/other/fresh"        # live: a fresh FILE
  BASE="$TMP/no-pytest-tree"
  out1="$(run_pgrep 1)"
  out2="$(run_pgrep 1)"                                      # the run that flapped
  chmod 755 "$CB/-p/$FLAP_S/scratchpad/drill/ro" "$CB/-p/$LIVEST_S/scratchpad/drill/ro"
  check "flap: first run on a stuck idle dir is a FAIL" \
    "$(echo "$out1" | grep -q 'FAIL' && echo ok)"
  check "flap: the NEXT run still FAILs (its own failed rm is not 'live')" \
    "$(echo "$out2" | grep -q 'FAIL' && echo "$out2" | grep -q "$FLAP_S" && echo ok)"
  check "flap control: a stuck tree with a fresh FILE is kept (live)" \
    "$([ -f "$CB/-p/$LIVEST_S/scratchpad/other/fresh" ] && echo ok)"
  check "flap control: a fresh EMPTY session dir is kept" \
    "$([ -d "$CB/-p/$EMPTY_S/scratchpad" ] && echo ok)"
  CLAUDE_TMP_BASE="$TMP/claude-none"
fi

# ── the named cure must actually CLEAR the blocker (review F4, 2026-10-18) ─
#
# chmod -R u+w cannot descend a 000/111 dir (what permission tests leave), and
# a writable-but-unreadable (300) dir blocks rm without being "non-writable".
# Run the printed cure literally and require the dir to be gone.
if [ "$(id -u)" != 0 ]; then
  for mode in 000 111 300; do
    BASE="$TMP/cure$mode"; mkdir -p "$BASE"
    for n in 1 2 3 4 5; do mkrun $n; done
    chmod "$mode" "$BASE/pytest-1/some_test0"
    out="$(run_pgrep 1)"
    check "mode $mode blocker is named (not 'no non-writable dir found')" \
      "$(echo "$out" | grep -q 'pytest-1/some_test0 (owner' && echo ok)"
    cure="$(echo "$out" | grep -m1 -o 'clear: chmod.*' | sed "s/^clear: //; s/;.*//")"
    [ -n "$cure" ] && bash -c "$cure" >/dev/null 2>&1
    check "mode $mode: the printed cure removes the run dir" \
      "$([ -d "$BASE/pytest-1" ] && echo '' || echo ok)"
    [ -d "$BASE/pytest-1/some_test0" ] && chmod 755 "$BASE/pytest-1/some_test0"
  done
fi

# ── full-tree EXPORTS inside a LIVE session are RAM too (2026-11-16) ──────
#
# Measured on the manager: the session pass keeps a dir while ANY file in it is
# fresh (correct — a live session writes there), so review/mutant/replay
# exports — full copies of the repo, ~60 MB each — accumulated INSIDE sessions
# younger than the idle window: 9 in one session + 14 in four 1-2-day-old ones,
# ~1.6 GB of tmpfs; user.slice hit 8.51/8.59 GB and paged the operator. An
# export is a scratchpad child holding a repo tree (src/launcher_tui); it goes
# when nothing inside changed for CLAUDE_TMP_EXPORT_IDLE_H. Controls: a FRESH
# export, an old non-tree dir (a drill/notes dir), and an old loose draft all
# stay — drafts are not regenerable.
CB="$TMP/claude-exports"; CLAUDE_TMP_BASE="$CB"
EX_S="13131313-2424-3535-4646-575757575757"
SP="$CB/-opt-meshforge/$EX_S/scratchpad"
mkdir -p "$SP/review_old/tree/src/launcher_tui" "$SP/mut_old/src/launcher_tui" \
         "$SP/review_fresh/tree/src/launcher_tui" "$SP/notes_old/sub" "$SP/drv"
echo x > "$SP/review_old/tree/src/launcher_tui/main.py"
echo x > "$SP/mut_old/src/launcher_tui/main.py"
echo x > "$SP/review_fresh/tree/src/launcher_tui/main.py"
echo x > "$SP/notes_old/sub/finding.md"
echo x > "$SP/draft.md"
two_days_ago="@$(( $(date +%s) - 30*3600 ))"
find "$CB" -exec touch -h -d "$two_days_ago" {} +
touch "$SP/review_fresh/tree/src/launcher_tui/main.py"   # an export still in use
echo y > "$SP/drv/live.py"                               # the session itself is LIVE
BASE="$TMP/no-pytest-tree"
out="$(run_pgrep 1)"
check "exports: an old tree export inside a live session is pruned" \
  "$([ -d "$SP/review_old" ] && echo '' || echo ok)"
check "exports: an old export with src/ at its top level is pruned too" \
  "$([ -d "$SP/mut_old" ] && echo '' || echo ok)"
check "exports: a FRESH tree export is kept" \
  "$([ -f "$SP/review_fresh/tree/src/launcher_tui/main.py" ] && echo ok)"
check "exports: an old NON-tree dir (notes, drills) is kept" \
  "$([ -f "$SP/notes_old/sub/finding.md" ] && echo ok)"
check "exports: an old loose draft is kept (not regenerable)" \
  "$([ -f "$SP/draft.md" ] && echo ok)"
check "exports: the live session itself is kept" \
  "$([ -f "$SP/drv/live.py" ] && echo ok)"
check "exports: the verdict SAYS what it pruned" \
  "$(echo "$out" | grep -q 'tree exports: pruned 2' && echo ok)"
CLAUDE_TMP_BASE="$TMP/claude-none"


# 8. Fable round 2 (2026-11-20). (a) a .lock that is NOT a regular file must
#    never be OPENED: a FIFO blocked `head` and hung the cron with no verdict
#    (the old code only stat'ed it); a symlink was followed to a dead pid.
#    (b) no pytest seen by pgrep (a pytest.main() driver does not match its
#    pattern) but the lock names a LIVE pid => KEEP — it used to be deleted
#    as "stranded (no live pytest)" mid-run.
BASE="$TMP/live8a"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkdir -p "$BASE/pytest-1/some_test0"; mkfifo "$BASE/pytest-1/.lock"
touch -h -d "@$(( $(date +%s) - 60 ))" "$BASE/pytest-1/.lock"
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB/pgrep"; chmod +x "$STUB/pgrep"
out="$(PATH="$STUB:$PATH" PYTEST_TMP_BASE="$BASE" CRON_VERDICT_LOG="$VERDICTS" \
  CRON_VERDICT_BIN="$HERE/../scripts/cron_verdict.sh" HOME="$TMP" \
  timeout 20 bash "$SCRIPT" 2>&1)"; rc=$?
check "a FIFO .lock + live pytest: the run FINISHES (rc $rc) and speaks" \
  "$(echo "$out" | grep -q 'pytest_tmp_prune: ' && echo ok)"
check "and the FIFO's dir is KEPT" "$([ -d "$BASE/pytest-1" ] && echo ok)"
if [ -n "$DEADPID" ]; then
  BASE="$TMP/live8s"; mkdir -p "$BASE"
  for n in 1 2 3 4 5; do mkrun $n; done
  printf '%s' "$DEADPID" > "$TMP/deadpid.target"
  mkdir -p "$BASE/pytest-1/some_test0"; ln -s "$TMP/deadpid.target" "$BASE/pytest-1/.lock"
  touch -h -d "@$(( $(date +%s) - 60 ))" "$BASE/pytest-1/.lock"
  run_pgrep 0 >/dev/null
  check "a SYMLINK .lock (even to a dead pid) + live pytest => KEPT" \
    "$([ -d "$BASE/pytest-1" ] && echo ok)"
fi
sleep 30 & LIVEPID2=$!
BASE="$TMP/live8b"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mklockpid 1 "$LIVEPID2"
out="$(run_pgrep 1)"          # pgrep: NO pytest seen — but the lock's owner lives
check "no pytest per pgrep, lock owner ALIVE => KEPT (pytest.main driver case)" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"
check "and it is not called 'stranded'" \
  "$(echo "$out" | grep -q 'stranded' && echo '' || echo ok)"
kill "$LIVEPID2" 2>/dev/null; wait "$LIVEPID2" 2>/dev/null

# 9. Fable r3 residuals (2026-11-20).
# 9a. PIN pytest's lock FORMAT — the reclaim rests on it. If a future pytest
#     wrote 'pid\n' (or anything but bare digits) every dead-owner lock would
#     read as "unparseable => keep" and the prune would silently go back to
#     skipping them all. A REAL pytest reads its own basetemp lock.
TR="$TMP/temproot"; mkdir -p "$TR" "$TMP/probe9"
cat > "$TMP/probe9/test_lockfmt.py" <<'PY'
import os
def test_lock_is_bare_pid(tmp_path):
    lock = tmp_path.parent / ".lock"          # basetemp/.lock; tmp_path = basetemp/<test>0
    data = lock.read_bytes()
    assert data == str(os.getpid()).encode(), (data, os.getpid())
PY
# hermetic (Fable r4): an inherited PYTEST_ADDOPTS (e.g. --basetemp) or an
# ancestor config must not steer the probe — `-c /dev/null`, ADDOPTS unset.
env -u PYTEST_ADDOPTS PYTEST_DEBUG_TEMPROOT="$TR" timeout 120 python3 -m pytest \
  -q -p no:cacheprovider -c /dev/null --rootdir "$TMP/probe9" \
  "$TMP/probe9/test_lockfmt.py" >"$TMP/probe9.out" 2>&1
check "pytest's own .lock is EXACTLY its pid's digits (the format _lock_pid reads)" \
  "$(grep -q '1 passed' "$TMP/probe9.out" && echo ok)"

# 9b. a DANGLING-symlink lock is a lock, not "no lock": kept while a pytest
#     may run (it used to fail `[ -e ]` and be pruned in every mode).
BASE="$TMP/live9b"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkdir -p "$BASE/pytest-1/some_test0"; ln -s "$TMP/no-such-target" "$BASE/pytest-1/.lock"
touch -h -d "@$(( $(date +%s) - 60 ))" "$BASE/pytest-1/.lock"
run_pgrep 0 >/dev/null
check "dangling-symlink .lock + live pytest => KEPT" "$([ -d "$BASE/pytest-1" ] && echo ok)"

# 9c. say what was MEASURED: a pgrep error is "liveness UNKNOWN", never "IS running".
BASE="$TMP/live9c"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 60
out="$(run_pgrep 2)"
check "pgrep error: the skip says liveness UNKNOWN" \
  "$(echo "$out" | grep -q 'liveness UNKNOWN' && echo ok)"
check "pgrep error: the skip never claims 'pytest IS running'" \
  "$(echo "$out" | grep -q 'pytest IS running' && echo '' || echo ok)"
BASE="$TMP/live9d"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mkrun 1 60
out="$(run_pgrep 0)"
check "pgrep match: the skip still says 'pytest IS running'" \
  "$(echo "$out" | grep -q 'pytest IS running' && echo ok)"

# 9e. an UNREADABLE lock mtime reads as FRESH, never "ancient" (Fable r4: the
#     fix had no test — reverting it to `|| echo 0` stayed green). The lock is
#     REALLY 4 days old, but a stub `stat` fails on %Y: the old fallback (age =
#     now - 0) pruned it as stale; now it is fresh + unparseable => KEPT while
#     a pytest runs.
BASE="$TMP/live9e"; mkdir -p "$BASE"
for n in 1 2 3 4 5; do mkrun $n; done
mklockpid 1 "not-a-pid"; touch -d "@$(( $(date +%s) - 4 * 86400 ))" "$BASE/pytest-1/.lock"
printf '#!/usr/bin/env bash\ncase "$*" in *%%Y*) exit 1 ;; esac\nexec /usr/bin/stat "$@"\n' > "$STUB/stat"
chmod +x "$STUB/stat"
run_pgrep 0 >/dev/null
rm -f "$STUB/stat"
check "unreadable lock mtime + live pytest => KEPT (fresh, not 'ancient')" \
  "$([ -d "$BASE/pytest-1" ] && echo ok)"
# ── it leaves a cron verdict so #78 can see it ───────────────────────────
check "writes a cron_verdict line under its own name" \
  "$(grep -q 'pytest_tmp_prune' "$VERDICTS" 2>/dev/null && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
