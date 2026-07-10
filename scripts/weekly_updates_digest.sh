#!/bin/bash
# weekly_updates_digest.sh — Monday updates-posture digest for the fleet.
#
# Born 2026-07-10 (updates-design arc): the operator replaced a daily
# reminder routine with this weekly digest. One min-priority ntfy message
# summarizing, per box: MeshForge git posture (SHA vs origin/main),
# meshtasticd installed/candidate/hold, CLI shim ownership, and the
# meshtasticd VSZ level (upstream firmware#10468 — the leak the weekly
# restart timer manages). Standing decisions (a candidate waiting behind
# the fleet hold) are surfaced explicitly so they can't be forgotten.
#
# Crontab idiom (verdict-wired, silence-proof via probe #78):
#   0 8 * * 1 /opt/meshforge/scripts/weekly_updates_digest.sh >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh weekly_updates_digest $?
#
# Exit: 0 = digest sent; 1 = digest could not be SENT (the failure that
# matters); unreachable boxes are reported inside the digest, not an exit.

set -u

HOSTS_FILE="${MESHFORGE_FLEET_HOSTS:-$HOME/.config/meshforge/fleet_hosts}"
TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
REPO=/opt/meshforge
SSH_OPTS=(-o ConnectTimeout=6 -o BatchMode=yes)

if [[ -z "$TOPIC" ]]; then
    echo "no ntfy topic (~/.config/fleet_push_topic)" >&2
    exit 1
fi

hosts=()
if [[ -r "$HOSTS_FILE" ]]; then
    while IFS= read -r line; do
        line="${line%%#*}"; line="$(echo "$line" | tr -d '[:space:]')"
        [[ -n "$line" ]] && hosts+=("$line")
    done < "$HOSTS_FILE"
fi

# ── Repo posture: fetch once, compare every box's HEAD to origin/main ──
git -C "$REPO" fetch origin main -q 2>/dev/null
remote_sha=$(git -C "$REPO" rev-parse --short=8 origin/main 2>/dev/null || echo '?')
local_sha=$(git -C "$REPO" rev-parse --short=8 HEAD 2>/dev/null || echo '?')
behind=$(git -C "$REPO" rev-list --count "HEAD..origin/main" 2>/dev/null || echo '?')

# ── meshtasticd candidate (local apt view; the repos are fleet-uniform) ──
candidate=$(apt-cache policy meshtasticd 2>/dev/null | awk '/Candidate:/{print $2}')

box_line() {  # $1 = host ('' = local); $2 = label
    local h="$1" label="$2" run sha ver hold shim vsz
    if [[ -z "$h" ]]; then run() { bash -c "$1" 2>/dev/null; }
    else run() { timeout 15 ssh "${SSH_OPTS[@]}" "$h" "$1" 2>/dev/null; }
    fi
    sha=$(run "git -C /opt/meshforge rev-parse --short=8 HEAD") || true
    if [[ -z "${sha:-}" ]]; then echo "$label: UNREACHABLE"; return 1; fi
    # dpkg -l parse, not a -f format string: a format survives local bash -c
    # but the remote shell expands ${Version} to empty over ssh (live-caught
    # on first run — every box read "absent").
    ver=$(run "dpkg -l meshtasticd 2>/dev/null | awk '/^.i/{print \$3}'")
    hold=$(run "apt-mark showhold meshtasticd 2>/dev/null")
    shim=$(run "head -c 80 ~/.local/bin/meshtastic 2>/dev/null | head -1")
    vsz=$(run 'pid=$(pgrep -x meshtasticd | head -1); [ -n "$pid" ] && awk "/^VmSize/{printf \"%.1fGB\", \$2/1048576}" /proc/$pid/status')
    local git_state="ok"
    [[ "$sha" != "$remote_sha" ]] && git_state="BEHIND ($sha)"
    local shim_state="pipx"
    case "$shim" in
        *"/pipx/venvs/"*) shim_state="pipx" ;;
        "") shim_state="no-cli" ;;
        *) shim_state="SHADOWED-SHIM" ;;
    esac
    echo "$label: git $git_state | meshtasticd ${ver:-absent}${hold:+ [HELD]} | cli $shim_state | vsz ${vsz:-n/a}"
    return 0
}

lines=()
issues=0
l=$(box_line "" "$(hostname)") || issues=$((issues+1)); lines+=("$l")
for h in "${hosts[@]}"; do
    l=$(box_line "$h" "$h") || issues=$((issues+1))
    lines+=("$l")
done

# ── Standing decisions ──
decisions=()
installed_local=$(dpkg-query -W -f='${Version}' meshtasticd 2>/dev/null)
if [[ -n "$candidate" && -n "$installed_local" && "$candidate" != "$installed_local" ]]; then
    decisions+=("meshtasticd $candidate waits behind the fleet hold (installed $installed_local) — roll deliberately via TUI Update meshtasticd, canary-first")
fi
if [[ "$behind" != "0" && "$behind" != "?" ]]; then
    decisions+=("this box is $behind commit(s) behind origin/main")
fi

body="repo origin/main @ $remote_sha
$(printf '%s\n' "${lines[@]}")"
if ((${#decisions[@]})); then
    body+=$'\n'"PENDING: $(printf '%s; ' "${decisions[@]}")"
fi
[[ $issues -gt 0 ]] && body+=$'\n'"($issues box(es) unreachable)"

if ! curl -fsS -m 15 -H "Title: Weekly updates posture" -H "Priority: min" \
        -H "Tags: package" -d "$body" "https://ntfy.sh/$TOPIC" >/dev/null; then
    echo "ntfy publish failed" >&2
    exit 1
fi

echo "$body"
exit 0
