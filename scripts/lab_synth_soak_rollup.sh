#!/usr/bin/env bash
# lab_synth_soak_rollup.sh — aggregate synth soak JSON files over a
# rolling window into per-pair stats.
#
# Reads ~/.local/state/meshforge/synth_soak/synth-*.json (written by
# lab_synth_soak_fire.sh), aggregates over a configurable window, and
# emits a markdown table of per-(user, peer) samples / ok / mean /
# p50 / p95 / fail %.
#
# Usage:
#   bash scripts/lab_synth_soak_rollup.sh [hours=24] [state_dir]
#
# Examples:
#   bash scripts/lab_synth_soak_rollup.sh           # last 24h
#   bash scripts/lab_synth_soak_rollup.sh 1         # last 1h
#   bash scripts/lab_synth_soak_rollup.sh 168       # last week
#
# Output is markdown to stdout (paste into rollup notebook). The fire
# log itself is at $STATE_DIR/fire.log.

set -u

hours="${1:-24}"
STATE_DIR="${2:-${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/synth_soak}"

if [ ! -d "$STATE_DIR" ]; then
    echo "rollup: state dir not found: $STATE_DIR" >&2
    exit 2
fi

# Find JSONs in the time window.
# `find -mmin -N` selects files modified in the last N minutes.
mins=$(( hours * 60 ))
files=$(find "$STATE_DIR" -maxdepth 1 -name 'synth-*.json' -mmin "-${mins}" | sort)

if [ -z "$files" ]; then
    echo "rollup: no synth-*.json in last ${hours}h under $STATE_DIR" >&2
    exit 2
fi

# Use Python for the aggregation — it's pre-installed everywhere we
# run, and jq doesn't do percentiles natively. Python stays inline so
# the rollup is one self-contained shell file.
python3 - "$hours" "$STATE_DIR" <<'PYEOF' "$@"
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

hours = int(sys.argv[1])
state_dir = Path(sys.argv[2])

# Gather files in window
import time
cutoff = time.time() - hours * 3600
files = sorted(
    p for p in state_dir.glob("synth-*.json")
    if p.stat().st_mtime >= cutoff
)

if not files:
    print(f"rollup: no synth-*.json in last {hours}h under {state_dir}",
          file=sys.stderr)
    sys.exit(2)

# Per-(user, peer) aggregation across files
pairs = defaultdict(lambda: {
    "samples": 0,
    "ok": 0,
    "timeout": 0,
    "no_route": 0,
    "send_error": 0,
    "rtts_ms": [],
})

fires = 0
pass_count = 0
fail_count = 0
errored_files = []

for fp in files:
    try:
        with open(fp) as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        errored_files.append((str(fp), str(exc)))
        continue

    fires += 1
    if report.get("pass_envelope"):
        pass_count += 1
    else:
        fail_count += 1

    for row in report.get("pair_results", []):
        key = (row["user"], row["peer"])
        slot = pairs[key]
        slot["samples"] += row.get("samples", 0)
        slot["ok"] += row.get("ok", 0)
        slot["timeout"] += row.get("timeout", 0)
        slot["no_route"] += row.get("no_route", 0)
        slot["send_error"] += row.get("send_error", 0)
        # Reconstruct sample RTTs we can — we only have mean + p95 + ok
        # count from the per-fire summary. Best effort: emit mean as
        # a proxy "ok-weighted RTT" so the rollup mean averages across
        # fires. Real per-sample data would require fire to write a
        # detailed CSV; deferred.
        if row.get("mean_ms") is not None and row.get("ok", 0) > 0:
            slot["rtts_ms"].extend([float(row["mean_ms"])] * int(row["ok"]))

# Headline
since_iso = time.strftime(
    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff),
)
print(f"# Synth soak rollup — last {hours}h "
      f"(since {since_iso}, {fires} fires)")
print()

if errored_files:
    print(f"_skipped {len(errored_files)} unreadable files:_")
    for f, msg in errored_files[:5]:
        print(f"- `{f}`: {msg}")
    if len(errored_files) > 5:
        print(f"- ...and {len(errored_files) - 5} more")
    print()

print(f"- fires: **{fires}** "
      f"(pass {pass_count}, fail {fail_count})")
if fires > 0:
    print(f"- envelope pass rate: "
          f"**{100.0 * pass_count / fires:.1f}%**")

# Totals
total_samples = sum(s["samples"] for s in pairs.values())
total_ok = sum(s["ok"] for s in pairs.values())
total_timeout = sum(s["timeout"] for s in pairs.values())
total_no_route = sum(s["no_route"] for s in pairs.values())
total_send_err = sum(s["send_error"] for s in pairs.values())

if total_samples > 0:
    print(f"- aggregate: **{total_ok}/{total_samples} "
          f"({100.0 * total_ok / total_samples:.2f}%)** ok across all pairs "
          f"(timeout={total_timeout}, no_route={total_no_route}, "
          f"send_error={total_send_err})")
print()

# Per-pair table
print("| user | peer | samples | ok | mean ms | p50 ms | p95 ms | fail % | breakdown |")
print("|---|---|---:|---:|---:|---:|---:|---:|---|")

def sort_key(item):
    (user, peer), _ = item
    try:
        u_idx = int(user.removeprefix("synth-u"))
    except ValueError:
        u_idx = -1
    return (u_idx, peer)

for (user, peer), slot in sorted(pairs.items(), key=sort_key):
    samples = slot["samples"]
    ok = slot["ok"]
    fail_pct = 100.0 * (samples - ok) / samples if samples else 0.0
    rtts = slot["rtts_ms"]
    if rtts:
        mean_ms = f"{statistics.fmean(rtts):.0f}"
        sorted_r = sorted(rtts)
        # nearest-rank percentile
        p50 = sorted_r[max(0, int(0.50 * len(sorted_r)) - 1)]
        p95 = sorted_r[max(0, int(0.95 * len(sorted_r)) - 1)]
        p50_ms = f"{p50:.0f}"
        p95_ms = f"{p95:.0f}"
    else:
        mean_ms = p50_ms = p95_ms = "—"

    breakdown_bits = []
    if slot["timeout"]:
        breakdown_bits.append(f"timeout={slot['timeout']}")
    if slot["no_route"]:
        breakdown_bits.append(f"no-route={slot['no_route']}")
    if slot["send_error"]:
        breakdown_bits.append(f"send-error={slot['send_error']}")
    breakdown = " ".join(breakdown_bits) or "(none)"

    print(f"| `{user}` | {peer} | {samples} | {ok} | "
          f"{mean_ms} | {p50_ms} | {p95_ms} | {fail_pct:.1f} | {breakdown} |")

print()
print(f"_aggregated from {len(files)} JSON files in {state_dir}_")

PYEOF
