#!/usr/bin/env bash
# Roll `git pull` + service restarts across the fleet for BOTH MeshForge repos:
#   /opt/meshforge          (this repo) → restart meshforge-gateway.service
#   /opt/meshforge-maps     (sister)    → restart meshforge-maps.service
#
# A box without one of the repos still updates the other (skip-if-absent).
# Without this, /opt/meshforge-maps drifts on boxes where it isn't manually
# pulled — observed Apr 24 2026 on fleet-host, where a 14 GB sqlite WAL
# accumulated because the WAL-cap fix shipped Apr 20 had never landed.
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
# Per-host: verify each present repo + branch, git pull --ff-only, restart
# the matching unit if installed, print one summary line per repo. A repo
# missing on a host is reported as `skip_no_repo`, not a failure.
#
# A host failing does NOT abort the rest. Exit code is the number of host
# x repo failures (0 = all ok).

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

# Remote recipe. Runs on each target Pi. Prints one tagged summary line per
# (repo, unit) pair so the driver can attribute each result independently.
# Format: TAG <repo_short> <head_or_status> [unit_status]
#   PASS meshforge a48ff82 restarted
#   PASS meshforge-maps 222265e no_unit
#   SKIP meshforge-maps no_repo
REMOTE_SCRIPT='
set -u

# sync_repo <short_name> <repo_path> <unit_name>
# Emits exactly one summary line. Returns 0 on PASS/SKIP, 1 on FAIL so the
# overall exit code reflects how many things broke.
sync_repo() {
    local short="$1" repo="$2" unit="$3"

    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $short no_repo"
        return 0
    fi
    if ! cd "$repo" 2>/dev/null; then
        echo "FAIL $short cd_failed"
        return 1
    fi

    local branch
    branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
    if [ "$branch" != "main" ]; then
        echo "FAIL $short wrong_branch $branch"
        return 1
    fi

    if ! git pull --ff-only origin main >/dev/null 2>pull.err; then
        local msg
        msg=$(tr "\n" "|" < pull.err | head -c 200)
        rm -f pull.err
        echo "FAIL $short git_pull $msg"
        return 1
    fi
    rm -f pull.err
    local new_head
    new_head=$(git rev-parse --short HEAD)

    if systemctl list-unit-files "${unit}.service" 2>/dev/null | grep -q "$unit"; then
        if sudo -n systemctl restart "${unit}.service" >/dev/null 2>restart.err; then
            rm -f restart.err
            echo "PASS $short $new_head restarted"
        else
            local emsg
            emsg=$(tr "\n" "|" < restart.err | head -c 200)
            rm -f restart.err
            echo "FAIL $short restart $emsg"
            return 1
        fi
    else
        echo "PASS $short $new_head no_unit"
    fi
    return 0
}

# Run both syncs even if one fails so a broken meshforge-maps does not mask
# a successful meshforge update.
sync_repo meshforge       /opt/meshforge       meshforge-gateway || rc1=$?
sync_repo meshforge-maps  /opt/meshforge-maps  meshforge-maps    || rc2=$?
exit $(( ${rc1:-0} + ${rc2:-0} ))
'

# Iterate hosts. Each host produces multiple summary lines (one per repo);
# we track host-level pass/fail counts (any FAIL on a host = host failed)
# AND per-action counts so the operator sees both views.
fail_count=0
pass_count=0
skip_count=0
action_pass=0
action_fail=0
action_skip=0

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

    # Pull all summary lines (PASS/FAIL/SKIP), one per repo.
    summaries="$(echo "$result" | grep -E '^(PASS|FAIL|SKIP) ')"
    if [[ -z "$summaries" ]]; then
        printf '[%-30s] SKIP unreachable (ssh rc=%d)\n' "$host" "$rc"
        skip_count=$((skip_count + 1))
        continue
    fi

    host_failed=0
    while IFS= read -r line; do
        printf '[%-30s] %s\n' "$host" "$line"
        case "$line" in
            PASS*) action_pass=$((action_pass + 1)) ;;
            SKIP*) action_skip=$((action_skip + 1)) ;;
            FAIL*) action_fail=$((action_fail + 1)); host_failed=1 ;;
        esac
    done <<< "$summaries"

    if [[ $host_failed -eq 1 ]]; then
        fail_count=$((fail_count + 1))
    else
        pass_count=$((pass_count + 1))
    fi
done < "$FLEET_FILE"

echo
printf 'Hosts:   %d ok, %d failed, %d unreachable\n' \
    "$pass_count" "$fail_count" "$skip_count"
printf 'Actions: %d ok, %d failed, %d skipped (no_repo)\n' \
    "$action_pass" "$action_fail" "$action_skip"

# Exit non-zero if any action failed or any host was unreachable. SKIP from
# no_repo is fine (idempotent install pattern); SKIP from ssh failure is not.
exit "$((action_fail + skip_count))"
