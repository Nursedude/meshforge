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
# PINNED, deliberately not overridable (07-23 audit): the watchdog probe
# reads ~operator/.local/state/meshforge/propagation_soak by construction
# (sandboxed root cannot see this unit's env), so honoring STATE_DIR /
# XDG_STATE_HOME here would publish envelopes where the probe never looks —
# a silently disarmed canary that reads INERT ("box doesn't run the drill").
# One path, both halves (honest_failure_modes #5).
STATE_DIR="$HOME/.local/state/meshforge/propagation_soak"

# Resolve repo root from the script's own location so this works wherever the
# clone lives (/opt/meshforge, ~/meshforge, ...). Same pattern as the synth fire.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/src" || {
    # A broken checkout must be LOUD (the old `|| exit 0` left only a header
    # line and 2.5h of silence — 2026-07-21 review).
    echo "propagation-soak fire: cannot cd $REPO_ROOT/src — broken checkout?" >&2
    exit 1
}

# INERT gate BEFORE any state write (2026-07-21 review F3): the probe's INERT
# contract is "state dir absent = box doesn't run the drill", and the old
# unconditional mkdir broke it — an unconfigured box with the timer installed
# created the dir, then published an hourly FAIL verdict and sat permanently
# indeterminate. Unconfigured now leaves NO trace: exit 0, no dir, no verdict
# (the probe also gates on intent, so a leftover dir from a de-adopted era
# stays quiet too). rc 4 = configured-but-malformed hash — a REAL operator
# error that falls through so the run below surfaces it as a FAIL verdict.
if [ -z "${PROP_NODE:-}" ]; then
    python3 -m lab.lxmf_propagation_soak --check-configured >/dev/null 2>&1
    cc_rc=$?
    if [ "$cc_rc" -eq 3 ]; then
        exit 0
    fi
fi

mkdir -p "$STATE_DIR"

# Single-writer: never run two fires at once (honest_failure_modes #8). Two
# concurrent fires in the SAME second collide on the timestamped envelope temp
# file, and — worse on a memory-constrained gateway — two simultaneous
# propagation stamps (CPU-bound proof-of-work) spike load and starve each
# other, which is what made back-to-back runs time out on moc3. flock -n makes
# a second fire skip cleanly: a skipped fire is NOT a failure (the running one
# publishes the envelope), so exit 0 with a log line rather than a FAIL verdict.
# The scheduled hourly timer never overlaps itself (a fire is capped at
# TimeoutStartSec well under an hour); this guards manual+scheduled overlap.
exec 9>"$STATE_DIR/.fire.lock"
if ! flock -n 9; then
    echo "$(date -u +%Y%m%dT%H%M%SZ) skip: another fire holds the lock" \
        >>"$STATE_DIR/fire.log"
    exit 0
fi

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
#   OK       pass_envelope=true  (node stored and returned the message)
#   CONCERN  pass_envelope=false (node had the message and failed to return it)
#   OK*      pass_envelope=null  (INDETERMINATE — the node was never exercised,
#            e.g. the sender was too slow generating the propagation stamp; NOT
#            a delivery failure, so not a CONCERN. The watchdog probe reads the
#            null envelope as "held" and the SILENCE leg still catches a truly
#            dead exerciser. Reported OK-status so /fleet/slo stays quiet, with
#            the message stating plainly that nothing was proven.)
#   FAIL     envelope present but MISSING the pass_envelope field (shape bug)
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
        # Prefer the DEFINITIVE failure over an indeterminate stall (F7) —
        # on a mixed run the first-match rule blamed the round that is by
        # design NOT a failure. Keep in lockstep with worst_round() in
        # lab/lxmf_propagation_soak.py.
        for r in rounds:
            if isinstance(r, dict) and r.get("ok") is False:
                line = f" — round {r.get('seq')} failed at {r.get('stage')}: {r.get('reason')}"
                if r.get("stage") not in ("send", "pull_link"):
                    why = line
                    break
                if not why:
                    why = line
        print(f"CONCERN store-and-forward {frac} round(s){why}")
    elif "pass_envelope" in d:
        # Explicit null: indeterminate. Say what happened without claiming
        # failure OR success — the node was never reached.
        ind = d.get("total_indeterminate")
        why = ""
        for r in (d.get("round_results") or []):
            if (isinstance(r, dict) and r.get("ok") is False
                    and r.get("stage") in ("send", "pull_link")):
                why = f" — {r.get('reason')}"
                break
        print(f"OK indeterminate: node not exercised ({ind} indeterminate round(s)){why}")
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
# Receiver identities leak when a fire is SIGTERM/SIGKILLed mid-round (a
# finally block does not run on SIGTERM) — the run cleans its own, so any
# survivor older than 2h is an orphan (2026-07-21 review F6).
find "$STATE_DIR" -maxdepth 1 -name 'round_b_identity_*' -mmin +120 -delete 2>/dev/null || true

# Always exit 0: the verdict LINE carries the result, not the unit state
# (same observability-not-red philosophy as the synth soak).
exit 0
