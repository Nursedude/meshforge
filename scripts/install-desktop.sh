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

# Create /opt/meshforge symlink
echo "Creating /opt/meshforge symlink..."
if [ -L /opt/meshforge ]; then
    rm /opt/meshforge
elif [ -d /opt/meshforge ]; then
    echo "Warning: /opt/meshforge exists as directory, backing up..."
    mv /opt/meshforge /opt/meshforge.backup.$(date +%Y%m%d)
fi
ln -sf "$PROJECT_DIR" /opt/meshforge
echo "  /opt/meshforge -> $PROJECT_DIR"

# Install icon
echo "Installing icon..."
ICON_SIZES=(16 24 32 48 64 128 256)
for size in "${ICON_SIZES[@]}"; do
    ICON_DIR="/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$ICON_DIR"

    # For now, just use the SVG at all sizes (works on modern systems)
    if [ -f "$PROJECT_DIR/assets/meshforge-icon.svg" ]; then
        cp "$PROJECT_DIR/assets/meshforge-icon.svg" "$ICON_DIR/meshforge.svg"
    fi
done

# Also install to pixmaps
echo "Installing to pixmaps..."
mkdir -p /usr/share/pixmaps
if [ -f "$PROJECT_DIR/assets/meshforge-icon.svg" ]; then
    cp "$PROJECT_DIR/assets/meshforge-icon.svg" /usr/share/pixmaps/meshforge.svg
    # Create a PNG version for compatibility
    if command -v rsvg-convert &> /dev/null; then
        rsvg-convert -w 128 -h 128 "$PROJECT_DIR/assets/meshforge-icon.svg" > /usr/share/pixmaps/meshforge.png
    elif command -v convert &> /dev/null; then
        convert -background none "$PROJECT_DIR/assets/meshforge-icon.svg" -resize 128x128 /usr/share/pixmaps/meshforge.png
    fi
fi

# Update desktop file to use installed icon
echo "Installing desktop file..."
DESKTOP_FILE="$PROJECT_DIR/meshforge.desktop"
INSTALLED_DESKTOP="/usr/share/applications/meshforge.desktop"

# Copy and modify desktop file
sed 's|Icon=.*|Icon=meshforge|' "$DESKTOP_FILE" > "$INSTALLED_DESKTOP"
chmod 644 "$INSTALLED_DESKTOP"

# Also install to user's local applications (for menu)
USER_APPS_DIR="$REAL_HOME/.local/share/applications"
mkdir -p "$USER_APPS_DIR"
cp "$INSTALLED_DESKTOP" "$USER_APPS_DIR/"
chown "$REAL_USER:$REAL_USER" "$USER_APPS_DIR/meshforge.desktop"

# Update desktop database
echo "Updating desktop database..."
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
    update-desktop-database "$USER_APPS_DIR" 2>/dev/null || true
fi

# Update icon cache
echo "Updating icon cache..."
if command -v gtk-update-icon-cache &> /dev/null; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
fi

# Install launcher script
echo "Installing launcher script..."
cp "$PROJECT_DIR/scripts/meshforge-launcher.sh" /usr/local/bin/meshforge
chmod 755 /usr/local/bin/meshforge

# Install polkit policy (for pkexec authentication)
echo "Installing polkit policy..."
POLKIT_DIR="/usr/share/polkit-1/actions"
if [ -d "$POLKIT_DIR" ]; then
    cp "$PROJECT_DIR/assets/org.meshforge.policy" "$POLKIT_DIR/"
    chmod 644 "$POLKIT_DIR/org.meshforge.policy"
    echo "  Polkit policy installed - pkexec will prompt for password"
else
    echo "  Warning: Polkit not found, desktop launcher may not work"
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
echo "  meshforge          # Uses pkexec for GUI"
echo "  sudo meshforge tui # For terminal UI"
echo
