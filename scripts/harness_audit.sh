#!/usr/bin/env bash
# harness_audit.sh — executable ground truth for the Claude HARNESS layer
# (2026-07-03). Complements honest_status.sh: that script verifies REPO/FLEET
# truth (CI, suite, SHA convergence); this one verifies the second-brain
# harness a session stands on — hooks, claim gate, calibration ledger, mini,
# cron spine, memory index, the manager deadman. Born from the 07-03 harness
# self-audit (.claude/foundations/harness_map.md is the companion map).
#
# Conventions match honest_status: PASS / FAIL / UNKNOWN per leg;
# UNKNOWN is NEVER a pass. Exit 0 = all PASS; 1 = any FAIL; 2 = UNKNOWN(s)
# but no FAIL. Run on the MANAGER box; legs self-guard elsewhere.
#
# Crontab (manager, daily):
#   35 5 * * * /opt/meshforge/scripts/harness_audit.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh harness_audit $?
set -uo pipefail

REPO="${MESHFORGE_REPO:-/opt/meshforge}"
MA_REPO="/opt/meshanchor"
# Shared pytest-invocation wrapper (run + classify honestly).
MF_REPO_ROOT="$REPO"
# shellcheck source=lib/pytest_checked.sh
. "$REPO/scripts/lib/pytest_checked.sh"
MEM_DIR="${HOME}/.claude/projects/-opt-meshforge/memory"
NOTES="${HOME}/.claude/plans/gateway-session-notes-$(hostname | tr '[:upper:]' '[:lower:]').md"
VERDICTS="${CRON_VERDICT_LOG:-$HOME/cron_verdicts.log}"
DEADMAN_PEER="${MANAGER_HEARTBEAT_PEER:-moc1}"

pass=0; fail=0; unknown=0
say() { printf '  %-28s %-8s %s\n' "$1" "$2" "$3"; }
P() { say "$1" "PASS" "$2"; pass=$((pass+1)); }
F() { say "$1" "FAIL" "$2"; fail=$((fail+1)); }
U() { say "$1" "UNKNOWN" "$2"; unknown=$((unknown+1)); }

echo "harness_audit — $(hostname) $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. git hooks live (the #29 spine; found dormant once, 2026-06-15)
for r in "$REPO" "$MA_REPO"; do
    [ -d "$r" ] || continue
    hp="$(git -C "$r" config core.hooksPath 2>/dev/null)"
    if [ "$hp" = ".githooks" ]; then P "hooksPath($(basename "$r"))" ".githooks"
    else F "hooksPath($(basename "$r"))" "got '${hp:-unset}'"; fi
done

# 2. session hooks wired (Stop->claim_gate is the calibrated-claims enforcer)
SETTINGS="$REPO/.claude/settings.json"
if [ -r "$SETTINGS" ]; then
    grep -q "claim_gate.py" "$SETTINGS" \
        && P "Stop->claim_gate" "wired in .claude/settings.json" \
        || F "Stop->claim_gate" "claim_gate.py not in settings hooks"
    grep -q "mini_dudeai.warmstart" "$SETTINGS" \
        && P "SessionStart->warmstart" "wired" \
        || F "SessionStart->warmstart" "not wired"
else
    U "session hooks" "cannot read $SETTINGS"
fi

# 3. mini fresh + seed coverage test
MINI_STATE="$HOME/mini_dudeai_state.json"
if [ -r "$MINI_STATE" ]; then
    age="$(python3 - "$MINI_STATE" <<'PY' 2>/dev/null || echo -1
import json, sys, time
try:
    print(int(time.time() - float(json.load(open(sys.argv[1])).get("last_tick_ts", 0))))
except Exception:
    print(-1)
PY
)"
    if [ "$age" -ge 0 ] && [ "$age" -lt 300 ]; then P "mini fresh" "last tick ${age}s ago"
    elif [ "$age" -ge 0 ]; then F "mini fresh" "last tick ${age}s ago (stale)"
    else U "mini fresh" "state unparseable"; fi
else
    U "mini fresh" "no state file (not a mini box?)"
fi
# Was a tail-piped conditional. `set -o pipefail` above meant it did propagate
# pytest's status (it was not the vacuous gate it first looked like), but it
# still trusted the exit code — the signal measured to flap 0 on a failed run
# (2026-07-28). Classify instead, and keep the third state: this audit already
# has a U() for "could not tell", and an unclassifiable run belongs there
# rather than in P.
_ha_cwd="$PWD"; cd "$REPO" 2>/dev/null || true
mf_pytest_checked tests/test_mini_dudeai_honest_failure_modes.py -k "SeedCovers" -q
_ha_rc=$?
cd "$_ha_cwd" 2>/dev/null || true
rm -f "$MF_PYTEST_LOG"
case "$_ha_rc" in
    0) P "seed coverage" "$MF_PYTEST_WHY" ;;
    1) F "seed coverage" "$MF_PYTEST_WHY" ;;
    *) U "seed coverage" "$MF_PYTEST_WHY" ;;
esac

# 4. calibration ledger + daily reverify freshness (<26h)
LEDGER="$HOME/calibration_ledger.jsonl"
[ -s "$LEDGER" ] && P "calibration ledger" "$(wc -l < "$LEDGER") events" \
                 || U "calibration ledger" "absent/empty at $LEDGER"
check_verdict_fresh() {  # $1=name $2=max_age_s
    line="$(grep " $1 " "$VERDICTS" 2>/dev/null | tail -1)"
    [ -n "$line" ] || { U "$1 verdict" "never reported"; return; }
    ts="$(date -d "$(printf '%s' "$line" | awk '{print $1}')" +%s 2>/dev/null || echo 0)"
    age=$(( $(date +%s) - ts ))
    status="$(printf '%s' "$line" | awk '{print $3}')"
    if [ "$age" -gt "$2" ]; then F "$1 verdict" "stale ${age}s (>${2}s)"
    elif [ "$status" = "OK" ]; then P "$1 verdict" "OK, ${age}s ago"
    else F "$1 verdict" "$status, ${age}s ago"; fi
}
check_verdict_fresh "calibration_reverify" 93600

# 5. cron freshness watcher healthy and reporting 0 stale
fresh_line="$(grep " cron_freshness " "$VERDICTS" 2>/dev/null | tail -1)"
if [ -z "$fresh_line" ]; then U "cron_freshness" "never reported"
elif printf '%s' "$fresh_line" | grep -q " OK "; then P "cron_freshness" "$(printf '%s' "$fresh_line" | cut -d' ' -f3-)"
else F "cron_freshness" "$(printf '%s' "$fresh_line" | cut -d' ' -f3-)"; fi

# 6. memory hot index under the load limit (24576 B; warn at 75%)
if [ -r "$MEM_DIR/MEMORY.md" ]; then
    sz="$(wc -c < "$MEM_DIR/MEMORY.md")"
    if [ "$sz" -ge 24576 ]; then F "memory index" "${sz}B >= 24576B limit"
    elif [ "$sz" -ge 18432 ]; then P "memory index" "${sz}B (>=75% — demote soon)"
    else P "memory index" "${sz}B"; fi
else
    U "memory index" "no MEMORY.md"
fi

# 7. memory git remote private (leaked-secret guard; self-guards offline)
if command -v gh >/dev/null 2>&1 && [ -d "$MEM_DIR/.git" ]; then
    url="$(git -C "$MEM_DIR" remote get-url origin 2>/dev/null | sed -E 's#(git@github.com:|https://github.com/)##; s/\.git$//')"
    vis="$(gh repo view "$url" --json visibility --jq .visibility 2>/dev/null)"
    case "$vis" in
        PRIVATE) P "memory repo" "PRIVATE" ;;
        "")      U "memory repo" "visibility unreadable (offline?)" ;;
        *)       F "memory repo" "visibility=$vis — SECRETS EXPOSED" ;;
    esac
else
    U "memory repo" "gh or .git absent"
fi

# 8. manager deadman spine (heartbeat cron here + deadman cron on peer)
crontab -l 2>/dev/null | grep -q "manager_heartbeat.sh" \
    && P "heartbeat cron (local)" "wired" \
    || F "heartbeat cron (local)" "missing from crontab"
peer_line="$(timeout 25 ssh -o ConnectTimeout=8 -o BatchMode=yes "$DEADMAN_PEER" "crontab -l 2>/dev/null | grep -c manager_deadman.sh" 2>/dev/null)"
if [ "${peer_line:-}" = "" ]; then U "deadman cron ($DEADMAN_PEER)" "peer unreachable"
elif [ "$peer_line" -ge 1 ]; then P "deadman cron ($DEADMAN_PEER)" "wired"
else F "deadman cron ($DEADMAN_PEER)" "missing from peer crontab"; fi

# 9. session-notes size (rotation convention: archive when large)
if [ -r "$NOTES" ]; then
    nsz="$(wc -c < "$NOTES")"
    if [ "$nsz" -gt 81920 ]; then F "session notes" "${nsz}B >80KB — rotate to archive"
    else P "session notes" "${nsz}B"; fi
else
    U "session notes" "absent"
fi

echo
echo "--> ${pass} PASS, ${fail} FAIL, ${unknown} UNKNOWN"
[ "$fail" -gt 0 ] && exit 1
[ "$unknown" -gt 0 ] && exit 2
exit 0
