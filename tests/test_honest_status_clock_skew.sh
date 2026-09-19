#!/usr/bin/env bash
# honest_status.sh — the running-code skew leg must not date a unit against a
# clock it cannot trust (2026-09-18).
#
# WHY: `B <repo> <unit> <days>` means "this unit started BEFORE its repo's
# newest code commit". That compares the COMMIT's wall clock against the
# BOX's wall clock at fork, and says something only while the two agree. On
# this fleet they do not -- RTC-less Pis, fake-hwclock restoring a stale time
# at boot, NTP unreachable through a WAN outage; moc4 ran ~8 days behind for
# days. Every attributed unit on such a box prints `B ... 8d`, and that 8 IS
# THE CLOCK SKEW wearing the name of deploy lag: a confident, specific, wrong
# number on the check of record.
#
# Unlike fleet_sync's decider -- which runs ON the box with only one clock to
# ask -- this leg runs on the MANAGER, so it has a SECOND clock and can catch
# skew in EITHER direction, including the false-GREEN one (a box AHEAD reads
# every unit `current`).
#
# Drives the REAL decision block extracted from the script, with a fabricated
# body, so the assertions are about behavior rather than source text.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
# Overridable so this pin can itself be DRILLED against a planted-violation
# copy (feedback_a_guard_that_never_failed_is_not_evidence).
SCRIPT="${HS_CLOCK_SCRIPT:-$HERE/../scripts/honest_status.sh}"
LIB="$HERE/../scripts/lib/code_paths.sh"
fails=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

[ -r "$LIB" ] || { echo "FAIL missing $LIB"; exit 1; }
. "$LIB"

# --- extract ONLY the clock decision block --------------------------------
# NOT the whole script: sourcing honest_status.sh RUNS A FLEET-WIDE PROBE.
BLOCK="$TMP/clock_block.sh"
awk '/^  box_clock_delta=""$/{f=1} f&&/^  skew_prose=/{exit} f{print}' "$SCRIPT" > "$BLOCK"
if [ ! -s "$BLOCK" ]; then
    echo "FAIL could not extract the clock decision block from $SCRIPT"
    echo "SOME FAILED (1 assertion(s))"; exit 1
fi

# $1=label $2=seconds the BOX clock differs from ours (or the literal word
# `none` to omit the line entirely) $3=nb $4=nu
# $5=expect behind $6=expect unknown $7=expect clock-flagged boxes
run_clock() {
    local label="$1" off="$2" b body
    local nb="$3" nu="$4"
    local skew_clock=0 skew_clock_desc="" skew_behind=0 skew_unknown=0
    b="testbox"
    if [ "$off" = none ]; then body="HSUP"
    else body="BOXNOW $(( $(date +%s) + off ))"; fi
    . "$BLOCK"
    local ok=1
    [ "$skew_behind"  = "$5" ] || ok=0
    [ "$skew_unknown" = "$6" ] || ok=0
    [ "$skew_clock"   = "$7" ] || ok=0
    if [ "$ok" = 1 ]; then
        echo "ok   $label behind=$skew_behind unknown=$skew_unknown clock=$skew_clock"
    else
        echo "FAIL $label expected behind=$5 unknown=$6 clock=$7, got behind=$skew_behind unknown=$skew_unknown clock=$skew_clock"
        fails=$((fails+1))
    fi
}

# Agreeing clocks: the leg must behave exactly as it always did. If this one
# breaks, the gate starts reporting UNKNOWN for a healthy fleet and gets
# ignored -- strictly worse than the bug being fixed.
run_clock "clocks-agree"        0        6 2   6 2 0
run_clock "jitter-tolerated"    5        6 2   6 2 0

# Box BEHIND (the moc4 shape): those 6 are NOT behind, they are undatable.
# Reclassified into unknown, never dropped -- dropping shrinks coverage
# silently, which is the failure this whole leg exists to end.
run_clock "box-8-days-behind"   -691200  6 2   0 8 1

# Box AHEAD: the FALSE-GREEN direction. Nothing in the B/P/U buckets would
# have said a word -- every unit reads `current` -- and this is the tell that
# fleet_sync's on-box decider structurally cannot see.
run_clock "box-8-days-ahead"    691200   6 2   0 8 1

# ABSENT is not agreement. Control only reaches this block after the payload
# answered, so a missing BOXNOW means `date` failed on that box -- a broken
# clock, the very thing being asked. Reading it as "clocks agree" would be
# the degraded-value-overlaps-healthy trap (hfm #1) inside its own fix.
run_clock "boxnow-absent"       none     6 2   0 8 1

# --- source pins: the parts a behavioural case cannot reach ----------------
# The payload must actually SEND the clock, or the block above is inert on
# every box at once.
if grep -q 'echo BOXNOW' "$SCRIPT"; then
    echo "ok   the remote payload reports the box clock"
else
    echo "FAIL remote payload never emits BOXNOW — the clock block is inert"
    fails=$((fails+1))
fi
# ONE tolerance, shared with fleet_sync via lib/code_paths.sh. A second
# hardcode here is how 712ac491 happened (hfm #5).
if grep -q 'MF_CLOCK_TOL_S' "$SCRIPT" && ! grep -qE '_ad" -gt "?[0-9]+' "$SCRIPT"; then
    echo "ok   tolerance comes from the shared constant, not a local hardcode"
else
    echo "FAIL the clock tolerance is hardcoded here instead of MF_CLOCK_TOL_S"
    fails=$((fails+1))
fi
# The disclosure must ride EVERY outcome line, including the clean one --
# same reason prose_note does. "No unit is behind" is not a true sentence on
# a box whose clock could not be read.
if [ "$(grep -c '${clock_note}' "$SCRIPT")" -ge 2 ]; then
    echo "ok   the clock disclosure rides both outcome lines"
else
    echo "FAIL clock_note is missing from an outcome line — a clean read would hide it"
    fails=$((fails+1))
fi

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "SOME FAILED ($fails assertion(s))"
exit 1
