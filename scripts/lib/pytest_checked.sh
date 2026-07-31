# pytest_checked.sh — source me. Run pytest and classify the result honestly.
#
# WHY: pytest's process exit status is not trustworthy on this fleet. Measured
# 2026-07-28 — on the full suite the interpreter exits 0 while pytest's own
# sessionfinish reports ExitCode.TESTS_FAILED:1, byte-identical output, ~50% of
# runs (a thread-join race at shutdown). It only loses failures TOWARD zero, so
# a bare `rc=$?` fails in the false-GREEN direction.
#
# scripts/pytest_verdict.sh owns the classification; honest_status.sh and
# calibration_reverify.sh already call it. This wrapper exists so the REMAINING
# consumers — the git hooks, healthcheck, harness_audit, pi_sanity_check — pair
# "run pytest" with "classify it" in ONE place instead of five, and so none of
# them can quietly go back to `if pytest ...; then`.
#
# ⚠️ Recorded because the mistake is instructive: two of those consumers pipe
# pytest into `tail` —
#   healthcheck.sh   `if python3 -m pytest ... | tail -50; then`
#   harness_audit.sh `if seed_out="$(... | tail -1)"; then`
# — and were first called VACUOUS on that shape alone, on the reasoning that
# the conditional tests `tail`'s always-zero status. That was WRONG: both files
# set `-o pipefail`, which propagates pytest's non-zero through the pipe. The
# shape is the banned one; the shell option rescues it. Reading a pipeline
# without reading the shell options is the same trust-the-representation error
# this whole wrapper exists to remove, committed while removing it.
#
# What is actually true of all five consumers: they trust pytest's EXIT CODE,
# and the exit code is the thing that flaps. That is reason enough.
#
# Usage:
#   . "$REPO_ROOT/scripts/lib/pytest_checked.sh"
#   if mf_pytest_checked tests/test_x.py -x -q; then ... fi
#   # or branch on all three states:
#   mf_pytest_checked tests/ -q; case $? in 0) ;; 1) ;; 2) ;; esac
#
# Sets: MF_PYTEST_VERDICT (PASS|FAIL|UNKNOWN), MF_PYTEST_WHY (one-line reason),
#       MF_PYTEST_LOG (path to the captured log; caller may keep or delete it).
# Returns: 0 PASS, 1 FAIL, 2 UNKNOWN.
#
# UNKNOWN is NEVER a pass. Callers that treat non-zero as failure get the safe
# behavior by default; callers that want to distinguish "proven bad" from
# "could not tell" branch on 1 vs 2.

mf_pytest_checked() {
    local _py="${MF_PYTEST_PY:-python3}"
    local _root="${MF_REPO_ROOT:-${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo /opt/meshforge)}}"
    local _verdict_sh="${MF_PYTEST_VERDICT_SH:-$_root/scripts/pytest_verdict.sh}"

    MF_PYTEST_LOG="$(mktemp)"
    "$_py" -m pytest "$@" >"$MF_PYTEST_LOG" 2>&1
    local _rc=$?

    if [ ! -x "$_verdict_sh" ]; then
        # Fail CLOSED. The classifier ships in the same commit as every caller,
        # so its absence means a broken tree, not an old one — and an
        # unclassifiable run is never a pass (honest_failure_modes #2).
        MF_PYTEST_VERDICT="UNKNOWN"
        MF_PYTEST_WHY="classifier $_verdict_sh missing or not executable — run unclassified (pytest exit $_rc)"
        return 2
    fi

    local _out
    _out="$("$_verdict_sh" --log "$MF_PYTEST_LOG" --rc "$_rc" 2>/dev/null)"
    MF_PYTEST_VERDICT="$(printf '%s' "$_out" | cut -f1)"
    MF_PYTEST_WHY="$(printf '%s' "$_out" | cut -f2-)"

    case "$MF_PYTEST_VERDICT" in
        PASS) return 0 ;;
        FAIL) return 1 ;;
        *)    MF_PYTEST_VERDICT="UNKNOWN"
              [ -n "$MF_PYTEST_WHY" ] || MF_PYTEST_WHY="classifier produced no verdict (pytest exit $_rc)"
              return 2 ;;
    esac
}
