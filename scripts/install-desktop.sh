#!/bin/bash
# MeshForge Desktop Integration Installer
#
# Installs:
# - Desktop launcher in applications menu
# - Icon in standard locations
# - Symlink in /opt/meshforge
#
# Usage: sudo ./scripts/install-desktop.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Resolve actual path (follow symlinks)
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"

# THE list of copied files, shared with ensure_desktop.sh's present check.
. "$SCRIPT_DIR/lib/desktop.sh"

# The install location. MESHFORGE_INSTALL_LINK exists for the test harness
# only; this runs under sudo, which resets the environment.
MF_LINK="${MESHFORGE_INSTALL_LINK:-/opt/meshforge}"

# Refuse to displace a REAL install directory with this checkout. This used
# to `mv` it to a .backup and symlink a worktree or second clone in its
# place — every fleet service runs from that directory (review of 5aec69f1).
# Checked BEFORE the root check so it refuses the same way for anyone.
if [ -d "$MF_LINK" ] && [ ! -L "$MF_LINK" ] && [ "$(cd "$MF_LINK" && pwd -P)" != "$PROJECT_DIR" ]; then
    echo "Refused: $MF_LINK is a real directory and this checkout is $PROJECT_DIR."
    echo "  Installing from here would move $MF_LINK aside and symlink this checkout in its place."
    echo "  Run the installer from the install itself: sudo bash $MF_LINK/scripts/install-desktop.sh"
    echo "  (to really switch installs, move $MF_LINK aside yourself first, then re-run)"
    exit 1
fi

echo "==========================================="
echo "MeshForge Desktop Integration Installer"
echo "==========================================="
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Determine the actual user (not root)
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_USER=$(whoami)
    REAL_HOME="$HOME"
fi

echo "Installing for user: $REAL_USER"
echo "Project directory: $PROJECT_DIR"
echo

# Create /opt/meshforge symlink (only if not already there)
echo "Setting up /opt/meshforge..."
if [ "$PROJECT_DIR" = "$MF_LINK" ]; then
    echo "  Already installed at $MF_LINK"
elif [ -L "$MF_LINK" ]; then
    # Check if symlink points to correct location
    CURRENT_TARGET="$(readlink -f "$MF_LINK" 2>/dev/null || echo '')"
    if [ "$CURRENT_TARGET" = "$PROJECT_DIR" ]; then
        echo "  Symlink already correct: $MF_LINK -> $PROJECT_DIR"
    else
        rm "$MF_LINK"
        ln -sf "$PROJECT_DIR" "$MF_LINK"
        echo "  Updated symlink: $MF_LINK -> $PROJECT_DIR"
    fi
elif [ -d "$MF_LINK" ]; then
    # Unreachable: the guard above refused a foreign checkout, so a real
    # directory here IS this checkout. Never move it aside.
    echo "  Already installed at $MF_LINK"
else
    ln -sf "$PROJECT_DIR" "$MF_LINK"
    echo "  $MF_LINK -> $PROJECT_DIR"
fi

# The per-user menu copy is decided BEFORE the system entry is replaced: it is
# ours to refresh only while absent or identical to the entry we installed
# last; anything else is a menu edit — kept (review of 5aec69f1).
DESKTOP_FILE="$PROJECT_DIR/org.meshforge.app.desktop"
APPS_DIR="${DESKTOP_APPS_DIR:-/usr/share/applications}"
INSTALLED_DESKTOP="$APPS_DIR/org.meshforge.app.desktop"
USER_APPS_DIR="$REAL_HOME/.local/share/applications"
REFRESH_USER_COPY=false
if desktop_user_copy_is_ours "$INSTALLED_DESKTOP" "$USER_APPS_DIR/org.meshforge.app.desktop"; then
    REFRESH_USER_COPY=true
fi

# Install icons, the desktop entry, the polkit policy and the SSH login
# message — every copied file, from THE shared list (scripts/lib/desktop.sh).
echo "Installing icons, desktop file, polkit policy and login message..."
# Remove old meshforge.desktop if it exists
rm -f "$APPS_DIR/meshforge.desktop" 2>/dev/null || true
while IFS="$(printf '\t')" read -r src dest; do
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    chmod 644 "$dest"
done < <(desktop_copies "$PROJECT_DIR")

if [ -f "$PROJECT_DIR/assets/meshforge-icon.svg" ]; then
    # Create a PNG version for compatibility
    if command -v rsvg-convert &> /dev/null; then
        rsvg-convert -w 128 -h 128 "$PROJECT_DIR/assets/meshforge-icon.svg" > /usr/share/pixmaps/meshforge.png
        rsvg-convert -w 128 -h 128 "$PROJECT_DIR/assets/meshforge-icon.svg" > /usr/share/pixmaps/org.meshforge.app.png
    elif command -v convert &> /dev/null; then
        convert -background none "$PROJECT_DIR/assets/meshforge-icon.svg" -resize 128x128 /usr/share/pixmaps/meshforge.png
        convert -background none "$PROJECT_DIR/assets/meshforge-icon.svg" -resize 128x128 /usr/share/pixmaps/org.meshforge.app.png
    fi
fi

# Also install to user's local applications (for menu) — unless it is the
# user's own edit (decided above, before the system entry changed).
mkdir -p "$USER_APPS_DIR"
rm -f "$USER_APPS_DIR/meshforge.desktop" 2>/dev/null || true
if [ "$REFRESH_USER_COPY" = true ]; then
    cp "$INSTALLED_DESKTOP" "$USER_APPS_DIR/"
    chown "$REAL_USER:$REAL_USER" "$USER_APPS_DIR/org.meshforge.app.desktop"
else
    echo "  Kept $USER_APPS_DIR/org.meshforge.app.desktop: it differs from the entry we installed (a menu edit). It shadows the system entry; rm it to use the system one."
fi

# Update desktop database
echo "Updating desktop database..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    update-desktop-database "$USER_APPS_DIR" 2>/dev/null || true
fi

# Update icon cache
echo "Updating icon cache..."
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
fi

# Install launcher scripts
echo "Installing launcher scripts..."
# SYMLINKS, not copies. This script runs on EVERY update.sh, and a `cp`
# here is what re-froze the installed command at install time while the
# repo moved on — it is how the fleet ended up running two different
# `meshforge` programs (2026-09-20). A symlink cannot go stale.
# ⚠️ Never go back to `cp`/`cat >`: both FOLLOW a symlink, so they would
# write straight through into the repo script and corrupt it.
ln -sfn "$PROJECT_DIR/scripts/meshforge-launcher.sh" /usr/local/bin/meshforge

# Install terminal launcher (sets proper window class for icons)
ln -sfn "$PROJECT_DIR/scripts/meshforge-terminal.sh" /usr/local/bin/meshforge-terminal


# Polkit policy (for pkexec authentication) and the SSH login message were
# copied with the shared list above, where their directories exist.
if [ -d "${DESKTOP_SHARE_DIR:-/usr/share}/polkit-1/actions" ]; then
    echo "  Polkit policy installed - pkexec will prompt for password"
else
    echo "  Warning: Polkit not found, desktop launcher may not work"
fi
if [ -d "${DESKTOP_ETC_DIR:-/etc}/profile.d" ]; then
    echo "  SSH users will see MeshForge prompt on login"
fi

echo
echo "==========================================="
echo "Installation complete!"
echo "==========================================="
echo
echo "MeshForge has been added to your applications menu."
echo "You can find it under:"
echo "  - Internet"
echo "  - System Tools"
echo
echo "Or search for 'MeshForge' in your application launcher."
echo
echo "To run from command line:"
echo "  meshforge            # TUI launcher (default, works over SSH)"
echo "  meshforge tui        # Same as default"
echo "  meshforge maps       # Coverage map generator"
echo "  meshforge prometheus # Metrics exporter"
echo
