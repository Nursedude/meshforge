#!/bin/bash
# fleet_os_upgrade.sh — distro package upgrades across the fleet, deliberately.
#
# Born 2026-09-19 from a fleet-wide trixie/bookworm roll that was driven by
# hand. Everything that made that roll SAFE was reconstructed live, in a
# terminal, under time pressure; everything that made it SLOW was discovered
# the same way. This script is that session compiled down so the next one
# does not re-derive it.
#
# ⚠️ This script ACTS. It is not a menu item and must not become one: a
# fleet-wide apt path behind a keystroke is the 2026-07-24 shape (a sweep
# that restarted "every installed unit" started a service that was off BY
# DESIGN). The read-only surface is the TUI's Update Readiness screen and
# `scripts/fleet_platform.py show`. This is the knowing act.
#
# What the 09-19 roll taught, encoded here:
#
#   1. HOLDS ARE THE ONLY GUARD. meshtasticd is apt-mark held on every box
#      that has it, because published builds regress the USB (CH341) boxes
#      into firmware#10468. Several boxes carry the OBS alpha repo at
#      priority 500, so a hold that quietly vanished would put an ALPHA on
#      the fleet. `survey` proves the hold against a real simulation, and
#      `upgrade` re-checks it AFTER and shouts if it moved.
#
#   2. SIMULATE BEFORE INSTALLING. `apt-get -s full-upgrade` names removals.
#      On 09-19 every box planned exactly one (pcmanfm -> pcmanfm-pi, a Pi OS
#      rename). Unreviewed removals are refused unless --allow-removals.
#
#   3. RUN DETACHED ON THE REMOTE. An ssh drop must never interrupt a dpkg
#      transaction. The work runs under setsid+nohup writing markers to
#      /var/log/mf_osupgrade.log; this script polls that file.
#
#   4. NON-INTERACTIVE OR IT HANGS. DEBIAN_FRONTEND=noninteractive,
#      NEEDRESTART_MODE=l, and --force-confold so a conffile prompt cannot
#      block a detached run forever. confold KEEPS local edits.
#
#   5. THE WAN IS ~2 Mbps AND THE BOXES ARE LAN-LOCAL. 522 MB x 8 boxes over
#      the uplink is ~4 hours; seeding one box's /var/cache/apt/archives to
#      the others over LAN made it ~35 min. `seed` does that. Seed FROM an
#      idle box: on 09-19 seeding from a box that was itself upgrading drove
#      its load average to 6.9 and crawled.
#
#   6. VERIFY AFTER SETTLE, NOT AT FIRST ANSWER. A box that answers ssh 38 s
#      after reboot still has units starting; sampling then reported
#      meshforge-watchdog "inactive" when it was merely not-yet-started.
#      `reboot` waits, then reads NRestarts and the radio init line rather
#      than trusting is-active (which reads a false green while a unit
#      cycles -- 2026-09-14 lehua).
#
# Usage:
#   scripts/fleet_os_upgrade.sh survey [box...]      # READ-ONLY (default)
#   scripts/fleet_os_upgrade.sh seed <src> <dst>...  # LAN cache copy
#   scripts/fleet_os_upgrade.sh upgrade <box>...     # [--allow-removals]
#   scripts/fleet_os_upgrade.sh reboot <box>...      # serial, verified
#
# No --all. Naming the boxes is the point: this is the knowing act.
#
# Exit: 0 all good; 1 a box failed or a hold moved; 2 nothing to act on.

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)
LOG=/var/log/mf_osupgrade.log
ALLOW_REMOVALS=0
RC=0

. "$REPO/scripts/lib/fleet_hosts.sh"

die() { echo "ERROR: $*" >&2; exit 1; }

# ── remote helpers ───────────────────────────────────────────────────────
rsh() { timeout "${2:-60}" ssh "${SSH_OPTS[@]}" "$1" "${3:-true}" 2>/dev/null; }

all_hosts() {
    if fleet_hosts_resolve "$REPO"; then printf '%s\n' "$FLEET_HOSTS_LIST"
    else die "no fleet_hosts list found; name boxes explicitly"; fi
}

# ── survey: read-only ────────────────────────────────────────────────────
do_survey() {
    local hosts=("$@"); [ ${#hosts[@]} -eq 0 ] && mapfile -t hosts < <(all_hosts)
    printf '%-20s %-9s %-6s %-5s %-8s %s\n' BOX BASE PEND REMV REBOOT HOLDS
    for h in "${hosts[@]}"; do
        out=$(timeout 300 ssh "${SSH_OPTS[@]}" "$h" '
            . /etc/os-release
            sudo apt-get update -qq >/dev/null 2>&1
            sudo apt-get -s full-upgrade 2>/dev/null > /tmp/mf_sim.txt
            i=$(grep -c "^Inst " /tmp/mf_sim.txt)
            r=$(grep -c "^Remv " /tmp/mf_sim.txt)
            rl=$(grep "^Remv " /tmp/mf_sim.txt | awk "{print \$2}" | tr "\n" "," | sed "s/,$//")
            hold=$(apt-mark showhold | tr "\n" "," | sed "s/,$//")
            rb=$([ -f /var/run/reboot-required ] && echo yes || echo no)
            echo "$VERSION_CODENAME|$i|$r|$rb|${hold:-none}|${rl:-none}"' 2>/dev/null)
        if [ -z "$out" ]; then
            printf '%-20s %s\n' "$h" "UNREACHABLE — state UNKNOWN, not 'fine'"; RC=1; continue
        fi
        IFS='|' read -r base pend remv rb hold rl <<<"$out"
        printf '%-20s %-9s %-6s %-5s %-8s %s\n' "$h" "$base" "$pend" "$remv" "$rb" "$hold"
        [ "$remv" != "0" ] && echo "                     removals: $rl"
    done
}

# ── seed: LAN cache copy ─────────────────────────────────────────────────
do_seed() {
    local src="$1"; shift
    [ $# -eq 0 ] && die "seed needs a source and at least one destination"
    local busy
    busy=$(rsh "$src" 30 'pgrep -c apt-get 2>/dev/null || echo 0')
    if [ "${busy:-0}" != "0" ]; then
        echo "WARNING: $src is running apt right now. Seeding from a busy box is slow"
        echo "         (09-19: load average hit 6.9 and the copy crawled). Prefer an idle box."
    fi
    for d in "$@"; do
        local t0 n; t0=$(date +%s)
        n=$(timeout 1800 ssh "${SSH_OPTS[@]}" "$src" 'sudo tar -C /var/cache/apt/archives --exclude=partial --exclude=lock -cf - . 2>/dev/null' \
            | timeout 1800 ssh "${SSH_OPTS[@]}" "$d" 'sudo tar -C /var/cache/apt/archives -xf - 2>/dev/null; ls /var/cache/apt/archives/*.deb 2>/dev/null | wc -l')
        if [ -z "$n" ]; then echo "seed $src -> $d FAILED"; RC=1
        else echo "seeded $d -> $n debs in $(( $(date +%s)-t0 ))s"; fi
    done
    echo "NOTE: a truncated .deb is harmless — apt checksum-verifies the cache and refetches."
}

# ── upgrade ──────────────────────────────────────────────────────────────
launch_upgrade() {
    local h="$1"
    # Clear any PRIOR run's log FIRST. The remote script truncates via
    # `exec >`, but the waiter polls for MF_UPGRADE_RC and a stale marker
    # from a previous upgrade would be read as THIS run's result -- a
    # degraded state wearing a valid-looking value (honest_failure_modes #1).
    rsh "$h" 30 "sudo rm -f $LOG" >/dev/null 2>&1
    timeout 120 ssh "${SSH_OPTS[@]}" "$h" 'cat <<'"'"'EOS2'"'"' | sudo tee /usr/local/sbin/mf_osupgrade.sh >/dev/null
#!/bin/bash
LOG=/var/log/mf_osupgrade.log
exec >"$LOG" 2>&1
echo "MF_UPGRADE_START=$(date -Is) host=$(hostname)"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get update -qq
apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade
rc=$?
echo "MF_UPGRADE_RC=$rc"
echo "MF_HOLD_AFTER=$(apt-mark showhold | tr "\n" " ")"
echo "MF_REBOOT_REQUIRED=$([ -f /var/run/reboot-required ] && echo yes || echo no)"
echo "MF_KERNEL_RUNNING=$(uname -r)"
echo "MF_UPGRADE_END=$(date -Is)"
EOS2
sudo chmod +x /usr/local/sbin/mf_osupgrade.sh
sudo setsid nohup /usr/local/sbin/mf_osupgrade.sh >/dev/null 2>&1 < /dev/null &
sleep 2' >/dev/null 2>&1
}

# How long to wait for ONE launched box's MF_UPGRADE_RC before calling its
# state UNKNOWN. 09-19's slowest box (a Pi4, ~520 MB from a seeded cache)
# finished well inside this; a box past it is not "still fine", it is a
# box nobody can vouch for — say so and move on to the others.
UPGRADE_DEADLINE_S="${MF_UPGRADE_DEADLINE_S:-7200}"

do_upgrade() {
    [ $# -eq 0 ] && die "upgrade needs at least one box"
    local -a holds_before=() launched=()
    for h in "$@"; do
        local sim remv rl hold
        sim=$(timeout 300 ssh "${SSH_OPTS[@]}" "$h" '
            sudo apt-get update -qq >/dev/null 2>&1
            sudo apt-get -s full-upgrade 2>/dev/null > /tmp/mf_sim.txt
            echo "$(grep -c "^Remv " /tmp/mf_sim.txt)|$(grep "^Remv " /tmp/mf_sim.txt | awk "{print \$2}" | tr "\n" "," | sed "s/,$//")|$(apt-mark showhold | tr "\n" "," | sed "s/,$//")"' 2>/dev/null)
        [ -z "$sim" ] && { echo "$h: UNREACHABLE — skipping"; RC=1; continue; }
        IFS='|' read -r remv rl hold <<<"$sim"
        holds_before+=("$h=$hold")
        if [ "$remv" != "0" ] && [ "$ALLOW_REMOVALS" != "1" ]; then
            echo "$h: REFUSING — plan removes $remv package(s): $rl"
            echo "     Review them, then re-run with --allow-removals."
            RC=1; continue
        fi
        echo "$h: launching (removals=$remv, holds=$hold)"
        if launch_upgrade "$h"; then
            launched+=("$h")
        else
            echo "  !! $h: launch failed — upgrade state UNKNOWN (it may or may not have started)"
            RC=1
        fi
    done

    # Wait ONLY on boxes this run launched. Until 2026-09-22 this looped over
    # every box NAMED: a refused box was polled forever, or — if an earlier
    # run's log was still on it (launch_upgrade is what clears it, and a
    # refused box never gets there) — its stale MF_UPGRADE_RC=0 was reported
    # as THIS run's success. And with no deadline, one box that never wrote
    # its marker hung the whole script (review 2026-09-22, Opus 5.5).
    [ ${#launched[@]} -eq 0 ] && { echo; echo "nothing launched."; return; }
    echo; echo "=== waiting for completion (${launched[*]}) ==="
    for h in "${launched[@]}"; do
        local deadline=$(( $(date +%s) + UPGRADE_DEADLINE_S ))
        until rsh "$h" 20 "sudo grep -q MF_UPGRADE_RC $LOG" 2>/dev/null; do
            if [ "$(date +%s)" -ge "$deadline" ]; then
                echo "  !! $h: no MF_UPGRADE_RC after ${UPGRADE_DEADLINE_S}s — state UNKNOWN."
                echo "     Check it by hand: ssh $h sudo tail $LOG"
                RC=1; continue 2
            fi
            sleep 30
        done
        local res rc_h hold_after before
        res=$(rsh "$h" 20 "sudo grep -E 'MF_UPGRADE_RC|MF_HOLD_AFTER|MF_REBOOT_REQUIRED|MF_KERNEL_RUNNING' $LOG")
        rc_h=$(echo "$res" | grep -oP 'MF_UPGRADE_RC=\K[0-9]+')
        hold_after=$(echo "$res" | grep -oP 'MF_HOLD_AFTER=\K.*' | tr -s ' ' | sed 's/ $//' | tr ' ' ',')
        echo "$h: $(echo "$res" | tr '\n' ' ')"
        [ "$rc_h" != "0" ] && { echo "  !! $h upgrade rc=$rc_h"; RC=1; }
        for b in "${holds_before[@]}"; do
            before="${b#*=}"; [ "${b%%=*}" = "$h" ] || continue
            if [ "$before" != "none" ] && [ "$hold_after" != "$before" ]; then
                echo "  !! HOLD CHANGED on $h: was '$before', now '$hold_after'"
                echo "     A vanished meshtasticd hold means the next upgrade can land an alpha."
                RC=1
            fi
        done
    done
}

# ── reboot: serial, verified ─────────────────────────────────────────────
do_reboot() {
    [ $# -eq 0 ] && die "reboot needs at least one box"
    for h in "$@"; do
        local need old t0 nb back
        need=$(rsh "$h" 20 '[ -f /var/run/reboot-required ] && echo yes || echo no')
        [ "$need" != "yes" ] && { echo "SKIP $h — no reboot required"; continue; }
        old=$(rsh "$h" 20 'cat /proc/sys/kernel/random/boot_id')
        [ -z "$old" ] && { echo "SKIP $h — unreachable, state UNKNOWN"; RC=1; continue; }
        t0=$(date +%s)
        rsh "$h" 20 'sudo systemctl reboot' >/dev/null 2>&1
        sleep 20; back=0
        for _ in $(seq 1 60); do
            nb=$(rsh "$h" 15 'cat /proc/sys/kernel/random/boot_id')
            [ -n "$nb" ] && [ "$nb" != "$old" ] && { back=1; break; }
            sleep 10
        done
        [ "$back" = 0 ] && { echo "!! $h DID NOT RETURN within 600s — state UNKNOWN"; RC=1; continue; }
        # settle before judging: at 38 s units are still starting (09-19)
        sleep 45
        echo "--- $h back after $(( $(date +%s)-t0 ))s ---"
        timeout 60 ssh "${SSH_OPTS[@]}" "$h" '
            echo "  kernel=$(uname -r)  reboot_required=$([ -f /var/run/reboot-required ] && echo STILL_YES || echo no)"
            for u in meshtasticd rnsd meshforge-map meshforge-watchdog meshforge-gateway; do
              systemctl cat $u >/dev/null 2>&1 && echo "  $u=$(systemctl is-active $u) NRestarts=$(systemctl show -p NRestarts --value $u)"
            done
            echo "  user_units_running=$(systemctl --user list-units --type=service --state=running --no-legend "meshforge*" 2>/dev/null | wc -l)"
            # rnstatus is often ONLY in ~/.local/bin, which is NOT on the
            # non-interactive ssh PATH (live-caught on kiai 2026-09-19: bare
            # `rnstatus` returned 127 -- "command not found" -- which reads
            # like a status code and is not one. An instrument that could not
            # RUN must say UNKNOWN, never emit a number a reader will skim as
            # a verdict.)
            if systemctl cat rnsd >/dev/null 2>&1; then
              rs=$(command -v rnstatus 2>/dev/null || true)
              [ -z "$rs" ] && [ -x "$HOME/.local/bin/rnstatus" ] && rs="$HOME/.local/bin/rnstatus"
              [ -z "$rs" ] && [ -x /usr/local/bin/rnstatus ] && rs=/usr/local/bin/rnstatus
              if [ -n "$rs" ]; then timeout 10 "$rs" >/dev/null 2>&1; echo "  rnstatus_rc=$? (via $rs)"
              else echo "  rnstatus=UNKNOWN - binary not found; RNS NOT verified here"; fi
            fi
            systemctl cat meshtasticd >/dev/null 2>&1 && echo "  radio: $(sudo journalctl -u meshtasticd -b --no-pager 2>/dev/null | grep -oE "init result [0-9-]+|init success" | tail -2 | tr "\n" " ")"
        ' 2>/dev/null
    done
}

# ── dispatch ─────────────────────────────────────────────────────────────
MODE="${1:-survey}"; shift 2>/dev/null || true
ARGS=()
for a in "$@"; do
    case "$a" in
        --allow-removals) ALLOW_REMOVALS=1 ;;
        -*) die "unknown flag: $a" ;;
        *) ARGS+=("$a") ;;
    esac
done

case "$MODE" in
    survey)  do_survey "${ARGS[@]+"${ARGS[@]}"}" ;;
    seed)    do_seed "${ARGS[@]+"${ARGS[@]}"}" ;;
    upgrade) do_upgrade "${ARGS[@]+"${ARGS[@]}"}" ;;
    reboot)  do_reboot "${ARGS[@]+"${ARGS[@]}"}" ;;
    *) die "usage: $0 {survey|seed|upgrade|reboot} [box...] [--allow-removals]" ;;
esac
exit $RC
