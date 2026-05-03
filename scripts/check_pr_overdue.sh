#!/bin/bash
# scripts/check_pr_overdue.sh — quick safety check before merging a PR.
#
# Driven by 2026-05-03 PR #1153 incident: web-Claude opened a PR with a
# 2-line CI fix in the title, but the branch was 54 commits behind main
# and would have reverted ~10,875 lines of recent work (MeshChatX,
# federation, node_history Issue #49/50/52). The diff stat is the only
# honest signal — title + summary can hide the real blast radius.
#
# Run before merging ANY PR (especially web-Claude's). The "lines vs.
# title scope" mismatch is the alarm.
#
# Usage: scripts/check_pr_overdue.sh <PR-number>
# Example: scripts/check_pr_overdue.sh 1153

set -u

if [ $# -ne 1 ]; then
    echo "Usage: $0 <PR-number>" >&2
    exit 64
fi

PR="$1"

if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI not installed" >&2
    exit 1
fi

# Title + headRefName for context.
META="$(gh pr view "$PR" --json title,headRefName,baseRefName,mergeable 2>/dev/null)"
if [ -z "$META" ]; then
    echo "Could not fetch PR #$PR" >&2
    exit 1
fi

TITLE="$(echo "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["title"])')"
HEAD_REF="$(echo "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["headRefName"])')"
BASE_REF="$(echo "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["baseRefName"])')"

printf "\n\033[1;36m=== PR #%s ===\033[0m\n" "$PR"
printf "Title:    %s\n" "$TITLE"
printf "Branch:   %s → %s\n\n" "$HEAD_REF" "$BASE_REF"

# Fetch the PR head into a local ref, then compute "behind main" count.
git fetch -q origin "pull/${PR}/head:pr-${PR}-overdue-check" 2>/dev/null || {
    echo "Failed to fetch PR head"; exit 1;
}
trap "git branch -D pr-${PR}-overdue-check >/dev/null 2>&1" EXIT

git fetch -q origin "$BASE_REF" 2>/dev/null

BEHIND="$(git rev-list --count "pr-${PR}-overdue-check..origin/${BASE_REF}")"
AHEAD="$(git rev-list --count "origin/${BASE_REF}.pr-${PR}-overdue-check")"

printf "\033[1;33mPR is %s commits ahead, %s commits BEHIND %s\033[0m\n\n" "$AHEAD" "$BEHIND" "$BASE_REF"

# Diff stat — this is the load-bearing signal.
printf "\033[1;36m--- Diff stat (last 25 lines) ---\033[0m\n"
git diff "origin/${BASE_REF}..pr-${PR}-overdue-check" --stat | tail -25

# Threshold heuristics. Tune as needed.
if [ "$BEHIND" -gt 20 ]; then
    printf "\n\033[1;31m⚠  BEHIND MAIN: %s commits behind %s.\033[0m\n" "$BEHIND" "$BASE_REF"
    printf "   This PR will REVERT downstream changes if merged as-is.\n"
    printf "   Inspect the diff line count above — if it dwarfs the PR title's\n"
    printf "   apparent scope, the branch base is too old. Ask author to rebase.\n"
    exit 2
fi

# Check insertion/deletion ratio for "wholesale revert" signal.
TOTAL_LINES="$(git diff "origin/${BASE_REF}..pr-${PR}-overdue-check" --shortstat | grep -oE '[0-9]+ insertions|[0-9]+ deletions' | grep -oE '^[0-9]+' | paste -sd+ | bc 2>/dev/null || echo 0)"
if [ "${TOTAL_LINES:-0}" -gt 2000 ]; then
    printf "\n\033[1;33m⚠  Large diff: %s lines changed total.\033[0m Verify scope matches title.\n" "$TOTAL_LINES"
fi

printf "\n\033[1;32m✓ PR #%s looks reasonable to review (%s behind, %s lines).\033[0m\n" "$PR" "$BEHIND" "${TOTAL_LINES:-?}"
