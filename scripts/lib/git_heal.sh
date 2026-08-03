#!/usr/bin/env bash
# git_heal.sh — repair foreign-owned artifacts under a repo's .git before a pull.
#
# WHY (2026-08-02, moc3): a `sudo git` run on 07-31 left 24 root-owned objects
# and directories under /opt/meshforge/.git. Every subsequent unprivileged pull
# died with `insufficient permission for adding an object to repository
# database`, so the box silently stopped receiving code and sat 4 commits
# behind until a human tried to deploy. fleet_sync.sh has warned against
# `sudo git pull` in a comment since 2026-05-03 (Insight 7) and it happened
# anyway — prose is not a gate.
#
# ⚠️ Repair, NOT prevention, and deliberately so. The obvious "prevention"
# (core.sharedRepository=group + setgid) was A/B drilled on moc3 and REJECTED:
# it does fix the object store (20/20 blocked dirs -> 0/20, root's git then
# writing 0:1000 drwxrwsr-x), but `.git/FETCH_HEAD` is created with root's
# umask outside what shared-perm governs, so the user pull still died with
#   error: cannot open '.git/FETCH_HEAD': Permission denied
# in BOTH arms. There is no repo-config-only cure. Chowning covers every
# poisoned artifact — objects, refs, FETCH_HEAD — rather than a subset.
#
# Reporting convention is fleet_hosts_selfheal.sh's, verbatim in spirit:
# heal, then SAY you healed. A repair is CONCERN, never OK — otherwise a box
# being re-poisoned every deploy looks identical to a clean one. And a box
# that CANNOT heal must never look like a box that had nothing to heal
# (honest_failure_modes #9).
#
# Tokens (single line on stdout, parsed by callers):
#   HEAL_NONE                 nothing foreign-owned — the healthy case
#   HEAL_OK <n>               repaired n artifact(s) — report CONCERN
#   HEAL_PARTIAL <n> <left>   chown ran, left still foreign — report FAIL
#   HEAL_DENIED <n>           found n, no passwordless sudo — report FAIL
#   HEAL_UNOBSERVABLE <why>   cannot even look — NEVER "none" (#2)
# Exit: 0 only for HEAL_NONE and HEAL_OK.

git_heal_ownership() {
    local repo="$1" me grp n left
    local gitdir="$repo/.git"

    # Unobservable is its own answer. An unreadable .git would make `find`
    # print nothing, and reporting that as HEAL_NONE would be the exact
    # "degraded state maps to a valid-looking value" defect this repo hunts.
    if [ ! -e "$gitdir" ]; then
        echo "HEAL_UNOBSERVABLE no_gitdir"
        return 1
    fi
    if [ ! -r "$gitdir" ] || [ ! -x "$gitdir" ]; then
        echo "HEAL_UNOBSERVABLE gitdir_unreadable"
        return 1
    fi

    me="$(id -un)"; grp="$(id -gn)"
    n=$(find "$gitdir" ! -user "$me" 2>/dev/null | wc -l)

    if [ "$n" -eq 0 ]; then
        echo "HEAL_NONE"
        return 0
    fi

    # -n: never block a deploy on a password prompt. A box that cannot heal
    # says so loudly rather than hanging or pretending.
    if ! sudo -n chown -R "$me:$grp" "$gitdir" 2>/dev/null; then
        echo "HEAL_DENIED $n"
        return 1
    fi

    left=$(find "$gitdir" ! -user "$me" 2>/dev/null | wc -l)
    if [ "$left" -ne 0 ]; then
        echo "HEAL_PARTIAL $n $left"
        return 1
    fi

    echo "HEAL_OK $n"
    return 0
}

# Standalone entrypoint: scripts/lib/git_heal.sh /opt/meshforge
# ⚠️ The `$# -gt 0` guard is load-bearing. Callers EMBED this file's text into
# a remote script fed to `bash -s`, where $0 and BASH_SOURCE[0] are both
# "bash" — without the arg check the heal would fire at the top of every
# remote script against a defaulted path, printing a stray token.
if [ "${BASH_SOURCE[0]}" = "${0}" ] && [ "$#" -gt 0 ]; then
    git_heal_ownership "${1:-/opt/meshforge}"
    exit $?
fi
