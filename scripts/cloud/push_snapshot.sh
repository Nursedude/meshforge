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
N_FEATURES=$(python3 -c "import json;print(len(json.load(open('$SNAPSHOT')).get('features',[])))")

# 3. Write a small meta.json with the freshness stamp + counts. The
#    cloud index.html reads this to display "last updated <X>s ago".
NOW_EPOCH=$(date +%s)
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$META" <<EOF
{
  "generated_at": "$NOW_ISO",
  "generated_at_epoch": $NOW_EPOCH,
  "feature_count": $N_FEATURES,
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

if ! rsync "${RSYNC_OPTS[@]}" \
        "$SNAPSHOT_FINAL" "$META_FINAL" \
        "$CLOUD_USER@$CLOUD_HOST:$CLOUD_WEBROOT/"; then
    err "rsync failed; will retry on next firing"
    exit 1
fi

date +%s > "$STAMP"
log "pushed $N_FEATURES features to $CLOUD_HOST in $((SECONDS))s"
exit 0
