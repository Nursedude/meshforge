#!/usr/bin/env bash
# gateway_rt_canary.sh — one gateway round-trip canary fire + cron verdict.
#
# Fires src/lab/gateway_rt_canary.py (one directed PING through the LIVE
# gateway queue -> LXMF CONFIRMED receipt -> echo ACK bridged back to a
# meshtastic queue row) and records the outcome via cron_verdict.sh so a
# silent canary is itself a caught failure (probe_cron_verdict_stale, #78).
#
# Crontab idiom (hourly, offset from other organs):
#   23 * * * * /opt/meshforge/scripts/gateway_rt_canary.sh >/dev/null 2>&1
#
# Knobs (env):
#   CANARY_PEER          lab_peers name of the echo target
#                        (default meshanchor-server — cross-box, no human inbox)
#   CANARY_LEG1_TIMEOUT  seconds to wait for CONFIRMED   (default 120)
#   CANARY_LEG2_TIMEOUT  seconds to wait for the ACK row (default 120)
#
# Verdicts:
#   OK       both legs proven (outbound confirmed + ACK bridged back)
#   CONCERN  outbound CONFIRMED but no ACK back (echo peer / return path)
#   FAIL     outbound pipeline broken (gateway down, enqueue refused,
#            dropped, not confirmed, or a DB unreadable)
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PEER="${CANARY_PEER:-meshanchor-server}"
LEG1="${CANARY_LEG1_TIMEOUT:-120}"
LEG2="${CANARY_LEG2_TIMEOUT:-120}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/gateway_rt_canary"
mkdir -p "$STATE_DIR"
out="$STATE_DIR/last_fire.log"

cd "$REPO_ROOT/src" || {
    "$REPO_ROOT/scripts/cron_verdict.sh" gateway_rt_canary FAIL "repo src dir missing"
    exit 1
}

python3 -m lab.gateway_rt_canary \
    --peer "$PEER" \
    --leg1-timeout "$LEG1" \
    --leg2-timeout "$LEG2" \
    >"$out" 2>&1
rc=$?

# The witness line — every code path in the module prints exactly one.
line="$(grep '^VERDICT ' "$out" | tail -1)"
status="$(printf '%s' "$line" | awk '{print $2}')"
detail="$(printf '%s' "$line" | cut -d' ' -f3-)"

case "$status" in
    OK|FAIL|CONCERN) ;;
    # No verdict line at all = the canary itself crashed. That is a FAIL
    # with its own reason — never silence (the canary must obey the same
    # honest-failure rules it enforces).
    *) status="FAIL"; detail="canary crashed without verdict (rc=$rc) — see $out" ;;
esac

"$REPO_ROOT/scripts/cron_verdict.sh" gateway_rt_canary "$status" "peer=$PEER $detail"
exit "$rc"
