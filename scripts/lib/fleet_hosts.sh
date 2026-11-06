# fleet_hosts.sh — THE fleet_hosts membership SSOT resolver. Source it; never copy it.
#
# Born 2026-07-28: honest_status.sh carried a hand-copy of fleet_pull.sh's
# resolution chain, held identical only by a "these must stay identical"
# comment — and the copies already disagreed at copy time (HOME defaulting,
# comment parsing), the exact two-consumers drift the copy claimed to end
# (honest_failure_modes #5). At convergence time (2026-07-29) the tree held
# ~13 independent copies across shell and Python. Since 2026-11-05 there is
# exactly ONE: this file asks src/utils/fleet_hosts.py (see below).
#
# Usage:
#   . "<repo>/scripts/lib/fleet_hosts.sh"
#   if fleet_hosts_resolve "/opt/meshforge"; then
#     echo "$FLEET_HOSTS_FILE"   # the file that won the resolution order
#     echo "$FLEET_HOSTS_LIST"   # hosts, one per line, comments stripped
#   fi                            # rc 1 = no list found anywhere:
#                                 #   $FLEET_HOSTS_WHY says what was checked
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
# ONE implementation (operator 2026-11-05). This lib used to re-derive the
# decision in shell; four review rounds (r7f..r7i) each found a new path
# shape on which it disagreed with the python resolver — an unsearchable
# parent, a symlink loop, ENAMETOOLONG, a trailing slash. It now ASKS
# src/utils/fleet_hosts.py (`--shell`) for the decision, the reason and the
# parsed list. Python's own failure (python3 missing, a traceback) is rc 2 —
# a resolver that could not run is never "no list".
#
# set -u and set -e safe. Needs bash (BASH_SOURCE) and python3; every caller
# is a bash script and every box runs python3.

# Located from THIS file, never from a failed cd (r8 review #4: a failed cd
# made the path "/fleet_hosts.py" — an unrelated file would have decided).
_FH_PY=""
# readlink -f (Fable r10 #7): sourced through a symlink, BASH_SOURCE is the
# LINK — its directory has no src/utils beside it.
_fh_self="$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null)" || _fh_self="${BASH_SOURCE[0]}"
if _fh_d="$(CDPATH= cd -- "$(dirname -- "$_fh_self")/../../src/utils" 2>/dev/null && pwd)" \
    && [ -f "$_fh_d/fleet_hosts.py" ]; then
  _FH_PY="$_fh_d/fleet_hosts.py"
fi

fleet_hosts_resolve() {
  FLEET_HOSTS_FILE=""
  FLEET_HOSTS_LIST=""
  FLEET_HOSTS_ERROR=""
  FLEET_HOSTS_WHY=""
  if [ -z "$_FH_PY" ]; then
    FLEET_HOSTS_ERROR="fleet host list could not be resolved (src/utils/fleet_hosts.py not found beside this lib) - refusing to guess"
    printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
    return 2
  fi
  # timeout (r8 review #3): every deploy and gate script calls this; a hung
  # interpreter must end as a refusal (rc 124 -> the * arm), not a hang.
  # -I: no script dir, cwd, user site or PYTHON* env on sys.path. HOME and
  # the overrides are passed EXPLICITLY — they may be unexported shell vars,
  # and the shell contract is $HOME (unset = skip the user tier), not the
  # sudo-real home.
  _fh_rc=0
  _fh_out="$(env HOME="${HOME:-}" MESHFORGE_FLEET_HOSTS="${MESHFORGE_FLEET_HOSTS:-}" \
      FLEET_HOSTS="${FLEET_HOSTS:-}" timeout "${FLEET_HOSTS_RESOLVE_TIMEOUT:-30}" python3 -I "$_FH_PY" --shell "${1:-/opt/meshforge}")" || _fh_rc=$?
  case "$_fh_rc" in
    0)
      # First line must be the format marker (r17): a wrapper that chatters
      # on stdout can never turn its stray line into FLEET_HOSTS_FILE.
      case "$_fh_out" in
        "fleet_hosts/1
"*)
          _fh_out="${_fh_out#*
}"
          FLEET_HOSTS_FILE="${_fh_out%%
*}"
          case "$_fh_out" in *"
"*) FLEET_HOSTS_LIST="${_fh_out#*
}" ;; esac
          return 0 ;;
      esac
      # rc 0 with NO file line: the resolver did not run (a shadowed
      # `timeout`/`python3`). An empty list here would read "lists nobody".
      FLEET_HOSTS_ERROR="fleet host list could not be resolved (python resolver answered rc 0 with no file) - refusing to guess" ;;   # also: no format marker
    10) FLEET_HOSTS_WHY="$_fh_out"; return 1 ;;   # why: for a status line
    11) FLEET_HOSTS_ERROR="$_fh_out" ;;
    *) FLEET_HOSTS_ERROR="fleet host list could not be resolved (python resolver $_FH_PY exited $_fh_rc) - refusing to guess" ;;
  esac
  printf 'fleet_hosts: %s\n' "$FLEET_HOSTS_ERROR" >&2
  return 2
}
