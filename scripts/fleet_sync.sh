#!/usr/bin/env bash
# Roll `git pull && systemctl restart meshforge-gateway` across the fleet.
#
# Reads a host list from the first file found:
#   $MESHFORGE_FLEET_HOSTS (if set)
#   $HOME/.config/meshforge/fleet_hosts
#   /etc/meshforge/fleet_hosts
#
# Host list format:
#   # comments and blank lines are ignored
#   fleet-host-1
#   fleet-host-2
#   wh6gxz@fleet-host
#   # jump-host syntax is supported via ~/.ssh/config
#   moc.via-volcano
#
# Per-host sequence: verify repo + branch + unit, git pull --ff-only,
# sudo systemctl restart meshforge-gateway, print PASS/FAIL.
#
# A host failing does NOT abort the rest. Exit code is the number of hosts
# that failed (0 = all ok).

set -uo pipefail

SELF="$(basename "$0")"

# Locate host list
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

# Remote recipe. Runs on each target Pi. Prints a single tagged summary
# line at the end so the driver can grep it cleanly.
REMOTE_SCRIPT='
set -u
REPO="/opt/meshforge"
if [ ! -d "$REPO/.git" ]; then
    echo "FAIL repo_missing $REPO"
    exit 1
fi
cd "$REPO" || { echo "FAIL cd_failed"; exit 1; }

branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
if [ "$branch" != "main" ]; then
    echo "FAIL wrong_branch $branch"
    exit 1
fi

# Pull — require fast-forward only, never merge
if ! git pull --ff-only origin main >/dev/null 2>pull.err; then
    echo "FAIL git_pull $(tr "\n" "|" < pull.err | head -c 200)"
    rm -f pull.err
    exit 1
fi
rm -f pull.err
new_head=$(git rev-parse --short HEAD)

# Only restart if the unit is installed; otherwise just report the sync
if systemctl list-unit-files meshforge-gateway.service 2>/dev/null | grep -q meshforge-gateway; then
    if sudo -n systemctl restart meshforge-gateway.service >/dev/null 2>restart.err; then
        rm -f restart.err
        echo "PASS $new_head restarted"
    else
        msg=$(tr "\n" "|" < restart.err | head -c 200)
        rm -f restart.err
        echo "FAIL restart $msg"
        exit 1
    fi
else
    echo "PASS $new_head no_unit"
fi
'

# Iterate hosts
fail_count=0
pass_count=0
skip_count=0

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    # strip leading/trailing whitespace, skip blank + comment
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$host" || "${host:0:1}" == "#" ]] && continue

    # SSH with short connect timeout; BatchMode=yes prevents password prompts
    # (operators must use key auth for fleet sync).
    result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                  -o StrictHostKeyChecking=accept-new \
                  "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
    rc=$?

    summary="$(echo "$result" | grep -E '^(PASS|FAIL) ' | tail -1)"
    if [[ $rc -ne 0 && -z "$summary" ]]; then
        printf '[%-30s] SKIP unreachable (ssh rc=%d)\n' "$host" "$rc"
        skip_count=$((skip_count + 1))
        continue
    fi

    if [[ "$summary" =~ ^PASS ]]; then
        printf '[%-30s] %s\n' "$host" "$summary"
        pass_count=$((pass_count + 1))
    else
        printf '[%-30s] %s\n' "$host" "${summary:-FAIL unknown}"
        fail_count=$((fail_count + 1))
    fi
done < "$FLEET_FILE"

echo
printf 'Summary: %d ok, %d failed, %d unreachable\n' \
    "$pass_count" "$fail_count" "$skip_count"

# Exit non-zero if anything went wrong (fail OR unreachable) — operators
# scripting this want a reliable signal, not a silent partial rollout
exit "$((fail_count + skip_count))"
