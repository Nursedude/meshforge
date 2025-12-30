#!/bin/bash
#
# Meshtasticd Interactive Installer - One-Liner Quick Install
#
# Downloads, installs, and launches the interactive installer automatically.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
#
# Options:
#   UPGRADE_SYSTEM=yes  - Automatically upgrade all system packages
#   SKIP_UPGRADE=yes    - Skip the system upgrade prompt entirely
#
# Examples:
#   # Install with system upgrade
#   curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo UPGRADE_SYSTEM=yes bash
#
#   # Install without upgrade prompt
#   curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo SKIP_UPGRADE=yes bash
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
echo "║   Meshtasticd Interactive Installer - Quick Install      ║"
echo "║   For Raspberry Pi OS                                     ║"
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
echo -e "${CYAN}[4/7] Installing required dependencies...${NC}"
apt-get install -y -qq python3 python3-pip python3-venv git wget curl &>/dev/null
echo -e "${GREEN}  ✓ Required dependencies installed${NC}"

# Clone or update repository
INSTALL_DIR="/opt/meshtasticd-installer"
echo -e "${CYAN}[5/7] Setting up installer...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Updating existing installation..."
    # Fix git safe directory issue (required when running as root)
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
    git pull -q
else
    echo "  Cloning repository..."
    git clone -q https://github.com/Nursedude/Meshtasticd_interactive_UI.git "$INSTALL_DIR"
    # Fix git safe directory issue for future updates
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    cd "$INSTALL_DIR"
fi

echo -e "${GREEN}  ✓ Repository ready${NC}"

# Install Python dependencies
echo -e "${CYAN}[6/7] Installing Python dependencies...${NC}"
# Create virtual environment if it doesn't exist
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
fi
# Install dependencies in virtual environment
echo "  Installing packages in virtual environment..."
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q -r requirements.txt
echo -e "${GREEN}  ✓ Python dependencies installed${NC}"

# Create symlink for easy access
echo -e "${CYAN}[7/7] Creating system command...${NC}"
cat > /usr/local/bin/meshtasticd-installer << 'EOF'
#!/bin/bash
cd /opt/meshtasticd-installer
exec sudo /opt/meshtasticd-installer/venv/bin/python src/main.py "$@"
EOF
chmod +x /usr/local/bin/meshtasticd-installer
echo -e "${GREEN}  ✓ Command 'meshtasticd-installer' created${NC}"

# Success message
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Installation Complete!                         ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Quick Start:${NC}"
echo "  1. Run interactively:  ${GREEN}sudo meshtasticd-installer${NC}"
echo "  2. Install meshtasticd: ${GREEN}sudo meshtasticd-installer --install stable${NC}"
echo "  3. Configure device:    ${GREEN}sudo meshtasticd-installer --configure${NC}"
echo ""
echo -e "${CYAN}Would you like to start the interactive installer now? [Y/n]${NC}"

# Try to read from TTY if available for interactive prompt
if [[ -c /dev/tty ]]; then
    read -r response < /dev/tty
    if [[ "$response" =~ ^([nN][oO]|[nN])$ ]]; then
        echo ""
        echo -e "${YELLOW}Installation complete. Run 'sudo meshtasticd-installer' when ready.${NC}"
        exit 0
    fi
fi

# Launch installer (default action)
echo ""
echo -e "${GREEN}Starting interactive installer...${NC}"
echo ""
exec /usr/local/bin/meshtasticd-installer
