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

# ── pytest LOG-LEVEL lines are not verdict lines: live/captured logging pads
#    the level to 8 chars ("ERROR    logger:file.py:N msg"), while real verdict
#    lines are "FAILED <nodeid>"/"ERROR <nodeid>" with ONE space. CI's first
#    run through the classifier failed a 10000-passed suite on 203 padded
#    lines (2026-07-31, run 30669844039). ──────────────────────────────────────
run 0 'ERROR    gateway.client:client.py:469 Failed to send want_config_id\n10000 passed, 49 skipped in 231.48s\n'
check "padded log-level ERROR lines do not fail a green run" "$(has "$out" '^PASS')"
run 0 'ERROR tests/test_a.py - ImportError: boom\n1 error in 1.00s\n'
check "a real single-space ERROR verdict line still fails the run" "$(has "$out" '^FAIL')"
check "  ...and is named" "$(has "$out" 'tests/test_a.py')"

# ── A NON-SUMMARY line must not be mistaken for the summary. CI run
#    30845638291 (2026-08-03) classified a 10117-passed 3.9 suite as FAIL:
#    `summ` takes the LAST line matching the count pattern, and background
#    output landing after the real summary included mini's liveness detail
#    "fire_count=0 error_count=0" — whose substring "0 error" matched both
#    `[0-9]+ (passed|failed|error)` and the nsumbad probe. The verdict line
#    read "FAIL exit 0, 0 FAILED/ERROR", i.e. it announced zero failures
#    while failing. Word-boundary the count words, and require the line to
#    actually look like a pytest summary. ─────────────────────────────────────
run 0 '10117 passed, 49 skipped in 344.40s\n  [OK ] liveness: ticking — last tick 0s ago, fire_count=0 error_count=0.\n'
check "trailing 'error_count=0' noise does not fail a green run" "$(has "$out" '^PASS')"
check "  ...and the REAL summary is quoted, not the noise" "$(has "$out" '10117 passed')"

# the same shape must not HIDE a real failure either (don't over-correct)
run 0 '1 failed, 10 passed in 2.00s\n  [OK ] liveness: ticking — fire_count=0 error_count=0.\n'
check "trailing noise does not mask a genuinely failed summary" "$(has "$out" '^FAIL')"

# a real plural "errors" summary is still a failure
run 0 '1 failed, 2 errors in 1.00s\n'
check "a real 'N errors' summary still reads FAIL" "$(has "$out" '^FAIL')"

# ── a BINARY log still classifies. The log goes binary in CI (a test emits a
#    NUL); plain grep then reports "binary file matches" and yields no lines,
#    so every signal here silently reads zero. ci.yml already learned this and
#    uses `grep -aE` — this script did not, which is the sibling-consumer
#    drift its own header warns about, turned on itself. ──────────────────────
printf '10078 passed, 1 skipped in 244.82s\n' > "$TMP/binlog"
printf 'stray \000 nul byte from a test\n' >> "$TMP/binlog"
out="$("$VERDICT" --log "$TMP/binlog" --rc 0)"; rc_out=$?
check "binary log with a healthy summary reads PASS" "$(has "$out" '^PASS')"

printf 'FAILED tests/test_a.py::test_x - assert\n1 failed, 9 passed in 1.00s\n' > "$TMP/binlog2"
printf 'stray \000 nul byte\n' >> "$TMP/binlog2"
out="$("$VERDICT" --log "$TMP/binlog2" --rc 1)"; rc_out=$?
check "binary log with real failures still reads FAIL" "$(has "$out" '^FAIL')"
check "  ...and still names the failing test" "$(has "$out" 'test_a.py::test_x')"

# ── THE ACTUAL CI FAILURE (run 30845638291 + its rerun, 2026-08-03), which
#    needs BOTH defects and a log big enough to span grep's read buffer:
#    a NUL lands mid-log, grep prints matches up to it and then stops with
#    "binary file matches", so `tail -1` returns the last match BEFORE the nul
#    -- a liveness line -- and the real summary further down is never seen.
#    The loose count pattern then accepted "fire_count=0 error_count=0" as a
#    summary and read its "0 error" as failures. Verdict: FAIL on a
#    10117-passed suite, while announcing "0 FAILED/ERROR".
#    A SMALL binary file does not reproduce it (grep decides binary within the
#    first buffer and prints nothing => UNKNOWN), which is why this case pads.
{
  printf '  [OK ] liveness: ticking — last tick 0s ago, fire_count=0 error_count=0.\n'
  awk 'BEGIN{for(i=0;i<4000;i++) print "tests/test_filler.py::test_" i " PASSED padding"}'
  printf 'a test emitted a nul \000 byte here\n'
  printf '================ 10117 passed, 49 skipped in 394.07s (0:06:34) =================\n'
} > "$TMP/ci_shape.log"
out="$("$VERDICT" --log "$TMP/ci_shape.log" --rc 0 2>/dev/null)"; rc_out=$?
check "nul-truncated grep does not turn a passing suite into FAIL" "$(has "$out" '^PASS')"
check "  ...and the REAL summary past the nul is found" "$(has "$out" '10117 passed')"

# the same shape must still report a genuine failure that sits past the nul
{
  printf '  [OK ] liveness: ticking — fire_count=0 error_count=0.\n'
  awk 'BEGIN{for(i=0;i<4000;i++) print "tests/test_filler.py::test_" i " PASSED padding"}'
  printf 'a test emitted a nul \000 byte here\n'
  printf 'FAILED tests/test_z.py::test_q - assert\n'
  printf '================ 1 failed, 10116 passed in 394.07s =================\n'
} > "$TMP/ci_shape_bad.log"
out="$("$VERDICT" --log "$TMP/ci_shape_bad.log" --rc 1 2>/dev/null)"; rc_out=$?
check "a real failure past the nul is still FAIL" "$(has "$out" '^FAIL')"
check "  ...and is named" "$(has "$out" 'test_z.py::test_q')"

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
