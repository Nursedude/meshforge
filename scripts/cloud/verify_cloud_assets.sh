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
fail() { log "FAIL: $*" >&2; exit 1; }

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
exit 0
