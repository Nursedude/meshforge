#!/usr/bin/env bash
# lab_traffic_rollup.sh — Roll up tracer JSON state files into a
# pairwise reliability matrix.
#
# For each fleet box in $HOME/.config/meshforge/fleet_hosts (or the
# fallback paths fleet_sync.sh uses), ssh in and concatenate the last
# $LOOKBACK_HOURS of `tracer-*.json` files from the box's state dir
# (~/.local/state/meshforge/tracer/). Each file is one fire's JSON
# object, newline-terminated → the concatenation is clean JSONL. The
# Python aggregator parses each line, bins per-(from→to) pair, and
# emits a markdown table of samples / mean / p95 / fail %.
#
# Why state files instead of the journal: every fleet box runs
# journald with `Storage=volatile`, and the per-user /run journals
# are size-capped at 17-74 MB on the small Pis. 24h of tracer history
# rotates away in ~1 hour, so the journal-based rollup undercounted
# non-tester boxes (caught 2026-05-14 — see
# project_lab_install_gap_moc3.md). State files persist across journal
# rotation; rotation is mtime-based pruning inside lxmf_tracer.py.
#
# Usage:
#   bash scripts/lab_traffic_rollup.sh                  # last 24h, alphabetical
#   LOOKBACK_HOURS=6 bash scripts/lab_traffic_rollup.sh
#   FLEET_HOSTS=~/.config/meshforge/fleet_hosts bash scripts/lab_traffic_rollup.sh
#   SORT_BY=fail bash scripts/lab_traffic_rollup.sh     # leaderboard: worst-fail-first
#   SORT_BY=rtt  bash scripts/lab_traffic_rollup.sh     # leaderboard: slowest-RTT-first
#
# SORT_BY values: pair (default), fail (desc), rtt (desc), samples (desc).
#
# Exit codes:
#   0 — rollup emitted (even if some hosts unreachable; those become
#       a "no state files" note in the footer)
#   2 — invalid args / fleet host file missing
#   3 — no JSON state files from any host (likely tracer not running,
#       or no fire has happened since the state-file rollout landed)

set -u
set -o pipefail

LOOKBACK_HOURS="${LOOKBACK_HOURS:-24}"
FLEET_HOSTS="${FLEET_HOSTS:-}"
SORT_BY="${SORT_BY:-pair}"

case "$SORT_BY" in
    pair|fail|rtt|samples) ;;
    *) echo "error: SORT_BY must be pair|fail|rtt|samples (got: $SORT_BY)" >&2; exit 2 ;;
esac

if [[ -z "$FLEET_HOSTS" ]]; then
    if [[ -r "$HOME/.config/meshforge/fleet_hosts" ]]; then
        FLEET_HOSTS="$HOME/.config/meshforge/fleet_hosts"
    elif [[ -r "/etc/meshforge/fleet_hosts" ]]; then
        FLEET_HOSTS="/etc/meshforge/fleet_hosts"
    else
        echo "error: no fleet_hosts file found." >&2
        echo "  Set \$FLEET_HOSTS or create ~/.config/meshforge/fleet_hosts" >&2
        exit 2
    fi
fi

if ! [[ "$LOOKBACK_HOURS" =~ ^[0-9]+$ ]]; then
    echo "error: LOOKBACK_HOURS must be a positive integer" >&2
    exit 2
fi

# State dir path on each fleet box. $HOME expansion happens on the
# remote side (single-quoted in the ssh command).
REMOTE_STATE_EXPR='"${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/tracer"'

# find -mmin uses minutes. Convert hours → minutes.
MINS=$(( LOOKBACK_HOURS * 60 ))

# Per-host fetch: list .json files in the window and cat them. Files
# are atomically written by lxmf_tracer.write_state_file (tmp + replace)
# and newline-terminated, so concatenation produces clean JSONL.
fetch_host_json() {
    local host="$1"
    if [[ "$host" == "__local__" ]]; then
        local sd
        sd="${XDG_STATE_HOME:-$HOME/.local/state}/meshforge/tracer"
        if [[ -d "$sd" ]]; then
            find "$sd" -maxdepth 1 -name 'tracer-*.json' -mmin "-${MINS}" \
                -exec cat {} + 2>/dev/null
        fi
        return 0
    fi
    # `ssh -n` is load-bearing: without it ssh consumes the host list
    # via stdin and silently truncates the rollup to the first peer
    # (fixed 2026-05-14 in commit 46522e4).
    ssh -n -o BatchMode=yes -o ConnectTimeout=8 "$host" \
        "sd=${REMOTE_STATE_EXPR}; \
         [ -d \"\$sd\" ] && find \"\$sd\" -maxdepth 1 \
            -name 'tracer-*.json' -mmin '-${MINS}' \
            -exec cat {} + 2>/dev/null" \
        2>/dev/null || true
}

# Collect every host's JSONL into one stream. Track which hosts
# returned anything so the footer can flag silent ones.
TMP="$(mktemp)"
EMPTY_HOSTS="$(mktemp)"
trap 'rm -f "$TMP" "$EMPTY_HOSTS"' EXIT

hosts_seen=0
hosts_with_data=0

emit_host_data() {
    local label="$1"
    local data="$2"
    hosts_seen=$((hosts_seen + 1))
    if [[ -n "$data" ]]; then
        hosts_with_data=$((hosts_with_data + 1))
        printf '%s\n' "$data" >> "$TMP"
    else
        printf '%s\n' "$label" >> "$EMPTY_HOSTS"
    fi
}

# Local box first (fleet_hosts excludes self by convention).
local_host="$(hostname -s 2>/dev/null || hostname)"
emit_host_data "$local_host (local)" "$(fetch_host_json __local__)"

while IFS= read -r raw; do
    host="${raw%%#*}"
    host="${host//[[:space:]]/}"
    [[ -z "$host" ]] && continue
    [[ "$host" == "$local_host" ]] && continue
    emit_host_data "$host" "$(fetch_host_json "$host")"
done < "$FLEET_HOSTS"

if [[ "$hosts_seen" -eq 0 ]]; then
    echo "error: no hosts in $FLEET_HOSTS" >&2
    exit 2
fi

lines="$(wc -l < "$TMP" 2>/dev/null || echo 0)"
if [[ "$lines" -eq 0 ]]; then
    echo "error: no tracer state files from any host (last ${LOOKBACK_HOURS}h)." >&2
    echo "       Has any box fired since the state-file rollout?" >&2
    echo "       Check on a fleet box:" >&2
    echo "         ls ~/.local/state/meshforge/tracer/ | wc -l" >&2
    exit 3
fi

# Aggregate. Python reads JSONL from $TMP (one fire per line),
# accumulates per-(from→to) pair stats, and emits the markdown table.
python3 - "$SORT_BY" "$LOOKBACK_HOURS" "$hosts_seen" "$hosts_with_data" \
         "$TMP" "$EMPTY_HOSTS" <<'PYEOF'
import json
import statistics
import sys
from collections import defaultdict

sort_by, lookback_h, hosts_seen, hosts_with_data, tmp_path, empty_path = \
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), \
    sys.argv[5], sys.argv[6]

# pairs[(from, to)] = {samples, ok, fail_kinds: {kind: count}, rtts: [int]}
pairs = defaultdict(lambda: {
    "samples": 0, "ok": 0, "fail_kinds": defaultdict(int), "rtts": [],
})
malformed = 0

with open(tmp_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        sender = doc.get("self_short")
        if not sender:
            malformed += 1
            continue
        for row in doc.get("results", []):
            peer = row.get("peer")
            result = row.get("result")
            if not peer or not result:
                continue
            key = (sender, peer) if sender != peer else (sender + " (loopback)", "")
            slot = pairs[key]
            slot["samples"] += 1
            if result == "ok":
                slot["ok"] += 1
                ms = row.get("rtt_ms")
                if isinstance(ms, (int, float)):
                    slot["rtts"].append(int(ms))
            else:
                slot["fail_kinds"][result] += 1

# Pretty pair label: loopback rows have empty "to", restore the original
# rollup shape "host (loopback)" for them.
def pair_label(key):
    sender, peer = key
    if peer == "" and sender.endswith(" (loopback)"):
        return sender
    return f"{sender} -> {peer}"

rows = []
for key, slot in pairs.items():
    samples = slot["samples"]
    ok = slot["ok"]
    fail_pct = 100.0 * (samples - ok) / samples if samples else 0.0
    rtts = slot["rtts"]
    if rtts:
        rtts_sorted = sorted(rtts)
        mean_ms = int(round(statistics.fmean(rtts)))
        # Nearest-rank, ceiling to match the existing awk behavior
        # (int(0.95 * n + 0.999999)).
        idx = max(1, min(len(rtts_sorted),
                         int(0.95 * len(rtts_sorted) + 0.999999)))
        p95_ms = rtts_sorted[idx - 1]
    else:
        mean_ms = None
        p95_ms = None
    breakdown_bits = [
        f"{kind}={cnt}" for kind, cnt in
        sorted(slot["fail_kinds"].items())
    ]
    breakdown = " ".join(breakdown_bits) if breakdown_bits else "(none)"
    rows.append({
        "label": pair_label(key),
        "samples": samples, "ok": ok,
        "mean_ms": mean_ms, "p95_ms": p95_ms,
        "fail_pct": fail_pct, "breakdown": breakdown,
    })

def sort_key(r):
    if sort_by == "fail":
        # desc fail, then label asc as tiebreak
        return (-r["fail_pct"], r["label"])
    if sort_by == "rtt":
        # Missing RTT (pure-fail pairs) leads the leaderboard.
        mean = r["mean_ms"]
        if mean is None:
            return (-1e18, r["label"])
        return (-mean, r["label"])
    if sort_by == "samples":
        return (-r["samples"], r["label"])
    return (r["label"],)

rows.sort(key=sort_key)

print()
print("## Lab traffic rollup")
print()
print("| pair | samples | mean ms | p95 ms | fail % | breakdown |")
print("|---|---:|---:|---:|---:|---|")
for r in rows:
    mean_s = "-" if r["mean_ms"] is None else str(r["mean_ms"])
    p95_s = "-" if r["p95_ms"] is None else str(r["p95_ms"])
    print(f"| {r['label']} | {r['samples']} | {mean_s} | {p95_s} | "
          f"{r['fail_pct']:.1f} | {r['breakdown']} |")

print()
empty_hosts = []
try:
    with open(empty_path) as f:
        empty_hosts = [ln.strip() for ln in f if ln.strip()]
except OSError:
    pass

if empty_hosts:
    print("_silent (no state files in window — tracer not firing, or "
          "rollout hasn't reached this box):_")
    for h in empty_hosts:
        print(f"- `{h}`")
    print()

footer = (f"_window: last {lookback_h} h    "
          f"hosts queried: {hosts_seen}    "
          f"hosts with data: {hosts_with_data}_")
if malformed:
    footer += f"    _malformed-line skipped: {malformed}_"
print(footer)
PYEOF

exit 0
