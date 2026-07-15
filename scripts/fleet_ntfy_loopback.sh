#!/usr/bin/env bash
# Fleet ntfy loopback monitor — Phase 2 of the ntfy receipt-heartbeat arc
# (.claude/plans/ntfy_receipt_heartbeat_2026_06_17.md). Publishes a nonce'd,
# MIN-priority heartbeat to the FLEET topic, then polls ntfy.sh's poll API to
# confirm the nonce LOOPS BACK within a window. That proves the alerting
# channel's server + the fleet topic's publish path are alive — the "send ≠
# receipt" lesson aimed at the spine itself (the 2026-06-14→17 dark incident,
# where every publish returned HTTP 200 while the phone got nothing).
#
# On a sustained MISS it escalates via the Phase-1 EMAIL backbone
# (fleet_alert_email.sh) — ntfy is the suspect channel, so we do NOT page back
# through it — and writes a verdict file the READ-ONLY watchdog probe
# (probe_ntfy_loopback) surfaces into mini's brief + /fleet.
#
# Catches: ntfy.sh down, the fleet topic's publish path broken, the sender-no-op
# class. Does NOT catch the operator's PHONE being on a wrong topic — that is
# Phase 3's tap-to-ack job (a different subscriber).
#
# Run by cron on the MANAGER box (where the fleet topic SSOT lives), e.g.:
#   */30 * * * * /opt/meshforge/scripts/fleet_ntfy_loopback.sh; \
#       /opt/meshforge/scripts/cron_verdict.sh ntfy_loopback $?
# The cron_verdict wiring means cron_verdict_stale (#78) watches THIS monitor —
# who watches the watcher.
#
# Topic: ~/.config/fleet_push_topic (SSOT, same as fleet_ntfy_push.sh). No topic
#        -> no-op exit 0 (this box doesn't page via ntfy, so nothing to test).
# MIN-priority: the heartbeat is ntfy priority 1 (min) -> silent on the phone
#        (no buzz/banner; visible in the app feed + to the poll API). Set your
#        ntfy subscription's minimum priority to ignore it if the feed clutters.
#
# Exit: 0 = looped back OK (or intentional no-op). 1 = MISS (published-but-lost
#       or publish failed). The cron idiom above feeds that into cron_verdict.
set -uo pipefail

STATE="${MESHFORGE_LOOPBACK_STATE:-$HOME/ntfy_loopback_state.json}"
INTERVAL_S="${MESHFORGE_LOOPBACK_INTERVAL_S:-1800}"   # intended cadence (recorded for context)
SETTLE_S=3                # wait after publish before the first poll
MAX_WAIT_S=20             # total time to keep polling for the nonce
POLL_SINCE="45s"          # how far back the poll API looks
ESCALATE_AFTER_MISSES=2   # email only after this many consecutive misses (ride a transient)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
[ -n "$TOPIC" ] || exit 0   # no topic — this box doesn't ntfy-page; nothing to test
TOKEN="${MESHFORGE_NTFY_TOKEN:-$(cat "$HOME/.config/fleet_push_token" 2>/dev/null)}"

if ! command -v curl >/dev/null 2>&1; then
    echo "fleet_ntfy_loopback: curl not found — cannot run loopback" >&2
    exit 1
fi

# Prior consecutive_misses (best-effort; absent/garbage -> 0).
prev_misses="$(python3 - "$STATE" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    print(int(json.load(open(sys.argv[1])).get("consecutive_misses", 0)))
except Exception:
    print(0)
PY
)"
[ -n "$prev_misses" ] || prev_misses=0

# Publish + poll ONE nonce'd heartbeat cycle. Sets published_ok / received /
# latency_s / nonce / now_ts for the caller. Extracted into a function so a
# transient first-cycle miss can be retried once before it becomes a FAIL that
# pages (see the retry below).
attempt_loopback() {
    now_ts="$(date +%s)"
    nonce="lb-$(hostname 2>/dev/null || echo box)-${now_ts}-${RANDOM}"

    # 1. Publish a MIN-priority heartbeat carrying the nonce.
    pub_args=( -s --max-time 12 -H "Title: fleet loopback heartbeat"
               -H "Priority: min" -H "Tags: loopback,heartbeat" )
    [ -n "$TOKEN" ] && pub_args+=( -H "Authorization: Bearer $TOKEN" )
    pub_args+=( --data-raw "$nonce" "https://ntfy.sh/$TOPIC" )
    published_ok=true
    curl "${pub_args[@]}" >/dev/null 2>&1 || published_ok=false

    # 2. Poll the topic back for the nonce, up to MAX_WAIT_S.
    received=false
    latency_s=0
    if [ "$published_ok" = true ]; then
        sleep "$SETTLE_S"
        waited="$SETTLE_S"
        while [ "$waited" -le "$MAX_WAIT_S" ]; do
            poll_args=( -s --max-time 12 )
            [ -n "$TOKEN" ] && poll_args+=( -H "Authorization: Bearer $TOKEN" )
            poll_args+=( "https://ntfy.sh/$TOPIC/json?poll=1&since=$POLL_SINCE" )
            if curl "${poll_args[@]}" 2>/dev/null | grep -qF "$nonce"; then
                received=true
                latency_s="$(( $(date +%s) - now_ts ))"
                break
            fi
            sleep 3
            waited="$(( waited + 3 ))"
        done
    fi
}

# One probe cycle, then RETRY ONCE on a miss. The dominant failure on the ntfy.sh
# free tier is a single transient poll miss — publish returns 200 but the nonce
# doesn't propagate to the poll API inside the window — which produced FAIL(1)
# blips that paged via cron_verdict/#78 while the channel was healthy. A fresh
# publish+poll absorbs that transient (and, re-publishing, a lost publish too). A
# REAL outage misses BOTH cycles and still escalates on the consecutive-miss
# accounting below — nothing about the sustained-failure path changes.
attempt_loopback
if [ "$received" != true ]; then
    echo "fleet_ntfy_loopback: first cycle missed (published_ok=$published_ok) — retrying once" >&2
    attempt_loopback
fi

# 3. Consecutive-miss accounting.
if [ "$received" = true ]; then
    misses=0
else
    misses="$(( prev_misses + 1 ))"
fi

# 4. Write the verdict file atomically (the read-only watchdog probe reads it).
#    NOTE: the topic is deliberately NOT written here — it must never land in a
#    probe/api/_fleet surface (MF015).
python3 - "$STATE" "$now_ts" "$nonce" "$published_ok" "$received" "$latency_s" "$misses" "$INTERVAL_S" <<'PY' 2>/dev/null || true
import json, os, sys, tempfile
path, ts, nonce, pub, recv, lat, misses, interval = sys.argv[1:9]
doc = {
    "ts": int(ts),
    "nonce": nonce,
    "published_ok": (pub == "true"),
    "received": (recv == "true"),
    "latency_s": int(lat),
    "consecutive_misses": int(misses),
    "interval_s": int(interval),
}
try:
    prev = json.load(open(path))
    doc["last_received_ts"] = int(ts) if recv == "true" else prev.get("last_received_ts")
except Exception:
    doc["last_received_ts"] = int(ts) if recv == "true" else None
d = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".ntfy_loopback.")
with os.fdopen(fd, "w") as fh:
    json.dump(doc, fh)
os.replace(tmp, path)
PY

# 5. On a SUSTAINED miss, escalate via the EMAIL backbone (NOT ntfy — it's the
#    suspect). Only after ESCALATE_AFTER_MISSES consecutive misses, so a single
#    transient ntfy hiccup doesn't page.
if [ "$received" != true ]; then
    if [ "$misses" -ge "$ESCALATE_AFTER_MISSES" ]; then
        if [ "$published_ok" = true ]; then
            why="published a heartbeat but it did NOT loop back via ntfy.sh — the server or the fleet topic's delivery is broken; ntfy pages may be silently lost."
        else
            why="could NOT publish to the fleet ntfy topic from $(hostname 2>/dev/null) — ntfy pages from here will not send."
        fi
        "$SCRIPT_DIR/fleet_alert_email.sh" \
            "ntfy loopback FAILED ($misses consecutive)" "high" "loopback,ntfy" \
            "Fleet ntfy alerting channel loopback FAILED: $why This is the Phase-1 email backbone escalating because ntfy itself is the suspect channel. Check ntfy.sh status + the fleet topic. (the watchdog probe_ntfy_loopback also surfaces this on /fleet.)" \
            || echo "fleet_ntfy_loopback: email escalation ALSO failed (curl exit $?) — both channels dark" >&2
    fi
    echo "fleet_ntfy_loopback: MISS (published_ok=$published_ok received=$received misses=$misses)" >&2
    exit 1
fi
exit 0
