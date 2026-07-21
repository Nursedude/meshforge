#!/usr/bin/env bash
# lab_propagation_soak_fire.sh — one LXMF store-and-forward drill, JSON to disk.
#
# Fired by meshforge-propagation-soak.timer (systemd-user). Each fire produces
# one timestamped JSON in ~/.local/state/meshforge/propagation_soak/, consumed
# by probe_propagation_soak_degraded (which owns the alerting, including the
# SILENCE leg — for a fixed-cadence generator, going quiet IS the failure).
#
# Sister to lab_synth_soak_fire.sh, deliberately the same shape: cadence and
# knobs live in shell (operator-tunable), the Python module does one run.
#
# What it proves that nothing else does: the configured propagation node
# actually STORES and FORWARDS. probe_lxmf_propagation_node_dark only watches
# whether that node ANNOUNCES, so a node that announces perfectly while
# dropping every stored message reads clean forever.
#
# Knobs (env vars):
#   PROP_ROUNDS        (default 1)
#   PROP_SEND_TIMEOUT  (default 180)
#   PROP_PULL_TIMEOUT  (default 180)
#   PROP_THRESHOLD     (default 1.0 — one round, no partial credit)
#   PROP_NODE          (default: read from gateway.json)
#   STATE_DIR          (default $XDG_STATE_HOME/meshforge/propagation_soak)
#
# Retention: files older than 14 days are pruned at end of run.

set -u

ROUNDS="${PROP_ROUNDS:-1}"
SEND_TIMEOUT="${PROP_SEND_TIMEOUT:-180}"
PULL_TIMEOUT="${PROP_PULL_TIMEOUT:-180}"
THRESHOLD="${PROP_THRESHOLD:-1.0}"
STATE_DIR="${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/propagation_soak}"

mkdir -p "$STATE_DIR"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$STATE_DIR/prop-$stamp.json"
# Write to a hidden temp, rename only once the run produced a COMPLETE
# envelope. A direct `>"$out"` truncates the published file at run start, so
# for the whole run the newest prop-*.json is unparseable and the probe
# false-fires — the 2026-06-15 moc incident, inherited lesson. The leading dot
# keeps the temp out of the prop-*.json glob.
tmp="$STATE_DIR/.prop-$stamp.json.partial"
log="$STATE_DIR/fire.log"

{
    echo "=== propagation soak fire @ $stamp ==="
    echo "  rounds=$ROUNDS send_timeout=${SEND_TIMEOUT}s pull_timeout=${PULL_TIMEOUT}s threshold=$THRESHOLD"
    echo "  output=$out"
} >>"$log"

# Resolve repo root from the script's own location so this works wherever the
# clone lives (/opt/meshforge, ~/meshforge, ...). Same pattern as the synth fire.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/src" || exit 0

node_args=()
[ -n "${PROP_NODE:-}" ] && node_args=(--propagation-node "$PROP_NODE")

python3 -m lab.lxmf_propagation_soak \
    --rounds "$ROUNDS" \
    --send-timeout "$SEND_TIMEOUT" \
    --pull-timeout "$PULL_TIMEOUT" \
    --ok-ratio-threshold "$THRESHOLD" \
    "${node_args[@]}" \
    --output json \
    --loglevel WARNING \
    >"$tmp" 2>>"$log"

rc=$?

# Publish atomically iff the run wrote a COMPLETE, parseable envelope.
# Deliberately NOT gated on rc: a drill that FAILS writes a valid envelope with
# pass_envelope=false and exits non-zero, and that file MUST publish so the
# probe's ENVELOPE leg catches the real failure. A crash (or an unconfigured
# box, which prints nothing) leaves no new file — the SILENCE leg owns that.
if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$out"
    echo "  exit=$rc published (size=$(wc -c <"$out") bytes)" >>"$log"
else
    echo "  exit=$rc NOT PUBLISHED — temp not valid JSON (size=$(wc -c <"$tmp" 2>/dev/null || echo 0) bytes); discarding" >>"$log"
    rm -f "$tmp"
fi

# --- First-class cron verdict ----------------------------------------------
# Surface the drill RESULT on /fleet/slo. systemd-timer organ, so the verdict is
# UNWIRED — the fleet snapshot keeps it while FRESH and drops it once stale
# (2026-06-27 orphan stale-gate). #78 only judges WIRED crons, so no double-page;
# the watchdog probe still owns alerting.
#   OK       envelope published, pass_envelope=true
#   CONCERN  envelope published, pass_envelope=false (store-and-forward broken)
#   FAIL     no/invalid envelope
VERDICT_BIN="$REPO_ROOT/scripts/cron_verdict.sh"
if [ -x "$VERDICT_BIN" ]; then
    if [ -f "$out" ]; then
        verdict_out=$(python3 - "$out" 2>/dev/null <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    ok, n = d.get("total_ok"), d.get("total_samples")
    passed = d.get("pass_envelope")
    lat = (d.get("latency_s") or {}).get("median")
    frac = f"{ok}/{n}" if isinstance(ok, int) and isinstance(n, int) else "?"
    if passed is True:
        tail = f" median {lat}s" if isinstance(lat, (int, float)) else ""
        print(f"OK store-and-forward {frac} round(s){tail}")
    elif passed is False:
        rounds = d.get("round_results") or []
        why = ""
        for r in rounds:
            if isinstance(r, dict) and r.get("ok") is False:
                why = f" — round {r.get('seq')} failed at {r.get('stage')}: {r.get('reason')}"
                break
        print(f"CONCERN store-and-forward {frac} round(s){why}")
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
    "$VERDICT_BIN" propagation_soak "$vstatus" "$vmsg" 2>>"$log" || true
    echo "  verdict: $vstatus $vmsg" >>"$log"
fi

# Retention prune (best-effort), plus orphaned temps a SIGKILL could leave.
find "$STATE_DIR" -maxdepth 1 -name 'prop-*.json' -mtime +14 -delete 2>/dev/null || true
find "$STATE_DIR" -maxdepth 1 -name '.prop-*.json.partial' -mmin +120 -delete 2>/dev/null || true

# Always exit 0: the verdict LINE carries the result, not the unit state
# (same observability-not-red philosophy as the synth soak).
exit 0
