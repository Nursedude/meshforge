#!/usr/bin/env bash
# Behavior test for honest_status.sh's SUITE leg (2026-07-28).
#
# WHY: pytest's process exit status is not trustworthy on this fleet. Measured
# on the full suite — the interpreter exits 0 while pytest's own
# pytest_sessionfinish hook reports `ExitCode.TESTS_FAILED: 1`, testsfailed=1.
# Byte-identical output, ~50% of runs, and it disappears when instrumentation
# adds work at shutdown, so it is a shutdown race, not bookkeeping.
#
# The gate survived that by luck: it also required nfail==0, and those runs
# printed FAILED lines. It would NOT have survived a lost exit code beside an
# INTERNALERROR (which starts "INTERNALERROR>" and matches neither ^FAILED nor
# ^ERROR) or a crash with no summary at all. Those cases are pinned below —
# they read PASS under the old logic.
#
# Drives the REAL script with a stub pytest whose OUTPUT and EXIT CODE are set
# independently, which is the only way to reproduce "says failed, exits 0".
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# Fake repo — the real one's venv/bin/python is absolute and would route
# around this PATH stub (see test_honest_status_preserve.sh).
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then
    printf '%b' "${FAKE_PYTEST_OUT:-}"
    exit "${FAKE_PYTEST_RC:-0}"
  fi
done
exec "$REAL_PYTHON3" "$@"
EOF
for c in gh ssh curl; do printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/$c"; done
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"

run() {  # $1 = fake rc, $2 = fake stdout
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="hs-dummy" \
    MESHFORGE_REPO="$FAKE_REPO" FAKE_PYTEST_RC="$1" FAKE_PYTEST_OUT="$2" \
    bash "$SCRIPT" 2>&1 | grep "full suite"
}

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# ── the measured bug: exit code lost, output says it failed ───────────────
out="$(run 0 'FAILED tests/t.py::test_x - boom\n1 failed, 10 passed in 1.00s\n')"
check "lost exit code + FAILED lines => FAIL" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── THE HOLE: lost exit code + INTERNALERROR (matches neither ^FAILED nor
#    ^ERROR) and no summary. Old logic: rc=0, nfail=0 => PASS. ─────────────
out="$(run 0 'INTERNALERROR> Traceback (most recent call last):\nINTERNALERROR> RuntimeError: plugin blew up\n')"
check "lost exit code + INTERNALERROR is never PASS" \
  "$(echo "$out" | grep -q 'PASS' && echo '' || echo ok)"

# ── THE OTHER HOLE: crash with no summary at all. ────────────────────────
out="$(run 0 '')"
check "no summary line => UNKNOWN, not PASS" \
  "$(echo "$out" | grep -q 'UNKNOWN' && echo ok)"

# ── summary says failed but no FAILED lines (torn/-q output) ─────────────
out="$(run 0 '1 failed, 3 passed in 0.50s\n')"
check "summary reporting failures => FAIL even with rc=0" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── disagreement the other way: clean output, non-zero code ──────────────
out="$(run 1 '10 passed in 1.00s\n')"
check "nonzero exit with clean output => FAIL (trust the worse signal)" \
  "$(echo "$out" | grep -q 'FAIL' && echo ok)"

# ── a broken invocation must not read green ──────────────────────────────
out="$(run 0 'no tests ran in 0.01s\n')"
check "'no tests ran' => UNKNOWN, nothing was verified" \
  "$(echo "$out" | grep -q 'UNKNOWN' && echo ok)"

# ── and the genuinely healthy run still passes ───────────────────────────
out="$(run 0 '9840 passed, 1 skipped in 232.49s\n')"
check "healthy suite still reads PASS" \
  "$(echo "$out" | grep -q 'PASS' && echo ok)"

# ── non-green runs preserve the log for forensics ────────────────────────
logf="$FAKE_HOME/.local/state/meshforge/hs_failures/last_failure.log"
rm -f "$logf"
run 0 'INTERNALERROR> boom\n' >/dev/null
check "an UNKNOWN/FAIL run preserves the pytest log" \
  "$([ -f "$logf" ] && echo ok)"

echo "---"
if [ "$fails" = 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED"; exit 1; fi
