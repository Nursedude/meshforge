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
#                                 # rc 2 = a list EXISTS but is unreadable:
#                                 #   $FLEET_HOSTS_ERROR says which and why,
#                                 #   and the lib has already said it on stderr
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
# A tier that is ABSENT passes to the next one. A tier that is PRESENT but
# unreadable (permission, a directory, a dangling symlink) FAILS the resolve
# with rc 2 — r7e #2, operator 2026-11-05: "quietly failing is not an answer".
# Falling through handed every consumer the NEXT tier's list: a different
# fleet, reported as the fleet. Every consumer already refuses on a non-zero
# rc; the stderr line here is what makes the refusal say the real reason.
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

_fh_refuse() {   # PATH WHY
  FLEET_HOSTS_ERROR="fleet host list $1 is unreadable ($2) - refusing to fall through to another list"
  printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
  return 2
}

# _fh_usable PATH — 0 readable regular file; 1 provably absent; 2 present
# but unusable, OR uncheckable (sets FLEET_HOSTS_ERROR, says it on stderr).
# Mirrors utils.fleet_hosts._usable — same reasons, same words.
_fh_usable() {
  if [ -f "$1" ] && [ -r "$1" ]; then return 0; fi
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then
    # [ -e ] is also false when a parent cannot be searched (EACCES) — that
    # is NOT absence (r7f F2: python refused, this lib fell through). Walk up
    # to the nearest directory that answers: searchable = the path is truly
    # absent below it; not searchable = we cannot know. Terminates: dirname
    # always ends at "/" or "." — both directories (r7g F9).
    _fh_d="$(dirname -- "$1")"
    while :; do
      if [ -d "$_fh_d" ]; then
        [ -x "$_fh_d" ] && return 1
        _fh_refuse "$1" "cannot be checked: a parent directory is not searchable"
        return 2
      fi
      _fh_d="$(dirname -- "$_fh_d")"
    done
  fi
  if [ -L "$1" ] && [ ! -e "$1" ]; then
    # r7g F1: [ -e ] is false for a dangling link, a loop AND a target under
    # an unsearchable dir — ask the kernel which (C locale = fixed strerror).
    case "$(LC_ALL=C cat -- "$1" 2>&1 >/dev/null)" in
      *"symbolic links"*) _fh_why="symlink loop" ;;
      *"ermission denied"*) _fh_why="symlink target cannot be checked: a parent directory is not searchable" ;;
      *) _fh_why="dangling symlink" ;;
    esac
  elif [ -d "$1" ]; then _fh_why="a directory, not a file"
  elif [ ! -f "$1" ]; then _fh_why="not a regular file"
  else _fh_why="permission denied"; fi
  _fh_refuse "$1" "$_fh_why"
}

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  FLEET_HOSTS_ERROR=""
  _fh_rb="$(basename "${1:-/opt/meshforge}")"
  for _fh_env in "${MESHFORGE_FLEET_HOSTS:-}" "${FLEET_HOSTS:-}"; do
    if [ -n "$_fh_env" ]; then
      _fh_usable "$_fh_env"; _fh_rc=$?
      [ "$_fh_rc" -eq 0 ] || return "$_fh_rc"  # authoritative: no fall-through
      FLEET_HOSTS_FILE="$_fh_env"
      break
    fi
  done
  if [ -z "$FLEET_HOSTS_FILE" ]; then
    for _fh_f in "${HOME:+$HOME/.config/meshforge/fleet_hosts.$_fh_rb}" \
                 "/etc/meshforge/fleet_hosts.$_fh_rb" \
                 "${HOME:+$HOME/.config/meshforge/fleet_hosts}" \
                 /etc/meshforge/fleet_hosts; do
      [ -n "$_fh_f" ] || continue
      _fh_usable "$_fh_f"; _fh_rc=$?
      [ "$_fh_rc" -eq 2 ] && return 2
      [ "$_fh_rc" -eq 0 ] && { FLEET_HOSTS_FILE="$_fh_f"; break; }
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
  # The read itself is checked (r7f F4): a failure here used to answer rc 0
  # with an EMPTY list ("the file lists nobody") while python raised.
  if ! _fh_raw="$(sed 's/#.*//' "$FLEET_HOSTS_FILE")"; then
    _fh_refuse "$FLEET_HOSTS_FILE" "read failed"
    FLEET_HOSTS_FILE=""
    return 2
  fi
  FLEET_HOSTS_LIST="$(printf '%s\n' "$_fh_raw" | tr -s ' \t\r' '\n' | grep -v '^$')"
  return 0
}
