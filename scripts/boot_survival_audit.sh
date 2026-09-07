#!/bin/bash
# boot_survival_audit.sh — per-box organ: after a boot (and daily), answer ONE
# question from ground truth: did everything that is SUPPOSED to run come back?
#
# WHY THIS EXISTS (2026-08-15, Hurricane Lala post-mortem)
# --------------------------------------------------------
# A power outage reboots every box at once, and what comes back is whatever
# survived its own boot races — silently. The night of 08-14 the fleet's core
# services all returned, but mesh_bot on the trdev edge box started before
# wlan0 was up, aborted CRITICAL, exited 0 (so Restart=on-failure read it as
# success), and sat dead for 12 hours behind an `enabled` status. trdev runs
# no watchdog and no mini — NOTHING watched it. This audit is the portable
# answer: one self-contained script, no repo dependencies, runnable on every
# box including edge boxes that carry nothing else of ours.
#
# WHAT IT JUDGES (systemd ground truth, not wiring)
#   * any unit in `failed` state                          -> CASUALTY
#   * an enabled long-running service that is not active  -> CASUALTY
#     (Type=oneshot units are exempt — enabled+inactive is their healthy
#      resting state after a successful boot run)
#   * an enabled timer that is not active                 -> CASUALTY
#   * user units judged the same way when a user manager exists; a missing
#     user manager with linger=no is reported as a SKIP line, never silently
#     absorbed (absence must be explained, honest_failure_modes #3)
#
# WAIVERS: a small built-in list of known desktop cruft that ships enabled
# but never runs headless (gcr-ssh-agent, fbd-alert-slider), plus optional
# operator lines in ~/.config/meshforge/boot_survival_waivers.txt (one unit
# name substring per line, # comments allowed). Waived units are counted and
# named in the OK line — a waiver hides a verdict, never an observation.
#
# VERDICT: writes one line to ~/cron_verdicts.log via cron_verdict.sh when
# the repo is present, else appends the same `<ISO8601> <name> <STATUS> ...`
# shape itself (documented fallback for edge boxes with no /opt/meshforge —
# same consumer format, single constant below).
#   exit 0 = OK, everything enabled is up
#   exit 1 = CASUALTY list follows (the boot left something dead)
#   exit 2 = cannot observe (no systemctl) — UNKNOWN, never healthy
#
# Install on a box (writes both cron lines, idempotent):
#   ./boot_survival_audit.sh --install-cron
# which wires:
#   @reboot sleep 420; boot_survival_audit.sh   (7 min grace for slow Pis)
#   17 7 * * * boot_survival_audit.sh           (daily re-derivation)

set -uo pipefail

NAME=boot_survival
WAIVER_FILE="$HOME/.config/meshforge/boot_survival_waivers.txt"
BUILTIN_WAIVERS="gcr-ssh-agent fbd-alert-slider wayvnc-control cloud-init"
VERDICT_HELPER=/opt/meshforge/scripts/cron_verdict.sh
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

verdict() {  # STATUS message...
    local st="$1"; shift
    if [ -x "$VERDICT_HELPER" ]; then
        "$VERDICT_HELPER" "$NAME" "$st" "$*"
    else
        # Fallback for edge boxes without the repo — same log shape.
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $NAME $st $*" >> "$HOME/cron_verdicts.log"
    fi
}

if [ "${1:-}" = "--install-cron" ]; then
    tmp=$(mktemp)
    crontab -l 2>/dev/null | grep -v "boot_survival_audit.sh" > "$tmp" || true
    {
        cat "$tmp"
        echo "@reboot sleep 420; $SELF >> \$HOME/.local/state/boot_survival.log 2>&1"
        echo "17 7 * * * $SELF >> \$HOME/.local/state/boot_survival.log 2>&1"
    } | crontab -
    rm -f "$tmp"
    mkdir -p "$HOME/.local/state"
    echo "installed: @reboot(+420s) and daily 07:17 cron lines"
    exit 0
fi

command -v systemctl >/dev/null 2>&1 || { verdict FAIL "no systemctl — cannot observe (UNKNOWN, not healthy)"; exit 2; }

waivers="$BUILTIN_WAIVERS"
if [ -f "$WAIVER_FILE" ]; then
    while IFS= read -r line; do
        line="${line%%#*}"; line="$(echo "$line" | tr -d '[:space:]')"
        [ -n "$line" ] && waivers="$waivers $line"
    done < "$WAIVER_FILE"
fi
is_waived() { local u="$1" w; for w in $waivers; do case "$u" in *"$w"*) return 0;; esac; done; return 1; }

casualties=() waived_hits=() ok_count=0 skips=""

scan() {  # $1 = "" for system, "--user" for user manager
    local scope="$1" tag u state act typ
    # failed units are casualties regardless of enablement or type
    while read -r u; do
        [ -z "$u" ] && continue
        if is_waived "$u"; then waived_hits+=("${scope:+usr:}$u(failed)"); else casualties+=("${scope:+usr:}$u FAILED"); fi
    done < <(systemctl $scope list-units --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}' | grep -v '^●')

    while read -r u state _; do
        [ "$state" = "enabled" ] || continue
        case "$u" in
        *@.service|*@.timer) continue;;   # templates have no instance state
        *.service)
            typ=$(systemctl $scope show "$u" -p Type --value 2>/dev/null)
            # oneshot: enabled+inactive is the healthy resting state after a
            # successful run. dbus: activation-on-demand, idle is healthy.
            case "$typ" in oneshot|dbus|idle) continue;; esac
            # A unit whose start was skipped by its own Condition* is not a
            # casualty — the box told it not to run here.
            [ "$(systemctl $scope show "$u" -p ConditionResult --value 2>/dev/null)" = "no" ] && continue
            act=$(systemctl $scope is-active "$u" 2>/dev/null)
            if [ "$act" = "active" ] || [ "$act" = "activating" ]; then
                ok_count=$((ok_count+1))
            elif is_waived "$u"; then
                waived_hits+=("${scope:+usr:}$u($act)")
            else
                casualties+=("${scope:+usr:}$u enabled-but-$act")
            fi;;
        *.timer)
            # Timers carry Condition* too (smartmon without smartctl, snapd
            # repair without snapd...) — a condition-refused timer is not dead.
            [ "$(systemctl $scope show "$u" -p ConditionResult --value 2>/dev/null)" = "no" ] && continue
            act=$(systemctl $scope is-active "$u" 2>/dev/null)
            if [ "$act" = "active" ]; then
                ok_count=$((ok_count+1))
            elif is_waived "$u"; then
                waived_hits+=("${scope:+usr:}$u($act)")
            else
                casualties+=("${scope:+usr:}$u enabled-but-$act")
            fi;;
        esac
    done < <(systemctl $scope list-unit-files --type=service,timer --no-legend --plain 2>/dev/null)
}

scan ""
# `systemctl --user` needs a bus ADDRESS, not merely a running manager. Under
# cron there is no session, so XDG_RUNTIME_DIR is unset and the probe below
# fails ("$DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined") even
# while the user manager is active under linger=yes. The whole USER scope
# then went unjudged on every scheduled run, and the old skip line said
# `user-manager-absent(linger=yes)` — an explanation its own parenthetical
# contradicts: the manager was present, the address was not. Measured cost
# (2026-09-06): a failed user unit sat unreported for 2 days and surfaced
# only because the audit was run by hand from a login session.
# Supply the address when the runtime dir exists; genuine absence stays a SKIP.
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if systemctl --user show-environment >/dev/null 2>&1; then
    scan "--user"
else
    # Report what was OBSERVED, not a cause we did not establish: linger says
    # whether a manager should persist, runtime_dir whether we could address
    # it. Both go in the line so the next reader is not guessing.
    linger=$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo "?")
    skips=" user-scope-unobservable(linger=$linger,runtime_dir=${XDG_RUNTIME_DIR:-unset})"
fi

waived_note=""
[ ${#waived_hits[@]} -gt 0 ] && waived_note=" waived:$(IFS=,; echo "${waived_hits[*]}")"

if [ ${#casualties[@]} -gt 0 ]; then
    msg="$(IFS='; '; echo "${casualties[*]}")"
    echo "CASUALTY(${#casualties[@]}): $msg"
    verdict FAIL "boot left ${#casualties[@]} unit(s) down: $msg$waived_note$skips"
    exit 1
fi
echo "OK: $ok_count enabled unit(s) active$waived_note$skips"
verdict OK "$ok_count enabled unit(s) active$waived_note$skips"
exit 0
