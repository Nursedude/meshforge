#!/bin/sh
# meshtasticd_mudp_guard.sh — self-heal the Meshtastic UDP-multicast leg.
#
# Why this exists (2026-06-24): the mains-outage reboot restarted meshtasticd
# before its UDP-multicast leg could bind, so `udp 224.0.0.69:4403`
# (Meshtastic-over-IP multicast — the source feeding Raven's "meshforge"
# channel on the QTH-hAP) never came up while meshtasticd still showed
# "active". The channel went dark for ~13h, silent. This guard detects that
# exact symptom (daemon active, multicast socket absent) and restarts
# meshtasticd once, with a cooldown so a genuinely-broken leg can't loop.
#
# FLEET-SAFE BY AUTO-LEARN: a box only gets "guarded" once it has been seen
# with the leg actually bound (last-seen marker, 7-day TTL). Boxes that never
# enable UDP multicast (`network.enabledProtocols` without the UDP bit) never
# bind 224.0.0.69:4403, never get a marker, and are left strictly alone — so
# this unit can be enabled fleet-wide without restart-looping a non-MUDP box.
#
# Deployed via templates/systemd/meshtasticd-mudp-guard.{service,timer};
# enabled per role in docs/fleet_roles.yaml. Remove the box's marker to
# de-guard it: rm /var/lib/meshforge/mudp-last-seen
set -eu

SVC=meshtasticd.service
STATE_DIR=/var/lib/meshforge
MARKER="$STATE_DIR/mudp-last-seen"     # epoch when the leg was last seen bound
RESTAMP="/run/meshtasticd-mudp-guard.last"  # epoch of last self-heal restart
COOLDOWN=600                            # min seconds between self-heal restarts
LEARN_TTL=604800                        # 7d: stop guarding if MUDP gone this long
GROUP='224\.0\.0\.69:4403'

now=$(date +%s)

# Only our business if the daemon claims to be up.
[ "$(systemctl is-active "$SVC" 2>/dev/null)" = active ] || exit 0

# Healthy = the multicast socket is bound. Learn it and leave.
if ss -uan 2>/dev/null | grep -q "$GROUP"; then
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    echo "$now" > "$MARKER" 2>/dev/null || true
    exit 0
fi

# Leg is NOT bound. Decide whether this box is supposed to have it.
seen=$(cat "$MARKER" 2>/dev/null || echo 0)
case "$seen" in *[!0-9]*) seen=0 ;; esac
if [ "$seen" -eq 0 ] || [ $((now - seen)) -gt "$LEARN_TTL" ]; then
    # Never observed (non-MUDP box) or MUDP absent past the TTL — leave alone.
    exit 0
fi

# MUDP is expected here but the socket is gone -> self-heal, with cooldown.
last=$(cat "$RESTAMP" 2>/dev/null || echo 0)
case "$last" in *[!0-9]*) last=0 ;; esac
if [ $((now - last)) -ge "$COOLDOWN" ]; then
    logger -t mudp-guard "224.0.0.69:4403 NOT bound but $SVC active (last-seen $((now - seen))s ago) -> restarting $SVC"
    echo "$now" > "$RESTAMP" 2>/dev/null || true
    systemctl restart "$SVC"
else
    logger -t mudp-guard "224.0.0.69:4403 still down; within ${COOLDOWN}s cooldown, not restarting"
fi
