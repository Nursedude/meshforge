#!/usr/bin/env bash
# Behavior test for the honest_status.sh suite-failure preservation (2026-07-11,
# MeshAnchor GH #144 follow-up, ported to MeshForge for parity). Drives the REAL
# script with a fake `python3` that injects a pytest failure, and asserts:
#   1. the failing run's log is preserved to a durable (non-/tmp) path,
#   2. the failing test id is surfaced in the output line,
#   3. a subsequent GREEN run does NOT clobber the preserved failure log.
# Fleet legs are neutralised (dummy box + ssh/curl/gh stubs → UNKNOWN, offline).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
STATE_SUBDIR="meshforge"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"
cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then
    if [ "${FAKE_PYTEST_FAIL:-1}" = "1" ]; then
      echo "FAILED tests/test_fake_flake.py::test_timing_race - AssertionError: injected"
      echo "1 failed, 42 passed in 3.00s"
      exit 1
    fi
    echo "43 passed in 3.00s"; exit 0
  fi
done
exec "$REAL_PYTHON3" "$@"
EOF
# offline stubs so the fleet/CI legs report UNKNOWN fast (no network/ssh).
for c in gh ssh curl; do printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/$c"; done
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"
logf="$FAKE_HOME/.local/state/$STATE_SUBDIR/hs_failures/last_failure.log"

# A FAKE repo, not the real one (2026-07-28). honest_status resolves its
# interpreter to "$REPO/venv/bin/python" when that exists (49c0b703, the
# consumer-of-record port) — an ABSOLUTE path, which routes straight around
# this harness's PATH stub. From that commit until now the fake pytest was
# never invoked, so 4 of the 5 assertions below could not pass and this file
# was a dead test. It went unnoticed because nothing ran it: it is a .sh and
# pytest only collects test_*.py (now wired up by test_honest_status_shell.py).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

run() {  # $1 = FAKE_PYTEST_FAIL
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="hs-test-dummy" \
    MESHFORGE_REPO="$FAKE_REPO" FAKE_PYTEST_FAIL="$1" bash "$SCRIPT" 2>&1
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

out_fail="$(run 1)"
check "persistent failure log written (not /tmp)" "$([ -f "$logf" ] && echo ok)"
check "preserved log holds the FAILED traceback" "$(grep -q 'test_timing_race' "$logf" 2>/dev/null && echo ok)"
check "output line surfaces the failing test id" "$(echo "$out_fail" | grep -q 'test_timing_race' && echo ok)"

before="$(cat "$logf" 2>/dev/null)"
out_ok="$(run 0)"
after="$(cat "$logf" 2>/dev/null)"
check "green run leaves the failure log intact" "$([ -f "$logf" ] && [ "$before" = "$after" ] && echo ok)"
check "green run reports the suite as passing" "$(echo "$out_ok" | grep -Eq 'full suite.*(exit 0|passed)' && echo ok)"

# ── the harness must prove it STUBBED — the failure mode is silence ──────
# This file already uses a FAKE_REPO precisely because honest_status prefers
# $REPO/venv/bin/python, an ABSOLUTE path that routes around the PATH stub
# (49c0b703: 4 of 5 assertions could not pass, and nobody noticed for weeks
# because nothing ran this file). The MA twin still had that defect on
# 2026-07-29 — found running THREE real nested pytest suites instead of the
# fixture. A harness whose stub can be bypassed must SAY so, not go quiet.
check "the fake pytest was actually invoked (stub not bypassed)" \
  "$(echo "$out_ok" | grep -q '43 passed in 3.00s' && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
