#!/bin/bash
# fleet_registry_sync.sh — daily manager-side organ: distribute the fleet
# naming registry (~/.config/meshforge/fleet_naming.json) to every fleet box,
# WITHOUT going quiet about what it changed.
#
# WHY THIS EXISTS (2026-08-15)
# ----------------------------
# The registry is an operator-values file (MF014, never committed), carried
# per-box, and until today had NO distribution mechanism — copies drifted
# apart by hand-edit. Measured cost: moc5's Hurricane-Lala front move
# (.32->.27, 2026-08-14) was fixed in the manager's copy the same night, but
# the other EIGHT boxes kept the stale ip_fallback and all FAILed their
# fleet_naming_drift cron the next morning; a moc3 note edited 07-26 had
# silently never propagated at all. One value, eight simultaneous pages.
#
# SSOT: the MANAGER's copy. Edit the registry on the manager box; this organ
# fans it out. An edit made on any other box is DRIFT — it gets backed up
# (timestamped .bak) and overwritten, and the CONCERN verdict names the box,
# so a legitimate remote edit is preserved and surfaced, never silently lost.
#
# WHY A HEAL IS REPORTED CONCERN, NOT OK (same doctrine as
# fleet_hosts_selfheal.sh): a repair that reports OK destroys the drift
# signal — a box whose copy churns daily would look identical to a stable
# fleet. CONCERN self-clears on the next run once the fleet is stable.
#
# HONEST FAILURE MODES
#   * An UNREACHABLE box is reported by name and the verdict is CONCERN at
#     best — unobservable != synced (#2). Its own daily fleet_naming_drift
#     cron still guards it independently.
#   * A box with NO registry file is a named skip, never a silent pass —
#     an absence must be explained (#3). Seeding a first copy is a human
#     decision, not this script's.
#   * The heal is VERIFIED by re-hashing the remote artifact after the swap,
#     never by trusting scp/mv exit codes (calibrated_claims #7).
#   * Every leg leaves its line in the run log; the verdict message carries
#     the per-box outcome so FAIL(cause) is greppable (#9).
#
# Host list: ~/.config/meshforge/fleet_hosts (the manager-only ssh SSOT, same
# list fleet_pull.sh walks). Running this anywhere without that file is
# miswiring and FAILs loudly.
#
# Crontab idiom (manager only; script writes its own verdict — the || only
# catches the case where it never got far enough to write one). Runs BEFORE
# the per-box 06:1x drift-check crons so a manager-side edit propagates the
# same morning it lands:
#   45 5 * * * /opt/meshforge/scripts/fleet_registry_sync.sh \
#     >> ~/.local/state/meshforge/fleet_registry_sync.log 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh fleet_registry_sync FAIL wrapper_crashed

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERDICT="$HERE/cron_verdict.sh"
NAME=fleet_registry_sync
REG="$HOME/.config/meshforge/fleet_naming.json"
REG_REL=".config/meshforge/fleet_naming.json"
# Env override exists so the skip/unreachable legs can be DRILLED against a
# planted host list (guard_drill doctrine) without touching the real one.
HOSTS_FILE="${FLEET_REGISTRY_SYNC_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"
LOCK="${FLEET_REGISTRY_SYNC_LOCK:-${TMPDIR:-/tmp}/fleet_registry_sync.lock}"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

say() { "$VERDICT" "$NAME" "$1" "$2"; }

# Serialize against a concurrent manual run (honest_failure_modes #8); a
# wedged lock degrades to running anyway — a skipped day is worse.
exec 9>"$LOCK" 2>/dev/null || true
flock -w 30 9 2>/dev/null || true

if [ ! -f "$HOSTS_FILE" ]; then
    say FAIL "no fleet_hosts at $HOSTS_FILE — this organ runs on the manager only"
    exit 0
fi
if [ ! -f "$REG" ]; then
    say FAIL "manager registry missing: $REG — nothing to distribute"
    exit 0
fi

# Re-derive each box's declared role INTO the manager's registry before the
# fan-out (2026-09-02). The federation collector reads `serves_map` from the
# registry to skip peers whose role runs no map, so the stamp must be refreshed
# by the organ that distributes it — a one-time manual stamp would silently go
# stale the first time a box changes role, and a stale FALSE is the dangerous
# direction (a box that GAINED a map would stay unpolled).
#
# Best-effort by design: this organ's job is distribution, and a stamp failure
# must not stop the fan-out. fleet_role_stamp holds a prior stamp for an
# unreachable box and never invents one, so the worst case here is a registry
# that is merely older than today — not a wrong one. Its own output lands in
# the run log either way (#9).
if [ -x "$HERE/fleet_role_stamp.py" ] || [ -f "$HERE/fleet_role_stamp.py" ]; then
    role_out=$(PYTHONPATH="$(dirname "$HERE")/src" timeout 300 \
        python3 "$HERE/fleet_role_stamp.py" --apply 2>&1) || true
    echo "$role_out"
    role_line=$(printf '%s\n' "$role_out" | tail -1)
else
    role_line="role stamp: script absent"
fi

local_hash=$(md5sum "$REG" | awk '{print $1}')
stamp=$(date -u +%Y%m%dT%H%M%SZ)

ok_boxes=() healed=() skipped=() unreachable=() failed=() dormant=()
# Declared posture (2026-09-01, DORMANT arc): a box switched OFF on purpose
# is reported as dormant, never as UNREACHABLE — an expected absence must
# not drive this organ to CONCERN for a whole storm. Absent/broken
# declaration = every box is checked, as before.
if [ -f "$HERE/lib/fleet_posture.sh" ]; then
    . "$HERE/lib/fleet_posture.sh"
    fleet_posture_read "$(dirname "$HERE")"
fi

# Loop input rides fd 3: ssh/scp inside the loop read stdin and would
# otherwise swallow the rest of the host list (first live run processed
# exactly ONE box and reported it honestly — the verdict caught its own bug).
while IFS= read -r host <&3; do
    host="${host%%#*}"; host="$(echo "$host" | tr -d '[:space:]')"
    [ -z "$host" ] && continue
    if fleet_posture_is_silent "$host" 2>/dev/null; then
        dormant+=("$host")
        echo "$host: declared $(fleet_posture_state "$host") — skipped (an expected absence, not unreachable)"
        continue
    fi

    remote=$(ssh "${SSH_OPTS[@]}" "$host" "md5sum $REG_REL 2>/dev/null" 2>/dev/null)
    rc=$?
    if [ $rc -ne 0 ] && [ -z "$remote" ]; then
        # ssh transport itself may have failed, or the file may be absent.
        # Distinguish: can we reach the box at all?
        if ssh "${SSH_OPTS[@]}" "$host" true 2>/dev/null; then
            skipped+=("$host")
            echo "$host: no registry file — skipped (seeding is a human decision)"
        else
            unreachable+=("$host")
            echo "$host: UNREACHABLE — state unknown"
        fi
        continue
    fi

    remote_hash=$(echo "$remote" | awk '{print $1}')
    if [ "$remote_hash" = "$local_hash" ]; then
        ok_boxes+=("$host")
        continue
    fi

    # Drift: back up the remote copy, stage, swap, then VERIFY the artifact.
    if scp -q "${SSH_OPTS[@]}" "$REG" "$host:$REG_REL.new-$stamp" 2>/dev/null \
       && ssh "${SSH_OPTS[@]}" "$host" \
            "cp $REG_REL $REG_REL.bak-$stamp && mv $REG_REL.new-$stamp $REG_REL" 2>/dev/null; then
        verify=$(ssh "${SSH_OPTS[@]}" "$host" "md5sum $REG_REL" 2>/dev/null | awk '{print $1}')
        if [ "$verify" = "$local_hash" ]; then
            healed+=("$host")
            echo "$host: healed $remote_hash -> $local_hash (old copy at $REG_REL.bak-$stamp)"
        else
            failed+=("$host")
            echo "$host: swap ran but verify hash '$verify' != '$local_hash'"
        fi
    else
        failed+=("$host")
        echo "$host: heal transfer/swap failed"
    fi
done 3< "$HOSTS_FILE"

join() { local IFS=,; echo "$*"; }

summary="ok=${#ok_boxes[@]}"
# Carry the role-stamp result into the verdict. It never CHANGES the verdict
# (distribution is this organ's job, not stamping), but a stamp that quietly
# stopped working must not be invisible — the whole federation role filter
# reads what it writes.
[ -n "${role_line:-}" ] && summary+=" | roles: $role_line"
[ ${#healed[@]} -gt 0 ]      && summary+=" healed=$(join "${healed[@]}")"
[ ${#skipped[@]} -gt 0 ]     && summary+=" no_registry=$(join "${skipped[@]}")"
[ ${#unreachable[@]} -gt 0 ] && summary+=" UNOBSERVABLE=$(join "${unreachable[@]}")"
[ ${#failed[@]} -gt 0 ]      && summary+=" FAILED=$(join "${failed[@]}")"
[ ${#dormant[@]} -gt 0 ]     && summary+=" dormant=$(join "${dormant[@]}")"

if [ ${#failed[@]} -gt 0 ]; then
    say FAIL "$summary"
elif [ ${#healed[@]} -gt 0 ] || [ ${#unreachable[@]} -gt 0 ] || [ ${#skipped[@]} -gt 0 ]; then
    say CONCERN "$summary (healed = a copy drifted, its backup is on the box; unobservable/skip != synced)"
else
    say OK "$summary — every fleet copy matches the manager's registry"
fi
exit 0
