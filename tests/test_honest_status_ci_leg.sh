#!/usr/bin/env bash
# Behavior test for honest_status.sh's CI leg (2026-09-03).
#
# WHY: the leg read CI state from `gh run list --branch main --limit 30` and
# treated "this SHA is not in that list" as "no CI run exists". That listing is
# a WINDOWED view of the Actions runs API and it transiently LAGS
# /commits/<sha>/check-runs in the minute after a run finishes — measured twice
# in one session, on two different SHAs whose CI was complete and green both
# times. The check of record therefore reported UNKNOWN over green work, which
# is the detector-defect class from its other side: a gate that cries UNKNOWN
# on healthy work gets tuned out, and then a REAL unknown has nowhere to stand.
#
# The leg now escalates a listing miss to the authoritative per-SHA endpoint,
# and keeps the states SEPARATE (honest_failure_modes #1): a failed QUERY must
# never render as "you haven't pushed yet" — that reading sends the operator to
# check their git remote instead of their credentials.
#
# Drives the REAL script with a stub `gh` whose two subcommands are controlled
# independently, which is the only way to reproduce "listing empty, check-runs
# green" — the exact shape of the observed flake.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/honest_status.sh"
fails=0

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SB="$TMP/bin"; mkdir -p "$SB"
FAKE_HOME="$TMP/home"; mkdir -p "$FAKE_HOME"

# Fake repo with a real commit + origin remote: the leg resolves owner/repo
# from the remote URL, so a repo without one must not be papered over.
FAKE_REPO="$TMP/repo"; mkdir -p "$FAKE_REPO/tests" "$FAKE_REPO/scripts"
printf 'import sys\nsys.exit(0)\n' > "$FAKE_REPO/scripts/lint.py"
git -C "$FAKE_REPO" init -q 2>/dev/null
git -C "$FAKE_REPO" config user.email t@t; git -C "$FAKE_REPO" config user.name t
git -C "$FAKE_REPO" remote add origin git@github.com:acme/widget.git
: > "$FAKE_REPO/f"; git -C "$FAKE_REPO" add f
git -C "$FAKE_REPO" commit -qm seed

cat > "$SB/gh" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  run)  [ "${GH_RUNLIST_RC:-0}" = 0 ] || { echo "HTTP 401: Bad credentials" >&2; exit 1; }
        printf '%s' "${GH_RUNLIST_OUT:-[]}" ;;
  api)  [ "${GH_CHECKRUNS_RC:-0}" = 0 ] || { echo "HTTP 404: Not Found" >&2; exit 1; }
        printf '%s' "${GH_CHECKRUNS_OUT:-{\"check_runs\":[]\}}" ;;
  *)    exit 1 ;;
esac
EOF
chmod +x "$SB/gh"

run() {  # env-driven; prints just the CI line
  PATH="$SB:$PATH" HOME="$FAKE_HOME" XDG_STATE_HOME="" HONEST_BOXES="hs-dummy" \
    MESHFORGE_REPO="$FAKE_REPO" \
    bash "$SCRIPT" --quick 2>&1 | grep "CI("
}

check() {  # $1 name, $2 output, $3 must-contain, $4 must-NOT-contain (optional)
  local nm="$1" out="$2" want="$3" nope="${4:-}"
  if ! printf '%s' "$out" | grep -q -- "$want"; then
    echo "FAIL: $nm — expected /$want/ in: $out"; fails=1; return
  fi
  if [ -n "$nope" ] && printf '%s' "$out" | grep -q -- "$nope"; then
    echo "FAIL: $nm — must NOT contain /$nope/: $out"; fails=1; return
  fi
  echo "PASS: $nm"
}

SHA="$(git -C "$FAKE_REPO" rev-parse HEAD)"
HIT="[{\"headSha\":\"$SHA\",\"status\":\"completed\",\"conclusion\":\"success\",\"databaseId\":42}]"

# ── the listing answers: unchanged fast path ──────────────────────────────
out="$(GH_RUNLIST_OUT="$HIT" run)"
check "listing hit + success => PASS" "$out" "PASS"

out="$(GH_RUNLIST_OUT="[{\"headSha\":\"$SHA\",\"status\":\"completed\",\"conclusion\":\"failure\",\"databaseId\":42}]" run)"
check "listing hit + failure => FAIL" "$out" "FAIL"

out="$(GH_RUNLIST_OUT="[{\"headSha\":\"$SHA\",\"status\":\"in_progress\",\"conclusion\":null,\"databaseId\":42}]" run)"
check "listing hit + still running => UNKNOWN" "$out" "UNKNOWN"

# ── THE BUG: listing lags, check-runs is authoritative and GREEN ───────────
# Old logic: not in the window => "no CI run found for this SHA (pushed yet?)".
out="$(GH_RUNLIST_OUT='[]' \
       GH_CHECKRUNS_OUT='{"check_runs":[{"name":"CI","status":"completed","conclusion":"success"}]}' run)"
check "listing lags + check-runs green => PASS" "$out" "PASS" "pushed yet"

# ── and it must not launder a RED head green ──────────────────────────────
out="$(GH_RUNLIST_OUT='[]' \
       GH_CHECKRUNS_OUT='{"check_runs":[{"name":"Test Suite","status":"completed","conclusion":"failure"}]}' run)"
check "listing lags + check-runs red => FAIL" "$out" "FAIL"

# skipped/neutral are not failures — the inverse overcorrection.
out="$(GH_RUNLIST_OUT='[]' \
       GH_CHECKRUNS_OUT='{"check_runs":[{"name":"CI","status":"completed","conclusion":"success"},{"name":"opt","status":"completed","conclusion":"skipped"}]}' run)"
check "skipped check does not turn green red" "$out" "PASS"

out="$(GH_RUNLIST_OUT='[]' \
       GH_CHECKRUNS_OUT='{"check_runs":[{"name":"CI","status":"queued","conclusion":null}]}' run)"
check "listing lags + check-runs pending => UNKNOWN" "$out" "UNKNOWN" "pushed yet"

# ── the three not-a-verdict states stay distinct ───────────────────────────
out="$(GH_RUNLIST_RC=1 run)"
check "gh run list FAILS => UNKNOWN, not 'pushed yet?'" "$out" "UNKNOWN" "pushed yet"
check "gh run list FAILS names the query failure" "$out" "FAILED"

out="$(GH_RUNLIST_OUT='[]' GH_CHECKRUNS_RC=1 run)"
check "check-runs fallback FAILS => UNKNOWN, not absence" "$out" "UNKNOWN" "pushed yet"

out="$(GH_RUNLIST_OUT='[]' GH_CHECKRUNS_OUT='{"check_runs":[]}' run)"
check "absent from BOTH => UNKNOWN naming both sources" "$out" "BOTH"

out="$(GH_RUNLIST_OUT='not json at all' run)"
check "unparseable listing => UNKNOWN, not absence" "$out" "UNKNOWN" "pushed yet"

[ "$fails" = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit "$fails"
