#!/usr/bin/env bash
# ma_health_uplink.sh — mini uplink for the MeshAnchor-only box (2026-07-03).
#
# meshanchor-server's health was operator-invisible: a meshtastic dep-floor
# drift sat silent at 2.7.8 while the fleet pin was 2.7.9, and a dead
# meshanchor unit pages no one (fleet_box_unreachable watches the BOX,
# federation watches :5000 — neither watches the units or the dep floor).
#
# ⚠️ Corrected 2026-08-12: this said "runs NO MeshForge (no watchdog, no
# mini)". Both halves are now false, and the conclusion survives anyway —
# measured on the box that day:
#   * ONE MeshForge unit runs there, meshforge-watchdog.service (the only
#     meshforge-* unit file present, enabled + active). But its per-box
#     override narrows services_expected_active to ["rnsd.service"], so it
#     judges rnsd and NOTHING meshanchor-shaped. "A dead meshanchor unit
#     pages no one" still holds — via a narrowed scope, not an absent probe.
#   * A mini runs there too: MeshAnchor's OWN meshanchor-mini-dudeai.service,
#     as a USER unit. Its journal is a black hole (`journalctl --user` →
#     "No journal files were found", still true 2026-08-12), so user-unit
#     warnings there vanish rather than page.
# The load-bearing reason for an OFF-box probe was never "nothing watches
# this box" — it is that a watchdog cannot report its own death, and neither
# can a mini whose journal is dark. Keep the uplink.
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
# Transitional unit states (activating/deactivating/reloading) get ONE bounded
# recheck: a deliberate restart (e.g. a daily restart timer) that coincides
# with this probe is ambiguous, not failed — mapping it straight to FAIL paged
# daily when meshanchor-map-restart.timer and this cron shared 04:30
# (2026-07-19). Still-transitional after the recheck = FAIL (real flapping).
remote_out="$(timeout 60 ssh "${SSH_OPTS[@]}" "$HOST" "
  for u in $UNITS; do
    s=\"\$(systemctl is-active \"\$u\" 2>/dev/null)\"
    case \"\$s\" in
      activating|deactivating|reloading)
        sleep 8
        s=\"\$(systemctl is-active \"\$u\" 2>/dev/null)\"
        ;;
    esac
    printf 'unit %s %s\n' \"\$u\" \"\$s\"
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
    # FAIL evidence must survive the next OK run's $DETAIL overwrite —
    # a self-clearing transient is undiagnosable otherwise (honest_failure_modes #9)
    cp -f "$DETAIL" "$STATE_DIR/ma_health_last_fail.txt" 2>/dev/null
    printf 'ma_health FAIL: %s\n' "$(IFS='; '; echo "${fail[*]}")"
    exit 1
fi
printf 'ma_health OK: %s\n' "$(IFS='; '; echo "${ok[*]}")"
exit 0
