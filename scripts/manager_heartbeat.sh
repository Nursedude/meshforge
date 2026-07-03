#!/usr/bin/env bash
# manager_heartbeat.sh — the manager's "I'm alive" beat to a peer box (2026-07-03).
#
# The 07-03 harness audit found the fleet's biggest structural gap: the ENTIRE
# alerting spine (offline monitor, ntfy loopback/ack, ma_health, mini
# federator, calibration reverify, cron freshness) is co-located on the
# manager box — if the manager dies, all paging stops SILENTLY. The fleet's
# ssh direction is manager→boxes only, so the fix is a push heartbeat:
#
#   manager (this script, cron */10, verdict-wired `manager_heartbeat`)
#       └── ssh $PEER  "write epoch to ~/.manager_heartbeat"
#   peer  (scripts/manager_deadman.sh, cron */10, verdict-wired
#          `manager_deadman`) — checks the LOCAL file mtime (peer's own
#          clock; no cross-box clock trust) and pages ntfy DIRECTLY when
#          stale, because in that scenario the manager's pager is dead.
#
# Every beat that lands proves the manager's crond + ssh + network in one
# stroke — the heartbeat IS the spine's liveness, not just the box's.
#
# Crontab (manager box):
#   */10 * * * * /opt/meshforge/scripts/manager_heartbeat.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh manager_heartbeat $?
#
# Exit 0 = beat delivered. Nonzero = beat failed (peer down/unreachable —
# cron_verdict + mini's cron_verdict_stale_any surface it manager-side, and
# the offline monitor independently covers a dead peer).
set -uo pipefail

PEER="${MANAGER_HEARTBEAT_PEER:-moc1}"
BEAT_FILE="${MANAGER_HEARTBEAT_FILE:-.manager_heartbeat}"

timeout 30 ssh -o ConnectTimeout=10 -o BatchMode=yes "$PEER" \
    "date -u +%s > \$HOME/$BEAT_FILE" || {
        echo "manager_heartbeat: beat to $PEER FAILED" >&2
        exit 1
    }
exit 0
