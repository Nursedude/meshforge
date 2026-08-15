#!/bin/bash
# fleet_front_probe.sh — daily manager organ: every NAT front the fleet
# depends on still answers on its forwarded port.
#
# WHY THIS EXISTS (2026-08-15)
# ----------------------------
# Several fleet boxes are reachable ONLY through a port-forward on an AREDN
# node's WAN front (trdev :22, pw2lab :2200, moc5 :22, node admin :2222).
# Those forwards live in router config that reboots, upgrades, and UI edits
# can silently revert — the day this was written, pw2lab's :2200 forward had
# drifted to :22 (shadowed, dead) across the Hurricane-Lala reboot and
# NOTHING watched it: fleet_offline_check watches boxes, watchdogs watch
# services, and a front port is neither. One TCP connect per front per day
# closes the class.
#
# FRONT LIST: ~/.config/meshforge/fleet_fronts.txt (operator values, MF014):
#   <label> <host> <port>        # comments and blank lines ignored
# Hosts are NAMES (the /etc/hosts fleet block resolves them with DNS or the
# uplink down) — never raw IPs, which is what went stale in the registry.
#
# JUDGMENT per front (the refused/timeout split is the whole point):
#   * connects            -> ok
#   * REFUSED (fast fail) -> FAIL leg — something ANSWERED and said no: the
#     forward rule is missing/wrong while the front itself is up. This is
#     the drifted-forward class; page-worthy truth.
#   * timeout             -> UNOBSERVABLE leg — the path or front is down;
#     that is fleet_offline_check's finding to own, so here it is CONCERN,
#     never folded into OK (unobservable != healthy) and never FAIL (a dark
#     front is not evidence the rule is wrong).
#
# Verdict `fleet_front_drift`: OK all fronts answer; FAIL any refused;
# CONCERN when the only problems are unobservable legs. Empty/missing front
# list is FAIL — this organ existing with nothing to probe is miswiring,
# not health (an audit of zero things can't mean "all fronts fine").
#
# Crontab idiom (manager):
#   52 6 * * * /opt/meshforge/scripts/fleet_front_probe.sh \
#     >> ~/.local/state/meshforge/fleet_front_probe.log 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh fleet_front_drift FAIL wrapper_crashed

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERDICT="$HERE/cron_verdict.sh"
NAME=fleet_front_drift
FRONTS_FILE="${FLEET_FRONTS_FILE:-$HOME/.config/meshforge/fleet_fronts.txt}"
CONNECT_TIMEOUT=6

say() { "$VERDICT" "$NAME" "$1" "$2"; }

if [ ! -f "$FRONTS_FILE" ]; then
    say FAIL "no front list at $FRONTS_FILE — nothing probed is not everything healthy"
    exit 0
fi

ok=() refused=() dark=()
while IFS= read -r line; do
    line="${line%%#*}"
    set -- $line
    [ $# -eq 0 ] && continue
    if [ $# -ne 3 ]; then refused+=("malformed-line:'$line'"); continue; fi
    label="$1" host="$2" port="$3"
    timeout "$CONNECT_TIMEOUT" bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null
    rc=$?
    if [ $rc -eq 0 ]; then
        ok+=("$label")
        echo "$label $host:$port open"
    elif [ $rc -eq 124 ]; then
        dark+=("$label")
        echo "$label $host:$port TIMEOUT — path/front dark, state unknown"
    else
        refused+=("$label")
        echo "$label $host:$port REFUSED — front up, forward rule broken"
    fi
done < "$FRONTS_FILE"

total=$(( ${#ok[@]} + ${#refused[@]} + ${#dark[@]} ))
if [ "$total" -eq 0 ]; then
    say FAIL "front list is empty — nothing probed is not everything healthy"
    exit 0
fi

join() { local IFS=,; echo "$*"; }
summary="ok=${#ok[@]}/$total"
[ ${#refused[@]} -gt 0 ] && summary+=" REFUSED=$(join "${refused[@]}")"
[ ${#dark[@]} -gt 0 ]    && summary+=" UNOBSERVABLE=$(join "${dark[@]}")"

if [ ${#refused[@]} -gt 0 ]; then
    say FAIL "$summary (refused = the front answered and said no: forward rule missing/wrong)"
elif [ ${#dark[@]} -gt 0 ]; then
    say CONCERN "$summary (dark front: liveness is fleet_offline_check's call; not counted healthy)"
else
    say OK "$summary"
fi
exit 0
