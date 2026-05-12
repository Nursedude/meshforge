#!/usr/bin/env bash
# lab_synth_soak_fire.sh — one synth soak cycle, JSON to disk.
#
# Fired by meshforge-synth-soak.timer (systemd-user, 30-min cadence by
# default). Each fire produces one timestamped JSON in
# ~/.local/state/meshforge/synth_soak/. lab_synth_soak_rollup.sh
# aggregates the directory into per-pair stats over a rolling window.
#
# Decoupled from the synth Python module so cadence + parameters live
# in shell (operator-tunable) and the Python module stays focused on
# one fire.
#
# Knobs (env vars):
#   SYNTH_USERS       (default 10)
#   SYNTH_PATTERN     (default burst)
#   SYNTH_INTERVAL    (default 5)
#   SYNTH_DURATION    (default 60)
#   SYNTH_ACK_TIMEOUT (default 30)
#   SYNTH_EXCLUDE     (default moc3 — gateway-only Pi, no echo by design)
#   SYNTH_THRESHOLD   (default 0.95)
#   STATE_DIR         (default $XDG_STATE_HOME/meshforge/synth_soak)
#
# Retention: files older than 14 days are pruned at end of run.

set -u

USERS="${SYNTH_USERS:-10}"
PATTERN="${SYNTH_PATTERN:-burst}"
INTERVAL="${SYNTH_INTERVAL:-5}"
DURATION="${SYNTH_DURATION:-60}"
ACK_TIMEOUT="${SYNTH_ACK_TIMEOUT:-30}"
EXCLUDE="${SYNTH_EXCLUDE:-moc3}"
THRESHOLD="${SYNTH_THRESHOLD:-0.95}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/synth_soak}"

mkdir -p "$STATE_DIR"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$STATE_DIR/synth-$stamp.json"
log="$STATE_DIR/fire.log"

{
    echo "=== synth soak fire @ $stamp ==="
    echo "  users=$USERS pattern=$PATTERN interval=${INTERVAL}s duration=${DURATION}s ack_timeout=${ACK_TIMEOUT}s"
    echo "  exclude=$EXCLUDE threshold=$THRESHOLD"
    echo "  output=$out"
} >>"$log"

cd /opt/meshforge/src

python3 -m lab.lxmf_multi_user_synth \
    --users "$USERS" \
    --pattern "$PATTERN" \
    --interval "$INTERVAL" \
    --duration "$DURATION" \
    --ack-timeout "$ACK_TIMEOUT" \
    --exclude "$EXCLUDE" \
    --ok-ratio-threshold "$THRESHOLD" \
    --output json \
    --loglevel WARNING \
    >"$out" 2>>"$log"

rc=$?
echo "  exit=$rc (size=$(wc -c <"$out") bytes)" >>"$log"

# Retention prune (best-effort).
find "$STATE_DIR" -maxdepth 1 -name 'synth-*.json' -mtime +14 -delete 2>/dev/null || true

# Always exit 0 — observability tool; soak should not flag service red
# on routine fleet-state variance. Same rationale as tracer (e27930e).
exit 0
