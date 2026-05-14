#!/usr/bin/env bash
# install_lab_traffic.sh — idempotent installer for the lab-traffic
# soak services (meshforge-echo + meshforge-tracer).
#
# Why this exists: on 2026-05-14 the rollup showed one fleet box at
# 96.5% no-route over 138/144 samples to another. Root cause: the
# destination box never got the echo systemd unit on the 2026-05-12
# fleet install — only the identity bootstrap (lab_echo_identity) ran.
# Every box was supposed to carry both. Hand-installs miss boxes; this
# script makes the install repeatable so the next fleet roll doesn't
# regress.
#
# Run once per fleet box, as the operator (no sudo):
#   bash /opt/meshforge/scripts/install_lab_traffic.sh
#
# Safe to re-run. Skips work that's already done. Respects operator
# intent: if a unit is currently disabled (you turned it off
# deliberately), this script does NOT re-enable it — it just confirms
# the unit file is in place. Pass --force-enable to override that.
#
# Tracer is opt-out: pass --no-tracer to install only the echo
# responder (useful on a box that should answer pings but not generate
# them — e.g. a heavily loaded gateway).

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DIR="$REPO_ROOT/templates/systemd"
USER_UNIT_DIR="$HOME/.config/systemd/user"
CONFIG_DIR="$HOME/.config/meshforge"

INSTALL_TRACER=1
FORCE_ENABLE=0
for arg in "$@"; do
    case "$arg" in
        --no-tracer)     INSTALL_TRACER=0 ;;
        --force-enable)  FORCE_ENABLE=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "error: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "error: do not run as root — lab services are user-scope." >&2
    echo "       Re-run as the operator: bash $0" >&2
    exit 2
fi

if [[ ! -d "$TEMPLATE_DIR" ]]; then
    echo "error: template dir missing: $TEMPLATE_DIR" >&2
    exit 2
fi

mkdir -p "$USER_UNIT_DIR" "$CONFIG_DIR"

# install_template src dest — copy if missing or different. Returns 0
# even on no-op; sets CHANGED=1 globally if we wrote something.
CHANGED=0
install_template() {
    local src="$1"
    local dest="$2"
    if [[ ! -f "$src" ]]; then
        echo "error: template missing: $src" >&2
        exit 2
    fi
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
        echo "  unchanged: $(basename "$dest")"
        return 0
    fi
    install -m 0644 "$src" "$dest"
    CHANGED=1
    echo "  installed: $(basename "$dest")"
}

echo "Installing lab-traffic user units → $USER_UNIT_DIR"
install_template "$TEMPLATE_DIR/meshforge-echo-user.service" \
                 "$USER_UNIT_DIR/meshforge-echo.service"
if [[ $INSTALL_TRACER -eq 1 ]]; then
    install_template "$TEMPLATE_DIR/meshforge-tracer-user.service" \
                     "$USER_UNIT_DIR/meshforge-tracer.service"
    install_template "$TEMPLATE_DIR/meshforge-tracer-user.timer" \
                     "$USER_UNIT_DIR/meshforge-tracer.timer"
fi

if [[ $CHANGED -eq 1 ]]; then
    systemctl --user daemon-reload
fi

# Generate identity if missing. --init is idempotent and prints
# "<short>=<hash>" to stdout — capture for the operator.
echo
echo "Identity bootstrap:"
if [[ ! -f "$CONFIG_DIR/lab_echo_identity" ]]; then
    echo "  no lab_echo_identity yet — generating"
fi
HASH_LINE="$(cd "$REPO_ROOT/src" && python3 -m lab.lxmf_echo --init 2>/dev/null \
             | grep -E '=[0-9a-f]{32}$' || true)"
if [[ -z "$HASH_LINE" ]]; then
    echo "  error: --init produced no hash line — check RNS/LXMF install" >&2
    exit 3
fi
echo "  $HASH_LINE"
echo "  (paste into ~/.config/meshforge/lab_peers on every box that should ping this one)"

# Enable services unless operator has explicitly disabled them.
# `is-enabled` returns 'enabled', 'disabled', 'static', or fails.
# We honor 'disabled' (operator intent) unless --force-enable.
enable_unit() {
    local unit="$1"
    local state
    state="$(systemctl --user is-enabled "$unit" 2>/dev/null || echo "missing")"
    case "$state" in
        enabled)
            echo "  $unit: already enabled"
            ;;
        disabled)
            if [[ $FORCE_ENABLE -eq 1 ]]; then
                systemctl --user enable --now "$unit"
                echo "  $unit: force-enabled"
            else
                echo "  $unit: DISABLED by operator — leaving alone (pass --force-enable to override)"
            fi
            ;;
        static)
            # static means the unit has no [Install] section; for the
            # tracer .service this is normal (timer pulls it).
            echo "  $unit: static (timer-driven; nothing to enable)"
            ;;
        *)
            systemctl --user enable --now "$unit"
            echo "  $unit: enabled"
            ;;
    esac
}

echo
echo "Enabling services:"
enable_unit meshforge-echo.service
if [[ $INSTALL_TRACER -eq 1 ]]; then
    enable_unit meshforge-tracer.timer
fi

# Status summary so the operator can see at a glance.
echo
echo "Status:"
for unit in meshforge-echo.service meshforge-tracer.timer; do
    if [[ $INSTALL_TRACER -eq 0 && "$unit" == "meshforge-tracer.timer" ]]; then
        continue
    fi
    state="$(systemctl --user is-active "$unit" 2>/dev/null || echo "inactive")"
    printf '  %-30s %s\n' "$unit" "$state"
done

echo
echo "Done. Tail the echo log:"
echo "  journalctl --user -u meshforge-echo --since '1 min ago' --no-pager"
