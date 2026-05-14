#!/bin/bash
# soak_check_fleet_2026_05_14.sh — 24h fleet soak check (committed 2026-05-13).
#
# Sibling to soak_issue52.sh (one-shot incident-style soak). Companion to the
# MeshAnchor soak pattern in claude-memory-meshanchor (retired 2026-05-13).
# MA used /fleet/blackouts + NRestarts on fleet-collector/watchdog; MF doesn't
# have that platform yet (MF observability roadmap is Prom + alertmanager,
# Phase D-4/E). So this script captures the equivalent signals from MF
# surfaces:
#   - NRestarts + ActiveEnterTimestamp on key units per host (the "did anything
#     restart in the soak window" question)
#   - /healthz + /metrics-bytes for this host's local map :5000 (remote fleet hosts
#     bind 127.0.0.1 only; fleet_runtime_health.sh covers their reachability)
#   - journal ERROR-priority count last 24h per host (the "things didn't
#     fall over silently" question)
#   - fleet_runtime_health.sh snapshot (the comprehensive view)
#
# Modes:
#   baseline    capture T0 state — run before scheduling the cron
#   check       capture T+24h state, diff against baseline, emit pass/fail report
#
# Files (under ${HOME} so cron user picks the right path):
#   ~/soak-check-meshforge-baseline-2026-05-13.txt   (T0 snapshot)
#   ~/soak-check-meshforge-2026-05-14-result.txt     (T+24h report)
#
# Hosts soaked: from ${HOME}/.config/meshforge/fleet_hosts (same lookup chain
# as fleet_runtime_health.sh). This host (the NOC control box, identified by
# `hostname` at runtime) runs MF map locally and is captured separately.
#
# SSH gotcha (committed for future readers): bare `ssh "$host" ...` inside
# a `while IFS= read -r host` loop eats the host list via stdin. Use `ssh -n`
# anywhere SSH is called inside a read loop.
#
# Fleet hygiene found+fixed during baseline capture (2026-05-13):
#   moc1 (Pi 5, Prometheus/Grafana box) had openipmi.service +
#   smartmontools.service in failed state for ~2 weeks. Hardware can't satisfy
#   them (no BMC, SD-only boot); they were pulled in transitively by
#   prometheus-node-exporter-collectors and node-exporter wasn't even
#   consuming the data (ARGS="", default collectors).
#   Fix: `sudo systemctl disable --now <unit> && sudo systemctl mask <unit>`
#   on moc1. Reversible via `sudo systemctl unmask <unit>`. Baseline
#   recaptured post-fix.
#
# Pass criteria:
#   For each (host, unit) pair captured at baseline:
#     - ActiveEnterTimestamp unchanged from baseline (no restart in soak window)
#     - NRestarts == baseline NRestarts (no auto-restart bursts)
#   For this host (local NOC control box):
#     - /healthz returns 200 / 18 bytes
#     - /metrics returns 200, >1000 bytes
#   Fleet-wide:
#     - fleet_runtime_health.sh: 4 ok, 0 red, 0 unreachable
#     - ~/.meshforge-fleet-RED absent

set -u

BASELINE_FILE="${HOME}/soak-check-meshforge-baseline-2026-05-13.txt"
RESULT_FILE="${HOME}/soak-check-meshforge-2026-05-14-result.txt"

# Match fleet_runtime_health.sh's host-list lookup chain.
FLEET_HOSTS_FILE="${MESHFORGE_FLEET_HOSTS:-}"
if [ -z "$FLEET_HOSTS_FILE" ]; then
    if [ -r "${HOME}/.config/meshforge/fleet_hosts" ]; then
        FLEET_HOSTS_FILE="${HOME}/.config/meshforge/fleet_hosts"
    elif [ -r "/etc/meshforge/fleet_hosts" ]; then
        FLEET_HOSTS_FILE="/etc/meshforge/fleet_hosts"
    fi
fi
LOCAL_MAP_PORT=5000

MODE="${1:-}"
case "$MODE" in
    baseline|check) ;;
    *) echo "usage: $0 {baseline|check}" >&2; exit 64 ;;
esac

read_hosts() {
    grep -vE '^[[:space:]]*(#|$)' "$FLEET_HOSTS_FILE"
}

# Emit per-(host, unit) NRestarts + ActiveEnterTimestamp in a stable
# format that's easy to diff. Each line: host|unit|NRestarts|ActiveEnterTimestamp
capture_unit_state() {
    local host="$1"
    local ssh_args=(-n -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
    local units=(meshforge-map.service meshforge-maps.service meshforge-gateway.service mosquitto.service rnsd.service meshtasticd.service)
    local block
    block=$(ssh "${ssh_args[@]}" "$host" \
        "systemctl show ${units[*]} -p Id,NRestarts,ActiveEnterTimestamp,LoadState,ActiveState 2>/dev/null" 2>&1) || {
        echo "$host|SSH_FAIL|-|-"
        return
    }
    echo "$block" | awk -v h="$host" '
        /^Id=/        {id=substr($0,4)}
        /^NRestarts=/ {nr=substr($0,11)}
        /^ActiveEnterTimestamp=/ {ts=substr($0,22)}
        /^LoadState=/ {load=substr($0,11)}
        /^ActiveState=/ {act=substr($0,13)}
        /^$/ && id {
            print h "|" id "|" nr "|" ts "|" load "|" act
            id=""; nr=""; ts=""; load=""; act=""
        }
        END {
            if (id) print h "|" id "|" nr "|" ts "|" load "|" act
        }
    '
}

capture_local_state() {
    local units=(meshforge-map.service meshforge-cloud-push.timer mosquitto.service rnsd.service meshtasticd.service)
    systemctl show "${units[@]}" -p Id,NRestarts,ActiveEnterTimestamp,LoadState,ActiveState 2>/dev/null \
        | awk -v h="$(hostname)" '
            /^Id=/        {id=substr($0,4)}
            /^NRestarts=/ {nr=substr($0,11)}
            /^ActiveEnterTimestamp=/ {ts=substr($0,22)}
            /^LoadState=/ {load=substr($0,11)}
            /^ActiveState=/ {act=substr($0,13)}
            /^$/ && id {
                print h "|" id "|" nr "|" ts "|" load "|" act
                id=""; nr=""; ts=""; load=""; act=""
            }
            END {
                if (id) print h "|" id "|" nr "|" ts "|" load "|" act
            }
        '
}

capture_endpoint_state_local() {
    # /healthz + /metrics on this host. Cross-host probes were unreliable
    # (remote fleet hosts bind 127.0.0.1 only); fleet_runtime_health.sh
    # covers the reachability story for the rest.
    local hz_code hz_bytes mt_code mt_bytes
    read -r hz_code hz_bytes < <(curl -s -m 5 -o /dev/null -w "%{http_code} %{size_download}" "http://127.0.0.1:${LOCAL_MAP_PORT}/healthz" 2>/dev/null || echo "000 0")
    read -r mt_code mt_bytes < <(curl -s -m 8 -o /dev/null -w "%{http_code} %{size_download}" "http://127.0.0.1:${LOCAL_MAP_PORT}/metrics"  2>/dev/null || echo "000 0")
    echo "$(hostname)|healthz=${hz_code}/${hz_bytes}b|metrics=${mt_code}/${mt_bytes}b"
}

capture_journal_err() {
    local host="$1"
    local count
    count=$(ssh -n -o BatchMode=yes -o ConnectTimeout=10 "$host" \
        'journalctl --since "24 hours ago" --priority=err --no-pager -q 2>/dev/null | wc -l' 2>/dev/null || echo "?")
    [ -z "$count" ] && count="?"
    echo "${host}|err_24h=${count}"
}

write_snapshot() {
    local out="$1"
    {
        echo "=== MeshForge soak snapshot — captured $(date -Iseconds) ==="
        echo
        echo "--- 1. Unit state per host (host|unit|NRestarts|ActiveEnterTimestamp|LoadState|ActiveState) ---"
        capture_local_state
        while IFS= read -r host; do
            [ -z "$host" ] && continue
            capture_unit_state "$host"
        done < <(read_hosts)
        echo
        echo "--- 2. /healthz + /metrics (local map service on this host) ---"
        capture_endpoint_state_local
        echo
        echo "--- 3. journal ERROR-priority count, last 24h per host ---"
        while IFS= read -r host; do
            [ -z "$host" ] && continue
            capture_journal_err "$host"
        done < <(read_hosts)
        echo
        echo "--- 4. fleet_runtime_health.sh snapshot ---"
        cd /opt/meshforge && bash scripts/fleet_runtime_health.sh 2>&1
        echo
        echo "--- 5. ~/.meshforge-fleet-RED after refresh ---"
        if [ -f "$HOME/.meshforge-fleet-RED" ]; then
            cat "$HOME/.meshforge-fleet-RED"
        else
            echo "(absent — fleet clean)"
        fi
    } > "$out"
}

if [ "$MODE" = "baseline" ]; then
    write_snapshot "$BASELINE_FILE"
    echo "baseline captured -> $BASELINE_FILE"
    exit 0
fi

# MODE = check
TMP_NOW="$(mktemp -t mf-soak-now.XXXXXX)"
trap 'rm -f "$TMP_NOW"' EXIT
write_snapshot "$TMP_NOW"

{
    echo "=== MeshForge 24h soak check — captured $(date -Iseconds) ==="
    echo "Baseline: $BASELINE_FILE"
    echo
    echo "=== T+24h snapshot ==="
    cat "$TMP_NOW"
    echo
    echo "=== DIFF vs baseline (unit-state section only — anything here = soak deviation) ==="
    if [ -f "$BASELINE_FILE" ]; then
        # Only diff the first per-host unit-state section. Endpoint + journal
        # sections naturally drift and aren't a soak signal — they're just
        # captured for context.
        diff \
            <(awk '/--- 1\. Unit state/,/--- 2\./' "$BASELINE_FILE" | grep -E '^[A-Za-z]') \
            <(awk '/--- 1\. Unit state/,/--- 2\./' "$TMP_NOW"        | grep -E '^[A-Za-z]') \
            || true
    else
        echo "(baseline file missing — cannot diff)"
    fi
    echo
    echo "=== Pass criteria ==="
    echo "  - No new lines in DIFF (no NRestarts bumps, no ActiveEnterTimestamp changes)"
    echo "  - Local /healthz line: 200/18b"
    echo "  - Local /metrics line: 200 with >1000 bytes"
    echo "  - fleet_runtime_health.sh summary: 4 ok, 0 red, 0 unreachable (moc1 openipmi/smartmontools masked 2026-05-13)"
    echo "  - ~/.meshforge-fleet-RED absent (it was absent at baseline post-fix)"
} > "$RESULT_FILE"

echo "soak result captured -> $RESULT_FILE"
