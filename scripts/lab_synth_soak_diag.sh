#!/usr/bin/env bash
# lab_synth_soak_diag.sh — one-command diagnostic over the synth soak.
#
# Bundles the three things you'd otherwise type separately:
#   1. Fire count + cadence sanity (gaps, missed fires)
#   2. Per-pair rollup (samples / mean / p50 / p95 / fail %)
#   3. Anomaly callouts (pass-rate drift, p95 outliers, breakdown shifts)
#
# Designed to be readable on its own — paste the output into a notebook
# or commit it to a diagnostic memory and it reads like a report.
#
# Usage:
#   bash scripts/lab_synth_soak_diag.sh [hours=24] [state_dir]
#
# Examples:
#   bash scripts/lab_synth_soak_diag.sh        # last 24h, default state dir
#   bash scripts/lab_synth_soak_diag.sh 6      # last 6h (early-soak check)
#   bash scripts/lab_synth_soak_diag.sh 168    # last week

set -u

hours="${1:-24}"
STATE_DIR="${2:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/synth_soak}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Locate the rollup helper. We could be invoked from anywhere
# (e.g. /tmp during smoke tests, or installed in /opt/meshforge/scripts).
# Prefer the sibling next to us; fall back to the canonical install.
if [ -x "$SCRIPT_DIR/lab_synth_soak_rollup.sh" ]; then
    ROLLUP="$SCRIPT_DIR/lab_synth_soak_rollup.sh"
elif [ -x "/opt/meshforge/scripts/lab_synth_soak_rollup.sh" ]; then
    ROLLUP="/opt/meshforge/scripts/lab_synth_soak_rollup.sh"
else
    echo "diag: cannot find lab_synth_soak_rollup.sh "
    echo "  (looked at $SCRIPT_DIR and /opt/meshforge/scripts/)" >&2
    exit 2
fi

if [ ! -d "$STATE_DIR" ]; then
    echo "diag: state dir not found: $STATE_DIR" >&2
    echo "diag: enable the timer first:" >&2
    echo "  systemctl --user enable --now meshforge-synth-soak.timer" >&2
    exit 2
fi

# Same selection criterion as the rollup so the two outputs reconcile.
mins=$(( hours * 60 ))

# ============================================================ section 1
# Cadence health.
echo "================================================================"
echo "  Synth soak diagnostic — last ${hours}h"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "================================================================"
echo

python3 - "$hours" "$STATE_DIR" <<'PYEOF'
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

hours = int(sys.argv[1])
state_dir = Path(sys.argv[2])
cutoff = time.time() - hours * 3600

files = sorted(
    p for p in state_dir.glob("synth-*.json")
    if p.stat().st_mtime >= cutoff
)

# Expected: every 30 min (timer cadence)
expected_per_hour = 2.0
expected = int(round(hours * expected_per_hour))
actual = len(files)
ratio = (actual / expected * 100.0) if expected else 0.0

print(f"## Cadence")
print()
print(f"- expected fires (30-min cadence): **{expected}**")
print(f"- actual fires: **{actual}** ({ratio:.0f}% of expected)")

if actual == 0:
    print()
    print("**NO FIRES IN WINDOW.** Check:")
    print("  systemctl --user status meshforge-synth-soak.timer")
    print("  systemctl --user status meshforge-synth-soak.service")
    print(f"  tail -20 {state_dir}/fire.log")
    sys.exit(0)

# Parse timestamps from filenames (synth-YYYYMMDDTHHMMSSZ.json)
stamp_re = re.compile(r"synth-(\d{8}T\d{6}Z)\.json")
stamps = []
for fp in files:
    m = stamp_re.match(fp.name)
    if m:
        stamps.append(datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ"))
stamps.sort()

# Gap detection: any inter-fire gap > 40 min (timer 30 min + 30s
# accuracy + 15s randomized delay + headroom) is a missed fire or
# slowdown.
gap_threshold = timedelta(minutes=40)
gaps = []
for i in range(1, len(stamps)):
    delta = stamps[i] - stamps[i-1]
    if delta > gap_threshold:
        gaps.append((stamps[i-1], stamps[i], delta))

if gaps:
    print(f"- gaps >40 min: **{len(gaps)}**")
    for prev, curr, delta in gaps[:5]:
        print(f"  - `{prev:%Y-%m-%dT%H:%M:%SZ}` → `{curr:%Y-%m-%dT%H:%M:%SZ}`  "
              f"(gap {int(delta.total_seconds() / 60)} min)")
    if len(gaps) > 5:
        print(f"  - ...and {len(gaps) - 5} more")
else:
    print(f"- gaps >40 min: **0** (cadence healthy)")

print(f"- first: `{stamps[0]:%Y-%m-%dT%H:%M:%SZ}`")
print(f"- last:  `{stamps[-1]:%Y-%m-%dT%H:%M:%SZ}`")
print()

# Recent fire.log tail for sanity (file is small, last 8 lines).
log = state_dir / "fire.log"
if log.exists():
    print("```")
    with open(log) as f:
        lines = f.readlines()
    for line in lines[-8:]:
        print(line.rstrip())
    print("```")
print()
PYEOF

# ============================================================ section 2
# Rollup — delegate to the existing aggregator.
echo
echo "## Rollup"
echo
bash "$ROLLUP" "$hours" "$STATE_DIR" \
    | sed -e '/^# /d' -e '/^_aggregated/d'

# ============================================================ section 3
# Anomaly callouts. Pure-Python; reads same files as rollup.
echo
echo "## Anomalies"
echo

python3 - "$hours" "$STATE_DIR" <<'PYEOF'
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

hours = int(sys.argv[1])
state_dir = Path(sys.argv[2])
cutoff = time.time() - hours * 3600

files = sorted(
    p for p in state_dir.glob("synth-*.json")
    if p.stat().st_mtime >= cutoff
)

if not files:
    print("_no data_")
    sys.exit(0)

# Walk fires, collect per-fire (pass, total_samples, total_ok, per-peer
# means) for trend detection.
fires = []
errored = 0
for fp in files:
    try:
        with open(fp) as f:
            r = json.load(f)
    except (OSError, json.JSONDecodeError):
        errored += 1
        continue
    fires.append({
        "stamp": fp.stat().st_mtime,
        "pass": bool(r.get("pass_envelope")),
        "total_samples": r.get("total_samples", 0),
        "total_ok": r.get("total_ok", 0),
        "pair_results": r.get("pair_results", []),
    })

if not fires:
    print("_all fires unreadable_")
    sys.exit(0)

flagged = []

# --- A. Recent pass-rate drift
# Compare oldest half vs newest half. If the newest half is meaningfully
# worse, gateway / fleet degradation is the leading hypothesis.
if len(fires) >= 4:
    half = len(fires) // 2
    old_half = fires[:half]
    new_half = fires[half:]
    old_pass_rate = sum(1 for f in old_half if f["pass"]) / len(old_half)
    new_pass_rate = sum(1 for f in new_half if f["pass"]) / len(new_half)
    if new_pass_rate < old_pass_rate - 0.05:  # >5pp drop
        flagged.append(
            f"- **pass-rate drift**: "
            f"oldest-half {old_pass_rate * 100:.0f}% → "
            f"newest-half {new_pass_rate * 100:.0f}% "
            f"({(new_pass_rate - old_pass_rate) * 100:+.0f}pp). "
            f"Gateway / fleet may be degrading over the window."
        )

# --- B. Per-pair failure clustering
# Walk all fires, aggregate per-(user, peer). Surface pairs with
# failure rate > 5% of samples (the inverse of the 95% envelope).
pair_totals = defaultdict(lambda: {"samples": 0, "ok": 0, "timeout": 0,
                                    "no_route": 0, "send_error": 0})
for f in fires:
    for row in f["pair_results"]:
        key = (row["user"], row["peer"])
        s = pair_totals[key]
        s["samples"] += row.get("samples", 0)
        s["ok"] += row.get("ok", 0)
        s["timeout"] += row.get("timeout", 0)
        s["no_route"] += row.get("no_route", 0)
        s["send_error"] += row.get("send_error", 0)

bad_pairs = []
for (user, peer), s in pair_totals.items():
    if s["samples"] < 20:
        continue  # too little data
    fail_pct = (s["samples"] - s["ok"]) / s["samples"] * 100
    if fail_pct > 5.0:
        bad_pairs.append((user, peer, fail_pct, s))
bad_pairs.sort(key=lambda x: -x[2])

if bad_pairs:
    flagged.append("- **failure-prone pairs (>5% fail across window)**:")
    for user, peer, fail_pct, s in bad_pairs[:8]:
        bits = []
        if s["timeout"]:
            bits.append(f"timeout={s['timeout']}")
        if s["no_route"]:
            bits.append(f"no-route={s['no_route']}")
        if s["send_error"]:
            bits.append(f"send-error={s['send_error']}")
        flagged.append(
            f"  - `{user}` → {peer}: {fail_pct:.1f}% fail "
            f"({s['ok']}/{s['samples']} ok, {' '.join(bits)})"
        )
    if len(bad_pairs) > 8:
        flagged.append(f"  - ...and {len(bad_pairs) - 8} more")

# --- C. Per-peer mean-RTT outliers (which peer is the slow one?)
peer_mean_rtts = defaultdict(list)
for f in fires:
    for row in f["pair_results"]:
        if row.get("mean_ms") is not None and row.get("ok", 0) > 0:
            peer_mean_rtts[row["peer"]].append(float(row["mean_ms"]))

if peer_mean_rtts:
    peer_stats = {}
    for peer, rtts in peer_mean_rtts.items():
        if len(rtts) >= 3:
            peer_stats[peer] = {
                "mean": statistics.fmean(rtts),
                "p95": sorted(rtts)[max(0, int(0.95 * len(rtts)) - 1)],
                "n": len(rtts),
            }
    # Sort slowest first
    slow_peers = sorted(peer_stats.items(), key=lambda x: -x[1]["mean"])
    if slow_peers:
        flagged.append("- **per-peer RTT ranking** (mean of per-fire means):")
        for peer, st in slow_peers:
            flagged.append(
                f"  - {peer}: mean={st['mean']:.0f}ms "
                f"p95={st['p95']:.0f}ms (n={st['n']})"
            )

# --- D. Errored files
if errored:
    flagged.append(f"- **{errored} fire JSON file(s) unreadable** in window")

# --- E. Total volume sanity
total_samples = sum(f["total_samples"] for f in fires)
total_ok = sum(f["total_ok"] for f in fires)
if total_samples > 0:
    overall = total_ok / total_samples * 100
    flagged.append(
        f"- **overall**: {total_ok}/{total_samples} ({overall:.2f}%) "
        f"ok across {len(fires)} fires"
    )

if not flagged or (len(flagged) == 1 and flagged[0].startswith("- **overall")):
    print("_no anomalies detected — soak is steady_")
    print()
    if flagged:
        for line in flagged:
            print(line)
else:
    for line in flagged:
        print(line)

print()
print("---")
print()
print(f"_diagnostic generated at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} over {len(fires)} fires_")
PYEOF
