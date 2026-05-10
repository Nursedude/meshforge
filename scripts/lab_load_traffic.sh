#!/usr/bin/env bash
# lab_load_traffic.sh — Sustained synthetic LXMF traffic generator for
# Phase-1 relay validation + Pi load-tolerance smoke testing.
#
# Wraps `scripts/validate_rns_to_mesh.py` in a loop that alternates between
# two peer-gateway destinations on a configurable cadence. Each message
# carries a unique timestamp + RANDOM seed so it does not collide with the
# 60s message_queue content-hash dedup window — every send should reach
# `_process_rns_to_mesh` and (for non-peer source identities) trigger the
# Phase-1 relay-on-receive path on the receiving gateway.
#
# Originally written 2026-05-10 to validate Phase-1 relay
# (`project_phase1_relay_soak_findings.md`) under sustained load and to
# exercise fleet boxes during the BIRC May-17 prep arc. Reusable for any
# future relay-path soak, message_queue dedup repro, or "is the gateway
# wedged?" check.
#
# Usage:
#   GATEWAY_A_HASH=<32-hex> GATEWAY_B_HASH=<32-hex> \
#     bash scripts/lab_load_traffic.sh [count] [cadence_sec]
#
#   GATEWAY_A_HASH       LXMF hash of first peer gateway       (required)
#   GATEWAY_B_HASH       LXMF hash of second peer gateway      (required)
#   GATEWAY_A_LABEL      Display label for A (default: gw_a)
#   GATEWAY_B_LABEL      Display label for B (default: gw_b)
#   LOG                  Output file                            (default: /tmp/lab_load_traffic.log)
#   count                Total messages to send                 (default: 16)
#   cadence_sec          Seconds between sends                  (default: 75 — clears the 60s dedup window)
#
# Find peer-gateway hashes via:
#   journalctl -u meshforge-gateway | grep 'Gateway LXMF destination'
#
# Sender must be running rnsd locally and have the validator identity loaded
# at ~/.config/meshforge/lxmf_validator_identity (auto-created on first
# validate_rns_to_mesh.py run). The validator's hash MUST NOT appear in either
# gateway's `rns.peer_gateway_destinations` config, otherwise the peer-source
# guard will skip relay.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

GATEWAY_A_HASH="${GATEWAY_A_HASH:-}"
GATEWAY_B_HASH="${GATEWAY_B_HASH:-}"
GATEWAY_A_LABEL="${GATEWAY_A_LABEL:-gw_a}"
GATEWAY_B_LABEL="${GATEWAY_B_LABEL:-gw_b}"
LOG="${LOG:-/tmp/lab_load_traffic.log}"

count="${1:-16}"
cadence="${2:-75}"

if [ -z "$GATEWAY_A_HASH" ] || [ -z "$GATEWAY_B_HASH" ]; then
    echo "error: GATEWAY_A_HASH and GATEWAY_B_HASH must be set" >&2
    echo "       find via: journalctl -u meshforge-gateway | grep 'Gateway LXMF destination'" >&2
    exit 2
fi

# Validate hash shape (32 hex chars).
for h in "$GATEWAY_A_HASH" "$GATEWAY_B_HASH"; do
    if ! [[ "$h" =~ ^[0-9a-fA-F]{32}$ ]]; then
        echo "error: gateway hash must be 32 hex chars, got: $h" >&2
        exit 2
    fi
done

if ! [[ "$count" =~ ^[0-9]+$ ]] || [ "$count" -lt 1 ]; then
    echo "error: count must be a positive integer, got: $count" >&2
    exit 2
fi
if ! [[ "$cadence" =~ ^[0-9]+$ ]] || [ "$cadence" -lt 1 ]; then
    echo "error: cadence must be a positive integer, got: $cadence" >&2
    exit 2
fi

echo "=== lab traffic generator started $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >>"$LOG"
echo "  count=$count cadence=${cadence}s" >>"$LOG"
echo "  A=$GATEWAY_A_LABEL ($GATEWAY_A_HASH)" >>"$LOG"
echo "  B=$GATEWAY_B_LABEL ($GATEWAY_B_HASH)" >>"$LOG"

for i in $(seq 1 "$count"); do
    if [ $((i % 2)) = 1 ]; then
        target="$GATEWAY_A_HASH"
        label="$GATEWAY_A_LABEL"
    else
        target="$GATEWAY_B_HASH"
        label="$GATEWAY_B_LABEL"
    fi

    echo "--- $(date -u +%H:%M:%S) sending msg $i/$count to $label ---" >>"$LOG"
    python3 scripts/validate_rns_to_mesh.py \
        --to "$target" \
        --text "[lab-load] msg $i to $label @ $(date -u +%H%M%S) iter=$RANDOM" \
        --title "Lab load test" \
        --path-timeout 8 \
        --delivery-timeout 12 >>"$LOG" 2>&1
    echo "--- $(date -u +%H:%M:%S) msg $i exit=$? ---" >>"$LOG"

    if [ "$i" -lt "$count" ]; then
        sleep "$cadence"
    fi
done

echo "=== lab traffic generator finished $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >>"$LOG"
