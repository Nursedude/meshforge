#!/bin/bash
# cron_verdict.sh — every fleet cron leaves a dated, greppable verdict.
#
# WHY: silence is the failure mode (the fleet's recurring lesson — a dead
# cron is indistinguishable from a healthy one). Every organ leaves one
# line per run in ~/cron_verdicts.log; cron_verdict_freshness.sh (on the
# federator) flags any organ whose last verdict is older than expected.
#
# Usage (crontab idiom — no script edits needed, captures the exit code):
#   */30 * * * * /path/job.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh job $?
#
# Or from inside a script, with a message:
#   cron_verdict.sh my_job OK "3 peers checked, all green"
#   cron_verdict.sh my_job CONCERN "2/3 peers slow"
#
# Args:
#   $1  name           short cron identifier (no spaces)
#   $2  status         numeric exit code (0->OK, nonzero->FAIL(n)) or
#                      literal OK | FAIL | CONCERN
#   $3+ message        optional free text
#
# Log shape: <ISO-8601-UTC> <name> <STATUS> <message>
# Self-truncating to the last 1000 lines (SD-card friendly).

LOG="${CRON_VERDICT_LOG:-$HOME/cron_verdicts.log}"
MAX_LINES=1000

name="${1:?usage: cron_verdict.sh <name> <exit-code|OK|FAIL|CONCERN> [msg]}"
raw="${2:?missing status}"
shift 2
msg="$*"

case "$raw" in
    0)            status="OK" ;;
    OK|FAIL|CONCERN) status="$raw" ;;
    ''|*[!0-9]*)  status="FAIL"; msg="bad status '$raw'${msg:+ — $msg}" ;;
    *)            status="FAIL($raw)" ;;
esac

printf '%s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$name" "$status" "$msg" >> "$LOG"

# Truncate (atomic-ish; losing a race here only costs old lines).
lines=$(wc -l < "$LOG" 2>/dev/null || echo 0)
if [ "$lines" -gt "$MAX_LINES" ]; then
    tmp=$(mktemp "${LOG}.XXXXXX") || exit 0
    tail -n "$MAX_LINES" "$LOG" > "$tmp" && mv "$tmp" "$LOG"
fi
exit 0
