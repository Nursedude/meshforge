#!/usr/bin/env bash
# manager_deadman.sh — pages when the MANAGER's alerting spine goes dark
# (2026-07-03). Runs on a PEER box (moc1); companion to manager_heartbeat.sh
# (see its header for the design story — the manager hosts the entire paging
# spine, so its death is otherwise SILENT).
#
# Checks the LOCAL heartbeat file's mtime (this box's clock — no cross-box
# clock trust, honest_failure_modes #6). The manager beats every 10 min;
# staleness beyond STALE_S (default 25 min = 2+ missed beats, persistence
# built into the age itself) or an ABSENT file (unobservable ≠ healthy) is a
# failure. On failure: page ntfy DIRECTLY via fleet_ntfy_push.sh — this
# cannot ride mini/watchdog, because the scenario it fires in has killed the
# manager-side pager. Re-pages every REPAGE_S while still dark (a long outage
# must not be one missable notification); sends a recovery page on clear.
#
# Crontab (peer box):
#   */10 * * * * /opt/meshforge/scripts/manager_deadman.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh manager_deadman $?
#
# The deadman itself is watched by the PEER's #78 machinery (cron_verdict
# freshness + mini) — who-watches-the-watcher closed on a second box.
#
# Exit 0 = manager fresh. Exit 1 = manager dark (page sent / re-page window).
set -uo pipefail

# Manager display name: env override (set it in the crontab line) else the
# box-local config file; generic fallback keeps this repo-portable (MF014).
MANAGER_NAME="${MANAGER_DEADMAN_NAME:-$(cat "$HOME/.config/meshforge/manager_name" 2>/dev/null)}"
MANAGER_NAME="${MANAGER_NAME:-the fleet manager}"
BEAT_FILE="${MANAGER_DEADMAN_FILE:-$HOME/.manager_heartbeat}"
STALE_S="${MANAGER_DEADMAN_STALE_S:-1500}"     # 25 min = 2+ missed 10-min beats
REPAGE_S="${MANAGER_DEADMAN_REPAGE_S:-21600}"  # re-page every 6h while dark
STATE="${MANAGER_DEADMAN_STATE:-$HOME/.local/state/meshforge/manager_deadman_state.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUSH="$SCRIPT_DIR/fleet_ntfy_push.sh"

mkdir -p "$(dirname "$STATE")"
now="$(date +%s)"

# --- read prior state (absent/garbage -> not-paged) ---
paged=0; last_page=0
if [ -f "$STATE" ]; then
    read -r paged last_page < <(python3 - "$STATE" <<'PY' 2>/dev/null || echo "0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(1 if d.get("paged") else 0, int(d.get("last_page_ts", 0)))
except Exception:
    print("0 0")
PY
) || { paged=0; last_page=0; }
fi

write_state() {
    python3 - "$STATE" "$1" "$2" <<'PY' 2>/dev/null || true
import json, os, sys, tempfile
path, paged, ts = sys.argv[1], sys.argv[2] == "1", int(sys.argv[3])
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".deadman.")
with os.fdopen(fd, "w") as fh:
    json.dump({"paged": paged, "last_page_ts": ts}, fh)
os.replace(tmp, path)
PY
}

# --- judge freshness from LOCAL mtime ---
if [ -f "$BEAT_FILE" ]; then
    mtime="$(stat -c %Y "$BEAT_FILE" 2>/dev/null || echo 0)"
    age=$(( now - mtime ))
else
    age=-1   # absent — never seen a beat (or file removed): dark, not healthy
fi

if [ "$age" -ge 0 ] && [ "$age" -le "$STALE_S" ]; then
    # Manager fresh. Recovery page if we had paged.
    if [ "$paged" -eq 1 ]; then
        "$PUSH" "MANAGER RECOVERED: $MANAGER_NAME spine is beating again" \
            "default" "green_circle,fleet" \
            "$MANAGER_NAME's alerting-spine heartbeat resumed (age ${age}s). Paging coverage is restored." || true
    fi
    write_state 0 "$last_page"
    exit 0
fi

# Manager dark. Page (first time, or re-page window elapsed).
if [ "$paged" -eq 0 ] || [ $(( now - last_page )) -ge "$REPAGE_S" ]; then
    if [ "$age" -ge 0 ]; then
        detail="last heartbeat $(( age / 60 )) min ago"
    else
        detail="NO heartbeat file has ever landed on this peer"
    fi
    "$PUSH" "MANAGER DARK: $MANAGER_NAME alerting spine is down" \
        "urgent" "rotating_light,fleet" \
        "$MANAGER_NAME's spine heartbeat is stale ($detail; threshold $(( STALE_S / 60 )) min). ALL fleet paging (offline monitor, ntfy loopback/ack, ma_health, mini federator) runs on that box and is presumed DOWN with it. Check power/network/crond on $MANAGER_NAME. This page comes from the peer deadman ($(hostname)) and re-pages every $(( REPAGE_S / 3600 ))h until the beat resumes." || true
    write_state 1 "$now"
else
    write_state 1 "$last_page"
fi
exit 1
