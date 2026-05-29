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
#   1  retryable error (network blip, VPS unreachable) — timer retries in 60s
#   2  configuration error — investigate, don't auto-retry

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

if [[ -z "${CLOUD_HOST:-}" ]]; then
    err "CLOUD_HOST not set; configure /etc/default/meshforge-cloud-push"
    exit 2
fi
if [[ ! -f "$CLOUD_SSH_KEY" ]]; then
    err "SSH key not found at $CLOUD_SSH_KEY"
    exit 2
fi

mkdir -p "$CACHE_DIR"
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
if ! curl -sS --max-time 5 -o /dev/null "https://$CLOUD_HOST/healthz" 2>/dev/null; then
    log "cloud healthcheck failed; will retry on next firing"
    exit 1
fi

# 6. Rsync. --temp-dir ensures the destination file swap is atomic from
#    Caddy's perspective. --inplace would race with concurrent reads.
RSYNC_OPTS=(
    -az
    --timeout=20
    --temp-dir="$CLOUD_WEBROOT/.tmp"
    -e "ssh -i $CLOUD_SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
)

PUSH_FILES=("$SNAPSHOT_FINAL" "$META_FINAL")
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

date +%s > "$STAMP"
log "pushed $N_FEATURES features${SW_PUSH:+ + space_weather}${AL_PUSH:+ + alerts} to $CLOUD_HOST in $((SECONDS))s"
exit 0
