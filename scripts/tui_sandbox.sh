#!/bin/bash
# tui_sandbox.sh — run a command beside a SIMULATED meshtasticd, sealed off
# from the real one. Invoked by scripts/tui_journey.py for journeys marked
# "sandbox"; not meant to be run by hand.
#
#   unshare -rnm bash scripts/tui_sandbox.sh <scratch_dir> <cmd> [args...]
#
# Isolation comes from the namespaces the CALLER opens, not from this script:
#   -n  a private network namespace: localhost:4403 inside is the SimRadio
#       meshtasticd started here; the real radio's 4403 is unreachable, and
#       abstract sockets (@rns/*) are per-namespace, so no host rnsd is seen.
#   -m  a private mount namespace: ~/.config/meshforge is shadowed by a
#       scratch COPY, so handlers that persist settings write the copy.
#   -r  unprivileged (uid 0 inside maps to the caller outside) — no real root.
#
# What the sim does NOT model (operator, 2026-09-27): RF, timing, a real
# radio's init/SPI behaviour. A sandbox journey proves the TUI's write
# reaches a device and reads back — plumbing, not radio behaviour.
set -u
SCRATCH="$1"; shift
[ -d "$SCRATCH" ] || { echo "tui_sandbox: scratch dir missing: $SCRATCH" >&2; exit 3; }
if [ "$(cat /proc/self/uid_map | awk '{print $1" "$2}')" = "0 0" ]; then
    echo "tui_sandbox: REFUSING — not inside a user namespace (run via unshare -rnm)" >&2
    exit 3
fi
if timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/4403' 2>/dev/null; then
    echo "tui_sandbox: REFUSING — 4403 already answers here; not an isolated netns" >&2
    exit 3
fi

ip link set lo up

# Shadow the operator's TUI config with a scratch copy (logs excluded).
# MF_REAL_HOME comes from the parent (utils.paths.get_real_user_home), which
# resolved it OUTSIDE the namespace where the uid still maps to the operator.
[ -n "${MF_REAL_HOME:-}" ] || { echo "tui_sandbox: MF_REAL_HOME not set" >&2; exit 3; }
CFG="$MF_REAL_HOME/.config/meshforge"
mkdir -p "$SCRATCH/config_copy"
if [ -d "$CFG" ]; then
    (cd "$CFG" && tar --exclude=./logs -cf - .) | (cd "$SCRATCH/config_copy" && tar -xf -)
    mount --bind "$SCRATCH/config_copy" "$CFG" || { echo "tui_sandbox: bind mount failed" >&2; exit 3; }
fi

mkdir -p "$SCRATCH/sim/fs" "$SCRATCH/sim/config.d"
cat > "$SCRATCH/sim/config.yaml" <<EOF
General:
  ConfigDirectory: $SCRATCH/sim/config.d/
  MaxNodes: 100
Logging:
  LogLevel: info
EOF
/usr/bin/meshtasticd --sim -d "$SCRATCH/sim/fs" -c "$SCRATCH/sim/config.yaml" -p 4403 \
    >"$SCRATCH/sim/sim.log" 2>&1 &
SIM=$!
trap 'kill $SIM 2>/dev/null; wait $SIM 2>/dev/null' EXIT
for _ in $(seq 1 30); do
    timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/4403' 2>/dev/null && break
    kill -0 $SIM 2>/dev/null || { echo "tui_sandbox: sim meshtasticd died — see $SCRATCH/sim/sim.log" >&2; exit 3; }
    sleep 1
done
timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/4403' 2>/dev/null \
    || { echo "tui_sandbox: sim never answered on 4403" >&2; exit 3; }

MF_TUI_SANDBOX=1 MF_SANDBOX_CONFIG="$CFG" "$@"
