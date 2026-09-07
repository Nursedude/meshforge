#!/bin/bash
# fleet_hosts_selfheal.sh — hourly organ: detect /etc/hosts fleet-block drift
# and repair it, WITHOUT going quiet about the repair.
#
# WHY THIS EXISTS (2026-07-27)
# ---------------------------
# The hourly cron used to run `gen_fleet_hosts.py --check` and only report.
# Detection worked (it caught the moc5 cloud-init wipe 15 minutes after the
# reboot), but a human had to run --apply, so a box could sit with no
# fleet-name resolution until someone noticed the FAIL.
#
# WHY NOT JUST SWAP --check FOR --apply
# -------------------------------------
# `--apply` exits 0 both when nothing was wrong AND when it silently rewrote
# the block. Wiring that straight into cron_verdict.sh would report OK every
# hour forever, so a box whose address churns every single hour would look
# exactly like a box that never drifts — the drift signal would be destroyed
# by the thing meant to fix it. That is honest_failure_modes #1 (a degraded
# state mapped to a valid-looking value) committed by the repair itself.
#
# So: heal, then say that you healed. A repair is reported CONCERN, not OK.
# It self-clears on the next hourly run once the block is stable, and while it
# is raised it tells the operator the truth that matters — a box moved. The
# durable cure for repeated heals is DHCP reservations, not this script.
#
# HONEST FAILURE MODES
#   * UNOBSERVABLE (--check rc=2) NEVER triggers a write. Blindness is not
#     drift; healing from it would write a guess into a file that SHADOWS DNS.
#     Unobservable is reported FAIL, never OK (#2 — unobservable != healthy).
#   * A failed `sudo -n` is reported, never swallowed — a box that cannot heal
#     must not look like a box that had nothing to heal (#9, every swallow
#     leaves a witness).
#   * The repair is VERIFIED by re-running --check afterwards rather than by
#     trusting --apply's exit code (calibrated_claims #7 — observe the
#     artifact, not the wiring).
#
# Verdict name stays `fleet_hosts_drift` so Issue #78's staleness probe keeps
# its history; renaming it would make the old name go silent and page.
#
# Crontab idiom (this script writes its own verdict, so the `||` only catches
# the case where the script itself never got far enough to write one):
#   47 * * * * /opt/meshforge/scripts/fleet_hosts_selfheal.sh >/dev/null 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh fleet_hosts_drift FAIL wrapper_crashed
#
# $MESHFORGE_FLEET_DNS (2026-09-07) — space-separated DNS server IPs passed to
# EVERY generator call as --server. For a box whose own resolver does not serve
# the fleet zone: lehua is an AREDN-attached field node whose only nameserver
# answers *.mf.internal with a confident NXDOMAIN, so without this the hourly
# run is permanently UNOBSERVABLE and this organ can never heal there.
#   47 * * * * MESHFORGE_FLEET_DNS=<fleet-dns-ip> /opt/meshforge/scripts/...
#
# It is an ENV VAR and nothing else — deliberately NOT a new file-resolution
# chain. utils/fleet_hosts.py exists because ~13 hand-rolled copies of one
# chain drifted apart (honest_failure_modes #5); adding a fourteenth to reach
# one box would be that mistake with a fresh coat of paint. The crontab line
# is already per-box, so the per-box value belongs there.
#
# ⚠️ When the override is in use EVERY verdict says so. An `OK` from a box
# whose own resolver is broken must not read as "this box's DNS is healthy" —
# that is honest_failure_modes #1 in the reporting layer.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN="$HERE/gen_fleet_hosts.py"
VERDICT="$HERE/cron_verdict.sh"
NAME=fleet_hosts_drift
LOCK="${FLEET_HOSTS_SELFHEAL_LOCK:-${TMPDIR:-/tmp}/fleet_hosts_selfheal.lock}"

# gen_fleet_hosts.py exit codes (EXIT_OK / EXIT_DRIFT / EXIT_UNKNOWN)
RC_OK=0; RC_DRIFT=1; RC_UNKNOWN=2

# One arg list, threaded to ALL THREE generator calls. A mechanism with a
# producing and a consuming half must wire together or fail together
# (honest_failure_modes #4): passing --server to --check but not to --apply
# would heal from one resolver's answer while checking against another's.
GEN_ARGS=()
SRC_NOTE=""
if [ -n "${MESHFORGE_FLEET_DNS:-}" ]; then
    for _srv in $MESHFORGE_FLEET_DNS; do
        GEN_ARGS+=(--server "$_srv")
    done
    SRC_NOTE=" [via --server ${MESHFORGE_FLEET_DNS// /,}; this box's own resolver does NOT serve the fleet zone]"
fi
# Appended in say() rather than at each call site, so a branch added later
# cannot quietly omit the disclosure.
say() { "$VERDICT" "$NAME" "$1" "$2$SRC_NOTE"; }

# ONE place where the override is attached to a generator call, root path and
# sudo path alike. It was two expansion sites for about an hour on 2026-09-07,
# and a mutation drill showed why that is wrong: deleting --server from the
# ROOT branch left the whole suite green, because the tests run unprivileged
# and only ever take the sudo branch. Two hardcodes of one rule, and the
# unexercised copy is the one that rots (honest_failure_modes #5).
#
# `${a[@]+"${a[@]}"}` — an empty array under `set -u` is an error on older
# bash; this expands to nothing there instead of aborting the hourly run.
gen() {
    local pre=()
    if [ "${1:-}" = "--as-root" ]; then
        shift
        [ "$(id -u)" -eq 0 ] || pre=(sudo -n)
    fi
    ${pre[@]+"${pre[@]}"} "$GEN" "$@" ${GEN_ARGS[@]+"${GEN_ARGS[@]}"}
}

# Serialize against a concurrent manual run so check and apply cannot interleave
# on the same file (honest_failure_modes #8). A missing/wedged flock degrades to
# running anyway — a skipped hour is worse than an unlikely overlap.
exec 9>"$LOCK" 2>/dev/null || true
flock -w 30 9 2>/dev/null || true

if [ ! -x "$GEN" ]; then
    say FAIL "generator missing or not executable: $GEN"
    exit 0
fi

out=$(gen --check 2>&1); rc=$?

case "$rc" in
  "$RC_OK")
      say OK "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
      exit 0
      ;;
  "$RC_UNKNOWN")
      # Cannot see DNS. Hold — do not write a guess into a file that shadows it.
      say FAIL "unobservable — not healing: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-140)"
      exit 0
      ;;
  "$RC_DRIFT") ;;   # fall through to the repair path
  *)
      say FAIL "unexpected --check rc=$rc: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-140)"
      exit 0
      ;;
esac

# --- drift: repair, then verify the repair against the artifact -------------
# TWO repairable shapes, counted separately because they mean different things
# to a human (2026-08-31): DRIFT = the address moved (a box changed IP);
# PROVENANCE = the address is right but its source marker is stale (e.g. a name
# that used to fall back to the registry now has a real DNS record). Collapsing
# them would report "a box moved" when nothing moved.
drifted=$(printf '%s\n' "$out" | awk '/^DRIFT /{print $2}' | tr -d ':' | paste -sd, -)
n=$(printf '%s\n' "$out" | grep -c '^DRIFT ')
provenance=$(printf '%s\n' "$out" | awk '/^PROVENANCE /{print $2}' | tr -d ':' | paste -sd, -)
np=$(printf '%s\n' "$out" | grep -c '^PROVENANCE ')

apply_out=$(gen --as-root --apply 2>&1); apply_rc=$?
# The sudo-refusal witness is checked unconditionally now: running as root it
# simply never matches, and a single path cannot drift from its own copy.
if [ "$apply_rc" -ne 0 ] && printf '%s' "$apply_out" | grep -qi "sudo\|password"; then
    say FAIL "drift in $n name(s) [$drifted] / $np stale marker(s) [$provenance] — cannot heal, sudo -n refused"
    exit 0
fi

verify_out=$(gen --check 2>&1); verify_rc=$?

if [ "$verify_rc" -eq "$RC_OK" ]; then
    if [ "$n" -gt 0 ] && [ "$np" -gt 0 ]; then
        say CONCERN "healed $n drifted name(s): $drifted (a box moved); refreshed $np stale provenance marker(s): $provenance"
    elif [ "$n" -gt 0 ]; then
        say CONCERN "healed $n drifted name(s): $drifted (a box moved — durable cure is a DHCP reservation)"
    else
        # Deliberately NOT worded as a box moving: addresses were already
        # correct. This is the block catching up to a change in where an
        # address CAME FROM — normally a name gaining (or losing) a real DNS
        # record. One-shot; it self-clears on the next run.
        say CONCERN "refreshed $np stale provenance marker(s): $provenance (addresses unchanged — no box moved)"
    fi
else
    say FAIL "drift in $n name(s) [$drifted] / $np stale marker(s) [$provenance] PERSISTS after --apply (apply rc=$apply_rc, recheck rc=$verify_rc): $(printf '%s' "$apply_out" | tr '\n' ' ' | cut -c1-120)"
fi
exit 0
