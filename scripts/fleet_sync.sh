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
#   ~/.claude/skills/                                 (user-level Claude skills)
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

# ---------------------------------------------------------------------------
# Flags. --no-restart suppresses THIS box's local daemon self-refresh (the
# meshforge-gateway / meshforge-map / meshforge-maps self-restart near the end
# of the run) while STILL syncing memory, fast-forwarding code everywhere, and
# deploying + restarting the remote fleet (that is the whole point of the host
# loop — suppressing it would leave remotes on stale code, the Issue #53 bug
# the self-refresh exists to prevent). Use it from the canonical box
# which also serves the :5000 federator map, to push to the fleet
# without risking an unwanted local map cold-start that could wedge on a flaky
# rnsd (Issue #68). Default (no flag): local self-restart-on-code-change.
# ---------------------------------------------------------------------------
NO_RESTART=0
MEMORY_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --no-restart) NO_RESTART=1 ;;
        --memory-only) MEMORY_ONLY=1 ;;
        -h|--help)
            echo "Usage: $SELF [--no-restart] [--memory-only]"
            echo "  --no-restart   Sync memory + deploy/restart the remote fleet as"
            echo "                 usual, but do NOT self-restart this box's local"
            echo "                 daemons (gateway/map/maps)."
            echo "  --memory-only  Commit + mirror the Claude memory corpus to every"
            echo "                 fleet box, then EXIT — NO code pull, NO service"
            echo "                 restarts anywhere. Safe to cron: no gateway churn"
            echo "                 (the reason the full sync can't be scheduled)."
            exit 0
            ;;
        *)
            echo "$SELF: unknown argument: $arg" >&2
            echo "Usage: $SELF [--no-restart] [--memory-only]" >&2
            exit 2
            ;;
    esac
done

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
        "$HOME/.claude/projects/-opt-meshforge-maps/memory/|.claude/projects/-opt-meshforge-maps/memory/|memory-project-maps" \
        "$HOME/.claude/skills/|.claude/skills/|claude-skills"
    do
        src="${pair%%|*}"
        rest="${pair#*|}"
        dst="${rest%%|*}"
        tag="${rest#*|}"

        if [[ ! -d "$src" ]]; then
            echo "SKIP $tag no_source"
            continue
        fi

        # Capture stderr — a bare "rsync_failed" is a swallow without a
        # witness (hfm #9): the 07-23 kiai claude-skills FAIL was undiagnosable
        # because the error went to /dev/null, and by the time a human re-ran
        # it the transient had cleared. One retry absorbs exactly that class
        # (ssh blip / vanished-file race); a REAL failure now names itself.
        local m_err m_rc
        m_err=$(rsync -aq --delete --mkpath \
                  -e 'ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new' \
                  "$src" "$host:$dst" 2>&1) && m_rc=0 || m_rc=$?
        if [[ $m_rc -ne 0 ]]; then
            m_err=$(rsync -aq --delete --mkpath \
                      -e 'ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new' \
                      "$src" "$host:$dst" 2>&1) && m_rc=0 || m_rc=$?
        fi
        if [[ $m_rc -eq 0 ]]; then
            echo "PASS $tag mirrored"
        else
            m_err=${m_err//$'\n'/ | }
            echo "FAIL $tag rsync_failed rc=$m_rc ${m_err:0:120}"
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
        # Tight include list — only paths the running daemon actually
        # imports at runtime:
        #   src/             — Python modules loaded by the daemon
        #   pyproject.toml   — package metadata + tooling
        #   requirements*.txt — runtime deps
        #
        # NOT included (intentionally):
        #   scripts/         — operator tooling. fleet_sync.sh, lint.py,
        #                      diag_*.sh, install_*.sh, cloud/* run out
        #                      of band; the daemon does not import them.
        #                      Changing them does not require a daemon
        #                      restart. (Surfaced 2026-05-11 by
        #                      scripts/cloud/push_snapshot.sh tuning
        #                      that triggered fleet-wide map restarts
        #                      for no benefit.)
        #   templates/       — copy targets for setup scripts. Even if
        #                      a *.service file changes, fleet_sync
        #                      uses try-restart not daemon-reload, so
        #                      a unit-file change would not take
        #                      effect via this script anyway.
        #   tests/ docs/ .claude/ .github/ *.md — never load at runtime.
        if git diff --name-only "$old_head" "$new_head_full" 2>/dev/null \
            | grep -qE "^src/|^pyproject\.toml$|^requirements.*\.txt$"; then
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

# sync_user_unit <short> <repo_path> <unit_name> <pre_head>
# USER-bus sibling of sync_repo for operator-scope units (no system unit,
# no sudo). The mini-dudeai daemon runs as a `systemctl --user` unit; nothing
# restarted it after a git pull before this, so it sat on OLD code until a
# hand-restart. Same code-vs-docs restart gate + try-restart (honor disabled)
# semantics as sync_repo, but on the user bus.
#
# The remote recipe runs as the operator over SSH; `systemctl --user` needs
# XDG_RUNTIME_DIR. Non-interactive SSH may not set it, so derive it from the
# operator uid and export it for the call.
sync_user_unit() {
    local short="$1" repo="$2" unit="$3" pre_head="$4"

    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $short no_repo"
        return 0
    fi

    # The repo was already pulled by sync_repo above (same path); compare the
    # caller-pinned pre-pull HEAD to the now-current HEAD using the same
    # code-vs-docs include list. No commits / docs-only -> no restart.
    local new_head_full new_head
    new_head_full=$(cd "$repo" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo "")
    new_head=$(cd "$repo" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo "?")

    if [ -z "$pre_head" ] || [ "$pre_head" = "$new_head_full" ]; then
        echo "PASS $short $new_head unchanged"
        return 0
    fi
    if ! (cd "$repo" 2>/dev/null && git diff --name-only "$pre_head" "$new_head_full" 2>/dev/null \
            | grep -qE "^src/|^pyproject\.toml$|^requirements.*\.txt$"); then
        echo "PASS $short $new_head docs_only"
        return 0
    fi

    local uid xdg
    uid=$(id -u 2>/dev/null || echo "")
    xdg="${XDG_RUNTIME_DIR:-}"
    if [ -z "$xdg" ] && [ -n "$uid" ]; then
        xdg="/run/user/$uid"
    fi

    # Existence check must be template-aware: for an instance unit
    # (name@inst) no unit FILE named name@inst.service ever exists — only
    # the template name@.service does — so list-unit-files on the instance
    # name matches nothing and this guard read PASS no_unit forever while
    # the instance ran on stale code (#79 re-opened, found by the 07-23
    # audit; verified against live systemd: getty@tty1 -> 0 unit files).
    local unit_file="$unit"
    case "$unit" in
        *@*) unit_file="${unit%%@*}@" ;;
    esac
    if ! XDG_RUNTIME_DIR="$xdg" systemctl --user list-unit-files "${unit_file}.service" 2>/dev/null | grep -q "${unit_file}.service"; then
        echo "PASS $short $new_head no_unit"
        return 0
    fi
    # try-restart only acts on an already-active unit; an operator-disabled
    # mini daemon stays stopped.
    if XDG_RUNTIME_DIR="$xdg" systemctl --user try-restart "${unit}.service" >/dev/null 2>uunit.err; then
        rm -f uunit.err
        if XDG_RUNTIME_DIR="$xdg" systemctl --user is-active "${unit}.service" >/dev/null 2>&1; then
            echo "PASS $short $new_head restarted"
        else
            echo "PASS $short $new_head not_running"
        fi
    else
        local emsg
        emsg=$(tr "\n" "|" < uunit.err | head -c 200)
        rm -f uunit.err
        echo "FAIL $short user_restart $emsg"
        return 1
    fi
    return 0
}

# promote_seed <short> <repo_path>
# Fold the role-seed promotion into the deploy itself.
#
# Before this, a seed bump landed as CODE on every box while each live
# rules file stayed behind until someone hand-ran promote_seed_rules.py
# --apply per box. That gap made rules_seed_drift ~15% of ALL fleet
# escalation volume (signal-yield pass, 2026-07-26, 25 fires over the
# window, every one a true positive that cleared in 9 min to 4 h). The
# detector was right every time; the DEPLOY was missing a step. Same
# shape as the #79 deploy-restart gap: code shipped, state did not.
#
# Deliberately UNCONDITIONAL rather than gated on a seed-file diff. The
# tool is idempotent (already in sync -> no write) and dry-run by default,
# so --apply is required and a no-op costs one python start. Gating on
# the diff would be an optimisation that reopens the gap for drift with
# any other cause -- a restored backup, a hand-edited live file -- which
# is exactly the class the probe exists to catch.
#
# Runs BEFORE the mini restart so the daemon comes up on the merged rules.
#
# A box with no declared role exits 2 BY DESIGN (meshanchor-server takes
# no MeshForge seed -- its no-declared-role error is correct, not a bug).
# That is never fatal to the deploy, and never silent either: it reports
# as NOTE with the real reason attached, so a genuine failure cannot hide
# behind the legitimate skip (honest_failure_modes #9).
#
# (Recipe rule: this body must stay single-quote-free -- an apostrophe
# terminates the outer REMOTE_SCRIPT string, see the note ~line 451.)
promote_seed() {
    local short="$1" repo="$2"

    if [ ! -d "$repo/.git" ]; then
        echo "SKIP $short no_repo"
        return 0
    fi
    if [ ! -f "$repo/scripts/promote_seed_rules.py" ]; then
        echo "SKIP $short no_tool"
        return 0
    fi

    local head out rc
    head=$(cd "$repo" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo "?")
    out=$(cd "$repo" 2>/dev/null && python3 scripts/promote_seed_rules.py --apply 2>&1)
    rc=$?

    if [ "$rc" -eq 0 ]; then
        if printf "%s" "$out" | grep -q "already in sync"; then
            echo "PASS $short $head seed_in_sync"
        else
            echo "PASS $short $head seed_promoted"
        fi
        return 0
    fi

    local emsg
    emsg=$(printf "%s" "$out" | tr "\n" "|" | head -c 140)
    echo "NOTE $short $head seed_not_promoted rc=$rc $emsg"
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
# Per-box watchdog (Phase 1 reliability — Issues #58-#69). Same repo as
# meshforge-gateway/meshforge-map, so the re-pull is a no-op; the
# try-restart picks up code changes in src/utils/watchdog_runner.py +
# src/utils/watchdog_probes.py + src/utils/watchdog_actions.py.
# Operator-disabled units stay disabled (try-restart semantics).
sync_repo meshforge-watchdog /opt/meshforge   meshforge-watchdog "$MF_PRE_HEAD"     || rc1c=$?
# mini-dudeai runs on the USER bus (systemctl --user), not as a system unit,
# so the sync_repo sudo try-restart cannot reach it. This user-bus sibling picks
# up src/mini_dudeai/* code changes from the same /opt/meshforge pull, so a git
# pull actually lands new mini code on the running daemon (the deploy gap).
# (Recipe rule: this body must stay single-quote-free — an apostrophe here
# terminates the outer REMOTE_SCRIPT string, see the note ~line 451.)
# Promote the role seed BEFORE the mini restart, so the daemon comes up on
# the merged rules instead of racing a later hand-promote (see promote_seed).
promote_seed meshforge-mini-seed /opt/meshforge || rc1s=$?
sync_user_unit meshforge-mini-dudeai /opt/meshforge meshforge-mini-dudeai "$MF_PRE_HEAD" || rc1d=$?
# Standalone dude-claw instance (second mini-dudeai daemon, claw-suffixed
# state). Only the claw-brain box (moc1) runs it; everywhere else this is a
# clean no_unit/not_running PASS (try-restart honors absent/disabled).
sync_user_unit meshforge-mini-dudeai-claw /opt/meshforge meshforge-mini-dudeai-claw "$MF_PRE_HEAD" || rc1e=$?
# Templated dude-claw brains (meshforge-mini-dudeai-claw@<inst>; W5.1).
# Enumerate ACTIVE instances instead of hardcoding claw02 — a future @claw03
# enrollment would silently re-open #79 otherwise. try-restart only acts on
# active units anyway, so the active-instance list IS the restart population.
uid2=$(id -u 2>/dev/null || echo "")
xdg2="${XDG_RUNTIME_DIR:-}"
if [ -z "$xdg2" ] && [ -n "$uid2" ]; then
    xdg2="/run/user/$uid2"
fi
for cin in $(XDG_RUNTIME_DIR="$xdg2" systemctl --user list-units --plain --no-legend "meshforge-mini-dudeai-claw@*.service" 2>/dev/null | cut -d" " -f1); do
    sync_user_unit "${cin%.service}" /opt/meshforge "${cin%.service}" "$MF_PRE_HEAD" || rc1e2=$?
done
# Other long-lived USER daemons that run /opt/meshforge code (same #79 deploy
# gap as mini): the lab echo responder (lab.lxmf_echo) and the nomadnet silence
# watcher (scripts/nomadnet_silence_watch.py). Restart on the user bus so a code
# pull reaches the running daemon. no_unit / not_running PASS on boxes that do
# not run them (try-restart honors absent/disabled).
sync_user_unit meshforge-echo /opt/meshforge meshforge-echo "$MF_PRE_HEAD" || rc1f=$?
sync_user_unit nomadnet-silence-watch /opt/meshforge nomadnet-silence-watch "$MF_PRE_HEAD" || rc1g=$?
sync_repo meshforge-maps  /opt/meshforge-maps  meshforge-maps    "$MFMAPS_PRE_HEAD" || rc2=$?

# Smoke: catch rollup self-loopback in ~/.config/meshanchor/fleet.json.
# An entry whose host resolves to this box (hostname / hostname.local /
# 127.0.0.1 / ::1 / one of our interface IPs) makes the MA rollup
# HTTP-poll its own listener on every dashboard tick, doubling
# handler-thread arrival rate and reproducing MA Issue #34 in ~8 min.
# `non_self_peers()` filters by NAME only (MA #130), so any operator-
# composed fleet.json that names the self entry anything other than
# the literal local hostname will silently regress. Catching at sync
# time keeps the misconfiguration from sitting silently until the
# blackout banner fires. Emits one WARN per offending peer; never
# fails the sync.
fleet_json="$HOME/.config/meshanchor/fleet.json"
if [ -r "$fleet_json" ]; then
    python3 - "$fleet_json" <<'PY' 2>/dev/null || true
import json, socket, sys
# NOTE: keep this body single-quote-free. REMOTE_SCRIPT (and the self-side
# smoke_line capture) wraps this python source in an outer bash single-
# quoted string; an apostrophe here terminates that string and concatenates
# bare tokens into the heredoc body, silently mangling the python source.
# Caught 2026-05-16: an earlier data.get with single-quoted key rendered
# remotely as data.get(peers, []) -> NameError, swallowed by 2>/dev/null.
try:
    fp = sys.argv[1]
    data = json.load(open(fp))
except Exception as e:
    print(f"WARN ma-fleet-json unparseable: {type(e).__name__}")
    sys.exit(0)
hn = socket.gethostname()
self_hosts = {hn, hn.lower(), f"{hn}.local",
              "localhost", "127.0.0.1", "::1", "0.0.0.0"}
try:
    for ip in socket.gethostbyname_ex(hn)[2]:
        self_hosts.add(ip)
except Exception:
    pass
peers = data.get("peers", []) or []
hits = []
for p in peers:
    if not isinstance(p, dict):
        continue
    host = str(p.get("host", "")).strip().lower()
    if host in self_hosts:
        hits.append((p.get("name", "?"), host, p.get("port", "?")))
if hits:
    for name, host, port in hits:
        print(f"WARN ma-fleet-json self_loopback name={name} host={host} port={port} — MA rollup will HTTP-poll its own listener, see MA Issue #130")
else:
    n = len(peers)
    print(f"PASS ma-fleet-json no_self_loopback peers={n}")
PY
fi

# MF fleet.json — different schema (keyed dict, peer.ip not peer.host+port),
# different bug class. MF carries a load-bearing self entry under this_host
# (used for backup target identity AND for federation self-elimination at
# src/utils/map_data_collector.py:_bootstrap_federation_peers). The risk
# here is NOT a stray self peer — that one is intentional — but a
# this_host VALUE that does not match the real local hostname / IP. When
# that happens, federation_peers ends up including this box, and the
# meshforge-map daemon HTTP-polls its own /api/nodes/directory every
# federation cycle (60s default). Same handler-thread amplifier shape as
# MA, just rarer and slower to surface.
mf_fleet_json="$HOME/.config/meshforge/fleet.json"
if [ -r "$mf_fleet_json" ]; then
    python3 - "$mf_fleet_json" <<'PY' 2>/dev/null || true
import json, socket, sys
# Single-quote-free body — see MA smoke note above.
try:
    fp = sys.argv[1]
    data = json.load(open(fp))
except Exception as e:
    print(f"WARN mf-fleet-json unparseable: {type(e).__name__}")
    sys.exit(0)
hn = socket.gethostname()
self_tokens = {hn, hn.lower(), f"{hn}.local"}
try:
    for ip in socket.gethostbyname_ex(hn)[2]:
        self_tokens.add(ip)
except Exception:
    pass
# gethostbyname_ex often returns 127.0.1.1 from /etc/hosts on Debian
# boxes — not the actual LAN IP. Augment with `hostname -I`, which
# lists every non-loopback interface address. Without this, a peer
# entry whose ip is the LAN address of this box would slip through.
try:
    import subprocess as _sp
    out = _sp.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
    for ip in (out.stdout or "").split():
        if ip:
            self_tokens.add(ip)
except Exception:
    pass
self_tokens.update({"localhost", "127.0.0.1", "::1"})
this_host = data.get("this_host")
peers_dict = data.get("peers") or {}
problems = []
if not isinstance(this_host, str) or not this_host.strip():
    problems.append("missing_this_host")
elif this_host.lower() not in {t.lower() for t in self_tokens}:
    # `this_host` is set, but does not match any local identity we recognize.
    # The federation self-elim filters by EXACT name match against this_host
    # (case-insensitive); a mismatch means the peer dict entry for THIS box
    # will not be filtered out -> HTTP self-poll on every federation cycle.
    problems.append(f"this_host_mismatch={this_host}")
# Defense-in-depth: if some peer entry whose dict-key is NOT this_host
# resolves to a local IP, federation will also poll self.
if isinstance(peers_dict, dict) and isinstance(this_host, str):
    for name, info in peers_dict.items():
        if name.lower() == (this_host or "").lower():
            continue
        if not isinstance(info, dict):
            continue
        ip = str(info.get("ip", "")).strip().lower()
        if ip and ip in {t.lower() for t in self_tokens}:
            problems.append(f"peer_self_ip name={name} ip={ip}")
if problems:
    for prob in problems:
        print(f"WARN mf-fleet-json {prob} — federation may HTTP-poll its own listener; check this_host + peer IPs in ~/.config/meshforge/fleet.json")
else:
    n = len(peers_dict) if isinstance(peers_dict, dict) else 0
    print(f"PASS mf-fleet-json this_host={this_host} peers={n}")
PY
fi

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
action_warn=0

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    # strip leading/trailing whitespace, skip blank + comment
    host="$(echo "$raw_line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$host" || "${host:0:1}" == "#" ]] && continue

    # Memory mirror first (local-side rsync). Runs before code pull so a
    # code-sync failure doesn't strand the fleet on a stale memory state.
    memory_summaries="$(mirror_memory_to_host "$host")"

    # SSH with short connect timeout; BatchMode=yes prevents password prompts
    # (operators must use key auth for fleet sync). Skipped in --memory-only
    # mode: that mode is the memory mirror above and nothing else — no code
    # pull, no service restart on any remote.
    if [ "$MEMORY_ONLY" = "1" ]; then
        result=""; rc=0
    else
        result="$(ssh -o BatchMode=yes -o ConnectTimeout=10 \
                      -o StrictHostKeyChecking=accept-new \
                      "$host" "bash -s" <<< "$REMOTE_SCRIPT" 2>&1)"
        rc=$?
    fi

    # Pull all summary lines (PASS/FAIL/SKIP/WARN), one per check, then
    # prepend the memory-mirror summaries from this box. WARN is the
    # advisory tier added by smoke checks (e.g. ma-fleet-json self-loopback):
    # surfaced but never counted as failure since the underlying issue is
    # a config misalignment the operator must fix manually.
    code_summaries="$(echo "$result" | grep -E '^(PASS|FAIL|SKIP|WARN) ')"
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
            WARN*) action_warn=$((action_warn + 1)) ;;
            FAIL*) action_fail=$((action_fail + 1)); host_failed=1 ;;
        esac
    done <<< "$summaries"

    if [[ $host_failed -eq 1 ]]; then
        fail_count=$((fail_count + 1))
    else
        pass_count=$((pass_count + 1))
    fi
done < "$FLEET_FILE"

# --memory-only stops here: memory was committed+pushed (pre-sync, above) and
# mirrored to every fleet box (host loop, above). Everything below is the
# code-deploy half — local self-sync + gateway/map/maps restarts — which is
# exactly the gateway-churn this mode exists to avoid, so it is safe to cron.
if [ "$MEMORY_ONLY" = "1" ]; then
    echo
    printf 'Hosts:   %d ok, %d failed, %d unreachable\n' \
        "$pass_count" "$fail_count" "$skip_count"
    printf 'Actions: %d ok, %d failed, %d warn, %d skipped (memory-only)\n' \
        "$action_pass" "$action_fail" "$action_warn" "$action_skip"
    exit "$((action_fail + skip_count))"
fi

# Local-box self-sync — fleet_hosts excludes this box (the operator
# runs fleet_sync FROM here), so the remote loop never reaches it.
# After a `git push` from here, the local daemons run on the
# pre-push code until something else restarts them — which is the
# silent-stale-daemon class we've already burned cycles on (Issue
# #53, 2026-05-02; also the 2026-05-16 tracer-fires drilldown ship
# where this box served 404s for ~33min until manual restart).
#
# Mechanism: for each daemon under the two MF repos, compare the
# daemon's process start time to the newest commit touching code
# paths (the same regex the remote classifier uses). If the daemon
# predates the newest code commit, restart it. Output format
# matches the remote sync (one tagged line per unit) so the operator
# scans one table.
sync_local_unit() {
    local unit="$1" repo="$2"
    local self_tag
    self_tag="self ($(hostname -s))"

    if ! systemctl list-unit-files "${unit}.service" 2>/dev/null | grep -q "$unit"; then
        return 0  # not installed locally — silent skip
    fi
    if ! systemctl is-active "${unit}.service" >/dev/null 2>&1; then
        # Honor operator-disabled / not-running units the same way the
        # remote sync_repo does — don't resurrect them.
        return 0
    fi
    if [ ! -d "$repo/.git" ]; then
        return 0
    fi

    local pid daemon_started newest_code_commit now
    pid="$(systemctl show "${unit}.service" -p MainPID --value 2>/dev/null)"
    if [ -z "$pid" ] || [ "$pid" = "0" ]; then
        return 0
    fi
    # /proc/<pid> directory's mtime is the process creation time
    # (kernel sets it on fork). Reliable on Linux; older than the
    # daemon's first request handling, which is what we want.
    daemon_started="$(stat -c %Y "/proc/$pid" 2>/dev/null || echo 0)"
    if [ "$daemon_started" = "0" ]; then
        return 0  # couldn't read; skip silently rather than thrash
    fi

    # Most-recent commit touching daemon-relevant paths. Matches the
    # remote classifier's include list: src/, pyproject.toml,
    # requirements*.txt. `--` separates path filters from refs.
    newest_code_commit="$(git -C "$repo" log -1 --format=%ct \
        -- 'src/*' 'pyproject.toml' 'requirements*.txt' 2>/dev/null)"
    if [ -z "$newest_code_commit" ]; then
        return 0  # no code commits ever (unlikely) — skip
    fi

    if [ "$newest_code_commit" -le "$daemon_started" ]; then
        # Daemon is current. Emit a clean line so the operator can
        # confirm self was checked (silence here would be confusing).
        printf '[%-30s] PASS %s current (no restart needed)\n' \
            "$self_tag" "$unit"
        action_pass=$((action_pass + 1))
        return 0
    fi

    if [ "$NO_RESTART" = "1" ]; then
        printf '[%-30s] SKIP %s self-restart suppressed (--no-restart; code changed)\n' \
            "$self_tag" "$unit"
        action_skip=$((action_skip + 1))
        return 0
    fi

    now="$(date +%s)"
    local age_s=$(( now - daemon_started ))
    local lag_s=$(( newest_code_commit - daemon_started ))
    if sudo -n systemctl restart "${unit}.service" >/dev/null 2>&1; then
        printf '[%-30s] PASS %s restarted (was %ds old, %ds behind code)\n' \
            "$self_tag" "$unit" "$age_s" "$lag_s"
        action_pass=$((action_pass + 1))
    else
        printf '[%-30s] FAIL %s restart (sudo or systemctl error)\n' \
            "$self_tag" "$unit"
        action_fail=$((action_fail + 1))
        # Self-restart failure doesn't fail the whole sync — the remote
        # propagation may still have succeeded. Operators see it in the
        # summary lines and can investigate.
    fi
}

# USER-bus sibling of sync_local_unit. mini-dudeai runs as a `systemctl --user`
# unit on this box too, so the sudo system-bus restart above can't reach it.
# fleet_sync runs AS the operator here (not over SSH), so `systemctl --user`
# works natively. Same proc-mtime vs newest-code-commit staleness check.
# Self-side twin of promote_seed (remote leg, see ~line 435). Keep the two in
# parity: the 2026-06-09 watchdog case is the standing lesson — the self leg
# omitted a restart the remote leg had, so a code change reached all 5 remotes
# and silently NOT this box until a hand-restart. A seed promotion that runs
# everywhere except the manager box is that same half-wired shape
# (honest_failure_modes #4), and the manager is where seeds are authored.
promote_local_seed() {
    local repo="$1"
    local self_tag
    self_tag="self ($(hostname -s))"

    [ -d "$repo/.git" ] || return 0
    [ -f "$repo/scripts/promote_seed_rules.py" ] || return 0

    local out rc
    out=$(cd "$repo" 2>/dev/null && python3 scripts/promote_seed_rules.py --apply 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ]; then
        if printf '%s' "$out" | grep -q "already in sync"; then
            return 0          # quiet: the common, healthy case
        fi
        printf '[%-30s] OK   %s\n' "$self_tag" "mini seed promoted"
        return 0
    fi
    # Never fatal (a box with no declared role exits 2 by design), never
    # silent either — a real failure must not hide behind the legit skip.
    printf '[%-30s] NOTE %s rc=%s %s\n' "$self_tag" "mini seed not promoted" \
        "$rc" "$(printf '%s' "$out" | tr '\n' '|' | head -c 120)"
    return 0
}

sync_local_user_unit() {
    local unit="$1" repo="$2"
    local self_tag
    self_tag="self ($(hostname -s))"

    # Template-aware existence check (same #79 re-open as the remote leg:
    # an instance name never matches a unit FILE, only its template does).
    local unit_file="$unit"
    case "$unit" in
        *@*) unit_file="${unit%%@*}@" ;;
    esac
    if ! systemctl --user list-unit-files "${unit_file}.service" 2>/dev/null | grep -q "${unit_file}.service"; then
        return 0  # not installed locally — silent skip
    fi
    if ! systemctl --user is-active "${unit}.service" >/dev/null 2>&1; then
        return 0  # honor operator-disabled / not-running units
    fi
    if [ ! -d "$repo/.git" ]; then
        return 0
    fi

    local pid daemon_started newest_code_commit now
    pid="$(systemctl --user show "${unit}.service" -p MainPID --value 2>/dev/null)"
    if [ -z "$pid" ] || [ "$pid" = "0" ]; then
        return 0
    fi
    daemon_started="$(stat -c %Y "/proc/$pid" 2>/dev/null || echo 0)"
    if [ "$daemon_started" = "0" ]; then
        return 0
    fi

    newest_code_commit="$(git -C "$repo" log -1 --format=%ct \
        -- 'src/*' 'pyproject.toml' 'requirements*.txt' 2>/dev/null)"
    if [ -z "$newest_code_commit" ]; then
        return 0
    fi

    if [ "$newest_code_commit" -le "$daemon_started" ]; then
        printf '[%-30s] PASS %s current (no restart needed)\n' \
            "$self_tag" "$unit"
        action_pass=$((action_pass + 1))
        return 0
    fi

    if [ "$NO_RESTART" = "1" ]; then
        printf '[%-30s] SKIP %s self-restart suppressed (--no-restart; code changed)\n' \
            "$self_tag" "$unit"
        action_skip=$((action_skip + 1))
        return 0
    fi

    now="$(date +%s)"
    local age_s=$(( now - daemon_started ))
    local lag_s=$(( newest_code_commit - daemon_started ))
    if systemctl --user restart "${unit}.service" >/dev/null 2>&1; then
        printf '[%-30s] PASS %s restarted (was %ds old, %ds behind code)\n' \
            "$self_tag" "$unit" "$age_s" "$lag_s"
        action_pass=$((action_pass + 1))
    else
        printf '[%-30s] FAIL %s user-restart (systemctl --user error)\n' \
            "$self_tag" "$unit"
        action_fail=$((action_fail + 1))
    fi
}

echo
echo "Self-sync (this box):"
sync_local_unit meshforge-gateway /opt/meshforge
sync_local_unit meshforge-map     /opt/meshforge
# The federator (self) runs the watchdog too. The remote leg restarts it via
# sync_repo, but the self leg omitted it — so a watchdog code change (e.g. a new
# probe) silently did NOT reach THIS box until a manual restart. Caught
# 2026-06-09: the 3 new mini probes (#79) deployed to all 5 remotes but not the
# federator. Keep self in parity with the remote watchdog restart.
sync_local_unit meshforge-watchdog /opt/meshforge
sync_local_unit meshforge-maps    /opt/meshforge-maps
# Promote the role seed BEFORE the restart, so the daemon comes up on the
# merged rules (self-side parity with the remote leg).
promote_local_seed /opt/meshforge
# mini-dudeai is a USER unit — restart it on the user bus when its code changed.
sync_local_user_unit meshforge-mini-dudeai /opt/meshforge
# Standalone dude-claw sibling (no-op on boxes without the unit).
sync_local_user_unit meshforge-mini-dudeai-claw /opt/meshforge
# Templated dude-claw brains — enumerate ACTIVE instances (no hardcoded
# claw02; a future @claw03 would silently re-open #79). no-op on boxes
# without any instance.
for cin in $(systemctl --user list-units --plain --no-legend "meshforge-mini-dudeai-claw@*.service" 2>/dev/null | cut -d" " -f1); do
    sync_local_user_unit "${cin%.service}" /opt/meshforge
done
# Other long-lived USER daemons from this repo (same #79 gap): the echo
# responder + nomadnet silence watcher. no-op on boxes without the unit.
sync_local_user_unit meshforge-echo /opt/meshforge
sync_local_user_unit nomadnet-silence-watch /opt/meshforge

# Self-side fleet-config smokes. Mirrors the remote checks so the
# canonical box (which fleet_hosts deliberately excludes) gets the same
# advisories. Same WARN-not-FAIL semantics.
self_tag="self ($(hostname -s))"

# Helper: print a tagged line + bump action counters.
emit_self_smoke() {
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        printf '[%-30s] %s\n' "$self_tag" "$line"
        case "$line" in
            PASS*) action_pass=$((action_pass + 1)) ;;
            WARN*) action_warn=$((action_warn + 1)) ;;
        esac
    done
}

self_fleet_json="$HOME/.config/meshanchor/fleet.json"
if [ -r "$self_fleet_json" ]; then
    python3 - "$self_fleet_json" <<'PY' 2>/dev/null | emit_self_smoke
import json, socket, sys
# Keep this body single-quote-free; it is duplicated verbatim above in
# REMOTE_SCRIPT where outer bash single-quoting would mangle apostrophes.
try:
    fp = sys.argv[1]
    data = json.load(open(fp))
except Exception as e:
    print(f"WARN ma-fleet-json unparseable: {type(e).__name__}")
    sys.exit(0)
hn = socket.gethostname()
self_hosts = {hn, hn.lower(), f"{hn}.local",
              "localhost", "127.0.0.1", "::1", "0.0.0.0"}
try:
    for ip in socket.gethostbyname_ex(hn)[2]:
        self_hosts.add(ip)
except Exception:
    pass
peers = data.get("peers", []) or []
hits = []
for p in peers:
    if not isinstance(p, dict):
        continue
    host = str(p.get("host", "")).strip().lower()
    if host in self_hosts:
        hits.append((p.get("name", "?"), host, p.get("port", "?")))
if hits:
    for name, host, port in hits:
        print(f"WARN ma-fleet-json self_loopback name={name} host={host} port={port} — MA rollup will HTTP-poll its own listener, see MA Issue #130")
else:
    n = len(peers)
    print(f"PASS ma-fleet-json no_self_loopback peers={n}")
PY
fi

self_mf_fleet_json="$HOME/.config/meshforge/fleet.json"
if [ -r "$self_mf_fleet_json" ]; then
    python3 - "$self_mf_fleet_json" <<'PY' 2>/dev/null | emit_self_smoke
import json, socket, sys
# Single-quote-free body — see MA smoke note above.
try:
    fp = sys.argv[1]
    data = json.load(open(fp))
except Exception as e:
    print(f"WARN mf-fleet-json unparseable: {type(e).__name__}")
    sys.exit(0)
hn = socket.gethostname()
self_tokens = {hn, hn.lower(), f"{hn}.local"}
try:
    for ip in socket.gethostbyname_ex(hn)[2]:
        self_tokens.add(ip)
except Exception:
    pass
# gethostbyname_ex often returns 127.0.1.1 from /etc/hosts on Debian
# boxes — not the actual LAN IP. Augment with `hostname -I`, which
# lists every non-loopback interface address. Without this, a peer
# entry whose ip is the LAN address of this box would slip through.
try:
    import subprocess as _sp
    out = _sp.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
    for ip in (out.stdout or "").split():
        if ip:
            self_tokens.add(ip)
except Exception:
    pass
self_tokens.update({"localhost", "127.0.0.1", "::1"})
this_host = data.get("this_host")
peers_dict = data.get("peers") or {}
problems = []
if not isinstance(this_host, str) or not this_host.strip():
    problems.append("missing_this_host")
elif this_host.lower() not in {t.lower() for t in self_tokens}:
    problems.append(f"this_host_mismatch={this_host}")
if isinstance(peers_dict, dict) and isinstance(this_host, str):
    for name, info in peers_dict.items():
        if name.lower() == (this_host or "").lower():
            continue
        if not isinstance(info, dict):
            continue
        ip = str(info.get("ip", "")).strip().lower()
        if ip and ip in {t.lower() for t in self_tokens}:
            problems.append(f"peer_self_ip name={name} ip={ip}")
if problems:
    for prob in problems:
        print(f"WARN mf-fleet-json {prob} — federation may HTTP-poll its own listener; check this_host + peer IPs in ~/.config/meshforge/fleet.json")
else:
    n = len(peers_dict) if isinstance(peers_dict, dict) else 0
    print(f"PASS mf-fleet-json this_host={this_host} peers={n}")
PY
fi

# --------------------------------------------------------------------------
# Fleet role check (v2.1) — read-only singleton/role-invariant validation via
# the provisioner. Advisory: surfaces role drift in the sync report, never
# mutates anything and never fails the sync (counts as a warn). Reuses the
# script's own ssh auth via $MESHFORGE_SSH. Skip with MESHFORGE_SKIP_ROLE_CHECK=1.
# --------------------------------------------------------------------------
if [[ -z "${MESHFORGE_SKIP_ROLE_CHECK:-}" && -f /opt/meshforge/scripts/provision_role.py ]]; then
    echo
    echo "Fleet role check:"
    role_out="$(MESHFORGE_SSH='ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new' \
        python3 /opt/meshforge/scripts/provision_role.py --fleet-check 2>&1)"
    role_rc=$?
    echo "$role_out" | sed 's/^/  /'
    if [[ $role_rc -ne 0 ]]; then
        printf '[%-30s] WARN fleet role invariant — see above\n' "role-check"
        action_warn=$((action_warn + 1))
    fi
fi

echo
printf 'Hosts:   %d ok, %d failed, %d unreachable\n' \
    "$pass_count" "$fail_count" "$skip_count"
printf 'Actions: %d ok, %d failed, %d warn, %d skipped (no_repo)\n' \
    "$action_pass" "$action_fail" "$action_warn" "$action_skip"

# Exit non-zero if any action failed or any host was unreachable. SKIP from
# no_repo is fine (idempotent install pattern); SKIP from ssh failure is not.
exit "$((action_fail + skip_count))"
