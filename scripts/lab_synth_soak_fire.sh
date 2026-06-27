#!/usr/bin/env bash
# lab_synth_soak_fire.sh — one synth soak cycle, JSON to disk.
#
# Fired by meshforge-synth-soak.timer (systemd-user, 30-min cadence by
# default). Each fire produces one timestamped JSON in
# ~/.local/state/meshforge/synth_soak/. lab_synth_soak_rollup.sh
# aggregates the directory into per-pair stats over a rolling window.
#
# Decoupled from the synth Python module so cadence + parameters live
# in shell (operator-tunable) and the Python module stays focused on
# one fire.
#
# Knobs (env vars):
#   SYNTH_USERS       (default 10)
#   SYNTH_PATTERN     (default burst)
#   SYNTH_INTERVAL    (default 5)
#   SYNTH_DURATION    (default 60)
#   SYNTH_ACK_TIMEOUT (default 30)
#   SYNTH_EXCLUDE     (default moc3 — gateway-only Pi, no echo by design)
#   SYNTH_THRESHOLD   (default 0.95)
#   STATE_DIR         (default $XDG_STATE_HOME/meshforge/synth_soak)
#
# Retention: files older than 14 days are pruned at end of run.

set -u

USERS="${SYNTH_USERS:-10}"
PATTERN="${SYNTH_PATTERN:-burst}"
INTERVAL="${SYNTH_INTERVAL:-5}"
DURATION="${SYNTH_DURATION:-60}"
ACK_TIMEOUT="${SYNTH_ACK_TIMEOUT:-30}"
EXCLUDE="${SYNTH_EXCLUDE:-moc3}"
THRESHOLD="${SYNTH_THRESHOLD:-0.95}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/synth_soak}"

mkdir -p "$STATE_DIR"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$STATE_DIR/synth-$stamp.json"
# Write to a hidden temp first, then atomically rename into "$out" only once
# the run has produced a COMPLETE envelope. A direct `>"$out"` truncates the
# published file at run start, so for the whole ~60s+ack run the newest
# synth-*.json is empty/partial — readers (the synth_soak_degraded probe,
# rollup, diag) see an unparseable newest envelope and false-fire (the
# 2026-06-15 moc incident). The leading dot keeps the temp out of the
# `synth-*.json` glob so it's never mistaken for the newest result.
tmp="$STATE_DIR/.synth-$stamp.json.partial"
log="$STATE_DIR/fire.log"

{
    echo "=== synth soak fire @ $stamp ==="
    echo "  users=$USERS pattern=$PATTERN interval=${INTERVAL}s duration=${DURATION}s ack_timeout=${ACK_TIMEOUT}s"
    echo "  exclude=$EXCLUDE threshold=$THRESHOLD"
    echo "  output=$out"
} >>"$log"

# Resolve repo root from the script's own location so this works whether
# the clone lives at /opt/meshforge, ~/meshforge (e.g. meshanchor-server
# where no /opt write perms), or anywhere else. Same pattern as
# install_lab_traffic.sh.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/src"

python3 -m lab.lxmf_multi_user_synth \
    --users "$USERS" \
    --pattern "$PATTERN" \
    --interval "$INTERVAL" \
    --duration "$DURATION" \
    --ack-timeout "$ACK_TIMEOUT" \
    --exclude "$EXCLUDE" \
    --ok-ratio-threshold "$THRESHOLD" \
    --output json \
    --loglevel WARNING \
    >"$tmp" 2>>"$log"

rc=$?

# Publish atomically iff the run wrote a COMPLETE, parseable envelope. This is
# deliberately NOT gated on rc: a soak that FAILS its delivery threshold writes
# a valid envelope with pass_envelope=false (and may exit non-zero), and that
# file MUST publish so the probe's ENVELOPE leg catches the real failure. Only
# a crash that produced no valid JSON leaves no new file — the probe's SILENCE
# leg owns that case. mv is atomic within the same dir/filesystem.
if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$out"
    echo "  exit=$rc published (size=$(wc -c <"$out") bytes)" >>"$log"
else
    echo "  exit=$rc NOT PUBLISHED — temp not valid JSON (size=$(wc -c <"$tmp" 2>/dev/null || echo 0) bytes); discarding" >>"$log"
    rm -f "$tmp"
fi

# --- First-class cron verdict ----------------------------------------------
# Emit a greppable verdict so the soak RESULT surfaces on /fleet/slo. This is
# a systemd-timer organ (not a crontab line), so its verdict is UNWIRED — the
# fleet snapshot keeps it while FRESH and drops it once stale (the 2026-06-27
# orphan stale-gate). The verdict reflects the published envelope's pass/fail:
#   OK       envelope published, pass_envelope=true  (ACK ratio >= threshold)
#   CONCERN  envelope published, pass_envelope=false (degraded delivery)
#   FAIL     no/!valid envelope (the soak run produced nothing) — SILENCE leg
# The service still exits 0 below: the verdict LINE, not the service state,
# carries the soak result (same observability-not-red philosophy as the run).
# #78's cron_verdict_stale only judges WIRED crons, so this never double-pages;
# the synth_soak_degraded watchdog probe still owns the alerting.
VERDICT_BIN="$REPO_ROOT/scripts/cron_verdict.sh"
if [ -x "$VERDICT_BIN" ]; then
    if [ -f "$out" ]; then
        verdict_out=$(python3 - "$out" "$THRESHOLD" 2>/dev/null <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    thr = d.get("ok_ratio_threshold")
    if not isinstance(thr, (int, float)):
        thr = float(sys.argv[2])
    ratio = d.get("ok_ratio")
    ok, n = d.get("total_ok"), d.get("total_samples")
    passed = d.get("pass_envelope")
    pct = f"{ratio * 100:.0f}%" if isinstance(ratio, (int, float)) else "?"
    thrpct = f"{thr * 100:.0f}%"
    frac = f"{ok}/{n}" if isinstance(ok, int) and isinstance(n, int) else "?"
    if passed is True:
        print(f"OK {pct} ACK ({frac}) >= {thrpct}")
    elif passed is False:
        print(f"CONCERN {pct} ACK ({frac}) < {thrpct} degraded delivery")
    else:
        print("FAIL envelope missing pass_envelope field")
except Exception as e:
    print(f"FAIL unparseable envelope: {e.__class__.__name__}")
PY
)
    else
        verdict_out="FAIL no envelope published (rc=$rc; run produced no valid JSON)"
    fi
    [ -z "$verdict_out" ] && verdict_out="FAIL verdict extraction failed"
    vstatus=${verdict_out%% *}
    vmsg=${verdict_out#* }
    "$VERDICT_BIN" synth_soak "$vstatus" "$vmsg" 2>>"$log" || true
    echo "  verdict: $vstatus $vmsg" >>"$log"
fi

# Retention prune (best-effort). Also sweep orphaned temps that a SIGKILL
# mid-run could leave behind (the happy/fail paths above already clean theirs).
find "$STATE_DIR" -maxdepth 1 -name 'synth-*.json' -mtime +14 -delete 2>/dev/null || true
find "$STATE_DIR" -maxdepth 1 -name '.synth-*.json.partial' -mmin +120 -delete 2>/dev/null || true

# Always exit 0 — observability tool; soak should not flag service red
# on routine fleet-state variance. Same rationale as tracer (e27930e).
exit 0
