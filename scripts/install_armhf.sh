#!/bin/bash
# Meshtasticd installation script for 32-bit Raspberry Pi OS (armhf)
# Uses official OpenSUSE Build Service repositories
#
# This script:
# 1. Installs meshtasticd daemon from official repos
# 2. Installs meshtastic CLI via pipx (isolated environment)
# 3. Configures SPI/I2C for LoRa HAT communication

set -e

VERSION_TYPE="${1:-stable}"
DEBUG_MODE="${DEBUG_MODE:-false}"

echo "========================================="
echo "Meshtasticd Installer for Raspbian armhf"
echo "Version: $VERSION_TYPE"
echo "Debug: $DEBUG_MODE"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Debug logging function
debug_log() {
    if [ "$DEBUG_MODE" = "true" ]; then
        echo -e "${CYAN}[DEBUG] $1${NC}"
    fi
}

# Error handling function
handle_error() {
    local exit_code=$?
    local line_number=$1
    echo -e "${RED}Error occurred on line $line_number (exit code: $exit_code)${NC}"
    echo -e "${YELLOW}Check /var/log/meshtasticd-installer-error.log for details${NC}"
    exit $exit_code
}

trap 'handle_error $LINENO' ERR

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    exit 1
fi

# Detect Raspbian/Debian version
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_VERSION="${VERSION_ID}"
    OS_NAME="${ID}"
    echo -e "${CYAN}Detected: ${NAME} ${VERSION}${NC}"
    debug_log "OS_NAME=$OS_NAME, OS_VERSION=$OS_VERSION"
else
    echo -e "${RED}Cannot detect OS version${NC}"
    exit 1
fi

# Install dependencies including pipx
echo -e "\n${GREEN}Installing system dependencies...${NC}"
apt-get install -y \
    curl \
    gnupg \
    lsb-release \
    apt-transport-https \
    ca-certificates \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    pipx \
    git

debug_log "System dependencies installed"

# Determine repository based on version type
# Official repositories: https://download.opensuse.org/repositories/network:/Meshtastic:/
case "$VERSION_TYPE" in
    stable|beta)
        REPO_CHANNEL="beta"
        echo -e "\n${GREEN}Using beta repository (latest stable releases)${NC}"
        ;;
    daily)
        REPO_CHANNEL="daily"
        echo -e "\n${YELLOW}Using daily repository (cutting-edge builds)${NC}"
        ;;
    alpha)
        REPO_CHANNEL="alpha"
        echo -e "\n${YELLOW}Using alpha repository (experimental builds)${NC}"
        ;;
    *)
        echo -e "${YELLOW}Unknown version type '$VERSION_TYPE', defaulting to beta${NC}"
        REPO_CHANNEL="beta"
        ;;
esac

debug_log "Using repository channel: $REPO_CHANNEL"

# Map OS version to repository version
if [ "$OS_NAME" = "raspbian" ]; then
    REPO_OS="Raspbian_${OS_VERSION}"
else
    REPO_OS="Debian_${OS_VERSION}"
fi

debug_log "Repository OS: $REPO_OS"

# Remove old Meshtastic repository files if they exist
echo -e "\n${CYAN}Cleaning up old repository configurations...${NC}"
rm -f /etc/apt/sources.list.d/meshtastic.list
rm -f /etc/apt/sources.list.d/network:Meshtastic:*.list
rm -f /usr/share/keyrings/meshtastic-archive-keyring.gpg
rm -f /etc/apt/trusted.gpg.d/network_Meshtastic_*.gpg

# Add official Meshtastic repository from OpenSUSE Build Service
echo -e "\n${GREEN}Adding official Meshtastic ${REPO_CHANNEL} repository...${NC}"
REPO_URL="http://download.opensuse.org/repositories/network:/Meshtastic:/${REPO_CHANNEL}/${REPO_OS}/"
echo -e "${CYAN}Repository: ${REPO_URL}${NC}"

echo "deb ${REPO_URL} /" | tee /etc/apt/sources.list.d/network:Meshtastic:${REPO_CHANNEL}.list

# Add repository key
echo -e "\n${GREEN}Adding repository signing key...${NC}"
KEY_URL="https://download.opensuse.org/repositories/network:/Meshtastic:/${REPO_CHANNEL}/${REPO_OS}/Release.key"
curl -fsSL "${KEY_URL}" | gpg --dearmor | tee /etc/apt/trusted.gpg.d/network_Meshtastic_${REPO_CHANNEL}.gpg > /dev/null

# Update package lists
echo -e "\n${GREEN}Updating package lists...${NC}"
apt-get update

# Install meshtasticd from official repository
echo -e "\n${GREEN}Installing meshtasticd from official repository...${NC}"
apt-get install -y meshtasticd

debug_log "meshtasticd package installed"

# Install meshtastic CLI via pipx (isolated environment - avoids packaging conflicts)
echo -e "\n${GREEN}Installing meshtastic CLI via pipx...${NC}"
echo -e "${CYAN}Using pipx for isolated installation (avoids system package conflicts)${NC}"

# Ensure pipx path is set up
pipx ensurepath 2>/dev/null || true

# Install meshtastic with CLI extras using pipx
# This creates an isolated environment, avoiding the packaging 25.0 conflict
if pipx list 2>/dev/null | grep -q "meshtastic"; then
    echo -e "${CYAN}Upgrading existing meshtastic CLI...${NC}"
    pipx upgrade meshtastic 2>&1 || pipx reinstall meshtastic
else
    echo -e "${GREEN}Installing meshtastic CLI...${NC}"
    # Use --force to handle any previous partial installs
    pipx install "meshtastic[cli]" --force 2>&1 || {
        echo -e "${YELLOW}Retrying with basic meshtastic package...${NC}"
        pipx install meshtastic --force 2>&1
    }
fi

# Verify meshtastic CLI installation
if command -v meshtastic &> /dev/null || [ -f "$HOME/.local/bin/meshtastic" ] || [ -f "/root/.local/bin/meshtastic" ]; then
    echo -e "${GREEN}✓ meshtastic CLI installed successfully${NC}"
    debug_log "meshtastic CLI location: $(which meshtastic 2>/dev/null || echo '/root/.local/bin/meshtastic')"
else
    echo -e "${YELLOW}⚠ meshtastic CLI may not be in PATH. Add ~/.local/bin to your PATH${NC}"
    echo -e "${CYAN}Run: export PATH=\"\$PATH:\$HOME/.local/bin\"${NC}"
fi

# Also install essential Python packages for the installer UI
# Use --break-system-packages since we're running as root
echo -e "\n${GREEN}Installing installer UI dependencies...${NC}"
python3 -m pip install --quiet --break-system-packages \
    click rich pyyaml requests psutil distro python-dotenv 2>&1 || {
    echo -e "${YELLOW}Note: Some packages may already be satisfied by system packages${NC}"
}

# Enable SPI and I2C
echo -e "\n${GREEN}Enabling SPI and I2C...${NC}"
CONFIG_FILE="/boot/config.txt"
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
fi

debug_log "Using config file: $CONFIG_FILE"

if ! grep -q "^dtparam=spi=on" "$CONFIG_FILE"; then
    echo "dtparam=spi=on" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ SPI enabled in ${CONFIG_FILE}${NC}"
else
    echo -e "${CYAN}SPI already enabled${NC}"
fi

if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_FILE"; then
    echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
    echo -e "${GREEN}✓ I2C enabled in ${CONFIG_FILE}${NC}"
else
    echo -e "${CYAN}I2C already enabled${NC}"
fi

# Load SPI module
echo -e "\n${GREEN}Loading SPI kernel module...${NC}"
modprobe spi_bcm2835 || true

# Show installed version and status
INSTALLED_VERSION=$(dpkg -l | grep meshtasticd | awk '{print $3}')
MESHTASTIC_CLI_VERSION=$(pipx list 2>/dev/null | grep meshtastic | head -1 || echo "Check with: pipx list")

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}Installation completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo -e "${CYAN}Installed versions:${NC}"
echo -e "  meshtasticd (daemon): ${INSTALLED_VERSION}"
echo -e "  meshtastic CLI: ${MESHTASTIC_CLI_VERSION}"
echo -e "${CYAN}Repository: network:Meshtastic:${REPO_CHANNEL}${NC}"
echo ""
echo -e "${CYAN}Useful commands:${NC}"
echo -e "  meshtastic --info       # Show connected device info"
echo -e "  meshtastic --nodes      # Show nodes in mesh"
echo -e "  systemctl status meshtasticd  # Check daemon status"
echo ""
echo -e "${YELLOW}Note: A reboot may be required for SPI/I2C changes to take effect${NC}"
echo -e "${YELLOW}If meshtastic command not found, run: export PATH=\"\$PATH:\$HOME/.local/bin\"${NC}"

debug_log "Installation complete. Debug mode was enabled."

exit 0
