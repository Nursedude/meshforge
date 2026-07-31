#!/usr/bin/env bash
# Corpus test for scripts/pytest_verdict.sh (2026-07-31).
#
# WHY: pytest's process exit status is not trustworthy on this fleet. Measured
# 2026-07-28 — on the full suite the interpreter exits 0 while pytest's own
# pytest_sessionfinish reports `ExitCode.TESTS_FAILED: 1`, byte-identical
# output, ~50% of runs. It is a shutdown race, and it only loses failures
# TOWARD zero, so a bare `rc=$?` fails in the false-GREEN direction.
#
# honest_status.sh carried this classification inline from 2026-07-28 until the
# 2026-07-31 convergence; it now CALLS this script, as does
# calibration_reverify.sh. There is one implementation, so there is nothing left
# to drift — this file pins the classifier directly, and
# tests/test_honest_status_suite_leg.sh pins the gate's use of it (including
# that a missing classifier reads UNKNOWN, never PASS).
#
# The corpora are deliberately kept parallel anyway: this one exercises the
# classifier as a unit, that one drives the real gate end-to-end with a stub
# pytest whose OUTPUT and EXIT CODE are set independently — the only way to
# reproduce "says failed, exits 0" against the actual consumer.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
VERDICT="$HERE/../scripts/pytest_verdict.sh"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# run <rc> <log-content> — sets globals $out and $rc_out.
# Deliberately NOT called via $(...): a command substitution runs in a subshell,
# so an rc captured inside it never reaches the caller and every exit-code
# assertion silently degrades to "unbound variable" (caught on first run).
out=""; rc_out=""
run() {
  printf '%b' "$2" > "$TMP/log"
  out="$("$VERDICT" --log "$TMP/log" --rc "$1")"; rc_out=$?
}

check() { # check <name> <condition-result>
  if [ "$2" = "0" ]; then printf '  ok   %s\n' "$1"
  else printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); fi
}
has() { printf '%s' "$1" | grep -q "$2"; echo $?; }

# ── THE HOLE the flap opens: lost exit code beside an INTERNALERROR, which
#    matches neither ^FAILED nor ^ERROR. A bare rc=0 reads this as green. ────
run 0 'INTERNALERROR> boom\n'
check "lost exit code + INTERNALERROR is never PASS" "$(has "$out" '^UNKNOWN\|^FAIL')"
check "  ...and rc reflects not-pass" "$([ "$rc_out" != 0 ] && echo 0 || echo 1)"

# ── THE OTHER HOLE: crash with no summary at all, clean exit code. ──────────
run 0 'Traceback (most recent call last):\n  ...\n'
check "no summary line => UNKNOWN, not PASS" "$(has "$out" '^UNKNOWN')"
check "  ...and UNKNOWN exits 2" "$([ "$rc_out" = 2 ] && echo 0 || echo 1)"

# ── crash WITH a nonzero code is proven-bad, not merely unverified. Ordering
#    matters: putting the empty-summary branch first downgrades an OOM-killed
#    suite from FAIL to UNKNOWN (2026-07-28 review). ──────────────────────────
run 137 'Killed\n'
check "no summary + nonzero exit => FAIL (trust the worse signal)" "$(has "$out" '^FAIL')"
check "  ...and the crash exit code is quoted" "$(has "$out" 'exit 137')"

# ── summary says failed but no FAILED lines (torn / -q output) ──────────────
run 0 '1 failed, 10 passed in 2.00s\n'
check "summary reporting failures => FAIL even with rc=0" "$(has "$out" '^FAIL')"

# ── disagreement the other way: clean output, non-zero code ─────────────────
run 1 '10 passed in 1.00s\n'
check "nonzero exit with clean output => FAIL (trust the worse signal)" "$(has "$out" '^FAIL')"
check "  ...and says the two signals disagree" "$(has "$out" 'disagree')"

# ── a broken invocation must not read green ─────────────────────────────────
run 0 'no tests ran in 0.01s\n'
check "'no tests ran' => UNKNOWN, nothing was verified" "$(has "$out" '^UNKNOWN')"

# ── the genuinely healthy run still passes ──────────────────────────────────
run 0 '10078 passed, 1 skipped in 244.82s\n'
check "healthy suite reads PASS" "$(has "$out" '^PASS')"
check "  ...and PASS exits 0" "$([ "$rc_out" = 0 ] && echo 0 || echo 1)"
check "  ...and quotes the summary" "$(has "$out" '10078 passed')"

# ── real FAILED lines are named, and a plain FAIL does not read ", 0
#    INTERNALERROR" (grep -c prints "0", which is non-empty — the 2026-07-28
#    display bug in the exact line operators quote into calibrated claims) ────
run 1 'FAILED tests/test_a.py::test_x - assert\n1 failed, 9 passed in 1.00s\n'
check "FAIL names the failing test" "$(has "$out" 'test_a.py::test_x')"
check "a plain FAIL does not read ', 0 INTERNALERROR'" \
  "$([ "$(has "$out" '0 INTERNALERROR')" != 0 ] && echo 0 || echo 1)"

# ── the INTERNALERROR count is line-anchored: the suite's own shell harnesses
#    print it mid-line as fixture output, and an unanchored count flipped green
#    runs to FAIL on display noise. ───────────────────────────────────────────
run 0 'fixture harness output: INTERNALERROR> boom (expected)\n10 passed in 1.00s\n'
check "mid-line INTERNALERROR in fixture output does not fail a green run" "$(has "$out" '^PASS')"
run 0 'INTERNALERROR> boom\n10 passed in 1.00s\n'
check "line-anchored INTERNALERROR still fails the run" "$(has "$out" '^FAIL')"
check "  ...and IS counted in the verdict line" "$(has "$out" 'INTERNALERROR')"

# ── an unreadable log is UNKNOWN, never a pass ──────────────────────────────
out="$("$VERDICT" --log "$TMP/definitely-absent" --rc 0)"; rc_out=$?
check "unreadable log => UNKNOWN" "$(has "$out" '^UNKNOWN')"
check "  ...and exits 2" "$([ "$rc_out" = 2 ] && echo 0 || echo 1)"

echo
# "ALL PASS" is the sentinel tests/test_honest_status_shell.py asserts on, in
# ADDITION to the exit code — a harness that silently stops running its cases
# would still exit 0. Emit the convention rather than loosening that check.
if [ "$fails" = 0 ]; then echo "--- "; echo "ALL PASS"; exit 0; fi
echo "test_pytest_verdict: $fails check(s) FAILED"; exit 1
