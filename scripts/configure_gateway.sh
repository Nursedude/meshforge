#!/usr/bin/env bash
# Configure a MeshForge gateway on the local box.
#
# Idempotent companion to scripts/install_gateway_service.sh:
#   install_gateway_service.sh  → installs the systemd unit
#   configure_gateway.sh        → installs deps, configures gateway.json,
#                                 enables MQTT flags on the meshforge channel,
#                                 verifies rpc_key pinning, backs up any
#                                 previous gateway.json.
#
# Per Issue #31 (no silent persistent system changes), only runs on explicit
# operator action. Safe to re-run — existing files are backed up with a
# .bak.<timestamp> suffix if they differ, untouched if already correct.
#
# Usage:
#   sudo scripts/configure_gateway.sh                # configure for $SUDO_USER
#   sudo scripts/configure_gateway.sh <username>     # specific user
#   sudo DRY_RUN=1 scripts/configure_gateway.sh      # preview only, no writes

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/templates/gateway/gateway.json.template"
DRY_RUN="${DRY_RUN:-0}"

if [[ $EUID -ne 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "error: template not found: $TEMPLATE" >&2
    exit 1
fi

TARGET_USER="${1:-${SUDO_USER:-}}"
if [[ -z "$TARGET_USER" ]] || ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "error: target user missing or invalid. Pass a username or run via sudo." >&2
    exit 1
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" && -d "$TARGET_HOME" ]] || {
    echo "error: cannot resolve home for '$TARGET_USER'" >&2
    exit 1
}

CONFIG_DIR="$TARGET_HOME/.config/meshforge"
CONFIG_FILE="$CONFIG_DIR/gateway.json"
NOMADNET_IDENTITY="$TARGET_HOME/.nomadnetwork/storage/identity"

step() { echo; echo "==> $1"; }
warn() { echo "WARN: $1" >&2; }
die()  { echo "ERR:  $1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
step "Preflight"

[[ -f "$NOMADNET_IDENTITY" ]] || die \
    "NomadNet identity not found at $NOMADNET_IDENTITY — start NomadNet once to create it."

command -v python3 >/dev/null || die "python3 not on PATH"

# Find a Python that can import LXMF + RNS. Prefer system Python (that's what
# the gateway systemd unit will use). Fall back to pipx nomadnet venv for
# hash derivation only — the gateway itself needs system Python.
SYS_PY="/usr/bin/python3"
PIPX_PY="$TARGET_HOME/.local/share/pipx/venvs/nomadnet/bin/python"
HASH_PY=""
if sudo -u "$TARGET_USER" "$SYS_PY" -c "import LXMF, RNS" >/dev/null 2>&1; then
    HASH_PY="$SYS_PY"
elif [[ -x "$PIPX_PY" ]] && sudo -u "$TARGET_USER" "$PIPX_PY" -c "import LXMF, RNS" >/dev/null 2>&1; then
    HASH_PY="$PIPX_PY"
    warn "System python3 lacks LXMF/RNS — deriving hash via pipx venv only. Install deps below will fix system python3."
else
    die "Neither system python3 nor pipx nomadnet venv has LXMF+RNS. Install python3-rns or pipx install nomadnet first."
fi

# rpc_key must be pinned in rnsd's config or Issue #37/#41 re-surface.
# rnsd reads /etc/reticulum/config first, then ~/.config/reticulum/config,
# then ~/.reticulum/config — check them in that order.
RPC_KEY_SOURCE=""
for candidate in /etc/reticulum/config "$TARGET_HOME/.config/reticulum/config" "$TARGET_HOME/.reticulum/config" /root/.reticulum/config; do
    if [[ -r "$candidate" ]] && grep -qE '^\s*rpc_key\s*=\s*[0-9a-fA-F]{64}\s*$' "$candidate" 2>/dev/null; then
        RPC_KEY_SOURCE="$candidate"
        break
    fi
done
if [[ -z "$RPC_KEY_SOURCE" ]]; then
    warn "rpc_key is NOT pinned in any RNS config. The gateway will likely hit"
    warn "AuthenticationError on inbound RNS traffic (Issue #37/#41)."
    warn "Fix: openssl rand -hex 32 → add to /etc/reticulum/config as"
    warn "     'rpc_key = <64hex>' under [reticulum], restart rnsd."
else
    echo "    rpc_key pinned via: $RPC_KEY_SOURCE"
fi

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
step "Installing system-python dependencies for the gateway service"

DEPS=(lxmf rns paho-mqtt meshtastic)
if [[ "$DRY_RUN" = "1" ]]; then
    echo "    (dry-run) would: sudo -u $TARGET_USER pip3 install --user --break-system-packages ${DEPS[*]}"
else
    sudo -u "$TARGET_USER" pip3 install --user --break-system-packages "${DEPS[@]}" \
        2>&1 | tail -5
fi

# Re-pick HASH_PY now that deps are installed.
if [[ "$DRY_RUN" != "1" ]] && sudo -u "$TARGET_USER" "$SYS_PY" -c "import LXMF, RNS" >/dev/null 2>&1; then
    HASH_PY="$SYS_PY"
fi

# ---------------------------------------------------------------------------
# Derive LXMF destination hash
# ---------------------------------------------------------------------------
step "Deriving NomadNet LXMF destination hash"

LXMF_HASH=""
if [[ "$DRY_RUN" = "1" ]]; then
    LXMF_HASH="@LXMF_DESTINATION@"
    echo "    (dry-run) would derive from $NOMADNET_IDENTITY"
else
    LXMF_HASH="$(sudo -u "$TARGET_USER" "$HASH_PY" - <<PY
import LXMF, RNS
import tempfile, os
tmp = tempfile.mkdtemp(prefix="mf_cfg_")
RNS.Reticulum(configdir=tmp, loglevel=0)
ident = RNS.Identity.from_file("$NOMADNET_IDENTITY")
dest = RNS.Destination(ident, RNS.Destination.IN, RNS.Destination.SINGLE, "lxmf", "delivery")
print(RNS.hexrep(dest.hash, delimit=False))
PY
)" || die "Failed to derive LXMF hash — check python deps above."
    [[ "$LXMF_HASH" =~ ^[0-9a-f]{32}$ ]] || die "Bad LXMF hash shape: $LXMF_HASH"
    echo "    LXMF destination: $LXMF_HASH"
fi

# ---------------------------------------------------------------------------
# Detect meshforge channel index + enable uplink/downlink
# ---------------------------------------------------------------------------
step "Detecting meshforge channel and enabling MQTT flags"

# Prefer pipx meshtastic CLI; fall back to .local/bin or system
MESH_CLI=""
for candidate in "$TARGET_HOME/.local/bin/meshtastic" "$TARGET_HOME/.local/share/pipx/venvs/meshtastic/bin/meshtastic" "/usr/local/bin/meshtastic" "/usr/bin/meshtastic"; do
    if [[ -x "$candidate" ]]; then MESH_CLI="$candidate"; break; fi
done

CHANNEL_INDEX=""
if [[ -z "$MESH_CLI" ]]; then
    warn "meshtastic CLI not found — assuming channel index 2 (fleet convention)."
    CHANNEL_INDEX=2
else
    # Find the line "Index N: ... "name": "meshforge" ..."
    CH_INFO="$(sudo -u "$TARGET_USER" "$MESH_CLI" --host localhost --info 2>&1 | grep -E '^\s*Index [0-9]+:.*"name":\s*"meshforge"' || true)"
    if [[ -n "$CH_INFO" ]]; then
        CHANNEL_INDEX="$(echo "$CH_INFO" | sed -E 's/^\s*Index ([0-9]+):.*/\1/')"
        echo "    meshforge found at channel index: $CHANNEL_INDEX"
    else
        warn "No 'meshforge' channel configured in meshtasticd yet."
        warn "Create it in your mesh client or CLI, then re-run. Using 2 as placeholder."
        CHANNEL_INDEX=2
    fi

    # Enable MQTT uplink + downlink on that channel. Idempotent: setting an
    # already-true flag is a no-op on the device.
    if [[ "$DRY_RUN" = "1" ]]; then
        echo "    (dry-run) would: meshtastic --ch-index $CHANNEL_INDEX --ch-set uplink_enabled true --ch-set downlink_enabled true"
    elif [[ -n "$CH_INFO" ]]; then
        sudo -u "$TARGET_USER" "$MESH_CLI" --host localhost \
            --ch-index "$CHANNEL_INDEX" \
            --ch-set uplink_enabled true \
            --ch-set downlink_enabled true 2>&1 | tail -3 || \
            warn "channel flag update failed — enable manually if needed."
    fi
fi

# ---------------------------------------------------------------------------
# Render gateway.json
# ---------------------------------------------------------------------------
step "Rendering gateway.json"

install -d -o "$TARGET_USER" -g "$TARGET_USER" -m 0755 "$CONFIG_DIR"

NEW_CONFIG="$(mktemp)"
trap 'rm -f "$NEW_CONFIG"' EXIT
sed -e "s|@LXMF_DESTINATION@|$LXMF_HASH|g" \
    -e "s|@MESHFORGE_CHANNEL_INDEX@|$CHANNEL_INDEX|g" \
    "$TEMPLATE" > "$NEW_CONFIG"

if [[ -f "$CONFIG_FILE" ]] && cmp -s "$NEW_CONFIG" "$CONFIG_FILE"; then
    echo "    gateway.json already matches template — no change."
elif [[ "$DRY_RUN" = "1" ]]; then
    echo "    (dry-run) would render to: $CONFIG_FILE"
    echo "    diff vs current:"
    diff -u "${CONFIG_FILE:-/dev/null}" "$NEW_CONFIG" | head -30 || true
else
    if [[ -f "$CONFIG_FILE" ]]; then
        BACKUP="$CONFIG_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$CONFIG_FILE" "$BACKUP"
        echo "    previous config backed up to $BACKUP"
    fi
    install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" "$NEW_CONFIG" "$CONFIG_FILE"
    echo "    wrote $CONFIG_FILE"
fi

# ---------------------------------------------------------------------------
# Post-configure: nudge the service if it's already installed
# ---------------------------------------------------------------------------
step "Done"

if systemctl list-unit-files meshforge-gateway.service >/dev/null 2>&1; then
    echo "systemd unit is installed. To apply this config:"
    echo "    sudo systemctl restart meshforge-gateway"
    echo "    sudo journalctl -u meshforge-gateway -f"
else
    echo "systemd unit is NOT installed. Run next:"
    echo "    sudo scripts/install_gateway_service.sh $TARGET_USER"
fi
