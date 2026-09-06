#!/usr/bin/env bash
# Post-push watcher for the cloud :8808 demo. Runs as ExecStartPost on
# meshforge-cloud-push.service so every push is self-checked against
# the set of known asset signatures. A failure exits non-zero, which
# systemd surfaces as service-failed (red in `systemctl status`) and
# also lands in journalctl.
#
# Configuration via /etc/default/meshforge-cloud-push or env vars:
#   CLOUD_HOST       hostname of the cloud VPS (required)
#
# Checks today:
#   1. Popup-wrap CSS selector group + rule (commit 7ae4d80) — guards
#      against a regression that lets long meshcore: hashes overflow
#      the Leaflet popup border. The bug is purely cosmetic but the
#      symptom is hard to spot without opening a popup.
#
# Add new checks as same-shape grep blocks below; keep each check
# independent so one failing asset doesn't mask another.

set -uo pipefail

CLOUD_HOST="${CLOUD_HOST:-}"
if [[ -z "$CLOUD_HOST" ]]; then
    printf "%(%Y-%m-%dT%H:%M:%S%z)T  verify: CLOUD_HOST not set; skipping\n" -1 >&2
    # Skip rather than fail — operator may run this on a non-cloud host
    exit 0
fi

URL="https://$CLOUD_HOST/"

log() { printf "%(%Y-%m-%dT%H:%M:%S%z)T  verify: %s\n" -1 "$*"; }
# exit 2, not 1: the unit declares SuccessExitStatus=0 1 (1 = a transient
# push failure that the timer will retry), so an exit 1 from this verifier
# would be counted as SUCCESS and never surface. 2 is outside that set.
fail() { log "FAIL: $*" >&2; exit 2; }

# Bypass any intermediate cache so we read what Caddy is serving right
# now, not what a downstream might have cached from the previous push.
HTML=$(curl -sS --max-time 10 -H "Cache-Control: no-cache" "$URL") || \
    fail "fetch from $URL failed"

# Check 1: popup-wrap CSS (commit 7ae4d80, 2026-05-11)
#  - Selector group identifies the wrap fix specifically (other popup
#    rules existed before, but this combined selector is the fix's
#    fingerprint).
#  - The actual rule confirms the styling, not just the selector.
grep -qF ".popup-name, .popup-id, .popup-row" <<< "$HTML" || \
    fail "popup-wrap CSS selector group missing on $CLOUD_HOST"
grep -q "overflow-wrap: anywhere" <<< "$HTML" || \
    fail "popup-wrap overflow-wrap rule missing on $CLOUD_HOST"

log "OK: popup-wrap CSS present on $CLOUD_HOST"

# The precompressed sidecar must actually be what browsers receive
# (2026-09-06): /data.geojson with gzip accepted must come back
# Content-Encoding: gzip and small. Without this reader, a missing or
# unserved sidecar would silently return the page to 4.2 MB per fetch.
GZ_HDR=$(curl -sSI --max-time 15 -H "Accept-Encoding: gzip" "https://$CLOUD_HOST/data.geojson") || \
    fail "HEAD /data.geojson (gzip accepted) failed on $CLOUD_HOST"
grep -qi "^content-encoding: *gzip" <<< "$GZ_HDR" || \
    fail "/data.geojson is not served precompressed on $CLOUD_HOST (no Content-Encoding: gzip — sidecar missing or Caddy lacks 'precompressed gzip')"
GZ_LEN=$(grep -i "^content-length:" <<< "$GZ_HDR" | tr -dc "0-9")
if [[ -n "$GZ_LEN" && "$GZ_LEN" -gt 1500000 ]]; then
    fail "/data.geojson gzip body is ${GZ_LEN} B — not the sidecar"
fi
log "OK: /data.geojson served precompressed (${GZ_LEN:-?} B) on $CLOUD_HOST"
exit 0
