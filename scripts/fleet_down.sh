#!/bin/bash
# fleet_down.sh — power the whole fleet down CLEANLY, in one command.
#
# Why (operator, 2026-09-27): "the rpi's have been going down the hard way —
# too many rpi's for me." A hard cut is how Lala (2026-08-27) left 13 corrupt
# state files. scripts/fleet_power.py already does the careful part (hop-aware
# order, posture declared in one write so nothing pages, clean systemd
# poweroff per box) — this is the front door to it: no box list to type, a
# dry-run plan first, one typed confirmation, then the manager itself LAST.
#
#   scripts/fleet_down.sh                  # plan, confirm, fleet off, then offer THIS box
#   scripts/fleet_down.sh --reason "Lala"  # recorded in the posture file
#   scripts/fleet_down.sh --drill moc4     # reboot instead of poweroff: exercise it safely
#   scripts/fleet_down.sh --yes            # NO prompts (for an automatic trigger) — powers
#                                          # the manager off too unless --no-self
#   scripts/fleet_up.sh                    # when power is back: clear the posture as boxes return
#
# Targets default to `fleet_power.py members` (monitored boxes + power_only).
# Routers/hops (alaula, trdev, hap) are NOT powered off: flash-based, and every
# box behind them is already down first.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FP="$REPO/scripts/fleet_power.py"
YES=0; DRILL=0; SELF=ask; REASON="fleet_down.sh"; UNTIL="+24h"; TARGETS=(); FORCE=()

while [ $# -gt 0 ]; do
    case "$1" in
        --yes) YES=1 ;;
        --drill) DRILL=1 ;;
        --no-self) SELF=no ;;
        --force) FORCE=(--force) ;;
        --reason) REASON="fleet_down.sh: $2"; shift ;;
        --until) UNTIL="$2"; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        -*) echo "fleet_down: unknown option $1" >&2; exit 2 ;;
        *) TARGETS+=("$1") ;;
    esac
    shift
done

mapfile -t MEMBERS < <(python3 "$FP" members)
if [ ${#TARGETS[@]} -eq 0 ]; then
    TARGETS=("${MEMBERS[@]}")
fi
# A WHOLE-fleet power-down silences every bridge by definition — that is the
# point, so the posture validator's "every bridge-capable box silenced"
# refusal is overridden for it (fleet_power records --force in the posture
# file). A PARTIAL list that happens to take out both bridges stays refused
# unless the operator passes --force deliberately.
if [ "$(printf '%s\n' "${TARGETS[@]}" | sort)" = "$(printf '%s\n' "${MEMBERS[@]}" | sort)" ]; then
    FORCE=(--force)
    echo "fleet_down: whole fleet (${#TARGETS[@]} boxes) — the bridge-silence refusal is overridden, recorded"
fi
if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "fleet_down: no targets (fleet_power.py members printed nothing) — REFUSING" >&2
    exit 2
fi

METHOD=poweroff
if [ $DRILL -eq 1 ]; then METHOD=reboot; SELF=no; fi
LOG_DIR="$HOME/.config/meshforge/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fleet_down_$(date +%Y%m%d_%H%M%S).log"

# 1. The plan — fleet_power.py's own dry run (it refuses unknown boxes,
#    stranding orders, and this box as a target, before anything happens).
python3 "$FP" down "${TARGETS[@]}" --method "$METHOD" --until "$UNTIL" \
    --reason "$REASON" "${FORCE[@]}" 2>&1 | tee "$LOG"
plan_rc=${PIPESTATUS[0]}
if [ "$plan_rc" -ne 0 ] || grep -q "^REFUSED" "$LOG"; then
    echo "fleet_down: the plan was REFUSED — nothing was touched (log: $LOG)" >&2
    exit 2
fi

# 2. One typed confirmation (never a y/n that a stray Enter can satisfy).
if [ $YES -eq 0 ]; then
    word=DOWN; [ $DRILL -eq 1 ] && word=DRILL
    echo
    ans=""
    if ! { true </dev/tty; } 2>/dev/null \
            || ! read -r -p "Type $word to ${METHOD} ${#TARGETS[@]} box(es): " ans </dev/tty; then
        echo "fleet_down: no terminal to confirm on — nothing was touched."
        echo "            Run it interactively, or pass --yes (an automatic trigger)."
        exit 1
    fi
    [ "$ans" = "$word" ] || { echo "fleet_down: not confirmed — nothing was touched."; exit 1; }
fi

# 3. Do it.
python3 "$FP" down "${TARGETS[@]}" --method "$METHOD" --until "$UNTIL" \
    --reason "$REASON" "${FORCE[@]}" --apply 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
    echo
    echo "⚠️ fleet_down: fleet_power exited $rc — NOT powering off this box, so you"
    echo "   keep a working seat to deal with the stragglers (log: $LOG)."
    exit "$rc"
fi

# 4. This box, last — only after every target confirmed dark.
[ "$SELF" = no ] && { echo "fleet_down: done; this box ($(hostname)) left up. Log: $LOG"; exit 0; }
if [ $YES -eq 0 ]; then
    ans=""
    { true </dev/tty; } 2>/dev/null \
        && read -r -p "Fleet is down. Type SELF to power off THIS box ($(hostname)) too: " ans </dev/tty
    [ "$ans" = "SELF" ] || { echo "fleet_down: this box left up. Log: $LOG"; exit 0; }
fi
echo "fleet_down: powering off $(hostname) — last. Log: $LOG" | tee -a "$LOG"
sync
exec sudo -n systemctl poweroff
