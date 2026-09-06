#!/usr/bin/env bash
# meshforge cloud :8808 — snapshot + push
#
# Generates a public-facing GeoJSON snapshot from the local
# meshforge-map service and rsyncs it to the cloud VPS.
#
# Triggered every 60s by templates/cloud/meshforge-cloud-push.timer.
# Idempotent — safe to fire concurrently (rsync is atomic via --temp-dir).
#
# Configuration via /etc/default/meshforge-cloud-push or env vars:
#
#   CLOUD_HOST         hostname or IP of the cloud VPS (required)
#   CLOUD_USER         SSH user (default: meshforge)
#   CLOUD_WEBROOT      path on VPS where files land (default: /var/www/meshforge)
#   CLOUD_SSH_KEY      path to SSH private key (default: ~/.ssh/id_ed25519)
#   LOCAL_MAP_URL      URL of the local meshforge-map (default: http://localhost:5000)
#   REGION             region preset to pull (default: hawaii)
#   CACHE_DIR          local working dir (default: /var/lib/meshforge/cloud)
#
# Exit codes:
#   0  pushed successfully
#   1  retryable error (network blip, VPS unreachable) — timer retries; the
#      unit declares SuccessExitStatus=0 1 so ONE of these stays quiet
#   2  configuration error — investigate, don't auto-retry
#   3  persistent: N consecutive runs pushed nothing, so the map is going
#      stale. Deliberately OUTSIDE SuccessExitStatus so the unit FAILS and
#      every existing detector (systemd state, boot_survival) can see it.

set -uo pipefail

CLOUD_USER="${CLOUD_USER:-meshforge}"
CLOUD_WEBROOT="${CLOUD_WEBROOT:-/var/www/meshforge}"
CLOUD_SSH_KEY="${CLOUD_SSH_KEY:-$HOME/.ssh/id_ed25519}"
LOCAL_MAP_URL="${LOCAL_MAP_URL:-http://localhost:5000}"
REGION="${REGION:-hawaii}"
CACHE_DIR="${CACHE_DIR:-/var/lib/meshforge/cloud}"

# log to journald via stdout (the systemd unit captures it)
log() { printf "%(%Y-%m-%dT%H:%M:%S%z)T  %s\n" -1 "$*"; }
err() { log "ERROR: $*" >&2; }

#: Consecutive pushed-nothing runs before this stops being "transient".
#: 3 x the 600 s timer = the map is ~30 min stale, still inside
#: cloud_map_freshness's 3600 s bound, so this speaks FIRST.
PUSH_SKIP_ESCALATE="${PUSH_SKIP_ESCALATE:-3}"

if [[ -z "${CLOUD_HOST:-}" ]]; then
    err "CLOUD_HOST not set; configure /etc/default/meshforge-cloud-push"
    exit 2
fi
if [[ ! -f "$CLOUD_SSH_KEY" ]]; then
    err "SSH key not found at $CLOUD_SSH_KEY"
    exit 2
fi

mkdir -p "$CACHE_DIR"

# --- consecutive-skip escalation -----------------------------------------
# ONE skipped push is a transient the timer retries, and the unit's
# SuccessExitStatus=0 1 keeps it quiet. 2026-09-06 showed the other half of
# that bargain was missing: FIVE consecutive skips, five unit "successes",
# ZERO rsync attempts, and the map sat 39 minutes stale while systemd,
# NRestarts and boot_survival all read healthy. Transient-quiet is only
# honest if persistent-loud exists.
#
# An EXIT trap rather than edits at each `exit 1`: there are six today, and a
# seventh added later must not silently escape the counter (the same
# reader/writer-drift class this file already carries scars from).
SKIP_STREAK_FILE="$CACHE_DIR/push_skip_streak"

_on_exit() {
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        rm -f "$SKIP_STREAK_FILE"          # a real push clears the streak
        exit 0
    fi
    [ "$rc" -ne 1 ] && exit "$rc"          # 2 and 3 are already loud

    local n
    n=$(cat "$SKIP_STREAK_FILE" 2>/dev/null || echo 0)
    case "$n" in ''|*[!0-9]*) n=0;; esac
    n=$((n + 1))

    if ! printf '%s\n' "$n" > "$SKIP_STREAK_FILE" 2>/dev/null; then
        # Cannot count means cannot honestly claim "only one". A streak file
        # that silently fails to save is how a debounce freezes one below its
        # threshold and never fires again (2026-09-02). Fail loud instead.
        err "cannot persist skip streak at $SKIP_STREAK_FILE — escalation would be BLIND"
        exit 3
    fi

    if [ "$n" -ge "$PUSH_SKIP_ESCALATE" ]; then
        err "pushed NOTHING on $n consecutive run(s) — the map is going stale; this is no longer transient"
        exit 3
    fi
    log "pushed nothing ($n/$PUSH_SKIP_ESCALATE consecutive; quiet until $PUSH_SKIP_ESCALATE)"
    exit 1
}
trap _on_exit EXIT
SNAPSHOT="$CACHE_DIR/data.geojson.tmp"
STAMP="$CACHE_DIR/last_pushed.txt"
META="$CACHE_DIR/meta.json.tmp"
SW="$CACHE_DIR/space_weather.json.tmp"
AL="$CACHE_DIR/alerts.json.tmp"

# 1. Pull GeoJSON from the local meshforge-map. Bound by --max-time so a
#    hung map service doesn't pile up timer firings. 90s budget reflects
#    the worst-case region-filter scan on a large directory (~76k nodes
#    observed in the field 2026-05-11: regional filter ~67s vs <1s
#    unfiltered). Follow-up: cache or precompute regional slices
#    server-side; for now the 90s ceiling matches the timer's 120s
#    cadence with margin.
URL="$LOCAL_MAP_URL/api/nodes/geojson?region=$REGION"
if ! curl -sS --max-time 90 -o "$SNAPSHOT" "$URL"; then
    err "curl failed against $URL"
    exit 1
fi

# 2. Sanity-check the response — must be valid JSON with at least one feature.
if ! python3 -c "import json,sys; d=json.load(open('$SNAPSHOT')); n=len(d.get('features',[])); sys.exit(0 if n>0 else 3)"; then
    err "snapshot empty or unparseable (URL=$URL)"
    rm -f "$SNAPSHOT"
    exit 1
fi
# Tally per-network counts in the same pass so the audience-facing page
# can show a slide-14-style "Meshtastic / AREDN / MeshCore" strip.
read N_FEATURES COUNTS_JSON < <(SNAP="$SNAPSHOT" python3 - <<'PYEOF'
import json, os
from collections import Counter
feats = json.load(open(os.environ['SNAP'])).get('features', [])
c = Counter((f.get('properties') or {}).get('network') for f in feats)
c.pop(None, None)
print(len(feats), json.dumps(dict(c), separators=(',', ':')))
PYEOF
)

# 3. Write a small meta.json with the freshness stamp + counts. The
#    cloud index.html reads this to display "last updated <X>s ago"
#    and the per-network breakdown.
NOW_EPOCH=$(date +%s)
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$META" <<EOF
{
  "generated_at": "$NOW_ISO",
  "generated_at_epoch": $NOW_EPOCH,
  "feature_count": $N_FEATURES,
  "counts_by_network": $COUNTS_JSON,
  "region": "$REGION",
  "source": "meshforge-cloud-push"
}
EOF

# 4. Atomically rename so anyone reading the local cache always sees a
#    consistent file.
mv "$SNAPSHOT" "${SNAPSHOT%.tmp}"
mv "$META" "${META%.tmp}"
SNAPSHOT_FINAL="${SNAPSHOT%.tmp}"
META_FINAL="${META%.tmp}"

# 4b. Space weather snapshot from NOAA SWPC (via on-prem propagation
#     module). Bounded to 15s — NOAA's HTTPS endpoint is normally <2s
#     but the audience-facing page can render without this strip, so a
#     failure here is non-fatal. Reuses `src/commands/propagation.py`,
#     same code path the local TUI uses.
#
#     Local cache: NOAA SFI updates daily, Kp every 3h, X-ray every
#     minute. Re-fetching every 180s wastes 5-15s of every push on
#     payloads that essentially never change between cycles. Cache for
#     600s (10 min) — still fresher than NOAA's slowest indices and
#     undetectable during a 25-min talk. Cache lives under CACHE_DIR
#     so it survives between firings without persisting across reboots.
NOAA_CACHE_TTL="${NOAA_CACHE_TTL:-600}"
SW_CACHE="$CACHE_DIR/space_weather.cache.json"
SW_PUSH=""
if [[ -s "$SW_CACHE" ]] && \
        (( NOW_EPOCH - $(stat -c %Y "$SW_CACHE" 2>/dev/null || echo 0) < NOAA_CACHE_TTL )); then
    cp "$SW_CACHE" "${SW%.tmp}"
    SW_PUSH="${SW%.tmp}"
else
    SW_TMP="$SW" NOW_EPOCH="$NOW_EPOCH" \
    timeout 15 python3 - <<'PYEOF' 2>/dev/null
import os, sys, json
sys.path.insert(0, '/opt/meshforge/src')
try:
    from commands import propagation
    r = propagation.get_space_weather()
    if not (r.success and r.data):
        sys.exit(1)
    payload = dict(r.data)
    payload['generated_at_epoch'] = int(os.environ.get('NOW_EPOCH') or 0)
    with open(os.environ['SW_TMP'], 'w') as f:
        json.dump(payload, f)
except Exception:
    sys.exit(1)
PYEOF
    if [[ -s "$SW" ]]; then
        mv "$SW" "${SW%.tmp}"
        cp "${SW%.tmp}" "$SW_CACHE"
        SW_PUSH="${SW%.tmp}"
    elif [[ -s "$SW_CACHE" ]]; then
        # Fetch failed but we have a stale cache — better than nothing.
        cp "$SW_CACHE" "${SW%.tmp}"
        SW_PUSH="${SW%.tmp}"
        log "space weather fetch failed; using stale cache from $(date -d @$(stat -c %Y "$SW_CACHE") -u +%H:%M:%SZ)"
    else
        rm -f "$SW"
        log "space weather fetch failed or empty; pushing without it this cycle"
    fi
fi

# 4c. NOAA active alerts (last 72h). Same non-fatal pattern as 4b — the
#     banner only renders when there's something to say. NOAA alert
#     cadence is bursty (days-quiet then a flare cluster), so a 24h
#     window often shows nothing for the audience; 72h keeps recent
#     space-weather context visible during a multi-day demo.
#
#     Same 600s local cache as space_weather (4b). NOAA alerts are
#     hours-to-days apart; a 10-min stale window is invisible to the
#     audience and saves 5-15s of NOAA HTTPS round-trip per push.
AL_CACHE="$CACHE_DIR/alerts.cache.json"
AL_PUSH=""
if [[ -s "$AL_CACHE" ]] && \
        (( NOW_EPOCH - $(stat -c %Y "$AL_CACHE" 2>/dev/null || echo 0) < NOAA_CACHE_TTL )); then
    cp "$AL_CACHE" "${AL%.tmp}"
    AL_PUSH="${AL%.tmp}"
else
    AL_TMP="$AL" NOW_EPOCH="$NOW_EPOCH" \
    timeout 15 python3 - <<'PYEOF' 2>/dev/null
import os, sys, re, json, time
from datetime import datetime, timedelta
sys.path.insert(0, '/opt/meshforge/src')
try:
    from commands import propagation
    r = propagation.get_alerts()
    if not (r.success and r.data):
        sys.exit(1)
    raw = (r.data or {}).get('alerts') or []
    cutoff = datetime.utcnow() - timedelta(hours=72)
    # NOAA re-issues the same product as a storm evolves: a WARNING
    # becomes "EXTENDED WARNING" (same subject, new issue time) and an
    # ALERT can be "CANCELLED ALERT". Left raw, the banner shows the same
    # subject 2-3x (the K-index dup). Collapse to one row per subject,
    # keeping the most recent issuance; if that latest issuance is a
    # cancellation the subject is no longer active, so drop it. The
    # optional qualifier group also stops "CANCELLED ALERT" from
    # rendering as a bare (still-active-looking) "ALERT".
    type_re = re.compile(
        r'\b(?:(EXTENDED|CONTINUED|CANCELLED|CANCELED)\s+)?'
        r'(ALERT|WARNING|WATCH|SUMMARY)\s*:\s*([^|\n]+)')
    by_subject = {}
    for a in raw:
        msg = (a.get('message') or '').strip()
        ts = (a.get('issue_datetime') or '').strip()
        # Parse the NOAA timestamp (e.g. "2026-05-10 14:14:33.073")
        try:
            issued = datetime.strptime(ts.split('.')[0], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        if issued < cutoff:
            continue
        m = type_re.search(msg)
        if m:
            qualifier = (m.group(1) or '').upper()
            alert_type, title = m.group(2), m.group(3).strip()
        else:
            qualifier, alert_type, title = '', 'ALERT', msg.split('|')[0].strip()[:80]
        # Trim title for banner display
        title = title.strip()[:120]
        key = title.lower()
        prev = by_subject.get(key)
        if prev is None or issued > prev['issued']:
            by_subject[key] = {
                'type': alert_type,
                'title': title,
                'issued': issued,
                'issue_datetime': ts.split('.')[0] + 'Z',
                'cancelled': qualifier in ('CANCELLED', 'CANCELED'),
            }
    # Active subjects only (latest issuance not a cancellation), newest first.
    out = [
        {'type': v['type'], 'title': v['title'], 'issue_datetime': v['issue_datetime']}
        for v in sorted(by_subject.values(), key=lambda v: v['issued'], reverse=True)
        if not v['cancelled']
    ][:3]
    payload = {
        'alerts': out,
        'count': len(out),
        'source': 'NOAA SWPC',
        'generated_at_epoch': int(os.environ.get('NOW_EPOCH') or 0),
    }
    with open(os.environ['AL_TMP'], 'w') as f:
        json.dump(payload, f)
except Exception:
    sys.exit(1)
PYEOF
    if [[ -s "$AL" ]]; then
        mv "$AL" "${AL%.tmp}"
        cp "${AL%.tmp}" "$AL_CACHE"
        AL_PUSH="${AL%.tmp}"
    elif [[ -s "$AL_CACHE" ]]; then
        cp "$AL_CACHE" "${AL%.tmp}"
        AL_PUSH="${AL%.tmp}"
        log "alerts fetch failed; using stale cache from $(date -d @$(stat -c %Y "$AL_CACHE") -u +%H:%M:%SZ)"
    else
        rm -f "$AL"
        log "alerts fetch failed or empty; pushing without it this cycle"
    fi
fi

# 5. Cloud healthcheck — don't bother pushing to a VPS that's down.
# RETRIED on purpose (2026-09-06). A single 5 s probe answers "did this one
# exchange get through", not "is the VPS up" — and on that day's ~50 %-loss
# transit event it answered no five times running and skipped the push every
# time, while the rsync below — hardened the day before to RIDE OUT exactly
# this (60 s stall timeout, --partial-dir resume) — never got to try. A gate
# must not be stricter than the thing it guards.
# Budget: 3 x 10 s + 2 x 3 s = 36 s worst case, well inside RuntimeMaxSec=540s.
HEALTH_TRIES="${HEALTH_TRIES:-3}"
health_ok=0
for _try in $(seq 1 "$HEALTH_TRIES"); do
    if curl -sS --max-time 10 -o /dev/null "https://$CLOUD_HOST/healthz" 2>/dev/null; then
        health_ok=1
        break
    fi
    [ "$_try" -lt "$HEALTH_TRIES" ] && sleep 3
done
if [ "$health_ok" -ne 1 ]; then
    log "cloud healthcheck failed ${HEALTH_TRIES}x (10s each); will retry on next firing"
    exit 1
fi

# 6. Rsync. --temp-dir ensures the destination file swap is atomic from
#    Caddy's perspective. --inplace would race with concurrent reads.
# --timeout is rsync's STALL timeout (no data for N s), not a total budget.
# 2026-09-05: the ISP's transit ran 5-10% loss at ~200 ms RTT to the VPS and
# TCP throughput fell to ~20-40 KB/s; a 4.2 MB snapshot then stalls past 20 s
# routinely and every push failed for 80+ min while the VPS was healthy.
# 60 s rides out a loss burst; --partial-dir keeps what was sent (a plain
# --partial is defeated by --temp-dir: the fragment lands in the temp dir
# where the next run never looks) so the next firing RESUMES instead of
# restarting. The total budget is the unit's RuntimeMaxSec (540 s, below the
# 600 s timer cadence), not this.
RSYNC_OPTS=(
    -az
    --partial-dir="$CLOUD_WEBROOT/.rsync-partial"
    --timeout=60
    --temp-dir="$CLOUD_WEBROOT/.tmp"
    -e "ssh -i $CLOUD_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
)

# 6a. Precompress the snapshot (2026-09-06). The raw geojson is 4.2 MB and
#     gzips to ~250 KB. Caddy's on-the-fly `encode` never touched it (its
#     default MIME match excludes application/geo+json), so every browser
#     fetched the raw 4.2 MB every 30 s, and every push carried the raw
#     file's delta over a residential uplink. The Caddyfile already declares
#     `file_server { precompressed gzip }`: a data.geojson.gz sidecar is
#     served as /data.geojson with Content-Encoding: gzip to any client that
#     accepts it (all browsers), transparently to the page.
#     --rsyncable keeps gzip's blocks aligned so rsync's delta transfer still
#     works on the compressed file (plain gzip changes the whole stream on any
#     byte change). Only the SIDECAR crosses the wire; the raw file the
#     sidecar lookup needs is re-inflated on the VPS in step 7.
GZ_FINAL="$SNAPSHOT_FINAL.gz"
if ! gzip -9 --rsyncable -c "$SNAPSHOT_FINAL" > "$GZ_FINAL.tmp"; then
    err "gzip of snapshot failed"
    rm -f "$GZ_FINAL.tmp"
    exit 1
fi
mv "$GZ_FINAL.tmp" "$GZ_FINAL"

PUSH_FILES=("$GZ_FINAL" "$META_FINAL")
[[ -n "$SW_PUSH" ]] && PUSH_FILES+=("$SW_PUSH")
[[ -n "$AL_PUSH" ]] && PUSH_FILES+=("$AL_PUSH")

# Also keep index.html in lockstep with the repo, so page changes
# (new panels, layout tweaks) auto-deploy on next push without an
# operator round-trip to the VPS. rsync's checksum/mtime check makes
# unchanged files a no-op.
INDEX_HTML="/opt/meshforge/web/cloud/index.html"
[[ -r "$INDEX_HTML" ]] && PUSH_FILES+=("$INDEX_HTML")

if ! rsync "${RSYNC_OPTS[@]}" \
        "${PUSH_FILES[@]}" \
        "$CLOUD_USER@$CLOUD_HOST:$CLOUD_WEBROOT/"; then
    err "rsync failed; will retry on next firing"
    exit 1
fi

# 7. Inflate the sidecar into the raw data.geojson ON the VPS, atomically.
#    Caddy's precompressed lookup stats the ORIGINAL path first, and the
#    freshness checker reads the original's Last-Modified, so the raw file
#    must exist and be current — but it never needs to cross the wire.
#    A failure here is a real half-push (fresh sidecar, stale raw): say so
#    and exit 1 so the verdict is FAIL, never "pushed".
if ! ssh -i "$CLOUD_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
        "$CLOUD_USER@$CLOUD_HOST" \
        "gzip -dc '$CLOUD_WEBROOT/data.geojson.gz' > '$CLOUD_WEBROOT/.tmp/data.geojson.inflate' && mv -f '$CLOUD_WEBROOT/.tmp/data.geojson.inflate' '$CLOUD_WEBROOT/data.geojson'"; then
    err "sidecar pushed but remote inflate failed — raw data.geojson on the VPS is STALE; will retry on next firing"
    exit 1
fi

date +%s > "$STAMP"
log "pushed $N_FEATURES features (gz $(stat -c %s "$GZ_FINAL") B)${SW_PUSH:+ + space_weather}${AL_PUSH:+ + alerts} to $CLOUD_HOST in $((SECONDS))s"
exit 0
