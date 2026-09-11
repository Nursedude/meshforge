#!/bin/bash
# fleet_posture_sync.sh — put the declared power posture ON the boxes that
# have to act on it.
#
# WHY THIS EXISTS (2026-09-10)
# ----------------------------
# The posture SSOT (~/.config/meshforge/fleet_posture.json) shipped 2026-09-01
# with nine consumers, and every one of them ran on the MANAGER. The design
# named the hole on day one (field_ecomm_and_dutycycle_fleet.md, Decision 3,
# attack #4): *if the declaration lives only on the manager and the manager is
# Tier-2, every other box loses the declaration exactly when it needs it.*
#
# Two things follow, and only the second needs a network:
#   * each box's own copy governs ITS judgments — watchdogs and mini run per
#     box and may not be able to reach the manager at all during a storm;
#   * so the declaration is a storm-PREP artifact, like the vault capture: it
#     is pushed BEFORE the power event, never during it.
#
# ⚠️ THE AUDIENCE IS THE SURVIVORS, NOT THE TARGETS. A box being powered down
# does not need to be told it is dormant — it is about to be dark. The boxes
# that need the document are the ones STAYING UP, because they are the ones
# whose peer-facing detectors would otherwise page about an absence the
# operator declared on purpose. Boxes already declared dormant/detached are
# therefore SKIPPED and said, not silently dropped.
#
# HOW IT DIFFERS FROM fleet_registry_sync.sh (same pattern, three inversions)
#   1. A box with NO posture file is SEEDED, not skipped. For the naming
#      registry, absence means "a human has not set this box up" and seeding
#      is a human decision. Here absence means UNDECLARED, which means "watch
#      and page everything" — the page storm this whole arc exists to end. The
#      absent case is exactly the case that needs the file.
#   2. CLEARING PROPAGATES. When the manager's document goes empty (the
#      resting state after `fleet_power.py resume`), that empty document is
#      pushed too. A mirror that only ever learns about declarations, never
#      about their end, is how a box stays silent about a peer that came back.
#   3. NO MANAGER FILE AT ALL = remove the remote copies (timestamped .bak
#      kept). Absent is the safe state: every box watched, exactly as before
#      this arc. Leaving stale copies behind would let a finished storm go on
#      silencing the fleet.
#
# THE MIRROR STAMP, and why a copy is not the original. Every distributed copy
# carries `"mirror": {"from": <manager>, "at": <iso>}`; the manager's own file
# never does. utils.fleet_posture reads that stamp and holds a MIRROR to a
# stricter rule than the authoritative document: a mirror whose reader cannot
# confirm its own clock silences NOTHING and says so. On the manager, HOLD
# (keep the declaration past `until` rather than silently lift it) is right —
# there is an operator and an authoritative file. On an RTC-less Pi restoring
# a stale time from fake-hwclock, there is neither, and an unverifiable window
# must not be allowed to mute a fleet indefinitely. Over-paging is recoverable;
# a fleet that quietly stopped reporting is not.
#
# HONEST FAILURE MODES
#   * UNREACHABLE is named, and the verdict is never OK — unobservable is not
#     synced (#2). That box keeps whatever copy it had; its own reader's
#     staleness rules are the backstop, not this script's optimism.
#   * The landed artifact is VERIFIED by re-hashing it on the remote after the
#     swap, never by trusting scp/ssh exit codes (calibrated_claims #7).
#   * --check writes NOTHING anywhere and reports drift only.
#
# ⚠️ NOT CRON-WIRED, deliberately. `.claude/rules/harness_restraint.md` is in
# force until 2026-10-09; a new verdict-wired cron is a new watched organ and
# this arc does not need one. The mirror runs where it is actually load-bearing
# — inside `fleet_power.py down`, between the confirmed declaration and the
# first poweroff — and by hand otherwise. Wiring it to a timer is a separate,
# deliberate decision for after the freeze.
#
# Usage (manager only):
#   scripts/fleet_posture_sync.sh            # push to every active box
#   scripts/fleet_posture_sync.sh --check    # report drift, change nothing
#   scripts/fleet_posture_sync.sh --quiet    # one summary line (the fleet_power call)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POSTURE="${MESHFORGE_FLEET_POSTURE:-$HOME/.config/meshforge/fleet_posture.json}"
# Remote-relative destination. Overridable so a drill can aim at a throwaway
# path on a throwaway host instead of the real one (guard_drill doctrine).
POSTURE_REL="${FLEET_POSTURE_SYNC_REL:-.config/meshforge/fleet_posture.json}"
# Env overrides so the skip / unreachable / seed legs can be DRILLED against a
# planted host list without touching the real fleet (guard_drill doctrine).
HOSTS_FILE="${FLEET_POSTURE_SYNC_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

CHECK=0; QUIET=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '1,60p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

say() { [ "$QUIET" -eq 1 ] || echo "$1"; }

if [ ! -f "$HOSTS_FILE" ]; then
    echo "fleet_posture_sync: no fleet_hosts at $HOSTS_FILE — this organ runs on the manager only" >&2
    exit 2
fi

# Read the declaration through the ONE reader so this script and every Python
# consumer can never disagree about which boxes are silent (hfm #5).
if [ -f "$HERE/lib/fleet_posture.sh" ]; then
    . "$HERE/lib/fleet_posture.sh"
    fleet_posture_read "$(dirname "$HERE")"
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
STAGE=""
MODE="distribute"

if [ ! -f "$POSTURE" ]; then
    MODE="withdraw"
    say "no posture file on the manager ($POSTURE) — WITHDRAWING remote copies"
    say "  (absent = undeclared = every box watched, which is the safe state)"
else
    # Stamp the copy as a MIRROR. Done here, not on the box, so the remote
    # never has to be trusted to label its own provenance.
    STAGE="${TMPDIR:-/tmp}/fleet_posture_mirror.$$.json"
    stamp_out=$(PYTHONPATH="$(dirname "$HERE")/src" python3 \
        "$HERE/fleet_posture_stamp.py" "$POSTURE" "$STAGE" "$(hostname)" 2>&1)
    stamp_rc=$?
    if [ $stamp_rc -ne 0 ]; then
        # The refusal reason is the point -- never swallowed, never --quiet'd.
        echo "fleet_posture_sync: REFUSING to distribute:" >&2
        printf '%s\n' "$stamp_out" >&2
        rm -f "$STAGE"
        exit 2
    fi
    say "$stamp_out"
    local_hash=$(md5sum "$STAGE" | awk '{print $1}')
fi
trap '[ -n "$STAGE" ] && rm -f "$STAGE"' EXIT

ok_boxes=() healed=() seeded=() withdrawn=() unreachable=() failed=() skipped=() drift=()

while IFS= read -r host <&3; do
    host="${host%%#*}"; host="$(echo "$host" | tr -d '[:space:]')"
    [ -z "$host" ] && continue

    # A box that is itself declared dormant/detached is the TARGET of the
    # declaration, not its audience — it is going dark (or is push-only by
    # definition). Named, never silently dropped.
    if fleet_posture_is_silent "$host" 2>/dev/null; then
        skipped+=("$host")
        say "$host: declared $(fleet_posture_state "$host") — not an audience for the mirror (it is the subject)"
        continue
    fi

    remote=$(ssh "${SSH_OPTS[@]}" "$host" "md5sum $POSTURE_REL 2>/dev/null" 2>/dev/null)
    if [ -z "$remote" ]; then
        if ! ssh "${SSH_OPTS[@]}" "$host" true 2>/dev/null; then
            unreachable+=("$host")
            say "$host: UNREACHABLE — its copy is UNKNOWN, not synced"
            continue
        fi
        remote_hash=""      # reachable, no posture file there yet
    else
        remote_hash=$(echo "$remote" | awk '{print $1}')
    fi

    if [ "$MODE" = "withdraw" ]; then
        if [ -z "$remote_hash" ]; then
            ok_boxes+=("$host"); continue          # already absent = already right
        fi
        if [ "$CHECK" -eq 1 ]; then
            drift+=("$host(stale-copy)"); say "$host: has a copy the manager no longer declares"; continue
        fi
        if ssh "${SSH_OPTS[@]}" "$host" \
             "mv $POSTURE_REL $POSTURE_REL.bak-$stamp" 2>/dev/null \
           && ! ssh "${SSH_OPTS[@]}" "$host" "test -f $POSTURE_REL" 2>/dev/null; then
            withdrawn+=("$host")
            say "$host: withdrawn (old copy at $POSTURE_REL.bak-$stamp)"
        else
            failed+=("$host")
            say "$host: withdraw FAILED — a stale declaration may still be in force there"
        fi
        continue
    fi

    if [ "$remote_hash" = "$local_hash" ]; then
        ok_boxes+=("$host"); continue
    fi
    if [ "$CHECK" -eq 1 ]; then
        if [ -z "$remote_hash" ]; then
            drift+=("$host(absent)"); say "$host: NO posture copy — would be seeded"
        else
            drift+=("$host(stale)");  say "$host: copy differs from the manager's — would be healed"
        fi
        continue
    fi

    # Stage, swap, then VERIFY the artifact that actually landed.
    if scp -q "${SSH_OPTS[@]}" "$STAGE" "$host:$POSTURE_REL.new-$stamp" 2>/dev/null \
       && ssh "${SSH_OPTS[@]}" "$host" \
            "mkdir -p $(dirname $POSTURE_REL); [ -f $POSTURE_REL ] && cp $POSTURE_REL $POSTURE_REL.bak-$stamp; mv $POSTURE_REL.new-$stamp $POSTURE_REL" 2>/dev/null; then
        verify=$(ssh "${SSH_OPTS[@]}" "$host" "md5sum $POSTURE_REL" 2>/dev/null | awk '{print $1}')
        if [ "$verify" = "$local_hash" ]; then
            if [ -z "$remote_hash" ]; then seeded+=("$host"); say "$host: seeded"
            else healed+=("$host"); say "$host: healed $remote_hash -> $local_hash"; fi
        else
            failed+=("$host")
            say "$host: swap ran but the landed hash '$verify' != '$local_hash'"
        fi
    else
        failed+=("$host")
        say "$host: transfer/swap failed — it does NOT have the declaration"
    fi
done 3< "$HOSTS_FILE"

join() { local IFS=,; echo "$*"; }
summary="mode=$MODE ok=${#ok_boxes[@]}"
[ ${#seeded[@]} -gt 0 ]      && summary+=" seeded=$(join "${seeded[@]}")"
[ ${#healed[@]} -gt 0 ]      && summary+=" healed=$(join "${healed[@]}")"
[ ${#withdrawn[@]} -gt 0 ]   && summary+=" withdrawn=$(join "${withdrawn[@]}")"
[ ${#skipped[@]} -gt 0 ]     && summary+=" subject_boxes=$(join "${skipped[@]}")"
[ ${#drift[@]} -gt 0 ]       && summary+=" DRIFT=$(join "${drift[@]}")"
[ ${#unreachable[@]} -gt 0 ] && summary+=" UNOBSERVABLE=$(join "${unreachable[@]}")"
[ ${#failed[@]} -gt 0 ]      && summary+=" FAILED=$(join "${failed[@]}")"

echo "fleet_posture_sync: $summary"

# Exit codes are for fleet_power.py to act on, not for a cron verdict:
#   0 every audience box carries the manager's declaration
#   1 something did not land / could not be observed — LOUD, never fatal to a
#     shutdown (the manager-side consumers still work; the cost is that
#     off-manager detectors will page about a declared absence)
#   3 --check found drift
if [ ${#failed[@]} -gt 0 ] || [ ${#unreachable[@]} -gt 0 ]; then exit 1; fi
if [ ${#drift[@]} -gt 0 ]; then exit 3; fi
exit 0
