#!/usr/bin/env bash
# gateway_resource_canary.sh — one RESOURCE round-trip canary fire.
#
# Fires src/lab/gateway_resource_canary.py (gateway-reliability arc A1): a
# control single-packet PING + a PINGBIG whose REPLY is resource-sized, so the
# gateway must assemble an inbound RNS Resource — the exact step the 2026-06-20
# wx-total-loss EROFS broke and that the single-packet gateway_rt_canary is
# blind to. The Python writes the verdict envelope itself (atomic last.json in
# STATE_DIR); probe_resource_canary_degraded consumes it (FAIL/CONCERN, or a
# stale file = the canary stopped → silence is the failure mode). No
# cron_verdict here on purpose — the dedicated probe is the consumer (mirrors
# the synth-soak pattern), so a FAIL pages once, not twice.
#
# Runs on a GATEWAY box (moc/moc3 — wherever Resource assembly is under test).
# The --peer must be a DIFFERENT box running a resource-aware lab echo
# (meshforge-echo with PINGBIG support; the round trip must leave and return).
#
# Knobs (env):
#   RESOURCE_CANARY_PEER         lab_peers name of the echo target
#                                (default meshanchor-server — cross-box, no inbox)
#   RESOURCE_CANARY_REPLY_BYTES  resource reply size (default 512; >319 = Resource)
#   RESOURCE_CANARY_LEG1_TIMEOUT seconds to wait for PINGBIG outbound CONFIRMED (120)
#   RESOURCE_CANARY_LEG2_TIMEOUT seconds to wait for the bridged-back rows      (150)
#   STATE_DIR                    verdict-envelope dir (MUST match the probe —
#                                default ~/.local/state/meshforge/gateway_resource_canary)
#
# Verdicts (in the envelope + the VERDICT line):
#   OK       control + resource both round-tripped
#   FAIL     resource path broken (control back, resource NOT = EROFS signature)
#            OR the PINGBIG outbound never confirmed
#   CONCERN  neither reply back (peer/path), or control-only loss, or leg2 DB unreadable
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PEER="${RESOURCE_CANARY_PEER:-meshanchor-server}"
REPLY_BYTES="${RESOURCE_CANARY_REPLY_BYTES:-512}"
LEG1="${RESOURCE_CANARY_LEG1_TIMEOUT:-120}"
LEG2="${RESOURCE_CANARY_LEG2_TIMEOUT:-150}"
# Keep this default byte-identical to default_state_dir() in the canary and
# _resolve_resource_canary_dir() in the probe (TestStateDirContract pins it).
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/gateway_resource_canary}"
mkdir -p "$STATE_DIR"
log="$STATE_DIR/fire.log"

cd "$REPO_ROOT/src" || {
    echo "$(date -u +%FT%TZ) resource-canary: repo src dir missing" >>"$log"
    exit 1
}

python3 -m lab.gateway_resource_canary \
    --peer "$PEER" \
    --reply-bytes "$REPLY_BYTES" \
    --leg1-timeout "$LEG1" \
    --leg2-timeout "$LEG2" \
    --state-dir "$STATE_DIR" \
    >>"$log" 2>&1
rc=$?

# Always exit 0 — this is an observability tool; the truth is in the envelope
# the probe reads (and the VERDICT line in the log). A non-zero canary exit
# (FAIL=1/CONCERN=2) must NOT make systemd mark the oneshot service "failed",
# which would muddy `systemctl --user status`. Same rationale as the synth-soak
# fire script.
{ echo "$(date -u +%FT%TZ) resource-canary exit=$rc peer=$PEER bytes=$REPLY_BYTES"; } >>"$log"
exit 0
