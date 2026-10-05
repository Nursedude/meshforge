#!/bin/sh
# wifi_reconnect_guard.sh — re-activate a Wi-Fi profile NetworkManager gave up on.
#
# Why this exists (lehua, 2026-10-04): a 3-minute weak-signal blip (14:30-14:33,
# ASSOC-REJECT status 16) ended with NetworkManager logging
# `association took too long` -> `failed (reason 'no-secrets')`. A no-secrets
# failure blocks autoconnect until an agent supplies secrets — on a headless box
# that is forever. NM wrote ZERO lines from 14:35 until the operator rebooted the
# box at 16:21: a 3-minute RF fault became a 2-hour outage.
#
# Acts ONLY on NM device state `disconnected` (the give-up state), and ONLY
# when no OTHER device carries the default route (the box is offline). `unavailable`
# (radio off / rfkill), `connecting`, `unmanaged` are left alone — a deliberate
# "Wi-Fi off" must never be fought. Needs an autoconnect=yes wifi profile bound
# to that device (or unbound). Outage age uses the MONOTONIC clock
# (/proc/uptime), so RTC-less boot time-steps cannot forge it; the marker lives
# in /run, so it never survives a reboot. Every heal leaves a witness line.
#
# Deployed via templates/systemd/meshforge-wifi-guard.{service,timer}, hand-
# enabled per box in docs/fleet_roles.yaml (lehua first).
set -eu

GRACE=${WIFI_GUARD_GRACE_S:-300}          # disconnected this long -> heal
COOLDOWN=${WIFI_GUARD_COOLDOWN_S:-120}    # min seconds between heals per device
STATE=${WIFI_GUARD_STATE:-/run/meshforge-wifi-guard}
UPTIME_FILE=${WIFI_GUARD_UPTIME_FILE:-/proc/uptime}
WITNESS=${WIFI_GUARD_WITNESS:-/var/lib/meshforge/wifi-guard-heals}

command -v nmcli >/dev/null 2>&1 || exit 0
mkdir -p "$STATE"
now=$(cut -d. -f1 "$UPTIME_FILE")

num() { v=$(cat "$1" 2>/dev/null || echo ""); case "$v" in ''|*[!0-9]*) echo "";; *) echo "$v";; esac; }

nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | while IFS=: read -r dev type st; do
    [ "$type" = wifi ] || continue
    down="$STATE/$dev.down"
    if [ "$st" != disconnected ]; then
        rm -f "$down"; continue
    fi
    since=$(num "$down")
    # first sighting, or a marker from "the future" (clock went backward): restart the window
    if [ -z "$since" ] || [ "$since" -gt "$now" ]; then
        echo "$now" > "$down"; continue
    fi
    [ $((now - since)) -ge "$GRACE" ] || continue
    # Only an OFFLINE box is our business. Measured 2026-10-04: eight fleet
    # boxes idle at wlan0 `disconnected` while eth0 carries the default route —
    # bringing Wi-Fi up beside a working uplink would change their routing.
    if ip route show default 2>/dev/null | grep -v " dev $dev " | grep -q "^default"; then
        continue
    fi
    last=$(num "$STATE/$dev.last")
    if [ -n "$last" ] && [ "$last" -le "$now" ] && [ $((now - last)) -lt "$COOLDOWN" ]; then
        continue
    fi
    con=""
    for c in $(nmcli -t -f NAME,TYPE,AUTOCONNECT connection show 2>/dev/null \
               | awk -F: '$2=="802-11-wireless" && $3=="yes" {print $1}'); do
        ifn=$(nmcli -g connection.interface-name connection show "$c" 2>/dev/null || echo "")
        if [ -z "$ifn" ] || [ "$ifn" = "$dev" ]; then con=$c; break; fi
    done
    [ -n "$con" ] || continue
    echo "$now" > "$STATE/$dev.last"
    logger -t wifi-guard "$dev disconnected $((now - since))s (NM gave up) -> nmcli connection up $con"
    if timeout 60 nmcli connection up "$con" ifname "$dev" >/dev/null 2>&1; then rc=0; else rc=$?; fi
    mkdir -p "$(dirname "$WITNESS")" 2>/dev/null || true
    echo "$(date -Is) uptime=$now dev=$dev con=$con down_s=$((now - since)) rc=$rc" >> "$WITNESS" 2>/dev/null || true
    logger -t wifi-guard "$dev heal attempt rc=$rc"
done
exit 0
