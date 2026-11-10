#!/bin/sh
# wait_for_ntp_sync.sh — hold meshtasticd's start until the system clock is
# NTP-synchronized, so meshtasticd rates its clock high enough to stamp packets.
#
# WHY (measured 2026-10-02): portduino meshtasticd asks ONCE, at startup,
#   `timedatectl status | grep synchronized | grep yes -c`
# (firmware src/main.cpp, v2.7.26). Not synced → RTCQualityDevice, below the
# RTCQualityFromNet that Router.cpp requires for `rx_time = getValidTime(...)`.
# So every received packet carries rx_time 0, NodeDB::updateFrom never moves
# `last_heard`, and the node DB reads EVERY node offline for the life of the
# process. Nothing re-checks: moc4 ran 5.8 days at 0 of 374 online (started
# 15 s after boot), moc2 at 0 of 6 (7 s after boot), while lehua (25 s) won
# the race. A restart once NTP was synced flipped moc4 to 10 online within
# 4 minutes, nodes heard 10 s before.
#
# The predicate below is meshtasticd's own command, on purpose: the gate and
# its consumer must not disagree about what "synchronized" means
# (honest_failure_modes #5).
#
# FAIL-OPEN, deliberately. A box with no WAN (field kit, Starlink outage) must
# still get its radio: refusing to start would trade a stale node list for no
# mesh at all. After the budget this exits 0 and prints a WITNESS that names
# the consequence and the fix (honest_failure_modes #9). This only SHRINKS the
# window; an offline boot still starts meshtasticd at Device quality.
#
# Usage: wait_for_ntp_sync.sh [budget_seconds]   (default 60)
set -u

BUDGET="${1:-60}"
i=0

# LINGER after speaking (2026-11-09, Fable 5.2 review r2 F1): journald reads
# a line's trusted _COMM from /proc/<pid> LAZILY, and the watchdog's
# meshtasticd_clock_unrated probe authenticates this gate's lines by that
# _COMM. Exiting right after `echo` lost it on 22/30 and 11/30 lines (0 s
# path, systemd-cat drill); a 0.3 s linger kept 30/30. 1 s is margin for a
# loaded boot — it delays meshtasticd's start by 1 s.
LINGER=1

synced() {
    [ "$(timedatectl status 2>/dev/null | grep synchronized | grep -c yes)" = "1" ]
}

while [ "$i" -lt "$BUDGET" ]; do
    if synced; then
        # ALWAYS speak (2026-11-09), even at 0 s: a start whose journal holds no
        # gate line then means the gate did NOT run — the watchdog's
        # meshtasticd_clock_unrated probe reads that absence as unknown.
        echo "wait_for_ntp_sync: clock synchronized after ${i}s" >&2
        sleep "$LINGER" 2>/dev/null
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

# 2026-11-09: the consequence is a RISK, not a certainty — lehua's gate timed
# out (10-26) and its radio read 17 -> 33 nodes online from the first telemetry
# line. The watchdog's meshtasticd_clock_unrated probe reads THIS line (and the
# telemetry below, labelled unverified) — OBSERVE-ONLY: it reports, never pages.
echo "wait_for_ntp_sync: TIMEOUT after ${BUDGET}s — system clock not NTP-synchronized." \
     "Starting meshtasticd anyway (fail-open). THIS LINE IS THE WITNESS: meshtasticd" \
     "may rate its clock Device quality, stamp received packets with rx_time 0, and" \
     "freeze every node's lastHeard (all read offline) until it restarts. CHECK:" \
     "its DeviceTelemetry lines read num_online_nodes=0 while it logs '[Router] Received'." \
     "FIX once 'timedatectl' shows synchronized: sudo systemctl restart meshtasticd." >&2
sleep "$LINGER" 2>/dev/null
exit 0
