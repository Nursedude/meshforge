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
# Self-truncating (SD-card friendly) — but retention is PER-NAME aware: the
# newest KEEP_PER_NAME lines of every cron survive truncation even when
# high-churn crons (5-min cadence) push them past MAX_LINES. Without this, a
# daily cron's single verdict scrolled out ~23.5h after its run and
# probe_cron_verdict_stale (Issue #78) read healthy crons as "silent: never"
# — the reader/writer retention drift found 2026-07-09 (the retention floor
# must exceed the slowest wired cadence x the probe's CADENCE_MULT).
# Bound: MAX_LINES + (#names x KEEP_PER_NAME).

LOG="${CRON_VERDICT_LOG:-$HOME/cron_verdicts.log}"
MAX_LINES=1000
KEEP_PER_NAME=30

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

# Truncate (atomic-ish; losing a race here only costs old lines). Keep the
# newest MAX_LINES overall PLUS the newest KEEP_PER_NAME lines of every name,
# so a slow-cadence cron's verdicts survive high-churn neighbors.
lines=$(wc -l < "$LOG" 2>/dev/null || echo 0)
if [ "$lines" -gt "$MAX_LINES" ]; then
    tmp=$(mktemp "${LOG}.XXXXXX") || exit 0
    awk -v max="$MAX_LINES" -v keep="$KEEP_PER_NAME" '
        { line[NR] = $0; nm[NR] = $2 }
        END {
            start = NR - max + 1; if (start < 1) start = 1
            for (i = NR; i >= 1; i--) {
                seen[nm[i]]++
                if (i >= start || seen[nm[i]] <= keep) keep_line[i] = 1
            }
            for (i = 1; i <= NR; i++) if (i in keep_line) print line[i]
        }' "$LOG" > "$tmp" && mv "$tmp" "$LOG"
fi
exit 0
