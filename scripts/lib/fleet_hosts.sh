# fleet_hosts.sh — THE fleet_hosts SSOT resolver. Source it; never copy it.
#
# Born 2026-07-28: honest_status.sh carried a hand-copy of fleet_pull.sh's
# resolution chain, held identical only by a "these must stay identical"
# comment — and the copies already disagreed at copy time (HOME defaulting,
# comment parsing), the exact two-consumers drift the copy claimed to end
# (honest_failure_modes #5). One sourceable function; both consumers use it.
#
# Usage:
#   . "<repo>/scripts/lib/fleet_hosts.sh"
#   if fleet_hosts_resolve "/opt/meshforge"; then
#     echo "$FLEET_HOSTS_FILE"   # the file that won the resolution order
#     echo "$FLEET_HOSTS_LIST"   # hosts, one per line, comments stripped
#   fi                            # rc 1 = no list found anywhere
#
# Resolution order (per-repo list wins over the generic one):
#   $MESHFORGE_FLEET_HOSTS
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
  for _fh_f in "${MESHFORGE_FLEET_HOSTS:-}" \
               "${HOME:+$HOME/.config/meshforge/fleet_hosts.$_fh_rb}" \
               "/etc/meshforge/fleet_hosts.$_fh_rb" \
               "${HOME:+$HOME/.config/meshforge/fleet_hosts}" \
               /etc/meshforge/fleet_hosts; do
    [ -n "$_fh_f" ] && [ -f "$_fh_f" ] && { FLEET_HOSTS_FILE="$_fh_f"; break; }
  done
  [ -n "$FLEET_HOSTS_FILE" ] || return 1
  FLEET_HOSTS_LIST="$(sed 's/#.*//' "$FLEET_HOSTS_FILE" | tr -s ' \t' '\n' | grep -v '^$')"
  return 0
}
