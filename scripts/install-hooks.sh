#!/bin/bash
# Install MeshForge git hooks via core.hooksPath.
#
# Usage: ./scripts/install-hooks.sh
#
# Sets git's core.hooksPath to the repo-tracked .githooks/ directory so the
# pre-commit hook is shared across clones (vs. copying into per-clone
# .git/hooks/, which drifts). Idempotent: re-running is a no-op once configured.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

if [ ! -d .git ]; then
    echo "ERROR: $REPO_ROOT is not a git repo." >&2
    exit 1
fi

if [ ! -x .githooks/pre-commit ]; then
    echo "ERROR: .githooks/pre-commit missing or not executable." >&2
    exit 1
fi

current=$(git config --get core.hooksPath || true)
if [ "$current" = ".githooks" ]; then
    echo "core.hooksPath already set to .githooks (no-op)."
else
    git config core.hooksPath .githooks
    echo "Set core.hooksPath = .githooks"
fi

echo ""
echo "Pre-commit hook active. Bypass with: git commit --no-verify"
