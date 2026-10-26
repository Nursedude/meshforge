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
#   exit 1 = CASUALTY list follows (something is down; each says WHEN)
#   exit 2 = cannot observe (no systemctl) — UNKNOWN, never healthy
#
# Install on a box (writes both cron lines, idempotent):
#   ./boot_survival_audit.sh --install-cron
# which wires:
#   @reboot sleep 420; boot_survival_audit.sh   (7 min grace for slow Pis)
#   17 7 * * * boot_survival_audit.sh  # cron_verdict:boot_survival
# The marker on the daily line is what lets probe_cron_verdict_stale judge this
# audit's SILENCE (not just its failures) — see the note at the install block.
# Re-running --install-cron is idempotent and is the migration path for a box
# whose crontab predates the marker.

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
        # The DAILY line carries the self-verdict declaration; the @reboot twin
        # deliberately does NOT. This script emits its verdict from inside its
        # own body, so no name reaches the crontab line and probe_cron_verdict_
        # stale could hear its FAILures but never its SILENCE (2026-09-06). The
        # marker supplies the name beside a schedule the cadence can be read
        # from — and @reboot has no cadence, so declaring both would let dict
        # ordering decide which schedule judges the silence, with @reboot
        # making it unjudgeable. One declaration, on the line that has a period.
        echo "17 7 * * * $SELF >> \$HOME/.local/state/boot_survival.log 2>&1  # cron_verdict:$NAME"
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

# WHEN a unit went down, on the MONOTONIC clock (us since boot — an RTC-less
# Pi steps its wall clock, hfm #6): the unit's InactiveEnterTimestampMonotonic
# against its manager's FinishTimestampMonotonic. 2026-10-26: 8 boxes paged
# "boot left 1 unit(s) down" for a desktop portal that died 17 h AFTER boot,
# when the headless switch took its display away — right FAIL, wrong cause.
# 0 = never went inactive = it never STARTED (nothing died). A user manager
# that (re)started after boot (no linger + a late login, a restart of
# user@UID) has every unit inside ITS start window — never call that the boot
# (Fable 3rd read, 2026-10-26). No answer is UNKNOWN, never "during boot"
# (hfm #2). Grace >= the @reboot run's sleep 420 so the run that judges the
# boot does not call a boot-time death "after boot".
BOOT_GRACE_US=600000000
_num() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
when_down() {  # $1 scope, $2 unit → " during boot" / " after boot (<ts>)" / ...
    local scope="$1" u="$2" fin t sysfin start late="" ts nr
    fin=$(systemctl $scope show -p FinishTimestampMonotonic --value 2>/dev/null)
    t=$(systemctl $scope show "$u" -p InactiveEnterTimestampMonotonic --value 2>/dev/null)
    if ! _num "$fin" || ! _num "$t" || [ "$fin" -eq 0 ]; then
        echo " (when unobserved)"; return
    fi
    if [ -n "$scope" ]; then
        sysfin=$(systemctl show -p FinishTimestampMonotonic --value 2>/dev/null)
        start=$(systemctl $scope show -p UserspaceTimestampMonotonic --value 2>/dev/null)
        # system boot unfinished (Finish=0) or unanswered → "late" cannot be
        # judged; say so rather than assert a cause (Fable delta read A-1)
        if ! _num "$sysfin" || [ "$sysfin" -eq 0 ]; then
            echo " (when unobserved)"; return
        fi
        _num "$start" && [ "$start" -gt $((sysfin + BOOT_GRACE_US)) ] && late=1
    fi
    if [ "$t" -eq 0 ]; then
        if [ -n "$late" ]; then echo " never started since the user manager (re)started"
        else echo " never started this boot"; fi
        return
    fi
    if [ -z "$late" ] && [ "$t" -le $((fin + BOOT_GRACE_US)) ]; then
        echo " during boot"; return
    fi
    ts=$(systemctl $scope show "$u" -p InactiveEnterTimestamp --value 2>/dev/null)
    nr=$(systemctl $scope show "$u" -p NRestarts --value 2>/dev/null)
    _num "$nr" && [ "$nr" -gt 0 ] && ts="$ts, NRestarts=$nr"
    [ -n "$late" ] && ts="$ts; user manager (re)started after boot"
    echo " after boot ($ts)"
}

# ConditionResult=no is ALSO systemd's default before any condition is
# evaluated (gcr-ssh-agent reads `no` with zero Condition lines — Fable read,
# 2026-10-26), so `no` alone used to skip every enabled unit nothing started.
# Echoes: refused   — a condition was EVALUATED and said no: skip, as before
#         notpulled:<entry> — never evaluated and nothing that wants it is
#                     active (a graphical unit on a headless box): not a
#                     casualty; the CALLER records <entry> for the OK line
#                     ($(...) is a subshell — an append in here is lost)
#         judge[ (wanted by X, active)] — judge it; the note rides a casualty
# An unanswered timestamp is not "evaluated" (hfm #2): it is judged.
not_pulled=()
_wanters() { echo "$(systemctl $1 show "$2" -p WantedBy --value 2>/dev/null) $(systemctl $1 show "$2" -p RequiredBy --value 2>/dev/null)"; }
# Who should have started $2? An ACTIVE wanter, or a down wanter that is itself
# wanted by something active — a target enabled into multi-user.target that
# never came up leaves its units unstarted too (Fable C-1: one level missed
# it). Bounded at $3 levels; echoes the chain, empty when nothing is up.
_active_wanter() {  # $1 scope, $2 unit, $3 depth
    local w sub
    [ "$3" -gt 0 ] || return 0
    for w in $(_wanters "$1" "$2"); do
        if [ "$(systemctl $1 is-active "$w" 2>/dev/null)" = "active" ]; then
            echo "$w, active"; return
        fi
    done
    for w in $(_wanters "$1" "$2"); do
        sub=$(_active_wanter "$1" "$w" $(($3 - 1)))
        [ -n "$sub" ] && { echo "$w, down; it is wanted by $sub"; return; }
    done
}
cond_state() {  # $1 scope, $2 unit
    local scope="$1" u="$2" ct wb up
    [ "$(systemctl $scope show "$u" -p ConditionResult --value 2>/dev/null)" = "no" ] \
        || { echo judge; return; }
    ct=$(systemctl $scope show "$u" -p ConditionTimestampMonotonic --value 2>/dev/null)
    if _num "$ct" && [ "$ct" -gt 0 ]; then echo refused; return; fi
    wb=$(_wanters "$scope" "$u")
    up=$(_active_wanter "$scope" "$u" 3)
    if [ -n "$up" ]; then echo "judge (wanted by $up)"; return; fi
    wb=$(echo $wb | tr ' ' ',')
    echo "notpulled:${scope:+usr:}$u(wanted by ${wb:-nothing})"
}

scan() {  # $1 = "" for system, "--user" for user manager
    local scope="$1" tag u state act typ
    # failed units are casualties regardless of enablement or type
    while read -r u; do
        [ -z "$u" ] && continue
        # A TRANSIENT unit (`systemd-run`, Transient=yes) was created AFTER
        # boot by whoever ran it, so its failure cannot be a boot casualty and
        # nothing was "supposed" to bring it back. Measured 2026-09-14 and
        # 2026-09-16 on the manager: a Claude session's pytest / honest_status
        # runner (`mf-honest-*`, `mf-hs-*`, `hsfull-*`) exits non-zero, stays
        # `failed` by design (its exit code must be READABLE, so it is never
        # --collect'ed), and the 07:17 audit paged FAIL on it — 3 of this
        # box's last 4 FAILs. Named in the OK line, never dropped: a waiver
        # hides a verdict, not an observation.
        if [ "$(systemctl $scope show "$u" -p Transient --value 2>/dev/null)" = "yes" ]; then
            waived_hits+=("${scope:+usr:}$u(transient-failed)"); continue
        fi
        if is_waived "$u"; then waived_hits+=("${scope:+usr:}$u(failed)"); else casualties+=("${scope:+usr:}$u FAILED$(when_down "$scope" "$u")"); fi
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
            # ACTIVATION-ON-DEMAND, the other mechanism (2026-09-14). Same
            # reasoning as `dbus` above: a unit with a non-empty TriggeredBy is
            # started BY its trigger (socket/path/timer), so enabled+inactive is
            # its resting state, not a boot casualty. cups.service is the live
            # case — Type=notify (so the Type test misses it), TriggeredBy
            # cups.socket+cups.path, and it idle-exits ~60s after each use:
            #     00:01:15 Started cups.service
            #     00:02:16 cups.service: Deactivated successfully
            # Whether the 07:17 audit caught it awake was a COIN FLIP, so this
            # fired on moc5 and not on kiai with byte-identical unit state, and
            # since cron_verdict_stale began paging (2026-09-14) that race was
            # an intermittent page nobody could act on.
            # ⚠️ This hides nothing that matters: a triggered unit that FAILS to
            # start lands in `failed`, and the failed-unit loop above catches it
            # regardless of enablement or type. Exempting it here drops only the
            # "idle is death" misreading.
            [ -n "$(systemctl $scope show "$u" -p TriggeredBy --value 2>/dev/null)" ] && continue
            cond=$(cond_state "$scope" "$u")
            case "$cond" in
                refused) continue;;
                notpulled:*) not_pulled+=("${cond#notpulled:}"); continue;;
            esac
            act=$(systemctl $scope is-active "$u" 2>/dev/null)
            if [ "$act" = "active" ] || [ "$act" = "activating" ]; then
                ok_count=$((ok_count+1))
            elif is_waived "$u"; then
                waived_hits+=("${scope:+usr:}$u($act)")
            else
                casualties+=("${scope:+usr:}$u enabled-but-$act$(when_down "$scope" "$u")${cond#judge}")
            fi;;
        *.timer)
            # Timers carry Condition* too (smartmon without smartctl, snapd
            # repair without snapd...) — same rule as services (cond_state).
            cond=$(cond_state "$scope" "$u")
            case "$cond" in
                refused) continue;;
                notpulled:*) not_pulled+=("${cond#notpulled:}"); continue;;
            esac
            act=$(systemctl $scope is-active "$u" 2>/dev/null)
            if [ "$act" = "active" ]; then
                ok_count=$((ok_count+1))
            elif is_waived "$u"; then
                waived_hits+=("${scope:+usr:}$u($act)")
            else
                casualties+=("${scope:+usr:}$u enabled-but-$act$(when_down "$scope" "$u")${cond#judge}")
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
[ ${#not_pulled[@]} -gt 0 ] && waived_note="$waived_note not-pulled-in:$(IFS=,; echo "${not_pulled[*]}")"

if [ ${#casualties[@]} -gt 0 ]; then
    msg="$(IFS='; '; echo "${casualties[*]}")"
    echo "CASUALTY(${#casualties[@]}): $msg"
    verdict FAIL "${#casualties[@]} unit(s) down: $msg$waived_note$skips"
    exit 1
fi
echo "OK: $ok_count enabled unit(s) active$waived_note$skips"
verdict OK "$ok_count enabled unit(s) active$waived_note$skips"
exit 0
