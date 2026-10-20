#!/usr/bin/env bash
# Re-exec update.sh after it updated ITSELF. Port of MeshAnchor 83208d96
# (MeshAnchor #151 re-test, KN4YDL 2026-10-19).
#
# update.sh runs `git pull`/`git checkout`, which REPLACE scripts/update.sh.
# Bash keeps reading the file it opened, so every step after the pull runs the
# PRE-update text. On MeshAnchor the old step 4 cp'd a new placeholder unit
# verbatim; here any fix to a later step landed one update late. Re-exec the
# new file instead, once, telling it the ORIGINAL commit so its change range
# is still counted from where this run started.
#
# ⚠️ Only cures updates FROM a version that carries this. An update from an
# older update.sh still runs that old text once; re-running it is the cure.
#
#   mf_reexec_if_self_changed <from-commit> <to-commit> <repo> <relpath> [args...]
# Returns 0 (stay) when unchanged or already re-executed; otherwise exec's.

mf_reexec_if_self_changed() {
    local from="$1" to="$2" repo="$3" rel="$4"
    shift 4
    [[ -n "${MESHFORGE_UPDATE_REEXEC:-}" ]] && return 0
    [[ "$from" == "$to" ]] && return 0
    # 0 = unchanged. 1 = changed; >1 = git could not tell — re-exec then too:
    # running the on-disk script costs a no-op pull, running a stale one is #151.
    git -C "$repo" diff --quiet "$from" "$to" -- "$rel" 2>/dev/null && return 0
    echo "update.sh changed in this pull — re-running the new version"
    exec env MESHFORGE_UPDATE_REEXEC=1 MESHFORGE_UPDATE_FROM="$from" \
        bash "$repo/$rel" "$@"
}
