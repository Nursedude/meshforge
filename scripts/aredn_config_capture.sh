#!/bin/bash
# aredn_config_capture.sh — monthly manager organ: snapshot every AREDN
# node's uci config into ~/fleet-configs/aredn-<name>/uci-export.txt, where
# the fleet-vault bundle carries it off-site.
#
# WHY (2026-08-15): pw2lab's port-forward drifted across the Hurricane-Lala
# reboot and there was NO capture of any AREDN node's config — no restore
# source, no diff base. The nodes hold the fleet's NAT fronts (trdev, pw2lab,
# moc5 all reachable only through them); losing one to a failed flash or a
# bad reset meant reconstructing forwards from memory.
#
# Node list: ~/.config/meshforge/aredn_nodes.txt (operator values, MF014):
#   <dirname> <ssh-host> [port] [type]   # port default 2222; type default
#                                        # aredn (root, `uci export`).
#                                        # type=routeros: admin login,
#                                        # `/export` -> rsc-export.txt
#                                        # (added 2026-08-15 for m1, the last
#                                        # uncaptured config in the domain)
#
# Each capture OVERWRITES uci-export.txt only after a successful fetch into
# a temp file (a failed ssh must never truncate the last good snapshot), and
# keeps the previous copy as uci-export.prev.txt so one bad capture is
# always diffable/recoverable. A node that cannot be captured is a named
# CONCERN leg — unobservable is not captured.
#
# Crontab idiom (manager, monthly):
#   37 7 5 * * /opt/meshforge/scripts/aredn_config_capture.sh \
#     >> ~/.local/state/meshforge/aredn_config_capture.log 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh aredn_config_capture FAIL wrapper_crashed

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERDICT="$HERE/cron_verdict.sh"
NAME=aredn_config_capture
NODES_FILE="${AREDN_NODES_FILE:-$HOME/.config/meshforge/aredn_nodes.txt}"
DEST_ROOT="${AREDN_CAPTURE_ROOT:-$HOME/fleet-configs}"

say() { "$VERDICT" "$NAME" "$1" "$2"; }

if [ ! -f "$NODES_FILE" ]; then
    say FAIL "no node list at $NODES_FILE — nothing captured is not everything safe"
    exit 0
fi

captured=() failed=()
# fd 3 carries the loop input: ssh reads stdin and would swallow the rest of
# the node list (the same bug fleet_registry_sync.sh shipped with today).
while IFS= read -r line <&3; do
    line="${line%%#*}"
    set -- $line
    [ $# -eq 0 ] && continue
    name="$1" host="$2" port="${3:-2222}" kind="${4:-aredn}"
    if [ "$kind" = "routeros" ]; then
        ruser=admin outfile=rsc-export.txt
        rcmd='/export'   # RouterOS stamps its own date/version/model header
    else
        ruser=root outfile=uci-export.txt
        rcmd='echo "# captured $(date -u +%Y-%m-%dT%H:%M:%SZ) from $(cat /etc/hostname)"; grep DISTRIB_DESCRIPTION /etc/openwrt_release; echo; uci export'
    fi
    dest="$DEST_ROOT/$name"
    mkdir -p "$dest"
    tmp="$dest/.$outfile.fetch.$$"
    if ssh -o ConnectTimeout=10 -o BatchMode=yes -p "$port" "$ruser@$host" \
         "$rcmd" > "$tmp" 2>/dev/null && [ "$(wc -l < "$tmp")" -gt 10 ]; then
        [ -f "$dest/$outfile" ] && cp "$dest/$outfile" "$dest/${outfile%.txt}.prev.txt"
        mv "$tmp" "$dest/$outfile"
        captured+=("$name($(wc -l < "$dest/$outfile")l)")
    else
        rm -f "$tmp"
        failed+=("$name")
    fi
done 3< "$NODES_FILE"

join() { local IFS=,; echo "$*"; }
total=$(( ${#captured[@]} + ${#failed[@]} ))
if [ "$total" -eq 0 ]; then
    say FAIL "node list is empty — nothing captured is not everything safe"
elif [ ${#failed[@]} -gt 0 ]; then
    say CONCERN "captured=$(join "${captured[@]:-}") UNCAPTURED=$(join "${failed[@]}") (last good snapshot retained)"
else
    say OK "captured $total node(s): $(join "${captured[@]}")"
fi
exit 0
