#!/usr/bin/env bash
# Behavior test for scripts/lib/pytest_checked.sh (2026-07-31).
#
# The wrapper is what the git hooks, healthcheck, harness_audit and
# pi_sanity_check now call instead of `if python3 -m pytest ...; then`. Its job
# is to pair "run pytest" with "classify it honestly" so no caller can drift
# back to trusting a bare exit code — the signal measured to flap 0 on a failed
# full-suite run (2026-07-28).
#
# The case that matters most is the LAST one: a missing classifier must fail
# CLOSED. Every caller uses `if ! mf_pytest_checked ...`, so a helper that
# returned 0 when it could not classify would wave a broken tree straight
# through the "can't push mis-wired work" gate.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

check() {
  if [ "$2" = "0" ]; then printf 'PASS: %s\n' "$1"
  else printf 'FAIL: %s\n' "$1"; fails=$((fails + 1)); fi
}

MF_REPO_ROOT="$REPO"
# shellcheck source=../scripts/lib/pytest_checked.sh
. "$REPO/scripts/lib/pytest_checked.sh"

# A real passing test module and a real failing one — drive the wrapper against
# actual pytest runs rather than a stub, since the pairing it guarantees is
# precisely "the run and the classification came from the same invocation".
cat > "$TMP/test_green.py" <<'EOF'
def test_ok():
    assert True
EOF
cat > "$TMP/test_red.py" <<'EOF'
def test_bad():
    assert False, "deliberate"
EOF

mf_pytest_checked "$TMP/test_green.py" -q; rc=$?
check "a passing run returns 0" "$([ "$rc" = 0 ] && echo 0 || echo 1)"
check "  ...and reports PASS" "$([ "$MF_PYTEST_VERDICT" = PASS ] && echo 0 || echo 1)"
check "  ...and captures a log" "$([ -s "$MF_PYTEST_LOG" ] && echo 0 || echo 1)"
rm -f "$MF_PYTEST_LOG"

mf_pytest_checked "$TMP/test_red.py" -q; rc=$?
check "a failing run returns 1" "$([ "$rc" = 1 ] && echo 0 || echo 1)"
check "  ...and reports FAIL" "$([ "$MF_PYTEST_VERDICT" = FAIL ] && echo 0 || echo 1)"
check "  ...and names the failing test" \
  "$(printf '%s' "$MF_PYTEST_WHY" | grep -q 'test_bad' && echo 0 || echo 1)"
rm -f "$MF_PYTEST_LOG"

# THE ONE THAT MATTERS: classifier gone. Callers use `if ! mf_pytest_checked`,
# so returning 0 here would wave a broken tree through every gate that uses it.
MF_PYTEST_VERDICT_SH="/nonexistent/pytest_verdict.sh" \
  mf_pytest_checked "$TMP/test_green.py" -q; rc=$?
check "a missing classifier does NOT return 0 (fails closed)" \
  "$([ "$rc" != 0 ] && echo 0 || echo 1)"
check "  ...and is UNKNOWN, not PASS" \
  "$([ "$rc" = 2 ] && echo 0 || echo 1)"
rm -f "$MF_PYTEST_LOG" 2>/dev/null

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; fi
echo "FAILED ($fails)"; exit 1
