#!/bin/bash
# ntp_island.sh — LAN NTP island: fleet clocks converge to EACH OTHER when
# the WAN is down, instead of every RTC-less Pi drifting alone.
#
# WHY THIS EXISTS (2026-08-27, Hurricane-Lala forensics)
# ------------------------------------------------------
# During the two-week outage moc4 ran ~8 days clock-stale: fake-hwclock
# restored the last-saved time at boot, NTP could not step it with the WAN
# down, and every wall-clock instrument on the box lied TOGETHER — cron
# fired 5 jobs in 4 days, verdict freshness lied, wtmp lied. Time was the
# last WAN-coupled substrate (names were decoupled 07-25 via the /etc/hosts
# block). This is roadmap #1 for the Starlink move:
# .claude/research/lala_outage_recovery_2026_08_27.md
#
# DESIGN
# ------
#   * 2 island SERVERS run chrony: normal ops they follow the distro's WAN
#     pools (so clients get real time through them); when the WAN dies,
#     `local stratum 10 orphan` keeps them serving their own clock, and the
#     orphan option lets the two agree on one leader instead of splitting.
#     Each server also peers with the other island server(s).
#   * CLIENTS keep systemd-timesyncd (no new daemon — footprint rule) and
#     prefer the island servers by their .mf.internal names, which the
#     /etc/hosts fleet block resolves with the uplink down. Public pools
#     stay as FallbackNTP.
#
# OWNERSHIP (honest_failure_modes #8 — the /etc/hosts cloud-init lesson):
# cloud-init lists the `ntp` module, but with no `ntp:` key in user-data it
# writes nothing; if the operator ever adds one, cc_ntp writes its OWN
# drop-in (cloud-init.conf), which sorts after ours and would win per-key —
# deliberate operator config beating this script is the right precedence.
# Verified with the real consumer:
#   sudo cloud-init single --name ntp --frequency always
#
# USAGE (run on the box being configured, with sudo):
#   sudo scripts/ntp_island.sh server-apply "<this-and-peer-server-names>" "<allow-cidrs>"
#   sudo scripts/ntp_island.sh client-apply "<island-server-names>"
#   scripts/ntp_island.sh check
#
# Island names and CIDRs are ARGUMENTS, never baked in (MF014). Examples:
#   sudo scripts/ntp_island.sh server-apply \
#       "<island-a>.mf.internal <island-b>.mf.internal" \
#       "192.0.2.0/24 198.51.100.0/24"
#   sudo scripts/ntp_island.sh client-apply "<island-a>.mf.internal <island-b>.mf.internal"
set -uo pipefail

MODE="${1:-}"
CHRONY_DROPIN=/etc/chrony/conf.d/meshforge-island.conf
TIMESYNCD_DROPIN=/etc/systemd/timesyncd.conf.d/50-meshforge-island.conf
FALLBACK_POOLS="0.debian.pool.ntp.org 1.debian.pool.ntp.org 2.debian.pool.ntp.org 3.debian.pool.ntp.org"

die() { echo "ntp_island: $*" >&2; exit 1; }

name_is_self() {
    # A name is "self" when it resolves to one of this box's addresses.
    # Hostname string-matching is NOT enough: a box's hostname need not
    # textually match its fleet DNS name, and a server peering with itself
    # is a sync loop.
    local ip
    ip="$(getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}')"
    [ -n "$ip" ] || return 1
    hostname -I 2>/dev/null | tr ' ' '\n' | grep -qx "$ip"
}

case "$MODE" in

server-apply)
    SERVERS="${2:-}"; ALLOW="${3:-}"
    [ -n "$SERVERS" ] || die "server-apply needs the island server name list"
    [ -n "$ALLOW" ]   || die "server-apply needs the allow-CIDR list (an island that serves nobody is miswiring)"
    [ "$(id -u)" -eq 0 ] || die "server-apply must run as root"

    if ! command -v chronyd >/dev/null 2>&1; then
        echo "ntp_island: installing chrony (replaces systemd-timesyncd on this box)"
        DEBIAN_FRONTEND=noninteractive timeout 300 apt-get install -y chrony \
            || die "chrony install failed"
    fi

    mkdir -p "$(dirname "$CHRONY_DROPIN")"
    {
        echo "# MeshForge LAN NTP island (scripts/ntp_island.sh server-apply)"
        echo "# Serve LAN clients even when WAN sync is lost; orphan mode picks"
        echo "# one leader among the island servers instead of splitting."
        echo "local stratum 10 orphan"
        for cidr in $ALLOW; do echo "allow $cidr"; done
        for s in $SERVERS; do
            name_is_self "$s" && continue   # a server peering with itself is a sync loop
            echo "server $s iburst"
        done
    } > "$CHRONY_DROPIN"

    systemctl enable chrony >/dev/null 2>&1
    systemctl restart chrony || die "chrony restart failed"
    sleep 3
    echo "--- chronyc tracking ---"
    chronyc tracking | sed -n '1,5p'
    echo "--- island drop-in ---"
    cat "$CHRONY_DROPIN"
    ;;

client-apply)
    SERVERS="${2:-}"
    [ -n "$SERVERS" ] || die "client-apply needs the island server name list"
    [ "$(id -u)" -eq 0 ] || die "client-apply must run as root"
    command -v chronyd >/dev/null 2>&1 && \
        die "this box runs chrony — it is an island SERVER, not a timesyncd client"

    mkdir -p "$(dirname "$TIMESYNCD_DROPIN")"
    {
        echo "# MeshForge LAN NTP island (scripts/ntp_island.sh client-apply)"
        echo "# Island first (resolves via /etc/hosts with the WAN down),"
        echo "# public pools only as fallback."
        echo "[Time]"
        echo "NTP=$SERVERS"
        echo "FallbackNTP=$FALLBACK_POOLS"
    } > "$TIMESYNCD_DROPIN"

    systemctl restart systemd-timesyncd || die "timesyncd restart failed"
    # give SNTP one poll cycle before reading the consumer-of-record
    sleep 5
    timedatectl timesync-status 2>/dev/null | sed -n '1,3p' \
        || timedatectl | grep -E "synchronized|service"
    ;;

check)
    if command -v chronyd >/dev/null 2>&1 && systemctl is-active --quiet chrony; then
        printf "role=server "
        chronyc tracking | awk -F': *' '/^Reference ID|^Stratum|^Leap status/ {printf "%s=%s ", $1, $2}'
        echo
    elif systemctl is-active --quiet systemd-timesyncd; then
        printf "role=client "
        timedatectl timesync-status 2>/dev/null | awk -F': *' '/Server/ {printf "server=%s ", $2}'
        timedatectl 2>/dev/null | awk -F': *' '/synchronized/ {printf "synced=%s", $2}'
        echo
    else
        echo "role=NONE — no active time service (this is a finding, not health)"
        exit 1
    fi
    ;;

*)
    die "usage: ntp_island.sh server-apply|client-apply|check ..."
    ;;
esac
