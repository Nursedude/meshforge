#!/usr/bin/env bash
# ma_health_uplink.sh — mini uplink for the MeshAnchor-only box (2026-07-03).
#
# meshanchor-server runs NO MeshForge (no watchdog, no mini), so its health
# was operator-invisible: a meshtastic dep-floor drift sat silent at 2.7.8
# while the fleet pin was 2.7.9, and a dead meshanchor unit pages no one
# (fleet_box_unreachable watches the BOX, federation watches :5000 — neither
# watches the units or the dep floor).
#
# This is the manager-box half of the uplink, same pattern as the fleet
# tracer / ntfy loopback: an ACTIVE probe run on cron cadence. It ssh-checks:
#   1. systemd units active (default: meshanchor meshanchor-map)
#   2. dep version-floor via MeshAnchor's scripts/dep_floor_check.py, run
#      under the box's venv python — the interpreter the services import
#      from (the consumer-of-record; the resident ActiveHealthProbe does not
#      run under core.orchestrator deployments, so a one-shot is the honest
#      check host there).
#
# Alerting rides the PROVEN layers — no new mini rule, no new signal class:
# wire the crontab line to cron_verdict (#78); a FAIL pages via mini's
# existing cron_verdict_stale_any rule, and a silent/dead cron is caught by
# probe_cron_verdict_stale's stale gate. Run on the MANAGER box:
#   */15 * * * * /opt/meshforge/scripts/ma_health_uplink.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh ma_health $?
#
# Exit 0 = everything checked healthy. Exit 1 = a concrete failure OR the
# box/observation was unreachable (unobservable is NEVER healthy; overlap
# with fleet_box_unreachable on a hard box-down is acceptable — both clear
# together). Detail for the operator lands in
# ~/.local/state/meshforge/ma_health_last.txt either way.
set -uo pipefail

HOST="${MA_HEALTH_HOST:-meshanchor-server}"
UNITS="${MA_HEALTH_UNITS:-meshanchor meshanchor-map}"
STATE_DIR="${MA_HEALTH_STATE_DIR:-$HOME/.local/state/meshforge}"
DETAIL="$STATE_DIR/ma_health_last.txt"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

mkdir -p "$STATE_DIR"

fail=()
ok=()

# One ssh round-trip carries both checks (cheap on the box, no partial reads).
remote_out="$(timeout 45 ssh "${SSH_OPTS[@]}" "$HOST" "
  for u in $UNITS; do
    printf 'unit %s %s\n' \"\$u\" \"\$(systemctl is-active \"\$u\" 2>/dev/null)\"
  done
  PY=/opt/meshanchor/venv/bin/python
  [ -x \"\$PY\" ] || PY=python3
  printf 'depfloor '
  timeout 30 \"\$PY\" /opt/meshanchor/scripts/dep_floor_check.py 2>&1 | head -1
" 2>&1)"
ssh_rc=$?

if [ "$ssh_rc" -ne 0 ] && [ -z "$remote_out" ]; then
    fail+=("unreachable: ssh to $HOST failed (rc=$ssh_rc)")
else
    while IFS= read -r line; do
        case "$line" in
            unit\ *)
                u="$(printf '%s' "$line" | awk '{print $2}')"
                state="$(printf '%s' "$line" | awk '{print $3}')"
                if [ "$state" = "active" ]; then
                    ok+=("$u active")
                else
                    fail+=("$u ${state:-unknown}")
                fi
                ;;
            depfloor\ OK*)
                ok+=("${line#depfloor }")
                ;;
            depfloor\ *)
                # FAIL, empty, or garbage — a failed observation is a failure.
                fail+=("${line#depfloor }")
                ;;
        esac
    done <<< "$remote_out"
    # No recognizable output at all = broken observation, never "healthy".
    if [ "${#ok[@]}" -eq 0 ] && [ "${#fail[@]}" -eq 0 ]; then
        fail+=("no parseable output from $HOST: $(printf '%s' "$remote_out" | head -c 200)")
    fi
fi

{
    printf 'ma_health_uplink %s host=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$HOST"
    for f in "${fail[@]}"; do printf 'FAIL %s\n' "$f"; done
    for o in "${ok[@]}";  do printf 'OK   %s\n' "$o"; done
} > "$DETAIL"

if [ "${#fail[@]}" -gt 0 ]; then
    printf 'ma_health FAIL: %s\n' "$(IFS='; '; echo "${fail[*]}")"
    exit 1
fi
printf 'ma_health OK: %s\n' "$(IFS='; '; echo "${ok[*]}")"
exit 0
