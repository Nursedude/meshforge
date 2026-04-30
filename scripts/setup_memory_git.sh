#!/usr/bin/env bash
# setup_memory_git.sh — initialize Tier-2 git tracking for Claude memory dirs.
#
# For each memory location:
#   ~/.claude/memory/                          (cross-repo / global)
#   ~/.claude/projects/-opt-meshforge/memory/  (this repo's memory)
#
# this script:
#   1. `git init -b main` (idempotent)
#   2. writes a minimal .gitignore (ephemeral lock files)
#   3. installs .git/hooks/pre-commit shim → memory_secrets_check.sh
#   4. makes an initial commit if the repo has no HEAD yet
#
# After this runs, the operator must (one-time per repo):
#   gh repo create Nursedude/<repo-name> --private --source <dir> --push
# OR add a remote manually and push.
#
# Re-running is safe: existing repos and commits are preserved; the hook is
# (re)installed each time so an updated secrets-check picks up.

set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "$(realpath "$0")")" && pwd)"
HOOK_SCRIPT="$REPO_ROOT/memory_secrets_check.sh"

if [[ ! -x "$HOOK_SCRIPT" ]]; then
    echo "ERROR: $HOOK_SCRIPT not found or not executable" >&2
    exit 1
fi

# Pull operator identity from the meshforge repo's git config so the initial
# commit lands under the noreply identity already used in /opt/meshforge.
GIT_NAME="$(git -C "$(dirname "$REPO_ROOT")" config user.name 2>/dev/null || echo Nursedude)"
GIT_EMAIL="$(git -C "$(dirname "$REPO_ROOT")" config user.email 2>/dev/null || echo noreply@anthropic.com)"

setup_repo() {
    local dir="$1"
    local label="$2"

    if [[ ! -d "$dir" ]]; then
        printf '[%-26s] SKIP no_source (%s missing)\n' "$label" "$dir"
        return 0
    fi

    if ! cd "$dir"; then
        printf '[%-26s] FAIL cd\n' "$label"
        return 1
    fi

    local init_status="existing"
    if [[ ! -d .git ]]; then
        git init -q -b main
        init_status="new"
    fi

    if [[ ! -f .gitignore ]]; then
        cat > .gitignore <<'EOF'
# Memory store — exclude ephemeral / lock files only. Memory content itself
# is the whole point of tracking, so do NOT add blanket excludes here.
.consolidate-lock
*.tmp
*.swp
.DS_Store
EOF
    fi

    mkdir -p .git/hooks
    cat > .git/hooks/pre-commit <<EOF
#!/usr/bin/env bash
# Auto-installed by /opt/meshforge/scripts/setup_memory_git.sh — do not edit.
exec "$HOOK_SCRIPT" "\$@"
EOF
    chmod +x .git/hooks/pre-commit

    local commit_status="head_exists"
    if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
        git add -A
        if git -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" \
               commit -q -m "Initial memory snapshot ($(date -u +%Y-%m-%d))"; then
            commit_status="initial_committed"
        else
            commit_status="initial_failed"
            printf '[%-26s] FAIL initial_commit\n' "$label"
            return 1
        fi
    fi

    local short_head
    short_head="$(git rev-parse --short HEAD 2>/dev/null || echo none)"
    printf '[%-26s] OK init=%s hook=installed head=%s status=%s\n' \
        "$label" "$init_status" "$short_head" "$commit_status"
    return 0
}

setup_repo "$HOME/.claude/memory"                          "global-memory"
setup_repo "$HOME/.claude/projects/-opt-meshforge/memory"  "project-meshforge-memory"

cat <<EOF

Local memory git repos initialized. Pre-commit hook will block obvious
secret patterns (PEM keys, AWS/GitHub/Slack tokens, rpc_key assignments).

Next step (operator action — requires gh CLI auth):
    gh repo create Nursedude/claude-memory-global \\
        --private --source "\$HOME/.claude/memory" --push
    gh repo create Nursedude/claude-memory-meshforge \\
        --private --source "\$HOME/.claude/projects/-opt-meshforge/memory" --push

Or, if creating remotes manually on github.com:
    cd ~/.claude/memory
    git remote add origin git@github.com:Nursedude/claude-memory-global.git
    git push -u origin main

    cd ~/.claude/projects/-opt-meshforge/memory
    git remote add origin git@github.com:Nursedude/claude-memory-meshforge.git
    git push -u origin main

After remotes are live: re-run this script (no-op) and update
fleet_sync.sh to switch from rsync push to git pull on fleet boxes.
EOF
