#!/usr/bin/env bash
# Behavior test: an INTERRUPTED honest_status must report UNKNOWN, never FAIL.
#
# 2026-09-07. The trap was `trap 'rm -rf "$HS_TMP"' EXIT INT TERM` — a handler
# that CLEANS but does not EXIT, so on SIGTERM bash deleted the scratch dir and
# then RESUMED the script. Every later leg read its log as missing:
#   full suite  UNKNOWN  log .../pytest.log unreadable — cannot classify
#   lint        FAIL     exit 1 —
# and the run closed "proven not-green" on a tree whose direct lint was exit 0
# and whose CI was success. A/B on the live script: unfixed exit=1 with 2 FAIL
# lines, fixed exit=2 with 0. This is the SECOND route into the same
# false-NOT-GREEN the fixed-tmp-name comment above HS_TMP describes (07-28) —
# the gate whose whole job is to not lie about green, lying about green.
#
# An interrupted check has observed nothing. UNKNOWN (2) is the only honest
# answer; a FAIL it never measured is worse than no answer at all.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
REAL_PYTHON3="$(command -v python3)"; export REAL_PYTHON3
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"

# A pytest that BLOCKS, so the run is reliably mid-flight when the signal lands.
# It CLOSES its stdout/stderr before sleeping: if this child outlives the
# signalled parent it must not keep the captured pipe open. It did, in the
# first cut — the sleeping orphan held the pipe, so the python globber that
# runs these harnesses (`subprocess.run(..., capture_output=True)`) blocked
# until ITS timeout and crashed the whole CI suite before pytest could report.
# Locally the same hang finished just under the local timeout, so this file
# went green by luck. Bounded sleep for the same reason.
cat > "$SB/python3" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "pytest" ]; then
    echo "__FAKE_PYTEST_STARTED__"
    exec 1>&- 2>&-        # never hold the harness's capture pipe
    sleep 20
    exit 0
  fi
done
exec "$REAL_PYTHON3" "$@"
EOF
for c in gh ssh curl; do printf '#!/usr/bin/env bash\nexit 1\n' > "$SB/$c"; done
chmod +x "$SB"/*

FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"

out="$TMP/out.log"
PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="hs-test-dummy" \
  MESHFORGE_REPO="$FAKE_REPO" setsid bash "$SCRIPT" > "$out" 2>&1 &
pid=$!
# Wait until the run is genuinely underway, then signal it.
for _ in $(seq 1 100); do
  grep -q "__FAKE_PYTEST_STARTED__\|honest_status —" "$out" 2>/dev/null && break
  sleep 0.2
done
# Signal the process GROUP (setsid above), so the fake pytest's sleeping
# child dies with its parent instead of outliving the run.
kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
wait "$pid"; rc=$?

check() { if [ -n "$2" ]; then echo "PASS: $1"; else echo "FAIL: $1"; fails=1; fi; }

# The harness must prove it interrupted a RUNNING script — a run that died
# before starting would pass the assertions below for the wrong reason.
check "the run was genuinely underway when signalled" \
  "$(grep -q 'honest_status —' "$out" && echo ok)"
check "interrupted run exits UNKNOWN (2), not FAIL (1)" \
  "$([ "$rc" -eq 2 ] && echo ok)"
check "it says it was interrupted" \
  "$(grep -q 'UNKNOWN: interrupted' "$out" && echo ok)"
check "it never claims 'proven not-green'" \
  "$(grep -q 'proven not-green' "$out" || echo ok)"
# Match the VERDICT COLUMN, not the word. The first cut grepped for 'FAIL'
# anywhere and tripped on an honest `CI(?) UNKNOWN 'gh run list' FAILED
# (auth/network — NOT evidence of no run)` — prose inside an UNKNOWN reason,
# which is the stub behaving correctly. An assertion that cannot tell a
# verdict from a word describing one measures the wrong thing.
check "it emits no FAIL verdict it did not observe" \
  "$(! grep -q "$(printf '\033')\[31mFAIL" "$out" && echo ok)"

[ "$fails" -eq 0 ] && echo "ALL PASS" || echo "SOME CHECKS FAILED"
exit "$fails"
