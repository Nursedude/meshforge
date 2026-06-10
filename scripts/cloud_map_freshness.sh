#!/bin/bash
# cloud_map_freshness.sh — end-to-end public cloud-map liveness check.
#
# ONE probe proves the whole chain: DNS resolves (a lapsed No-IP hostname
# fails here), TLS is valid, the VPS webserver is up, AND the publisher's
# push chain (meshforge-cloud-push.timer -> push_snapshot.sh -> rsync)
# delivered recently — via the Last-Modified header of data.geojson.
# Born in the 2026-06-09 version-updates arc: a push failure or domain
# lapse previously only left a journal line on the publisher; nothing paged.
#
# Run it on the CLOUD-PUBLISHER box (moc1), wired into the cron_verdict
# regime so cron_verdict_freshness.sh (federator) + probe_cron_verdict_stale
# page on FAIL or silence:
#   17 * * * * /opt/meshforge/scripts/cloud_map_freshness.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh cloud_map_freshness $?
#
# Env overrides:
#   CLOUD_MAP_URL              full URL of the pushed artifact to check
#   CLOUD_MAP_MAX_AGE_SECONDS  staleness threshold (default 3600 = 6 missed
#                              pushes at the 10-min timer cadence)
#
# Exit: 0 fresh · 1 stale · 2 unreachable / bad response / unparseable.
# Honest-failure-modes: every degraded path exits nonzero with a distinct
# message — absence of a header or an unparseable date is NEVER "fresh".
set -u

URL="${CLOUD_MAP_URL:-https://meshforge-maps.ddns.net/data.geojson}"
MAX_AGE="${CLOUD_MAP_MAX_AGE_SECONDS:-3600}"

hdr="$(curl -sSI --max-time 20 "$URL" 2>&1)"
rc=$?
if [ $rc -ne 0 ]; then
    echo "FAIL: curl rc=$rc: $(printf '%s' "$hdr" | head -1)"
    exit 2
fi

code="$(printf '%s' "$hdr" | awk 'NR==1{print $2}')"
if [ "$code" != "200" ]; then
    echo "FAIL: HTTP ${code:-<no status line>}"
    exit 2
fi

lm="$(printf '%s' "$hdr" | awk -F': ' 'tolower($1)=="last-modified"{print $2}' | tr -d '\r')"
if [ -z "$lm" ]; then
    echo "FAIL: 200 but no Last-Modified header (server config changed?)"
    exit 2
fi

lm_epoch="$(date -ud "$lm" +%s 2>/dev/null)"
if [ -z "$lm_epoch" ]; then
    echo "FAIL: unparseable Last-Modified '$lm'"
    exit 2
fi

now="$(date -u +%s)"
age=$(( now - lm_epoch ))

# Small negative = clock skew between us and the VPS, fine. Large negative
# means somebody's clock is lying (RTC-less Pi after a bad NTP step) — say so.
if [ "$age" -lt -300 ]; then
    echo "FAIL: Last-Modified is ${age}s in the future — clock trouble, not freshness"
    exit 2
fi

if [ "$age" -gt "$MAX_AGE" ]; then
    echo "STALE: data.geojson is ${age}s old (max ${MAX_AGE}s) — push chain or domain broken"
    exit 1
fi

echo "OK: data.geojson is ${age}s old"
exit 0
