#!/usr/bin/env bash
# Roll `git pull` + service restarts across the fleet for BOTH MeshForge repos:
#   /opt/meshforge          (this repo) → restart meshforge-gateway.service
#   /opt/meshforge-maps     (sister)    → restart meshforge-maps.service
#
# Plus auto-commit + mirror Claude memory from THIS box (the canonical writer)
# to each fleet host:
#   ~/.claude/memory/                                 (cross-repo memory)
#   ~/.claude/projects/-opt-meshforge/memory/         (meshforge repo memory)
#   ~/.claude/projects/-opt-meshforge-maps/memory/    (meshforge-maps repo memory)
#
# Each sync run starts with `git add -A && git commit && git push origin main`
# on each memory repo (no-op when nothing changed; blocks on the secrets-grep
# pre-commit hook if a secret pattern is detected). Then rsync mirrors the
# committed working tree + .git/ to every fleet box.
#
# Canonical-writer model: --delete is on by design. Fleet boxes are pull-only
# replicas with the full git history available locally (rsync copies .git/),
# but they don't run their own commits — writing memory on a fleet box is a
# footgun the next sync will erase. Author memory on the canonical box only.
#
# A box without one of the repos still updates the other (skip-if-absent).
# Without this, /opt/meshforge-maps drifts on boxes where it isn't manually
# pulled — a real-world incident left a 14 GB sqlite WAL on an unsynced box
# because a WAL-cap fix had never landed there.
#
# Reads a host list from the first file found:
#   $MESHFORGE_FLEET_HOSTS (if set)
#   $HOME/.config/meshforge/fleet_hosts
#   /etc/meshforge/fleet_hosts
#
# Host list format:
#   # comments and blank lines are ignored
#   pi-node-1
#   pi-node-2
#   operator@gateway-host
#   # jump-host syntax is supported via ~/.ssh/config
#   inner-host.via-bastion
#
# Per-host: mirror memory dirs (rsync), then verify each present repo + branch,
# git pull --ff-only, restart the matching unit if installed, print one summary
# line per repo. A repo missing on a host is reported as `skip_no_repo`, not a
# failure.
#
# A host failing does NOT abort the rest. Exit code is the number of host
# x action failures (0 = all ok).

set -uo pipefail

SELF="$(basename "$0")"

# Locate host list
FLEET_FILE="${MESHFORGE_FLEET_HOSTS:-}"
if [[ -z "$FLEET_FILE" ]]; then
    if [[ -r "$HOME/.config/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="$HOME/.config/meshforge/fleet_hosts"
    elif [[ -r "/etc/meshforge/fleet_hosts" ]]; then
        FLEET_FILE="/etc/meshforge/fleet_hosts"
    fi
fi

if [[ -z "$FLEET_FILE" || ! -r "$FLEET_FILE" ]]; then
    cat >&2 <<EOF
$SELF: no fleet host list found.

Create one at \$HOME/.config/meshforge/fleet_hosts (one host per line, '#'
comments allowed). Example:

    cp contrib/fleet/fleet_hosts.example ~/.config/meshforge/fleet_hosts
    \$EDITOR ~/.config/meshforge/fleet_hosts

Or set \$MESHFORGE_FLEET_HOSTS to a different path.
EOF
    exit 2
fi

# Auto-commit any pending memory changes to the canonical repo and push to
# origin. Runs ONCE before the host loop so all fleet boxes receive the same
# committed state via rsync. Returns 0 on success (committed or no changes),
# 1 if commit failed (typically the secrets-grep pre-commit hook blocked).
#
# Push failure (network/auth) is non-fatal: the commit lands locally, rsync
# still propagates the new .git state to fleet, and the next sync retries
# the push.
commit_memory_repo() {
    local dir="$1"
    local label="$2"

    if [[ ! -d "$dir/.git" ]]; then
        printf '[%-26s] SKIP not_a_git_repo\n' "$label"
        return 0
    fi

    git -C "$dir" add -A 2>/dev/null

    if git -C "$dir" diff --cached --quiet; then
        printf '[%-26s] CLEAN no_changes\n' "$label"
        return 0
    fi

    local changed_count
    changed_count=$(git -C "$dir" diff --cached --name-only | wc -l)

    if ! git -C "$dir" commit -q -m "memory sync $(date -u +%Y-%m-%dT%H:%M:%SZ) — $changed_count files" 2>/dev/null; then
        printf '[%-26s] FAIL commit_blocked (likely pre-commit secrets gate)\n' "$label"
        return 1
    fi

    local new_head
    new_head=$(git -C "$dir" rev-parse --short HEAD)

    if timeout 30 git -C "$dir" push -q origin main 2>/dev/null; then
        printf '[%-26s] PUSHED %s (%d files)\n' "$label" "$new_head" "$changed_count"
    else
        printf '[%-26s] LOCAL_COMMIT %s (%d files) push_failed\n' "$label" "$new_head" "$changed_count"
    fi

    return 0
}

# Mirror Claude memory dirs from THIS box to one fleet host. Runs LOCALLY
# (rsync drives its own ssh transport). Prints one summary line per dir in
# the same PASS/FAIL/SKIP format the host-loop already counts. --delete is
# on by design: canonical-writer model means fleet replicas are not allowed
# to diverge silently. --mkpath creates missing parent dirs on first sync.
mirror_memory_to_host() {
    local host="$1"
    local src dst tag

    for pair in \
        "$HOME/.claude/memory/|.claude/memory/|memory-global" \
        "$HOME/.claude/projects/-opt-meshforge/memory/|.claude/projects/-opt-meshforge/memory/|memory-project" \
        "$HOME/.claude/projects/-opt-meshforge-maps/memory/|.claude/projects/-opt-meshforge-maps/memory/|memory-project-maps"
    do
        src="${pair%%|*}"
        rest="${pair#*|}"
        dst="${rest%%|*}"
        tag="${rest#*|}"

        if [[ ! -d "$src" ]]; then
            echo "SKIP $tag no_source"
            continue
        fi

        if rsync -aq --delete --mkpath \
                  -e 'ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new' \
                  "$src" "$host:$dst" 2>/dev/null; then
            echo "PASS $tag mirrored"
        else
            echo "FAIL $tag rsync_failed"
        fi
    done
}

# Remote recipe. Runs on each target Pi. Prints one tagged summary line per
# (repo, unit) pair so the driver can attribute each result independently.
# Format: TAG <repo_short> <head_or_status> [unit_status]
#   PASS meshforge a48ff82 restarted
#   PASS meshforge-maps 222265e no_unit
#   SKIP meshforge-maps no_repo
REMOTE_SCRIPT='
set -u

# sync_repo <short_name> <repo_path> <unit_name>
# Emits exactly one summary line. Returns 0 on PASS/SKIP, 1 on FAIL so the
# overall exit code reflects how many things broke.
sync_repo() {
    local short="$1" repo="$2" unit="$3" override_old_head="${4:-}"

    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $short no_repo"
        return 0
    fi
    if ! cd "$repo" 2>/dev/null; then
        echo "FAIL $short cd_failed"
        return 1
    fi

    local branch
    branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
    if [ "$branch" != "main" ]; then
        echo "FAIL $short wrong_branch $branch"
        return 1
    fi

    # NOT sudo: pulling as root creates root-owned refs/objects under .git/,
    # which silently break subsequent unprivileged fetches (Insight 7,
    # 2026-05-03). The repo tree is wh6gxz-owned on every fleet box; if a
    # future box has different ownership, this fails LOUD rather than
    # leaking a root-owned ref. Use `sudo -u <user>` if elevation is ever
    # truly required for path access — never `sudo git pull` directly.
    #
    # `override_old_head` lets the caller pin the pre-pull baseline so
    # multiple sync_repo calls against the same repo (gateway + map both
    # live in /opt/meshforge) share one diff window. Without it, the
    # second call sees `old_head == new_head` because the first call
    # already pulled, masking real code changes from the unit it owns.
    local old_head
    if [ -n "$override_old_head" ]; then
        old_head="$override_old_head"
    else
        old_head=$(git rev-parse HEAD 2>/dev/null || echo "")
    fi
    if ! git pull --ff-only origin main >/dev/null 2>pull.err; then
        local msg
        msg=$(tr "\n" "|" < pull.err | head -c 200)
        rm -f pull.err
        echo "FAIL $short git_pull $msg"
        return 1
    fi
    rm -f pull.err
    local new_head
    new_head=$(git rev-parse --short HEAD)
    local new_head_full
    new_head_full=$(git rev-parse HEAD)

    # Idempotently wire the repo-tracked .githooks/ dir as the hooks path so
    # every fleet box runs the pre-commit hook on local edits. core.hooksPath
    # lives in .git/config (per-clone, not repo-tracked); without this every
    # new clone silently skips the hook.
    if [ -x .githooks/pre-commit ] && [ "$(git config --get core.hooksPath || true)" != ".githooks" ]; then
        git config core.hooksPath .githooks 2>/dev/null || true
    fi

    # Decide whether the pulled changes warrant a service restart. Three
    # cases — only the third pays the cold-start cost (6-10 min on the
    # heavy-DB boxes for meshforge-map):
    #   (a) No commits pulled. Restart is pure waste.
    #   (b) Commits pulled but only docs / .md / .claude / tests. Daemon
    #       does not read them at runtime, so the running process keeps
    #       serving correct behavior.
    #   (c) Code, config, templates, or scripts changed. Restart so the
    #       daemon loads the new code.
    # This closes the workflow gap surfaced 2026-05-11 by Path B fleet
    # rollup work: every memory-only commit was triggering a full
    # cold-start cycle on moc/moc1 because sync_repo restarted
    # unconditionally.
    local restart_reason=""
    if [ -z "$old_head" ] || [ "$old_head" = "$new_head_full" ]; then
        restart_reason=""  # case (a) — no change, no restart
    else
        # case (b) vs (c): grep for service-relevant paths in the diff.
        # The narrow include list — only paths the running daemon
        # actually loads at import or invocation time:
        #   src/           — Python runtime modules
        #   scripts/       — shell scripts (incl. ExecStart wrappers)
        #   pyproject.toml — package + tooling config
        #   requirements*.txt — runtime deps (pip install is manual,
        #                       but restarting on dep change is correct)
        # Everything else (tests/, docs/, .claude/, .github/, *.md,
        # README*) is excluded by absence from the pattern. Tests run
        # in CI, docs do not load at runtime, .github is CI-only.
        if git diff --name-only "$old_head" "$new_head_full" 2>/dev/null \
            | grep -qE "^(src|scripts)/|^pyproject\.toml$|^requirements.*\.txt$"; then
            restart_reason="code"
        else
            restart_reason="docs_only"
        fi
    fi

    if [ "$restart_reason" = "docs_only" ]; then
        echo "PASS $short $new_head docs_only"
        return 0
    fi

    if [ -z "$restart_reason" ]; then
        # No commits to apply — repo is already at HEAD. Skip restart;
        # any running service is on the same code the restart would
        # bring up.
        echo "PASS $short $new_head unchanged"
        return 0
    fi

    if systemctl list-unit-files "${unit}.service" 2>/dev/null | grep -q "$unit"; then
        # try-restart only acts if the unit is already active. A disabled+stopped
        # unit stays stopped — operator intent is honored. Without this, every
        # sync would resurrect services we explicitly disabled (e.g. extra
        # gateways on non-canonical boxes). See Issue #47 follow-up 2026-04-26.
        if sudo -n systemctl try-restart "${unit}.service" >/dev/null 2>restart.err; then
            rm -f restart.err
            if systemctl is-active "${unit}.service" >/dev/null 2>&1; then
                echo "PASS $short $new_head restarted"
            else
                echo "PASS $short $new_head not_running"
            fi
        else
            local emsg
            emsg=$(tr "\n" "|" < restart.err | head -c 200)
            rm -f restart.err
            echo "FAIL $short restart $emsg"
            return 1
        fi
    else
        echo "PASS $short $new_head no_unit"
    fi
    return 0
}

# Run all three syncs even if one fails so a broken meshforge-maps does not
# mask a successful meshforge update. meshforge-map is the singular :5000
# map daemon from this repo (separate from the :8808 sister meshforge-maps);
# without it the daemon stays on stale code after a git pull and re-creates
# the project_tcp_contention_pattern starvation (Issue #53, 2026-05-02).
# The second sync_repo call against /opt/meshforge re-pulls the same repo
# (no-op, ~50ms LAN) and try-restarts the meshforge-map unit only when it
# is already active — operator-disabled units stay disabled.
#
# Snapshot pre-pull HEAD per unique repo path BEFORE any sync_repo runs,
# then thread it into both calls that target the same path. This keeps
# the restart decision honest when two units share one repo: a real code
# change to /opt/meshforge correctly restarts BOTH gateway and map; a
# docs-only or no-op pull restarts neither.
MF_PRE_HEAD=$(cd /opt/meshforge       2>/dev/null && git rev-parse HEAD 2>/dev/null || echo "")
MFMAPS_PRE_HEAD=$(cd /opt/meshforge-maps 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo "")
sync_repo meshforge       /opt/meshforge       meshforge-gateway "$MF_PRE_HEAD"     || rc1=$?
sync_repo meshforge-map   /opt/meshforge       meshforge-map     "$MF_PRE_HEAD"     || rc1b=$?
sync_repo meshforge-maps  /opt/meshforge-maps  meshforge-maps    "$MFMAPS_PRE_HEAD" || rc2=$?
exit $(( ${rc1:-0} + ${rc1b:-0} + ${rc2:-0} ))
'

# Pre-sync: auto-commit memory changes on the canonical box and push to
# origin. Runs ONCE before the host loop so all fleet boxes receive the
# same committed state via rsync. If commit is blocked (e.g. the
# secrets-grep pre-commit hook fired), abort BEFORE any fleet propagation.
echo "Pre-sync memory commit:"
memory_commit_failed=0
commit_memory_repo "$HOME/.claude/memory"                               "global-memory"                 || memory_commit_failed=1
commit_memory_repo "$HOME/.claude/projects/-opt-meshforge/memory"       "project-meshforge-memory"      || memory_commit_failed=1
commit_memory_repo "$HOME/.claude/projects/-opt-meshforge-maps/memory"  "project-meshforge-maps-memory" || memory_commit_failed=1

if [[ $memory_commit_failed -ne 0 ]]; then
    cat >&2 <<EOF

Aborting fleet_sync: a memory commit was blocked (likely a secret pattern
caught by the pre-commit hook). Fix the offending file, then re-run:
    git -C \$HOME/.claude/memory                                status
    git -C \$HOME/.claude/projects/-opt-meshforge/memory        status
    git -C \$HOME/.claude/projects/-opt-meshforge-maps/memory   status

This abort is intentional — propagating un-vetted memory state to the fleet
would defeat the secrets gate.
EOF
    exit 3
fi
echo

# Iterate hosts. Each host produces multiple summary lines (one per repo);
# we track host-level pass/fail counts (any FAIL on a host = host failed)
# AND per-action counts so the operator sees both views.
fail_count=0
pass_count=0
skip_count=0
action_pass=0
action_fail=0
action_skip=0

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    # strip leading/trailing whitespace, skip blank + comment
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$host" || "${host:0:1}" == "#" ]] && continue

    # Memory mirror first (local-side rsync). Runs before code pull so a
    # code-sync failure doesn't strand the fleet on a stale memory state.
    memory_summaries="$(mirror_memory_to_host "$host")"

    # SSH with short connect timeout; BatchMode=yes prevents password prompts
    # (operators must use key auth for fleet sync).
    result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                  -o StrictHostKeyChecking=accept-new \
                  "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
    rc=$?

    # Pull all summary lines (PASS/FAIL/SKIP), one per repo, then prepend the
    # memory-mirror summaries from this box.
    code_summaries="$(echo "$result" | grep -E '^(PASS|FAIL|SKIP) ')"
    if [[ -n "$memory_summaries" && -n "$code_summaries" ]]; then
        summaries="$memory_summaries"$'\n'"$code_summaries"
    elif [[ -n "$memory_summaries" ]]; then
        summaries="$memory_summaries"
    else
        summaries="$code_summaries"
    fi

    if [[ -z "$summaries" ]]; then
        printf '[%-30s] SKIP unreachable (ssh rc=%d)\n' "$host" "$rc"
        skip_count=$((skip_count + 1))
        continue
    fi

    host_failed=0
    while IFS= read -r line; do
        printf '[%-30s] %s\n' "$host" "$line"
        case "$line" in
            PASS*) action_pass=$((action_pass + 1)) ;;
            SKIP*) action_skip=$((action_skip + 1)) ;;
            FAIL*) action_fail=$((action_fail + 1)); host_failed=1 ;;
        esac
    done <<< "$summaries"

    if [[ $host_failed -eq 1 ]]; then
        fail_count=$((fail_count + 1))
    else
        pass_count=$((pass_count + 1))
    fi
done < "$FLEET_FILE"

echo
printf 'Hosts:   %d ok, %d failed, %d unreachable\n' \
    "$pass_count" "$fail_count" "$skip_count"
printf 'Actions: %d ok, %d failed, %d skipped (no_repo)\n' \
    "$action_pass" "$action_fail" "$action_skip"

# Exit non-zero if any action failed or any host was unreachable. SKIP from
# no_repo is fine (idempotent install pattern); SKIP from ssh failure is not.
exit "$((action_fail + skip_count))"
