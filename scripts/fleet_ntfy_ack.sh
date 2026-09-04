#!/usr/bin/env bash
# Fleet ntfy ack monitor — Phase 3 of the ntfy receipt-heartbeat arc
# (.claude/plans/ntfy_receipt_heartbeat_2026_06_17.md). The ONLY rung that
# confirms the operator's DEVICE actually receives fleet pages.
#
# Sends a WEEKLY tap-to-ack page to the fleet topic carrying an ntfy "http"
# action button (method=POST). Tapping it makes the PHONE publish a small
# "Receipt confirmed" message (body "ack…") back onto the SAME fleet topic.
#
# WHY the same topic (2026-09-03): the ack used to go to a dedicated side
# topic (<fleet>-ack) that only this poller read. The tap therefore had NO
# visible effect on the device — the notification stayed put, nothing
# answered — and one minute later the fleet paged "ack UNCONFIRMED" (the
# probe judging LAST week's page at the very moment the new one arrived).
# The operator tapped four times in four seconds and reported the button
# broken, while the server held all four acks. A record nobody can see is
# not a receipt (MF018: the truth must be told IN the app). Publishing the
# ack onto the fleet topic makes the phone SEE its own tap land as a
# "✅ Receipt confirmed" notification within a second — the ntfy mechanism
# itself is the feedback, no cron cadence in the loop. The legacy side topic
# is still polled so a page delivered before this change still acks.
#
# History: "http" -> "view" on 2026-07-23 (http actions were thought
# Android-only), then "view" -> "http, method=POST" on 2026-07-26 (review
# D11): the view action's bare GET publish URL meant any link-prefetcher /
# notification-preview fetcher could record an ack WITHOUT a human tap — a
# forged "your phone receives pages" confirmation. A POST-only ack cannot be
# minted by a prefetcher; a client that doesn't render http actions simply
# shows no button, which reads as un-acked and escalates via email — the
# honest failure direction.
#
# This script (run hourly) polls for the newest ack, tracks how many
# consecutive weekly pings went un-acked, escalates via the Phase-1 EMAIL
# backbone at >=2 unacked weeks (the channel to the device is unconfirmed),
# and writes a verdict file the READ-ONLY watchdog probe
# (probe_ntfy_ack_stale) surfaces into mini's brief + /fleet. Catches the
# exact 2026-06-14->17 incident (phone on a wrong/dead topic, app killed,
# notifications off) — what the Phase-2 loopback (a different subscriber)
# structurally cannot.
#
# Run HOURLY on the MANAGER box; the cron_verdict tail lets cron_verdict_stale
# (#78) watch this monitor itself:
#   17 * * * * /opt/meshforge/scripts/fleet_ntfy_ack.sh; /opt/meshforge/scripts/cron_verdict.sh ntfy_ack $?
#
# Topic: ~/.config/fleet_push_topic (SSOT). Legacy ack-topic = "<topic>-ack".
#        No topic -> no-op exit 0.
# The weekly page is DEFAULT priority (it MUST notify — it's the one you tap).
# The ack the tap publishes is LOW priority (visible, no second buzz).
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

# The ack message the tap publishes. The poller matches the TITLE exactly and
# the body's leading word — never a loose substring on the fleet topic, where
# "backoff" / "stack" / "track" in a real page would forge a receipt. ONE
# constant, used by both the action header and the poll filter (hfm #5).
ACK_TITLE="Receipt confirmed"
ACK_BODY="ack: your tap registered. The fleet monitor records it within the hour."
BUTTON_LABEL="Confirm receipt"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
[ -n "$TOPIC" ] || exit 0   # no topic — this box doesn't ntfy-page; nothing to do
LEGACY_ACK_TOPIC="${TOPIC}-ack"
TOKEN="${MESHFORGE_NTFY_TOKEN:-$(cat "$HOME/.config/fleet_push_token" 2>/dev/null)}"

if ! command -v curl >/dev/null 2>&1; then
    echo "fleet_ntfy_ack: curl not found — cannot run" >&2
    exit 1
fi

now_ts="$(date +%s)"

# 1. Load prior state (best-effort; absent/garbage -> zeros).
state_line="$(python3 - "$STATE" <<'PY' 2>/dev/null || echo "0 0 0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(int(d.get("last_ping_ts", 0)), int(d.get("last_ack_ts", 0)),
          int(d.get("consecutive_unacked_pings", 0)),
          int(d.get("unacked_ping_ts", 0) or 0))
except Exception:
    print("0 0 0 0")
PY
)"
read -r last_ping_ts last_ack_ts unacked unacked_ping_ts <<< "$state_line"
[ -n "${last_ping_ts:-}" ]   || last_ping_ts=0
[ -n "${last_ack_ts:-}" ]    || last_ack_ts=0
[ -n "${unacked:-}" ]        || unacked=0
[ -n "${unacked_ping_ts:-}" ] || unacked_ping_ts=0

# 2. Poll for the newest tap-to-ack.
#    poll_acks <topic> <mode>  -> "<newest_ts> <count>"
#      mode=strict : fleet topic — title == ACK_TITLE AND body starts "ack"
#      mode=legacy : the old side topic — any body containing "ack" (the
#                    pre-09-03 filter; nothing else was ever published there)
poll_acks() {
    local topic="$1" mode="$2" out
    out="$(mktemp "${TMPDIR:-/tmp}/.ntfy_ack_poll.XXXXXX")"
    local args=( -s --max-time 12 )
    [ -n "$TOKEN" ] && args+=( -H "Authorization: Bearer $TOKEN" )
    args+=( "https://ntfy.sh/$topic/json?poll=1&since=$POLL_SINCE" )
    curl "${args[@]}" >"$out" 2>/dev/null || true
    python3 - "$out" "$mode" "$ACK_TITLE" <<'PY' 2>/dev/null || echo "0 0"
import json, sys
path, mode, ack_title = sys.argv[1:4]
best, count = 0, 0
try:
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("event") != "message":
            continue
        msg = str(o.get("message", ""))
        if mode == "strict":
            if str(o.get("title", "")) != ack_title:
                continue
            if not msg.strip().lower().startswith("ack"):
                continue
        else:
            if "ack" not in msg.lower():
                continue
        try:
            t = int(o.get("time") or 0)
        except Exception:
            t = 0
        count += 1
        if t > best:
            best = t
except Exception:
    pass
print(best, count)
PY
    rm -f "$out"
}

read -r newest_ack acks_seen <<< "$(poll_acks "$TOPIC" strict)"
read -r legacy_ack legacy_seen <<< "$(poll_acks "$LEGACY_ACK_TOPIC" legacy)"
[ -n "${newest_ack:-}" ] || newest_ack=0
[ -n "${legacy_ack:-}" ] || legacy_ack=0
[ -n "${acks_seen:-}" ]  || acks_seen=0
[ -n "${legacy_seen:-}" ] || legacy_seen=0
ack_source=""
if [ "$legacy_ack" -gt "$newest_ack" ]; then
    newest_ack="$legacy_ack"; ack_source="legacy-ack-topic"
elif [ "$newest_ack" -gt 0 ]; then
    ack_source="fleet-topic"
fi
if [ "$newest_ack" -gt "$last_ack_ts" ]; then
    last_ack_ts="$newest_ack"
fi

# 3. Decide whether to send this week's ping.
if [ "$(( now_ts - last_ping_ts ))" -ge "$PING_INTERVAL_S" ]; then
    # Was the PREVIOUS ping acked? (first-ever ping: last_ping_ts=0 -> no penalty)
    if [ "$last_ping_ts" -gt 0 ] && [ "$last_ack_ts" -ge "$last_ping_ts" ]; then
        unacked=0; unacked_ping_ts=0
    elif [ "$last_ping_ts" -gt 0 ]; then
        unacked="$(( unacked + 1 ))"
        unacked_ping_ts="$last_ping_ts"     # the page that went un-acked (probe names it)
    fi
    # "http" action with method=POST (07-26 review D11): only a deliberate tap
    # publishes the ack. The tap publishes onto THIS topic (09-03) so the phone
    # sees "Receipt confirmed" land at once — the tap is no longer invisible.
    # headers.* ride the action; no comma may appear in any value (ntfy's
    # simple-format delimiter).
    pub_args=( -s --max-time 12
        -H "Title: Fleet alert check - tap to confirm"
        -H "Priority: default"
        -H "Tags: white_check_mark,fleet"
        -H "Actions: http, $BUTTON_LABEL, https://ntfy.sh/$TOPIC, method=POST, body=$ACK_BODY, headers.Title=$ACK_TITLE, headers.Priority=low, headers.Tags=white_check_mark, clear=true" )
    [ -n "$TOKEN" ] && pub_args+=( -H "Authorization: Bearer $TOKEN" )
    pub_args+=( --data-raw "Weekly fleet-alert receipt check. If you can see this, tap '$BUTTON_LABEL' once: a '$ACK_TITLE' notice appears on this topic within a second (that IS the ack). No tap for ~2 weeks -> the fleet emails you (the alert channel to your device may be dark)." "https://ntfy.sh/$TOPIC" )
    # Only advance last_ping_ts on a successful publish (a failed publish retries
    # next hour; the publish-path failure itself is Phase-2 loopback's job).
    if curl "${pub_args[@]}" >/dev/null 2>&1; then
        last_ping_ts="$now_ts"
    fi
else
    # Mid-week: a late ack for the CURRENT ping clears the counter.
    if [ "$last_ping_ts" -gt 0 ] && [ "$last_ack_ts" -ge "$last_ping_ts" ]; then
        unacked=0; unacked_ping_ts=0
    fi
fi

# 4. Write state atomically (the read-only watchdog probe reads it).
python3 - "$STATE" "$last_ping_ts" "$last_ack_ts" "$unacked" "$now_ts" "$PING_INTERVAL_S" \
          "$unacked_ping_ts" "$acks_seen" "$legacy_seen" "$ack_source" <<'PY' 2>/dev/null || true
import json, os, sys, tempfile
path, lp, la, un, nowts, interval, upt, seen, lseen, src = sys.argv[1:11]
doc = {"last_ping_ts": int(lp), "last_ack_ts": int(la),
       "consecutive_unacked_pings": int(un), "last_poll_ts": int(nowts),
       "ping_interval_s": int(interval),
       # the page that went un-acked (0 = none) — the probe names it, so
       # "UNCONFIRMED" can say WHICH page instead of reading as "your tap failed"
       "unacked_ping_ts": int(upt),
       # witnesses for the last poll: how many acks each leg saw, which won
       "acks_seen_last_poll": int(seen), "legacy_acks_seen_last_poll": int(lseen),
       "last_ack_source": src or None}
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
        "The weekly fleet-alert tap-to-ack has gone unanswered for $unacked consecutive weeks. Your phone may not be receiving fleet pages (wrong/dead ntfy topic, app killed, or notifications off) — the exact 2026-06-14->17 failure. This email is the Phase-1 backbone reaching you another way. Re-check your ntfy subscription and tap '$BUTTON_LABEL' on the next weekly page (a '$ACK_TITLE' notice appears within a second when it lands). (the watchdog probe_ntfy_ack_stale also surfaces this on /fleet.)" \
        || echo "fleet_ntfy_ack: email escalation FAILED (curl exit $?)" >&2
fi

exit 0
