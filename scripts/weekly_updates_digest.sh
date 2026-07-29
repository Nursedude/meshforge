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
# matters); 2 = digest sent but NO fleet hosts file was found (boxes silently
# dropped — a non-zero exit so cron_verdict does not record a false pass).
# Unreachable boxes are reported inside the digest, not an exit.

set -u

TOPIC="${MESHFORGE_NTFY_TOPIC:-$(cat "$HOME/.config/fleet_push_topic" 2>/dev/null)}"
REPO=/opt/meshforge
SSH_OPTS=(-o ConnectTimeout=6 -o BatchMode=yes)

if [[ -z "$TOPIC" ]]; then
    echo "no ntfy topic (~/.config/fleet_push_topic)" >&2
    exit 1
fi

# ── Fleet hosts (3-tier, matching fleet_sync.sh): env → ~/.config → /etc.
# A MISSING/unreadable hosts file must NOT silently degrade to a one-box digest
# that reads OK (honest_failure_modes point 1: dropped boxes look healthy). The
# wrong-user-$HOME cron case (#78 class) lands here — surface it loudly.
# Host list via THE shared resolver (scripts/lib/fleet_hosts.sh) — this was
# one of ~13 independent copies of the chain (converged 2026-07-29).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/fleet_hosts.sh"
hosts=()
hosts_missing=0
if fleet_hosts_resolve "/opt/meshforge"; then
    while IFS= read -r line; do
        [[ -n "$line" ]] && hosts+=("$line")
    done <<< "$FLEET_HOSTS_LIST"
else
    hosts_missing=1
fi

# ── Repo posture: fetch once, compare every box's HEAD to origin/main. If the
# fetch FAILS, origin/main is not trustworthy this run — mark the baseline
# UNKNOWN rather than computing a false "BEHIND" against a stale ref (a network
# blip must not read as fleet-wide divergence). ──
baseline_ok=1
git -C "$REPO" fetch origin main -q 2>/dev/null || baseline_ok=0
remote_sha=$(git -C "$REPO" rev-parse --short=8 origin/main 2>/dev/null || echo '?')
[[ "$remote_sha" == "?" ]] && baseline_ok=0
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
    local git_state
    if [[ "$baseline_ok" != 1 ]]; then
        git_state="? (origin/main unknown)"
    elif [[ "$sha" == "$remote_sha" ]]; then
        git_state="ok"
    else
        # Differs from origin/main; direction is not proven for a remote box
        # (we don't have its rev-list), so report DRIFT, never a false BEHIND.
        git_state="DRIFT ($sha)"
    fi
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
if [[ "$baseline_ok" == 1 && "$behind" != "0" && "$behind" != "?" ]]; then
    decisions+=("this box is $behind commit(s) behind origin/main")
fi

body="repo origin/main @ $remote_sha
$(printf '%s\n' "${lines[@]}")"
if ((${#decisions[@]})); then
    body+=$'\n'"PENDING: $(printf '%s; ' "${decisions[@]}")"
fi
[[ $issues -gt 0 ]] && body+=$'\n'"($issues box(es) unreachable)"
[[ "$baseline_ok" != 1 ]] && body+=$'\n'"⚠ origin/main baseline unavailable (git fetch failed) — git-drift shown as '?' this run"
if [[ "$hosts_missing" == 1 ]]; then
    body+=$'\n'"⚠ NO fleet hosts file found (checked \$MESHFORGE_FLEET_HOSTS, ~/.config/meshforge/fleet_hosts, /etc/meshforge/fleet_hosts) — digest covers ONLY $(hostname); the rest of the fleet is UNMONITORED this run"
fi

if ! curl -fsS -m 15 -H "Title: Weekly updates posture" -H "Priority: min" \
        -H "Tags: package" -d "$body" "https://ntfy.sh/$TOPIC" >/dev/null; then
    echo "ntfy publish failed" >&2
    exit 1
fi

echo "$body"
# Sent — but a missing hosts file silently dropped the rest of the fleet, so
# exit non-zero: cron_verdict must not read this as a clean pass (#78 pages
# until the config is fixed).
[[ "$hosts_missing" == 1 ]] && exit 2
exit 0
