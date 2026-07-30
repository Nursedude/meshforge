# fleet_hosts.sh — THE fleet_hosts membership SSOT resolver. Source it; never copy it.
#
# Born 2026-07-28: honest_status.sh carried a hand-copy of fleet_pull.sh's
# resolution chain, held identical only by a "these must stay identical"
# comment — and the copies already disagreed at copy time (HOME defaulting,
# comment parsing), the exact two-consumers drift the copy claimed to end
# (honest_failure_modes #5). At convergence time (2026-07-29) the tree held
# ~13 independent copies across shell and Python. Now there are exactly TWO
# implementations — this file and src/utils/fleet_hosts.py — pinned to each
# other by tests/test_fleet_hosts_resolver.py feeding both the same fixture
# tree. Change one, run that test, change the other.
#
# Usage:
#   . "<repo>/scripts/lib/fleet_hosts.sh"
#   if fleet_hosts_resolve "/opt/meshforge"; then
#     echo "$FLEET_HOSTS_FILE"   # the file that won the resolution order
#     echo "$FLEET_HOSTS_LIST"   # hosts, one per line, comments stripped
#   fi                            # rc 1 = no list found anywhere
#
# Resolution order (per-repo list wins over the generic one):
#   $MESHFORGE_FLEET_HOSTS   — AUTHORITATIVE when set: a missing/unreadable
#   $FLEET_HOSTS               override FAILS the resolve rather than falling
#                              through to the box's real config (a degraded
#                              state must not read as a valid value —
#                              honest_failure_modes #1; FLEET_HOSTS is the
#                              legacy alias lab_traffic_rollup documented)
#   ~/.config/meshforge/fleet_hosts.<repo-basename>
#   /etc/meshforge/fleet_hosts.<repo-basename>
#   ~/.config/meshforge/fleet_hosts
#   /etc/meshforge/fleet_hosts
#
# HOME may be UNSET (cron/daemon context, set -u): the user-config tiers are
# then SKIPPED, never defaulted. The old copies disagreed here — fleet_pull
# aborted on bare $HOME while honest_status silently read /root's list; both
# were wrong, and worse, they were wrong DIFFERENTLY.
#
# File format: hosts separated by whitespace/newlines; '#' starts a comment
# anywhere on the line, so "moc1  # retired 07-28" parses as host "moc1" —
# not as a garbage two-token hostname (the second copy-time divergence).
#
# set -u safe. POSIX sh compatible (no arrays, no bashisms).

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  _fh_rb="$(basename "${1:-/opt/meshforge}")"
  for _fh_env in "${MESHFORGE_FLEET_HOSTS:-}" "${FLEET_HOSTS:-}"; do
    if [ -n "$_fh_env" ]; then
      if [ -f "$_fh_env" ] && [ -r "$_fh_env" ]; then
        FLEET_HOSTS_FILE="$_fh_env"
        break
      fi
      return 1   # explicit override that doesn't resolve = no list, loudly
    fi
  done
  if [ -z "$FLEET_HOSTS_FILE" ]; then
    for _fh_f in "${HOME:+$HOME/.config/meshforge/fleet_hosts.$_fh_rb}" \
                 "/etc/meshforge/fleet_hosts.$_fh_rb" \
                 "${HOME:+$HOME/.config/meshforge/fleet_hosts}" \
                 /etc/meshforge/fleet_hosts; do
      [ -n "$_fh_f" ] && [ -f "$_fh_f" ] && [ -r "$_fh_f" ] && { FLEET_HOSTS_FILE="$_fh_f"; break; }
    done
  fi
  [ -n "$FLEET_HOSTS_FILE" ] || return 1
  # \r is in the tr set (2026-07-29 review): without it a CRLF-encoded
  # fleet_hosts leaves "moc1\r" in the list, so every SHELL consumer would
  # `ssh moc1\r` (DNS failure) while every PYTHON consumer read "moc1" cleanly —
  # str.splitlines() + str.split() both treat \r as whitespace. Two
  # implementations of one SSOT disagreeing on the same file is the exact drift
  # this shared lib exists to end (honest_failure_modes #5), and it would have
  # split the deploy tools from the gate that verifies them.
  FLEET_HOSTS_LIST="$(sed 's/#.*//' "$FLEET_HOSTS_FILE" | tr -s ' \t\r' '\n' | grep -v '^$')"
  return 0
}
