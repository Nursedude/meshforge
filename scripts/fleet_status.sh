#!/usr/bin/env bash
# Read-only "is everyone current?" dashboard for the MeshForge fleet.
#
# Per host, reports: current HEAD, branch, worktree cleanliness, gateway
# unit presence, and unit ActiveState. Makes NO state changes — pure poll.
#
# Reads the same host list as fleet_sync.sh:
#   $MESHFORGE_FLEET_HOSTS (if set)
#   $HOME/.config/meshforge/fleet_hosts
#   /etc/meshforge/fleet_hosts
#
# Exit code is the number of hosts that were unreachable or returned an
# error — 0 means every host responded with a STATUS line.

set -uo pipefail

SELF="$(basename "$0")"

FLEET_FILE="${MESHFORGE_FLEET_HOSTS:-}"
if [[ -z "$FLEET_FILE" ]]; then
    if [[ -r "$HOME/.config/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="$HOME/.config/meshforge/fleet_hosts"
    elif [[ -r "/etc/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="/etc/meshforge/fleet_hosts"
    fi
fi

if [[ -z "$FLEET_FILE" || ! -r "$FLEET_FILE" ]]; then
    cat >&2 <<EOF
$SELF: no fleet host list found.

Create one at \$HOME/.config/meshforge/fleet_hosts (one host per line, '#'
comments allowed). Example:

    cp contrib/fleet/fleet_hosts.example ~/.config/meshforge/fleet_hosts
    \$EDITOR ~/.config/meshforge/fleet_hosts

Or set \$MESHFORGE_FLEET_HOSTS to a different path.
EOF
    exit 2
fi

# Remote recipe. Pure read — no git pull, no service restart, no writes.
# Emits exactly one STATUS line on success, one ERROR line on failure.
REMOTE_SCRIPT='
set -u
REPO="/opt/meshforge"
if [ ! -d "$REPO/.git" ]; then
    echo "ERROR repo_missing"
    exit 1
fi
cd "$REPO" || { echo "ERROR cd_failed"; exit 1; }

head=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
if [ -n "$(git status --porcelain 2>/dev/null | head -1)" ]; then
    dirty=1
else
    dirty=0
fi

if systemctl list-unit-files meshforge-gateway.service 2>/dev/null | grep -q meshforge-gateway; then
    unit="installed"
    state=$(systemctl show meshforge-gateway.service -p ActiveState --value 2>/dev/null || echo "unknown")
    [ -z "$state" ] && state="unknown"
else
    unit="missing"
    state="-"
fi

echo "STATUS head=$head branch=$branch dirty=$dirty unit=$unit state=$state"
'

# Parse a STATUS line into the named-field variables we need to format.
# Sets: f_head f_branch f_dirty f_unit f_state
parse_status() {
    local line="$1"
    f_head="?"; f_branch="?"; f_dirty="?"; f_unit="?"; f_state="?"
    for pair in $line; do
        case "$pair" in
            head=*)   f_head="${pair#head=}" ;;
            branch=*) f_branch="${pair#branch=}" ;;
            dirty=*)  f_dirty="${pair#dirty=}" ;;
            unit=*)   f_unit="${pair#unit=}" ;;
            state=*)  f_state="${pair#state=}" ;;
        esac
    done
}

# Print a column header so the dashboard reads cleanly without an external
# pager. Widths chosen to fit common host aliases + Tailscale-style names.
printf '%-26s %-8s %-7s %-10s %-10s %s\n' \
    "HOST" "HEAD" "BRANCH" "UNIT" "STATE" "NOTES"
printf '%-26s %-8s %-7s %-10s %-10s %s\n' \
    "----" "----" "------" "----" "-----" "-----"

ok_count=0
err_count=0
unreach_count=0

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$host" || "${host:0:1}" == "#" ]] && continue

    result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                  -o StrictHostKeyChecking=accept-new \
                  "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
    rc=$?

    status_line="$(echo "$result" | grep -E '^STATUS ' | tail -1)"
    error_line="$(echo "$result" | grep -E '^ERROR ' | tail -1)"

    if [[ -n "$status_line" ]]; then
        parse_status "$status_line"
        notes=""
        [[ "$f_dirty" == "1" ]] && notes="dirty-worktree"
        printf '%-26s %-8s %-7s %-10s %-10s %s\n' \
            "$host" "$f_head" "$f_branch" "$f_unit" "$f_state" "$notes"
        ok_count=$((ok_count + 1))
    elif [[ -n "$error_line" ]]; then
        printf '%-26s %-8s %-7s %-10s %-10s %s\n' \
            "$host" "-" "-" "-" "-" "${error_line#ERROR }"
        err_count=$((err_count + 1))
    else
        # SSH itself failed (connect timeout, bad key, host down).
        # Trim any banner spam to keep the row narrow.
        msg="$(echo "$result" | tail -1 | tr -d '\r' | head -c 60)"
        printf '%-26s %-8s %-7s %-10s %-10s %s\n' \
            "$host" "-" "-" "-" "-" "unreachable (rc=$rc) ${msg}"
        unreach_count=$((unreach_count + 1))
    fi
done < "$FLEET_FILE"

echo
printf 'Summary: %d ok, %d errored, %d unreachable\n' \
    "$ok_count" "$err_count" "$unreach_count"

exit "$((err_count + unreach_count))"
