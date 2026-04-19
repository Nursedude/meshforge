#!/usr/bin/env bash
# Install the MeshForge gateway as a systemd unit.
#
# Per Issue #31 (no silent persistent system changes), this is only ever
# run by explicit operator action — never from the launcher on startup.
#
# Usage:
#   sudo scripts/install_gateway_service.sh             # install for $SUDO_USER
#   sudo scripts/install_gateway_service.sh <username>  # install for a specific user

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/contrib/systemd/meshforge-gateway.service.in"
UNIT_PATH="/etc/systemd/system/meshforge-gateway.service"

if [[ $EUID -ne 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "error: template not found at $TEMPLATE" >&2
    exit 1
fi

# Resolve target user: first positional arg, else SUDO_USER, else fail
TARGET_USER="${1:-${SUDO_USER:-}}"
if [[ -z "$TARGET_USER" ]]; then
    echo "error: no target user given. Pass a username or run via sudo." >&2
    exit 1
fi

if ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "error: user '$TARGET_USER' does not exist" >&2
    exit 1
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    echo "error: cannot resolve home for '$TARGET_USER'" >&2
    exit 1
fi

echo "Installing meshforge-gateway.service"
echo "  User: $TARGET_USER"
echo "  Home: $TARGET_HOME"
echo "  Unit: $UNIT_PATH"

# Render template — write to tmp first, then atomic move
TMP_UNIT="$(mktemp /tmp/meshforge-gateway.service.XXXXXX)"
trap 'rm -f "$TMP_UNIT"' EXIT

sed -e "s|@USER@|$TARGET_USER|g" \
    -e "s|@HOME@|$TARGET_HOME|g" \
    "$TEMPLATE" > "$TMP_UNIT"

install -m 0644 "$TMP_UNIT" "$UNIT_PATH"

# Ensure the user's config/cache dirs exist and are user-owned — systemd's
# ProtectSystem=strict + ReadWritePaths= require these paths to exist at
# start time, otherwise the unit fails.
for dir in "$TARGET_HOME/.config/meshforge" "$TARGET_HOME/.cache/meshforge"; do
    if [[ ! -d "$dir" ]]; then
        install -d -o "$TARGET_USER" -g "$TARGET_USER" -m 0755 "$dir"
        echo "  created $dir"
    fi
done

systemctl daemon-reload
systemctl enable meshforge-gateway.service
systemctl restart meshforge-gateway.service

echo
echo "Status:"
systemctl --no-pager status meshforge-gateway.service | head -10 || true

echo
echo "Recent logs (journalctl -u meshforge-gateway -f for live):"
journalctl -u meshforge-gateway --since "10 seconds ago" --no-pager | tail -15 || true
