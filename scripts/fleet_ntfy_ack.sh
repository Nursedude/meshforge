#!/usr/bin/env bash
# Fleet ntfy ack monitor — Phase 3 of the ntfy receipt-heartbeat arc
# (.claude/plans/ntfy_receipt_heartbeat_2026_06_17.md). The ONLY rung that
# confirms the operator's DEVICE actually receives fleet pages.
#
# Sends a WEEKLY tap-to-ack page to the fleet topic carrying an ntfy "http"
# action button; tapping it makes the PHONE POST to a dedicated ack-topic
# (<fleet>-ack). This script (run hourly) polls that ack-topic, tracks how many
# consecutive weekly pings went un-acked, escalates via the Phase-1 EMAIL
# backbone at >=2 unacked weeks (the channel to the device is unconfirmed), and
# writes a verdict file the READ-ONLY watchdog probe (probe_ntfy_ack_stale)
# surfaces into mini's brief + /fleet. Catches the exact 2026-06-14->17 incident
# (phone on a wrong/dead topic, app killed, notifications off) — what the Phase-2
# loopback (a different subscriber) structurally cannot.
#
# Run HOURLY on the MANAGER box; the cron_verdict tail lets cron_verdict_stale
# (#78) watch this monitor itself:
#   17 * * * * /opt/meshforge/scripts/fleet_ntfy_ack.sh; /opt/meshforge/scripts/cron_verdict.sh ntfy_ack $?
#
# Topic: ~/.config/fleet_push_topic (SSOT). Ack-topic = "<topic>-ack" (derived,
#        no new config). No topic -> no-op exit 0.
# The weekly page is DEFAULT priority (it MUST notify — it's the one you tap).
# All ntfy HTTP headers are kept ASCII-only (header encoding safety).
#
# Exit 0 = the monitor ran fine (poll + maybe ping). The ack STATE (unacked) is
# surfaced by the probe + the email, NOT by this exit code (which reports only
# monitor health to cron_verdict — the script-runs vs ack-state separation).
set -uo pipefail

STATE="${MESHFORGE_ACK_STATE:-$HOME/ntfy_ack_state.json}"
PING_INTERVAL_S="${MESHFORGE_ACK_PING_INTERVAL_S:-604800}"   # 7 days
ESCALATE_AFTER=2          # email after this many consecutive unacked weeks
POLL_SINCE="3h"           # how far back to poll for a tap-to-ack. Go-duration
                          # (ntfy 'since' rejects a 'd' unit); covers the hourly
                          # cron cadence with margin. ntfy.sh caches only ~12h
                          # anyway, but last_ack_ts persists once a tap is seen.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
[ -n "$TOPIC" ] || exit 0   # no topic — this box doesn't ntfy-page; nothing to do
ACK_TOPIC="${TOPIC}-ack"
TOKEN="${MESHFORGE_NTFY_TOKEN:-$(cat "$HOME/.config/fleet_push_token" 2>/dev/null)}"

if ! command -v curl >/dev/null 2>&1; then
    echo "fleet_ntfy_ack: curl not found — cannot run" >&2
    exit 1
fi

now_ts="$(date +%s)"

# 1. Load prior state (best-effort; absent/garbage -> zeros).
state_line="$(python3 - "$STATE" <<'PY' 2>/dev/null || echo "0 0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(int(d.get("last_ping_ts", 0)), int(d.get("last_ack_ts", 0)),
          int(d.get("consecutive_unacked_pings", 0)))
except Exception:
    print("0 0 0")
PY
)"
read -r last_ping_ts last_ack_ts unacked <<< "$state_line"
[ -n "${last_ping_ts:-}" ] || last_ping_ts=0
[ -n "${last_ack_ts:-}" ]  || last_ack_ts=0
[ -n "${unacked:-}" ]      || unacked=0

# 2. Poll the ack-topic for the newest tap-to-ack (phone POSTs body "ack" here).
poll_out="$(mktemp "${TMPDIR:-/tmp}/.ntfy_ack_poll.XXXXXX")"
poll_args=( -s --max-time 12 )
[ -n "$TOKEN" ] && poll_args+=( -H "Authorization: Bearer $TOKEN" )
poll_args+=( "https://ntfy.sh/$ACK_TOPIC/json?poll=1&since=$POLL_SINCE" )
curl "${poll_args[@]}" >"$poll_out" 2>/dev/null || true
newest_ack="$(python3 - "$poll_out" <<'PY' 2>/dev/null || echo 0
import json, sys
best = 0
try:
    for line in open(sys.argv[1]):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("event") != "message":
            continue
        if "ack" not in str(o.get("message", "")).lower():
            continue
        try:
            t = int(o.get("time") or 0)
        except Exception:
            t = 0
        if t > best:
            best = t
except Exception:
    pass
print(best)
PY
)"
rm -f "$poll_out"
[ -n "$newest_ack" ] || newest_ack=0
if [ "$newest_ack" -gt "$last_ack_ts" ]; then
    last_ack_ts="$newest_ack"
fi

# 3. Decide whether to send this week's ping.
if [ "$(( now_ts - last_ping_ts ))" -ge "$PING_INTERVAL_S" ]; then
    # Was the PREVIOUS ping acked? (first-ever ping: last_ping_ts=0 -> no penalty)
    if [ "$last_ping_ts" -gt 0 ] && [ "$last_ack_ts" -ge "$last_ping_ts" ]; then
        unacked=0
    elif [ "$last_ping_ts" -gt 0 ]; then
        unacked="$(( unacked + 1 ))"
    fi
    pub_args=( -s --max-time 12
        -H "Title: Fleet alert check - tap to confirm"
        -H "Priority: default"
        -H "Tags: white_check_mark,fleet"
        -H "Actions: http, Got it, https://ntfy.sh/$ACK_TOPIC, method=POST, body=ack, clear=true" )
    [ -n "$TOKEN" ] && pub_args+=( -H "Authorization: Bearer $TOKEN" )
    pub_args+=( --data-raw "Weekly fleet-alert receipt check. If you can see this, tap 'Got it' to confirm your phone receives fleet pages. No tap for ~2 weeks -> the fleet emails you (the alert channel to your device may be dark)." "https://ntfy.sh/$TOPIC" )
    # Only advance last_ping_ts on a successful publish (a failed publish retries
    # next hour; the publish-path failure itself is Phase-2 loopback's job).
    if curl "${pub_args[@]}" >/dev/null 2>&1; then
        last_ping_ts="$now_ts"
    fi
else
    # Mid-week: a late ack for the CURRENT ping clears the counter.
    if [ "$last_ping_ts" -gt 0 ] && [ "$last_ack_ts" -ge "$last_ping_ts" ]; then
        unacked=0
    fi
fi

# 4. Write state atomically (the read-only watchdog probe reads it).
python3 - "$STATE" "$last_ping_ts" "$last_ack_ts" "$unacked" "$now_ts" "$PING_INTERVAL_S" <<'PY' 2>/dev/null || true
import json, os, sys, tempfile
path, lp, la, un, nowts, interval = sys.argv[1:7]
doc = {"last_ping_ts": int(lp), "last_ack_ts": int(la),
       "consecutive_unacked_pings": int(un), "last_poll_ts": int(nowts),
       "ping_interval_s": int(interval)}
d = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".ntfy_ack.")
with os.fdopen(fd, "w") as fh:
    json.dump(doc, fh)
os.replace(tmp, path)
PY

# 5. Escalate via the EMAIL backbone if the device is unconfirmed >=2 weeks.
if [ "$unacked" -ge "$ESCALATE_AFTER" ]; then
    "$SCRIPT_DIR/fleet_alert_email.sh" \
        "fleet ack UNCONFIRMED ($unacked weeks)" "high" "ack,ntfy" \
        "The weekly fleet-alert tap-to-ack has gone unanswered for $unacked consecutive weeks. Your phone may not be receiving fleet pages (wrong/dead ntfy topic, app killed, or notifications off) — the exact 2026-06-14->17 failure. This email is the Phase-1 backbone reaching you another way. Re-check your ntfy subscription and tap 'Got it' on the next weekly page. (the watchdog probe_ntfy_ack_stale also surfaces this on /fleet.)" \
        || echo "fleet_ntfy_ack: email escalation FAILED (curl exit $?)" >&2
fi

exit 0
