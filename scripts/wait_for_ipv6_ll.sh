#!/bin/sh
# wait_for_ipv6_ll.sh — block until a usable IPv6 link-local address exists.
#
# WHY: RNS AutoInterface binds IPv6 link-local multicast. If it initialises
# before the kernel has finished Duplicate Address Detection on a real
# interface, the bind fails with [Errno 99] EADDRNOTAVAIL, RNS reports
#   "Could not configure the system interface <dev> for use with
#    AutoInterface[...]"
# and rnsd exits 255/EXCEPTION. systemd's Restart= then recovers it ~5s later.
#
# MEASURED on moc4, 2026-09-10 — and the asymmetry is the whole diagnosis:
#   boot 19:28 warm reboot  -> 1 rnsd failure
#   boot 18:55 warm reboot  -> 1 rnsd failure
#   boot 18:52 warm reboot  -> 1 rnsd failure
#   boot 15:17 COLD power-on -> 0 failures
# A cold boot is slow enough that DAD completes before rnsd starts; a warm
# reboot is not. `network-online.target` does NOT cover this — it can be
# reached while an interface's fe80 address is still `tentative`.
#
# This matters more than one retry suggests: the duty-cycle arc reboots boxes
# routinely, and every one of those currently burns a failed start plus a 5s
# gap in RNS reachability.
#
# FAIL-OPEN, deliberately. rnsd runs fine on other interface types (TCP,
# RNode, Meshtastic), so a box with no IPv6 at all must still start it —
# refusing would be a REGRESSION against today's behaviour, which recovers on
# its own. After the budget this exits 0 anyway and prints a loud witness, so
# a gate that did not help leaves a trace instead of failing silently
# (honest_failure_modes #9: every swallow gets a witness).
#
# Usage: wait_for_ipv6_ll.sh [budget_seconds]   (default 30)
set -u

BUDGET="${1:-30}"
i=0

ll_ready() {
    # A non-loopback, UP interface carrying an fe80 address that is no longer
    # `tentative`. Loopback is excluded because ::1/fe80 on lo is always there
    # and would make the gate pass instantly without proving anything.
    ip -6 -o addr show scope link up 2>/dev/null \
        | grep -v ' lo ' \
        | grep 'fe80:' \
        | grep -v 'tentative' \
        | grep -q .
}

while [ "$i" -lt "$BUDGET" ]; do
    if ll_ready; then
        [ "$i" -gt 0 ] && echo "wait_for_ipv6_ll: link-local ready after ${i}s" >&2
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

echo "wait_for_ipv6_ll: TIMEOUT after ${BUDGET}s — no non-tentative fe80 on any" \
     "non-loopback interface. Proceeding anyway (fail-open). rnsd will start and" \
     "AutoInterface may fail with EADDRNOTAVAIL exactly as before, then Restart=" \
     "recovers it. THIS LINE IS THE WITNESS that the gate ran and did not help —" \
     "if you see it repeatedly, the interface is not coming up at all and the" \
     "problem is the network, not the race." >&2
exit 0
