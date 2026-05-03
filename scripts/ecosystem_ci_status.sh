#!/bin/bash
# scripts/ecosystem_ci_status.sh — daily check across all 6 first-party repos.
#
# Born from the 2026-05-03 audit
# (project_ecosystem_ci_audit_2026_05_03.md): meshforge-maps was red 3
# days, meshing_around_meshforge was red 4 days, and nobody noticed
# because the production deployments kept running. Pattern: prod runs
# fine, repo CI rots invisibly. This script closes that visibility gap.
#
# Outputs:
#   - Always: $HOME/.meshforge-ci-status — one line per repo (latest CI
#     conclusion on main). Plain ASCII, easy to cat or shell-prompt.
#   - On red: $HOME/.meshforge-ci-RED — details about which repo(s)
#     are red, since when, and the failing job. File present = unclear,
#     file absent = all green. Easy to spot via `ls ~`.
#   - Stdout: same content as ~/.meshforge-ci-status (cron picks this up
#     and emails it if MAILTO is set in crontab).
#
# Exit codes:
#   0 — all repos green
#   1 — one or more red (cron's MAILTO will deliver stdout if set)
#   2 — gh CLI not available / auth failure
#
# Install (cron, runs daily at 08:00):
#   (crontab -l 2>/dev/null; echo '0 8 * * * /opt/meshforge/scripts/ecosystem_ci_status.sh') | crontab -
#
# Install (systemd user timer, runs every 6 hours):
#   See templates/systemd/meshforge-ci-status.{service,timer} (sibling files).

set -u

REPOS=(
    "Nursedude/meshforge"
    "Nursedude/meshanchor"
    "Nursedude/meshforge-maps"
    "Nursedude/meshing_around_meshforge"
    "Nursedude/RNS-Management-Tool"
    "Nursedude/RNS-Meshtastic-Gateway-Tool"
)

STATUS_FILE="$HOME/.meshforge-ci-status"
RED_FILE="$HOME/.meshforge-ci-RED"

if ! command -v gh >/dev/null 2>&1; then
    echo "$(date -Iseconds) gh CLI not installed" > "$STATUS_FILE"
    exit 2
fi

# Quick auth sanity. If gh isn't authenticated, we'd rather know now
# than emit a confusing "all repos missing" report.
if ! gh auth status >/dev/null 2>&1; then
    echo "$(date -Iseconds) gh CLI not authenticated (run: gh auth login)" > "$STATUS_FILE"
    exit 2
fi

NOW="$(date -Iseconds)"
red_lines=()
status_lines=()
status_lines+=("# MeshForge ecosystem CI status — generated $NOW")

for repo in "${REPOS[@]}"; do
    short="${repo#Nursedude/}"
    # Latest run on main. Some repos use 'main', not 'master' — all six
    # of the first-party repos are on 'main' as of 2026-05-03.
    raw="$(gh run list --repo "$repo" --branch main --limit 1 \
        --json conclusion,createdAt,displayTitle,headSha 2>/dev/null)"
    if [ -z "$raw" ] || [ "$raw" = "[]" ]; then
        status_lines+=("$(printf '  %-35s  no-runs' "$short")")
        continue
    fi
    conclusion="$(echo "$raw" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0].get("conclusion") or "in_progress")')"
    title="$(echo "$raw" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0].get("displayTitle","")[:50])')"
    when="$(echo "$raw" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0].get("createdAt",""))')"
    sha="$(echo "$raw" | python3 -c 'import json,sys; r=json.load(sys.stdin); print((r[0].get("headSha") or "")[:7])')"
    status_lines+=("$(printf '  %-35s  %-10s  %s  %s' "$short" "$conclusion" "$sha" "$title")")
    if [ "$conclusion" = "failure" ]; then
        red_lines+=("$(printf '%s  %s  %s  %s\n    %s' "$short" "$conclusion" "$sha" "$when" "$title")")
    fi
done

# Always rewrite the status file, atomic.
tmp="$(mktemp)"
printf '%s\n' "${status_lines[@]}" > "$tmp"
mv "$tmp" "$STATUS_FILE"

# Overdue open-PR sweep — a PR sitting open >14 days is rot too,
# even if not red. Dependabot/web-Claude PRs accumulate silently.
# Doesn't fail the script (exit code stays driven by CI red), but
# appends a "overdue PRs" section to the status file when found.
overdue_pr_lines=()
OVERDUE_DAYS=14
for repo in "${REPOS[@]}"; do
    short="${repo#Nursedude/}"
    prs_json="$(gh pr list --repo "$repo" --state open --limit 20 \
        --json number,title,updatedAt,author 2>/dev/null)"
    [ -z "$prs_json" ] || [ "$prs_json" = "[]" ] && continue
    while IFS=$'\t' read -r pr_num pr_age pr_title pr_author; do
        [ -z "$pr_num" ] && continue
        if [ "$pr_age" -gt "$OVERDUE_DAYS" ]; then
            overdue_pr_lines+=("$(printf '  %s#%s  %dd  %s — %s' "$short" "$pr_num" "$pr_age" "$pr_author" "${pr_title:0:60}")")
        fi
    done < <(echo "$prs_json" | python3 -c '
import json, sys, datetime
now = datetime.datetime.now(datetime.timezone.utc)
for pr in json.load(sys.stdin):
    age = (now - datetime.datetime.fromisoformat(pr["updatedAt"].replace("Z","+00:00"))).days
    print(pr["number"], age, pr["title"], pr["author"]["login"], sep="\t")
')
done

if [ "${#overdue_pr_lines[@]}" -gt 0 ]; then
    {
        printf '\n# Overdue open PRs (>%d days)\n' "$OVERDUE_DAYS"
        printf '%s\n' "${overdue_pr_lines[@]}"
    } >> "$STATUS_FILE"
fi

# Stdout mirrors status file (cron MAILTO picks this up).
cat "$STATUS_FILE"

# Red marker — present iff any repo is red. Operators notice it on
# `ls ~` or via shell prompt integrations.
if [ "${#red_lines[@]}" -gt 0 ]; then
    {
        echo "# MeshForge ecosystem CI RED — generated $NOW"
        echo "# Delete this file or fix the offending repo to clear."
        echo ""
        for line in "${red_lines[@]}"; do
            printf -- '%s\n\n' "$line"
        done
        echo "# Triage: bash scripts/ecosystem_ci_status.sh"
        echo "# Per-repo logs:"
        for repo in "${REPOS[@]}"; do
            echo "#   gh run view --repo $repo \$(gh run list --repo $repo --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
        done
    } > "$RED_FILE"
    exit 1
else
    rm -f "$RED_FILE"
    exit 0
fi
