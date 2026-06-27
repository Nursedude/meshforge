#!/usr/bin/env bash
# gateway_resourcepath.sh — discover the live gateway RNS resourcepath.
#
# WHY this exists (2026-06-27): the gateway-reliability A1 canary, the #60
# sandbox protection, and the A1 validation drill all *assumed* the gateway
# assembles inbound RNS Resources under /etc/reticulum/storage/resources. That
# is NOT reliably true. The gateway's runtime resourcepath is
# ``RNS.Reticulum.resourcepath`` — whichever configdir wins the PROCESS-SINGLETON
# RNS init (the node_tracker's /tmp/meshforge_rns_client won on moc 2026-06-27;
# the main bridge's /etc/reticulum won at #60 time). Under PrivateTmp the /tmp
# path is INSIDE the gateway's mount namespace. The only way to know the live
# path for sure is to OBSERVE the assembly: strace the gateway across one
# canary fire and read the openat() target. That is what this does.
#
# Use it: (a) when A1 FAILs, to confirm whether the gateway's real resourcepath
# is writable; (b) in the validation drill (Phase 0) to learn WHERE to inject
# the EROFS fault.
#
# Output: prints the resourcepath (dir holding the assembled resource file),
# plus a PrivateTmp note + the namespace-aware injection hint for the drill.
#
# Read-only: strace attaches/detaches; the canary fire is exactly what the
# timer already does. Needs sudo for strace -p on the gateway PID.
#
# Knobs (env):
#   RESOURCE_CANARY_PEER   lab_peers name of the resource-aware echo peer
#                          (REQUIRED — must round-trip; a peer from lab_peers)
#   STRACE_WINDOW_S        max seconds to strace before giving up (default 60)
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PEER="${RESOURCE_CANARY_PEER:-}"
WINDOW="${STRACE_WINDOW_S:-60}"

if [[ -z "$PEER" ]]; then
    echo "error: set RESOURCE_CANARY_PEER to a resource-aware echo peer " \
         "from lab_peers (RESOURCE_CANARY_PEER=<peer> $0)" >&2
    exit 2
fi
if ! command -v strace >/dev/null 2>&1; then
    echo "error: strace not installed (apt install strace)" >&2
    exit 2
fi
if ! command -v sudo >/dev/null 2>&1; then
    echo "error: sudo required to strace the gateway PID" >&2
    exit 2
fi

PID="$(pgrep -f 'gateway/bridge_cli.py' | head -1)"
if [[ -z "$PID" ]]; then
    echo "error: gateway (bridge_cli.py) not running — nothing to trace" >&2
    exit 1
fi
echo "gateway pid=$PID  peer=$PEER  window=${WINDOW}s"

trace="$(mktemp /tmp/gw_resourcepath_strace.XXXXXX)"
state="$(mktemp -d /tmp/gw_resourcepath_state.XXXXXX)"
cleanup() { rm -rf "$trace" "$state" 2>/dev/null; }
trap cleanup EXIT

# Trace only openat; -qq quiets attach/detach chatter. timeout SIGTERM detaches
# strace cleanly (the gateway keeps running normally).
sudo timeout "$WINDOW" strace -f -e trace=openat -qq -p "$PID" 2>"$trace" &
sp=$!
sleep 2

# Fire one 512 canary (the default — proven to round-trip and to assemble an
# on-disk Resource). Scratch state dir so the production verdict is untouched.
RESOURCE_CANARY_PEER="$PEER" RESOURCE_CANARY_REPLY_BYTES=512 \
    STATE_DIR="$state" \
    timeout "$WINDOW" bash "$REPO_ROOT/scripts/gateway_resource_canary.sh" \
    >/dev/null 2>&1
sleep 3
sudo kill "$sp" 2>/dev/null
wait "$sp" 2>/dev/null

# The assembled-Resource file is ``<resourcepath>/<64-hex-hash>`` opened
# O_CREAT during the fire. Pull the first such path and report its dirname.
hit="$(grep -oE '"/[^"]*/storage/resources/[0-9a-f]{16,}"' "$trace" \
       | tr -d '"' | head -1)"

verdict="$(grep -oE '"verdict":"[A-Z]+"' "$state/last.json" 2>/dev/null \
           | head -1)"
echo "canary $verdict (512B fire)"

if [[ -z "$hit" ]]; then
    echo "RESOURCEPATH: (none observed)"
    echo "  No on-disk Resource assembly was traced in ${WINDOW}s. Either the"
    echo "  512 reply did not round-trip (check the echo peer / RNS path), or"
    echo "  the gateway assembles in a namespace this trace did not see."
    echo "  Re-run with a known-good peer, or inspect with:"
    echo "    sudo nsenter -t $PID -m -- ls -la /tmp/meshforge_rns_client/storage/resources /etc/reticulum/storage/resources"
    exit 1
fi

resourcepath="$(dirname "$hit")"
echo "RESOURCEPATH: $resourcepath"
case "$resourcepath" in
    /tmp/*)
        echo "  NOTE: this is inside the gateway's PrivateTmp namespace. The"
        echo "  path above is as the GATEWAY sees it; from the host shell reach"
        echo "  it via the gateway mount namespace:"
        echo "    sudo nsenter -t $PID -m -- ls -la $resourcepath"
        echo
        echo "  Drill EROFS injection (reversible, NO restart):"
        echo "    sudo nsenter -t $PID -m -- chmod a-w $resourcepath   # fault in"
        echo "    # fire A1 -> expect resource_back=false FAIL + A2 journal EROFS"
        echo "    sudo nsenter -t $PID -m -- chmod u+rwx $resourcepath # restore"
        ;;
    /etc/reticulum/*)
        echo "  NOTE: under /etc/reticulum/storage — the #60-protected path."
        echo "  ReadWritePaths must include /etc/reticulum/storage (it does on"
        echo "  the fleet). Drill EROFS via a BindReadOnlyPaths drop-in on the"
        echo "  gateway unit (see the A1 validation protocol)."
        ;;
esac
exit 0
