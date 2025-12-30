#!/bin/bash
# Meshtasticd installation script for 32-bit Raspberry Pi OS (armhf)
# Uses official OpenSUSE Build Service repositories

set -e

VERSION_TYPE="${1:-stable}"

echo "========================================="
echo "Meshtasticd Installer for Raspbian armhf"
echo "Version: $VERSION_TYPE"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

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
else
    echo -e "${RED}Cannot detect OS version${NC}"
    exit 1
fi

# Install dependencies
echo -e "\n${GREEN}Installing dependencies...${NC}"
apt-get install -y \
    curl \
    gnupg \
    lsb-release \
    apt-transport-https \
    ca-certificates \
    build-essential \
    python3 \
    python3-pip \
    git

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

# Map OS version to repository version
if [ "$OS_NAME" = "raspbian" ]; then
    REPO_OS="Raspbian_${OS_VERSION}"
else
    REPO_OS="Debian_${OS_VERSION}"
fi

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

# Pre-emptively fix common packaging conflicts
echo -e "\n${CYAN}Checking for packaging conflicts...${NC}"
if dpkg -l | grep -q python3-packaging; then
    echo -e "${YELLOW}Detected python3-packaging (Debian package) - removing to prevent conflicts${NC}"

    # Remove python3-packaging (this may also remove pip)
    apt-get remove --purge python3-packaging -y || true

    # Ensure pip is installed
    echo -e "${GREEN}Ensuring pip is available...${NC}"
    apt-get install -y python3-pip python3-setuptools python3-wheel

    # Now install packaging via pip
    echo -e "${GREEN}Installing packaging via pip${NC}"
    python3 -m pip install --upgrade --force-reinstall packaging --break-system-packages || true
fi

# Install meshtasticd
echo -e "\n${GREEN}Installing meshtasticd from official repository...${NC}"
apt-get install -y meshtasticd

# Install Python meshtastic library with force-reinstall for packaging conflicts
echo -e "\n${GREEN}Installing meshtastic Python library...${NC}"
python3 -m pip install --upgrade --force-reinstall --break-system-packages meshtastic click rich pyyaml requests packaging psutil distro python-dotenv

# Enable SPI and I2C
echo -e "\n${GREEN}Enabling SPI and I2C...${NC}"
CONFIG_FILE="/boot/config.txt"
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
fi

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

# Show installed version
INSTALLED_VERSION=$(dpkg -l | grep meshtasticd | awk '{print $3}')
echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}Installation completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo -e "${CYAN}Installed version: ${INSTALLED_VERSION}${NC}"
echo -e "${CYAN}Repository: network:Meshtastic:${REPO_CHANNEL}${NC}"
echo -e "${YELLOW}Note: A reboot may be required for SPI/I2C changes to take effect${NC}"

exit 0
