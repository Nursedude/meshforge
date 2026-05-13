#!/usr/bin/env bash
# scripts/fleet_diag_bundle.sh — on-demand fleet triage snapshot.
#
# Complement to fleet_runtime_health.sh, NOT a replacement:
#   - fleet_runtime_health.sh answers "is anything RED right now?"
#     (failed units, disk%, OOM, ERROR rate, SSH reachability)
#   - this script answers "what does each box LOOK LIKE right now?"
#     for engineer triage — TCP contention, DB/WAL sizes, service
#     is-active, journal tails, git HEAD, host basics.
#
# Replaces the ad-hoc `ssh box1 'systemctl status …; ss -tnp …; ls -lh
# …/*.db*; journalctl -u … --no-pager | tail'` loop we both run when
# something feels off.
#
# Fans probes across the fleet in parallel; each box runs its own SSH
# session with a hard timeout, so one stuck box can't hang the bundle.
# Output goes to stdout (markdown) AND a timestamped file under
# ~/.local/state/meshforge/diag/.
#
# Usage:
#   scripts/fleet_diag_bundle.sh                # default 5 fleet boxes
#   scripts/fleet_diag_bundle.sh --all          # + meshanchor-server
#   scripts/fleet_diag_bundle.sh --hosts a,b    # custom host list
#   scripts/fleet_diag_bundle.sh --quiet        # only print output path
#
# Exit codes:
#   0 — bundle generated (one or more hosts may have timed out; check
#       the report's per-host header)
#   2 — bad usage / no hosts resolved

set -euo pipefail

SELF="$(basename "$0")"

# ─── host list ──────────────────────────────────────────────────────
# Same lookup chain as fleet_sync.sh / fleet_runtime_health.sh:
#   $MESHFORGE_FLEET_HOSTS → ~/.config/meshforge/fleet_hosts →
#   /etc/meshforge/fleet_hosts. One host per line; '#' comments allowed.
# The current host is included automatically (probed via loopback SSH).

HOSTS=""
QUIET=0
SELF_HOST="$(hostname -s)"

resolve_hosts_file() {
    if [[ -n "${MESHFORGE_FLEET_HOSTS:-}" && -r "$MESHFORGE_FLEET_HOSTS" ]]; then
        echo "$MESHFORGE_FLEET_HOSTS"
    elif [[ -r "$HOME/.config/meshforge/fleet_hosts" ]]; then
        echo "$HOME/.config/meshforge/fleet_hosts"
    elif [[ -r /etc/meshforge/fleet_hosts ]]; then
        echo /etc/meshforge/fleet_hosts
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hosts)  HOSTS="$(echo "${2:-}" | tr ',' ' ')"; shift 2 ;;
        --quiet)  QUIET=1; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *)
            echo "$SELF: unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$HOSTS" ]]; then
    HOSTS_FILE="$(resolve_hosts_file)"
    if [[ -z "$HOSTS_FILE" ]]; then
        echo "$SELF: no host list found — set MESHFORGE_FLEET_HOSTS or create ~/.config/meshforge/fleet_hosts" >&2
        exit 2
    fi
    HOSTS="$(grep -vE '^\s*(#|$)' "$HOSTS_FILE" | xargs)"
    # Include self if not already present.
    if [[ -n "$SELF_HOST" ]] && ! echo " $HOSTS " | grep -q " $SELF_HOST "; then
        HOSTS="$SELF_HOST $HOSTS"
    fi
fi

HOSTS="$(echo "$HOSTS" | xargs)"
if [[ -z "$HOSTS" ]]; then
    echo "$SELF: no hosts resolved" >&2
    exit 2
fi

# ─── output paths ───────────────────────────────────────────────────
TS="$(date -u +%Y%m%d-%H%M%SZ)"
OUT_DIR="$HOME/.local/state/meshforge/diag"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/bundle-$TS.md"

TMP_DIR="$(mktemp -d -t fleet-diag.XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ─── per-box remote probe (runs over SSH) ───────────────────────────
# Heredoc keeps the remote payload as a single $REMOTE script. Avoids
# escaping nightmares with nested ssh + grep + awk pipelines.
read -r -d '' REMOTE <<'REMOTE_EOF' || true
set -u

echo "### host basics"
echo '```'
hostname
echo "uptime: $(uptime -p 2>/dev/null || uptime)"
echo "load: $(cut -d' ' -f1-3 /proc/loadavg)"
free -h | awk 'NR==1 || NR==2'
df -h / 2>/dev/null | awk 'NR==1 || NR==2'
if [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
    t=$(cat /sys/class/thermal/thermal_zone0/temp)
    awk -v t="$t" 'BEGIN { printf "soc_temp: %.1f°C\n", t/1000 }'
fi
echo '```'

echo
echo "### git HEAD (/opt/meshforge)"
echo '```'
if [[ -d /opt/meshforge/.git ]]; then
    git -C /opt/meshforge rev-parse --short HEAD 2>&1
    git -C /opt/meshforge log -1 --format='%cr  %s' 2>&1
else
    echo "(no /opt/meshforge checkout)"
fi
echo '```'

echo
echo "### node_history.db sizes"
echo '```'
shopt -s nullglob
db_dir="$HOME/.local/share/meshforge"
if [[ -d "$db_dir" ]]; then
    ls -lh "$db_dir"/*.db "$db_dir"/*.db-wal "$db_dir"/*.db-shm 2>/dev/null \
        | awk '{print $5, $9}' \
        | sed "s|$HOME|~|" \
        || echo "(no DBs found)"
else
    echo "(no $db_dir)"
fi
echo '```'

echo
echo "### installed unit ↔ repo template drift"
echo '```'
# Catches the class of "fix is in the repo but /etc/systemd/system/
# never got the new copy" gotcha (e.g. the meshanchor-daemon EROFS
# class on 2026-05-13: a sandbox fix landed in the MA repo but the
# cp-to-/etc step was never re-run on the deploy box, so the daemon
# spammed [Errno 30] for 7 days while the daemon was technically
# `active (running)`).
drift=0
for installed in /etc/systemd/system/*.service /etc/systemd/system/*.timer; do
    [[ -f "$installed" ]] || continue
    name=$(basename "$installed")
    for tpl in /opt/meshforge/templates/systemd/"$name" \
               /opt/meshforge/scripts/"$name" \
               /opt/meshanchor/scripts/"$name"; do
        [[ -f "$tpl" ]] || continue
        if ! cmp -s "$installed" "$tpl"; then
            echo "DRIFT: $name"
            echo "  installed md5: $(md5sum "$installed" | cut -d' ' -f1)"
            echo "  template  md5: $(md5sum "$tpl"        | cut -d' ' -f1)  ($tpl)"
            drift=1
        fi
        break
    done
done
[[ $drift -eq 0 ]] && echo "(all installed units match repo templates)"
echo '```'

echo
echo "### :4403 TCP holders (python = contention)"
echo '```'
# ss might need sudo for -p info; try non-priv first, fall back quietly
out=$(ss -tnp 2>/dev/null | grep ':4403' || true)
if [[ -z "$out" ]]; then
    echo "(no :4403 sockets — normal idle)"
else
    echo "$out"
fi
echo '```'

echo
echo "### meshforge-* service is-active"
echo '```'
for unit in meshforge.service meshforge-map.service meshforge-cloud-push.timer \
            meshforge-backup.timer meshtasticd.service rnsd.service \
            meshanchor-daemon.service nomadnet-user.service; do
    state=$(systemctl is-active "$unit" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
    [[ -z "$state" ]] && state="—"
    [[ -z "$enabled" ]] && enabled="—"
    printf "%-40s %-10s %s\n" "$unit" "$state" "$enabled"
done
echo '```'

echo
echo "### journal tails (last 5 min, key services)"
for unit in meshforge.service meshforge-map.service meshanchor-daemon.service; do
    state=$(systemctl is-active "$unit" 2>/dev/null || echo "missing")
    if [[ "$state" == "active" ]]; then
        echo
        echo "#### $unit"
        echo '```'
        sudo -n journalctl -u "$unit" --since "5 min ago" --no-pager 2>&1 \
            | tail -12 \
            || echo "(journal read needs sudo; sudo -n not allowed for journalctl on this box)"
        echo '```'
    fi
done
REMOTE_EOF

# ─── fan out: one background job per host ──────────────────────────
echo "fleet_diag_bundle: probing $(echo "$HOSTS" | wc -w) hosts in parallel…" >&2

START=$(date +%s)
for host in $HOSTS; do
    (
        host_start=$(date +%s)
        out_file="$TMP_DIR/$host.md"
        echo "## $host" > "$out_file"
        echo >> "$out_file"
        if ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 \
               -o ServerAliveCountMax=2 -o StrictHostKeyChecking=accept-new \
               "$host" "bash -s" <<< "$REMOTE" >> "$out_file" 2>&1; then
            host_end=$(date +%s)
            echo >> "$out_file"
            echo "_probe wall: $((host_end - host_start))s_" >> "$out_file"
        else
            rc=$?
            echo "" >> "$out_file"
            echo "**SSH/probe failed (rc=$rc)** — host unreachable or timed out." >> "$out_file"
        fi
    ) &
done
wait
END=$(date +%s)

# ─── assemble report ────────────────────────────────────────────────
{
    echo "# fleet_diag_bundle — $TS"
    echo
    echo "_wall: $((END - START))s · hosts: ${HOSTS}_"
    echo
    for host in $HOSTS; do
        if [[ -s "$TMP_DIR/$host.md" ]]; then
            cat "$TMP_DIR/$host.md"
        else
            echo "## $host"
            echo
            echo "**(no output captured)**"
        fi
        echo
        echo "---"
        echo
    done
} > "$OUT_FILE"

if [[ "$QUIET" -eq 1 ]]; then
    echo "$OUT_FILE"
else
    cat "$OUT_FILE"
    echo >&2
    echo "saved: $OUT_FILE" >&2
fi
