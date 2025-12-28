#!/bin/bash
#
# Meshtasticd Interactive Installer - CLI Quick Install
# One-liner: curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_IU/main/install.sh | sudo bash
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
echo -e "${CYAN}[2/6] Updating package lists...${NC}"
apt-get update -qq
echo -e "${GREEN}  ✓ Package lists updated${NC}"

# Optional: Upgrade system packages
# Check if running interactively or if UPGRADE_SYSTEM is set
if [[ -t 0 ]] && [[ -z "${SKIP_UPGRADE}" ]]; then
    echo -e "${CYAN}[3/6] System upgrade (optional)...${NC}"
    echo -e "${YELLOW}  This will upgrade all system packages and may take several minutes.${NC}"
    read -p "  Would you like to upgrade system packages now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        apt-get upgrade -y
        echo -e "${GREEN}  ✓ System upgraded${NC}"
    else
        echo -e "${YELLOW}  ⊘ System upgrade skipped${NC}"
    fi
elif [[ "${UPGRADE_SYSTEM}" == "yes" ]]; then
    echo -e "${CYAN}[3/6] Upgrading system packages...${NC}"
    apt-get upgrade -y
    echo -e "${GREEN}  ✓ System upgraded${NC}"
else
    echo -e "${CYAN}[3/6] System upgrade...${NC}"
    echo -e "${YELLOW}  ⊘ Skipped (set UPGRADE_SYSTEM=yes to enable)${NC}"
fi

# Install system dependencies
echo -e "${CYAN}[4/6] Installing required dependencies...${NC}"
apt-get install -y -qq python3 python3-pip python3-venv git wget curl &>/dev/null
echo -e "${GREEN}  ✓ Required dependencies installed${NC}"

# Clone or update repository
INSTALL_DIR="/opt/meshtasticd-installer"
echo -e "${CYAN}[5/6] Setting up installer...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull -q
else
    echo "  Cloning repository..."
    git clone -q https://github.com/Nursedude/Meshtasticd_interactive_IU.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo -e "${GREEN}  ✓ Repository ready${NC}"

# Install Python dependencies
echo -e "${CYAN}[6/6] Installing Python dependencies...${NC}"
python3 -m pip install -q --upgrade pip
python3 -m pip install -q -r requirements.txt
echo -e "${GREEN}  ✓ Python dependencies installed${NC}"

# Create symlink for easy access
echo -e "${CYAN}[6/6] Creating system command...${NC}"
cat > /usr/local/bin/meshtasticd-installer << 'EOF'
#!/bin/bash
cd /opt/meshtasticd-installer
exec sudo python3 src/main.py "$@"
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
echo -e "${CYAN}Would you like to start the installer now? [Y/n]${NC}"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY]|"")$ ]]; then
    exec /usr/local/bin/meshtasticd-installer
fi
