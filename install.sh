#!/bin/bash
#
# MeshForge - One-Liner Quick Install
#
# LoRa Mesh Network Development & Operations Suite
# Downloads, installs, and launches MeshForge automatically.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo bash
#
# Options:
#   UPGRADE_SYSTEM=yes  - Automatically upgrade all system packages
#   SKIP_UPGRADE=yes    - Skip the system upgrade prompt entirely
#   INSTALL_GTK=yes     - Install GTK4/libadwaita for desktop GUI
#   INSTALL_DESKTOP=yes - Create desktop entry for MeshForge
#
# Examples:
#   # Install with system upgrade and desktop GUI
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo UPGRADE_SYSTEM=yes INSTALL_GTK=yes bash
#
#   # Install without upgrade prompt
#   curl -sSL https://raw.githubusercontent.com/Nursedude/meshforge/main/install.sh | sudo SKIP_UPGRADE=yes bash
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   MeshForge - LoRa Mesh Network Operations Suite          ║"
echo "║   For Raspberry Pi OS & Linux                             ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   echo "Please run: sudo bash install.sh"
   exit 1
fi

# Detect OS and architecture
echo -e "${CYAN}[1/5] Detecting system...${NC}"
OS=$(uname -s)
ARCH=$(uname -m)
echo "  OS: $OS"
echo "  Architecture: $ARCH"

# Check for Raspberry Pi OS
if [[ ! -f /etc/os-release ]]; then
    echo -e "${YELLOW}Warning: Cannot detect OS version${NC}"
fi

# System update
echo -e "${CYAN}[2/7] Updating package lists...${NC}"
apt-get update -qq
echo -e "${GREEN}  ✓ Package lists updated${NC}"

# Optional: Upgrade system packages
# Check if running interactively or if UPGRADE_SYSTEM is set
echo -e "${CYAN}[3/7] System upgrade (optional)...${NC}"
if [[ "${UPGRADE_SYSTEM}" == "yes" ]]; then
    echo -e "${YELLOW}  Upgrading all system packages (this may take several minutes)...${NC}"
    apt-get upgrade -y
    echo -e "${GREEN}  ✓ System upgraded${NC}"
elif [[ "${SKIP_UPGRADE}" == "yes" ]]; then
    echo -e "${YELLOW}  ⊘ Skipped (SKIP_UPGRADE=yes)${NC}"
else
    # Try to read from TTY if available for interactive prompt
    if [[ -c /dev/tty ]]; then
        echo -e "${YELLOW}  This will upgrade all system packages and may take several minutes.${NC}"
        read -p "  Would you like to upgrade system packages now? [y/N] " -n 1 -r < /dev/tty
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            apt-get upgrade -y
            echo -e "${GREEN}  ✓ System upgraded${NC}"
        else
            echo -e "${YELLOW}  ⊘ System upgrade skipped${NC}"
        fi
    else
        echo -e "${YELLOW}  ⊘ Skipped (non-interactive mode - use UPGRADE_SYSTEM=yes to enable)${NC}"
    fi
fi

# Install system dependencies
echo -e "${CYAN}[4/8] Installing required dependencies...${NC}"
apt-get install -y -qq python3 python3-pip python3-venv git wget curl &>/dev/null
echo -e "${GREEN}  ✓ Core dependencies installed${NC}"

# Install GTK4/libadwaita for desktop GUI (optional but recommended)
echo -e "${CYAN}[5/8] Installing GUI dependencies...${NC}"
if [[ "${INSTALL_GTK}" == "yes" ]] || [[ -n "$DISPLAY" ]] || [[ -n "$WAYLAND_DISPLAY" ]]; then
    echo "  Installing GTK4/libadwaita for desktop GUI..."
    apt-get install -y -qq python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1 &>/dev/null || {
        echo -e "${YELLOW}  ⊘ GTK4 not available (headless mode OK)${NC}"
    }
    # Install WebKit for embedded web views (optional)
    apt-get install -y -qq gir1.2-webkit2-4.1 &>/dev/null || true
    echo -e "${GREEN}  ✓ GUI dependencies installed${NC}"
else
    echo -e "${YELLOW}  ⊘ Skipped (no display detected - use INSTALL_GTK=yes to force)${NC}"
fi

# Clone or update repository
INSTALL_DIR="/opt/meshforge"
echo -e "${CYAN}[6/8] Setting up MeshForge...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Updating existing installation..."
    # Fix git safe directory issue (required when running as root)
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
    git pull -q || echo -e "${YELLOW}  Warning: Could not pull updates${NC}"
else
    echo "  Cloning repository..."
    git clone -q https://github.com/Nursedude/meshforge.git "$INSTALL_DIR"
    # Fix git safe directory issue for future updates
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
fi

echo -e "${GREEN}  ✓ Repository ready${NC}"

# Install Python dependencies
echo -e "${CYAN}[7/8] Installing Python dependencies...${NC}"
# Create virtual environment if it doesn't exist
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv" --system-site-packages
fi
# Install dependencies in virtual environment
echo "  Installing packages in virtual environment..."
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r requirements.txt
echo -e "${GREEN}  ✓ Python dependencies installed${NC}"

# Create symlink for easy access
echo -e "${CYAN}[8/8] Creating system commands...${NC}"

# Main launcher wizard (default)
cat > /usr/local/bin/meshforge << 'EOF'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/launcher.py "$@"
EOF
chmod +x /usr/local/bin/meshforge

# Direct GTK GUI access
cat > /usr/local/bin/meshforge-gtk << 'EOF'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/main_gtk.py "$@"
EOF
chmod +x /usr/local/bin/meshforge-gtk

# Direct CLI access
cat > /usr/local/bin/meshforge-cli << 'EOF'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/main.py "$@"
EOF
chmod +x /usr/local/bin/meshforge-cli

# Web UI access
cat > /usr/local/bin/meshforge-web << 'EOF'
#!/bin/bash
cd /opt/meshforge
exec sudo /opt/meshforge/venv/bin/python src/main_web.py "$@"
EOF
chmod +x /usr/local/bin/meshforge-web

# Legacy aliases for backwards compatibility
ln -sf /usr/local/bin/meshforge /usr/local/bin/meshtasticd-installer 2>/dev/null || true
ln -sf /usr/local/bin/meshforge-cli /usr/local/bin/meshtasticd-cli 2>/dev/null || true

echo -e "${GREEN}  ✓ Commands created: meshforge, meshforge-gtk, meshforge-cli, meshforge-web${NC}"

# Create desktop entry if display is available
if [[ "${INSTALL_DESKTOP}" == "yes" ]] || [[ -n "$DISPLAY" ]] || [[ -n "$WAYLAND_DISPLAY" ]]; then
    echo -e "${CYAN}Creating desktop entry...${NC}"
    mkdir -p /usr/share/applications
    cat > /usr/share/applications/meshforge.desktop << 'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=MeshForge
Comment=LoRa Mesh Network Operations Suite
Exec=/usr/local/bin/meshforge-gtk
Icon=/opt/meshforge/assets/shaka.svg
Terminal=false
Categories=Network;HamRadio;Settings;
Keywords=meshtastic;lora;mesh;radio;
EOF
    chmod 644 /usr/share/applications/meshforge.desktop
    echo -e "${GREEN}  ✓ Desktop entry created${NC}"
fi

# Success message
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         MeshForge Installation Complete!                  ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Quick Start:${NC}"
echo "  ${GREEN}sudo meshforge${NC}         - Launch interface wizard"
echo "  ${GREEN}sudo meshforge-gtk${NC}     - GTK desktop application"
echo "  ${GREEN}sudo meshforge-web${NC}     - Web UI (browser access)"
echo "  ${GREEN}sudo meshforge-cli${NC}     - Rich CLI interface"
echo ""
echo -e "${CYAN}Features:${NC}"
echo "  • Meshtastic + Reticulum (RNS) gateway bridge"
echo "  • RF tools (LOS, Fresnel, path loss)"
echo "  • Hardware simulation mode"
echo "  • HamClock space weather integration"
echo "  • Interactive node map"
echo ""
echo -e "${CYAN}Would you like to start MeshForge now? [Y/n]${NC}"

# Try to read from TTY if available for interactive prompt
if [[ -c /dev/tty ]]; then
    read -r response < /dev/tty
    if [[ "$response" =~ ^([nN][oO]|[nN])$ ]]; then
        echo ""
        echo -e "${YELLOW}Installation complete. Run 'sudo meshforge' when ready.${NC}"
        exit 0
    fi
fi

# Launch installer (default action)
echo ""
echo -e "${GREEN}Starting MeshForge...${NC}"
echo ""
exec /usr/local/bin/meshforge
