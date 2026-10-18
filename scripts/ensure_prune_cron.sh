#!/usr/bin/env bash
# ensure_prune_cron.sh — install the pytest_tmp_prune cron where it is missing.
#
# WHY (2026-10-18): the prune cron was hand-installed box by box and nothing in
# the repo carried it; lehua never got it and no check could notice. /tmp is
# RAM on every fleet Pi, so the first suite run or Claude session on such a box
# would accumulate unreclaimed. Called by update.sh (any box) and fleet_sync.sh
# (every fleet box, both legs) so the next box cannot be forgotten.
#
# It stays a CRON, not a systemd timer: the watchdog's cron_verdict_stale
# derives its watched set from the crontab, so a timer would silently drop the
# prune out of the only probe that notices it stopped.
#
# Rules, in order — any line that MENTIONS the script decides:
#   * a comment mentions it  -> opted_out  (a deliberate absence; never undone)
#   * a live line runs it    -> present    (any minute, any repo path — the
#                                           MeshAnchor twin's copy counts)
#   * neither                -> would_add / added
# An unreadable crontab is an ERROR, never read as empty: writing over it would
# replace a crontab we could not see.
#
# Run AS the crontab's owner (update.sh uses sudo -u). Dry-run by default.
# Output: one status line (present | opted_out | would_add | added | error).
# Exit 0 on the first four, 1 on error.
#
# Usage: scripts/ensure_prune_cron.sh [--apply]
set -u

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
CRONTAB="${CRONTAB_BIN:-crontab}"
REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
STATE="$HOME/.local/state/meshforge"

# The line the fleet runs (minute 11 matches every hand-installed box). The
# script speaks its own verdict; the || guard is only for dying unspoken, and
# the redirect is cron_capture_wire's capture idiom.
LINE="11 */6 * * * $REPO/scripts/pytest_tmp_prune.sh >\"\$HOME/.local/state/meshforge/cron_out/pytest_tmp_prune.out\" 2>&1 || $REPO/scripts/cron_verdict.sh pytest_tmp_prune FAIL wrapper_crashed"

err="$(mktemp)"; cur="$(mktemp)"; trap 'rm -f "$err" "$cur" "$cur.new"' EXIT
if ! "$CRONTAB" -l >"$cur" 2>"$err"; then
    if grep -qi "no crontab for" "$err"; then
        : >"$cur"                                   # genuinely empty
    else
        echo "error: crontab unreadable — $(tr '\n' ' ' <"$err" | head -c 120)"
        exit 1
    fi
fi

if grep -E '^[[:space:]]*#' "$cur" | grep -q 'pytest_tmp_prune'; then
    echo "opted_out: a commented line mentions pytest_tmp_prune — deliberate absence, left alone"
    exit 0
fi
if grep -q 'pytest_tmp_prune\.sh' "$cur"; then
    echo "present: $(grep -m1 'pytest_tmp_prune\.sh' "$cur" | cut -c1-80)"
    exit 0
fi

if [ "$APPLY" = 0 ]; then
    echo "would_add: $LINE"
    exit 0
fi

mkdir -p "$STATE/cron_out" || { echo "error: cannot create $STATE/cron_out"; exit 1; }
cp "$cur" "$STATE/crontab.bak-$(date -u +%Y%m%dT%H%M%SZ)" \
    || { echo "error: cannot back up the crontab to $STATE"; exit 1; }
{ cat "$cur"; [ -s "$cur" ] && [ -n "$(tail -c1 "$cur")" ] && echo; echo "$LINE"; } >"$cur.new"
if "$CRONTAB" "$cur.new" 2>"$err"; then
    echo "added: $LINE"
    exit 0
fi
echo "error: crontab install refused — $(tr '\n' ' ' <"$err" | head -c 120)"
exit 1
