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

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN="$HERE/gen_fleet_hosts.py"
VERDICT="$HERE/cron_verdict.sh"
NAME=fleet_hosts_drift
LOCK="${FLEET_HOSTS_SELFHEAL_LOCK:-${TMPDIR:-/tmp}/fleet_hosts_selfheal.lock}"

# gen_fleet_hosts.py exit codes (EXIT_OK / EXIT_DRIFT / EXIT_UNKNOWN)
RC_OK=0; RC_DRIFT=1; RC_UNKNOWN=2

say() { "$VERDICT" "$NAME" "$1" "$2"; }

# Serialize against a concurrent manual run so check and apply cannot interleave
# on the same file (honest_failure_modes #8). A missing/wedged flock degrades to
# running anyway — a skipped hour is worse than an unlikely overlap.
exec 9>"$LOCK" 2>/dev/null || true
flock -w 30 9 2>/dev/null || true

if [ ! -x "$GEN" ]; then
    say FAIL "generator missing or not executable: $GEN"
    exit 0
fi

out=$("$GEN" --check 2>&1); rc=$?

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
drifted=$(printf '%s\n' "$out" | awk '/^DRIFT /{print $2}' | tr -d ':' | paste -sd, -)
n=$(printf '%s\n' "$out" | grep -c '^DRIFT ')

if [ "$(id -u)" -eq 0 ]; then
    apply_out=$("$GEN" --apply 2>&1); apply_rc=$?
else
    apply_out=$(sudo -n "$GEN" --apply 2>&1); apply_rc=$?
    if [ "$apply_rc" -ne 0 ] && printf '%s' "$apply_out" | grep -qi "sudo\|password"; then
        say FAIL "drift in $n name(s) [$drifted] — cannot heal, sudo -n refused"
        exit 0
    fi
fi

verify_out=$("$GEN" --check 2>&1); verify_rc=$?

if [ "$verify_rc" -eq "$RC_OK" ]; then
    say CONCERN "healed $n drifted name(s): $drifted (a box moved — durable cure is a DHCP reservation)"
else
    say FAIL "drift in $n name(s) [$drifted] PERSISTS after --apply (apply rc=$apply_rc, recheck rc=$verify_rc): $(printf '%s' "$apply_out" | tr '\n' ' ' | cut -c1-120)"
fi
exit 0
