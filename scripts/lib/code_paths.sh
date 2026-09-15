# scripts/lib/code_paths.sh — THE definition of "code a running daemon loaded".
#
# Source this file; it defines one variable and one function, no side effects.
#
# Consumers (keep this list current — it is the grep target when this changes):
#   - scripts/fleet_sync.sh    sync_local_unit + sync_local_user_unit: decides
#                              whether to restart a daemon on this box
#   - scripts/honest_status.sh hs_codehead (interpolated into the REMOTE probe
#                              script, so the value must survive ssh as text)
#   - scripts/fleet_pull.sh    the meshforge-map "started before HEAD" nag
#
# WHY THIS FILE EXISTS (2026-09-15). The same question had THREE different
# answers, and they disagreed in production:
#
#   fleet_sync.sh    src/*  pyproject.toml  requirements*.txt
#   honest_status.sh src  requirements  requirements.txt  templates  scripts
#   fleet_pull.sh    <no pathspec at all — the whole repo's HEAD>
#
# So commit 712ac491, which touched ONLY scripts/fleet_sync.sh, made
# honest_status report 45 daemons "behind their repo's newest CODE commit"
# while fleet_sync correctly restarted none of them, and made fleet_pull nag
# about meshforge-map whose `server_class_skew` was in fact `{}`. The mirror
# case is the one that bites: a pyproject.toml-only change would have
# fleet_sync restarting daemons that honest_status calls current.
# honest_failure_modes #5 — two consumers of one artifact share ONE constant.
# Resolved by adopting the NARROW list (the remote classifier's, which is the
# one with measurements behind it) plus fleet_sync's pyproject.toml, which
# honest_status had been missing. A union was tried first and reverted: see
# WHAT COUNTS below for why, and for the gap that choice leaves open.
#
# WHAT COUNTS — and this list is NARROW on purpose, after the union was tried
# and reverted on 2026-09-15 (operator call). Two prior decisions, each with a
# measurement behind it, say so:
#
#   * 2026-05-11 — scripts/ was IN, and tuning scripts/cloud/push_snapshot.sh
#     "triggered fleet-wide map restarts for no benefit". The map does not
#     import that file. Excluded, and the remote classifier has carried that
#     exclusion ever since.
#   * templates/ cannot take effect through this mechanism AT ALL: fleet_sync
#     uses `try-restart`, never `daemon-reload`, so a changed unit file is not
#     picked up by the restart it would trigger. Including it is pointless by
#     construction, not a judgement call.
#
#   src               the daemon's own importable modules
#   pyproject.toml    packaging + dependency pins the interpreter resolves
#   requirements      the requirements/ directory (e.g. requirements/rns.txt)
#   requirements.txt  the top-level pin file
#
# ⚠️ KNOWN GAP, deliberately accepted — do NOT "fix" it by widening this list.
# Some resident daemons ExecStart straight out of scripts/:
#
#     nomadnet-silence-watch
#       /usr/bin/python3 /opt/meshforge/scripts/nomadnet_silence_watch.py
#
# A commit touching only that file does not move this code-head, so the daemon
# can keep running pre-fix code. This is not hypothetical: 366b435d
# (2026-09-02, "a box with no NomadNet logfile is INERT, not silent" — the cure
# for a LATCHING alarm) touched only scripts/nomadnet_silence_watch.py and its
# test, and the code-head at that commit resolved to a src/ commit 7.8 HOURS
# EARLIER, so a daemon started in that window read "current".
#
# Widening brought back the 05-11 churn. An earlier draft of this note named
# PER-UNIT ExecStart attribution as "the real fix" — "let the file it actually
# runs decide its restart". ⚠️ DO NOT BUILD THAT AS WRITTEN. It is wrong in two
# ways, and it was written before anyone counted the population:
#
#   1. It REPLACES the global list per unit, and file granularity is blind to
#      TRANSITIVE IMPORTS. nomadnet_silence_watch.py imports from src/utils/*,
#      so attributing it to its own file alone would stop catching src/ changes
#      it depends on — a NEW under-restart gap wearing the appearance of
#      precision. If it is ever built it must be ADDITIVE (this list UNION the
#      unit's own ExecStart file), never a replacement.
#   2. The population does not justify the machinery. Measured on the manager box
#      2026-09-15: SIX units ExecStart out of scripts/, and FIVE are
#      timers/oneshots (backup, ci-status, cloud-push, dep-advisory,
#      dep-range) — invoked fresh each run, so never stale and pointless to
#      restart. Exactly ONE is a long-lived Type=simple daemon:
#      nomadnet-silence-watch. Every other resident daemon already runs
#      `-m module` out of src/ (mini_dudeai, utils.watchdog_runner,
#      lab.lxmf_echo, lab.lxmf_tracer, core.orchestrator) and is covered here
#      already. Attribution would mean parsing `-m` module resolution and
#      `bash -c` one-liners (meshforge-map's ExecStart is a buried shell
#      string) for the nine units that do NOT need it, to serve the one that
#      does. ⚠️ Measured on ONE box — sweep the fleet before trusting the count.
#
# PREFERRED FIX: move the daemon's body into src/ and point ExecStart at
# `-m`, like every other resident daemon. Then this list is correct BY
# CONSTRUCTION, both dated decisions hold with no special case, and scripts/
# goes back to meaning what 05-11 assumed it meant — operator-invoked tooling,
# never resident daemon code. One file moved + a unit template edit, against a
# design change inside a fragile REMOTE_SCRIPT with fleet-wide blast radius.
# Fix the layout violation; do not teach the tool about the violation.
#
# Until then the gap stands, written down rather than quietly absorbed.

#: Pathspecs for code a resident daemon has loaded. Word-split on purpose:
#: every consumer passes it unquoted after `--` so each entry is its own
#: pathspec. Plain directory names match recursively; no globs needed.
MF_DAEMON_CODE_PATHS="src pyproject.toml requirements requirements.txt"

# mf_code_head <repo> -> epoch seconds of the newest commit touching that code,
# or EMPTY when git fails or the paths were never touched. Empty is NOT zero:
# a caller must decide what an unknown code-head means for it (fleet_sync
# treats it as "cannot tell", honest_status falls back to the repo HEAD), and
# collapsing it to 0 would silently read as "infinitely old" — the absent-value
# -in-the-measurement-domain trap.
mf_code_head() {
    git -C "$1" log -1 --format=%ct -- $MF_DAEMON_CODE_PATHS 2>/dev/null
}
