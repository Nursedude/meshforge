#!/usr/bin/env bash
# fleet_pull.sh — fast-forward every fleet box to the current HEAD, NO restarts.
#
# WHY THIS EXISTS (2026-07-15): the "pull the fleet" operation is a repeated
# footgun when hand-typed as an ssh loop — the remote `git pull` must run inside
# the repo dir, but it is easy to bind `cd` to the LOCAL shell and leave the
# remote command running in the home dir (not a repo → every box mislabeled
# "unreachable"). That happened 4× in one session. The lesson (cross-model
# agentic health / calibrated_claims): compile the reliability OUT of model
# memory and INTO the harness. This script bakes the remote `cd` + repo dir in
# once, so no operator — human or model, this model or the next — has to
# remember it. It is the low-risk, restart-free counterpart to fleet_sync.sh:
# use THIS to deploy a src/doc change the fleet should pull without bouncing any
# service; use fleet_sync.sh only when services must restart.
#
# Usage:
#   scripts/fleet_pull.sh                 # ff-only pull every box to /opt/meshforge HEAD
#   scripts/fleet_pull.sh /opt/meshforge-maps   # …of the sister repo instead
#   REPO_DIR=/path scripts/fleet_pull.sh  # same, via env
#
# Host list source (first found). A PER-REPO list wins over the generic one
# (added 2026-07-16: the sister repo lives on 2 boxes, so the shared list made
# every MA deploy "6 NOT converged" — an exit code that cried wolf):
#   $MESHFORGE_FLEET_HOSTS                                (explicit override)
#   ~/.config/meshforge/fleet_hosts.<repo-basename>       (e.g. fleet_hosts.meshanchor)
#   /etc/meshforge/fleet_hosts.<repo-basename>
#   ~/.config/meshforge/fleet_hosts                       (generic fallback)
#   /etc/meshforge/fleet_hosts
# Comments (#) and blank lines are ignored. Hosts resolve via ~/.ssh/config
# (aliases + ProxyCommand/jump-hosts supported). A selected list that yields
# ZERO hosts is an error, never a silent all-converged no-op.
#
# Behaviour:
#   - Reads the TARGET sha from the LOCAL repo (this box), then ff-only pulls
#     each remote to origin/main and reports its post-pull sha vs the target.
#   - A host failing (unreachable, no repo, diverged) does NOT abort the rest.
#   - NEVER restarts a service (that is fleet_sync.sh's job).
#   - Exit code = number of boxes NOT converged on the target (0 = all match).

set -uo pipefail

REPO_DIR="${1:-${REPO_DIR:-/opt/meshforge}}"

# --- resolve the target sha from the local repo -------------------------------
if ! TARGET_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null); then
    echo "fleet_pull: '$REPO_DIR' is not a git repo on this box — nothing to target." >&2
    exit 2
fi
BRANCH=$(git -C "$REPO_DIR" branch --show-current 2>/dev/null || echo "?")

# --- resolve the host list (per-repo list wins over the generic one) ----------
# The resolution chain lives in ONE sourceable lib shared with honest_status.sh
# — the deployer and the gate that verifies its deploys must read the SAME
# list, and hand-copies of this chain had already diverged (2026-07-28 review).
REPO_BASE="$(basename "$REPO_DIR")"
FP_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/fleet_hosts.sh"
if [ ! -f "$FP_LIB" ]; then
    echo "fleet_pull: missing $FP_LIB — cannot resolve the host list." >&2
    exit 2
fi
. "$FP_LIB"

# The .git ownership heal travels WITH the remote command (its text is
# embedded below), not as a file on the target — a box whose .git is poisoned
# is exactly a box that cannot pull the heal script itself (2026-08-02 moc3).
GIT_HEAL_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/git_heal.sh"
if ! GIT_HEAL_SRC="$(cat "$GIT_HEAL_LIB" 2>/dev/null)" || [ -z "$GIT_HEAL_SRC" ]; then
    # Fail together, never half-wired (honest_failure_modes #4): deploying
    # without the heal silently re-opens the class on any poisoned box.
    echo "fleet_pull: missing or empty $GIT_HEAL_LIB — refusing to deploy without the .git ownership heal." >&2
    exit 2
fi

if ! fleet_hosts_resolve "$REPO_DIR"; then
    echo "fleet_pull: no fleet_hosts list found (set \$MESHFORGE_FLEET_HOSTS or create ~/.config/meshforge/fleet_hosts[.$REPO_BASE])." >&2
    exit 2
fi
HOSTS_FILE="$FLEET_HOSTS_FILE"

mapfile -t HOSTS < <(printf '%s\n' "$FLEET_HOSTS_LIST" | grep -v '^$')
if [ "${#HOSTS[@]}" -eq 0 ]; then
    # An empty list must fail LOUD — falling through to "all 0 host(s)
    # converged" (exit 0) would read as a successful deploy that pulled nobody.
    echo "fleet_pull: host list $HOSTS_FILE contains no hosts — refusing the silent no-op." >&2
    exit 2
fi
echo "fleet_pull: $REPO_DIR @ ${BRANCH}=${TARGET_SHORT} → ${#HOSTS[@]} host(s) from $(basename "$HOSTS_FILE") (no restarts)"

# The remote command: cd into the repo (bake it in — the whole point), ff-only
# pull, then print a status token + the resulting short sha. Always prints a
# sha even on pull failure so a divergence is distinguishable from unreachable.
remote_cmd=$(cat <<REMOTE
$GIT_HEAL_SRC
cd "$REPO_DIR" 2>/dev/null || { echo "NOREPO"; exit 0; }
# Repair a .git poisoned by a prior \`sudo git\` BEFORE pulling, or the pull
# dies with "insufficient permission for adding an object". A repair is
# REPORTED (HEALED), never swallowed — a box being re-poisoned every deploy
# must not look identical to a clean one (fleet_hosts_selfheal convention).
heal_out="\$(git_heal_ownership "$REPO_DIR")"
case "\${heal_out%% *}" in
    HEAL_NONE) heal_note="" ;;
    HEAL_OK)   heal_note=" HEALED(\${heal_out#* })" ;;
    *)         echo "HEALFAIL \$heal_out"; exit 0 ;;
esac
if git pull --ff-only origin main >/dev/null 2>&1; then
    echo "PULLED \$(git rev-parse --short HEAD)\$heal_note"
else
    echo "PULLFAIL \$(git rev-parse --short HEAD 2>/dev/null || echo '?')\$heal_note"
fi
REMOTE
)

failures=0
for h in "${HOSTS[@]}"; do
    out=$(ssh -o ConnectTimeout=15 -o BatchMode=yes "$h" "$remote_cmd" 2>/dev/null)
    # Fields, not suffix-strip: the sha may now carry a trailing HEALED(n)
    # note, and `${out#* }` would fold it into the sha and fail every
    # comparison against $TARGET_SHORT.
    read -r status sha heal_note <<<"$out"
    heal_note=${heal_note:+ $heal_note}
    case "$status" in
        PULLED)
            if [ "$sha" = "$TARGET_SHORT" ]; then
                printf '  %-20s OK        %s%s\n' "$h" "$sha" "$heal_note"
            else
                printf '  %-20s MISMATCH  %s (target %s)%s\n' "$h" "$sha" "$TARGET_SHORT" "$heal_note"
                failures=$((failures + 1))
            fi
            ;;
        PULLFAIL)
            printf '  %-20s PULL_FAIL %s (diverged / not ff-only — inspect)%s\n' "$h" "$sha" "$heal_note"
            failures=$((failures + 1))
            ;;
        HEALFAIL)
            # Found foreign-owned .git artifacts and could NOT repair them —
            # this box cannot receive code until a human intervenes. Never
            # silently degraded to "unreachable" (honest_failure_modes #9).
            printf '  %-20s HEAL_FAIL %s %s (cannot receive deploys — chown %s/.git)\n' \
                "$h" "$sha" "$heal_note" "$REPO_DIR"
            failures=$((failures + 1))
            ;;
        NOREPO)
            printf '  %-20s NO_REPO   (%s absent on this host)\n' "$h" "$REPO_DIR"
            failures=$((failures + 1))
            ;;
        *)
            printf '  %-20s UNREACHABLE\n' "$h"
            failures=$((failures + 1))
            ;;
    esac
done

if [ "$failures" -eq 0 ]; then
    echo "fleet_pull: all ${#HOSTS[@]} host(s) converged on ${TARGET_SHORT}."
else
    echo "fleet_pull: ${failures} host(s) NOT converged — see above."
fi

# --- long-running consumers keep the OLD code in memory ----------------------
# 2026-07-20: converging the WORKING TREE is not the same as converging what is
# RUNNING. meshforge-map imports SIGNAL_CLASSES once at process start, so after
# a pull that adds a signal class it keeps publishing the old, short class list
# on /api/fleet/truth — for a full day, in the incident that prompted this,
# while 49-of-52 looked complete. That is the Issue #79 deploy-restart gap in
# its truth-API skin.
#
# This stays an ADVISORY, never an automatic restart: fleet_pull's whole value
# is that it is the restart-free path (safe mid-soak), and quietly bouncing a
# service here would destroy the one property that makes it usable. The
# authoritative detector is server_class_skew in /api/fleet/truth, which now
# renders a banner and forces the verdict DARK rather than under-reporting in
# silence. This line just puts it in front of whoever ran the deploy.
#
# Scoped to the MeshForge repo ONLY: meshforge-map serves THIS repo's code, so
# naming it after a `fleet_pull.sh /opt/meshanchor` run is a confidently wrong
# line (caught 2026-07-20, minutes after the advisory shipped — the sister repo
# has its own NOC service and its own restart story).
if [ "$failures" -eq 0 ] && [ "$(basename "$REPO_DIR")" = "meshforge" ] \
   && [ "$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null)" = "$TARGET_SHORT" ]; then
    if systemctl is-active --quiet meshforge-map 2>/dev/null; then
        # Both sides in WALL-CLOCK epoch seconds. (First cut used
        # ActiveEnterTimestampMonotonic, which is time-since-BOOT, not service
        # uptime — comparing it against a commit age is incommensurate and made
        # the note fire unconditionally. Caught the same evening; a nag that is
        # not actually derived from anything is worse than no nag.)
        map_started_raw=$(systemctl show meshforge-map -p ActiveEnterTimestamp --value 2>/dev/null)
        map_started=$(date -d "$map_started_raw" +%s 2>/dev/null || echo 0)
        head_ct=$(git -C "$REPO_DIR" log -1 --format=%ct 2>/dev/null || echo 0)
        # Only nag when HEAD is NEWER than the running process (the risky case).
        if [ "${map_started:-0}" -gt 0 ] && [ "${head_ct:-0}" -gt 0 ] \
           && [ "$map_started" -lt "$head_ct" ]; then
            echo "fleet_pull: NOTE — meshforge-map on this box started before ${TARGET_SHORT};"
            echo "            it serves /api/fleet/truth from the code it booted with."
            echo "            Check 'server_class_skew' there (or the /fleet banner); if it is"
            echo "            non-empty, restart meshforge-map. No restart is done for you."
        fi
    fi
fi
exit "$failures"
