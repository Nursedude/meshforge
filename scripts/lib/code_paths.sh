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
# Resolved by UNION (honest_status's list plus fleet_sync's pyproject.toml),
# not by picking a favourite: each list was missing something real.
#
# WHAT COUNTS. The canonical list is the UNION of what the three consumers
# used, because the two risks are wildly asymmetric: being over-broad costs a
# needless daemon restart (seconds, safe), being too narrow lets a daemon run
# stale code forever — which is the bug that produced this file.
#
#   src               the obvious case: importable modules
#   scripts           ⚠️ NOT a tooling-only directory on this fleet. Resident
#                     units ExecStart straight out of it — verified 2026-09-15:
#                     nomadnet-silence-watch runs
#                     `/usr/bin/python3 /opt/meshforge/scripts/nomadnet_silence_watch.py`.
#                     Dropping it would mean that daemon NEVER restarts when
#                     its own source changes. A 2026-08-12 review had already
#                     established this ("resident daemons live there") and a
#                     2026-09-15 session re-derived the opposite from the
#                     directory's NAME, was wrong, and was caught by
#                     tests/test_honest_status_skew_codehead.sh. Check
#                     ExecStart before you believe a directory is inert.
#   templates         unit FILES. A change means the installed unit is stale
#                     even though the process's Python did not move; the
#                     conservative reading (flag it, let the installer settle
#                     it) is the one that cannot hide a stale deployment.
#   pyproject.toml    packaging + dependency pins the interpreter resolves.
#                     Only fleet_sync had this; honest_status did not, so a
#                     pyproject-only change restarted daemons that the fleet
#                     report called current. The mirror of the scripts/ gap.
#   requirements      the requirements/ directory (e.g. requirements/rns.txt)
#   requirements.txt  the top-level pin file
#
# ⚠️ The honest limit of this constant: "is this daemon behind its code?" is
# really a PER-UNIT question — what a unit loads depends on its own ExecStart,
# and mini-dudeai (`-m mini_dudeai`, from src/) genuinely does not care about a
# scripts/ commit. One global pathspec cannot express that, so it answers the
# union and over-restarts rather than under-restarts. If that churn ever costs
# more than it saves, the fix is per-unit attribution from ExecStart — NOT
# trimming this list, which is how a daemon goes quietly stale.

#: Pathspecs for code a resident daemon has loaded. Word-split on purpose:
#: every consumer passes it unquoted after `--` so each entry is its own
#: pathspec. Plain directory names match recursively; no globs needed.
MF_DAEMON_CODE_PATHS="src scripts templates pyproject.toml requirements requirements.txt"

# mf_code_head <repo> -> epoch seconds of the newest commit touching that code,
# or EMPTY when git fails or the paths were never touched. Empty is NOT zero:
# a caller must decide what an unknown code-head means for it (fleet_sync
# treats it as "cannot tell", honest_status falls back to the repo HEAD), and
# collapsing it to 0 would silently read as "infinitely old" — the absent-value
# -in-the-measurement-domain trap.
mf_code_head() {
    git -C "$1" log -1 --format=%ct -- $MF_DAEMON_CODE_PATHS 2>/dev/null
}
