#!/bin/bash
# raven_soak_watch.sh — self-healing watch for the raven-on-hAP Meshtastic bridge.
#
# Runs on the cron host (full Linux); reaches the AREDN hAP over ssh. The hAP
# target is operator-specific (a LAN IP) and MUST NOT live in this repo
# (MF014/MF015) — pass it via RAVEN_HAP from a local, untracked wrapper, e.g.:
#
#     #!/bin/bash
#     export RAVEN_HAP="root@<hap-lan-ip>"
#     exec /opt/meshforge/scripts/raven_soak_watch.sh "$@"
#
# and wire that wrapper to cron_verdict.sh (exit code drives the verdict):
#
#     17 */3 * * * $HOME/raven_soak_watch.sh >/dev/null 2>&1; \
#         /opt/meshforge/scripts/cron_verdict.sh raven_soak $?
#
# WHY SELF-HEAL (2026-06-27): the original watch only DETECTED `no-pid`. On the
# 56 MB no-swap hAP the kernel OOM-kills raven.uc under memory spikes; after a
# few kills procd hits its respawn ceiling and GIVES UP, leaving Raven dark for
# hours until a human restarts it. Detection without recovery is half a loop.
#
# Behaviour:
#   alive + RSS ok      -> OK (exit 0); clears the flap streak.
#   alive + RSS > limit -> FAIL rss-high (exit 1); distinct fault, no restart.
#   no-pid (reachable)  -> ONE `/etc/init.d/raven restart`, then re-probe:
#       recovered, < FLAP_THRESHOLD revivals in FLAP_WINDOW_S -> OK (exit 0):
#           a transient blip, auto-recovered, no page.
#       recovered, but >= FLAP_THRESHOLD revivals in window  -> FAIL (exit 1):
#           datapath restored, but the box is OOM-flapping — keep the signal RED
#           rather than silently papering over chronic memory pressure
#           (honest_failure_modes.md #1/#9: never mask a degraded state).
#       restart did not bring it back -> FAIL (exit 1): a real problem.
#   unreachable         -> FAIL (exit 1); cannot ssh-restart a dead host.
#
# Crash-loop tell in the log: AGE stays young across runs / repeated self-heals.

set -u

HAP="${RAVEN_HAP:-}"
KEY="${RAVEN_KEY:-$HOME/.ssh/id_ed25519}"
SSH_PORT="${RAVEN_SSH_PORT:-2222}"
LOG="${RAVEN_LOG:-$HOME/raven_soak.log}"
STATE="${RAVEN_STATE:-$HOME/.raven_soak_state}"
RSS_LIMIT_KB="${RAVEN_RSS_LIMIT_KB:-15000}"
FLAP_THRESHOLD="${RAVEN_FLAP_THRESHOLD:-3}"
FLAP_WINDOW_S="${RAVEN_FLAP_WINDOW_S:-86400}"
RESTART_WAIT_S="${RAVEN_RESTART_WAIT_S:-7}"
TS="$(date -Is)"
NOW="$(date +%s)"

log() { printf '%s raven_soak %s\n' "$TS" "$*" >> "$LOG"; }

# --- external seams (overridable for hermetic tests) -------------------------
# Print one of: "PID=<p> RSS=<r>kb AGE=<a>s" | "NOPID" | "<error text>"
raven_probe() {
  if [ -n "${RAVEN_PROBE_CMD:-}" ]; then "$RAVEN_PROBE_CMD"; return; fi
  ssh -p "$SSH_PORT" -i "$KEY" -o ConnectTimeout=10 "$HAP" \
    'P=$(cat /var/run/raven.pid 2>/dev/null); if [ -z "$P" ] || [ ! -d "/proc/$P" ]; then echo "NOPID"; else R=$(awk "/VmRSS/{print \$2}" "/proc/$P/status"); U=$(cut -d. -f1 /proc/uptime); S=$(awk "{print \$22}" "/proc/$P/stat"); AGE=$((U - S/100)); echo "PID=$P RSS=${R}kb AGE=${AGE}s"; fi' 2>&1 | tail -1
}
# Restart raven on the hAP; return 0 on success.
raven_restart() {
  if [ -n "${RAVEN_RESTART_CMD:-}" ]; then "$RAVEN_RESTART_CMD"; return; fi
  ssh -p "$SSH_PORT" -i "$KEY" -o ConnectTimeout=10 "$HAP" '/etc/init.d/raven restart' >/dev/null 2>&1
}

# Fail loud if we have no way to reach the hAP (MF014: no default IP in repo).
if [ -z "${RAVEN_PROBE_CMD:-}" ] && [ -z "$HAP" ]; then
  log "FAIL config: RAVEN_HAP unset (no hAP target)"
  exit 1
fi

# --- flap-streak state ("<heal_count> <last_heal_ts>") -----------------------
heal_count=0; last_heal_ts=0
if [ -f "$STATE" ]; then
  read -r _hc _ht _rest < "$STATE" || true
  case "${_hc:-}" in ''|*[!0-9]*) _hc=0 ;; esac
  case "${_ht:-}" in ''|*[!0-9]*) _ht=0 ;; esac
  heal_count="$_hc"; last_heal_ts="$_ht"
fi
# Expire a stale streak: revivals long ago are not "flapping now".
if [ "$(( NOW - last_heal_ts ))" -gt "$FLAP_WINDOW_S" ]; then heal_count=0; fi

save_state() {
  local tmp
  tmp="$(mktemp "${STATE}.XXXXXX")" || return 0
  printf '%s %s\n' "$1" "$2" > "$tmp" && mv "$tmp" "$STATE"
}

OUT="$(raven_probe)"

case "$OUT" in
  PID=*)
    RSS="$(printf '%s' "$OUT" | sed -n 's/.*RSS=\([0-9]*\)kb.*/\1/p')"
    if [ "${RSS:-0}" -gt "$RSS_LIMIT_KB" ]; then
      log "FAIL rss-high $OUT"
      exit 1
    fi
    # Healthy without intervention -> recovery held -> clear the flap streak.
    if [ "$heal_count" -ne 0 ]; then save_state 0 0; fi
    log "OK $OUT"
    exit 0
    ;;
  NOPID)
    log "WARN no-pid — attempting self-heal (one restart)"
    raven_restart; restart_rc=$?
    [ "$RESTART_WAIT_S" -gt 0 ] && sleep "$RESTART_WAIT_S"
    OUT2="$(raven_probe)"
    heal_count="$(( heal_count + 1 ))"
    save_state "$heal_count" "$NOW"
    case "$OUT2" in
      PID=*)
        if [ "$heal_count" -ge "$FLAP_THRESHOLD" ]; then
          log "FAIL raven-oom-flapping (heal #$heal_count in window, datapath restored) $OUT2"
          exit 1
        fi
        log "OK self-healed (heal #$heal_count) $OUT2"
        exit 0
        ;;
      *)
        log "FAIL no-pid self-heal-failed (restart rc=$restart_rc, heal #$heal_count) reprobe=[$OUT2]"
        exit 1
        ;;
    esac
    ;;
  *)
    log "FAIL unreachable: $OUT"
    exit 1
    ;;
esac
