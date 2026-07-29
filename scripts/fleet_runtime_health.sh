#!/bin/bash
# scripts/fleet_runtime_health.sh — runtime-side companion to ecosystem_ci_status.sh.
#
# Born from Insight 1 of the 2026-05-03 diagnostic-insights plan. The CI
# audit (meshforge-maps red 3 days, meshing_around red 4 days, nobody
# noticed) caught one half of the visibility gap. The other half: prod
# could be quietly degrading too — disk filling, OOMs accumulating,
# units failing, journal error rate climbing — and the same blindness
# applies. Production resilience MASKS rot; explicit polling closes the
# gap.
#
# Polls each fleet box via SSH and collects:
#   - failed systemd units (count + names)
#   - disk % (root + meshforge data partition if separate)
#   - OOM journal events in the last 24h
#   - journal ERROR-priority count in the last hour
#   - meshforge-known service active/inactive states
#   - last meshforge-gateway "bridged" event age (where service exists)
#
# Outputs:
#   - Always: $HOME/.meshforge-fleet-status — one section per host.
#     Plain ASCII, easy to cat or shell-prompt.
#   - On red: $HOME/.meshforge-fleet-RED — details about which host(s)
#     are red, why. File present = unhealthy, file absent = all clean.
#   - Stdout: same content as ~/.meshforge-fleet-status.
#
# Reads the same host list as fleet_sync.sh / fleet_status.sh:
#   $MESHFORGE_FLEET_HOSTS, then $HOME/.config/meshforge/fleet_hosts,
#   then /etc/meshforge/fleet_hosts.
#
# Red criteria (any one trips RED):
#   - any failed systemd unit
#   - / or /home > 90% used
#   - any OOM event in last 24h
#   - journal ERROR rate > 200/hour
#   - SSH unreachable (host down / key broken)
#
# Exit codes:
#   0 — all hosts healthy
#   1 — one or more red
#   2 — no host list, or every host unreachable

set -u

SELF="$(basename "$0")"

# Host list via THE shared resolver (scripts/lib/fleet_hosts.sh) — this was
# one of ~13 independent copies of the chain (converged 2026-07-29).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/fleet_hosts.sh"
FLEET_FILE=""
fleet_hosts_resolve "/opt/meshforge" && FLEET_FILE="$FLEET_HOSTS_FILE"

if [ -z "$FLEET_FILE" ]; then
    echo "$SELF: no fleet host list found at \$MESHFORGE_FLEET_HOSTS, ~/.config/meshforge/fleet_hosts, or /etc/meshforge/fleet_hosts" >&2
    exit 2
fi

STATUS_FILE="$HOME/.meshforge-fleet-status"
RED_FILE="$HOME/.meshforge-fleet-RED"

# Remote recipe. Single SSH per host, emits one block of "key=value"
# lines per host. Keep it compact — runs as the SSH user, no sudo. Uses
# only POSIX-portable bits so it works on any fleet box.
REMOTE_SCRIPT='
set -u

failed_units="$(systemctl --failed --plain --no-legend 2>/dev/null | awk "{print \$1}" | tr "\n" "," | sed "s/,$//")"
failed_count=$(echo -n "$failed_units" | tr "," "\n" | grep -c .)

disk_root=$(df -P / | awk "NR==2 {print \$5}" | tr -d "%")
# Meshforge data lives in ~/.local/share — same partition as $HOME.
disk_home=$(df -P "$HOME" | awk "NR==2 {print \$5}" | tr -d "%")

# OOM in last 24h. journalctl -k for kernel; OOM messages are
# "Out of memory" or "oom-kill". -q suppresses "no entries" noise.
oom_count=$(journalctl -k --since "24 hours ago" --no-pager -q 2>/dev/null \
    | grep -ciE "out of memory|oom-kill|killed process" || echo 0)

# ERROR-priority entries in last hour. Tolerate older journalctl
# without --priority by falling back to plain count.
err_count=$(journalctl --since "1 hour ago" --priority=err --no-pager -q 2>/dev/null | wc -l)

# Known meshforge services — report active/inactive/missing for each.
svc_states=""
for svc in meshforge-gateway meshforge-map meshforge-maps rnsd meshtasticd mosquitto prometheus grafana-server; do
    if systemctl list-unit-files "${svc}.service" 2>/dev/null | grep -q "^${svc}\\.service"; then
        # is-active prints exactly one of {active, inactive, failed,
        # activating, deactivating, unknown} regardless of exit code,
        # so capture stdout and ignore exit. NOT `cmd || echo unknown`
        # — that would print both lines on inactive (exit=3 + word).
        st=$(systemctl is-active "${svc}.service" 2>/dev/null; true)
        [ -z "$st" ] && st="unknown"
    else
        st="missing"
    fi
    svc_states="${svc_states}${svc}=${st};"
done

# Last gateway "bridged" event age (seconds). Only meaningful where
# meshforge-gateway exists; else emit -1.
if systemctl list-unit-files meshforge-gateway.service 2>/dev/null | grep -q meshforge-gateway; then
    last_bridged=$(journalctl -u meshforge-gateway --since "24 hours ago" --no-pager -q 2>/dev/null \
        | grep -aE "bridged|Bridge.*->" | tail -1 \
        | awk "{print \$1, \$2, \$3}")
    if [ -n "$last_bridged" ]; then
        bridged_age_s=$(( $(date +%s) - $(date -d "$last_bridged" +%s 2>/dev/null || echo $(date +%s)) ))
    else
        bridged_age_s=-1
    fi
else
    bridged_age_s=-2
fi

# Uptime + load for context.
uptime_str=$(uptime -p 2>/dev/null | sed "s/^up //" | head -c 40)
load1=$(awk "{print \$1}" /proc/loadavg 2>/dev/null || echo "?")

echo "BLOCK_START"
echo "failed_count=$failed_count"
echo "failed_units=$failed_units"
echo "disk_root=$disk_root"
echo "disk_home=$disk_home"
echo "oom_count=$oom_count"
echo "err_count=$err_count"
echo "svc_states=$svc_states"
echo "bridged_age_s=$bridged_age_s"
echo "uptime=$uptime_str"
echo "load1=$load1"
echo "BLOCK_END"
'

NOW="$(date -Iseconds)"
status_lines=()
red_lines=()
ok_count=0
red_count=0
unreach_count=0

status_lines+=("# MeshForge fleet runtime health — generated $NOW")
status_lines+=("# (See ~/.meshforge-ci-status for the CI-side view; this is the prod-side view.)")
status_lines+=("")

# Parse "key=value;" line. Sets remote_<name>.
parse_block() {
    local block="$1"
    remote_failed_count="?"; remote_failed_units=""
    remote_disk_root="?"; remote_disk_home="?"
    remote_oom_count="?"; remote_err_count="?"
    remote_svc_states=""; remote_bridged_age_s="?"
    remote_uptime=""; remote_load1="?"
    while IFS='=' read -r k v; do
        case "$k" in
            failed_count)   remote_failed_count="$v" ;;
            failed_units)   remote_failed_units="$v" ;;
            disk_root)      remote_disk_root="$v" ;;
            disk_home)      remote_disk_home="$v" ;;
            oom_count)      remote_oom_count="$v" ;;
            err_count)      remote_err_count="$v" ;;
            svc_states)     remote_svc_states="$v" ;;
            bridged_age_s)  remote_bridged_age_s="$v" ;;
            uptime)         remote_uptime="$v" ;;
            load1)          remote_load1="$v" ;;
        esac
    done <<< "$block"
}

while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$host" ] || [ "${host:0:1}" = "#" ] && continue

    result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                  -o StrictHostKeyChecking=accept-new \
                  "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
    rc=$?
    block="$(echo "$result" | awk '/^BLOCK_START$/{flag=1; next} /^BLOCK_END$/{flag=0} flag')"

    if [ -z "$block" ]; then
        msg="$(echo "$result" | tail -1 | tr -d '\r' | head -c 80)"
        status_lines+=("$host: UNREACHABLE rc=$rc ${msg}")
        red_lines+=("$host UNREACHABLE rc=$rc ${msg}")
        unreach_count=$((unreach_count + 1))
        continue
    fi

    parse_block "$block"
    host_red=0
    why=()

    [ "$remote_failed_count" != "?" ] && [ "$remote_failed_count" -gt 0 ] 2>/dev/null && {
        host_red=1; why+=("failed=${remote_failed_count}(${remote_failed_units})")
    }
    [ "$remote_disk_root" != "?" ] && [ "$remote_disk_root" -gt 90 ] 2>/dev/null && {
        host_red=1; why+=("disk_root=${remote_disk_root}%")
    }
    [ "$remote_disk_home" != "?" ] && [ "$remote_disk_home" -gt 90 ] 2>/dev/null && {
        host_red=1; why+=("disk_home=${remote_disk_home}%")
    }
    [ "$remote_oom_count" != "?" ] && [ "$remote_oom_count" -gt 0 ] 2>/dev/null && {
        host_red=1; why+=("oom_24h=${remote_oom_count}")
    }
    [ "$remote_err_count" != "?" ] && [ "$remote_err_count" -gt 200 ] 2>/dev/null && {
        host_red=1; why+=("err_1h=${remote_err_count}")
    }

    if [ "$host_red" = "1" ]; then
        red_count=$((red_count + 1))
        red_lines+=("$host  ${why[*]}")
    else
        ok_count=$((ok_count + 1))
    fi

    status_lines+=("## $host")
    status_lines+=("  uptime=${remote_uptime}  load1=${remote_load1}")
    status_lines+=("  failed=${remote_failed_count}${remote_failed_units:+ ($remote_failed_units)}  disk_root=${remote_disk_root}%  disk_home=${remote_disk_home}%")
    status_lines+=("  oom_24h=${remote_oom_count}  err_1h=${remote_err_count}")
    case "$remote_bridged_age_s" in
        -2) bridged_note="not_a_gateway" ;;
        -1) bridged_note="no_bridged_event_24h" ;;
         *) bridged_note="last_bridged=${remote_bridged_age_s}s" ;;
    esac
    status_lines+=("  ${bridged_note}")
    status_lines+=("  services: ${remote_svc_states}")
    [ "$host_red" = "1" ] && status_lines+=("  RED: ${why[*]}")
    status_lines+=("")
done <<< "$FLEET_HOSTS_LIST"

status_lines+=("# Summary: ${ok_count} ok, ${red_count} red, ${unreach_count} unreachable")

tmp="$(mktemp)"
printf '%s\n' "${status_lines[@]}" > "$tmp"
mv "$tmp" "$STATUS_FILE"

cat "$STATUS_FILE"

if [ "${#red_lines[@]}" -gt 0 ]; then
    {
        echo "# MeshForge fleet runtime RED — generated $NOW"
        echo "# Delete this file or fix the offending host(s) to clear."
        echo ""
        for line in "${red_lines[@]}"; do
            printf -- '%s\n' "$line"
        done
        echo ""
        echo "# Triage:"
        echo "#   bash scripts/fleet_runtime_health.sh   # re-poll"
        echo "#   ssh <host> systemctl --failed         # see failed units"
        echo "#   ssh <host> 'journalctl --since \"1 hour ago\" --priority=err'"
    } > "$RED_FILE"
    exit 1
else
    rm -f "$RED_FILE"
    [ "$unreach_count" -gt 0 ] && [ "$ok_count" = "0" ] && exit 2
    exit 0
fi
