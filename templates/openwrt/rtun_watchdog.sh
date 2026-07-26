#!/bin/sh
# rtun watchdog — self-heal the Dropbear reverse tunnel to the home endpoint WITHOUT a reboot.
#
# Origin: 2026-07-21. rtun's `ssh -R` (dropbear) stayed ALIVE on a wedged link for ~3h
# — the ssh control channel kept answering keepalives while the forwarded ports
# (:2222 -> owrt1:22, :14403 -> :4403, :19443 -> kiai:9443) stopped working. procd's
# respawn only fires when the process EXITS, and dropbear -K cannot see a forward-only
# failure, so nothing self-healed until a manual reboot.
#
# This tests the tunnel END-TO-END (owrt1 -> the home endpoint -> back through :2222) with a
# FRESH ssh (independent of the tunnel), and restarts rtun only when the home endpoint is
# reachable but the reverse tunnel is confirmed dead.
#
# ── 2026-07-26 revision: the probe was crying wolf ───────────────────────────
# Measured over 91 h: 134 TUN_DEAD, EVERY ONE "#1 consecutive" and recovered on
# the next 3-min cycle, and 4 bounces. Cross-checks over the same window:
#   * router_scout pulls THROUGH this tunnel every 30 min — 30/30 OK, 0 fail
#   * the inner leg alone, 40 fresh runs from the home endpoint — 40/40 OK, mean 0.30 s
#   * this nested probe, 15 runs — 1 failed, by HANGING the full timeout
# The tunnel carries real work reliably; it is the 3-hop probe that is flaky.
# The gap distribution is memoryless (CV 0.89, 3→192 min, flat across all 24 h),
# so it is NOT a periodic idle/NAT timeout on the m1 hop — it is a random
# per-attempt failure of the probe's own fresh connections. Three fixes:
#
#   1. UNOBSERVABLE != DEAD. An outer-ssh timeout returns WD_OUT="" and used to
#      fall straight into the DEAD counter as loop='<empty/timeout>' (19 of the
#      134). Learning nothing about the tunnel is not evidence it is down —
#      honest_failure_modes #2. It now HOLDS state and never counts.
#   2. RETRY before counting. At a ~7% independent per-attempt failure rate one
#      probe is not evidence; 3 spaced attempts put a false DEAD near 0.03%.
#      A real wedge still fails all 3, so heal time is unchanged (~6 min).
#   3. EVERY SWALLOW GETS A WITNESS (hfm #9). The inner stderr was sent to
#      /dev/null, which is why the residual mechanism behind the 115 genuine
#      TUN_DEAD replies is still unknown. It is now captured into the log so
#      the next real failure is diagnosable instead of guessed at.
#
# Retire: remove the crontab line. Log: /root/rtun_watchdog.log
# Operator values live OUTSIDE the repo (MF014). Deploy the .example alongside
# this script and fill it in:  RTUN_REMOTE="user@host-or-ip" of the endpoint
# that rtun dials home to.
CONF="${RTUN_CONF:-/etc/meshforge/rtun_watchdog.conf}"
[ -r "$CONF" ] && . "$CONF"
REMOTE="${RTUN_REMOTE}"
KEY="/root/.ssh/id_dropbear"
LOG="/root/rtun_watchdog.log"
STATE="/root/.rtun_wd_fail"
HEARTBEAT="/root/.rtun_wd_last_ok"   # single-line, overwritten each healthy run — a witness that the watchdog is alive (honest_failure_modes #9)
FAIL_LIMIT=2
PROBE_TRIES=3                        # independent attempts before believing DEAD
PROBE_GAP=5                          # seconds between attempts
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# Refuse LOUDLY rather than silently disarming. An unconfigured watchdog that
# exits 0 looks identical to a healthy one, and the thing it guards is the only
# way back into this box (honest_failure_modes #2/#9).
if [ -z "$REMOTE" ]; then
  echo "$TS CONFIG-MISSING: RTUN_REMOTE unset (looked in $CONF) — watchdog DISARMED" >> "$LOG"
  exit 78
fi

# BusyBox ash has no `timeout` applet — poor-man's bound: run in bg, kill after N s.
# Sets WD_OUT to captured stdout; returns cmd rc, or 124 on timeout.
bounded() {  # bounded <secs> <cmd...>
  secs="$1"; shift
  out="/tmp/rtun_wd.$$"
  "$@" >"$out" 2>/dev/null &
  p=$!
  i=0
  while [ "$i" -lt "$secs" ]; do
    kill -0 "$p" 2>/dev/null || break
    sleep 1; i=$((i+1))
  done
  if kill -0 "$p" 2>/dev/null; then
    kill "$p" 2>/dev/null; sleep 1; kill -9 "$p" 2>/dev/null
    WD_OUT=""; rm -f "$out"; return 124
  fi
  wait "$p"; rc=$?
  WD_OUT="$(cat "$out" 2>/dev/null)"; rm -f "$out"
  return "$rc"
}

# 1) Is the home endpoint reachable at all? Don't thrash rtun during a real upstream/network outage.
if ! bounded 15 ssh -y -y -i "$KEY" "$REMOTE" true; then
  echo "$TS net-down: the home endpoint unreachable — no action (procd/dropbear resume on return)" >> "$LOG"
  exit 0
fi

# 2) the home endpoint reachable -> ask it to loop back THROUGH the reverse tunnel.
#    Inner stderr is kept (hfm #9) so a real failure names itself.
INNER='e=$(ssh -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=no -p 2222 root@127.0.0.1 true 2>&1) && echo TUN_OK || echo "TUN_DEAD: $e"'

LOOP=""
UNOBSERVED=0
try=1
while [ "$try" -le "$PROBE_TRIES" ]; do
  bounded 22 ssh -y -y -i "$KEY" "$REMOTE" "$INNER"
  rc=$?
  LOOP="$WD_OUT"
  case "$LOOP" in
    TUN_OK) break ;;
  esac
  # Outer hop timed out or produced nothing: we learned NOTHING about the
  # tunnel. Record it, but never let it count as evidence of death.
  if [ "$rc" = "124" ] || [ -z "$LOOP" ]; then
    UNOBSERVED=$((UNOBSERVED+1))
  fi
  [ "$try" -lt "$PROBE_TRIES" ] && sleep "$PROBE_GAP"
  try=$((try+1))
done

if [ "$LOOP" = "TUN_OK" ]; then
  echo "$TS TUN_OK" > "$HEARTBEAT"    # witness: watchdog ran and the tunnel is healthy
  if [ -f "$STATE" ]; then
    echo "$TS TUN_OK — recovered after $(cat "$STATE") consecutive fail(s)" >> "$LOG"
    rm -f "$STATE"
  fi
  exit 0
fi

# 2b) Every attempt was unobservable -> blind, not dead. HOLD state, do not count,
#     do not bounce. Absence of evidence is not evidence of absence (hfm #2).
if [ "$UNOBSERVED" -ge "$PROBE_TRIES" ]; then
  echo "$TS UNOBSERVED: outer ssh to the home endpoint gave no answer in $PROBE_TRIES tries — tunnel state UNKNOWN, holding (no count, no bounce)" >> "$LOG"
  exit 0
fi

# 3) Reverse tunnel dead while the home endpoint reachable -> count consecutive, bounce at limit.
n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
echo "$TS TUN_DEAD (consecutive #$n after $PROBE_TRIES tries; loop='${LOOP:-<empty/timeout>}')" >> "$LOG"
if [ "$n" -ge "$FAIL_LIMIT" ]; then
  echo "$TS BOUNCE -> /etc/init.d/rtun restart (self-heal, no reboot)" >> "$LOG"
  /etc/init.d/rtun restart
  rm -f "$STATE"
fi
exit 0
