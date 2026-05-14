#!/usr/bin/env bash
# lab_traffic_rollup.sh — Roll up tracer journal lines into a pairwise
# reliability matrix.
#
# For each fleet box in $HOME/.config/meshforge/fleet_hosts (or the
# fallback paths fleet_sync.sh uses), ssh in and pull the last
# $LOOKBACK_HOURS of journalctl --user-unit=meshforge-tracer output
# filtered for "rtt seq=" lines. Compute per-(from→to) pair:
#
#   samples | mean ms | p95 ms | fail %
#
# Emit a markdown table to stdout — operator pipes to `tee
# ~/lab_rollup_$(date +%Y%m%d).md` or pastes into substack writeups.
#
# Usage:
#   bash scripts/lab_traffic_rollup.sh                  # last 24h, alphabetical
#   LOOKBACK_HOURS=6 bash scripts/lab_traffic_rollup.sh
#   FLEET_HOSTS=~/.config/meshforge/fleet_hosts bash scripts/lab_traffic_rollup.sh
#   SORT_BY=fail bash scripts/lab_traffic_rollup.sh     # leaderboard: worst-fail-first
#   SORT_BY=rtt  bash scripts/lab_traffic_rollup.sh     # leaderboard: slowest-RTT-first
#
# SORT_BY values: pair (default), fail (desc), rtt (desc), samples (desc).
# Leaderboard ordering (fail/rtt) makes outliers pop in dashboards.
#
# Exit codes:
#   0 — rollup emitted (even if some hosts unreachable; those become "?"s)
#   2 — invalid args / fleet host file missing
#   3 — no rtt lines from any host (likely tracer not running)

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

# Collect rtt lines from each host. Tracer runs on (typically) one
# designated box, but we sweep all fleet boxes — each box's own short
# name appears in the rtt lines as "from=" (we tag it via ssh hostname).
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Headers + a "from-host" tag so we don't depend on the journal
# carrying $(hostname) in every line.
echo "# fleet-from-host | journal-line" > "$TMP"

hosts_seen=0
hosts_with_data=0

# Always pull the local journal first. fleet_hosts excludes "self" by
# convention (see scripts/fleet_sync.sh), but the tester box typically
# IS the local box — without this we'd silently miss its data.
local_host="$(hostname -s 2>/dev/null || hostname)"
hosts_seen=$((hosts_seen + 1))
local_result=$(journalctl --user-unit=meshforge-tracer \
    --since "${LOOKBACK_HOURS} hours ago" --no-pager -o cat 2>/dev/null \
    | grep -E 'tracer: rtt seq=' || true)
if [[ -n "$local_result" ]]; then
    hosts_with_data=$((hosts_with_data + 1))
    while IFS= read -r line; do
        printf '%s | %s\n' "$local_host" "$line" >> "$TMP"
    done <<< "$local_result"
fi

while IFS= read -r raw; do
    # Strip comments and trim. Skip blank lines.
    host="${raw%%#*}"
    host="${host//[[:space:]]/}"
    [[ -z "$host" ]] && continue
    # Skip if it matches the local host (already pulled).
    [[ "$host" == "$local_host" ]] && continue
    hosts_seen=$((hosts_seen + 1))

    # journalctl emits "<ts> <host> <id>[<pid>]: <message>". We only
    # want the message body. -o cat strips everything but the body.
    result=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$host" \
        "journalctl --user-unit=meshforge-tracer --since '${LOOKBACK_HOURS} hours ago' \
         --no-pager -o cat 2>/dev/null | grep -E 'tracer: rtt seq='" \
        2>/dev/null || true)

    if [[ -n "$result" ]]; then
        hosts_with_data=$((hosts_with_data + 1))
        # Tag each line with the host we pulled from, since the rtt
        # line itself doesn't echo "from=" (that's implicit in which
        # box ran the tracer).
        while IFS= read -r line; do
            printf '%s | %s\n' "$host" "$line" >> "$TMP"
        done <<< "$result"
    fi
done < "$FLEET_HOSTS"

if [[ "$hosts_seen" -eq 0 ]]; then
    echo "error: no hosts in $FLEET_HOSTS" >&2
    exit 2
fi

# Count payload rows (everything past the header).
rows="$(tail -n +2 "$TMP" | wc -l)"
if [[ "$rows" -eq 0 ]]; then
    echo "error: no rtt lines from any host (last ${LOOKBACK_HOURS}h)." >&2
    echo "       Is meshforge-tracer.timer enabled on a tester box?" >&2
    exit 3
fi

# Aggregate. Each row is "<from-host> | tracer: rtt seq=N peer=<name>
# result=<r> ms=<int>". We pull (from, peer, result, ms) into a small
# AWK pipeline.
#
# AWK fields after the "|" split:
#   $0 split by "|": from=$1
#   right half tokenized: seq= peer= result= ms=
awk -F'\\|' '
NR == 1 { next }    # skip header
{
    from = $1
    gsub(/[[:space:]]/, "", from)
    body = $2

    seq=""; peer=""; result=""; ms=""
    n = split(body, tokens, " ")
    for (i = 1; i <= n; i++) {
        t = tokens[i]
        if (t ~ /^seq=/)    { seq    = substr(t, 5) }
        else if (t ~ /^peer=/)   { peer   = substr(t, 6) }
        else if (t ~ /^result=/) { result = substr(t, 8) }
        else if (t ~ /^ms=/)     { ms     = substr(t, 4) }
    }

    if (peer == "" || result == "") next
    if (from == peer) { pair = from " (loopback)" }
    else              { pair = from " -> " peer }

    samples[pair] += 1
    if (result == "ok") {
        ok[pair] += 1
        # Track RTTs for mean + p95.
        rtt_count[pair] += 1
        idx = pair "/" rtt_count[pair]
        rtts[idx] = ms + 0
    } else {
        fail_kind[pair "/" result] += 1
    }
}

END {
    # Header.
    print ""
    print "## Lab traffic rollup"
    print ""
    print "| pair | samples | mean ms | p95 ms | fail % | breakdown |"
    print "|---|---:|---:|---:|---:|---|"

    # Compute per-pair stats first (mean/p95/fail_pct), then sort keys
    # by SORT_BY before printing. Two-pass so the sort comparator has
    # the stats it needs.
    n = 0
    for (k in samples) { keys[++n] = k }

    # First pass: compute mean / p95 / fail_pct per key.
    for (i = 1; i <= n; i++) {
        p = keys[i]
        s = samples[p]
        o = ok[p] + 0
        fail_pct_arr[p] = (s == 0) ? 0 : 100 * (s - o) / s

        cnt = rtt_count[p] + 0
        if (cnt == 0) {
            mean_arr[p] = -1   # sentinel — formatted as "-" later
            p95_arr[p]  = -1
        } else {
            sum = 0
            for (j = 1; j <= cnt; j++) {
                v[j] = rtts[p "/" j]
                sum += v[j]
            }
            for (a = 2; a <= cnt; a++) {
                x = v[a]; b = a - 1
                while (b >= 1 && v[b] > x) { v[b+1] = v[b]; b-- }
                v[b+1] = x
            }
            mean_arr[p] = sum / cnt
            p95_idx = int(0.95 * cnt + 0.999999)
            if (p95_idx < 1) p95_idx = 1
            if (p95_idx > cnt) p95_idx = cnt
            p95_arr[p] = v[p95_idx]
        }
    }

    # Sort comparator key per SORT_BY. Insertion sort — fleet is small.
    sort_by = "'"$SORT_BY"'"
    for (i = 2; i <= n; i++) {
        x = keys[i]
        j = i - 1
        while (j >= 1 && _greater(keys[j], x, sort_by, fail_pct_arr, mean_arr, samples)) {
            keys[j+1] = keys[j]
            j--
        }
        keys[j+1] = x
    }

    for (i = 1; i <= n; i++) {
        p = keys[i]
        s = samples[p]

        if (mean_arr[p] < 0) {
            mean = "-"; p95 = "-"
        } else {
            mean = sprintf("%.0f", mean_arr[p])
            p95  = sprintf("%.0f", p95_arr[p])
        }
        fail_pct = fail_pct_arr[p]

        # Breakdown of failure kinds for this pair.
        breakdown = ""
        for (k in fail_kind) {
            if (index(k, p "/") == 1) {
                kind = substr(k, length(p) + 2)
                c = fail_kind[k]
                breakdown = breakdown sprintf("%s=%d ", kind, c)
            }
        }
        if (breakdown == "") breakdown = "(none)"
        sub(/ +$/, "", breakdown)

        printf("| %s | %d | %s | %s | %.1f | %s |\n",
            p, s, mean, p95, fail_pct, breakdown)
    }
}

# Returns 1 if a sorts after b under the chosen sort key (= "a is greater").
# For pair: alphabetical asc (a > b means a comes after b). For fail/rtt/samples:
# desc order (a > b means a has a SMALLER value, so it should be moved later).
function _greater(a, b, kind, fail, mean, samples_,    av, bv) {
    if (kind == "pair") return a > b
    if (kind == "fail") {
        av = fail[a]; bv = fail[b]
        if (av == bv) return a > b   # stable tiebreak by name
        return av < bv               # desc: smaller fail sorts later
    }
    if (kind == "rtt") {
        # Treat missing RTT (-1) as "highest" — pure-fail pairs lead the leaderboard.
        av = mean[a]; bv = mean[b]
        if (av < 0 && bv >= 0) return 0   # a is "infinity" — a stays earlier
        if (bv < 0 && av >= 0) return 1   # b is "infinity" — a moves later
        if (av == bv) return a > b
        return av < bv               # desc
    }
    if (kind == "samples") {
        av = samples_[a]; bv = samples_[b]
        if (av == bv) return a > b
        return av < bv               # desc
    }
    return a > b   # fallback
}

# Awk concatenates multiple END blocks in source order. The window
# footer is split into a second END so the comparator function can sit
# at top level (awk forbids function defs inside blocks).
END {
    print ""
    printf("_window: last %d h    hosts queried: %d    hosts with data: %d_\n",
        '"$LOOKBACK_HOURS"', '"$hosts_seen"', '"$hosts_with_data"')
}
' "$TMP"

exit 0
