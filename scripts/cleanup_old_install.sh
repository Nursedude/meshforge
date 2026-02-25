#!/bin/bash
#
# MeshForge - Old Installation Cleanup
#
# Removes legacy artifacts from previous MeshForge/Meshtasticd installations:
# - Old GTK commands and desktop entries
# - Legacy "meshtasticd-installer" aliases
# - Stale systemd units referencing removed files
# - Old desktop files that point to GTK launcher
#
# Safe to run multiple times (idempotent).
#
# Usage:
#   sudo bash scripts/cleanup_old_install.sh
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   MeshForge - Old Installation Cleanup                    ║"
echo "║   Removing GTK/legacy artifacts from your system          ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo bash scripts/cleanup_old_install.sh"
    exit 1
fi

REMOVED=0
SKIPPED=0

remove_if_exists() {
    local path="$1"
    local desc="$2"
    if [[ -e "$path" ]] || [[ -L "$path" ]]; then
        rm -f "$path"
        echo -e "  ${GREEN}✓ Removed${NC}: $desc ($path)"
        REMOVED=$((REMOVED + 1))
    else
        SKIPPED=$((SKIPPED + 1))
    fi
}

remove_dir_if_exists() {
    local path="$1"
    local desc="$2"
    if [[ -d "$path" ]]; then
        rm -rf "$path"
        echo -e "  ${GREEN}✓ Removed${NC}: $desc ($path)"
        REMOVED=$((REMOVED + 1))
    else
        SKIPPED=$((SKIPPED + 1))
    fi
}

echo -e "${CYAN}[1/5] Removing old commands...${NC}"
remove_if_exists "/usr/local/bin/meshforge-gtk" "GTK launcher command"
remove_if_exists "/usr/local/bin/meshforge-cli" "CLI command (broken - pointed to missing src/main.py)"
remove_if_exists "/usr/local/bin/meshforge-web" "Web launcher command"
remove_if_exists "/usr/local/bin/meshtasticd-installer" "Legacy meshtasticd-installer alias"
remove_if_exists "/usr/local/bin/meshtasticd-cli" "Legacy meshtasticd-cli alias"

echo -e "${CYAN}[2/5] Removing old desktop entries...${NC}"
remove_if_exists "/usr/share/applications/meshforge.desktop" "Old GTK desktop entry"
# Also check user-level desktop entries
for user_home in /home/*/; do
    remove_if_exists "${user_home}.local/share/applications/meshforge.desktop" "User GTK desktop entry"
    remove_if_exists "${user_home}.local/share/applications/org.meshforge.app.desktop" "User desktop entry (will be replaced)"
done

echo -e "${CYAN}[3/5] Removing old GTK polkit policy...${NC}"
remove_if_exists "/usr/share/polkit-1/actions/org.meshforge.policy" "GTK polkit authentication policy"

echo -e "${CYAN}[4/5] Cleaning up old files in /opt/meshforge...${NC}"
if [[ -d "/opt/meshforge" ]]; then
    remove_if_exists "/opt/meshforge/meshforge.desktop" "Old duplicate desktop file"
    remove_if_exists "/opt/meshforge/setup.py" "Legacy setup.py (meshtasticd-installer)"
    remove_if_exists "/opt/meshforge/web_installer.py" "Old web installer"
    remove_if_exists "/opt/meshforge/src/main_gtk.py" "Frozen GTK main"
    remove_if_exists "/opt/meshforge/src/launcher_vte.py" "VTE terminal wrapper"
    remove_dir_if_exists "/opt/meshforge/src/gtk_ui" "Frozen GTK UI module"
    remove_if_exists "/opt/meshforge/scripts/install_arm64.sh" "Duplicate arm64 installer"
    remove_if_exists "/opt/meshforge/scripts/install_armhf.sh" "Duplicate armhf installer"
fi

echo -e "${CYAN}[5/5] Checking for stale systemd units...${NC}"
if systemctl is-enabled meshforge.service &>/dev/null 2>&1; then
    echo -e "  ${YELLOW}!${NC} meshforge.service is enabled - checking if it references removed files..."
    # Only disable if it points to something that no longer exists
    EXEC_PATH=$(systemctl show meshforge.service -p ExecStart 2>/dev/null | grep -oP 'path=\K[^ ;]+' || true)
    if [[ -n "$EXEC_PATH" ]] && [[ ! -f "$EXEC_PATH" ]]; then
        systemctl disable meshforge.service 2>/dev/null || true
        echo -e "  ${GREEN}✓ Disabled${NC}: stale meshforge.service (pointed to missing $EXEC_PATH)"
        REMOVED=$((REMOVED + 1))
    else
        echo -e "  ${GREEN}✓ OK${NC}: meshforge.service references valid paths"
    fi
else
    SKIPPED=$((SKIPPED + 1))
fi

# Update desktop database if available
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Cleanup Complete                       ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Removed: ${GREEN}${REMOVED}${NC} old artifacts"
echo -e "  Skipped: ${YELLOW}${SKIPPED}${NC} (not found / already clean)"
echo ""
echo -e "${CYAN}Your system now uses:${NC}"
echo "  meshforge            - TUI launcher (raspi-config style)"
echo "  meshforge-tui        - Same (explicit)"
echo ""
echo -e "${CYAN}To update MeshForge to latest:${NC}"
echo "  cd /opt/meshforge && sudo git pull"
echo ""
echo -e "${YELLOW}Optional: Remove unused GTK4 packages to free space:${NC}"
echo "  sudo apt remove --autoremove python3-gi gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1 gir1.2-webkit2-4.1"
echo ""
