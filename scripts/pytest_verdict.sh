#!/usr/bin/env bash
# pytest_verdict.sh — classify a pytest run from its LOG plus its exit code,
# because on this fleet neither one alone is trustworthy.
#
# WHY (measured 2026-07-28, honest_status.sh): on the full suite the
# interpreter exits 0 while pytest's own pytest_sessionfinish reports
# `ExitCode.TESTS_FAILED: 1`. Byte-identical output, ~50% of runs; it vanishes
# when instrumentation adds work at shutdown, so it is a race in interpreter
# shutdown (the suite leaks ~25 non-daemon ThreadPoolExecutor workers joined
# there). pytest computed 1; the kernel reported 0. **The flap only loses
# failures TOWARD zero** — it never invents a nonzero — so a bare `rc=$?` fails
# in the false-GREEN direction, which is the direction that matters.
#
# honest_status.sh has carried this logic inline since 2026-07-28. This script
# exists because its SIBLING consumers did not: calibration_reverify.sh ran the
# same full-suite invocation, trusted `pyrc=$?` alone, and wrote held/broke
# verdicts into the calibration ledger from it (audited 2026-07-31 — 21 of 34
# `held` verdicts were minted through that unguarded path). A cure that reaches
# one consumer of a phenomenon and not its sibling is honest_failure_modes #5.
#
# Usage:
#   pytest_verdict.sh --log <file> --rc <n>     # classify an existing run
#   pytest_verdict.sh --run -- <pytest args...> # run, then classify
#
# Prints one line to stdout:  PASS|FAIL|UNKNOWN<TAB><reason>
# Exit code: 0 = PASS, 1 = FAIL, 2 = UNKNOWN.
#
# UNKNOWN is NEVER a pass (calibrated_claims): a suite that did not report is
# unobservable, not healthy.
set -u

LOG=""; RC=""; MODE="classify"
while [ $# -gt 0 ]; do
  case "$1" in
    --log) LOG="${2:-}"; shift 2 ;;
    --rc)  RC="${2:-}";  shift 2 ;;
    --run) MODE="run"; shift; [ "${1:-}" = "--" ] && shift; break ;;
    *) echo "pytest_verdict: unknown arg '$1'" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "run" ]; then
  LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
  "${PYTEST_VERDICT_PY:-python3}" -m pytest "$@" >"$LOG" 2>&1; RC=$?
fi

[ -n "$LOG" ] && [ -n "$RC" ] || {
  echo "usage: pytest_verdict.sh --log <file> --rc <n> | --run -- <args>" >&2
  exit 2
}
[ -r "$LOG" ] || { printf 'UNKNOWN\tlog %s unreadable — cannot classify\n' "$LOG"; exit 2; }

# Signals. Each grep is ANCHORED: the suite's own shell harnesses print
# "INTERNALERROR> boom" as fixture output mid-line, and an unanchored count
# flipped green runs to FAIL on display noise (2026-07-28 review).
summ=$(grep -E "[0-9]+ (passed|failed|error)|no tests ran" "$LOG" | tail -1)
nfail=$(grep -cE "^FAILED|^ERROR" "$LOG")
ninternal=$(grep -cE "^INTERNALERROR" "$LOG")
nsumbad=$(printf '%s' "$summ" | grep -cE "[0-9]+ (failed|errors?)")
nsumok=$(printf '%s' "$summ" | grep -cE "[0-9]+ passed")
names=$(grep -E "^FAILED|^ERROR" "$LOG" | sed -E 's/^(FAILED|ERROR) //; s/ -.*//' \
        | head -3 | paste -sd' ' -)
intern=""; [ "$ninternal" != 0 ] && intern=", $ninternal INTERNALERROR"

# Branch order is load-bearing and mirrors honest_status.sh exactly; a drift-pin
# test runs the same corpus through both. The empty-summary+nonzero case MUST be
# checked first — ordering it after the empty-summary case silently downgraded a
# proven-bad OOM-killed suite from FAIL to UNKNOWN (2026-07-28 review).
if [ -z "$summ" ] && [ "$RC" != 0 ]; then
  printf 'FAIL\texit %s with no pytest summary — suite crashed before reporting\n' "$RC"; exit 1
elif [ -z "$summ" ]; then
  printf 'UNKNOWN\tno pytest summary line — suite did not report (exit %s)\n' "$RC"; exit 2
elif [ "$nfail" != 0 ] || [ "$ninternal" != 0 ] || [ "$nsumbad" != 0 ]; then
  printf 'FAIL\texit %s, %s FAILED/ERROR%s%s — %s\n' \
    "$RC" "$nfail" "$intern" "${names:+ ($names)}" "$summ"; exit 1
elif [ "$RC" != 0 ]; then
  printf 'FAIL\texit %s with no FAILED/ERROR lines — exit code and output disagree — %s\n' \
    "$RC" "$summ"; exit 1
elif [ "$nsumok" = 0 ]; then
  printf 'UNKNOWN\tsummary reports no passing tests — nothing was verified — %s\n' "$summ"; exit 2
else
  printf 'PASS\t%s (exit 0)\n' "$summ"; exit 0
fi
