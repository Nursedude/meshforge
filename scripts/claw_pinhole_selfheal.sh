#!/bin/bash
# claw_pinhole_selfheal.sh — hourly organ: keep the claw NATS pinhole pointed at
# the uplink's ACTUAL address, and never go quiet about a repair.
#
# WHY THIS EXISTS (2026-07-29, live 6.5 h outage)
# ----------------------------------------------
# The brain box default-denies TCP 4222 and admits a small hardcoded source
# allowlist, because WireClaw firmware cannot send NATS credentials. One entry is
# the WAN address of the AREDN node that fronts the claw's own /28 — an address
# handed out by a router this fleet does NOT administer.
#
# That node rebooted, its lease moved, the pinhole kept the old address, and
# every SYN from a perfectly healthy claw hit `tcp dport 4222 drop` for 6.5
# hours. The claw stayed associated, held its lease, and kept retrying the
# correct broker; /proc/net/nf_conntrack showed SYN_SENT [UNREPLIED], reply
# packets=0. Every signal the fleet owned blamed the DEVICE.
#
# WHY NOT A DHCP RESERVATION
# --------------------------
# Because fleet reachability must not depend on config in a box outside this
# domain. The fleet already made that call for names: the /etc/hosts fleet block
# exists so names resolve "with DNS or the uplink down", and the router-side fix
# was rejected for coupling the fleet to a box we do not own. A reservation is
# that same coupling one layer lower. Our connectivity is our concern.
#
# WHY NOT JUST CRON `--apply`
# ---------------------------
# `--apply` exits 0 both when nothing was wrong AND when it silently rewrote the
# firewall. Wired straight into cron_verdict.sh that reports OK forever, so a
# node whose lease churns hourly would look identical to one that never moves —
# the drift signal destroyed by its own repair (honest_failure_modes #1,
# committed by the fix). So: heal, then SAY you healed. A repair is CONCERN,
# never OK. It self-clears once the address is stable.
#
# HONEST FAILURE MODES
#   * UNOBSERVABLE (--check rc=2) NEVER triggers a write. We are rewriting a
#     FIREWALL; healing from a guess severs the claw this protects (#2).
#   * An unobserved uplink HOLDS its existing allowlist entry rather than being
#     dropped — blindness is not departure.
#   * A refused `sudo -n` is reported, never swallowed: a box that CANNOT heal
#     must not look like a box with nothing to heal (#9).
#   * The repair is verified by RE-RUNNING --check (which re-reads the file and
#     the live ruleset), not by trusting --apply's exit code
#     (calibrated_claims #7 — observe the artifact, not the wiring).
#
# Crontab idiom (this script writes its own verdict; the `||` only catches the
# case where the script never got far enough to write one):
#   23 * * * * /opt/meshforge/scripts/claw_pinhole_selfheal.sh >/dev/null 2>&1 \
#     || /opt/meshforge/scripts/cron_verdict.sh claw_pinhole_drift FAIL wrapper_crashed

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN="$HERE/gen_claw_pinhole.py"
VERDICT="$HERE/cron_verdict.sh"
NAME=claw_pinhole_drift
LOCK="${CLAW_PINHOLE_SELFHEAL_LOCK:-${TMPDIR:-/tmp}/claw_pinhole_selfheal.lock}"

RC_OK=0; RC_DRIFT=1; RC_UNKNOWN=2

say() { "$VERDICT" "$NAME" "$1" "$2"; }

# Serialize against a concurrent manual run so check and apply cannot interleave
# on the same firewall file (honest_failure_modes #8: "exclude or merge, never
# interleave... flock-refuse-loud or single-writer design").
#
# REFUSE LOUD (2026-07-29 review). This block used `2>/dev/null || true` on BOTH
# lines, so all three failure modes — $LOCK unwritable (/tmp is tmpfs RAM on this
# fleet), flock TIMING OUT because another run holds it, flock absent — fell
# through and rewrote the firewall unserialized. The exclusion existed but was
# optional, which is fail-permissive: the timeout case is precisely the concurrent
# run the lock was built to exclude. Sibling `2a0865d5` fixed the neighbouring
# variant ("a lock only protects a run that is actually running") 24 h earlier —
# same class, one door over, which is this window's whole meta-pattern.
#
# A skipped cycle is cheap (hourly cron, and the holder is doing this same work);
# an unserialized firewall rewrite is not. Speak a verdict so the skip leaves a
# witness (#9), then exit 0 so the crontab's `||` wrapper cannot overwrite it.
if ! exec 9>"$LOCK"; then
    say FAIL "cannot open lock $LOCK — refusing to touch the firewall unserialized"
    exit 0
fi
if ! flock -n 9; then
    say CONCERN "another claw-pinhole run holds $LOCK — skipping this cycle rather than interleaving two firewall writers (next run in an hour)"
    exit 0
fi

if [ ! -r "$GEN" ]; then
    say FAIL "generator missing or unreadable: $GEN"
    exit 0
fi

out=$(python3 "$GEN" --check 2>&1); rc=$?

case "$rc" in
  "$RC_OK")
      say OK "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
      exit 0
      ;;
  "$RC_UNKNOWN")
      # Cannot determine the uplink's address. Hold — never write a guess into
      # a firewall that gates the claw's only path home.
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
detail=$(printf '%s\n' "$out" | grep '^DRIFT' | head -1 | cut -c1-160)

if [ "$(id -u)" -eq 0 ]; then
    apply_out=$(python3 "$GEN" --apply 2>&1); apply_rc=$?
else
    apply_out=$(sudo -n python3 "$GEN" --apply 2>&1); apply_rc=$?
    if [ "$apply_rc" -ne 0 ] && printf '%s' "$apply_out" | grep -qi "sudo\|password"; then
        say FAIL "pinhole drift [$detail] — cannot heal, sudo -n refused"
        exit 0
    fi
fi

verify_out=$(python3 "$GEN" --check 2>&1); verify_rc=$?

if [ "$verify_rc" -eq "$RC_OK" ]; then
    say CONCERN "healed claw pinhole [$detail] — the uplink's lease moved; the fleet re-derived it (this is the router-independent cure, so a repeat is expected on every lease change, not a fault)"
else
    say FAIL "pinhole drift [$detail] PERSISTS after --apply (apply rc=$apply_rc, recheck rc=$verify_rc): $(printf '%s' "$apply_out" | tr '\n' ' ' | cut -c1-120)"
fi
exit 0
